"""Tests for worker pool — parallel execution, concurrency, error handling."""

import asyncio
import time

import pytest

from providers.base import SubTask, TaskStatus, TaskType, WorkerResult
from workers.worker_pool import PoolResult, WorkerPool, run_pool


# --- Fixtures ---


def _task(id: str, type: TaskType = TaskType.CODE) -> SubTask:
    return SubTask(id=id, type=type, description=f"do {id}", target_repo="/tmp")


class InstantWorker:
    """Worker that completes immediately."""

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, task: SubTask) -> WorkerResult:
        self.executed.append(task.id)
        return WorkerResult(
            task_id=task.id,
            status=TaskStatus.DONE,
            files_changed=[f"{task.id}.py"],
        )


class SlowWorker:
    """Worker that takes a configurable delay."""

    def __init__(self, delay: float = 0.05):
        self._delay = delay
        self.concurrent_count = 0
        self.max_concurrent = 0

    async def execute(self, task: SubTask) -> WorkerResult:
        self.concurrent_count += 1
        if self.concurrent_count > self.max_concurrent:
            self.max_concurrent = self.concurrent_count
        await asyncio.sleep(self._delay)
        self.concurrent_count -= 1
        return WorkerResult(task_id=task.id, status=TaskStatus.DONE)


class FailingWorker:
    """Worker that fails on specific task IDs."""

    def __init__(self, fail_ids: set[str]):
        self._fail_ids = fail_ids

    async def execute(self, task: SubTask) -> WorkerResult:
        if task.id in self._fail_ids:
            raise RuntimeError(f"Worker crashed on {task.id}")
        return WorkerResult(task_id=task.id, status=TaskStatus.DONE)


class MixedWorker:
    """Worker that returns different statuses per task."""

    def __init__(self, blocked_ids: set[str] = set(), error_ids: set[str] = set()):
        self._blocked = blocked_ids
        self._errors = error_ids

    async def execute(self, task: SubTask) -> WorkerResult:
        if task.id in self._errors:
            return WorkerResult(task_id=task.id, status=TaskStatus.ERROR, error="failed")
        if task.id in self._blocked:
            return WorkerResult(task_id=task.id, status=TaskStatus.BLOCKED, notes="blocked")
        return WorkerResult(
            task_id=task.id, status=TaskStatus.DONE, files_changed=[f"{task.id}.py"]
        )


# --- PoolResult ---


def test_pool_result_empty():
    pr = PoolResult()
    assert pr.total == 0
    assert pr.succeeded == 0
    assert pr.all_succeeded is False
    assert pr.all_files_changed == []


def test_pool_result_all_done():
    results = [
        WorkerResult(task_id="a", status=TaskStatus.DONE, files_changed=["a.py"]),
        WorkerResult(task_id="b", status=TaskStatus.DONE, files_changed=["b.py"]),
    ]
    pr = PoolResult(results=results)
    assert pr.total == 2
    assert pr.succeeded == 2
    assert pr.failed == 0
    assert pr.blocked == 0
    assert pr.all_succeeded is True
    assert pr.all_files_changed == ["a.py", "b.py"]


def test_pool_result_mixed():
    results = [
        WorkerResult(task_id="a", status=TaskStatus.DONE),
        WorkerResult(task_id="b", status=TaskStatus.ERROR, error="boom"),
        WorkerResult(task_id="c", status=TaskStatus.BLOCKED),
    ]
    pr = PoolResult(results=results)
    assert pr.succeeded == 1
    assert pr.failed == 1
    assert pr.blocked == 1
    assert pr.all_succeeded is False


def test_pool_result_deduplicates_files():
    results = [
        WorkerResult(task_id="a", status=TaskStatus.DONE, files_changed=["shared.py", "a.py"]),
        WorkerResult(task_id="b", status=TaskStatus.DONE, files_changed=["shared.py", "b.py"]),
    ]
    pr = PoolResult(results=results)
    assert pr.all_files_changed == ["shared.py", "a.py", "b.py"]


# --- WorkerPool execution ---


