"""The security master: identity, metadata, classification, provenance, history.

v2.11 made the registry the authority on *what* an instrument is classified as.
It could not say **who said so, from what source, or as of when**, and a
reclassification overwrote the previous label leaving no trace --
`classify_instrument`'s own docstring described it as happening "silently, with
no refusal and no event".

This suite holds what v2.15 completes, and -- as importantly -- holds the v2.11
and ADR-0016 behaviour that must not have changed underneath it: derived
identity, refusal-not-overwrite, the identity key excluding sector, and
attribution frozen per fill.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.core.enums import AssetType
from alphalab.instrument.classification import (
    OPERATOR,
    ClassificationHistory,
    SectorClassification,
)
from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classification_history,
    classification_of,
    classify_instrument,
    classify_instruments,
    get_instrument,
    register_instrument,
    register_instruments,
    sector_as_of,
)
from alphalab.instrument.snapshot import (
    INSTRUMENT_SNAPSHOT_SCHEMA,
    capture,
    from_primitives,
    restore,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize

_VENDOR = "GICS-VENDOR"


def _equity(symbol: str, sector: str | None = None) -> InstrumentRecord:
    return InstrumentRecord(symbol, AssetType.EQUITY, "XNAS", "USD", sector=sector)


def _registry() -> InstrumentRegistry:
    return register_instruments(
        InstrumentRegistry(),
        [
            InstrumentRecord(
                "ACME", AssetType.EQUITY, "XNAS", "USD", aliases={"yahoo": "ACME", "iex": "ACME.N"}
            ),
            _equity("BETA"),
        ],
    )


def _acme() -> str:
    return _equity("ACME").asset_id


# ---------------------------------------------------------------------------
# Authoritative lookup and immutable identity (v2.7/v2.11 -- must not regress)
# ---------------------------------------------------------------------------


def test_identity_is_derived_and_reproducible_without_a_shared_database() -> None:
    assert _equity("ACME").asset_id == _equity("ACME").asset_id
    assert _equity("ACME").asset_id != _equity("BETA").asset_id


def test_the_registry_answers_both_authoritative_questions() -> None:
    registry = _registry()

    assert registry.resolve("yahoo", "ACME") == _acme()
    assert registry.resolve("iex", "ACME.N") == _acme()
    assert get_instrument(registry, _acme()).exchange == "XNAS"
    assert registry.resolve("yahoo", "NOT-LISTED") is None


def test_classification_never_changes_identity() -> None:
    """ADR-0016 N5, restated with provenance now attached."""

    registry = _registry()
    before = get_instrument(registry, _acme())

    classified = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    after = get_instrument(classified, _acme())

    assert after.asset_id == before.asset_id
    assert after.canonical_key == before.canonical_key
    assert classified.resolve("yahoo", "ACME") == before.asset_id


def test_registering_different_content_is_still_refused() -> None:
    """Same identity key, different content. A changed `asset_type` would derive
    a *different* asset_id and is a different instrument, so the refusal has to
    be provoked with a field outside the key -- which is also the case the
    registry docstring names: a stale, unclassified record must not silently
    un-classify a classified one."""

    registry = register_instrument(InstrumentRegistry(), _equity("ACME", sector="Technology"))

    with pytest.raises(InstrumentRegistrationError, match="already registered"):
        register_instrument(registry, _equity("ACME"))


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_a_classification_records_who_said_so_and_when() -> None:
    """The v2.15 gap: a bare label becomes an attributable, dated act."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 1_700_000_000.0)

    current = classification_of(registry, _acme())
    assert current is not None
    assert current.sector == "Technology"
    assert current.source == _VENDOR
    assert current.as_of == 1_700_000_000.0
    assert get_instrument(registry, _acme()).sector == "Technology", "the label is where it was"


def test_classifying_without_naming_a_source_attributes_the_operator() -> None:
    """Not a sentinel for unknown: somebody called this and named nobody upstream."""

    registry = classify_instrument(_registry(), _acme(), "Technology")

    current = classification_of(registry, _acme())
    assert current is not None
    assert current.source == OPERATOR
    assert current.as_of is None, "no effective date was declared, and none is invented"


