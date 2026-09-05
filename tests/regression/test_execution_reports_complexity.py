"""Regression guard for the O(N^2) execution report index fixed in v2.2.

The same defect the OMS order book had, on the same execution path and found the
same way -- by running the benchmark. ``ExecutionEngine.execute`` and
``partial_fill`` stored a report by rebuilding the whole ``reports`` dict, so N
fills copied O(N^2) entries: ``benchmarks/benchmarks_execution.py``'s 100k-fill
workload took over two minutes, and every fill a backtest produced paid a copy
of every fill before it.

``ExecutionState.reports`` is now a
:class:`~alphalab.common.persistent_map.PersistentMap`. It is still an immutable
``Mapping`` keyed by execution id and still serializes as the JSON object it
always did; what changed is that storing a report no longer rebuilds the index.

As in ``test_oms_book_complexity``, the structural tests are deterministic and
the timing test is a coarse backstop with the cyclic collector paused around the
measurement.
"""

import gc
import time
from decimal import Decimal

from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import Side
from alphalab.execution.engine import ExecutionEngine
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState
from alphalab.execution.views import all_reports, report

SCALE = 4
SMALL = 2_000
LARGE = SMALL * SCALE
# Linear predicts ~4x, quadratic ~16x. Under 8x is comfortably not quadratic.
MAX_GROWTH = 8.0
LARGE_WORKLOAD_BUDGET_SECONDS = 10.0

INSTRUCTION = OrderInstruction(
    "ORD-1", "STRAT", "AAPL", Decimal("100"), Decimal("150.00"), Side.BUY, "SIM", "USD"
)


def _simulate(count: int) -> ExecutionState:
    state = ExecutionState()
    simulator = ExecutionSimulator()
    for i in range(count):
        state = ExecutionEngine.simulate(
            state,
            simulator,
            INSTRUCTION,
            Decimal("1"),
            Decimal("150.00"),
            float(i) + 1.0,
            FillStatus.FULL_FILL,
        )
    return state


def _time_fills(count: int) -> float:
    """Time a run of fills with the cyclic collector paused; see module docstring."""

    gc.disable()
    try:
        start = time.perf_counter()
        _simulate(count)
        return time.perf_counter() - start
    finally:
        gc.enable()


# ---------------------------------------------------------------------------
# Structural guarantees (deterministic)
# ---------------------------------------------------------------------------


def test_the_report_index_is_persistent() -> None:
    assert isinstance(ExecutionState().reports, PersistentMap)


def test_storing_reports_does_not_rebuild_the_index() -> None:
    state = ExecutionState()
    simulator = ExecutionSimulator()
    stores = set()
    for i in range(200):
        state = ExecutionEngine.simulate(
            state,
            simulator,
            INSTRUCTION,
            Decimal("1"),
            Decimal("150.00"),
            float(i) + 1.0,
            FillStatus.FULL_FILL,
        )
        stores.add(id(state.reports._store))

    assert len(stores) == 1
    assert len(state.reports) == 200


def test_partial_fills_share_the_index_too() -> None:
    state = ExecutionState()
    simulator = ExecutionSimulator()
    stores = set()
    for i in range(100):
        state = ExecutionEngine.simulate(
            state,
            simulator,
            INSTRUCTION,
            Decimal("1"),
            Decimal("150.00"),
            float(i) + 1.0,
            FillStatus.PARTIAL_FILL,
        )
        stores.add(id(state.reports._store))

    assert len(stores) == 1
    assert len(state.reports) == 100


# ---------------------------------------------------------------------------
# The index is still a value, and still readable
# ---------------------------------------------------------------------------


def test_an_earlier_state_does_not_see_later_reports() -> None:
    first = _simulate(1)
    second = ExecutionEngine.simulate(
        first,
        ExecutionSimulator(),
        INSTRUCTION,
        Decimal("1"),
        Decimal("150.00"),
        99.0,
        FillStatus.FULL_FILL,
    )

    assert len(first.reports) == 1
    assert len(second.reports) == 2
    assert all_reports(first)[0] in all_reports(second)


def test_every_report_is_retained_and_findable_by_id() -> None:
    """The fix must not drop reports to go faster."""

    state = _simulate(500)

    assert len(state.reports) == 500
    assert len(state.history) == 500
    for stored in all_reports(state):
        assert report(state, stored.execution_id) is stored


def test_reports_iterate_in_execution_order() -> None:
    state = _simulate(50)

    assert [r.execution_id for r in all_reports(state)] == [r.execution_id for r in state.history]


# ---------------------------------------------------------------------------
# Timing backstop (coarse)
# ---------------------------------------------------------------------------


def test_fill_cost_grows_linearly_with_the_workload() -> None:
    _time_fills(200)  # warm up

    small = min(_time_fills(SMALL) for _ in range(2))
    large = min(_time_fills(LARGE) for _ in range(2))

    assert large < LARGE_WORKLOAD_BUDGET_SECONDS, f"{LARGE} fills took {large:.2f}s"
    growth = large / max(small, 1e-6)
    assert growth < MAX_GROWTH, (
        f"{SCALE}x the workload cost {growth:.1f}x the time "
        f"({small:.3f}s -> {large:.3f}s); the report index looks quadratic again"
    )
