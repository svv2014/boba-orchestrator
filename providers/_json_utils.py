"""Shared JSON extraction helper for boba-orchestrator providers."""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict:
    """Extract the first valid JSON object from model response text.

    Handles:
    - Clean JSON
    - ```json ... ``` fences (case-insensitive)
    - Bare ``` ... ``` fences
    - Preamble text before the JSON
    - Trailing text after the JSON
    """
    # First, try to extract from a code fence (```json or bare ```)
    fence_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)```",
        text,
        re.DOTALL,
    )
    if fence_match:
        fenced = fence_match.group(1).strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass  # Fall through to brace-matching

    # Find the first '{' and try to parse progressively larger slices
    # to find the first complete JSON object
    start_idx = text.find("{")
    if start_idx == -1:
        raise json.JSONDecodeError(
            f"No JSON object found in response (first 200 chars): {text[:200]}",
            text,
            0,
        )

    # Track brace depth to find the matching closing brace
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            if in_string:
                escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start_idx : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # This balanced block wasn't valid JSON; keep searching
                    # from the next '{' after start_idx
                    next_start = text.find("{", start_idx + 1)
                    if next_start != -1 and next_start < i:
                        # Reset — but for simplicity, fall through to
                        # the final error below
                        pass
                    break

    # Last resort: try json.loads on the whole stripped text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        raise json.JSONDecodeError(
            f"Could not extract valid JSON (first 200 chars): {text[:200]}",
            text,
            0,
        )
