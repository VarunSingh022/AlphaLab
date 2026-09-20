"""How much capital a strategy can actually trade, and what stops it.

Capacity is not a property of a return series. It is the point at which a
strategy's own trading runs into the market it trades in, and it is therefore a
statement about five things at once: the capital deployed, how that capital is
spread across positions, how fast it turns over, how much liquidity each name
has, and how much the resulting order moves the price.

``alphalab.research.capacity`` answers a different question and keeps its own
name for it -- there, ``estimate_capacity`` degrades a CAGR at three fixed AUM
levels from a trade count, which is a research-report heuristic over a
:class:`~alphalab.research.protocol.ResearchPayload` and reads no liquidity at
all. The two are pinned apart in
``tests/regression/test_shared_names_stay_distinct.py``. This module is the
execution-side authority: it reads the same liquidity and impact models a fill
is priced with, so the capacity it reports and the costs a backtest pays come
from one set of assumptions rather than two.

The model
---------

For one asset ``i`` at deployed capital ``C``, with portfolio weight ``w``,
price ``p``, average daily volume ``adv`` (in units, not currency) and a
per-period turnover fraction ``t``::

    traded units per period  =  C * w * t / p
    participation            =  traded units / adv

Capacity is the largest ``C`` at which every binding constraint still holds, and
:class:`CapacityModel` evaluates two of them:

``PARTICIPATION``
    The strategy may take at most ``participation_limit`` of a name's volume.
    Rearranged, that is a closed-form capital ceiling per asset::

        C_i = participation_limit * adv * p / (w * t)

``IMPACT_BUDGET``
    The price concession the strategy's own size causes, expressed as a fraction
    of the notional it trades, may not exceed ``impact_budget``. This has no
    closed form for a general impact model, so it is solved by bisection on
    capital -- deterministically, to a stated tolerance, and only over the
    monotone region the search is valid on (see :meth:`CapacityModel.capacity`).

Portfolio capacity is the **minimum** over assets: the first name to bind caps
the whole book, and :attr:`CapacityResult.binding_asset_id` names it. Reporting a
mean or a sum here would describe a portfolio that cannot be traded.

Nothing is assumed
------------------

Every input is required. There is no default participation limit, no default
turnover and no default impact budget, because each is a decision that moves the
answer by orders of magnitude and none of them has a universal value. A strategy
that wants only the participation ceiling passes ``impact_budget=None`` and gets
a result that says the impact constraint was not evaluated, rather than one that
quietly evaluated it against an invented budget.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.core.enums import Side
from alphalab.execution.costs import CostContext, ImpactModel
from alphalab.execution.exceptions import ExecutionValidationError

__all__ = [
    "AssetCapacity",
    "AssetLiquidity",
    "CapacityConstraint",
    "CapacityModel",
    "CapacityResult",
    "capacity_curve",
]

_ZERO = Decimal("0")

#: Bisection stops when the bracket is narrower than this fraction of its own
#: upper end -- a *relative* precision on the answer, not on the ceiling the
#: search started from. Measuring it against the ceiling instead would round
#: every capacity far below that ceiling to zero, which is the difference
#: between "this strategy holds about 32,000" and "this strategy holds nothing".
#: Stated rather than tuned: it fixes the reproducibility of every impact-bound
#: capacity figure this module reports.
_SOLVE_TOLERANCE = Decimal("0.0001")

#: Bisection iteration ceiling. With a halving step the tolerance above is
#: reached in well under this many steps for any bracket; it exists so that a
#: pathological impact model cannot make the search run for ever.
_SOLVE_MAX_STEPS = 200


class CapacityConstraint(Enum):
    """Which constraint capped the capital, or that none did."""

    #: The strategy would take more of a name's volume than allowed.
    PARTICIPATION = auto()

    #: The strategy's own impact cost would exceed the budget.
    IMPACT_BUDGET = auto()

    #: No constraint bound below the ceiling searched. The reported figure is
    #: that ceiling, and it is a lower bound on capacity rather than capacity.
    UNBOUND = auto()


@dataclass(frozen=True, slots=True)
class AssetLiquidity:
    """What one name can absorb, and the strategy's exposure to it.

    Attributes:
        asset_id: The asset.
        price: Reference price, in ``currency``.
        average_daily_volume: Volume in **units** (shares, contracts), not
            currency. Units rather than notional because that is what a venue
            reports and what a participation rate is measured in; converting
            here would bake today's price into a liquidity fact.
        weight: The share of deployed capital this name carries, as a fraction.
            The weights of a portfolio passed to :class:`CapacityModel` are not
            required to sum to one -- a long/short book's gross exposure may
            exceed it, and normalising silently would understate participation.
        currency: Currency ``price`` is quoted in.
        venue: Venue the capacity is measured against.
    """

    asset_id: str
    price: Decimal
    average_daily_volume: Decimal
    weight: Decimal
    currency: str
    venue: str

    def __post_init__(self) -> None:
        if self.price <= _ZERO:
            raise ExecutionValidationError(
                f"{self.asset_id} has price {self.price}; capacity is measured against a "
                "traded price and a non-positive one is not a price."
            )
        if self.average_daily_volume <= _ZERO:
            raise ExecutionValidationError(
                f"{self.asset_id} has average_daily_volume {self.average_daily_volume}. A "
                "name that trades nothing has no capacity, which is a refusal rather than "
                "a very small number: remove it from the universe or supply its volume."
            )
        if self.weight < _ZERO:
            raise ExecutionValidationError(
                f"{self.asset_id} has weight {self.weight}. Capacity reads gross exposure, "
                "so a short leg is carried as its absolute weight; a negative weight here "
                "would reduce the participation it actually causes."
            )

    @property
    def daily_notional(self) -> Decimal:
        """``average_daily_volume * price`` -- what the name turns over."""

        return self.average_daily_volume * self.price


@dataclass(frozen=True, slots=True)
class AssetCapacity:
    """One name's contribution to the portfolio's ceiling.

    Attributes:
        asset_id: The asset.
        participation_capacity: Capital at which the participation limit binds
            for this name.
        impact_capacity: Capital at which the impact budget binds, or ``None``
            when no budget was supplied.
        capacity: The lower of the two that were evaluated.
        constraint: Which of them produced :attr:`capacity`.
        participation_at_capacity: Share of this name's volume the strategy
            takes at :attr:`capacity`.
    """

    asset_id: str
    participation_capacity: Decimal
    impact_capacity: Decimal | None
    capacity: Decimal
    constraint: CapacityConstraint
    participation_at_capacity: Decimal


@dataclass(frozen=True, slots=True)
class CapacityResult:
    """What the portfolio can carry, and the name and constraint that cap it.

    Attributes:
        capacity: Deployable capital, in the model's currency.
        constraint: Which constraint bound first.
        binding_asset_id: The name that bound, or ``None`` when nothing bound.
        per_asset: Every name's own ceiling, ordered by ``asset_id``, so a
            reader can see how far the second-tightest name is from the first.
        assumptions: The inputs that produced this figure, echoed back. A
            capacity number is meaningless without them, so it does not travel
            without them.
    """

    capacity: Decimal
    constraint: CapacityConstraint
    binding_asset_id: str | None
    per_asset: tuple[AssetCapacity, ...]
    assumptions: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CapacityModel:
    """The assumptions a capacity figure is measured under.

    Attributes:
        participation_limit: The largest share of a name's average daily volume
            the strategy is permitted to take in one period, as a fraction.
        turnover: The fraction of deployed capital traded in one period. A
            strategy that replaces its whole book daily has ``1``; one that
            replaces a twentieth has ``0.05``. It multiplies traded volume
            directly, so capacity is inversely proportional to it.
        impact_model: The model used to price the strategy's own footprint. The
            **same** protocol a fill is priced with in
            :mod:`alphalab.execution.costs`, so a capacity study and a backtest
            cannot disagree about impact.
        impact_budget: The largest impact concession tolerated, as a fraction of
            the price traded at. ``None`` means the impact constraint is not
            evaluated and the result says so -- it is not zero, and it is not
            infinite.
        search_ceiling: The largest capital the impact search will consider.
            Required, because a bisection needs a bracket and the alternative is
            an invented upper bound. A result that reaches it reports
            :attr:`CapacityConstraint.UNBOUND`.
    """

    participation_limit: Decimal
    turnover: Decimal
    impact_model: ImpactModel
    impact_budget: Decimal | None
    search_ceiling: Decimal

    def __post_init__(self) -> None:
        if not _ZERO < self.participation_limit <= Decimal("1"):
            raise ExecutionValidationError(
                f"participation_limit is {self.participation_limit}; it is a fraction of a "
                "name's volume and must lie in (0, 1]. A limit of zero permits no trading "
                "and a limit above one claims more volume than the market printed."
            )
        if self.turnover <= _ZERO:
            raise ExecutionValidationError(
                f"turnover is {self.turnover}. A strategy that trades nothing has no "
                "capacity limit to measure, which is a different statement from an "
                "unbounded one."
            )
        if self.impact_budget is not None and self.impact_budget <= _ZERO:
            raise ExecutionValidationError(
                f"impact_budget is {self.impact_budget}; a budget of zero or less admits no "
                "capital at all. Pass None to leave the impact constraint unevaluated."
            )
        if self.search_ceiling <= _ZERO:
            raise ExecutionValidationError(
                f"search_ceiling is {self.search_ceiling}; the search needs a positive "
                "bracket to bisect."
            )

    # ------------------------------------------------------------------ #
    # The two constraints
    # ------------------------------------------------------------------ #

    def participation_capacity(self, asset: AssetLiquidity) -> Decimal:
        """Capital at which ``asset`` hits :attr:`participation_limit`.

        ``participation_limit * adv * price / (weight * turnover)``. A name the
        strategy does not hold (``weight == 0``) never binds, and is reported as
        the search ceiling rather than as infinity -- a capacity result carries
        Decimals, and one of them cannot be infinite.
        """

        if asset.weight == _ZERO:
            return self.search_ceiling
        return self.participation_limit * asset.daily_notional / (asset.weight * self.turnover)

    def participation_at(self, asset: AssetLiquidity, capital: Decimal) -> Decimal:
        """The share of ``asset``'s volume the strategy takes at ``capital``."""

        traded_units = capital * asset.weight * self.turnover / asset.price
        return traded_units / asset.average_daily_volume

    def impact_at(self, asset: AssetLiquidity, capital: Decimal, timestamp: float) -> Decimal:
        """Per-unit impact concession for ``asset`` at ``capital``.

        Priced through :attr:`impact_model` against a context whose participation
        is the one :meth:`participation_at` reports, so the impact a capacity
        study charges is the impact a fill of that size would be charged.
        """

        traded_units = capital * asset.weight * self.turnover / asset.price
        context = CostContext(
            asset_id=asset.asset_id,
            # Capacity is a question about size, not direction, and a buy is the
            # side every impact model in alphalab.execution.costs treats as
            # positive. Both sides give the same magnitude.
            side=Side.BUY,
            quantity=traded_units,
            reference_price=asset.price,
            currency=asset.currency,
            venue=asset.venue,
            timestamp=timestamp,
            available_liquidity=asset.average_daily_volume,
        )
        return self.impact_model.impact(context)

    def impact_capacity(self, asset: AssetLiquidity, timestamp: float) -> Decimal | None:
        """Capital at which ``asset``'s impact reaches :attr:`impact_budget`.

        ``None`` when no budget was supplied. Solved by bisection on capital
        rather than in closed form, because :class:`~alphalab.execution.costs.ImpactModel`
        is a protocol and a square-root, linear or caller-supplied model each
        invert differently.

        The search assumes impact is **non-decreasing in capital**, which every
        model in :mod:`alphalab.execution.costs` satisfies and which is the
        defining property of an impact model. A model that is not monotone
        breaks the bisection's precondition rather than this function's
        arithmetic, and the result would be one of possibly several crossings.

        The resolution of the answer is bounded by the impact model's own
        quantization, not by :data:`_SOLVE_TOLERANCE` alone: an impact quantized
        to a price tick is a step function, and the search locates a step rather
        than a point. At a budget worth only a few ticks the last digits of the
        result are therefore the model's granularity showing through. It is
        deterministic and reproducible either way, which is what a capacity
        figure has to be; it is not accurate beyond the tick the impact was
        quantized to, which is what this paragraph exists to stop a reader from
        assuming.
        """

        budget = self.impact_budget
        if budget is None:
            return None
        if asset.weight == _ZERO:
            return self.search_ceiling

        def over_budget(capital: Decimal) -> bool:
            return self.impact_at(asset, capital, timestamp) / asset.price > budget

        if not over_budget(self.search_ceiling):
            return self.search_ceiling

        low, high = _ZERO, self.search_ceiling
        for _ in range(_SOLVE_MAX_STEPS):
            if high - low <= _SOLVE_TOLERANCE * high:
                break
            middle = (low + high) / Decimal("2")
            if over_budget(middle):
                high = middle
            else:
                low = middle
        return low

    # ------------------------------------------------------------------ #
    # The portfolio answer
    # ------------------------------------------------------------------ #

    def capacity(self, universe: Sequence[AssetLiquidity], timestamp: float) -> CapacityResult:
        """The capital this universe can carry under these assumptions.

        Raises:
            ExecutionValidationError: If ``universe`` is empty, or if two
                entries name the same asset -- a duplicated name would have its
                weight counted once and its liquidity twice.
        """

        if not universe:
            raise ExecutionValidationError(
                "Capacity over an empty universe is undefined. A portfolio with no names "
                "has no liquidity to measure against, which is not the same as unlimited "
                "capacity."
            )
        seen = [asset.asset_id for asset in universe]
        if len(set(seen)) != len(seen):
            duplicates = sorted({name for name in seen if seen.count(name) > 1})
            raise ExecutionValidationError(
                f"Duplicate assets in the capacity universe: {duplicates}. Each name "
                "carries one weight and one liquidity; two entries would double its depth."
            )

        per_asset: list[AssetCapacity] = []
        for asset in sorted(universe, key=lambda item: item.asset_id):
            participation_ceiling = self.participation_capacity(asset)
            impact_ceiling = self.impact_capacity(asset, timestamp)

            ceiling = participation_ceiling
            constraint = CapacityConstraint.PARTICIPATION
            if impact_ceiling is not None and impact_ceiling < ceiling:
                ceiling = impact_ceiling
                constraint = CapacityConstraint.IMPACT_BUDGET
            if ceiling >= self.search_ceiling:
                ceiling = self.search_ceiling
                constraint = CapacityConstraint.UNBOUND

            per_asset.append(
                AssetCapacity(
                    asset_id=asset.asset_id,
                    participation_capacity=participation_ceiling,
                    impact_capacity=impact_ceiling,
                    capacity=ceiling,
                    constraint=constraint,
                    participation_at_capacity=self.participation_at(asset, ceiling),
                )
            )

        binding = min(per_asset, key=lambda item: (item.capacity, item.asset_id))
        unbound = binding.constraint is CapacityConstraint.UNBOUND

        return CapacityResult(
            capacity=binding.capacity,
            constraint=binding.constraint,
            binding_asset_id=None if unbound else binding.asset_id,
            per_asset=tuple(per_asset),
            assumptions={
                "participation_limit": str(self.participation_limit),
                "turnover": str(self.turnover),
                "impact_model": type(self.impact_model).__name__,
                "impact_budget": "unevaluated"
                if self.impact_budget is None
                else str(self.impact_budget),
                "search_ceiling": str(self.search_ceiling),
            },
        )


