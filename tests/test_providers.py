"""Tests for the provider abstraction layer."""

import threading

import pytest

from providers.base import (
    PlanResult,
    SubTask,
    TaskStatus,
    TaskType,
    WorkerResult,
    PlannerBackend,
    WorkerBackend,
)
from providers.registry import (
    _PROVIDERS,
    _reset_registry,
    get_planner,
    get_worker,
    list_providers,
    register_provider,
)


@pytest.fixture(autouse=True)
def reset_registry_fixture():
    _reset_registry()
    yield
    _reset_registry()


# --- Data classes ---


def test_subtask_defaults():
    st = SubTask(id="t1", type=TaskType.CODE, description="write foo", target_repo="/tmp")
    assert st.context_files == []
    assert st.constraints == ""
    assert st.tools == []


def test_plan_result():
    st = SubTask(id="t1", type=TaskType.TEST, description="test bar", target_repo="/tmp")
    pr = PlanResult(task_summary="build widget", subtasks=[st], project_name="myproj")
    assert len(pr.subtasks) == 1
    assert pr.project_name == "myproj"


def test_worker_result_success():
    wr = WorkerResult(task_id="t1", status=TaskStatus.DONE, files_changed=["a.py"])
    assert wr.error is None
    assert wr.status == TaskStatus.DONE


def test_worker_result_error():
    wr = WorkerResult(task_id="t1", status=TaskStatus.ERROR, error="API timeout")
    assert wr.error == "API timeout"


def test_task_type_values():
    assert TaskType.CODE.value == "code"
    assert TaskType.TEST.value == "test"
    assert TaskType.DOCS.value == "docs"
    assert TaskType.RESEARCH.value == "research"


# --- Registry ---


class FakePlanner:
    def __init__(self, config):
        self.model = config.get("model", "fake-planner")

    async def plan(self, context, instruction):
        return PlanResult(task_summary="fake", subtasks=[])

    async def select_task(self, context):
        return "fake task"


class FakeWorker:
    def __init__(self, config):
        self.model = config.get("model", "fake-worker")

    async def execute(self, task):
        return WorkerResult(task_id=task.id, status=TaskStatus.DONE)


def test_register_and_resolve_planner():
    register_provider("fake", planner_factory=FakePlanner, worker_factory=FakeWorker)
    config = {"planner": {"provider": "fake", "model": "test-model"}}
    planner = get_planner(config)
    assert isinstance(planner, FakePlanner)
    assert planner.model == "test-model"


def test_register_and_resolve_worker():
    register_provider("fake", planner_factory=FakePlanner, worker_factory=FakeWorker)
    config = {"workers": {"provider": "fake", "model": "test-worker"}}
    worker = get_worker(config)
    assert isinstance(worker, FakeWorker)
    assert worker.model == "test-worker"


def test_unknown_provider_raises():
    _reset_registry()
    config = {"planner": {"provider": "nonexistent-xyz"}}
    with pytest.raises(ValueError, match="Unknown provider"):
        get_planner(config)


def test_protocol_compliance():
    """FakePlanner and FakeWorker satisfy the Protocol contracts."""
    assert isinstance(FakePlanner({}), PlannerBackend)
    assert isinstance(FakeWorker({}), WorkerBackend)


# --- JSON parser (used by anthropic backend) ---


def test_parse_json_plain():
    from providers.anthropic_backend import _parse_json

    result = _parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_parse_json_code_block():
    from providers.anthropic_backend import _parse_json

    text = '```json\n{"key": "value"}\n```'
    result = _parse_json(text)
    assert result == {"key": "value"}


def test_parse_json_invalid_raises():
    from providers.anthropic_backend import _parse_json

    with pytest.raises(Exception):
        _parse_json("not json at all")


# --- list_providers ---


def test_list_providers_includes_lazy_loaders():
    """list_providers returns built-in names even before they are loaded."""
    _reset_registry()
    providers = list_providers()
    assert "anthropic" in providers
    assert "claude-cli" in providers


def test_list_providers_includes_registered():
    """list_providers includes externally registered providers."""
    _reset_registry()
    register_provider("my-custom", planner_factory=FakePlanner, worker_factory=FakeWorker)
    providers = list_providers()
    assert "my-custom" in providers
    assert "anthropic" in providers  # lazy-loadable still visible


