"""A strategy that declares its configuration's type reads it back exactly (v4.1.1, FUT-002).

``test_strategy_configuration_contract.py`` holds what a configuration reads
back as when nothing declares its type: JSON's reading of the encoder's form.
This holds the other half: a strategy class setting ``configuration_type`` is
configured only with a value of that type, and a restore reads the recorded
value back *as* that type -- equal, and of the same types all the way down --
through the path a restore takes (``capture``, ``serialize``, ``deserialize``,
``from_primitives``, ``restore``). The payload is unchanged, so a run's record
and identities are what they were.
"""

from __future__ import annotations

import enum
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal

import pytest

from alphalab.backtesting import BacktestEngine
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    StrategyConfigurationError,
    decode_configuration,
    require_configuration,
)
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import ScriptedStrategy, backtest_config, dataset_of_quotes

STRATEGY = "FUT002-STRATEGY"
ASSET = "FUT002-ASSET"


class Side(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class Mode(enum.Enum):
    FAST = 1
    SLOW = 2


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


@dataclass(frozen=True)
class Window:
    fast: int
    slow: int

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError(f"fast {self.fast} must be below slow {self.slow}")


@dataclass(frozen=True)
class Bands:
    width: Decimal
    windows: tuple[Window, ...]
    sides: tuple[Side, ...]
    mode: Mode
    levels: dict[int, Decimal]
    by_level: Mapping[Level, str]
    flags: dict[bool, float]
    cap: Decimal | None
    run_id: uuid.UUID
    kind: Literal["mean", "trend"]
    weights: list[Annotated[float, "a weight"]]
    note: Any
    pair: tuple[str, Decimal]
    #: Added "later": a payload without it reads back at the default.
    tolerance: Decimal = Decimal("0.01")
    tags: tuple[str, ...] = field(default_factory=tuple)


BANDS = Bands(
    width=Decimal("2.50"),
    windows=(Window(5, 20), Window(10, 50)),
    sides=(Side.BUY, Side.SELL),
    mode=Mode.FAST,
    levels={1: Decimal("1E+3"), -2: Decimal("-0")},
    by_level=MappingProxyType({Level.LOW: "low", Level.HIGH: "high"}),
    flags={True: 1.5, False: -0.0},
    cap=None,
    run_id=uuid.UUID(int=42),
    kind="mean",
    weights=[0.25, 0.75],
    note={"free": [1, "two", None, {"three": 3.0}]},
    pair=("XNAS", Decimal("0.10")),
    tags=("a", "b"),
)


class Typed(ScriptedStrategy):
    configuration_type = Bands


def _same_types(left: Any, right: Any, where: str = "config") -> None:
    """Equal is not enough: every value must also be of the type it was."""

    assert type(left) is type(right), f"{where}: {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, Mapping):
        assert list(left) == list(right) or set(left) == set(right), where
        for key in left:
            matching = next(other for other in right if other == key)
            _same_types(key, matching, f"{where} key")
            _same_types(left[key], right[key], f"{where}[{key!r}]")
    elif isinstance(left, list | tuple):
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            _same_types(a, b, f"{where}[{index}]")
    elif hasattr(left, "__dataclass_fields__"):
        for name in left.__dataclass_fields__:
            _same_types(getattr(left, name), getattr(right, name), f"{where}.{name}")


def _state(strategy: ScriptedStrategy, configured: Any) -> Any:
    runtime = register_strategy(create_runtime(), STRATEGY, strategy)
    entry, _ = RuntimeSupervisor.configure(runtime.strategies[STRATEGY], configured, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(runtime, strategies={STRATEGY: entry})


def _objects(strategy: ScriptedStrategy) -> RunObjects:
    config = backtest_config(STRATEGY)
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY: strategy},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def _payload(strategy: ScriptedStrategy, configured: Any) -> Any:
    state = BacktestEngine.initialize(backtest_config(STRATEGY), _state(strategy, configured))
    return deserialize(serialize(capture(state)))


def _restored(payload: Any, strategy: ScriptedStrategy) -> Any:
    run = restore(from_primitives(payload), _objects(strategy))
    return run.pipeline.strategy.strategies[STRATEGY].config


def _typed() -> Typed:
    return Typed(STRATEGY, ASSET, {})


def _config_of(payload: Any) -> Any:
    (record,) = payload["pipeline"]["strategy"]
    return record["config"]


# --------------------------------------------------------------------------- #
# Round trips
# --------------------------------------------------------------------------- #


def test_a_declared_configuration_reads_back_equal_and_of_every_type() -> None:
    restored = _restored(_payload(_typed(), BANDS), _typed())

    assert restored == BANDS
    _same_types(restored, BANDS)
    assert math.copysign(1.0, restored.flags[False]) == -1.0, "the sign of zero"
    assert str(restored.levels[1]) == "1E+3", "a Decimal's exponent"


@pytest.mark.parametrize(
    ("declared", "value"),
    [
        (Decimal, Decimal("10.00")),
        (Decimal, Decimal("1E+3")),
        (Decimal, Decimal("-0")),
        (tuple[int, ...], (2, 4)),
        (tuple[int, ...], ()),
        (tuple[int, tuple[int, int]], (2, (3, 4))),
        (dict[int, str], {1: "one", 2: "two"}),
        (dict[bool, str], {True: "yes"}),
        (dict[float, str], {1.5: "x", -2.0: "y"}),
        (Side, Side.BUY),
        (Mode, Mode.FAST),
        (Level, Level.HIGH),
        (dict[Level, int], {Level.LOW: 1}),
        (uuid.UUID, uuid.UUID(int=7)),
        (Window, Window(2, 4)),
        (Decimal | None, None),
        (Decimal | None, Decimal("0.5")),
        (Literal[1, "x", True], True),
        (Any, {"native": [1, 2.5, None, True, "s"]}),
    ],
    ids=repr,
)
def test_every_value_the_contract_converts_reads_back_exactly_when_declared(
    declared: Any, value: Any
) -> None:
    class Declared(ScriptedStrategy):
        configuration_type = declared

    strategy = Declared(STRATEGY, ASSET, {})
    restored = _restored(_payload(strategy, value), Declared(STRATEGY, ASSET, {}))

    assert restored == value
    _same_types(restored, value)


def test_the_payload_and_the_runs_identity_do_not_move() -> None:
    """Declaring a type changes how a configuration is read, never how it is written."""

    typed = _payload(_typed(), BANDS)
    plain = _payload(ScriptedStrategy(STRATEGY, ASSET, {}), BANDS)
    assert _config_of(typed) == _config_of(plain)

    data = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101"), Decimal("102")])

    def run(strategy: ScriptedStrategy) -> Any:
        return BacktestEngine.run(
            backtest_config(STRATEGY), data, _state(strategy, BANDS), _context
        )

    # Two classes of one name, one declaring a type and one not: one record.
    class Undeclared(ScriptedStrategy):
        pass

    Undeclared.__name__ = Undeclared.__qualname__ = Typed.__name__
    declared_digest = digest_run(run(_typed()))
    undeclared_digest = digest_run(run(Undeclared(STRATEGY, ASSET, {})))
    assert declared_digest.result_id == undeclared_digest.result_id
    assert declared_digest.configuration_id == undeclared_digest.configuration_id


def _context(strategy_id: str) -> Any:
    from tests.integration.harness import context_factory

    return context_factory(strategy_id)


# --------------------------------------------------------------------------- #
# Refused at configure, naming the field
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "named"),
    [
        (replace(BANDS, width=2.5), r"config\.width: expected Decimal, found float"),  # type: ignore[arg-type]
        (replace(BANDS, windows=[Window(1, 2)]), r"config\.windows: expected a tuple"),  # type: ignore[arg-type]
        (replace(BANDS, sides=("buy",)), r"config\.sides\[0\]: expected a Side member"),  # type: ignore[arg-type]
        (replace(BANDS, weights=[1]), r"config\.weights\[0\]: expected float, found int"),
        (replace(BANDS, cap=Decimal("NaN")), r"config\.cap: Decimal\('NaN'\) is not finite"),
        (replace(BANDS, kind="other"), r"config\.kind: expected one of"),  # type: ignore[arg-type]
        (replace(BANDS, levels={"1": Decimal("1")}), r"config\.levels key: expected int"),  # type: ignore[dict-item]
        (replace(BANDS, note={"d": Decimal("1")}), r"config\.note\['d'\].*declare its type"),
        (replace(BANDS, note=(1, 2)), r"config\.note.*declare its type"),
        (replace(BANDS, pair=("XNAS",)), r"config\.pair: expected 2 items"),  # type: ignore[arg-type]
        ({"width": Decimal("1")}, r"config: expected a Bands, found dict"),
        (replace(BANDS, windows=(Window(True, 4),)), r"\.fast: expected int, found bool"),
        (replace(BANDS, pair=(Side.BUY, Decimal("1"))), r"pair\[0\]: expected str, found Side"),
    ],
    ids=["float-for-decimal", "list-for-tuple", "text-for-enum", "int-for-float", "nan",
         "literal", "key-type", "decimal-in-any", "tuple-in-any", "tuple-length", "dict",
         "bool-for-int", "str-subclass-for-str"],
)  # fmt: skip
def test_a_value_not_of_the_declared_type_is_refused_at_configure(value: Any, named: str) -> None:
    runtime = register_strategy(create_runtime(), STRATEGY, _typed())
    with pytest.raises(StrategyConfigurationError, match=named):
        RuntimeSupervisor.configure(runtime.strategies[STRATEGY], value, 1.0)


