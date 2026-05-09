"""Tests for planner/task_selector.py."""

import asyncio

import pytest

from planner.project_scanner import LogEntry, Milestone, ProjectState, Task
from planner.task_selector import SelectedTask, select_by_recency, select_task


# --- Helpers ---


def _make_project(name, last_worked=None, has_task=True, blocked=False):
    tasks = []
    if has_task:
        tasks = [
            Task(description="do something", done=False, blocked=blocked,
                 blocker_reason="reason" if blocked else None),
        ]
    log = []
    if last_worked:
        log = [LogEntry(date=last_worked, agent="Boba", description="did stuff")]
    return ProjectState(
        name=name,
        path=f"/tmp/{name}",
        milestones=[Milestone(number=1, title="Foundation", tasks=tasks)],
        log=log,
    )


# --- select_by_recency ---


def test_recency_picks_oldest():
    states = [
        _make_project("alpha", last_worked="2026-03-27"),
        _make_project("beta", last_worked="2026-03-25"),
        _make_project("gamma", last_worked="2026-03-26"),
    ]
    result = select_by_recency(states)
    assert result is not None
    assert result.project_name == "beta"


def test_recency_picks_never_worked():
    states = [
        _make_project("alpha", last_worked="2026-03-27"),
        _make_project("beta", last_worked=None),
    ]
    result = select_by_recency(states)
    assert result is not None
    assert result.project_name == "beta"


def test_recency_skips_blocked():
    states = [
        _make_project("alpha", last_worked="2026-03-27", blocked=True),
        _make_project("beta", last_worked="2026-03-28"),
    ]
    result = select_by_recency(states)
    assert result is not None
    assert result.project_name == "beta"


def test_recency_skips_no_tasks():
    states = [
        _make_project("alpha", last_worked="2026-03-25", has_task=False),
        _make_project("beta", last_worked="2026-03-27"),
    ]
    result = select_by_recency(states)
    assert result is not None
    assert result.project_name == "beta"


def test_recency_returns_none_when_all_done():
    states = [
        _make_project("alpha", has_task=False),
        _make_project("beta", has_task=False),
    ]
    result = select_by_recency(states)
    assert result is None


def test_recency_returns_none_when_all_blocked():
    states = [
        _make_project("alpha", blocked=True),
        _make_project("beta", blocked=True),
    ]
    result = select_by_recency(states)
    assert result is None


def test_selected_task_fields():
    states = [_make_project("alpha", last_worked="2026-03-20")]
    result = select_by_recency(states)
    assert result.milestone_number == 1
    assert result.milestone_title == "Foundation"
    assert result.task_description == "do something"
    assert "recency" in result.reasoning.lower() or "least" in result.reasoning.lower()


# --- select_task (async, with planner) ---


class FakePlannerSuccess:
    async def select_task(self, context):
        return "boba-beta has the highest value task"

    async def plan(self, context, instruction):
        pass


class FakePlannerUnmatchable:
    async def select_task(self, context):
        return "some project that doesn't exist"

    async def plan(self, context, instruction):
        pass


class FakePlannerError:
    async def select_task(self, context):
        raise RuntimeError("API down")

    async def plan(self, context, instruction):
        pass


@pytest.mark.asyncio
async def test_select_task_no_planner_uses_recency():
    states = [
        _make_project("alpha", last_worked="2026-03-27"),
        _make_project("beta", last_worked="2026-03-25"),
    ]
    result = await select_task(states, planner=None)
    assert result is not None
    assert result.project_name == "beta"


@pytest.mark.asyncio
async def test_select_task_with_planner():
    states = [
        _make_project("boba-alpha", last_worked="2026-03-25"),
        _make_project("boba-beta", last_worked="2026-03-27"),
    ]
    result = await select_task(states, planner=FakePlannerSuccess())
    assert result is not None
    assert result.project_name == "boba-beta"
    assert "highest value" in result.reasoning.lower()


@pytest.mark.asyncio
async def test_select_task_planner_unmatchable_falls_back():
    states = [
        _make_project("alpha", last_worked="2026-03-27"),
        _make_project("beta", last_worked="2026-03-25"),
    ]
    result = await select_task(states, planner=FakePlannerUnmatchable())
    assert result is not None
    assert result.project_name == "beta"  # recency fallback


@pytest.mark.asyncio
async def test_select_task_planner_error_falls_back():
    states = [
        _make_project("alpha", last_worked="2026-03-27"),
        _make_project("beta", last_worked="2026-03-25"),
    ]
    result = await select_task(states, planner=FakePlannerError())
    assert result is not None
    assert result.project_name == "beta"  # recency fallback


@pytest.mark.asyncio
async def test_select_task_no_available_tasks():
    states = [_make_project("alpha", has_task=False)]
    result = await select_task(states, planner=FakePlannerSuccess())
    assert result is None
