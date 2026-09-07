"""Classification writes a sector and cannot touch an identity.

ADR-0016 N5 excluded ``sector`` from the canonical key so that classification
could arrive later without re-identifying an instrument and orphaning every fill
recorded against it. It also left ``sector`` unwritable: ``register_instrument``
refuses a record whose content differs from one already held, and
``InstrumentRecord.__eq__`` does not ignore ``sector``, so a field documented as
mutable was in practice immutable.

These tests pin the release that fixes that, and the three independent
guarantees it rests on: the field is outside the key, the signature exposes no
identity field, and ``dataclasses.replace`` itself refuses ``asset_id`` because
that field is ``init=False``. See ADR-0027.
"""

import inspect
from dataclasses import replace

import pytest

from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import InstrumentRecord, normalize_sector_label
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instrument,
    classify_instruments,
    get_instrument,
    register_instrument,
    register_instruments,
)

#: ADR-0016's worked example, and the identifier every stability test re-derives.
GOLDEN_ASSET_ID = "2b670078-27a6-57c2-b359-4e64d8809ea2"


def _apple() -> InstrumentRecord:
    return InstrumentRecord(
        "AAPL", AssetType.EQUITY, "XNAS", "USD", aliases={"polygon": "AAPL", "yahoo": "AAPL.US"}
    )


def _microsoft() -> InstrumentRecord:
    return InstrumentRecord("MSFT", AssetType.EQUITY, "XNAS", "USD", aliases={"polygon": "MSFT"})


def _registry() -> InstrumentRegistry:
    return register_instruments(InstrumentRegistry(), (_apple(), _microsoft()))


# --------------------------------------------------------------------------- #
# A. Classification
# --------------------------------------------------------------------------- #


def test_classifying_an_unclassified_instrument_records_the_sector() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(classified, GOLDEN_ASSET_ID).sector == "Technology"


def test_classification_is_keyed_by_asset_id_and_touches_no_other_instrument() -> None:
    registry = _registry()
    classified = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(classified, _microsoft().asset_id).sector is None


def test_the_api_is_keyed_by_asset_id_and_not_by_a_record() -> None:
    """A record would admit one whose identity fields disagree with the registry's."""

    parameters = list(inspect.signature(classify_instrument).parameters)

    assert parameters == ["registry", "asset_id", "sector"]
    assert inspect.signature(classify_instrument).parameters["asset_id"].annotation == "str"


def test_classifying_an_unregistered_asset_id_is_refused_naming_it() -> None:
    with pytest.raises(InstrumentInputError, match="No instrument is registered"):
        classify_instrument(_registry(), "nope", "Technology")


def test_classifying_an_unregistered_asset_id_writes_nothing() -> None:
    registry = _registry()
    with pytest.raises(InstrumentInputError):
        classify_instrument(registry, "nope", "Technology")

    assert registry.record_for("nope") is None
    assert get_instrument(registry, GOLDEN_ASSET_ID).sector is None


def test_the_registry_it_came_from_is_unchanged() -> None:
    """Copy-on-write: a classified and an unclassified view stay simultaneously readable."""

    registry = _registry()
    classified = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(registry, GOLDEN_ASSET_ID).sector is None
    assert get_instrument(classified, GOLDEN_ASSET_ID).sector == "Technology"


def test_classification_mints_no_identifier() -> None:
    from alphalab.common.ids import current_id_position, id_scope

    with id_scope(11):
        before = current_id_position().draws
        classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")
        assert current_id_position().draws == before


# --------------------------------------------------------------------------- #
# B. Bulk classification
# --------------------------------------------------------------------------- #


def test_classify_instruments_applies_every_entry() -> None:
    classified = classify_instruments(
        _registry(), {GOLDEN_ASSET_ID: "Technology", _microsoft().asset_id: "Software"}
    )

    assert get_instrument(classified, GOLDEN_ASSET_ID).sector == "Technology"
    assert get_instrument(classified, _microsoft().asset_id).sector == "Software"


def test_classify_instruments_accepts_none_alongside_labels() -> None:
    classified = classify_instruments(_registry(), {GOLDEN_ASSET_ID: "Technology"})
    cleared = classify_instruments(
        classified, {GOLDEN_ASSET_ID: None, _microsoft().asset_id: "Software"}
    )

    assert get_instrument(cleared, GOLDEN_ASSET_ID).sector is None
    assert get_instrument(cleared, _microsoft().asset_id).sector == "Software"


