#!/usr/bin/env python3
"""Drift-detection check: every project root in loop's config must appear in
the orchestrator's effective allowed_repos set.

Exit 0 = no drift (or loop config absent).
Exit 1 = drift detected — prints missing roots under a MISSING header.
"""
from __future__ import annotations

import os
import sys


def _load_yaml(path: str) -> object:
    import yaml  # type: ignore[import-untyped]

    with open(path) as fh:
        return yaml.safe_load(fh)


def _resolve(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def effective_allowed_repos(config_path: str) -> set[str]:
    """Return the effective allowed_repos set from orchestrator config.

    The result is the union of guardrails.allowed_repos (if any) and
    projects[*].path (always). This mirrors the runtime union semantics in
    GuardrailConfig.from_config so the drift check stays consistent.
    """
    config = _load_yaml(config_path)
    if not isinstance(config, dict):
        return set()

    guardrails = config.get("guardrails", {}) or {}
    explicit: list[str] = list(guardrails.get("allowed_repos") or [])

    # Derive from projects[*].path (resolved relative to config file's directory)
    config_dir = os.path.dirname(os.path.abspath(config_path))
    derived: list[str] = []
    for project in config.get("projects", []) or []:
        path = (project or {}).get("path")
        if path:
            if not os.path.isabs(path):
                path = os.path.join(config_dir, path)
            derived.append(_resolve(path))

    return {_resolve(p) for p in explicit} | set(derived)


def loop_roots(loop_config_path: str) -> list[str]:
    """Return resolved root paths for each loop project."""
    data = _load_yaml(loop_config_path)
    if not isinstance(data, dict):
        return []
    roots: list[str] = []
    for project in data.get("projects", []) or []:
        root = (project or {}).get("root")
        if root:
            roots.append(_resolve(root))
    return roots


def root_is_covered(root: str, allowed: set[str]) -> bool:
    """True if root equals or is under any entry in allowed_repos."""
    for entry in allowed:
        if root == entry or root.startswith(entry + os.sep):
            return True
    return False


def main() -> int:
    config_path = os.environ.get("ORCHESTRATOR_CONFIG", "config/orchestrator.yaml")
    # The drift check compares orchestrator allowed_repos against an external
    # source-of-truth (typically loop's projects.yaml). The source is operator-
    # configurable via $LOOP_PROJECTS_YAML; when unset the check is a no-op.
    # Most users don't run loop and can ignore this script.
    loop_config_path = os.environ.get("LOOP_PROJECTS_YAML", "").strip()
    if not loop_config_path:
        print(
            "LOOP_PROJECTS_YAML not set, skipping drift check "
            "(this is normal if you don't run loop)",
            file=sys.stderr,
        )
        return 0

    loop_config_path = os.path.expanduser(loop_config_path)

    if not os.path.exists(loop_config_path):
        print(
            f"loop config not found, skipping drift check: {loop_config_path}",
            file=sys.stderr,
        )
        return 0

    allowed = effective_allowed_repos(config_path)
    roots = loop_roots(loop_config_path)

    missing = [r for r in roots if not root_is_covered(r, allowed)]

    if missing:
        print("MISSING from allowed_repos:")
        for m in missing:
            print(f"  {m}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