@pytest.mark.asyncio
async def test_pool_executes_all_tasks():
    worker = InstantWorker()
    tasks = [_task("t1"), _task("t2"), _task("t3")]
    result = await run_pool(tasks, worker)
    assert result.total == 3
    assert result.all_succeeded
    assert set(worker.executed) == {"t1", "t2", "t3"}


@pytest.mark.asyncio
async def test_pool_empty_tasks():
    worker = InstantWorker()
    result = await run_pool([], worker)
    assert result.total == 0


@pytest.mark.asyncio
async def test_pool_single_task():
    worker = InstantWorker()
    result = await run_pool([_task("solo")], worker)
    assert result.total == 1
    assert result.results[0].task_id == "solo"


@pytest.mark.asyncio
async def test_pool_respects_max_parallel():
    worker = SlowWorker(delay=0.05)
    tasks = [_task(f"t{i}") for i in range(6)]
    result = await run_pool(tasks, worker, max_parallel=2)
    assert result.total == 6
    assert result.all_succeeded
    assert worker.max_concurrent <= 2


@pytest.mark.asyncio
async def test_pool_catches_worker_exceptions():
    worker = FailingWorker(fail_ids={"t2"})
    tasks = [_task("t1"), _task("t2"), _task("t3")]
    result = await run_pool(tasks, worker)
    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1
    # Error result has the error message
    error_result = [r for r in result.results if r.task_id == "t2"][0]
    assert error_result.status == TaskStatus.ERROR
    assert "crashed" in error_result.error


@pytest.mark.asyncio
async def test_pool_preserves_task_order():
    worker = InstantWorker()
    tasks = [_task("c"), _task("a"), _task("b")]
    result = await run_pool(tasks, worker)
    assert [r.task_id for r in result.results] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_pool_mixed_statuses():
    worker = MixedWorker(blocked_ids={"t2"}, error_ids={"t3"})
    tasks = [_task("t1"), _task("t2"), _task("t3")]
    result = await run_pool(tasks, worker)
    assert result.succeeded == 1
    assert result.blocked == 1
    assert result.failed == 1


@pytest.mark.asyncio
async def test_pool_runs_in_parallel():
    """Verify tasks actually run concurrently, not sequentially."""
    worker = SlowWorker(delay=0.05)
    tasks = [_task(f"t{i}") for i in range(3)]
    start = time.monotonic()
    result = await run_pool(tasks, worker, max_parallel=3)
    elapsed = time.monotonic() - start
    assert result.all_succeeded
    # 3 tasks at 50ms each should take ~50ms parallel, not ~150ms sequential
    assert elapsed < 0.12  # generous margin


# --- Integration: decompose → pool ---


@pytest.mark.asyncio
async def test_integration_decompose_to_pool():
    """End-to-end: decompose a task into subtasks, run through pool."""
    from planner.project_scanner import Milestone, ProjectState, Task, LogEntry
    from planner.task_decomposer import DecompositionContext, decompose_task
    from planner.task_selector import SelectedTask
    from providers.base import PlanResult

    # Fake planner that returns a 2-subtask plan
    class FakePlanner:
        async def plan(self, context, instruction):
            return PlanResult(
                task_summary="build foo",
                subtasks=[
                    SubTask(id="code-1", type=TaskType.CODE, description="write foo.py", target_repo="/tmp"),
                    SubTask(id="test-1", type=TaskType.TEST, description="test foo.py", target_repo="/tmp"),
                ],
            )
        async def select_task(self, context):
            return "foo"

    # Decompose
    selected = SelectedTask(
        project_name="test",
        milestone_number=1,
        milestone_title="Foundation",
        task_description="Write foo module",
    )
    state = ProjectState(
        name="test",
        path="/tmp/test",
        milestones=[Milestone(number=1, title="Foundation", tasks=[
            Task(description="Write foo module", done=False),
        ])],
    )
    ctx = DecompositionContext(selected=selected, project_state=state)
    plan = await decompose_task(ctx, FakePlanner())

    # Execute through pool
    worker = InstantWorker()
    pool_result = await run_pool(plan.subtasks, worker)

    assert pool_result.total == 2
    assert pool_result.all_succeeded
    assert set(worker.executed) == {"code-1", "test-1"}
