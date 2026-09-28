"""ACC-004: a run's books do not depend on the caller's decimal context.

``Decimal`` arithmetic rounds to the thread's current context, and so does
``quantize`` unless told otherwise. Measured at v3.9.0, one round trip booked
cash ``999603.30`` under the default context and ``999603.31`` after
``getcontext().rounding = ROUND_DOWN``; a caller that lowered the precision got
an ``InvalidOperation`` from deep inside a fill. v3.9 pinned four modules; v3.10
pins the whole accounting and execution path to
:data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`.

The oracle is the strongest surface there is: a whole backtest -- quoted spread,
square-root impact, percentage commission, a proportional fee and a buy-side
tax, partial reversals -- serialized with ``serialize(capture(...))``, which
must be **byte-identical** under every hostile ambient context to the run under
the default one. A field added to a captured state joins the oracle
automatically.
"""

from __future__ import annotations

import decimal
import hashlib
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.common.arithmetic import in_accounting_context
from alphalab.common.ids import id_scope
from alphalab.core.enums import Side
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    FixedHalfSpread,
    NoImpact,
    NoSlippage,
    ProportionalFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
)
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.record import MarketRecord
from alphalab.persistence import serialize
from alphalab.runtime.broker_routing import RoutingConfig
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
from alphalab.runtime.live import LiveSession
from alphalab.runtime.live_snapshot import capture as capture_live
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.run_snapshot import capture
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)

ASSET = str(uuid.UUID(int=0xACC004))
STRATEGY = "AMBIENT"

#: Built once, in the default context: the inputs are data, not arithmetic the
#: library performs, and a hostile context must not reach them.
QUOTES = tuple(
    sized_quote(
        ASSET,
        2.0 + index,
        Decimal("100.005") + Decimal(index) / Decimal(7),
        Decimal("333"),
        Decimal("0.013"),
    )
    for index in range(12)
)
PLAN = {
    2.0: Decimal("7.3333"),
    4.0: Decimal("-2.1"),
    6.0: Decimal("-9"),
    8.0: Decimal("5.55"),
    10.0: Decimal("-1.7833"),
}
SIMULATOR = ExecutionSimulator(
    cost_model=ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=SquareRootImpact(Decimal("0.1")),
        commission_model=PercentageCommission(Decimal("0.00123")),
        fee_model=ProportionalFee(Decimal("0.0000345")),
        tax_model=ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
    )
)


#: Built once in the default context, like the quotes: the harness computes the
#: budget's exposure ceiling, and that is the test's arithmetic, not the run's.
CONFIG = backtest_config(STRATEGY, simulator=SIMULATOR)


def _run() -> str:
    dataset = MarketDataset.of("DS", QUOTES)
    strategies = running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, PLAN))
    with id_scope(5):
        result = BacktestEngine.run(CONFIG, dataset, strategies, context_factory)
    return serialize(capture(result.run))


REFERENCE = _run()


@pytest.mark.parametrize(
    ("rounding", "precision"),
    [
        (decimal.ROUND_DOWN, 28),
        (decimal.ROUND_HALF_UP, 28),
        (decimal.ROUND_UP, 50),
        (decimal.ROUND_CEILING, 12),
        (decimal.ROUND_FLOOR, 9),
    ],
)
def test_a_backtest_is_byte_identical_under_any_ambient_context(
    rounding: str, precision: int
) -> None:
    with decimal.localcontext() as context:
        context.rounding = rounding
        context.prec = precision
        assert _run() == REFERENCE


#: The same run over figures with more significant digits than a hostile context
#: keeps -- nine- and ten-digit prices and quantities -- because the scenario
#: above never exceeds eight, and at v3.11 that hid a defect: a sale's signed
#: quantity was negated in the caller's context, which *rounds* a ``Decimal``,
#: so under a precision of five a sale of 185.295944 was booked as 185.30.
LONG_QUOTES = tuple(
    sized_quote(
        ASSET,
        2.0 + index,
        Decimal("1234.56789") + Decimal(index) * Decimal("3.14159"),
        Decimal("100000"),
        Decimal("0.0123"),
    )
    for index in range(12)
)
LONG_PLAN = {
    2.0: Decimal("185.295944"),
    4.0: Decimal("-97.123457"),
    6.0: Decimal("-150.000001"),
    8.0: Decimal("12.345678"),
    10.0: Decimal("49.481836"),
}


def _long_run() -> str:
    dataset = MarketDataset.of("DS", LONG_QUOTES)
    strategies = running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, LONG_PLAN))
    with id_scope(5):
        result = BacktestEngine.run(CONFIG, dataset, strategies, context_factory)
    return serialize(capture(result.run))


LONG_REFERENCE = _long_run()


