"""Point-in-time symbology: a provider symbol resolved at the record's instant (DAT-004).

And the defect found beside it: until v3.11 an alias added with
``register_alias`` was dropped by an instrument-registry snapshot round trip.
"""

import pytest

from alphalab.core.enums import AssetType
from alphalab.instrument import (
    InstrumentRecord,
    InstrumentRegistry,
    register_alias,
    register_instrument,
)
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import DatedAlias
from alphalab.instrument.registry import register_dated_alias
from alphalab.instrument.snapshot import capture, from_primitives, restore
from alphalab.market.exceptions import InstrumentResolutionError
from alphalab.market.normalization import NormalizationPolicy, normalize_wire_quote
from alphalab.marketdata.feed import Quote as WireQuote
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize

RENAME = 1_654_732_800.0  # 2022-06-09, when FB became META
LATER = 1_700_000_000.0

#: The canonical symbol is a permanent identifier, so the instrument's identity
#: survives the ticker change; the tickers are dated aliases of it.
META = InstrumentRecord(
    "BBG000MM2P62",
    AssetType.EQUITY,
    "XNAS",
    "USD",
    dated_aliases=(
        DatedAlias("vendor", "FB", None, RENAME),
        DatedAlias("vendor", "META", RENAME, None),
    ),
)
#: An unrelated company later issued the freed ticker.
REUSER = InstrumentRecord(
    "BBG01REUSE00",
    AssetType.EQUITY,
    "XNYS",
    "USD",
    dated_aliases=(DatedAlias("vendor", "FB", LATER, None),),
)


def _registry() -> InstrumentRegistry:
    return register_instrument(register_instrument(InstrumentRegistry(), META), REUSER)


def _policy(registry: InstrumentRegistry) -> NormalizationPolicy:
    return NormalizationPolicy(identity=registry, provider="vendor", currency="USD")


def _quote(symbol: str, stamp: float) -> WireQuote:
    return WireQuote(symbol, stamp, 99.0, 101.0, 10.0, 10.0)


def test_a_renamed_ticker_resolves_to_one_identity_on_both_sides_of_the_rename() -> None:
    registry = _registry()
    before = normalize_wire_quote(_quote("FB", RENAME - 1.0), _policy(registry))
    after = normalize_wire_quote(_quote("META", RENAME), _policy(registry))
    assert before.asset_id == after.asset_id == META.asset_id


def test_a_symbol_outside_its_interval_is_refused_naming_the_intervals() -> None:
    registry = _registry()
    with pytest.raises(InstrumentResolutionError, match=r"registered only for vendor:META"):
        normalize_wire_quote(_quote("META", RENAME - 1.0), _policy(registry))
    # FB in the gap between the rename and its reuse names nothing.
    with pytest.raises(InstrumentResolutionError, match="names no instrument"):
        normalize_wire_quote(_quote("FB", RENAME + 1.0), _policy(registry))


def test_a_reused_ticker_names_the_new_company_after_its_reissue() -> None:
    registry = _registry()
    assert registry.resolve_at("vendor", "FB", RENAME - 1.0) == META.asset_id
    assert registry.resolve_at("vendor", "FB", LATER) == REUSER.asset_id
    assert registry.resolve_at("vendor", "FB", (RENAME + LATER) / 2) is None
    # The intervals are half-open: the rename instant belongs to the new symbol.
    assert registry.resolve_at("vendor", "FB", RENAME) is None
    assert registry.resolve_at("vendor", "META", RENAME) == META.asset_id


def test_a_timeless_lookup_answers_only_for_static_aliases() -> None:
    assert _registry().resolve("vendor", "FB") is None


def test_an_instant_claimed_twice_is_refused() -> None:
    clash = InstrumentRecord(
        "BBG0CLASH000",
        AssetType.EQUITY,
        "XNYS",
        "USD",
        dated_aliases=(DatedAlias("vendor", "FB", RENAME - 10.0, RENAME + 10.0),),
    )
    with pytest.raises(InstrumentRegistrationError, match="overlaps"):
        register_instrument(_registry(), clash)


