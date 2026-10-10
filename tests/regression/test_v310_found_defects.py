"""Defects found while v3.10 was being built, each pinned by its behaviour.

The pre-v4 ledger records what its audit found; building v3.10 found more, and
every one was fixed in this release (ledger ACC-012, ACC-013, ACC-014,
NUM-009). Each test here fails against v3.9.0.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.statistics import (
    linear_regression,
    mean,
    median,
    percentile,
    sample_variance,
)
from alphalab.core.enums import Side
from alphalab.execution.costs import CostContext, ExecutionCostModel
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.exceptions import InvalidTransactionError

ACCOUNT = Account(account_id="ACC", base_currency="USD", name="Fund", created_at=0.0)


def _funded() -> PortfolioState:
    return PortfolioEngine.apply_deposit(
        PortfolioState(account=ACCOUNT), Decimal("100000"), "USD", 1.0
    )


# --------------------------------------------------------------------------- #
# ACC-012: a deposit or withdrawal moves a positive amount of money
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("amount", ["0", "-5", "0.001"])
def test_a_deposit_or_withdrawal_that_moves_no_money_is_refused(amount: str) -> None:
    """``-5`` was a withdrawal booked as a deposit; ``0.001`` rounds to nothing."""

    state = _funded()
    with pytest.raises(InvalidTransactionError, match="positive amount"):
        PortfolioEngine.apply_deposit(state, Decimal(amount), "USD", 2.0)
    with pytest.raises(InvalidTransactionError, match="positive amount"):
        PortfolioEngine.apply_withdrawal(state, Decimal(amount), "USD", 2.0)


# --------------------------------------------------------------------------- #
# ACC-013: one position has one currency
# --------------------------------------------------------------------------- #


def test_a_fill_in_another_currency_is_not_booked_into_an_open_position() -> None:
    """v3.9 added a EUR fill's quantity and basis to a USD position."""

    state = PortfolioEngine.apply_fill(
        _funded(), "X", Decimal("10"), Decimal("100"), Decimal("0"), 2.0, "USD"
    )

    with pytest.raises(InvalidTransactionError, match="held in USD"):
        PortfolioEngine.apply_fill(
            state, "X", Decimal("5"), Decimal("90"), Decimal("0"), 3.0, "EUR"
        )


# --------------------------------------------------------------------------- #
# ACC-014: no floor under a sale's price
# --------------------------------------------------------------------------- #


def _sale(price: str) -> CostContext:
    return CostContext(
        asset_id="X",
        side=Side.SELL,
        quantity=Decimal("1000000"),
        reference_price=Decimal(price),
        currency="USD",
        venue="SIM",
        timestamp=1.0,
    )


def test_a_sub_penny_instrument_is_sold_at_its_price_not_at_a_cent() -> None:
    """v3.9 floored every sale at 0.01 -- above a 0.004 market, with no concession."""

    assert ExecutionCostModel.fill_price_from(_sale("0.004"), Decimal("0.0001")) == Decimal(
        "0.0039"
    )


def test_a_concession_that_takes_a_sale_to_zero_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="at or below zero"):
        ExecutionCostModel.fill_price_from(_sale("0.004"), Decimal("0.004"))


# --------------------------------------------------------------------------- #
# NUM-009: near the float limit, a representable answer or a named refusal
# --------------------------------------------------------------------------- #
#
# v3.9 raised a bare ``OverflowError`` from some statistics and, from others,
# returned infinity for a question with a finite answer: the mean, the median
# and a percentile of values near ``1e308`` overflowed in an intermediate sum
# or difference. Every ordinary input keeps its exact float.


def test_a_finite_answer_near_the_float_limit_is_returned() -> None:
    assert mean([1e308, 1e308]) == 1e308
    assert median([1e308, 1e308]) == 1e308
    assert percentile([-1e308, 1e308], 0.5) == 0.0
    assert sample_variance([1e308, 1e308, 1e308]) == 0.0


def test_an_answer_beyond_the_float_limit_is_refused_by_name() -> None:
    with pytest.raises(AlphaLabValidationError, match="float range"):
        sample_variance([1e308, -1e308, 1e308])
    with pytest.raises(AlphaLabValidationError, match="float range"):
        linear_regression([1e308, -1e308, 1e308], [1.0, 2.0, 3.0])


def test_ordinary_inputs_keep_their_exact_floats() -> None:
    values = [0.1, 0.2, 0.3, 0.4]

    assert mean(values) == sum(values) / len(values)
    assert median(values) == (0.2 + 0.3) / 2.0
    assert percentile(values, 0.25) == 0.1 + (0.2 - 0.1) * 0.75
