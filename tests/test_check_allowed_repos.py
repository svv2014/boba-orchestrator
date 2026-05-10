"""Tests for scripts/check-allowed-repos.py.

Background: four production stalls (loop-monitor, boba-event, pa-scanner,
ppl-study) were traced to silent drift between loop's project list and
orchestrator's allowed_repos. This script is the tripwire.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


SCRIPT = "scripts/check-allowed-repos.py"


def run_check(
    tmp_path,
    *,
    orchestrator_yaml: str,
    loop_yaml: str | None,
) -> subprocess.CompletedProcess:
    """Run the drift-check script with synthetic configs under tmp_path."""
    orch_cfg = tmp_path / "orchestrator.yaml"
    orch_cfg.write_text(orchestrator_yaml)

    env_extra: dict[str, str] = {"ORCHESTRATOR_CONFIG": str(orch_cfg)}

    if loop_yaml is not None:
        loop_cfg = tmp_path / "loop_projects.yaml"
        loop_cfg.write_text(loop_yaml)
        env_extra["LOOP_PROJECTS_YAML"] = str(loop_cfg)
    else:
        # Point at a path that definitely does not exist.
        env_extra["LOOP_PROJECTS_YAML"] = str(tmp_path / "nonexistent.yaml")

    import os

    env = {**os.environ, **env_extra}

    return subprocess.run(
        [sys.executable, SCRIPT],
        capture_output=True,
        text=True,
        env=env,
    )


def _orch_with_explicit_allowed(paths: list[str]) -> str:
    entries = "\n".join(f"      - {p}" for p in paths)
    return f"guardrails:\n  allowed_repos:\n{entries}\nprojects: []\n"


def _orch_with_projects(paths: list[str]) -> str:
    entries = "\n".join(f"  - name: p\n    path: {p}" for p in paths)
    return f"guardrails: {{}}\nprojects:\n{entries}\n"


def _loop_yaml(roots: list[str]) -> str:
    entries = "\n".join(f"  - name: proj\n    root: {r}" for r in roots)
    return f"projects:\n{entries}\n"


# (a) Drift detected — loop root absent from allowed_repos → non-zero + message.
def test_drift_detected_explicit_allowed(tmp_path):
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(tmp_path / "repo-a")]),
        loop_yaml=_loop_yaml([str(tmp_path / "repo-b")]),
    )
    assert result.returncode != 0
    assert "MISSING from allowed_repos:" in result.stdout
    assert str(tmp_path / "repo-b") in result.stdout


def test_drift_detected_projects_derived(tmp_path):
    allowed_root = tmp_path / "repo-a"
    missing_root = tmp_path / "repo-b"
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_projects([str(allowed_root)]),
        loop_yaml=_loop_yaml([str(missing_root)]),
    )
    assert result.returncode != 0
    assert str(missing_root) in result.stdout


# (b) Superset is OK — orchestrator allowed_repos > loop projects → exit 0.
def test_superset_no_drift_explicit(tmp_path):
    loop_root = tmp_path / "repo-a"
    extra = tmp_path / "repo-b"
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(loop_root), str(extra)]),
        loop_yaml=_loop_yaml([str(loop_root)]),
    )
    assert result.returncode == 0
    assert "MISSING" not in result.stdout


def test_exact_match_no_drift(tmp_path):
    root = tmp_path / "my-project"
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(root)]),
        loop_yaml=_loop_yaml([str(root)]),
    )
    assert result.returncode == 0


def test_subdirectory_of_allowed_no_drift(tmp_path):
    # allowed_repos lists a parent → loop root under it is covered.
    parent = tmp_path / "workspace"
    child = parent / "projects" / "my-repo"
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(parent)]),
        loop_yaml=_loop_yaml([str(child)]),
    )
    assert result.returncode == 0


# (c) Missing loop config file → exit 0 with stderr message.
def test_missing_loop_config_exits_zero(tmp_path):
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(tmp_path / "repo-a")]),
        loop_yaml=None,
    )
    assert result.returncode == 0
    assert "loop config not found" in result.stderr


def test_empty_loop_projects_no_drift(tmp_path):
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(tmp_path / "repo-a")]),
        loop_yaml="projects: []\n",
    )
    assert result.returncode == 0


def test_multiple_missing_all_reported(tmp_path):
    loop_roots = [str(tmp_path / f"repo-{i}") for i in range(3)]
    result = run_check(
        tmp_path,
        orchestrator_yaml=_orch_with_explicit_allowed([str(tmp_path / "other")]),
        loop_yaml=_loop_yaml(loop_roots),
    )
    assert result.returncode != 0
    for r in loop_roots:
        assert r in result.stdout
