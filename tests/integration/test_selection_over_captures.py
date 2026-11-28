"""PBO over sixteen captured backtests is PBO over their full histories (M3's exit gate).

ADR-0055 section 6 and ADR-0054. Sixteen variants of one strategy run twice:
captured under a retention policy that keeps a few dozen entries of every log,
and uncaptured with every entry kept. The probability of backtest overfitting
computed through :func:`~alphalab.lifecycle.selection.assess_captures` equals
the one computed from the full histories' analytics, exactly -- the same
fraction, the same selections, the same logits -- because the captured returns
are the full history's ``period_returns``, value for value, whatever the run
kept. The bridge refuses captures that do not share one extractor and one grid.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureRecord,
    CaptureSpec,
    CashFlows,
    Costs,
    Insolvency,
    ReturnExtractor,
    run_captured,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.selection import assess_captures, trial_matrix_from_captures
from alphalab.research.selection import (
    SelectionMeasure,
    TrialMatrix,
    Trim,
    probability_of_backtest_overfitting,
)
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import RunConfig
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import context_factory, scripted_run

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
RECORDS = 240
#: A drifting, oscillating price, so that sixteen variants perform differently.
MIDS = tuple(
    Decimal(100) + Decimal((i * 7) % 23) / 4 - Decimal((i * 3) % 11) / 3 + Decimal(i) / 40
    for i in range(RECORDS)
)
VARIANTS = tuple((every, offset) for every in (2, 3, 4, 5) for offset in range(4))
BOUNDED = RetentionPolicy(market_history=24, steps=10, audit_events=16, results=12)
NET = ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE)
BLOCKS = 8

Run = tuple[RunConfig, MarketDataset, StrategyRuntimeState]


def _plan(every: int, offset: int) -> dict[float, Decimal]:
    """Buy on every ``every``-th record from ``offset``, sell back half a cycle later."""

    plan: dict[float, Decimal] = {}
    for index in range(offset, RECORDS, every):
        plan[2.0 + index] = (
            Decimal(1 + offset) if (index // every) % 2 == 0 else -Decimal(1 + offset)
        )
    return plan


def _label(every: int, offset: int) -> str:
    return f"every-{every}-from-{offset}"


def _bounded(run: Run) -> Run:
    config, dataset, state = run
    return replace(config, pipeline=replace(config.pipeline, retention=BOUNDED)), dataset, state


def _captures(extractor: ReturnExtractor = NET) -> list[CaptureRecord]:
    return [
        run_captured(
            *_bounded(scripted_run(_plan(*variant), MIDS, STRATEGY_ID, ASSET_ID)),
            context_factory,
            capture=CaptureSpec(extractor, trace=False),
        ).record
        for variant in VARIANTS
    ]


def _full_history() -> TrialMatrix:
    columns: dict[str, tuple[float, ...]] = {}
    instants: tuple[float, ...] = ()
    for variant in VARIANTS:
        result = BacktestEngine.run(
            *scripted_run(_plan(*variant), MIDS, STRATEGY_ID, ASSET_ID), context_factory
        )
        report = result.run.pipeline.analytics.reports[-1]
        columns[_label(*variant)] = report.returns.period_returns
        points = {point.timestamp for point in result.run.pipeline.portfolio_snapshots}
        instants = tuple(sorted(points))[1:]
    return TrialMatrix.from_columns(columns, instants, "full-history period returns")


@pytest.fixture(scope="module")
def captures() -> list[CaptureRecord]:
    return _captures()


@pytest.fixture(scope="module")
def full_history() -> TrialMatrix:
    return _full_history()


LABELS = [_label(*variant) for variant in VARIANTS]


@pytest.mark.parametrize("measure", list(SelectionMeasure))
def test_pbo_from_sixteen_captures_is_pbo_from_their_full_histories(
    captures: list[CaptureRecord], full_history: TrialMatrix, measure: SelectionMeasure
) -> None:
    assessed = assess_captures(
        captures, labels=LABELS, blocks=BLOCKS, measure=measure, trim=Trim.NONE
    )
    reference = probability_of_backtest_overfitting(
        full_history, blocks=BLOCKS, measure=measure, trim=Trim.NONE
    )

    assert assessed.matrix.columns == full_history.columns
    assert assessed.matrix.instants == full_history.instants
    assert assessed.overfitting.probability == reference.probability
    assert assessed.overfitting.selected == reference.selected
    assert assessed.overfitting.logits == reference.logits
    assert assessed.overfitting.combinations == 70
    assert len(set(assessed.matrix.columns)) == len(VARIANTS), "the variants should differ"


def test_the_assessment_names_what_it_read(captures: list[CaptureRecord]) -> None:
    assessed = assess_captures(
        captures, labels=LABELS, blocks=BLOCKS, measure=SelectionMeasure.MEAN, trim=Trim.NONE
    )

    assert assessed.extractor_id == NET.extractor_id
    assert assessed.series_ids == tuple(record.series_id for record in captures)
    assert assessed.matrix.labels == tuple(LABELS)
    assert NET.extractor_id in assessed.matrix.measure


def test_captures_under_two_extractors_are_refused(captures: list[CaptureRecord]) -> None:
    gross = ReturnExtractor(
        EVERY_INSTANT, Costs.GROSS, CashFlows.INCLUDED, "USD", Insolvency.REFUSE
    )
    other = run_captured(
        *_bounded(scripted_run(_plan(2, 0), MIDS, STRATEGY_ID, ASSET_ID)),
        context_factory,
        capture=CaptureSpec(gross, trace=False),
    ).record

    with pytest.raises(LifecycleInputError, match="only under one extractor"):
        trial_matrix_from_captures([captures[0], other], labels=["net", "gross"])


def test_captures_over_two_grids_are_refused(captures: list[CaptureRecord]) -> None:
    shorter = run_captured(
        *_bounded(scripted_run(_plan(2, 0), MIDS[:200], STRATEGY_ID, ASSET_ID)),
        context_factory,
        capture=CaptureSpec(NET, trace=False),
    ).record

    with pytest.raises(LifecycleInputError, match="one common set of instants"):
        trial_matrix_from_captures([captures[0], shorter], labels=["whole", "shorter"])


@pytest.mark.parametrize(
    ("labels", "match"),
    [(["only"], "labels"), (["a", "a"], "no trial matrix"), ([], "labels")],
)
def test_labels_are_one_per_capture_and_distinct(
    captures: list[CaptureRecord], labels: list[str], match: str
) -> None:
    with pytest.raises(LifecycleInputError, match=match):
        trial_matrix_from_captures(captures[:2], labels=labels)


def test_no_captures_and_not_captures_are_refused() -> None:
    with pytest.raises(LifecycleInputError, match="at least one"):
        trial_matrix_from_captures([], labels=[])
    with pytest.raises(LifecycleInputError, match="CaptureRecord"):
        trial_matrix_from_captures(["not a capture"], labels=["x"])  # type: ignore[list-item]
