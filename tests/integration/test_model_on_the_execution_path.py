"""Research outputs reach the execution path without a second definition or a look-ahead (v4.1.1).

``examples/73_train_and_trade_a_model.py`` fits a linear model on two
``FeatureDefinition``s computed by the study path, and trades it through the
canonical path with a strategy that evaluates the same definitions on the
closes it has been shown. Ledger FUT-006 (one feature definition, in a study and
in a strategy) and FUT-007 (a trained model on the execution path, evidenced)
are closed by what this file establishes:

* the strategy's feature values equal the study's at every instant both have;
* every training label was knowable before the boundary, and changing every
  price from the boundary on changes neither the model nor any decision before it;
* no order exists before the boundary;
* the fitted parameters are the strategy's declared parameters -- fingerprinted
  -- and its state round-trips exactly through a stored, continued run;
* a rerun, in this process and a fresh one, is identical.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.api import to_market_dataset
from alphalab.backtesting import BacktestEngine, BacktestResult
from alphalab.common.ids import id_scope
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.run import RunEngine
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "73_train_and_trade_a_model.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_73", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(example: ModuleType) -> Any:
    return example.dataset()


@pytest.fixture(scope="module")
def training(example: ModuleType, data: Any) -> Any:
    return example.fit(data)


@pytest.fixture(scope="module")
def result(example: ModuleType, data: Any, training: Any) -> BacktestResult:
    run: BacktestResult = example.run(data, training)
    return run


def test_the_strategy_computes_the_studys_features_exactly(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    study = example.study_features(data)
    seen = example.instance_of(result).seen
    # Every instant past the longer window, both computed, all equal.
    assert len(seen) == example.SESSIONS - 9
    assert example.agreement(data, result) == len(seen)
    for name in example.FEATURE_NAMES:
        assert set(study[name]) >= set(seen)


def test_every_training_label_was_known_before_the_boundary(
    example: ModuleType, training: Any
) -> None:
    day, first = example.DAY, example.FIRST_BAR
    assert training.boundary_time == first + example.BOUNDARY * day
    last = training.sample_times[-1]
    # Its label is the close HORIZON sessions later: before the boundary.
    assert last + example.HORIZON * day < training.boundary_time
    # The sample one session later -- whose label is the boundary's close -- is purged.
    assert last + day not in training.sample_times
    assert training.sample_times == tuple(sorted(training.sample_times))


def test_prices_from_the_boundary_on_change_neither_the_model_nor_an_earlier_decision(
    example: ModuleType, training: Any, result: BacktestResult
) -> None:
    shocked_data = example.dataset(shock=Decimal("25"))
    shocked = example.fit(shocked_data)
    assert shocked.model == training.model
    assert shocked.sample_times == training.sample_times

    other = example.run(shocked_data, shocked)
    boundary = training.boundary_time
    before = example.instance_of(result).seen
    after = example.instance_of(other).seen
    assert {at: v for at, v in before.items() if at < boundary} == {
        at: v for at, v in after.items() if at < boundary
    }
    assert [o.created_at for o in other.orders] and min(
        o.created_at for o in other.orders
    ) >= boundary


def test_no_order_is_placed_before_the_boundary(training: Any, result: BacktestResult) -> None:
    assert result.orders
    assert min(order.created_at for order in result.orders) == training.boundary_time
    assert all(fill.filled_at > training.boundary_time for fill in result.fills)


def test_the_model_is_the_strategys_declared_parameters(example: ModuleType, training: Any) -> None:
    parameters = example.model_parameters(training)
    assert parameters["intercept"] == training.model.intercept
    for name, coefficient in zip(example.FEATURE_NAMES, training.model.coefficients, strict=True):
        assert parameters[f"coef.{name}"] == coefficient
    built = example.CLASSES.construct(example.STRATEGY_ID, parameters)
    assert built._model == training.model


@pytest.mark.parametrize("stop_after", [10, 79, 80, 81, 119])
def test_a_run_stored_and_continued_with_a_rebuilt_strategy_is_the_same_run(
    example: ModuleType, data: Any, training: Any, result: BacktestResult, stop_after: int
) -> None:
    config = example.run_config()
    records = to_market_dataset(data, example.NORMALIZATION).records
    initial = example.running(training)
    assert config.seed is not None
    with id_scope(config.seed):
        state = replace(BacktestEngine.initialize(config, initial), source_id=data.dataset_version)
        for record in records[:stop_after]:
            state = RunEngine.close_slice(state, example.CONTEXTS, before=record.timestamp)
            state, _ = RunEngine.advance(state, record, example.CONTEXTS)
    payload = serialize(capture(state))

    parameters = example.model_parameters(training)
    restored = restore(
        from_primitives(deserialize(payload)),
        RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=config.pipeline.sizing_model,
                simulator=config.pipeline.simulator,
                strategies={
                    example.STRATEGY_ID: example.CLASSES.construct(example.STRATEGY_ID, parameters)
                },
                instruments=example.REGISTRY,
            ),
            fill_policy=config.fill_policy,
        ),
    )
    with BacktestEngine.resume(restored):
        for record in records[stop_after:]:
            restored = RunEngine.close_slice(restored, example.CONTEXTS, before=record.timestamp)
            restored, _ = RunEngine.advance(restored, record, example.CONTEXTS)
        finished = BacktestResult(
            run=RunEngine.finalize(RunEngine.close_slice(restored, example.CONTEXTS))
        )
    assert digest_run(finished).result_id == digest_run(result).result_id


def test_a_strategy_built_with_another_model_refuses_the_captured_state(
    example: ModuleType, training: Any, result: BacktestResult
) -> None:
    captured = example.instance_of(result).capture_state()
    other = dict(example.model_parameters(training))
    other["intercept"] = float(other["intercept"]) + 1.0
    built = example.CLASSES.construct(example.STRATEGY_ID, other)
    with pytest.raises(ValueError, match="not the one"):
        built.restore_state(deserialize(serialize(captured)), 1)


def test_a_fresh_interpreter_produces_the_same_result(result: BacktestResult) -> None:
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('ex73', {str(EXAMPLE)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['ex73'] = module\n"
        "spec.loader.exec_module(module)\n"
        "from alphalab.lifecycle import digest_run\n"
        "data = module.dataset()\n"
        "print(digest_run(module.run(data, module.fit(data))).result_id)\n"
    )
    for seed in ("0", "777"):
        completed = subprocess.run(
            [sys.executable, "-W", "error", "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)},
            cwd=ROOT,
        )
        assert completed.stdout.strip() == digest_run(result).result_id
