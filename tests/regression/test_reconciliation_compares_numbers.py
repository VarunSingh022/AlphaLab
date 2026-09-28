"""Reconciliation compares amounts as numbers, and identifies them as numbers (BRK-001).

Until v3.10 ``reconcile_snapshot`` compared a fill's quantity, price and
commission as text, so a venue that reported ``100`` for a fill the mirror holds
as ``100.00`` produced an ``EXECUTION_MISMATCH`` for two equal amounts. And a
snapshot's identity rendered amounts with ``str``, so the same evidence reported
with other exponents was a different snapshot.
"""

import dataclasses
from decimal import Decimal

from alphalab.broker.reconciliation import (
    VENUE_SNAPSHOT_SCHEME,
    SnapshotDivergenceKind,
    VenueSnapshot,
    reconcile_snapshot,
)
from tests.unit.broker.test_snapshot_reconciliation import _snapshot, _state


def _restated(value: Decimal) -> Decimal:
    """The same number with two more decimal places: ``100`` -> ``100.00``."""

    return value.quantize(Decimal(1).scaleb(value.as_tuple().exponent - 2))  # type: ignore[operator]


def _restate_executions(snapshot: VenueSnapshot) -> VenueSnapshot:
    assert snapshot.executions is not None
    return dataclasses.replace(
        snapshot,
        executions=tuple(
            dataclasses.replace(
                execution,
                fill_quantity=_restated(execution.fill_quantity),
                fill_price=_restated(execution.fill_price),
                commission=_restated(execution.commission),
            )
            for execution in snapshot.executions
        ),
    )


def test_a_fill_reported_with_other_exponents_is_not_a_mismatch() -> None:
    original, restated = _snapshot(), _restate_executions(_snapshot())
    assert original.executions is not None and restated.executions is not None
    # Equal numbers, written differently.
    assert restated.executions[0].fill_price == original.executions[0].fill_price
    assert str(restated.executions[0].fill_price) != str(original.executions[0].fill_price)

    result = reconcile_snapshot(_state(), restated, evaluated_at=100.0, max_age_seconds=60.0)

    assert result.of(SnapshotDivergenceKind.EXECUTION_MISMATCH) == ()
    assert result.reconciled


def test_a_fill_that_really_differs_is_still_a_mismatch() -> None:
    snapshot = _snapshot()
    assert snapshot.executions is not None
    first, *rest = snapshot.executions
    moved = dataclasses.replace(first, fill_price=first.fill_price + Decimal("0.01"))

    result = reconcile_snapshot(
        _state(),
        dataclasses.replace(snapshot, executions=(moved, *rest)),
        evaluated_at=100.0,
        max_age_seconds=60.0,
    )

    assert [d.key for d in result.of(SnapshotDivergenceKind.EXECUTION_MISMATCH)] == [
        first.execution_id
    ]


def test_equal_evidence_has_one_identity_whatever_its_exponents() -> None:
    assert _restate_executions(_snapshot()).snapshot_id == _snapshot().snapshot_id
    assert VENUE_SNAPSHOT_SCHEME.endswith(".v2")