def test_static_and_dated_meanings_of_one_symbol_cannot_coexist() -> None:
    registry = _registry()
    with pytest.raises(InstrumentRegistrationError, match="static alias claims every instant"):
        register_alias(registry, REUSER.asset_id, "vendor", "FB")
    static = register_instrument(
        InstrumentRegistry(),
        InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", aliases={"vendor": "ACME"}),
    )
    acme = static.resolve("vendor", "ACME")
    assert acme is not None
    with pytest.raises(InstrumentRegistrationError, match="static alias"):
        register_dated_alias(static, acme, DatedAlias("vendor", "ACME", 0.0, 1.0))


def test_registering_the_same_dated_alias_twice_is_a_no_op() -> None:
    registry = _registry()
    again = register_dated_alias(registry, META.asset_id, DatedAlias("vendor", "FB", None, RENAME))
    assert again is registry


@pytest.mark.parametrize(
    ("provider", "symbol", "start", "end", "message"),
    [
        ("", "FB", None, None, "provider"),
        ("vendor", " ", None, None, "symbol"),
        ("vendor", "FB", 10.0, 10.0, "empty"),
        ("vendor", "FB", 10.0, 5.0, "empty"),
        ("vendor", "FB", float("nan"), None, "finite"),
        ("vendor", "FB", None, float("inf"), "finite"),
        ("vendor", "FB", True, None, "finite"),
    ],
)
def test_a_malformed_dated_alias_is_refused(
    provider: str, symbol: str, start: float | None, end: float | None, message: str
) -> None:
    with pytest.raises(InstrumentInputError, match=message):
        DatedAlias(provider, symbol, start, end)


def test_dated_aliases_are_not_identity() -> None:
    plain = InstrumentRecord("BBG000MM2P62", AssetType.EQUITY, "XNAS", "USD")
    assert plain.asset_id == META.asset_id


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _round_trip(registry: InstrumentRegistry) -> InstrumentRegistry:
    return restore(from_primitives(deserialize(serialize(capture(registry)))))


def test_dated_and_later_aliases_survive_a_round_trip() -> None:
    registry = register_alias(_registry(), META.asset_id, "other", "US30303M1027")
    registry = register_dated_alias(
        registry, REUSER.asset_id, DatedAlias("other", "RSR", LATER, None)
    )
    back = _round_trip(registry)
    assert back == registry
    assert back.resolve("other", "US30303M1027") == META.asset_id
    assert back.resolve_at("other", "RSR", LATER + 1.0) == REUSER.asset_id
    assert back.resolve_at("vendor", "FB", LATER) == REUSER.asset_id


def test_an_alias_registered_after_its_instrument_is_no_longer_lost() -> None:
    """The v3.10 defect: register_alias's alias vanished on restore."""

    record = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"vendor": "AAPL"})
    registry = register_instrument(InstrumentRegistry(), record)
    registry = register_alias(registry, record.asset_id, "vendor", "AAPL.O")
    back = _round_trip(registry)
    assert back.resolve("vendor", "AAPL.O") == record.asset_id
    assert back == registry
    # The declaration itself is unchanged: the later alias is not folded into it.
    assert back.record_for(record.asset_id) == record


def test_the_capture_does_not_depend_on_the_order_later_aliases_were_added() -> None:
    record = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD")
    base = register_instrument(InstrumentRegistry(), record)
    one = register_alias(register_alias(base, record.asset_id, "a", "X"), record.asset_id, "b", "Y")
    two = register_alias(register_alias(base, record.asset_id, "b", "Y"), record.asset_id, "a", "X")
    assert serialize(capture(one)) == serialize(capture(two))


def test_an_edited_snapshot_claiming_an_instant_twice_is_refused() -> None:
    payload = deserialize(serialize(capture(_registry())))
    payload["instruments"][1]["dated_aliases"][0]["valid_from"] = RENAME - 5.0
    with pytest.raises(StateDecodeError, match="overlaps"):
        restore(from_primitives(payload))
