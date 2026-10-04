"""Certify a build of AlphaLab: one statement of what it was checked to do (ledger FEA-006).

Usage, from the repository root::

    python docs/audit/scripts/certify_release.py           # run every check, write the report
    python docs/audit/scripts/certify_release.py --check   # run every check, compare with it

The first form writes ``docs/audit/release_certification.json`` -- the
machine-readable certificate -- and ``docs/audit/RELEASE_CERTIFICATION.md``,
its rendering. The second is what CI runs: it fails when a check fails, *or*
when a check's evidence differs from the committed certificate -- a canonical
run whose identity moved, a reference value that changed -- so a change in what
the engine computes is a reviewed re-certification, never a silent drift.

What is certified
-----------------

Four claims a consumer of a release needs and cannot cheaply check alone:

* **Determinism.** A seeded run is one run: twice in one process, in fresh
  interpreters under different hash seeds, and under a hostile ambient decimal
  context, it produces the byte-identical record.
* **Parity.** A backtest, a replay and a paper session of one dataset produce
  the same orders, fills, trades, cash, positions and equity curve; a live
  session submits the same orders and differs only at the venue.
* **Reproducibility.** A run re-executed from its manifest is ``REPRODUCED``; a
  run stopped, written to JSON, read back and continued finishes byte-identical
  to one that never stopped; every payload v3.9.0, v3.11.0 and v3.12.0 wrote is
  read, or refused for the reason its upgrade documents.
* **Numerical references.** Published values -- J. C. Hull's Black-Scholes-Merton
  example and his convergence table for an American put on a binomial tree --
  and exact identities: put-call parity, early exercise worth nothing on a call
  without dividends, normal-distribution values to the last digit, and a
  sample whose mean and deviation are known exactly despite a large offset.

And one statement about the build itself: its public API is the one
``docs/api/public_api.json`` records for this release (ledger API-002).

The canonical scenario is built here, through the public API alone -- an
ingested dataset with provenance, a declared instrument, a seeded run, and a
strategy that is a pure function of the bar it is shown -- so the certificate
does not rest on the test suite's helpers.
"""

from __future__ import annotations