def test_a_blank_source_is_refused() -> None:
    with pytest.raises(InstrumentInputError, match="source cannot be blank"):
        classify_instrument(_registry(), _acme(), "Technology", "   ")


def test_a_sector_set_by_constructing_a_record_has_no_fabricated_provenance() -> None:
    """Absent, not fabricated. Nobody recorded where that label came from."""

    registry = register_instrument(InstrumentRegistry(), _equity("GAMMA", sector="Energy"))
    asset_id = _equity("GAMMA").asset_id

    assert get_instrument(registry, asset_id).sector == "Energy", "the label is readable"
    assert classification_of(registry, asset_id) is None, "its origin is not invented"


def test_no_clock_is_read_when_classifying() -> None:
    """A registry that stamped itself would not reproduce across two processes."""

    first = classify_instrument(_registry(), _acme(), "Technology", _VENDOR)
    second = classify_instrument(_registry(), _acme(), "Technology", _VENDOR)

    assert classification_of(first, _acme()) == classification_of(second, _acme())


# ---------------------------------------------------------------------------
# History: a reclassification is a new fact, not an erasure
# ---------------------------------------------------------------------------


def test_reclassifying_records_both_acts() -> None:
    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 200.0)

    history = classification_history(registry, _acme())
    assert [entry.sector for entry in history] == ["Technology", "Industrials"]
    assert [entry.source for entry in history] == [_VENDOR, "ANALYST"]
    assert get_instrument(registry, _acme()).sector == "Industrials", "the latest is in effect"


def test_the_previous_classification_is_still_readable_after_a_correction() -> None:
    """The audit property. Overwriting in place would be mutable history."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)
    corrected = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 200.0)

    superseded = classification_history(corrected, _acme()).entries[0]
    assert superseded.sector == "Technology"
    assert superseded.source == _VENDOR


def test_the_earlier_registry_is_unchanged_by_a_later_classification() -> None:
    """Copy-on-write: the value a caller holds keeps saying what it said."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)
    _ = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 200.0)

    assert get_instrument(registry, _acme()).sector == "Technology"
    assert len(classification_history(registry, _acme())) == 1


def test_withdrawing_a_classification_is_recorded_rather_than_leaving_a_gap() -> None:
    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)
    withdrawn = classify_instrument(registry, _acme(), None, "ANALYST", 200.0)

    assert get_instrument(withdrawn, _acme()).sector is None
    history = classification_history(withdrawn, _acme())
    assert len(history) == 2
    assert history.entries[-1].sector is None
    assert history.entries[-1].classified is False


def test_the_same_label_from_a_second_source_is_a_real_event() -> None:
    """Corroboration is a fact an audit needs; it is not a no-op."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)
    corroborated = classify_instrument(registry, _acme(), "Technology", "ANALYST", 200.0)

    assert len(classification_history(corroborated, _acme())) == 2
    assert classification_history(corroborated, _acme()).sources == (_VENDOR, "ANALYST")


def test_an_identical_redeclaration_is_a_no_op() -> None:
    """Same label, same source, same date. Nothing happened."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)

    assert classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0) is registry


def test_unclassifying_an_unclassified_instrument_remains_a_no_op() -> None:
    """The v2.11 behaviour, preserved: there is nothing to withdraw."""

    registry = _registry()

    assert classify_instrument(registry, _acme(), None) is registry
    assert classify_instrument(registry, _acme(), None, _VENDOR) is registry
    assert len(classification_history(registry, _acme())) == 0


def test_whitespace_does_not_make_a_second_classification() -> None:
    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)

    assert classify_instrument(registry, _acme(), "  Technology  ", _VENDOR, 100.0) is registry


