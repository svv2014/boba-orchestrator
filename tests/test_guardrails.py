"""Tests for runtime guardrails."""
from __future__ import annotations

from security.guardrails import (
    GuardrailConfig,
    RunBudget,
    validate_worker_timeout,
    validate_target_repo,
    validate_subtask_count,
)


# --- GuardrailConfig ---


def test_default_config():
    g = GuardrailConfig()
    assert g.max_worker_timeout_seconds == 1800
    assert g.max_consecutive_failures == 3
    assert g.max_tasks_per_run == 10
    assert len(g.blocked_commands) > 0


def test_from_config_with_overrides():
    config = {
        "guardrails": {
            "max_worker_timeout_seconds": 600,
            "max_consecutive_failures": 5,
        },
        "projects": [
            {"name": "proj-a", "path": "/tmp/proj-a"},
            {"name": "proj-b", "path": "/tmp/proj-b"},
        ],
    }
    g = GuardrailConfig.from_config(config)
    assert g.max_worker_timeout_seconds == 600
    assert g.max_consecutive_failures == 5
    assert "/tmp/proj-a" in g.allowed_repos
    assert "/tmp/proj-b" in g.allowed_repos


def test_from_config_empty():
    g = GuardrailConfig.from_config({})
    assert g.max_worker_timeout_seconds == 1800


# --- RunBudget ---


def test_budget_success():
    b = RunBudget()
    b.record_success(60.0)
    assert b.tasks_completed == 1
    assert b.consecutive_failures == 0


def test_budget_failure_increments_consecutive():
    b = RunBudget()
    b.record_failure(30.0)
    b.record_failure(30.0)
    assert b.consecutive_failures == 2
    assert b.tasks_failed == 2


def test_budget_success_resets_consecutive():
    b = RunBudget()
    b.record_failure(10.0)
    b.record_failure(10.0)
    b.record_success(10.0)
    assert b.consecutive_failures == 0


def test_budget_circuit_breaker():
    g = GuardrailConfig(max_consecutive_failures=2)
    b = RunBudget()
    b.record_failure(10.0)
    assert b.should_stop(g) is None
    b.record_failure(10.0)
    reason = b.should_stop(g)
    assert reason is not None
    assert "consecutive" in reason.lower()


def test_budget_task_limit():
    g = GuardrailConfig(max_tasks_per_run=3)
    b = RunBudget()
    b.record_success(10.0)
    b.record_success(10.0)
    assert b.should_stop(g) is None
    b.record_success(10.0)
    reason = b.should_stop(g)
    assert reason is not None
    assert "limit" in reason.lower()


def test_budget_time_limit():
    g = GuardrailConfig(max_total_worker_seconds=100)
    b = RunBudget()
    b.record_success(90.0)
    assert b.should_stop(g) is None
    b.record_success(20.0)
    reason = b.should_stop(g)
    assert reason is not None
    assert "budget" in reason.lower()


def test_budget_summary():
    b = RunBudget()
    b.record_success(60.0)
    b.record_failure(30.0)
    s = b.summary
    assert "1 succeeded" in s
    assert "1 failed" in s


# --- Validators ---


def test_validate_timeout_caps():
    g = GuardrailConfig(max_worker_timeout_seconds=300)
    assert validate_worker_timeout(600, g) == 300
    assert validate_worker_timeout(100, g) == 100
    assert validate_worker_timeout(0, g) == 300


def test_validate_target_repo_allowed(tmp_path):
    g = GuardrailConfig(allowed_repos=[str(tmp_path)])
    assert validate_target_repo(str(tmp_path), g) is None
    # Subdirectory is also OK
    sub = tmp_path / "subdir"
    sub.mkdir()
    assert validate_target_repo(str(sub), g) is None


def test_validate_target_repo_denied(tmp_path):
    g = GuardrailConfig(allowed_repos=[str(tmp_path)])
    error = validate_target_repo("/etc", g)
    assert error is not None
    assert "not in allowed_repos" in error


def test_validate_target_repo_no_allowlist():
    g = GuardrailConfig(allowed_repos=[])
    assert validate_target_repo("/anywhere", g) is None


def test_validate_subtask_count():
    g = GuardrailConfig(max_subtasks_per_plan=3)
    assert validate_subtask_count(2, g) is None
    error = validate_subtask_count(5, g)
    assert error is not None
    assert "max" in error.lower()
