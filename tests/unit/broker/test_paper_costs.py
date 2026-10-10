"""A paper venue charges its cost model, and keeps its books right (BRK-008).

Until v3.10 every paper fill carried a commission of zero, so a paper run was
systematically cheaper than the live run it rehearsed. The venue now takes the
execution cost model -- the simulator's own authority -- with no default. The
same release corrected the venue's books: a sell's commission was added to cash,
and a short position had no average price and realized nothing when covered.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.broker import (
    BrokerAdapter,
    BrokerEngine,
    BrokerOrderType,
    BrokerState,
    BrokerValidationError,
    PaperBroker,
    account_snapshot,
    executions,
    positions,
)
from alphalab.broker.paper import position_after_fill
from alphalab.broker.position import BrokerPosition
from alphalab.core.enums import Side
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    FREE,
    ExecutionCostModel,
    FixedHalfSpread,
    NoImpact,
    NoSlippage,
    NoSpread,
    NoTax,
    PerTradeFee,
    ProportionalTax,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class _Order:
    order_id: str
    asset_id: str
    side: str
    quantity: str
    price: str


def _state() -> BrokerState:
    return BrokerEngine.initialize("PAPER-1", Decimal("100000.00"), "USD")


def _fill(broker: PaperBroker, state: BrokerState, side: str, quantity: str, price: str, n: int):  # type: ignore[no-untyped-def]
    order = BrokerAdapter.to_broker_order(
        _Order(f"OMS-{n}", "AAPL", side, quantity, price), f"B-{n}", BrokerOrderType.MARKET, 1.0
    )
    return broker.submit_order(state, order, float(n))[0]


CHARGED = ExecutionCostModel(
    spread_model=NoSpread(),
    slippage_model=NoSlippage(),
    impact_model=NoImpact(),
    commission_model=FixedCommission(Decimal("1.50")),
    fee_model=PerTradeFee(Decimal("0.25")),
    tax_model=ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
)


def test_the_cost_model_is_required() -> None:
    with pytest.raises(TypeError):
        PaperBroker()  # type: ignore[call-arg]
    with pytest.raises(BrokerValidationError, match="ExecutionCostModel"):
        PaperBroker("free")  # type: ignore[arg-type]


def test_a_free_venue_charges_nothing_and_says_so() -> None:
    broker = PaperBroker(FREE)
    state = _fill(broker, _state(), "BUY", "100", "150.00", 1)
    assert executions(state)[0].commission == ZERO
    assert account_snapshot(state).cash == Decimal("85000.00")
    assert broker.cost_model is FREE


def test_cash_charges_leave_the_account_on_both_sides() -> None:
    broker = PaperBroker(CHARGED)
    bought = _fill(broker, _state(), "BUY", "100", "150.00", 1)
    # 100 x 150 = 15000; commission 1.50 + fee 0.25 + stamp tax 0.5% of 15000 = 75
    assert executions(bought)[0].commission == Decimal("76.75")
    assert account_snapshot(bought).cash == Decimal("100000.00") - Decimal("15000.00") - Decimal(
        "76.75"
    )
    sold = _fill(broker, bought, "SELL", "50", "160.00", 2)
    # the tax falls on purchases only: 1.50 + 0.25
    sell_charge = next(e for e in executions(sold) if e.broker_order_id == "B-2").commission
    assert sell_charge == Decimal("1.75")
    assert account_snapshot(sold).cash == account_snapshot(bought).cash + Decimal(
        "8000.00"
    ) - Decimal("1.75")


def test_price_concessions_move_the_paper_fill_price() -> None:
    spread = ExecutionCostModel(
        spread_model=FixedHalfSpread(Decimal("0.05")),
        slippage_model=NoSlippage(),
        impact_model=NoImpact(),
        commission_model=FixedCommission(Decimal("0")),
        fee_model=PerTradeFee(Decimal("0")),
        tax_model=NoTax(),
    )
    broker = PaperBroker(spread)
    bought = _fill(broker, _state(), "BUY", "10", "100.00", 1)
    assert executions(bought)[0].fill_price == Decimal("100.05")
    sold = _fill(broker, bought, "SELL", "10", "101.00", 2)
    assert next(e for e in executions(sold) if e.broker_order_id == "B-2").fill_price == Decimal(
        "100.95"
    )


# --------------------------------------------------------------------------- #
# The venue's books
# --------------------------------------------------------------------------- #


def _flat() -> BrokerPosition:
    return BrokerPosition("AAPL", ZERO, ZERO, ZERO, ZERO, ZERO)


def test_a_short_carries_its_entry_price_and_realizes_when_covered() -> None:
    short = position_after_fill(_flat(), Side.SELL, Decimal("10"), Decimal("50"))
    assert (short.quantity, short.average_price) == (Decimal("-10"), Decimal("50"))
    covered = position_after_fill(short, Side.BUY, Decimal("4"), Decimal("45"))
    # a short gains when the price falls: 4 x (50 - 45)
    assert covered.realized_pnl == Decimal("20")
    assert (covered.quantity, covered.average_price) == (Decimal("-6"), Decimal("50"))


def test_a_sell_through_zero_realizes_only_what_closed_and_opens_the_rest() -> None:
    long = position_after_fill(_flat(), Side.BUY, Decimal("10"), Decimal("100"))
    crossed = position_after_fill(long, Side.SELL, Decimal("15"), Decimal("110"))
    assert crossed.realized_pnl == Decimal("100")  # 10 x (110 - 100), not 15 x 10
    assert (crossed.quantity, crossed.average_price) == (Decimal("-5"), Decimal("110"))


def test_adding_to_a_position_averages_its_cost() -> None:
    first = position_after_fill(_flat(), Side.BUY, Decimal("10"), Decimal("100"))
    second = position_after_fill(first, Side.BUY, Decimal("30"), Decimal("104"))
    assert second.average_price == Decimal("103")
    flat = position_after_fill(second, Side.SELL, Decimal("40"), Decimal("103"))
    assert (flat.quantity, flat.average_price, flat.realized_pnl) == (ZERO, ZERO, ZERO)


def test_positions_through_the_venue_match_the_book_function() -> None:
    broker = PaperBroker(FREE)
    state = _fill(broker, _state(), "SELL", "10", "50.00", 1)
    state = _fill(broker, state, "BUY", "4", "45.00", 2)
    (position,) = positions(state)
    assert position.quantity == Decimal("-6")
    assert position.realized_pnl == Decimal("20")
