"""The reference experiment's identities are pinned, as every host computes them (plan O.3).

ADR-0072 section 3: every value an experiment's identities cover is exact or
correctly rounded (ADR-0058), and a trial's diagnostics enter none, so the same
experiment has the same ``experiment_record_id`` on every host. Each value below
was computed identically under CPython 3.12, 3.13 and 3.14 before it was pinned,
and hosted CI holds it on Linux and macOS. A change here is a change to the
ledger's format or to a run's, and needs its own record.
"""

from __future__ import annotations

from alphalab.lifecycle.experiments import LedgerCount
from tests.integration.experiment_workload import run


def test_the_reference_experiment_is_pinned() -> None:
    spec, record = run()

    assert record.counts == LedgerCount(
        completed=6, insolvent=1, strategy_failed=1, refused=1, failed=1
    )
    assert spec.experiment_id == "1d85bac7b717e2efc537f30b285ff74fe3c0d2e3fd737114ac2f96de89bb0d22"
    assert record.matrix_id == "3d8b86b288d0e72f3c4bfa40b5ddfee1b465d0458a18095e65cda178d6c98202"
    assert record.experiment_record_id == (
        "3cae8798dcf485d9a6898daa01c55d637893f6764fc7eff188e362941daeb082"
    )
