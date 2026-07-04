"""Comprehensive tests validating strict cluster supervision, health, and checkpointing."""

import pytest

from alphalab.production import (
    Checkpoint,
    CheckpointError,
    HeartbeatStatus,
    InvalidRuntimeStateError,
    LogLevel,
    ProcessState,
    ProductionAdapter,
    ProductionEngine,
    ProductionState,
    ProductionValidationError,
    RecoveryError,
    alert_summary,
    checkpoint_history,
    get_module,
    health_report,
    heartbeat_status,
    runtime_metrics,
    runtime_summary,
)


@pytest.fixture
def base_state() -> ProductionState:
    return ProductionEngine.initialize("PROD-01")


@pytest.fixture
def running_state(base_state: ProductionState) -> ProductionState:
    return ProductionEngine.start(base_state, 1000.0)


@pytest.fixture
def sample_checkpoint() -> Checkpoint:
    return ProductionAdapter.to_checkpoint("CP-1", 1000.0, "r", "p", "o", "pos", "res", "rep")


# --- INITIALIZATION & LIFECYCLE (6 Tests) ---


def test_initialization() -> None:
    state = ProductionEngine.initialize("R-1")
    assert state.runtime_id == "R-1"
    assert not state.is_running
    with pytest.raises(ValueError):
        ProductionEngine.initialize("")


def test_start_success(base_state: ProductionState) -> None:
    s1 = ProductionEngine.start(base_state, 1000.0)
    assert s1.is_running
    assert s1.start_time == 1000.0
    assert any(type(e).__name__ == "RuntimeStarted" for e in s1.events)


def test_start_already_running(running_state: ProductionState) -> None:
    with pytest.raises(InvalidRuntimeStateError, match="already active"):
        ProductionEngine.start(running_state, 1001.0)


def test_stop_success(running_state: ProductionState) -> None:
    s1 = ProductionEngine.stop(running_state, "Manual", 1001.0)
    assert not s1.is_running
    assert any(type(e).__name__ == "RuntimeStopped" for e in s1.events)


def test_stop_not_running(base_state: ProductionState) -> None:
    with pytest.raises(InvalidRuntimeStateError, match="not active"):
        ProductionEngine.stop(base_state, "Fail", 1000.0)


# --- SUPERVISOR TESTS (8 Tests) ---


