"""Structured JSON logger for orchestrator operations.

Wraps planner and worker backends transparently — every call gets logged
with timing, model, status, and result metadata. No config needed.

Usage:
    from providers.logger import logged_planner, logged_worker

    planner = logged_planner(AnthropicPlanner(config))
    worker = logged_worker(AnthropicWorker(config))

    # Use exactly as before — logging is automatic
    result = await planner.plan(context, instruction)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from .base import PlannerBackend, PlanResult, SubTask, WorkerBackend, WorkerResult

# Structured JSON logger — writes to stderr, doesn't interfere with stdout output
_logger = logging.getLogger("orchestrator")

if not _logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def _log(event: str, **fields: Any) -> None:
    """Emit a structured JSON log line."""
    entry = {"event": event, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    _logger.info(json.dumps(entry, default=str))


class LoggedPlanner:
    """Transparent logging wrapper for any PlannerBackend."""

    def __init__(self, inner: PlannerBackend, model: str = "unknown") -> None:
        self._inner = inner
        self._model = model

    async def plan(self, context: str, instruction: str) -> PlanResult:
        start = time.monotonic()
        _log("planner.plan.start", model=self._model, instruction_len=len(instruction))

        try:
            result = await self._inner.plan(context, instruction)
            duration_ms = int((time.monotonic() - start) * 1000)
            _log(
                "planner.plan.done",
                model=self._model,
                duration_ms=duration_ms,
                subtask_count=len(result.subtasks),
                project=result.project_name,
                milestone=result.milestone,
            )
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            _log(
                "planner.plan.error",
                model=self._model,
                duration_ms=duration_ms,
                error=str(e),
            )
            raise

    async def select_task(self, context: str) -> str:
        start = time.monotonic()
        _log("planner.select.start", model=self._model)

        try:
            result = await self._inner.select_task(context)
            duration_ms = int((time.monotonic() - start) * 1000)
            _log(
                "planner.select.done",
                model=self._model,
                duration_ms=duration_ms,
                selected=result[:100],
            )
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            _log(
                "planner.select.error",
                model=self._model,
                duration_ms=duration_ms,
                error=str(e),
            )
            raise


class LoggedWorker:
    """Transparent logging wrapper for any WorkerBackend."""

    def __init__(self, inner: WorkerBackend, model: str = "unknown") -> None:
        self._inner = inner
        self._model = model

    async def execute(self, task: SubTask) -> WorkerResult:
        start = time.monotonic()
        _log(
            "worker.execute.start",
            model=self._model,
            task_id=task.id,
            task_type=task.type.value,
        )

        try:
            result = await self._inner.execute(task)
            duration_ms = int((time.monotonic() - start) * 1000)

            _log(
                "worker.execute.done",
                model=self._model,
                task_id=result.task_id,
                status=result.status.value,
                duration_ms=duration_ms,
                files_changed=len(result.files_changed),
                error=result.error,
            )
            return result
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            _log(
                "worker.execute.error",
                model=self._model,
                task_id=task.id,
                duration_ms=duration_ms,
                error=str(e),
            )
            raise


def logged_planner(inner: PlannerBackend, model: str = "unknown") -> LoggedPlanner:
    """Wrap a planner backend with structured logging."""
    return LoggedPlanner(inner, model)


def logged_worker(inner: WorkerBackend, model: str = "unknown") -> LoggedWorker:
    """Wrap a worker backend with structured logging."""
    return LoggedWorker(inner, model)
