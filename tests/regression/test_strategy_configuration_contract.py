"""What a strategy's ``configure`` value reads back as after a restore (v4.1, LIM-004).

``StrategyRecord.config`` is written by the encoder every payload uses and read
back as JSON reads it (ADR-0025 decision 4). v4.0 recorded that as LIM-004 and
pinned one case; v4.1 states the whole conversion as a contract and holds every
row of it here, through the path a restore takes -- ``capture``, ``serialize``,
``deserialize``, ``from_primitives``, ``restore`` -- not through the encoder
alone:

* **Exact**: ``None``, ``bool``, ``int``, ``str``, a finite ``float``, and lists
  and string-keyed mappings of these, at any depth.
* **Converted**: a ``Decimal`` reads back as its exact text (exponent kept), a
  tuple as a list, a non-string mapping key as its JSON text, a ``StrEnum`` as
  its value, a plain ``Enum`` as ``ClassName.MEMBER``, a ``UUID`` as its text, a
  dataclass as a mapping of its fields.
* **Refused at capture**: a ``set``, a non-finite ``float``, and any other object
  -- ``SerializationError``, naming the type, and nothing is written.

Why the format is not changed in 4.1: a typed form would move the pipeline
schema, and so every run's ``result_id`` -- which ``docs/api/PUBLIC_API.md``'s
stability policy does not let a minor release do. Nothing on the execution path
reads the value (a strategy's parameters are its constructor's, its durable
state goes through ``StrategyStateProtocol``, which is exact), and a run's
``configuration_id`` is the same before and after a restore (held below).
"""

from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, BacktestResult
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import SerializationError
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
)

STRATEGY = "LIM004-STRATEGY"
ASSET = "LIM004-ASSET"


class Side(enum.StrEnum):
    BUY = "buy"


class Mode(enum.Enum):
    FAST = 1


@dataclass(frozen=True)
class Window:
    fast: int
    slow: int


def _strategy() -> ScriptedStrategy:
    return ScriptedStrategy(STRATEGY, ASSET, {})


def _restored(configured: Any) -> Any:
    """``configured``, after a run holding it is captured, serialized and restored."""

    runtime = register_strategy(create_runtime(), STRATEGY, _strategy())
    entry, _ = RuntimeSupervisor.configure(runtime.strategies[STRATEGY], configured, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    config = backtest_config(STRATEGY)
    state = BacktestEngine.initialize(config, replace(runtime, strategies={STRATEGY: entry}))
    payload = deserialize(serialize(capture(state)))
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY: _strategy()},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )
    return restore(from_primitives(payload), objects).pipeline.strategy.strategies[STRATEGY].config


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        0,
        -(2**70),
        "",
        "Decimal('10')",
        1.5,
        -0.0,
        1e308,
        5e-324,
        [],
        {},
        [1, [2, [3, {"deep": [None, False]}]]],
        {"size": 10, "venues": ["XNAS", "XNYS"], "limits": {"max": 2.5, "on": True}},
        {"__alphalab_type__": "a key a typed format might use"},
    ],
    ids=repr,
)
def test_a_json_native_value_reads_back_exactly(value: Any) -> None:
    restored = _restored(value)

    assert restored == value and type(restored) is type(value)
    if isinstance(value, float):
        assert math.copysign(1.0, restored) == math.copysign(1.0, value), "the sign of zero"


@pytest.mark.parametrize(
    ("value", "read_back"),
    [
        (Decimal("10"), "10"),
        (Decimal("10.00"), "10.00"),
        (Decimal("1E+3"), "1E+3"),
        (Decimal("-0"), "-0"),
        (Decimal("0.1"), "0.1"),
        (Decimal("NaN"), "NaN"),
        ((2, 4), [2, 4]),
        ((), []),
        ({"windows": (2, (3, 4)), "size": Decimal("2.5")}, {"windows": [2, [3, 4]], "size": "2.5"}),
        ({1: "one", 2: "two"}, {"1": "one", "2": "two"}),
        ({True: "yes"}, {"true": "yes"}),
        (Side.BUY, "buy"),
        (Mode.FAST, "Mode.FAST"),
        (uuid.UUID(int=7), "00000000-0000-0000-0000-000000000007"),
        (Window(2, 4), {"fast": 2, "slow": 4}),
    ],
    ids=repr,
)
def test_a_converted_value_reads_back_as_the_contract_states(value: Any, read_back: Any) -> None:
    restored = _restored(value)

    assert restored == read_back and type(restored) is type(read_back)
    assert serialize(restored) == serialize(value), "both forms are written the same way"


def test_a_decimal_reads_back_as_text_that_rebuilds_it_exactly() -> None:
    """A host that keeps a Decimal in its configuration rebuilds it from the text, exactly."""

    for value in (
        Decimal("10.00"),
        Decimal("1E+3"),
        Decimal("-0"),
        Decimal("123.456789012345678901"),
    ):
        text = _restored({"size": value})["size"]
        assert Decimal(text) == value and str(Decimal(text)) == str(value)


@pytest.mark.parametrize(
    ("value", "named"),
    [
        ({"assets": {"A", "B"}}, "builtins.set"),
        ({"assets": frozenset({"A"})}, "builtins.frozenset"),
        ({"threshold": math.nan}, "Out of range float values"),
        ({"threshold": math.inf}, "Out of range float values"),
        ({"handle": object()}, "builtins.object"),
    ],
    ids=["set", "frozenset", "nan", "inf", "object"],
)
def test_a_value_json_cannot_hold_is_refused_at_capture(value: Any, named: str) -> None:
    with pytest.raises(SerializationError, match=named):
        _restored(value)


def test_the_configuration_identity_does_not_move_across_a_restore() -> None:
    """The recorded configuration serializes alike before and after, so its digest is one."""

    configured = {"size": Decimal("10"), "windows": (2, 4), "side": Side.BUY}
    runtime = register_strategy(create_runtime(), STRATEGY, _strategy())
    entry, _ = RuntimeSupervisor.configure(runtime.strategies[STRATEGY], configured, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    config = backtest_config(STRATEGY)
    state = BacktestEngine.initialize(config, replace(runtime, strategies={STRATEGY: entry}))
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY: _strategy()},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )
    restored = restore(from_primitives(deserialize(serialize(capture(state)))), objects)

    # The recorded run's identities, before finalizing -- which would mint
    # analytics identifiers outside any scope here.
    before = digest_run(BacktestResult(run=state))
    after = digest_run(BacktestResult(run=restored))
    assert before.configuration_id == after.configuration_id
    assert before.result_id == after.result_id
