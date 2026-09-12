"""v2.10: a declaring strategy's memory survives a restore, and continuation holds.

This file was `test_strategy_internal_state_is_not_captured.py`, the v2.10
milestone-1 characterization of ADR-0023's never-implemented testing invariant 9.
It asserted that a stateful strategy **diverges** after a fresh-instance restore,
because v2.9 captured a strategy's type and not its memory.

**It has been inverted, because production now restores declared strategy
memory.** The lesson it recorded is not deleted: it is kept, exactly, in "The
historical lesson" below, where a strategy that declines to declare state still
diverges and a test still names that expectation. That is ADR-0025 testing
invariant 12, and it is the documented behaviour of a non-declaring strategy
rather than a defect.

What changed
------------
`StrategyStateProtocol` (ADR-0025 decisions 1-3): a strategy declares durable
state by defining a version, an encode and a decode. Both codec directions are
required, because the shared encoder writes a `Decimal` and a `str` identically
and no generic decoder can tell them apart afterwards -- which is the whole
reason the contract is two-sided rather than "return something encodable".

The runtime asks at exactly two points, both between completed pipeline steps:
`alphalab.runtime.snapshot.capture` and `.restore`. Neither is a hook, neither
runs per event, and neither adds a strategy dispatch.

`PIPELINE_SNAPSHOT_SCHEMA` moved 1 -> 2 for the one new field.
`RUN_SNAPSHOT_SCHEMA` and `RUN_SNAPSHOT_SCHEMA` did not move, which is
ADR-0023 decision 1's envelope split working as designed, and is asserted here.
"""

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting.engine import BacktestEngine
from alphalab.common.ids import IdStreamPosition, current_id_position, id_scope
from alphalab.execution.policy import ImmediateFill
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import SerializationError, StateDecodeError
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState
from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA, RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import (
    NOT_ASKED,
    PIPELINE_SNAPSHOT_SCHEMA,
    READABLE_PIPELINE_SCHEMAS,
    RuntimeObjects,
    StrategyStateRecord,
)
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy, StrategyStateProtocol
from tests.integration.harness import (
    backtest_config,
    context_factory,
    dataset_of_quotes,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

# The Class-1 projection the v2.9 certification asserts on. Imported rather than
# restated: a second definition of "what must be identical" is how two readers
# come to disagree about the same guarantee.
from tests.regression.test_durable_run_continuation import _class_one

SEED = 20250907
STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())

#: The strategy acts on every ``_EVERY``-th quote it has seen.
_EVERY = 3

#: Nine records. Split points are exercised at every boundary; ``BOUNDARY`` is
#: the one the single-split tests use, chosen so a restarted counter is visibly
#: out of phase rather than accidentally realigned.
TOTAL, BOUNDARY = 9, 4

#: The strategy's own state schema. AlphaLab carries this integer and never
#: interprets it (ADR-0025 decision 7).
_STATE_VERSION = 1


# ---------------------------------------------------------------------------
# A strategy with non-trivial declared state
# ---------------------------------------------------------------------------


class CountingStrategy(BaseStrategy):
    """Acts on every ``_EVERY``-th quote *it* has seen, and declares its memory.

    Three shapes of state, chosen because they are the three a generic decoder
    provably cannot recover: an ``int`` (which JSON does carry), a sequence of
    ``Decimal`` (which JSON flattens to a list of strings), and a nested mapping
    of ``str -> Decimal``. The codec is what puts the types back.

    ``_floor`` is compared against the newest window entry on every quote. It is
    never actually breached by this workload, so it changes no behaviour -- it
    is there so that a restore returning ``str`` where ``Decimal`` was captured
    raises ``TypeError`` inside the hook instead of passing quietly.
    """

    def __init__(self, strategy_id: str, asset_id: str, every: int = _EVERY) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._every = every
        self._floor = Decimal("0")
        self._seen = 0
        self._window: tuple[Decimal, ...] = ()
        self._by_asset: dict[str, Decimal] = {}

    # -- StrategyProtocol ---------------------------------------------------

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        mid = event.quote.bid
        self._seen += 1
        self._window = (*self._window, mid)[-4:]
        self._by_asset[self._asset_id] = self._by_asset.get(self._asset_id, Decimal("0")) + mid

        # Decimal-vs-Decimal. Raises TypeError if a restore lost the type.
        if self._window[-1] < self._floor:  # pragma: no cover - never breached
            return ()
        if self._seen % self._every != 0:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=Decimal("5"),
                timestamp=event.quote.timestamp,
            ),
        )

    # -- StrategyStateProtocol ----------------------------------------------

    def strategy_state_version(self) -> int:
        return _STATE_VERSION

    def capture_state(self) -> Any:
        return {
            "seen": self._seen,
            "window": self._window,
            "by_asset": dict(self._by_asset),
        }

    def restore_state(self, payload: Any, version: int) -> None:
        if version != _STATE_VERSION:
            raise ValueError(f"{type(self).__name__} reads state version {_STATE_VERSION}")
        self._seen = int(payload["seen"])
        self._window = tuple(Decimal(str(item)) for item in payload["window"])
        self._by_asset = {key: Decimal(str(value)) for key, value in payload["by_asset"].items()}


