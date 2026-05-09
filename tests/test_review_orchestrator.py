"""Tests for ReviewOrchestrator — review loop, retry, and escalation."""

from unittest.mock import AsyncMock, patch

import pytest

from providers.base import SubTask, TaskStatus, TaskType, WorkerResult
from workers.review_orchestrator import ReviewOrchestrator, ReviewOutcome


# --- Helpers ---


def _coder_task(id: str = "task-1") -> SubTask:
    return SubTask(
        id=id,
        type=TaskType.CODE,
        description="implement foo module",
        target_repo="/tmp/repo",
        persona="coder",
    )


def _done(task_id: str, output: str = "done") -> WorkerResult:
    return WorkerResult(task_id=task_id, status=TaskStatus.DONE, output=output)


class ScriptedWorker:
    """Returns pre-scripted WorkerResults in order, distinguished by call index."""

    def __init__(self, responses: list[WorkerResult]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def execute(self, task: SubTask, **kwargs) -> WorkerResult:
        result = self._responses[self._index]
        self._index += 1
        return result


# --- Tests ---


@pytest.mark.asyncio
async def test_approved_on_first_review():
    """Reviewer approves immediately — no retry, final_result is the original coder result."""
    coder_result = _done("task-1", "wrote foo.py")
    worker = ScriptedWorker([
        _done("task-1-review-0", "APPROVED\nReasoning: implementation is correct"),
    ])
    orchestrator = ReviewOrchestrator(worker, max_retries=2)

    with patch(
        "workers.review_orchestrator.get_git_diff",
        new=AsyncMock(return_value="+ def foo(): pass"),
    ):
        outcome = await orchestrator.review_and_fix(_coder_task(), coder_result, "/tmp/repo")

    assert outcome.status == "approved"
    assert outcome.retry_count == 0
    assert outcome.final_result is coder_result
    assert "APPROVED" in outcome.review_output


@pytest.mark.asyncio
async def test_fix_and_approve_on_retry():
    """Reviewer requests changes once; coder fixes; reviewer approves on second pass."""
    coder_result = _done("task-1", "initial implementation")
    fix_result = _done("task-1-fix-1", "fixed implementation with docstring")

    worker = ScriptedWorker([
        _done("task-1-review-0", "CHANGES_REQUESTED\nIssues:\n1. Missing docstring"),
        fix_result,
        _done("task-1-review-1", "APPROVED\nReasoning: docstring added"),
    ])
    orchestrator = ReviewOrchestrator(worker, max_retries=2)

    with patch(
        "workers.review_orchestrator.get_git_diff",
        new=AsyncMock(return_value="+ def foo():\n+     '''Does foo.'''"),
    ):
        outcome = await orchestrator.review_and_fix(_coder_task(), coder_result, "/tmp/repo")

    assert outcome.status == "approved"
    assert outcome.retry_count == 1
    assert outcome.final_result is fix_result
    assert "APPROVED" in outcome.review_output


@pytest.mark.asyncio
async def test_escalate_after_max_retries():
    """Reviewer keeps requesting changes; escalates when max_retries reached."""
    coder_result = _done("task-1", "initial")

    # max_retries=1: one fix attempt allowed; second CHANGES_REQUESTED → escalate
    worker = ScriptedWorker([
        _done("task-1-review-0", "CHANGES_REQUESTED\nIssues:\n1. No tests"),
        _done("task-1-fix-1", "attempted fix"),
        _done("task-1-review-1", "CHANGES_REQUESTED\nIssues:\n1. Tests still missing"),
    ])
    orchestrator = ReviewOrchestrator(worker, max_retries=1)

    with patch(
        "workers.review_orchestrator.get_git_diff",
        new=AsyncMock(return_value="+ x = 1"),
    ):
        outcome = await orchestrator.review_and_fix(_coder_task(), coder_result, "/tmp/repo")

    assert outcome.status == "escalated"
    assert outcome.retry_count == 1


@pytest.mark.asyncio
async def test_escalate_on_reviewer_escalate_verdict():
    """Reviewer outputs ESCALATE directly — no retries attempted."""
    coder_result = _done("task-1", "implementation")
    worker = ScriptedWorker([
        _done("task-1-review-0", "ESCALATE\nThis requires architectural rethinking"),
    ])
    orchestrator = ReviewOrchestrator(worker, max_retries=2)

    with patch(
        "workers.review_orchestrator.get_git_diff",
        new=AsyncMock(return_value=""),
    ):
        outcome = await orchestrator.review_and_fix(_coder_task(), coder_result, "/tmp/repo")

    assert outcome.status == "escalated"
    assert outcome.retry_count == 0


@pytest.mark.asyncio
async def test_escalate_on_reviewer_error():
    """Reviewer task returns ERROR status — escalate immediately."""
    coder_result = _done("task-1", "implementation")
    worker = ScriptedWorker([
        WorkerResult(task_id="task-1-review-0", status=TaskStatus.ERROR, error="timeout"),
    ])
    orchestrator = ReviewOrchestrator(worker, max_retries=2)

    with patch(
        "workers.review_orchestrator.get_git_diff",
        new=AsyncMock(return_value=""),
    ):
        outcome = await orchestrator.review_and_fix(_coder_task(), coder_result, "/tmp/repo")

    assert outcome.status == "escalated"
    assert "timeout" in outcome.review_output
