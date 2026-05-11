#!/usr/bin/env python3
"""Garbage-collect bloated claude CLI session jsonl files.

Background (issue #25): when ``claude -p`` is invoked in a project directory
without an explicit ``--session-id`` it silently auto-resumes the most recent
session stored in ``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``. The file
grows across every run in that cwd. Once it exceeds the model's context window
``claude -p`` rejects locally with ``"Prompt is too long"``.

This script prunes those files by **archiving** (never deleting) any jsonl
older than ``--max-age-days`` (default 7) OR larger than ``--max-size-mb``
(default 10). Archived files are moved to ``~/.claude/_archived/`` so they can
be inspected or restored.

Idempotent: re-running with no eligible files is a no-op. Safe: never deletes,
only moves. Supports ``--dry-run``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple

DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_MAX_SIZE_MB = 10
DEFAULT_SESSIONS_ROOT = "~/.claude/projects"
DEFAULT_ARCHIVE_ROOT = "~/.claude/_archived"


class Candidate(NamedTuple):
    path: Path
    size_bytes: int
    age_seconds: float
    reason: str  # "size" | "age" | "size+age"


def find_candidates(
    sessions_root: Path,
    max_age_seconds: float,
    max_size_bytes: int,
    now: float | None = None,
) -> List[Candidate]:
    """Return jsonl files under sessions_root eligible for archival."""
    if now is None:
        now = time.time()
    results: List[Candidate] = []
    if not sessions_root.is_dir():
        return results

    for jsonl in sessions_root.glob("*/*.jsonl"):
        try:
            st = jsonl.stat()
        except OSError:
            continue
        size = st.st_size
        age = now - st.st_mtime
        over_age = age > max_age_seconds
        over_size = size > max_size_bytes
        if not (over_age or over_size):
            continue
        if over_age and over_size:
            reason = "size+age"
        elif over_size:
            reason = "size"
        else:
            reason = "age"
        results.append(Candidate(path=jsonl, size_bytes=size, age_seconds=age, reason=reason))
    return results


def archive_path_for(jsonl: Path, sessions_root: Path, archive_root: Path) -> Path:
    """Compute the destination under archive_root, preserving the project subdir."""
    try:
        rel = jsonl.relative_to(sessions_root)
    except ValueError:
        rel = Path(jsonl.name)
    return archive_root / rel


def archive_candidates(
    candidates: Iterable[Candidate],
    sessions_root: Path,
    archive_root: Path,
    dry_run: bool,
) -> List[tuple[Path, Path]]:
    """Move each candidate to archive_root preserving the project subdir.

    Returns a list of (src, dst) tuples that were (or would be) moved.
    """
    moved: List[tuple[Path, Path]] = []
    for cand in candidates:
        dst = archive_path_for(cand.path, sessions_root, archive_root)
        if dry_run:
            moved.append((cand.path, dst))
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # If a same-named archived file already exists, suffix with mtime to keep both.
            if dst.exists():
                dst = dst.with_name(f"{dst.stem}.{int(cand.path.stat().st_mtime)}{dst.suffix}")
            shutil.move(str(cand.path), str(dst))
            moved.append((cand.path, dst))
        except OSError as exc:
            print(f"WARN: could not archive {cand.path}: {exc}", file=sys.stderr)
    return moved


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n = int(n / 1024)
    return f"{n}GB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS,
        help=f"Archive files older than this (default {DEFAULT_MAX_AGE_DAYS}d)",
    )
    parser.add_argument(
        "--max-size-mb", type=float, default=DEFAULT_MAX_SIZE_MB,
        help=f"Archive files larger than this (default {DEFAULT_MAX_SIZE_MB} MB)",
    )
    parser.add_argument(
        "--sessions-root", default=DEFAULT_SESSIONS_ROOT,
        help=f"Path to claude projects dir (default {DEFAULT_SESSIONS_ROOT})",
    )
    parser.add_argument(
        "--archive-root", default=DEFAULT_ARCHIVE_ROOT,
        help=f"Path to archive dir (default {DEFAULT_ARCHIVE_ROOT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be archived without moving anything",
    )
    args = parser.parse_args(argv)

    sessions_root = Path(os.path.expanduser(args.sessions_root))
    archive_root = Path(os.path.expanduser(args.archive_root))
    max_age_seconds = args.max_age_days * 86400.0
    max_size_bytes = int(args.max_size_mb * 1024 * 1024)

    candidates = find_candidates(sessions_root, max_age_seconds, max_size_bytes)
    if not candidates:
        print(f"gc-claude-sessions: nothing to archive under {sessions_root}")
        return 0

    moved = archive_candidates(candidates, sessions_root, archive_root, args.dry_run)
    verb = "would archive" if args.dry_run else "archived"
    total_bytes = sum(c.size_bytes for c in candidates)
    print(
        f"gc-claude-sessions: {verb} {len(moved)} file(s), "
        f"{_format_size(total_bytes)} total"
    )
    for cand, (_src, dst) in zip(candidates, moved):
        age_days = cand.age_seconds / 86400.0
        print(
            f"  [{cand.reason:8s}] {_format_size(cand.size_bytes):>8s} "
            f"age={age_days:5.1f}d  {cand.path}  ->  {dst}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
