"""The standalone packages accumulate the canonical way, and their indexes agree.

ADR-0032 category C finding 1: ten standalone packages grew ``state.events`` with
tuple splats and their indexes with ``dict()`` copies, so ``N`` transitions
copied ``O(N^2)`` entries. Measured at v2.16 they ran at 3.4x-4.9x per doubling
where linear is ~2x. ADR-0034 converts them to
:class:`~alphalab.common.append_log.AppendOnlyLog` and
:class:`~alphalab.common.persistent_map.PersistentMap` / ``PersistentSet`` -- the
containers the execution path has used since v2.1 and v2.2. Two of the ten,
``alphalab.integrations`` and ``alphalab.production``, were removed instead,
and v3.12 removed two more, ``alphalab.plugins`` and ``alphalab.optimizer``
(ledger SCF-003): a plugin loader whose ``execute()`` was a placeholder, and a
second parameter search beside ``research.parameter_sweep``.

**The containers were not the whole story, and profiling first is what found
that.** Three packages had a larger term the finding did not name, and converting
the containers alone would have left most of the cost in place -- in
``feature_store`` it made registration *four times slower*, because a
``PersistentMap`` iterates more slowly than a ``dict`` and the real cost was a
full scan per call:

======================= ====================================================
Package                 The term that actually dominated
======================= ====================================================
``distributed``         re-sorting the whole queue per submission (~65%) and
                        building a union of four containers per validation
                        (~26%)
``feature_store``       ``{m.feature_id for m in features.values()}`` per
                        registration
``plugins``             ``{p.metadata().name for p in plugins.values()}``
                        per registration (~54%) -- until the package was
                        removed in v3.12
======================= ====================================================

Each is now answered by a **derived index** carried on the state --
``queued_ids`` and ``registered_feature_ids`` -- exactly as
the OMS order book has carried its asset and strategy indexes since v2.2. A
derived index is only as good as its agreement with what it indexes, so this
file asserts that agreement after every operation that can move it.

Until v3.12 one thing was deliberately *not* linear:
``OptimizerState.pending_trials``. It went with the optimizer (ledger OFE-013).
"""

from collections.abc import Callable

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.reporting.state import ReportingState
from tests.regression._timing import CLOCK, timings

# --------------------------------------------------------------------------- #
# Builders: one per package, each driving the path that used to be quadratic
# --------------------------------------------------------------------------- #


def _scheduler(n: int) -> object:
    from alphalab.scheduler import SchedulerEngine, ScheduleType, Timer

    state = SchedulerEngine.initialize(1000.0)
    for i in range(n):
        state = SchedulerEngine.schedule_timer(
            state, Timer(f"T-{i}", 1000.0 + float(i + 1), ScheduleType.ONE_SHOT), 1000.0
        )
    return state


def _feature_store(n: int) -> object:
    from alphalab.feature_store import (
        FeatureMetadata,
        FeatureRegistry,
        FeatureStoreEngine,
        FeatureType,
        FeatureValueType,
    )

    state = FeatureStoreEngine.initialize("FS")
    for i in range(n):
        state = FeatureRegistry.register(
            state,
            FeatureMetadata(
                feature_id=f"f{i}",
                name=f"F{i}",
                version=1,
                feature_type=FeatureType.DERIVED,
                value_type=FeatureValueType.FLOAT,
                owner="bench",
                description="synthetic",
            ),
            float(i),
        )
    return state


def _distributed(n: int) -> object:
    from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType

    state = DistributedEngine.initialize("D")
    for i in range(n):
        state = DistributedEngine.submit_job(
            state,
            Job(
                f"J-{i}",
                JobType.BACKTEST,
                JobStatus.PENDING,
                priority=10,
                created_timestamp=float(i),
            ),
            float(i),
        )
    return state


def _reporting(n: int) -> ReportingState:
    from alphalab.reporting import (
        Report,
        ReportingEngine,
        ReportSection,
        ReportSectionType,
        ReportType,
    )

    section = ReportSection(name="D", section_type=ReportSectionType.TABLE, content=[{"a": 1}])
    state = ReportingEngine.initialize("R")
    for i in range(n):
        state = ReportingEngine.register_report(
            state, Report(f"R-{i}", f"T{i}", float(i), ReportType.PERFORMANCE, (section,))
        )
    return state


