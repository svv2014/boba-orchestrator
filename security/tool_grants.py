"""Per-subtask minimal tool assignment based on TaskType.

Each task type gets only the tools it needs — no worker inherits
the full tool set. This limits blast radius if a worker is compromised
or receives injected instructions.
"""

from __future__ import annotations

from providers.base import SubTask, TaskType

# Default tool grants per task type.
# These are the MAXIMUM tools a worker of this type can receive.
# The orchestrator may further restrict based on specific task constraints.
_DEFAULT_GRANTS: dict[TaskType, list[str]] = {
    TaskType.CODE: ["read", "write", "bash", "glob", "grep"],
    TaskType.TEST: ["read", "write", "bash", "glob", "grep"],
    TaskType.DOCS: ["read", "write", "glob", "grep"],
    TaskType.RESEARCH: ["read", "glob", "grep", "web_search", "web_fetch"],
}

# Tools that should NEVER be granted to workers
_DENIED_TOOLS = {
    "message",       # no sending messages
    "cron",          # no scheduling
    "config",        # no config changes
    "gateway",       # no gateway control
    "agent",         # no spawning sub-agents (orchestrator controls this)
}


def get_tool_grants(task: SubTask) -> list[str]:
    """Get the minimal tool set for a subtask.

    If the subtask has explicit tools set, intersect with the allowed
    set for its type. Otherwise, use the default grants for the type.

    Args:
        task: SubTask to compute grants for.

    Returns:
        List of allowed tool names.
    """
    allowed = set(_DEFAULT_GRANTS.get(task.type, []))

    if task.tools:
        # Intersect requested tools with what's allowed for this type
        requested = set(task.tools)
        granted = requested & allowed
        # Filter out denied tools
        granted -= _DENIED_TOOLS
        return sorted(granted)

    # Default: use type's grant list, minus denied
    return sorted(allowed - _DENIED_TOOLS)


def validate_tool_request(task: SubTask) -> list[str]:
    """Check if a subtask requests any denied or unexpected tools.

    Returns list of warnings (empty = all good).
    """
    warnings: list[str] = []

    if not task.tools:
        return warnings

    allowed = set(_DEFAULT_GRANTS.get(task.type, []))
    requested = set(task.tools)

    denied = requested & _DENIED_TOOLS
    if denied:
        warnings.append(f"Denied tools requested: {sorted(denied)}")

    outside_type = requested - allowed - _DENIED_TOOLS
    if outside_type:
        warnings.append(
            f"Tools outside {task.type.value} scope: {sorted(outside_type)}"
        )

    return warnings