def test_classifying_in_bulk_attributes_every_entry_to_one_source() -> None:
    """What loading a vendor file actually looks like."""

    registry = classify_instruments(
        _registry(),
        {_acme(): "Technology", _equity("BETA").asset_id: "Energy"},
        _VENDOR,
        500.0,
    )

    for asset_id in (_acme(), _equity("BETA").asset_id):
        current = classification_of(registry, asset_id)
        assert current is not None
        assert current.source == _VENDOR
        assert current.as_of == 500.0


def test_classifying_an_unregistered_instrument_is_still_refused() -> None:
    with pytest.raises(InstrumentInputError, match="No instrument is registered"):
        classify_instrument(_registry(), "not-an-asset-id", "Technology")


def test_history_of_an_unregistered_instrument_is_refused_not_empty() -> None:
    with pytest.raises(InstrumentInputError, match="No instrument is registered"):
        classification_history(_registry(), "not-an-asset-id")


# ---------------------------------------------------------------------------
# Effective classification
# ---------------------------------------------------------------------------


def test_the_sector_in_effect_at_an_instant_is_answerable() -> None:
    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), "Industrials", _VENDOR, 200.0)

    assert sector_as_of(registry, _acme(), 50.0) is None, "not yet classified"
    assert sector_as_of(registry, _acme(), 150.0) == "Technology"
    assert sector_as_of(registry, _acme(), 250.0) == "Industrials"
    assert sector_as_of(registry, _acme(), 200.0) == "Industrials", "effective from, inclusive"


def test_a_withdrawn_classification_leaves_no_sector_in_effect_afterwards() -> None:
    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), None, _VENDOR, 200.0)

    assert sector_as_of(registry, _acme(), 150.0) == "Technology"
    assert sector_as_of(registry, _acme(), 250.0) is None


def test_an_undated_classification_is_in_effect_at_every_instant() -> None:
    """`None` is undated, not epoch. It is the only thing the registry knows."""

    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR)

    assert sector_as_of(registry, _acme(), 0.0) == "Technology"
    assert sector_as_of(registry, _acme(), 1_900_000_000.0) == "Technology"


def test_a_backfilled_correction_supersedes_what_it_corrects() -> None:
    """Declaration order breaks the tie, so a fix wins over the entry it fixes."""

    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 100.0)

    assert sector_as_of(registry, _acme(), 150.0) == "Industrials"
    assert len(classification_history(registry, _acme())) == 2, "both acts are recorded"


# ---------------------------------------------------------------------------
# The classification value itself
# ---------------------------------------------------------------------------


def test_a_classification_normalizes_its_label_the_way_the_registry_does() -> None:
    assert SectorClassification("  Consumer Discretionary  ").sector == "Consumer Discretionary"


def test_a_classification_refuses_a_label_with_a_control_character() -> None:
    with pytest.raises(InstrumentInputError, match="control character"):
        SectorClassification("Tech\nnology")


def test_a_classification_refuses_a_non_finite_effective_date() -> None:
    with pytest.raises(InstrumentInputError, match="finite timestamp"):
        SectorClassification("Technology", _VENDOR, float("inf"))


def test_an_empty_history_answers_every_question_without_raising() -> None:
    history = ClassificationHistory()

    assert history.current is None
    assert history.at(100.0) is None
    assert history.sector_at(100.0) is None
    assert history.sources == ()
    assert not history


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_a_registry_round_trips_through_a_snapshot() -> None:
    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 200.0)

    assert restore(capture(registry)) == registry


def test_a_registry_round_trips_across_json() -> None:
    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)

    payload = serialize(capture(registry))
    assert restore(from_primitives(deserialize(payload))) == registry


def test_the_audit_trail_survives_the_round_trip() -> None:
    """The reason this snapshot exists: history is not re-derivable."""

    registry = _registry()
    registry = classify_instrument(registry, _acme(), "Technology", _VENDOR, 100.0)
    registry = classify_instrument(registry, _acme(), "Industrials", "ANALYST", 200.0)

    reloaded = restore(from_primitives(deserialize(serialize(capture(registry)))))
    history = classification_history(reloaded, _acme())

    assert [(e.sector, e.source, e.as_of) for e in history] == [
        ("Technology", _VENDOR, 100.0),
        ("Industrials", "ANALYST", 200.0),
    ]
    assert sector_as_of(reloaded, _acme(), 150.0) == "Technology"


