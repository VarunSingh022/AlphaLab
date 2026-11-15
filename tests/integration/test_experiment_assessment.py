"""The statistics over a ledgered experiment (v4.3, ADR-0073 section 7, WS-06).

The reference experiment, assessed under each design: the family is read from
the ledger, and PBO, the deflated Sharpe ratio, the Reality Check and SPA are
research's own over it -- equal, float for float, to calling them by hand.

* A full-sample family is the completed trials, and its matrix is the record's.
* A walk-forward family joins each candidate's validation windows; a
  candidate that never trades in them has no dispersion, so the statistics
  that divide by one leave it out, and the assessment names it.
* A CPCV family is one named path of the candidates' histories.
* The assessment's identity states the record, every choice and the exact
  results; it is pinned, and CI holds the pin on CPython 3.12, 3.13 and 3.14.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable
from dataclasses import replace

import pytest

from alphalab.lifecycle.assessment import (
    EffectiveTrials,
    ExperimentAssessment,
    assess_experiment,
    trial_matrix_of,
)
from alphalab.lifecycle.designs import (
    SELECT,
    VALIDATE,
    CrossValidated,
    ExperimentError,
    PurgeSpec,
    WalkForward,
)
from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    ExperimentSpec,
    RunnerRegistry,
    SequentialExecutor,
    TrialInputs,
    TrialLedger,
    TrialOutcome,
    TrialSpec,
    run_experiment,
    run_trial,
)
from alphalab.lifecycle.selection import assess_captures
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex
from alphalab.research.data_snooping import (
    RealityCheckResult,
    SPAResult,
    reality_check,
    superior_predictive_ability,
)
from alphalab.research.selection import (
    SelectionMeasure,
    TrialMatrix,
    Trim,
    probability_of_backtest_overfitting,
)
from alphalab.research.sharpe_inference import deflated_sharpe_ratio, per_period_sharpe
from alphalab.research.time_series_cv import CVMethod
from tests.integration.experiment_workload import (
    CPCV_DEFINITION,
    REGISTRY,
    build,
    spec,
    walk_forward_spec,
)

BEST = "mode='normal',size=2,window=2"
SQUEEZE = "mode='squeeze',size=1,window=7"
FIRST = "mode='normal',size=1,window=2"

#: The assessments' identities, pinned. ``alphalab.experiment_assessment.v1``
#: over exact quantities only, so CPython 3.12, 3.13 and 3.14 agree on them.
FULL_SAMPLE_ID = "e5fb89d24a42e536f2651cfdce50ca0b26fc80db062253446833157841ebfa88"
WALK_FORWARD_ID = "0dfb29311b3cf948e1b3bc120eab70be955b8ffaa58d5f1750925ca3132cfbde"
CPCV_PATH_ONE_ID = "b0001996b91411c7c11756e27563de4191f8c715328c75619842adc3161eb27e"


def _ledger() -> TrialLedger:
    return TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))


@functools.cache
def _ran(design: str) -> tuple[ExperimentSpec, TrialLedger, ExperimentRecord]:
    makers: dict[str, Callable[[], ExperimentSpec]] = {
        "full": spec,
        "walk-forward": walk_forward_spec,
        "cross-validated": lambda: replace(
            spec(),
            validation=CrossValidated(
                3, CVMethod.PURGED, 4, SelectionMeasure.MEAN, PurgeSpec(1, 0.0), None
            ),
        ),
        "cpcv": lambda: CPCV_DEFINITION.spec,
    }
    experiment = makers[design]()
    ledger = _ledger()
    return (
        experiment,
        ledger,
        run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor()),
    )


def _assess(
    design: str,
    *,
    path: int | None = None,
    benchmark: str = "cash",
    blocks: int = 6,
    trim: Trim = Trim.NONE,
    replications: int = 200,
    seed: int = 7,
    effective: EffectiveTrials | None = None,
) -> ExperimentAssessment:
    experiment, ledger, record = _ran(design)
    matrix = trial_matrix_of(experiment, ledger, record, path=path)
    held = [0.0] * matrix.periods if benchmark == "cash" else list(matrix.column(benchmark))
    return assess_experiment(
        experiment,
        ledger,
        record,
        path=path,
        blocks=blocks,
        measure=SelectionMeasure.MEAN,
        trim=trim,
        benchmark=held,
        replications=replications,
        mean_block_length=5.0,
        seed=seed,
        effective_trials=effective,
    )


def _rc(relative: TrialMatrix) -> RealityCheckResult:
    return reality_check(relative, replications=200, mean_block_length=5.0, seed=7)


def _spa(relative: TrialMatrix) -> SPAResult:
    return superior_predictive_ability(relative, replications=200, mean_block_length=5.0, seed=7)


def _without(matrix: TrialMatrix, label: str) -> TrialMatrix:
    kept = [k for k, each in enumerate(matrix.labels) if each != label]
    return TrialMatrix(
        labels=tuple(matrix.labels[k] for k in kept),
        instants=matrix.instants,
        columns=tuple(matrix.columns[k] for k in kept),
        measure=matrix.measure,
    )


def test_a_full_sample_family_is_its_completed_trials_and_the_records_matrix() -> None:
    experiment, ledger, record = _ran("full")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)

    completed = [
        trial.label
        for trial in experiment.trials()
        if (held := ledger.find(trial.trial_id)) is not None
        and held.outcome is TrialOutcome.COMPLETED
    ]
    assert matrix.labels == tuple(completed) and matrix.trials == 6
    assert matrix.periods == 60
    assessment = _assess("full")
    assert record.matrix_id is not None
    assert assessment.matrix_id == record.matrix_id, "ADR-0072's matrix, read back"


def test_the_statistics_are_researchs_own_over_the_family() -> None:
    experiment, ledger, record = _ran("full")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)
    assessment = _assess("full")

    pbo = probability_of_backtest_overfitting(
        matrix, blocks=6, measure=SelectionMeasure.MEAN, trim=Trim.NONE
    )
    assert assessment.overfitting == pbo
    captures = [
        ran.capture
        for trial in experiment.trials()
        if trial.label in matrix.labels
        and (ran := run_trial(experiment, build, trial)).capture is not None
    ]
    assert (
        assess_captures(
            captures, labels=matrix.labels, blocks=6, measure=SelectionMeasure.MEAN, trim=Trim.NONE
        ).overfitting
        == pbo
    ), "M3's bridge over the same trials, run again"
    best = matrix.labels.index(BEST)
    assert assessment.selected == BEST
    assert assessment.deflated == deflated_sharpe_ratio(
        matrix.columns[best],
        [per_period_sharpe(column) for column in matrix.columns],
        independent_trials=6,
    )
    assert assessment.reality_check == _rc(matrix)
    assert assessment.superior == _spa(matrix)
    assert assessment.flat == assessment.constant == ()
    assert assessment.family == record.counts.recorded == 10, "failures are counted"


def test_a_walk_forward_family_joins_each_candidates_validation_windows() -> None:
    experiment, ledger, record = _ran("walk-forward")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)

    assert matrix.instants == tuple(float(t) for t in range(22, 52))
    assert set(matrix.labels) == {
        *(f"mode='normal',size={size},window={window}" for window in (2, 3, 5) for size in (1, 2)),
        SQUEEZE,
    }, "a candidate that failed in any fold has no history over the sample"
    for label, column in zip(matrix.labels, matrix.columns, strict=True):
        joined: list[float] = []
        for trial in experiment.trials():
            if trial.label == label:
                assert trial.phase == SELECT
                held = ledger.find(trial.trial_id)
                assert held is not None and held.series_id is not None
                joined.extend(ledger.series(held.series_id)[1])
        assert list(column) == joined
    assert _assess("walk-forward").family == record.counts.recorded == 33


def test_a_cross_validated_family_joins_each_candidates_folds() -> None:
    """The first fold takes its four-instant warm-up from itself; the folds then tile."""

    experiment, ledger, record = _ran("cross-validated")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)

    assert matrix.trials == 7 and matrix.instants[0] == 6.0 and matrix.instants[-1] == 61.0
    for label, column in zip(matrix.labels, matrix.columns, strict=True):
        joined: list[float] = []
        for trial in experiment.trials():
            if trial.label == label:
                assert trial.phase == VALIDATE
                held = ledger.find(trial.trial_id)
                assert held is not None and held.series_id is not None
                joined.extend(ledger.series(held.series_id)[1])
        assert list(column) == joined


@pytest.mark.parametrize("step", [12, 5], ids=["gaps", "overlaps"])
def test_windows_that_do_not_tile_the_sample_have_no_family(step: int) -> None:
    experiment = walk_forward_spec()
    design = experiment.validation
    assert isinstance(design, WalkForward)
    experiment = replace(experiment, validation=replace(design, step=step))
    ledger = _ledger()
    record = run_experiment(experiment, REGISTRY, "reference", ledger, SequentialExecutor())

    with pytest.raises(ExperimentError, match="do not tile the sample"):
        trial_matrix_of(experiment, ledger, record, path=None)


def test_a_candidate_that_never_trades_is_left_out_of_what_divides_by_its_dispersion() -> None:
    """The squeeze candidate's one trade precedes every window: its returns are all zero."""

    experiment, ledger, record = _ran("walk-forward")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)
    assert set(matrix.column(SQUEEZE)) == {0.0}
    assessment = _assess("walk-forward")

    assert assessment.flat == (SQUEEZE,), "no Sharpe ratio, so no trial Sharpe ratio"
    assert assessment.constant == (SQUEEZE,), "against cash, its relative performance is 0"
    rest = _without(matrix, SQUEEZE)
    assert assessment.deflated == deflated_sharpe_ratio(
        matrix.column(BEST),
        [per_period_sharpe(column) for column in rest.columns],
        independent_trials=6,
    )
    assert assessment.superior == _spa(rest)
    assert assessment.reality_check == _rc(matrix), "the whole family"