class UndeclaredCountingStrategy(BaseStrategy):
    """`CountingStrategy`'s behaviour with no codec: the historical lesson.

    Identical decisions, and deliberately no `StrategyStateProtocol` members. A
    strategy like this keeps exactly the v2.9 conditional guarantee, and this is
    the class the inverted characterization below still runs.
    """

    def __init__(self, strategy_id: str, asset_id: str, every: int = _EVERY) -> None:
        self._strategy_id = strategy_id
        self._asset_id = asset_id
        self._every = every
        self._seen = 0

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        self._seen += 1
        if self._seen % self._every != 0:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=self._asset_id,
                target=Decimal("5"),
                timestamp=event.quote.timestamp,
            ),
        )


class EncodeOnlyStrategy(UndeclaredCountingStrategy):
    """Defines an encode and a version, and no decode. Refused at capture."""

    def strategy_state_version(self) -> int:
        return _STATE_VERSION

    def capture_state(self) -> Any:
        return {"seen": self._seen}


class DecodeOnlyStrategy(UndeclaredCountingStrategy):
    """Defines a decode and a version, and no encode. Refused at capture."""

    def strategy_state_version(self) -> int:
        return _STATE_VERSION

    def restore_state(self, payload: Any, version: int) -> None:
        self._seen = int(payload["seen"])


class Band:
    """A strategy-owned type JSON cannot carry, with the projection that exists.

    A plain object is refused by the encoder; declaring ``__serializable__`` is
    how every other such type in the repository crosses the boundary, and
    strategy state uses that same extension point rather than a new one.
    """

    __slots__ = ("high", "low")

    def __init__(self, low: Decimal, high: Decimal) -> None:
        self.low = low
        self.high = high

    def __serializable__(self) -> Any:
        return {"low": self.low, "high": self.high}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Band) and (self.low, self.high) == (other.low, other.high)


class BandStrategy(CountingStrategy):
    """Declares state containing a custom type, a Decimal and a nested mapping."""

    def __init__(self, strategy_id: str, asset_id: str, every: int = _EVERY) -> None:
        super().__init__(strategy_id, asset_id, every)
        self._band = Band(Decimal("99.5"), Decimal("108.25"))

    def capture_state(self) -> Any:
        return {**super().capture_state(), "band": self._band}

    def restore_state(self, payload: Any, version: int) -> None:
        super().restore_state(payload, version)
        self._band = Band(Decimal(payload["band"]["low"]), Decimal(payload["band"]["high"]))


class RaisingCaptureStrategy(CountingStrategy):
    def capture_state(self) -> Any:
        raise RuntimeError("capture blew up")


class RaisingRestoreStrategy(CountingStrategy):
    def restore_state(self, payload: Any, version: int) -> None:
        raise RuntimeError("restore blew up")


class UnencodableStateStrategy(CountingStrategy):
    def capture_state(self) -> Any:
        return {"window": {Decimal("1")}}  # a set: the encoder has no branch


class BadVersionStrategy(CountingStrategy):
    def strategy_state_version(self) -> Any:
        return "1"


# ---------------------------------------------------------------------------
# One deterministic workload
# ---------------------------------------------------------------------------


def _records(count: int = TOTAL) -> list[MarketRecord]:
    return [
        MarketRecord(
            event_id=f"REC-{index}",
            timestamp=2.0 + index,
            payload=sized_quote(ASSET_ID, 2.0 + index, Decimal(100 + index), Decimal("100")),
        )
        for index in range(count)
    ]


def _config() -> RunConfig:
    return RunConfig(
        pipeline=pipeline_config(STRATEGY_ID),
        mode=ExecutionMode.BACKTEST,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
    )


def _objects(config: RunConfig, strategy: object) -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},  # type: ignore[dict-item]
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _drive(state: RunState, records: list[MarketRecord]) -> RunState:
    for record in records:
        state, _ = TradingSession.advance(state, record, context_factory)
    return state


def _start(config: RunConfig, strategy: object) -> RunState:
    return TradingSession.initialize(
        config,
        running_strategy_state(STRATEGY_ID, strategy),  # type: ignore[arg-type]
    )


def _uninterrupted(count: int = TOTAL, factory: Any = CountingStrategy) -> RunState:
    config = _config()
    with id_scope(SEED):
        return _drive(_start(config, factory(STRATEGY_ID, ASSET_ID)), _records(count))


