"""Per-run structured transcript log for boba-orchestrator.

Each orchestrator run writes a <run_id>.jsonl file to
${ORCHESTRATOR_TRANSCRIPT_DIR:-/tmp/orchestrator-transcripts}.

Usage:
    from observability.transcript import Transcript

    t = Transcript(run_id="2026-05-09-abc12345")
    t.emit("task.start", task="write tests", mode="quick")
    t.emit("claude.exec.start", task_id="quick-1", model="sonnet")
    t.emit("claude.exec.done", task_id="quick-1", status="done", duration_ms=1200)
    t.emit("task.done", status="done", elapsed_s=2)
    t.close()
"""

from __future__ import annotations

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_TRANSCRIPT_DIR_ENV = "ORCHESTRATOR_TRANSCRIPT_DIR"
_DEFAULT_TRANSCRIPT_DIR = "/tmp/orchestrator-transcripts"

_ROTATION_DAYS = 7


def _transcript_dir() -> str:
    return os.environ.get(_TRANSCRIPT_DIR_ENV, _DEFAULT_TRANSCRIPT_DIR)


def _make_run_id() -> str:
    date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{date}-{uuid4().hex[:8]}"


class Transcript:
    """Thread-safe per-run JSONL transcript writer.

    Writes one JSON object per line (JSONL) with a timestamp and event kind.
    All write failures are swallowed — never aborts the orchestrator run.
    """

    def __init__(self, run_id: str | None = None, transcript_dir: str | None = None) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._file = None

        self.run_id = run_id or _make_run_id()
        dir_path = transcript_dir or _transcript_dir()

        try:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            self._path = os.path.join(dir_path, f"{self.run_id}.jsonl")
            # line-buffered (buffering=1) so each emit is immediately visible
            self._file = open(self._path, "a", buffering=1, encoding="utf-8")
            print(f"[transcript] {os.path.abspath(self._path)}")
        except Exception as exc:
            # Degraded mode: transcript unavailable, but orchestrator still runs
            self._file = None
            self._path = None
            import logging
            logging.getLogger(__name__).warning("Transcript unavailable: %s", exc)

    def emit(self, kind: str, **fields: Any) -> None:
        """Write a structured event to the transcript file.

        Args:
            kind: Event kind string (e.g. ``task.start``, ``claude.exec.done``).
            **fields: Arbitrary key/value pairs merged into the log record.
        """
        if self._file is None:
            return
        record = {
            "ts": time.time(),
            "run_id": self.run_id,
            "kind": kind,
            **fields,
        }
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            with self._lock:
                if not self._closed and self._file is not None:
                    self._file.write(line)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Transcript emit failed (%s): %s", kind, exc)

    def close(self) -> None:
        """Flush and close the transcript file."""
        try:
            with self._lock:
                if self._file is not None and not self._closed:
                    self._file.flush()
                    self._file.close()
                self._closed = True
        except Exception:
            pass

    def __enter__(self) -> "Transcript":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def rotate_old(directory: str | None = None, days: int = _ROTATION_DAYS) -> int:
    """Delete transcript files older than *days* days.

    Args:
        directory: Path to scan. Defaults to ``$ORCHESTRATOR_TRANSCRIPT_DIR``.
        days: Files older than this many days are removed.

    Returns:
        Number of files deleted.
    """
    dir_path = directory or _transcript_dir()
    cutoff = time.time() - days * 86400
    deleted = 0
    try:
        p = Path(dir_path)
        if not p.exists():
            return 0
        for f in p.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception:
                pass
    except Exception:
        pass
    return deleted
