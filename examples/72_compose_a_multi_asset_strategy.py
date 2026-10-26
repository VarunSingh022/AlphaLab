"""
AlphaLab Examples
=================

Example 72 : Compose a Multi-Asset Strategy from Prices, Sentiment and Macro Data

Difficulty : Advanced

Estimated Time : 25 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 51 (alternative data with provenance)
✓ Example 33 (futures contracts and rolls)

Topics
------

• Two price datasets from two venues -- a cash equity and an index future --
  read by one run under one derived identity (``combine_datasets``)
• Social-media sentiment and a macroeconomic series with revisions, each
  ingested with the instant it became available, delivered to the strategy at
  that instant and never earlier
• A strategy written by the researcher -- no template -- that turns those
  inputs into features, a signal, a position in the equity and a hedge in the
  future, and remembers what it has seen as declared state
• Execution costs, a pre-trade risk gate that refuses an order, and the
  analytics, lineage and identities of the result
• The same run as a replay, paused and resumed, and as a backtest stopped,
  stored and continued -- each the same result

What this shows
---------------

v4.1.1's acceptance scenario: that a quantitative researcher can combine
heterogeneous inputs in one strategy through the public API, and that the
engine keeps each input's time semantics while doing so. Every row is written
out below and every figure is **synthetic**. Nothing here says anything about
whether sentiment or inflation predicts returns; the scenario proves that the
components compose, not that the strategy is profitable.

The fixture is built to be dangerous. A post written on 8 April was only
collected on 22 April; March's inflation figure is revised on 24 April; May's
is published after the data ends; one post says nothing about when it became
available; the future misses one day's bar. The strategy logs what it knew at
every decision, and ``tests/integration/test_multi_asset_composition.py``
proves from that log, and from runs whose late values are changed, that none
of it was used before it existed.

Run

    python examples/72_compose_a_multi_asset_strategy.py
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.alt_data import ExternalObservation, delivery_schedule
from alphalab.alt_data.streaming import DeliverySchedule, ObservationDelivery
from alphalab.api import (
    AvailabilityFromColumn,
    CombinedDataset,
    ObservationColumns,
    PointInTimeIngestion,
    TimestampReading,
    combine_datasets,
    combined_market_dataset,
    ingest_observations,
    ingest_rows,
    observation_source,
)
from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    ExecutionMode,
    RunConfig,
    replay_snapshot,
)
from alphalab.backtesting.engine import finalize
from alphalab.backtesting.replay import PausedReplay, ReplayBacktest
from alphalab.backtesting.schedule import RunSchedule, ScheduleCursor
from alphalab.backtesting.state import ReplayResult
from alphalab.common.ids import id_scope
from alphalab.common.order_terms import OrderTerms
from alphalab.common.point_in_time import VisibilityRule
from alphalab.common.types import ParamValue
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.cleaning import REFUSE_EVERYTHING
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import RawSource, SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import BarStamp, TimeFrequency, TimestampFormat
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    FixedHalfSpread,
    NoImpact,
    NoTax,
    PerTradeFee,
)
from alphalab.execution.policy import FillTiming, LiquidityCappedFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    LifecycleState,
    ReproducibilityManifest,
    StrategyFingerprint,
    code_identity_for,
    digest_run,
    fingerprint_for_version,
    get_strategy_version,
    manifest_for_run,
    register_strategy,
    research_configuration,
    running_build,
    running_engine,
)
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
from alphalab.runtime.run import RunEngine
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    IntentKind,
    StrategyContext,
    StrategyDefinition,
    context_factory,
)
from alphalab.strategy.events import ObservationReceived, SliceClosed
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.registry import StrategyClassRegistry, runtime_for
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = "EX72-COMPOSITE"
PROVIDER = "ex72-vendor"
SEED = 72
START_CASH = Decimal("1000000.00")
#: The data's last instant is 31 May; a figure published after it is never read.
FIRST_DAY, LAST_DAY = date(2024, 4, 1), date(2024, 5, 31)

#: New York's regular session. AlphaLab ships no holidays: the one in the window
#: is declared.
XNYS = MarketCalendar(
    calendar_id="XNYS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    holidays=frozenset({date(2024, 5, 27)}),
)
SESSIONS: tuple[date, ...] = XNYS.sessions_between(FIRST_DAY, LAST_DAY)
_NEW_YORK = XNYS.local_datetime(0.0).tzinfo

#: The future's daily settlement is stamped at 17:00 New York (16:00 Chicago),
#: an hour after the equity's close: two calendars, two instants a day, and
#: nothing aligns them but time itself.
EQUITY_CLOSE, FUTURE_SETTLE = "16:00:00", "17:00:00"
#: The future's vendor file has no bar for this session -- a gap, not a holiday.
FUTURE_GAP = date(2024, 5, 6)

ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNYS", "USD", aliases={PROVIDER: "ACME"})
#: An equity-index future: fifty index points a contract, variation margin
#: settled daily, traded in whole contracts. Without the declaration it could
#: not be booked at all (ledger ACC-005).
IDX = InstrumentRecord(
    "IDXM4",
    AssetType.FUTURE,
    "XCME",
    "USD",
    aliases={PROVIDER: "IDXM4"},
    economics=InstrumentEconomics(
        Decimal("50"),
        SettlementModel.FUTURES_VARIATION,
        lot=LotSpecification(Decimal("1"), Decimal("1")),
    ),
)
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (ACME, IDX))


def _policy(venue: str) -> NormalizationPolicy:
    return NormalizationPolicy(
        bar_stamp=BarStamp.INTERVAL_END,
        venue=venue,
        currency="USD",
        timeframe=TimeFrame.D1,
        identity=REGISTRY,
        provider=PROVIDER,
    )


EQUITY_POLICY, FUTURE_POLICY = _policy("XNYS"), _policy("XCME")
READING = TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None)
CONTEXTS = context_factory(FixedClock(0.0), DiscardingLogger())


def ny(text: str) -> float:
    """A New York wall clock, ``"2024-04-10 08:30"``, as Unix seconds."""

    return datetime.fromisoformat(text).replace(tzinfo=_NEW_YORK).timestamp()


def label(instant: float) -> str:
    return XNYS.local_datetime(instant).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# 1. The data: two price files, a social feed, a macro release calendar
# --------------------------------------------------------------------------- #


def csv_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    """The rows as the CSV bytes they stand for, header first."""

    header = list(rows[0])
    lines = [",".join(header), *(",".join(str(row[key]) for key in header) for row in rows)]
    return "\n".join(lines).encode("utf-8")


def vendor_file(name: str, rows: Sequence[Mapping[str, object]], retrieved: str) -> RawSource:
    return raw_source_from_bytes(
        SourceKind.VENDOR_FILE, name, csv_bytes(rows), ny(retrieved), "text/csv", "utf-8"
    )


def _path(start: Decimal, pattern: Sequence[int], scale: Decimal) -> list[Decimal]:
    """A deterministic price path: each session moves by ``pattern[i] * scale``."""

    closes, price = [], start
    for index in range(len(SESSIONS)):
        price = (price * (1 + pattern[index % len(pattern)] * scale)).quantize(Decimal("0.01"))
        closes.append(price)
    return closes


#: Synthetic closes, written as a rule rather than drawn: a rise, a fall, a rise.
EQUITY_CLOSES = _path(Decimal("100"), (3, 4, -1, 5, 2, -2, 3, -6, -5, -4, 1, -7), Decimal("0.004"))
FUTURE_CLOSES = _path(Decimal("500"), (2, 1, -1, 3, -2, 1, 2, -3, -2, -1, 1, -2), Decimal("0.003"))


def bar_rows(symbol: str, closes: Sequence[Decimal], stamp: str) -> list[dict[str, str]]:
    rows = []
    previous = closes[0]
    for day, close in zip(SESSIONS, closes, strict=True):
        if symbol == IDX.symbol and day == FUTURE_GAP:
            previous = close
            continue
        high = max(previous, close) * Decimal("1.002")
        low = min(previous, close) * Decimal("0.998")
        rows.append(
            {
                "symbol": symbol,
                "timestamp": f"{day.isoformat()} {stamp}",
                "open": str(previous),
                "high": str(high.quantize(Decimal("0.01"))),
                "low": str(low.quantize(Decimal("0.01"))),
                "close": str(close),
                "volume": "250000" if symbol == ACME.symbol else "40000",
            }
        )
        previous = close
    return rows


def ingest_prices(
    name: str, rows: Sequence[Mapping[str, object]], asset_class: DataAssetClass
) -> Dataset:
    """One price file as a versioned dataset; any defect at all refuses it."""

    request = IngestionRequest(
        name=name,
        source=vendor_file(f"{name.lower()}.csv", rows, "2024-06-01 06:00"),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=asset_class,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
        timezone_name="America/New_York",
    )
    return ingest_rows(rows, request).dataset


def prices() -> CombinedDataset:
    """Both files, read by the run as one input with one derived identity."""

    equity = ingest_prices(
        "EX72-ACME", bar_rows(ACME.symbol, EQUITY_CLOSES, EQUITY_CLOSE), DataAssetClass.EQUITY
    )
    future = ingest_prices(
        "EX72-IDX", bar_rows(IDX.symbol, FUTURE_CLOSES, FUTURE_SETTLE), DataAssetClass.FUTURE
    )
    return combine_datasets([(equity, EQUITY_POLICY), (future, FUTURE_POLICY)])


#: The post written on 8 April that the vendor only collected on 22 April.
LATE_POST = ("2024-04-08 10:00", "2024-04-22 09:00")


def sentiment_rows(late_score: str = "0.95") -> list[dict[str, str]]:
    """One scored post a session about ACME, and four rows that are not what they seem.

    Out of order on purpose: the late post is the file's first row.
    """

    rows = [
        {
            "ticker": "ACME",
            "metric": "post_sentiment",
            "score": late_score,
            "posted_at": LATE_POST[0],
            "collected_at": LATE_POST[1],
        }
    ]
    for index, day in enumerate(SESSIONS):
        score = Decimal(((index * 5) % 9) - 4) / Decimal(5)
        rows.append(
            {
                "ticker": "ACME",
                "metric": "post_sentiment",
                "score": str(score),
                "posted_at": f"{day.isoformat()} 12:00",
                "collected_at": f"{day.isoformat()} 12:05",
            }
        )
    rows.append(  # says nothing about when it became available: never delivered
        {
            "ticker": "ACME",
            "metric": "post_sentiment",
            "score": "-1.0",
            "posted_at": "2024-04-15 11:00",
            "collected_at": "",
        }
    )
    rows.append(dict(rows[5]))  # an exact repeat: rejected with its line
    rows.append(  # unreadable: rejected with its line
        {
            "ticker": "ACME",
            "metric": "post_sentiment",
            "score": "n/a",
            "posted_at": "2024-04-16 11:00",
            "collected_at": "2024-04-16 11:01",
        }
    )
    return rows


SENTIMENT_COLUMNS = ObservationColumns(
    category="sentiment",
    unit="score",
    subject="ticker",
    metric="metric",
    value="score",
    observed_at="posted_at",
    availability=AvailabilityFromColumn("collected_at"),
    effective_at=None,
    revision=None,
    period=None,
    ingested_at_retrieval=False,
)


def ingest_sentiment(late_score: str = "0.95") -> PointInTimeIngestion[ExternalObservation]:
    rows = sentiment_rows(late_score)
    raw = vendor_file("social_posts.csv", rows, "2024-06-01 06:00")
    source = observation_source(raw, "social.posts", "2024.06")
    return ingest_observations(rows, "social", source, SENTIMENT_COLUMNS, READING)


def macro_rows(march_revision: str = "3.40") -> list[dict[str, str]]:
    """US CPI, year on year: each month's figure, each revision, each release instant.

    February's was published before the data starts, March's is revised on
    24 April, and May's is published after the data ends.
    """

    def row(
        period: str, start: str, end: str, value: str, released: str, vintage: int
    ) -> dict[str, str]:
        return {
            "region": "US",
            "series": "cpi_yoy",
            "value": value,
            "period": period,
            "period_start": f"{start} 00:00",
            "period_end": f"{end} 23:59",
            "released": released,
            "vintage": str(vintage),
        }

    return [
        row("2024-02", "2024-02-01", "2024-02-29", "3.20", "2024-03-12 08:30", 0),
        row("2024-03", "2024-03-01", "2024-03-31", "3.50", "2024-04-10 08:30", 0),
        row("2024-03", "2024-03-01", "2024-03-31", march_revision, "2024-04-24 08:30", 1),
        row("2024-04", "2024-04-01", "2024-04-30", "3.70", "2024-05-15 08:30", 0),
        row("2024-05", "2024-05-01", "2024-05-31", "3.10", "2024-06-12 08:30", 0),
    ]


MACRO_COLUMNS = ObservationColumns(
    category="economic",
    unit="percent",
    subject="region",
    metric="series",
    value="value",
    observed_at="period_end",
    availability=AvailabilityFromColumn("released"),
    effective_at=None,
    revision="vintage",
    period=("period_start", "period_end", "period"),
    ingested_at_retrieval=False,
)


def ingest_macro(march_revision: str = "3.40") -> PointInTimeIngestion[ExternalObservation]:
    rows = macro_rows(march_revision)
    raw = vendor_file("cpi_releases.csv", rows, "2024-06-13 06:00")
    source = observation_source(raw, "macro.us_cpi", "2024.06")
    return ingest_observations(rows, "us_cpi", source, MACRO_COLUMNS, READING)


@dataclass(frozen=True)
class Inputs:
    """Everything one run reads, each piece with its own identity."""

    prices: CombinedDataset
    sentiment: PointInTimeIngestion[ExternalObservation]
    macro: PointInTimeIngestion[ExternalObservation]

    def schedules(self) -> tuple[DeliverySchedule[ExternalObservation], ...]:
        """Each set's records at the instant each was published -- never earlier."""

        return tuple(
            delivery_schedule(ingested.observations, VisibilityRule.PUBLICATION)
            for ingested in (self.sentiment, self.macro)
        )

    def deliveries(self) -> tuple[ObservationDelivery[ExternalObservation], ...]:
        return tuple(delivery for schedule in self.schedules() for delivery in schedule)

    def schedule(self) -> RunSchedule:
        """Every timed input of the run: here, both information sets."""

        return RunSchedule.of(observations=self.deliveries())

    def parameters(self) -> dict[str, ParamValue]:
        """The strategy's parameters, which name the information sets it reads."""

        return {
            **PARAMETERS,
            "sentiment_set": self.sentiment.observations.version,
            "macro_set": self.macro.observations.version,
        }


