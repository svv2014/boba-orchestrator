"""Abstract interfaces for planner and worker backends.

Any LLM provider (Anthropic, OpenAI, Ollama, etc.) implements these protocols.
The orchestrator never imports a specific SDK — it talks through these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class TaskType(str, Enum):
    CODE = "code"
    TEST = "test"
    DOCS = "docs"
    RESEARCH = "research"
    REVIEW = "review"
    ARCHITECTURE = "architecture"


class TaskStatus(str, Enum):
    DONE = "done"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class SubTask:
    """A single unit of work dispatched to a worker."""

    id: str
    type: TaskType
    description: str
    target_repo: str
    context_files: list[str] = field(default_factory=list)
    constraints: str = ""
    tools: list[str] = field(default_factory=list)  # minimal tool grants
    estimated_seconds: int = 0  # planner's time estimate (0 = use default)
    complexity: str = "medium"  # low, medium, high — set by planner
    persona: str = "coder"      # which agent persona: architect|coder|reviewer|tester|researcher
    retry_count: int = 0        # review loop retry counter
    original_spec: str = ""     # preserved for reviewer to compare against


@dataclass
class PlanResult:
    """Output of the planner: a task decomposed into parallel subtasks."""

    task_summary: str
    subtasks: list[SubTask]
    reasoning: str = ""
    project_name: str = ""
    milestone: str = ""


@dataclass
class WorkerResult:
    """Output of a single worker execution."""

    task_id: str
    status: TaskStatus
    files_changed: list[str] = field(default_factory=list)
    output: str = ""
    notes: str = ""
    error: Optional[str] = None
    duration_ms: int = 0


@runtime_checkable
class PlannerBackend(Protocol):
    """Interface for the planning model.

    The planner receives project state and returns a decomposed plan.
    Typically the strongest model available (Opus, GPT-5, etc.).
    """

    async def plan(self, context: str, instruction: str) -> PlanResult:
        """Analyze project state and decompose the next task into subtasks.

        Args:
            context: Formatted project state (from project_scanner).
            instruction: What to plan — e.g. "select highest-value task and decompose."

        Returns:
            PlanResult with subtasks ready for worker dispatch.
        """
        ...

    async def select_task(self, context: str) -> str:
        """Given project states, select the single highest-value next task.

        Args:
            context: Formatted project state summary.

        Returns:
            Natural language description of the selected task.
        """
        ...


@runtime_checkable
class WorkerBackend(Protocol):
    """Interface for worker execution models.

    Workers receive a single subtask and return results.
    Typically a fast/cheap model (Sonnet, GPT-4o, etc.).
    """

    async def execute(self, task: SubTask) -> WorkerResult:
        """Execute a single subtask.

        Args:
            task: The subtask with description, target, context, and tool grants.

        Returns:
            WorkerResult with status, changed files, and output.
        """
        ...