def _portfolio_optimizer(n: int) -> object:
    from alphalab.portfolio_optimizer import Portfolio, PortfolioEngine

    state = PortfolioEngine.initialize("PO")
    for i in range(n):
        state = PortfolioEngine.create(state, Portfolio(f"P-{i}", f"p{i}", "USD", 1000.0), float(i))
    return state


def _data(n: int) -> object:
    from alphalab.data import DataAdapter, UniversalDataEngine

    rows = ({"Date": 0.0, "O": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Vol": 1.0},)
    state = UniversalDataEngine.initialize("U")
    for i in range(n):
        meta = DataAdapter.create_metadata(f"DS-{i}", "CSV", "EQUITY", "TICK", 0.0, 1.0)
        state = UniversalDataEngine.ingest(state, UniversalDataEngine.load(meta, rows), float(i))
    return state


#: The converted packages, and how to drive each one's accumulation path.
BUILDERS: dict[str, Callable[[int], object]] = {
    "scheduler": _scheduler,
    "feature_store": _feature_store,
    "distributed": _distributed,
    "reporting": _reporting,
    "portfolio_optimizer": _portfolio_optimizer,
    "data": _data,
}

#: ``package -> the state fields that must hold a canonical container``.
CANONICAL_FIELDS: dict[str, dict[str, type]] = {
    "alphalab.scheduler.state.SchedulerState": {
        "timers": PersistentMap,
        "active_sessions": PersistentMap,
        "events": AppendOnlyLog,
    },
    "alphalab.feature_store.state.FeatureStoreState": {
        "features": PersistentMap,
        "registered_feature_ids": PersistentSet,
        "deprecated_keys": PersistentSet,
        "values": PersistentMap,
        "history": AppendOnlyLog,
        "events": AppendOnlyLog,
    },
    "alphalab.distributed.state.DistributedState": {
        "workers": PersistentMap,
        "queued_jobs": AppendOnlyLog,
        "queued_ids": PersistentSet,
        "running_jobs": PersistentMap,
        "completed_jobs": PersistentMap,
        "failed_jobs": PersistentMap,
        "cancelled_jobs": PersistentMap,
        "events": AppendOnlyLog,
    },
    "alphalab.reporting.state.ReportingState": {
        "reports": PersistentMap,
        "exports": PersistentMap,
        "events": AppendOnlyLog,
    },
    "alphalab.data.state.UniversalDataState": {
        "datasets": PersistentMap,
        "catalog": PersistentMap,
        "quality_reports": PersistentMap,
        "schemas": PersistentMap,
        "metadata": PersistentMap,
        "events": AppendOnlyLog,
    },
    "alphalab.portfolio_optimizer.state.PortfolioEngineState": {
        "portfolios": PersistentMap,
        "weights": PersistentMap,
        "constraints": PersistentMap,
        "metrics": PersistentMap,
        "exposures": PersistentMap,
        "allocations": PersistentMap,
        "cost_estimates": PersistentMap,
        "events": AppendOnlyLog,
    },
}


# --------------------------------------------------------------------------- #
# 1. The containers are the canonical ones
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "field", "container"),
    [
        (path, field, container)
        for path, fields in CANONICAL_FIELDS.items()
        for field, container in fields.items()
    ],
    ids=lambda v: str(v),
)
def test_the_field_defaults_to_the_canonical_container(
    path: str, field: str, container: type
) -> None:
    """Declared *and* defaulted: a ``dict`` default would reintroduce the copy."""

    import importlib

    module_name, class_name = path.rsplit(".", 1)
    state_type = getattr(importlib.import_module(module_name), class_name)

    declared = state_type.__dataclass_fields__[field]
    assert container.__name__ in str(declared.type), f"{path}.{field} is {declared.type}"
    assert declared.default_factory is container


#: Functions allowed to take a local ``dict`` copy of a state index.
#:
#: Each is a **batch** operation over the whole queue in one call, not an
#: accumulation across calls: one copy in, one immutable value out, so the cost
#: is O(queue) per call rather than O(queue) per job. Converting these to
#: per-key ``set`` calls inside the loop would be slower, not faster.
BATCH_ASSIGNERS = frozenset(
    {
        "alphalab/distributed/scheduler.py",
        "alphalab/cluster_scheduler/affinity_scheduler.py",
        "alphalab/cluster_scheduler/priority_scheduler.py",
        # ``SchedulerEngine.advance_clock`` expires every due timer in one pass.
        # Measured: writing each expiry through ``PersistentMap.set`` rather
        # than into a local dict cost ~30% of the 100,000-timer benchmark.
        "alphalab/scheduler/engine.py",
    }
)


