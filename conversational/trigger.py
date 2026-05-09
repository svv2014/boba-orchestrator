"""Conversational mode trigger for boba-orchestrator.

Receives an inbound message, classifies intent, decides:
  - Answer directly (no workers needed)
  - Spawn background worker + send ack immediately
  - Both: answer + background worker

The human stays in conversation throughout. Workers get a task brief only —
never the full conversation context.
"""
from __future__ import annotations

import asyncio
import os
import time
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Intent(Enum):
    DIRECT = "direct"       # Answer inline, no worker needed
    WORKER = "worker"       # Background worker only
    BOTH = "both"           # Answer inline + spawn worker


@dataclass
class InboundMessage:
    text: str
    sender: str = "unknown"
    timestamp: float = field(default_factory=time.time)


@dataclass
class TriggerResult:
    intent: Intent
    direct_reply: Optional[str]          # Immediate text back to human
    worker_task: Optional[str]           # Task brief sent to worker (sanitized)
    worker_result: Optional[str] = None  # Filled in when worker completes
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


# ─── Intent classification ────────────────────────────────────────────────────

# Keywords that signal a background task is wanted
_TASK_SIGNALS = [
    "translate", "summarize", "summarise", "research", "find",
    "write", "generate", "create", "build", "analyse", "analyze",
    "check", "review", "convert", "transcribe", "look up",
]

# Keywords that strongly suggest a quick direct answer
_DIRECT_SIGNALS = [
    "what is", "what's", "who is", "when is", "how do",
    "can you", "do you", "are you", "what time", "what date",
]


def classify_intent(message: InboundMessage) -> tuple[Intent, str]:
    """Classify message intent and extract worker task brief if needed.

    Returns (Intent, task_brief_or_empty).
    """
    text = message.text.strip()
    lower = text.lower()

    has_task_signal = any(s in lower for s in _TASK_SIGNALS)
    has_direct_signal = any(s in lower for s in _DIRECT_SIGNALS)

    if has_task_signal and not has_direct_signal:
        return Intent.WORKER, text
    elif has_task_signal and has_direct_signal:
        return Intent.BOTH, text
    else:
        return Intent.DIRECT, ""


# ─── Core trigger ─────────────────────────────────────────────────────────────

async def handle_message(
    message: InboundMessage,
    *,
    worker_fn: Callable[[str], "asyncio.Future[str]"],
    notify_fn: Callable[[str], "asyncio.Future[None]"],
    ack_threshold_seconds: float = 10.0,
    direct_reply_fn: Optional[Callable[[str], "asyncio.Future[str]"]] = None,
) -> TriggerResult:
    """Handle an inbound message in conversational mode.

    Immediately returns a TriggerResult with:
      - direct_reply: sent right away (ack or inline answer)
      - worker_task: dispatched in background (if applicable)

    When the worker completes, notify_fn is called with the result.

    Args:
        message: The inbound message from the human.
        worker_fn: Async callable that executes the task brief, returns result text.
        notify_fn: Async callable that sends a message back to the human.
        ack_threshold_seconds: Send ack if worker will likely take longer than this.
        direct_reply_fn: Optional LLM callable for generating direct answers.
    """
    start = time.monotonic()
    intent, task_brief = classify_intent(message)

    result = TriggerResult(
        intent=intent,
        direct_reply=None,
        worker_task=task_brief if task_brief else None,
    )

    # --- Direct answers ---
    if intent in (Intent.DIRECT, Intent.BOTH):
        if direct_reply_fn is not None:
            try:
                reply = await direct_reply_fn(message.text)
                result.direct_reply = reply
            except Exception as e:
                result.direct_reply = f"(direct reply failed: {e})"
        else:
            result.direct_reply = None  # caller handles direct response

    # --- Background worker ---
    if intent in (Intent.WORKER, Intent.BOTH) and task_brief:
        # Send ack immediately so human knows work is happening
        ack = f"On it — working in background: {task_brief[:80]}"
        await notify_fn(ack)

        # Dispatch worker in background
        asyncio.create_task(
            _run_worker_and_notify(
                task_brief=task_brief,
                worker_fn=worker_fn,
                notify_fn=notify_fn,
                start_time=start,
            )
        )

    result.elapsed_seconds = time.monotonic() - start
    return result


async def _run_worker_and_notify(
    task_brief: str,
    worker_fn: Callable[[str], "asyncio.Future[str]"],
    notify_fn: Callable[[str], "asyncio.Future[None]"],
    start_time: float,
) -> None:
    """Run worker in background, send result when done."""
    try:
        worker_result = await worker_fn(task_brief)
        elapsed = int(time.monotonic() - start_time)
        msg = f"Done ({elapsed}s):\n{worker_result}"
    except Exception as e:
        elapsed = int(time.monotonic() - start_time)
        msg = f"Worker failed ({elapsed}s): {e}"

    await notify_fn(msg)


# ─── Signal notifier integration ──────────────────────────────────────────────

def make_signal_notify_fn(
    recipient: str,
    signal_url: str = os.environ.get("SIGNAL_URL", "http://127.0.0.1:18080/api/v1/rpc"),
    account: str = os.environ.get("SIGNAL_ACCOUNT", ""),
) -> Callable[[str], "asyncio.Future[None]"]:
    """Build an async notify_fn that sends text via Signal JSON-RPC daemon."""
    import json
    import urllib.request

    if not account:
        warnings.warn(
            "SIGNAL_ACCOUNT is not set; Signal notifications will fail",
            RuntimeWarning,
            stacklevel=2,
        )

    async def _notify(text: str) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "id": int(time.time()),
            "params": {
                "recipient": [recipient],
                "message": text,
            },
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            signal_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Run blocking urllib in thread to keep async clean
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, urllib.request.urlopen, req)

    return _notify


def make_signal_voice_notify_fn(
    recipient: str,
    tts_script: str,
    voice: str = "aiden",
    signal_url: str = os.environ.get("SIGNAL_URL", "http://127.0.0.1:18080/api/v1/rpc"),
) -> Callable[[str], "asyncio.Future[None]"]:
    """Build an async notify_fn that generates TTS and sends as voice note."""
    import json
    import subprocess
    import base64
    import tempfile
    import urllib.request

    async def _notify_voice(text: str) -> None:
        loop = asyncio.get_running_loop()

        def _generate_and_send():
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                wav_path = tmp_wav.name
            m4a_path = wav_path.replace(".wav", ".m4a")

            # Generate TTS
            subprocess.run(
                [
                    tts_script,
                    "--text", text,
                    "--out-prefix", wav_path.replace(".wav", ""),
                    "--mode", "named",
                    "--voice", voice,
                    "--audio-format", "wav",
                ],
                check=True,
                capture_output=True,
            )

            actual_wav = wav_path.replace(".wav", "_000.wav")

            # Convert to M4A
            subprocess.run(
                ["ffmpeg", "-y", "-i", actual_wav, "-c:a", "aac", "-b:a", "64k", m4a_path],
                check=True,
                capture_output=True,
            )

            # Send via Signal JSON-RPC
            with open(m4a_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            payload = {
                "jsonrpc": "2.0",
                "method": "send",
                "id": int(time.time()),
                "params": {
                    "recipient": [recipient],
                    "message": "",
                    "attachments": [f"data:audio/mp4;base64,{b64}"],
                },
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                signal_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req)

        await loop.run_in_executor(None, _generate_and_send)

    return _notify_voice
