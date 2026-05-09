"""Anthropic (Claude) backend — first-class provider for boba-orchestrator.

Uses the Anthropic Python SDK for both planner and worker calls.
This is the reference implementation that other backends should follow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Optional

import anthropic

from .base import (
    PlanResult,
    SubTask,
    TaskStatus,
    TaskType,
    WorkerResult,
)
from security.tool_grants import get_tool_grants
from workers.prompts import build_worker_message, get_system_prompt

logger = logging.getLogger(__name__)

# Default models — overridden by config
DEFAULT_PLANNER_MODEL = "claude-opus-4-6"
DEFAULT_WORKER_MODEL = "claude-sonnet-4-6"

DEFAULT_TIMEOUT = 120  # seconds

# System prompts
PLANNER_SYSTEM = """You are a task planner for a multi-agent orchestration system.
Given project state, you select the highest-value next task and decompose it
into 1-3 independent subtasks that can be executed in parallel by worker agents.

Respond with valid JSON matching this schema:
{
  "task_summary": "brief description of what we're building",
  "reasoning": "why this task, why this decomposition",
  "project_name": "which project",
  "milestone": "which milestone",
  "subtasks": [
    {
      "id": "unique-id",
      "type": "code|test|docs|research",
      "description": "what the worker should do",
      "target_repo": "path to repo",
      "context_files": ["files worker should read first"],
      "constraints": "any restrictions",
      "tools": ["bash", "write", "read"],
      "estimated_seconds": 300,
      "complexity": "low|medium|high"
    }
  ]
}"""

# Regex to find the first JSON object in text: matches opening { through balanced closing }
_JSON_OBJECT_RE = re.compile(r"\{", re.DOTALL)


class AnthropicPlanner:
    """Planner backend using Claude (typically Opus)."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model = config.get("model", DEFAULT_PLANNER_MODEL)
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT)
        self.client = anthropic.AsyncAnthropic()

    async def plan(self, context: str, instruction: str) -> PlanResult:
        message = await asyncio.wait_for(
            self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=PLANNER_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"## Project State\n\n{context}\n\n"
                            f"## Instruction\n\n{instruction}"
                        ),
                    }
                ],
            ),
            timeout=self.timeout,
        )

        raw = message.content[0].text
        data = _parse_json(raw)

        subtasks = [
            SubTask(
                id=st["id"],
                type=TaskType(st.get("type", "code")),
                description=st["description"],
                target_repo=st.get("target_repo", ""),
                context_files=st.get("context_files", []),
                constraints=st.get("constraints", ""),
                tools=st.get("tools", []),
            )
            for st in data.get("subtasks", [])
        ]

        return PlanResult(
            task_summary=data.get("task_summary", ""),
            subtasks=subtasks,
            reasoning=data.get("reasoning", ""),
            project_name=data.get("project_name", ""),
            milestone=data.get("milestone", ""),
        )

    async def select_task(self, context: str) -> str:
        message = await asyncio.wait_for(
            self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=(
                    "You are a task selector. Given project states, identify the "
                    "single highest-value next task. Respond with just the task "
                    "description — no JSON, no explanation."
                ),
                messages=[
                    {"role": "user", "content": context},
                ],
            ),
            timeout=self.timeout,
        )
        return message.content[0].text.strip()


class AnthropicWorker:
    """Worker backend using Claude (typically Sonnet)."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.model = config.get("model", DEFAULT_WORKER_MODEL)
        self.timeout = config.get("timeout", DEFAULT_TIMEOUT)
        self.client = anthropic.AsyncAnthropic()

    async def execute(self, task: SubTask) -> WorkerResult:
        start = time.monotonic()
        raw: Optional[str] = None

        # Get per-TaskType system prompt and structured user message
        system_prompt = get_system_prompt(task.type)
        user_message = build_worker_message(task)

        # Compute minimal tool grants for this task
        granted_tools = get_tool_grants(task)
        if granted_tools:
            user_message += f"\n\nGranted tools: {', '.join(granted_tools)}"

        try:
            message = await asyncio.wait_for(
                self.client.messages.create(
                    model=self.model,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=[
                        {
                            "role": "user",
                            "content": user_message,
                        }
                    ],
                ),
                timeout=self.timeout,
            )

            raw = message.content[0].text
            data = _parse_json(raw)
            duration = int((time.monotonic() - start) * 1000)

            return WorkerResult(
                task_id=task.id,
                status=TaskStatus(data.get("status", "done")),
                files_changed=data.get("files_changed", []),
                output=data.get("output", raw),
                notes=data.get("notes", ""),
                duration_ms=duration,
            )

        except anthropic.APIError as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error(
                "Anthropic API error for task %s: %s", task.id, e,
            )
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.ERROR,
                error=f"API error: {e}",
                duration_ms=duration,
            )

        except json.JSONDecodeError as e:
            duration = int((time.monotonic() - start) * 1000)
            preview = (raw or "")[:200]
            logger.error(
                "JSON parse failed for task %s: %s — raw response: %s",
                task.id, e, preview,
            )
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.ERROR,
                error=f"JSON parse error: {e}",
                output=raw or "",
                duration_ms=duration,
            )

        except asyncio.TimeoutError:
            duration = int((time.monotonic() - start) * 1000)
            logger.error(
                "Timeout after %ds for task %s", self.timeout, task.id,
            )
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.ERROR,
                error=f"Timeout after {self.timeout}s",
                duration_ms=duration,
            )


def _parse_json(text: str) -> dict:
    """Thin wrapper — delegates to the shared extract_json helper."""
    from ._json_utils import extract_json
    return extract_json(text)
