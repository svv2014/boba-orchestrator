# Personas

A persona is a named worker profile: a system prompt, a model, a
timeout, an output-format expectation, and (optionally) a tool
allowlist. Workers dispatch with a persona name and the orchestrator
loads the matching config.

## Built-in personas

The framework ships with eight personas focused on software
engineering workflows:

| Name | Purpose |
|---|---|
| `architect` | High-level design, decomposition, trade-off analysis |
| `coder` | Implements features, writes patches |
| `reviewer` | Reads diffs, flags issues, proposes verdicts |
| `tester` | Writes / runs tests, reports results |
| `assistant` | Operational tasks (scheduling, lookups, notes) |
| `engineering_director` | Coordination, prioritization, status |
| `researcher` | Information gathering, source synthesis |
| `designer` | UI/UX exploration, mockup generation |

These are general-purpose. Operator-specific personas (content
publishing, media production, domain-specific workflows) are *not*
shipped — you register your own.

## Persona schema

```python
PERSONA_CONFIGS["my_persona"] = {
    "model": "claude-sonnet-4-6",
    "timeout_seconds": 600,
    "scope": "Short one-line description of what this persona does.",
    "output_format": "Description of the expected output structure.",
    "system_prefix": (
        "You are <role>. Your job is <responsibility>.\n\n"
        "ROLE: <what you do>.\n\n"
        "SCOPE: <what you may and may not do>.\n\n"
        "OUTPUT FORMAT: <structure of the response>.\n\n"
        "CONSTRAINTS: <hard rules, refusals, safety boundaries>."
    ),
    "tools_disabled": ["Bash", "Edit"],  # optional; omit for full access
}
```

All fields except `tools_disabled` are required.

## Registering a custom persona

The simplest path: edit `providers/persona_registry.py` directly and
add an entry to the `PERSONA_CONFIGS` dict.

For larger persona libraries, register from your own module:

```python
# my_personas.py
from providers.persona_registry import PERSONA_CONFIGS

PERSONA_CONFIGS["writer"] = {
    "model": "claude-sonnet-4-6",
    "timeout_seconds": 900,
    "scope": "Content drafts.",
    "output_format": "Markdown draft + word count + notes.",
    "system_prefix": (
        "You are a content writer. ...\n\n"
        "SCOPE: ...\n\n"
        "EDITORIAL POLICY:\n"
        "- <your operator-specific rules here>\n"
    ),
    "tools_disabled": ["Edit", "Write", "Bash"],
}
```

Import `my_personas` from your bootstrap; the dict is mutated at
import time.

## When to write a new persona

Write one when **the system prompt is materially different** from
existing personas — different role framing, different output
structure, different tool boundaries. Don't write a new persona just
to change a model; that's what the per-call `--model` override is
for.

## Persona vs task-type

A `TaskType` (`code`, `test`, `docs`, `research`) is a coarse
category that selects a default system prompt from `workers/prompts.py`.
A persona is a finer-grained specialization that overrides those
defaults for a specific role.

Most callers don't think about personas — they pass a task and the
orchestrator picks `coder` (or whatever maps from the `TaskType`).
Personas matter when you want non-coding agents in the loop:
content review, design, research synthesis, etc.

## Local personas (operator-only, gitignored)

For personas that reference private skills, internal paths, or
operator-specific tooling, keep them out of version control using the
local config mechanism:

1. Copy the example file:
   ```bash
   cp config/personas.local.example.yaml config/personas.local.yaml
   ```

2. Edit `config/personas.local.yaml` to add or adjust your personas.
   The file is listed in `.gitignore` and will never be committed.

3. The registry loads this file automatically at import time — no
   code change required. If the file is absent, the loader is a
   silent no-op.

`config/personas.local.example.yaml` (tracked) serves as both
documentation and a ready-to-copy template. It contains the four
voice/media persona examples (`qwen-voice-reply`, `kokoro-tts`,
`voice-project`, `media-production`) showing the full schema in YAML form.

The local file is merged via `providers.persona_registry.load_local_personas()`.
You can also call this function directly with a custom path if you need
to load personas from a non-standard location.

## Tips

- Lead with **ROLE / SCOPE / OUTPUT FORMAT / CONSTRAINTS** — that
  structure helps the model index its own behavior under pressure
- Be explicit about what the persona **must not** do; positive-only
  instruction leaks into off-task output
- Write OUTPUT FORMAT as a literal template the model can copy. The
  closer to verbatim, the more reliable the parse downstream
- Keep operator-specific lists (employer names, codenames,
  domain-specific vocabulary) in your own persona module; never
  upstream those into the framework defaults
