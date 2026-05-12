"""Tests for _resolve_claude_bin() and _run_claude() in providers/claude_cli_backend.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providers.claude_cli_backend import _resolve_claude_bin


def test_resolve_uses_claude_cli_path(monkeypatch):
    """CLAUDE_CLI_PATH takes priority and is returned as-is."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/custom/bin/claude")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    assert _resolve_claude_bin() == "/custom/bin/claude"


def test_resolve_uses_claude_bin_as_fallback(monkeypatch):
    """CLAUDE_BIN is honoured when CLAUDE_CLI_PATH is absent (deprecated alias)."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_BIN", "/legacy/bin/claude")
    assert _resolve_claude_bin() == "/legacy/bin/claude"


def test_resolve_logs_deprecation_warning_for_claude_bin(monkeypatch, caplog):
    """Using CLAUDE_BIN emits a deprecation warning."""
    import logging

    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.setenv("CLAUDE_BIN", "/legacy/bin/claude")
    with caplog.at_level(logging.WARNING, logger="providers.claude_cli_backend"):
        _resolve_claude_bin()
    assert any("deprecated" in r.message.lower() for r in caplog.records)


def test_resolve_falls_back_to_which(monkeypatch):
    """If both env vars are unset, shutil.which('claude') result is returned."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/claude")
    assert _resolve_claude_bin() == "/usr/local/bin/claude"


def test_resolve_raises_when_nothing_found(monkeypatch):
    """RuntimeError is raised with install-instructions hint when resolution fails."""
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="CLAUDE_CLI_PATH"):
        _resolve_claude_bin()


def test_resolve_claude_cli_path_takes_priority_over_claude_bin(monkeypatch):
    """CLAUDE_CLI_PATH wins even when CLAUDE_BIN is also set."""
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/primary/claude")
    monkeypatch.setenv("CLAUDE_BIN", "/legacy/claude")
    assert _resolve_claude_bin() == "/primary/claude"


def _make_proc(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    """Return a mock subprocess with the given returncode and output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = 12345
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_run_claude_transient_retry_uses_retry_returncode(monkeypatch):
    """RuntimeError exit code reflects the retry process, not the original."""
    from providers.claude_cli_backend import _run_claude

    monkeypatch.setenv("CLAUDE_CLI_PATH", "/fake/claude")
    monkeypatch.setenv("CLAUDE_RETRY_DELAY_SECONDS", "0")

    # Original proc exits 1 (triggers transient retry); retry proc exits 2.
    original_proc = _make_proc(1, stderr=b"rate limit exceeded")
    retry_proc = _make_proc(2, stderr=b"internal error on retry")

    call_count = 0

    async def fake_create_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_proc
        return retry_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(
        "providers.claude_cli_backend._is_recoverable", lambda _: True
    )

    with pytest.raises(RuntimeError) as exc_info:
        await _run_claude("hello")

    assert "code 2" in str(exc_info.value), (
        f"Expected 'code 2' in error but got: {exc_info.value}"
    )
    assert "code 1" not in str(exc_info.value), (
        f"Original exit code 1 leaked into error: {exc_info.value}"
    )
