# Adding a provider

A provider is a plug-in that wires the orchestrator to a specific LLM
backend. Built-ins are `anthropic` (API) and `claude-cli` (local
subscription). Adding a new one means implementing two protocols and
registering them.

## What you need to implement

```python
# providers/base.py
class PlannerBackend(Protocol):
    async def plan(self, context: str, instruction: str) -> PlanResult: ...
    async def select_task(self, context: str) -> str: ...

class WorkerBackend(Protocol):
    async def execute(self, task: SubTask) -> WorkerResult: ...
```

`PlanResult`, `SubTask`, and `WorkerResult` are dataclasses defined
in `providers/base.py`.

## Skeleton

Create `providers/myprovider_backend.py`:

```python
from providers.base import (
    PlannerBackend, WorkerBackend, PlanResult, SubTask, WorkerResult,
)

class MyProviderPlanner:
    def __init__(self, model: str, **kwargs):
        self._model = model
        # initialise client/sdk here

    async def plan(self, context: str, instruction: str) -> PlanResult:
        # call your LLM; return PlanResult(subtasks=[...], reasoning=...)
        ...

    async def select_task(self, context: str) -> str:
        # return the task description as a single string
        ...

class MyProviderWorker:
    def __init__(self, model: str, **kwargs):
        self._model = model

    async def execute(self, task: SubTask) -> WorkerResult:
        # run the task; return WorkerResult(status=..., output=..., error=...)
        ...
```

## Registering the provider

Open `providers/registry.py` and add your factory functions:

```python
def register_provider(name: str, planner_cls, worker_cls) -> None: ...
```

There's already a registration block for `anthropic` and `claude-cli`
to copy from. Aim for the same shape.

## Testing your provider

Two layers:

1. **Unit:** mock the SDK / subprocess; verify your protocol
   methods return well-formed `PlanResult` / `WorkerResult`. See
   `tests/test_anthropic_backend.py` for the pattern.
2. **Integration:** drop your provider into `config/orchestrator.yaml`
   and run `orchestrator.py --dry-run` against a real project. The
   `--dry-run` path exercises `select_task` without dispatching
   workers.

## Configuration shape

Operators select your provider via:

```yaml
planner:
  provider: myprovider
  model: my-strong-model
  # any other kwargs your __init__ takes
workers:
  provider: myprovider
  model: my-fast-model
```

Whatever kwargs your `__init__` accepts are passed through from the
config dict. Document them in your provider module's docstring.

## Things to avoid

- **Don't import your SDK at module top-level** — keeps `providers/`
  importable on machines that don't have the SDK installed
- **Don't read environment variables directly inside the protocol
  methods** — read them in `__init__` and store; makes testing easier
- **Don't catch and swallow errors silently** — return
  `WorkerResult(status="error", error=...)` so the orchestrator's
  retry / circuit-breaker logic can react

## Reference implementations

- `providers/anthropic_backend.py` — the cleanest reference; uses the
  `anthropic` SDK
- `providers/claude_cli_backend.py` — subprocess-based; useful when
  the LLM is invoked via a CLI rather than an SDK
