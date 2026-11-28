"""Validation designs: what each trial is fitted on and scored on (v4.3, ADR-0073).

An experiment's design (:attr:`~alphalab.lifecycle.experiments.ExperimentSpec.validation`)
says how a search is validated:

* :class:`FullSample` -- every candidate over the whole sample (ADR-0072);
* :class:`WalkForward` -- folds of train, validation and test: every candidate
  is scored on each fold's validation window, the best is selected, and the
  selection alone is scored on the fold's test window;
* :class:`CrossValidated` -- purged and embargoed time-series cross-validation:
  every candidate scored on every fold's validation window;
* :class:`CombinatorialPurged` -- CPCV: every candidate scored on every test
  group of every split, the groups assembled into paths.

Every choice is stated; nothing is defaulted. The folds are the research
splitters' (:mod:`alphalab.research`), drawn over the dataset's instants before
the lockbox, so the split mathematics has one authority.

A trial's :class:`TrialWindow` is what its runner may fit on, what it is scored
on, and where its run starts: a declared ``warm_up`` of instants before the
scored window is run, so a strategy's state is formed, and never scored.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.research.combinatorial_cv import (
    CombinatorialSplitReport,
    combinatorial_purged_splits,
)
from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.purging import PurgePolicy, label_ends_from_horizon
from alphalab.research.selection import SelectionMeasure
from alphalab.research.splits import SplitReport
from alphalab.research.time_series_cv import CVMethod, cross_validation_splits
from alphalab.research.walk_forward import WindowMode, walk_forward_splits
from alphalab.research.walk_forward_optimization import Refit

__all__ = [
    "DESIGNS",
    "REPORT",
    "SEARCH",
    "SELECT",
    "TEST",
    "VALIDATE",
    "CombinatorialPurged",
    "CrossValidated",
    "ExperimentError",
    "FullSample",
    "Lockbox",
    "PurgeSpec",
    "TrialWindow",
    "ValidationDesign",
    "WalkForward",
    "combinatorial",
    "design_key",
    "report_window",
    "sample",
    "windows",
]

#: The phase of a trial in a search over the whole sample.
SEARCH: Final = "search"
#: A walk-forward fold's validation window: every candidate, to select by.
SELECT: Final = "select"
#: A walk-forward fold's test window: the candidate the fold selected.
REPORT: Final = "report"
#: A cross-validation fold's validation window: every candidate.
VALIDATE: Final = "validate"
#: A combinatorial split's test group: every candidate. The phase names the group.
TEST: Final = "test"


class ExperimentError(LifecycleInputError):
    """An experiment refused: a specification that is not one, or a ledger it cannot keep."""


def _require_text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{where} must be stated, got {value!r}.")
    return value


def _require_count(value: object, where: str, least: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ExperimentError(f"{where} must be an integer of at least {least}, got {value!r}.")
    return value


@dataclass(frozen=True, slots=True)
class Lockbox:
    """The records an experiment holds back from every trial, read once (R54-10).

    Attributes:
        start: Every record at or after this instant is held back.
        reread: ``None`` for an experiment's own lockbox. A child experiment
            that reads a lockbox its parent already read states why here, and
            the read is counted.
    """

    start: float
    reread: str | None

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or not isinstance(self.start, int | float):
            raise ExperimentError(f"Lockbox.start must be an instant, got {self.start!r}.")
        if not math.isfinite(self.start):
            raise ExperimentError(f"Lockbox.start must be finite, got {self.start!r}.")
        object.__setattr__(self, "start", float(self.start))
        if self.reread is not None:
            _require_text(self.reread, "Lockbox.reread")


def _require_lockbox(lockbox: object, owner: str) -> None:
    if lockbox is not None and not isinstance(lockbox, Lockbox):
        raise ExperimentError(f"{owner}.lockbox must be a Lockbox, got {lockbox!r}.")


@dataclass(frozen=True, slots=True)
class FullSample:
    """Every trial runs over the whole dataset, but for a lockbox when one is declared."""

    lockbox: Lockbox | None

    def __post_init__(self) -> None:
        _require_lockbox(self.lockbox, "FullSample")


@dataclass(frozen=True, slots=True)
class PurgeSpec:
    """How far a decision's outcome reaches, and the embargo after a scored window.

    Attributes:
        horizon: How many instants a decision's outcome reaches forward: an
            instant whose outcome reaches into a scored window is purged from
            what a trial is fitted on.
        embargo_seconds: How long after a scored window an instant is still
            contaminated by it; ``0.0`` for none.
    """

    horizon: int
    embargo_seconds: float

    def __post_init__(self) -> None:
        _require_count(self.horizon, "PurgeSpec.horizon", 1)
        embargo = self.embargo_seconds
        if (
            isinstance(embargo, bool)
            or not isinstance(embargo, int | float)
            or not math.isfinite(embargo)
            or embargo < 0
        ):
            raise ExperimentError(
                f"PurgeSpec.embargo_seconds must be a finite, non-negative time, got {embargo!r}."
            )
        object.__setattr__(self, "embargo_seconds", float(embargo))

    def policy(self, instants: Sequence[float]) -> PurgePolicy:
        """Research's purge policy over ``instants``."""

        return PurgePolicy(
            label_ends_from_horizon(instants, self.horizon), embargo_seconds=self.embargo_seconds
        )