def inputs(late_score: str = "0.95", march_revision: str = "3.40") -> Inputs:
    return Inputs(prices(), ingest_sentiment(late_score), ingest_macro(march_revision))


# --------------------------------------------------------------------------- #
# 2. The strategy: the researcher's, against the protocol
# --------------------------------------------------------------------------- #

#: What drives its orders, and therefore what its fingerprint hashes.
PARAMETERS: dict[str, ParamValue] = {
    "lookback": 5,
    "momentum_weight": 20.0,
    "sentiment_decay": 0.5,
    "shares_per_unit": 1000,
    "max_shares": 2000,
    "hot_inflation": 3.5,
    "hedge_ratio": 0.5,
    "fresh_seconds": 7200.0,
}

ZERO = Decimal("0")


@dataclass(frozen=True)
class Decision:
    """What the strategy knew and wanted at one decision -- the evidence of its timing.

    Attributes:
        at: The instant decided at.
        seen: Every observation delivered to it so far, as ``(known_at, delivery_id)``.
        cpi: The inflation reading it used: ``(period, vintage, value)``, or ``None``.
        equity_target: The shares it asked to hold.
        future_target: The contracts it asked to hold, or ``None`` when the
            future's last price was too old to act on.
    """

    at: float
    seen: tuple[tuple[float, str], ...]
    cpi: tuple[str, int, str] | None
    equity_target: Decimal
    future_target: Decimal | None


