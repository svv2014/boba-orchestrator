"""Tests for observability.transcript."""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from observability.transcript import Transcript, rotate_old, _make_run_id


# ─── helpers ──────────────────────────────────────────────────────────────────

def _read_jsonl(path: str) -> list[dict]:
    """Parse a .jsonl file into a list of dicts."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ─── run_id ───────────────────────────────────────────────────────────────────

def test_make_run_id_format():
    run_id = _make_run_id()
    # Expect YYYY-MM-DD-<8 hex chars>
    parts = run_id.split("-")
    assert len(parts) == 4, f"Unexpected run_id format: {run_id}"
    year, month, day, suffix = parts
    assert len(year) == 4
    assert len(month) == 2
    assert len(day) == 2
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex


# ─── file creation ────────────────────────────────────────────────────────────

def test_transcript_creates_file(capsys):
    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="test-run-00000001", transcript_dir=d)
        t.close()

        path = os.path.join(d, "test-run-00000001.jsonl")
        assert os.path.exists(path)

        captured = capsys.readouterr()
        assert "[transcript]" in captured.out
        assert "test-run-00000001.jsonl" in captured.out


def test_transcript_file_created_even_without_emit():
    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="empty-run", transcript_dir=d)
        t.close()
        assert os.path.exists(os.path.join(d, "empty-run.jsonl"))


# ─── well-formed JSONL ────────────────────────────────────────────────────────

def test_emit_writes_valid_jsonl():
    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="jsonl-run", transcript_dir=d)
        t.emit("task.start", task="do something", mode="quick")
        t.emit("claude.exec.start", task_id="t1", model="sonnet")
        t.emit("claude.exec.done", task_id="t1", status="done", duration_ms=500)
        t.emit("task.done", status="done", elapsed_s=1)
        t.close()

        records = _read_jsonl(os.path.join(d, "jsonl-run.jsonl"))
        assert len(records) == 4

        kinds = [r["kind"] for r in records]
        assert kinds == ["task.start", "claude.exec.start", "claude.exec.done", "task.done"]

        for r in records:
            assert "ts" in r
            assert "run_id" in r
            assert r["run_id"] == "jsonl-run"
            assert isinstance(r["ts"], float)


def test_emit_no_newline_inside_line():
    """Each record must be exactly one line (no embedded newlines)."""
    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="newline-run", transcript_dir=d)
        t.emit("task.start", task="multi\nline\ntask")
        t.close()

        path = os.path.join(d, "newline-run.jsonl")
        with open(path) as f:
            lines = [ln for ln in f.readlines() if ln.strip()]
        assert len(lines) == 1
        # Must be parseable
        rec = json.loads(lines[0])
        assert "\n" not in json.dumps(rec, separators=(",", ":"))


# ─── env-var override ─────────────────────────────────────────────────────────

def test_env_var_override(monkeypatch, tmp_path):
    custom_dir = str(tmp_path / "custom_transcripts")
    monkeypatch.setenv("ORCHESTRATOR_TRANSCRIPT_DIR", custom_dir)

    # Import after monkeypatching so _transcript_dir() picks up new env
    from observability.transcript import _transcript_dir
    assert _transcript_dir() == custom_dir

    t = Transcript(run_id="env-run")  # no transcript_dir arg → uses env var
    t.emit("task.start", task="env test")
    t.close()

    assert os.path.exists(os.path.join(custom_dir, "env-run.jsonl"))


# ─── rotation ────────────────────────────────────────────────────────────────

def test_rotate_old_deletes_old_files(tmp_path):
    old_file = tmp_path / "old-run.jsonl"
    old_file.write_text('{"kind":"task.start"}\n')

    # Back-date mtime to 8 days ago
    old_mtime = time.time() - 8 * 86400
    os.utime(str(old_file), (old_mtime, old_mtime))

    recent_file = tmp_path / "recent-run.jsonl"
    recent_file.write_text('{"kind":"task.start"}\n')

    deleted = rotate_old(directory=str(tmp_path), days=7)
    assert deleted == 1
    assert not old_file.exists()
    assert recent_file.exists()


def test_rotate_old_nonexistent_dir():
    """rotate_old must not raise even if directory doesn't exist."""
    deleted = rotate_old(directory="/tmp/does-not-exist-xyz-boba", days=7)
    assert deleted == 0


def test_rotate_old_ignores_non_jsonl(tmp_path):
    txt_file = tmp_path / "old-run.txt"
    txt_file.write_text("not a transcript")
    old_mtime = time.time() - 10 * 86400
    os.utime(str(txt_file), (old_mtime, old_mtime))

    deleted = rotate_old(directory=str(tmp_path), days=7)
    assert deleted == 0
    assert txt_file.exists()


# ─── thread safety ────────────────────────────────────────────────────────────

def test_emit_thread_safe():
    """Multiple threads emitting concurrently must not corrupt the file."""
    import threading

    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="threaded-run", transcript_dir=d)

        errors = []

        def emit_many(n: int):
            try:
                for i in range(n):
                    t.emit("ping", i=i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_many, args=(50,)) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        t.close()
        assert not errors

        records = _read_jsonl(os.path.join(d, "threaded-run.jsonl"))
        assert len(records) == 250  # 5 threads × 50 emits


# ─── fault tolerance ──────────────────────────────────────────────────────────

def test_emit_after_close_does_not_raise():
    """Emitting after close must silently no-op."""
    with tempfile.TemporaryDirectory() as d:
        t = Transcript(run_id="after-close", transcript_dir=d)
        t.close()
        t.emit("should.be.ignored", x=1)  # must not raise


def test_transcript_invalid_dir_does_not_raise(capsys):
    """If the transcript dir cannot be created, orchestrator should not crash."""
    t = Transcript(run_id="bad-dir", transcript_dir="/proc/no-such-dir/cannot-create")
    t.emit("task.start", task="should not crash")
    t.close()
    # No assertion on output — just must not raise


# ─── context manager ──────────────────────────────────────────────────────────

def test_context_manager():
    with tempfile.TemporaryDirectory() as d:
        with Transcript(run_id="ctx-run", transcript_dir=d) as t:
            t.emit("task.start", task="ctx")
            t.emit("task.done", status="done")

        records = _read_jsonl(os.path.join(d, "ctx-run.jsonl"))
        assert len(records) == 2