@pytest.mark.parametrize(
    ("rounding", "precision"),
    [(decimal.ROUND_UP, 3), (decimal.ROUND_FLOOR, 5), (decimal.ROUND_DOWN, 8)],
)
def test_figures_longer_than_the_ambient_precision_are_booked_exactly(
    rounding: str, precision: int
) -> None:
    with decimal.localcontext() as context:
        context.rounding = rounding
        context.prec = precision
        # Compared by digest: pytest's diff of two whole runs takes minutes.
        assert _digest(_long_run()) == _digest(LONG_REFERENCE)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


#: The live path over the same figures: routing to a paper venue that charges
#: the same cost model, and settling what it reports a step later. The venue
#: mirror's bookkeeping, the request and child ledgers and the settlement are
#: arithmetic a backtest never reaches.
LIVE_CONFIG = RunConfig(
    pipeline=replace(CONFIG.pipeline, routing=ExecutionRouting.EXTERNAL),
    mode=ExecutionMode.LIVE,
    seed=4242,
    start_timestamp=1.0,
    compile_analytics=False,
)
LIVE_BROKER = BrokerEngine.initialize("VENUE-X", Decimal("1000000"), "USD")
LIVE_RECORDS = tuple(MarketRecord("DS", quote.timestamp, quote) for quote in LONG_QUOTES)


#: A paper venue sees no quote, so its spread is stated rather than observed and
#: it charges no impact, which needs the liquidity a quote shows.
PAPER_COSTS = replace(
    SIMULATOR.costs, spread_model=FixedHalfSpread(Decimal("0.00617")), impact_model=NoImpact()
)


def _live_run() -> str:
    venue = PaperBroker(PAPER_COSTS)
    strategies = running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, LONG_PLAN))
    with id_scope(5):
        state = LiveSession.initialize(
            LIVE_CONFIG, strategies, LIVE_BROKER, RoutingConfig(venue="VENUE-X", currency="USD")
        )
        state, _ = LiveSession.connect(state, venue, 1.5)
        settled = 0
        for record in LIVE_RECORDS:
            reported = tuple(state.broker.executions.values())
            state, _ = LiveSession.advance(
                state, record, context_factory, venue, executions=reported[settled:]
            )
            settled = len(reported)
        state, _ = LiveSession.settle(state, tuple(state.broker.executions.values())[settled:])
    # Five fills, all settled: the plan's round trip closes flat.
    assert len(state.broker.executions) == len(state.run.pipeline.execution.reports) == 5
    return serialize(capture_live(state))


LIVE_REFERENCE = _live_run()


@pytest.mark.parametrize(
    ("rounding", "precision"),
    [(decimal.ROUND_UP, 3), (decimal.ROUND_FLOOR, 5), (decimal.ROUND_DOWN, 8)],
)
def test_a_live_session_is_byte_identical_under_any_ambient_context(
    rounding: str, precision: int
) -> None:
    with decimal.localcontext() as context:
        context.rounding = rounding
        context.prec = precision
        assert _digest(_live_run()) == _digest(LIVE_REFERENCE)


#: The code object every function :func:`in_accounting_context` wraps shares --
#: so a check can tell that decorator from any other ``functools.wraps``.
_PINNED = in_accounting_context(lambda: None).__code__

#: Entry points that do no arithmetic: publishing a record stores it, and
#: resuming returns the identifier scope a restored run continues in.
_NOTHING_TO_PIN = frozenset({"publish_record", "resume"})


@pytest.mark.parametrize("engine", [ExecutionPipeline, RunEngine, LiveSession])
def test_every_entry_point_of_a_run_is_pinned(engine: type) -> None:
    """Structural, so an entry point added later cannot be left in the caller's context.

    At v3.10 the engines were pinned one by one and the drivers above them were
    not: a live session under a thread precision of five raised
    ``InvalidOperation`` from the venue mirror, and the pipeline negated a
    sale's quantity -- which rounds -- in the caller's context.
    """

    public = {
        name: value.__func__
        for name, value in vars(engine).items()
        if isinstance(value, staticmethod) and not name.startswith("_")
    }
    unpinned = sorted(
        name
        for name, function in public.items()
        if name not in _NOTHING_TO_PIN and function.__code__ is not _PINNED
    )

    assert public and unpinned == []


def test_the_run_leaves_the_callers_context_as_it_found_it() -> None:
    """Pinning is done on copies: the caller's own context is never modified."""

    with decimal.localcontext() as context:
        context.rounding = decimal.ROUND_DOWN
        context.prec = 17
        _run()
        assert decimal.getcontext().rounding == decimal.ROUND_DOWN
        assert decimal.getcontext().prec == 17
