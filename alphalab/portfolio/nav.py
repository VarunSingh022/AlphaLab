"""Net asset value: cash plus the marked value of every open position."""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import assert_single_currency_book, cash_in


class NAVCalculator:
    @staticmethod
    def calculate(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        base_currency: str = "USD",
        rates: FxRates = NO_RATES,
        as_of: float | None = None,
    ) -> Decimal:
        """NAV of the book, expressed in ``base_currency``.

        Names a currency and aggregates across positions, so it is a valuation
        and it refuses a book it cannot express -- through
        :func:`~alphalab.portfolio.valuation.assert_single_currency_book`, the
        one rule :meth:`~alphalab.portfolio.valuation.PortfolioValuation.snapshot`
        uses, so the two can never disagree about what a mixed book is.

        Until v2.12 this read cash for the base currency alone while summing
        every position regardless of what it traded in, producing a figure in no
        currency at all -- the v2.7 defect ADR-0020 removed from ``snapshot``
        and left here. ADR-0020 decision 5 deferred the fix to "the release that
        supplies the rate source" because this runs on the per-event risk resync;
        it is closed early instead, on measurement, at roughly two per cent of
        that resync. See ADR-0028 decision 7.

        v2.16 supplies that rate source. With an
        :class:`~alphalab.portfolio.fx.FxRates` table covering the book this
        converts instead of refusing, through the same one rule; with none --
        the default -- it refuses exactly as it did. The homogeneous path is
        unchanged and takes no conversion, which is what keeps the per-event
        risk resync at the cost that was measured.

        Raises:
            MixedCurrencyValuationError: If the book holds a currency
                ``base_currency`` cannot express and no rate converts it.
        """

        foreign_positions, foreign_cash = assert_single_currency_book(
            cash_ledger, positions, base_currency, rates
        )
        if not foreign_positions and not foreign_cash:
            cash = cash_ledger.balance(base_currency)
            long_value = sum(
                (p.market_value for p in positions.values() if p.market_value > 0),
                Decimal("0.00"),
            )
            short_liability = sum(
                (p.market_value for p in positions.values() if p.market_value < 0),
                Decimal("0.00"),
            )
            return cash + long_value + short_liability  # Short value is inherently negative

        # Mixed, and convertible: value each position in the currency it
        # declares, so a sum is never taken across two.
        total = cash_in(cash_ledger, base_currency, rates, as_of)[0]
        for position in positions.values():
            total += rates.convert(
                position.market_value, position.currency, base_currency, as_of
            ).converted
        return total
