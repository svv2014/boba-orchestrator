"""Session manager — tracks persistent claude -p sessions per persona.

Handles:
- Session creation with UUIDs from config
- Token tracking across runs (parsed from claude -p JSON output)
- Automatic rotation when approaching token limit
- Flush to boba-memory before rotation
- Persistence of session state to disk
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 150_000
DEFAULT_STATE_FILE = "~/.orchestrator/sessions.json"
BOBA_EVENT_URL = "http://localhost:8765"

# Module-level singleton — initialized once, accessed by workers
_instance: "SessionManager | None" = None


def init_session_manager(config: Dict[str, Any]) -> "SessionManager":
    """Initialize the global session manager from orchestrator config."""
    global _instance
    _instance = SessionManager(config)
    return _instance


def get_session_manager() -> "SessionManager | None":
    """Get the global session manager (None if not initialized)."""
    return _instance


class SessionState:
    """State for a single persona session."""

    def __init__(self, persona: str, session_id: str, resume: bool = True,
                 slot: int = 0):
        self.persona = persona
        self.session_id = session_id
        self.resume = resume
        self.slot = slot  # 0 = primary, 1+ = parallel worker slots
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.run_count = 0
        self.created_at = time.time()
        self.last_used_at = time.time()
        self.in_use = False  # True while a worker is actively using this session

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def track_usage(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.run_count += 1
        self.last_used_at = time.time()

    def to_dict(self) -> dict:
        return {
            "persona": self.persona,
            "session_id": self.session_id,
            "resume": self.resume,
            "slot": self.slot,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        s = cls(data["persona"], data["session_id"], data.get("resume", True),
                data.get("slot", 0))
        s.total_input_tokens = data.get("total_input_tokens", 0)
        s.total_output_tokens = data.get("total_output_tokens", 0)
        s.run_count = data.get("run_count", 0)
        s.created_at = data.get("created_at", time.time())
        s.last_used_at = data.get("last_used_at", time.time())
        return s


class SessionManager:
    """Manages persistent sessions across orchestrator runs."""

    def __init__(self, config: Dict[str, Any]):
        sessions_config = config.get("sessions", {})
        self.max_tokens = sessions_config.get("max_tokens", DEFAULT_MAX_TOKENS)
        self.token_tracking = sessions_config.get("token_tracking", True)
        self.flush_to_memory = sessions_config.get("flush_to_memory", True)
        self.state_file = os.path.expanduser(
            sessions_config.get("state_file", DEFAULT_STATE_FILE)
        )

        # Load persona configs
        self.persona_configs: Dict[str, Dict[str, Any]] = config.get("personas", {})

        # Load persisted state or initialize from config
        self.sessions: Dict[str, SessionState] = {}
        self._load_state()

        # Ensure all configured personas have sessions
        for persona, pcfg in self.persona_configs.items():
            if persona not in self.sessions:
                self.sessions[persona] = SessionState(
                    persona=persona,
                    session_id=pcfg.get("session_id", str(uuid.uuid4())),
                    resume=pcfg.get("resume_session", True),
                )

        # Planner session
        planner_cfg = config.get("planner", {})
        if "planner" not in self.sessions and planner_cfg.get("session_id"):
            self.sessions["planner"] = SessionState(
                persona="planner",
                session_id=planner_cfg["session_id"],
                resume=planner_cfg.get("resume_session", True),
            )

    def get_session(self, persona: str) -> Optional[SessionState]:
        """Get primary session for a persona. Returns None if no session configured."""
        return self.sessions.get(persona)

    def get_session_args(self, persona: str) -> Dict[str, Any]:
        """Get session_id and resume args for _run_claude(). For sequential use only."""
        session = self.get_session(persona)
        if not session:
            return {}
        return {
            "session_id": session.session_id,
            "resume": session.resume,
        }

    def acquire_session(self, persona: str) -> SessionState:
        """Acquire a session for parallel work. Returns an available slot.

        If the primary session is free, use it. Otherwise, create or reuse
        a parallel slot (persona:1, persona:2, etc.). Each slot has its own
        session_id so multiple workers can run the same persona concurrently.
        """
        # Try primary session first
        primary = self.sessions.get(persona)
        if primary and not primary.in_use:
            primary.in_use = True
            logger.info("Acquired primary session for %s (%s)", persona, primary.session_id[:8])
            return primary

        # Find an available parallel slot
        for key, session in self.sessions.items():
            if key.startswith(f"{persona}:") and not session.in_use:
                session.in_use = True
                logger.info("Acquired slot session for %s (%s)", key, session.session_id[:8])
                return session

        # No slots available — create a new one
        slot_num = sum(1 for k in self.sessions if k.startswith(f"{persona}:")) + 1
        slot_key = f"{persona}:{slot_num}"
        new_session = SessionState(
            persona=persona,
            session_id=str(uuid.uuid4()),
            resume=True,
            slot=slot_num,
        )
        new_session.in_use = True
        self.sessions[slot_key] = new_session
        logger.info(
            "Created new slot %s for %s (%s)",
            slot_key, persona, new_session.session_id[:8],
        )
        self._save_state()
        return new_session

    def release_session(self, session: SessionState) -> None:
        """Release a session after work is done. Checks for rotation."""
        session.in_use = False
        session.last_used_at = time.time()
        self._save_state()

        if session.total_tokens >= self.max_tokens:
            slot_key = (
                session.persona if session.slot == 0
                else f"{session.persona}:{session.slot}"
            )
            logger.warning(
                "Session %s at %d tokens — rotating",
                slot_key, session.total_tokens,
            )
            self.rotate_session(slot_key)

    def track_usage(
        self,
        persona: str,
        claude_output: str,
        session: Optional[SessionState] = None,
    ) -> None:
        """Parse token usage from claude -p JSON output and track it.

        If ``session`` is provided, usage is recorded against that specific
        SessionState (used by workers that acquired a parallel slot). Otherwise
        we fall back to the persona's primary session.
        """
        if not self.token_tracking:
            return
        if session is None:
            session = self.get_session(persona)
        if not session:
            return

        try:
            # claude -p --output-format json returns usage info
            data = json.loads(claude_output)
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0) + usage.get(
                "cache_read_input_tokens", 0
            )
            output_tokens = usage.get("output_tokens", 0)
            session.track_usage(input_tokens, output_tokens)
            logger.info(
                "Session %s (%s): +%d/%d tokens, total %d/%d",
                persona,
                session.session_id[:8],
                input_tokens,
                output_tokens,
                session.total_tokens,
                self.max_tokens,
            )
        except (json.JSONDecodeError, KeyError):
            # Text output mode — can't track tokens, just count runs
            session.run_count += 1
            session.last_used_at = time.time()

        self._save_state()

        # Check if rotation needed (only rotate when this is the persona's
        # primary session — slot sessions are short-lived and managed by
        # acquire/release).
        primary = self.get_session(persona)
        if (
            primary is session
            and session.total_tokens >= self.max_tokens
        ):
            logger.warning(
                "Session %s (%s) at %d tokens — rotating",
                persona,
                session.session_id[:8],
                session.total_tokens,
            )
            self.rotate_session(persona)

    def rotate_session(self, persona: str) -> Optional[str]:
        """Flush session to memory, create new session. Returns new session_id."""
        session = self.get_session(persona)
        if not session:
            return None

        old_id = session.session_id
        logger.info(
            "Rotating session %s (%s): %d tokens across %d runs",
            persona,
            old_id[:8],
            session.total_tokens,
            session.run_count,
        )

        # Flush to boba-memory via event bus
        if self.flush_to_memory:
            self._flush_to_memory(session)

        # Create new session
        new_id = str(uuid.uuid4())
        self.sessions[persona] = SessionState(
            persona=persona,
            session_id=new_id,
            resume=True,
        )

        self._save_state()
        logger.info(
            "Session rotated: %s %s → %s",
            persona,
            old_id[:8],
            new_id[:8],
        )
        return new_id

    def _flush_to_memory(self, session: SessionState) -> None:
        """Fire memory.session_ended event to an external memory store.

        The flush is opt-in via the ``BOBA_SESSION_FLUSH_SCRIPT`` env var.
        When unset, this is a no-op — the orchestrator does not phone home
        or assume any particular memory backend. When set, the script is
        invoked as ``<script> memory.session_ended <payload_json> 0 6`` and
        its stdout/stderr are captured.

        Originally hard-wired to a specific operator's event-bus script.
        Decoupled to keep the open-source distribution backend-agnostic.
        """
        flush_script = os.environ.get("BOBA_SESSION_FLUSH_SCRIPT", "").strip()
        if not flush_script:
            logger.debug(
                "Session flush skipped — BOBA_SESSION_FLUSH_SCRIPT unset (session=%s)",
                session.persona,
            )
            return

        try:
            payload = json.dumps({
                "session_id": session.session_id,
                "persona": session.persona,
                "total_tokens": session.total_tokens,
                "run_count": session.run_count,
                "created_at": session.created_at,
            })

            subprocess.run(
                [
                    "bash",
                    os.path.expanduser(flush_script),
                    "memory.session_ended",
                    payload,
                    "0",
                    "6",
                ],
                capture_output=True,
                timeout=10,
            )
            logger.info(
                "Flushed session %s via BOBA_SESSION_FLUSH_SCRIPT",
                session.persona,
            )
        except Exception as e:
            logger.error("Failed to flush session %s to memory: %s", session.persona, e)

    def _load_state(self) -> None:
        """Load persisted session state from disk."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file) as f:
                    data = json.load(f)
                for key, sdata in data.items():
                    self.sessions[key] = SessionState.from_dict(sdata)
                logger.info("Loaded %d session states from %s", len(self.sessions), self.state_file)
        except Exception as e:
            logger.warning("Could not load session state: %s", e)

    def _save_state(self) -> None:
        """Persist session state to disk."""
        try:
            Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(
                    {k: v.to_dict() for k, v in self.sessions.items()},
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.warning("Could not save session state: %s", e)

    def status(self) -> Dict[str, Any]:
        """Return current session status for all personas."""
        return {
            persona: {
                "session_id": s.session_id[:8] + "...",
                "tokens": f"{s.total_tokens}/{self.max_tokens}",
                "runs": s.run_count,
                "age_hours": round((time.time() - s.created_at) / 3600, 1),
            }
            for persona, s in self.sessions.items()
        }
