"""
AlphaLab Examples
=================

Example 73 : Train a Model on the Past and Trade It Forward

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 70 (build a strategy)
✓ Example 17 (feature engineering)
✓ Example 20 (walk-forward validation)

Topics
------

• Two features defined once, as ``FeatureDefinition``s, and computed by the
  study path over a versioned dataset
• A linear model fitted only on samples whose label was knowable before a
  boundary -- the sample whose forward return reaches across it is purged
• The same definitions evaluated inside the strategy, on the execution path,
  from the closes it has been shown -- and equal to the study's values
• The fitted coefficients as the strategy's declared parameters and state:
  fingerprinted, captured, restored exactly
• Trading only after the boundary, with costs, and reproducing the run

What this shows
---------------

v4.1.1's evidence for ledger FUT-006 and FUT-007: research outputs -- features
and a fitted model -- reach the canonical engine path without a second
definition of either, and without information from after the boundary. The
prices are **synthetic**; nothing here says the model predicts anything.
``tests/integration/test_model_on_the_execution_path.py`` holds it to that.

Run

    python examples/73_train_and_trade_a_model.py
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.api import backtest, ingest_rows, observe
from alphalab.backtesting import BacktestResult, ExecutionMode, RunConfig
from alphalab.common.types import ParamValue
from alphalab.core.enums import AssetType
from alphalab.data import (
    REFUSE_EVERYTHING,
    DataAssetClass,
    Dataset,
    IngestionRequest,
    PriceBasis,
    SourceKind,
    TimeFrequency,
    raw_source_from_bytes,
)
from alphalab.data.time import BarStamp
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillTiming
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.factor_library.compute import compute_feature
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.lifecycle import digest_run
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy
from alphalab.ml import LinearRegressionModel, predict_linear, train_linear_regression
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
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.registry import StrategyClassRegistry, runtime_for
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor

STRATEGY_ID = "EX73-MODEL"
SYMBOL = "SYN"
PROVIDER = "synthetic"
SEED = 73
START_CASH = Decimal("1000000.00")
DAY = 86_400.0
FIRST_BAR = 1_704_153_600.0
SESSIONS = 120
#: Samples before this session train the model; the strategy trades from it on.
BOUNDARY = 80
#: The label is the next session's return: one session ahead.
HORIZON = 1

INSTRUMENT = InstrumentRecord(SYMBOL, AssetType.EQUITY, "XSYN", "USD", aliases={PROVIDER: SYMBOL})
REGISTRY: InstrumentRegistry = register_instruments(InstrumentRegistry(), (INSTRUMENT,))
NORMALIZATION = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="XSYN",
    currency="USD",
    timeframe=TimeFrame.D1,
    identity=REGISTRY,
    provider=PROVIDER,
)
CONTEXTS = context_factory(FixedClock(FIRST_BAR), DiscardingLogger())

#: The two features, defined once and read by the study and the strategy alike.
FEATURES = (
    FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5),
    FeatureDefinition("z_10", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=10),
)
FEATURE_NAMES = tuple(definition.feature_id for definition in FEATURES)


# --------------------------------------------------------------------------- #
# 1. Data
# --------------------------------------------------------------------------- #


def closes(shock: Decimal = Decimal("0")) -> list[Decimal]:
    """A synthetic path with some persistence; ``shock`` moves every close after the boundary."""

    pattern = (3, 2, -1, 4, -2, -3, 1, 2, -4, 3, -1, -2, 2)
    out, price = [], Decimal("100")
    for index in range(SESSIONS):
        step = Decimal(pattern[index % len(pattern)]) / Decimal(250)
        price = (price * (1 + step)).quantize(Decimal("0.01"))
        out.append(price + (shock if index >= BOUNDARY else Decimal("0")))
    return out


def dataset(shock: Decimal = Decimal("0")) -> Dataset:
    rows = [
        {
            "timestamp": str(FIRST_BAR + index * DAY),
            "symbol": SYMBOL,
            "open": str(close),
            "high": str(close),
            "low": str(close),
            "close": str(close),
            "volume": "1000000",
        }
        for index, close in enumerate(closes(shock))
    ]
    payload = json.dumps(rows, sort_keys=True).encode("utf-8")
    request = IngestionRequest(
        name="EX73-SYNTHETIC",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "synthetic://example-73", payload, FIRST_BAR, "application/json"
        ),
        frequency=TimeFrequency.DAILY,
        bar_stamp=BarStamp.INTERVAL_END,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=REFUSE_EVERYTHING,
        price_basis=PriceBasis.RAW,
    )
    return ingest_rows(rows, request).dataset


# --------------------------------------------------------------------------- #
# 2. The study: features over the dataset, and a model fitted before the boundary
# --------------------------------------------------------------------------- #


def study_features(data: Dataset) -> dict[str, dict[float, float]]:
    """Each feature's value at each instant, computed by the study path."""

    frame = observe(data, FeatureField.CLOSE)
    out: dict[str, dict[float, float]] = {}
    for definition in FEATURES:
        (series,) = compute_feature(definition, frame)
        out[definition.feature_id] = dict(zip(series.timestamps, series.values, strict=True))
    return out


