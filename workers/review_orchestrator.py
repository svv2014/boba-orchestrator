"""Review loop orchestrator — runs Reviewer after Coder and retries on CHANGES_REQUESTED.

Flow:
  1. Build reviewer SubTask from original spec + coder output + git diff
  2. Parse APPROVED / CHANGES_REQUESTED / ESCALATE from reviewer output
  3. On CHANGES_REQUESTED: build a fix SubTask and run coder again, then re-review
  4. On ESCALATE or max retries exceeded: return ReviewOutcome(status='escalated')
  5. On APPROVED: return ReviewOutcome(status='approved')
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from providers.base import SubTask, TaskStatus, TaskType, WorkerResult

try:
    from providers.persona_registry import get_persona_config as _get_persona_config
except ImportError:
    def _get_persona_config(persona: str) -> dict:  # type: ignore[misc]
        return {"system_prefix": ""}

logger = logging.getLogger(__name__)

_APPROVED = "APPROVED"
_CHANGES = "CHANGES_REQUESTED"
_ESCALATE = "ESCALATE"


@dataclass
class ReviewOutcome:
    """Result of the review loop for a single coder task."""

    status: str              # 'approved' | 'changes_requested' | 'escalated'
    review_output: str       # last reviewer response
    retry_count: int         # how many fix attempts were made
    final_result: Optional[WorkerResult]  # last coder WorkerResult


async def get_git_diff(repo_path: str) -> str:
    """Run `git -C repo_path diff HEAD~1..HEAD`, truncated to 4000 chars."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "diff", "HEAD~1..HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        diff = stdout.decode("utf-8", errors="replace").strip()
        if len(diff) > 4000:
            diff = diff[:4000] + "\n... [truncated]"
        return diff or "[empty diff — no changes committed yet]"
    except Exception as exc:
        logger.warning("get_git_diff failed for %s: %s", repo_path, exc)
        return f"[git diff unavailable: {exc}]"


def _parse_verdict(output: str) -> str:
    """Return APPROVED / CHANGES_REQUESTED / ESCALATE from reviewer text."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(_APPROVED):
            return _APPROVED
        if stripped.startswith(_CHANGES):
            return _CHANGES
        if stripped.startswith(_ESCALATE):
            return _ESCALATE
    # Fallback: search anywhere (case-insensitive for robustness)
    upper = output.upper()
    if _APPROVED in upper:
        return _APPROVED
    if _CHANGES in upper:
        return _CHANGES
    return _ESCALATE


async def _execute(worker, task: SubTask) -> WorkerResult:
    """Execute task, injecting persona system_prefix when the worker supports it."""
    persona = task.persona or "coder"
    system_prefix = _get_persona_config(persona).get("system_prefix", "")
    try:
        return await worker.execute(task, system_prefix=system_prefix)
    except TypeError:
        return await worker.execute(task)


class ReviewOrchestrator:
    """Run a reviewer loop after a coder completes work.

    Args:
        worker_backend: Any object with an async execute(task) method.
        max_retries: Max coder fix attempts before escalating (default 2).
    """

    def __init__(self, worker_backend, max_retries: int = 2) -> None:
        self._worker = worker_backend
        self.max_retries = max_retries

    async def review_and_fix(
        self,
        original_task: SubTask,
        coder_result: WorkerResult,
        repo_path: str,
    ) -> ReviewOutcome:
        """Run the review loop starting from an already-completed coder result.

        Args:
            original_task: The SubTask the coder executed (used for spec context).
            coder_result: WorkerResult from the coder.
            repo_path: Repo directory for git diff.

        Returns:
            ReviewOutcome with final status and the last coder WorkerResult.
        """
        current_result = coder_result
        retry_count = 0

        while True:
            diff = await get_git_diff(repo_path)

            reviewer_task = SubTask(
                id=f"{original_task.id}-review-{retry_count}",
                type=TaskType.REVIEW,
                description=(
                    "Review the following completed work.\n\n"
                    f"## Original Specification\n{original_task.description}\n\n"
                    f"## Coder Output Summary\n{current_result.output}\n\n"
                    f"## Git Diff\n```diff\n{diff}\n```\n\n"
                    "Respond with APPROVED, CHANGES_REQUESTED, or ESCALATE followed by reasoning."
                ),
                target_repo=repo_path,
                persona="reviewer",
                original_spec=original_task.description,
                retry_count=retry_count,
            )

            review_result = await _execute(self._worker, reviewer_task)

            if review_result.status == TaskStatus.ERROR:
                return ReviewOutcome(
                    status="escalated",
                    review_output=review_result.error or "reviewer task failed",
                    retry_count=retry_count,
                    final_result=current_result,
                )

            verdict = _parse_verdict(review_result.output)

            if verdict == _APPROVED:
                return ReviewOutcome(
                    status="approved",
                    review_output=review_result.output,
                    retry_count=retry_count,
                    final_result=current_result,
                )

            if verdict == _ESCALATE:
                return ReviewOutcome(
                    status="escalated",
                    review_output=review_result.output,
                    retry_count=retry_count,
                    final_result=current_result,
                )

            # CHANGES_REQUESTED — check retry budget before attempting fix
            if retry_count >= self.max_retries:
                return ReviewOutcome(
                    status="escalated",
                    review_output=review_result.output,
                    retry_count=retry_count,
                    final_result=current_result,
                )

            retry_count += 1
            fix_task = SubTask(
                id=f"{original_task.id}-fix-{retry_count}",
                type=original_task.type,
                description=(
                    f"Fix the issues identified by the reviewer.\n\n"
                    f"## Original Specification\n{original_task.description}\n\n"
                    f"## Reviewer Feedback\n{review_result.output}\n\n"
                    f"Fix ONLY the issues listed. This is retry {retry_count}/{self.max_retries}.\n"
                    "When done, your output will be reviewed again."
                ),
                target_repo=repo_path,
                persona="coder",
                original_spec=original_task.description,
                retry_count=retry_count,
                context_files=original_task.context_files,
                constraints=original_task.constraints,
            )

            current_result = await _execute(self._worker, fix_task)

            if current_result.status == TaskStatus.ERROR:
                return ReviewOutcome(
                    status="escalated",
                    review_output=(
                        f"Coder fix attempt {retry_count} failed: {current_result.error}"
                    ),
                    retry_count=retry_count,
                    final_result=current_result,
                )