def _split(
    *,
    boundary: int = BOUNDARY,
    fresh: bool = True,
    factory: Any = CountingStrategy,
    seed: int | None = SEED,
) -> RunState:
    """Run to ``boundary``, round-trip through the snapshot path, resume."""

    config = replace(_config(), seed=seed)
    records = _records()
    warm = factory(STRATEGY_ID, ASSET_ID)

    with id_scope(seed):
        partial = _drive(_start(config, warm), records[:boundary])

    payload = serialize(capture_run(partial))
    supplied = factory(STRATEGY_ID, ASSET_ID) if fresh else warm
    restored = restore_run(run_from_primitives(deserialize(payload)), _objects(config, supplied))

    with TradingSession.resume(restored):
        return _drive(restored, records[boundary:])


def _acted_on(state: RunState) -> tuple[float, ...]:
    """Market timestamps at which the strategy's intents became orders.

    Read from the OMS order book -- what the run *did* -- never from the
    strategy's attributes.
    """

    return tuple(order.created_at for order in state.pipeline.oms.orders.orders())


def _declared_state(state: RunState) -> Mapping[str, Any]:
    """Each declaring strategy's state, asked for the same way capture asks."""

    return {
        strategy_id: entry.instance.capture_state()
        for strategy_id, entry in state.pipeline.strategy.strategies.items()
        if isinstance(entry.instance, StrategyStateProtocol)
    }


def _pipeline_payload(state: RunState) -> dict[str, Any]:
    return dict(deserialize(serialize(capture_pipeline(state.pipeline))))


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


def test_the_declaring_strategy_satisfies_the_protocol_and_the_undeclared_one_does_not() -> None:
    assert isinstance(CountingStrategy(STRATEGY_ID, ASSET_ID), StrategyStateProtocol)
    assert not isinstance(UndeclaredCountingStrategy(STRATEGY_ID, ASSET_ID), StrategyStateProtocol)


def test_declaring_is_structural_and_needs_no_registration() -> None:
    """Nothing registers a strategy, and nothing introspects its attributes."""

    class Ad_hoc:
        def strategy_state_version(self) -> int:
            return 1

        def capture_state(self) -> Any:
            return {}

        def restore_state(self, payload: Any, version: int) -> None: ...

    assert isinstance(Ad_hoc(), StrategyStateProtocol)


def test_base_strategy_does_not_make_every_strategy_declare_state() -> None:
    """A no-op default would make every existing strategy claim state it lacks."""

    assert not isinstance(BaseStrategy(), StrategyStateProtocol)


# ---------------------------------------------------------------------------
# 1 / 2 / 3 -- the contract this milestone exists for
# ---------------------------------------------------------------------------


def test_a_fresh_instance_resumes_and_agrees_with_the_control() -> None:
    """THE inversion. v2.9 diverged here; v2.10 restores the memory.

    The strategy handed to `restore` is a brand-new object whose counter, window
    and mapping are empty. Its state is rebuilt from the payload before a single
    further record is processed.
    """

    resumed = _split(fresh=True)
    control = _uninterrupted()

    assert _acted_on(resumed) == _acted_on(control) == (4.0, 7.0, 10.0)
    assert _class_one(resumed) == _class_one(control)
    assert _declared_state(resumed) == _declared_state(control)


def test_the_warm_and_fresh_resumes_are_now_indistinguishable() -> None:
    """The distinction milestone 1 was built to expose no longer exists."""

    assert _acted_on(_split(fresh=True)) == _acted_on(_split(fresh=False))
    assert _class_one(_split(fresh=True)) == _class_one(_split(fresh=False))


@pytest.mark.parametrize("boundary", list(range(1, TOTAL)))
def test_every_split_boundary_reproduces_the_control(boundary: int) -> None:
    """Split at every record boundary, restore into a fresh instance, continue."""

    resumed = _split(boundary=boundary, fresh=True)
    control = _uninterrupted()

    assert _class_one(resumed) == _class_one(control), f"boundary {boundary} diverged"
    assert _declared_state(resumed) == _declared_state(control), f"boundary {boundary} lost state"
    assert _acted_on(resumed) == _acted_on(control), f"boundary {boundary} behaved differently"


def test_the_restored_state_carries_the_types_it_was_captured_with() -> None:
    """A `Decimal` comes back a `Decimal`, not the `str` JSON wrote."""

    state = _declared_state(_split(fresh=True))[STRATEGY_ID]

    assert isinstance(state["seen"], int)
    assert state["window"] and all(isinstance(item, Decimal) for item in state["window"])
    assert state["by_asset"] and all(isinstance(v, Decimal) for v in state["by_asset"].values())


def test_the_state_under_test_is_substantial_enough_to_mean_something() -> None:
    """Guards every assertion above from becoming a statement about emptiness."""

    state = _declared_state(_uninterrupted())[STRATEGY_ID]

    entry = _uninterrupted().pipeline.strategy.strategies[STRATEGY_ID]
    assert entry.status.name == "RUNNING", f"the strategy failed: {entry.last_error}"
    assert entry.last_error is None

    assert state["seen"] == TOTAL
    assert len(state["window"]) == 4
    assert len(state["by_asset"]) == 1
    assert state["by_asset"][ASSET_ID] > Decimal("900")


