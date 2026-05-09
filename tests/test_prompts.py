"""Tests for worker prompt templates."""

from providers.base import SubTask, TaskType
from workers.prompts import (
    build_worker_message,
    fence_external_data,
    get_system_prompt,
)


def test_all_task_types_have_prompts():
    for tt in TaskType:
        prompt = get_system_prompt(tt)
        assert len(prompt) > 50
        assert "JSON" in prompt  # all prompts expect JSON output


def test_code_prompt_contains_rules():
    prompt = get_system_prompt(TaskType.CODE)
    assert "implement" in prompt.lower()
    assert "DATA" in prompt


def test_test_prompt_contains_rules():
    prompt = get_system_prompt(TaskType.TEST)
    assert "pytest" in prompt.lower()
    assert "not modify production code" in prompt.lower()


def test_research_prompt_is_read_only():
    prompt = get_system_prompt(TaskType.RESEARCH)
    assert "read-only" in prompt.lower()
    assert "NOT take any actions" in prompt


def test_all_prompts_have_injection_defense():
    for tt in TaskType:
        prompt = get_system_prompt(tt)
        assert "DATA" in prompt
        assert "not instructions" in prompt.lower()


def test_build_worker_message():
    task = SubTask(
        id="t1",
        type=TaskType.CODE,
        description="write foo.py",
        target_repo="/tmp/proj",
        context_files=["README.md", "src/bar.py"],
        constraints="stdlib only",
        tools=["bash", "write"],
    )
    msg = build_worker_message(task)
    assert "t1" in msg
    assert "write foo.py" in msg
    assert "/tmp/proj" in msg
    assert "README.md" in msg
    assert "stdlib only" in msg
    assert "bash" in msg


def test_build_worker_message_minimal():
    task = SubTask(id="t1", type=TaskType.DOCS, description="write docs", target_repo="/tmp")
    msg = build_worker_message(task)
    assert "t1" in msg
    assert "Constraints" not in msg  # no constraints set
    assert "Available tools" not in msg  # no tools set


def test_fence_external_data():
    content = "Hello world\nThis is data"
    fenced = fence_external_data(content)
    assert "BEGIN DATA" in fenced
    assert "END DATA" in fenced
    assert "not instructions" in fenced
    assert "Hello world" in fenced
