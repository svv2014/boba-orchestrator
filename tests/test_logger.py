"""Tests for the structured logging wrapper."""

import json

import pytest

from providers.base import PlanResult, SubTask, TaskStatus, TaskType, WorkerResult
from providers.logger import LoggedPlanner, LoggedWorker, logged_planner, logged_worker


class FakePlanner:
    def __init__(self):
        self.called = False

    async def plan(self, context, instruction):
        self.called = True
        return PlanResult(
            task_summary="test task",
            subtasks=[SubTask(id="s1", type=TaskType.CODE, description="do thing", target_repo="/tmp")],
            project_name="test-proj",
        )

    async def select_task(self, context):
        return "selected task"


class FakeWorker:
    async def execute(self, task):
        return WorkerResult(task_id=task.id, status=TaskStatus.DONE, files_changed=["a.py"])


class FailingPlanner:
    async def plan(self, context, instruction):
        raise RuntimeError("API down")

    async def select_task(self, context):
        raise RuntimeError("API down")


@pytest.mark.asyncio
async def test_logged_planner_passes_through():
    inner = FakePlanner()
    planner = logged_planner(inner, model="test-opus")
    result = await planner.plan("context", "instruction")
    assert inner.called
    assert result.task_summary == "test task"
    assert len(result.subtasks) == 1


@pytest.mark.asyncio
async def test_logged_planner_select():
    inner = FakePlanner()
    planner = logged_planner(inner, model="test-opus")
    result = await planner.select_task("context")
    assert result == "selected task"


@pytest.mark.asyncio
async def test_logged_worker_passes_through():
    inner = FakeWorker()
    worker = logged_worker(inner, model="test-sonnet")
    task = SubTask(id="w1", type=TaskType.TEST, description="test thing", target_repo="/tmp")
    result = await worker.execute(task)
    assert result.task_id == "w1"
    assert result.status == TaskStatus.DONE


@pytest.mark.asyncio
async def test_logged_planner_error_propagates():
    inner = FailingPlanner()
    planner = logged_planner(inner, model="test-opus")
    with pytest.raises(RuntimeError, match="API down"):
        await planner.plan("ctx", "instr")


@pytest.mark.asyncio
async def test_logged_planner_emits_structured_logs(caplog):
    inner = FakePlanner()
    planner = logged_planner(inner, model="test-opus")
    with caplog.at_level("INFO", logger="orchestrator"):
        await planner.plan("ctx", "instr")
    # Verify structured JSON log lines were emitted
    assert len(caplog.records) >= 2
    for record in caplog.records:
        data = json.loads(record.message)
        assert "event" in data
        assert "ts" in data
    events = [json.loads(r.message)["event"] for r in caplog.records]
    assert "planner.plan.start" in events
    assert "planner.plan.done" in events
