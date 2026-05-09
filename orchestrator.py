#!/usr/bin/env python3
"""boba-orchestrator — Opus plans, Sonnet workers execute in parallel.

Three execution modes:
  --mode quick      Skip planner. Direct dispatch to one worker. Fast.
  --mode background Full pipeline: scan -> select -> decompose -> parallel workers -> commit
  --mode queue      Continuous: pick task, execute, pick next, repeat until empty or --max-tasks
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

from planner.project_scanner import ProjectState, scan_all, format_summary
from planner.task_selector import select_task, select_by_recency, SelectedTask
from providers.base import SubTask, TaskType
from providers.registry import get_planner, get_worker


def _load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


async def _run_dry_run(config_path: str, use_llm: bool = False) -> int:
    """Scan projects, select task, print plan — no execution."""
    config = _load_config(config_path)

    # Scan all projects
    states = scan_all(config_path)
    print(format_summary(states))

    # Select task
    if use_llm:
        try:
            from providers.logger import logged_planner
            planner = logged_planner(get_planner(config), model=config.get("planner", {}).get("model", "unknown"))
            selected = await select_task(states, planner=planner)
        except Exception as e:
            print(f"[dry-run] LLM selection failed ({e}), falling back to recency")
            selected = select_by_recency(states)
    else:
        selected = select_by_recency(states)

    if selected is None:
        print("[dry-run] No actionable tasks found across any project.")
        return 0

    print("\u2500" * 60)
    print(f"Selected: {selected.project_name}")
    print(f"Milestone: M{selected.milestone_number} \u2014 {selected.milestone_title}")
    print(f"Task: {selected.task_description}")
    print(f"Reasoning: {selected.reasoning}")
    print("\u2500" * 60)
    print("[dry-run] Would decompose and dispatch workers. Stopping here.")
    return 0


def _find_project_state(states: list, project_name: str) -> Optional[ProjectState]:
    for s in states:
        if s.name == project_name:
            return s
    return None


def _build_notification_summary(
    selected: SelectedTask,
    pool_result: object,
    merge: object,
    commit_done: bool,
) -> str:
    """Build a human-readable notification summary of the run."""
    lines = [
        "boba-orchestrator run complete",
        "",
        f"Project: {selected.project_name}",
        f"Task: {selected.task_description}",
        f"Milestone: M{selected.milestone_number} \u2014 {selected.milestone_title}",
        "",
    ]

    if hasattr(pool_result, "succeeded"):
        lines.append(
            f"Workers: {pool_result.succeeded}/{pool_result.total} succeeded, "
            f"{pool_result.failed} failed, {pool_result.blocked} blocked"
        )

    if merge is not None and hasattr(merge, "all_succeeded"):
        lines.append(f"Merge: {'clean' if merge.all_succeeded else 'conflicts detected'}")
        if hasattr(merge, "conflicts") and merge.conflicts:
            lines.append(f"Conflicts: {merge.conflicts}")

    lines.append(f"Committed: {'yes' if commit_done else 'no'}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# MODE: QUICK — Skip planner, direct worker dispatch
# ─────────────────────────────────────────────────────────

async def _run_quick(
    task_description: str,
    config_path: str,
    task_type: str = "code",
    cwd: Optional[str] = None,
    model_override: Optional[str] = None,
) -> int:
    """Quick mode: skip planner, dispatch one worker directly.

    No scanning, no decomposition, no merge. Just execute.

    If model_override is set, it replaces config["workers"]["model"] for this
    run only — useful when callers (e.g. loop's PO handler) want a stronger
    model for spec-writing tasks without changing the global config.
    """
    from providers.logger import logged_worker
    from notifier.telegram_notifier import notify
    from security.guardrails import GuardrailConfig, validate_worker_timeout, validate_target_repo
    from security.sanitizer import sanitize
    from observability.transcript import Transcript, rotate_old

    config = _load_config(config_path)
    if model_override:
        config.setdefault("workers", {})["model"] = model_override
        print(f"[quick] Worker model overridden via --model: {model_override}")
    guardrails = GuardrailConfig.from_config(config)

    # Initialize session manager
    from providers.session_manager import init_session_manager
    init_session_manager(config)

    # Sanitize task description
    sanitized = sanitize(task_description)
    if sanitized.is_dangerous:
        print(f"[quick] SECURITY: Dangerous content in task. Flags: {sanitized.flags}. Aborting.")
        return 1

    # Validate target directory
    if cwd:
        repo_error = validate_target_repo(cwd, guardrails)
        if repo_error:
            print(f"[quick] SECURITY: {repo_error}")
            return 1

    print(f"[quick] Task: {task_description}")
    print(f"[quick] Type: {task_type}")
    print("[quick] Dispatching worker...")

    # Build a single subtask directly — no planner needed.
    # Use the full guardrail cap rather than 600s — dev tasks (commit+PR) can
    # legitimately take 30-60 min when the codebase is large.
    timeout = validate_worker_timeout(guardrails.max_worker_timeout_seconds, guardrails)
    subtask = SubTask(
        id="quick-1",
        type=TaskType(task_type),
        description=task_description,
        target_repo=cwd or ".",
        estimated_seconds=timeout,
        complexity="medium",
    )

    raw_worker = get_worker(config)
    worker_model = config.get("workers", {}).get("model", "unknown")
    worker = logged_worker(raw_worker, model=worker_model)

    # Result store — persist for async pickup
    run_id = f"quick-{uuid.uuid4().hex[:8]}"
    result_store = None
    try:
        from results.store import ResultStore, db_path_from_config
        result_store = ResultStore(db_path_from_config(config))
    except Exception as e:
        logger.warning("Result store unavailable: %s", e)

    # Open transcript and rotate old files
    rotate_old()
    transcript = Transcript(run_id=run_id)
    transcript.emit(
        "task.start",
        mode="quick",
        task=task_description,
        task_type=task_type,
        cwd=cwd,
        model=worker_model,
    )

    # Register transcript for claude.exec events
    try:
        from providers.claude_cli_backend import set_active_transcript
        set_active_transcript(transcript)
    except Exception:
        pass

    start = time.monotonic()
    result = await worker.execute(subtask)
    elapsed = int(time.monotonic() - start)

    # Deregister transcript
    try:
        from providers.claude_cli_backend import set_active_transcript
        set_active_transcript(None)
    except Exception:
        pass

    transcript.emit(
        "task.done",
        status=result.status.value,
        elapsed_s=elapsed,
        error=result.error,
    )
    transcript.close()

    if result_store is not None:
        try:
            result_store.save_result(run_id, result, task_description=task_description)
        except Exception as e:
            logger.warning("Failed to save result: %s", e)

    print(f"[quick] Done in {elapsed}s — status: {result.status.value}")
    print(f"[quick] Run ID: {run_id}")

    if result.output:
        print(f"\n{result.output[:2000]}")

    if result.error:
        print(f"[quick] Error: {result.error}")

    # Notify
    msg = f"Quick task: {task_description}\nStatus: {result.status.value}\nDuration: {elapsed}s\nRun ID: {run_id}"
    await notify(msg, config)

    return 0 if result.status.value == "done" else 1


# ─────────────────────────────────────────────────────────
# MODE: CONVERSATIONAL — Stay in chat, dispatch workers async
# ─────────────────────────────────────────────────────────

async def _run_conversational(
    task: str,
    config_path: str,
    recipient: str = "",
    voice: bool = False,
) -> int:
    """Conversational mode: handle a message, dispatch worker in background.

    Sends ack immediately, runs worker async, sends result when done.
    """
    from conversational.trigger import (
        InboundMessage,
        handle_message,
        make_signal_notify_fn,
        make_signal_voice_notify_fn,
    )
    from providers.base import SubTask, TaskType
    from providers.registry import get_worker
    from providers.logger import logged_worker
    from security.sanitizer import sanitize

    config = _load_config(config_path)

    tts_script = os.environ.get("BOBA_TTS_SCRIPT")

    if voice and tts_script:
        notify_fn = make_signal_voice_notify_fn(
            recipient=recipient,
            tts_script=tts_script,
            voice="aiden",
        )
    else:
        if voice and not tts_script:
            logger.warning(
                "Voice notify requested but BOBA_TTS_SCRIPT is not set; "
                "falling back to text-only notifications."
            )
        notify_fn = make_signal_notify_fn(recipient=recipient)

    raw_worker = get_worker(config)
    worker_model = config.get("workers", {}).get("model", "unknown")
    worker = logged_worker(raw_worker, model=worker_model)

    async def worker_fn(task_brief: str) -> str:
        sanitized = sanitize(task_brief)
        if sanitized.is_dangerous:
            return f"Blocked: dangerous content detected ({sanitized.flags})"
        subtask = SubTask(
            id="conv-1",
            type=TaskType.RESEARCH,
            description=task_brief,
            target_repo=".",
            estimated_seconds=1800,
            complexity="high",
        )
        result = await worker.execute(subtask)
        return result.output or result.error or f"Status: {result.status.value}"

    msg = InboundMessage(text=task, sender="orchestrator")
    result = await handle_message(
        msg,
        worker_fn=worker_fn,
        notify_fn=notify_fn,
    )

    if result.direct_reply:
        print(f"[conversational] Direct reply: {result.direct_reply}")

    if result.worker_task:
        print(f"[conversational] Worker dispatched: {result.worker_task[:80]}")
        print("[conversational] Waiting for background worker...")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    return 0 if result.error is None else 1


# ─────────────────────────────────────────────────────────
# MODE: BACKGROUND — Full pipeline (existing)
# ─────────────────────────────────────────────────────────

async def _run_background(config_path: str, use_llm: bool = False) -> int:
    """Full execution pipeline: scan -> select -> decompose -> execute -> merge -> commit -> notify."""
    from notifier.telegram_notifier import notify
    from security.sanitizer import sanitize
    from providers.logger import logged_planner, logged_worker

    config = _load_config(config_path)

    # Initialize session manager
    from providers.session_manager import init_session_manager
    init_session_manager(config)

    # --- Scan ---
    print("[background] Scanning projects...")
    states = scan_all(config_path)
    if not states:
        await notify("No projects found to scan.", config)
        print("[background] No projects found.")
        return 0

    # --- Select task ---
    print("[background] Selecting task...")
    try:
        if use_llm:
            raw_planner = get_planner(config)
            planner_model = config.get("planner", {}).get("model", "unknown")
            selection_planner = logged_planner(raw_planner, model=planner_model)
            selected = await select_task(states, planner=selection_planner)
        else:
            selected = select_by_recency(states)
    except Exception as e:
        print(f"[background] LLM selection failed ({e}), falling back to recency")
        selected = select_by_recency(states)

    if selected is None:
        msg = "No actionable tasks found across any project."
        await notify(msg, config)
        print(f"[background] {msg}")
        return 0

    print(f"[background] Selected: {selected.project_name} \u2014 {selected.task_description}")

    # --- Find project state ---
    project_state = _find_project_state(states, selected.project_name)
    if project_state is None:
        msg = f"Could not find project state for '{selected.project_name}'"
        await notify(msg, config)
        print(f"[background] ERROR: {msg}")
        return 1

    # --- Sanitize project context ---
    print("[background] Sanitizing project context...")
    summary_text = format_summary(states)
    sanitized = sanitize(summary_text)
    if sanitized.is_dangerous:
        msg = f"SECURITY: Dangerous content detected. Flags: {sanitized.flags}. Aborting."
        await notify(msg, config)
        print(f"[background] {msg}")
        return 1

    if sanitized.is_suspicious:
        print(f"[background] WARNING: Suspicious content (flags: {sanitized.flags}), proceeding with cleaned content")

    # --- Decompose task ---
    print("[background] Decomposing task...")
    try:
        from planner.task_decomposer import decompose_task, DecompositionContext

        raw_planner = get_planner(config)
        planner_model = config.get("planner", {}).get("model", "unknown")
        decomp_planner = logged_planner(raw_planner, model=planner_model)

        ctx = DecompositionContext(
            selected=selected,
            project_state=project_state,
        )
        plan = await decompose_task(ctx, decomp_planner)
    except Exception as e:
        msg = f"Task decomposition failed: {e}"
        await notify(msg, config)
        print(f"[background] ERROR: {msg}")
        traceback.print_exc()
        return 1

    print(f"[background] Decomposed into {len(plan.subtasks)} subtask(s)")
    for st in plan.subtasks:
        est = f" ~{st.estimated_seconds}s" if st.estimated_seconds > 0 else ""
        print(f"  - [{st.type.value}] {st.id}: {st.description[:60]} ({st.complexity}{est})")

    # --- Execute workers ---
    print("[background] Dispatching workers...")
    try:
        from workers.worker_pool import run_pool

        raw_worker = get_worker(config)
        worker_model = config.get("workers", {}).get("model", "unknown")
        pool_worker = logged_worker(raw_worker, model=worker_model)
        max_parallel = config.get("workers", {}).get("max_parallel", 3)

        pool_result = await run_pool(plan.subtasks, pool_worker, max_parallel=max_parallel)
    except Exception as e:
        msg = f"Worker pool execution failed: {e}"
        await notify(msg, config)
        print(f"[background] ERROR: {msg}")
        traceback.print_exc()
        return 1

    print(
        f"[background] Workers done: {pool_result.succeeded}/{pool_result.total} succeeded, "
        f"{pool_result.failed} failed, {pool_result.blocked} blocked"
    )

    # --- Merge results ---
    merge = None
    commit_done = False
    try:
        from coordinator.result_merger import merge_results

        print("[background] Merging results...")
        merge = merge_results(pool_result)
    except ImportError:
        print("[background] SKIP: coordinator.result_merger not yet implemented")
    except Exception as e:
        msg = f"Result merge failed: {e}"
        await notify(msg, config)
        print(f"[background] ERROR: {msg}")
        traceback.print_exc()

    # --- Commit if all good ---
    if merge is not None:
        try:
            from coordinator.commit_agent import commit_changes

            if hasattr(merge, "all_succeeded") and merge.all_succeeded:
                if not (hasattr(merge, "conflicts") and merge.conflicts):
                    print("[background] Committing changes...")
                    await commit_changes(project_state.path, merge, selected)
                    commit_done = True
                else:
                    print("[background] SKIP commit: merge has conflicts")
            else:
                print("[background] SKIP commit: not all workers succeeded")
        except ImportError:
            print("[background] SKIP: coordinator.commit_agent not yet implemented")
        except Exception as e:
            msg = f"Commit failed: {e}"
            await notify(msg, config)
            print(f"[background] ERROR: {msg}")
            traceback.print_exc()

    # --- Notify ---
    summary = _build_notification_summary(selected, pool_result, merge, commit_done)
    result = await notify(summary, config)
    if result.sent:
        print("[background] Notification sent.")
    else:
        print(f"[background] Notification failed: {result.error}")

    # --- Print results ---
    print()
    print("=" * 60)
    print(summary)
    print("=" * 60)

    if pool_result.failed > 0:
        return 1
    return 0


# ─────────────────────────────────────────────────────────
# MODE: QUEUE — Continuous task execution
# ─────────────────────────────────────────────────────────

async def _run_queue(
    config_path: str,
    max_tasks: int = 5,
    use_llm: bool = False,
) -> int:
    """Queue mode: pick task, execute, pick next, repeat.

    Runs until no tasks remain, max_tasks reached, or circuit breaker trips.
    """
    from notifier.telegram_notifier import notify
    from security.guardrails import GuardrailConfig, RunBudget

    config = _load_config(config_path)
    guardrails = GuardrailConfig.from_config(config)
    budget = RunBudget()

    # Use the lower of --max-tasks and guardrail limit
    effective_max = min(max_tasks, guardrails.max_tasks_per_run)
    print(f"[queue] Starting continuous execution (max {effective_max} tasks)")

    for i in range(effective_max):
        # Check circuit breaker before each task
        stop_reason = budget.should_stop(guardrails)
        if stop_reason:
            print(f"[queue] Stopping: {stop_reason}")
            break

        print(f"\n{'=' * 60}")
        print(f"[queue] Task {i + 1}/{effective_max}")
        print(f"{'=' * 60}")

        task_start = time.monotonic()
        result = await _run_background(config_path, use_llm=use_llm)
        task_duration = time.monotonic() - task_start

        if result == 0:
            budget.record_success(task_duration)
        elif result == 1:
            budget.record_failure(task_duration)
        else:
            print("[queue] No more tasks available.")
            break

    summary = f"Queue complete: {budget.summary}"
    print(f"\n[queue] {summary}")
    await notify(f"Queue run: {summary}", config)

    return 0 if budget.tasks_failed == 0 else 1


# ─────────────────────────────────────────────────────────
# SMART ROUTER — One entry point, orchestrator decides
# ─────────────────────────────────────────────────────────

# Heuristics for quick vs background routing
_COMPLEX_SIGNALS = [
    "and", "then", "also", "multiple", "across", "integrate",
    "refactor", "redesign", "migrate", "architecture",
]


def _is_complex_task(description: str) -> bool:
    """Heuristic: does this task need planner decomposition?

    Simple tasks (one file, one action) → quick mode.
    Complex tasks (multi-step, multi-file) → background mode.
    """
    desc_lower = description.lower()
    signal_count = sum(1 for s in _COMPLEX_SIGNALS if s in desc_lower)
    word_count = len(description.split())

    # Short description + few signals = simple
    if word_count < 15 and signal_count < 2:
        return False

    # Long description or multiple complexity signals = complex
    if word_count > 40 or signal_count >= 2:
        return True

    return False


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────

VERSION = "0.1.0"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "boba-orchestrator: one entry point for task execution.\n\n"
            "  orchestrator.py 'write tests for foo.py'   → auto-routes to quick or background\n"
            "  orchestrator.py                             → picks next task from TODO queue\n"
            "  orchestrator.py --dry-run                   → preview what would happen\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"boba-orchestrator {VERSION}",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Task description (if given, auto-routes to quick or background mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview: scan + select, no execution",
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "background", "queue", "conversational"],
        default=None,
        help="Force a specific mode (default: auto-detect from task)",
    )
    parser.add_argument(
        "--recipient",
        type=str,
        default=os.environ.get("SIGNAL_RECIPIENT", ""),
        help="Signal recipient phone number for conversational mode (or set SIGNAL_RECIPIENT env var)",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Reply with TTS voice note (conversational mode only)",
    )
    parser.add_argument(
        "--task-type",
        choices=["code", "test", "docs", "research"],
        default="code",
        help="Task type hint (default: code)",
    )
    parser.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="Working directory for worker",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=5,
        help="Max tasks in queue mode (default: 5)",
    )
    parser.add_argument(
        "--config",
        default="config/orchestrator.yaml",
        help="Config file path",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use LLM for task selection (default: deterministic)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the worker model from config (quick/conversational mode only). "
             "Example: --model claude-opus-4-7",
    )
    args = parser.parse_args()

    task_text = " ".join(args.task) if args.task else ""

    # --- Dry run ---
    if args.dry_run:
        return asyncio.run(_run_dry_run(args.config, use_llm=args.llm))

    # --- Explicit mode override ---
    if args.mode == "quick":
        if not task_text:
            print("Error: task description required for quick mode")
            return 1
        return asyncio.run(_run_quick(task_text, args.config, args.task_type, args.cwd, args.model))

    if args.mode == "background":
        return asyncio.run(_run_background(args.config, use_llm=args.llm))

    if args.mode == "queue":
        return asyncio.run(_run_queue(args.config, args.max_tasks, args.llm))

    if args.mode == "conversational":
        if not task_text:
            print("Error: task/message required for conversational mode")
            return 1
        return asyncio.run(_run_conversational(
            task_text, args.config,
            recipient=args.recipient,
            voice=args.voice,
        ))

    # --- Auto-route ---
    if task_text:
        # Task given — decide quick vs background
        if _is_complex_task(task_text):
            print("[auto] Complex task detected → background mode (planner + workers)")
            return asyncio.run(_run_background_with_task(
                task_text, args.config, args.task_type, args.cwd
            ))
        else:
            print("[auto] Simple task → quick mode (direct worker)")
            return asyncio.run(_run_quick(task_text, args.config, args.task_type, args.cwd, args.model))
    else:
        # No task — pick from TODO queue
        print("[auto] No task given → queue mode (picking from TODOs)")
        return asyncio.run(_run_background(args.config, use_llm=args.llm))


async def _run_background_with_task(
    task_description: str,
    config_path: str,
    task_type: str = "code",
    cwd: Optional[str] = None,
) -> int:
    """Background mode with a freeform task (no TODO.md scanning).

    Uses the planner to decompose the task, then dispatches workers.
    """
    from providers.logger import logged_planner, logged_worker
    from notifier.telegram_notifier import notify
    from security.sanitizer import sanitize
    from planner.task_decomposer import decompose_task, DecompositionContext
    from workers.worker_pool import run_pool
    from observability.transcript import Transcript, rotate_old

    config = _load_config(config_path)

    # Initialize session manager for persona sessions
    from providers.session_manager import init_session_manager
    init_session_manager(config)

    print(f"[background] Task: {task_description}")

    # Sanitize input
    sanitized = sanitize(task_description)
    if sanitized.is_dangerous:
        print(f"[background] SECURITY: Dangerous content. Flags: {sanitized.flags}. Aborting.")
        return 1

    # Create a synthetic SelectedTask
    selected = SelectedTask(
        project_name="freeform",
        milestone_number=0,
        milestone_title="Ad-hoc",
        task_description=task_description,
    )

    # Create a minimal project state
    project_state = ProjectState(
        name="freeform",
        path=cwd or ".",
    )

    # Decompose
    print("[background] Decomposing task...")
    raw_planner = get_planner(config)
    planner_model = config.get("planner", {}).get("model", "unknown")
    planner = logged_planner(raw_planner, model=planner_model)

    ctx = DecompositionContext(selected=selected, project_state=project_state)
    try:
        plan = await decompose_task(ctx, planner)
    except Exception as e:
        print(f"[background] Decomposition failed: {e}")
        return 1

    print(f"[background] Decomposed into {len(plan.subtasks)} subtask(s)")
    for st in plan.subtasks:
        est = f" ~{st.estimated_seconds}s" if st.estimated_seconds > 0 else ""
        print(f"  - [{st.type.value}] {st.id}: {st.description[:60]} ({st.complexity}{est})")

    # Execute
    print("[background] Dispatching workers...")
    raw_worker = get_worker(config)
    worker_model = config.get("workers", {}).get("model", "unknown")
    worker = logged_worker(raw_worker, model=worker_model)
    max_parallel = config.get("workers", {}).get("max_parallel", 3)

    # Result store — persist for async pickup
    run_id = f"bg-{uuid.uuid4().hex[:8]}"
    result_store = None
    try:
        from results.store import ResultStore, db_path_from_config
        result_store = ResultStore(db_path_from_config(config))
        print(f"[background] Run ID: {run_id}")
    except Exception as e:
        logger.warning("Result store unavailable: %s", e)

    # Open transcript and rotate old files
    rotate_old()
    worker_model = config.get("workers", {}).get("model", "unknown")
    transcript = Transcript(run_id=run_id)
    transcript.emit(
        "task.start",
        mode="background",
        task=task_description,
        task_type=task_type,
        cwd=cwd,
        model=worker_model,
        subtask_count=len(plan.subtasks),
    )

    # Register transcript for claude.exec events
    try:
        from providers.claude_cli_backend import set_active_transcript
        set_active_transcript(transcript)
    except Exception:
        pass

    pool_result = await run_pool(
        plan.subtasks, worker,
        max_parallel=max_parallel,
        result_store=result_store,
        run_id=run_id,
    )

    # Deregister transcript
    try:
        from providers.claude_cli_backend import set_active_transcript
        set_active_transcript(None)
    except Exception:
        pass

    transcript.emit(
        "task.done",
        succeeded=pool_result.succeeded,
        failed=pool_result.failed,
        total=pool_result.total,
    )
    transcript.close()

    print(
        f"[background] Done: {pool_result.succeeded}/{pool_result.total} succeeded, "
        f"{pool_result.failed} failed"
    )

    # Print worker outputs
    for r in pool_result.results:
        if r.output:
            print(f"\n--- {r.task_id} ({r.status.value}) ---")
            print(r.output[:2000])

    msg = f"Task: {task_description}\nWorkers: {pool_result.succeeded}/{pool_result.total} succeeded"
    await notify(msg, config)

    return 0 if pool_result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
