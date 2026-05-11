"""Tests for the transient-failure classifier in claude_cli_backend."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
# Transient retry: stdout-only error path (issue #28)
# ---------------------------------------------------------------------------

class TestTransientRetryStdoutPath:
    """Verify that _run_claude triggers the transient retry when a 429/rate-limit
    message appears only in stdout with empty stderr."""

    def test_429_in_stdout_empty_stderr_is_recoverable(self):
        """Effective error body falls back to stdout when stderr is empty."""
        stdout_body = "HTTP 429 Too Many Requests"
        # Mirror the effective = (stderr or stdout or b"") logic from _run_claude
        stderr = b""
        stdout = stdout_body.encode()
        effective = (stderr or stdout or b"").decode("utf-8", errors="replace")
        assert _is_recoverable(effective)

    def test_stderr_wins_when_populated(self):
        """When stderr is non-empty it still takes priority over stdout."""
        stderr = b"Internal server error (500)"
        stdout = b"some partial output"
        effective = (stderr or stdout or b"").decode("utf-8", errors="replace")
        assert _is_recoverable(effective)

    def test_permanent_error_in_stdout_not_retried(self):
        """A non-recoverable error in stdout is still classified permanent."""
        stderr = b""
        stdout = b"SyntaxError: unexpected token in agent code"
        effective = (stderr or stdout or b"").decode("utf-8", errors="replace")
        assert not _is_recoverable(effective)

    @pytest.mark.asyncio
    async def test_run_claude_retries_on_429_in_stdout(self):
        """_run_claude must attempt a retry when 429 appears only on stdout."""
        rate_limit_msg = b"HTTP 429 Too Many Requests"

        # First process: exit 1, stdout=429, stderr empty
        first_proc = MagicMock()
        first_proc.returncode = 1
        first_proc.pid = 1234
        first_proc.communicate = AsyncMock(return_value=(rate_limit_msg, b""))

        # Second process: success
        second_proc = MagicMock()
        second_proc.returncode = 0
        second_proc.pid = 1235
        second_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        call_count = 0

        async def fake_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return first_proc if call_count == 1 else second_proc

        with (
            patch(
                "providers.claude_cli_backend.asyncio.create_subprocess_exec",
                side_effect=fake_subprocess,
            ),
            patch(
                "providers.claude_cli_backend.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch(
                "providers.claude_cli_backend._resolve_claude_bin",
                return_value="claude",
            ),
            patch(
                "providers.claude_cli_backend._session_exists",
                return_value=False,
            ),
            patch.dict("os.environ", {"CLAUDE_RETRY_DELAY_SECONDS": "0"}),
        ):
            from providers.claude_cli_backend import _run_claude

            result = await _run_claude("hello", cwd="/tmp", timeout=30)

        assert call_count == 2, "expected exactly one retry after the 429-in-stdout failure"
        assert result == "ok"
