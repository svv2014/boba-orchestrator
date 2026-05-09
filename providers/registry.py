"""Provider registry — resolves config to backend instances.

Usage:
    planner = get_planner(config)  # returns PlannerBackend
    worker = get_worker(config)    # returns WorkerBackend

The registry auto-discovers built-in providers (anthropic, claude-cli)
and supports external registration for custom backends.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .base import PlannerBackend, WorkerBackend

# Provider factories: name -> {planner, worker}
_PROVIDERS: dict[str, dict[str, Any]] = {}

# Lock protecting all mutations of _PROVIDERS
_REGISTRY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Lazy loaders — add new built-in backends here without touching _ensure_builtins
# ---------------------------------------------------------------------------

def _load_anthropic() -> None:
    from .anthropic_backend import AnthropicPlanner, AnthropicWorker  # noqa: PLC0415
    register_provider("anthropic", planner_factory=AnthropicPlanner, worker_factory=AnthropicWorker)


def _load_claude_cli() -> None:
    from .claude_cli_backend import ClaudeCliPlanner, ClaudeCliWorker  # noqa: PLC0415
    register_provider("claude-cli", planner_factory=ClaudeCliPlanner, worker_factory=ClaudeCliWorker)


_LAZY_LOADERS: dict[str, Callable[[], None]] = {
    "anthropic": _load_anthropic,
    "claude-cli": _load_claude_cli,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_provider(
    name: str,
    planner_factory: Any = None,
    worker_factory: Any = None,
) -> None:
    """Register a provider backend by name.

    Args:
        name: Provider name (e.g. "anthropic", "openai", "ollama").
        planner_factory: Callable(config) -> PlannerBackend.
        worker_factory: Callable(config) -> WorkerBackend.
    """
    with _REGISTRY_LOCK:
        _PROVIDERS[name] = {
            "planner": planner_factory,
            "worker": worker_factory,
        }


def list_providers() -> list[str]:
    """Return all available provider names (registered + lazy-loadable).

    Returns:
        Sorted list of provider names.
    """
    with _REGISTRY_LOCK:
        registered = set(_PROVIDERS.keys())
    return sorted(registered | set(_LAZY_LOADERS.keys()))


def get_planner(config: dict) -> PlannerBackend:
    """Resolve planner backend from config.

    Config format:
        planner:
          provider: anthropic
          model: claude-opus-4-6
    """
    _ensure_builtins()

    planner_config = config.get("planner", {})
    provider_name = planner_config.get("provider", "anthropic")

    if provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider_name}'. "
            f"Available: {list_providers()}"
        )

    factory = _PROVIDERS[provider_name]["planner"]
    if factory is None:
        raise ValueError(f"Provider '{provider_name}' has no planner backend.")

    return factory(planner_config)


def get_worker(config: dict) -> WorkerBackend:
    """Resolve worker backend from config.

    Config format:
        workers:
          provider: anthropic
          model: claude-sonnet-4-6
          max_parallel: 3
    """
    _ensure_builtins()

    worker_config = config.get("workers", {})
    provider_name = worker_config.get("provider", "anthropic")

    if provider_name not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider_name}'. "
            f"Available: {list_providers()}"
        )

    factory = _PROVIDERS[provider_name]["worker"]
    if factory is None:
        raise ValueError(f"Provider '{provider_name}' has no worker backend.")

    return factory(worker_config)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_builtins() -> None:
    """Lazy-register built-in providers on first access."""
    for name, loader in _LAZY_LOADERS.items():
        if name not in _PROVIDERS:
            try:
                loader()
            except ImportError:
                pass


def _reset_registry() -> None:
    """Clear all registered providers and reset lazy-loader state.

    For testing only — ensures no cross-test pollution from prior
    register_provider() calls.  Clearing _PROVIDERS also resets lazy-loader
    state: _ensure_builtins() will re-run each loader on the next access.
    """
    with _REGISTRY_LOCK:
        _PROVIDERS.clear()
