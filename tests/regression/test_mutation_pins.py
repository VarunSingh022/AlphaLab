"""Pins for the defects the suite let through (ledger TST-005).

The pre-v4 audit injected defects into the code and ran the whole suite against
each (the master audit, section W). Every injected defect the suite did not
fail on is a behaviour nothing asserted. Each one is pinned here by a test that
fails with the defect in place, named after the mutation it answers.
"""

from __future__ import annotations

from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.risk import OrderSizeLimit, RiskEngine
from tests.regression.test_risk_projection import _book, _limits, _order, _rules

# --------------------------------------------------------------------------- #
# M01: the order-size limit is inclusive
# --------------------------------------------------------------------------- #
#
# ``request.quantity > limit.max_quantity`` mutated to ``>=`` refused an order of
# exactly the permitted size, and the whole suite passed: every order-size test
# was comfortably inside or outside the limit. A limit states the largest order
# allowed, so an order *at* it is allowed.


def test_an_order_of_exactly_the_permitted_quantity_is_approved() -> None:
    limits = _limits(order_size=OrderSizeLimit(Decimal("10"), Decimal("1000000")))
    state = _book(limits, cash="1000000", nav="1000000")

    _, at_limit = RiskEngine.evaluate(state, _order(Side.BUY, "10"), 1.0)
    _, above = RiskEngine.evaluate(state, _order(Side.BUY, "10.000001"), 1.0)

    assert at_limit.approved, at_limit.violations
    assert "OrderSizeQuantity" in _rules(above)


def test_an_order_of_exactly_the_permitted_notional_is_approved() -> None:
    limits = _limits(order_size=OrderSizeLimit(Decimal("1000000"), Decimal("1000")))
    state = _book(limits, cash="1000000", nav="1000000")

    _, at_limit = RiskEngine.evaluate(state, _order(Side.BUY, "10", price="100"), 1.0)
    _, above = RiskEngine.evaluate(state, _order(Side.BUY, "10", price="100.01"), 1.0)

    assert at_limit.approved, at_limit.violations
    assert "OrderSizeNotional" in _rules(above)
