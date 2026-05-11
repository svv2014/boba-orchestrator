"""Tests for issue #25: _run_claude must pass a fresh --session-id per call
when the caller did not supply an explicit one, so claude CLI does not
auto-resume the cwd-bound session jsonl.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from providers import claude_cli_backend


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class _FakeProc:
    def __init__(self, stdout=b"ok", stderr=b"", returncode=0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 12345

    async def communicate(self):
        return self._stdout, self._stderr


def _patch_subprocess(captured: list[list[str]]):
    """Patch asyncio.create_subprocess_exec to capture argv and return a fake proc."""

    async def fake_exec(*args, **kwargs):
        captured.append(list(args))
        return _FakeProc()

    return patch.object(claude_cli_backend.asyncio, "create_subprocess_exec",
                        new=AsyncMock(side_effect=fake_exec))


@pytest.fixture(autouse=True)
def _stub_claude_bin(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/fake/claude")


def _extract_session_id(argv: list[str]) -> str | None:
    if "--session-id" not in argv:
        return None
    return argv[argv.index("--session-id") + 1]


def test_no_session_id_argument_generates_fresh_uuid():
    captured: list[list[str]] = []
    with _patch_subprocess(captured):
        asyncio.run(claude_cli_backend._run_claude("hello"))
    assert len(captured) == 1
    sid = _extract_session_id(captured[0])
    assert sid is not None, "must inject --session-id even when none supplied"
    assert UUID_RE.match(sid), f"injected id is not a uuid4: {sid!r}"


def test_each_invocation_gets_a_different_uuid():
    captured: list[list[str]] = []
    with _patch_subprocess(captured):
        asyncio.run(claude_cli_backend._run_claude("first"))
        asyncio.run(claude_cli_backend._run_claude("second"))
    sids = [_extract_session_id(argv) for argv in captured]
    assert all(sids), sids
    assert sids[0] != sids[1], "fresh uuid per invocation, not a module-level constant"


def test_explicit_session_id_is_preserved():
    explicit = str(uuid.uuid4())
    captured: list[list[str]] = []
    with _patch_subprocess(captured):
        asyncio.run(claude_cli_backend._run_claude("hi", session_id=explicit, resume=False))
    sid = _extract_session_id(captured[0])
    assert sid == explicit, "caller-supplied session id must be used verbatim"


def test_output_format_stays_text_when_no_explicit_session():
    """Planner / generic callers must keep getting plain text output."""
    captured: list[list[str]] = []
    with _patch_subprocess(captured):
        asyncio.run(claude_cli_backend._run_claude("hello"))
    argv = captured[0]
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "text"


def test_output_format_json_when_explicit_session():
    explicit = str(uuid.uuid4())
    captured: list[list[str]] = []
    with _patch_subprocess(captured):
        asyncio.run(claude_cli_backend._run_claude("hi", session_id=explicit, resume=False))
    argv = captured[0]
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "json"