def test_register_module(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    mod = get_module(s1, "OMS")
    assert mod is not None
    assert mod.state == ProcessState.STOPPED


def test_register_duplicate(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    with pytest.raises(ProductionValidationError, match="already registered"):
        ProductionEngine.register_module(s1, "OMS", 1001.0)


def test_start_module(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    s2 = ProductionEngine.start_module(s1, "OMS", 1001.0)
    mod = get_module(s2, "OMS")
    assert mod is not None
    assert mod.state == ProcessState.RUNNING
    assert any(type(e).__name__ == "ModuleStarted" for e in s2.events)


def test_start_missing_module(running_state: ProductionState) -> None:
    with pytest.raises(InvalidRuntimeStateError, match="not found"):
        ProductionEngine.start_module(running_state, "MISSING", 1000.0)


def test_stop_module(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    s2 = ProductionEngine.start_module(s1, "OMS", 1001.0)
    s3 = ProductionEngine.stop_module(s2, "OMS", 1002.0)
    mod = get_module(s3, "OMS")
    assert mod is not None
    assert mod.state == ProcessState.STOPPED


def test_restart_module(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    s2 = ProductionEngine.start_module(s1, "OMS", 1001.0)
    s3 = ProductionEngine.restart_module(s2, "OMS", 1002.0)
    mod = get_module(s3, "OMS")
    assert mod is not None
    assert mod.state == ProcessState.RUNNING
    assert mod.restart_count == 1
    assert any(type(e).__name__ == "ModuleRestarted" for e in s3.events)


# --- HEARTBEAT & SCHEDULER TESTS (6 Tests) ---


def test_heartbeat_creation(running_state: ProductionState) -> None:
    s1 = ProductionEngine.heartbeat(running_state, "OMS", 1.5, 1000.0)
    hbs = heartbeat_status(s1)
    assert len(hbs) == 1
    assert hbs[0].status == HeartbeatStatus.ALIVE
    assert runtime_metrics(s1).heartbeats_received == 1


def test_scheduler_tick_updates_uptime(running_state: ProductionState) -> None:
    s1 = ProductionEngine.tick(running_state, 1005.0)
    assert s1.uptime == 5.0


def test_scheduler_heartbeat_timeout(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    s2 = ProductionEngine.heartbeat(s1, "OMS", 1.0, 1000.0)
    # Expected interval = 1.0, threshold = 3.0. Tick at 1004.0 breaches threshold.
    s3 = ProductionEngine.tick(s2, 1004.0)

    hbs = heartbeat_status(s3)
    assert hbs[0].status == HeartbeatStatus.TIMEOUT
    assert hbs[0].missed_count == 1

    mod = get_module(s3, "OMS")
    assert mod is not None
    assert mod.state == ProcessState.FAILED
    assert len(alert_summary(s3)) == 1


# --- HEALTH TESTS (4 Tests) ---


def test_update_health(running_state: ProductionState) -> None:
    s1 = ProductionEngine.health(running_state, 10.0, 10.0, 0, True, True, 1000.0)
    hr = health_report(s1)
    assert hr is not None
    assert hr.score > 90.0
    assert hr.is_healthy


def test_health_degradation(running_state: ProductionState) -> None:
    s1 = ProductionEngine.health(running_state, 100.0, 100.0, 500, False, False, 1000.0)
    hr = health_report(s1)
    assert hr is not None
    assert hr.score == 0.0
    assert not hr.is_healthy


# --- CHECKPOINT TESTS (6 Tests) ---


def test_create_checkpoint(running_state: ProductionState, sample_checkpoint: Checkpoint) -> None:
    s1 = ProductionEngine.checkpoint(running_state, sample_checkpoint, 1000.0)
    assert len(checkpoint_history(s1)) == 1
    assert runtime_metrics(s1).total_checkpoints == 1


def test_duplicate_checkpoint(
    running_state: ProductionState, sample_checkpoint: Checkpoint
) -> None:
    s1 = ProductionEngine.checkpoint(running_state, sample_checkpoint, 1000.0)
    with pytest.raises(CheckpointError, match="Duplicate"):
        ProductionEngine.checkpoint(s1, sample_checkpoint, 1001.0)


def test_restore_checkpoint(running_state: ProductionState, sample_checkpoint: Checkpoint) -> None:
    s1 = ProductionEngine.checkpoint(running_state, sample_checkpoint, 1000.0)
    s2 = ProductionEngine.restore(s1, "CP-1", 1001.0)
    assert any(type(e).__name__ == "CheckpointRestored" for e in s2.events)


def test_restore_missing(running_state: ProductionState) -> None:
    with pytest.raises(CheckpointError, match="not found"):
        ProductionEngine.restore(running_state, "CP-MISSING", 1000.0)


# --- RECOVERY TESTS (4 Tests) ---


def test_recover_success(running_state: ProductionState, sample_checkpoint: Checkpoint) -> None:
    s1 = ProductionEngine.checkpoint(running_state, sample_checkpoint, 1000.0)
    s2 = ProductionEngine.stop(s1, "Crash", 1001.0)
    assert not s2.is_running

    s3 = ProductionEngine.recover(s2, "AutoRecover", 1002.0)
    assert s3.is_running
    assert runtime_metrics(s3).total_recoveries == 1
    assert any(type(e).__name__ == "RecoveryCompleted" for e in s3.events)


def test_recover_no_checkpoints(running_state: ProductionState) -> None:
    s1 = ProductionEngine.stop(running_state, "Crash", 1001.0)
    with pytest.raises(RecoveryError, match="No checkpoints exist"):
        ProductionEngine.recover(s1, "AutoRecover", 1002.0)


# --- LOGGING & VIEWS TESTS (5 Tests) ---


def test_logging(running_state: ProductionState) -> None:
    entry = ProductionAdapter.to_log(1000.0, LogLevel.INFO, "SYS", "Boot")
    s1 = ProductionEngine.log(running_state, entry)
    assert len(s1.logs) == 1
    assert s1.logs[0].message == "Boot"


def test_views_access(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    summ = runtime_summary(s1)
    assert summ["runtime_id"] == "PROD-01"
    assert summ["total_modules"] == 1
    assert summ["is_running"]


def test_immutability(running_state: ProductionState) -> None:
    s1 = ProductionEngine.register_module(running_state, "OMS", 1000.0)
    assert s1 is not running_state
    assert len(running_state.processes) == 0
    assert len(s1.processes) == 1