import argparse
import decimal
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The imports below are the package being certified; they follow the path setup.
from alphalab.allocation.budget import CapitalBudget  # noqa: E402
from alphalab.allocation.constraints import AllocationConstraints  # noqa: E402
from alphalab.api import backtest, ingest_rows, replay, to_market_dataset  # noqa: E402
from alphalab.backtesting.state import BacktestResult  # noqa: E402
from alphalab.common.ids import id_scope  # noqa: E402
from alphalab.common.statistics import mean, normal_cdf, standard_deviation  # noqa: E402
from alphalab.core.enums import AssetType  # noqa: E402
from alphalab.data.cleaning import (  # noqa: E402
    CleaningPolicy,
    DuplicatePolicy,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
)
from alphalab.data.corporate_actions import PriceBasis  # noqa: E402
from alphalab.data.dataset import Dataset  # noqa: E402
from alphalab.data.ingestion import IngestionRequest  # noqa: E402
from alphalab.data.source import SourceKind, raw_source_from_bytes  # noqa: E402
from alphalab.data.symbols import DataAssetClass  # noqa: E402
from alphalab.data.time import BarStamp, TimeFrequency  # noqa: E402
from alphalab.execution.policy import ImmediateFill  # noqa: E402
from alphalab.execution.simulator import ExecutionSimulator  # noqa: E402
from alphalab.instrument.record import InstrumentRecord  # noqa: E402
from alphalab.instrument.registry import InstrumentRegistry, register_instruments  # noqa: E402
from alphalab.lifecycle import (  # noqa: E402
    NO_DEPENDENCIES,
    CodeIdentity,
    RerunOutcome,
    StrategyFingerprint,
    digest_run,
    fingerprint_for_version,
    manifest_for_run,
    rerun_from_manifest,
    research_configuration,
    running_build,
    running_engine,
    source_digest,
)
from alphalab.lifecycle.strategy_version import StrategyVersion  # noqa: E402
from alphalab.market.bar import TimeFrame  # noqa: E402
from alphalab.market.normalization import NormalizationPolicy  # noqa: E402
from alphalab.market.record import MarketRecord  # noqa: E402
from alphalab.market.source import SequenceSource  # noqa: E402
from alphalab.model_registry import ModelStage  # noqa: E402
from alphalab.options import (  # noqa: E402
    BinomialLattice,
    OptionContract,
    binomial_value,
    black_scholes_value,
    dividend_yield,
    implied_volatility,
)
from alphalab.options.enums import ExerciseStyle, OptionType  # noqa: E402
from alphalab.persistence import deserialize, serialize  # noqa: E402
from alphalab.persistence.upgrade import SchemaUpgradeRefused, SchemaUpgradeWarning  # noqa: E402
from alphalab.portfolio.account import Account  # noqa: E402
from alphalab.risk.limits import (  # noqa: E402
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import (  # noqa: E402
    ExecutionPipelineConfig,
    ExecutionRouting,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState  # noqa: E402
from alphalab.runtime.run_snapshot import RunObjects  # noqa: E402
from alphalab.runtime.run_snapshot import capture as capture_run  # noqa: E402
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives  # noqa: E402
from alphalab.runtime.run_snapshot import restore as restore_run  # noqa: E402
from alphalab.runtime.session import TradingSession  # noqa: E402
from alphalab.runtime.snapshot import RuntimeObjects  # noqa: E402
from alphalab.strategy import StrategyDefinition  # noqa: E402
from alphalab.strategy.context import (  # noqa: E402
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    StrategyContext,
)
from alphalab.strategy.events import Intent  # noqa: E402
from alphalab.strategy.protocol import BaseStrategy  # noqa: E402
from alphalab.strategy.runtime import create_runtime, register_strategy  # noqa: E402
from alphalab.strategy.state import RuntimeState  # noqa: E402
from alphalab.strategy.supervisor import RuntimeSupervisor  # noqa: E402

REPORT_JSON = ROOT / "docs" / "audit" / "release_certification.json"
REPORT_MD = ROOT / "docs" / "audit" / "RELEASE_CERTIFICATION.md"
FIXTURES = ROOT / "tests" / "fixtures" / "snapshots"

# --------------------------------------------------------------------------- #
# The canonical scenario
# --------------------------------------------------------------------------- #

SEED = 20_261_004
STRATEGY_ID = "CERT-STRATEGY"
SYMBOL = "CRTF"
PROVIDER = "certification-vendor"
CASH = Decimal("1000000")
CLOSES = ("100.00", "101.25", "100.50", "102.75", "103.10", "101.90", "104.40", "105.05")
#: Bar index -> signed quantity: buy, trim, add, then reverse through flat.
PLAN = {1: Decimal("10"), 3: Decimal("-4"), 5: Decimal("6"), 6: Decimal("-15")}
#: Where the stop-and-continue check stops: after this many records.
STOP_AFTER = 4

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: SYMBOL})
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XNYS",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)


class ScheduledStrategy(BaseStrategy):
    """Trades a fixed quantity at chosen bar instants: a pure function of the bar.

    Keyed by the bar's timestamp rather than a count of bars seen, so a restored
    instance needs no state of its own to continue exactly.
    """

    def __init__(self, plan: Mapping[float, Decimal]) -> None:
        self._plan = dict(plan)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quantity = self._plan.get(event.bar.timestamp)
        if quantity is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=INSTRUMENT.asset_id,
                target=quantity,
                timestamp=event.bar.timestamp,
            ),
        )


class _Clock:
    def now(self) -> float:
        return 0.0


class _Logger:
    def info(self, msg: str) -> None: ...

    def error(self, msg: str) -> None: ...


def context_factory(strategy_id: str) -> StrategyContext:
    return StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=_Clock(),
        logger=_Logger(),
        risk_view=NoRiskView(),
        config={"strategy_id": strategy_id},
        orders=NoOrders(),
    )