def capacity_curve(
    model: CapacityModel,
    universe: Sequence[AssetLiquidity],
    capital_levels: Sequence[Decimal],
    timestamp: float,
) -> tuple[tuple[Decimal, Decimal, Decimal], ...]:
    """``(capital, participation, impact_fraction)`` at each level, worst name.

    The sensitivity view: how hard the portfolio is pressing on the market as
    capital grows. At each level the **maximum** participation across names and
    the impact fraction of that same name are reported, because the worst name
    is what binds and an average would hide it.

    Levels are reported in the order given, so a caller controls the spacing and
    nothing is interpolated.

    Raises:
        ExecutionValidationError: If ``universe`` is empty or a level is
            negative.
    """

    if not universe:
        raise ExecutionValidationError("A capacity curve over an empty universe is undefined.")
    if any(level < _ZERO for level in capital_levels):
        raise ExecutionValidationError("A capacity curve has no meaning at negative capital.")

    points: list[tuple[Decimal, Decimal, Decimal]] = []
    for capital in capital_levels:
        worst = max(
            universe,
            key=lambda asset: (model.participation_at(asset, capital), asset.asset_id),
        )
        points.append(
            (
                capital,
                model.participation_at(worst, capital),
                model.impact_at(worst, capital, timestamp) / worst.price,
            )
        )
    return tuple(points)
