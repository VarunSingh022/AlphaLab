"""A live session's order bindings and reconciliation history grow linearly (ledger PRF-003).

Until v3.10 ``ExternalOrderMap.bind`` copied both of its dictionaries on every
binding, so binding ``N`` orders over a session cost ``O(N^2)``, and
``ReconciliationLog.record`` concatenated a tuple per decision. Both are
persistent now: a binding shares structure with the map before it, and a record
is an append. Timed with the stabilized method (``tests/regression/_timing.py``).
"""

from __future__ import annotations

from decimal import Decimal

from alphalab.broker.execution import BrokerExecution
from alphalab.broker.reconciliation import (
    ExecutionDecision,
    ExecutionOutcome,
    ExternalOrderMap,
    ReconciliationLog,
)
from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from tests.regression._timing import growth

SMALL, LARGE = 2_000, 8_000

#: 4x the work. Linear growth reads about 4; copying per operation, about 16.
MAX_GROWTH = 8.0


def _bind(count: int) -> ExternalOrderMap:
    mapping = ExternalOrderMap()
    for index in range(count):
        mapping = mapping.bind(f"oms-{index}", f"venue-{index}")
    return mapping


def _decision(index: int) -> ExecutionDecision:
    execution = BrokerExecution(
        f"E-{index}", f"venue-{index}", "X", Decimal("1"), Decimal("10"), Decimal("0"), 1.0
    )
    return ExecutionDecision(ExecutionOutcome.DUPLICATE, execution, "redelivered")


def _record(count: int) -> ReconciliationLog:
    log = ReconciliationLog()
    for index in range(count):
        log = log.record(_decision(index))
    return log


def test_binding_orders_over_a_session_stays_linear() -> None:
    ratio = growth(lambda: _bind(SMALL), lambda: _bind(LARGE))

    assert ratio < MAX_GROWTH, f"4x the bindings took {ratio:.1f}x as long"


def test_recording_decisions_over_a_session_stays_linear() -> None:
    ratio = growth(lambda: _record(SMALL), lambda: _record(LARGE))

    assert ratio < MAX_GROWTH, f"4x the decisions took {ratio:.1f}x as long"


def test_a_binding_leaves_the_map_before_it_unchanged() -> None:
    before = ExternalOrderMap().bind("oms-a", "venue-a")
    after = before.bind("oms-b", "venue-b")

    assert before.broker_id_for("oms-b") is None
    assert after.broker_id_for("oms-b") == "venue-b"
    assert after.oms_id_for("venue-a") == "oms-a"
    assert isinstance(after.to_broker, PersistentMap)
    assert isinstance(after.to_oms, PersistentMap)


def test_the_reconciliation_history_is_append_only() -> None:
    log = _record(3)

    assert isinstance(log.duplicates, AppendOnlyLog)
    assert [decision.execution.execution_id for decision in log.duplicates] == [
        "E-0",
        "E-1",
        "E-2",
    ]
