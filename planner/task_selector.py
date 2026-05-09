"""Selects the highest-value next task across all scanned projects.

Uses PlannerBackend.select_task() for LLM-based selection, with a
deterministic fallback (least-recently-worked project) when no backend
is available or the LLM call fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from planner.project_scanner import ProjectState, Task, format_summary
from providers.base import PlannerBackend


@dataclass
class SelectedTask:
    """The task chosen for execution."""

    project_name: str
    milestone_number: int
    milestone_title: str
    task_description: str
    reasoning: str = ""


def select_by_recency(states: list[ProjectState]) -> Optional[SelectedTask]:
    """Deterministic fallback: pick the project least recently worked on.

    Among projects with an available next task, choose the one whose last
    log entry has the oldest date. Ties broken by list order.
    """
    candidates = []
    for s in states:
        nt = s.next_task
        cm = s.current_milestone
        if nt and cm and not nt.blocked:
            candidates.append((s, cm, nt))

    if not candidates:
        return None

    # Sort by last_worked date ascending (None = never worked = highest priority)
    candidates.sort(key=lambda c: c[0].last_worked or "0000-00-00")

    s, cm, nt = candidates[0]
    return SelectedTask(
        project_name=s.name,
        milestone_number=cm.number,
        milestone_title=cm.title,
        task_description=nt.description,
        reasoning=f"Least recently worked (last: {s.last_worked or 'never'})",
    )


async def select_task(
    states: list[ProjectState],
    planner: Optional[PlannerBackend] = None,
) -> Optional[SelectedTask]:
    """Select the highest-value next task.

    If a planner backend is provided, uses LLM-based selection. Falls back
    to recency-based selection on failure or when no planner is given.

    Args:
        states: Scanned project states from project_scanner.scan_all().
        planner: Optional PlannerBackend for LLM-based selection.

    Returns:
        SelectedTask or None if no tasks are available.
    """
    # Quick check: any tasks available at all?
    fallback = select_by_recency(states)
    if fallback is None:
        return None

    if planner is None:
        return fallback

    # Try LLM-based selection
    try:
        summary = format_summary(states)
        llm_response = await planner.select_task(summary)
        selected = _match_llm_response(llm_response, states)
        if selected:
            return selected
    except Exception:
        pass  # Fall through to deterministic fallback

    return fallback


def _word_overlap_score(text_a: str, text_b: str) -> float:
    """Compute Jaccard-like word overlap between two texts.

    Returns a float in [0, 1]. Higher means more overlap.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    # Drop very short words (articles, prepositions) to reduce noise
    words_a = {w for w in words_a if len(w) > 2}
    words_b = {w for w in words_b if len(w) > 2}
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    return len(intersection) / min(len(words_a), len(words_b))


_MIN_OVERLAP = 0.25  # require at least 25% word overlap to consider a match


def _match_llm_response(
    response: str,
    states: list[ProjectState],
) -> Optional[SelectedTask]:
    """Match the LLM's natural language selection to a concrete task.

    The LLM returns a free-text description. We match it to an actual
    unchecked task by:
      1. Finding which project the LLM mentioned by name.
      2. Scoring all unchecked tasks in that project by word overlap
         with the LLM response, picking the best match.
      3. Falling back to the project's next_task if no task description
         has sufficient overlap.
    """
    response_lower = response.lower()

    for s in states:
        cm = s.current_milestone
        nt = s.next_task
        if not cm or not nt or nt.blocked:
            continue

        # Check if the LLM mentioned this project
        if s.name.lower() not in response_lower:
            continue

        # Collect all unchecked, unblocked tasks across milestones
        candidates: list[tuple[float, int, str, Task]] = []
        for ms in s.milestones:
            for task in ms.tasks:
                if task.done or task.blocked:
                    continue
                score = _word_overlap_score(response, task.description)
                candidates.append((score, ms.number, ms.title, task))

        if not candidates:
            # No unchecked tasks — shouldn't happen but guard anyway
            continue

        # Pick the task with the highest overlap score
        candidates.sort(key=lambda c: c[0], reverse=True)
        best_score, best_ms_num, best_ms_title, best_task = candidates[0]

        if best_score >= _MIN_OVERLAP:
            return SelectedTask(
                project_name=s.name,
                milestone_number=best_ms_num,
                milestone_title=best_ms_title,
                task_description=best_task.description,
                reasoning=response,
            )

        # No good match on task description — fall back to next_task
        return SelectedTask(
            project_name=s.name,
            milestone_number=cm.number,
            milestone_title=cm.title,
            task_description=nt.description,
            reasoning=response,
        )

    # LLM response didn't match any project name — can't resolve
    return None
