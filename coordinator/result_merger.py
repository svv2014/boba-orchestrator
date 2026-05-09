"""Merges worker pool results into a unified MergeResult.

Collects files changed across all workers, detects conflicts (same file
modified by multiple workers), and produces a human-readable summary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from providers.base import TaskStatus
from workers.worker_pool import PoolResult


@dataclass
class MergeResult:
    """Unified result after merging all worker outputs."""

    merged_files: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    summary: str = ""
    all_succeeded: bool = False
    error_reports: list[str] = field(default_factory=list)


def merge_results(pool: PoolResult) -> MergeResult:
    """Merge a PoolResult into a single MergeResult.

    Args:
        pool: Aggregated results from the worker pool.

    Returns:
        MergeResult with deduplicated files, detected conflicts,
        a human-readable summary, and any error reports.
    """
    # Count how many workers touched each file
    file_counts: Counter[str] = Counter()
    for r in pool.results:
        if r.status == TaskStatus.DONE:
            for f in r.files_changed:
                file_counts[f] += 1

    # Deduplicated file list (preserving order from PoolResult)
    merged_files = pool.all_files_changed

    # Files touched by more than one worker
    conflicts = [f for f, count in file_counts.items() if count > 1]

    # Collect error reports from failed workers
    error_reports: list[str] = []
    for r in pool.results:
        if r.status == TaskStatus.ERROR:
            msg = f"[{r.task_id}] {r.error or 'unknown error'}"
            error_reports.append(msg)

    summary = _build_summary(pool, merged_files, conflicts, error_reports)

    return MergeResult(
        merged_files=merged_files,
        conflicts=conflicts,
        summary=summary,
        all_succeeded=pool.all_succeeded,
        error_reports=error_reports,
    )


def _build_summary(
    pool: PoolResult,
    merged_files: list[str],
    conflicts: list[str],
    error_reports: list[str],
) -> str:
    """Build a human-readable summary of the merge."""
    parts: list[str] = []

    parts.append(
        f"{pool.succeeded}/{pool.total} workers succeeded"
    )

    if pool.failed:
        parts.append(f"{pool.failed} failed")
    if pool.blocked:
        parts.append(f"{pool.blocked} blocked")

    parts.append(f"{len(merged_files)} files changed")

    if conflicts:
        parts.append(
            f"CONFLICTS in {len(conflicts)} file(s): {', '.join(conflicts)}"
        )

    # Append individual worker summaries for succeeded tasks
    worker_notes: list[str] = []
    for r in pool.results:
        if r.status == TaskStatus.DONE and r.output:
            worker_notes.append(f"- [{r.task_id}] {r.output[:200]}")

    summary = ". ".join(parts) + "."
    if worker_notes:
        summary += "\n\nWorker outputs:\n" + "\n".join(worker_notes)

    return summary
