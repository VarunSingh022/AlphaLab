"""Portfolio valuation: the derived, read-only view of a marked portfolio.

Nothing here holds state. Every value is a deterministic function of the
canonical :class:`~alphalab.portfolio.engine.PortfolioState` -- its cash ledger,
its open positions and their current marks -- so a snapshot taken twice from the
same state is identical.

One currency, or no answer
--------------------------

:meth:`PortfolioValuation.snapshot` returns one number labelled with one
currency. Until v2.8 it produced that number from a book that might hold
several, and the two halves of the calculation disagreed about what to do:
``cash`` was read for the base currency alone, silently dropping every other
balance, while ``long_value`` and ``short_value`` summed every position
regardless of what it traded in. A book of 1000 USD and 500 EUR cash against
1100 USD and 1100 EUR of positions reported ``equity=3200.00`` labelled
``"USD"`` -- a figure in no currency at all.

There is no honest single number without a rate, so
:func:`assert_single_currency` refuses instead of inventing one. **This is the
absence of FX, not a rule that foreign-currency instruments are invalid.**
:class:`~alphalab.portfolio.cash.CashLedger` is already keyed by currency and
:class:`~alphalab.portfolio.position.Position` already declares its own, so
holding and booking in a foreign currency is supported today; only aggregating
two currencies into one figure is not. See ADR-0019.

Which helpers refuse, and which do not
--------------------------------------

Until v2.12 the check was confined to :meth:`~PortfolioValuation.snapshot` and
its siblings were left currency-blind, deferred to "the release that supplies
the rate source" (ADR-0020 decision 5). That release has not arrived, and the
deferral was closed earlier instead, on measurement. What sorts the helpers is
not whether they *could* produce a wrong figure but **what each one claims**:

=========================================  ==========================  ========
Kind                                       Helpers                     Refuses
=========================================  ==========================  ========
Aggregates across positions **and** names  ``snapshot``,               **Yes**
a base currency -- so it is a valuation,   ``portfolio_value``,
and a valuation in a currency the book     ``NAVCalculator.calculate``,
is not in is the v2.7 defect.              ``_risk_exposure``
Aggregates and names no currency -- a      ``long_value``,             No
component of a valuation, not one. It      ``short_value``,
returns an unlabelled sum and cannot be    ``asset_values``,
told what to refuse against, because a     ``ExposureEngine.*``
``Mapping[str, Position]`` carries no
account.
Names a currency but aggregates nothing    ``cash_value``,             No
-- a keyed lookup returns what it was      ``CashLedger.balance``
asked for.
=========================================  ==========================  ========

``long_value`` and ``short_value`` are therefore components, and their one
production caller is :meth:`~PortfolioValuation.snapshot`, which calls them
*after* :func:`assert_single_currency` and is the thing that attaches a currency
label. Guarding them was measured at +1.78% end-to-end against a 0.27% noise
floor -- ``snapshot`` runs twice per market event, and each guard would add a
traversal of a book the assertion has already proven homogeneous. See ADR-0028
decision 7, which pins this table in
``tests/regression/test_currency_authority.py`` so a later reclassification is
deliberate.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.exceptions import MixedCurrencyValuationError
from alphalab.portfolio.money import CURRENCY_QUANT, ZERO_MONEY
from alphalab.portfolio.position import Position

__all__ = [
    "CURRENCY_QUANT",
    "PortfolioValuation",
    "PortfolioValuationSnapshot",
    "assert_single_currency",
    "assert_single_currency_book",
]


def assert_single_currency_book(
    cash: CashLedger, positions: Mapping[str, Position], base_currency: str
) -> None:
    """*The* rule: refuse a book that is not one figure in ``base_currency``.

    Written over the two components rather than over a
    :class:`~alphalab.portfolio.engine.PortfolioState` so that every caller can
    reach it. :func:`assert_single_currency` has a state and delegates here;
    :meth:`NAVCalculator.calculate <alphalab.portfolio.nav.NAVCalculator.calculate>`
    and :meth:`PortfolioValuation.portfolio_value` have a ledger and a mapping
    and call it directly. One implementation, one message, one place to change
    -- which is what stops the rule drifting into two rules that disagree about
    what a mixed book is (ADR-0028 decision 6).

    See :func:`assert_single_currency` for the two conditions and why each
    exists; they are stated once, there.

    Raises:
        MixedCurrencyValuationError: If either condition fails.
    """

    foreign_positions = sorted(
        {position.currency for position in positions.values()} - {base_currency}
    )
    foreign_cash = sorted(
        currency
        for currency, amount in cash.balances.items()
        if currency != base_currency and amount != ZERO_MONEY
    )
    if not foreign_positions and not foreign_cash:
        return

    raise MixedCurrencyValuationError(
        f"This portfolio cannot be valued as one figure in {base_currency!r}: "
        f"positions denominated in {foreign_positions} would be summed into a "
        f"total they are not in, and cash held in {foreign_cash} would be dropped "
        "from it. AlphaLab has no FX rate source, so there is no honest single "
        "number to return. This is the absence of a rate, not a rule that "
        "foreign-currency instruments are invalid: the cash ledger and every "
        "position already carry their own currency, and holding them is "
        "supported. Value each currency separately, or supply a base currency "
        "the whole book is denominated in."
    )


def assert_single_currency(state: PortfolioState, base_currency: str) -> None:
    """Refuse a book that cannot be expressed as one figure in ``base_currency``.

    Two conditions, because there were two independent silent errors:

    * every :class:`~alphalab.portfolio.position.Position` must declare
      ``base_currency``, or its market value would be summed into a total it is
      not denominated in;
    * no other currency may hold a **non-zero** cash balance, or that balance
      would be dropped from the total without trace.

    Zero balances are ignored. ``CashLedger.withdraw`` subtracts in place and
    leaves the key behind, so a spent currency is a routine residue rather than
    a second currency. ``reserved`` is not inspected separately: ``reserve``
    requires ``available_cash`` to cover the amount, so a non-zero reservation
    implies a non-zero balance the cash condition already sees.

    The test is on a position's **declared** currency, never on whether it
    currently has value -- a flat position still says what it trades in, and a
    rule that ignored it would answer differently depending on the order fills
    arrived in.

    Raises:
        MixedCurrencyValuationError: If either condition fails. The message
            names the base currency and both sets of offenders.
    """

    assert_single_currency_book(state.cash, state.positions, base_currency)


@dataclass(frozen=True, slots=True)
class PortfolioValuationSnapshot:
    """Deterministic mark-to-market valuation of a portfolio at one instant.

    Attributes:
        timestamp: Instant the valuation was taken.
        currency: Base currency the valuation is expressed in.
        cash: Cash balance in ``currency``.
        long_value: Summed market value of long positions (>= 0).
        short_value: Summed market value of short positions (<= 0).
        positions_value: ``long_value + short_value``.
        unrealized_pnl: Open P&L across all positions at their current marks.
        realized_pnl: Cumulative P&L crystallised by reductions and closes.
        commission_paid: Cumulative commissions already expensed to cash.
        equity: Total account equity, ``cash + positions_value``.
    """

    timestamp: float
    currency: str
    cash: Decimal
    long_value: Decimal
    short_value: Decimal
    positions_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    commission_paid: Decimal
    equity: Decimal


class PortfolioValuation:
    @staticmethod
    def asset_values(positions: Mapping[str, Position]) -> Mapping[str, Decimal]:
        return {asset: p.market_value for asset, p in positions.items()}

    @staticmethod
    def cash_value(cash_ledger: CashLedger, base_currency: str = "USD") -> Decimal:
        # A keyed lookup, not an aggregation: it returns the balance in the one
        # currency it was asked for and claims nothing about any other, so there
        # is no mixed book for it to refuse. See the module docstring.
        return cash_ledger.balance(base_currency)

    @staticmethod
    def long_value(positions: Mapping[str, Position]) -> Decimal:
        """Summed market value of long positions; zero or positive.

        A **component of a valuation, not a valuation.** It names no currency,
        returns an unlabelled sum, and deliberately does not refuse a mixed book
        -- it has no base currency to refuse against, because a
        ``Mapping[str, Position]`` carries no account. The caller that attaches
        a currency label is :meth:`snapshot`, and that is where the refusal
        lives. Summing positions in two currencies here is meaningless, and it
        is the caller's business to have established that they are not. See the
        module docstring and ADR-0028 decision 7.
        """

        return sum((p.market_value for p in positions.values() if p.quantity > 0), Decimal("0.00"))

    @staticmethod
    def short_value(positions: Mapping[str, Position]) -> Decimal:
        """Summed market value of short positions; zero or negative.

        A component, on the same terms as :meth:`long_value`: no currency named,
        none claimed, and no refusal.
        """

        return sum((p.market_value for p in positions.values() if p.quantity < 0), Decimal("0.00"))

    @staticmethod
    def portfolio_value(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
    ) -> Decimal:
        """Total value of cash and positions, expressed in ``base_currency``.

        Names a currency and aggregates across positions, so it is a valuation
        and it refuses a book it cannot express -- through the same rule
        :meth:`snapshot` uses, and before anything is computed, so a refused
        call returns no partial figure. Until v2.12 it summed every position
        regardless of currency and labelled the result with ``base_currency``.

        Raises:
            MixedCurrencyValuationError: If the book holds positions or non-zero
                cash in any other currency.
        """

        assert_single_currency_book(cash_ledger, positions, base_currency)
        cash_val = PortfolioValuation.cash_value(cash_ledger, base_currency)
        pos_val = sum(PortfolioValuation.asset_values(positions).values(), Decimal("0.00"))
        return cash_val + pos_val

    @staticmethod
    def snapshot(
        state: PortfolioState, timestamp: float, currency: str | None = None
    ) -> PortfolioValuationSnapshot:
        """Value ``state`` as it currently stands, at ``timestamp``.

        Positions are valued at whatever mark they currently carry, so run
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.update_market_prices`
        first if fresh market data is available.

        ``currency`` defaults to the account's base currency. The book must be
        expressible in it: see :func:`assert_single_currency`, which runs before
        anything is computed so a refused valuation returns no partial figure.

        Raises:
            MixedCurrencyValuationError: If the book holds positions or non-zero
                cash in any other currency.
        """

        base_currency = currency if currency is not None else state.account.base_currency
        assert_single_currency(state, base_currency)
        positions = state.positions

        cash = state.cash.balance(base_currency)
        long_value = PortfolioValuation.long_value(positions)
        short_value = PortfolioValuation.short_value(positions)
        positions_value = long_value + short_value
        unrealized = sum((p.unrealized_pnl for p in positions.values()), ZERO_MONEY)

        return PortfolioValuationSnapshot(
            timestamp=timestamp,
            currency=base_currency,
            cash=cash,
            long_value=long_value,
            short_value=short_value,
            positions_value=positions_value,
            unrealized_pnl=unrealized,
            realized_pnl=state.realized_pnl,
            commission_paid=state.commission_paid,
            equity=cash + positions_value,
        )
