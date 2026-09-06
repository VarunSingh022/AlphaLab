"""Canonical instrument identity: derivation, its rules, and the registry.

The golden test in this file is not a formality. Byte-identical derivation
across independent processes is the property that lets two environments agree on
what they traded, and it is a property of an exact string: field order, case
folding, the enum's rendered form and the namespace constant all feed the
digest. Any of them drifting changes every ``asset_id`` in existence, so the key
and the identifier it produces are pinned to literals here rather than
recomputed from the code under test.
"""

from decimal import Decimal
from uuid import UUID

import pytest

from alphalab.core.enums import AssetType, Side
from alphalab.core.fill import Fill
from alphalab.core.ids import AssetId, new_fill_id, new_order_id, new_trade_id, validate_uuid_id
from alphalab.core.trade import Trade
from alphalab.instrument import (
    ALPHALAB_INSTRUMENT_NAMESPACE,
    InstrumentInputError,
    InstrumentRecord,
    InstrumentRegistrationError,
    InstrumentRegistry,
    get_instrument,
    register_alias,
    register_instrument,
    register_instruments,
)

#: The declaration ADR-0016 N2 works through, and the identifier it must yield.
GOLDEN_KEY = "alphalab.instrument.v1\nasset_type=equity\nexchange=XNAS\nsymbol=AAPL\ncurrency=USD"
GOLDEN_ASSET_ID = "2b670078-27a6-57c2-b359-4e64d8809ea2"


def _apple() -> InstrumentRecord:
    return InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD")


# --------------------------------------------------------------------------- #
# A. The golden canonical key and identifier
# --------------------------------------------------------------------------- #


def test_the_namespace_constant_is_the_one_the_adr_fixed() -> None:
    assert UUID("1935bdfa-e8c0-5611-ae10-607c3a67c19b") == ALPHALAB_INSTRUMENT_NAMESPACE


def test_the_canonical_key_renders_exactly_as_specified() -> None:
    """Field order, the scheme tag and the enum's value are all part of the key."""

    assert _apple().canonical_key == GOLDEN_KEY


def test_the_golden_declaration_derives_the_golden_identifier() -> None:
    assert _apple().asset_id == GOLDEN_ASSET_ID


def test_the_asset_type_renders_as_its_value_not_its_name() -> None:
    """``AssetType`` is a StrEnum, so the declared lowercase value is the stable form."""

    assert "asset_type=equity" in _apple().canonical_key
    assert "EQUITY" not in _apple().canonical_key


# --------------------------------------------------------------------------- #
# B. A derived identifier is one core.Fill / core.Trade accepts
# --------------------------------------------------------------------------- #


def test_a_derived_identifier_is_a_version_five_uuid() -> None:
    assert UUID(_apple().asset_id).version == 5


def test_a_derived_identifier_passes_the_core_uuid_check() -> None:
    validate_uuid_id(_apple().asset_id, "asset_id")


def test_a_derived_identifier_constructs_a_real_fill_and_trade() -> None:
    """The whole point of deriving rather than minting: Fill validation is unchanged."""

    fill_id, order_id = new_fill_id(), new_order_id()
    # `AssetId` is a NewType, so the cast is the same no-op the production
    # adapter performs at `runtime.execution_adapters`. What makes the value
    # acceptable is the UUID check inside `Fill`, which this release does not
    # relax -- it supplies a producer that can satisfy it.
    asset_id = AssetId(_apple().asset_id)
    fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        asset_id=asset_id,
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.005"),
        filled_at=1.0,
    )
    trade = Trade(
        trade_id=new_trade_id(),
        asset_id=asset_id,
        side=Side.BUY,
        quantity=Decimal("10"),
        average_price=Decimal("100.005"),
        fill_ids=(fill_id,),
        executed_at=1.0,
    )

    assert fill.asset_id == trade.asset_id == GOLDEN_ASSET_ID


# --------------------------------------------------------------------------- #
# C. Determinism without coordination
# --------------------------------------------------------------------------- #


def test_two_independent_registries_agree_on_one_instrument() -> None:
    """No shared state: the identifier is a function of the declaration alone."""

    left = register_instrument(InstrumentRegistry(), _apple())
    right = register_instrument(InstrumentRegistry(), _apple())

    assert left.resolve("nasdaq", "AAPL") is None, "no alias was declared"
    assert set(left.instruments) == set(right.instruments)
    assert _apple().asset_id == InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD").asset_id


def test_different_instruments_derive_different_identifiers() -> None:
    same_ticker_other_venue = InstrumentRecord("AAPL", AssetType.EQUITY, "XLON", "USD")
    same_ticker_other_currency = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "GBP")
    same_ticker_other_type = InstrumentRecord("AAPL", AssetType.OPTION, "XNAS", "USD")

    derived = {
        _apple().asset_id,
        same_ticker_other_venue.asset_id,
        same_ticker_other_currency.asset_id,
        same_ticker_other_type.asset_id,
    }
    assert len(derived) == 4, "every key field must change the identity"


# --------------------------------------------------------------------------- #
# The field rules that keep derivation reproducible (ADR-0016 N3)
# --------------------------------------------------------------------------- #


def test_case_and_surrounding_whitespace_are_normalized_not_refused() -> None:
    assert InstrumentRecord(" aapl ", AssetType.EQUITY, " xnas ", " usd ").asset_id == (
        GOLDEN_ASSET_ID
    )


