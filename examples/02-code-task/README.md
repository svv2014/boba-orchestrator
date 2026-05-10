# Example 02 — Code Modification Task

This example runs the orchestrator in `quick` mode to add a new function to a tiny Python module and a corresponding test.

The target code lives in `target/` — fully self-contained, no network access required.

## What it does

Dispatches a single worker with the instruction:

> "Add a function `add(a, b)` to `math_utils.py` that returns the sum of `a` and `b`. Add a test for it in `test_math_utils.py`."

The worker edits `target/math_utils.py` and `target/test_math_utils.py` in place.

## Expected output

After the run, `target/math_utils.py` will contain an `add` function alongside the existing `multiply`, and `target/test_math_utils.py` will have at least one test for it.

```python
# added by the worker
def add(a: int | float, b: int | float) -> int | float:
    """Return the sum of a and b."""
    return a + b
```

## Run

From the `examples/02-code-task/` directory:

```bash
cd examples/02-code-task
python ../../orchestrator.py --mode quick \
  --config ./config/orchestrator.yaml \
  "Add a function add(a, b) to math_utils.py that returns the sum of a and b. Add a test for it in test_math_utils.py." \
  --cwd ./target
```

> `--cwd ./target` scopes the worker to the target directory so its file edits land in the right place.

After the run, verify the result:

```bash
cd target
python -m pytest test_math_utils.py -v
```

## Prerequisites

- Python 3.11+
- `pip install -e "../../"` (install boba-orchestrator from the repo root)
- `claude-cli` authenticated (or set `ANTHROPIC_API_KEY` and switch `provider: anthropic` in the config)

## Files

```
02-code-task/
├── config/
│   └── orchestrator.yaml   # minimal config, claude-cli provider, 1 worker
├── target/
│   ├── math_utils.py       # module to extend (has multiply; worker adds add)
│   └── test_math_utils.py  # existing tests; worker appends add tests
└── README.md               # this file
```
