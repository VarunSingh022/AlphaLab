"""An FX deal, booked as the two currencies it exchanges (v4.3.0, ADR-0067).

* **Two currencies, no position.** Buying ``q`` at ``p`` credits ``q`` of the
  base and debits ``q x p`` of the quote, with the commission; selling
  reverses it. Nothing is opened, closed or realized.
* **Rounded once, half-even**, each amount to its currency's minor unit: a yen
  amount has none.
* **Pending to the value date.** Both legs move trade-date cash at the deal and
  are unsettled until the value date's instant: the base's arrival is no cash to
  spend yet, and the quote's payment is committed.
* **Recorded in the ledger.** Each leg is an ``FX_LEG`` transaction naming the
  deal and its value date. The pending index the ledger implies is the one
  kept, and the book reads back equal from its snapshot.
* **Per currency**, cash is what was deposited, plus what was exchanged, less
  the commission.
* **Refused**, leaving the book as it was: a debit cash does not cover, one
  currency for itself, no quantity, no positive rate, a leg that rounds to
  nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import FxDealBooked
from alphalab.portfolio.exceptions import InsufficientFundsError, InvalidTransactionError
from alphalab.portfolio.settlement import (
    FX_DEAL,
    SETTLEMENT_CASH,
    SETTLES_AT,
    VALUE_DATE,
    pending_from,
)
from alphalab.portfolio.snapshot import capture, from_primitives, restore
from alphalab.portfolio.types import TransactionType

D = Decimal
PAIR = "EURUSD-ASSET"
VALUE = date(2025, 7, 7)
TRADED, SETTLES = 10.0, 100.0


@pytest.fixture
def funded() -> PortfolioState:
    account = Account(account_id="ACC-FX", base_currency="USD", name="FX deals", created_at=1.0)
    state = PortfolioEngine.apply_deposit(PortfolioState(account=account), D("2000000"), "USD", 1.0)
    return PortfolioEngine.apply_deposit(state, D("200000000"), "JPY", 1.0)


def deal(
    state: PortfolioState,
    quantity: str,
    price: str,
    commission: str = "25",
    *,
    base: str = "EUR",
    quote: str = "USD",
    at: float = TRADED,
    settles_at: float = SETTLES,
) -> PortfolioState:
    return PortfolioEngine.apply_fx_deal(
        state,
        PAIR,
        D(quantity),
        D(price),
        D(commission),
        at,
        base,
        quote,
        value_date=VALUE,
        settles_at=settles_at,
    )


def test_buying_credits_the_base_and_debits_the_quote_with_its_commission(
    funded: PortfolioState,
) -> None:
    state = deal(funded, "1000000", "1.0845")

    assert state.cash.balance("EUR") == D("1000000.00")
    assert state.cash.balance("USD") == D("2000000") - D("1084500.00") - D("25.00")
    assert state.positions == {} and state.pending_marks == ()
    assert state.realized_pnl.of("USD") == 0 and state.realized_pnl.of("EUR") == 0
    assert state.commission_paid.of("USD") == D("25.00")
    (event,) = state.events[len(funded.events) :]
    assert event == FxDealBooked(
        TRADED,
        "ACC-FX",
        PAIR,
        "EUR",
        D("1000000.00"),
        "USD",
        D("-1084500.00"),
        D("1.0845"),
        D("25.00"),
        "2025-07-07",
        SETTLES,
    )


def test_selling_reverses_both_legs(funded: PortfolioState) -> None:
    bought = deal(funded, "1000000", "1.0845")
    sold = deal(bought, "-1000000", "1.0900", at=20.0, settles_at=200.0)

    assert sold.cash.balance("EUR") == D("0.00")
    assert sold.cash.balance("USD") == (
        D("2000000") - D("1084500.00") - D("25.00") + D("1090000.00") - D("25.00")
    )
    assert sold.positions == {} and sold.realized_pnl.of("USD") == 0


def test_each_amount_is_rounded_once_half_even_to_its_own_minor_unit(
    funded: PortfolioState,
) -> None:
    # 1,000,000.005 EUR rounds half-even to 1,000,000.00; at 161.2345 it is
    # 161,234,500.806... JPY, which has no minor unit: 161,234,501.
    state = deal(funded, "1000000.005", "161.2345", "0", quote="JPY")
    assert state.cash.balance("EUR") == D("1000000.00")
    assert state.cash.balance("JPY") == D("200000000") - D("161234501")


def test_both_legs_are_pending_until_the_value_date(funded: PortfolioState) -> None:
    state = deal(funded, "1000000", "1.0845")

    euro = PortfolioEngine.cash_position(state, "EUR", SETTLES - 1)
    dollar = PortfolioEngine.cash_position(state, "USD", SETTLES - 1)
    # The euros have not arrived: none is spendable or settled.
    assert (euro.trade_date, euro.unsettled_credits, euro.settled, euro.available) == (
        D("1000000.00"),
        D("1000000.00"),
        D("0.00"),
        D("0.00"),
    )
    # The dollars are committed: spent from what is available, not yet from the bank.
    assert dollar.unsettled_debits == D("1084525.00")
    assert dollar.settled == D("2000000")
    assert dollar.available == D("2000000") - D("1084525.00")
    # At the value date's instant both have settled.
    after = PortfolioEngine.cash_position(state, "EUR", SETTLES)
    assert after.settled == after.available == D("1000000.00")
    assert PortfolioEngine.cash_position(state, "USD", SETTLES).settled == D("915475.00")


def test_each_leg_is_a_ledger_transaction_and_the_index_follows_from_the_ledger(
    funded: PortfolioState,
) -> None:
    state = deal(funded, "1000000", "1.0845")
    base, quote = state.ledger.transactions.to_tuple()[-2:]

    assert (base.type, quote.type) == (TransactionType.FX_LEG, TransactionType.FX_LEG)
    assert (base.currency, base.quantity, base.commission) == ("EUR", D("1000000.00"), D("0"))
    assert (quote.currency, quote.quantity, quote.commission) == (
        "USD",
        D("-1084500.00"),
        D("25"),
    )
    for leg in (base, quote):
        assert leg.asset_id == PAIR and leg.price == D("1.0845")
        assert leg.metadata[SETTLES_AT] == SETTLES
        assert leg.metadata[VALUE_DATE] == "2025-07-07"
        assert leg.metadata[FX_DEAL] == base.transaction_id
    assert (base.metadata[SETTLEMENT_CASH], quote.metadata[SETTLEMENT_CASH]) == (
        "1000000.00",
        "-1084525.00",
    )
    assert pending_from(state.ledger.transactions) == state.unsettled
    assert len(state.unsettled) == 2


def test_the_book_reads_back_equal_from_its_snapshot(funded: PortfolioState) -> None:
    state = deal(deal(funded, "1000000", "1.0845"), "-400000", "1.0900", at=20.0)
    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    assert restored.cash == state.cash
    assert restored.ledger.transactions.to_tuple() == state.ledger.transactions.to_tuple()
    assert tuple(restored.events) == tuple(state.events)
    assert restored.unsettled == state.unsettled
    assert restored.commission_paid == state.commission_paid


def test_per_currency_cash_is_deposits_plus_exchanged_less_commission(
    funded: PortfolioState,
) -> None:
    state = deal(deal(funded, "1000000", "1.0845"), "-250000", "1.0911", "12.5", at=20.0)
    state = deal(state, "300000", "161.2345", "0", quote="JPY", at=30.0)
    transactions = state.ledger.transactions.to_tuple()
    for currency in ("USD", "EUR", "JPY"):
        deposited = sum(
            (
                t.quantity
                for t in transactions
                if t.type is TransactionType.DEPOSIT and t.currency == currency
            ),
            D("0"),
        )
        exchanged = sum(
            (
                t.quantity
                for t in transactions
                if t.type is TransactionType.FX_LEG and t.currency == currency
            ),
            D("0"),
        )
        assert state.cash.balance(currency) == (
            deposited + exchanged - state.commission_paid.of(currency)
        )


@pytest.mark.parametrize(
    ("quantity", "price", "base", "quote", "error", "reason"),
    [
        ("10000000", "1.08", "EUR", "USD", InsufficientFundsError, "Cannot withdraw"),
        ("-1000", "1.08", "EUR", "USD", InsufficientFundsError, "Cannot withdraw"),
        ("1000", "1.08", "USD", "USD", InvalidTransactionError, "two currencies"),
        ("0", "1.08", "EUR", "USD", InvalidTransactionError, "non-zero quantity"),
        ("1000", "0", "EUR", "USD", InvalidTransactionError, "a rate is positive"),
        ("0.004", "1.08", "EUR", "USD", InvalidTransactionError, "rounds to nothing"),
    ],
    ids=["quote-uncovered", "base-not-held", "one-currency", "zero", "rate", "rounds-away"],
)
def test_a_deal_that_cannot_be_booked_is_refused_and_moves_nothing(
    funded: PortfolioState,
    quantity: str,
    price: str,
    base: str,
    quote: str,
    error: type[Exception],
    reason: str,
) -> None:
    with pytest.raises(error, match=reason):
        deal(funded, quantity, price, base=base, quote=quote)
    assert funded.cash.balance("USD") == D("2000000") and funded.unsettled == ()
