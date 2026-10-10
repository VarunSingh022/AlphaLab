"""Regression guard for API-004: a finished multi-currency run can be valued.

``BacktestResult.valuation`` values the book in the run's currency and takes no
rates, so a run that held cash or a position in another currency raised there
and had no other way to be valued. ``valuation_in(currency, rates)`` is that way;
the property is documented as single-currency, and still refuses a mixed book
rather than summing currencies.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.backtesting import BacktestResult
from alphalab.portfolio.exceptions import MixedCurrencyValuationError
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.strategy.runtime import create_runtime
from tests.integration.harness import START_CASH, pipeline_config

RATES = FxRates.of([FxRate("EUR", "USD", Decimal("1.10"), 0.0, "test")])


def _two_currency_pipeline() -> ExecutionPipelineConfig:
    config = pipeline_config("S")
    return replace(config, also_settles=frozenset({"EUR"}), budget=config.budget.in_currency("USD"))


def _finished_run_holding_euros() -> BacktestResult:
    config = RunConfig(
        mode=ExecutionMode.BACKTEST,
        pipeline=_two_currency_pipeline(),
        seed=7,
        start_timestamp=1.0,
    )
    state = RunEngine.initialize(config, create_runtime())
    funded = ExecutionPipeline.fund(state.pipeline, Decimal("1000"), "EUR", 1.5, RATES)
    return BacktestResult(RunEngine.finalize(replace(state, pipeline=funded)))


def test_the_property_refuses_a_book_it_cannot_value_without_rates() -> None:
    with pytest.raises(MixedCurrencyValuationError):
        _ = _finished_run_holding_euros().valuation


def test_valuation_in_converts_every_other_currency_at_the_callers_rates() -> None:
    valuation = _finished_run_holding_euros().valuation_in("USD", RATES)

    assert valuation.currency == "USD"
    assert valuation.cash == START_CASH + Decimal("1100.00")
    assert valuation.equity == START_CASH + Decimal("1100.00")


def test_valuation_in_values_in_the_currency_it_is_asked_for() -> None:
    """Not the run's own: a run settled in dollars, valued in euros (mutation X06)."""

    both_ways = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.25"), 0.0, "test"),
            FxRate("USD", "EUR", Decimal("0.80"), 0.0, "test"),
        ]
    )

    valuation = _finished_run_holding_euros().valuation_in("EUR", both_ways)

    assert valuation.currency == "EUR"
    assert valuation.cash == Decimal("1000.00") + START_CASH * Decimal("0.80")


def test_a_single_currency_run_values_the_same_either_way() -> None:
    config = RunConfig(
        mode=ExecutionMode.BACKTEST, pipeline=pipeline_config("S"), seed=7, start_timestamp=1.0
    )
    result = BacktestResult(RunEngine.finalize(RunEngine.initialize(config, create_runtime())))

    assert result.valuation == result.valuation_in("USD", FxRates())
