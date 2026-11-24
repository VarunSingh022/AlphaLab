"""An FX deal's terms on its record: declared, refused, and persisted with the registry (ADR-0067).

* **Declared.** Spot, a forward by tenor or by an outright date, and an NDF
  with its fixing, each on a currency pair priced in its quote currency, its
  value date's calendars named by id.
* **Refused**, with the reason: a code that is not ISO 4217's shape, one
  currency twice, spot with a forward date, a forward with none or both, a tenor
  without its convention and end-of-month rule, an NDF without its fixing or
  settling outside its pair, no calendar or one named twice; and on a record, a
  pair that is not FOREX, not priced in its quote currency, with economics other
  than a fully paid unit (a lot or a minimum notional may be stated), or
  declaring a settlement rule, margin terms or borrow terms beside its own.
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
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.core.enums import AssetType
from alphalab.instrument.borrow import BorrowAvailability, BorrowTerms
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.margin import MarginBasis, MarginTerms
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
            "one unit of its base",
        ),
        (
            lambda: InstrumentRecord(
                "EURUSD",
                AssetType.FOREX,
                "XFXV",
                "USD",
                economics=InstrumentEconomics(Decimal("1000"), SettlementModel.CASH_EQUITY),
                terms=SPOT,
            ),
            "multiplier of 1000",
        ),
        (
            lambda: InstrumentRecord(
                "EURUSD",
                AssetType.FOREX,
                "XFXV",
                "USD",
                settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 2),
                terms=SPOT,
            ),
            "a second date",
        ),
        (
            lambda: InstrumentRecord(
                "EURUSD",
                AssetType.FOREX,
                "XFXV",
                "USD",
                margin=MarginTerms(MarginBasis.VALUE_RATE, Decimal("0.5"), Decimal("0.3")),
                terms=SPOT,
            ),
            "margin terms: an FX deal holds no position",
        ),
        (
            lambda: InstrumentRecord(
                "EURUSD",
                AssetType.FOREX,
                "XFXV",
                "USD",
                borrow=BorrowTerms(
                    (BorrowAvailability(0.0, Decimal("100"), Decimal("0.01")),), DayCount.ACT_360
                ),
                terms=SPOT,
            ),
            "borrow terms: an FX deal holds no position",
        ),
    ],
    ids=["equity", "priced-in-base", "economics", "multiplier", "settlement", "margin", "borrow"],
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


def test_a_pair_may_state_its_lot_and_minimum_notional() -> None:
    economics = InstrumentEconomics(
        Decimal("1"),
        SettlementModel.CASH_EQUITY,
        lot=LotSpecification(Decimal("1000"), Decimal("1000")),
        minimum_notional=Decimal("10000"),
    )
    record = InstrumentRecord(
        "EURUSD", AssetType.FOREX, "XFXV", "USD", economics=economics, terms=SPOT
    )
    assert record.economics == economics and record.terms == SPOT


def test_the_registry_indexes_its_fx_pairs_as_it_indexes_its_margined_records() -> None:
    plain = InstrumentRecord("EURUSD", AssetType.FOREX, "XFXV", "USD")
    forward = pair("EURUSD3M", "USD", FORWARD)
    registry = register_instruments(InstrumentRegistry(), (plain, forward))
    assert registry.fx_pairs is not None and dict(registry.fx_pairs) == {forward.asset_id: None}
    # Derived again from the records alone, as a restored registry is.
    assert InstrumentRegistry(instruments=registry.instruments).fx_pairs == registry.fx_pairs
    restored = restore(from_primitives(deserialize(serialize(capture(registry)))))
    assert restored.fx_pairs == registry.fx_pairs
    assert not register_instruments(InstrumentRegistry(), (plain,)).fx_pairs
