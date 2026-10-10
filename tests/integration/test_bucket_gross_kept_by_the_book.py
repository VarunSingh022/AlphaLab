"""The risk gate reads a bucket's gross from the book, as the sum it replaced (v3.12).

The 10,000-asset stress run (``docs/audit/scripts/stress_v3_12.py assets``)
found every order judged against a classification limit summing its whole
bucket: 1,225 microseconds a record at 10,000 names against 366 at 400, where
the same run with no limit grew 1.23x. The book now keeps each bucket's gross as
it changes (:meth:`~alphalab.portfolio.book.PositionBook.grouped`) and the gate
adds only what working orders commit.

The oracle is the member sum itself: every order of a randomized run -- resting
and marketable limit orders, market orders, flips, flat positions, refusals and
a snapshot restore part-way -- is judged both ways, and the two must agree as
numbers and as written.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.evolve import evolve
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.core.enums import AssetType, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.costs import FREE
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument import SECTOR, InstrumentRecord, InstrumentRegistry, classify_dimensions
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.book import BookMarketValues
from alphalab.risk import ClassificationLimit
from alphalab.risk.projection import BucketExposure, WorkingExposure
from alphalab.runtime import execution_pipeline
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

ISSUER = "issuer"
RECORDS = [
    InstrumentRecord(
        f"N{index:02d}",
        AssetType.EQUITY,
        "XNYS",
        "USD",
        sector=None if index % 9 == 8 else f"S{index % 4}",
    )
    for index in range(30)
]


def _registry() -> InstrumentRegistry:
    issuers = {
        record.asset_id: f"I{index % 5}" for index, record in enumerate(RECORDS) if index % 4
    }
    return classify_dimensions(registry_of(*RECORDS), ISSUER, issuers, source="test")


LIMITS = (
    ClassificationLimit(SECTOR, max_gross=Decimal("9000")),
    ClassificationLimit(ISSUER, max_share=Decimal("0.006")),
)


class _Random(BaseStrategy):
    """On each quote, a random order: market, or a limit resting or crossing."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        observed = event.quote
        rng = random.Random(f"{self.seed}:{observed.timestamp}:{observed.asset_id}")
        if rng.random() < 0.2:
            return ()
        mid = (observed.bid + observed.ask) / 2
        choice = rng.random()
        if choice < 0.4:
            terms = OrderTerms.market()
        elif choice < 0.8:
            # Away from the market: rests, and is working while later orders are judged.
            away = Decimal(rng.choice([-3, -2, 2, 3]))
            terms = OrderTerms.limit(mid + away, TimeInForce.GTC)
        else:
            terms = OrderTerms.limit(mid, TimeInForce.GTC)
        target = Decimal(rng.choice([-25, -10, -5, -1, 1, 5, 10, 25]))
        return (Intent("R", observed.asset_id, target, terms=terms, timestamp=observed.timestamp),)


def _initialized(seed: int) -> tuple[ExecutionPipelineState, _Random]:
    strategy = _Random(seed)
    config = replace(
        pipeline_config("R", simulator=ExecutionSimulator(cost_model=FREE)),
        risk_limits=replace(permissive_risk_limits(), classification=LIMITS),
        instruments=_registry(),
    )
    return ExecutionPipeline.initialize(
        config, running_strategy_state("R", strategy), 1.0
    ), strategy


class _Judged:
    """Every order's buckets, judged from the book's gross and from the member sum."""

    def __init__(self, original: Callable[..., tuple[BucketExposure, ...]]) -> None:
        self.original = original
        self.kept = 0
        self.kept_with_working = 0

    def __call__(
        self,
        state: ExecutionPipelineState,
        request: OrderRequest,
        price: Decimal,
        working: Mapping[str, WorkingExposure],
    ) -> tuple[BucketExposure, ...]:
        judged = self.original(state, request, price, working)
        exposure = state.risk.exposure
        values = exposure.asset_exposure
        summed_state = evolve(
            state,
            risk=evolve(state.risk, exposure=evolve(exposure, asset_exposure=dict(values))),
        )
        summed = self.original(summed_state, request, price, working)

        assert judged == summed
        assert [repr(bucket) for bucket in judged] == [repr(bucket) for bucket in summed]
        if isinstance(values, BookMarketValues) and values.groups is not None:
            self.kept += 1
            if working:
                self.kept_with_working += 1
        return judged


def _play(
    state: ExecutionPipelineState, rng: random.Random, start: float, records: int
) -> tuple[ExecutionPipelineState, int]:
    refused = 0
    for step in range(records):
        record = rng.choice(RECORDS)
        mid = Decimal(rng.randrange(9000, 11000)) / Decimal("100")
        result = ExecutionPipeline.process_quote(
            state, quote(record.asset_id, start + step, mid), context_factory
        )
        refused += sum(1 for decision in result.risk_decisions if not decision.approved)
        state = result.state
    return state, refused


@pytest.mark.parametrize("seed", range(4))
def test_every_order_reads_the_gross_the_member_sum_gives(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    judged = _Judged(execution_pipeline._bucket_exposures)
    monkeypatch.setattr(execution_pipeline, "_bucket_exposures", judged)
    rng = random.Random(seed)

    state, strategy = _initialized(seed)
    assert state.portfolio.book.groups is not None
    state, refused_before = _play(state, rng, 2.0, 150)

    # A restored run keeps the gross again, and is the run it was.
    restored = restore(
        from_primitives(deserialize(serialize(capture(state)))),
        RuntimeObjects(
            sizing_model=state.config.sizing_model,
            simulator=state.config.simulator,
            strategies={"R": strategy},
            instruments=state.config.instruments,
        ),
    )
    assert restored == state
    groups = restored.portfolio.book.groups
    assert groups is not None and groups.source is restored.config.instruments
    state, refused_after = _play(restored, rng, 152.0, 150)

    # The oracle ran on the path it guards, with working orders in play, and
    # the limits bound some orders: none of this passes vacuously.
    assert judged.kept > 150
    assert judged.kept_with_working > 50
    assert refused_before + refused_after > 0


def test_a_run_with_no_classification_limit_keeps_no_groups() -> None:
    config = replace(
        pipeline_config("R", simulator=ExecutionSimulator(cost_model=FREE)),
        instruments=_registry(),
    )
    state = ExecutionPipeline.initialize(config, running_strategy_state("R", _Random(0)), 1.0)

    assert state.portfolio.book.groups is None


def test_a_registry_other_than_the_one_grouped_by_is_summed_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state given another registry cannot read totals kept for the first."""

    state, _ = _initialized(0)
    state, _ = _play(state, random.Random(0), 2.0, 60)
    other = evolve(state, config=replace(state.config, instruments=_registry()))
    request = OrderRequest("O-1", "R", RECORDS[1].asset_id, Side.BUY, Decimal("1"), Decimal("100"))
    called: list[str] = []
    original = execution_pipeline._kept_bucket_gross

    def kept(*args: Any) -> Decimal | None:
        found = original(*args)
        called.append("summed" if found is None else "kept")
        return found

    monkeypatch.setattr(execution_pipeline, "_kept_bucket_gross", kept)
    execution_pipeline._bucket_exposures(other, request, Decimal("100"), {})
    execution_pipeline._bucket_exposures(state, request, Decimal("100"), {})

    assert called == ["summed", "summed", "kept", "kept"]
