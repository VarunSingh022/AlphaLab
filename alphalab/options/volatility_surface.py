"""Implied volatility surface: strike/expiry-indexed implied vol observations.

This is deliberately a simple model: exact (strike, expiry) match, linear
interpolation between the two nearest strikes at a matching expiry, and -- since
v3.13, by a named method -- interpolation in total variance between the two
nearest expiries. It does not fit a parametric surface (SVI, SABR): a fit
reports a model's parameters as though they were observations, which is a
boundary rather than a gap. `implied_vol_at` returns None rather than
extrapolating when a strike falls outside the observed range or no data exists
for the requested expiry.

Across expiries: total variance, by name
----------------------------------------

Two expiries' volatilities are not comparable by linear interpolation: variance
accumulates with time, so the quantity that interpolates between a one-month and
a three-month observation is total variance, ``w = sigma^2 T``, and reading a
two-month volatility as the average of its neighbours' *volatilities* is wrong
by an amount that grows with the gap. Until v3.13 that made interpolation across
expiries a boundary. :func:`implied_vol_across_expiries` now does it the way
that is right, and only when asked by name
(:attr:`ExpiryInterpolation.TOTAL_VARIANCE_LINEAR`): linear in total variance
between the two nearest quoted expiries, at a fixed strike, exact at a quoted
expiry. It refuses what would need a guess -- an expiry outside the quoted range
(extrapolation) -- and quotes whose total variance falls with maturity, a
calendar arbitrage that interpolating between them would price in (ledger
FEA-005). :func:`term_structure` still reports only observed expiries, and
:func:`surface_slice` still refuses an expiry nobody quoted.

Building a surface from a chain
--------------------------------

:func:`surface_from_chain` inverts every quoted contract through
:func:`alphalab.options.implied.implied_volatility` and returns the surface
alongside the contracts it **could not** invert, each with the reason. A chain
always contains some of those -- deep wings quoted at intrinsic, an expiring
strike with no vega -- and dropping them silently is how a surface comes to
look complete while its corners are missing.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.options.binomial import BinomialLattice
from alphalab.options.carry import Carry
from alphalab.options.chain import OptionChain
from alphalab.options.contract import occ_symbol
from alphalab.options.exceptions import OptionInputError, OptionsError
from alphalab.options.implied import ImpliedVolatility, implied_volatility


@dataclass(frozen=True, slots=True)
class VolPoint:
    """A single implied volatility observation.

    Attributes:
        strike: Strike price the observation applies to.
        expiry: Unix timestamp of the expiry the observation applies to.
        implied_vol: Observed implied volatility, e.g. 0.25 for 25%.
    """

    strike: float
    expiry: float
    implied_vol: float


@dataclass(frozen=True, slots=True)
class VolatilitySurface:
    """An immutable collection of implied volatility observations for one underlying.

    Attributes:
        underlying_asset_id: Identifier of the underlying asset.
        timestamp: Unix timestamp this surface is as-of.
        points: Every observation, in no particular order.
    """

    underlying_asset_id: str
    timestamp: float
    points: tuple[VolPoint, ...]


def implied_vol_at(surface: VolatilitySurface, strike: float, expiry: float) -> float | None:
    """Looks up implied volatility for a strike/expiry pair.

    Returns an exact match if present, otherwise linearly interpolates between the
    two nearest strikes at the same expiry. Returns None if no observations exist
    for the given expiry, or if the strike falls outside the observed strike range
    at that expiry (no extrapolation).
    """
    same_expiry = sorted((p for p in surface.points if p.expiry == expiry), key=lambda p: p.strike)
    if not same_expiry:
        return None

    for point in same_expiry:
        if point.strike == strike:
            return point.implied_vol

    if strike < same_expiry[0].strike or strike > same_expiry[-1].strike:
        return None

    lower = max((p for p in same_expiry if p.strike < strike), key=lambda p: p.strike)
    upper = min((p for p in same_expiry if p.strike > strike), key=lambda p: p.strike)

    weight = (strike - lower.strike) / (upper.strike - lower.strike)
    return lower.implied_vol + weight * (upper.implied_vol - lower.implied_vol)


@dataclass(frozen=True, slots=True)
class VolSlice:
    """Every observation at one expiry, ascending by strike.

    A slice is the shape a smile is read from, and it is its own type so that a
    function taking one cannot be handed a whole surface and silently read a
    mixture of maturities.

    Attributes:
        underlying_asset_id: The underlying, carried from the surface.
        expiry: The one expiry these observations share.
        points: Ascending by strike. At least one.
    """

    underlying_asset_id: str
    expiry: float
    points: tuple[VolPoint, ...]

    @property
    def strikes(self) -> tuple[float, ...]:
        """The observed strikes, ascending."""

        return tuple(point.strike for point in self.points)

    @property
    def volatilities(self) -> tuple[float, ...]:
        """The observed volatilities, in strike order."""

        return tuple(point.implied_vol for point in self.points)


@dataclass(frozen=True, slots=True)
class SurfaceRefusal:
    """One contract a surface could not be built from, and why.

    Attributes:
        contract_symbol: ``occ_symbol`` of the contract.
        strike: Its strike, so a reader can see where the hole is.
        expiry: Its expiry.
        reason: The refusal message, verbatim from the inversion.
    """

    contract_symbol: str
    strike: float
    expiry: float
    reason: str


def surface_expiries(surface: VolatilitySurface) -> tuple[float, ...]:
    """Every distinct expiry the surface holds, ascending."""

    return tuple(sorted({point.expiry for point in surface.points}))


def surface_slice(surface: VolatilitySurface, expiry: float) -> VolSlice:
    """The smile at one expiry.

    Raises:
        OptionInputError: If no observation carries that expiry. Returning an
            empty slice would let a caller compute a mean of nothing and report
            it as a smile.
    """

    points = tuple(
        sorted((p for p in surface.points if p.expiry == expiry), key=lambda p: p.strike)
    )
    if not points:
        raise OptionInputError(
            f"{surface.underlying_asset_id} has no observation at expiry {expiry}; observed "
            f"expiries are {surface_expiries(surface)}. Interpolating across expiries would "
            "average two volatilities whose variances accumulate over different horizons."
        )
    return VolSlice(underlying_asset_id=surface.underlying_asset_id, expiry=expiry, points=points)


def term_structure(surface: VolatilitySurface, strike: float) -> tuple[tuple[float, float], ...]:
    """``(expiry, implied_vol)`` at one strike, ascending by expiry.

    Only expiries that actually quote this strike appear. A surface whose
    strikes differ by maturity -- which is every real one, since the listed
    strikes widen with time -- yields a short term structure rather than an
    interpolated one, and the gap is visible as a missing expiry instead of
    being filled.
    """

    return tuple(
        (point.expiry, point.implied_vol)
        for point in sorted(surface.points, key=lambda p: p.expiry)
        if point.strike == strike
    )


_SECONDS_PER_YEAR = 365.25 * 86400


class ExpiryInterpolation(Enum):
    """How a volatility between two quoted expiries is read. One method, by name."""

    #: Linear in total variance ``sigma^2 T`` between the two nearest quoted
    #: expiries, at a fixed strike; exact at a quoted expiry; no extrapolation.
    #: ``T`` is years from the surface's as-of instant over 365.25 days, the
    #: basis the pricers use.
    TOTAL_VARIANCE_LINEAR = auto()


def implied_vol_across_expiries(
    surface: VolatilitySurface,
    strike: float,
    expiry: float,
    *,
    method: ExpiryInterpolation,
) -> float:
    """The implied volatility at ``(strike, expiry)``, between quoted expiries.

    At each of the two nearest quoted expiries around ``expiry`` the strike is
    read as :func:`implied_vol_at` reads it -- exact, or linear between the two
    nearest strikes -- and the two are joined in total variance. A quoted
    expiry returns its own reading.

    The total variance is taken at a fixed strike. With a non-zero carry the
    forward moves with maturity, so a fixed strike is not a fixed moneyness;
    the method states it rather than inferring forwards it was not given.

    Raises:
        OptionInputError: If ``method`` is not an :class:`ExpiryInterpolation`,
            ``expiry`` is not after the surface's as-of instant, ``expiry`` lies
            outside the quoted expiries (no extrapolation), the strike cannot be
            read at either bracketing expiry, or the total variance falls from
            the nearer expiry to the farther (a calendar arbitrage).
    """

    if not isinstance(method, ExpiryInterpolation):
        raise OptionInputError(
            f"method must be an ExpiryInterpolation, got {method!r}; interpolating across "
            "expiries is done only by a named method."
        )
    if not expiry > surface.timestamp:
        raise OptionInputError(
            f"expiry {expiry} is not after the surface's as-of instant {surface.timestamp}."
        )
    quoted = surface_expiries(surface)
    if not quoted or expiry < quoted[0] or expiry > quoted[-1]:
        raise OptionInputError(
            f"{surface.underlying_asset_id} quotes expiries {quoted}; {expiry} lies outside "
            "them, and reading it would extrapolate the term structure."
        )

    def read(at: float) -> float:
        volatility = implied_vol_at(surface, strike, at)
        if volatility is None:
            raise OptionInputError(
                f"Strike {strike} cannot be read at expiry {at}: it lies outside that "
                "expiry's quoted strikes, and a volatility there would be an extrapolation."
            )
        return volatility

    if expiry in quoted:
        return read(expiry)
    near = max(at for at in quoted if at < expiry)
    far = min(at for at in quoted if at > expiry)
    near_years = (near - surface.timestamp) / _SECONDS_PER_YEAR
    far_years = (far - surface.timestamp) / _SECONDS_PER_YEAR
    if near_years <= 0.0:
        raise OptionInputError(
            f"Quoted expiry {near} is not after the surface's as-of instant {surface.timestamp}; "
            "it has no total variance to interpolate from."
        )
    near_variance = read(near) ** 2 * near_years
    far_variance = read(far) ** 2 * far_years
    if far_variance < near_variance:
        raise OptionInputError(
            f"At strike {strike} total variance falls from {near_variance:.6g} at expiry "
            f"{near} to {far_variance:.6g} at {far}: the quotes admit a calendar arbitrage, "
            "and interpolating between them would price it in."
        )
    years = (expiry - surface.timestamp) / _SECONDS_PER_YEAR
    weight = (years - near_years) / (far_years - near_years)
    variance = near_variance + weight * (far_variance - near_variance)
    return math.sqrt(variance / years)


def surface_from_chain(
    chain: OptionChain,
    market_prices: Mapping[str, Decimal],
    spot: Decimal,
    risk_free_rate: float,
    valuation_timestamp: float,
    *,
    carry: Carry,
    lattice: BinomialLattice | None = None,
) -> tuple[VolatilitySurface, tuple[SurfaceRefusal, ...]]:
    """Invert a whole chain into a surface, and say what could not be inverted.

    Args:
        chain: The contracts quoted.
        market_prices: Price per unit of the underlying, keyed by
            ``occ_symbol``. A contract with no entry is skipped and reported --
            an unquoted contract is not a failed inversion.
        spot: The underlying at the same instant.
        risk_free_rate: Continuously-compounded annual rate. Required.
        valuation_timestamp: When the quotes were observed.
        carry: The underlying's carry, the same for every contract in the chain.
        lattice: Invert on this lattice -- early exercise for each American
            contract, the stated cash dividends -- rather than the European
            closed form. ``None`` keeps the closed form, which reads an
            American quote's early-exercise premium as volatility.

    Returns:
        ``(surface, refusals)``. The surface holds one point per contract that
        inverted; the refusals name every one that did not, with the reason. The
        two together account for every contract in the chain, which is what
        ``tests/regression/test_v34_invariants.py`` checks -- a surface that
        silently dropped its wings would still look like a surface.
    """

    points: list[VolPoint] = []
    refusals: list[SurfaceRefusal] = []
    for contract in sorted(chain.contracts, key=lambda c: (c.expiry, float(c.strike))):
        symbol = occ_symbol(contract)
        price = market_prices.get(symbol)
        if price is None:
            refusals.append(
                SurfaceRefusal(
                    contract_symbol=symbol,
                    strike=float(contract.strike),
                    expiry=contract.expiry,
                    reason="no price was supplied for this contract",
                )
            )
            continue
        try:
            inverted: ImpliedVolatility = implied_volatility(
                contract,
                price,
                spot,
                risk_free_rate,
                valuation_timestamp,
                carry=carry,
                lattice=lattice,
            )
        except OptionsError as error:
            refusals.append(
                SurfaceRefusal(
                    contract_symbol=symbol,
                    strike=float(contract.strike),
                    expiry=contract.expiry,
                    reason=str(error),
                )
            )
            continue
        points.append(
            VolPoint(
                strike=float(contract.strike),
                expiry=contract.expiry,
                implied_vol=inverted.value,
            )
        )

    surface = VolatilitySurface(
        underlying_asset_id=chain.underlying_asset_id,
        timestamp=valuation_timestamp,
        points=tuple(points),
    )
    return surface, tuple(refusals)
