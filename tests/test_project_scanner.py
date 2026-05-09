"""Tests for planner/project_scanner.py"""

import os
import tempfile
import textwrap

import pytest

from planner.project_scanner import (
    ProjectState,
    Task,
    _parse_todo,
    scan_project,
    scan_all,
    format_summary,
)


SAMPLE_TODO = textwrap.dedent("""\
    # TODO — test-project

    ## Milestone 1 — Foundation

    - [x] Set up project — 2026-03-25
    - [x] Write store.py — 2026-03-26
    - [ ] Write runner.py

    ## Milestone 2 — Checks

    - [ ] Write file_check.py
    - [ ] Write http_check.py ← BLOCKED: waiting for API spec

    ---

    ## Log

    | Date | Agent | What was done |
    |------|-------|---------------|
    | 2026-03-25 | Boba | Set up project |
    | 2026-03-26 | Boba | Wrote store.py |
""")


def test_parse_milestones():
    milestones, log = _parse_todo(SAMPLE_TODO)
    assert len(milestones) == 2
    assert milestones[0].number == 1
    assert milestones[0].title == "Foundation"
    assert milestones[0].total == 3
    assert milestones[0].completed == 2


def test_parse_tasks():
    milestones, _ = _parse_todo(SAMPLE_TODO)
    tasks = milestones[0].tasks
    assert tasks[0].done is True
    assert tasks[0].date_completed == "2026-03-25"
    assert tasks[2].done is False
    assert tasks[2].date_completed is None


def test_parse_blocked():
    milestones, _ = _parse_todo(SAMPLE_TODO)
    blocked_task = milestones[1].tasks[1]
    assert blocked_task.blocked is True
    assert "API spec" in blocked_task.blocker_reason


def test_parse_log():
    _, log = _parse_todo(SAMPLE_TODO)
    assert len(log) == 2
    assert log[0].date == "2026-03-25"
    assert log[1].agent == "Boba"


def test_milestone_properties():
    milestones, _ = _parse_todo(SAMPLE_TODO)
    m1 = milestones[0]
    assert m1.is_complete is False
    assert m1.next_task.description.startswith("Write runner.py")

    m2 = milestones[1]
    assert m2.is_complete is False
    assert m2.completed == 0


def test_scan_project():
    with tempfile.TemporaryDirectory() as tmpdir:
        todo_path = os.path.join(tmpdir, "TODO.md")
        with open(todo_path, "w") as f:
            f.write(SAMPLE_TODO)

        state = scan_project("test-proj", tmpdir)
        assert state.name == "test-proj"
        assert state.total_tasks == 5
        assert state.completed_tasks == 2
        assert state.last_worked == "2026-03-26"
        assert state.current_milestone.number == 1
        assert state.next_task is not None


def test_scan_project_missing_todo():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = scan_project("empty", tmpdir)
        assert state.total_tasks == 0
        assert state.current_milestone is None
        assert state.next_task is None


def test_format_summary():
    milestones, log = _parse_todo(SAMPLE_TODO)
    state = ProjectState(name="test", path="/tmp", milestones=milestones, log=log)
    summary = format_summary([state])
    assert "test" in summary
    assert "2/5" in summary
    assert "Last worked: 2026-03-26" in summary


def test_scan_all_with_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mimic real layout: projects/ contains orchestrator/ and sibling projects
        projects_dir = os.path.join(tmpdir, "projects")
        orch_dir = os.path.join(projects_dir, "orchestrator")
        os.makedirs(orch_dir)

        for name in ["proj-a", "proj-b"]:
            proj_dir = os.path.join(projects_dir, name)
            os.makedirs(proj_dir)
            with open(os.path.join(proj_dir, "TODO.md"), "w") as f:
                f.write(f"# TODO — {name}\n\n## Milestone 1 — Init\n\n- [ ] First task\n\n---\n\n## Log\n\n| Date | Agent | What |\n|------|-------|------|\n")

        # Config at orchestrator/config/orchestrator.yaml, paths relative to orchestrator root
        config_dir = os.path.join(orch_dir, "config")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "orchestrator.yaml")
        with open(config_path, "w") as f:
            f.write("projects:\n  - path: ../proj-a\n    name: proj-a\n  - path: ../proj-b\n    name: proj-b\n")

        states = scan_all(config_path)
        assert len(states) == 2
        assert states[0].name == "proj-a"
        assert states[1].total_tasks == 1
