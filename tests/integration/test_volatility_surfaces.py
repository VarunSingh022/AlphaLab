"""Options repriced from a dated volatility surface (phase 4, ledger FUT-032).

A repriced option that declares no volatility of its own reads one from the
options :class:`~alphalab.options.volatility_surface.VolatilitySurface` the
scenario model declares for its underlying -- the latest as of the margined
instant, never a later one -- at its strike and expiry, as
``implied_vol_across_expiries`` reads it: linear between quoted strikes, linear
in total variance between quoted expiries, refusing anything beyond the quotes
or admitting a calendar arbitrage. Where the surface gives none, no volatility
is assumed: the option is margined by its own terms. A declared horizon values
each option at the margined instant plus it, so a scenario counts its decay.

ACME quotes at 100; its options expire ninety days after the first quote (the
helpers are tests/integration/test_margin_option_repricing.py's).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.margin import (
    OptionPricing,
    OptionRisk,
    ScenarioMargin,
    margin_model_from_primitives,
    margin_model_to_primitives,
)
from alphalab.instrument.registry import declare_margin_model
from alphalab.options.carry import dividend_yield
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.volatility_surface import VolatilitySurface, VolPoint
from tests.integration import test_margin_option_repricing as reprice
from tests.integration.harness import registry_of

D = Decimal
T0, EXPIRES, SHARE = reprice.T0, reprice.EXPIRES, reprice.SHARE
CALL, PUT = reprice.CALL, reprice.PUT
FROM_SURFACE = OptionPricing(None, D("0.04"), dividend_yield(0.0))
DAY = 86400.0


def surface(
    *points: tuple[str, float, str], at: float = T0 - 10, underlying: str = ""
) -> VolatilitySurface:
    """Points as ``(strike, expiry, volatility)``."""

    return VolatilitySurface(
        underlying or SHARE.asset_id,
        at,
        tuple(VolPoint(float(k), e, float(v)) for k, e, v in points),
    )


def model(*surfaces: VolatilitySurface, option: Any = PUT, **kw: Any) -> ScenarioMargin:
    base = reprice.scenarios(option, vol=kw.pop("vol", "0"))
    return ScenarioMargin(
        base.scenarios,
        base.maintenance_ratio,
        repriced={option.asset_id: FROM_SURFACE},
        surfaces=surfaces,
        **kw,
    )


def short_put(declared: ScenarioMargin) -> Any:
    return reprice.book({PUT.asset_id: "-10"}, declared, PUT, premium={PUT.asset_id: "5"})


DECLARED = reprice.required(
    reprice.book({PUT.asset_id: "-10"}, reprice.scenarios(PUT), PUT, premium={PUT.asset_id: "5"})
)
#: What the put's own terms require: its whole value, 10 x 100 x 5.
OWN_TERMS = D("5000")


# --------------------------------------------------------------------------- #
# Read at the option's strike and expiry
# --------------------------------------------------------------------------- #

FLAT = surface(("100", EXPIRES, "0.30"))


@pytest.mark.parametrize(
    "declared",
    [
        FLAT,
        # Halfway between 90 at 40% and 110 at 20%: 30%.
        surface(("90", EXPIRES, "0.40"), ("110", EXPIRES, "0.20")),
    ],
)
def test_a_surface_point_values_the_option_as_the_same_declared_volatility(
    declared: VolatilitySurface,
) -> None:
    assert reprice.required(short_put(model(declared))) == DECLARED


def test_between_strikes_the_volatility_is_linear_from_the_nearer_quote() -> None:
    declared = model(surface(("90", EXPIRES, "0.40"), ("130", EXPIRES, "0.20")))
    # 100 is a quarter of the way from 90 to 130: 40% less a quarter of 20 points.
    risk = OptionRisk(
        SHARE.asset_id, D("100"), D("100"), OptionType.PUT, ExerciseStyle.EUROPEAN,
        EXPIRES, T0 + 60, D("-1000"), D("1"),
    )  # fmt: skip
    assert declared.surface_volatility(risk) == D("0.35")


def test_between_expiries_the_volatility_is_interpolated_in_total_variance() -> None:
    near, far = T0 + 30 * DAY, T0 + 150 * DAY
    at = T0 - 10
    grid = surface(("100", near, "0.20"), ("100", far, "0.40"), at=at)
    year = 365.25 * DAY
    t0, t1, t = (near - at) / year, (far - at) / year, (EXPIRES - at) / year
    weight = (t - t0) / (t1 - t0)
    sigma = math.sqrt((0.04 * t0 + (0.16 * t1 - 0.04 * t0) * weight) / t)
    now = reprice.bs(OptionType.PUT, 100.0, vol=round(sigma, 6))
    expected = max(
        reprice.bs(OptionType.PUT, s, vol=round(sigma, 6)) - now for s in (85.0, 115.0, 100.0)
    )
    assert reprice.required(short_put(model(grid))) == expected * 1000


def test_the_surface_as_of_the_margined_instant_is_read_never_a_later_one() -> None:
    later = surface(("100", EXPIRES, "0.60"), at=T0 + 120)
    # Margined at T0 + 60: the surface as of T0 + 120 is not yet known.
    assert reprice.required(short_put(model(FLAT, later))) == DECLARED
    assert reprice.required(short_put(model(later))) == OWN_TERMS


# --------------------------------------------------------------------------- #
# Nothing assumed where nothing was quoted
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "declared",
    [
        # The strike is beyond the quoted strikes.
        surface(("110", EXPIRES, "0.30"), ("120", EXPIRES, "0.30")),
        # The expiry is beyond the quoted expiries.
        surface(("100", T0 + 30 * DAY, "0.30")),
        # The strike is quoted at one bracketing expiry and not the other.
        surface(("100", T0 + 30 * DAY, "0.30"), ("120", T0 + 150 * DAY, "0.30")),
        # Total variance falling with maturity: a calendar arbitrage.
        surface(("100", T0 + 30 * DAY, "0.60"), ("100", T0 + 150 * DAY, "0.20")),
    ],
)
def test_where_the_surface_gives_no_volatility_the_option_is_margined_by_its_own_terms(
    declared: VolatilitySurface,
) -> None:
    assert reprice.required(short_put(model(declared))) == OWN_TERMS


# --------------------------------------------------------------------------- #
# Shocks and the horizon
# --------------------------------------------------------------------------- #


def test_a_scenarios_volatility_shift_moves_the_surfaces_volatility() -> None:
    plan, premium = {CALL.asset_id: "-10"}, {CALL.asset_id: "6"}
    stressed = reprice.book(plan, model(FLAT, option=CALL, vol="0.10"), CALL, premium=premium)
    now = reprice.bs(OptionType.CALL, 100.0)
    assert reprice.required(stressed) == (reprice.bs(OptionType.CALL, 115.0, vol=0.40) - now) * 1000


def test_a_horizon_values_each_scenario_at_the_instant_plus_it() -> None:
    horizon = 30 * DAY
    result = short_put(model(FLAT, horizon=horizon))
    now = reprice.bs(OptionType.PUT, 100.0)
    later = T0 + 60 + horizon
    expected = max(reprice.bs(OptionType.PUT, s, at=later) - now for s in (85.0, 115.0, 100.0))
    # A month's decay is the short's: the requirement is below the instant's.
    assert reprice.required(result) == max(expected * 1000, D("0"))
    assert reprice.required(result) < DECLARED


# --------------------------------------------------------------------------- #
# Declarations and persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: model(surface()),
        lambda: model(surface(("0", EXPIRES, "0.30"))),
        lambda: model(surface(("100", EXPIRES, "0"))),
        lambda: model(surface(("100", EXPIRES, "0.30"), ("100", EXPIRES, "0.40"))),
        lambda: model(FLAT, surface(("100", EXPIRES, "0.40"))),
        lambda: model(),
        lambda: model(FLAT, horizon=0),
    ],
)
def test_a_surface_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_an_option_reading_a_surface_needs_one_for_its_own_underlying() -> None:
    elsewhere = surface(("100", EXPIRES, "0.30"), underlying="ELSEWHERE")
    with pytest.raises(InstrumentRegistrationError):
        declare_margin_model(registry_of(SHARE, PUT), model(elsewhere))


def test_surfaces_round_trip_and_a_model_without_them_keeps_its_form() -> None:
    declared = model(
        surface(("90", EXPIRES, "0.40"), ("110", EXPIRES, "0.20")),
        surface(("100", EXPIRES, "0.35"), at=T0 + 5),
        horizon=DAY,
    )
    assert margin_model_from_primitives(margin_model_to_primitives(declared)) == declared
    plain = margin_model_to_primitives(reprice.scenarios(PUT))
    assert plain is not None and "surfaces" not in plain and "horizon" not in plain


def test_a_surface_is_recorded_with_the_registry_that_declares_it() -> None:
    from alphalab.instrument.snapshot import capture as capture_registry
    from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
    from alphalab.instrument.snapshot import restore as restore_registry
    from alphalab.persistence import deserialize, serialize

    declared = model(FLAT)
    registry = declare_margin_model(registry_of(SHARE, PUT), declared)
    payload = deserialize(serialize(capture_registry(registry)))
    assert "surfaces" in payload["margin_model"]
    assert restore_registry(registry_from_primitives(payload)).margin_model == declared
