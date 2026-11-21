"""What a partial fill leaves, and a cap that refuses data without sizes (ADR-0060, GAP-38).

Every figure is calculated by hand. A buy of 100 against quotes showing 60, at a
participation cap of one half, fills 30 a turn:

* **Cancel, the default.** 30 fill and the remainder of 70 is cancelled in the
  same step, as every run before ADR-0060 did (ADR-0014 decision 3).
* **Rest for events.** ``RestRemainder(events=2)``: 30 at the decision, then 30
  at each of the next two events, 90 in all; the remainder of 10 is cancelled
  after its second turn, and what it held is freed.
* **Rest for seconds.** ``RestRemainder(seconds=1.5)``: decided at 2, the
  remainder works until 3.5. It fills 30 at 3, and an event at 3.6 finds its
  rest over and withdraws it without a turn: 60 in all.
* **A turn that fills nothing counts.** At an event showing no size the
  remainder fills nothing, spends a turn, and works on.
* **An immediate order is withdrawn after its first turn,** whatever the policy.
* **A strict cap refuses an event with no size**, naming the asset and instant,
  and otherwise caps as ``LiquidityCappedFill`` does.
* **What is recorded.** The policy in ``execution_assumptions`` and its
  settings only when declared, and a snapshot at version 9 that resumes a
  resting remainder to the same fills.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from alphalab.common.order_terms import MARKET, OrderTerms, TimeInForce
from alphalab.core.enums import Side
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import (
    FillPolicy,
    LiquidityCappedFill,
    LiquidityContext,
    RestRemainder,
    StrictLiquidityCappedFill,
)
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(UUID(int=0x0F1))
ASSET = str(UUID(int=0x0F2, version=4))
CAP = LiquidityCappedFill(participation_rate=Decimal("0.5"))


class _Buy(BaseStrategy):
    """Buys 100 at the quote stamped 2.0, on the terms given."""

    def __init__(self, terms: OrderTerms = MARKET) -> None:
        self._terms = terms

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.timestamp != 2.0:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=ASSET,
                target=Decimal("100"),
                timestamp=2.0,
                terms=self._terms,
            ),
        )


def _pipeline(
    remainder: RestRemainder | None, terms: OrderTerms = MARKET
) -> ExecutionPipelineState:
    config = replace(pipeline_config(STRATEGY_ID), remainder=remainder)
    strategy = running_strategy_state(STRATEGY_ID, _Buy(terms))
    return ExecutionPipeline.initialize(config, strategy, 1.0)


def _quote(
    state: ExecutionPipelineState, at: float, mid: str, size: str, policy: FillPolicy = CAP
) -> ExecutionPipelineResult:
    event = sized_quote(ASSET, at, Decimal(mid), Decimal(size))
    return ExecutionPipeline.process_quote(state, event, context_factory, fill_policy=policy)


def _run(
    state: ExecutionPipelineState, quotes: Iterable[tuple[float, str, str]]
) -> tuple[ExecutionPipelineState, list[tuple[Decimal, Decimal, float]]]:
    fills: list[tuple[Decimal, Decimal, float]] = []
    for at, mid, size in quotes:
        result = _quote(state, at, mid, size)
        fills.extend((fill.quantity, fill.price, fill.filled_at) for fill in result.fills)
        state = result.state
    return state, fills


QUOTES = ((2.0, "100", "60"), (3.0, "101", "60"), (4.0, "102", "60"), (5.0, "103", "60"))


def _settled(state: ExecutionPipelineState) -> None:
    assert not state.oms.active_orders
    assert dict(state.allocation.reservations) == {}


# --------------------------------------------------------------------------- #
# The policies
# --------------------------------------------------------------------------- #


def test_cancel_is_the_default_and_takes_one_turn() -> None:
    state, fills = _run(_pipeline(None), QUOTES)
    assert fills == [(Decimal("30"), Decimal("100"), 2.0)]
    _settled(state)
    assert state.portfolio.positions[ASSET].quantity == Decimal("30")


def test_a_remainder_rests_for_its_events_then_is_cancelled() -> None:
    state, fills = _run(_pipeline(RestRemainder(events=2)), QUOTES)
    assert fills == [
        (Decimal("30"), Decimal("100"), 2.0),
        (Decimal("30"), Decimal("101"), 3.0),
        (Decimal("30"), Decimal("102"), 4.0),
    ]
    _settled(state)
    assert state.portfolio.positions[ASSET].quantity == Decimal("90")


def test_a_remainder_rests_for_its_seconds_then_is_withdrawn_without_a_turn() -> None:
    quotes = ((2.0, "100", "60"), (3.0, "101", "60"), (3.6, "102", "60"))
    state, fills = _run(_pipeline(RestRemainder(seconds=1.5)), quotes)
    assert fills == [(Decimal("30"), Decimal("100"), 2.0), (Decimal("30"), Decimal("101"), 3.0)]
    _settled(state)


def test_a_turn_that_fills_nothing_counts_and_the_remainder_works_on() -> None:
    quotes = ((2.0, "100", "60"), (3.0, "101", "0"), (4.0, "102", "60"), (5.0, "103", "60"))
    state, fills = _run(_pipeline(RestRemainder(events=2)), quotes)
    assert fills == [(Decimal("30"), Decimal("100"), 2.0), (Decimal("30"), Decimal("102"), 4.0)]
    _settled(state)


def test_an_immediate_order_is_withdrawn_after_its_first_turn() -> None:
    terms = OrderTerms(time_in_force=TimeInForce.IOC)
    state, fills = _run(_pipeline(RestRemainder(events=2), terms), QUOTES)
    assert fills == [(Decimal("30"), Decimal("100"), 2.0)]
    _settled(state)


@pytest.mark.parametrize(
    ("events", "seconds", "match"),
    [
        (None, None, "exactly one"),
        (2, 1.5, "exactly one"),
        (0, None, "positive whole number"),
        (True, None, "positive whole number"),
        (None, 0.0, "positive, finite"),
        (None, float("inf"), "positive, finite"),
    ],
)
def test_a_rest_must_be_stated_exactly(events: Any, seconds: Any, match: str) -> None:
    with pytest.raises(ExecutionValidationError, match=match):
        RestRemainder(events=events, seconds=seconds)


# --------------------------------------------------------------------------- #
# Strict caps
# --------------------------------------------------------------------------- #


def _context(available: str | None) -> LiquidityContext:
    return LiquidityContext(
        asset_id=ASSET,
        side=Side.BUY,
        requested_quantity=Decimal("100"),
        price=Decimal("100"),
        available_quantity=None if available is None else Decimal(available),
        timestamp=7.0,
    )


def test_a_strict_cap_refuses_an_event_with_no_size() -> None:
    assert CAP.decide(_context(None)).status is FillStatus.FULL_FILL, "the lenient cap fills"
    with pytest.raises(ExecutionValidationError, match=rf"{ASSET} at 7\.0 shows no size"):
        StrictLiquidityCappedFill(Decimal("0.5")).decide(_context(None))


@pytest.mark.parametrize("available", ["60", "0", "400"])
def test_a_strict_cap_caps_as_the_lenient_one_where_there_is_size(available: str) -> None:
    strict = StrictLiquidityCappedFill(Decimal("0.5"))
    assert strict.decide(_context(available)) == CAP.decide(_context(available))


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("1.5"), Decimal("NaN"), 0.5])
def test_a_strict_caps_rate_is_a_share(rate: Any) -> None:
    with pytest.raises(ExecutionValidationError, match="participation rate"):
        StrictLiquidityCappedFill(rate)


# --------------------------------------------------------------------------- #
# What is recorded
# --------------------------------------------------------------------------- #


def test_the_policy_is_described_with_the_fill_policy_only_when_declared() -> None:
    """Appended as a maker commission is to the costs: the assumptions keep their shape."""

    plain = execution_assumptions(pipeline_config(STRATEGY_ID), CAP)
    assert "remainder" not in plain.fill_policy
    assert plain.settings()["execution.fill_policy"] == plain.fill_policy

    config = replace(pipeline_config(STRATEGY_ID), remainder=RestRemainder(events=2))
    declared = execution_assumptions(config, CAP)
    assert declared.fill_policy == (
        f"{plain.fill_policy}; remainder=RestRemainder(events=2, seconds=None)"
    )
    assert declared.settings()["execution.fill_policy"] == declared.fill_policy
    assert set(declared.settings()) == set(plain.settings())


def test_a_paused_resting_remainder_resumes_to_the_same_fills() -> None:
    state = _pipeline(RestRemainder(events=2))
    paused = _quote(state, *QUOTES[0]).state
    payload = deserialize(serialize(capture(paused)))
    assert payload["schema_version"] == 9
    assert payload["config"]["remainder"] == {"events": 2, "seconds": None}

    objects = RuntimeObjects(
        sizing_model=paused.config.sizing_model,
        simulator=paused.config.simulator,
        strategies={STRATEGY_ID: paused.strategy.strategies[STRATEGY_ID].instance},
        instruments=paused.config.instruments,
    )
    resumed = restore(from_primitives(payload), objects)
    assert _run(resumed, QUOTES[1:])[1] == _run(paused, QUOTES[1:])[1]
    assert len(_run(resumed, QUOTES[1:])[1]) == 2


def test_an_undeclared_runs_payload_carries_no_remainder() -> None:
    payload = deserialize(serialize(capture(_quote(_pipeline(None), *QUOTES[0]).state)))
    assert payload["schema_version"] == 8
    assert "remainder" not in payload["config"]
