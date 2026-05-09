"""Tests for persona_registry local-file loader."""

import pathlib
import textwrap

import pytest

from providers.persona_registry import (
    PERSONA_CONFIGS,
    get_persona_config,
    list_personas,
    load_local_personas,
)

_GENERIC_PERSONAS = {"architect", "coder", "reviewer", "tester", "assistant", "engineering_director", "researcher", "designer"}


def test_without_local_file_only_generic_personas(tmp_path):
    """Registry contains only generic personas when no local file is present."""
    missing = tmp_path / "personas.local.yaml"
    original_keys = set(PERSONA_CONFIGS.keys())

    load_local_personas(path=missing)

    assert set(PERSONA_CONFIGS.keys()) == original_keys
    assert _GENERIC_PERSONAS.issubset(original_keys)
    assert "qwen-voice-reply" not in PERSONA_CONFIGS
    assert "kokoro-tts" not in PERSONA_CONFIGS


def test_with_fixture_local_file_merges_entries(tmp_path):
    """Registry gains extra entries when a local personas file is present."""
    local_yaml = tmp_path / "personas.local.yaml"
    local_yaml.write_text(
        textwrap.dedent("""\
            test-voice-persona:
              model: "claude-sonnet-4-6"
              timeout_seconds: 60
              scope: "Test voice persona."
              output_format: "Plain text."
              system_prefix: "You are a test voice agent."
              tools_disabled: []

            test-media-persona:
              model: "claude-haiku-4-5-20251001"
              timeout_seconds: 120
              scope: "Test media persona."
              output_format: "Markdown."
              system_prefix: "You are a test media agent."
        """)
    )

    try:
        load_local_personas(path=local_yaml)

        assert "test-voice-persona" in PERSONA_CONFIGS
        assert "test-media-persona" in PERSONA_CONFIGS
        assert _GENERIC_PERSONAS.issubset(set(PERSONA_CONFIGS.keys()))

        cfg = PERSONA_CONFIGS["test-voice-persona"]
        assert cfg["model"] == "claude-sonnet-4-6"
        assert cfg["timeout_seconds"] == 60
        assert cfg["tools_disabled"] == []
    finally:
        PERSONA_CONFIGS.pop("test-voice-persona", None)
        PERSONA_CONFIGS.pop("test-media-persona", None)


def test_local_file_empty_is_noop(tmp_path):
    """Empty YAML file does not corrupt the registry."""
    local_yaml = tmp_path / "personas.local.yaml"
    local_yaml.write_text("")
    original_keys = set(PERSONA_CONFIGS.keys())

    load_local_personas(path=local_yaml)

    assert set(PERSONA_CONFIGS.keys()) == original_keys


def test_get_persona_config_fallback():
    """Unknown persona falls back to 'coder'."""
    cfg = get_persona_config("nonexistent-persona-xyz")
    assert cfg is PERSONA_CONFIGS["coder"]


def test_list_personas_sorted():
    personas = list_personas()
    assert personas == sorted(personas)
    assert "coder" in personas