# ---------------------------------------------------------------------------
# 4 -- round trips, at all three envelopes
# ---------------------------------------------------------------------------


def test_the_pipeline_round_trips_in_memory_and_across_json() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records()).pipeline

    objects = _objects(config, strategy).pipeline
    snapshot = capture_pipeline(state)

    assert restore_pipeline(snapshot, objects) == state
    assert restore_pipeline(
        pipeline_from_primitives(deserialize(serialize(snapshot))), objects
    ) == (state)


def test_the_session_round_trips_and_the_session_schema_did_not_move() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records())

    payload = dict(deserialize(serialize(capture_run(state))))

    assert payload["schema_version"] == RUN_SNAPSHOT_SCHEMA == 1
    assert payload["pipeline"]["schema_version"] == PIPELINE_SNAPSHOT_SCHEMA == 2
    assert restore_run(run_from_primitives(payload), _objects(config, strategy)) == state


def test_the_backtest_round_trips_and_the_backtest_schema_did_not_move() -> None:
    dataset = dataset_of_quotes(ASSET_ID, [Decimal(100 + index) for index in range(TOTAL)])
    config = backtest_config(STRATEGY_ID, seed=SEED)
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)

    with id_scope(SEED):
        state = BacktestEngine.initialize(config, running_strategy_state(STRATEGY_ID, strategy))
        for record in dataset.records:
            state, _ = BacktestEngine.advance(state, record, context_factory)

    payload = dict(deserialize(serialize(capture_run(state))))
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )

    assert payload["schema_version"] == RUN_SNAPSHOT_SCHEMA == 1
    assert payload["pipeline"]["schema_version"] == 2
    assert restore_run(run_from_primitives(payload), objects) == state


def test_a_fresh_instance_resumes_a_backtest_too() -> None:
    dataset = dataset_of_quotes(ASSET_ID, [Decimal(100 + index) for index in range(TOTAL)])
    config = backtest_config(STRATEGY_ID, seed=SEED)

    def objects_for(strategy: object) -> RunObjects:
        return RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=config.pipeline.sizing_model,
                simulator=config.pipeline.simulator,
                strategies={STRATEGY_ID: strategy},  # type: ignore[dict-item]
                instruments=config.pipeline.instruments,
            ),
            fill_policy=config.fill_policy,
        )

    def run(records: Any, state: RunState) -> RunState:
        for record in records:
            state, _ = BacktestEngine.advance(state, record, context_factory)
        return state

    with id_scope(SEED):
        control = run(
            dataset.records,
            BacktestEngine.initialize(
                config, running_strategy_state(STRATEGY_ID, CountingStrategy(STRATEGY_ID, ASSET_ID))
            ),
        )

    with id_scope(SEED):
        partial = run(
            dataset.records[:BOUNDARY],
            BacktestEngine.initialize(
                config, running_strategy_state(STRATEGY_ID, CountingStrategy(STRATEGY_ID, ASSET_ID))
            ),
        )

    payload = serialize(capture_run(partial))
    restored = restore_run(
        run_from_primitives(deserialize(payload)),
        objects_for(CountingStrategy(STRATEGY_ID, ASSET_ID)),
    )
    with BacktestEngine.resume(restored):
        resumed = run(dataset.records[BOUNDARY:], restored)

    assert [o.created_at for o in resumed.pipeline.oms.orders.orders()] == [
        o.created_at for o in control.pipeline.oms.orders.orders()
    ]
    assert resumed.pipeline.portfolio.positions == control.pipeline.portfolio.positions
    assert resumed.pipeline.id_position == control.pipeline.id_position


# ---------------------------------------------------------------------------
# The persisted representation
# ---------------------------------------------------------------------------


def test_the_payload_carries_the_declared_state_and_its_version() -> None:
    record = _pipeline_payload(_uninterrupted())["strategy"][0]

    assert record["state"]["version"] == _STATE_VERSION
    assert record["state"]["payload"]["seen"] == TOTAL
    # A Decimal is written as a string, deterministically, by the shared encoder.
    assert record["state"]["payload"]["window"] == ["105", "106", "107", "108"]


def test_a_non_declaring_strategy_records_null_and_not_an_empty_object() -> None:
    """`{}` is a declared state; ``null`` is a declaration of none."""

    record = _pipeline_payload(_uninterrupted(factory=UndeclaredCountingStrategy))["strategy"][0]

    assert "state" in record, "version 2 always asks, and always records the answer"
    assert record["state"] is None


def test_declared_none_and_declared_empty_are_different_records() -> None:
    assert StrategyStateRecord(payload={}, version=1) is not None
    assert StrategyStateRecord(payload={}, version=1) != StrategyStateRecord(
        payload=None, version=1
    )