@dataclass(frozen=True)
class Training:
    """What was fitted, and on what."""

    model: LinearRegressionModel
    sample_times: tuple[float, ...]
    boundary_time: float


def fit(data: Dataset) -> Training:
    """Fit on every sample whose features *and* label were known before the boundary.

    A sample at session ``t`` is labelled with the return to ``t + HORIZON``;
    it is used only if ``t + HORIZON`` is before the boundary -- the purge that
    stops the last training label from reaching into the trading period.
    """

    frame = observe(data, FeatureField.CLOSE)
    (row,) = frame.series.values()
    stamps, values = row.timestamps, row.values
    boundary_time = stamps[BOUNDARY]
    features = study_features(data)
    x: list[tuple[float, ...]] = []
    y: list[float] = []
    times: list[float] = []
    for index in range(len(stamps) - HORIZON):
        if stamps[index + HORIZON] >= boundary_time:
            break
        at = stamps[index]
        if not all(at in features[name] for name in FEATURE_NAMES):
            continue
        x.append(tuple(features[name][at] for name in FEATURE_NAMES))
        y.append(values[index + HORIZON] / values[index] - 1)
        times.append(at)
    model = train_linear_regression(FEATURE_NAMES, tuple(x), tuple(y), l2_penalty=0.01)
    return Training(model, tuple(times), boundary_time)


# --------------------------------------------------------------------------- #
# 3. The strategy: the same features, the fitted model, the canonical path
# --------------------------------------------------------------------------- #


def model_parameters(training: Training) -> dict[str, ParamValue]:
    """The model as the strategy's declared parameters -- so its fingerprint hashes it."""

    parameters: dict[str, ParamValue] = {
        "intercept": training.model.intercept,
        "l2_penalty": training.model.l2_penalty,
        "boundary_time": training.boundary_time,
        "size": 1000,
    }
    for name, coefficient in zip(FEATURE_NAMES, training.model.coefficients, strict=True):
        parameters[f"coef.{name}"] = coefficient
    return parameters


class ModelStrategy(BaseStrategy):
    """Predicts the next return from the two features; holds ``size`` shares long or short.

    The features are computed with the study's own definitions over the closes
    the strategy has been shown, so they cannot see a later close. Orders are
    placed only from the boundary on.
    """

    def __init__(self, strategy_id: str, parameters: Mapping[str, ParamValue]) -> None:
        self._strategy_id = strategy_id
        self._model = LinearRegressionModel(
            FEATURE_NAMES,
            tuple(float(parameters[f"coef.{name}"]) for name in FEATURE_NAMES),
            float(parameters["intercept"]),
            float(parameters["l2_penalty"]),
        )
        self._boundary = float(parameters["boundary_time"])
        self._size = Decimal(str(parameters["size"]))
        self._times: list[float] = []
        self._closes: list[float] = []
        self.seen: dict[float, tuple[float, ...]] = {}

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._times.append(bar.timestamp)
        self._closes.append(float(bar.close))
        frame = ObservationFrame(
            FeatureField.CLOSE,
            {SYMBOL: ObservationSeries(SYMBOL, tuple(self._times), tuple(self._closes))},
            "UTC",
        )
        values = []
        for definition in FEATURES:
            (series,) = compute_feature(definition, frame)
            if not series.timestamps or series.timestamps[-1] != bar.timestamp:
                return ()
            values.append(series.values[-1])
        self.seen[bar.timestamp] = tuple(values)
        if bar.timestamp < self._boundary:
            return ()
        (prediction,) = predict_linear(self._model, (tuple(values),))
        target = self._size if prediction > 0 else -self._size
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=INSTRUMENT.asset_id,
                target=target,
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )

    def strategy_state_version(self) -> int:
        return 1

    def capture_state(self) -> dict[str, Any]:
        return {
            "times": list(self._times),
            "closes": list(self._closes),
            "model": {
                "coefficients": list(self._model.coefficients),
                "intercept": self._model.intercept,
                "l2_penalty": self._model.l2_penalty,
            },
        }

    def restore_state(self, payload: Any, version: int) -> None:
        if version != 1:
            raise ValueError(f"ModelStrategy cannot read state version {version}")
        self._times = [float(value) for value in payload["times"]]
        self._closes = [float(value) for value in payload["closes"]]
        model = payload["model"]
        restored = LinearRegressionModel(
            FEATURE_NAMES,
            tuple(float(value) for value in model["coefficients"]),
            float(model["intercept"]),
            float(model["l2_penalty"]),
        )
        if restored != self._model:
            raise ValueError("the captured model is not the one this strategy was built with")