def _require_kind(value: object, kind: type, where: str) -> None:
    if not isinstance(value, kind):
        raise ExperimentError(f"{where} must be a {kind.__name__}, got {value!r}.")


@dataclass(frozen=True, slots=True)
class WalkForward:
    """Select on each fold's validation window, report the selection on its test window.

    Attributes:
        train: Instants each fold is fitted on.
        validation: Instants each fold's candidates are scored on, to select by.
        test: Instants the selection is scored on.
        mode: Whether the training window slides or grows.
        step: Instants between folds; ``None`` for the test window's length,
            so the test windows tile the sample.
        warm_up: Instants run before a scored window, and never scored.
        refit: What the selection is fitted on before its test window.
        measure: What a candidate is selected by.
        purge: How far a decision reaches, and the embargo; ``None`` for none.
        lockbox: The held-back records, or ``None``.
    """

    train: int
    validation: int
    test: int
    mode: WindowMode
    step: int | None
    warm_up: int
    refit: Refit
    measure: SelectionMeasure
    purge: PurgeSpec | None
    lockbox: Lockbox | None

    def __post_init__(self) -> None:
        for name in ("train", "validation", "test"):
            _require_count(getattr(self, name), f"WalkForward.{name}", 1)
        if self.step is not None:
            _require_count(self.step, "WalkForward.step", 1)
        _require_count(self.warm_up, "WalkForward.warm_up", 0)
        _require_kind(self.mode, WindowMode, "WalkForward.mode")
        _require_kind(self.refit, Refit, "WalkForward.refit")
        _require_kind(self.measure, SelectionMeasure, "WalkForward.measure")
        if self.purge is not None:
            _require_kind(self.purge, PurgeSpec, "WalkForward.purge")
        _require_lockbox(self.lockbox, "WalkForward")


@dataclass(frozen=True, slots=True)
class CrossValidated:
    """Score every candidate on every fold's validation window.

    Attributes:
        folds: How many folds.
        method: Which construction: forward (``ROLLING``, ``EXPANDING``) or
            blocked and purged (``PURGED``, ``EMBARGOED``).
        warm_up: Instants run before a scored window, and never scored.
        measure: What a candidate is selected by, over its mean fold score.
        purge: How far a decision reaches, and the embargo. Required by the
            purged methods, whose names claim it.
        lockbox: The held-back records, or ``None``.
    """

    folds: int
    method: CVMethod
    warm_up: int
    measure: SelectionMeasure
    purge: PurgeSpec | None
    lockbox: Lockbox | None

    def __post_init__(self) -> None:
        _require_count(self.folds, "CrossValidated.folds", 2)
        _require_kind(self.method, CVMethod, "CrossValidated.method")
        _require_count(self.warm_up, "CrossValidated.warm_up", 0)
        _require_kind(self.measure, SelectionMeasure, "CrossValidated.measure")
        if self.purge is not None:
            _require_kind(self.purge, PurgeSpec, "CrossValidated.purge")
        _require_lockbox(self.lockbox, "CrossValidated")