def test_no_strategy_object_appears_in_the_payload() -> None:
    payload = _pipeline_payload(_uninterrupted())
    text = serialize(payload)

    assert payload["strategy"][0]["instance_type"] == "CountingStrategy"
    assert "instance" not in payload["strategy"][0]
    assert "CountingStrategy object at" not in text


def test_serialization_is_deterministic_across_a_re_capture() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records())

    assert serialize(capture_run(state)) == serialize(capture_run(state))


# ---------------------------------------------------------------------------
# 5 -- the four refusals, and the schema ones
# ---------------------------------------------------------------------------


def _schema_one_payload(instance_type: str = "UndeclaredCountingStrategy") -> dict[str, Any]:
    """A payload shaped exactly as v2.9 wrote one: version 1, no state field."""

    payload = _pipeline_payload(_uninterrupted(factory=UndeclaredCountingStrategy))
    payload["schema_version"] = 1
    for record in payload["strategy"]:
        del record["state"]
        record["instance_type"] = instance_type
    return payload


def test_a_declaring_strategy_against_a_schema_one_payload_is_refused() -> None:
    objects = _objects(_config(), CountingStrategy(STRATEGY_ID, ASSET_ID)).pipeline

    with pytest.raises(StateDecodeError, match=re.escape(STRATEGY_ID)) as excinfo:
        restore_pipeline(pipeline_from_primitives(_schema_one_payload("CountingStrategy")), objects)

    assert "schema version 1" in str(excinfo.value)


def test_a_declaring_strategy_against_an_explicit_null_is_refused() -> None:
    payload = _pipeline_payload(_uninterrupted(factory=UndeclaredCountingStrategy))
    objects = _objects(_config(), CountingStrategy(STRATEGY_ID, ASSET_ID)).pipeline
    # Same recorded type, so _require_object passes and reconciliation is what
    # is under test rather than the type check in front of it.
    payload["strategy"][0]["instance_type"] = "CountingStrategy"

    assert payload["strategy"][0]["state"] is None
    with pytest.raises(StateDecodeError, match="declared none"):
        restore_pipeline(pipeline_from_primitives(payload), objects)


def test_a_non_declaring_strategy_against_a_payload_carrying_state_is_refused() -> None:
    payload = _pipeline_payload(_uninterrupted())
    objects = _objects(_config(), UndeclaredCountingStrategy(STRATEGY_ID, ASSET_ID)).pipeline
    payload["strategy"][0]["instance_type"] = "UndeclaredCountingStrategy"

    with pytest.raises(StateDecodeError, match="does not declare durable state"):
        restore_pipeline(pipeline_from_primitives(payload), objects)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda r: r.__setitem__("state", {"version": 1}), "missing 'payload'"),
        (lambda r: r.__setitem__("state", {"payload": {}}), "missing 'version'"),
        (lambda r: r.__setitem__("state", {"payload": {}, "version": "1"}), "not an integer"),
        (lambda r: r.__setitem__("state", []), "state"),
    ],
)
def test_a_malformed_strategy_state_is_refused_naming_the_field(mutate: Any, match: str) -> None:
    payload = _pipeline_payload(_uninterrupted())
    mutate(payload["strategy"][0])

    with pytest.raises(StateDecodeError, match=match):
        pipeline_from_primitives(payload)


def test_a_missing_state_field_at_schema_two_is_refused() -> None:
    payload = _pipeline_payload(_uninterrupted())
    del payload["strategy"][0]["state"]

    with pytest.raises(StateDecodeError, match="missing 'state'"):
        pipeline_from_primitives(payload)


def test_a_schema_one_payload_carrying_state_is_refused() -> None:
    """A payload that says it predates the field and then carries it is malformed."""

    payload = _pipeline_payload(_uninterrupted())
    payload["schema_version"] = 1

    with pytest.raises(StateDecodeError, match="carries 'state'"):
        pipeline_from_primitives(payload)


def test_a_strategy_decode_failure_refuses_the_whole_restore_and_chains() -> None:
    payload = _pipeline_payload(_uninterrupted())
    objects = _objects(_config(), RaisingRestoreStrategy(STRATEGY_ID, ASSET_ID)).pipeline

    # Same class name, so _require_object is satisfied and the failure is the
    # strategy's own -- which is the case under test.
    payload["strategy"][0]["instance_type"] = "RaisingRestoreStrategy"

    with pytest.raises(StateDecodeError) as excinfo:
        restore_pipeline(pipeline_from_primitives(payload), objects)

    assert STRATEGY_ID in str(excinfo.value)
    assert "nothing partial is returned" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert str(excinfo.value.__cause__) == "restore blew up"