class Printed(enum.Enum):
    ONE = 1

    def __str__(self) -> str:
        return "one"


@dataclass(frozen=True)
class Derived:
    base: int
    double: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "double", self.base * 2)


class Plain:
    pass


@pytest.mark.parametrize(
    ("declared", "named"),
    [
        (set[int], "cannot be declared"),
        (frozenset[str], "cannot be declared"),
        (datetime, "cannot be declared"),
        (int | str, "union of two types"),
        (dict[Decimal, int], "keyed by"),
        (dict[uuid.UUID, int], "keyed by"),
        (dict[Mode, int], "keyed by"),
        (Plain, "cannot be declared"),
        (Derived, "not an __init__ field"),
        (Printed, "redefines how its members print"),
        (Literal[1.5], "is not a bool, int or str"),
    ],
    ids=repr,
)
def test_a_type_whose_values_cannot_be_read_back_is_refused(declared: Any, named: str) -> None:
    with pytest.raises(StrategyConfigurationError, match=named):
        require_configuration(None, declared)


def test_a_naive_and_an_aware_datetime_are_both_refused_not_coerced() -> None:
    for value in (datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC)):
        with pytest.raises(StrategyConfigurationError, match="cannot be declared"):
            require_configuration(value, datetime)


# --------------------------------------------------------------------------- #
# Read back: defaults, constraints, and what is refused
# --------------------------------------------------------------------------- #


