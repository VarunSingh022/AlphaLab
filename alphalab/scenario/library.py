"""Named stress scenarios: the historical ones, and the synthetic ones.

The difference between the two halves of this module is the whole point of it.

A **synthetic** scenario is one the caller parameterises. ``flash_crash`` with a
magnitude of ``-0.10`` is a complete, honest statement: somebody asked what a ten
percent instantaneous fall would do. The number came from the caller, so the
scenario carries no claim about the world.

A **historical** scenario is a claim about the world. "2008" is not a magnitude,
it is an assertion that markets moved in a particular way over a particular
window, and AlphaLab does not know how they moved: it ships no market data, has
no dataset of its own, and cannot acquire one. So the historical half of this
module ships **definitions, not numbers**.

:class:`ScenarioDefinition` names the episode, the window it refers to, and the
observations it needs to become an applicable :class:`~alphalab.scenario.scenario.Scenario`.
:meth:`ScenarioDefinition.realize` turns it into one *from observations the
caller supplies*, and refuses -- naming exactly what is absent -- when they do
not. A hard-coded ``-0.37`` for 2008 would be the same kind of invention as a
default exchange rate: a figure that looks measured, is not, and is wrong by
however much the caller's actual universe differed from whatever index the
number was lifted from.

This is the same division the repository already draws. ``NO_RATES`` is the empty
FX table rather than a table of ones, and every consumer refuses when it needs a
rate it has not got. :data:`CRISIS_2008` is the empty 2008 scenario, and it
refuses in the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from alphalab.scenario.exceptions import ScenarioValidationError
from alphalab.scenario.scenario import Scenario
from alphalab.scenario.shock import Shock, ShockKind, ShockScope, everything

__all__ = [
    "COMMODITY_SHOCK_2022",
    "COVID_CRASH_2020",
    "CRISIS_2008",
    "HISTORICAL_SCENARIOS",
    "RATES_REPRICING_2022",
    "Requirement",
    "ScenarioDefinition",
    "commodity_shock",
    "flash_crash",
    "fx_shock",
    "rate_shock",
    "sector_shock",
]


# --------------------------------------------------------------------------- #
# Synthetic: the caller supplies the magnitude, so nothing is claimed
# --------------------------------------------------------------------------- #


def flash_crash(magnitude: Decimal, scope: ShockScope | None = None) -> Scenario:
    """An instantaneous price fall, with liquidity left untouched.

    ``magnitude`` is required and negative by convention, though a positive one
    is permitted: a violent upward move is the same arithmetic and is what a
    short book needs stressing against.

    Liquidity is deliberately *not* shocked here. A real flash crash withdraws
    liquidity as well, but by how much is a separate assumption, and folding a
    guess at it into this function would make every flash-crash result depend on
    a number the caller never saw. Compose the two when that is wanted::

        flash_crash(Decimal("-0.10")).then(scenario("dry-up", liquidity_shock=Decimal("-0.80")))
    """

    return Scenario(
        "flash_crash",
        (Shock(ShockKind.PRICE, magnitude, everything() if scope is None else scope),),
    )


def rate_shock(magnitude: Decimal, scope: ShockScope) -> Scenario:
    """A repricing of rate-sensitive holdings.

    ``scope`` is **required** and has no default. A rate move does not reach
    every instrument equally -- it reprices a bond, moves an equity through a
    discount rate, and may leave a commodity alone -- and the mapping from a
    rate move to a price move is a duration or sensitivity assumption AlphaLab
    does not hold. Naming the scope forces the caller to say which holdings they
    mean, and ``magnitude`` is the price effect they have already derived, not
    the rate change itself.
    """

    return Scenario("rate_shock", (Shock(ShockKind.PRICE, magnitude, scope),))


def fx_shock(magnitude: Decimal, *currencies: str) -> Scenario:
    """A move in the named currencies against the reporting currency.

    At least one currency must be named: an FX shock over an unnamed set would
    have to guess whether it meant every foreign currency the book holds or the
    reporting one, and those are different scenarios.
    """

    if not currencies:
        raise ScenarioValidationError(
            "fx_shock names no currency. Name the ones that move, or use "
            "scenario(..., fx_shock=...) which moves every foreign currency the book "
            "holds."
        )
    from alphalab.scenario.shock import of_currency

    return Scenario("fx_shock", (Shock(ShockKind.FX, magnitude, of_currency(*currencies)),))


def commodity_shock(magnitude: Decimal, *asset_ids: str) -> Scenario:
    """A price move in named commodity holdings.

    Scoped by asset rather than by an asset class, because AlphaLab's
    :class:`~alphalab.core.enums.AssetType` does not reach a
    :class:`~alphalab.scenario.exposure.ScenarioExposure` -- and inferring
    "this is a commodity" from an asset id would be exactly the kind of guess
    this package refuses elsewhere.
    """

    if not asset_ids:
        raise ScenarioValidationError(
            "commodity_shock names no asset. AlphaLab cannot tell which holdings are "
            "commodities from a ScenarioExposure, so the caller names them."
        )
    from alphalab.scenario.shock import of_assets

    return Scenario("commodity_shock", (Shock(ShockKind.PRICE, magnitude, of_assets(*asset_ids)),))


def sector_shock(magnitude: Decimal, *sectors: str) -> Scenario:
    """A price move in named sectors.

    Reaches only exposures that carry a sector. One projected with ``sector=None``
    is not matched -- AlphaLab has no security master, so an unclassified holding
    is genuinely unclassified, and putting it in every sector's shock or none of
    them would both be inventions. :attr:`~alphalab.scenario.scenario.ScenarioResult.reached`
    reports how many exposures the shock actually found.
    """

    if not sectors:
        raise ScenarioValidationError("sector_shock names no sector.")
    from alphalab.scenario.shock import of_sector

    return Scenario("sector_shock", (Shock(ShockKind.PRICE, magnitude, of_sector(*sectors)),))


# --------------------------------------------------------------------------- #
# Historical: definitions, and the data they require
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Requirement:
    """One observation a historical scenario needs before it can be applied.

    Attributes:
        key: What the caller supplies it under.
        kind: Which shock the observation becomes.
        description: What must be measured, in words precise enough that two
            people reading it would supply the same number from the same data.
    """

    key: str
    kind: ShockKind
    description: str


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """A historical episode, and what must be supplied to apply it.

    This is deliberately not a :class:`~alphalab.scenario.scenario.Scenario`. It
    carries no magnitudes, because AlphaLab has not observed any: it is the
    *contract* for a scenario, and :meth:`realize` is where a caller's data
    turns it into one.

    Attributes:
        name: The episode.
        window: The period the observations must be measured over, as an
            inclusive ISO date range. Part of the contract: a "2008" number
            measured over the calendar year and one measured over the September
            to December collapse are different numbers.
        source_note: What data is needed, and what AlphaLab does not have.
        requirements: The observations, ordered.
    """

    name: str
    window: str
    source_note: str
    requirements: tuple[Requirement, ...]

    def required_keys(self) -> tuple[str, ...]:
        """The keys :meth:`realize` needs, ordered."""

        return tuple(requirement.key for requirement in self.requirements)

    def realize(
        self,
        observations: Mapping[str, Decimal],
        scopes: Mapping[str, ShockScope] | None = None,
    ) -> Scenario:
        """Turn this definition into an applicable scenario.

        ``observations`` maps each key in :meth:`required_keys` to the move the
        caller measured over :attr:`window`, as a relative change. ``scopes``
        optionally narrows each requirement to part of the book; a key with no
        scope applies to everything.

        Raises:
            ScenarioValidationError: If any requirement is unsupplied, or if
                ``observations`` carries a key this definition does not define.
                Both are refusals rather than silent behaviour: a missing
                observation would otherwise become an unshocked leg, and an
                unexpected key means the caller is realizing a different
                scenario from the one they think they are.
        """

        supplied = dict(observations)
        expected = set(self.required_keys())

        missing = sorted(expected - set(supplied))
        if missing:
            detail = "\n  ".join(
                f"{requirement.key}: {requirement.description}"
                for requirement in self.requirements
                if requirement.key in missing
            )
            raise ScenarioValidationError(
                f"{self.name} cannot be applied: no observation supplied for "
                f"{missing}. AlphaLab ships no market data and will not invent a "
                f"historical move. Measure these over {self.window} from your own "
                f"dataset and supply them:\n  {detail}\n"
                f"({self.source_note})"
            )

        unexpected = sorted(set(supplied) - expected)
        if unexpected:
            raise ScenarioValidationError(
                f"{self.name} does not define {unexpected}; it requires "
                f"{sorted(expected)}. An observation it has no requirement for would be "
                "silently discarded."
            )

        where = dict(scopes) if scopes is not None else {}
        return Scenario(
            self.name,
            tuple(
                Shock(
                    requirement.kind,
                    supplied[requirement.key],
                    where.get(requirement.key, everything()),
                )
                for requirement in self.requirements
            ),
        )


CRISIS_2008 = ScenarioDefinition(
    name="crisis_2008",
    window="2008-09-15..2008-12-31",
    source_note=(
        "Requires a price history covering the run's own universe across the window. "
        "AlphaLab ships none, and a figure lifted from a broad index would misstate any "
        "book that is not that index."
    ),
    requirements=(
        Requirement(
            "equity_price",
            ShockKind.PRICE,
            "Relative price change of the holdings over the window, measured on the "
            "run's own universe.",
        ),
        Requirement(
            "volatility",
            ShockKind.VOLATILITY,
            "Relative change in realized volatility between the window and the period before it.",
        ),
        Requirement(
            "liquidity",
            ShockKind.LIQUIDITY,
            "Relative change in average daily volume, or in quoted depth, over the window.",
        ),
    ),
)

COVID_CRASH_2020 = ScenarioDefinition(
    name="covid_crash_2020",
    window="2020-02-19..2020-03-23",
    source_note=(
        "Requires a price and volume history over the drawdown window for the run's own universe."
    ),
    requirements=(
        Requirement(
            "equity_price",
            ShockKind.PRICE,
            "Relative price change from the pre-crash high to the trough.",
        ),
        Requirement(
            "volatility",
            ShockKind.VOLATILITY,
            "Relative change in realized volatility across the window.",
        ),
        Requirement(
            "liquidity",
            ShockKind.LIQUIDITY,
            "Relative change in available depth across the window.",
        ),
    ),
)

RATES_REPRICING_2022 = ScenarioDefinition(
    name="rates_repricing_2022",
    window="2022-01-01..2022-10-31",
    source_note=(
        "Requires the price effect of the year's rate repricing on the run's own "
        "holdings. AlphaLab holds no duration or rate sensitivity for any instrument, "
        "so the translation from a rate move to a price move is the caller's."
    ),
    requirements=(
        Requirement(
            "price",
            ShockKind.PRICE,
            "Relative price change attributable to the repricing, per the caller's own "
            "duration or sensitivity assumptions.",
        ),
        Requirement(
            "fx",
            ShockKind.FX,
            "Relative move of each foreign currency held against the reporting currency "
            "over the window.",
        ),
    ),
)

COMMODITY_SHOCK_2022 = ScenarioDefinition(
    name="commodity_shock_2022",
    window="2022-02-24..2022-06-30",
    source_note=(
        "Requires a price history for the commodity holdings being stressed. Scope the "
        "realized scenario to them: AlphaLab cannot tell which holdings are commodities."
    ),
    requirements=(
        Requirement(
            "commodity_price",
            ShockKind.PRICE,
            "Relative price change of the commodity holdings over the window.",
        ),
        Requirement(
            "volatility",
            ShockKind.VOLATILITY,
            "Relative change in their realized volatility over the window.",
        ),
    ),
)

#: Every historical definition this module ships, by name. Each is a contract
#: requiring data, never a set of numbers -- see this module's docstring.
HISTORICAL_SCENARIOS: Mapping[str, ScenarioDefinition] = {
    definition.name: definition
    for definition in (
        CRISIS_2008,
        COVID_CRASH_2020,
        RATES_REPRICING_2022,
        COMMODITY_SHOCK_2022,
    )
}
