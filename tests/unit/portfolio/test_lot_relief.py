"""Lot relief: each method's gains on one record, by hand (v4.3, GAP-50).

One holding, six fills, each figure worked by hand below:

* day 1, buy 1,000 at 4.80 (4,800.00); day 10, buy 500 at 4.00 (2,000.00);
* day 20, sell 700 at 5.00 (3,500.00), then buy 200 at 5.10 (1,020.00);
* day 25, buy 300 at 4.90 (1,470.00); day 60, sell 600 at 5.50 (3,300.00).

=============  =========  ==================
Method         Realized   Open: 700 units at
=============  =========  ==================
Average cost      803.59            3,293.59
FIFO              800.00            3,290.00
LIFO              870.00            3,360.00
Highest cost      470.00            2,960.00
Lowest cost       960.00            3,450.00
Section 104       683.33            3,173.33
=============  =========  ==================

Average cost is the book's own: the same gain at every fill, to the cent.
However the gains are split, what was realized plus what is still held is the
same: realized less open cost is -2,490.00 under every method. Under Section
104 the day-20 sale is matched with the 200 bought later that day, then the
300 bought on day 25, then 200 from the pool at 6,800 x 200 / 1,500 = 906.67;
the day-60 sale takes 600 of the pool's 1,300 at 5,893.33, 2,720.00, and is
provisional: a later acquisition within 30 days could still be matched.
Shorts, a reversal and a split are relieved the same way, and what lot relief
cannot read is refused.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.evolve import evolve
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.portfolio.account import Account
from alphalab.portfolio.corporate_actions import Split
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.events import PositionClosed, PositionExchanged, PositionReduced
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.lots import LotRelief, relieve_lots

D = Decimal
ACME = "ACME"
DAY = 86_400.0
START = date(2025, 1, 1)


def day_of(at: float) -> date:
    return START + timedelta(days=int(at // DAY))


def fills(*steps: tuple[float, str, str]) -> PortfolioState:
    account = Account(account_id="ACC-LOTS", base_currency="USD", name="Lots", created_at=0.0)
    state = PortfolioEngine.apply_deposit(PortfolioState(account=account), D("100000"), "USD", 0.0)
    for at, quantity, price in steps:
        state = PortfolioEngine.apply_fill(state, ACME, D(quantity), D(price), D("0"), at, "USD")
    return state


BOOK = fills(
    (1 * DAY, "1000", "4.80"),
    (10 * DAY, "500", "4.00"),
    (20 * DAY + 1, "-700", "5.00"),
    (20 * DAY + 2, "200", "5.10"),
    (25 * DAY, "300", "4.90"),
    (60 * DAY, "-600", "5.50"),
)


@pytest.mark.parametrize(
    ("method", "gains", "open_cost"),
    [
        (LotRelief.AVERAGE_COST, ["326.67", "476.92"], "3293.59"),
        (LotRelief.FIFO, ["140.00", "210.00", "450.00"], "3290.00"),
        (LotRelief.LIFO, ["500.00", "40.00", "180.00", "80.00", "70.00"], "3360.00"),
        (LotRelief.HIGHEST_COST, ["140.00", "80.00", "180.00", "70.00"], "2960.00"),
        (LotRelief.LOWEST_COST, ["500.00", "40.00", "420.00"], "3450.00"),
        (LotRelief.SECTION_104, ["-20.00", "30.00", "93.33", "580.00"], "3173.33"),
    ],
    ids=lambda value: value.name.lower() if isinstance(value, LotRelief) else None,
)
def test_each_method_realizes_its_gains_by_hand(
    method: LotRelief, gains: list[str], open_cost: str
) -> None:
    report = relieve_lots(BOOK, ACME, method, day_of=day_of)
    assert [match.gain for match in report.matches] == [D(gain) for gain in gains]
    assert report.open_quantity == D("700") and report.open_cost == D(open_cost)
    # Realized plus what is held is the same, however it is split.
    assert report.realized - report.open_cost == D("-2490.00")


def test_average_cost_is_the_books_own_to_the_cent() -> None:
    report = relieve_lots(BOOK, ACME, LotRelief.AVERAGE_COST)
    assert report.realized == BOOK.realized_pnl.of("USD") == D("803.59")
    booked = [
        e.realized_pnl for e in BOOK.events if isinstance(e, PositionReduced | PositionClosed)
    ]
    assert [match.gain for match in report.matches] == booked
    position = BOOK.positions[ACME]
    assert (report.open_quantity, report.open_cost) == (position.quantity, position.basis)


def test_each_match_names_what_it_was_matched_against() -> None:
    fifo = relieve_lots(BOOK, ACME, LotRelief.FIFO)
    ids = [t.transaction_id for t in BOOK.ledger.transactions if t.asset_id == ACME]
    first, second = ids[0], ids[1]
    assert [(m.acquisitions, m.quantity, m.proceeds, m.cost) for m in fifo.matches] == [
        ((first,), D("700"), D("3500.00"), D("3360.00")),
        ((first,), D("300"), D("1650.00"), D("1440.00")),
        ((second,), D("300"), D("1650.00"), D("1200.00")),
    ]
    section = relieve_lots(BOOK, ACME, LotRelief.SECTION_104, day_of=day_of)
    assert [(m.rule, m.quantity, m.proceeds, m.cost) for m in section.matches] == [
        ("same day", D("200"), D("1000.00"), D("1020.00")),
        ("30 days", D("300"), D("1500.00"), D("1470.00")),
        ("pool", D("200"), D("1000.00"), D("906.67")),
        ("pool", D("600"), D("3300.00"), D("2720.00")),
    ]
    # The day-60 sale is within 30 days of the last day read: a later
    # acquisition could still be matched with it.
    assert [m.provisional for m in section.matches] == [False, False, False, True]


def test_shorts_a_reversal_and_a_split_are_relieved_alike() -> None:
    # A short: 100 sold at 10 and 50 at 12, then 120 bought back at 9 (1,080.00).
    short = fills((1.0, "-100", "10"), (2.0, "-50", "12"), (3.0, "120", "9"))
    fifo = relieve_lots(short, ACME, LotRelief.FIFO)
    # 100 received 1,000.00, paid 900.00; 20 of the 50 received 240.00, paid 180.00.
    assert [match.gain for match in fifo.matches] == [D("100.00"), D("60.00")]
    assert relieve_lots(short, ACME, LotRelief.AVERAGE_COST).realized == short.realized_pnl.of(
        "USD"
    )
    # A long reversed into a short: 100 at 10, then 150 sold at 12.
    reversed_ = fills((1.0, "100", "10"), (2.0, "-150", "12"))
    for method in (LotRelief.AVERAGE_COST, LotRelief.FIFO):
        report = relieve_lots(reversed_, ACME, method)
        assert report.realized == D("200.00")
        assert (report.open_quantity, report.open_cost) == (D("-50"), D("600.00"))
    # Split two for one between a purchase and a sale: the cost is kept.
    split = PortfolioEngine.apply_split(fills((1.0, "100", "10")), Split(ACME, D("2")), 2.0)
    split = PortfolioEngine.apply_fill(split, ACME, D("-50"), D("6"), D("0"), 3.0, "USD")
    for method in (LotRelief.AVERAGE_COST, LotRelief.FIFO):
        report = relieve_lots(split, ACME, method)
        assert report.realized == D("50.00") == split.realized_pnl.of("USD")
        assert (report.open_quantity, report.open_cost) == (D("150"), D("750.00"))


def _trimmed() -> PortfolioState:
    kept = AppendOnlyLog.restored(BOOK.ledger.transactions.to_tuple()[1:], 1)
    return evolve(BOOK, ledger=TransactionLedger(transactions=kept))


def _exchanged() -> PortfolioState:
    event = PositionExchanged(
        99 * DAY, "ACC-LOTS", ACME, D("700"), {}, {"USD": D("1")}, D("0"), False, ""
    )
    return evolve(BOOK, events=BOOK.events.append(event))


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: relieve_lots(BOOK, ACME, LotRelief.SECTION_104), "state the trade day"),
        (
            lambda: relieve_lots(
                fills((1.0, "-10", "5")), ACME, LotRelief.SECTION_104, day_of=day_of
            ),
            "sells short",
        ),
        (
            lambda: relieve_lots(
                PortfolioEngine.apply_split(fills((1.0, "100", "10")), Split(ACME, D("2")), 2.0),
                ACME,
                LotRelief.SECTION_104,
                day_of=day_of,
            ),
            "a split of it is refused",
        ),
        (
            lambda: relieve_lots(
                BOOK,
                ACME,
                LotRelief.FIFO,
                economics=InstrumentEconomics(D("50"), SettlementModel.FUTURES_VARIATION),
            ),
            "settles its gains as cash",
        ),
        (lambda: relieve_lots(_trimmed(), ACME, LotRelief.FIFO), "trimmed"),
        (lambda: relieve_lots(_exchanged(), ACME, LotRelief.FIFO), "holding exchange"),
        (lambda: relieve_lots(BOOK, "OTHER", LotRelief.FIFO), "holds no fill"),
        (lambda: relieve_lots(BOOK, ACME, "FIFO"), "not a LotRelief"),  # type: ignore[arg-type]
    ],
    ids=["no-day", "short", "split", "future", "trimmed", "exchange", "no-fill", "method"],
)
def test_what_lot_relief_cannot_read_is_refused(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(InvalidTransactionError, match=reason):
        build()
