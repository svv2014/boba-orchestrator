# Architecture

The orchestrator is built around a single core idea: **a planner model
decomposes tasks; worker models execute them in parallel.** Everything
else — the modes, the providers, the personas, the guardrails —
exists in service of that split.

## The planner / worker split

Most multi-agent frameworks treat model selection as a per-call
concern. The orchestrator makes the cost-tier split a first-class
architectural decision.

```
                    ┌──────────────────────────────┐
                    │   PlannerBackend (strong)    │
                    │   plan() · select_task()     │
                    └──────────┬───────────────────┘
                               │
                          subtasks
                               │
              ┌────────────────┴────────────────┐
              │                                  │
              ▼                                  ▼
   ┌───────────────────┐                ┌───────────────────┐
   │ WorkerBackend     │      ...       │ WorkerBackend     │
   │ (fast, parallel)  │                │ (fast, parallel)  │
   │ execute()         │                │ execute()         │
   └─────────┬─────────┘                └─────────┬─────────┘
             │                                     │
             └──────────────┬──────────────────────┘
                            ▼
                    ┌────────────────┐
                    │  Coordinator   │
                    │  merge · commit│
                    └────────────────┘
```

The planner picks tasks and decomposes them — work that benefits from
the largest reasoning model available. The workers execute the
resulting subtasks in parallel — work where speed and cost matter
more than depth.

## Components

### `orchestrator.py` — entry point and mode routing
Parses CLI args, loads config, dispatches to one of the four `_run_*`
functions. The single `--model` flag overrides `workers.model` for
the run; useful when one task warrants a stronger model than the
default workers tier.

### `planner/` — task selection and decomposition
- `project_scanner.py` — walks configured project directories,
  reports `ProjectState` (files, recent activity, open TODOs)
- `task_selector.py` — given a list of `ProjectState`, picks the
  highest-value task (LLM-driven or recency-based)
- `task_decomposer.py` — turns a task description into a list of
  `SubTask` objects suitable for parallel dispatch

### `workers/` — parallel execution
- `worker_pool.py` — `asyncio` orchestration of N parallel workers,
  bounded by `max_parallel_workers` guardrail
- `prompts.py` — system prompts per `TaskType` (code, test, docs,
  research)
- `review_orchestrator.py` — post-execution review loop with
  retry-on-changes-requested + max-retry escalation

### `providers/` — pluggable LLM backends
Two protocols:

```python
class PlannerBackend(Protocol):
    async def plan(self, context: str, instruction: str) -> PlanResult: ...
    async def select_task(self, context: str) -> str: ...

class WorkerBackend(Protocol):
    async def execute(self, task: SubTask) -> WorkerResult: ...
```

Built-ins: `anthropic` (API), `claude-cli` (local subscription).
Adding a provider is documented in [`providers.md`](providers.md).

### `coordinator/` — result handling
- `result_merger.py` — combines worker outputs, resolves conflicts
- `commit_agent.py` — opens a single git commit for the run

### `security/` — guardrails
- `guardrails.py` — declarative limits (timeout, parallel cap, total
  run time, subtask count, target-repo allowlist, consecutive
  failure circuit breaker)
- `sanitizer.py` — flags dangerous patterns in task descriptions
  before they reach action-capable prompts

### `notifier/` — outbound notification
Currently a Telegram stub that logs to file. Real delivery is a
follow-up.

## Execution modes

| Mode | When to use | Skips planner? |
|---|---|---|
| `quick` | One-off task with a known target repo | yes |
| `background` | Scheduled autonomous run, picks task itself | no |
| `queue` | Drain a backlog in one process | no |
| `conversational` | Chat-driven dispatch with async result delivery | yes |

Auto-route (no `--mode` flag) decides between `quick` and
`background` based on a complexity heuristic on the task text.

## Why these choices

**Why protocols instead of base classes?**
Backends are owned by their authors. Subclassing inverts that
relationship; protocols don't.

**Why asyncio for workers?**
Most backend providers are I/O bound (HTTP requests). `asyncio` lets
N workers share one event loop without thread overhead.

**Why a separate planner tier?**
A $15/MTok planner running 5% of total tokens + a $3/MTok worker
running 95% costs less than a single $15/MTok everywhere — and is
often better because the planner can take its time on a small,
high-value reasoning step.

**Why guardrails as config?**
Operators run the orchestrator inside their environments; their risk
tolerance varies. Hard limits in code are wrong; runtime caps with
sensible defaults are right.

## What lives outside the core

These are deliberate non-features in v0.1:

- **State store** — runs persist results to `results/store.py` (SQLite),
  but there's no cross-run learning, no model fine-tuning, no
  long-term memory.
- **Web UI** — the orchestrator is a CLI. Companion projects
  (e.g. dashboards) consume the result store or notifier output.
- **Built-in webhook server** — the `conversational` mode is invoked
  by an external trigger; the orchestrator itself doesn't listen on
  ports.

## Further reading

- [`providers.md`](providers.md) — adding a new LLM backend
- [`personas.md`](personas.md) — registering custom worker personas
- [`threat-model.md`](threat-model.md) — security boundaries
- [`competitive-analysis.md`](competitive-analysis.md) — vs Symphony
  / AutoGen / LangGraph
