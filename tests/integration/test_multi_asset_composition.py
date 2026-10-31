"""A researcher-defined multi-asset strategy over prices, sentiment and macro data (v4.1.1).

The acceptance scenario of v4.1.1. ``examples/72_compose_a_multi_asset_strategy.py``
builds it from the public API: an equity and an index future from two vendor
files read as one input, social-media posts and a US CPI series with revisions
read with the instant each became available, a strategy written against the
protocol that combines them, execution costs, a risk gate that refuses, and the
lineage of the result. This file imports that example and holds it to each
property the scenario exists to establish:

1. every eligible input is consumed;
2. late information is usable only once it was available -- proved from the
   strategy's own log of what it knew, and from runs in which the late values
   are changed, against a control that leaks and is caught;
3. out-of-order, missing, duplicate, unreadable and undated data each have an
   explicit, tested behaviour;
4. the future is booked as a future, in contracts, with its declared multiplier;
5. every order, fill and change to the book goes through the canonical path;
6. refusals and failures are in the result, not hidden;
7. the result names its datasets, strategy, configuration and engine;
8. a rerun -- in this process or a fresh one -- is identical;
9. a replay, a replay paused at any boundary, written to a payload, restored
   and resumed, and a backtest stored and continued are each the same run.

None of it says the strategy makes money. The prices are synthetic.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.api import combine_datasets, combined_market_dataset
from alphalab.backtesting import BacktestEngine, BacktestResult, RunSchedule, replay_snapshot
from alphalab.backtesting.replay import PausedReplay, ReplayBacktest
from alphalab.data.exceptions import DataValidationError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    LifecycleInputError,
    digest_run,
    external_requirements,
    manifest_for_run,
    running_build,
    running_engine,
    verify_manifest,
)
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.nav import NAVCalculator

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "72_compose_a_multi_asset_strategy.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_72", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(example: ModuleType) -> Any:
    return example.inputs()


@pytest.fixture(scope="module")
def result(example: ModuleType, data: Any) -> BacktestResult:
    run: BacktestResult = example.run(data)
    return run


def _decisions(example: ModuleType, run: BacktestResult) -> list[Any]:
    return list(example.instance_of(run).decisions)


def _choices(decision: Any) -> tuple[Any, ...]:
    """What a decision chose and on what reading -- not the ids of what it was told,
    which name the set they came from and so differ between two sets of inputs."""

    return (decision.at, decision.equity_target, decision.future_target, decision.cpi)


def _orders(run: BacktestResult) -> list[tuple[float, str, str, Decimal]]:
    return sorted(
        (order.created_at, order.asset_id, order.side.value, order.quantity) for order in run.orders
    )


# --------------------------------------------------------------------------- #
# 1. Every eligible input is consumed
# --------------------------------------------------------------------------- #


def test_every_price_of_both_instruments_is_read(example: ModuleType, result: Any) -> None:
    market = combined_market_dataset(example.prices())
    by_asset: dict[str, int] = {}
    for record in market.records:
        by_asset[record.payload.asset_id] = by_asset.get(record.payload.asset_id, 0) + 1

    sessions = len(example.SESSIONS)
    assert by_asset == {example.ACME.asset_id: sessions, example.IDX.asset_id: sessions - 1}
    assert result.records_processed == len(market) == 2 * sessions - 1
    assert len(example.instance_of(result)._closes) == sessions


def test_every_observation_knowable_inside_the_data_is_delivered_and_seen(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    last = result.run.last_record_timestamp
    assert last is not None
    deliveries = data.deliveries()
    # Both sets merged into one stream, ordered by when each record became knowable.
    eligible = sorted(
        (delivery for delivery in deliveries if delivery.known_at <= last),
        key=lambda delivery: delivery.order_key,
    )

    # May's CPI is published after the data ends, and only it.
    assert [d.record.period.label for d in deliveries if d.known_at > last] == ["2024-05"]
    assert result.run.observations_delivered == len(eligible) == len(deliveries) - 1
    seen = example.instance_of(result)._seen
    assert [delivery for _, delivery in seen] == [d.delivery_id for d in eligible]


def test_both_instruments_are_traded_on_the_combined_signal(
    example: ModuleType, result: BacktestResult
) -> None:
    traded = {fill.asset_id for fill in result.fills}
    assert traded == {example.ACME.asset_id, example.IDX.asset_id}
    hedges = [d for d in _decisions(example, result) if d.future_target not in (None, Decimal(0))]
    assert hedges, "the inflation hedge was taken"
    assert all(d.cpi is not None and Decimal(d.cpi[2]) >= Decimal("3.5") for d in hedges)


# --------------------------------------------------------------------------- #
# 2. Late information is usable only once it was available
# --------------------------------------------------------------------------- #


def test_no_decision_saw_anything_before_it_was_knowable(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    known = {delivery.delivery_id: delivery.known_at for delivery in data.deliveries()}
    for decision in _decisions(example, result):
        for at, delivery in decision.seen:
            assert at == known[delivery], "delivered at its knowledge instant, exactly"
            assert at <= decision.at


def test_the_late_post_reaches_the_strategy_when_it_was_collected_not_when_written(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    written, collected = (example.ny(text) for text in example.LATE_POST)
    sentiment, _ = data.schedules()
    (late,) = [d for d in sentiment if d.record.stamp.observed_at == written]
    assert late.known_at == collected

    decisions = _decisions(example, result)
    before = [d for d in decisions if d.at < collected]
    after = [d for d in decisions if d.at >= collected]
    assert before and after
    assert all(late.delivery_id not in {delivery for _, delivery in d.seen} for d in before)
    assert all(late.delivery_id in {delivery for _, delivery in d.seen} for d in after)


def _vintage_known_at(example: ModuleType, at: float) -> tuple[str, int, str] | None:
    """The CPI reading a decision at ``at`` may use, worked out from the release rows alone."""

    released: dict[str, tuple[float, int, str]] = {}
    for row in example.macro_rows():
        published = example.ny(row["released"])
        if published > at:
            continue
        period, vintage = row["period"], int(row["vintage"])
        if period not in released or vintage > released[period][1]:
            released[period] = (example.ny(row["period_end"]), vintage, row["value"])
    if not released:
        return None
    period = max(released, key=lambda name: released[name][0])
    return period, released[period][1], released[period][2]


def test_every_decision_used_the_cpi_vintage_published_by_then(
    example: ModuleType, result: BacktestResult
) -> None:
    decisions = _decisions(example, result)
    for decision in decisions:
        expected = _vintage_known_at(example, decision.at)
        assert decision.cpi is not None and expected is not None
        assert (decision.cpi[0], decision.cpi[1], Decimal(decision.cpi[2])) == (
            expected[0],
            expected[1],
            Decimal(expected[2]),
        )
    # Each vintage was in use at some point: the first release, the revision, April.
    used = {(d.cpi[0], d.cpi[1]) for d in decisions if d.cpi is not None}
    assert {("2024-02", 0), ("2024-03", 0), ("2024-03", 1), ("2024-04", 0)} <= used


def test_changing_late_values_changes_nothing_before_they_were_known(
    example: ModuleType, result: BacktestResult
) -> None:
    """The counterfactual: the late post and March's revision rewritten, nothing else."""

    altered = example.run(example.inputs(late_score="-0.95", march_revision="3.90"))
    earliest = example.ny(example.LATE_POST[1])  # the revision is published later still

    assert [o for o in _orders(altered) if o[0] < earliest] == [
        o for o in _orders(result) if o[0] < earliest
    ]
    pairs = list(zip(_decisions(example, result), _decisions(example, altered), strict=True))
    for ours, theirs in pairs:
        if ours.at < earliest:
            assert _choices(ours) == _choices(theirs)
    changed = [ours.at for ours, theirs in pairs if _choices(ours) != _choices(theirs)]
    assert changed and min(changed) >= earliest, "used once known, never before"