class CompositeSignal(BaseStrategy):
    """ACME momentum plus sentiment about it, hedged with the future while inflation runs hot.

    * **Features.** Five-session momentum of ACME's closes; an exponentially
      decayed average of post sentiment; the latest *known* vintage of the most
      recent inflation figure.
    * **Signal.** ``momentum * momentum_weight + sentiment``, scaled to shares
      and capped at ``max_shares`` either way, in hundreds.
    * **Hedge.** While inflation is at or above ``hot_inflation``, short
      ``hedge_ratio`` of the equity position's value in futures, in whole
      contracts -- valued with the contract's multiplier, read from the
      instrument registry the context exposes.
    * **Freshness.** It decides on every completed instant, and only touches
      the future when the future's last settlement is within ``fresh_seconds``:
      on the day the future's bar is missing it says nothing about the future,
      rather than hedge at yesterday's price.

    Orders go to the next session's opening auction (``OrderTerms.at_open()``):
    a decision taken on a close is never filled at that close.
    """

    def __init__(self, strategy_id: str, parameters: Mapping[str, ParamValue]) -> None:
        self._strategy_id = strategy_id
        self._lookback = int(parameters["lookback"])
        self._momentum_weight = Decimal(str(parameters["momentum_weight"]))
        self._decay = Decimal(str(parameters["sentiment_decay"]))
        self._shares_per_unit = Decimal(str(parameters["shares_per_unit"]))
        self._max_shares = Decimal(str(parameters["max_shares"]))
        self._hot = Decimal(str(parameters["hot_inflation"]))
        self._hedge_ratio = Decimal(str(parameters["hedge_ratio"]))
        self._fresh = float(parameters["fresh_seconds"])
        # Declared state: everything below is captured and restored.
        self._closes: list[Decimal] = []
        self._sentiment: Decimal | None = None
        self._cpi: dict[str, tuple[float, int, Decimal]] = {}
        self._seen: list[tuple[float, str]] = []
        self.decisions: list[Decision] = []

    # -- what it is told ------------------------------------------------------

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        if bar.asset_id == ACME.asset_id:
            self._closes.append(bar.close)
        return ()

    def on_observation(
        self, context: StrategyContext, event: ObservationReceived
    ) -> Iterable[Intent]:
        record = event.record
        if not isinstance(record, ExternalObservation):
            return ()
        self._seen.append((event.timestamp, event.delivery_id))
        if record.category == "sentiment":
            previous = record.value if self._sentiment is None else self._sentiment
            self._sentiment = self._decay * record.value + (1 - self._decay) * previous
        elif record.metric == "cpi_yoy" and record.period is not None:
            held = self._cpi.get(record.period.label)
            if held is None or record.revision > held[1]:
                self._cpi[record.period.label] = (record.period.end, record.revision, record.value)
        return ()

    # -- what it decides ------------------------------------------------------

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        if len(self._closes) <= self._lookback:
            return ()
        momentum = self._closes[-1] / self._closes[-1 - self._lookback] - 1
        signal = momentum * self._momentum_weight + (self._sentiment or ZERO)
        shares = (signal * self._shares_per_unit / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        ) * 100
        # ``+ 0`` writes a zero target as 0, never -0.
        equity_target = max(-self._max_shares, min(self._max_shares, shares)) + 0

        cpi = self._latest_cpi()
        future_target = self._hedge(context, event.timestamp, equity_target, cpi)
        self.decisions.append(
            Decision(
                event.timestamp,
                tuple(self._seen),
                None if cpi is None else (cpi[0], cpi[1], str(cpi[2])),
                equity_target,
                future_target,
            )
        )
        intents = [self._target(ACME.asset_id, equity_target, event.timestamp)]
        if future_target is not None:
            intents.append(self._target(IDX.asset_id, future_target, event.timestamp))
        return tuple(intents)

    def _latest_cpi(self) -> tuple[str, int, Decimal] | None:
        if not self._cpi:
            return None
        period = max(self._cpi, key=lambda name: self._cpi[name][0])
        _, vintage, value = self._cpi[period]
        return period, vintage, value

    def _hedge(
        self,
        context: StrategyContext,
        at: float,
        equity_target: Decimal,
        cpi: tuple[str, int, Decimal] | None,
    ) -> Decimal | None:
        bar = context.market.bar(IDX.asset_id)
        equity = context.market.price(ACME.asset_id)
        if bar is None or equity is None or at - bar.timestamp > self._fresh:
            return None
        if cpi is None or cpi[2] < self._hot:
            return ZERO
        # The multiplier is the instrument's declaration, read from the run's
        # registry -- never assumed. Without one a hedge cannot be sized.
        instrument = context.universe.instrument(IDX.asset_id)
        economics = None if instrument is None else instrument.economics
        if economics is None:
            raise ValueError(f"{IDX.symbol} declares no multiplier; a hedge cannot be sized")
        multiplier: Decimal = economics.multiplier
        contracts: Decimal = self._hedge_ratio * equity_target * equity / (bar.close * multiplier)
        return -contracts.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)

    def _target(self, asset_id: str, quantity: Decimal, at: float) -> Intent:
        return Intent(
            strategy_id=self._strategy_id,
            instrument=asset_id,
            target=quantity,
            timestamp=at,
            kind=IntentKind.TARGET_QUANTITY,
            terms=OrderTerms.at_open(),
        )

    # -- what it remembers ----------------------------------------------------

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, Any]:
        return {
            "closes": [str(close) for close in self._closes],
            "sentiment": None if self._sentiment is None else str(self._sentiment),
            "cpi": {
                period: [end, vintage, str(value)]
                for period, (end, vintage, value) in sorted(self._cpi.items())
            },
            "seen": [[at, delivery] for at, delivery in self._seen],
        }

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"CompositeSignal cannot read state version {version}")
        self._closes = [Decimal(close) for close in payload["closes"]]
        sentiment = payload["sentiment"]
        self._sentiment = None if sentiment is None else Decimal(sentiment)
        self._cpi = {
            str(period): (float(end), int(vintage), Decimal(value))
            for period, (end, vintage, value) in payload["cpi"].items()
        }
        self._seen = [(float(at), str(delivery)) for at, delivery in payload["seen"]]


