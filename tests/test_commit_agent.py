"""Tests for coordinator.commit_agent — git staging and committing."""

from __future__ import annotations

import os
from typing import Optional

import pytest

from coordinator.commit_agent import CommitResult, commit_changes
from coordinator.result_merger import MergeResult
from planner.task_selector import SelectedTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selected(
    project: str = "my-project",
    milestone: int = 5,
    title: str = "Core Implementation",
    description: str = "Implement the result merger",
) -> SelectedTask:
    return SelectedTask(
        project_name=project,
        milestone_number=milestone,
        milestone_title=title,
        task_description=description,
    )


def _merge(
    files: Optional[list[str]] = None,
    conflicts: Optional[list[str]] = None,
    all_succeeded: bool = True,
    summary: str = "2/2 workers succeeded. 1 files changed.",
    error_reports: Optional[list[str]] = None,
) -> MergeResult:
    return MergeResult(
        merged_files=files if files is not None else ["src/main.py"],
        conflicts=conflicts if conflicts is not None else [],
        all_succeeded=all_succeeded,
        summary=summary,
        error_reports=error_reports if error_reports is not None else [],
    )


def _init_git_repo(path) -> None:
    """Initialise a bare git repo with user config so commits work."""
    os.system(f"git -C {path} init -q")
    os.system(f'git -C {path} config user.email "test@test.com"')
    os.system(f'git -C {path} config user.name "Test"')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_commit_creates_git_commit(tmp_path):
    """Happy path: files staged and committed, CommitResult.committed=True."""
    _init_git_repo(tmp_path)

    # Create the file that will be committed
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.write_text("# placeholder\n")

    merge = _merge(files=["src/main.py"], summary="2/2 workers succeeded. 1 files changed.")
    selected = _selected(description="Implement the result merger for M5")

    result = await commit_changes(
        project_path=str(tmp_path),
        merge=merge,
        selected=selected,
        push=False,
    )

    assert result.committed is True
    assert result.commit_hash != ""
    assert result.error == ""

    # Verify commit message contains expected components
    assert "[my-project]" in result.message
    assert "M5:" in result.message
    assert "Implement the result merger" in result.message
    assert "2/2 workers succeeded" in result.message


@pytest.mark.asyncio
async def test_commit_message_format(tmp_path):
    """Commit message has correct title + body structure."""
    _init_git_repo(tmp_path)
    (tmp_path / "fix.py").write_text("x = 1\n")

    merge = _merge(files=["fix.py"], summary="Custom summary line.")
    selected = _selected(
        project="alpha-repo",
        milestone=3,
        description="Fix the broken pipeline handler",
    )

    result = await commit_changes(str(tmp_path), merge, selected)

    lines = result.message.splitlines()
    assert lines[0] == "[alpha-repo] M3: Fix the broken pipeline handler"
    assert lines[1] == ""  # blank separator
    assert "Custom summary line." in result.message


@pytest.mark.asyncio
async def test_conflicts_prevent_commit(tmp_path):
    """When MergeResult has conflicts, committed=False with explanation."""
    _init_git_repo(tmp_path)
    (tmp_path / "shared.py").write_text("pass\n")

    merge = _merge(
        files=["shared.py"],
        conflicts=["shared.py"],
        all_succeeded=True,
    )
    selected = _selected()

    result = await commit_changes(str(tmp_path), merge, selected)

    assert result.committed is False
    assert "conflict" in result.error.lower()
    assert "shared.py" in result.error


@pytest.mark.asyncio
async def test_no_succeeded_workers_no_files_does_not_commit(tmp_path):
    """all_succeeded=False and no files — CommitResult.committed=False."""
    _init_git_repo(tmp_path)

    merge = _merge(files=[], all_succeeded=False)
    selected = _selected()

    result = await commit_changes(str(tmp_path), merge, selected)

    assert result.committed is False
    assert result.error != ""


@pytest.mark.asyncio
async def test_push_disabled_by_default(tmp_path):
    """push=False (default) — no push attempted, commit still succeeds."""
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n")

    merge = _merge(files=["a.py"])
    selected = _selected()

    # Should not raise even with no remote configured
    result = await commit_changes(str(tmp_path), merge, selected, push=False)

    assert result.committed is True
    assert "push" not in result.error.lower()


@pytest.mark.asyncio
async def test_not_a_git_repository_returns_error(tmp_path):
    """Non-git directory returns CommitResult with descriptive error."""
    merge = _merge(files=["a.py"])
    selected = _selected()

    result = await commit_changes(str(tmp_path), merge, selected)

    assert result.committed is False
    assert "Not a git repository" in result.error


@pytest.mark.asyncio
async def test_empty_merged_files_returns_error(tmp_path):
    """No files changed — CommitResult.committed=False."""
    _init_git_repo(tmp_path)

    merge = _merge(files=[], all_succeeded=True)
    selected = _selected()

    result = await commit_changes(str(tmp_path), merge, selected)

    assert result.committed is False
    assert result.error != ""


@pytest.mark.asyncio
async def test_partial_success_with_files_can_commit(tmp_path):
    """all_succeeded=False but some files present — commit is allowed."""
    _init_git_repo(tmp_path)
    (tmp_path / "partial.py").write_text("partial = True\n")

    merge = _merge(
        files=["partial.py"],
        all_succeeded=False,
        summary="1/3 workers succeeded. 1 files changed.",
    )
    selected = _selected()

    result = await commit_changes(str(tmp_path), merge, selected)

    assert result.committed is True
    assert result.commit_hash != ""
