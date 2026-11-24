"""An FX deal's terms on its record: declared, refused, and persisted with the registry (ADR-0067).

* **Declared.** Spot, a forward by tenor or by an outright date, and an NDF
  with its fixing, each on a currency pair priced in its quote currency, its
  value date's calendars named by id.
* **Refused**, with the reason: a code that is not ISO 4217's shape, one
  currency twice, spot with a forward date, a forward with none or both, a tenor
  without its convention and end-of-month rule, an NDF without its fixing or
  settling outside its pair, no calendar or one named twice; and on a record, a
  pair that is not FOREX, not priced in its quote currency, or with economics
  that are not a cash equity.
* **Persisted.** Each kind reads back equal through the terms codec and
  through the registry's snapshot, and a registry with no FX terms writes what
  it always wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from alphalab.conventions.business_days import BusinessDayConvention, Tenor, TenorUnit
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.snapshot import capture, from_primitives, registry_digest, restore
from alphalab.instrument.terms import (
    FxKind,
    FxTerms,
    FxValueDateRule,
    NdfFixing,
    terms_from_primitives,
    terms_to_primitives,
)
from alphalab.persistence.serializer import deserialize, serialize

SPOT_RULE = FxValueDateRule(2, ("TARGET",), ("TARGET", "USNY"))
THREE_MONTHS = FxValueDateRule(
    2,
    ("TARGET",),
    ("TARGET", "USNY"),
    tenor=Tenor(3, TenorUnit.MONTHS),
    convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    end_of_month=True,
)
OUTRIGHT = FxValueDateRule(2, ("TARGET",), ("TARGET", "USNY"), outright=date(2025, 3, 21))

SPOT = FxTerms("EUR", "USD", FxKind.SPOT, SPOT_RULE)
FORWARD = FxTerms("EUR", "USD", FxKind.FORWARD, THREE_MONTHS)
DATED = FxTerms("EUR", "USD", FxKind.FORWARD, OUTRIGHT)
NDF = FxTerms(
    "USD",
    "INR",
    FxKind.NDF,
    FxValueDateRule(
        2,
        ("USNY", "XBOM"),
        ("USNY", "XBOM"),
        tenor=Tenor(1, TenorUnit.MONTHS),
        convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        end_of_month=False,
    ),
    NdfFixing("USD", 2, "the run's INR reference rate"),
)


def pair(symbol: str, currency: str, terms: FxTerms) -> InstrumentRecord:
    return InstrumentRecord(symbol, AssetType.FOREX, "XFXV", currency, terms=terms)


@pytest.mark.parametrize(
    "terms", [SPOT, FORWARD, DATED, NDF], ids=["spot", "tenor", "outright", "ndf"]
)
def test_a_pair_declares_each_kind_on_its_record(terms: FxTerms) -> None:
    record = pair(f"{terms.base}{terms.quote}", terms.quote, terms)
    assert record.terms == terms
    assert terms.value_dates.forward is (terms.kind is not FxKind.SPOT)


@pytest.mark.parametrize(
    "terms", [SPOT, FORWARD, DATED, NDF], ids=["spot", "tenor", "outright", "ndf"]
)
def test_each_kind_reads_back_through_the_codec(terms: FxTerms) -> None:
    written = terms_to_primitives(terms)
    assert written is not None and written["kind"] == "fx"
    assert terms_from_primitives(deserialize(serialize(written))) == terms


def test_fx_terms_travel_with_the_registry_and_a_registry_without_them_is_stored_as_before() -> (
    None
):
    plain = register_instruments(
        InstrumentRegistry(), (InstrumentRecord("EURUSD", AssetType.FOREX, "XFXV", "USD"),)
    )
    declared = register_instruments(
        InstrumentRegistry(), (pair("EURUSD3M", "USD", FORWARD), pair("USDINR1M", "INR", NDF))
    )
    # A record's terms take part in its equality, so this is the terms read back.
    assert restore(from_primitives(deserialize(serialize(capture(declared))))) == declared
    # Nothing is written for a record that declares none: its terms stay null,
    # as every earlier release wrote them (the golden snapshots pin those bytes).
    assert capture(plain).instruments[0].terms is None
    assert '"fx_kind"' not in serialize(capture(plain))
    assert '"fx_kind"' in serialize(capture(declared))
    assert registry_digest(declared) != registry_digest(plain)


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: FxTerms("eur", "USD", FxKind.SPOT, SPOT_RULE), "ISO 4217"),
        (lambda: FxTerms("EURO", "USD", FxKind.SPOT, SPOT_RULE), "ISO 4217"),
        (lambda: FxTerms("EUR", "EUR", FxKind.SPOT, SPOT_RULE), "two currencies"),
        (lambda: FxTerms("EUR", "USD", FxKind.SPOT, THREE_MONTHS), "settles at spot"),
        (lambda: FxTerms("EUR", "USD", FxKind.FORWARD, SPOT_RULE), "outright date or a tenor"),
        (lambda: FxTerms("EUR", "USD", FxKind.NDF, THREE_MONTHS), "states its fixing"),
        (
            lambda: FxTerms("EUR", "USD", FxKind.FORWARD, THREE_MONTHS, NdfFixing("USD", 2, "x")),
            "only an NDF",
        ),
        (
            lambda: FxTerms("USD", "INR", FxKind.NDF, THREE_MONTHS, NdfFixing("EUR", 2, "x")),
            "settles in one of them",
        ),
        (
            lambda: FxValueDateRule(
                2, ("T",), ("T",), outright=date(2025, 3, 21), tenor=Tenor(1, TenorUnit.MONTHS)
            ),
            "not both",
        ),
        (
            lambda: FxValueDateRule(2, ("T",), ("T",), tenor=Tenor(1, TenorUnit.MONTHS)),
            "stated BusinessDayConvention",
        ),
        (
            lambda: FxValueDateRule(2, ("T",), ("T",), convention=BusinessDayConvention.FOLLOWING),
            "there is no tenor",
        ),
        (lambda: FxValueDateRule(2, (), ("T",)), "at least one calendar"),
        (lambda: FxValueDateRule(2, ("T", "T"), ("T",)), "twice"),
        (lambda: FxValueDateRule(-1, ("T",), ("T",)), "non-negative"),
        (lambda: NdfFixing("USD", -1, "x"), "non-negative"),
        (lambda: NdfFixing("USD", 2, " "), "names its fixing source"),
    ],
    ids=[
        "lower",
        "long",
        "same",
        "spot-dated",
        "forward-undated",
        "ndf-unfixed",
        "fixed-forward",
        "ndf-elsewhere",
        "both",
        "tenor-bare",
        "convention-alone",
        "no-calendar",
        "twice",
        "lag",
        "fixing-lag",
        "source",
    ],
)
def test_a_deal_states_every_term(build: Callable[[], object], reason: str) -> None:
    with pytest.raises(InstrumentInputError, match=reason):
        build()


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (
            lambda: InstrumentRecord("EURUSD", AssetType.EQUITY, "XFXV", "USD", terms=SPOT),
            "currency pair's terms",
        ),
        (lambda: pair("EURUSD", "EUR", SPOT), "priced in its quote currency"),
        (
            lambda: InstrumentRecord(
                "EURUSD",
                AssetType.FOREX,
                "XFXV",
                "USD",
                economics=InstrumentEconomics(Decimal("100"), SettlementModel.FUTURES_VARIATION),
                terms=SPOT,
            ),
            "one unit of its base currency",
        ),
    ],
    ids=["equity", "priced-in-base", "economics"],
)
def test_a_record_holds_fx_terms_only_as_a_pair_priced_in_its_quote(
    build: Callable[[], object], reason: str
) -> None:
    with pytest.raises(InstrumentInputError, match=reason):
        build()


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda written: written.update(fx_kind="SWAP"), "not contract terms"),
        (lambda written: written["value_dates"].update(tenor="3X"), "tenor does not read"),
    ],
    ids=["kind", "tenor"],
)
def test_written_terms_that_do_not_read_are_refused(
    mutate: Callable[[dict[str, object]], None], reason: str
) -> None:
    written = terms_to_primitives(FORWARD)
    assert written is not None
    mutate(written)
    with pytest.raises(InstrumentInputError, match=reason):
        terms_from_primitives(written)