def test_a_benchmark_that_is_a_candidates_own_leaves_that_candidate_out_of_spa() -> None:
    experiment, ledger, record = _ran("full")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)
    assessment = _assess("full", benchmark=FIRST)

    assert assessment.constant == (FIRST,) and assessment.flat == ()
    assert assessment.superior is not None
    relative = TrialMatrix(
        labels=matrix.labels,
        instants=matrix.instants,
        columns=tuple(
            tuple(v - b for v, b in zip(column, matrix.column(FIRST), strict=True))
            for column in matrix.columns
        ),
        measure="relative",
    )
    assert assessment.superior == _spa(_without(relative, FIRST))
    assert assessment.reality_check == _rc(relative)


def test_a_cpcv_family_is_the_path_the_caller_names() -> None:
    experiment, ledger, record = _ran("cpcv")
    with pytest.raises(ExperimentError, match="name it"):
        trial_matrix_of(experiment, ledger, record, path=None)
    with pytest.raises(ExperimentError, match="there is no path 3"):
        trial_matrix_of(experiment, ledger, record, path=3)

    paths = dict(record.paths)
    for path in range(3):
        matrix = trial_matrix_of(experiment, ledger, record, path=path)
        assert matrix.trials == 7 and matrix.periods == 57
        for label, column in zip(matrix.labels, matrix.columns, strict=True):
            series_id = paths[label][path]
            assert series_id is not None
            assert list(column) == list(ledger.series(series_id)[1])
    # The reference runner fits nothing, so its paths coincide; the path read
    # is still the assessment's to state.
    ids = {_assess("cpcv", path=p, trim=Trim.OLDEST).assessment_id for p in range(3)}
    assert len(ids) == 3