def composite_factory(
    strategy_id: str, parameters: Mapping[str, ParamValue], /
) -> StrategyProtocol:
    return CompositeSignal(strategy_id, parameters)


#: The one mapping from the strategy's identity to its code. A registry
#: discovers and versions a researcher's code; it does not limit what it may be.
CLASSES = StrategyClassRegistry().register(STRATEGY_ID, composite_factory)


def definition(data: Inputs) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=STRATEGY_ID,
        name="Composite signal with an inflation hedge",
        version="1",
        author="examples",
        description="equity momentum + post sentiment, hedged with futures when CPI runs hot",
        parameters=data.parameters(),
    )


def running(data: Inputs) -> RuntimeState:
    """A fresh instance, built by the class registry from the definition, and started."""

    state = runtime_for(CLASSES, (definition(data),))
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(
        strategy, frozenset({"bars", "slices", "observations"}), 1.2
    )
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def instance_of(result: BacktestResult) -> CompositeSignal:
    instance = result.state.strategy.strategies[STRATEGY_ID].instance
    assert isinstance(instance, CompositeSignal)
    return instance


# --------------------------------------------------------------------------- #
# 3. The run: costs, limits, the canonical path
# --------------------------------------------------------------------------- #

#: Every cost stated. The half-spread and fee are assumptions, and naming them
#: here is what puts them in the run's recorded configuration.
COSTS = ExecutionCostModel(
    spread_model=FixedHalfSpread(Decimal("0.01")),
    slippage_model=PercentageSlippage(Decimal("0.0005")),
    impact_model=NoImpact(),
    commission_model=PercentageCommission(Decimal("0.0001")),
    fee_model=PerTradeFee(Decimal("1.00")),
    tax_model=NoTax(),
)

