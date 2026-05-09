"""Tests for task decomposer — validation, prompt building, and decomposition."""

import pytest

from planner.project_scanner import Milestone, ProjectState, Task
from planner.task_decomposer import (
    DecompositionContext,
    DecompositionError,
    build_decomposition_prompt,
    build_project_context,
    decompose_task,
    validate_plan,
)
from planner.task_selector import SelectedTask
from providers.base import PlanResult, SubTask, TaskStatus, TaskType


# --- Fixtures ---


def _make_state(name="test-proj", tasks=None, log_date="2026-03-27"):
    if tasks is None:
        tasks = [
            Task(description="done task", done=True, date_completed="2026-03-25"),
            Task(description="next task to do", done=False),
            Task(description="future task", done=False),
        ]
    ms = Milestone(number=1, title="Foundation", tasks=tasks)
    from planner.project_scanner import LogEntry

    log = [LogEntry(date=log_date, agent="Boba", description="did stuff")]
    return ProjectState(name=name, path=f"/tmp/{name}", milestones=[ms], log=log)


def _make_selected(project_name="test-proj"):
    return SelectedTask(
        project_name=project_name,
        milestone_number=1,
        milestone_title="Foundation",
        task_description="Write foo.py — implementation of the foo module",
    )


def _make_ctx(project_name="test-proj"):
    return DecompositionContext(
        selected=_make_selected(project_name),
        project_state=_make_state(project_name),
    )


def _make_valid_plan(num_subtasks=2):
    subtasks = [
        SubTask(
            id=f"task-{i+1}",
            type=TaskType.CODE if i == 0 else TaskType.TEST,
            description=f"Subtask {i+1} description",
            target_repo="/tmp/test-proj",
        )
        for i in range(num_subtasks)
    ]
    return PlanResult(
        task_summary="Build the foo module",
        subtasks=subtasks,
        reasoning="Split into implementation and tests",
        project_name="test-proj",
        milestone="M1",
    )


# --- validate_plan ---


def test_validate_valid_plan():
    plan = _make_valid_plan()
    errors = validate_plan(plan)
    assert errors == []


def test_validate_single_subtask():
    plan = _make_valid_plan(num_subtasks=1)
    errors = validate_plan(plan)
    assert errors == []


def test_validate_empty_subtasks():
    plan = PlanResult(task_summary="empty", subtasks=[])
    errors = validate_plan(plan)
    assert len(errors) == 1
    assert "no subtasks" in errors[0].lower()


def test_validate_too_many_subtasks():
    plan = _make_valid_plan(num_subtasks=6)
    errors = validate_plan(plan)
    assert any("max 5" in e for e in errors)


def test_validate_missing_id():
    plan = _make_valid_plan()
    plan.subtasks[0].id = ""
    errors = validate_plan(plan)
    assert any("missing id" in e for e in errors)


def test_validate_duplicate_id():
    plan = _make_valid_plan()
    plan.subtasks[1].id = plan.subtasks[0].id
    errors = validate_plan(plan)
    assert any("duplicate id" in e for e in errors)


def test_validate_missing_description():
    plan = _make_valid_plan()
    plan.subtasks[0].description = ""
    errors = validate_plan(plan)
    assert any("missing description" in e for e in errors)


def test_validate_missing_target_repo():
    plan = _make_valid_plan()
    plan.subtasks[0].target_repo = ""
    errors = validate_plan(plan)
    assert any("missing target_repo" in e for e in errors)


def test_validate_multiple_errors():
    plan = _make_valid_plan()
    plan.subtasks[0].id = ""
    plan.subtasks[0].description = ""
    errors = validate_plan(plan)
    assert len(errors) >= 2


# --- build_decomposition_prompt ---


def test_prompt_contains_task_info():
    ctx = _make_ctx()
    prompt = build_decomposition_prompt(ctx)
    assert "test-proj" in prompt
    assert "Foundation" in prompt
    assert "foo.py" in prompt


def test_prompt_contains_rules():
    ctx = _make_ctx()
    prompt = build_decomposition_prompt(ctx)
    assert "independent" in prompt.lower()
    assert "unique id" in prompt.lower()


def test_prompt_includes_context_hint():
    ctx = _make_ctx()
    ctx.context_hint = "This module handles authentication"
    prompt = build_decomposition_prompt(ctx)
    assert "authentication" in prompt


# --- build_project_context ---


def test_project_context_shows_tasks():
    state = _make_state()
    context = build_project_context(state)
    assert "test-proj" in context
    assert "[x]" in context
    assert "[ ]" in context
    assert "1/3" in context  # completed/total


# --- decompose_task (async) ---


class FakePlanner:
    """Returns a configurable PlanResult."""

    def __init__(self, plan: PlanResult):
        self._plan = plan
        self.call_count = 0

    async def plan(self, context: str, instruction: str) -> PlanResult:
        self.call_count += 1
        return self._plan

    async def select_task(self, context: str) -> str:
        return "fake"


class FailingPlanner:
    """Returns an invalid plan N times, then a valid one."""

    def __init__(self, fail_count: int, valid_plan: PlanResult):
        self._fail_count = fail_count
        self._valid = valid_plan
        self.call_count = 0

    async def plan(self, context: str, instruction: str) -> PlanResult:
        self.call_count += 1
        if self.call_count <= self._fail_count:
            return PlanResult(task_summary="bad", subtasks=[])  # invalid
        return self._valid

    async def select_task(self, context: str) -> str:
        return "fake"


@pytest.mark.asyncio
async def test_decompose_valid_plan():
    plan = _make_valid_plan()
    planner = FakePlanner(plan)
    ctx = _make_ctx()
    result = await decompose_task(ctx, planner)
    assert len(result.subtasks) == 2
    assert result.project_name == "test-proj"


@pytest.mark.asyncio
async def test_decompose_fills_metadata():
    plan = _make_valid_plan()
    plan.project_name = ""
    plan.milestone = ""
    planner = FakePlanner(plan)
    ctx = _make_ctx()
    result = await decompose_task(ctx, planner)
    assert result.project_name == "test-proj"
    assert result.milestone == "M1"


@pytest.mark.asyncio
async def test_decompose_retries_on_invalid():
    valid = _make_valid_plan()
    planner = FailingPlanner(fail_count=1, valid_plan=valid)
    ctx = _make_ctx()
    result = await decompose_task(ctx, planner, max_retries=1)
    assert len(result.subtasks) == 2
    assert planner.call_count == 2  # first failed, second succeeded


@pytest.mark.asyncio
async def test_decompose_raises_after_max_retries():
    valid = _make_valid_plan()
    planner = FailingPlanner(fail_count=5, valid_plan=valid)
    ctx = _make_ctx()
    with pytest.raises(DecompositionError, match="failed validation"):
        await decompose_task(ctx, planner, max_retries=1)
    assert planner.call_count == 2  # initial + 1 retry


@pytest.mark.asyncio
async def test_decompose_no_retries():
    plan = PlanResult(task_summary="bad", subtasks=[])
    planner = FakePlanner(plan)
    ctx = _make_ctx()
    with pytest.raises(DecompositionError):
        await decompose_task(ctx, planner, max_retries=0)
    assert planner.call_count == 1
