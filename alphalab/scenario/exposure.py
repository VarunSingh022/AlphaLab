"""The neutral view a scenario is applied to.

A scenario must work on any portfolio or strategy class without knowing what it
is. So it does not take one: it takes a :class:`ScenarioState`, which is a flat,
immutable projection that anything holding positions can produce.

This is the same separation :class:`~alphalab.analytics.attribution.TradeRecord`
keeps from :class:`~alphalab.execution.report.ExecutionReport`, and it is what
keeps :mod:`alphalab.scenario` importing nothing but :mod:`alphalab.common`. A
scenario contract that named :class:`~alphalab.portfolio.engine.PortfolioState`
would be a scenario contract usable by exactly one portfolio class, which is the
thing the v3.3 brief asks it not to be.

:func:`from_positions` is offered for the common case, and takes the fields
rather than the type -- a caller with a
:class:`~alphalab.portfolio.position.Position`, a broker position, an optimizer
target or rows out of a spreadsheet passes the same four things.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from alphalab.scenario.exceptions import ScenarioValidationError

__all__ = [
    "ScenarioExposure",
    "ScenarioState",
    "from_positions",
]


@dataclass(frozen=True, slots=True)
class ScenarioExposure:
    """One position, as a scenario sees it.

    Attributes:
        asset_id: The asset.
        quantity: Signed units held. Negative is short.
        price: Price per unit, in ``currency``.
        currency: Settlement currency of this exposure.
        volatility: The asset's volatility, or ``None`` when the caller did not
            project one. A volatility shock applied to an exposure carrying
            ``None`` raises rather than being skipped.
        available_liquidity: Units the venue shows, or ``None``. A liquidity
            shock behaves the same way.
        sector: Classification, or ``None``. Only used to resolve a sector
            scope.
    """

    asset_id: str
    quantity: Decimal
    price: Decimal
    currency: str
    volatility: float | None = None
    available_liquidity: Decimal | None = None
    sector: str | None = None

    def __post_init__(self) -> None:
        if self.price < Decimal("0"):
            raise ScenarioValidationError(
                f"{self.asset_id} has price {self.price}; a scenario shocks prices "
                "relatively and a negative base price would invert every shock applied "
                "to it."
            )
        if self.volatility is not None and self.volatility < 0.0:
            raise ScenarioValidationError(
                f"{self.asset_id} has volatility {self.volatility}; volatility is a "
                "dispersion and is not negative."
            )

    @property
    def market_value(self) -> Decimal:
        """``quantity * price``, in this exposure's own currency."""

        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class ScenarioState:
    """A book, ready to be shocked.

    Attributes:
        exposures: The positions, ordered by ``asset_id`` on construction so
            that two states built from the same holdings in different orders are
            equal and produce identical results.
        base_currency: The currency results are reported in.
        rates: Exchange rates from each exposure currency **to**
            ``base_currency``: ``value_in_base = value_in_ccy * rates[ccy]``.
            An exposure already in ``base_currency`` needs no entry and is never
            converted. A currency with no entry is refused at valuation rather
            than assumed to be one-for-one -- ADR-0020's rule, and the reason
            this mapping is empty by default rather than populated with ones.
    """

    exposures: tuple[ScenarioExposure, ...]
    base_currency: str
    rates: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if not self.base_currency.strip():
            raise ScenarioValidationError(
                "base_currency is blank. A scenario reports a value, and a value with no "
                "currency is not one."
            )
        names = [exposure.asset_id for exposure in self.exposures]
        if len(set(names)) != len(names):
            repeated = sorted({name for name in names if names.count(name) > 1})
            raise ScenarioValidationError(
                f"Duplicate assets in the scenario state: {repeated}. One exposure shocked "
                "twice would move twice as far as the scenario asked for."
            )
        object.__setattr__(
            self, "exposures", tuple(sorted(self.exposures, key=lambda item: item.asset_id))
        )

    def rate_for(self, currency: str) -> Decimal:
        """The rate converting ``currency`` into :attr:`base_currency`.

        Raises:
            ScenarioValidationError: If no rate was supplied. AlphaLab does not
                default a rate to one, triangulate one or invert one silently;
                a missing rate is a refusal.
        """

        if currency == self.base_currency:
            return Decimal("1")
        rate = self.rates.get(currency)
        if rate is None:
            raise ScenarioValidationError(
                f"No rate supplied for {currency}->{self.base_currency}, and the book "
                f"holds an exposure settling in {currency}. A scenario will not assume a "
                "rate of one: the resulting value would be wrong by whatever the pair "
                "actually is, and would look exactly like a measurement."
            )
        return rate

    def value(self) -> Decimal:
        """Total market value in :attr:`base_currency`.

        Raises:
            ScenarioValidationError: If any exposure's currency has no rate.
        """

        return sum(
            (
                exposure.market_value * self.rate_for(exposure.currency)
                for exposure in self.exposures
            ),
            Decimal("0"),
        )

    def with_exposures(self, exposures: Sequence[ScenarioExposure]) -> ScenarioState:
        """A copy carrying different exposures. The base state is untouched."""

        return replace(self, exposures=tuple(exposures))

    def with_rates(self, rates: Mapping[str, Decimal]) -> ScenarioState:
        """A copy carrying different rates. The base state is untouched."""

        return replace(self, rates=dict(rates))


def from_positions(
    positions: Sequence[tuple[str, Decimal, Decimal, str]],
    base_currency: str,
    rates: Mapping[str, Decimal] | None = None,
) -> ScenarioState:
    """Build a state from ``(asset_id, quantity, price, currency)`` rows.

    The minimum a scenario needs. Volatility, liquidity and sector are left
    ``None``, so a state built this way supports a price shock and an FX shock
    and *refuses* a volatility or liquidity shock -- which is the correct
    outcome, rather than silently ignoring one.

    ``rates`` is ``None`` for a single-currency book, which is the common case
    and the only one that needs no rates at all.
    """

    return ScenarioState(
        exposures=tuple(
            ScenarioExposure(asset_id, quantity, price, currency)
            for asset_id, quantity, price, currency in positions
        ),
        base_currency=base_currency,
        rates={} if rates is None else dict(rates),
    )