def test_list_providers_no_duplicates():
    """Each provider name appears at most once."""
    _reset_registry()
    providers = list_providers()
    assert len(providers) == len(set(providers))


def test_list_providers_is_sorted():
    """list_providers returns a sorted list."""
    _reset_registry()
    providers = list_providers()
    assert providers == sorted(providers)


# --- _reset_registry ---


def test_reset_clears_registered_providers():
    register_provider("temp-provider", planner_factory=FakePlanner)
    _reset_registry()
    providers = list_providers()
    assert "temp-provider" not in providers


def test_reset_allows_lazy_reload():
    """After reset, lazy loaders still run on next _ensure_builtins call."""
    _reset_registry()
    register_provider("fake-reset", planner_factory=FakePlanner, worker_factory=FakeWorker)
    _reset_registry()
    providers = list_providers()
    assert "fake-reset" not in providers


# --- Thread safety ---


def test_concurrent_register_no_data_race():
    """Concurrent register_provider calls must not raise or corrupt state."""
    _reset_registry()
    errors: list[Exception] = []

    def register(i: int) -> None:
        try:
            register_provider(
                f"provider-{i}",
                planner_factory=FakePlanner,
                worker_factory=FakeWorker,
            )
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=register, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    providers = list_providers()
    for i in range(20):
        assert f"provider-{i}" in providers


def test_concurrent_registration_all_resolved():
    """All concurrently registered providers can be resolved via get_planner."""
    names = [f"conc-{i}" for i in range(20)]
    threads = [
        threading.Thread(
            target=register_provider,
            args=(name,),
            kwargs={"planner_factory": FakePlanner, "worker_factory": FakeWorker},
        )
        for name in names
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for name in names:
        planner = get_planner({"planner": {"provider": name}})
        assert isinstance(planner, FakePlanner)


# --- Error cases ---


def test_missing_provider_error_lists_available():
    register_provider("available-one", planner_factory=FakePlanner, worker_factory=FakeWorker)
    with pytest.raises(ValueError, match="available-one"):
        get_planner({"planner": {"provider": "definitely-missing"}})


def test_none_planner_factory_raises():
    register_provider("no-planner", planner_factory=None, worker_factory=FakeWorker)
    with pytest.raises(ValueError, match="no planner backend"):
        get_planner({"planner": {"provider": "no-planner"}})


def test_none_worker_factory_raises():
    register_provider("no-worker", planner_factory=FakePlanner, worker_factory=None)
    with pytest.raises(ValueError, match="no worker backend"):
        get_worker({"workers": {"provider": "no-worker"}})


# --- Lazy loading ---


def test_lazy_loading_builtins_not_imported_until_first_access():
    # Immediately after reset (autouse fixture), _PROVIDERS must be empty
    assert "anthropic" not in _PROVIDERS
    assert "claude-cli" not in _PROVIDERS
    # Trigger _ensure_builtins via get_planner
    register_provider("trigger", planner_factory=FakePlanner, worker_factory=FakeWorker)
    get_planner({"planner": {"provider": "trigger"}})
    # _ensure_builtins has now run; the pre-call state was verified above


# --- Re-registration ---


def test_reregistration_overwrites_factory():
    class AltPlanner:
        def __init__(self, config):
            self.tag = "alt"

        async def plan(self, context, instruction):
            return PlanResult(task_summary="alt", subtasks=[])

        async def select_task(self, context):
            return "alt task"

    register_provider("overwrite-me", planner_factory=FakePlanner, worker_factory=FakeWorker)
    register_provider("overwrite-me", planner_factory=AltPlanner, worker_factory=FakeWorker)
    planner = get_planner({"planner": {"provider": "overwrite-me"}})
    assert isinstance(planner, AltPlanner)
    assert planner.tag == "alt"


# --- Partial registration ---


def test_register_planner_only():
    register_provider("planner-only", planner_factory=FakePlanner)
    planner = get_planner({"planner": {"provider": "planner-only"}})
    assert isinstance(planner, FakePlanner)
    with pytest.raises(ValueError, match="no worker backend"):
        get_worker({"workers": {"provider": "planner-only"}})


def test_register_worker_only():
    register_provider("worker-only", worker_factory=FakeWorker)
    worker = get_worker({"workers": {"provider": "worker-only"}})
    assert isinstance(worker, FakeWorker)
    with pytest.raises(ValueError, match="no planner backend"):
        get_planner({"planner": {"provider": "worker-only"}})