#: The pre-trade gate. No order may move more than 1,000 units at once -- so a
#: decision whose target jumps further is refused, the strategy keeps what it
#: holds, and the refusal is in the result rather than quietly resized.
LIMITS = RiskLimits(
    order_size=OrderSizeLimit(Decimal("1000"), Decimal("1000000")),
    position=PositionLimit(Decimal("1000000"), Decimal("2000000")),
    exposure=ExposureLimit(Decimal("2000000"), Decimal("2000000")),
    leverage=LeverageLimit(Decimal("2")),
    margin=MarginLimit(Decimal("1")),
    daily_loss=DailyLossLimit(Decimal("100000"), "America/New_York"),
    drawdown=DrawdownLimit(Decimal("0.25")),
)


def run_config(seed: int | None = SEED) -> RunConfig:
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX72", "USD", "Example 72", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH * 2,
                cash_buffer=ZERO,
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=LIMITS,
            simulator=ExecutionSimulator(cost_model=COSTS),
            instruments=REGISTRY,
            # A decision is never filled at the price that made it.
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        # No fill takes more than a tenth of the volume its bar printed.
        fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.1")),
        seed=seed,
        start_timestamp=1.0,
        periods_per_year=252.0,
    )


def run(data: Inputs, seed: int | None = SEED) -> BacktestResult:
    """Prices, information and a researcher's strategy, through the canonical path."""

    return BacktestEngine.run(
        run_config(seed),
        combined_market_dataset(data.prices),
        running(data),
        CONTEXTS,
        schedule=data.schedule(),
    )


