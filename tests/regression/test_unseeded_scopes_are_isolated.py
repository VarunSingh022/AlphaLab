"""An unseeded scope is isolated: it draws nothing from a stream around it (DET-007).

Every driver opens its run's identifier stream with ``id_scope(config.seed)``. A
seeded run mints reproducible identifiers and an unseeded one keeps ``uuid4``:
the function's docstring and ARCHITECTURE's identity section both say so. Until
v4.3 an unseeded scope was ``nullcontext()``, so a run declared unseeded but
started inside another seeded scope minted from that scope's stream. Its
identifiers looked reproducible and were not its own, and every identifier the
outer scope minted after it moved -- the class of defect ADR-0022 removed from
resume and ADR-0029 D6 from persistence: a component that draws from a stream it
does not own changes what it runs beside.

These probes hold the corrected contract as ADR-0029 section 7 holds the
run-state store. Across a nested unseeded run the outer stream advances by
exactly zero, so the outer computation mints what it would have minted without
it, in this process and in a fresh one. The nested run mints from ``uuid4``, so
two of them differ. A seeded run is its seed's wherever it starts.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, ReplayBacktest
from alphalab.common.ids import current_id_position, id_scope, new_id
from alphalab.lifecycle import digest_run
from alphalab.market.source import SequenceSource
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.session import TradingSession
from tests.integration import replay_workload as workload
from tests.integration.harness import context_factory

ROOT = Path(__file__).resolve().parents[2]
#: The seed of the scope every probe runs its nested run inside.
OUTER = 11

#: A run, given everything it starts from. Its inputs are built before the outer
#: scope is entered: building a strategy state emits its lifecycle events, which
#: are the caller's, minted in whatever scope the caller builds it in.
type Run = Callable[[], Any]


def _backtest(seed: int | None) -> Run:
    config, dataset, strategies = (
        workload.config(seed),
        workload.dataset(),
        workload.strategy_state(),
    )
    return lambda: BacktestEngine.run(config, dataset, strategies, context_factory)


def _replay(seed: int | None) -> Run:
    config, dataset, strategies = (
        workload.config(seed),
        workload.dataset(),
        workload.strategy_state(),
    )
    return lambda: ReplayBacktest.run(config, dataset, strategies, context_factory).backtest


def _session(seed: int | None) -> Run:
    dataset = workload.dataset()
    config = replace(workload.config(seed), mode=ExecutionMode.PAPER)
    source = SequenceSource.from_records(dataset.dataset_id, dataset.records)
    strategies = workload.strategy_state()
    return lambda: TradingSession.run(config, source, strategies, context_factory)


#: Every driver that opens a run's scope with ``id_scope(config.seed)``.
DRIVERS: dict[str, Callable[[int | None], Run]] = {
    "backtest": _backtest,
    "replay": _replay,
    "session": _session,
}


def outer_identifiers(driver: str | None) -> tuple[str, str, str]:
    """Three identifiers the outer scope mints, with ``driver``'s unseeded run between them."""

    run = DRIVERS[driver](None) if driver is not None else None
    with id_scope(OUTER):
        first = new_id()
        if run is not None:
            run()
        return first, new_id(), new_id()


@pytest.mark.parametrize("driver", sorted(DRIVERS))
def test_an_unseeded_run_draws_nothing_from_the_scope_around_it(driver: str) -> None:
    run = DRIVERS[driver](None)
    with id_scope(OUTER):
        before = current_id_position()
        run()
        after = current_id_position()

    assert after == before


@pytest.mark.parametrize("driver", sorted(DRIVERS))
def test_the_outer_scope_mints_what_it_would_have_minted_without_the_nested_run(
    driver: str,
) -> None:
    assert outer_identifiers(driver) == outer_identifiers(None)


@pytest.mark.parametrize("driver", ["backtest", "replay"])
def test_a_nested_unseeded_run_mints_from_uuid4(driver: str) -> None:
    """Started twice inside the same seeded scope, an unseeded run is two different runs."""

    def nested() -> str:
        run = DRIVERS[driver](None)
        with id_scope(OUTER):
            return digest_run(run()).result_id

    assert nested() != nested()


def test_a_seeded_run_is_its_seeds_wherever_it_starts() -> None:
    alone = digest_run(_backtest(workload.SEED)()).result_id
    run = _backtest(workload.SEED)
    with id_scope(OUTER):
        inside = digest_run(run()).result_id

    assert inside == alone


_IN_A_FRESH_INTERPRETER = textwrap.dedent(
    """
    import sys
    from tests.regression.test_unseeded_scopes_are_isolated import outer_identifiers
    print(" ".join(outer_identifiers(sys.argv[1])))
    """
)


@pytest.mark.parametrize("driver", sorted(DRIVERS))
def test_in_a_fresh_interpreter_the_outer_stream_is_the_one_without_the_nested_run(
    driver: str,
) -> None:
    finished = subprocess.run(
        [sys.executable, "-c", _IN_A_FRESH_INTERPRETER, driver],
        cwd=ROOT,
        env={**os.environ, "PYTHONHASHSEED": "7", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.split() == list(outer_identifiers(None))
