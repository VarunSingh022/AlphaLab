"""Classification dimensions beyond sector, and limits on their buckets (ledger OFE-001).

Sector was the one dimension an instrument could be classified along, and its
exposure was visibility only. :func:`~alphalab.instrument.registry.classify_dimension`
classifies along any dimension the caller names -- issuer, country, industry,
rating -- with the sector's provenance rules, the registry indexes every
dimension's buckets, and a :class:`~alphalab.risk.limits.ClassificationLimit`
bounds what one bucket may hold, counting working orders, reduce-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import AssetType
from alphalab.execution.costs import FREE
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument import (
    SECTOR,
    InstrumentInputError,
    InstrumentRecord,
    InstrumentRegistry,
    classify_dimension,
    classify_dimensions,
    classify_instrument,
    dimension_history,
    label_as_of,
    normalize_dimension,
)
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.persistence import deserialize, serialize
from alphalab.risk import ClassificationLimit, RiskValidationError
from alphalab.runtime import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

ACME_A = InstrumentRecord("ACMEA", AssetType.EQUITY, "XNYS", "USD", sector="Industrials")
ACME_B = InstrumentRecord("ACMEB", AssetType.EQUITY, "XNYS", "USD", sector="Industrials")
GOV = InstrumentRecord("GOV10", AssetType.EQUITY, "XNYS", "USD", sector="Government")
LOOSE = InstrumentRecord("LOOSE", AssetType.EQUITY, "XNYS", "USD")
ISSUER = "issuer"


def _registry() -> InstrumentRegistry:
    registry = registry_of(ACME_A, ACME_B, GOV, LOOSE)
    return classify_dimensions(
        registry,
        ISSUER,
        {ACME_A.asset_id: "Acme Corp", ACME_B.asset_id: "Acme Corp", GOV.asset_id: "Treasury"},
        source="vendor-file-2026-09",
    )


# --------------------------------------------------------------------------- #
# The registry: any dimension, with provenance, indexed by bucket
# --------------------------------------------------------------------------- #


def test_a_dimension_is_classified_with_provenance_and_indexed_by_bucket() -> None:
    registry = _registry()

    assert registry.label_of(ACME_A.asset_id, ISSUER) == "Acme Corp"
    assert registry.label_of(LOOSE.asset_id, ISSUER) is None
    assert set(registry.bucket_members(ISSUER, "Acme Corp")) == {
        ACME_A.asset_id,
        ACME_B.asset_id,
    }
    (entry,) = dimension_history(registry, ACME_A.asset_id, ISSUER)
    assert (entry.label, entry.source, entry.as_of) == ("Acme Corp", "vendor-file-2026-09", None)
    # The sector is a dimension the index reads from the record.
    assert set(registry.bucket_members(SECTOR, "Industrials")) == {
        ACME_A.asset_id,
        ACME_B.asset_id,
    }
    assert registry.label_of(GOV.asset_id, SECTOR) == "Government"


def test_a_reclassification_moves_the_bucket_and_keeps_the_history() -> None:
    registry = _registry()
    moved = classify_dimension(
        registry, ACME_B.asset_id, ISSUER, "Acme Holdings", source="analyst", as_of=100.0
    )

    assert set(moved.bucket_members(ISSUER, "Acme Corp")) == {ACME_A.asset_id}
    assert set(moved.bucket_members(ISSUER, "Acme Holdings")) == {ACME_B.asset_id}
    assert [entry.label for entry in dimension_history(moved, ACME_B.asset_id, ISSUER)] == [
        "Acme Corp",
        "Acme Holdings",
    ]
    assert label_as_of(moved, ACME_B.asset_id, ISSUER, 99.0) == "Acme Corp"
    assert label_as_of(moved, ACME_B.asset_id, ISSUER, 100.0) == "Acme Holdings"
    # The registry it came from is unchanged.
    assert set(registry.bucket_members(ISSUER, "Acme Corp")) == {
        ACME_A.asset_id,
        ACME_B.asset_id,
    }

    withdrawn = classify_dimension(moved, ACME_B.asset_id, ISSUER, None)
    assert withdrawn.label_of(ACME_B.asset_id, ISSUER) is None
    assert ACME_B.asset_id not in withdrawn.bucket_members(ISSUER, "Acme Holdings")


def test_saying_nothing_new_returns_the_same_registry() -> None:
    registry = _registry()

    same = classify_dimension(
        registry, ACME_A.asset_id, ISSUER, "Acme Corp", source="vendor-file-2026-09"
    )
    assert same is registry
    assert classify_dimension(registry, LOOSE.asset_id, ISSUER, None) is registry
    # The same label from a new source is a real act: recorded, bucket unchanged.
    restated = classify_dimension(registry, ACME_A.asset_id, ISSUER, "Acme Corp", source="desk")
    assert len(dimension_history(restated, ACME_A.asset_id, ISSUER)) == 2
    assert restated.bucket_members(ISSUER, "Acme Corp") == registry.bucket_members(
        ISSUER, "Acme Corp"
    )


def test_the_sector_keeps_its_own_path_and_a_classified_sector_moves_its_bucket() -> None:
    registry = _registry()
    with pytest.raises(InstrumentInputError, match="classify_instrument"):
        classify_dimension(registry, ACME_A.asset_id, "Sector", "Energy")

    moved = classify_instrument(registry, ACME_A.asset_id, "Energy")
    assert set(moved.bucket_members(SECTOR, "Energy")) == {ACME_A.asset_id}
    assert set(moved.bucket_members(SECTOR, "Industrials")) == {ACME_B.asset_id}


@pytest.mark.parametrize("name", ["", "  ", "has space", "-leading", "ünïcode", "x" * 65])
def test_a_dimension_name_is_an_identifier(name: str) -> None:
    with pytest.raises(InstrumentInputError, match="dimension"):
        normalize_dimension(name)


def test_dimension_names_are_normalized() -> None:
    assert normalize_dimension(" Country ") == "country"
    registry = classify_dimension(_registry(), GOV.asset_id, "COUNTRY", "US")
    assert registry.label_of(GOV.asset_id, "country") == "US"


def test_an_unregistered_instrument_cannot_be_classified() -> None:
    with pytest.raises(InstrumentInputError, match="No instrument"):
        classify_dimension(_registry(), "not-registered", ISSUER, "Acme Corp")


def test_the_index_is_the_same_built_or_kept() -> None:
    registry = _registry()
    rebuilt = InstrumentRegistry(
        instruments=registry.instruments,
        by_provider=registry.by_provider,
        classifications=registry.classifications,
        dated=registry.dated,
        dimensions=registry.dimensions,
    )

    assert rebuilt.members == registry.members
    assert rebuilt == registry


def test_dimensions_survive_a_registry_snapshot_and_rebuild_the_index() -> None:
    registry = classify_dimension(_registry(), GOV.asset_id, "country", "US", as_of=5.0)

    payload = deserialize(serialize(capture_registry(registry)))
    restored = restore_registry(registry_from_primitives(payload))

    assert payload["schema_version"] == 3
    assert restored == registry
    assert set(restored.bucket_members(ISSUER, "Acme Corp")) == {
        ACME_A.asset_id,
        ACME_B.asset_id,
    }


def test_a_version_2_registry_payload_reads_with_no_dimension() -> None:
    payload = deserialize(serialize(capture_registry(registry_of(ACME_A))))
    payload["schema_version"] = 2
    for entry in payload["instruments"]:
        del entry["dimensions"]

    restored = restore_registry(registry_from_primitives(payload))

    assert restored.dimensions == {}
    assert set(restored.bucket_members(SECTOR, "Industrials")) == {ACME_A.asset_id}


@pytest.mark.parametrize("name", ["sector", "Not A Name"])
def test_a_registry_payload_naming_no_dimension_is_refused(name: str) -> None:
    from alphalab.persistence.exceptions import StateDecodeError

    payload = deserialize(serialize(capture_registry(registry_of(ACME_A))))
    payload["instruments"][0]["dimensions"] = {name: []}

    with pytest.raises(StateDecodeError, match="dimension"):
        registry_from_primitives(payload)


# --------------------------------------------------------------------------- #
# The limit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: ClassificationLimit(ISSUER), "max_gross, a max_share or both"),
        (lambda: ClassificationLimit(ISSUER, max_gross=Decimal("-1")), "non-negative"),
        (lambda: ClassificationLimit(ISSUER, max_share=Decimal("NaN")), "finite"),
        (lambda: ClassificationLimit("Not A Name", max_gross=Decimal("1")), "malformed"),
        (
            lambda: ClassificationLimit(
                ISSUER, max_gross=Decimal("1"), label="Acme", refuse_unclassified=True
            ),
            "whole dimension",
        ),
    ],
)
def test_a_malformed_limit_is_refused(build: Any, message: str) -> None:
    with pytest.raises(RiskValidationError, match=message):
        build()


def test_a_labelled_limit_replaces_the_dimensions_for_its_bucket() -> None:
    every = ClassificationLimit(ISSUER, max_gross=Decimal("1500"))
    treasury = ClassificationLimit(ISSUER, max_gross=Decimal("50000"), label="Treasury")
    limits = replace(permissive_risk_limits(), classification=(every, treasury))

    assert limits.bucket_limits(ISSUER, "Acme Corp") == (every,)
    assert limits.bucket_limits(ISSUER, "Treasury") == (treasury,)
    assert limits.bucket_limits(ISSUER, None) == (every,)
    assert limits.bucket_limits("country", "US") == ()


# --------------------------------------------------------------------------- #
# On the execution path
# --------------------------------------------------------------------------- #


class _Planned(BaseStrategy):
    """At each planned instant, orders ``quantity`` of the quoted asset."""

    def __init__(self, plan: Mapping[float, tuple[str, OrderTerms]]) -> None:
        self.plan = dict(plan)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        planned = self.plan.get(event.quote.timestamp)
        if planned is None:
            return ()
        quantity, terms = planned
        return (
            Intent(
                "A",
                event.quote.asset_id,
                Decimal(quantity),
                terms=terms,
                timestamp=event.quote.timestamp,
            ),
        )


MARKET = OrderTerms.market()


def _pipeline(
    plan: Mapping[float, tuple[str, OrderTerms]],
    *limits: ClassificationLimit,
    registry: InstrumentRegistry | None = None,
) -> tuple[ExecutionPipelineState, _Planned]:
    strategy = _Planned(plan)
    config = replace(
        pipeline_config("A", simulator=ExecutionSimulator(cost_model=FREE)),
        risk_limits=replace(permissive_risk_limits(), classification=limits),
        instruments=_registry() if registry is None else registry,
    )
    return ExecutionPipeline.initialize(
        config, running_strategy_state("A", strategy), 1.0
    ), strategy


def _at(state: ExecutionPipelineState, record: InstrumentRecord, timestamp: float) -> Any:
    return ExecutionPipeline.process_quote(
        state, quote(record.asset_id, timestamp, Decimal("100")), context_factory
    )


def _refused(result: Any) -> list[str]:
    return [
        violation.rule
        for decision in result.risk_decisions
        if not decision.approved
        for violation in decision.violations
    ]


ISSUER_CAP = ClassificationLimit(ISSUER, max_gross=Decimal("1500"))


def test_a_bucket_is_capped_across_its_instruments_and_another_bucket_is_not() -> None:
    state, _ = _pipeline({2.0: ("10", MARKET), 3.0: ("6", MARKET), 4.0: ("10", MARKET)}, ISSUER_CAP)

    first = _at(state, ACME_A, 2.0)
    assert [fill.quantity for fill in first.fills] == [Decimal("10")]

    second = _at(first.state, ACME_B, 3.0)  # Acme would hold 1,600
    assert second.fills == ()
    assert _refused(second) == ["ClassificationLimit"]

    other = _at(second.state, GOV, 4.0)  # Treasury's own 1,500 of room
    assert [fill.quantity for fill in other.fills] == [Decimal("10")]


def test_a_share_of_net_asset_value_is_a_ceiling_too() -> None:
    share = ClassificationLimit(ISSUER, max_share=Decimal("0.001"))  # 1,000 of 1,000,000
    state, _ = _pipeline({2.0: ("10", MARKET), 3.0: ("1", MARKET)}, share)

    at_ceiling = _at(state, ACME_A, 2.0)
    assert [fill.quantity for fill in at_ceiling.fills] == [Decimal("10")]
    over = _at(at_ceiling.state, ACME_B, 3.0)

    assert _refused(over) == ["ClassificationShareLimit"]


def test_a_reduction_passes_a_bucket_over_its_limit() -> None:
    state, _ = _pipeline({2.0: ("20", MARKET), 3.0: ("-5", MARKET)})
    over = _at(state, ACME_A, 2.0).state  # 2,000 held before any limit
    over = replace(
        over,
        risk=replace(
            over.risk,
            active_limits=replace(over.risk.active_limits, classification=(ISSUER_CAP,)),
        ),
    )

    reduced = _at(over, ACME_A, 3.0)

    assert [fill.quantity for fill in reduced.fills] == [Decimal("5")]
    assert _refused(reduced) == []


def test_a_reduction_that_leaves_its_bucket_over_the_limit_still_passes() -> None:
    """Reduce-only: 2,000 held against a 1,500 cap, one unit sold, 1,900 left (mutation X13).

    The reduction above lands exactly on the cap, which a limit with no
    reduce-only rule would also pass; this one does not.
    """

    state, _ = _pipeline({2.0: ("20", MARKET), 3.0: ("-1", MARKET)})
    over = _at(state, ACME_A, 2.0).state
    over = replace(
        over,
        risk=replace(
            over.risk,
            active_limits=replace(over.risk.active_limits, classification=(ISSUER_CAP,)),
        ),
    )

    reduced = _at(over, ACME_A, 3.0)

    assert [fill.quantity for fill in reduced.fills] == [Decimal("1")]
    assert _refused(reduced) == []


def test_working_orders_count_toward_their_bucket() -> None:
    resting = OrderTerms.limit(Decimal("90"), TimeInForce.GTC)
    state, _ = _pipeline({2.0: ("10", resting), 3.0: ("6", MARKET)}, ISSUER_CAP)

    working = _at(state, ACME_A, 2.0)
    assert working.fills == () and working.oms_orders
    blocked = _at(working.state, ACME_B, 3.0)

    assert _refused(blocked) == ["ClassificationLimit"]


def test_a_labelled_bucket_can_be_allowed_more_than_the_rest() -> None:
    treasury = ClassificationLimit(ISSUER, max_gross=Decimal("50000"), label="Treasury")
    state, _ = _pipeline({2.0: ("100", MARKET)}, ISSUER_CAP, treasury)

    allowed = _at(state, GOV, 2.0)

    assert [fill.quantity for fill in allowed.fills] == [Decimal("100")]


def test_an_unclassified_instrument_is_outside_every_bucket_unless_refused() -> None:
    state, _ = _pipeline({2.0: ("100", MARKET)}, ISSUER_CAP)
    assert [fill.quantity for fill in _at(state, LOOSE, 2.0).fills] == [Decimal("100")]

    strict = ClassificationLimit(ISSUER, max_gross=Decimal("1500"), refuse_unclassified=True)
    state, _ = _pipeline({2.0: ("100", MARKET)}, strict)
    refused = _at(state, LOOSE, 2.0)

    assert refused.fills == ()
    assert _refused(refused) == ["ClassificationUnclassified"]


def test_the_sector_is_a_dimension_a_limit_can_bound() -> None:
    sector_cap = ClassificationLimit(SECTOR, max_gross=Decimal("1500"))
    state, _ = _pipeline({2.0: ("10", MARKET), 3.0: ("6", MARKET)}, sector_cap)

    state = _at(state, ACME_A, 2.0).state

    assert _refused(_at(state, ACME_B, 3.0)) == ["ClassificationLimit"]


def test_a_limit_with_no_registry_to_read_is_refused_when_the_run_is_configured() -> None:
    config = replace(
        pipeline_config("A"),
        risk_limits=replace(permissive_risk_limits(), classification=(ISSUER_CAP,)),
    )

    with pytest.raises(RuntimeValidationError, match="issuer buckets"):
        ExecutionPipeline.initialize(config, running_strategy_state("A", _Planned({})), 1.0)


def test_the_limits_survive_a_pipeline_snapshot() -> None:
    treasury = ClassificationLimit(ISSUER, max_share=Decimal("0.5"), label="Treasury")
    strict = ClassificationLimit(ISSUER, max_gross=Decimal("1500"), refuse_unclassified=True)
    state, strategy = _pipeline({2.0: ("10", MARKET), 3.0: ("6", MARKET)}, strict, treasury)
    state = _at(state, ACME_A, 2.0).state

    payload = deserialize(serialize(capture(state)))
    assert len(payload["config"]["risk_limits"]["classification"]) == 2
    restored = restore(
        from_primitives(payload),
        RuntimeObjects(
            sizing_model=state.config.sizing_model,
            simulator=state.config.simulator,
            strategies={"A": strategy},
            instruments=state.config.instruments,
        ),
    )

    assert restored == state
    assert _refused(_at(restored, ACME_B, 3.0)) == ["ClassificationLimit"]
