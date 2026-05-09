"""Tests for tool grants — minimal tool assignment per task type."""

from typing import Optional

from providers.base import SubTask, TaskType
from security.tool_grants import get_tool_grants, validate_tool_request


def _task(type: TaskType, tools: Optional[list] = None) -> SubTask:
    t = SubTask(id="t1", type=type, description="test", target_repo="/tmp")
    if tools:
        t.tools = tools
    return t


# --- Default grants ---


def test_code_gets_write_and_bash():
    grants = get_tool_grants(_task(TaskType.CODE))
    assert "write" in grants
    assert "bash" in grants
    assert "read" in grants


def test_test_gets_write_and_bash():
    grants = get_tool_grants(_task(TaskType.TEST))
    assert "write" in grants
    assert "bash" in grants


def test_docs_gets_write_no_bash():
    grants = get_tool_grants(_task(TaskType.DOCS))
    assert "write" in grants
    assert "bash" not in grants


def test_research_gets_web_no_write():
    grants = get_tool_grants(_task(TaskType.RESEARCH))
    assert "web_search" in grants
    assert "web_fetch" in grants
    assert "write" not in grants
    assert "bash" not in grants


# --- Denied tools never granted ---


def test_message_never_granted():
    grants = get_tool_grants(_task(TaskType.CODE, tools=["read", "write", "message"]))
    assert "message" not in grants


def test_cron_never_granted():
    grants = get_tool_grants(_task(TaskType.CODE, tools=["read", "cron"]))
    assert "cron" not in grants


def test_agent_never_granted():
    grants = get_tool_grants(_task(TaskType.CODE, tools=["read", "agent"]))
    assert "agent" not in grants


# --- Intersection with requested tools ---


def test_explicit_tools_intersected():
    # Request only read and write for a CODE task
    grants = get_tool_grants(_task(TaskType.CODE, tools=["read", "write"]))
    assert "read" in grants
    assert "write" in grants
    assert "bash" not in grants  # not requested


def test_requested_tool_outside_type_filtered():
    # Research worker requests bash — not in research grants
    grants = get_tool_grants(_task(TaskType.RESEARCH, tools=["read", "bash"]))
    assert "read" in grants
    assert "bash" not in grants


# --- Validation ---


def test_validate_no_warnings_default():
    warnings = validate_tool_request(_task(TaskType.CODE))
    assert warnings == []


def test_validate_warns_denied_tools():
    warnings = validate_tool_request(_task(TaskType.CODE, tools=["read", "message"]))
    assert any("Denied" in w for w in warnings)


def test_validate_warns_outside_scope():
    warnings = validate_tool_request(_task(TaskType.DOCS, tools=["bash"]))
    assert any("outside" in w.lower() for w in warnings)


def test_validate_no_warnings_valid_request():
    warnings = validate_tool_request(_task(TaskType.CODE, tools=["read", "write"]))
    assert warnings == []
