"""Tests for SessionManager._flush_to_memory env-gated behavior."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from providers.session_manager import SessionManager, SessionState


@pytest.fixture
def session():
    s = SessionState(persona="test-persona", session_id="abc12345")
    s.total_input_tokens = 700
    s.total_output_tokens = 300
    s.run_count = 3
    return s


@pytest.fixture
def manager(tmp_path):
    return SessionManager({"sessions": {"state_file": str(tmp_path / "state.json")}})


def test_flush_is_noop_when_env_var_unset(manager, session, caplog):
    """Without BOBA_SESSION_FLUSH_SCRIPT, flush must be a no-op (no subprocess)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BOBA_SESSION_FLUSH_SCRIPT", None)
        with patch("providers.session_manager.subprocess.run") as mock_run:
            manager._flush_to_memory(session)
            mock_run.assert_not_called()


def test_flush_invokes_script_when_env_var_set(manager, session, tmp_path):
    """When BOBA_SESSION_FLUSH_SCRIPT is set, the script is invoked with the payload."""
    fake_script = str(tmp_path / "fake-flush.sh")
    with patch.dict(os.environ, {"BOBA_SESSION_FLUSH_SCRIPT": fake_script}):
        with patch("providers.session_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager._flush_to_memory(session)
            assert mock_run.called
            args = mock_run.call_args[0][0]
            assert args[0] == "bash"
            assert args[1] == fake_script
            assert args[2] == "memory.session_ended"
            # The payload is a JSON blob containing session fields
            assert "test-persona" in args[3]
            assert args[4] == "0"
            assert args[5] == "6"


def test_flush_swallows_subprocess_errors(manager, session, tmp_path):
    """A failing flush script must not raise out of _flush_to_memory."""
    fake_script = str(tmp_path / "broken.sh")
    with patch.dict(os.environ, {"BOBA_SESSION_FLUSH_SCRIPT": fake_script}):
        with patch("providers.session_manager.subprocess.run", side_effect=OSError("boom")):
            # Should not raise
            manager._flush_to_memory(session)