def replayed(data: Inputs, pause_at: int | None = None) -> ReplayResult:
    """The same run as a replay -- paused after ``pause_at`` records, if given, written to a
    payload, restored with fresh objects as another process would, and resumed."""

    config = run_config()
    market = combined_market_dataset(data.prices)
    if pause_at is None:
        result = ReplayBacktest.run(
            config, market, running(data), CONTEXTS, schedule=data.schedule()
        )
    else:
        paused = ReplayBacktest.run(
            config,
            market,
            running(data),
            CONTEXTS,
            pause_when=lambda progress: progress.records_replayed == pause_at,
            schedule=data.schedule(),
        )
        assert isinstance(paused, PausedReplay)
        payload = serialize(replay_snapshot.capture(paused))
        restored = replay_snapshot.restore(
            replay_snapshot.from_primitives(deserialize(payload)),
            fresh_objects(data),
            market,
            data.schedule(),
        )
        result = ReplayBacktest.resume(restored, CONTEXTS)
    assert isinstance(result, ReplayResult)
    return result


def fresh_objects(data: Inputs) -> RunObjects:
    """The live objects a fresh process builds again: the strategy from its registry."""

    fresh = run_config()
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=fresh.pipeline.sizing_model,
            simulator=fresh.pipeline.simulator,
            strategies={STRATEGY_ID: CLASSES.construct(STRATEGY_ID, data.parameters())},
            instruments=REGISTRY,
        ),
        fill_policy=fresh.fill_policy,
    )


