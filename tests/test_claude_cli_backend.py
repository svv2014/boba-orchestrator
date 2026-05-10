"""Tests for _resolve_claude_bin() in providers/claude_cli_backend.py."""

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
