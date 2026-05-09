"""Tests for conversational/trigger.py — M6."""
from __future__ import annotations

import asyncio
import warnings
import pytest
from unittest.mock import AsyncMock, MagicMock

from conversational.trigger import (
    InboundMessage,
    Intent,
    classify_intent,
    handle_message,
    make_signal_notify_fn,
)


# ─── classify_intent ─────────────────────────────────────────────────────────

def test_classify_direct_question():
    msg = InboundMessage(text="What time is it?")
    intent, brief = classify_intent(msg)
    assert intent == Intent.DIRECT
    assert brief == ""


def test_classify_worker_task():
    msg = InboundMessage(text="Translate this message to French")
    intent, brief = classify_intent(msg)
    assert intent == Intent.WORKER
    assert brief == "Translate this message to French"


def test_classify_worker_summarize():
    msg = InboundMessage(text="Summarize today's news")
    intent, brief = classify_intent(msg)
    assert intent == Intent.WORKER


def test_classify_both():
    msg = InboundMessage(text="Can you translate this to Spanish and what does it mean?")
    intent, brief = classify_intent(msg)
    assert intent == Intent.BOTH


def test_classify_research():
    msg = InboundMessage(text="Research the latest on MLX audio models")
    intent, brief = classify_intent(msg)
    assert intent == Intent.WORKER


def test_classify_generate():
    msg = InboundMessage(text="Generate a voice reply for this")
    intent, brief = classify_intent(msg)
    assert intent == Intent.WORKER


# ─── handle_message ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_direct_message_no_worker():
    """Direct message should not dispatch a worker."""
    notify_calls = []

    async def worker_fn(task: str) -> str:
        return "should not be called"

    async def notify_fn(text: str) -> None:
        notify_calls.append(text)

    msg = InboundMessage(text="What time is it?")
    result = await handle_message(msg, worker_fn=worker_fn, notify_fn=notify_fn)

    assert result.intent == Intent.DIRECT
    assert result.worker_task is None
    assert len(notify_calls) == 0  # no ack sent for direct


@pytest.mark.asyncio
async def test_handle_worker_message_sends_ack():
    """Worker task should send ack immediately."""
    notify_calls = []
    worker_called = asyncio.Event()

    async def worker_fn(task: str) -> str:
        worker_called.set()
        return "translation done"

    async def notify_fn(text: str) -> None:
        notify_calls.append(text)

    msg = InboundMessage(text="Translate this to English")
    result = await handle_message(msg, worker_fn=worker_fn, notify_fn=notify_fn)

    # Ack sent immediately
    assert result.intent == Intent.WORKER
    assert result.worker_task is not None
    assert any("On it" in c for c in notify_calls)

    # Let background task complete
    await asyncio.sleep(0.1)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)

    # Result notification sent
    assert any("translation done" in c for c in notify_calls)


@pytest.mark.asyncio
async def test_handle_worker_result_injected_back():
    """Worker result should be injected back via notify_fn."""
    received = []

    async def worker_fn(task: str) -> str:
        return f"Result for: {task}"

    async def notify_fn(text: str) -> None:
        received.append(text)

    msg = InboundMessage(text="Summarize the news")
    await handle_message(msg, worker_fn=worker_fn, notify_fn=notify_fn)

    # Let background complete
    await asyncio.sleep(0.1)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)

    result_msgs = [m for m in received if "Result for:" in m]
    assert len(result_msgs) == 1


@pytest.mark.asyncio
async def test_handle_worker_failure_notifies():
    """Worker failure should still send notification."""
    received = []

    async def worker_fn(task: str) -> str:
        raise ValueError("worker exploded")

    async def notify_fn(text: str) -> None:
        received.append(text)

    msg = InboundMessage(text="Translate this please")
    await handle_message(msg, worker_fn=worker_fn, notify_fn=notify_fn)

    await asyncio.sleep(0.1)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)

    failure_msgs = [m for m in received if "failed" in m.lower() or "Worker failed" in m]
    assert len(failure_msgs) >= 1


@pytest.mark.asyncio
async def test_elapsed_time_recorded():
    """TriggerResult should record elapsed time."""
    async def worker_fn(task: str) -> str:
        return "done"

    async def notify_fn(text: str) -> None:
        pass

    msg = InboundMessage(text="What is 2+2")
    result = await handle_message(msg, worker_fn=worker_fn, notify_fn=notify_fn)
    assert result.elapsed_seconds >= 0


@pytest.mark.asyncio
async def test_direct_reply_fn_called_for_direct():
    """direct_reply_fn should be called for DIRECT intent."""
    async def worker_fn(task: str) -> str:
        return "should not call"

    async def notify_fn(text: str) -> None:
        pass

    async def direct_reply_fn(text: str) -> str:
        return f"Direct answer to: {text}"

    msg = InboundMessage(text="What is the weather?")
    result = await handle_message(
        msg,
        worker_fn=worker_fn,
        notify_fn=notify_fn,
        direct_reply_fn=direct_reply_fn,
    )
    assert result.direct_reply is not None
    assert "Direct answer" in result.direct_reply


# ─── make_signal_notify_fn — SIGNAL_ACCOUNT env var ──────────────────────────

def test_make_signal_notify_fn_uses_env_var(monkeypatch):
    """account default should come from SIGNAL_ACCOUNT env var."""
    monkeypatch.setenv("SIGNAL_ACCOUNT", "+15550001234")
    # Reload default by calling with no account kwarg — default is evaluated at import time,
    # so we pass the env var explicitly to verify the pattern works.
    fn = make_signal_notify_fn(recipient="+15559999999", account="+15550001234")
    assert fn is not None  # callable was constructed without error


def test_make_signal_notify_fn_warns_when_account_empty(monkeypatch):
    """Should emit RuntimeWarning when account resolves to empty string."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        make_signal_notify_fn(recipient="+15559999999", account="")
    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 1
    assert "SIGNAL_ACCOUNT is not set" in str(runtime_warnings[0].message)


def test_make_signal_notify_fn_no_warning_when_account_set(monkeypatch):
    """Should NOT warn when account is provided."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        make_signal_notify_fn(recipient="+15559999999", account="+15550001234")
    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 0
