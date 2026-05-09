"""Scans boba-* projects: reads TODO.md files and returns structured project states."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class Task:
    description: str
    done: bool
    date_completed: Optional[str] = None
    blocked: bool = False
    blocker_reason: Optional[str] = None


@dataclass
class Milestone:
    number: int
    title: str
    tasks: list[Task] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def completed(self) -> int:
        return sum(1 for t in self.tasks if t.done)

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.completed == self.total

    @property
    def next_task(self) -> Optional[Task]:
        for t in self.tasks:
            if not t.done:
                return t
        return None


@dataclass
class LogEntry:
    date: str
    agent: str
    description: str


@dataclass
class ProjectState:
    name: str
    path: str
    milestones: list[Milestone] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)

    @property
    def current_milestone(self) -> Optional[Milestone]:
        for m in self.milestones:
            if not m.is_complete:
                return m
        return None

    @property
    def next_task(self) -> Optional[Task]:
        cm = self.current_milestone
        return cm.next_task if cm else None

    @property
    def last_worked(self) -> Optional[str]:
        if self.log:
            return self.log[-1].date
        return None

    @property
    def total_tasks(self) -> int:
        return sum(m.total for m in self.milestones)

    @property
    def completed_tasks(self) -> int:
        return sum(m.completed for m in self.milestones)


# Patterns
_MILESTONE_RE = re.compile(r"^#{2,3}\s+(?:Milestone\s+)?M?(\d+)\s*[—:\-]\s*(.+)", re.IGNORECASE)
_TASK_RE = re.compile(r"^- \[([ xX])\]\s+(.+)")
_BLOCKED_RE = re.compile(r"←\s*BLOCKED:\s*(.+)", re.IGNORECASE)
_DATE_RE = re.compile(r"—\s*(\d{4}-\d{2}-\d{2})")
_LOG_ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\S+(?:\s+\S+)*?)\s*\|\s*(.+?)\s*\|")


def _parse_todo(content: str) -> tuple[list[Milestone], list[LogEntry]]:
    milestones: list[Milestone] = []
    log: list[LogEntry] = []
    current_ms: Optional[Milestone] = None
    in_log = False

    for line in content.splitlines():
        stripped = line.strip()

        # Detect log section
        if stripped.startswith("## Log"):
            in_log = True
            continue

        if in_log:
            m = _LOG_ROW_RE.match(stripped)
            if m:
                log.append(LogEntry(date=m.group(1), agent=m.group(2), description=m.group(3)))
            continue

        # Milestone header
        m = _MILESTONE_RE.match(stripped)
        if m:
            current_ms = Milestone(number=int(m.group(1)), title=m.group(2).strip())
            milestones.append(current_ms)
            continue

        # Task line
        m = _TASK_RE.match(stripped)
        if m and current_ms is not None:
            done = m.group(1).lower() == "x"
            desc = m.group(2).strip()

            date_match = _DATE_RE.search(desc)
            date_completed = date_match.group(1) if date_match else None

            blocked_match = _BLOCKED_RE.search(desc)
            blocked = blocked_match is not None
            blocker_reason = blocked_match.group(1).strip() if blocked_match else None

            current_ms.tasks.append(Task(
                description=desc,
                done=done,
                date_completed=date_completed,
                blocked=blocked,
                blocker_reason=blocker_reason,
            ))

    return milestones, log


def scan_project(name: str, path: str) -> ProjectState:
    """Scan a single project's TODO.md and return structured state."""
    todo_path = os.path.join(path, "TODO.md")
    if not os.path.isfile(todo_path):
        return ProjectState(name=name, path=path)

    with open(todo_path, "r") as f:
        content = f.read()

    milestones, log = _parse_todo(content)
    return ProjectState(name=name, path=path, milestones=milestones, log=log)


def scan_all(config_path: str = "config/orchestrator.yaml") -> list[ProjectState]:
    """Scan all projects defined in the orchestrator config."""
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Paths in config are relative to the project root (parent of config dir)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(config_path)))
    states = []
    for proj in config.get("projects", []):
        proj_path = os.path.normpath(os.path.join(project_root, proj["path"]))
        states.append(scan_project(proj["name"], proj_path))

    return states


def format_summary(states: list[ProjectState]) -> str:
    """Human-readable summary of all project states."""
    lines = []
    for s in states:
        cm = s.current_milestone
        nt = s.next_task
        lines.append(f"## {s.name} ({s.completed_tasks}/{s.total_tasks} tasks)")
        lines.append(f"  Last worked: {s.last_worked or 'never'}")
        if cm:
            lines.append(f"  Current milestone: M{cm.number} — {cm.title} ({cm.completed}/{cm.total})")
        if nt:
            status = " [BLOCKED]" if nt.blocked else ""
            lines.append(f"  Next task: {nt.description[:80]}{status}")
        lines.append("")
    return "\n".join(lines)