def test_a_field_added_since_the_payload_was_written_reads_back_at_its_default() -> None:
    payload = _payload(_typed(), BANDS)
    recorded = _config_of(payload)
    del recorded["tolerance"], recorded["tags"]

    restored = _restored(payload, _typed())

    assert restored == replace(BANDS, tolerance=Decimal("0.01"), tags=())


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (lambda c: c.update(width=2.5), r"width: expected a Decimal's text"),
        (lambda c: c.update(width="2.5000000000000000000000000001x"), r"is not a Decimal"),
        (lambda c: c.update(width="NaN"), r"not the text of a finite Decimal"),
        (lambda c: c.update(mode="Other.FAST"), r"is not a Mode member"),
        (lambda c: c.update(run_id="not-a-uuid"), r"is not a UUID"),
        (lambda c: c.update(windows=[{"fast": 9, "slow": 3}]), r"Window refused the recorded"),
        (lambda c: c.update(windows=[{"fast": 1, "slow": 2, "extra": 0}]), r"no field 'extra'"),
        (lambda c: c.pop("width"), r"width: missing"),
        (lambda c: c.update(levels={"01": "1"}), r"'01' is not the text an int key"),
        (lambda c: c.update(weights=[1, 0.5]), r"weights\[0\]: expected float, found int"),
        (lambda c: c.update(width="+2.50"), r"'\+2.50' is not the text of a finite Decimal"),
        (lambda c: c.update(mode="FAST"), r"'FAST' is not a Mode member"),
    ],
    ids=["number-for-decimal", "garbled", "nan", "enum", "uuid", "constraint", "unknown-field",
         "missing", "noncanonical-key", "int-for-float", "noncanonical-decimal", "bare-name"],
)  # fmt: skip
def test_a_recorded_value_not_of_the_declared_type_is_refused_on_restore(
    change: Any, named: str
) -> None:
    payload = _payload(_typed(), BANDS)
    change(_config_of(payload))
    with pytest.raises(StateDecodeError, match=rf"strategy {STRATEGY!r}.*{named}"):
        _restored(payload, _typed())


def test_a_strategy_that_declares_nothing_reads_back_as_before() -> None:
    plain = ScriptedStrategy(STRATEGY, ASSET, {})
    restored = _restored(_payload(plain, BANDS), ScriptedStrategy(STRATEGY, ASSET, {}))
    assert restored == _config_of(_payload(plain, BANDS))
    assert restored["width"] == "2.50" and restored["sides"] == ["buy", "sell"]


def test_decoding_is_the_inverse_of_the_encoder_for_the_declared_type() -> None:
    encoded = deserialize(serialize(BANDS))
    assert decode_configuration(encoded, Bands) == BANDS
    with pytest.raises(StrategyConfigurationError, match="expected an object of Bands"):
        decode_configuration([encoded], Bands)


def test_a_paused_replay_resumes_with_the_declared_configuration() -> None:
    from alphalab.backtesting import PausedReplay, ReplayBacktest
    from alphalab.backtesting.replay_snapshot import capture as capture_replay
    from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
    from alphalab.backtesting.replay_snapshot import restore as restore_replay

    data = dataset_of_quotes(ASSET, [Decimal("100"), Decimal("101"), Decimal("102")])
    paused = ReplayBacktest.run(
        backtest_config(STRATEGY),
        data,
        _state(_typed(), BANDS),
        _context,
        pause_when=lambda progress: progress.records_replayed == 2,
    )
    assert isinstance(paused, PausedReplay)
    payload = deserialize(serialize(capture_replay(paused)))
    restored = restore_replay(replay_from_primitives(payload), _objects(_typed()), data)
    config = restored.run.pipeline.strategy.strategies[STRATEGY].config
    assert config == BANDS
    _same_types(config, BANDS)
    resumed = ReplayBacktest.resume(restored, _context)
    whole = ReplayBacktest.run(backtest_config(STRATEGY), data, _state(_typed(), BANDS), _context)
    assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
