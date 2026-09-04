"""Portfolio accounting invariants and mark-to-market (v2.1).

These tests pin the accounting model down at the engine level:

* cash moves only by trade proceeds/cost and commission,
* realized P&L accrues only on reductions and closes, and survives a close,
* unrealized P&L is derived from the current mark and nothing else,
* commissions are charged exactly once and stay out of the cost basis,
* the accounting identity holds after every operation.
"""

from decimal import Decimal

import pytest

from alphalab.portfolio import (
    Account,
    InvalidTransactionError,
    MarketValueUpdated,
    PnLEngine,
    PortfolioEngine,
    PortfolioState,
    PortfolioValuation,
    PositionClosed,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
)

INITIAL = Decimal("100000.00")


@pytest.fixture
def funded() -> PortfolioState:
    account = Account(account_id="ACC-INV", base_currency="USD", name="Invariants", created_at=1.0)
    return PortfolioEngine.apply_deposit(PortfolioState(account=account), INITIAL, "USD", 1.0)


def assert_identity(state: PortfolioState, deposits: Decimal = INITIAL) -> None:
    """equity == deposits + realized P&L + unrealized P&L - commissions."""

    valuation = PortfolioValuation.snapshot(state, 0.0)
    expected = (
        deposits + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    ).quantize(Decimal("0.01"))
    assert valuation.equity == expected


# ---------------------------------------------------------------------------
# Cash, realized P&L and commissions
# ---------------------------------------------------------------------------


def test_opening_a_long_moves_cash_by_cost_plus_commission_only(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("2.50"), 2.0
    )

    assert state.cash.balance("USD") == INITIAL - Decimal("1002.50")
    assert state.realized_pnl == Decimal("0.00")
    assert state.commission_paid == Decimal("2.50")
    assert state.positions["AAPL"].average_cost == Decimal("100.0000")
    assert isinstance(state.events[-1], PositionOpened)
    assert_identity(state)


def test_increasing_a_long_averages_cost_and_realizes_nothing(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("30"), Decimal("120.00"), Decimal("0.00"), 3.0
    )

    position = state.positions["AAPL"]
    assert position.quantity == Decimal("40.000000")
    assert position.average_cost == Decimal("115.0000")  # (10*100 + 30*120) / 40
    assert state.realized_pnl == Decimal("0.00")
    assert isinstance(state.events[-1], PositionIncreased)
    assert_identity(state)


def test_reducing_a_long_realizes_only_the_closed_portion(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-4"), Decimal("130.00"), Decimal("0.00"), 3.0
    )

    position = state.positions["AAPL"]
    assert position.quantity == Decimal("6.000000")
    assert position.average_cost == Decimal("100.0000")
    assert state.realized_pnl == Decimal("120.00")
    event = state.events[-1]
    assert isinstance(event, PositionReduced)
    assert event.realized_pnl == Decimal("120.00")
    assert_identity(state)


def test_realized_pnl_survives_the_position_being_closed_and_dropped(
    funded: PortfolioState,
) -> None:
    """Closing removes the position; the account's realized P&L must remain."""

    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-10"), Decimal("140.00"), Decimal("0.00"), 3.0
    )

    assert state.positions == {}
    assert PnLEngine.realized_pnl(state.positions) == Decimal("0.00")  # no open positions left
    assert state.realized_pnl == Decimal("400.00")  # ... but the account keeps the P&L
    assert state.cash.balance("USD") == INITIAL + Decimal("400.00")
    event = state.events[-1]
    assert isinstance(event, PositionClosed)
    assert event.realized_pnl == Decimal("400.00")
    assert_identity(state)


def test_realized_pnl_accumulates_across_several_round_trips(funded: PortfolioState) -> None:
    state = funded
    round_trips = (
        (Decimal("100.00"), Decimal("110.00")),
        (Decimal("50.00"), Decimal("45.00")),
    )
    for entry, exit_price in round_trips:
        state = PortfolioEngine.apply_fill(
            state, "AAPL", Decimal("10"), entry, Decimal("0.00"), 2.0
        )
        state = PortfolioEngine.apply_fill(
            state, "AAPL", Decimal("-10"), exit_price, Decimal("0.00"), 3.0
        )

    assert state.realized_pnl == Decimal("50.00")  # +100 then -50
    assert state.cash.balance("USD") == INITIAL + Decimal("50.00")
    assert_identity(state)