def test_one_bad_strategy_refuses_the_whole_restore_and_not_just_itself() -> None:
    """Nine good strategies and one bad one restore nothing."""

    payload = _pipeline_payload(_uninterrupted())
    good = dict(payload["strategy"][0])
    others = []
    strategies: dict[str, Any] = {}
    for index in range(3):
        clone = dict(good)
        clone["strategy_id"] = f"S{index}"
        others.append(clone)
        strategies[f"S{index}"] = CountingStrategy(f"S{index}", ASSET_ID)
    others[2]["state"] = {"payload": {"seen": 1, "window": [], "by_asset": {}}, "version": 99}
    payload["strategy"] = others

    objects = replace(_objects(_config(), None).pipeline, strategies=strategies)

    with pytest.raises(StateDecodeError, match="S2"):
        restore_pipeline(pipeline_from_primitives(payload), objects)


@pytest.mark.parametrize("version", [3, 99, 0, -1])
def test_an_unreadable_pipeline_version_is_refused(version: int) -> None:
    payload = _pipeline_payload(_uninterrupted())
    payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match=f"declares schema version {version}"):
        pipeline_from_primitives(payload)


def test_a_missing_pipeline_version_is_still_refused_with_no_legacy_path() -> None:
    payload = _pipeline_payload(_uninterrupted())
    del payload["schema_version"]

    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        pipeline_from_primitives(payload)


def test_the_readable_versions_are_exactly_one_and_two() -> None:
    assert READABLE_PIPELINE_SCHEMAS == (1, 2)
    assert PIPELINE_SNAPSHOT_SCHEMA == 2


def test_a_schema_one_payload_restores_a_non_declaring_strategy() -> None:
    """v2.9 payloads stay readable. That is the OMS precedent, not the portfolio's."""

    objects = _objects(_config(), UndeclaredCountingStrategy(STRATEGY_ID, ASSET_ID)).pipeline
    restored = restore_pipeline(pipeline_from_primitives(_schema_one_payload()), objects)

    assert restored.strategy.strategies[STRATEGY_ID].status.name == "RUNNING"


def test_a_schema_one_payload_records_that_nobody_was_asked() -> None:
    snapshot = pipeline_from_primitives(_schema_one_payload())

    assert snapshot.strategy[0].state is NOT_ASKED


# ---------------------------------------------------------------------------
# 6 -- the codec contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "missing"),
    [(EncodeOnlyStrategy, "restore_state"), (DecodeOnlyStrategy, "capture_state")],
)
def test_a_half_declared_codec_is_refused_at_capture(factory: Any, missing: str) -> None:
    """Not silently treated as declaring nothing, which is what isinstance says."""

    config = _config()
    strategy = factory(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(2))

    with pytest.raises(SerializationError, match=missing) as excinfo:
        capture_pipeline(state.pipeline)

    assert STRATEGY_ID in str(excinfo.value)


def test_a_capture_failure_names_the_strategy_and_chains() -> None:
    config = _config()
    strategy = RaisingCaptureStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(2))

    with pytest.raises(SerializationError, match=re.escape(STRATEGY_ID)) as excinfo:
        capture_pipeline(state.pipeline)

    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_a_non_integer_state_version_is_refused() -> None:
    config = _config()
    strategy = BadVersionStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(2))

    with pytest.raises(SerializationError, match="not an integer"):
        capture_pipeline(state.pipeline)


def test_state_the_shared_encoder_refuses_is_refused_at_capture() -> None:
    """The encodability boundary is `capture`, not some later `serialize`.

    An unencodable state must never reach a `PipelineSnapshot` that looks valid
    in memory and blows up at an unrelated call. `capture` puts the state
    through the *existing* encoder, so the refusal happens where the bad value
    was produced and names the strategy that produced it.
    """

    config = _config()
    strategy = UnencodableStateStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(2))

    with pytest.raises(SerializationError) as excinfo:
        capture_pipeline(state.pipeline)  # no serialize() anywhere near this

    assert STRATEGY_ID in str(excinfo.value)
    assert "encoder cannot write" in str(excinfo.value)
    # The refusal comes from the shared encoder, not a rule reimplemented here.
    assert "No deterministic JSON representation" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, SerializationError)


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ({Decimal("1")}, "set"),
        (frozenset({1}), "frozenset"),
        (b"bytes", "bytes"),
        (complex(1, 2), "complex"),
        (object(), "object"),
        ({"nested": {"deeper": [{1, 2}]}}, "set nested three levels down"),
    ],
)
def test_no_unencodable_state_can_reach_an_in_memory_snapshot(value: Any, label: str) -> None:
    """Every shape the encoder refuses is refused at capture, however buried."""

    class Returns(CountingStrategy):
        def capture_state(self) -> Any:
            return {"bad": value}

    config = _config()
    with id_scope(SEED):
        state = _drive(_start(config, Returns(STRATEGY_ID, ASSET_ID)), _records(2))

    with pytest.raises(SerializationError, match="encoder cannot write"):
        capture_pipeline(state.pipeline), label


