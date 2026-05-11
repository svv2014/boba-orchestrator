"""Tests for scripts/gc-claude-sessions.py (issue #25)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gc-claude-sessions.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gc_claude_sessions", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def gc():
    return _load_module()


def _make_jsonl(path: Path, size_bytes: int, age_seconds: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    if age_seconds is not None:
        t = time.time() - age_seconds
        os.utime(path, (t, t))


def test_find_candidates_picks_oversize(tmp_path, gc):
    root = tmp_path / "projects"
    big = root / "proj-a" / "abc.jsonl"
    _make_jsonl(big, size_bytes=3 * 1024 * 1024, age_seconds=60)

    out = gc.find_candidates(root, max_age_seconds=7 * 86400, max_size_bytes=1 * 1024 * 1024)
    assert len(out) == 1
    assert out[0].path == big
    assert out[0].reason == "size"


def test_find_candidates_picks_old(tmp_path, gc):
    root = tmp_path / "projects"
    old = root / "proj-a" / "abc.jsonl"
    _make_jsonl(old, size_bytes=100, age_seconds=10 * 86400)

    out = gc.find_candidates(root, max_age_seconds=7 * 86400, max_size_bytes=1 * 1024 * 1024)
    assert len(out) == 1
    assert out[0].path == old
    assert out[0].reason == "age"


def test_find_candidates_picks_both(tmp_path, gc):
    root = tmp_path / "projects"
    p = root / "proj-a" / "abc.jsonl"
    _make_jsonl(p, size_bytes=3 * 1024 * 1024, age_seconds=10 * 86400)

    out = gc.find_candidates(root, max_age_seconds=7 * 86400, max_size_bytes=1 * 1024 * 1024)
    assert len(out) == 1
    assert out[0].reason == "size+age"


def test_find_candidates_skips_small_and_fresh(tmp_path, gc):
    root = tmp_path / "projects"
    small = root / "proj-a" / "fresh.jsonl"
    _make_jsonl(small, size_bytes=1024, age_seconds=60)

    out = gc.find_candidates(root, max_age_seconds=7 * 86400, max_size_bytes=10 * 1024 * 1024)
    assert out == []


def test_archive_moves_to_archive_root_preserving_subdir(tmp_path, gc):
    root = tmp_path / "projects"
    archive = tmp_path / "archive"
    p = root / "proj-x" / "deadbeef.jsonl"
    _make_jsonl(p, size_bytes=12 * 1024 * 1024, age_seconds=60)

    cands = gc.find_candidates(root, 7 * 86400, 10 * 1024 * 1024)
    moved = gc.archive_candidates(cands, root, archive, dry_run=False)

    assert len(moved) == 1
    src, dst = moved[0]
    assert src == p
    assert dst == archive / "proj-x" / "deadbeef.jsonl"
    assert not p.exists()
    assert dst.exists()


def test_dry_run_does_not_move(tmp_path, gc):
    root = tmp_path / "projects"
    archive = tmp_path / "archive"
    p = root / "proj-y" / "abc.jsonl"
    _make_jsonl(p, size_bytes=12 * 1024 * 1024, age_seconds=60)

    cands = gc.find_candidates(root, 7 * 86400, 10 * 1024 * 1024)
    moved = gc.archive_candidates(cands, root, archive, dry_run=True)

    assert len(moved) == 1
    assert p.exists(), "dry-run must not move the source file"
    assert not (archive / "proj-y" / "abc.jsonl").exists()


def test_idempotent_collision_keeps_both(tmp_path, gc):
    """Second run with a same-named archived file already present must not clobber."""
    root = tmp_path / "projects"
    archive = tmp_path / "archive"
    # Pre-existing archived file with the same name
    existing = archive / "proj-z" / "abc.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("OLD")

    p = root / "proj-z" / "abc.jsonl"
    _make_jsonl(p, size_bytes=12 * 1024 * 1024, age_seconds=60)

    cands = gc.find_candidates(root, 7 * 86400, 10 * 1024 * 1024)
    gc.archive_candidates(cands, root, archive, dry_run=False)

    assert existing.exists() and existing.read_text() == "OLD"
    archived_now = list((archive / "proj-z").glob("abc*.jsonl"))
    assert len(archived_now) == 2, archived_now


def test_missing_sessions_root_is_a_noop(tmp_path, gc):
    out = gc.find_candidates(tmp_path / "nope", 1, 1)
    assert out == []


def test_cli_smoke(tmp_path):
    """End-to-end: invoke the script as a subprocess against a fake sessions tree."""
    root = tmp_path / "projects"
    archive = tmp_path / "archive"
    big = root / "proj-a" / "big.jsonl"
    _make_jsonl(big, size_bytes=12 * 1024 * 1024, age_seconds=60)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--sessions-root", str(root),
            "--archive-root", str(archive),
            "--max-size-mb", "10",
            "--max-age-days", "7",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "archived 1 file" in result.stdout
    assert not big.exists()
    assert (archive / "proj-a" / "big.jsonl").exists()


def test_cli_dry_run_smoke(tmp_path):
    root = tmp_path / "projects"
    archive = tmp_path / "archive"
    big = root / "proj-a" / "big.jsonl"
    _make_jsonl(big, size_bytes=12 * 1024 * 1024, age_seconds=60)

    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--sessions-root", str(root),
            "--archive-root", str(archive),
            "--max-size-mb", "10",
            "--dry-run",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "would archive 1 file" in result.stdout
    assert big.exists(), "dry-run must not move"