@dataclass(frozen=True, slots=True)
class CombinatorialPurged:
    """CPCV: score every candidate on every test group of every split.

    Attributes:
        groups: How many groups the sample is cut into.
        test_groups: How many each split tests.
        warm_up: Instants run before a scored group, and never scored.
        measure: What a candidate is selected by, over its mean path score.
        purge: How far a decision reaches, and the embargo: required, since
            the method's name claims it.
        lockbox: The held-back records, or ``None``.
    """

    groups: int
    test_groups: int
    warm_up: int
    measure: SelectionMeasure
    purge: PurgeSpec
    lockbox: Lockbox | None

    def __post_init__(self) -> None:
        _require_count(self.groups, "CombinatorialPurged.groups", 2)
        _require_count(self.test_groups, "CombinatorialPurged.test_groups", 1)
        if self.test_groups >= self.groups:
            raise ExperimentError(
                f"CombinatorialPurged.test_groups must be below groups ({self.groups}), got "
                f"{self.test_groups}: a split tests some groups and trains on the others."
            )
        _require_count(self.warm_up, "CombinatorialPurged.warm_up", 0)
        _require_kind(self.measure, SelectionMeasure, "CombinatorialPurged.measure")
        _require_kind(self.purge, PurgeSpec, "CombinatorialPurged.purge")
        _require_lockbox(self.lockbox, "CombinatorialPurged")


#: An experiment's validation design.
type ValidationDesign = FullSample | WalkForward | CrossValidated | CombinatorialPurged

#: Every kind of design, for an ``isinstance`` check.
DESIGNS: Final = (FullSample, WalkForward, CrossValidated, CombinatorialPurged)


@dataclass(frozen=True, slots=True)
class TrialWindow:
    """What a trial may be fitted on, what it is scored on, and where its run starts.

    Attributes:
        fit: The instants a runner may fit a model on, ascending.
        evaluate: The instants the trial is scored on, ascending and
            contiguous in the sample.
        start: The run's first instant: ``warm_up`` instants before
            ``evaluate``, run and never scored. A window too near the sample's
            start takes the warm-up it lacks from its own first instants.
    """

    fit: tuple[float, ...]
    evaluate: tuple[float, ...]
    start: float


def _window(
    instants: Sequence[float],
    fit: Sequence[float],
    evaluate: Sequence[float],
    warm_up: int,
    where: str,
) -> TrialWindow:
    first = bisect.bisect_left(instants, evaluate[0])
    short = max(0, warm_up - first)
    # A window too near the sample's start -- the first block of a blocked
    # scheme has nothing before it -- takes the warm-up it lacks from its own
    # first instants, which are then run and never scored.
    scored = tuple(evaluate)[short:]
    if len(scored) < 2:
        raise ExperimentError(
            f"{where} has {len(evaluate)} instants, and a warm-up of {warm_up} with {first} "
            f"before it leaves {len(scored)} to score; a score needs two. Shorten the "
            "warm-up, or lengthen the window."
        )
    return TrialWindow(fit=tuple(fit), evaluate=scored, start=instants[max(0, first - warm_up)])


def sample(design: ValidationDesign, instants: Sequence[float]) -> tuple[float, ...]:
    """The instants a design splits: the dataset's, before its lockbox."""

    lockbox = design.lockbox
    return tuple(instants) if lockbox is None else tuple(t for t in instants if t < lockbox.start)


