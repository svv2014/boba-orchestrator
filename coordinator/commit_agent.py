"""Applies merged results to git: stages files, composes commit message, commits.

Uses gitpython to interact with the repository. Does NOT push by default.
Refuses to commit when there are file conflicts or no succeeded workers.
"""

from __future__ import annotations

from dataclasses import dataclass

from git import Repo, InvalidGitRepositoryError, GitCommandError

from coordinator.result_merger import MergeResult
from planner.task_selector import SelectedTask


@dataclass
class CommitResult:
    """Outcome of a commit attempt."""

    committed: bool = False
    commit_hash: str = ""
    message: str = ""
    error: str = ""


def _compose_message(selected: SelectedTask, merge: MergeResult) -> str:
    """Compose a commit message from the selected task and merge summary."""
    title = (
        f"[{selected.project_name}] "
        f"M{selected.milestone_number}: {selected.task_description[:60]}"
    )
    body = merge.summary
    return f"{title}\n\n{body}"


async def commit_changes(
    project_path: str,
    merge: MergeResult,
    selected: SelectedTask,
    push: bool = False,
) -> CommitResult:
    """Stage changed files and commit to the project repo.

    Args:
        project_path: Absolute path to the git repository.
        merge: MergeResult from result_merger.merge_results().
        selected: The SelectedTask that produced this work.
        push: If True, push to the tracking remote after committing.

    Returns:
        CommitResult indicating success or failure.
    """
    if merge.conflicts:
        return CommitResult(
            error=(
                f"Cannot commit: {len(merge.conflicts)} file conflict(s) — "
                f"{', '.join(merge.conflicts)}"
            ),
        )

    if not merge.all_succeeded:
        # Check if there are ANY succeeded workers with files
        if not merge.merged_files:
            return CommitResult(
                error="Cannot commit: no workers succeeded or no files changed",
            )

    if not merge.merged_files:
        return CommitResult(error="Nothing to commit: no files changed")

    try:
        repo = Repo(project_path)
    except InvalidGitRepositoryError:
        return CommitResult(error=f"Not a git repository: {project_path}")

    message = _compose_message(selected, merge)

    try:
        repo.index.add(merge.merged_files)
        commit = repo.index.commit(message)
    except GitCommandError as exc:
        return CommitResult(error=f"Git commit failed: {exc}")
    except OSError as exc:
        return CommitResult(error=f"File error during commit: {exc}")

    result = CommitResult(
        committed=True,
        commit_hash=commit.hexsha,
        message=message,
    )

    if push:
        try:
            tracking = repo.active_branch.tracking_branch()
            if tracking:
                remote_name = tracking.remote_name
            else:
                remote_name = "origin"
            repo.remotes[remote_name].push()
        except (GitCommandError, IndexError, ValueError) as exc:
            result.error = f"Commit succeeded but push failed: {exc}"

    return result
