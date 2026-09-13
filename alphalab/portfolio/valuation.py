"""Portfolio valuation: the derived, read-only view of a marked portfolio.

Nothing here holds state. Every value is a deterministic function of the
canonical :class:`~alphalab.portfolio.engine.PortfolioState` -- its cash ledger,
its open positions and their current marks -- so a snapshot taken twice from the
same state is identical.

One currency, or a rate that says how
--------------------------------------

:meth:`PortfolioValuation.snapshot` returns one number labelled with one
currency. Until v2.8 it produced that number from a book that might hold
several, and the two halves of the calculation disagreed about what to do:
``cash`` was read for the base currency alone, silently dropping every other
balance, while ``long_value`` and ``short_value`` summed every position
regardless of what it traded in. A book of 1000 USD and 500 EUR cash against
1100 USD and 1100 EUR of positions reported ``equity=3200.00`` labelled
``"USD"`` -- a figure in no currency at all.

There is no honest single number without a rate, so
:func:`assert_single_currency` refused instead of inventing one. **That was the
absence of FX, not a rule that foreign-currency instruments are invalid.**
:class:`~alphalab.portfolio.cash.CashLedger` is already keyed by currency and
:class:`~alphalab.portfolio.position.Position` already declares its own, so
holding and booking in a foreign currency has always been supported; only
aggregating two currencies into one figure was not. See ADR-0019.

**v2.16 supplies the rate.** :mod:`alphalab.portfolio.fx` holds quoted rates
with the provenance that makes a converted figure honest, and every function
here takes an :class:`~alphalab.portfolio.fx.FxRates` table. Given one that
covers the book, a mixed portfolio values as one figure and the snapshot records
every conversion it performed. Given none -- the default, and the state of every
run before this release -- the refusal is exactly what it was.

The rule did not move and did not fork. :func:`assert_single_currency_book` is
still the one implementation; what changed is that a currency it can *convert*
is no longer a currency it must refuse. A pair it was given no rate for still
is, and says which pair.

**The fast path is untouched.** A homogeneous book -- every book AlphaLab was
used for before v2.16 -- takes the same code it always did, calling the
unguarded :meth:`~PortfolioValuation.long_value` and
:meth:`~PortfolioValuation.short_value` after the assertion has proven the book
homogeneous. The converting path exists only for a book that is actually mixed,
so the +1.78% ADR-0028 decision 7 measured for guarding the components is not
paid by anyone.

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
from alphalab.portfolio.fx import NO_RATES, FxConversion, FxRates
from alphalab.portfolio.money import CURRENCY_QUANT, ZERO_MONEY
from alphalab.portfolio.position import Position

__all__ = [
    "CURRENCY_QUANT",
    "PortfolioValuation",
    "PortfolioValuationSnapshot",
    "assert_single_currency",
    "assert_single_currency_book",
    "cash_in",
    "foreign_currencies",
]


def foreign_currencies(
    cash: CashLedger, positions: Mapping[str, Position], base_currency: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which currencies a book holds besides ``base_currency``.

    Returns ``(position_currencies, cash_currencies)``, each sorted. The one
    place that decides what "another currency" means, so the refusal and the
    conversion cannot disagree about it.

    A **zero** cash balance is not another currency: ``CashLedger.withdraw``
    subtracts in place and leaves the key behind, so a spent currency is routine
    residue. A **flat position** is, because it still declares what it trades in
    and a rule that ignored it would answer differently depending on the order
    fills arrived in.
    """

    foreign_positions = sorted(
        {position.currency for position in positions.values()} - {base_currency}
    )
    foreign_cash = sorted(
        currency
        for currency, amount in cash.balances.items()
        if currency != base_currency and amount != ZERO_MONEY
    )
    return tuple(foreign_positions), tuple(foreign_cash)