def test_a_family_is_its_own_records_and_at_least_two_candidates() -> None:
    experiment, ledger, record = _ran("full")
    _, _, other = _ran("walk-forward")
    with pytest.raises(ExperimentError, match="is of experiment"):
        trial_matrix_of(experiment, ledger, other, path=None)
    with pytest.raises(ExperimentError, match="Only a CPCV experiment has paths"):
        trial_matrix_of(experiment, ledger, record, path=0)

    def failing(trial: TrialSpec) -> TrialInputs:
        raise ValueError("every candidate fails")

    failed = walk_forward_spec()
    failed_ledger = _ledger()
    nothing = run_experiment(
        failed,
        RunnerRegistry().register("reference", failing),
        "reference",
        failed_ledger,
        SequentialExecutor(),
    )
    with pytest.raises(ExperimentError, match="at least two"):
        trial_matrix_of(failed, failed_ledger, nothing, path=None)


@pytest.mark.parametrize(
    "benchmark",
    [[0.0] * 59, [0.0] * 61, [0.0] * 59 + [math.nan], [math.inf] + [0.0] * 59],
)
def test_the_benchmark_is_one_finite_return_a_period(benchmark: list[float]) -> None:
    experiment, ledger, record = _ran("full")
    with pytest.raises(ExperimentError, match=r"one return a period|it is finite"):
        assess_experiment(
            experiment,
            ledger,
            record,
            path=None,
            blocks=6,
            measure=SelectionMeasure.MEAN,
            trim=Trim.NONE,
            benchmark=benchmark,
            replications=200,
            mean_block_length=5.0,
            seed=7,
            effective_trials=None,
        )