def model_factory(strategy_id: str, parameters: Mapping[str, ParamValue], /) -> StrategyProtocol:
    return ModelStrategy(strategy_id, parameters)


CLASSES = StrategyClassRegistry().register(STRATEGY_ID, model_factory)


def running(training: Training) -> RuntimeState:
    definition = StrategyDefinition(
        strategy_id=STRATEGY_ID,
        name="Linear model on momentum and z-score",
        version="1",
        author="examples",
        description="fitted before the boundary, traded after it",
        parameters=model_parameters(training),
    )
    state = runtime_for(CLASSES, (definition,))
    strategy = state.strategies[STRATEGY_ID]
    strategy, _ = RuntimeSupervisor.configure(strategy, {}, 1.0)
    strategy, _ = RuntimeSupervisor.initialize(strategy, 1.1)
    strategy, _ = RuntimeSupervisor.subscribe(strategy, frozenset({"bars"}), 1.2)
    strategy, _ = RuntimeSupervisor.start(strategy, 1.3)
    return replace(state, strategies={STRATEGY_ID: strategy})


def run_config() -> RunConfig:
    ample = Decimal("1000000000")
    return RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=ExecutionPipelineConfig(
            account=Account("ACC-EX73", "USD", "Example 73", 1.0),
            starting_cash=START_CASH,
            budget=CapitalBudget(
                global_capital=START_CASH,
                maximum_exposure=START_CASH,
                cash_buffer=Decimal("0"),
                strategy_budgets={STRATEGY_ID: START_CASH},
            ),
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            risk_limits=RiskLimits(
                order_size=OrderSizeLimit(ample, ample),
                position=PositionLimit(ample, ample),
                exposure=ExposureLimit(ample, ample),
                leverage=LeverageLimit(Decimal("2")),
                margin=MarginLimit(Decimal("1")),
                daily_loss=DailyLossLimit(ample, "UTC"),
                drawdown=DrawdownLimit(Decimal("1")),
            ),
            simulator=ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.01"))),
            instruments=REGISTRY,
            fill_timing=FillTiming.NEXT_EVENT,
        ),
        seed=SEED,
        start_timestamp=FIRST_BAR - 1.0,
    )


def run(data: Dataset, training: Training) -> BacktestResult:
    return backtest(run_config(), data, running(training), CONTEXTS, NORMALIZATION)


def instance_of(result: BacktestResult) -> ModelStrategy:
    instance = result.state.strategy.strategies[STRATEGY_ID].instance
    assert isinstance(instance, ModelStrategy)
    return instance


def agreement(data: Dataset, result: BacktestResult) -> int:
    """How many instants the strategy's features equal the study's exactly."""

    study = study_features(data)
    seen = instance_of(result).seen
    for at, values in seen.items():
        expected = tuple(study[name][at] for name in FEATURE_NAMES)
        if values != expected:
            raise AssertionError(f"at {at} the strategy computed {values}, the study {expected}")
    return len(seen)


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 73 : Train a Model on the Past and Trade It Forward")
    print("=" * 72)
    data = dataset()
    training = fit(data)
    print(f"\n  dataset    {data.dataset_version}")
    last = round((training.sample_times[-1] - FIRST_BAR) / DAY)
    print(
        f"  trained on {len(training.sample_times)} samples, the last at session {last}; "
        f"the boundary is session {BOUNDARY}"
    )
    coefficients = ", ".join(f"{c:+.6f}" for c in training.model.coefficients)
    print(f"  model      intercept {training.model.intercept:+.6f}, coefficients {coefficients}")
    shocked = fit(dataset(shock=Decimal("25")))
    unchanged = shocked.model == training.model
    print(f"  prices after the boundary changed: model {'unchanged' if unchanged else 'CHANGED'}")

    result = run(data, training)
    print(f"\n  features equal to the study's at {agreement(data, result)} instants")
    first = min(order.created_at for order in result.orders)
    print(
        f"  first order at session {round((first - FIRST_BAR) / DAY)}; "
        f"{len(result.fills)} fills; equity {result.valuation.equity}"
    )
    print(f"  result     {digest_run(result).result_id}")
    again = run(dataset(), fit(dataset()))
    print(f"  rerun      {'identical' if digest_run(again) == digest_run(result) else 'DIFFERENT'}")


if __name__ == "__main__":
    main()
