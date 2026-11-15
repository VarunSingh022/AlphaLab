"""A captured run is the uncaptured run, with its series and its history's identity (ADR-0055).

:func:`~alphalab.backtesting.capture.run_captured` drives the step loop
:meth:`~alphalab.backtesting.engine.BacktestEngine.run` drives, with an observer
that reads and changes nothing. These tests hold it to ADR-0055:

* the result is the uncaptured run's, field for field and by ``result_id``, for
  every result type, and the capture draws no identifier (AC-05);
* an ``EVERY_INSTANT``, ``NET``, ``INCLUDED`` series is analytics'
  ``period_returns`` of the same run with full history, value for value;
* ``GROSS`` adds back exactly the commission: the series of a run that paid
  commissions equals the ``NET`` series of the same run paying none;
* ``EXCLUDED`` removes exactly the booked flows: its returns equal the
  flow-free run's change over the flow-bearing run's equity;
* a fixed interval reads the last point at or before each grid instant, compared
  exactly;
* insolvency is refused, or ends the series at the instant it happened;
* the trace is the same under every retention, moves with the history, and a
  bound smaller than one operation's output is refused rather than traced short.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import pairwise
from typing import Any

import pytest

from alphalab.analytics.engine import PortfolioSnapshot
from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureError,
    CaptureSpec,
    CashFlows,
    Costs,
    EveryInstant,
    FixedInterval,
    Insolvency,
    InsolventCaptureError,
    ReturnExtractor,
    _Capture,
    run_captured,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.schedule import Funding, RunSchedule, ScheduledCashFlow
from alphalab.backtesting.state import AlgorithmicResult, BacktestResult, QueuedResult
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.execution.commission import FixedCommission, PerShareCommission
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.lifecycle.reproducibility import digest_run
from alphalab.portfolio.corporate_actions import CashFlow, CashFlowKind
from alphalab.portfolio.events import CashConverted, CashDeposited, CashFlowBooked, CashWithdrawn
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import RunConfig
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import context_factory, scripted_run
from tests.integration.test_algorithms_on_the_historical_path import (
    BUY_100,
    COSTS,
    TWAP_PLAN,
    bars,
)
from tests.integration.test_algorithms_on_the_historical_path import config as algorithmic_config
from tests.integration.test_algorithms_on_the_historical_path import (
    strategies as algorithmic_strategies,
)
from tests.integration.test_queue_model import DEPLETION, QUEUE
from tests.integration.test_queue_model import config as queue_config
from tests.integration.test_queue_model import dataset as queue_dataset
from tests.integration.test_queue_model import strategies as queue_strategies

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
RECORDS = 240
MIDS = tuple(Decimal(100) + Decimal(i % 9) - Decimal(i) / 50 for i in range(RECORDS))
PLAN = {2.0 + i: (Decimal(5) if i % 3 == 0 else Decimal(-2)) for i in range(0, RECORDS, 2)}
COMMISSION = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.35")))

Run = tuple[RunConfig, MarketDataset, StrategyRuntimeState]


def _spec(
    grid: EveryInstant | FixedInterval = EVERY_INSTANT,
    costs: Costs = Costs.NET,
    cash_flows: CashFlows = CashFlows.INCLUDED,
    insolvency: Insolvency = Insolvency.REFUSE,
    *,
    trace: bool = True,
    currency: str = "USD",
) -> CaptureSpec:
    return CaptureSpec(ReturnExtractor(grid, costs, cash_flows, currency, insolvency), trace)


def _run(**settings: Any) -> Run:
    return scripted_run(PLAN, MIDS, STRATEGY_ID, ASSET_ID, **settings)


def _bounded(run: Run, policy: RetentionPolicy) -> Run:
    config, dataset, state = run
    return replace(config, pipeline=replace(config.pipeline, retention=policy)), dataset, state


def _per_instant(snapshots: Sequence[PortfolioSnapshot]) -> dict[float, Decimal]:
    equity: dict[float, Decimal] = {}
    for snapshot in snapshots:
        equity[snapshot.timestamp] = snapshot.total_equity
    return equity


def _returns(equity: Sequence[Decimal]) -> tuple[float, ...]:
    with localcontext(ACCOUNTING_CONTEXT):
        return tuple(float((b - a) / a) for a, b in pairwise(equity))


# --- the result is the uncaptured run's ---------------------------------------------


def _algorithmic() -> tuple[AlgorithmicResult, Any]:
    args = (algorithmic_config(), bars(), algorithmic_strategies(BUY_100), context_factory)
    plain = BacktestEngine.run(*args, execution=TWAP_PLAN)
    captured = run_captured(*args, execution=TWAP_PLAN, capture=_spec())
    return plain, captured


def _queued() -> tuple[QueuedResult, Any]:
    args = (queue_config(), queue_dataset(DEPLETION), queue_strategies(), context_factory)
    plain = BacktestEngine.run(*args, queue=QUEUE)
    captured = run_captured(*args, queue=QUEUE, capture=_spec())
    return plain, captured


def _plain_run() -> tuple[BacktestResult, Any]:
    run = _run()
    return BacktestEngine.run(*run, context_factory), run_captured(
        *run, context_factory, capture=_spec()
    )


@pytest.mark.parametrize(
    ("make", "kind"),
    [(_plain_run, BacktestResult), (_algorithmic, AlgorithmicResult), (_queued, QueuedResult)],
    ids=["plain", "algorithmic", "queued"],
)
def test_a_captured_run_returns_the_uncaptured_result_field_for_field(
    make: Any, kind: type[BacktestResult]
) -> None:
    plain, captured = make()

    assert type(captured.result) is type(plain) is kind
    assert captured.result == plain
    assert digest_run(captured.result).result_id == digest_run(plain).result_id
    # The capture draws no identifier (AC-05): the stream stands where it stood.
    assert captured.result.run.pipeline.id_position == plain.run.pipeline.id_position


def test_the_record_names_the_runs_configuration_and_counts_its_steps() -> None:
    plain, captured = _plain_run()
    record = captured.record

    assert record.configuration_id == digest_run(plain).configuration_id
    # Its initialization, one step per record, and the end of its data.
    assert record.steps == RECORDS + 2
    assert record.traced is not None
    assert record.traced.fills == len(plain.run.pipeline.fills)
    assert record.traced.equity_points == len(plain.run.pipeline.portfolio_snapshots)
    assert record.traced.transactions == len(plain.run.pipeline.portfolio.ledger.transactions)
    assert record.traced.order_events == len(plain.run.pipeline.oms.events)


# --- the series ---------------------------------------------------------------------


def test_every_instant_net_included_is_analytics_period_returns_value_for_value() -> None:
    plain, captured = _plain_run()
    report = plain.run.pipeline.analytics.reports[-1]
    points = _per_instant(plain.run.pipeline.portfolio_snapshots)

    assert captured.record.returns == report.returns.period_returns
    assert captured.record.instants == tuple(points)
    assert len(captured.record.returns) == RECORDS
    assert captured.record.insolvent_at is None


def test_the_series_with_schedule_flows_is_still_analytics_value_for_value() -> None:
    config, dataset, state = _run()
    schedule = _flows()
    plain = BacktestEngine.run(config, dataset, state, context_factory, schedule=schedule)
    captured = run_captured(
        config, dataset, state, context_factory, schedule=schedule, capture=_spec()
    )

    assert captured.result == plain
    assert (
        captured.record.returns == plain.run.pipeline.analytics.reports[-1].returns.period_returns
    )


def test_gross_adds_back_exactly_the_commission_paid() -> None:
    gross = run_captured(
        *_run(simulator=COMMISSION), context_factory, capture=_spec(costs=Costs.GROSS)
    )
    net = run_captured(*_run(simulator=COMMISSION), context_factory, capture=_spec())
    free = run_captured(*_run(), context_factory, capture=_spec())

    assert net.record.returns != free.record.returns, "the run should pay commissions"
    assert gross.record.returns == free.record.returns


def test_gross_places_a_child_fills_commission_in_the_next_point() -> None:
    """An algorithm's children book fills after the record's point; the next point shows them.

    The reference is the same run with the same half-spread and no commission:
    GROSS adds back what reaches the portfolio as one debit -- commission, fees
    and tax -- and leaves in the fill price what is in it.
    """

    assert COSTS.cost_model is not None
    free = replace(
        COSTS, cost_model=replace(COSTS.cost_model, commission_model=FixedCommission(Decimal(0)))
    )
    args = (algorithmic_strategies(BUY_100), context_factory)
    costly = run_captured(
        algorithmic_config(), bars(), *args, execution=TWAP_PLAN, capture=_spec(costs=Costs.GROSS)
    )
    reference = run_captured(
        algorithmic_config(simulator=free),
        bars(),
        algorithmic_strategies(BUY_100),
        context_factory,
        execution=TWAP_PLAN,
        capture=_spec(),
    )
    paid = costly.result.run.pipeline.portfolio.commission_paid

    assert paid.of("USD") > 0, "the children should pay commissions"
    assert costly.record.returns == reference.record.returns


def _flows() -> RunSchedule:
    dividends = [
        ScheduledCashFlow(
            CashFlow(CashFlowKind.DIVIDEND, Decimal("125.50"), "USD", ASSET_ID, "dividend"),
            at=2.0 + index,
        )
        for index in (10, 40, 70, 150)
    ]
    fee = ScheduledCashFlow(CashFlow(CashFlowKind.FEE, Decimal("-30"), "USD", "", "fee"), at=57.0)
    return RunSchedule.of(
        cash_flows=[*dividends, fee], fundings=[Funding(Decimal("5000"), "USD", at=92.0)]
    )


def test_excluded_removes_exactly_the_booked_flows() -> None:
    config, dataset, state = _run()
    schedule = _flows()
    excluded = run_captured(
        config,
        dataset,
        state,
        context_factory,
        schedule=schedule,
        capture=_spec(cash_flows=CashFlows.EXCLUDED),
    )
    included = run_captured(
        config, dataset, state, context_factory, schedule=schedule, capture=_spec()
    )
    with_flows = _per_instant(excluded.result.run.pipeline.portfolio_snapshots)
    without = _per_instant(
        BacktestEngine.run(config, dataset, state, context_factory).run.pipeline.portfolio_snapshots
    )
    instants = list(with_flows)
    assert instants == list(without)

    with localcontext(ACCOUNTING_CONTEXT):
        oracle = tuple(
            float((without[t] - without[s]) / with_flows[s]) for s, t in pairwise(instants)
        )
    assert excluded.record.returns == oracle
    assert excluded.record.returns != included.record.returns


def test_excluded_reads_deposits_withdrawals_and_flows_but_not_conversions() -> None:
    capture = _Capture(_spec(cash_flows=CashFlows.EXCLUDED, trace=False))
    capture._book(
        [
            CashFlowBooked(1.0, "A", "DIVIDEND", "X", Decimal("10"), "USD", ""),
            CashDeposited(2.0, "A", Decimal("100"), "USD"),
            CashWithdrawn(3.0, "A", Decimal("40"), "EUR"),
            CashConverted(
                4.0,
                "A",
                Decimal("50"),
                "USD",
                Decimal("45"),
                "EUR",
                Decimal("0.9"),
                4.0,
                "s",
                False,
            ),
        ]
    )

    assert dict(capture._flows) == {"USD": Decimal("110"), "EUR": Decimal("-40")}


def test_a_fixed_interval_reads_the_last_point_at_or_before_each_instant() -> None:
    seconds, origin = 2.5, 0.7
    captured = run_captured(
        *_run(), context_factory, capture=_spec(grid=FixedInterval(seconds, origin), trace=False)
    )
    points = list(_per_instant(captured.result.run.pipeline.portfolio_snapshots).items())
    first, last = Fraction(points[0][0]), Fraction(points[-1][0])
    step, start = Fraction(seconds), Fraction(origin)
    index = math.ceil((first - start) / step)
    grid = []
    while start + index * step <= last:
        grid.append(start + index * step)
        index += 1
    read = [max((t, e) for t, e in points if Fraction(t) <= instant)[1] for instant in grid]

    assert captured.record.instants == tuple(float(instant) for instant in grid)
    assert captured.record.returns == _returns(read)
    assert captured.record.trace_id is None


def test_a_fixed_interval_on_the_records_own_instants_is_every_instant() -> None:
    every = run_captured(*_run(), context_factory, capture=_spec(trace=False))
    fixed = run_captured(
        *_run(), context_factory, capture=_spec(grid=FixedInterval(1, 0), trace=False)
    )

    assert fixed.record.instants == every.record.instants
    assert fixed.record.returns == every.record.returns


# --- insolvency ---------------------------------------------------------------------


def _insolvent() -> Run:
    """A short of 8 at 100 on 1,000 of capital, and a price that rises to 250.

    Equity is ``1,800 - 8 * price``: zero at 225, below it after.
    """

    mids = [Decimal(100)] * 4 + [Decimal(100 + 15 * step) for step in range(1, 11)]
    return scripted_run(
        {2.0: Decimal(-8)},
        mids,
        STRATEGY_ID,
        ASSET_ID,
        starting_cash=Decimal(1000),
        compile_analytics=False,
    )


def test_insolvency_is_refused_under_refuse() -> None:
    with pytest.raises(InsolventCaptureError) as refused:
        run_captured(*_insolvent(), context_factory, capture=_spec())

    assert refused.value.equity <= 0


def test_insolvency_ends_the_series_at_its_instant_under_truncate() -> None:
    captured = run_captured(
        *_insolvent(), context_factory, capture=_spec(insolvency=Insolvency.TRUNCATE)
    )
    points = _per_instant(captured.result.run.pipeline.portfolio_snapshots)
    instants = list(points)
    end = next(index for index, t in enumerate(instants) if points[t] <= 0)

    assert captured.record.insolvent_at == instants[end]
    assert captured.record.instants == tuple(instants[: end + 1])
    assert captured.record.returns == _returns([points[t] for t in instants[: end + 1]])
    assert captured.record.returns[-1] <= -1.0, "the last return is the wipe-out itself"


# --- the trace ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        RetentionPolicy(market_history=24, steps=10, audit_events=16, results=12),
        RetentionPolicy(results=12, keep_equity=True),
        RetentionPolicy(steps=1),
    ],
    ids=["bounded", "kept-equity", "one-step"],
)
def test_the_trace_and_the_series_are_the_same_under_every_retention(
    policy: RetentionPolicy,
) -> None:
    whole = run_captured(*_run(), context_factory, capture=_spec())
    bounded = run_captured(*_bounded(_run(), policy), context_factory, capture=_spec())

    assert bounded.record.trace_id == whole.record.trace_id
    assert bounded.record.series_id == whole.record.series_id
    assert bounded.record.traced == whole.record.traced
    # What the record kept is another record, as retention says (AC-06).
    assert digest_run(bounded.result).result_id != digest_run(whole.result).result_id
    assert bounded.record.configuration_id == digest_run(bounded.result).configuration_id


def test_the_trace_moves_with_the_history_and_with_the_configuration() -> None:
    reference = run_captured(*_run(), context_factory, capture=_spec()).record
    other_plan = {**PLAN, 2.0: Decimal(6)}
    traded_otherwise = run_captured(
        *scripted_run(other_plan, MIDS, STRATEGY_ID, ASSET_ID), context_factory, capture=_spec()
    ).record
    assert traded_otherwise.trace_id != reference.trace_id

    # A seed is not configuration -- a manifest records it beside it -- but the
    # identifiers it draws name the fills and orders, so the history moves.
    reseeded = run_captured(*_run(seed=7), context_factory, capture=_spec()).record
    assert reseeded.configuration_id == reference.configuration_id
    assert reseeded.trace_id != reference.trace_id

    # A configuration that trades identically is another configuration, and its
    # trace says so; what a statistic reads is the same series.
    config, dataset, state = _run()
    restated = run_captured(
        replace(config, risk_free_rate=0.03), dataset, state, context_factory, capture=_spec()
    ).record
    assert restated.configuration_id != reference.configuration_id
    assert restated.trace_id != reference.trace_id
    assert restated.traced == reference.traced
    assert restated.series_id == reference.series_id


def test_a_bound_smaller_than_one_operations_output_is_refused_not_traced_short() -> None:
    # A record's step places, accepts and fills an order: three order events,
    # where a bound of one keeps one.
    tight = _bounded(_run(), RetentionPolicy(audit_events=1))

    with pytest.raises(CaptureError, match="raise the bound"):
        run_captured(*tight, context_factory, capture=_spec())
    # Without a trace the order events are not read, and the series is whole.
    series = run_captured(*tight, context_factory, capture=_spec(trace=False))
    whole = run_captured(*_run(), context_factory, capture=_spec(trace=False))
    assert series.record.series_id == whole.record.series_id


def test_a_series_in_another_currency_is_refused() -> None:
    with pytest.raises(CaptureError, match="LIM-012"):
        run_captured(*_run(), context_factory, capture=_spec(currency="EUR"))