def test_the_record_carries_json_decoded_primitives_not_the_captured_objects() -> None:
    """What `restore_state` is promised: the JSON-decoded shape, on both paths.

    Without this the in-memory path would hand a strategy back its ``Decimal``
    and ``tuple`` while the JSON path handed it ``str`` and ``list``, and a
    codec written against one shape would break on the other.
    """

    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(4))

    captured = strategy.capture_state()
    record = capture_pipeline(state.pipeline).strategy[0].state
    assert isinstance(record, StrategyStateRecord)
    recorded = record.payload

    assert isinstance(captured["window"], tuple)
    assert all(isinstance(item, Decimal) for item in captured["window"])
    assert isinstance(recorded["window"], list)
    assert all(isinstance(item, str) for item in recorded["window"])


def test_a_custom_strategy_type_round_trips_through_its_serializable_projection() -> None:
    """`__serializable__` is preserved as the extension point, not replaced."""

    config = _config()
    with id_scope(SEED):
        control = _drive(_start(config, BandStrategy(STRATEGY_ID, ASSET_ID)), _records())

    with id_scope(SEED):
        partial = _drive(_start(config, BandStrategy(STRATEGY_ID, ASSET_ID)), _records()[:BOUNDARY])

    fresh = BandStrategy(STRATEGY_ID, ASSET_ID)
    fresh._band = Band(Decimal("0"), Decimal("0"))  # provably not the captured one
    payload = serialize(capture_run(partial))
    restored = restore_run(run_from_primitives(deserialize(payload)), _objects(config, fresh))
    with TradingSession.resume(restored):
        resumed = _drive(restored, _records()[BOUNDARY:])

    instance = control.pipeline.strategy.strategies[STRATEGY_ID].instance
    assert isinstance(instance, StrategyStateProtocol)
    captured = instance.capture_state()
    assert captured["band"] == Band(Decimal("99.5"), Decimal("108.25"))
    assert fresh.capture_state()["band"] == captured["band"], "the custom type was restored"
    assert isinstance(captured["band"], Band)
    assert _acted_on(resumed) == _acted_on(control)
    assert _class_one(resumed) == _class_one(control)


def test_the_custom_type_reaches_json_through_its_projection_and_not_as_an_object() -> None:
    config = _config()
    with id_scope(SEED):
        state = _drive(_start(config, BandStrategy(STRATEGY_ID, ASSET_ID)), _records(4))

    record = dict(deserialize(serialize(capture_pipeline(state.pipeline))))["strategy"][0]

    assert record["state"]["payload"]["band"] == {"low": "99.5", "high": "108.25"}
    assert "Band object at" not in serialize(capture_pipeline(state.pipeline))


def test_a_custom_type_without_a_projection_is_refused_at_capture() -> None:
    """The projection is required; nothing reflects over an unknown object."""

    class Opaque:
        __slots__ = ()

    class OpaqueStrategy(CountingStrategy):
        def capture_state(self) -> Any:
            return {"opaque": Opaque()}

    config = _config()
    with id_scope(SEED):
        state = _drive(_start(config, OpaqueStrategy(STRATEGY_ID, ASSET_ID)), _records(2))

    with pytest.raises(SerializationError, match="encoder cannot write"):
        capture_pipeline(state.pipeline)


def test_restore_state_receives_the_same_shape_in_memory_and_across_json() -> None:
    seen: list[dict[str, str]] = []

    class Probe(CountingStrategy):
        def restore_state(self, payload: Any, version: int) -> None:
            seen.append({key: type(value).__name__ for key, value in payload.items()})
            super().restore_state(payload, version)

    config = _config()
    with id_scope(SEED):
        state = _drive(_start(config, Probe(STRATEGY_ID, ASSET_ID)), _records(4))

    objects = _objects(config, Probe(STRATEGY_ID, ASSET_ID)).pipeline
    snapshot = capture_pipeline(state.pipeline)

    restore_pipeline(snapshot, objects)
    restore_pipeline(pipeline_from_primitives(deserialize(serialize(snapshot))), objects)

    assert len(seen) == 2
    assert seen[0] == seen[1], "the two paths handed the strategy different shapes"
    assert seen[0]["window"] == "list"


def test_a_codec_failure_does_not_fail_the_strategy_in_the_runtime() -> None:
    """Persistence failures are not routed through strategy status handling."""

    config = _config()
    strategy = RaisingCaptureStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(2))

    with pytest.raises(SerializationError):
        capture_pipeline(state.pipeline)

    entry = state.pipeline.strategy.strategies[STRATEGY_ID]
    assert entry.status.name == "RUNNING"
    assert entry.last_error is None


# ---------------------------------------------------------------------------
# Capture is a pure read, and restore constructs nothing
# ---------------------------------------------------------------------------


def test_capture_does_not_mutate_the_strategy_or_change_its_behaviour() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(BOUNDARY))

    before = strategy.capture_state()
    capture_pipeline(state.pipeline)
    capture_pipeline(state.pipeline)

    assert strategy.capture_state() == before


