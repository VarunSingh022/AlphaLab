"""Price bands computed from declared reference prices (phase 3, ledger FUT-028).

A :class:`ReferenceBand` is a venue's rule -- ten per cent either side of the
reference, or an absolute amount -- around the :class:`BandReference` in effect,
each dated as published. The band it gives bounds where the instrument trades,
as a declared band does (FUT-020), its edges rounded inward to the tick grid;
with no current reference -- none published yet, or one older than the rule
allows -- the venue's limits are unknown and every order rests until one is.

ACME (in CNY) quotes from 2.0, one quote a second; orders are scripted (the
helpers are tests/integration/test_trading_controls.py's).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.conventions.tick import TickSchedule
from alphalab.instrument.controls import (
    BandAction,
    BandReference,
    PriceBand,
    ReferenceBand,
    TradingControls,
    controls_from_primitives,
    controls_to_primitives,
)
from alphalab.instrument.exceptions import InstrumentInputError
from tests.integration import test_trading_controls as controls

D = Decimal
ACME = controls.ACME


def rule(**kw: Any) -> ReferenceBand:
    fields: dict[str, Any] = {
        "asset_id": ACME.asset_id,
        "start": 1.0,
        "end": None,
        "beyond": BandAction.REFUSE,
        "lower_rate": D("0.10"),
        "upper_rate": D("0.10"),
    }
    fields.update(kw)
    return ReferenceBand(**fields)


def referenced(*refs: tuple[float, str], **kw: Any) -> TradingControls:
    return TradingControls(
        references=tuple(BandReference(ACME.asset_id, at, D(p), "close") for at, p in refs),
        reference_bands=(rule(**kw),),
    )


def limit(price: str) -> OrderTerms:
    return OrderTerms.limit(D(price), TimeInForce.GTC)


def placed(result: Any) -> list[Decimal]:
    return [o.limit_price for o in result.state.oms.orders.orders() if o.limit_price]


# --------------------------------------------------------------------------- #
# The band a reference gives
# --------------------------------------------------------------------------- #


def test_a_limit_beyond_the_reference_band_is_refused_naming_the_band() -> None:
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("115")),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        referenced((1.0, "100")),
    )
    assert placed(result) == []
    (reason,) = strategy.rejections
    assert "90.00 to 110.00" in reason


def test_a_new_reference_moves_the_band_from_its_instant() -> None:
    script = {
        (2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("125")),),
        (4.0, ACME.asset_id): (controls.buy(4.0, terms=limit("125")),),
    }
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "120", 5.0: "120"}}
    result, strategy = controls.run(script, mids, referenced((1.0, "100"), (3.5, "120")))
    # At 2.0 the band is 90 to 110; from 3.5 it is 108 to 132.
    assert placed(result) == [D("125")]
    assert len(strategy.rejections) == 1


def test_nothing_fills_at_an_event_outside_the_reference_band() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "115", 4.0: "105"}}
    result, _ = controls.run(
        {(3.0, ACME.asset_id): (controls.buy(3.0),)}, mids, referenced((1.0, "100"))
    )
    # 115 is beyond the limit up of 110: the market order rests to 4.0.
    assert controls.trades(result) == [(4.0, D("10.000000"), D("105.00"))]


def test_absolute_offsets_give_the_band_the_venue_states() -> None:
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("106")),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        referenced(
            (1.0, "100"),
            lower_rate=None,
            upper_rate=None,
            lower_offset=D("5"),
            upper_offset=D("5"),
        ),
    )
    assert placed(result) == []
    assert "95 to 105" in strategy.rejections[0]


def test_a_capping_rule_caps_to_the_band_rounded_inward_to_the_grid() -> None:
    gridded = replace(ACME, tick=TickSchedule.flat(D("0.05")))
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0, terms=limit("120"), asset=gridded),)},
        {ACME.asset_id: controls.flat(2.0, 3.0)},
        referenced(
            (1.0, "100.3"), beyond=BandAction.CAP, lower_rate=D("0.07"), upper_rate=D("0.07")
        ),
        gridded,
    )
    # 100.3 +- 7% is 93.279 to 107.321: on a 0.05 grid, inward, 93.30 to 107.30.
    assert placed(result) == [D("107.30")]
    assert not strategy.rejections
    (seen,) = {band for at, _, band in strategy.seen if at == 2.0}
    assert seen == (D("93.30"), D("107.30"))


# --------------------------------------------------------------------------- #
# A reference missing, stale, or not yet published
# --------------------------------------------------------------------------- #


def test_with_no_reference_yet_every_order_rests_until_one_is_published() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "101"}}
    result, strategy = controls.run(
        {(2.0, ACME.asset_id): (controls.buy(2.0),)}, mids, referenced((3.5, "100"))
    )
    # Nothing is published until 3.5: the 2.0 and 3.0 quotes trade nothing.
    assert controls.trades(result) == [(4.0, D("10.000000"), D("101.00"))]
    assert not strategy.rejections


def test_a_stale_reference_holds_orders_as_a_missing_one_does() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "102", 5.0: "103"}}
    result, _ = controls.run(
        {(3.0, ACME.asset_id): (controls.buy(3.0),)},
        mids,
        referenced((1.0, "100"), (4.5, "102"), stale_after=1.5),
    )
    # From 2.5 the 1.0 reference is stale; 4.5's is current: trades at 5.0.
    assert controls.trades(result) == [(5.0, D("10.000000"), D("103.00"))]


def test_a_strategy_sees_the_band_of_the_reference_in_effect_and_never_a_later_one() -> None:
    mids = {ACME.asset_id: {2.0: "100", 3.0: "100", 4.0: "120"}}
    _, strategy = controls.run({}, mids, referenced((1.0, "100"), (3.5, "120")))
    bands = {at: band for at, _, band in strategy.seen}
    assert bands[2.0] == bands[3.0] == (D("90.00"), D("110.00"))
    assert bands[4.0] == (D("108.00"), D("132.00"))


def test_the_band_rule_says_why_it_gives_no_band() -> None:
    declared = referenced((5.0, "100"), stale_after=2.0)
    assert declared.unbanded(ACME.asset_id, 2.0) is not None
    assert declared.band(ACME.asset_id, 2.0) is None
    assert declared.unbanded(ACME.asset_id, 6.0) is None
    assert "more than 2.0 seconds" in (declared.unbanded(ACME.asset_id, 8.0) or "")


# --------------------------------------------------------------------------- #
# Declarations and persistence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: rule(lower_rate=None),
        lambda: rule(lower_offset=D("5"), upper_offset=D("5")),
        lambda: rule(lower_rate=D("1"), upper_rate=D("0.1")),
        lambda: rule(lower_rate=None, upper_rate=None, lower_offset=D("0"), upper_offset=D("5")),
        lambda: rule(stale_after=0),
        lambda: rule(start=5.0, end=4.0),
        lambda: BandReference(ACME.asset_id, 1.0, D("0")),
        lambda: TradingControls(
            references=(
                BandReference(ACME.asset_id, 1.0, D("100")),
                BandReference(ACME.asset_id, 1.0, D("101")),
            )
        ),
        lambda: TradingControls(reference_bands=(rule(), rule(start=2.0))),
        lambda: TradingControls(
            bands=(PriceBand(ACME.asset_id, 1.0, None, D("90"), D("110"), BandAction.CAP),),
            reference_bands=(rule(),),
        ),
    ],
)
def test_a_band_rule_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_reference_bands_round_trip_and_controls_without_them_keep_their_form() -> None:
    declared = referenced((1.0, "100"), (3.0, "105"), stale_after=86400.0, beyond=BandAction.CAP)
    assert controls_from_primitives(controls_to_primitives(declared)) == declared
    plain = controls_to_primitives(controls.HALT)
    assert plain is not None
    assert "references" not in plain and "reference_bands" not in plain
