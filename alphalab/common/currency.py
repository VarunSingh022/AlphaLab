"""The currency seam: converting between currencies without importing who converts.

:class:`~alphalab.portfolio.fx.FxRates` is AlphaLab's one FX authority -- every
rate supplied, attributed and refused when missing, stale or dated after the
instant it is asked about. Packages that must convert but may not import
:mod:`alphalab.portfolio` reach it through the structural protocols here, which
``FxRates`` satisfies as it stands: it is passed in by whoever holds it, and a
consumer names only the shape.

v3.8 defined these three in :mod:`alphalab.allocation.capital`, the first such
consumer. v3.9's execution-quality measurements (:mod:`alphalab.execution.quality`)
are the second, and :mod:`alphalab.execution` may import neither
``alphalab.allocation`` nor ``alphalab.portfolio``, so the protocols moved down to
the bottom layer unchanged. ``alphalab.allocation`` re-exports the same objects,
so every import written against v3.8 still resolves to them.

Nothing here converts anything. A protocol with no implementation cannot invent
a rate, which is the property the move preserves.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

__all__ = ["ConversionRecord", "CurrencyConverter", "RateRecord"]


class RateRecord(Protocol):
    """The facts of one rate: what :class:`~alphalab.portfolio.fx.FxRate` carries."""

    @property
    def base(self) -> str:
        """The currency converted from."""
        ...

    @property
    def quote(self) -> str:
        """The currency converted to."""
        ...

    @property
    def rate(self) -> Decimal:
        """Units of ``quote`` per unit of ``base``."""
        ...

    @property
    def as_of(self) -> float:
        """The instant the rate was true."""
        ...

    @property
    def source(self) -> str:
        """Who quoted it, in words."""
        ...

    @property
    def derived(self) -> bool:
        """Whether it was derived (an inverse or a cross) rather than quoted."""
        ...


class ConversionRecord(Protocol):
    """One conversion: what :class:`~alphalab.portfolio.fx.FxConversion` carries."""

    @property
    def amount(self) -> Decimal:
        """The figure converted, in the rate's base currency."""
        ...

    @property
    def converted(self) -> Decimal:
        """The result, in the rate's quote currency, rounded once by the converter."""
        ...

    @property
    def rate(self) -> RateRecord:
        """The rate the conversion used."""
        ...


class CurrencyConverter(Protocol):
    """Converts an amount between two currencies at an instant, or refuses.

    :class:`~alphalab.portfolio.fx.FxRates` satisfies this as it stands. It must
    refuse -- raise -- rather than invent a rate; ``FxRates`` refuses a missing,
    stale or future-dated one.
    """

    def convert(
        self, amount: Decimal, base: str, quote: str, as_of: float | None
    ) -> ConversionRecord:
        """``amount`` of ``base`` in ``quote`` at ``as_of``, with the rate used; or raise."""
        ...
