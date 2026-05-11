"""Tests for SessionManager.track_usage (issue #25).

The bug observed in production: every persona in ~/.orchestrator/sessions.json
showed run_count=0 / total_tokens=0 because workers acquired slot sessions
(e.g. dev:1) but track_usage looked up by persona name and only ever updated
the primary entry. The fix: track_usage now accepts the acquired session
directly and credits *that* session.
"""

from __future__ import annotations

import json

from providers.session_manager import SessionManager, SessionState


def _make_manager(tmp_path):
    return SessionManager({
        "sessions": {
            "state_file": str(tmp_path / "sessions.json"),
            "token_tracking": True,
            "flush_to_memory": False,
            "max_tokens": 1_000_000,
        },
        "personas": {
            "dev": {"session_id": "00000000-0000-0000-0000-000000000001"},
        },
    })


def _claude_json(input_tokens=100, output_tokens=50) -> str:
    return json.dumps({
        "result": "ok",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })


def test_track_usage_increments_primary_when_no_session_passed(tmp_path):
    sm = _make_manager(tmp_path)
    sm.track_usage("dev", _claude_json(100, 50))
    primary = sm.get_session("dev")
    assert primary is not None
    assert primary.run_count == 1
    assert primary.total_input_tokens == 100
    assert primary.total_output_tokens == 50


def test_track_usage_credits_acquired_slot_not_primary(tmp_path):
    """The original bug: slot work was silently mis-credited to primary."""
    sm = _make_manager(tmp_path)
    # Acquire two parallel slots: primary is taken by acq1, acq2 is dev:1
    acq1 = sm.acquire_session("dev")
    acq2 = sm.acquire_session("dev")
    assert acq1.slot == 0
    assert acq2.slot >= 1

    sm.track_usage("dev", _claude_json(200, 100), session=acq2)
    # The slot session must be credited
    assert acq2.run_count == 1
    assert acq2.total_input_tokens == 200
    assert acq2.total_output_tokens == 100
    # Primary must NOT be credited for the slot's work
    assert acq1.run_count == 0
    assert acq1.total_input_tokens == 0


def test_track_usage_persists_to_state_file(tmp_path):
    sm = _make_manager(tmp_path)
    sm.track_usage("dev", _claude_json(10, 5))
    state = json.loads((tmp_path / "sessions.json").read_text())
    assert state["dev"]["run_count"] == 1
    assert state["dev"]["total_input_tokens"] == 10
    assert state["dev"]["total_output_tokens"] == 5


def test_track_usage_no_persona_session_is_a_noop(tmp_path):
    sm = _make_manager(tmp_path)
    # Unknown persona, no session passed → silent no-op (no exception)
    sm.track_usage("nonexistent", _claude_json(1, 1))


def test_track_usage_with_text_output_increments_run_count_only(tmp_path):
    """When claude returned text (not JSON), we can't parse tokens but still
    count the run so run_count != 0 in steady state."""
    sm = _make_manager(tmp_path)
    sm.track_usage("dev", "plain text response")
    primary = sm.get_session("dev")
    assert primary is not None
    assert primary.run_count == 1
    assert primary.total_input_tokens == 0
