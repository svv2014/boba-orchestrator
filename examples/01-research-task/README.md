# Example 01 — Research Summarisation Task

This example runs the orchestrator in `quick` mode to summarise three bundled AI papers into a single one-paragraph-per-paper digest.

Everything needed is committed here — no external data is fetched at runtime.

## What it does

Dispatches a single worker with the instruction:

> "Read the three markdown files in `papers/` and produce a one-paragraph summary of each paper covering: main contribution, key result, and why it matters."

The worker reads `papers/attention_is_all_you_need.md`, `papers/bert_pretraining.md`, and `papers/scaling_laws.md` and writes its output to stdout.

## Expected output shape

```
**Attention Is All You Need (Vaswani et al., 2017)**
<one paragraph>

**BERT (Devlin et al., 2018)**
<one paragraph>

**Scaling Laws (Kaplan et al., 2020)**
<one paragraph>
```

## Run

From the `examples/01-research-task/` directory:

```bash
cd examples/01-research-task
python ../../orchestrator.py --mode quick \
  --config ./config/orchestrator.yaml \
  "Read the three markdown files in papers/ and produce a one-paragraph summary of each paper covering: main contribution, key result, and why it matters." \
  --cwd .
```

> `--cwd .` tells the worker its working directory is this example folder so it can read the `papers/` files using relative paths.

## Prerequisites

- Python 3.11+
- `pip install -e "../../"` (install boba-orchestrator from the repo root)
- `claude-cli` authenticated (or set `ANTHROPIC_API_KEY` and switch `provider: anthropic` in the config)

## Files

```
01-research-task/
├── config/
│   └── orchestrator.yaml   # minimal config, claude-cli provider, 1 worker
├── papers/
│   ├── attention_is_all_you_need.md
│   ├── bert_pretraining.md
│   └── scaling_laws.md
└── README.md               # this file
```
