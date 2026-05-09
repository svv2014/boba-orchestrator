# Security policy

## Reporting a vulnerability

If you find a security issue, please **do not open a public GitHub
issue**. Instead, use one of the following channels:

- GitHub's [private vulnerability reporting](https://github.com/svv2014/boba-orchestrator/security/advisories/new)
- Email the maintainer at the address listed on
  https://github.com/svv2014

You'll receive an acknowledgment within a few business days. Coordinated
disclosure timeline is typically 30–90 days depending on severity and
upstream fixes required.

## Threat model

boba-orchestrator dispatches LLM agents that may execute code, call
shell commands, and write to the filesystem. The threat model lives in
[`docs/threat-model.md`](docs/threat-model.md). High-level summary:

- **Prompt injection.** Worker prompts assemble user-controlled content;
  task descriptions are sanitized via `security/sanitizer.py` before
  dispatch. Dangerous patterns abort the run.
- **Repo escape.** `validate_target_repo` enforces an allowlist of
  directories workers may operate in. Configured via
  `guardrails.allowed_repos` or auto-populated from `projects:`.
- **Resource exhaustion.** Hard caps on worker timeout, parallel workers,
  total run time, subtasks per plan, and consecutive failures.
  See `guardrails:` in `config/orchestrator.example.yaml`.
- **Credential exfiltration.** API keys read from environment only;
  never logged in cleartext. The sanitizer flags responses containing
  credential-like patterns.

## Out of scope

- Vulnerabilities in upstream LLM providers (report to the provider).
- Issues caused by removing or weakening guardrails in operator config.
- Local-attacker scenarios where the attacker already has filesystem
  access — the orchestrator is not a sandboxing system.

## Hardening recommendations for operators

- Run inside a container or unprivileged user account.
- Set `guardrails.max_worker_timeout_seconds` and
  `max_total_worker_seconds` to values appropriate for your hardware.
- Keep `guardrails.allowed_repos` explicit and minimal.
- Rotate API keys regularly; never check them into config files.
- Review `sanitizer.py`'s flagged patterns periodically against your
  task corpus.