def test_a_schedule_that_leaks_is_caught_by_the_same_check(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    """The control: deliver every record when it was *observed*, as a naive join would.

    The late post then moves decisions two weeks before anyone could have read
    it -- so the counterfactual above would have failed had the engine leaked.
    """

    def leaky(inputs: Any) -> list[ObservationDelivery[Any]]:
        return [
            replace(delivery, known_at=delivery.record.stamp.observed_at)
            for delivery in inputs.deliveries()
        ]

    def run_with(inputs: Any) -> BacktestResult:
        run: BacktestResult = BacktestEngine.run(
            example.run_config(),
            combined_market_dataset(inputs.prices),
            example.running(inputs),
            example.CONTEXTS,
            leaky(inputs),
        )
        return run

    plain = run_with(data)
    altered = run_with(example.inputs(late_score="-0.95"))
    earliest = example.ny(example.LATE_POST[1])
    pairs = zip(_decisions(example, plain), _decisions(example, altered), strict=True)
    leaked = [ours.at for ours, theirs in pairs if _choices(ours) != _choices(theirs)]
    assert leaked and min(leaked) < earliest


# --------------------------------------------------------------------------- #
# 3. Out-of-order, missing, duplicate, unreadable and undated data
# --------------------------------------------------------------------------- #


def test_rows_out_of_order_are_delivered_in_the_order_they_became_knowable(
    example: ModuleType, data: Any
) -> None:
    rows = example.sentiment_rows()
    assert rows[0]["posted_at"] == example.LATE_POST[0], "the file leads with the late post"
    sentiment, macro = data.schedules()
    for schedule in (sentiment, macro):
        keys = [delivery.order_key for delivery in schedule]
        assert keys == sorted(keys)


def test_a_repeated_row_and_an_unreadable_one_are_rejected_by_line(data: Any) -> None:
    rejected = {
        rejection.line_number: rejection.findings[0].detail for rejection in data.sentiment.rejected
    }
    assert len(rejected) == 2
    assert any("repeats record" in detail for detail in rejected.values())
    assert any("'n/a' is not a number" in detail for detail in rejected.values())


def test_a_post_with_no_stated_availability_is_counted_and_never_delivered(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    sentiment, _ = data.schedules()
    assert sentiment.unverified == 1
    undated = example.ny("2024-04-15 11:00")
    assert all(d.record.stamp.observed_at != undated for d in sentiment)
    for decision in _decisions(example, result):
        assert all(not delivery.endswith("UNKNOWN") for _, delivery in decision.seen)


def test_a_missing_future_bar_leaves_the_future_alone_rather_than_hedging_at_a_stale_price(
    example: ModuleType, result: BacktestResult
) -> None:
    gap = [
        d
        for d in _decisions(example, result)
        if example.XNYS.local_datetime(d.at).date() == example.FUTURE_GAP
    ]
    assert gap, "the strategy still decided on the equity that day"
    assert all(d.future_target is None for d in gap)
    assert not [
        order
        for order in result.orders
        if order.asset_id == example.IDX.asset_id
        and example.XNYS.local_datetime(order.created_at).date() == example.FUTURE_GAP
    ]


def test_two_datasets_carrying_one_instruments_price_at_one_instant_are_refused(
    example: ModuleType,
) -> None:
    rows = example.bar_rows(example.ACME.symbol, example.EQUITY_CLOSES, example.EQUITY_CLOSE)
    first = example.ingest_prices("EX72-ACME", rows, example.DataAssetClass.EQUITY)
    second = example.ingest_prices("EX72-ACME-COPY", rows[:3], example.DataAssetClass.EQUITY)
    combined = combine_datasets([(first, example.EQUITY_POLICY), (second, example.EQUITY_POLICY)])

    with pytest.raises(DataValidationError, match="choose one source"):
        combined_market_dataset(combined)
    with pytest.raises(DataValidationError, match="given twice"):
        combine_datasets([(first, example.EQUITY_POLICY), (first, example.EQUITY_POLICY)])
    with pytest.raises(DataValidationError, match="two or more"):
        combine_datasets([(first, example.EQUITY_POLICY)])


# --------------------------------------------------------------------------- #
# 4. The future is booked as a future
# --------------------------------------------------------------------------- #


def test_the_future_is_held_in_whole_contracts_and_its_notional_is_never_paid(
    example: ModuleType, result: BacktestResult
) -> None:
    contracts = [fill for fill in result.fills if fill.asset_id == example.IDX.asset_id]
    assert contracts
    assert all(fill.quantity == fill.quantity.to_integral_value() for fill in contracts)
    position = result.state.portfolio.positions[example.IDX.asset_id]
    assert position.multiplier == Decimal("50")
    assert not position.pays_notional
    # A contract's notional is many thousands; the account never paid it.
    notional = sum(fill.quantity * fill.price * 50 for fill in contracts)
    paid = example.START_CASH - result.valuation.cash
    assert abs(paid) < notional


def test_the_book_is_one_currency_and_its_identity_holds(
    example: ModuleType, result: BacktestResult
) -> None:
    portfolio = result.state.portfolio
    assert portfolio.settlement_currencies == ("USD",)
    valuation = result.valuation
    assert valuation.equity == (
        example.START_CASH
        + valuation.realized_pnl
        + valuation.unrealized_pnl
        - valuation.commission_paid
    )
    assert valuation.equity == NAVCalculator.calculate(portfolio.cash, portfolio.positions, "USD")


def test_a_strategy_whose_hedge_instrument_declares_no_multiplier_fails_visibly(
    example: ModuleType, data: Any
) -> None:
    bare = InstrumentRecord(
        "IDXM4", example.AssetType.FUTURE, "XCME", "USD", aliases={example.PROVIDER: "IDXM4"}
    )
    registry = register_instruments(InstrumentRegistry(), (example.ACME, bare))
    config = example.run_config()
    config = replace(config, pipeline=replace(config.pipeline, instruments=registry))
    run = BacktestEngine.run(
        config,
        combined_market_dataset(data.prices),
        example.running(data),
        example.CONTEXTS,
        data.deliveries(),
    )
    (failure,) = run.strategy_failures
    assert "declares no multiplier" in failure.error
    assert not [fill for fill in run.fills if fill.asset_id == example.IDX.asset_id]


# --------------------------------------------------------------------------- #
# 5-6. The canonical path, and refusals in the result
# --------------------------------------------------------------------------- #


def test_every_fill_belongs_to_an_order_and_comes_after_the_decision(
    result: BacktestResult,
) -> None:
    orders = {str(order.order_id.value): order for order in result.orders}
    assert result.fills
    for fill in result.fills:
        order = orders[fill.order_id]
        assert fill.filled_at > order.created_at, "never at the price that decided it"
    assert result.execution_assumptions.optimistic == ()


def test_orders_the_risk_gate_refuses_are_in_the_result_and_never_filled(
    example: ModuleType, result: BacktestResult
) -> None:
    refused = [d for d in result.state.risk.history if not d.approved]
    assert refused
    for decision in refused:
        (violation,) = decision.violations
        assert violation.current_value > violation.allowed_value == Decimal("1000")
    filled = {fill.order_id for fill in result.fills}
    assert not {decision.order_id for decision in refused} & filled
    assert result.strategy_failures == ()


def test_costs_are_charged_on_every_fill(result: BacktestResult) -> None:
    paid = result.state.portfolio.commission_paid["USD"]
    assert paid >= Decimal(len(result.fills)) * Decimal("1.00"), "at least the per-trade fee"


# --------------------------------------------------------------------------- #
# 7. Lineage
# --------------------------------------------------------------------------- #


def test_the_manifest_names_the_combined_datasets_and_checks_them(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    manifest = example.manifest(data, result)
    provenance = data.prices.require_provenance()

    assert result.dataset_id == manifest.dataset_version == provenance.dataset_version
    assert manifest.dataset_content_hash == provenance.content_hash
    assert [version for version, _ in provenance.parts] == [
        dataset.require_provenance().dataset_version for dataset, _ in data.prices.parts
    ]
    assert verify_manifest(manifest)
    assert manifest.engine == running_engine()

    # Each part is named, in order, for a rerun to be given back (FUT-005).
    assert manifest.dataset_parts == provenance.parts
    details = [r.detail for r in external_requirements(manifest)]
    for version, content in provenance.parts:
        assert any(version in d and content in d for d in details)
    # Parts the combined identity was not derived from do not verify.
    swapped = replace(manifest, dataset_parts=tuple(reversed(manifest.dataset_parts)))
    assert not verify_manifest(swapped)

    reversed_parts = combine_datasets(tuple(reversed(data.prices.parts)))
    assert reversed_parts.dataset_version != data.prices.dataset_version
    with pytest.raises(LifecycleInputError, match="dataset supplied"):
        manifest_for_run(
            result,
            reversed_parts,
            example.fingerprint(data),
            running_engine(),
            build=running_build(),
        )


def test_a_dataset_stating_parts_it_was_not_derived_from_is_refused(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    provenance = data.prices.require_provenance()

    class Lying:
        dataset_version = provenance.dataset_version

        def require_provenance(self) -> Any:
            return replace(provenance, parts=tuple(reversed(provenance.parts)))

    with pytest.raises(LifecycleInputError, match="were not derived from"):
        manifest_for_run(
            result, Lying(), example.fingerprint(data), running_engine(), build=running_build()
        )


def test_the_manifest_names_the_schedule_and_refuses_one_the_run_was_not_given(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    manifest = example.manifest(data, result)
    assert manifest.schedule_digest == data.schedule().digest
    assert any(
        manifest.schedule_digest in requirement.detail
        for requirement in external_requirements(manifest)
    )
    # Another information set is another manifest.
    other = example.inputs(late_score="-0.95")
    assert other.schedule().digest != data.schedule().digest
    # A schedule missing what the run delivered last is not this run's.
    last = result.run.last_observation
    assert last is not None
    partial = RunSchedule.of(
        observations=[d for d in data.deliveries() if d.delivery_id != last[1]]
    )
    with pytest.raises(LifecycleInputError, match="does not hold"):
        manifest_for_run(
            result,
            data.prices,
            example.fingerprint(data),
            running_engine(),
            build=running_build(),
            schedule=partial,
        )


def test_the_information_sets_are_part_of_the_strategys_identity(
    example: ModuleType, data: Any
) -> None:
    other = example.inputs(late_score="-0.95")
    assert other.sentiment.observations.version != data.sentiment.observations.version
    assert other.macro.observations.version == data.macro.observations.version
    assert example.fingerprint(other).fingerprint != example.fingerprint(data).fingerprint
    assert data.parameters()["sentiment_set"] == data.sentiment.observations.version


# --------------------------------------------------------------------------- #
# 8. Reruns
# --------------------------------------------------------------------------- #


def test_a_rerun_is_identical(example: ModuleType, result: BacktestResult) -> None:
    assert digest_run(example.run(example.inputs())) == digest_run(result)


def test_a_fresh_interpreter_under_another_hash_seed_produces_the_same_result(
    result: BacktestResult,
) -> None:
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('ex72', {str(EXAMPLE)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['ex72'] = module\n"
        "spec.loader.exec_module(module)\n"
        "from alphalab.lifecycle import digest_run\n"
        "print(digest_run(module.run(module.inputs())).result_id)\n"
    )
    for seed in ("0", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
            cwd=ROOT,
        )
        assert completed.stdout.strip() == digest_run(result).result_id


# --------------------------------------------------------------------------- #
# 9. Replay, pause and resume, stop and continue
# --------------------------------------------------------------------------- #


def _economics(run: BacktestResult) -> tuple[Any, ...]:
    return (
        _orders(run),
        run.fills,
        run.valuation,
        run.run.observations_delivered,
        run.run.last_observation,
    )


def test_the_replay_is_the_backtest(example: ModuleType, data: Any, result: BacktestResult) -> None:
    replay = example.replayed(data)
    assert _economics(replay.backtest) == _economics(result)
    assert example.instance_of(replay.backtest).decisions == _decisions(example, result)


def _boundaries(example: ModuleType) -> Iterator[int]:
    total = len(combined_market_dataset(example.prices()))
    yield from range(0, total, 1)


def test_a_replay_paused_at_any_boundary_resumes_into_the_same_result(
    example: ModuleType, data: Any
) -> None:
    reference = digest_run(example.replayed(data).backtest)
    for boundary in _boundaries(example):
        assert digest_run(example.replayed(data, pause_at=boundary).backtest) == reference


def test_a_backtest_stored_at_any_record_and_continued_is_the_same_result(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    reference = digest_run(result).result_id
    for stop_after in _boundaries(example):
        assert digest_run(example.continued(data, stop_after)).result_id == reference


def test_a_paused_replay_is_held_to_the_schedule_it_was_paused_on(
    example: ModuleType, data: Any
) -> None:
    """Every pause above went through a payload; this one is resumed with another schedule."""

    paused = ReplayBacktest.run(
        example.run_config(),
        combined_market_dataset(data.prices),
        example.running(data),
        example.CONTEXTS,
        pause_when=lambda progress: progress.records_replayed == 20,
        schedule=data.schedule(),
    )
    assert isinstance(paused, PausedReplay)
    snapshot = replay_snapshot.from_primitives(
        deserialize(serialize(replay_snapshot.capture(paused)))
    )
    assert snapshot.schedule_digest == data.schedule().digest
    altered = example.inputs(late_score="-0.95")
    with pytest.raises(StateDecodeError, match="other inputs"):
        replay_snapshot.restore(
            snapshot,
            example.fresh_objects(data),
            combined_market_dataset(data.prices),
            altered.schedule(),
        )
