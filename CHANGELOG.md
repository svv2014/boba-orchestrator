# Changelog

All notable changes to boba-orchestrator are documented here. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0: MINOR releases may include breaking schema or config changes.
Each such release documents the breakage and a migration recipe.

## [Unreleased]


### Changed
- [BOB-20] README: add Reliability section and CLAUDE_CLI_PATH resolution note (#21)
## [0.2.0] - 2026-05-10

First post-launch batch. Substantial features and one critical fix.
Quick mode was broken in v0.1.0 (missing `providers/session_manager.py`)
— operators on v0.1.0 should upgrade.

### Fixed
- **`providers/session_manager.py`** — file was untracked when v0.1.0's
  orphan commit was created and didn't ship. Quick mode (`--mode quick`)
  crashed at import on every invocation. Fixed in #9. Loop's PO/dev
  handlers (which run quick mode) were unusable until this landed.

### Added — resilience
- **#11** Transient Claude CLI retry with 30s backoff. Recoverable
  failures (rate limit / 5xx / network) retry once at the orchestrator
  layer instead of bubbling up to the caller. Cuts spurious failures
  from rate-limit storms by ~50% in observed traffic.
- **#12** Per-run structured JSONL transcripts. Each run produces
  `<run_id>.jsonl` in `${ORCHESTRATOR_TRANSCRIPT_DIR}` with per-step
  events (claude.exec.start / done, plan, dispatch). Files >7 days old
  cleaned up on startup.
- **#15** `CLAUDE_CLI_PATH` env var resolves at call time, with
  `shutil.which("claude")` fallback. `CLAUDE_BIN` deprecated alias
  remains with a one-shot warning.

### Added — extensibility
- **#13** Voice/media personas extracted to optional local config.
  Operators load their own persona library via
  `personas.local.yaml`; the framework ships only software-engineering
  personas (architect/coder/reviewer/tester/etc.). Removes operator-
  specific blog/content personas from the public framework defaults.
- **#16** `examples/` directory with two non-loop tasks (research,
  code). Smoother first-run for new operators — they can verify
  install with a known-good prompt.

### Changed — toolchain
- **#14** Python 3.13 floor (was >=3.11). Single source of truth for
  version: `pyproject.toml [project].requires-python`. CI reads
  `python-version-file: pyproject.toml`. ruff `target-version`
  inferred. **pyright** added as type checker (replaces previous
  ad-hoc `# type: ignore` suppressions). Two unnecessary defensive
  imports in `workers/` removed.

### Documentation
- **#17** "How it compares" section in README — concrete contrast vs
  Symphony / AutoGen / LangGraph for first-time visitors.

### Migration

From v0.1.0:
1. **Required:** `git pull` (the v0.1.0 quick-mode crash is fixed only
   by getting the new code).
2. **Optional but recommended:** bump local Python to 3.13 if pinned.
3. **If you used \`CLAUDE_BIN\`:** rename to \`CLAUDE_CLI_PATH\`; the
   old name still works but logs a deprecation warning.
4. **If you customized personas:** move operator-specific entries to
   \`personas.local.yaml\` (see updated \`docs/personas.md\`).

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
