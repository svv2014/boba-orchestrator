"""Tests for notifier.telegram_notifier — notification delivery."""

from __future__ import annotations

import os
from typing import Optional

import pytest

from notifier.telegram_notifier import NotifyResult, notify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(channel: str = "telegram", chat_id: str = "12345") -> dict:
    return {"notify": {"channel": channel, "chat_id": chat_id}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_notification_writes_to_log_file(tmp_path, monkeypatch):
    """Happy path: notify() writes to the log file and returns sent=True."""
    log_file = tmp_path / "notifications.log"
    monkeypatch.setattr("notifier.telegram_notifier._LOG_PATH", str(log_file))

    result = await notify("hello world", _config())

    assert result.sent is True
    assert result.error is None
    assert log_file.exists()
    content = log_file.read_text()
    assert "hello world" in content


@pytest.mark.asyncio
async def test_no_notify_config_returns_sent_false():
    """Config dict with no 'notify' key — returns sent=False."""
    result = await notify("test message", {})

    assert result.sent is False
    assert result.error is not None
    assert "no notify config" in result.error


@pytest.mark.asyncio
async def test_message_content_written_correctly(tmp_path, monkeypatch):
    """Log entry contains channel, chat_id, and the message text."""
    log_file = tmp_path / "notifications.log"
    monkeypatch.setattr("notifier.telegram_notifier._LOG_PATH", str(log_file))

    await notify("M5 completed: 3 files changed", _config(channel="telegram", chat_id="99"))

    content = log_file.read_text()
    assert "channel=telegram" in content
    assert "chat_id=99" in content
    assert "M5 completed: 3 files changed" in content


@pytest.mark.asyncio
async def test_multiple_notifications_append_to_log(tmp_path, monkeypatch):
    """Successive calls append entries rather than overwriting."""
    log_file = tmp_path / "notifications.log"
    monkeypatch.setattr("notifier.telegram_notifier._LOG_PATH", str(log_file))

    await notify("first message", _config())
    await notify("second message", _config())

    content = log_file.read_text()
    assert "first message" in content
    assert "second message" in content


@pytest.mark.asyncio
async def test_log_entry_contains_timestamp(tmp_path, monkeypatch):
    """Each log entry is prefixed with a timestamp in ISO-like format."""
    log_file = tmp_path / "notifications.log"
    monkeypatch.setattr("notifier.telegram_notifier._LOG_PATH", str(log_file))

    await notify("timestamped", _config())

    content = log_file.read_text()
    # Timestamp looks like [2026-03-27T...]
    assert "[20" in content  # year starts with 20xx


@pytest.mark.asyncio
async def test_unwritable_path_returns_sent_false(monkeypatch):
    """If the log path is unwritable, notify() returns sent=False with error."""
    monkeypatch.setattr(
        "notifier.telegram_notifier._LOG_PATH",
        "/nonexistent_dir/orchestrator.log",
    )

    result = await notify("should fail", _config())

    assert result.sent is False
    assert result.error is not None
    assert "failed to write log" in result.error


@pytest.mark.asyncio
async def test_notify_config_with_only_channel(tmp_path, monkeypatch):
    """Partial notify config (no chat_id) still succeeds with defaults."""
    log_file = tmp_path / "notifications.log"
    monkeypatch.setattr("notifier.telegram_notifier._LOG_PATH", str(log_file))

    config = {"notify": {"channel": "slack"}}
    result = await notify("partial config test", config)

    assert result.sent is True
    content = log_file.read_text()
    assert "channel=slack" in content
    assert "chat_id=unknown" in content
