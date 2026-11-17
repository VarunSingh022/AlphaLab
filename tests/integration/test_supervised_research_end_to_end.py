"""S-07: supervised research end to end, with the core's reference model (v4.3, plan S-07).

``examples/81_search_train_and_trade_a_model.py`` searches, trains and trades a
meta-model of a momentum rule's side. These tests hold it to the plan's S-07:

* **The panel is point-in-time**: moving every close from the lockbox on
  changes nothing the panel known before the lockbox holds -- no feature,
  label, end, weight or training set.
* **Every configuration the search evaluated is a counted trial**: 33 trials
  over 6 configurations in three rounds of purged cross-validation over
  backtests, each round a child experiment of the one before.
* **Every fold trains on rows purged of its scored window and the lockbox**: no
  training row's label overlaps the window the trial is scored on, or is
  realized in the lockbox -- the leak this test found in the example's first
  runner, which fitted labels reaching into the held-back data.
* **The lockbox is held back from every trial and read once**; a second read
  is refused.
* **The model trades by its artifact**: restored from the store byte for byte,
  no order before the lockbox, and a rerun reproduces the run.
* **Another artifact is another run's inputs**: ``INPUTS_DIFFER``, where the
  same artifact's rerun is ``REPRODUCED``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.lifecycle import RerunOutcome, assess_reproducibility, digest_run
from alphalab.lifecycle.designs import ExperimentError
from alphalab.lifecycle.experiments import TrialLedger, TrialOutcome, read_lockbox
from alphalab.ml import SampleWeights
from alphalab.model_registry.registry import ArtifactRef

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "81_search_train_and_trade_a_model.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_81", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def searched(example: ModuleType) -> tuple[TrialLedger, Any]:
    ledger = example.ledger()
    return ledger, example.search(ledger)


def test_the_panel_known_before_the_lockbox_ignores_every_close_in_it(example: ModuleType) -> None:
    moved = example.research_panel(example.dataset(moved_after=example.LOCKBOX_SESSION))
    assert moved.labels != example.PANEL.labels, "the move reaches the labels it can"
    known = example.fit_rows(None)
    again = moved.known_by(example.KNOWN_BY)
    assert len(known) > 150
    assert (known.features, known.subjects, known.starts) == (
        again.features,
        again.subjects,
        again.starts,
    )
    assert (known.ends, known.labels, known.outcomes) == (again.ends, again.labels, again.outcomes)
    assert known.uniqueness() == again.uniqueness()
    weighed = SampleWeights.UNIQUENESS
    assert (
        known.training_set(weights=weighed).training_id
        == again.training_set(weights=weighed).training_id
    )


def test_every_configuration_the_search_evaluated_is_a_counted_trial(
    searched: tuple[TrialLedger, Any],
) -> None:
    ledger, record = searched
    assert record.configurations == 6
    assert [len(each.scores) for each in record.rounds] == [6, 3, 2]
    assert [each.budget for each in record.rounds] == [10, 30, 60]
    assert [each.record.counts.recorded for each in record.rounds] == [18, 9, 6]
    assert record.counts.recorded == record.counts.completed == 33
    assert record.rounds[0].parent_id is None
    for earlier, later in zip(record.rounds, record.rounds[1:], strict=False):
        assert later.parent_id == earlier.experiment_id
    for each in record.rounds:
        for trial in each.spec.trials():
            held = ledger.find(trial.trial_id)
            assert held is not None and held.outcome is TrialOutcome.COMPLETED
    assert record.selected is not None and record.selected["trees"] == 60


def test_every_fold_trains_on_rows_purged_of_its_scored_window(
    example: ModuleType, searched: tuple[TrialLedger, Any]
) -> None:
    _, record = searched
    trials = record.rounds[0].spec.trials()
    assert {trial.fold for trial in trials} == {0, 1, 2}
    for trial in trials:
        window = trial.window
        assert window is not None
        rows = example.fit_rows(window)
        assert len(rows) > 50
        for start, end in zip(rows.starts, rows.ends, strict=True):
            assert end < window.evaluate[0] or start > window.evaluate[-1], (start, end)
        assert all(end < example.LOCKBOX_START for end in rows.ends), "the lockbox is held back"


def test_the_lockbox_is_read_once(example: ModuleType, searched: tuple[TrialLedger, Any]) -> None:
    ledger, record = searched
    last = record.rounds[-1].spec
    chosen = next(t for t in last.trials() if t.fold == 0 and t.candidate == record.selected)
    read = read_lockbox(last, example.RUNNERS, "s07", ledger, chosen)
    assert read.record.outcome is TrialOutcome.COMPLETED
    with pytest.raises(ExperimentError, match="has been read"):
        read_lockbox(last, example.RUNNERS, "s07", ledger, chosen)


def test_the_model_trades_by_its_artifact_and_a_rerun_reproduces_it(
    example: ModuleType, searched: tuple[TrialLedger, Any]
) -> None:
    _, record = searched
    filed = example.final(record)
    known = example.fit_rows(None)
    assert filed.training_id == known.training_set(weights=SampleWeights.UNIQUENESS).training_id
    result = example.trade(filed.artifact_id)
    restored = example.instance_of(result).model
    assert restored.capture() == example.STORE.get(ArtifactRef(uri=filed.artifact_id))
    assert result.orders, "the model trades"
    assert min(order.created_at for order in result.orders) >= example.LOCKBOX_START
    assert digest_run(example.trade(filed.artifact_id)) == digest_run(result)


def test_another_artifact_is_another_runs_inputs(
    example: ModuleType, searched: tuple[TrialLedger, Any]
) -> None:
    _, record = searched
    filed = example.final(record)
    other = example.final(record, seed=example.SEED + 1)
    assert other.artifact_id != filed.artifact_id, "the subsample follows the seed"
    result = example.trade(filed.artifact_id)
    original = example.manifest(filed.artifact_id, result)
    differ = assess_reproducibility(
        original, example.manifest(other.artifact_id, example.trade(other.artifact_id))
    )
    assert differ.rerun is RerunOutcome.INPUTS_DIFFER
    same = assess_reproducibility(
        original, example.manifest(filed.artifact_id, example.trade(filed.artifact_id))
    )
    assert same.rerun is RerunOutcome.REPRODUCED