def test_capture_mints_no_identifier_and_does_not_advance_the_stream() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(BOUNDARY))
        before = current_id_position()
        capture_pipeline(state.pipeline)
        after = current_id_position()

    assert before == after
    assert state.pipeline.id_position.seed == SEED


def test_restore_mints_no_identifier_and_processes_no_record() -> None:
    config = _config()
    strategy = CountingStrategy(STRATEGY_ID, ASSET_ID)
    with id_scope(SEED):
        state = _drive(_start(config, strategy), _records(BOUNDARY))

    payload = serialize(capture_run(state))
    fresh = CountingStrategy(STRATEGY_ID, ASSET_ID)

    with id_scope(SEED):
        before = current_id_position()
        restored = restore_run(run_from_primitives(deserialize(payload)), _objects(config, fresh))
        after = current_id_position()

    assert before == after == IdStreamPosition(SEED, 0)
    assert restored.processed == BOUNDARY
    assert restored.pipeline.strategy.strategies[STRATEGY_ID].instance is fresh


def test_the_module_still_constructs_nothing_from_a_recorded_type_name() -> None:
    import ast
    import inspect

    from alphalab.runtime import snapshot as pipeline_snapshot

    source = inspect.getsource(pipeline_snapshot)
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "getattr" not in called
    assert "eval" not in called
    assert "__import__" not in called
    assert "importlib" not in source


# ---------------------------------------------------------------------------
# 9 -- identifier determinism
# ---------------------------------------------------------------------------


def test_capture_and_restore_add_no_identifier_draws() -> None:
    """The stream position after a split matches the uninterrupted control."""

    assert _split(fresh=True).pipeline.id_position == _uninterrupted().pipeline.id_position


@pytest.mark.parametrize("boundary", list(range(1, TOTAL)))
def test_no_boundary_changes_the_identifier_stream(boundary: int) -> None:
    resumed = _split(boundary=boundary, fresh=True)
    control = _uninterrupted()

    assert resumed.pipeline.id_position == control.pipeline.id_position
    ids = [str(order.order_id.value) for order in resumed.pipeline.oms.orders.orders()]
    assert ids == [str(order.order_id.value) for order in control.pipeline.oms.orders.orders()]
    assert len(ids) == len(set(ids)), "a continued run re-minted an identifier"


def test_an_unseeded_run_restores_its_state_without_claiming_id_continuity() -> None:
    """The ADR-0022 carve-out: no seed, no promise about identifiers."""

    resumed = _split(fresh=True, seed=None)
    control = _uninterrupted()

    assert resumed.pipeline.id_position == IdStreamPosition(None, 0)
    assert _acted_on(resumed) == _acted_on(control)
    assert _declared_state(resumed) == _declared_state(control)
    assert (
        resumed.pipeline.portfolio.positions[ASSET_ID].quantity
        == control.pipeline.portfolio.positions[ASSET_ID].quantity
    )


# ---------------------------------------------------------------------------
# The historical lesson -- kept, not deleted
# ---------------------------------------------------------------------------


def test_a_non_declaring_stateful_strategy_still_diverges() -> None:
    """ADR-0025 testing invariant 12, and the whole of milestone 1.

    This is the assertion this file was originally written to make, retained
    verbatim in intent. It is no longer a gap: it is the documented behaviour of
    a strategy that declines to declare state, and the v2.9 conditional
    guarantee still describes it exactly.

    If this ever converges, either `BaseStrategy` grew a default codec -- which
    would make every strategy claim state it does not have -- or something began
    introspecting attributes. Both are refused by ADR-0025 decision 1.
    """

    resumed = _split(fresh=True, factory=UndeclaredCountingStrategy)
    control = _uninterrupted(factory=UndeclaredCountingStrategy)

    assert _acted_on(control) == (4.0, 7.0, 10.0)
    assert _acted_on(resumed) == (4.0, 8.0)
    assert _acted_on(resumed) != _acted_on(control)


def test_a_non_declaring_strategy_keeps_the_v2_9_conditional_guarantee() -> None:
    """Warm resume still converges for it, exactly as it did at v2.9.0."""

    resumed = _split(fresh=False, factory=UndeclaredCountingStrategy)
    control = _uninterrupted(factory=UndeclaredCountingStrategy)

    assert _acted_on(resumed) == _acted_on(control)
    assert _class_one(resumed) == _class_one(control)


def test_the_stateless_case_is_unaffected_in_both_directions() -> None:
    """A strategy whose intents are a function of the event, declaring or not."""

    for factory in (
        lambda sid, aid: CountingStrategy(sid, aid, every=1),
        lambda sid, aid: UndeclaredCountingStrategy(sid, aid, every=1),
    ):
        control = _uninterrupted(factory=factory)
        resumed = _split(fresh=True, factory=factory)
        assert _acted_on(resumed) == _acted_on(control)