def test_a_bulk_classification_leaves_the_callers_registry_untouched_on_refusal() -> None:
    """Nothing partial survives: the caller's own value never changed."""

    registry = _registry()
    with pytest.raises(InstrumentInputError):
        classify_instruments(registry, {GOLDEN_ASSET_ID: "Technology", "nope": "Software"})

    assert get_instrument(registry, GOLDEN_ASSET_ID).sector is None


def test_an_empty_bulk_classification_is_the_same_registry() -> None:
    registry = _registry()

    assert classify_instruments(registry, {}) is registry


# --------------------------------------------------------------------------- #
# C. Identity stability -- the guarantee ADR-0016 N5 exists to protect
# --------------------------------------------------------------------------- #


def test_classification_does_not_change_the_asset_id() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(classified, GOLDEN_ASSET_ID).asset_id == GOLDEN_ASSET_ID


def test_classification_does_not_change_the_canonical_key() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(classified, GOLDEN_ASSET_ID).canonical_key == _apple().canonical_key


def test_a_classified_record_re_derives_the_golden_identifier() -> None:
    """The ADR-0016 worked example, asked again of a classified instrument."""

    classified = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")

    assert classified.asset_id == GOLDEN_ASSET_ID
    assert "Technology" not in classified.canonical_key


def test_classification_does_not_change_any_alias_or_resolution() -> None:
    registry = _registry()
    classified = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    assert classified.resolve("polygon", "AAPL") == GOLDEN_ASSET_ID
    assert classified.resolve("yahoo", "AAPL.US") == GOLDEN_ASSET_ID
    assert dict(get_instrument(classified, GOLDEN_ASSET_ID).aliases) == dict(_apple().aliases)


def test_every_provider_resolution_is_identical_before_and_after() -> None:
    registry = _registry()
    classified = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    pairs = [("polygon", "AAPL"), ("yahoo", "AAPL.US"), ("polygon", "MSFT")]

    assert [classified.resolve(*p) for p in pairs] == [registry.resolve(*p) for p in pairs]


# --------------------------------------------------------------------------- #
# D. Identity mutation is unreachable, three ways
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field_name", ["asset_type", "exchange", "symbol", "currency"])
def test_the_classification_api_offers_no_identity_parameter(field_name: str) -> None:
    assert field_name not in inspect.signature(classify_instrument).parameters
    assert field_name not in inspect.signature(classify_instruments).parameters


def test_dataclasses_replace_refuses_to_set_the_derived_identifier() -> None:
    """Pinned as a guarantee rather than left as an accident of ``init=False``."""

    with pytest.raises(ValueError, match="init=False"):
        replace(_apple(), asset_id="anything")


def test_changing_an_identity_field_is_a_different_instrument_not_a_reclassification() -> None:
    moved = replace(_apple(), exchange="XLON")

    assert moved.asset_id != GOLDEN_ASSET_ID


def test_classification_writes_only_the_sector() -> None:
    before = _apple()
    after = get_instrument(
        classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology"), GOLDEN_ASSET_ID
    )

    assert (after.symbol, after.asset_type, after.exchange, after.currency) == (
        before.symbol,
        before.asset_type,
        before.exchange,
        before.currency,
    )


# --------------------------------------------------------------------------- #
# E. Reclassification and unclassification
# --------------------------------------------------------------------------- #


def test_reclassifying_replaces_the_sector() -> None:
    once = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")
    twice = classify_instrument(once, GOLDEN_ASSET_ID, "Healthcare")

    assert get_instrument(twice, GOLDEN_ASSET_ID).sector == "Healthcare"
    assert get_instrument(twice, GOLDEN_ASSET_ID).asset_id == GOLDEN_ASSET_ID


def test_reclassifying_to_the_same_label_is_a_no_op() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    assert classify_instrument(classified, GOLDEN_ASSET_ID, "Technology") is classified


def test_surrounding_whitespace_does_not_make_a_second_classification() -> None:
    """The comparison happens after normalization, so one label typed twice is one label."""

    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    assert classify_instrument(classified, GOLDEN_ASSET_ID, "  Technology  ") is classified


def test_unclassifying_restores_the_absent_state() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")
    cleared = classify_instrument(classified, GOLDEN_ASSET_ID, None)

    assert get_instrument(cleared, GOLDEN_ASSET_ID).sector is None
    assert get_instrument(cleared, GOLDEN_ASSET_ID).asset_id == GOLDEN_ASSET_ID


def test_unclassifying_an_unclassified_instrument_is_a_no_op() -> None:
    registry = _registry()

    assert classify_instrument(registry, GOLDEN_ASSET_ID, None) is registry