def continued(data: Inputs, stop_after: int) -> BacktestResult:
    """Stop after ``stop_after`` records, store the run, restore it with fresh objects, finish.

    :meth:`BacktestEngine.run`'s loop written out, with the cursor it uses:
    :meth:`ScheduleCursor.start` given the record the run stopped after hands
    the continuation exactly the inputs it has not applied.
    """

    config = run_config()
    market = combined_market_dataset(data.prices)
    initial = running(data)
    assert config.seed is not None
    with id_scope(config.seed):
        state = replace(BacktestEngine.initialize(config, initial), source_id=market.dataset_id)
        cursor = ScheduleCursor.start(data.schedule())
        for record in market.records[:stop_after]:
            cursor, state = cursor.before(state, record.timestamp, CONTEXTS)
            state = RunEngine.close_slice(state, CONTEXTS, cursor.rates, before=record.timestamp)
            state, _ = RunEngine.advance(state, record, CONTEXTS, rates=cursor.rates)
    payload = serialize(capture(state))

    restored = restore(from_primitives(deserialize(payload)), fresh_objects(data))
    after = market.records[stop_after - 1].timestamp if stop_after else None
    cursor = ScheduleCursor.start(data.schedule(), after)
    with BacktestEngine.resume(restored):
        for record in market.records[stop_after:]:
            cursor, restored = cursor.before(restored, record.timestamp, CONTEXTS)
            restored = RunEngine.close_slice(
                restored, CONTEXTS, cursor.rates, before=record.timestamp
            )
            restored, _ = RunEngine.advance(restored, record, CONTEXTS, rates=cursor.rates)
        cursor, restored = cursor.finish(restored, CONTEXTS)
        return finalize(RunEngine.close_slice(restored, CONTEXTS, cursor.rates))


# --------------------------------------------------------------------------- #
# 4. Identity: the strategy's fingerprint, and the manifest of the result
# --------------------------------------------------------------------------- #


def fingerprint(data: Inputs) -> StrategyFingerprint:
    state, reference = register_strategy(LifecycleState(), "ex72-composite", definition(data), 2.0)
    version = get_strategy_version(state.strategies, reference.name, reference.version)
    code = code_identity_for(
        CLASSES.require(STRATEGY_ID),
        "alphalab-examples",
        "72",
        {"72_compose_a_multi_asset_strategy.py": Path(__file__).read_bytes()},
    )
    research = research_configuration({"validation": "single backtest, synthetic data"})
    return fingerprint_for_version(version, code, NO_DEPENDENCIES, research, running_engine())


def manifest(data: Inputs, result: BacktestResult) -> ReproducibilityManifest:
    return manifest_for_run(
        result,
        data.prices,
        fingerprint(data),
        running_engine(),
        build=running_build(),
        schedule=data.schedule(),
    )


# --------------------------------------------------------------------------- #
# The walk-through
# --------------------------------------------------------------------------- #


