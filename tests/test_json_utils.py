"""Unit tests for providers._json_utils.extract_json."""

import json
import pytest

from providers._json_utils import extract_json


def test_plain_json():
    assert extract_json('{"key": "value"}') == {"key": "value"}


def test_json_code_fence():
    text = '```json\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}


def test_json_code_fence_no_lang():
    text = '```\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}


def test_json_code_fence_uppercase():
    text = '```JSON\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}


def test_preamble_text():
    text = 'Here is the result:\n{"key": "value"}'
    assert extract_json(text) == {"key": "value"}


def test_trailing_text():
    text = '{"key": "value"}\nSome trailing commentary.'
    assert extract_json(text) == {"key": "value"}


def test_nested_object():
    text = '{"outer": {"inner": 42}}'
    assert extract_json(text) == {"outer": {"inner": 42}}


def test_string_with_braces_inside():
    text = '{"key": "value with { brace }"}'
    assert extract_json(text) == {"key": "value with { brace }"}


def test_string_with_escaped_quote():
    text = r'{"key": "val\"ue"}'
    assert extract_json(text) == {"key": 'val"ue'}


def test_no_json_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("not json at all")


def test_no_braces_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("just plain text")


def test_last_resort_full_text():
    assert extract_json('  {"key": "value"}  ') == {"key": "value"}
