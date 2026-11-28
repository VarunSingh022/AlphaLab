"""An NDF: dealt with no currency moved, fixed at a supplied rate (v4.3.0, ADR-0067).

* **Dealt.** Only the commission moves, in the quote currency. The deal records
  its notional, rate, fixing date and settlement currency, and stays pending
  until it is fixed, past its value date too.
* **Fixed.** A buyer of 1,000,000 USD against INR at 83.50, fixed at 84.00, is
  owed 1,000,000 x 0.50 = 500,000 INR. Paid in dollars that is 500,000 / 84 =
  5,952.38; a seller owes it. Paid in the quote, the difference is the amount,
  rounded once, half-even: 1.225 is 1.22. It is realized in the settlement
  currency and pending to the value date.
* **Recorded.** The ledger rebuilds the same index, the deal fixed, and the
  book reads back equal from its snapshot.
* **Refused**: a deal fixed twice; a transaction that is no NDF's deal; a
  fixing that is not a positive rate; a settlement currency of neither; no
  notional; no rate; a commission the quote's cash cannot pay.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import NdfDealt, NdfFixed
from alphalab.portfolio.exceptions import InsufficientFundsError, InvalidTransactionError
from alphalab.portfolio.settlement import (
    FX_DEAL,
    NDF_FIXES_ON,
    NDF_FIXING,
    NDF_NOTIONAL,
    NDF_RATE,
    NDF_SETTLES_IN,
    NDF_SOURCE,
    SETTLEMENT_CASH,
    pending_from,
)
from alphalab.portfolio.snapshot import capture, from_primitives, restore
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType

D = Decimal
PAIR = "USDINR-ASSET"
VALUE, FIXES = date(2025, 8, 7), date(2025, 8, 5)
TRADED, FIXED, SETTLES = 10.0, 50.0, 100.0
SOURCE = "the INR reference rate"


@pytest.fixture
def funded() -> PortfolioState:
    account = Account(account_id="ACC-NDF", base_currency="USD", name="NDFs", created_at=1.0)
    state = PortfolioEngine.apply_deposit(PortfolioState(account=account), D("2000000"), "USD", 1.0)
    return PortfolioEngine.apply_deposit(state, D("100000"), "INR", 1.0)


def dealt(
    state: PortfolioState,
    notional: str = "1000000",
    rate: str = "83.50",
    commission: str = "500",
    settles_in: str = "USD",
) -> PortfolioState:
    return PortfolioEngine.apply_ndf_deal(
        state,
        PAIR,
        D(notional),
        D(rate),
        D(commission),
        TRADED,
        "USD",
        "INR",
        settles_in,
        value_date=VALUE,
        settles_at=SETTLES,
        fixes_on=FIXES,
    )


def deal_of(state: PortfolioState) -> Transaction:
    return next(t for t in state.ledger.transactions if NDF_NOTIONAL in t.metadata)


def fixed(state: PortfolioState, fixing: str = "84.00") -> PortfolioState:
    return PortfolioEngine.fix_ndf(state, deal_of(state), D(fixing), SOURCE, 45.0, FIXED)


def test_a_deal_moves_no_currency_but_its_commission(funded: PortfolioState) -> None:
    state = dealt(funded)
    assert state.cash.balance("USD") == D("2000000")
    assert state.cash.balance("INR") == D("100000") - D("500.00")
    assert state.positions == {} and state.realized_pnl.of("USD") == 0
    assert state.commission_paid.of("INR") == D("500.00")
    deal = deal_of(state)
    assert (deal.type, deal.quantity, deal.currency, deal.commission) == (
        TransactionType.FX_LEG,
        D("0"),
        "INR",
        D("500.00"),
    )
    assert deal.metadata[NDF_NOTIONAL] == "1000000" and deal.metadata[NDF_RATE] == "83.50"
    assert deal.metadata[NDF_FIXES_ON] == "2025-08-05"
    assert deal.metadata[NDF_SETTLES_IN] == "USD" and deal.metadata[FX_DEAL] == deal.transaction_id
    (event,) = [e for e in state.events if isinstance(e, NdfDealt)]
    assert (event.deal, event.notional, event.fixes_on, event.value_date) == (
        deal.transaction_id,
        D("1000000"),
        "2025-08-05",
        "2025-08-07",
    )


def test_a_deal_stays_pending_until_it_is_fixed_past_its_value_date_too(
    funded: PortfolioState,
) -> None:
    state = dealt(funded)
    (entry,) = state.unsettled
    assert entry.fixes_on == FIXES
    assert entry.pending_at(SETTLES + 1_000_000)
    assert pending_from(state.ledger.transactions) == state.unsettled


@pytest.mark.parametrize(
    ("notional", "owed"),
    [("1000000", D("5952.38")), ("-1000000", D("-5952.38"))],
    ids=["buyer-gains", "seller-owes"],
)
def test_a_fixing_settles_the_difference_in_dollars(
    funded: PortfolioState, notional: str, owed: Decimal
) -> None:
    state = fixed(dealt(funded, notional))
    # 1,000,000 x (84.00 - 83.50) = 500,000 INR; in dollars, / 84.00.
    assert state.cash.balance("USD") == D("2000000") + owed
    assert state.realized_pnl.of("USD") == owed
    leg = state.ledger.transactions.to_tuple()[-1]
    assert (leg.type, leg.currency, leg.quantity, leg.price) == (
        TransactionType.FX_LEG,
        "USD",
        owed,
        D("84.00"),
    )
    assert leg.metadata[FX_DEAL] == deal_of(state).transaction_id
    assert leg.metadata[NDF_FIXING] == "84.00" and leg.metadata[NDF_SOURCE] == SOURCE
    assert leg.metadata[SETTLEMENT_CASH] == str(owed)
    (event,) = [e for e in state.events if isinstance(e, NdfFixed)]
    assert (event.amount, event.currency, event.source) == (owed, "USD", SOURCE)


def test_paid_in_the_quote_the_difference_is_the_amount_rounded_half_even(
    funded: PortfolioState,
) -> None:
    # 1,225 x (1.001 - 1.000) = 1.225 INR: half-even to the paisa is 1.22.
    state = fixed(dealt(funded, "1225", "1.000", "0", "INR"), "1.001")
    assert state.cash.balance("INR") == D("100000") + D("1.22")
    assert state.realized_pnl.of("INR") == D("1.22")


def test_the_difference_is_pending_to_the_value_date_and_the_deal_settles_then(
    funded: PortfolioState,
) -> None:
    state = fixed(dealt(funded))
    dollar = PortfolioEngine.cash_position(state, "USD", SETTLES - 1)
    assert dollar.unsettled_credits == D("5952.38") and dollar.settled == D("2000000")
    assert PortfolioEngine.cash_position(state, "USD", SETTLES).settled == D("2005952.38")
    # The deal is fixed: from now on it settles at its value date.
    assert all(entry.fixes_on is None for entry in state.unsettled)
    assert not any(entry.pending_at(SETTLES) for entry in state.unsettled)
    assert pending_from(state.ledger.transactions) == state.unsettled


def test_the_book_reads_back_equal_from_its_snapshot(funded: PortfolioState) -> None:
    for state in (dealt(funded), fixed(dealt(funded))):
        restored = restore(from_primitives(deserialize(serialize(capture(state)))))
        assert restored.cash == state.cash
        assert restored.ledger.transactions.to_tuple() == state.ledger.transactions.to_tuple()
        assert tuple(restored.events) == tuple(state.events)
        assert restored.unsettled == state.unsettled
        assert restored.realized_pnl == state.realized_pnl


def test_a_fixing_is_refused_for_what_it_cannot_fix(funded: PortfolioState) -> None:
    state = fixed(dealt(funded))
    with pytest.raises(InvalidTransactionError, match="awaiting its fixing"):
        PortfolioEngine.fix_ndf(state, deal_of(state), D("84"), SOURCE, 45.0, 60.0)
    deposit = funded.ledger.transactions.to_tuple()[0]
    with pytest.raises(InvalidTransactionError, match="awaiting its fixing"):
        PortfolioEngine.fix_ndf(funded, deposit, D("84"), SOURCE, 45.0, 60.0)
    open_ = dealt(funded)
    with pytest.raises(InvalidTransactionError, match="positive rate"):
        PortfolioEngine.fix_ndf(open_, deal_of(open_), D("0"), SOURCE, 45.0, 60.0)


@pytest.mark.parametrize(
    ("notional", "rate", "commission", "settles_in", "error", "reason"),
    [
        ("1000000", "83.50", "500", "EUR", InvalidTransactionError, "one of its two currencies"),
        ("0", "83.50", "500", "USD", InvalidTransactionError, "non-zero notional"),
        ("1000000", "0", "500", "USD", InvalidTransactionError, "a rate is positive"),
        ("1000000", "83.50", "200000", "USD", InsufficientFundsError, "Cannot withdraw"),
    ],
    ids=["settles-elsewhere", "no-notional", "no-rate", "commission-uncovered"],
)
def test_a_deal_that_cannot_be_booked_is_refused(
    funded: PortfolioState,
    notional: str,
    rate: str,
    commission: str,
    settles_in: str,
    error: type[Exception],
    reason: str,
) -> None:
    with pytest.raises(error, match=reason):
        dealt(funded, notional, rate, commission, settles_in)
