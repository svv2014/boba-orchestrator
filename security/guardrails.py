"""Runtime guardrails for worker and orchestrator safety.

Prevents runaway workers, cost overruns, and scope violations.
All limits are configurable via orchestrator.yaml under 'guardrails:'.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuardrailConfig:
    """Safety limits for an orchestrator run."""

    # Worker limits
    max_worker_timeout_seconds: int = 1800      # hard cap — no worker runs longer than this
    max_subtasks_per_plan: int = 5              # planner can't create more than this
    max_parallel_workers: int = 3               # concurrency limit

    # Cost limits
    max_tasks_per_run: int = 10                 # queue mode circuit breaker
    max_consecutive_failures: int = 3           # stop queue after N failures in a row
    max_total_worker_seconds: int = 7200        # 2 hour hard cap on total worker time per run

    # Scope limits
    allowed_repos: list[str] = field(default_factory=list)  # if set, workers can only run in these dirs
    blocked_commands: list[str] = field(default_factory=lambda: [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf .",
        "DROP TABLE",
        "DROP DATABASE",
        "format ",
        "mkfs",
        "> /dev/sd",
    ])

    @classmethod
    def from_config(cls, config: dict) -> GuardrailConfig:
        """Load guardrails from orchestrator config."""
        g = config.get("guardrails", {})
        projects = config.get("projects", [])

        # Build effective allowed_repos as the union of explicit config entries and
        # project paths. Previously explicit entries replaced project-derived paths;
        # now both sources are merged so adding an extra path doesn't silently drop
        # the primary project list. Order: explicit first, then projects-derived.
        explicit = list(g.get("allowed_repos") or [])
        derived = [p.get("path", "") for p in projects if p.get("path")]
        seen: set[str] = set()
        allowed: list[str] = []
        for p in explicit + derived:
            if p and p not in seen:
                seen.add(p)
                allowed.append(p)

        return cls(
            max_worker_timeout_seconds=g.get("max_worker_timeout_seconds", 1800),
            max_subtasks_per_plan=g.get("max_subtasks_per_plan", 5),
            max_parallel_workers=g.get("max_parallel_workers", 3),
            max_tasks_per_run=g.get("max_tasks_per_run", 10),
            max_consecutive_failures=g.get("max_consecutive_failures", 3),
            max_total_worker_seconds=g.get("max_total_worker_seconds", 7200),
            allowed_repos=allowed,
            blocked_commands=g.get("blocked_commands", GuardrailConfig.__dataclass_fields__["blocked_commands"].default_factory()),
        )


@dataclass
class RunBudget:
    """Tracks resource usage during an orchestrator run."""

    started_at: float = field(default_factory=time.monotonic)
    tasks_completed: int = 0
    tasks_failed: int = 0
    consecutive_failures: int = 0
    total_worker_seconds: float = 0.0

    def record_success(self, duration_seconds: float) -> None:
        self.tasks_completed += 1
        self.consecutive_failures = 0
        self.total_worker_seconds += duration_seconds

    def record_failure(self, duration_seconds: float) -> None:
        self.tasks_failed += 1
        self.consecutive_failures += 1
        self.total_worker_seconds += duration_seconds

    def should_stop(self, guardrails: GuardrailConfig) -> Optional[str]:
        """Check if the run should stop. Returns reason string or None."""
        if self.consecutive_failures >= guardrails.max_consecutive_failures:
            return (
                f"Circuit breaker: {self.consecutive_failures} consecutive failures "
                f"(limit: {guardrails.max_consecutive_failures})"
            )

        total_tasks = self.tasks_completed + self.tasks_failed
        if total_tasks >= guardrails.max_tasks_per_run:
            return f"Task limit reached: {total_tasks}/{guardrails.max_tasks_per_run}"

        if self.total_worker_seconds >= guardrails.max_total_worker_seconds:
            return (
                f"Time budget exhausted: {int(self.total_worker_seconds)}s "
                f"(limit: {guardrails.max_total_worker_seconds}s)"
            )

        return None

    @property
    def summary(self) -> str:
        elapsed = int(time.monotonic() - self.started_at)
        return (
            f"{self.tasks_completed} succeeded, {self.tasks_failed} failed, "
            f"{int(self.total_worker_seconds)}s worker time, {elapsed}s elapsed"
        )


def validate_worker_timeout(requested: int, guardrails: GuardrailConfig) -> int:
    """Cap worker timeout to guardrail maximum."""
    if requested <= 0:
        return guardrails.max_worker_timeout_seconds
    return min(requested, guardrails.max_worker_timeout_seconds)


def _resolve_worktree(repo_path: str) -> str:
    """If repo_path is a git worktree, return the canonical repo path; else return repo_path."""
    git_marker = os.path.join(repo_path, ".git")
    if not os.path.isfile(git_marker):
        return repo_path
    try:
        with open(git_marker) as fh:
            first_line = fh.readline().strip()
    except OSError:
        return repo_path
    if not first_line.startswith("gitdir: "):
        return repo_path
    gitdir = first_line[len("gitdir: "):]
    # Expected: <canonical>/.git/worktrees/<name>
    marker = os.sep + ".git" + os.sep + "worktrees" + os.sep
    if marker not in gitdir:
        return repo_path
    canonical = gitdir.split(marker, 1)[0]
    return canonical


def validate_target_repo(repo_path: str, guardrails: GuardrailConfig) -> Optional[str]:
    """Validate that the target repo is in the allowed list.

    Returns error string if invalid, None if OK.
    """
    if not guardrails.allowed_repos:
        return None  # no allowlist = no restriction

    resolved = _resolve_worktree(repo_path)
    abs_repo = os.path.abspath(resolved)
    for allowed in guardrails.allowed_repos:
        abs_allowed = os.path.abspath(allowed)
        if abs_repo == abs_allowed or abs_repo.startswith(abs_allowed + os.sep):
            return None

    if resolved != repo_path:
        return (
            f"Target repo '{repo_path}' (resolved to '{resolved}') "
            f"is not in allowed_repos: {guardrails.allowed_repos}"
        )
    return f"Target repo '{repo_path}' is not in allowed_repos: {guardrails.allowed_repos}"


def validate_subtask_count(count: int, guardrails: GuardrailConfig) -> Optional[str]:
    """Check if subtask count is within limits."""
    if count > guardrails.max_subtasks_per_plan:
        return f"Plan has {count} subtasks (max: {guardrails.max_subtasks_per_plan})"
    return None


def validate_command(text: str, guardrails: GuardrailConfig) -> Optional[str]:
    """Check text against the blocked_commands list (case-insensitive substring match).

    Returns an error string when a blocked pattern is found, None otherwise.
    This is a defense-in-depth signal — not a hard sandbox boundary.
    """
    lower = text.lower()
    for pattern in guardrails.blocked_commands:
        if pattern.lower() in lower:
            return f"Blocked command pattern detected: '{pattern}'"
    return None