def test_short_round_trip_credits_then_debits_cash(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "TSLA", Decimal("-10"), Decimal("200.00"), Decimal("1.00"), 2.0
    )
    assert state.positions["TSLA"].quantity == Decimal("-10.000000")
    assert state.cash.balance("USD") == INITIAL + Decimal("1999.00")
    assert_identity(state)

    state = PortfolioEngine.apply_fill(
        state, "TSLA", Decimal("10"), Decimal("180.00"), Decimal("1.00"), 3.0
    )
    assert state.positions == {}
    assert state.realized_pnl == Decimal("200.00")
    assert state.commission_paid == Decimal("2.00")
    assert state.cash.balance("USD") == INITIAL + Decimal("198.00")
    assert_identity(state)


def test_reversing_a_long_into_a_short_realizes_the_old_leg_only(
    funded: PortfolioState,
) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "AAPL", Decimal("-25"), Decimal("120.00"), Decimal("0.00"), 3.0
    )

    position = state.positions["AAPL"]
    assert position.quantity == Decimal("-15.000000")
    assert position.average_cost == Decimal("120.0000")  # the new short leg's basis
    assert state.realized_pnl == Decimal("200.00")  # only the 10 long shares realized
    assert_identity(state)


def test_commission_is_counted_exactly_once_per_fill(funded: PortfolioState) -> None:
    state = funded
    for i in range(4):
        state = PortfolioEngine.apply_fill(
            state, "AAPL", Decimal("1"), Decimal("10.00"), Decimal("0.25"), 2.0 + i
        )

    assert state.commission_paid == Decimal("1.00")
    assert state.cash.balance("USD") == INITIAL - Decimal("40.00") - Decimal("1.00")
    assert sum((t.commission for t in state.ledger.by_asset("AAPL")), Decimal("0.00")) == Decimal(
        "1.00"
    )
    assert_identity(state)


def test_each_fill_produces_exactly_one_event_and_one_transaction(
    funded: PortfolioState,
) -> None:
    events_before = len(funded.events)
    ledger_before = len(funded.ledger.history())

    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("5"), Decimal("20.00"), Decimal("0.10"), 2.0
    )

    assert len(state.events) == events_before + 1
    assert len(state.ledger.history()) == ledger_before + 1


# ---------------------------------------------------------------------------
# Rejected fills
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "price", "commission", "match"),
    [
        (Decimal("0"), Decimal("10.00"), Decimal("0.00"), "non-zero quantity"),
        (Decimal("1"), Decimal("0"), Decimal("0.00"), "positive price"),
        (Decimal("1"), Decimal("-5.00"), Decimal("0.00"), "positive price"),
        (Decimal("1"), Decimal("10.00"), Decimal("-1.00"), "cannot be negative"),
    ],
)
def test_malformed_fills_are_rejected(
    funded: PortfolioState,
    quantity: Decimal,
    price: Decimal,
    commission: Decimal,
    match: str,
) -> None:
    with pytest.raises(InvalidTransactionError, match=match):
        PortfolioEngine.apply_fill(funded, "AAPL", quantity, price, commission, 2.0)


# ---------------------------------------------------------------------------
# Mark to market
# ---------------------------------------------------------------------------


def test_marking_moves_unrealized_pnl_and_nothing_else(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("1.00"), 2.0
    )
    cash_before = state.cash.balance("USD")
    ledger_before = len(state.ledger.history())

    marked = PortfolioEngine.update_market_prices(state, {"AAPL": Decimal("150.00")}, 3.0)

    assert marked.positions["AAPL"].unrealized_pnl == Decimal("500.00")
    assert marked.positions["AAPL"].average_cost == Decimal("100.0000")
    assert marked.cash.balance("USD") == cash_before
    assert marked.realized_pnl == Decimal("0.00")
    assert marked.commission_paid == Decimal("1.00")
    assert len(marked.ledger.history()) == ledger_before
    assert isinstance(marked.events[-1], MarketValueUpdated)
    assert_identity(marked)


