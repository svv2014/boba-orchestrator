"""Async worker pool — dispatches subtasks in parallel via WorkerBackend.

Respects max_parallel from config using an asyncio.Semaphore.
Collects all WorkerResults, including errors — never drops a result.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from providers.base import SubTask, WorkerBackend, WorkerResult, TaskStatus

try:
    from providers.persona_registry import get_persona_config
except ImportError:
    def get_persona_config(persona: str) -> dict:  # type: ignore[misc]
        return {"system_prefix": ""}

logger = logging.getLogger(__name__)


def _resolve_signal_script() -> str:
    script = os.environ.get("BOBA_NOTIFY_SCRIPT")
    if script:
        return script
    legacy = os.environ.get("SIGNAL_NOTIFY_SCRIPT")
    if legacy:
        logger.warning(
            "SIGNAL_NOTIFY_SCRIPT is deprecated; use BOBA_NOTIFY_SCRIPT instead."
        )
        return legacy
    return ""


_SIGNAL_SCRIPT = _resolve_signal_script()
_SIGNAL_SKIP_WARNED = False


@dataclass
class PoolResult:
    """Aggregated results from a worker pool execution."""

    results: list[WorkerResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.ERROR)

    @property
    def blocked(self) -> int:
        return sum(1 for r in self.results if r.status == TaskStatus.BLOCKED)

    @property
    def all_succeeded(self) -> bool:
        return self.total > 0 and self.succeeded == self.total

    @property
    def all_files_changed(self) -> list[str]:
        """Deduplicated list of all files changed across workers."""
        seen: set[str] = set()
        files: list[str] = []
        for r in self.results:
            for f in r.files_changed:
                if f not in seen:
                    seen.add(f)
                    files.append(f)
        return files


async def _send_signal(message: str) -> None:
    """Fire-and-forget Signal notification via shell script."""
    global _SIGNAL_SKIP_WARNED
    if not _SIGNAL_SCRIPT:
        if not _SIGNAL_SKIP_WARNED:
            logger.warning(
                "BOBA_NOTIFY_SCRIPT not set; skipping escalation notification."
            )
            _SIGNAL_SKIP_WARNED = True
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            _SIGNAL_SCRIPT, message,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as exc:
        logger.warning("Signal notification failed: %s", exc)


class WorkerPool:
    """Dispatches subtasks to workers in parallel with concurrency control.

    Args:
        worker: WorkerBackend to execute subtasks.
        max_parallel: Maximum concurrent workers (default 3).
        review_orchestrator: Optional ReviewOrchestrator; when set, coder tasks
            are automatically reviewed and retried before returning.
    """

    def __init__(
        self,
        worker: WorkerBackend,
        max_parallel: int = 3,
        review_orchestrator=None,
    ) -> None:
        self._worker = worker
        self._max_parallel = max_parallel
        self._review_orchestrator = review_orchestrator

    async def execute(self, tasks: list[SubTask]) -> PoolResult:
        """Execute all subtasks in parallel, respecting concurrency limit.

        Never raises — individual worker errors are captured in WorkerResult.
        Returns PoolResult with all results in original task order.
        """
        if not tasks:
            return PoolResult()

        semaphore = asyncio.Semaphore(self._max_parallel)

        async def _run_one(task: SubTask) -> WorkerResult:
            async with semaphore:
                try:
                    persona = getattr(task, "persona", "coder") or "coder"
                    system_prefix = get_persona_config(persona).get("system_prefix", "")
                    result = await self._worker.execute(task, system_prefix=system_prefix)

                    if (
                        self._review_orchestrator is not None
                        and persona == "coder"
                        and result.status == TaskStatus.DONE
                    ):
                        outcome = await self._review_orchestrator.review_and_fix(
                            task, result, task.target_repo
                        )
                        if outcome.status == "escalated":
                            logger.warning(
                                "Task %s escalated after %d retries: %s",
                                task.id, outcome.retry_count, outcome.review_output[:200],
                            )
                            asyncio.ensure_future(
                                _send_signal(
                                    f"ESCALATED: Task {task.id} needs human review "
                                    f"after {outcome.retry_count} retries.\n"
                                    f"Spec: {task.description[:200]}\n"
                                    f"Reviewer: {outcome.review_output[:400]}"
                                )
                            )
                        result = outcome.final_result or result

                    return result
                except Exception as e:
                    return WorkerResult(
                        task_id=task.id,
                        status=TaskStatus.ERROR,
                        error=str(e),
                    )

        results = await asyncio.gather(*[_run_one(t) for t in tasks])
        return PoolResult(results=list(results))


async def run_pool(
    tasks: list[SubTask],
    worker: WorkerBackend,
    max_parallel: int = 3,
    review_orchestrator=None,
) -> PoolResult:
    """Convenience function to run a worker pool.

    Args:
        tasks: SubTasks to execute.
        worker: WorkerBackend implementation.
        max_parallel: Max concurrent workers.

    Returns:
        PoolResult with all worker results.
    """
    pool = WorkerPool(worker, max_parallel=max_parallel, review_orchestrator=review_orchestrator)
    return await pool.execute(tasks)
