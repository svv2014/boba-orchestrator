"""Strips instruction-like patterns from external content.

This is the primary defense against prompt injection in the orchestrator.
All external data (web pages, emails, fetched files) must pass through
sanitize() before reaching any prompt that can trigger tool use.

Design principle: fail closed. If content looks suspicious, flag it
and let the caller decide whether to skip or proceed with caution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"


@dataclass
class SanitizeResult:
    """Result of sanitizing external content."""

    original: str
    cleaned: str
    severity: Severity
    flags: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return self.severity == Severity.CLEAN

    @property
    def is_suspicious(self) -> bool:
        return self.severity == Severity.SUSPICIOUS

    @property
    def is_dangerous(self) -> bool:
        return self.severity == Severity.DANGEROUS


# Patterns that look like prompt injection attempts.
# Each tuple: (compiled_regex, flag_name, severity)
_PATTERNS: list[tuple[re.Pattern, str, Severity]] = [
    # Direct instruction injection
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.I),
     "ignore_previous_instructions", Severity.DANGEROUS),

    (re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
     "role_override", Severity.DANGEROUS),

    (re.compile(r"(?:^|\n)\s*(system|assistant)\s*:\s*", re.I),
     "role_tag_injection", Severity.DANGEROUS),

    (re.compile(r"<\s*(system|assistant|user)\s*>", re.I),
     "xml_role_injection", Severity.DANGEROUS),

    (re.compile(r"forget\s+(everything|all|your)\s+(you\s+)?(know|learned|were told)", re.I),
     "memory_wipe", Severity.DANGEROUS),

    (re.compile(r"(do\s+not|don'?t|never)\s+(follow|obey|listen\s+to)\s+(the|your|any)\s+(previous|original|system)", re.I),
     "instruction_override", Severity.DANGEROUS),

    # Tool/action manipulation
    (re.compile(r"(run|execute|call|invoke)\s+(this|the\s+following)\s+(command|script|code|bash)", re.I),
     "command_injection", Severity.DANGEROUS),

    (re.compile(r"(send|post|forward|email)\s+(this|the|a)\s+(message|email|notification)\s+to\s+", re.I),
     "action_injection", Severity.SUSPICIOUS),

    (re.compile(r"(delete|remove|drop|truncate)\s+(all|the|every)\s+(file|data|table|record)", re.I),
     "destructive_action", Severity.SUSPICIOUS),

    # Exfiltration attempts — "key" alone excluded (too broad: matches R2/S3 object keys,
    # lookup keys, etc.). Covered specifically by api.?key, secret.?key, access.?key.
    (re.compile(r"(send|post|upload|forward)\s+.{0,30}(api.?key|secret.?key|access.?key|auth.?key|token|password|credential)", re.I),
     "exfiltration_attempt", Severity.DANGEROUS),

    (re.compile(r"(curl|wget|fetch)\s+https?://", re.I),
     "external_request", Severity.SUSPICIOUS),

    # Encoding evasion
    (re.compile(r"(base64|rot13|hex)\s*(encode|decode|convert)", re.I),
     "encoding_evasion", Severity.SUSPICIOUS),

    # Prompt leaking
    (re.compile(r"(print|show|display|reveal|output)\s+(your|the|all)\s+(system\s+)?(prompt|instructions?|rules?|context)", re.I),
     "prompt_leak_attempt", Severity.SUSPICIOUS),
]


def sanitize(content: str) -> SanitizeResult:
    """Scan content for injection patterns and return a sanitize result.

    The cleaned output has flagged patterns replaced with [REDACTED].
    The caller should check severity and decide whether to proceed.

    Args:
        content: Raw external content to sanitize.

    Returns:
        SanitizeResult with cleaned content, severity, and flags.
    """
    if not content or not content.strip():
        return SanitizeResult(
            original=content,
            cleaned=content,
            severity=Severity.CLEAN,
        )

    flags: list[str] = []
    max_severity = Severity.CLEAN
    cleaned = content

    for pattern, flag_name, severity in _PATTERNS:
        matches = pattern.findall(cleaned)
        if matches:
            flags.append(flag_name)
            if severity == Severity.DANGEROUS and max_severity != Severity.DANGEROUS:
                max_severity = Severity.DANGEROUS
            elif severity == Severity.SUSPICIOUS and max_severity == Severity.CLEAN:
                max_severity = Severity.SUSPICIOUS

            # Redact the matched patterns
            cleaned = pattern.sub("[REDACTED]", cleaned)

    return SanitizeResult(
        original=content,
        cleaned=cleaned,
        severity=max_severity,
        flags=flags,
    )


def is_safe(content: str) -> bool:
    """Quick check — returns True if content has no injection patterns."""
    return sanitize(content).is_safe
