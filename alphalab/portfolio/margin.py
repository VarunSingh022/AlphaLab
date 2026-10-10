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

``base_currency`` is required too, as of v3.10. v2.17 left it defaulted to
``"USD"`` on the grounds that it flows into
:meth:`~alphalab.portfolio.nav.NAVCalculator.calculate`, which refuses a book it
cannot express in that currency -- true for a mixed book, and not for a book
held entirely in another currency with no cash in dollars, which the default
valued as if it were in dollars.

The margin figures are exact products in the accounting context and are not
rounded to a cent: they sum market values that name no currency (see
:mod:`alphalab.portfolio.exposure`), so there is no minor unit to round them to,
and a positions mapping in more than one currency is refused rather than summed.
"""

from collections.abc import Mapping
from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.exceptions import MixedCurrencyValuationError
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

        return ACCOUNTING_CONTEXT.multiply(_gross(positions), margin_rate)

    @staticmethod
    def maintenance_margin(positions: Mapping[str, Position], maint_rate: Decimal) -> Decimal:
        """Margin required to keep ``positions`` open at ``maint_rate``."""

        return ACCOUNTING_CONTEXT.multiply(_gross(positions), maint_rate)

    @staticmethod
    def buying_power(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        margin_rate: Decimal,
        base_currency: str,
        rates: FxRates = NO_RATES,
    ) -> Decimal:
        """Notional this book may still deploy at ``margin_rate``.

        Raises:
            MixedCurrencyValuationError: If the book holds a currency
                ``base_currency`` cannot express and ``rates`` does not convert.
        """

        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency, rates)
        used_margin = MarginEngine.initial_margin(positions, margin_rate)
        return max(Decimal("0"), ACCOUNTING_CONTEXT.divide(nav - used_margin, margin_rate))

    @staticmethod
    def margin_remaining(
        cash_ledger: CashLedger,
        positions: Mapping[str, Position],
        margin_rate: Decimal,
        base_currency: str,
        rates: FxRates = NO_RATES,
    ) -> Decimal:
        """NAV less the margin already committed at ``margin_rate``."""

        nav = NAVCalculator.calculate(cash_ledger, positions, base_currency, rates)
        used = MarginEngine.initial_margin(positions, margin_rate)
        return nav - used


def _gross(positions: Mapping[str, Position]) -> Decimal:
    """Gross exposure of a single-currency positions mapping, or refuse a mixed one."""

    currencies = sorted({position.currency for position in positions.values()})
    if len(currencies) > 1:
        raise MixedCurrencyValuationError(
            f"These positions are held in {currencies}. A margin requirement is one "
            "figure in one currency, and summing market values across currencies without "
            "a rate is the figure ADR-0020 removed; compute it per currency."
        )
    return ExposureEngine.gross_exposure(positions)
