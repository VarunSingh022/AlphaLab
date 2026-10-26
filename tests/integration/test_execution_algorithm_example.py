"""Example 74 measures what it says it measures (v4.1.1, ledger FUT-009).

``examples/74_evaluate_an_execution_algorithm.py`` executes one decision five
ways over the same bars. These tests hold its figures to the mechanism: the
whole order is cut by the liquidity cap, every algorithm completes the parent in
children, each child pays the run's costs, and the replay and a resumed replay
are the run.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.core.enums import OrderStatus
from alphalab.lifecycle import digest_run

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "74_evaluate_an_execution_algorithm.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_74", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_whole_order_is_cut_by_the_cap_and_every_algorithm_completes(
    example: ModuleType,
) -> None:
    whole = example.measure("whole", example.run("whole"))
    # A quarter of the next bar's volume, and the rest withdrawn.
    assert whole.status is OrderStatus.CANCELLED and whole.fills == 1
    assert whole.filled == (example.volume(1) / 4).to_integral_value(rounding="ROUND_DOWN")
    for name in ("twap", "vwap", "participation", "iceberg"):
        measured = example.measure(name, example.run(name))
        assert measured.status is OrderStatus.FILLED, name
        assert measured.filled == example.QUANTITY
        assert measured.commission == Decimal("1.00") * measured.fills
        assert measured.shortfall_bps is not None


def test_every_child_fill_is_capped_by_its_own_bar(example: ModuleType) -> None:
    for name in ("twap", "vwap", "participation", "iceberg"):
        book = example.run(name).algorithms
        assert book is not None
        for fill in book.fills:
            minute = round((fill.timestamp - example.OPEN) / example.MINUTE)
            assert fill.quantity <= example.volume(minute) / 4, (name, minute)


def test_the_replay_and_a_resumed_replay_are_the_run(example: ModuleType) -> None:
    backtest = example.run("twap")
    replay = example.replayed("twap")
    assert replay.fills == backtest.fills
    for pause in (1, 7, 26):
        resumed = example.replayed("twap", pause=pause)
        assert digest_run(resumed).result_id == digest_run(replay).result_id


def test_the_example_runs_to_its_end(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    example.main()
    out = capsys.readouterr().out
    assert "replay paused at bar 7" in out and "rerun: the same result" in out
