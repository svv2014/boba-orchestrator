"""Verify get_running_loop() does not emit DeprecationWarning inside a coroutine."""

import asyncio
import warnings

import pytest


@pytest.mark.asyncio
async def test_no_deprecation_warning_in_notify():
    """get_running_loop() must not emit DeprecationWarning inside a coroutine."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        loop = asyncio.get_running_loop()
        assert loop is not None
