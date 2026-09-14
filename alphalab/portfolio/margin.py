"""Margin arithmetic: exposure and a rate in, a requirement out.

Every rate here is **required**, and that is the whole of what v2.17 changed.
``initial_margin`` defaulted to ``0.50`` and ``maintenance_margin`` to ``0.25``,
which are the US Reg-T conventions -- and a convention is not a universal. A
futures account, a portfolio-margin account, a non-US broker and a crypto venue
each answer differently, and a caller who took the default would have got a
requirement computed against a policy nobody in the conversation chose.

That is the same refusal ADR-0020 makes of a configured FX rate and ADR-0033
decision 10 makes of a default approval policy: *a figure derived from an
invented parameter is exactly as wrong as no figure, with the added cost of
looking authoritative.* AlphaLab does not know what margin a given desk runs to,
so it asks.

``base_currency`` stays defaulted, and the difference is worth stating: it flows
into :meth:`~alphalab.portfolio.nav.NAVCalculator.calculate`, which **refuses** a
book it cannot express in that currency. A wrong currency there produces an
error, never a number. A wrong margin rate produces a number.
"""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.exposure import ExposureEngine
from alphalab.portfolio.fx import NO_RATES, FxRates
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.position import Position


class MarginEngine:
    @staticmethod
    def initial_margin(positions: Mapping[str, Position], margin_rate: Decimal) -> Decimal:
        """Margin required to open ``positions`` at ``margin_rate``.

        Args:
            positions: The book to compute against.
            margin_rate: The fraction of gross exposure the venue requires.
                **Required**: see the module docstring.
        """

        return (ExposureEngine.gross_exposure(positions) * margin_rate).quantize(Decimal("0.01"))

    @staticmethod
    def maintenance_margin(positions: Mapping[str, Position], maint_rate: Decimal) -> Decimal:
        """Margin required to keep ``positions`` open at ``maint_rate``."""

        return (ExposureEngine.gross_exposure(positions) * maint_rate).quantize(Decimal("0.01"))

    @staticmethod
    def buying_power(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        margin_rate: Decimal,
        base_currency: str = "USD",
        rates: FxRates = NO_RATES,
    ) -> Decimal:
        """Notional this book may still deploy at ``margin_rate``.

        Raises:
            MixedCurrencyValuationError: If the book holds a currency
                ``base_currency`` cannot express and ``rates`` does not convert.
        """

        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency, rates)
        used_margin = MarginEngine.initial_margin(positions, margin_rate)
        return max(Decimal("0.00"), (nav - used_margin) / margin_rate)

    @staticmethod
    def margin_remaining(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        margin_rate: Decimal,
        base_currency: str = "USD",
        rates: FxRates = NO_RATES,
    ) -> Decimal:
        """NAV less the margin already committed at ``margin_rate``."""

        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency, rates)
        used = MarginEngine.initial_margin(positions, margin_rate)
        return nav - used
