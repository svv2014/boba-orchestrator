"""Tests for _run_queue exit code correctness."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_run_queue_returns_zero_on_all_success(tmp_path):
    """_run_queue returns 0 when no tasks failed."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("projects: []\n")

    mock_budget = MagicMock()
    mock_budget.tasks_failed = 0
    mock_budget.summary = "1 succeeded, 0 failed"
    mock_budget.should_stop.return_value = None

    with (
        patch("orchestrator._load_config", return_value={}),
        patch("orchestrator._run_background", new_callable=AsyncMock, return_value=2),
        patch("security.guardrails.RunBudget", return_value=mock_budget),
        patch("security.guardrails.GuardrailConfig.from_config") as mock_gc,
        patch("notifier.telegram_notifier.notify", new_callable=AsyncMock),
    ):
        mock_gc.return_value.max_tasks_per_run = 5

        from orchestrator import _run_queue
        result = await _run_queue(str(config_file), max_tasks=5)

    assert result == 0


@pytest.mark.asyncio
async def test_run_queue_returns_one_on_failure(tmp_path):
    """_run_queue returns 1 when at least one task failed."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("projects: []\n")

    mock_budget = MagicMock()
    mock_budget.tasks_failed = 1
    mock_budget.summary = "0 succeeded, 1 failed"
    mock_budget.should_stop.return_value = None

    with (
        patch("orchestrator._load_config", return_value={}),
        patch("orchestrator._run_background", new_callable=AsyncMock, return_value=2),
        patch("security.guardrails.RunBudget", return_value=mock_budget),
        patch("security.guardrails.GuardrailConfig.from_config") as mock_gc,
        patch("notifier.telegram_notifier.notify", new_callable=AsyncMock),
    ):
        mock_gc.return_value.max_tasks_per_run = 5

        from orchestrator import _run_queue
        result = await _run_queue(str(config_file), max_tasks=5)

    assert result == 1
