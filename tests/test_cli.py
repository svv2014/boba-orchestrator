"""CLI tests for orchestrator."""

import subprocess
import sys


def test_dry_run_exits_zero():
    result = subprocess.run(
        [sys.executable, "orchestrator.py", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "dry-run" in result.stdout


def test_dry_run_shows_project_summary():
    """dry-run should scan projects and print a summary."""
    result = subprocess.run(
        [sys.executable, "orchestrator.py", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # Should show at least one project from config
    output = result.stdout
    assert "Selected:" in output or "No actionable tasks" in output


def test_dry_run_selects_task():
    """dry-run should select a task and show milestone + reasoning."""
    result = subprocess.run(
        [sys.executable, "orchestrator.py", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # If tasks exist, should show selection details
    if "Selected:" in result.stdout:
        assert "Milestone:" in result.stdout
        assert "Task:" in result.stdout
        assert "Reasoning:" in result.stdout


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "orchestrator.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "orchestrator" in result.stdout.lower()


def test_help_shows_llm_flag():
    result = subprocess.run(
        [sys.executable, "orchestrator.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert "--llm" in result.stdout


def test_imports():
    """Verify all dependencies are importable."""
    import anthropic  # noqa: F401
    import yaml  # noqa: F401
    import git  # noqa: F401