def test_marking_a_short_position_inverts_the_sign_of_the_move(
    funded: PortfolioState,
) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "TSLA", Decimal("-10"), Decimal("200.00"), Decimal("0.00"), 2.0
    )

    gain = PortfolioEngine.update_market_prices(state, {"TSLA": Decimal("180.00")}, 3.0)
    assert gain.positions["TSLA"].unrealized_pnl == Decimal("200.00")
    assert gain.positions["TSLA"].market_value == Decimal("-1800.00")
    assert_identity(gain)

    loss = PortfolioEngine.update_market_prices(state, {"TSLA": Decimal("230.00")}, 3.0)
    assert loss.positions["TSLA"].unrealized_pnl == Decimal("-300.00")
    assert_identity(loss)


def test_marking_is_deterministic_and_idempotent(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )

    once = PortfolioEngine.update_market_prices(state, {"AAPL": Decimal("140.00")}, 3.0)
    twice = PortfolioEngine.update_market_prices(once, {"AAPL": Decimal("140.00")}, 4.0)

    assert PortfolioValuation.snapshot(once, 0.0).equity == (
        PortfolioValuation.snapshot(twice, 0.0).equity
    )
    assert once.positions["AAPL"].market_price == twice.positions["AAPL"].market_price


def test_prices_for_unheld_or_invalid_assets_are_ignored(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    events_before = len(state.events)

    marked = PortfolioEngine.update_market_prices(
        state, {"MSFT": Decimal("300.00"), "AAPL": Decimal("0.00")}, 3.0
    )

    assert marked is state  # nothing was re-marked, so no new state and no event
    assert len(marked.events) == events_before
    assert "MSFT" not in marked.positions
    assert marked.positions["AAPL"].market_price == Decimal("100.0000")


def test_a_position_with_no_price_keeps_its_previous_mark(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "MSFT", Decimal("5"), Decimal("300.00"), Decimal("0.00"), 2.0
    )

    marked = PortfolioEngine.update_market_prices(state, {"AAPL": Decimal("120.00")}, 3.0)

    assert marked.positions["AAPL"].market_price == Decimal("120.0000")
    assert marked.positions["MSFT"].market_price == Decimal("300.0000")  # stale mark retained
    assert_identity(marked)


# ---------------------------------------------------------------------------
# Valuation snapshot
# ---------------------------------------------------------------------------


def test_valuation_separates_long_short_cash_and_equity(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.apply_fill(
        state, "TSLA", Decimal("-5"), Decimal("200.00"), Decimal("0.00"), 2.0
    )
    state = PortfolioEngine.update_market_prices(
        state, {"AAPL": Decimal("110.00"), "TSLA": Decimal("190.00")}, 3.0
    )

    valuation = PortfolioValuation.snapshot(state, 3.0)

    assert valuation.cash == INITIAL - Decimal("1000.00") + Decimal("1000.00")
    assert valuation.long_value == Decimal("1100.00")
    assert valuation.short_value == Decimal("-950.00")
    assert valuation.positions_value == Decimal("150.00")
    assert valuation.unrealized_pnl == Decimal("150.00")  # +100 long, +50 short
    assert valuation.realized_pnl == Decimal("0.00")
    assert valuation.equity == INITIAL + Decimal("150.00")
    assert valuation.currency == "USD"
    assert_identity(state)


def test_valuation_of_an_empty_portfolio_is_all_cash(funded: PortfolioState) -> None:
    valuation = PortfolioValuation.snapshot(funded, 1.0)

    assert valuation.equity == INITIAL
    assert valuation.cash == INITIAL
    assert valuation.positions_value == Decimal("0.00")
    assert valuation.unrealized_pnl == Decimal("0.00")
    assert valuation.realized_pnl == Decimal("0.00")
    assert valuation.commission_paid == Decimal("0.00")


def test_withdrawal_is_reflected_in_the_identity(funded: PortfolioState) -> None:
    state = PortfolioEngine.apply_fill(
        funded, "AAPL", Decimal("10"), Decimal("100.00"), Decimal("1.00"), 2.0
    )
    state = PortfolioEngine.apply_withdrawal(state, Decimal("5000.00"), "USD", 3.0)

    assert_identity(state, deposits=INITIAL - Decimal("5000.00"))
