"""S-07 with a boosting adapter: scikit-learn's model searched, filed and traded (plan S-07).

The core's ``examples/81_search_train_and_trade_a_model.py`` runs the scenario
with the core's reference ensemble. Here the same example, loaded afresh, trains
scikit-learn's ``GradientBoostingClassifier`` through
:class:`~alphalab_models.sklearn.SklearnModel` instead, and the plan's
acceptance holds for an adapter's model:

* **Every configuration the search evaluated is a counted trial**: 33 trials
  over 6 configurations, every one completed, the lockbox read once.
* **The adapter's model trades and restores identically**: the traded model is
  restored from the store byte for byte, and a rerun reproduces the run.
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
from alphalab_models.sklearn import SklearnModel
from sklearn.ensemble import GradientBoostingClassifier

from alphalab.lifecycle import RerunOutcome, assess_reproducibility, digest_run
from alphalab.lifecycle.designs import ExperimentError
from alphalab.lifecycle.experiments import TrialLedger, TrialOutcome, read_lockbox
from alphalab.model_registry.registry import ArtifactRef

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "81_search_train_and_trade_a_model.py"


def _boosted(search: Any) -> SklearnModel:
    return SklearnModel(
        GradientBoostingClassifier(
            n_estimators=search.trees,
            learning_rate=search.learning_rate,
            max_depth=search.depth,
            min_samples_leaf=8,
            subsample=0.8,
        )
    )


@pytest.fixture(scope="module")
def example() -> ModuleType:
    """Example 81, its model scikit-learn's through the adapter."""

    spec = importlib.util.spec_from_file_location("example_81_sklearn", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    vars(module).update(MODEL=SklearnModel, model_of=_boosted)
    return module


@pytest.fixture(scope="module")
def searched(example: ModuleType) -> tuple[TrialLedger, Any]:
    ledger = example.ledger()
    return ledger, example.search(ledger)


def test_every_configuration_is_a_counted_trial_and_the_lockbox_is_read_once(
    example: ModuleType, searched: tuple[TrialLedger, Any]
) -> None:
    ledger, record = searched
    assert record.configurations == 6
    assert [len(each.scores) for each in record.rounds] == [6, 3, 2]
    assert record.counts.recorded == record.counts.completed == 33
    for each in record.rounds:
        for trial in each.spec.trials():
            held = ledger.find(trial.trial_id)
            assert held is not None and held.outcome is TrialOutcome.COMPLETED
    last = record.rounds[-1].spec
    chosen = next(t for t in last.trials() if t.fold == 0 and t.candidate == record.selected)
    read = read_lockbox(last, example.RUNNERS, "s07", ledger, chosen)
    assert read.record.outcome is TrialOutcome.COMPLETED
    with pytest.raises(ExperimentError, match="has been read"):
        read_lockbox(last, example.RUNNERS, "s07", ledger, chosen)


def test_the_adapters_model_trades_by_its_artifact_and_restores_identically(
    example: ModuleType, searched: tuple[TrialLedger, Any]
) -> None:
    _, record = searched
    filed = example.final(record)
    assert filed.model_kind == "sklearn:GradientBoostingClassifier"
    result = example.trade(filed.artifact_id)
    restored = example.instance_of(result).model
    assert isinstance(restored, SklearnModel)
    assert restored.capture() == example.STORE.get(ArtifactRef(uri=filed.artifact_id))
    assert result.orders, "the adapter's model trades"
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
