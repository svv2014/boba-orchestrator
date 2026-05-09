# Contributing to boba-orchestrator

External contributions welcome. The orchestrator is small enough that
most patches are reviewable in one sitting; please keep PRs scoped.

## Quick rules

- One concern per PR — don't bundle unrelated changes
- Branch naming: `fix/issue-N-short-slug` or `feat/issue-N-short-slug`
- PR body should reference the issue: `Closes #N`
- All CI checks must pass (lint + tests)
- Approval from a [CODEOWNERS](.github/CODEOWNERS) reviewer is required
  before merge

## Local dev

Requirements: Python 3.11+, `pip`.

```bash
git clone https://github.com/svv2014/boba-orchestrator
cd boba-orchestrator
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Copy the example config and edit for your projects:

```bash
cp config/orchestrator.example.yaml config/orchestrator.yaml
$EDITOR config/orchestrator.yaml
```

`config/orchestrator.yaml` is gitignored — it's operator-specific.

## Running tests

```bash
pytest
```

The full suite should pass before any PR. New features need new tests.

## Running locally

```bash
# Dispatch a single task to a worker (skips planner)
python orchestrator.py "fix lint errors" --mode quick --cwd /path/to/repo

# Background mode — scans configured projects, picks highest-value task
python orchestrator.py --mode background

# Dry run — see what would be planned without executing
python orchestrator.py --dry-run
```

## Code style

- Type hints on public APIs (we ship `py.typed` via `pyproject.toml`)
- Format with `black` (line length 100), lint with `ruff`
- One assertion per test function where reasonable

## Architecture overview

See [README.md](README.md) for the high-level picture and
[docs/](docs/) for design notes.

The orchestrator splits work across two protocol interfaces:

- `PlannerBackend` — plans tasks, selects work, decomposes
- `WorkerBackend` — executes individual subtasks

A new LLM provider implements both protocols and registers via
`providers.registry.register_provider()`. No core changes required.

## Reporting bugs

Open an issue with the `bug_report.md` template. Include:

- The orchestrator version (`python orchestrator.py --version`)
- Provider + model in use
- A minimal reproduction (or task description that triggered it)
- Relevant log lines

## Security issues

Please do not open public issues for security findings. See
[SECURITY.md](SECURITY.md) for the disclosure process.