def test_no_converted_package_still_splats_a_state_tuple() -> None:
    """``(*state.events, evt)`` and ``dict(state.index)`` per transition are gone.

    Read from the source rather than inferred, and over *executable* lines only:
    two of these modules describe the removed pattern in their own docstrings,
    which is documentation and not a defect.
    """

    import ast
    import pathlib

    package_root = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
    converted = (
        "scheduler",
        "feature_store",
        "distributed",
        "reporting",
        "data",
        "cluster_scheduler",
        "portfolio_optimizer",
    )

    offenders: list[str] = []
    for package in converted:
        for path in sorted((package_root / package).rglob("*.py")):
            relative = str(path.relative_to(package_root.parent))
            if relative in BATCH_ASSIGNERS:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # ``dict(state.x)`` / ``tuple(state.x)``
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"dict", "set", "frozenset"}
                    and node.args
                    and isinstance(node.args[0], ast.Attribute)
                    and isinstance(node.args[0].value, ast.Name)
                    and node.args[0].value.id == "state"
                ):
                    offenders.append(f"{relative}:{node.lineno}: {node.func.id}(state...)")
                # ``(*state.x, y)``
                if isinstance(node, ast.Tuple) and any(
                    isinstance(element, ast.Starred)
                    and isinstance(element.value, ast.Attribute)
                    and isinstance(element.value.value, ast.Name)
                    and element.value.value.id == "state"
                    for element in node.elts
                ):
                    offenders.append(f"{relative}:{node.lineno}: (*state..., x)")

    assert not offenders, "quadratic accumulation survives at:\n" + "\n".join(offenders)


def test_a_batch_assigner_produces_a_canonical_container() -> None:
    """The exemption is for the *local* copy, not for what reaches the state."""

    from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType, WorkerNode
    from alphalab.distributed.node import WorkerStatus

    state = DistributedEngine.initialize("D")
    state = DistributedEngine.register_worker(
        state, WorkerNode("W", "host", WorkerStatus.IDLE, 4), 0.0
    )
    for i in range(3):
        state = DistributedEngine.submit_job(
            state,
            Job(f"J-{i}", JobType.BACKTEST, JobStatus.PENDING, 1, created_timestamp=float(i)),
            float(i),
        )

    assigned = DistributedEngine.assign_jobs(state, 9.0)

    assert isinstance(assigned.workers, PersistentMap)
    assert isinstance(assigned.running_jobs, PersistentMap)
    assert isinstance(assigned.queued_jobs, AppendOnlyLog)
    assert isinstance(assigned.queued_ids, PersistentSet)
    assert isinstance(assigned.events, AppendOnlyLog)
    assert set(assigned.queued_ids) == {job.job_id for job in assigned.queued_jobs}


# --------------------------------------------------------------------------- #
# 2. The derived indexes agree with what they index
# --------------------------------------------------------------------------- #


def test_the_distributed_queue_index_agrees_after_every_operation() -> None:
    from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType, WorkerNode
    from alphalab.distributed.node import WorkerStatus

    def _check(state: object, where: str) -> None:
        assert set(state.queued_ids) == {job.job_id for job in state.queued_jobs}, where  # type: ignore[attr-defined]

    state = DistributedEngine.initialize("D")
    state = DistributedEngine.register_worker(
        state, WorkerNode("W-0", "host", WorkerStatus.IDLE, 2), 0.0
    )

    for i in range(6):
        state = DistributedEngine.submit_job(
            state,
            Job(f"J-{i}", JobType.BACKTEST, JobStatus.PENDING, priority=i, created_timestamp=0.0),
            float(i),
        )
        _check(state, f"after submitting J-{i}")

    state = DistributedEngine.cancel_job(state, "J-3", 10.0)
    _check(state, "after cancelling")

    state = DistributedEngine.assign_jobs(state, 11.0)
    _check(state, "after assignment")