def dataset() -> Dataset:
    """The canonical bars, ingested with provenance so the run names their bytes."""

    rows = [
        {
            "symbol": SYMBOL,
            "timestamp": f"2026-01-{day + 5:02d} 00:00:00",
            "open": close,
            "high": str(Decimal(close) + Decimal("0.50")),
            "low": str(Decimal(close) - Decimal("0.50")),
            "close": close,
            "volume": 1_000_000 + day,
        }
        for day, close in enumerate(CLOSES)
    ]
    header = list(rows[0])
    payload = "\n".join(
        [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    ).encode("utf-8")
    request = IngestionRequest(
        name="CERTIFICATION-BARS",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "certification", payload, 1_767_225_600.0, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CleaningPolicy(
            duplicates=DuplicatePolicy.KEEP_FIRST,
            ordering=OrderingPolicy.SORT,
            invalid_records=InvalidRecordPolicy.DROP,
            missing_values=MissingValuePolicy.DROP_ROW,
        ),
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    return ingest_rows(rows, request).dataset


def records(data: Dataset) -> tuple[MarketRecord, ...]:
    return tuple(to_market_dataset(data, NORMALIZATION).records)


def strategy(data: Dataset) -> ScheduledStrategy:
    stream = records(data)
    return ScheduledStrategy({stream[index].timestamp: qty for index, qty in PLAN.items()})


def running(instance: ScheduledStrategy) -> RuntimeState:
    """A strategy runtime holding ``instance``, configured, subscribed and running."""

    state = register_strategy(create_runtime(), STRATEGY_ID, instance)
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={STRATEGY_ID: entry})


def config(mode: ExecutionMode = ExecutionMode.BACKTEST) -> RunConfig:
    huge = Decimal("100000000")
    pipeline = ExecutionPipelineConfig(
        account=Account("CERT-ACCOUNT", "USD", "Certification account", 1.0),
        starting_cash=CASH,
        budget=CapitalBudget(
            global_capital=CASH,
            maximum_exposure=CASH * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={STRATEGY_ID: CASH},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=RiskLimits(
            order_size=OrderSizeLimit(Decimal("100000"), huge),
            position=PositionLimit(huge, huge),
            exposure=ExposureLimit(huge, huge),
            leverage=LeverageLimit(Decimal("1000")),
            margin=MarginLimit(Decimal("1.00")),
            daily_loss=DailyLossLimit(huge, "UTC"),
            drawdown=DrawdownLimit(Decimal("1.00")),
        ),
        simulator=ExecutionSimulator(),
        instruments=REGISTRY,
    )
    if mode is ExecutionMode.LIVE:
        pipeline = replace(pipeline, routing=ExecutionRouting.EXTERNAL)
    return RunConfig(
        pipeline=pipeline,
        mode=mode,
        fill_policy=ImmediateFill(),
        seed=SEED,
        start_timestamp=1.0,
        compile_analytics=mode is not ExecutionMode.LIVE,
    )


def run(data: Dataset) -> BacktestResult:
    """The canonical run: a seeded backtest of the canonical bars."""

    return backtest(config(), data, running(strategy(data)), context_factory, NORMALIZATION)


def record_of(state: RunState) -> str:
    return serialize(capture_run(state))


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Check:
    """One certified claim: what it says, whether it held, and the evidence."""

    check_id: str
    claim: str
    passed: bool
    evidence: dict[str, Any]
    detail: str = ""


def _fixed(value: float, places: int) -> str:
    return f"{value:.{places}f}"


def determinism_in_process(data: Dataset) -> Check:
    first, second = run(data), run(data)
    same = record_of(first.run) == record_of(second.run)
    return Check(
        "DET-1",
        "A seeded run executed twice in one process produces the byte-identical record.",
        same,
        {
            "result_id": digest_run(first).result_id,
            "record_bytes": len(record_of(first.run)),
            "orders": len(first.orders),
            "fills": len(first.fills),
        },
    )


def _run_in_fresh_interpreter(hash_seed: str) -> str:
    environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
    completed = subprocess.run(
        [sys.executable, "-W", "error", str(Path(__file__).resolve()), "--emit-result-id"],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
        cwd=str(ROOT),
    )
    return completed.stdout.strip()


def determinism_across_processes(data: Dataset) -> Check:
    here = digest_run(run(data)).result_id
    # The inputs are the caller's and are built first: a caller who computes its
    # own configuration under a six-digit context has given the engine other
    # numbers. What is certified is that the engine's own arithmetic does not
    # read the context it is called under.
    run_config, state = config(), running(strategy(data))
    with decimal.localcontext() as hostile:
        hostile.prec = 6
        hostile.rounding = decimal.ROUND_FLOOR
        under_hostile_context = digest_run(
            backtest(run_config, data, state, context_factory, NORMALIZATION)
        ).result_id
    elsewhere = {seed: _run_in_fresh_interpreter(seed) for seed in ("0", "4242")}
    identities = {here, under_hostile_context, *elsewhere.values()}
    return Check(
        "DET-2",
        "The same run in fresh interpreters under different hash seeds, and under an ambient "
        "decimal context of six digits rounding down, is the same run.",
        len(identities) == 1,
        {
            "in_process": here,
            "ambient_context_prec_6_floor": under_hostile_context,
            **{f"fresh_interpreter_PYTHONHASHSEED_{seed}": rid for seed, rid in elsewhere.items()},
        },
    )


def _paper_or_live(data: Dataset, mode: ExecutionMode) -> RunState:
    stream = records(data)
    source = SequenceSource.from_records(to_market_dataset(data, NORMALIZATION).dataset_id, stream)
    return TradingSession.run(config(mode), source, running(strategy(data)), context_factory)


def environment_parity(data: Dataset) -> Check:
    historical = run(data)
    replayed = replay(config(), data, running(strategy(data)), context_factory, NORMALIZATION)
    paper = _paper_or_live(data, ExecutionMode.PAPER)
    replay_run = replayed.backtest
    cash = historical.state.portfolio.cash.balance("USD")
    agree = {
        "fills": historical.fills == replay_run.fills == paper.pipeline.fills.to_tuple(),
        "trades": historical.trades == replay_run.trades == paper.pipeline.trades.to_tuple(),
        "orders": historical.orders
        == replay_run.orders
        == tuple(paper.pipeline.oms.orders.orders()),
        "cash": cash
        == replay_run.state.portfolio.cash.balance("USD")
        == paper.pipeline.portfolio.cash.balance("USD"),
        "positions": historical.state.portfolio.positions
        == replay_run.state.portfolio.positions
        == paper.pipeline.portfolio.positions,
        "equity_curve": historical.equity_curve
        == replay_run.equity_curve
        == paper.pipeline.portfolio_snapshots.to_tuple(),
    }
    return Check(
        "PAR-1",
        "A backtest, a replay and a paper session of one dataset produce the same orders, "
        "fills, trades, cash, positions and equity curve.",
        all(agree.values()),
        {
            **{f"{name}_agree": value for name, value in agree.items()},
            "fills_digest": sha256(serialize(historical.fills)),
            "ending_cash_usd": str(cash),
        },
    )


def live_parity(data: Dataset) -> Check:
    paper = _paper_or_live(data, ExecutionMode.PAPER)
    live = _paper_or_live(data, ExecutionMode.LIVE)

    def submitted(state: RunState) -> list[tuple[str, str, str]]:
        return [
            (order.asset_id, order.side.value, str(order.quantity))
            for order in state.pipeline.oms.orders.orders()
        ]

    same_orders = submitted(live) == submitted(paper)
    return Check(
        "PAR-2",
        "A live session submits exactly the orders the paper session submits, and differs "
        "only at the venue: nothing fills until a venue reports it.",
        same_orders and len(live.pipeline.fills) == 0 and len(paper.pipeline.fills) > 0,
        {
            "orders_submitted": len(submitted(live)),
            "live_fills": len(live.pipeline.fills),
            "paper_fills": len(paper.pipeline.fills),
        },
    )


def _fingerprint() -> StrategyFingerprint:
    definition = StrategyDefinition(
        strategy_id=STRATEGY_ID,
        name="Certification schedule",
        version="1",
        author="docs/audit/scripts/certify_release.py",
        description="A fixed trading schedule over the canonical bars.",
        parameters={str(index): float(quantity) for index, quantity in PLAN.items()},
    )
    version = StrategyVersion("certification", 1, definition, ModelStage.STAGING)
    code = CodeIdentity(
        package="alphalab-certification",
        version="1",
        entry_point="certify_release.ScheduledStrategy",
        source_digest=source_digest({"certify_release.py": Path(__file__).read_bytes()}),
    )
    research = research_configuration({"validation": "certification scenario"})
    return fingerprint_for_version(version, code, NO_DEPENDENCIES, research, running_engine())


def rerun_reproduces(data: Dataset) -> Check:
    original = run(data)
    engine, build, fingerprint = running_engine(), running_build(), _fingerprint()
    manifest = manifest_for_run(original, data, fingerprint, engine, build=build)
    report = rerun_from_manifest(
        manifest,
        lambda: run(data),
        dataset=data,
        fingerprint=fingerprint,
        engine=engine,
        build=build,
        original=original,
    )
    return Check(
        "REP-1",
        "A run re-executed from its reproducibility manifest is REPRODUCED.",
        report.outcome is RerunOutcome.REPRODUCED and report.differences == (),
        {"outcome": report.outcome.name, "result_id": manifest.result_id},
        "; ".join(report.detail),
    )


def stop_and_continue(data: Dataset) -> Check:
    stream = records(data)
    run_config = config()

    def drive(state: RunState, batch: Iterable[MarketRecord]) -> RunState:
        for record in batch:
            state, _ = TradingSession.advance(state, record, context_factory)
        return state

    instance = strategy(data)
    with id_scope(SEED):
        uninterrupted = drive(TradingSession.initialize(run_config, running(instance)), stream)

    instance = strategy(data)
    with id_scope(SEED):
        partial = drive(
            TradingSession.initialize(run_config, running(instance)), stream[:STOP_AFTER]
        )
    payload = record_of(partial)
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=run_config.pipeline.sizing_model,
            simulator=run_config.pipeline.simulator,
            strategies={STRATEGY_ID: strategy(data)},
            instruments=run_config.pipeline.instruments,
        ),
        fill_policy=run_config.fill_policy,
    )
    restored = restore_run(run_from_primitives(deserialize(payload)), objects)
    with TradingSession.resume(restored):
        continued = drive(restored, stream[STOP_AFTER:])
    return Check(
        "REP-2",
        f"A run stopped after {STOP_AFTER} of {len(stream)} records, written to JSON, read back "
        "and continued finishes byte-identical to the run that never stopped.",
        record_of(continued) == record_of(uninterrupted),
        {
            "stopped_payload_bytes": len(payload),
            "final_record_digest": sha256(record_of(uninterrupted)),
        },
    )


