"""Walk-forward optimization: choose on the past, report on the future, fold by fold (FEA-003).

:func:`~alphalab.research.walk_forward.walk_forward_splits` cuts a series into
folds of train, validation and test, and
:func:`~alphalab.research.overfitting.parameter_sweep` scores every
configuration of a search. Composing the two by hand is where parameters leak:
a loop that scores candidates over "the data up to now" includes the test
window one off-by-one away, and nothing downstream can tell. This module is
the composition, stated once:

* for each fold, every candidate of a :class:`ParameterSpace` is scored by the
  caller's objective **fitted on the fold's training instants and evaluated on
  its validation instants** -- the objective is never handed a test instant
  while a selection is being made;
* the best candidate is **selected** by that validation score (ties go to the
  first in the space's order, so a selection never depends on anything but
  the scores and the stated order);
* the selection alone is then **reported** on the fold's test instants, fitted
  on what :class:`Refit` says -- the training instants, or training and
  validation.

The whole search is counted: every fold's full validation surface is kept
(:class:`~alphalab.research.overfitting.SweepResult`), and the trial count
every multiple-testing correction needs is folds times candidates.

What the harness can and cannot guarantee
-----------------------------------------

The objective is called with the instants it may use and a seed; it reads its
data by those instants. The harness guarantees what it hands over -- no
selection call receives an instant of that fold's test window or anything
after it, which the test suite checks by brute force -- and cannot stop an
objective that closes over the whole series from reading outside the instants
it was given. That is the caller's contract, stated here rather than implied.

Identity
--------

The result is a :class:`~alphalab.research.study.StudyResult` of the caller's
:class:`~alphalab.research.study.ResearchStudy`, and the study must describe
this experiment: its ``splits`` must be the report's scheme, its seed is the
one the objective is seeded from (required), and it must name the design --
the space and the refit rule -- as its ``"walk_forward"`` input
(:attr:`WalkForwardDesign.design_id`). A result whose study did not state what
was searched would identify a different experiment from the one run. Each
fold's selection enters the result's metrics as the candidate's position in
the space, so two runs that selected differently never share a result id.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from alphalab.common.statistics import mean
from alphalab.common.types import ParamValue
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.overfitting import SweepResult, parameter_sweep, sample_degradation
from alphalab.research.splits import SplitReport, TimeSplit
from alphalab.research.study import ResearchStudy, StudyResult, build_result

__all__ = [
    "WALK_FORWARD_DESIGN_SCHEME",
    "FoldSelection",
    "ParameterSpace",
    "Refit",
    "WalkForwardDesign",
    "WalkForwardObjective",
    "WalkForwardOptimization",
    "walk_forward_optimize",
]

WALK_FORWARD_DESIGN_SCHEME: Final = "alphalab.walk_forward_design.v1"

#: ``objective(parameters, fit, evaluate, seed) -> score``: fit on the ``fit``
#: instants, score on the ``evaluate`` instants, higher is better. ``seed`` is
#: derived from the study's seed, the fold, the phase and the candidate, so a
#: stochastic objective is reproducible whatever order it is called in.
type WalkForwardObjective = Callable[
    [Mapping[str, ParamValue], tuple[float, ...], tuple[float, ...], int], float
]


class Refit(Enum):
    """What the selected candidate is fitted on before it is scored on the test window."""

    #: The fold's training instants: the model reported is the model selected.
    TRAIN = "train"
    #: Training and validation instants: the selection is refitted on all the
    #: history the fold has before its test window. The validation instants
    #: were purged against the test span, so neither set's labels reach it.
    TRAIN_AND_VALIDATION = "train_and_validation"


def _value(name: str, value: ParamValue) -> ParamValue:
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResearchValidationError(f"Parameter {name!r} is {value!r}; a value is finite.")
        return value
    raise ResearchValidationError(
        f"Parameter {name!r} is {value!r}; a value is a bool, int, float or str."
    )


def _render(candidate: Mapping[str, ParamValue]) -> str:
    return ",".join(f"{name}={candidate[name]!r}" for name in sorted(candidate))


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    """The candidates a search considers, in the order that makes them neighbours.

    Build one with :meth:`grid` or :meth:`of`. Every candidate names the same
    parameters; none repeats. The order is part of the space: it is the order
    ties are broken in and neighbours are read in
    (:attr:`~alphalab.research.overfitting.SweepResult.neighbour_drop`).

    Raises:
        ResearchValidationError: If there is no candidate, candidates name
            different parameters, one repeats, a name is blank or a value is
            not a finite bool, int, float or str.
    """

    candidates: tuple[Mapping[str, ParamValue], ...]

    def __post_init__(self) -> None:
        if not self.candidates:
            raise ResearchValidationError("A parameter space needs at least one candidate.")
        names = sorted(self.candidates[0])
        frozen: list[Mapping[str, ParamValue]] = []
        for candidate in self.candidates:
            if sorted(candidate) != names:
                raise ResearchValidationError(
                    f"Every candidate names the same parameters: {names} and "
                    f"{sorted(candidate)} differ."
                )
            for name in names:
                if not isinstance(name, str) or not name.strip() or name != name.strip():
                    raise ResearchValidationError(f"Parameter name {name!r} is not a name.")
            frozen.append(MappingProxyType({name: _value(name, candidate[name]) for name in names}))
        rendered = [_render(candidate) for candidate in frozen]
        if len(set(rendered)) != len(rendered):
            raise ResearchValidationError(
                "The space repeats a candidate, so its trial count would overstate the search."
            )
        object.__setattr__(self, "candidates", tuple(frozen))

    @classmethod
    def grid(cls, axes: Mapping[str, Sequence[ParamValue]]) -> ParameterSpace:
        """Every combination of the axes' values: axes by name, each in its given order."""

        if not axes:
            raise ResearchValidationError("A grid needs at least one axis.")
        names = sorted(axes)
        for name in names:
            if not axes[name]:
                raise ResearchValidationError(f"Axis {name!r} has no value.")
        combinations: list[dict[str, ParamValue]] = [{}]
        for name in names:
            combinations = [
                {**known, name: value} for known in combinations for value in axes[name]
            ]
        return cls(tuple(combinations))

    @classmethod
    def of(cls, candidates: Sequence[Mapping[str, ParamValue]]) -> ParameterSpace:
        """An explicit list of candidates, in the order given."""

        return cls(tuple(candidates))

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def rendered(self) -> tuple[str, ...]:
        """Each candidate as ``name=value,...``, names sorted, values by ``repr``."""

        return tuple(_render(candidate) for candidate in self.candidates)

    @property
    def space_id(self) -> str:
        """The derived identity of these candidates in this order."""

        return hashlib.sha256("\n".join(self.rendered).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WalkForwardDesign:
    """What a walk-forward optimization searches, and how it refits.

    Attributes:
        space: The candidates.
        refit: What the selection is fitted on before the test window. No
            default: whether a model is refitted on its validation data before
            it is reported is a methodological choice, and results under the
            two differ.
    """

    space: ParameterSpace
    refit: Refit

    def __post_init__(self) -> None:
        if not isinstance(self.space, ParameterSpace):
            raise ResearchValidationError(f"space must be a ParameterSpace, got {self.space!r}.")
        if not isinstance(self.refit, Refit):
            raise ResearchValidationError(f"refit must be a Refit, got {self.refit!r}.")

    @property
    def design_id(self) -> str:
        """The identity a study names under its ``"walk_forward"`` input."""

        lines = [
            WALK_FORWARD_DESIGN_SCHEME,
            f"space={self.space.space_id}",
            f"refit={self.refit.value}",
        ]
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FoldSelection:
    """One fold: every candidate's validation score, the choice, and its test score.

    Attributes:
        fold: The fold's index.
        selected: The chosen candidate, rendered.
        parameters: The chosen candidate.
        position: Its position in the space.
        surface: Every candidate's validation score -- the whole search, not
            the survivor.
        validation_score: The chosen candidate's validation score.
        test_score: Its score on the test window, fitted per :class:`Refit`.
        degradation: ``(validation - test) / |validation|``, or ``None`` when
            the validation score is zero.
    """

    fold: int
    selected: str
    parameters: Mapping[str, ParamValue]
    position: int
    surface: SweepResult
    validation_score: float
    test_score: float
    degradation: float | None


@dataclass(frozen=True, slots=True)
class WalkForwardOptimization:
    """The whole walk-forward optimization: per-fold evidence and the study result.

    Attributes:
        design: What was searched.
        folds: One :class:`FoldSelection` per fold, in fold order.
        result: The study result: the metrics below, the split report, and the
            identity. ``wfo.fold<i>.selected`` is the selected candidate's
            position in the space; ``.validation`` and ``.test`` its scores;
            ``wfo.test.mean`` and ``wfo.validation.mean`` the means over folds;
            ``wfo.degradation`` their degradation when defined;
            ``wfo.trials`` the evaluations made to select; ``wfo.folds``,
            ``wfo.candidates`` and ``wfo.distinct_selections`` the counts.
    """

    design: WalkForwardDesign
    folds: tuple[FoldSelection, ...]
    result: StudyResult

    @property
    def trials(self) -> int:
        """Configurations evaluated to make the selections: folds x candidates."""

        return len(self.folds) * len(self.design.space)

    @property
    def out_of_sample(self) -> tuple[float, ...]:
        """Each fold's test score, in fold order."""

        return tuple(fold.test_score for fold in self.folds)


def _seed(seed: int, fold: int, phase: str, candidate: str) -> int:
    digest = hashlib.sha256(f"{seed}\n{fold}\n{phase}\n{candidate}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _score(value: object, fold: int, phase: str, candidate: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ResearchValidationError(
            f"Fold {fold}, {phase} of {candidate}: the objective returned {value!r}, not a score."
        )
    score = float(value)
    if not math.isfinite(score):
        raise ResearchValidationError(
            f"Fold {fold}, {phase} of {candidate}: the objective returned {score!r}. A "
            "non-finite score cannot be ranked; make the objective say what it measured."
        )
    return score


def walk_forward_optimize(
    study: ResearchStudy,
    design: WalkForwardDesign,
    objective: WalkForwardObjective,
    splits: SplitReport,
    *,
    produced_at: float,
) -> WalkForwardOptimization:
    """Select on each fold's validation window, report on its test window. See the module.

    Args:
        study: The experiment. Its ``splits`` must be ``splits.scheme``, it
            must carry a seed, and its ``"walk_forward"`` input must be
            ``design.design_id``.
        design: The candidates and the refit rule.
        objective: Scores a candidate -- see :data:`WalkForwardObjective`.
        splits: The folds, each with a test window
            (:func:`~alphalab.research.walk_forward.walk_forward_splits`).
        produced_at: When the result was assembled; recorded, never hashed.

    Raises:
        ResearchValidationError: If the study does not describe this
            experiment, a fold has no test window, there is no fold, or the
            objective returns a score that is not a finite number.
    """

    if study.splits != splits.scheme:
        raise ResearchValidationError(
            f"The study's splits are {study.splits!r} and the folds are {splits.scheme!r}; a "
            "result must be measured on the split its study names."
        )
    seed = study.require_seed()
    named = study.inputs.get("walk_forward")
    if named != design.design_id:
        raise ResearchValidationError(
            f"The study names walk_forward={named!r}, not this design's {design.design_id!r}. "
            "Name the design as the study's 'walk_forward' input, so the study says what was "
            "searched."
        )
    if not splits.folds:
        raise ResearchValidationError("There is no fold to optimize over.")
    for fold in splits.folds:
        if not fold.test:
            raise ResearchValidationError(
                f"Fold {fold.index} has no test window. Walk-forward optimization reports "
                "each selection on data it was not selected on; a scheme without test "
                "windows would report on the data it selected by."
            )

    space = design.space
    rendered = space.rendered
    position = {name: index for index, name in enumerate(rendered)}
    selections: list[FoldSelection] = []
    for fold in splits.folds:
        selections.append(_fold(fold, design, objective, seed, rendered, position))

    metrics: dict[str, float] = {
        "wfo.folds": float(len(selections)),
        "wfo.candidates": float(len(space)),
        "wfo.trials": float(len(selections) * len(space)),
        "wfo.distinct_selections": float(len({s.selected for s in selections})),
    }
    for selection in selections:
        prefix = f"wfo.fold{selection.fold}"
        metrics[f"{prefix}.selected"] = float(selection.position)
        metrics[f"{prefix}.validation"] = selection.validation_score
        metrics[f"{prefix}.test"] = selection.test_score
    in_sample = mean([selection.validation_score for selection in selections])
    out_of_sample = mean([selection.test_score for selection in selections])
    metrics["wfo.validation.mean"] = in_sample
    metrics["wfo.test.mean"] = out_of_sample
    degradation = sample_degradation(in_sample, out_of_sample)
    findings: list[str] = []
    if degradation is None:
        findings.append(
            "The mean validation score is zero, so the degradation to the test windows is "
            "undefined and is not reported."
        )
    else:
        metrics["wfo.degradation"] = degradation
    result = build_result(study, metrics, produced_at, splits=splits, findings=tuple(findings))
    return WalkForwardOptimization(design, tuple(selections), result)


def _fold(
    fold: TimeSplit,
    design: WalkForwardDesign,
    objective: WalkForwardObjective,
    seed: int,
    rendered: tuple[str, ...],
    position: Mapping[str, int],
) -> FoldSelection:
    candidates = dict(zip(rendered, design.space.candidates, strict=True))

    def validation(candidate: str) -> float:
        return _score(
            objective(
                candidates[candidate],
                fold.train,
                fold.validation,
                _seed(seed, fold.index, "select", candidate),
            ),
            fold.index,
            "selection",
            candidate,
        )

    surface = parameter_sweep(f"fold {fold.index} validation", rendered, validation)
    selected = surface.best
    fit = (
        fold.train
        if design.refit is Refit.TRAIN
        else tuple(sorted((*fold.train, *fold.validation)))
    )
    test = _score(
        objective(
            candidates[selected], fit, fold.test, _seed(seed, fold.index, "report", selected)
        ),
        fold.index,
        "report",
        selected,
    )
    return FoldSelection(
        fold=fold.index,
        selected=selected,
        parameters=candidates[selected],
        position=position[selected],
        surface=surface,
        validation_score=surface.best_score,
        test_score=test,
        degradation=sample_degradation(surface.best_score, test),
    )