def banner(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 66 - len(title)))


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 72 : Compose a Multi-Asset Strategy")
    print("=" * 72)

    data = inputs()
    provenance = data.prices.require_provenance()

    banner("1. Inputs, each with its own identity")
    for version, content in provenance.parts:
        print(f"  prices    {version}  bytes {content[:16]}")
    print(f"  combined  {provenance.dataset_version}")
    print(
        f"  sentiment {data.sentiment.observations.version}: "
        f"{len(data.sentiment.observations)} posts, {len(data.sentiment.rejected)} rows rejected"
    )
    for rejection in data.sentiment.rejected:
        print(f"            line {rejection.line_number}: {rejection.findings[0].detail[:58]}")
    sentiment, _ = data.schedules()
    print(f"            {sentiment.unverified} post with no stated availability, never delivered")
    print(f"  macro     {data.macro.observations.version}: {len(data.macro.observations)} releases")
    late = next(d for d in sentiment if d.record.stamp.observed_at == ny(LATE_POST[0]))
    print(
        f"  the late post: written {label(late.record.stamp.observed_at)}, "
        f"knowable {label(late.known_at)}"
    )

    banner("2. The run")
    result = run(data)
    strategy = instance_of(result)
    print(
        f"  records {result.records_processed}, observations delivered "
        f"{result.run.observations_delivered} of {len(data.deliveries())}"
    )
    print(
        f"  orders {len(result.orders)}, fills {len(result.fills)}, decisions "
        f"{len(strategy.decisions)}"
    )
    refused = [decision for decision in result.state.risk.history if not decision.approved]
    print(f"  refused by the risk gate: {len(refused)}")
    for decision in refused[:2]:
        (violation,) = decision.violations
        print(
            f"    {label(decision.timestamp)}  {violation.rule}: "
            f"{violation.current_value} > {violation.allowed_value}"
        )
    stale = [d for d in strategy.decisions if d.future_target is None]
    print(f"  decisions that left the future alone (its price too old): {len(stale)}")
    positions = result.state.portfolio.positions
    for record in (ACME, IDX):
        held = positions.get(record.asset_id)
        print(f"  {record.symbol:6} {'0' if held is None else held.quantity}")

    banner("3. Analytics")
    valuation = result.valuation
    print(
        f"  equity {valuation.equity}  realized {valuation.realized_pnl}  "
        f"unrealized {valuation.unrealized_pnl}"
    )
    print(f"  costs paid {result.state.portfolio.commission_paid['USD']}")
    report = result.report
    assert report is not None
    print(
        f"  total return {report.returns.total_return:.6f}  "
        f"max drawdown {report.drawdowns.max_drawdown:.6f}"
    )
    for record in (ACME, IDX):
        pnl = report.attribution.pnl_by_asset.get(record.asset_id, ZERO)
        print(f"  closed-trade P&L, {record.symbol:6} {pnl}")
    print("  (the future's daily variation margin is realized as it settles, so the")
    print("   closed-trade figures need not sum to the realized total)")
    optimistic = ", ".join(result.execution_assumptions.optimistic)
    print(f"  optimistic execution assumptions: {optimistic or 'none'}")

    banner("4. Identity and reproducibility")
    digest = digest_run(result)
    evidence = manifest(data, result)
    strategy_fingerprint = evidence.fingerprint
    assert strategy_fingerprint is not None
    print(f"  result     {digest.result_id}")
    print(f"  dataset    {evidence.dataset_version}")
    print(f"  strategy   {strategy_fingerprint.fingerprint}")
    print(f"  schedule   {evidence.schedule_digest}")
    print(f"  manifest   {evidence.manifest_id}")
    again = digest_run(run(data))
    print(f"  rerun      {'identical' if again.result_id == digest.result_id else 'DIFFERENT'}")

    banner("5. Replay, pause and resume, stop and continue")
    reference = result.state
    replay = replayed(data)
    print(f"  replay      fills {'equal' if replay.backtest.fills == result.fills else 'DIFFER'}")
    resumed = replayed(data, pause_at=len(combined_market_dataset(data.prices)) // 2)
    print(
        f"  paused at half, written down, restored and resumed: "
        f"{'equal' if digest_run(resumed.backtest) == digest_run(replay.backtest) else 'DIFFER'}"
    )
    stopped = continued(data, stop_after=37)
    print(
        f"  stored at 37 records and continued: "
        f"{'equal' if digest_run(stopped).result_id == digest.result_id else 'DIFFER'}"
    )
    assert reference is result.state

    banner("6. What a late value changes, and when")
    altered = run(inputs(late_score="-0.95", march_revision="3.90"))
    first_change = next(
        (
            (a.at, b.at)
            for a, b in zip(strategy.decisions, instance_of(altered).decisions, strict=True)
            if (a.equity_target, a.future_target) != (b.equity_target, b.future_target)
        ),
        None,
    )
    print("  the late post and March's revision, changed:")
    print(f"  first decision that differs: {label(first_change[0]) if first_change else 'none'}")
    print(
        f"  (the post became knowable {label(ny(LATE_POST[1]))}, "
        f"the revision {label(ny('2024-04-24 08:30'))})"
    )


if __name__ == "__main__":
    main()