def test_a_smaller_n_is_taken_with_its_reason_and_enters_the_identity() -> None:
    """ADR-0073 section 7: an explicit smaller ``N``, recorded with its reason."""

    experiment, ledger, record = _ran("full")
    matrix = trial_matrix_of(experiment, ledger, record, path=None)
    stated = EffectiveTrials(3, "the six trials' returns cluster into three by correlation")
    assessment = _assess("full", effective=stated)

    assert assessment.effective_trials == stated
    assert assessment.deflated == deflated_sharpe_ratio(
        matrix.column(BEST),
        [per_period_sharpe(column) for column in matrix.columns],
        independent_trials=3,
    )
    assert assessment.assessment_id != FULL_SAMPLE_ID
    reasoned = _assess("full", effective=EffectiveTrials(3, "another reason")).assessment_id
    assert reasoned != assessment.assessment_id, "the reason is part of what was stated"
    with pytest.raises(ExperimentError, match="exceeds the 6 members with a Sharpe ratio"):
        _assess("full", effective=EffectiveTrials(7, "more than were run"))


@pytest.mark.parametrize(
    ("count", "reason", "match"),
    [
        (1, "one trial", "at least 2"),
        (True, "a boolean", "at least 2"),
        (2.0, "a float", "at least 2"),
        (3, "  ", "must be stated"),
    ],
)
def test_a_stated_n_is_a_count_of_at_least_two_with_a_reason(
    count: object, reason: str, match: str
) -> None:
    with pytest.raises(ExperimentError, match=match):
        EffectiveTrials(count, reason)  # type: ignore[arg-type]


def test_the_assessments_identity_is_pinned_and_states_every_choice() -> None:
    full = _assess("full")
    walk_forward = _assess("walk-forward")
    cpcv = _assess("cpcv", path=1, benchmark=FIRST, trim=Trim.OLDEST)

    assert full.assessment_id == FULL_SAMPLE_ID
    assert walk_forward.assessment_id == WALK_FORWARD_ID
    assert cpcv.assessment_id == CPCV_PATH_ONE_ID
    assert str(walk_forward.overfitting.probability) == "3/10"
    assert str(walk_forward.reality_check.p_value) == "69/200"
    assert walk_forward.superior is not None
    assert [
        str(walk_forward.superior.p_lower),
        str(walk_forward.superior.p_consistent),
        str(walk_forward.superior.p_upper),
    ] == ["23/100", "59/200", "21/50"]
    assert (str(cpcv.overfitting.probability), str(cpcv.reality_check.p_value)) == (
        "7/20",
        "167/200",
    )
    changed = {
        _assess("full", seed=8).assessment_id,
        _assess("full", blocks=4).assessment_id,
        _assess("full", replications=100).assessment_id,
        _assess("full", benchmark=FIRST).assessment_id,
    }
    assert FULL_SAMPLE_ID not in changed and len(changed) == 4


def _fitting(trial: TrialSpec) -> TrialInputs:
    """A runner whose position depends on what it was fitted on: CPCV's splits then differ."""

    held = trial.window
    plan = trial.configuration
    size = plan.size + (0 if held is None else len(held.fit) % 3)
    return build(replace(trial, configuration=replace(plan, size=size)))


def test_a_cpcv_family_reads_the_named_path_when_paths_differ() -> None:
    experiment = CPCV_DEFINITION.spec
    ledger = _ledger()
    record = run_experiment(
        experiment,
        RunnerRegistry().register("reference", _fitting),
        "reference",
        ledger,
        SequentialExecutor(),
    )
    paths = dict(record.paths)
    assert len(set(paths[BEST])) == 3, "each path is another history"

    for path in range(3):
        matrix = trial_matrix_of(experiment, ledger, record, path=path)
        for label, column in zip(matrix.labels, matrix.columns, strict=True):
            series_id = paths[label][path]
            assert series_id is not None
            assert list(column) == list(ledger.series(series_id)[1])