def assert_single_currency_book(
    cash: CashLedger,
    positions: Mapping[str, Position],
    base_currency: str,
    rates: FxRates = NO_RATES,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """*The* rule: refuse a book that cannot be expressed in ``base_currency``.

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

    ``rates`` is what v2.16 adds. A foreign currency the table can convert is no
    longer a reason to refuse; one it cannot is, and the message names exactly
    which pairs are missing rather than the whole book. Supplying no table --
    the default -- reproduces the pre-v2.16 refusal exactly.

    Returns:
        The foreign position currencies and the foreign cash currencies it
        found, both empty for a homogeneous book. **Returned rather than
        recomputed**: ``snapshot`` runs twice per market event and needs the
        same answer to choose its path, and walking the book a second time to
        get it was measured at roughly +5% end to end. A caller that only wants
        the refusal ignores the value.

    Raises:
        MixedCurrencyValuationError: If the book holds a currency
            ``base_currency`` cannot express, and no rate converts it.
    """

    foreign_positions, foreign_cash = foreign_currencies(cash, positions, base_currency)
    if not foreign_positions and not foreign_cash:
        return foreign_positions, foreign_cash

    present = sorted({*foreign_positions, *foreign_cash})
    missing = [currency for currency in present if rates.rate_for(currency, base_currency) is None]
    if not missing:
        return foreign_positions, foreign_cash

    if not rates:
        raise MixedCurrencyValuationError(
            f"This portfolio cannot be valued as one figure in {base_currency!r}: "
            f"positions denominated in {list(foreign_positions)} would be summed into "
            f"a total they are not in, and cash held in {list(foreign_cash)} would be "
            "dropped from it. No FX rates were supplied, so there is no honest single "
            "number to return. This is the absence of a rate, not a rule that "
            "foreign-currency instruments are invalid: the cash ledger and every "
            "position already carry their own currency, and holding them is "
            "supported. Pass an alphalab.portfolio.fx.FxRates table covering "
            f"{present}, value each currency separately, or supply a base currency "
            "the whole book is denominated in."
        )

    raise MixedCurrencyValuationError(
        f"This portfolio cannot be valued as one figure in {base_currency!r}: rates "
        f"were supplied but none converts {missing} into {base_currency!r}. The table "
        f"holds {list(rates.pairs)}. AlphaLab does not triangulate or invert a rate it "
        "was not given -- supply the missing pair, or call with_inverses() if the "
        "opposite direction is an acceptable derivation."
    )


def assert_single_currency(
    state: PortfolioState, base_currency: str, rates: FxRates = NO_RATES
) -> tuple[tuple[str, ...], tuple[str, ...]]:
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

    return assert_single_currency_book(state.cash, state.positions, base_currency, rates)


def _convert(
    amount: Decimal, base: str, quote: str, rates: FxRates, as_of: float | None
) -> FxConversion:
    """One conversion, or the identity when there is nothing to convert."""

    return rates.convert(amount, base, quote, as_of)


def cash_in(
    cash: CashLedger, base_currency: str, rates: FxRates, as_of: float | None
) -> tuple[Decimal, tuple[FxConversion, ...]]:
    """Total cash expressed in ``base_currency``, and the conversions used.

    Every balance is included, which is the whole point: before v2.8 this read
    the base currency alone and silently dropped the rest, and before v2.16 a
    book holding another currency was refused rather than converted. A zero
    balance in another currency is skipped -- it converts to zero and recording
    a rate for it would put noise in the provenance.
    """

    total = ZERO_MONEY
    performed: list[FxConversion] = []
    for currency, amount in cash.balances.items():
        if currency == base_currency:
            total += amount
            continue
        if amount == ZERO_MONEY:
            continue
        conversion = rates.convert(amount, currency, base_currency, as_of)
        total += conversion.converted
        performed.append(conversion)
    return total, tuple(performed)


def _positions_in(
    positions: Mapping[str, Position], base_currency: str, rates: FxRates, as_of: float | None
) -> tuple[Decimal, tuple[FxConversion, ...]]:
    """Total position value in ``base_currency``, and the conversions used."""

    total = ZERO_MONEY
    performed: list[FxConversion] = []
    for position in positions.values():
        if position.currency == base_currency:
            total += position.market_value
            continue
        conversion = rates.convert(position.market_value, position.currency, base_currency, as_of)
        total += conversion.converted
        performed.append(conversion)
    return total, tuple(performed)


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
        conversions: Every FX conversion this valuation performed, in the order
            performed. **Empty for a single-currency book**, which is every book
            that existed before v2.16 and the overwhelming majority since.

            It is here because a figure in a currency the book is not wholly in,
            with no statement of how it got there, is precisely what ADR-0020
            removed. Recording the rates keeps the number attributable without
            changing the shape of any existing field -- ADR-0020 rejected
            turning ``equity`` into a per-currency mapping, and this is not that.
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
    conversions: tuple[FxConversion, ...] = ()

    @property
    def converted(self) -> bool:
        """Whether any figure here came through an exchange rate."""

        return bool(self.conversions)

    @property
    def rate_sources(self) -> tuple[str, ...]:
        """Every distinct source this valuation's rates came from, sorted."""

        return tuple(sorted({conversion.rate.source for conversion in self.conversions}))


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
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        base_currency: str = "USD",
        rates: FxRates = NO_RATES,
        as_of: float | None = None,
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

        assert_single_currency_book(cash_ledger, positions, base_currency, rates)
        cash_val, _ = cash_in(cash_ledger, base_currency, rates, as_of)
        pos_val, _ = _positions_in(positions, base_currency, rates, as_of)
        return cash_val + pos_val

    @staticmethod
    def snapshot(
        state: PortfolioState,
        timestamp: float,
        currency: str | None = None,
        rates: FxRates = NO_RATES,
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
        foreign_positions, foreign_cash = assert_single_currency(state, base_currency, rates)
        positions = state.positions

        if not foreign_positions and not foreign_cash:
            # The homogeneous path, byte-for-byte what it was before v2.16. The
            # assertion has already proven the book is in one currency, so the
            # unguarded component sums are correct and nothing is converted.
            cash = state.cash.balance(base_currency)
            long_value = PortfolioValuation.long_value(positions)
            short_value = PortfolioValuation.short_value(positions)
            positions_value = long_value + short_value
            unrealized = sum((p.unrealized_pnl for p in positions.values()), ZERO_MONEY)
            conversions: tuple[FxConversion, ...] = ()
        else:
            # The converting path, reached only by a book that is actually
            # mixed. Each position is converted from the currency it declares,
            # so a sum is never taken across two.
            performed: list[FxConversion] = []
            cash, cash_conversions = cash_in(state.cash, base_currency, rates, timestamp)
            performed.extend(cash_conversions)

            long_value = ZERO_MONEY
            short_value = ZERO_MONEY
            unrealized = ZERO_MONEY
            for position in positions.values():
                value = _convert(
                    position.market_value, position.currency, base_currency, rates, timestamp
                )
                pnl = _convert(
                    position.unrealized_pnl, position.currency, base_currency, rates, timestamp
                )
                performed.extend(c for c in (value, pnl) if c.rate.source != "identity")
                if position.quantity > 0:
                    long_value += value.converted
                elif position.quantity < 0:
                    short_value += value.converted
                unrealized += pnl.converted

            positions_value = long_value + short_value
            conversions = tuple(performed)

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
            conversions=conversions,
        )
