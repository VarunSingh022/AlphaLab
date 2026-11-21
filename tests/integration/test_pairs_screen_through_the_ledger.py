"""S-02's screening stage through the ledger: 200 pairs, each a counted trial (ADR-0074 s. 8).

* **The screen** tests every candidate over the 45 days before the lockbox and
  corrects the family by Holm's procedure: the five planted pairs are
  selected, no independent pair is, and the family counts all 200.
* **The ledger** runs every candidate as a trial in spawned workers. Each
  trial's runner fits its pair on the days before the lockbox -- its own window
  -- and trades the spread. Every one of the 200 is recorded, so a selected
  pair's evidence carries the search behind it: its trial completed, and the
  experiment counts the 199 others.
* **One fit, two readers**: a trial's hedge ratio is the screen's for its pair,
  bit for bit, since both fit the same days the same way.
"""

from __future__ import annotations

from alphalab.econometrics import Deterministic
from alphalab.lifecycle.experiments import SpawnExecutor, TrialLedger, TrialOutcome, run_experiment
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.cointegration_screen import cointegration_screen
from alphalab.research.multiple_testing import Correction
from tests.integration.pairs_screen_workload import (
    CANDIDATES,
    CLOSES,
    CONDITION,
    FITTED,
    FITTED_RATIOS,
    LAGS,
    PLANTED,
    REGISTRY,
    SPEC,
    Pair,
    build,
)

WHERE = "tests.integration.pairs_screen_workload:DEFINITION"
PLANTED_PAIRS = {Pair(f"P{i:02d}", f"W{i:02d}") for i in range(PLANTED)}


def test_two_hundred_pairs_are_screened_and_every_one_is_a_counted_trial() -> None:
    screen = cointegration_screen(
        CLOSES,
        [(p.dependent, p.regressor) for p in CANDIDATES],
        start=0,
        stop=FITTED,
        deterministic=Deterministic.CONSTANT,
        lags=LAGS,
        maximum_condition=CONDITION,
        correction=Correction.HOLM,
        alpha=0.05,
    )
    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    record = run_experiment(
        SPEC, REGISTRY, "pairs", ledger, SpawnExecutor(WHERE, workers=4, trials_per_worker=None)
    )

    assert len(CANDIDATES) == screen.family.trials == record.counts.recorded == 200
    selected = {Pair(p.dependent, p.regressor) for p in screen.selected}
    assert selected == PLANTED_PAIRS
    trials = {Pair(t.configuration.dependent, t.configuration.regressor): t for t in SPEC.trials()}
    for pair in selected:
        kept = ledger.find(trials[pair].trial_id)
        assert kept is not None and kept.outcome is TrialOutcome.COMPLETED


def test_each_trial_fits_its_pair_on_its_own_window_as_the_screen_does() -> None:
    screen = cointegration_screen(
        CLOSES,
        [(p.dependent, p.regressor) for p in CANDIDATES],
        start=0,
        stop=FITTED,
        deterministic=Deterministic.CONSTANT,
        lags=LAGS,
        maximum_condition=CONDITION,
        correction=Correction.HOLM,
        alpha=0.05,
    )
    for trial in SPEC.trials()[:20]:
        build(trial)
    for pair in screen.pairs[:20]:
        assert FITTED_RATIOS[Pair(pair.dependent, pair.regressor)] == pair.hedge_ratio
