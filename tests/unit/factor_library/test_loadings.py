"""Factor loadings read from the factor library's panels, with each factor's lineage."""

from __future__ import annotations

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FactorInputError,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    compute_panel,
    loadings_from_panels,
    observations_from_records,
    percentile_rank_panel,
)

DAY = 86400.0
START = 1_735_689_600.0


def panel(
    rows: dict[float, dict[str, float]], name: str = "f", version: str | None = "ds@v1"
) -> FeaturePanel:
    definition = FeatureDefinition(name, FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=1)
    return FeaturePanel(
        definition=definition,
        dataset_version=version,
        timezone_name="UTC",
        rows={stamp: dict(row) for stamp, row in sorted(rows.items())},
        symbols=tuple(sorted({asset for row in rows.values() for asset in row})),
    )


MOMENTUM = panel({START: {"AAA": 0.9, "BBB": -0.3, "CCC": 0.1}}, "momentum")
VALUE = panel({START: {"AAA": -0.2, "BBB": 0.7, "CCC": 0.0}}, "value")


def test_each_panel_s_cross_section_becomes_one_factor_s_loadings() -> None:
    loadings = loadings_from_panels(
        {"momentum": MOMENTUM, "value": VALUE}, instant=START, source="house model", asset_ids=None
    )

    assert loadings.factors == ("momentum", "value")
    assert loadings.assets == ("AAA", "BBB", "CCC")
    assert loadings.loading("BBB", "value") == 0.7
    assert loadings.as_of == START
    assert loadings.lineage["momentum"] == MOMENTUM.lineage
    assert loadings.source == "house model"


def test_the_lineage_names_every_transform_so_two_normalizations_never_share_an_identity() -> None:
    wire = [
        WireBar(symbol, START + index * DAY, close, close, close, close, 1000.0)
        for symbol, closes in {
            "AAA": [10.0, 11.0, 12.0],
            "BBB": [20.0, 19.0, 18.5],
            "CCC": [5.0, 5.5, 5.4],
        }.items()
        for index, close in enumerate(closes)
    ]
    frame = observations_from_records(wire, FeatureField.CLOSE, "UTC", "ds@v1")
    raw = compute_panel(
        FeatureDefinition("mom", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=1), frame
    )
    ranked = percentile_rank_panel(raw).panel
    instant = START + 2 * DAY

    raw_loadings = loadings_from_panels({"mom": raw}, instant=instant, source="m", asset_ids=None)
    ranked_loadings = loadings_from_panels(
        {"mom": ranked}, instant=instant, source="m", asset_ids=None
    )

    assert ranked_loadings.lineage["mom"] != raw_loadings.lineage["mom"]
    assert "->" in ranked_loadings.lineage["mom"]
    assert ranked_loadings.loadings_id != raw_loadings.loadings_id


def test_symbols_are_mapped_to_asset_ids_only_when_stated() -> None:
    ids = {"AAA": "id-a", "BBB": "id-b", "CCC": "id-c"}
    loadings = loadings_from_panels(
        {"momentum": MOMENTUM}, instant=START, source="m", asset_ids=ids
    )

    assert loadings.assets == ("id-a", "id-b", "id-c")
    with pytest.raises(FactorInputError, match="No asset id"):
        loadings_from_panels(
            {"momentum": MOMENTUM}, instant=START, source="m", asset_ids={"AAA": "id-a"}
        )
    with pytest.raises(FactorInputError, match="silently replace"):
        loadings_from_panels(
            {"momentum": MOMENTUM},
            instant=START,
            source="m",
            asset_ids={"AAA": "x", "BBB": "x", "CCC": "y"},
        )


def test_an_asset_missing_from_one_factor_is_refused_not_given_zero() -> None:
    partial = panel({START: {"AAA": 0.1, "BBB": 0.2}}, "value")
    with pytest.raises(FactorInputError, match="not neutral to it"):
        loadings_from_panels(
            {"momentum": MOMENTUM, "value": partial}, instant=START, source="m", asset_ids=None
        )


def test_empty_instants_mixed_datasets_and_no_panels_are_refused() -> None:
    with pytest.raises(FactorInputError, match="hold no cross-section"):
        loadings_from_panels(
            {"momentum": MOMENTUM}, instant=START + DAY, source="m", asset_ids=None
        )
    other = panel({START: {"AAA": 0.1, "BBB": 0.2, "CCC": 0.3}}, "value", version="ds@v2")
    with pytest.raises(FactorInputError, match="different dataset versions"):
        loadings_from_panels(
            {"momentum": MOMENTUM, "value": other}, instant=START, source="m", asset_ids=None
        )
    with pytest.raises(FactorInputError, match="at least one panel"):
        loadings_from_panels({}, instant=START, source="m", asset_ids=None)
    with pytest.raises(FactorInputError, match="blank"):
        loadings_from_panels({"momentum": MOMENTUM}, instant=START, source="", asset_ids=None)
