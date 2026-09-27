"""Classifications read from the canonical instrument registry (v3.8)."""

from __future__ import annotations

import pytest

from alphalab.api import currency_classification, sector_classification
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instrument,
    register_instruments,
)

APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")
SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR")
EXXON = InstrumentRecord("XOM", AssetType.EQUITY, "XNYS", "USD")
REGISTRY = classify_instrument(
    classify_instrument(
        register_instruments(InstrumentRegistry(), (APPLE, SAP, EXXON)),
        SAP.asset_id,
        "Technology",
        source="vendor-gics-2026",
        as_of=100.0,
    ),
    EXXON.asset_id,
    "Energy",
    source="analyst correction",
    as_of=200.0,
)


def test_the_current_sector_of_every_instrument_with_every_source_named() -> None:
    sectors = sector_classification(REGISTRY, (EXXON.asset_id, APPLE.asset_id, SAP.asset_id), None)

    assert sectors.dimension == "sector"
    assert sectors.labels == {
        APPLE.asset_id: "Technology",
        SAP.asset_id: "Technology",
        EXXON.asset_id: "Energy",
    }
    assert "vendor-gics-2026" in sectors.source and "analyst correction" in sectors.source
    assert "declared on the instrument record" in sectors.source
    assert sectors.as_of is None


def test_a_historical_sector_is_read_from_the_append_only_history() -> None:
    at_150 = sector_classification(REGISTRY, (SAP.asset_id,), 150.0)

    assert at_150.labels == {SAP.asset_id: "Technology"}
    assert at_150.as_of == 150.0
    with pytest.raises(AlphaLabValidationError, match=r"have no sector at 150\.0"):
        sector_classification(REGISTRY, (EXXON.asset_id,), 150.0)


def test_an_unclassified_instrument_is_refused_by_name() -> None:
    bare = register_instruments(InstrumentRegistry(), (EXXON,))

    with pytest.raises(AlphaLabValidationError, match="not assigned a default sector"):
        sector_classification(bare, (EXXON.asset_id,), None)


def test_unknown_instruments_and_empty_requests_are_refused() -> None:
    with pytest.raises(InstrumentInputError):
        sector_classification(REGISTRY, ("not-registered",), None)
    with pytest.raises(AlphaLabValidationError, match="no instrument"):
        sector_classification(REGISTRY, (), None)
    with pytest.raises(AlphaLabValidationError, match="no instrument"):
        currency_classification(REGISTRY, ())


def test_the_currency_classification_is_the_record_s_own_currency() -> None:
    currencies = currency_classification(REGISTRY, (SAP.asset_id, APPLE.asset_id))

    assert currencies.dimension == "currency"
    assert currencies.labels == {SAP.asset_id: "EUR", APPLE.asset_id: "USD"}
    assert currencies.source == "instrument registry: InstrumentRecord.currency"
