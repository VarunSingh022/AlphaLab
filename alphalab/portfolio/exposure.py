"""Exposure components of a book: long, short, gross, net, and asset weights.

Each component sums position market values and **names no currency**: like
:meth:`~alphalab.portfolio.valuation.PortfolioValuation.long_value`, it is a
component of a valuation rather than a valuation, and a caller holding a book in
more than one currency converts first (see ADR-0028 decision 7).
:meth:`ExposureEngine.asset_weights` is the exception, because it divides by a
NAV, and a NAV names a currency -- so it requires one.
"""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import PortfolioValuation


class ExposureEngine:
    @staticmethod
    def long_exposure(positions: Mapping[str, Position]) -> Decimal:
        return sum((p.market_value for p in positions.values() if p.market_value > 0), ZERO_MONEY)

    @staticmethod
    def short_exposure(positions: Mapping[str, Position]) -> Decimal:
        return sum(
            (abs(p.market_value) for p in positions.values() if p.market_value < 0), ZERO_MONEY
        )

    @staticmethod
    def gross_exposure(positions: Mapping[str, Position]) -> Decimal:
        return ExposureEngine.long_exposure(positions) + ExposureEngine.short_exposure(positions)

    @staticmethod
    def net_exposure(positions: Mapping[str, Position]) -> Decimal:
        return ExposureEngine.long_exposure(positions) - ExposureEngine.short_exposure(positions)

    @staticmethod
    def asset_weights(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        base_currency: str,
        rates: FxRates = NO_RATES,
        as_of: float | None = None,
    ) -> Mapping[str, Decimal]:
        """Each position's market value as a fraction of the book's NAV.

        ``base_currency`` is required as of v3.10 (it defaulted to ``"USD"``).
        The weights are exact quotients in the accounting context; until v3.10
        they were rounded to four decimals, which is a presentation choice made
        inside a figure other code reads.

        Raises:
            MixedCurrencyValuationError: If the book cannot be expressed in
                ``base_currency`` with ``rates``.
        """

        nav = PortfolioValuation.portfolio_value(
            cash_ledger, positions, base_currency, rates, as_of
        )
        if nav == 0:
            return dict.fromkeys(positions, ZERO_MONEY)
        return {
            asset: ACCOUNTING_CONTEXT.divide(p.market_value, nav) for asset, p in positions.items()
        }