@pytest.mark.parametrize(
    ("symbol", "reason"),
    [
        ("", "empty"),
        ("   ", "empty after stripping"),
        ("AA PL", "internal whitespace"),
        ("AA\tPL", "internal tab"),
        ("AAPL\nexchange=XLON", "a newline would forge a second key line"),
        ("AAPL\x00", "control character"),
        ("AAPLÉ", "non-ASCII"),
    ],
)
def test_a_field_that_would_make_derivation_ambiguous_is_refused(symbol: str, reason: str) -> None:
    with pytest.raises(InstrumentInputError):
        InstrumentRecord(symbol, AssetType.EQUITY, "XNAS", "USD")


def test_there_is_no_unknown_sentinel_for_a_missing_exchange_or_currency() -> None:
    """A sentinel would give two different instruments one identity."""

    with pytest.raises(InstrumentInputError, match="cannot be empty"):
        InstrumentRecord("AAPL", AssetType.EQUITY, "", "USD")
    with pytest.raises(InstrumentInputError, match="cannot be empty"):
        InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "")


def test_an_asset_type_that_is_not_an_asset_type_is_refused() -> None:
    with pytest.raises(InstrumentInputError, match="must be an AssetType"):
        InstrumentRecord("AAPL", "equity", "XNAS", "USD")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# D. Resolution
# --------------------------------------------------------------------------- #


def test_a_registered_provider_symbol_resolves_to_its_instrument() -> None:
    record = InstrumentRecord(
        "AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"polygon": "AAPL", "yahoo": "AAPL.US"}
    )
    registry = register_instrument(InstrumentRegistry(), record)

    assert registry.resolve("polygon", "AAPL") == record.asset_id
    assert registry.resolve("yahoo", "AAPL.US") == record.asset_id


def test_an_unregistered_pair_resolves_to_none_rather_than_guessing() -> None:
    """Refusing is the boundary's job; the registry only answers what it knows."""

    registry = register_instrument(InstrumentRegistry(), _apple())

    assert registry.resolve("polygon", "AAPL") is None, "no alias was declared"
    assert registry.resolve("nobody", "ANYTHING") is None


def test_a_registered_instrument_can_be_read_back_by_identifier() -> None:
    registry = register_instrument(InstrumentRegistry(), _apple())

    assert get_instrument(registry, GOLDEN_ASSET_ID).symbol == "AAPL"
    assert registry.record_for("nope") is None
    with pytest.raises(InstrumentInputError, match="No instrument is registered"):
        get_instrument(registry, "nope")


# --------------------------------------------------------------------------- #
# Registration refuses rather than overwrites
# --------------------------------------------------------------------------- #


def test_registering_the_same_instrument_twice_is_a_no_op() -> None:
    once = register_instrument(InstrumentRegistry(), _apple())
    twice = register_instrument(once, _apple())

    assert dict(twice.instruments) == dict(once.instruments)


def test_a_provider_symbol_cannot_be_repointed_at_a_second_instrument() -> None:
    apple = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"p": "X"})
    other = InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD", aliases={"p": "X"})
    registry = register_instrument(InstrumentRegistry(), apple)

    with pytest.raises(InstrumentRegistrationError, match="already resolves to asset_id"):
        register_instrument(registry, other)


def test_registering_several_instruments_indexes_all_of_them() -> None:
    records = [
        InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"p": "AAPL"}),
        InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD", aliases={"p": "MSFT"}),
    ]
    registry = register_instruments(InstrumentRegistry(), records)

    assert [registry.resolve("p", r.symbol) for r in records] == [r.asset_id for r in records]


# --------------------------------------------------------------------------- #
# I. Aliases are lookup keys, never identity inputs
# --------------------------------------------------------------------------- #


def test_declaring_an_alias_does_not_change_the_identifier() -> None:
    bare = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD")
    aliased = InstrumentRecord(
        "AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"polygon": "AAPL", "yahoo": "AAPL.US"}
    )

    assert bare.asset_id == aliased.asset_id == GOLDEN_ASSET_ID
    assert "polygon" not in aliased.canonical_key
    assert "AAPL.US" not in aliased.canonical_key


def test_adding_an_alias_afterwards_does_not_change_the_identifier() -> None:
    registry = register_instrument(InstrumentRegistry(), _apple())
    extended = register_alias(registry, GOLDEN_ASSET_ID, "polygon", "AAPL")

    assert extended.resolve("polygon", "AAPL") == GOLDEN_ASSET_ID
    assert get_instrument(extended, GOLDEN_ASSET_ID).asset_id == GOLDEN_ASSET_ID


def test_an_alias_for_an_unregistered_instrument_is_refused() -> None:
    with pytest.raises(InstrumentInputError, match="No instrument is registered"):
        register_alias(InstrumentRegistry(), "nope", "polygon", "AAPL")


def test_an_alias_may_carry_a_spelling_the_canonical_symbol_could_not() -> None:
    """Aliases reproduce the provider verbatim, so the key rules do not apply."""

    record = InstrumentRecord(
        "AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"bloomberg": "AAPL US Equity"}
    )
    registry = register_instrument(InstrumentRegistry(), record)

    assert registry.resolve("bloomberg", "AAPL US Equity") == GOLDEN_ASSET_ID


# --------------------------------------------------------------------------- #
# J. Sector is declared, absent, and outside the identity
# --------------------------------------------------------------------------- #


def test_sector_is_absent_by_default_and_is_not_part_of_the_identity() -> None:
    """v2.7 adds no security master; a classification must never re-identify an asset."""

    unclassified = _apple()
    classified = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="TECH")

    assert unclassified.sector is None
    assert classified.asset_id == unclassified.asset_id == GOLDEN_ASSET_ID
    assert "TECH" not in classified.canonical_key
    assert "sector" not in classified.canonical_key