def test_the_feature_store_id_index_agrees_after_every_registration() -> None:
    from alphalab.feature_store import (
        FeatureMetadata,
        FeatureRegistry,
        FeatureStoreEngine,
        FeatureType,
        FeatureValueType,
    )

    state = FeatureStoreEngine.initialize("FS")
    for version in (1, 2):
        for i in range(4):
            state = FeatureRegistry.register(
                state,
                FeatureMetadata(
                    feature_id=f"f{i}",
                    name=f"F{i}v{version}",
                    version=version,
                    feature_type=FeatureType.DERIVED,
                    value_type=FeatureValueType.FLOAT,
                    owner="test",
                    description="synthetic",
                ),
                0.0,
            )
            assert set(state.registered_feature_ids) == {
                metadata.feature_id for metadata in state.features.values()
            }


# --------------------------------------------------------------------------- #
# 3. Semantics the conversion had to preserve
# --------------------------------------------------------------------------- #


def test_the_priority_queue_still_orders_by_priority_then_arrival() -> None:
    """The fast path appends; it must agree with what a full sort would produce."""

    from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType
    from alphalab.distributed.queue import queue_key

    submitted = [
        ("J-low-early", 1, 0.0),
        ("J-high-late", 9, 5.0),
        ("J-low-late", 1, 7.0),
        ("J-high-early", 9, 1.0),
        ("J-mid", 5, 3.0),
    ]

    state = DistributedEngine.initialize("D")
    for job_id, priority, created in submitted:
        state = DistributedEngine.submit_job(
            state,
            Job(job_id, JobType.BACKTEST, JobStatus.PENDING, priority, created_timestamp=created),
            created,
        )

    expected = sorted(
        (
            Job(job_id, JobType.BACKTEST, JobStatus.PENDING, priority, created_timestamp=created)
            for job_id, priority, created in submitted
        ),
        key=queue_key,
    )

    assert [job.job_id for job in state.queued_jobs] == [job.job_id for job in expected]


def test_a_duplicate_job_id_is_still_refused_from_every_container() -> None:
    """The union set was replaced by four lookups; all four must still refuse."""

    from alphalab.distributed import DistributedEngine, Job, JobStatus, JobType, WorkerNode
    from alphalab.distributed.exceptions import DistributedValidationError
    from alphalab.distributed.node import WorkerStatus

    def _job(job_id: str) -> Job:
        return Job(job_id, JobType.BACKTEST, JobStatus.PENDING, 1, created_timestamp=0.0)

    state = DistributedEngine.initialize("D")
    state = DistributedEngine.register_worker(
        state, WorkerNode("W", "host", WorkerStatus.IDLE, 8), 0.0
    )

    state = DistributedEngine.submit_job(state, _job("queued"), 0.0)
    with pytest.raises(DistributedValidationError, match="Duplicate"):
        DistributedEngine.submit_job(state, _job("queued"), 1.0)

    state = DistributedEngine.submit_job(state, _job("running"), 1.0)
    state = DistributedEngine.assign_jobs(state, 2.0)
    assert "running" in state.running_jobs
    with pytest.raises(DistributedValidationError, match="Duplicate"):
        DistributedEngine.submit_job(state, _job("running"), 3.0)

    state = DistributedEngine.start_job(state, "running", 3.5)
    state = DistributedEngine.complete_job(state, "running", 4.0)
    assert "running" in state.completed_jobs
    with pytest.raises(DistributedValidationError, match="Duplicate"):
        DistributedEngine.submit_job(state, _job("running"), 5.0)

    state = DistributedEngine.submit_job(state, _job("cancelled"), 6.0)
    state = DistributedEngine.cancel_job(state, "cancelled", 7.0)
    # Recorded among the cancellations since v3.12 (ledger SCF-003), not the failures.
    assert "cancelled" in state.cancelled_jobs
    with pytest.raises(DistributedValidationError, match="Duplicate"):
        DistributedEngine.submit_job(state, _job("cancelled"), 8.0)


def test_a_feature_is_refused_until_what_it_depends_on_is_registered() -> None:
    from alphalab.feature_store import (
        FeatureMetadata,
        FeatureRegistry,
        FeatureStoreEngine,
        FeatureType,
        FeatureValueType,
    )
    from alphalab.feature_store.exceptions import FeatureValidationError

    state = FeatureStoreEngine.initialize("FS")
    depending = FeatureMetadata(
        feature_id="derived",
        name="Derived",
        version=1,
        feature_type=FeatureType.DERIVED,
        value_type=FeatureValueType.FLOAT,
        owner="test",
        description="synthetic",
        depends_on=("missing",),
    )

    with pytest.raises(FeatureValidationError):
        FeatureRegistry.register(state, depending, 0.0)

    state = FeatureRegistry.register(
        state,
        FeatureMetadata(
            feature_id="missing",
            name="Upstream",
            version=1,
            feature_type=FeatureType.PRICE,
            value_type=FeatureValueType.FLOAT,
            owner="test",
            description="synthetic",
        ),
        0.0,
    )
    state = FeatureRegistry.register(state, depending, 1.0)

    assert "derived:1" in state.features