def test_a_classification_can_be_reinstated_after_being_withdrawn() -> None:
    registry = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")
    registry = classify_instrument(registry, GOLDEN_ASSET_ID, None)
    registry = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    assert get_instrument(registry, GOLDEN_ASSET_ID).sector == "Technology"


def test_re_registering_a_stale_unclassified_record_is_still_refused() -> None:
    """A stale declaration must not silently un-classify an instrument."""

    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")

    with pytest.raises(InstrumentRegistrationError, match="already registered to a different"):
        register_instrument(classified, _apple())


def test_registering_the_identical_classified_record_is_still_a_no_op() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "Technology")
    again = register_instrument(classified, replace(_apple(), sector="Technology"))

    assert get_instrument(again, GOLDEN_ASSET_ID).sector == "Technology"


def test_repeated_classification_shares_structure_rather_than_rebuilding() -> None:
    """O(1) per classification, as ``PersistentMap.set`` is; N cost O(N), not O(N^2)."""

    registry = _registry()
    stores = set()
    for index in range(200):
        registry = classify_instrument(registry, GOLDEN_ASSET_ID, f"Sector-{index}")
        stores.add(id(registry.instruments._store))

    assert len(stores) == 1
    assert get_instrument(registry, GOLDEN_ASSET_ID).sector == "Sector-199"
    assert isinstance(registry.instruments, PersistentMap)


def test_reclassification_does_not_grow_the_registry() -> None:
    registry = _registry()
    classified = classify_instrument(registry, GOLDEN_ASSET_ID, "Technology")

    assert len(classified.instruments) == len(registry.instruments) == 2


# --------------------------------------------------------------------------- #
# F. What a sector label may be
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("label", ["", "   ", "\t\n"])
def test_a_blank_sector_is_refused_because_none_means_unclassified(label: str) -> None:
    with pytest.raises(InstrumentInputError, match="cannot be empty"):
        normalize_sector_label(label)


@pytest.mark.parametrize("label", ["TE\nCH", "TE\tCH", "TE\x00CH", "\x07TECH", "TECH\x1b"])
def test_a_control_character_is_refused(label: str) -> None:
    with pytest.raises(InstrumentInputError, match="control character"):
        normalize_sector_label(label)


@pytest.mark.parametrize("label", [5, None, 1.5, b"TECH", ["TECH"]])
def test_a_non_string_sector_is_refused(label: object) -> None:
    with pytest.raises(InstrumentInputError, match="must be a string"):
        normalize_sector_label(label)  # type: ignore[arg-type]


def test_a_sector_name_containing_a_space_is_accepted() -> None:
    """``normalize_key_field`` would refuse this; a sector derives no identifier."""

    assert normalize_sector_label("Consumer Discretionary") == "Consumer Discretionary"


def test_a_non_ascii_sector_name_is_accepted() -> None:
    assert normalize_sector_label("Tecnología") == "Tecnología"


def test_surrounding_whitespace_is_stripped() -> None:
    assert normalize_sector_label("  Energy  ") == "Energy"
    assert normalize_sector_label("Energy\n") == "Energy"


def test_case_is_preserved_and_never_folded() -> None:
    """ADR-0019's rule: a label used as a map key is compared exactly."""

    assert normalize_sector_label("tech") == "tech"
    assert normalize_sector_label("Consumer Staples") == "Consumer Staples"


def test_two_spellings_of_one_sector_stay_two_labels() -> None:
    """Stated rather than repaired: AlphaLab owns no taxonomy to fold into."""

    registry = classify_instrument(_registry(), GOLDEN_ASSET_ID, "tech")
    other = classify_instrument(registry, _microsoft().asset_id, "TECH")

    assert get_instrument(other, GOLDEN_ASSET_ID).sector == "tech"
    assert get_instrument(other, _microsoft().asset_id).sector == "TECH"


def test_a_blank_sector_is_refused_through_the_registry_too() -> None:
    with pytest.raises(InstrumentInputError, match="cannot be empty"):
        classify_instrument(_registry(), GOLDEN_ASSET_ID, "")


def test_the_stored_label_is_the_normalized_one() -> None:
    classified = classify_instrument(_registry(), GOLDEN_ASSET_ID, "  Energy  ")

    assert get_instrument(classified, GOLDEN_ASSET_ID).sector == "Energy"


def test_the_field_name_appears_in_the_refusal() -> None:
    with pytest.raises(InstrumentInputError, match="industry"):
        normalize_sector_label("", "industry")
