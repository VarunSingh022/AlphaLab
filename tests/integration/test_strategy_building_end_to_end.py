"""A new strategy, built and checked through the public API end to end (v4.0, ledger DOC-009).

``examples/70_build_a_strategy.py`` is the v4.0 acceptance exercise: ingest
synthetic rows, write a strategy, start it, run it, read what it did, check it
against arithmetic done by hand, run it with no signal, refuse what is invalid,
and reproduce it -- again, under another seed, and continued from a snapshot.
This test imports that file and holds each of those to a value, so the example a
new user is pointed at cannot drift from what the engine does.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.lifecycle import digest_run

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "70_build_a_strategy.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_70", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_crossover_trades_what_the_hand_computation_says(example: ModuleType) -> None:
    result = example.run(example.TRENDING)
    expected = example.by_hand(example.TRENDING)

    # Fast/slow averages of (2, 4): above from the fifth close (101), below from the ninth (100).
    assert expected.fills == (
        example.HandFill("buy", Decimal("10"), Decimal("101"), Decimal("0.10")),
        example.HandFill("sell", Decimal("10"), Decimal("100"), Decimal("0.10")),
    )
    assert example.fills_of(result) == expected.fills
    assert result.valuation.cash == expected.cash == Decimal("99989.80")
    assert result.valuation.realized_pnl == Decimal("-10.00")
    assert result.state.portfolio.commission_paid["USD"] == Decimal("0.20")
    assert result.records_processed == 12
    assert len(result.orders) == 2
    assert result.strategy_failures == ()
    report = result.report
    assert report is not None
    assert report.returns.total_return == pytest.approx(-0.000102)
    assert report.trades.closed_trades == 1


def test_no_signal_places_no_order(example: ModuleType) -> None:
    flat = example.run(example.FLAT)

    assert flat.orders == () and flat.fills == ()
    assert flat.valuation.cash == example.START_CASH


def test_the_run_is_reproduced_by_its_digests(example: ModuleType) -> None:
    first, again = (
        digest_run(example.run(example.TRENDING)),
        digest_run(example.run(example.TRENDING)),
    )
    other = example.run(example.TRENDING, seed=example.SEED + 1)

    assert first.result_id == again.result_id
    assert first.configuration_id == again.configuration_id
    assert example.fills_of(other) == example.fills_of(example.run(example.TRENDING))
    assert digest_run(other).result_id != first.result_id


@pytest.mark.parametrize("stop_after", [1, 5, 6, 11])
def test_a_run_continued_from_its_snapshot_is_the_uninterrupted_run(
    example: ModuleType, stop_after: int
) -> None:
    uninterrupted = digest_run(example.run(example.TRENDING))
    resumed = digest_run(example.continued(example.TRENDING, stop_after=stop_after))

    assert resumed.result_id == uninterrupted.result_id


def test_every_invalid_input_is_refused(example: ModuleType) -> None:
    refused = example.refusals()

    assert set(refused) == {
        "fast window not shorter than slow",
        "a close that is not a number",
        "a missing close",
        "a negative close",
        "continuing under another commission",
    }
    assert all(outcome != "ACCEPTED" for outcome in refused.values()), refused
    assert refused["a close that is not a number"].startswith("DataQualityError")
    assert refused["a missing close"].startswith("DataQualityError")
    assert refused["continuing under another commission"].startswith("StateDecodeError")


def test_the_example_runs_as_a_script(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    example.main()

    printed = capsys.readouterr().out
    assert "fills           : 2 of 2 as computed" in printed
    assert "continued: same result id" in printed