def test_resolution_still_works_after_a_round_trip() -> None:
    """`by_provider` is rebuilt from the aliases rather than stored twice."""

    reloaded = restore(capture(_registry()))

    assert reloaded.resolve("yahoo", "ACME") == _acme()
    assert reloaded.resolve("iex", "ACME.N") == _acme()


def test_a_snapshot_cannot_assert_an_identity() -> None:
    """`asset_id` is re-derived on restore, never read from the payload."""

    payload = deserialize(serialize(capture(_registry())))
    assert all("asset_id" not in record for record in payload["instruments"])

    reloaded = restore(from_primitives(payload))
    assert _acme() in reloaded.instruments


def test_a_snapshot_declaring_one_instrument_twice_is_refused() -> None:
    payload = deserialize(serialize(capture(_registry())))
    payload["instruments"].append(payload["instruments"][0])

    with pytest.raises(StateDecodeError, match="twice"):
        restore(from_primitives(payload))


def test_a_snapshot_pointing_one_symbol_at_two_instruments_is_refused() -> None:
    registry = register_instruments(
        InstrumentRegistry(),
        [
            InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", aliases={"yahoo": "X"}),
            InstrumentRecord("BETA", AssetType.EQUITY, "XNAS", "USD"),
        ],
    )
    payload = deserialize(serialize(capture(registry)))
    payload["instruments"][1]["aliases"] = {"yahoo": "X"}

    with pytest.raises(StateDecodeError, match="One provider symbol names one instrument"):
        restore(from_primitives(payload))


def test_a_snapshot_from_a_future_schema_is_refused() -> None:
    payload = deserialize(serialize(capture(_registry())))
    payload["schema_version"] = INSTRUMENT_SNAPSHOT_SCHEMA + 1

    with pytest.raises(StateDecodeError):
        from_primitives(payload)


def test_capture_is_deterministic() -> None:
    registry = classify_instrument(_registry(), _acme(), "Technology", _VENDOR, 100.0)

    assert serialize(capture(registry)) == serialize(capture(registry))


# ---------------------------------------------------------------------------
# Downstream attribution is untouched
# ---------------------------------------------------------------------------


def test_a_finished_runs_attribution_is_not_resolved_through_the_registry() -> None:
    """ADR-0027 decision 5, still true: a reclassification cannot rewrite a run.

    The sector is read once and frozen onto each ``TradeRecord`` at fill time.
    Reclassifying afterwards changes the registry and nothing about what the run
    recorded -- which is what this asserts end to end through the real pipeline.
    """

    from dataclasses import replace as dataclass_replace

    from alphalab.backtesting.engine import BacktestEngine
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    strategy_id = "SECMASTER-STRAT"
    instrument = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")
    asset_id = instrument.asset_id
    registry = classify_instrument(
        register_instrument(InstrumentRegistry(), instrument), asset_id, "Technology", _VENDOR, 1.0
    )

    config = backtest_config(strategy_id, seed=7)
    config = dataclass_replace(
        config, pipeline=dataclass_replace(config.pipeline, instruments=registry)
    )
    result = BacktestEngine.run(
        config,
        dataset_of_quotes(asset_id, [Decimal("100"), Decimal("101"), Decimal("102")]),
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
        context_factory,
    )

    trades = result.state.trade_records.to_tuple()
    assert trades, "the run traded"
    assert all(trade.sector_id == "Technology" for trade in trades)

    # Reclassify afterwards. The run's record is frozen and must not move.
    reclassified = classify_instrument(registry, asset_id, "Industrials", "ANALYST", 999.0)
    assert get_instrument(reclassified, asset_id).sector == "Industrials"
    assert all(trade.sector_id == "Technology" for trade in result.state.trade_records.to_tuple())
