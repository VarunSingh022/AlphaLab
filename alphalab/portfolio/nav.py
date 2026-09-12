"""Net asset value: cash plus the marked value of every open position."""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import assert_single_currency_book


class NAVCalculator:
    @staticmethod
    def calculate(
        cash_ledger: CashLedger, positions: Mapping[str, Position], base_currency: str = "USD"
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

        Raises:
            MixedCurrencyValuationError: If the book holds positions or non-zero
                cash in any other currency.
        """

        assert_single_currency_book(cash_ledger, positions, base_currency)
        cash = cash_ledger.balance(base_currency)
        long_value = sum(
            (p.market_value for p in positions.values() if p.market_value > 0), Decimal("0.00")
        )
        short_liability = sum(
            (p.market_value for p in positions.values() if p.market_value < 0), Decimal("0.00")
        )
        return cash + long_value + short_liability  # Short value is inherently negative
