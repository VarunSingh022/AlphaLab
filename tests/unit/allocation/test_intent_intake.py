"""What allocation does with the intents it is handed (ledger ALC-004, v3.12).

Until v3.12 an intent equal to one already in the batch was dropped without a
trace, by a membership test quadratic in the batch, and *every* exception raised
while validating an intent -- a defect as much as a refusal -- was recorded as a
refused intent and swallowed. A duplicate is now refused on the record, and only
a validation refusal is a rejection.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.allocation import (
    AllocationConstraints,
    AllocationEngine,
    CapitalBudget,
    FixedQuantitySizing,
)
from alphalab.allocation.events import AllocationRejected
from alphalab.common.order_terms import OrderTerms
from alphalab.strategy.events import Intent

BUDGET = CapitalBudget(Decimal("1000000"), Decimal("1000000"))
PRICES = {"AAPL": Decimal("100")}
CONSTRAINTS = AllocationConstraints(allow_shorting=True, enforce_integer_quantities=False)


def _allocate(*intents: Intent) -> tuple[list[str], list[tuple[str, Decimal]]]:
    state = AllocationEngine.initialize(BUDGET)
    state, requests = AllocationEngine.allocate(
        state, intents, PRICES, FixedQuantitySizing(), CONSTRAINTS, 1.0
    )
    reasons = [event.reason for event in state.events if isinstance(event, AllocationRejected)]
    return reasons, [(request.side.value, request.quantity) for request in requests]


def test_a_duplicate_intent_is_counted_once_and_the_duplicate_is_recorded() -> None:
    intent = Intent("A", "AAPL", Decimal("10"), timestamp=1.0, metadata={"why": "signal"})

    reasons, requests = _allocate(
        intent, Intent("A", "AAPL", Decimal("10.0"), timestamp=1.0, metadata={"why": "signal"})
    )

    assert requests == [("buy", Decimal("10"))]
    (reason,) = reasons
    assert "stated the same intent twice" in reason and "AAPL" in reason


def test_intents_that_differ_in_anything_are_two_intents() -> None:
    first = Intent("A", "AAPL", Decimal("10"), timestamp=1.0)

    for other in (
        Intent("B", "AAPL", Decimal("10"), timestamp=1.0),
        Intent("A", "AAPL", Decimal("10"), timestamp=1.0, metadata={"why": "other"}),
        Intent("A", "AAPL", Decimal("10"), timestamp=1.0, terms=OrderTerms.limit(Decimal("99"))),
    ):
        reasons, requests = _allocate(first, other)
        assert reasons == []
        assert sum(quantity for _, quantity in requests) == Decimal("20")


def test_a_validation_refusal_is_recorded_and_the_batch_goes_on() -> None:
    reasons, requests = _allocate(
        Intent("A", "AAPL", Decimal("10"), strength=Decimal("2"), timestamp=1.0),
        Intent("A", "AAPL", Decimal("5"), timestamp=1.0),
    )

    assert requests == [("buy", Decimal("5"))]
    assert reasons == ["Intent strength must be between 0.0 and 1.0."]


def test_a_defect_raised_during_validation_is_not_recorded_as_a_refusal() -> None:
    broken = Intent("A", "AAPL", "10", timestamp=1.0)  # type: ignore[arg-type]

    with pytest.raises(AttributeError):
        _allocate(broken)


def test_a_large_batch_of_distinct_intents_is_taken_whole() -> None:
    intents = [Intent(f"S{i}", "AAPL", Decimal("1"), timestamp=1.0) for i in range(5_000)]

    reasons, requests = _allocate(*intents)

    assert reasons == []
    assert requests == [("buy", Decimal("5000"))]
