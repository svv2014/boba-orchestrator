"""Tests for coordinator.result_merger — merging worker pool results."""

from __future__ import annotations

from typing import Optional

import pytest

from providers.base import TaskStatus, TaskType, WorkerResult
from workers.worker_pool import PoolResult
from coordinator.result_merger import MergeResult, merge_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _done(task_id: str, files: list[str], output: str = "") -> WorkerResult:
    return WorkerResult(
        task_id=task_id,
        status=TaskStatus.DONE,
        files_changed=files,
        output=output,
    )


def _error(task_id: str, error: Optional[str] = None) -> WorkerResult:
    return WorkerResult(
        task_id=task_id,
        status=TaskStatus.ERROR,
        error=error,
    )


def _blocked(task_id: str) -> WorkerResult:
    return WorkerResult(
        task_id=task_id,
        status=TaskStatus.BLOCKED,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_workers_succeeded_no_conflicts():
    """All workers DONE, distinct files — no conflicts, summary generated."""
    pool = PoolResult(results=[
        _done("w1", ["src/a.py", "src/b.py"], output="added feature A"),
        _done("w2", ["src/c.py"], output="added feature B"),
    ])

    result = merge_results(pool)

    assert result.all_succeeded is True
    assert set(result.merged_files) == {"src/a.py", "src/b.py", "src/c.py"}
    assert result.conflicts == []
    assert result.error_reports == []
    assert "2/2 workers succeeded" in result.summary
    assert "3 files changed" in result.summary
    # Worker output lines should appear
    assert "added feature A" in result.summary
    assert "added feature B" in result.summary


def test_two_workers_modified_same_file_conflict():
    """Two workers both touched the same file — conflict detected."""
    pool = PoolResult(results=[
        _done("w1", ["shared.py", "a.py"]),
        _done("w2", ["shared.py", "b.py"]),
    ])

    result = merge_results(pool)

    assert "shared.py" in result.conflicts
    assert "a.py" not in result.conflicts
    assert "b.py" not in result.conflicts
    assert "CONFLICTS" in result.summary
    assert "shared.py" in result.summary


def test_mixed_results_done_error_blocked():
    """Mixed statuses: summary counts each, error_reports collected."""
    pool = PoolResult(results=[
        _done("w1", ["ok.py"]),
        _error("w2", error="timeout after 30s"),
        _blocked("w3"),
    ])

    result = merge_results(pool)

    assert result.all_succeeded is False
    assert result.merged_files == ["ok.py"]
    assert result.conflicts == []
    assert len(result.error_reports) == 1
    assert "[w2]" in result.error_reports[0]
    assert "timeout after 30s" in result.error_reports[0]

    summary = result.summary
    assert "1/3 workers succeeded" in summary
    assert "1 failed" in summary
    assert "1 blocked" in summary


def test_empty_pool_result():
    """PoolResult with no workers — zero files, all_succeeded False."""
    pool = PoolResult(results=[])

    result = merge_results(pool)

    assert result.merged_files == []
    assert result.conflicts == []
    assert result.all_succeeded is False
    assert result.error_reports == []
    assert "0/0 workers succeeded" in result.summary
    assert "0 files changed" in result.summary


def test_single_worker_result():
    """Single DONE worker — clean result, no conflicts."""
    pool = PoolResult(results=[
        _done("solo", ["main.py"], output="bootstrap complete"),
    ])

    result = merge_results(pool)

    assert result.all_succeeded is True
    assert result.merged_files == ["main.py"]
    assert result.conflicts == []
    assert "1/1 workers succeeded" in result.summary
    assert "bootstrap complete" in result.summary


def test_error_reports_collected_from_all_failed_workers():
    """Multiple failed workers each produce an error report entry."""
    pool = PoolResult(results=[
        _error("w1", error="network unreachable"),
        _error("w2", error="OOM killed"),
        _error("w3"),  # no message — defaults to 'unknown error'
    ])

    result = merge_results(pool)

    assert len(result.error_reports) == 3
    assert any("network unreachable" in r for r in result.error_reports)
    assert any("OOM killed" in r for r in result.error_reports)
    assert any("unknown error" in r for r in result.error_reports)
    assert all(result.error_reports[i].startswith("[w") for i in range(3))


def test_merged_files_are_deduplicated():
    """Files touched by multiple workers appear only once in merged_files."""
    pool = PoolResult(results=[
        _done("w1", ["shared.py", "a.py"]),
        _done("w2", ["shared.py", "b.py"]),
    ])

    result = merge_results(pool)

    # shared.py should appear exactly once despite two workers touching it
    assert result.merged_files.count("shared.py") == 1
    assert set(result.merged_files) == {"shared.py", "a.py", "b.py"}


def test_no_worker_output_omits_worker_notes_section():
    """Workers with empty output do not produce a 'Worker outputs' section."""
    pool = PoolResult(results=[
        _done("w1", ["x.py"], output=""),
    ])

    result = merge_results(pool)

    assert "Worker outputs" not in result.summary


def test_blocked_workers_do_not_contribute_files():
    """BLOCKED workers have no files in merged_files."""
    pool = PoolResult(results=[
        _done("w1", ["real.py"]),
        _blocked("w2"),
    ])

    result = merge_results(pool)

    assert result.merged_files == ["real.py"]
