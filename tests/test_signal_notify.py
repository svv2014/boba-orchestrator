"""QA-driven tests for PR #20 — BOBA_NOTIFY_SCRIPT parameterization."""

import logging

import pytest

import workers.worker_pool as wp
from workers.worker_pool import _resolve_signal_script


# --- _resolve_signal_script() unit tests ---


def test_resolve_uses_boba_env_var(monkeypatch):
    monkeypatch.setenv("BOBA_NOTIFY_SCRIPT", "/boba/script.sh")
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    assert _resolve_signal_script() == "/boba/script.sh"


def test_resolve_legacy_fallback_returns_path(monkeypatch):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.setenv("SIGNAL_NOTIFY_SCRIPT", "/legacy/script.sh")
    assert _resolve_signal_script() == "/legacy/script.sh"


def test_resolve_legacy_fallback_logs_deprecation(monkeypatch, caplog):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.setenv("SIGNAL_NOTIFY_SCRIPT", "/legacy/script.sh")
    with caplog.at_level(logging.WARNING, logger="workers.worker_pool"):
        _resolve_signal_script()
    assert "deprecated" in caplog.text.lower()


def test_resolve_returns_empty_when_neither_set(monkeypatch):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    assert _resolve_signal_script() == ""


def test_resolve_boba_takes_precedence_over_legacy(monkeypatch):
    monkeypatch.setenv("BOBA_NOTIFY_SCRIPT", "/boba/script.sh")
    monkeypatch.setenv("SIGNAL_NOTIFY_SCRIPT", "/legacy/script.sh")
    assert _resolve_signal_script() == "/boba/script.sh"


# --- _send_signal() no-op behaviour ---


@pytest.mark.asyncio
async def test_send_signal_noop_when_script_unset(monkeypatch, caplog):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    monkeypatch.setattr(wp, "_SIGNAL_SKIP_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="workers.worker_pool"):
        await wp._send_signal("hello")
    assert "BOBA_NOTIFY_SCRIPT not set" in caplog.text


@pytest.mark.asyncio
async def test_send_signal_warns_only_once(monkeypatch, caplog):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    monkeypatch.setattr(wp, "_SIGNAL_SKIP_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="workers.worker_pool"):
        await wp._send_signal("first")
        await wp._send_signal("second")
    assert caplog.text.count("BOBA_NOTIFY_SCRIPT not set") == 1


@pytest.mark.asyncio
async def test_send_signal_sets_warned_flag(monkeypatch):
    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    monkeypatch.setattr(wp, "_SIGNAL_SKIP_WARNED", False)
    await wp._send_signal("msg")
    assert wp._SIGNAL_SKIP_WARNED is True


@pytest.mark.asyncio
async def test_send_signal_respects_post_import_env_var(monkeypatch, tmp_path):
    """Regression: env var set after module import must be picked up at call time."""
    marker = tmp_path / "called"
    script = tmp_path / "notify.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)

    monkeypatch.delenv("BOBA_NOTIFY_SCRIPT", raising=False)
    monkeypatch.delenv("SIGNAL_NOTIFY_SCRIPT", raising=False)
    # Set the env var post-import — the fix ensures this is honored
    monkeypatch.setenv("BOBA_NOTIFY_SCRIPT", str(script))
    monkeypatch.setattr(wp, "_SIGNAL_SKIP_WARNED", False)

    await wp._send_signal("test message")
    assert marker.exists(), "Script was not invoked despite BOBA_NOTIFY_SCRIPT being set after import"
