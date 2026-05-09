"""tests/test_claude_error_formatter.py — coverage for #21.

Verifies _format_claude_error builds a useful error string from
non-zero claude CLI exits. The 2026-05-02 failure cluster (8+ tickets
losing dev/review/qa cycles) exposed that claude with `-p --output-format
text` writes diagnostics to stdout, not stderr. The previous code only
captured stderr and emitted "claude CLI exited with code 1: " (empty
body), leaving operators blind.
"""

from __future__ import annotations

from providers.claude_cli_backend import _format_claude_error


def test_stderr_present_uses_stderr() -> None:
    err = _format_claude_error(
        returncode=1,
        stdout=b"some stdout that should NOT win",
        stderr=b"rate limit exceeded",
    )
    assert "[stderr]" in err
    assert "rate limit exceeded" in err
    assert "stdout" not in err  # stderr wins when both are present


def test_stderr_empty_falls_through_to_stdout() -> None:
    err = _format_claude_error(
        returncode=1,
        stdout=b"Error: context window exceeded (212k > 200k)",
        stderr=b"",
    )
    assert "[stdout]" in err
    assert "context window exceeded" in err


def test_stderr_whitespace_only_treated_as_empty() -> None:
    err = _format_claude_error(
        returncode=1,
        stdout=b"actual error in stdout",
        stderr=b"   \n\n  \t  ",
    )
    assert "[stdout]" in err
    assert "actual error in stdout" in err


def test_both_empty_returns_explicit_marker() -> None:
    err = _format_claude_error(returncode=1, stdout=b"", stderr=b"")
    assert "no stderr or stdout captured" in err
    assert "exit code 1" in err
    # No "[stderr]" / "[stdout]" tags when both empty.
    assert "[stderr]" not in err
    assert "[stdout]" not in err


def test_long_body_is_tailed_with_ellipsis() -> None:
    long = b"x" * 2000  # 2000 chars > default max_chars=800
    err = _format_claude_error(returncode=1, stdout=long, stderr=b"")
    # Tail is included with leading ellipsis; head is dropped.
    assert err.startswith("[stdout] …")
    assert err.endswith("x" * 800)
    # Total payload (excluding source tag + ellipsis) should be capped.
    body = err.split("] ", 1)[1]
    assert len(body) <= 802  # 800 chars + leading "…" + nothing


def test_short_body_not_tailed() -> None:
    err = _format_claude_error(returncode=1, stdout=b"", stderr=b"short error")
    assert "…" not in err
    assert err == "[stderr] short error"


def test_unicode_decoding_does_not_crash() -> None:
    # Bad UTF-8 bytes get replaced rather than raising.
    bad = b"valid prefix \xff\xfe \xc0 trailing"
    err = _format_claude_error(returncode=1, stdout=b"", stderr=bad)
    # Replacement chars present, no exception.
    assert "valid prefix" in err
    assert "[stderr]" in err


def test_custom_max_chars_respected() -> None:
    body = b"y" * 100
    err = _format_claude_error(
        returncode=1, stdout=b"", stderr=body, max_chars=20
    )
    # Body trimmed to 20 chars + ellipsis.
    assert "…" in err
    assert err.endswith("y" * 20)


def test_returncode_in_empty_marker() -> None:
    err = _format_claude_error(returncode=137, stdout=b"", stderr=b"")
    assert "137" in err
