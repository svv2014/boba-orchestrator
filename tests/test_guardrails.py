"""Tests for runtime guardrails."""
from __future__ import annotations

import os

from security.guardrails import (
    GuardrailConfig,
    RunBudget,
    _resolve_worktree,
    validate_worker_timeout,
    validate_target_repo,
    validate_subtask_count,
    validate_command,
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


# --- Worktree resolution ---


def _make_worktree_git_file(worktree_dir, canonical_repo: str, wt_name: str = "wt") -> None:
    """Fabricate a .git file as git-worktree produces."""
    gitdir = os.path.join(canonical_repo, ".git", "worktrees", wt_name)
    (worktree_dir / ".git").write_text(f"gitdir: {gitdir}\n")


def test_resolve_worktree_returns_canonical(tmp_path):
    canonical = tmp_path / "canonical-repo"
    canonical.mkdir()
    worktree = tmp_path / "loop-rework-wt-1"
    worktree.mkdir()
    _make_worktree_git_file(worktree, str(canonical))
    assert _resolve_worktree(str(worktree)) == str(canonical)


def test_resolve_worktree_standard_repo_unchanged(tmp_path):
    repo = tmp_path / "my-repo"
    repo.mkdir()
    git_dir = repo / ".git"
    git_dir.mkdir()  # standard repo: .git is a directory
    assert _resolve_worktree(str(repo)) == str(repo)


def test_resolve_worktree_no_git_unchanged(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert _resolve_worktree(str(plain)) == str(plain)


def test_resolve_worktree_gitdir_without_worktrees_marker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # .git file without the /worktrees/ marker — not a standard worktree
    (repo / ".git").write_text("gitdir: /some/other/path/.git\n")
    assert _resolve_worktree(str(repo)) == str(repo)


# --- validate_target_repo with worktree resolution ---


def test_worktree_of_allowed_repo_accepted(tmp_path):
    canonical = tmp_path / "canonical-repo"
    canonical.mkdir()
    worktree = tmp_path / "loop-rework-slug-1"
    worktree.mkdir()
    _make_worktree_git_file(worktree, str(canonical))

    g = GuardrailConfig(allowed_repos=[str(canonical)])
    assert validate_target_repo(str(worktree), g) is None


def test_worktree_of_unallowed_repo_rejected(tmp_path):
    canonical = tmp_path / "unallowed-repo"
    canonical.mkdir()
    allowed = tmp_path / "allowed-repo"
    allowed.mkdir()
    worktree = tmp_path / "loop-rework-slug-2"
    worktree.mkdir()
    _make_worktree_git_file(worktree, str(canonical))

    g = GuardrailConfig(allowed_repos=[str(allowed)])
    error = validate_target_repo(str(worktree), g)
    assert error is not None
    assert "not in allowed_repos" in error


def test_random_tmp_path_without_git_file_rejected(tmp_path):
    random_dir = tmp_path / "tmp" / "anything-else"
    random_dir.mkdir(parents=True)
    g = GuardrailConfig(allowed_repos=[str(tmp_path / "some-allowed-repo")])
    error = validate_target_repo(str(random_dir), g)
    assert error is not None
    assert "not in allowed_repos" in error


# --- from_config union semantics ---


def test_from_config_explicit_plus_projects_produces_union(tmp_path):
    extra = str(tmp_path / "extra-path")
    proj_a = str(tmp_path / "proj-a")
    proj_b = str(tmp_path / "proj-b")
    config = {
        "guardrails": {"allowed_repos": [extra]},
        "projects": [
            {"name": "a", "path": proj_a},
            {"name": "b", "path": proj_b},
        ],
    }
    g = GuardrailConfig.from_config(config)
    assert extra in g.allowed_repos
    assert proj_a in g.allowed_repos
    assert proj_b in g.allowed_repos


def test_from_config_explicit_does_not_override_projects(tmp_path):
    extra = str(tmp_path / "extra")
    proj = str(tmp_path / "proj")
    config = {
        "guardrails": {"allowed_repos": [extra]},
        "projects": [{"name": "p", "path": proj}],
    }
    g = GuardrailConfig.from_config(config)
    # Both must appear — explicit must not suppress project paths
    assert extra in g.allowed_repos
    assert proj in g.allowed_repos


def test_from_config_no_duplicates_in_union(tmp_path):
    shared = str(tmp_path / "shared")
    config = {
        "guardrails": {"allowed_repos": [shared]},
        "projects": [{"name": "p", "path": shared}],
    }
    g = GuardrailConfig.from_config(config)
    assert g.allowed_repos.count(shared) == 1


# --- validate_command ---


def test_validate_command_blocked_pattern():
    g = GuardrailConfig()
    error = validate_command("please run rm -rf / on the server", g)
    assert error is not None
    assert "rm -rf /" in error


def test_validate_command_clean_prompt():
    g = GuardrailConfig()
    assert validate_command("add a unit test for the parse function", g) is None


def test_validate_command_case_insensitive():
    g = GuardrailConfig()
    # "DROP TABLE" in mixed case
    error = validate_command("execute Drop Table users", g)
    assert error is not None


def test_validate_command_empty_blocked_list():
    g = GuardrailConfig(blocked_commands=[])
    assert validate_command("rm -rf /", g) is None


def test_validate_command_custom_pattern():
    g = GuardrailConfig(blocked_commands=["danger_cmd"])
    assert validate_command("safe operation", g) is None
    assert validate_command("call danger_cmd now", g) is not None


# --- worker pool blocked-command dispatch tests ---


def test_worker_pool_blocks_task_without_spawning():
    """WorkerPool rejects a blocked task without calling the backend execute."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from providers.base import SubTask, TaskType, TaskStatus
    from workers.worker_pool import WorkerPool

    mock_backend = MagicMock()
    mock_backend.execute = AsyncMock()

    guardrails = GuardrailConfig(blocked_commands=["rm -rf /"])
    pool = WorkerPool(mock_backend, guardrails=guardrails)

    task = SubTask(
        id="t1",
        type=TaskType.CODE,
        description="run rm -rf / to clean up",
        target_repo="/tmp/repo",
    )

    result = asyncio.run(pool.execute([task]))

    mock_backend.execute.assert_not_called()
    assert result.results[0].status == TaskStatus.ERROR
    assert "rm -rf /" in result.results[0].error


def test_worker_pool_allows_clean_task():
    """WorkerPool passes a clean task through to the backend."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from providers.base import SubTask, TaskType, TaskStatus, WorkerResult
    from workers.worker_pool import WorkerPool

    mock_backend = MagicMock()
    mock_backend.execute = AsyncMock(return_value=WorkerResult(
        task_id="t2", status=TaskStatus.DONE
    ))

    guardrails = GuardrailConfig()
    pool = WorkerPool(mock_backend, guardrails=guardrails)

    task = SubTask(
        id="t2",
        type=TaskType.CODE,
        description="add a docstring to the parse function",
        target_repo="/tmp/repo",
    )

    result = asyncio.run(pool.execute([task]))

    mock_backend.execute.assert_called_once()
    assert result.results[0].status == TaskStatus.DONE
