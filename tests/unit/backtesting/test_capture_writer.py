"""The trace's writer is ``serialize``, byte for byte; its batches are its steps (ADR-0055).

A trace hashes each step's entries as ``serialize`` writes them. The capture
writes that text itself, because ``serialize`` is too slow to run on every step
(PC-07). These tests hold the writer to ``serialize`` for every kind of value an
entry can hold, not only the kinds the engine records today.

The chain is computed in batches of steps. Each step still gets its own link,
in step order. These tests hold a batched chain to the one computed step by
step, across batch boundaries. A step the trace cannot write fails the capture
before anything the run did after that step fails it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, Flag, IntEnum, StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

import pytest

import alphalab.backtesting.capture as capture
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
    run_captured,
)
from alphalab.common.append_log import AppendOnlyLog
from alphalab.persistence.exceptions import SerializationError
from alphalab.persistence.serializer import serialize
from tests.integration.harness import context_factory, scripted_run


class Plain(Enum):
    A = 1


class Named(StrEnum):
    B = "b"


class Number(IntEnum):
    C = 3


class Flags(Flag):
    X = 1
    Y = 2


@dataclass(frozen=True)
class Empty:
    pass


@dataclass(frozen=True)
class Inner:
    value: str


class Projected:
    """A value that states its own serializable form."""

    def __init__(self, items: tuple[Any, ...]) -> None:
        self.items = items

    def __serializable__(self) -> tuple[Any, ...]:
        return self.items


@dataclass(frozen=True)
class Every:
    """One field of every kind a trace's writer distinguishes."""

    text: str
    decimal: Decimal
    identifier: UUID
    whole: int
    real: float
    flag: bool
    nothing: None
    plain: Plain
    named: Named
    number: Number
    flags: Flags
    inner: Inner
    empty: Empty
    mapping: dict[Any, Any]
    proxy: MappingProxyType[str, Any]
    items: list[Any]
    pair: tuple[Any, ...]
    log: AppendOnlyLog[Any]
    projected: Projected
    loose: Any = field(default=None)


def _every(index: int) -> Every:
    decimals = [Decimal(0), Decimal("-1.50"), Decimal("1E+3"), Decimal("NaN"), Decimal("-0")]
    reals = [0.0, -0.0, 5e-324, 1e300, 1.5]
    texts = ['quote " and \\ backslash', "accent é and emoji 🦆", "control \n\t\x01", "%s %d %%"]
    keys: list[dict[Any, Any]] = [
        {"b": 1, "a": Decimal("2.5")},
        {2: "two", 1: "one"},
        {0.5: None, -1.0: True},
        {True: "t", False: "f"},
        {},
    ]
    return Every(
        text=texts[index % len(texts)],
        decimal=decimals[index % len(decimals)],
        identifier=UUID(int=index * 0x1234_5678_9ABC_DEF0 + 1),
        whole=[0, -7, 10**30][index % 3],
        real=reals[index % len(reals)],
        flag=index % 2 == 0,
        nothing=None,
        plain=Plain.A,
        named=Named.B,
        number=Number.C,
        flags=Flags.X | Flags.Y if index % 2 else Flags.X,
        inner=Inner(texts[(index + 1) % len(texts)]),
        empty=Empty(),
        mapping=keys[index % len(keys)],
        proxy=MappingProxyType({"z": [1, 2.5, None], "y": Inner("nested")}),
        items=[Decimal("0.1"), (1, "a"), {"k": Plain.A}],
        pair=(None, False, 3),
        log=AppendOnlyLog([Inner("logged"), 7]),
        projected=Projected((Inner("projected"), Decimal(5))),
        loose=[Named.B, Number.C][index % 2],
    )


def _reference(logs: tuple[list[Any], ...]) -> str:
    return serialize([[[type(entry).__name__, entry] for entry in log] for log in logs])


@pytest.mark.parametrize("index", range(8))
def test_every_kind_of_value_is_written_as_serialize_writes_it(index: int) -> None:
    entry = _every(index)
    logs: tuple[list[Any], ...] = ([entry], [], [Inner("event"), entry], [Empty()])

    assert capture._payload(*logs) == _reference(logs)
    # Written again, through the writers each type now has, still the same.
    assert capture._payload(*logs) == _reference(logs)


def test_an_entry_that_is_not_a_record_is_written_as_serialize_writes_it() -> None:
    logs: tuple[list[Any], ...] = ([Decimal("1.25"), "text"], [None], [Plain.A], [[1, 2]])

    assert capture._payload(*logs) == _reference(logs)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), Decimal, object()])
def test_a_value_json_cannot_hold_fails_as_serialize_fails(bad: object) -> None:
    entry = Inner("x")
    logs: tuple[list[Any], ...] = ([entry], [], [], [{"value": bad}])

    with pytest.raises(SerializationError) as written:
        capture._payload(*logs)
    with pytest.raises(SerializationError) as serialized:
        _reference(logs)
    assert str(written.value) == str(serialized.value)


EXTRACTOR = ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE)
STRATEGY_ID = "5d3c7a10-6f0e-4c8b-9a51-2b7e8f9d4c31"
ASSET_ID = "9b2e4f60-1c7d-4e3a-8f25-6d0a1b3c5e77"


def _trace_id(records: int, monkeypatch: pytest.MonkeyPatch, batch: int) -> str:
    monkeypatch.setattr(capture, "_CHAIN_BATCH", batch)
    mids = [Decimal(100) + Decimal(i % 7) - Decimal(i) / 40 for i in range(records)]
    plan = {2.0 + i: (Decimal(3) if i % 4 == 0 else Decimal(-1)) for i in range(0, records, 3)}
    run = scripted_run(plan, mids, STRATEGY_ID, ASSET_ID)
    extractor = EXTRACTOR
    record = run_captured(*run, context_factory, capture=CaptureSpec(extractor, True)).record
    assert record.trace_id is not None
    return record.trace_id


@pytest.mark.parametrize("records", [1, 62, 63, 64, 65, 130])
def test_a_batched_chain_is_the_chain_computed_step_by_step(
    records: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    batched = _trace_id(records, monkeypatch, capture._CHAIN_BATCH)

    assert batched == _trace_id(records, monkeypatch, 1)
    assert batched == _trace_id(records, monkeypatch, 7)


def test_a_step_the_trace_cannot_write_fails_the_capture_before_a_later_failure_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step is taken, its batch is not yet chained, and the run then fails."""

    def drive(*arguments: Any) -> Any:
        observer = arguments[-1]
        observer._trace = capture._Trace("0" * 64)
        observer._trace.points.append({"equity": float("nan")})
        observer._trace.absorb((), (), ())
        assert observer._trace.pending, "one step taken, not yet chained"
        raise RuntimeError("the run failed after that step")

    monkeypatch.setattr(capture, "_drive", drive)
    nothing: Any = None
    with pytest.raises(SerializationError, match="not JSON compliant") as failed:
        run_captured(nothing, nothing, nothing, nothing, capture=CaptureSpec(EXTRACTOR, True))
    causes = []
    cause: BaseException | None = failed.value
    while cause is not None:
        causes.append(type(cause))
        cause = cause.__context__
    assert RuntimeError in causes, causes


def test_a_batch_that_fails_is_not_chained_twice() -> None:
    trace = capture._Trace("0" * 64)
    trace.points.append({"equity": float("inf")})
    trace.absorb((), (), ())
    first = trace.link

    with pytest.raises(SerializationError):
        trace.chain()
    trace.chain()
    assert trace.pending == []
    assert trace.link == first
