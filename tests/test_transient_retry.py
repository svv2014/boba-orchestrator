"""Tests for the transient-failure classifier in claude_cli_backend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from providers import claude_cli_backend
from providers.claude_cli_backend import _is_recoverable


# ---------------------------------------------------------------------------
# Recoverable (transient) cases
# ---------------------------------------------------------------------------

class TestRecoverable:
    def test_rate_limit_text(self):
        assert _is_recoverable("Error: rate limit exceeded, please retry")

    def test_429_status(self):
        assert _is_recoverable("HTTP 429 Too Many Requests")

    def test_server_error(self):
        assert _is_recoverable("Internal server error (500)")

    def test_5xx_literal(self):
        assert _is_recoverable("5xx error from upstream")

    def test_connection_refused(self):
        assert _is_recoverable("Connection refused to api.anthropic.com")

    def test_connection_timed_out(self):
        assert _is_recoverable("Connection timed out after 30s")

    def test_network_error(self):
        assert _is_recoverable("Network error: could not reach host")

    def test_timeout_generic(self):
        assert _is_recoverable("Request timeout")

    def test_auth_flake_401_with_auth(self):
        assert _is_recoverable("401 Unauthorized — auth token may have expired, retrying")

    def test_case_insensitive(self):
        assert _is_recoverable("RATE LIMIT exceeded")
        assert _is_recoverable("SERVER ERROR encountered")


# ---------------------------------------------------------------------------
# Non-recoverable (permanent) cases
# ---------------------------------------------------------------------------

class TestPermanent:
    def test_tool_denied(self):
        assert not _is_recoverable("Tool denied: bash is not permitted")

    def test_sandbox_rejection(self):
        assert not _is_recoverable("Sandbox rejection: unsafe command")

    def test_permission_denied_by_user(self):
        assert not _is_recoverable("Permission denied by user")

    def test_syntax_error(self):
        assert not _is_recoverable("SyntaxError: unexpected token in agent code")

    def test_context_window(self):
        assert not _is_recoverable("Context window exceeded — reduce input length")

    def test_context_length(self):
        assert not _is_recoverable("context length limit reached")

    def test_maximum_context(self):
        assert not _is_recoverable("maximum context tokens exceeded")

    def test_too_many_tokens(self):
        assert not _is_recoverable("too many tokens in the prompt")

    def test_unknown_error_is_permanent(self):
        assert not _is_recoverable("Some completely unknown fatal error occurred")

    def test_bare_401_without_auth_context(self):
        """A 401 without auth context is NOT classified as transient."""
        assert not _is_recoverable("Error code 401")

    def test_permanent_wins_over_recoverable_signals(self):
        """If a message contains both patterns, permanent takes priority."""
        assert not _is_recoverable("syntax error during rate limit handling")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        assert not _is_recoverable("")

    def test_whitespace_only(self):
        assert not _is_recoverable("   \n\t  ")

    def test_multiline_stderr(self):
        stderr = "Attempting connection...\nError: connection refused\nAborting."
        assert _is_recoverable(stderr)

    def test_multiline_permanent(self):
        stderr = "Starting claude...\ntoo many tokens in prompt\nExiting."
        assert not _is_recoverable(stderr)


# ---------------------------------------------------------------------------
# Integration: _run_claude retry path with 429 in stdout, empty stderr
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 99999

    async def communicate(self):
        return self._stdout, self._stderr


class TestRunClaudeStdoutRetry:
    """Verify that _run_claude triggers the transient retry when a recoverable
    error (e.g. 429) appears only on stdout with an empty stderr stream."""

    def test_429_in_stdout_empty_stderr_triggers_retry(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CLI_PATH", "/fake/claude")
        # Zero out the retry delay so the test does not actually wait 30 s.
        monkeypatch.setenv("CLAUDE_RETRY_DELAY_SECONDS", "0")

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: 429 on stdout, stderr empty — the bug scenario.
                return _FakeProc(
                    stdout=b"HTTP 429 Too Many Requests",
                    stderr=b"",
                    returncode=1,
                )
            # Second attempt (the retry): success.
            return _FakeProc(stdout=b"ok", stderr=b"", returncode=0)

        with patch.object(
            claude_cli_backend.asyncio,
            "create_subprocess_exec",
            new=AsyncMock(side_effect=fake_exec),
        ):
            asyncio.run(claude_cli_backend._run_claude("hello"))

        assert call_count == 2, (
            "Expected exactly 2 subprocess calls (initial + retry), "
            f"got {call_count}. Retry was not triggered."
        )