#: Which decoder reads each golden payload, by file name.
_DECODERS = {
    "portfolio": "alphalab.portfolio.snapshot",
    "oms": "alphalab.oms.snapshot",
    "allocation": "alphalab.allocation.snapshot",
    "instrument": "alphalab.instrument.snapshot",
    "lifecycle": "alphalab.lifecycle.snapshot",
    "fx_feed": "alphalab.portfolio.fx_feed",
    "broker": "alphalab.broker.snapshot",
    "run": "alphalab.runtime.run_snapshot",
    "live": "alphalab.runtime.live_snapshot",
}
#: The payloads a release wrote that an upgrade refuses, and the reason it gives.
_REFUSED = {"v3.9.0/portfolio_fractional_yen.json": "JPY amounts that are not whole"}


def golden_payloads() -> Check:
    read: dict[str, int] = {}
    warned = refused = 0
    failures: list[str] = []
    for release in ("v3.9.0", "v3.11.0", "v3.12.0"):
        count = 0
        for path in sorted((FIXTURES / release).glob("*.json")):
            name = f"{release}/{path.name}"
            module = importlib.import_module(
                next(
                    decoder
                    for stem, decoder in _DECODERS.items()
                    if path.stem == stem or path.stem.startswith(stem + "_")
                )
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    module.from_primitives(payload)
                except SchemaUpgradeRefused as refusal:
                    if name in _REFUSED and _REFUSED[name] in str(refusal):
                        refused += 1
                        continue
                    failures.append(f"{name}: refused: {refusal}")
                    continue
                except Exception as error:  # a payload that does not read is the finding
                    failures.append(f"{name}: {type(error).__name__}: {error}")
                    continue
            if name in _REFUSED:
                failures.append(f"{name}: read, where its upgrade documents a refusal")
            warned += sum(isinstance(item.message, SchemaUpgradeWarning) for item in caught)
            count += 1
        read[release] = count
    return Check(
        "REP-3",
        "Every payload v3.9.0, v3.11.0 and v3.12.0 wrote is read by this build, or refused for "
        "the reason its upgrade documents.",
        not failures,
        {
            **{f"read_{release}": count for release, count in read.items()},
            "refused_as_documented": refused,
            "upgrade_warnings": warned,
        },
        "; ".join(failures),
    )


_YEAR = 365.25 * 86_400.0
_NOW = 1_767_225_600.0
_NO_DIVIDENDS = dividend_yield(0.0)


def _contract(kind: OptionType, style: ExerciseStyle, strike: str, years: float) -> OptionContract:
    return OptionContract("UNDERLYING", Decimal(strike), _NOW + years * _YEAR, kind, style, 100)


def black_scholes_reference() -> Check:
    call = _contract(OptionType.CALL, ExerciseStyle.EUROPEAN, "40", 0.5)
    put = _contract(OptionType.PUT, ExerciseStyle.EUROPEAN, "40", 0.5)
    c = black_scholes_value(call, 42.0, 0.20, 0.10, 0.5, carry=_NO_DIVIDENDS)
    p = black_scholes_value(put, 42.0, 0.20, 0.10, 0.5, carry=_NO_DIVIDENDS)
    parity = c - p - (42.0 - 40.0 * math.exp(-0.10 * 0.5))
    implied = implied_volatility(
        call, Decimal("4.76"), Decimal("42"), 0.10, _NOW, carry=_NO_DIVIDENDS
    )
    return Check(
        "NUM-1",
        "Black-Scholes-Merton reproduces Hull's example -- S=42, K=40, r=10%, sigma=20%, "
        "T=0.5: call 4.76, put 0.81 to the cent -- put-call parity holds to 1e-12, and the "
        "volatility implied by the quoted 4.76 reprices it.",
        round(c, 2) == 4.76
        and round(p, 2) == 0.81
        and abs(parity) < 1e-12
        and abs(implied.repriced - 4.76) < 1e-6,
        {
            "call": _fixed(c, 6),
            "put": _fixed(p, 6),
            "parity_residual_below_1e-12": abs(parity) < 1e-12,
            "implied_volatility_of_4.76": _fixed(implied.value, 6),
        },
    )


#: Hull's convergence table for an American put on a CRR tree: steps -> value.
_HULL_AMERICAN_PUT = {5: "4.488", 30: "4.263", 50: "4.272", 100: "4.278", 500: "4.283"}


def binomial_reference() -> Check:
    put = _contract(OptionType.PUT, ExerciseStyle.AMERICAN, "50", 5 / 12)
    computed = {
        steps: binomial_value(
            put, 50.0, 0.40, 0.10, _NOW, carry=_NO_DIVIDENDS, lattice=BinomialLattice(steps, ())
        )
        for steps in _HULL_AMERICAN_PUT
    }
    american_call = _contract(OptionType.CALL, ExerciseStyle.AMERICAN, "50", 5 / 12)
    european_call = _contract(OptionType.CALL, ExerciseStyle.EUROPEAN, "50", 5 / 12)
    lattice = BinomialLattice(200, ())
    early_exercise_worth = binomial_value(
        american_call, 50.0, 0.40, 0.10, _NOW, carry=_NO_DIVIDENDS, lattice=lattice
    ) - binomial_value(european_call, 50.0, 0.40, 0.10, _NOW, carry=_NO_DIVIDENDS, lattice=lattice)
    return Check(
        "NUM-2",
        "An American put on the CRR lattice reproduces Hull's convergence table -- S=K=50, "
        "r=10%, sigma=40%, T=5 months: 4.488, 4.263, 4.272, 4.278, 4.283 at 5, 30, 50, 100 "
        "and 500 steps -- and early exercise of a call on a stock paying nothing is worth "
        "exactly nothing.",
        all(_fixed(computed[steps], 3) == value for steps, value in _HULL_AMERICAN_PUT.items())
        and early_exercise_worth == 0.0,
        {
            **{f"steps_{steps}": _fixed(value, 6) for steps, value in computed.items()},
            "call_early_exercise_premium": early_exercise_worth,
        },
    )


#: Standard normal distribution function at chosen points, to 15 significant digits.
_NORMAL_CDF = {-1.0: 0.158655253931457, 1.96: 0.975002104851780, 3.0: 0.998650101968370}


def statistics_reference() -> Check:
    cdf = {point: normal_cdf(point) for point in _NORMAL_CDF}
    # The NIST StRD "NumAcc" design: a large offset, so a one-pass sum of squares
    # loses the deviation. 1001 values, 1000000.2 then 500 pairs either side of it:
    # the mean is 1000000.2 and the sample standard deviation exactly 0.1.
    sample = [1_000_000.2] + [1_000_000.1, 1_000_000.3] * 500
    centre, spread = mean(sample), standard_deviation(sample)
    return Check(
        "NUM-3",
        "The normal distribution function matches tabulated values to 1e-15, and the sample "
        "statistics recover the exact mean 1000000.2 and deviation 0.1 of a NIST NumAcc-style "
        "series whose offset defeats a one-pass sum of squares.",
        all(abs(cdf[point] - value) < 1e-15 for point, value in _NORMAL_CDF.items())
        and abs(centre - 1_000_000.2) < 1e-9
        and abs(spread - 0.1) < 1e-9,
        {
            **{f"normal_cdf({point})": f"{value:.15f}" for point, value in cdf.items()},
            "numacc_mean": _fixed(centre, 9),
            "numacc_standard_deviation": _fixed(spread, 12),
        },
    )


def public_api() -> Check:
    spec = importlib.util.spec_from_file_location(
        "generate_public_api", ROOT / "docs" / "api" / "generate_public_api.py"
    )
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    manifest_path = ROOT / "docs" / "api" / "public_api.json"
    recorded = manifest_path.read_text(encoding="utf-8")
    built = generator.render(generator.build(json.loads(recorded)))
    manifest = json.loads(recorded)
    return Check(
        "API-1",
        "The build's public API is the one docs/api/public_api.json records for this release.",
        built == recorded and manifest["release"] == running_engine().version,
        {
            "manifest_digest": sha256(recorded),
            "packages": len(manifest["packages"]),
            "exports": sum(len(names) for names in manifest["packages"].values()),
            "shared_names": len(manifest["shared_names"]),
        },
    )


def certify() -> dict[str, Any]:
    data = dataset()
    checks = [
        determinism_in_process(data),
        determinism_across_processes(data),
        environment_parity(data),
        live_parity(data),
        rerun_reproduces(data),
        stop_and_continue(data),
        golden_payloads(),
        black_scholes_reference(),
        binomial_reference(),
        statistics_reference(),
        public_api(),
    ]
    engine, build = running_engine(), running_build()
    return {
        "release": engine.version,
        "build": {
            "engine": f"{engine.name} {engine.version}",
            "source_digest": build.source_digest,
            "tz_database": build.tz_database,
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "passed": all(check.passed for check in checks),
        "checks": [
            {
                "id": check.check_id,
                "claim": check.claim,
                "passed": check.passed,
                "evidence": check.evidence,
                **({"detail": check.detail} if check.detail else {}),
            }
            for check in checks
        ],
    }


#: What --check compares: everything a build computes, nothing about the host.
def _comparable(certificate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "release": certificate["release"],
        "passed": certificate["passed"],
        "checks": certificate["checks"],
    }


def render(certificate: Mapping[str, Any]) -> str:
    verdict = "PASSED" if certificate["passed"] else "FAILED"
    build = certificate["build"]
    environment = certificate["environment"]
    lines = [
        f"# Release certification: AlphaLab {certificate['release']}",
        "",
        "Generated by `python docs/audit/scripts/certify_release.py` (ledger FEA-006) from "
        "`release_certification.json`, which is the certificate; this page renders it. CI runs "
        "the same script with `--check`, which fails when a check fails or when a check's "
        "evidence differs from what is committed here.",
        "",
        f"**Verdict: {verdict}** -- {sum(c['passed'] for c in certificate['checks'])} of "
        f"{len(certificate['checks'])} checks passed.",
        "",
        "| Build | |",
        "| --- | --- |",
        f"| Engine | {build['engine']} |",
        f"| Source digest | `{build['source_digest']}` |",
        f"| Time-zone database | {build['tz_database']} |",
        f"| Certified on | Python {environment['python']} ({environment['implementation']}), "
        f"{environment['platform']} |",
        "",
        "| Check | Claim | Result |",
        "| --- | --- | --- |",
    ]
    for check in certificate["checks"]:
        lines.append(
            f"| {check['id']} | {check['claim']} | {'PASS' if check['passed'] else 'FAIL'} |"
        )
    lines += ["", "## Evidence", ""]
    for check in certificate["checks"]:
        lines.append(f"### {check['id']}")
        lines.append("")
        for key, value in check["evidence"].items():
            lines.append(f"- `{key}`: `{value}`")
        if check.get("detail"):
            lines.append(f"- detail: {check['detail']}")
        lines.append("")
    lines += [
        "## What this does not certify",
        "",
        "- **Other hosts.** The determinism checks run on the certifying host. Every certified "
        "identity is a SHA-256 of JSON the engine writes from `Decimal` amounts and IEEE-754 "
        "doubles; a host whose `libm` rounds `exp` or `log` differently in the last bit may "
        "compute a different analytics float and so a different identity. `--check` on such a "
        "host reports exactly which evidence differs.",
        "- **A venue.** Live parity stops where the venue begins: what a broker fills is the "
        "broker's (PAR-2).",
        "- **A strategy.** The scenario's strategy is a fixed schedule; certifying a "
        "strategy's own behaviour is `alphalab.lifecycle` certification, a different claim.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed one")
    parser.add_argument("--emit-result-id", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)

    if arguments.emit_result_id:
        print(digest_run(run(dataset())).result_id)
        return 0

    certificate = certify()
    failed = [check["id"] for check in certificate["checks"] if not check["passed"]]
    if arguments.check:
        committed = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
        if _comparable(committed) != _comparable(certificate):
            moved = [
                fresh["id"]
                for fresh, old in zip(certificate["checks"], committed["checks"], strict=False)
                if fresh != old
            ]
            print(
                "the build's certificate differs from the committed one "
                f"(release {committed['release']} -> {certificate['release']}; checks {moved}). "
                "Re-certify with `python docs/audit/scripts/certify_release.py` and review "
                "the diff."
            )
            return 1
        print(f"certified: {len(certificate['checks'])} checks passed, matching the committed one")
        return 1 if failed else 0

    REPORT_JSON.write_text(
        json.dumps(certificate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    REPORT_MD.write_text(render(certificate), encoding="utf-8")
    print(
        f"{REPORT_JSON.relative_to(ROOT)}: "
        f"{'PASSED' if not failed else 'FAILED ' + ', '.join(failed)}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
