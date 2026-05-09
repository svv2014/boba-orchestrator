"""Telegram notification sender for orchestrator runs.

Currently writes to a local log file. Real Telegram delivery is a
follow-up: see issues filed against this repo for the integration plan.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

_LOG_PATH = "/tmp/orchestrator-notifications.log"


@dataclass
class NotifyResult:
    """Result of a notification attempt."""

    sent: bool
    error: Optional[str] = None


async def notify(message: str, config: dict) -> NotifyResult:
    """Send a notification via the configured channel.

    Currently writes to a log file at /tmp/orchestrator-notifications.log.
    Real Telegram delivery is a follow-up — see open issues for status.

    Args:
        message: The notification text to send.
        config: Full orchestrator config dict (reads config["notify"]).

    Returns:
        NotifyResult indicating success or failure.
    """
    notify_config = config.get("notify")
    if not notify_config:
        return NotifyResult(sent=False, error="no notify config")

    channel = notify_config.get("channel", "unknown")
    chat_id = notify_config.get("chat_id", "unknown")

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    log_line = f"[{timestamp}] channel={channel} chat_id={chat_id}\n{message}\n---\n"

    try:
        with open(_LOG_PATH, "a") as f:
            f.write(log_line)
        return NotifyResult(sent=True)
    except OSError as e:
        return NotifyResult(sent=False, error=f"failed to write log: {e}")
