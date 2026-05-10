# Changelog

All notable changes to boba-orchestrator are documented here. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0: MINOR releases may include breaking schema or config changes.
Each such release documents the breakage and a migration recipe.

## [Unreleased]


### Changed
- [BOB-10] Modernize Python tooling: 3.13 floor, single-source version, pyright, remove type-ignores (#14)
- [BOB-3] Add examples/ directory with research and code tasks (#16)
## [0.1.0] - 2026-05-09

Initial public release. The orchestrator has been running internally
for the past two months; this release polishes documentation, removes
internal-only artifacts, and adds the contribution + CI infrastructure
needed for external contributors.

### Added — public-launch infrastructure
- `CONTRIBUTING.md` — local dev, test, code style, bug-reporting guidance
- `SECURITY.md` — vulnerability disclosure process + threat-model summary
- `CHANGELOG.md` — this file
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/CODEOWNERS`
- `.github/workflows/ci.yml` — lint (ruff) + tests (pytest) on every PR
- `config/orchestrator.example.yaml` — committed template; the live
  `config/orchestrator.yaml` is now gitignored

### Core capabilities (developed pre-release)
- **Multi-agent orchestration:** planner decomposes tasks, workers
  execute in parallel (`asyncio`)
- **Provider-agnostic:** `PlannerBackend` + `WorkerBackend` protocols;
  Anthropic Claude API and Claude CLI shipped as built-in providers
- **Four execution modes:**
  - `--mode quick` — direct worker dispatch, no planner
  - `--mode background` — full pipeline (scan → select → decompose →
    parallel workers → commit)
  - `--mode queue` — drain a backlog of tasks
  - `--mode conversational` — chat-driven dispatch with async result
    delivery
- **Per-call model override** via `--model` (added in #30) — quick
  mode can run a stronger model than the workers config
- **Result store** at `results/store.py` — persistent across runs
- **Guardrails:** worker timeout, total run time, subtask count,
  parallel worker count, consecutive failures, target-repo allowlist,
  prompt sanitizer
- **Notifiers:** Telegram (built-in); shell-script hook for arbitrary
  notifiers
- **Review orchestrator:** post-execution review loop with retry-on-
  changes-requested + max-retry escalation

### Removed (internal-only)
- `AGENTS.md` — personal AI persona definitions
- `TODO.md` — internal dev journal
- `docs/system-review-2026-04-28.md` — internal architecture review

### Notes

The full pre-release commit history is preserved in the predecessor
private repository. The git history starting from this tag is the
official public lineage.