def test_the_histories_are_retained_in_full_and_in_order() -> None:
    """A faster container that drops history is not the same container."""

    state = _scheduler(50)
    assert len(state.events) == 50  # type: ignore[attr-defined]
    assert [event.timer_id for event in state.events] == [f"T-{i}" for i in range(50)]  # type: ignore[attr-defined]

    reports = _reporting(30)
    assert len(reports.events) == 30
    assert len(reports.reports) == 30
    assert list(reports.reports) == [f"R-{i}" for i in range(30)], "insertion order preserved"


def test_the_states_are_still_values() -> None:
    """Equality, not identity: two runs of the same input must compare equal.

    Built inside ``id_scope`` because every one of these engines stamps its
    events with ``new_id()``; the seed is what makes two independently built
    states comparable, exactly as ``RunConfig.seed`` does for a run.
    """

    from alphalab.common.ids import id_scope

    seed = 20260914
    # _plugins is deliberately absent: PluginState holds plugin
    # *instances*, which are arbitrary caller objects with no value equality, so
    # two independently built plugin states never compare equal and never did.
    for build in (_scheduler, _reporting, _portfolio_optimizer, _data):
        with id_scope(seed):
            first = build(20)
        with id_scope(seed):
            second = build(20)
        assert first == second, build.__name__


def test_the_converted_containers_still_serialize_as_the_shapes_they_replaced() -> None:
    """A log serializes as a list and a map as an object, exactly as before."""

    import json

    from alphalab.persistence import serialize

    decoded = json.loads(serialize(_scheduler(5)))

    assert isinstance(decoded["events"], list) and len(decoded["events"]) == 5
    assert isinstance(decoded["timers"], dict) and len(decoded["timers"]) == 5
    assert "AppendOnlyLog(" not in json.dumps(decoded)
    assert "PersistentMap(" not in json.dumps(decoded)


# --------------------------------------------------------------------------- #
# 4. Scaling
# --------------------------------------------------------------------------- #

#: Workload sizes. Linear predicts ~4x over the 4x input and quadratic ~16x.
#: The ceiling sits between them -- the square of the 3.0x-per-doubling ceiling
#: the sweep this replaced used -- far enough from 4 to survive a loaded machine
#: and far enough from 16 to fail a reintroduced quadratic. The development
#: machine measured 2.04x-2.11x per doubling for every package here, against
#: 3.4x-4.9x before the conversion.
SMALL, LARGE = 1_000, 4_000
MAX_GROWTH = 9.0


def _measure(build: Callable[[int], object], size: int) -> float:
    start = CLOCK()
    build(size)
    return CLOCK() - start


@pytest.mark.parametrize("package", sorted(BUILDERS))
def test_accumulation_is_no_longer_quadratic(package: str) -> None:
    """Read with the one stabilized method every guard shares (tests/regression/_timing.py).

    Until v3.10 this was a doubling sweep on the wall clock with the collector
    running, and it failed on a timing blip under load (ledger TST-001).
    """

    build = BUILDERS[package]
    small, large = timings(lambda size: _measure(build, size), SMALL, LARGE)
    growth = large / max(small, 1e-6)

    assert growth <= MAX_GROWTH, (
        f"{package} grows {growth:.2f}x over a {LARGE // SMALL}x input "
        f"({small:.4f}s -> {large:.4f}s); quadratic accumulation is back"
    )


def test_the_one_term_left_super_linear_went_with_the_optimizer() -> None:
    """Recorded rather than hidden, until v3.12 (ledger OFE-013).

    ``OptimizerState.pending_trials`` dropped its head with ``pending[1:]``,
    which copies, so the queue was the one term these packages kept
    super-linear: making it O(1) needed a start offset on ``AppendOnlyLog``,
    measured at +3.9% on the execution pipeline and refused (ADR-0034). v3.12
    removed the package instead -- a second parameter search beside
    ``research.parameter_sweep`` (SCF-003) -- and the term with it.
    """

    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("alphalab.optimizer.state")