def _split(design: WalkForward | CrossValidated, instants: tuple[float, ...]) -> SplitReport:
    policy = None if design.purge is None else design.purge.policy(instants)
    try:
        if isinstance(design, WalkForward):
            return walk_forward_splits(
                instants,
                design.train,
                design.validation,
                design.test,
                design.mode,
                step=design.step,
                policy=policy,
            )
        return cross_validation_splits(instants, design.folds, design.method, policy=policy)
    except ResearchValidationError as error:
        raise ExperimentError(f"The design cannot split the sample: {error}") from error


def combinatorial(
    design: CombinatorialPurged, instants: Sequence[float]
) -> CombinatorialSplitReport:
    """CPCV's splits and paths over the design's sample."""

    held = sample(design, instants)
    try:
        return combinatorial_purged_splits(
            held, design.groups, design.test_groups, design.purge.policy(held)
        )
    except ResearchValidationError as error:
        raise ExperimentError(f"The design cannot split the sample: {error}") from error


def windows(
    design: ValidationDesign, instants: Sequence[float]
) -> tuple[tuple[int, str, TrialWindow | None], ...]:
    """Every (fold, phase, window) a design names before any selection.

    A full-sample design names one, with no window: its trials run over the
    whole sample, as ADR-0072's do. A walk-forward design names each fold's
    validation window; its test windows wait on the fold's selection
    (:func:`report_window`).
    """

    if isinstance(design, FullSample):
        return ((0, SEARCH, None),)
    held = sample(design, instants)
    if isinstance(design, CombinatorialPurged):
        report = combinatorial(design, instants)
        named: list[tuple[int, str, TrialWindow | None]] = []
        for split in report.splits:
            for group in split.test_groups:
                named.append(
                    (
                        split.index,
                        f"{TEST}:{group}",
                        _window(
                            held,
                            split.train,
                            report.groups[group],
                            design.warm_up,
                            f"Split {split.index}'s group {group}",
                        ),
                    )
                )
        return tuple(named)
    phase = SELECT if isinstance(design, WalkForward) else VALIDATE
    return tuple(
        (
            fold.index,
            phase,
            _window(held, fold.train, fold.validation, design.warm_up, f"Fold {fold.index}"),
        )
        for fold in _split(design, held).folds
    )


def report_window(design: WalkForward, instants: Sequence[float], fold: int) -> TrialWindow:
    """A walk-forward fold's test window, fitted as ``refit`` says."""

    held = sample(design, instants)
    chosen = _split(design, held).folds[fold]
    fit = (
        chosen.train
        if design.refit is Refit.TRAIN
        else tuple(sorted((*chosen.train, *chosen.validation)))
    )
    return _window(held, fit, chosen.test, design.warm_up, f"Fold {fold}'s test window")


def design_key(design: ValidationDesign) -> Mapping[str, Any]:
    """A design's canonical form, as the experiment's identity covers it.

    A full-sample design's is ADR-0072's, so its experiments' identities are
    unchanged.
    """

    lockbox = (
        None
        if design.lockbox is None
        else {"reread": design.lockbox.reread, "start": design.lockbox.start}
    )
    if isinstance(design, FullSample):
        return {"design": "FULL_SAMPLE", "lockbox": lockbox}
    purge = (
        None
        if design.purge is None
        else {"embargo_seconds": design.purge.embargo_seconds, "horizon": design.purge.horizon}
    )
    common = {"lockbox": lockbox, "measure": design.measure.name, "purge": purge}
    if isinstance(design, WalkForward):
        return {
            **common,
            "design": "WALK_FORWARD",
            "mode": design.mode.name,
            "refit": design.refit.name,
            "step": design.step,
            "test": design.test,
            "train": design.train,
            "validation": design.validation,
            "warm_up": design.warm_up,
        }
    if isinstance(design, CrossValidated):
        return {
            **common,
            "design": "CROSS_VALIDATED",
            "folds": design.folds,
            "method": design.method.name,
            "warm_up": design.warm_up,
        }
    return {
        **common,
        "design": "COMBINATORIAL_PURGED",
        "groups": design.groups,
        "test_groups": design.test_groups,
        "warm_up": design.warm_up,
    }
