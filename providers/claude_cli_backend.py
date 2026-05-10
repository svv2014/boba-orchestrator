"""Claude CLI backend — spawns `claude -p` sessions as workers.

Uses the locally installed `claude` CLI which is already authenticated
and has full tool access (bash, file system, etc.). This is the backend
that makes workers actually write code, not just generate text.

Each worker runs as a subprocess: `claude -p "task description" --model sonnet`
The output is captured and parsed into WorkerResult.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    PlanResult,
    SubTask,
    TaskStatus,
    TaskType,
    WorkerResult,
)

logger = logging.getLogger(__name__)

# Module-level active transcript — set by orchestrator before dispatching workers.
# Workers read this to emit exec events. Thread-safe: Transcript itself uses a lock.
_active_transcript = None


def set_active_transcript(transcript: "Any") -> None:
    """Register a Transcript instance for the current run.

    Called by orchestrator immediately after opening the transcript so that
    ClaudeCliWorker can emit ``claude.exec.start`` / ``claude.exec.done`` events
    without needing an explicit reference passed through every call chain.
    """
    global _active_transcript
    _active_transcript = transcript


def _emit(kind: str, **fields: "Any") -> None:
    """Emit to the active transcript. Silently no-ops if none is set."""
    try:
        t = _active_transcript
        if t is not None:
            t.emit(kind, **fields)
    except Exception:
        pass


def _resolve_claude_bin() -> str:
    """Resolve the Claude CLI binary path at call time.

    Resolution order:
      1. CLAUDE_CLI_PATH env var (primary)
      2. CLAUDE_BIN env var (deprecated alias — logs a warning once)
      3. shutil.which("claude")

    Raises RuntimeError if none of the above resolves to a non-empty string.
    """
    path = os.environ.get("CLAUDE_CLI_PATH", "")
    if path:
        return path

    legacy = os.environ.get("CLAUDE_BIN", "")
    if legacy:
        logger.warning(
            "CLAUDE_BIN is deprecated; rename to CLAUDE_CLI_PATH. "
            "Support will be removed in a future release."
        )
        return legacy

    found = shutil.which("claude")
    if found:
        return found

    raise RuntimeError(
        "Claude CLI binary not found. "
        "Set CLAUDE_CLI_PATH or install via https://docs.claude.com/claude-code"
    )
DEFAULT_PLANNER_MODEL = "sonnet"

# Default backoff before retrying a transient failure (configurable via env var)
_DEFAULT_RETRY_DELAY = 30

# Patterns that indicate a transient (retryable) failure — mirrors loop's runner.sh
# _loop_is_recoverable classifier so retry decisions are consistent across both layers.
_RECOVERABLE_PATTERNS = [
    "rate limit",
    "429",
    "server error",
    "5xx",
    "connection refused",
    "connection timed out",
    "network error",
    "timeout",
    # Auth-flake: only when "401" appears alongside "auth" context
    "401",
]

# Patterns that indicate a permanent failure — never retry these.
_PERMANENT_PATTERNS = [
    "tool denied",
    "sandbox rejection",
    "permission denied by user",
    "syntax error",
    "context window",
    "context length",
    "maximum context",
    "too many tokens",
]


def _is_recoverable(stderr_text: str) -> bool:
    """Return True if the failure looks transient and should be retried.

    Mirrors the ``_loop_is_recoverable`` classifier in loop's runner.sh so
    the retry decision is consistent across the orchestrator and loop layers.

    Permanent failures (tool denial, syntax errors, context window exceeded)
    are detected first and always return False regardless of other signals.
    """
    lowered = stderr_text.lower()

    # Permanent patterns take priority — never retry these.
    for pat in _PERMANENT_PATTERNS:
        if pat in lowered:
            return False

    # Auth-flake: "401" is only transient when the word "auth" appears nearby.
    has_401 = "401" in lowered
    if has_401 and "auth" not in lowered:
        # A bare 401 without auth context is not classified as transient.
        # Remove "401" from consideration by checking remaining patterns.
        recoverable_without_401 = [p for p in _RECOVERABLE_PATTERNS if p != "401"]
        return any(pat in lowered for pat in recoverable_without_401)

    return any(pat in lowered for pat in _RECOVERABLE_PATTERNS)
DEFAULT_WORKER_MODEL = "sonnet"
DEFAULT_TIMEOUT = 3600  # 60 min default — overridden by planner's estimate per subtask


def _format_claude_error(returncode: int, stdout: bytes, stderr: bytes,
                         max_chars: int = 800) -> str:
    """Build a useful error string from a non-zero claude CLI exit (closes #21).

    Claude `-p --output-format text` writes its response to stdout, so when it
    fails partway, the actual error message is in stdout — not stderr. The
    previous code only captured stderr and produced "claude CLI exited with
    code 1: " (empty body) for the entire 2026-05-02 failure cluster, leaving
    operators to guess at the cause (rate limit / auth / context window /
    sandbox rejection).

    Strategy:
      - Decode both streams.
      - If stderr has content: return its tail (most relevant for CLI errors).
      - Else if stdout has content: return its tail (claude's diagnostic body).
      - Else explicit `<no stderr or stdout captured>` so the format is unambiguous.

    Tails are capped at ``max_chars`` so log lines remain greppable.
    """
    err = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
    out = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

    if err:
        body = err
        source = "stderr"
    elif out:
        body = out
        source = "stdout"
    else:
        return f"<no stderr or stdout captured> (exit code {returncode})"

    if len(body) > max_chars:
        body = "…" + body[-max_chars:]
    return f"[{source}] {body}"

def _session_exists(session_id: str) -> bool:
    """Check if a claude session has been used before.

    Checks our own session state file first (fast), then falls back
    to checking Claude's session storage on disk.
    """
    # Check our state file
    state_file = os.path.expanduser("~/.orchestrator/sessions.json")
    try:
        if os.path.exists(state_file):
            import json as _json
            with open(state_file) as f:
                data = _json.load(f)
            for key, sdata in data.items():
                if sdata.get("session_id") == session_id and sdata.get("run_count", 0) > 0:
                    return True
    except Exception:
        pass

    # Check Claude's session storage
    import glob
    home = os.path.expanduser("~")
    for pattern in [
        f"{home}/.claude/projects/*/{session_id}*",
        f"{home}/.claude/sessions/{session_id}*",
    ]:
        if glob.glob(pattern):
            return True

    return False


_PROGRESS_DIR_ENV = "ORCHESTRATOR_PROGRESS_DIR"
_DEFAULT_PROGRESS_DIR = "~/.orchestrator/progress"


def _progress_dir() -> str:
    return os.path.expanduser(
        os.environ.get(_PROGRESS_DIR_ENV, _DEFAULT_PROGRESS_DIR)
    )


def _write_progress(task_id: str, status: str, pid: Optional[int] = None,
                    description: str = "", started_at: Optional[float] = None) -> None:
    """Write a JSON progress file for a running worker.

    Files live in ORCHESTRATOR_PROGRESS_DIR/{task_id}.progress and are
    watched by scripts/watch-workers.sh.  Best-effort — never raises.
    """
    try:
        d = _progress_dir()
        Path(d).mkdir(parents=True, exist_ok=True)
        path = os.path.join(d, f"{task_id}.progress")
        data = {
            "task_id": task_id,
            "status": status,
            "pid": pid,
            "description": description[:200] if description else "",
            "started_at": started_at or time.time(),
            "updated_at": time.time(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.debug("Progress write failed for %s: %s", task_id, exc)


class ClaudeCliPlanner:
    """Planner that uses claude CLI for task selection and decomposition."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model = config.get("model", DEFAULT_PLANNER_MODEL)
        # Strip provider prefix if present (e.g. "claude-cli/opus" -> "opus")
        if "/" in self.model:
            self.model = self.model.split("/", 1)[1]
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT)
        # Planner doesn't use persistent sessions — each plan is stateless
        self.session_id = None
        self.resume = False

    async def plan(self, context: str, instruction: str) -> PlanResult:
        prompt = (
            f"You are a task planner. Given project state, decompose the task "
            f"into 1-3 independent parallel subtasks.\n\n"
            f"## Project State\n\n{context}\n\n"
            f"## Instruction\n\n{instruction}\n\n"
            f"For each subtask, estimate:\n"
            f"- estimated_seconds: how long a worker will need (120-3600)\n"
            f"- complexity: low (simple file edit), medium (new module), high (multi-file + iteration)\n\n"
            f"If a task requires human judgment or open-ended iteration, mark complexity 'high' "
            f"and set estimated_seconds to 3600. Prefer concrete, completable subtasks over open-ended ones.\n\n"
            f"Respond with ONLY valid JSON matching this schema:\n"
            f'{{"task_summary": "...", "reasoning": "...", "project_name": "...", '
            f'"milestone": "...", "subtasks": [{{"id": "...", "type": "code|test|docs|research", '
            f'"description": "...", "target_repo": "...", "context_files": [], '
            f'"constraints": "", "estimated_seconds": 300, "complexity": "medium"}}]}}'
        )

        raw = await _run_claude(prompt, self.model, self.timeout)
        data = _extract_json(raw)

        subtasks = [
            SubTask(
                id=st["id"],
                type=TaskType(st.get("type", "code")),
                description=st["description"],
                target_repo=st.get("target_repo", ""),
                context_files=st.get("context_files", []),
                constraints=st.get("constraints", ""),
                tools=st.get("tools", []),
                estimated_seconds=st.get("estimated_seconds", 0),
                complexity=st.get("complexity", "medium"),
            )
            for st in data.get("subtasks", [])
        ]

        return PlanResult(
            task_summary=data.get("task_summary", ""),
            subtasks=subtasks,
            reasoning=data.get("reasoning", ""),
            project_name=data.get("project_name", ""),
            milestone=data.get("milestone", ""),
        )

    async def select_task(self, context: str) -> str:
        prompt = (
            f"You are a task selector. Given these project states, identify "
            f"the single highest-value next task. Respond with just the task "
            f"description — no JSON, no explanation.\n\n{context}"
        )
        return await _run_claude(prompt, self.model, self.timeout)


class ClaudeCliWorker:
    """Worker that spawns claude CLI sessions with full tool access.

    Each worker runs in the target repo directory, so `claude -p` has
    access to the repo's files and can write code directly.
    Persona-typed tasks use persistent sessions; generic tasks start fresh.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model = config.get("model", DEFAULT_WORKER_MODEL)
        if "/" in self.model:
            self.model = self.model.split("/", 1)[1]
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT)
        self.session_id = config.get("session_id")
        self.resume = config.get("resume_session", True)

    async def execute(self, task: SubTask, system_prefix: str = "") -> WorkerResult:
        start = time.monotonic()
        started_at = time.time()

        # Build the prompt for the worker
        prompt_parts = []
        if system_prefix:
            prompt_parts.extend([system_prefix, ""])
        prompt_parts.extend([
            "You are executing a focused task. Do ONLY what is described below.",
            "",
            f"Task: {task.description}",
        ])

        if task.context_files:
            prompt_parts.append(f"Read these files first: {', '.join(task.context_files)}")

        if task.constraints:
            prompt_parts.append(f"Constraints: {task.constraints}")

        prompt_parts.extend([
            "",
            "Rules:",
            "- Implement only what is described. Nothing extra.",
            "- Write tests if the task involves code.",
            "- Do not modify files outside the scope of this task.",
            "",
            "When done, output a summary of what you did and which files you changed.",
        ])

        prompt = "\n".join(prompt_parts)

        # Run in the target repo directory if it exists
        cwd = task.target_repo if os.path.isdir(task.target_repo) else None

        # Use planner's estimate if available, otherwise fall back to config timeout
        timeout = task.estimated_seconds if task.estimated_seconds > 0 else self.timeout

        # Write initial progress marker (PID filled in by _run_claude)
        _write_progress(task.id, "starting", description=task.description, started_at=started_at)

        try:
            # Acquire a persona session (parallel-safe) or use a fresh one
            from .session_manager import get_session_manager
            sm = get_session_manager()
            session_args = {}
            persona = task.type.value if task.type else None
            acquired_session = None

            if sm and persona:
                acquired_session = sm.acquire_session(persona)
                session_args = {
                    "session_id": acquired_session.session_id,
                    "resume": acquired_session.resume,
                }
            elif self.session_id:
                session_args = {"session_id": self.session_id, "resume": self.resume}

            _emit(
                "claude.exec.start",
                task_id=task.id,
                model=self.model,
                timeout=timeout,
                cwd=cwd,
                description=task.description[:200],
            )

            raw_output = await _run_claude(
                prompt, self.model, timeout, cwd=cwd,
                task_id=task.id, started_at=started_at,
                description=task.description,
                **session_args,
            )

            # Track tokens and extract text from JSON
            output = raw_output
            if acquired_session and sm:
                sm.track_usage(persona or "", raw_output)
                sm.release_session(acquired_session)
                try:
                    parsed = json.loads(raw_output)
                    output = parsed.get("result", raw_output)
                except (json.JSONDecodeError, TypeError):
                    pass

            duration = int((time.monotonic() - start) * 1000)

            _emit(
                "claude.exec.done",
                task_id=task.id,
                status="done",
                duration_ms=duration,
            )

            _write_progress(task.id, "done", description=task.description, started_at=started_at)

            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.DONE,
                output=output,
                duration_ms=duration,
            )

        except asyncio.TimeoutError:
            duration = int((time.monotonic() - start) * 1000)
            _emit(
                "claude.exec.done",
                task_id=task.id,
                status="timeout",
                duration_ms=duration,
                error=f"Timeout after {timeout}s",
            )
            _write_progress(task.id, "timeout", description=task.description, started_at=started_at)
            if acquired_session and sm:
                sm.release_session(acquired_session)
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.ERROR,
                error=f"Timeout after {timeout}s (complexity: {task.complexity})",
                duration_ms=duration,
            )

        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("Claude CLI worker failed for task %s: %s", task.id, e)
            _emit(
                "claude.exec.done",
                task_id=task.id,
                status="error",
                duration_ms=duration,
                error=str(e)[:500],
            )
            _write_progress(task.id, "error", description=task.description, started_at=started_at)
            if acquired_session and sm:
                sm.release_session(acquired_session)
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.ERROR,
                error=str(e),
                duration_ms=duration,
            )


async def _run_claude(
    prompt: str,
    model: str = "sonnet",
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Optional[str] = None,
    task_id: Optional[str] = None,
    started_at: Optional[float] = None,
    description: str = "",
    session_id: Optional[str] = None,
    resume: bool = False,
) -> str:
    """Run a claude CLI command and return the output.

    Args:
        prompt: The prompt to send.
        model: Model name (opus, sonnet, haiku).
        timeout: Timeout in seconds.
        cwd: Working directory for the process.
        task_id: If given, write PID to progress file immediately after spawn.
        started_at: Start timestamp for the progress file.
        description: Short description for the progress file.
        session_id: If given, use a persistent session (pass --session-id).
        resume: If True and session_id is set, resume the session (pass --resume).

    Returns:
        The text output from claude. If json_output=True, returns raw JSON string.
    """
    claude_bin = _resolve_claude_bin()
    # Use JSON output when we have a session (need token tracking)
    output_format = "json" if session_id else "text"
    cmd = [
        claude_bin, "-p",
        "--model", model,
        "--output-format", output_format,
        "--dangerously-skip-permissions",
    ]

    if session_id:
        # Claude CLI semantics (updated):
        # - --resume <id>        → resume existing session by ID (no --session-id needed)
        # - --session-id <id>    → name the new session (cannot combine with --resume/--continue
        #                          unless --fork-session is also specified)
        # - --fork-session       → use with --resume to create a new session ID from resumed context
        session_exists = _session_exists(session_id)
        if resume and session_exists:
            # Resume existing session: pass ID as argument to --resume
            cmd.extend(["--resume", session_id])
        else:
            # Start fresh but name the session with a specific ID
            cmd.extend(["--session-id", session_id])

    cmd.append(prompt)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    # Write PID to progress file as soon as the process is alive
    if task_id:
        _write_progress(
            task_id, "running", pid=proc.pid,
            description=description, started_at=started_at,
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise

    if proc.returncode != 0:
        error_msg = _format_claude_error(proc.returncode or -1, stdout, stderr)

        # Transient-failure retry: one attempt with a backoff before propagating.
        # Only applies to exit-1 (not timeouts, which are handled above).
        if proc.returncode == 1 and _is_recoverable(stderr.decode("utf-8", errors="replace") if stderr else ""):
            delay = int(os.environ.get("CLAUDE_RETRY_DELAY_SECONDS", _DEFAULT_RETRY_DELAY))
            logger.warning(
                "Transient claude failure detected (exit 1). Retrying in %ds. Error: %s",
                delay, error_msg[:200],
            )
            await asyncio.sleep(delay)
            retry_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                retry_stdout, retry_stderr = await asyncio.wait_for(
                    retry_proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                retry_proc.kill()
                await retry_proc.communicate()
                raise
            if retry_proc.returncode == 0:
                return retry_stdout.decode("utf-8", errors="replace").strip()
            # Retry failed — overwrite stdout/stderr/error_msg so the rest of the
            # error-handling path sees the final attempt's output.
            stdout, stderr = retry_stdout, retry_stderr
            error_msg = _format_claude_error(retry_proc.returncode or 1, stdout, stderr)
            logger.error("Retry also failed. Propagating error: %s", error_msg[:200])

        # Handle "No conversation found" — session exists in state file but not in Claude's storage
        # This happens with stale session state. Retry as a fresh session with the same ID.
        if "No conversation found" in error_msg and session_id:
            logger.warning("Session %s not found in Claude storage, starting fresh", session_id[:8])
            fresh_cmd = [
                claude_bin, "-p",
                "--model", model,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                "--session-id", session_id,
                prompt,
            ]
            fresh_proc = await asyncio.create_subprocess_exec(
                *fresh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    fresh_proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                fresh_proc.kill()
                await fresh_proc.communicate()
                raise
            if fresh_proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace").strip()
            error_msg = _format_claude_error(fresh_proc.returncode or -1, stdout, stderr)
            raise RuntimeError(f"Claude CLI exited with code {fresh_proc.returncode}: {error_msg}")

        # Handle "session already in use" — retry with --continue instead of --resume
        if "already in use" in error_msg and session_id and resume:
            logger.warning("Session %s in use, retrying with --continue", session_id[:8])
            retry_cmd = [
                claude_bin, "-p",
                "--model", model,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                "--continue",
                prompt,
            ]
            retry_proc = await asyncio.create_subprocess_exec(
                *retry_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    retry_proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                retry_proc.kill()
                await retry_proc.communicate()
                raise
            if retry_proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace").strip()
            # If --continue also fails, fall through to fresh session
            logger.warning("--continue also failed, starting fresh session")
            fresh_cmd = [
                claude_bin, "-p",
                "--model", model,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                prompt,
            ]
            fresh_proc = await asyncio.create_subprocess_exec(
                *fresh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    fresh_proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                fresh_proc.kill()
                await fresh_proc.communicate()
                raise
            if fresh_proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace").strip()

        raise RuntimeError(
            f"claude CLI exited with code {proc.returncode}: {error_msg[:500]}"
        )

    return stdout.decode("utf-8", errors="replace").strip()


def _extract_json(text: str) -> dict:
    """Thin wrapper — delegates to the shared extract_json helper."""
    from ._json_utils import extract_json
    return extract_json(text)
