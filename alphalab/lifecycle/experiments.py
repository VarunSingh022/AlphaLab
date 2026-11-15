"""Experiments and the trial ledger: every trial counted, identified and resumable (v4.3, ADR-0072).

A search over backtests is measured by ADR-0054's statistics, given its
population. This module records the population.

::

    spec = ExperimentSpec(
        name="bands", hypothesis="mean reversion pays at short windows", ...,
        space=ParameterSpace.grid({"window": [10, 20, 40]}), ...,
    )
    registry = RunnerRegistry().register("bands", build_bands_trial)
    record = run_experiment(spec, registry, "bands", TrialLedger(store),
                            SequentialExecutor())
    record.counts.completed, record.counts.insolvent, record.counts.failed

What an experiment states
-------------------------
An :class:`ExperimentSpec` has no defaults: everything a trial's result turns
on is stated, and every trial is held to it. A runner -- a function registered
by name in a :class:`RunnerRegistry` -- builds a trial's inputs from its typed
configuration; the ledger then refuses inputs that are not the experiment's
(another dataset, schedule, registry or execution model), sets the trial's seed
and the experiment's retention, and runs it through
:func:`~alphalab.backtesting.capture.run_captured` with the experiment's
capture. A runner's name is looked up in the registry and never resolved to
code (BDY-028).

What a trial leaves
-------------------
Every trial has an outcome (:class:`TrialOutcome`). Five are facts about the
trial and are recorded: it completed, went insolvent, its strategy failed,
AlphaLab refused its inputs, or it failed otherwise. An interruption -- a
cancellation, a dead worker -- is infrastructure's, writes nothing, and a
resume runs the trial again. A :class:`TrialRecord` holds digests and the
series' identity, never the run's result (R54-2): the series is filed beside it.
Records are filed in an :class:`~alphalab.model_registry.evidence.EvidenceStore`
under their ``trial_id``, so resuming runs only the trials with no record, and a
trial that runs again and leaves another record is refused: the experiment is
nondeterministic, and the store says where.

Identities
----------
``experiment_id``, ``trial_id``, ``trial_record_id``, ``matrix_id`` and
``experiment_record_id`` are SHA-256 digests of canonical text under named
schemes. A trial's seed is the walk-forward search's own formula over the
experiment's seed, the fold, the phase and the rendered candidate. Every value
an identity covers is exact or correctly rounded (ADR-0058), so the same
experiment has the same record on every host.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum, unique
from typing import Any, Final, Protocol

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.capture import (
    CaptureRecord,
    CaptureSpec,
    InsolventCaptureError,
    TraceCounts,
    canonical_extractor_key,
    run_captured,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.provenance import run_provenance
from alphalab.backtesting.replay_snapshot import dataset_digest
from alphalab.backtesting.schedule import RunSchedule
from alphalab.cloud_research.cluster import (
    AssignmentPolicy,
    CloudResearchState,
    run_cluster_cycle,
    submit_research_job,
)
from alphalab.common.exceptions import AlphaLabError
from alphalab.common.ids import id_scope
from alphalab.common.registry import RunnerRegistry
from alphalab.distributed.job import JobType
from alphalab.experiment_tracking.tracker import ExperimentRun, ExperimentTracker
from alphalab.instrument.snapshot import registry_digest
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.reproducibility import digest_run
from alphalab.lifecycle.selection import trial_matrix_from_captures
from alphalab.model_registry.evidence import EvidenceStore
from alphalab.persistence.serializer import serialize
from alphalab.research.walk_forward_optimization import ParameterSpace, _render, _seed
from alphalab.runtime.assumptions import ExecutionAssumptions, execution_assumptions
from alphalab.runtime.execution_pipeline import ContextFactory
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import RunConfig
from alphalab.runtime.schedule_check import ScheduleBinding
from alphalab.strategy.configuration import decode_configuration
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

__all__ = [
    "EXPERIMENT_RECORD_SCHEME",
    "EXPERIMENT_SCHEME",
    "EXPERIMENT_TAG",
    "SEARCH",
    "TRIAL_MATRIX_SCHEME",
    "TRIAL_RECORD_SCHEME",
    "TRIAL_SCHEME",
    "ClusterExecutor",
    "DatasetIdentity",
    "ExperimentDefinition",
    "ExperimentError",
    "ExperimentRecord",
    "ExperimentSpec",
    "FullSample",
    "LedgerCount",
    "Lockbox",
    "LockboxRead",
    "NondeterministicTrialError",
    "Runner",
    "RunnerRegistry",
    "SequentialExecutor",
    "SpawnExecutor",
    "TrialExecutor",
    "TrialInputs",
    "TrialLedger",
    "TrialLimits",
    "TrialOutcome",
    "TrialRecord",
    "TrialRerun",
    "TrialSpec",
    "canonical_experiment_key",
    "dataset_identity",
    "experiment_tags",
    "read_lockbox",
    "rerun_trial",
    "run_experiment",
    "run_trial",
    "tracked_runs",
]

#: The scheme an experiment's canonical key is written under.
EXPERIMENT_SCHEME: Final = "alphalab.experiment.v1"
#: The scheme a trial's identity is written under.
TRIAL_SCHEME: Final = "alphalab.trial.v1"
#: The scheme a trial record's canonical text is written under.
TRIAL_RECORD_SCHEME: Final = "alphalab.trial_record.v1"
#: The scheme a trial matrix's canonical text is written under.
TRIAL_MATRIX_SCHEME: Final = "alphalab.trial_matrix.v1"
#: The scheme an experiment record's canonical text is written under.
EXPERIMENT_RECORD_SCHEME: Final = "alphalab.experiment_record.v1"

#: The phase of a trial in a search over the whole sample.
SEARCH: Final = "search"

#: What a trial's runner draws identifiers under: the trial's own formula, in a
#: phase no trial has, so its stream is the trial's and not its run's.
_RUNNER_PHASE: Final = "runner"

_HEX = frozenset("0123456789abcdef")


class ExperimentError(LifecycleInputError):
    """An experiment refused: a specification that is not one, or a ledger it cannot keep."""


class NondeterministicTrialError(ExperimentError):
    """A trial ran again and left another record under its ``trial_id``.

    Attributes:
        trial_id: The trial.
    """

    def __init__(self, trial_id: str, detail: str) -> None:
        super().__init__(
            f"Trial {trial_id} ran again and left another record: the experiment is "
            f"nondeterministic, and its ledger refuses the second value ({detail})."
        )
        self.trial_id = trial_id


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_text(value: object) -> str:
    return _sha256(serialize(value))


def _require_hex(value: object, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise ExperimentError(f"{where} must be a SHA-256 hex digest, got {value!r}.")
    return value


def _require_text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{where} must be stated, got {value!r}.")
    return value


# --- what an experiment states ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """Which data an experiment's trials run over: its id, and the digest of its records.

    Attributes:
        dataset_id: The dataset's ``dataset_id``.
        digest: :func:`~alphalab.backtesting.replay_snapshot.dataset_digest`
            of its records: the fingerprint a paused replay holds its input to.
    """

    dataset_id: str
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.dataset_id, "DatasetIdentity.dataset_id")
        _require_hex(self.digest, "DatasetIdentity.digest")


def dataset_identity(dataset: MarketDataset) -> DatasetIdentity:
    """``dataset``'s identity: its id, and the digest of its records in their canonical form."""

    return DatasetIdentity(dataset.dataset_id, dataset_digest(dataset))


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


@dataclass(frozen=True, slots=True)
class FullSample:
    """Every trial runs over the whole dataset, but for a lockbox when one is declared.

    The validation design of v4.3's M4; walk-forward, purged CV and CPCV are
    M5's (WS-24).
    """

    lockbox: Lockbox | None

    def __post_init__(self) -> None:
        if self.lockbox is not None and not isinstance(self.lockbox, Lockbox):
            raise ExperimentError(f"FullSample.lockbox must be a Lockbox, got {self.lockbox!r}.")


@dataclass(frozen=True, slots=True)
class TrialLimits:
    """What a trial may use. A trial never touches the network.

    Attributes:
        seconds: The wall-clock time a trial may run in a spawned worker, or
            ``None``: timed from the moment the worker has loaded the
            experiment, so an interpreter's start and its imports, which a busy
            host slows, are never the trial's. Past it the worker stops itself
            and the trial is interrupted.
    """

    seconds: float | None

    def __post_init__(self) -> None:
        if self.seconds is None:
            return
        if (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, int | float)
            or not math.isfinite(self.seconds)
            or self.seconds <= 0
        ):
            raise ExperimentError(f"TrialLimits.seconds must be positive, got {self.seconds!r}.")
        object.__setattr__(self, "seconds", float(self.seconds))


def _type_name(kind: type) -> str:
    return f"{kind.__module__}.{kind.__qualname__}"


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """Everything an experiment's trials turn on, stated (ADR-0072 section 2).

    Attributes:
        name: What the researcher calls it.
        hypothesis: What is being tested; its digest enters the identity
            (R54-9).
        strategy: The strategy's fingerprint, ``name@sha256`` (ADR-0041).
        configuration_type: The dataclass every candidate is typed as (R54-3).
        space: The candidates, in their order.
        dataset: The data every trial runs over.
        schedule: The binding of the schedule every trial is given.
        instruments: The digest of the registry every trial runs with, or
            ``None`` for none.
        execution: What every trial's execution assumes (PER-008).
        capture: How every trial is captured (ADR-0055).
        retention: What every trial keeps.
        validation: The validation design.
        seed: The experiment's seed.
        parent_id: The experiment this one continues, or ``None``.
        limits: What a trial may use.

    Raises:
        ExperimentError: If a field is missing or not of its kind.
    """

    name: str
    hypothesis: str
    strategy: str
    configuration_type: type
    space: ParameterSpace
    dataset: DatasetIdentity
    schedule: ScheduleBinding
    instruments: str | None
    execution: ExecutionAssumptions
    capture: CaptureSpec
    retention: RetentionPolicy
    validation: FullSample
    seed: int
    parent_id: str | None
    limits: TrialLimits

    def __post_init__(self) -> None:
        _require_text(self.name, "ExperimentSpec.name")
        _require_text(self.hypothesis, "ExperimentSpec.hypothesis")
        name, at, digest = (
            self.strategy.partition("@") if isinstance(self.strategy, str) else ("", "", "")
        )
        if not name or not at:
            raise ExperimentError(
                "ExperimentSpec.strategy must be a fingerprint, name@sha256, got "
                f"{self.strategy!r}."
            )
        _require_hex(digest, "ExperimentSpec.strategy's digest")
        if not (
            isinstance(self.configuration_type, type) and is_dataclass(self.configuration_type)
        ):
            raise ExperimentError(
                "ExperimentSpec.configuration_type must be a dataclass type, every candidate "
                f"typed as it (R54-3); got {self.configuration_type!r}."
            )
        for name_, kind in (
            ("space", ParameterSpace),
            ("dataset", DatasetIdentity),
            ("schedule", ScheduleBinding),
            ("execution", ExecutionAssumptions),
            ("capture", CaptureSpec),
            ("retention", RetentionPolicy),
            ("validation", FullSample),
            ("limits", TrialLimits),
        ):
            if not isinstance(getattr(self, name_), kind):
                raise ExperimentError(
                    f"ExperimentSpec.{name_} must be a {kind.__name__}, got "
                    f"{getattr(self, name_)!r}."
                )
        if self.instruments is not None:
            _require_hex(self.instruments, "ExperimentSpec.instruments")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ExperimentError(f"ExperimentSpec.seed must be an integer, got {self.seed!r}.")
        if self.parent_id is not None:
            _require_hex(self.parent_id, "ExperimentSpec.parent_id")
        lockbox = self.validation.lockbox
        if lockbox is not None and lockbox.reread is not None and self.parent_id is None:
            raise ExperimentError(
                "A lockbox read again is read by a child experiment, which names its parent "
                "(R54-10)."
            )
        names = [item.name for item in fields(self.configuration_type)]
        for candidate in self.space.candidates:
            unknown = sorted(set(candidate) - set(names))
            if unknown:
                raise ExperimentError(
                    f"The space's candidates name {unknown}, which "
                    f"{_type_name(self.configuration_type)} does not declare."
                )

    @property
    def experiment_id(self) -> str:
        """SHA-256 of :func:`canonical_experiment_key`."""

        return _sha256(canonical_experiment_key(self))

    def trials(self) -> tuple[TrialSpec, ...]:
        """Every trial the experiment runs, in the order they are submitted."""

        experiment_id = self.experiment_id
        return tuple(
            TrialSpec(
                experiment_id=experiment_id,
                candidate=candidate,
                configuration=_typed(candidate, self.configuration_type),
                fold=0,
                phase=SEARCH,
                trial_seed=_seed(self.seed, 0, SEARCH, _render(candidate)),
            )
            for candidate in self.space.candidates
        )


def _typed(candidate: Mapping[str, Any], declared: type) -> Any:
    try:
        return decode_configuration(dict(candidate), declared, "candidate")
    except AlphaLabError as error:
        raise ExperimentError(
            f"Candidate {_render(candidate)} is not a {_type_name(declared)}: {error}"
        ) from error


def canonical_experiment_key(spec: ExperimentSpec) -> str:
    """The canonical text of ``spec``, under :data:`EXPERIMENT_SCHEME`."""

    lockbox = spec.validation.lockbox
    return serialize(
        {
            "capture": {
                "extractor": canonical_extractor_key(spec.capture.extractor),
                "trace": spec.capture.trace,
            },
            "configuration_type": _type_name(spec.configuration_type),
            "dataset": {"dataset_id": spec.dataset.dataset_id, "digest": spec.dataset.digest},
            "execution": spec.execution,
            "hypothesis_sha256": _sha256(spec.hypothesis),
            "instruments": spec.instruments,
            "limits": {"seconds": spec.limits.seconds},
            "name": spec.name,
            "parent_id": spec.parent_id,
            "retention": spec.retention,
            "schedule": spec.schedule,
            "scheme": EXPERIMENT_SCHEME,
            "seed": spec.seed,
            "space": [_render(candidate) for candidate in spec.space.candidates],
            "strategy": spec.strategy,
            "validation": {
                "design": "FULL_SAMPLE",
                "lockbox": None
                if lockbox is None
                else {"reread": lockbox.reread, "start": lockbox.start},
            },
        }
    )


@dataclass(frozen=True, slots=True)
class TrialSpec:
    """One trial: a candidate, typed, with its fold, phase and seed.

    Attributes:
        experiment_id: The experiment it belongs to.
        candidate: The candidate, as the space renders it.
        configuration: The candidate typed as the experiment's
            ``configuration_type``.
        fold: Its fold; ``0`` in a full-sample search.
        phase: Its phase; :data:`SEARCH` in a full-sample search.
        trial_seed: Its seed: the walk-forward search's formula over the
            experiment's seed, the fold, the phase and the rendered candidate.
    """

    experiment_id: str
    candidate: Mapping[str, Any]
    configuration: Any
    fold: int
    phase: str
    trial_seed: int

    @property
    def label(self) -> str:
        """The rendered candidate: ``"window=20,width=Decimal('1.5')"``."""

        return _render(self.candidate)

    @property
    def trial_id(self) -> str:
        """SHA-256 of the experiment, the rendered candidate, the fold and the phase."""

        return _digest_text(
            {
                "candidate": self.label,
                "experiment_id": self.experiment_id,
                "fold": self.fold,
                "phase": self.phase,
                "scheme": TRIAL_SCHEME,
            }
        )


# --- what a trial leaves ---------------------------------------------------------


@unique
class TrialOutcome(Enum):
    """What became of a trial (ADR-0072 section 4)."""

    #: It ran to its end, and its series was captured.
    COMPLETED = "COMPLETED"
    #: Its equity reached zero or below; its series, if captured, runs to that instant.
    INSOLVENT = "INSOLVENT"
    #: The researcher's strategy raised.
    STRATEGY_FAILED = "STRATEGY_FAILED"
    #: AlphaLab refused the trial's inputs.
    REFUSED = "REFUSED"
    #: Anything else went wrong in the trial.
    FAILED = "FAILED"
    #: Infrastructure's: a cancellation, a dead worker, a time limit. Not recorded.
    INTERRUPTED = "INTERRUPTED"


#: The outcomes a ledger records.
RECORDED: Final = frozenset(
    outcome for outcome in TrialOutcome if outcome is not TrialOutcome.INTERRUPTED
)


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """What a trial left: digests and the series' identity, never the result (R54-2).

    Attributes:
        trial_id: The trial.
        experiment_id: Its experiment.
        label: Its rendered candidate.
        fold: Its fold.
        phase: Its phase.
        trial_seed: Its seed.
        outcome: :class:`TrialOutcome`, never ``INTERRUPTED``.
        result_id: The run's ``result_id``, when it ran to its end.
        configuration_id: The run's ``configuration_id``, when traced.
        trace_id: The run's history's identity, when traced (ADR-0055).
        provenance_id: The run's provenance's identity (ADR-0053), when it ran
            to its end.
        extractor_id: The extractor its series was read by.
        series_id: The series' identity, when one was captured; the series is
            filed beside the record under it.
        insolvent_at: Where its series ended at insolvency, or ``None``.
        diagnostics: For a trial that did not complete, the error's type and
            message; otherwise ``""``. Kept as evidence beside the record, it
            enters no identity: an interpreter's own error text differs between
            its versions, and the same trial failing the same way is one record
            on every host.
    """

    trial_id: str
    experiment_id: str
    label: str
    fold: int
    phase: str
    trial_seed: int
    outcome: TrialOutcome
    result_id: str | None
    configuration_id: str | None
    trace_id: str | None
    provenance_id: str | None
    extractor_id: str
    series_id: str | None
    insolvent_at: float | None
    diagnostics: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, TrialOutcome) or self.outcome is TrialOutcome.INTERRUPTED:
            raise ExperimentError(
                f"A trial record's outcome is one a ledger records, got {self.outcome!r}."
            )

    @property
    def record_id(self) -> str:
        """SHA-256 of the record's canonical text, under :data:`TRIAL_RECORD_SCHEME`.

        Its diagnostics aside, which enter no identity.
        """

        return _digest_text({"record": _identified(self), "scheme": TRIAL_RECORD_SCHEME})


def _record_payload(record: TrialRecord) -> dict[str, Any]:
    payload = {item.name: getattr(record, item.name) for item in fields(record)}
    payload["outcome"] = record.outcome.name
    return payload


def _identified(record: TrialRecord) -> dict[str, Any]:
    """What a record's identity covers: everything but its diagnostics."""

    payload = _record_payload(record)
    del payload["diagnostics"]
    return payload


@dataclass(frozen=True, slots=True)
class LedgerCount:
    """How many trials ended each recorded way (D-54-4, D-54-12).

    ``completed`` is the conservative N a deflated Sharpe ratio reads; every
    recorded trial is a test attempted, and counts in a multiple-testing family.
    """

    completed: int
    insolvent: int
    strategy_failed: int
    refused: int
    failed: int

    @property
    def recorded(self) -> int:
        """Every trial recorded: the tests attempted."""

        return self.completed + self.insolvent + self.strategy_failed + self.refused + self.failed

    @classmethod
    def of(cls, records: Iterable[TrialRecord]) -> LedgerCount:
        counts: dict[TrialOutcome, int] = dict.fromkeys(RECORDED, 0)
        for record in records:
            counts[record.outcome] += 1
        return cls(
            completed=counts[TrialOutcome.COMPLETED],
            insolvent=counts[TrialOutcome.INSOLVENT],
            strategy_failed=counts[TrialOutcome.STRATEGY_FAILED],
            refused=counts[TrialOutcome.REFUSED],
            failed=counts[TrialOutcome.FAILED],
        )


# --- runners -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrialInputs:
    """What a runner builds for a trial: everything but what the experiment fixes.

    The ledger sets the run's seed to the trial's and its retention to the
    experiment's, and runs it with the experiment's capture.
    """

    config: RunConfig
    dataset: MarketDataset
    strategy_state: StrategyRuntimeState
    context_factory: ContextFactory
    schedule: RunSchedule
    observations: tuple[ObservationDelivery[Any], ...]


#: A function that builds a trial's inputs from its specification.
Runner = Callable[[TrialSpec], TrialInputs]


# --- running one trial ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Ran:
    record: TrialRecord
    capture: CaptureRecord | None


def _diagnosis(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _refusal(spec: ExperimentSpec, inputs: TrialInputs) -> str | None:
    """Why ``inputs`` are not the experiment's, or ``None`` when they are."""

    if not isinstance(inputs, TrialInputs):
        return f"the runner returned {type(inputs).__name__}, not TrialInputs"
    if dataset_identity(inputs.dataset) != spec.dataset:
        return f"the dataset is not {spec.dataset.dataset_id} with digest {spec.dataset.digest}"
    if inputs.schedule.binding != spec.schedule:
        return "the schedule is not the one the experiment binds"
    instruments = inputs.config.pipeline.instruments
    digest = None if instruments is None else registry_digest(instruments)
    if digest != spec.instruments:
        return "the instrument registry is not the one the experiment states"
    assumed = execution_assumptions(inputs.config.pipeline, inputs.config.fill_policy)
    if assumed != spec.execution:
        return "the execution assumptions are not the ones the experiment states"
    return None


def _held_back(dataset: MarketDataset, lockbox: Lockbox | None) -> MarketDataset:
    if lockbox is None:
        return dataset
    kept = tuple(record for record in dataset.records if record.timestamp < lockbox.start)
    if not kept:
        raise ExperimentError(
            f"The lockbox from {lockbox.start} holds back every record of "
            f"{dataset.dataset_id}: no trial would see any data."
        )
    return replace(dataset, records=kept)


def _trial_left(
    spec: ExperimentSpec,
    trial: TrialSpec,
    outcome: TrialOutcome,
    diagnostics: str = "",
    *,
    captured: CaptureRecord | None = None,
    result_id: str | None = None,
    provenance_id: str | None = None,
) -> _Ran:
    return _Ran(
        TrialRecord(
            trial_id=trial.trial_id,
            experiment_id=trial.experiment_id,
            label=trial.label,
            fold=trial.fold,
            phase=trial.phase,
            trial_seed=trial.trial_seed,
            outcome=outcome,
            result_id=result_id,
            configuration_id=None if captured is None else captured.configuration_id,
            trace_id=None if captured is None else captured.trace_id,
            provenance_id=provenance_id,
            extractor_id=spec.capture.extractor.extractor_id,
            series_id=None if captured is None else captured.series_id,
            insolvent_at=None if captured is None else captured.insolvent_at,
            diagnostics=diagnostics,
        ),
        captured,
    )


def run_trial(spec: ExperimentSpec, runner: Runner, trial: TrialSpec) -> _Ran:
    """Run ``trial`` of ``spec`` through ``runner``, and return what it left.

    The trial draws no identifier from the scope it is run in: the runner mints
    from a stream seeded by the trial, and the run from the trial's seed. A
    ``KeyboardInterrupt`` or ``SystemExit`` propagates: an interruption leaves
    nothing.
    """

    def record(
        outcome: TrialOutcome,
        diagnostics: str = "",
        *,
        captured: CaptureRecord | None = None,
        result_id: str | None = None,
        provenance_id: str | None = None,
    ) -> _Ran:
        return _trial_left(
            spec,
            trial,
            outcome,
            diagnostics,
            captured=captured,
            result_id=result_id,
            provenance_id=provenance_id,
        )

    try:
        # The runner is the researcher's code, and may mint identifiers. It mints
        # them from the trial's own stream, never the scope around the
        # experiment, so a trial draws nothing from its caller (ADR-0029 D6) and
        # mints the same identifiers in any process; the run mints from its seed.
        with id_scope(_seed(trial.trial_seed, trial.fold, _RUNNER_PHASE, trial.label)):
            inputs = runner(trial)
    except AlphaLabError as error:
        return record(TrialOutcome.REFUSED, _diagnosis(error))
    except Exception as error:
        return record(TrialOutcome.FAILED, _diagnosis(error))
    refused = _refusal(spec, inputs)
    if refused is not None:
        return record(TrialOutcome.REFUSED, f"ExperimentError: {refused}")
    config = replace(
        inputs.config,
        seed=trial.trial_seed,
        pipeline=replace(inputs.config.pipeline, retention=spec.retention),
    )
    try:
        dataset = _held_back(inputs.dataset, spec.validation.lockbox)
        captured = run_captured(
            config,
            dataset,
            inputs.strategy_state,
            inputs.context_factory,
            inputs.observations,
            capture=spec.capture,
            schedule=inputs.schedule,
        )
    except InsolventCaptureError as error:
        return record(TrialOutcome.INSOLVENT, _diagnosis(error))
    except AlphaLabError as error:
        return record(TrialOutcome.REFUSED, _diagnosis(error))
    except Exception as error:
        return record(TrialOutcome.FAILED, _diagnosis(error))
    result = captured.result
    failures = result.run.strategy_failures
    digest = digest_run(result)
    provenance = run_provenance(
        result,
        schedule=inputs.schedule,
        observations=inputs.observations,
        result_id=digest.result_id,
    )
    if failures:
        first = failures[0]
        return record(
            TrialOutcome.STRATEGY_FAILED,
            f"{len(failures)} strategy failure(s); the first: {first.error}",
            captured=captured.record,
            result_id=digest.result_id,
            provenance_id=provenance.provenance_id,
        )
    outcome = (
        TrialOutcome.INSOLVENT
        if captured.record.insolvent_at is not None
        else TrialOutcome.COMPLETED
    )
    return record(
        outcome,
        captured=captured.record,
        result_id=digest.result_id,
        provenance_id=provenance.provenance_id,
    )


# --- executors and the ledger ------------------------------------------------------------


class TrialExecutor(Protocol):
    """Runs trials and yields what each left, in the order they were submitted."""

    def execute(
        self,
        spec: ExperimentSpec,
        registry: RunnerRegistry,
        runner: str,
        trials: Sequence[TrialSpec],
    ) -> Iterator[_Ran]: ...


@dataclass(frozen=True, slots=True)
class SequentialExecutor:
    """Runs each trial in this process, one after another, in submission order."""

    def execute(
        self,
        spec: ExperimentSpec,
        registry: RunnerRegistry,
        runner: str,
        trials: Sequence[TrialSpec],
    ) -> Iterator[_Ran]:
        build = registry.runner(runner)
        for trial in trials:
            yield run_trial(spec, build, trial)


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    """An experiment and the runners its trials name, defined where a worker can load them.

    A :class:`SpawnExecutor`'s worker imports the module that defines one, after
    it has refused the network, and holds it to the experiment the parent
    submitted. Define it at a module's top level.
    """

    spec: ExperimentSpec
    registry: RunnerRegistry

    def __post_init__(self) -> None:
        if not isinstance(self.spec, ExperimentSpec):
            raise ExperimentError(
                f"ExperimentDefinition.spec must be an ExperimentSpec, got {self.spec!r}."
            )
        if not isinstance(self.registry, RunnerRegistry):
            raise ExperimentError(
                f"ExperimentDefinition.registry must be a RunnerRegistry, got {self.registry!r}."
            )


def _ran_to_payload(ran: _Ran) -> dict[str, Any]:
    capture = ran.capture
    return {
        "capture": None
        if capture is None
        else {
            "configuration_id": capture.configuration_id,
            "extractor_id": capture.extractor_id,
            "insolvent_at": capture.insolvent_at,
            "instants": list(capture.instants),
            "returns": list(capture.returns),
            "series_id": capture.series_id,
            "steps": capture.steps,
            "trace_id": capture.trace_id,
            "traced": None
            if capture.traced is None
            else [
                capture.traced.fills,
                capture.traced.transactions,
                capture.traced.order_events,
                capture.traced.equity_points,
            ],
        },
        "record": _record_payload(ran.record),
    }


def _ran_from_payload(payload: Mapping[str, Any]) -> _Ran:
    held = payload["capture"]
    capture = None
    if held is not None:
        traced = held["traced"]
        capture = CaptureRecord(
            extractor_id=held["extractor_id"],
            instants=tuple(held["instants"]),
            returns=tuple(held["returns"]),
            series_id=held["series_id"],
            insolvent_at=held["insolvent_at"],
            steps=held["steps"],
            configuration_id=held["configuration_id"],
            trace_id=held["trace_id"],
            traced=None if traced is None else TraceCounts(*traced),
        )
    return _Ran(_record_from_payload(payload["record"]), capture)


#: The module a spawned trial runs as: AlphaLab's own, whatever the trial is.
_WORKER: Final = "alphalab.lifecycle._trial_worker"

#: How long a worker of a time-limited trial may take to start and load the
#: experiment before the trial's own time begins. The worker enforces the
#: trial's limit itself; past the limit and this allowance its parent stops a
#: worker that never reported, as dead.
_START_ALLOWANCE: Final = 600.0


def _require_definition(definition: object, owner: str) -> str:
    module, colon, attribute = (
        definition.partition(":") if isinstance(definition, str) else ("", "", "")
    )
    if not module or not colon or not attribute.isidentifier():
        raise ExperimentError(
            f"{owner}.definition names where the experiment is defined, "
            f"'package.module:NAME'; got {definition!r}."
        )
    return str(definition)


def _spawn(
    definition: str,
    spec: ExperimentSpec,
    experiment_id: str,
    runner: str,
    trial: TrialSpec,
    path: list[str],
) -> _Ran | None:
    """Run ``trial`` in a fresh interpreter; ``None`` when it was interrupted."""

    request = json.dumps(
        {
            "definition": definition,
            "experiment_id": experiment_id,
            "path": path,
            "runner": runner,
            "trial_id": trial.trial_id,
        }
    )
    # AlphaLab's own worker module, with fixed arguments: nothing in the
    # command comes from a request.
    process = subprocess.Popen(
        [sys.executable, "-m", _WORKER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    seconds = spec.limits.seconds
    try:
        out, _ = process.communicate(
            request, timeout=None if seconds is None else seconds + _START_ALLOWANCE
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    if process.returncode != 0 or not lines:
        return None
    answer = json.loads(lines[-1])
    if "mismatch" in answer:
        raise ExperimentError(
            f"The spawned worker loaded another experiment: {answer['mismatch']}."
        )
    if "failed" in answer:
        return _trial_left(
            spec,
            trial,
            TrialOutcome.FAILED,
            f"the experiment's definition did not load: {answer['failed']}",
        )
    return _ran_from_payload(answer)


def _search_path() -> list[str]:
    return [entry for entry in sys.path if entry]


@dataclass(frozen=True, slots=True)
class SpawnExecutor:
    """Runs each trial in a fresh interpreter that refuses the network (ADR-0072 section 6).

    Each worker is a new Python process started as AlphaLab's own worker module.
    Before it imports anything of the researcher's it installs an audit hook
    that refuses the network; it then imports ``definition`` -- an
    :class:`ExperimentDefinition`, named here in code as ``"module:attribute"``
    -- holds it to the submitted experiment, and runs the one trial its request
    names by identity. A request carries identities only, never code.

    Up to ``workers`` trials run at once, and what they left is yielded in the
    order they were submitted, whatever order they finish in. A trial past the
    experiment's time limit, or a worker that dies, is interrupted: it leaves
    nothing, and a resume runs it again. A definition that does not load is the
    researcher's code failing, and each trial is recorded ``FAILED``.

    Attributes:
        definition: Where the experiment is defined, ``"package.module:NAME"``.
        workers: How many trials run at once.
    """

    definition: str
    workers: int

    def __post_init__(self) -> None:
        _require_definition(self.definition, "SpawnExecutor")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) or self.workers < 1:
            raise ExperimentError(f"SpawnExecutor.workers must be positive, got {self.workers!r}.")

    def execute(
        self,
        spec: ExperimentSpec,
        registry: RunnerRegistry,
        runner: str,
        trials: Sequence[TrialSpec],
    ) -> Iterator[_Ran]:
        registry.runner(runner)
        experiment_id = spec.experiment_id
        path = _search_path()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            running = [
                pool.submit(_spawn, self.definition, spec, experiment_id, runner, trial, path)
                for trial in trials
            ]
            for future in running:
                ran = future.result()
                if ran is not None:
                    yield ran


#: The registered task a cluster job of a :class:`ClusterExecutor` runs.
_CLUSTER_TASK: Final = "trial"


class _Interrupted(Exception):
    """A trial's time limit passed, or its worker died: its job fails, and it records nothing."""


@dataclass(frozen=True, slots=True)
class ClusterExecutor:
    """Runs each trial as a job on a research cluster: the one executor path (GAP-31).

    The trials are submitted to ``cluster`` as jobs and run by
    :func:`~alphalab.cloud_research.run_cluster_cycle` through a
    :class:`~alphalab.common.registry.RunnerRegistry`, as any registered task
    is: :mod:`alphalab.distributed`'s job lifecycle queues, starts and completes
    or fails each one; ``assign`` decides which worker takes which -- the
    cluster's own scheduler, or a policy of :mod:`alphalab.cluster_scheduler`;
    and each trial runs in a fresh interpreter that refuses the network, as
    under :class:`SpawnExecutor`. A request carries identities only.

    A cycle runs as many trials as the workers have capacity for, and what the
    trials left is yielded in submission order as each prefix of them is done.
    A trial past its time limit, or a worker that dies, fails its job and is
    interrupted: it leaves nothing, and a resume runs it again. The cluster's
    own bookkeeping stays the executor's; the ledger records the trials.

    Attributes:
        definition: Where the experiment is defined, ``"package.module:NAME"``.
        cluster: The cluster the trials run on, with no job queued or running.
        assign: The assignment policy; the cluster's own scheduler when ``None``.
        timestamp: The instant the cluster's jobs and events are stamped with.
    """

    definition: str
    cluster: CloudResearchState
    assign: AssignmentPolicy | None = None
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        _require_definition(self.definition, "ClusterExecutor")
        if not isinstance(self.cluster, CloudResearchState):
            raise ExperimentError(
                f"ClusterExecutor.cluster must be a CloudResearchState, got {self.cluster!r}."
            )
        distributed = self.cluster.distributed
        if not any(worker.capacity > 0 for worker in distributed.workers.values()):
            raise ExperimentError("ClusterExecutor.cluster has no worker to run a trial on.")
        if distributed.queued_jobs or distributed.running_jobs:
            raise ExperimentError(
                "ClusterExecutor.cluster has jobs queued or running; an experiment runs on an "
                "idle cluster, so that no job but its trials is run in its cycles."
            )
        if self.assign is not None and not callable(self.assign):
            raise ExperimentError(f"ClusterExecutor.assign must be callable, got {self.assign!r}.")
        if (
            isinstance(self.timestamp, bool)
            or not isinstance(self.timestamp, (int, float))
            or not math.isfinite(self.timestamp)
        ):
            raise ExperimentError(
                f"ClusterExecutor.timestamp must be a finite instant, got {self.timestamp!r}."
            )

    def execute(
        self,
        spec: ExperimentSpec,
        registry: RunnerRegistry,
        runner: str,
        trials: Sequence[TrialSpec],
    ) -> Iterator[_Ran]:
        registry.runner(runner)
        experiment_id = spec.experiment_id
        path = _search_path()
        by_id = {trial.trial_id: trial for trial in trials}
        # What a trial's spawning raised: a worker that loaded another experiment,
        # or anything else SpawnExecutor would let through. A cycle records it as
        # its job's failure; it is raised here, as SpawnExecutor raises it.
        raised: list[Exception] = []

        def run(trial_id: str) -> _Ran:
            try:
                ran = _spawn(self.definition, spec, experiment_id, runner, by_id[trial_id], path)
            except Exception as error:
                raised.append(error)
                raise
            if ran is None:
                raise _Interrupted(f"trial {trial_id} was interrupted")
            return ran

        tasks = RunnerRegistry().register(_CLUSTER_TASK, run)
        state = self.cluster
        pending: list[str] = []
        # The cluster's events are its own bookkeeping, and stamped from a stream
        # of their own: an experiment draws no identifier from the scope around it,
        # whichever executor runs it (ADR-0029 D6). A scope is entered around each
        # call, never across a yield, which hands control back to the ledger.
        with id_scope(None):
            for trial in trials:
                state, job_id = submit_research_job(
                    state,
                    JobType.BACKTEST,
                    _CLUSTER_TASK,
                    {"trial_id": trial.trial_id},
                    0,
                    float(self.timestamp),
                )
                pending.append(job_id)
        capacity = sum(worker.capacity for worker in state.distributed.workers.values())
        with ThreadPoolExecutor(max_workers=capacity) as pool:
            while pending:
                waiting = len(pending)
                with id_scope(None):
                    state = run_cluster_cycle(
                        state, pool, float(self.timestamp), registry=tasks, assign=self.assign
                    )
                if raised:
                    raise raised[0]
                done = state.distributed
                while pending and (
                    pending[0] in done.completed_jobs or pending[0] in done.failed_jobs
                ):
                    held = state.results.get(pending.pop(0))
                    if held is not None:
                        yield held
                unassigned = sum(1 for job in done.queued_jobs if job.job_id in pending)
                if unassigned == waiting:
                    raise ExperimentError(
                        f"The cluster's workers took none of the {waiting} trials left: its "
                        "assignment policy assigns no queued job to any worker."
                    )


#: The evidence kinds a ledger files under.
_TRIAL_KIND: Final = "trial_record"
_SERIES_KIND: Final = "capture_series"
_LOCKBOX_KIND: Final = "lockbox_read"


@dataclass(frozen=True, slots=True)
class LockboxRead:
    """One read of an experiment's lockbox, counted.

    Attributes:
        experiment_id: The experiment that read it.
        lockbox_id: The lockbox's identity: the data it holds back.
        trial_id: The trial whose candidate was evaluated over it.
        reread: Why it was read again, or ``None`` for the first read.
    """

    experiment_id: str
    lockbox_id: str
    trial_id: str
    reread: str | None


class TrialLedger:
    """An experiment's records, filed in an evidence store under their identities."""

    def __init__(self, store: EvidenceStore) -> None:
        if not isinstance(store, EvidenceStore):
            raise ExperimentError(f"A trial ledger keeps an EvidenceStore, got {store!r}.")
        self._store = store

    def find(self, trial_id: str) -> TrialRecord | None:
        """The record of ``trial_id``, or ``None`` when the ledger holds none."""

        ref = self._store.find(_TRIAL_KIND, trial_id)
        if ref is None:
            return None
        return _record_from_payload(self._store.get(ref).value)

    def keep(self, ran: _Ran) -> TrialRecord:
        """File what a trial left. The same record twice is kept once.

        Raises:
            NondeterministicTrialError: If the ledger already holds another
                record under the trial's identity.
        """

        record = ran.record
        if ran.capture is not None:
            self._store.put(
                {"instants": list(ran.capture.instants), "returns": list(ran.capture.returns)},
                kind=_SERIES_KIND,
                identity=ran.capture.series_id,
            )
        held = self.find(record.trial_id)
        if held is not None:
            if _identified(held) != _identified(record):
                raise NondeterministicTrialError(
                    record.trial_id,
                    f"{held.outcome.name} {held.result_id} against "
                    f"{record.outcome.name} {record.result_id}",
                )
            # The same record: the first is kept, its diagnostics with it.
            return held
        self._store.put(_record_payload(record), kind=_TRIAL_KIND, identity=record.trial_id)
        return record

    def series(self, series_id: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """The instants and returns filed under ``series_id``."""

        ref = self._store.find(_SERIES_KIND, series_id)
        if ref is None:
            raise ExperimentError(f"The ledger holds no series {series_id}.")
        value = self._store.get(ref).value
        return tuple(value["instants"]), tuple(value["returns"])

    def lockbox_reads(self, lockbox_id: str) -> int:
        """How many times the lockbox ``lockbox_id`` has been read."""

        return sum(
            1
            for identity in self._store.identities(_LOCKBOX_KIND)
            if identity.startswith(lockbox_id)
        )

    def keep_lockbox_read(self, read: LockboxRead) -> None:
        count = self.lockbox_reads(read.lockbox_id)
        self._store.put(
            {
                "experiment_id": read.experiment_id,
                "lockbox_id": read.lockbox_id,
                "reread": read.reread,
                "trial_id": read.trial_id,
            },
            kind=_LOCKBOX_KIND,
            identity=f"{read.lockbox_id}{count:04d}",
        )


def _record_from_payload(value: Mapping[str, Any]) -> TrialRecord:
    payload = dict(value)
    payload["outcome"] = TrialOutcome[payload["outcome"]]
    return TrialRecord(**payload)


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """What an experiment left: its trials' records in submission order, and their counts.

    Attributes:
        experiment_id: The experiment.
        trial_record_ids: Each recorded trial's ``record_id``, in submission order.
        counts: How many trials ended each recorded way.
        matrix_id: The identity of the completed trials' trial matrix (ADR-0054),
            or ``None`` with fewer than two.
    """

    experiment_id: str
    trial_record_ids: tuple[str, ...]
    counts: LedgerCount
    matrix_id: str | None

    @property
    def experiment_record_id(self) -> str:
        """SHA-256 of the record's canonical text, under :data:`EXPERIMENT_RECORD_SCHEME`."""

        return _digest_text(
            {
                "counts": self.counts,
                "experiment_id": self.experiment_id,
                "matrix_id": self.matrix_id,
                "scheme": EXPERIMENT_RECORD_SCHEME,
                "trial_record_ids": list(self.trial_record_ids),
            }
        )


def _matrix_id(captures: Sequence[CaptureRecord], labels: Sequence[str]) -> str | None:
    if len(captures) < 2:
        return None
    matrix = trial_matrix_from_captures(captures, labels=labels)
    return _digest_text(
        {
            "columns": [list(column) for column in matrix.columns],
            "instants": list(matrix.instants),
            "labels": list(matrix.labels),
            "measure": matrix.measure,
            "scheme": TRIAL_MATRIX_SCHEME,
        }
    )


def run_experiment(
    spec: ExperimentSpec,
    registry: RunnerRegistry,
    runner: str,
    ledger: TrialLedger,
    executor: TrialExecutor,
) -> ExperimentRecord:
    """Run every trial of ``spec`` the ledger has no record of, and record the experiment.

    A resume runs only what is missing, so an experiment stopped part way
    continues where it stopped. Records are kept in submission order, whatever
    order an executor finishes them in.

    Raises:
        ExperimentError: If ``runner`` is not in ``registry``.
        NondeterministicTrialError: If a trial left a record other than the one
            the ledger holds.
    """

    registry.runner(runner)
    trials = spec.trials()
    missing = [trial for trial in trials if ledger.find(trial.trial_id) is None]
    for ran in executor.execute(spec, registry, runner, missing):
        ledger.keep(ran)
    records: list[TrialRecord] = []
    for trial in trials:
        held = ledger.find(trial.trial_id)
        if held is not None:
            records.append(held)
    completed = [record for record in records if record.outcome is TrialOutcome.COMPLETED]
    captures = [_capture_of(ledger, record) for record in completed]
    return ExperimentRecord(
        experiment_id=spec.experiment_id,
        trial_record_ids=tuple(record.record_id for record in records),
        counts=LedgerCount.of(records),
        matrix_id=_matrix_id(captures, [record.label for record in completed]),
    )


def _capture_of(ledger: TrialLedger, record: TrialRecord) -> CaptureRecord:
    assert record.series_id is not None
    instants, returns = ledger.series(record.series_id)
    return CaptureRecord(
        extractor_id=record.extractor_id,
        instants=instants,
        returns=returns,
        series_id=record.series_id,
        insolvent_at=record.insolvent_at,
        steps=0,
        configuration_id=None,
        trace_id=None,
        traced=None,
    )


def read_lockbox(
    spec: ExperimentSpec,
    registry: RunnerRegistry,
    runner: str,
    ledger: TrialLedger,
    trial: TrialSpec,
) -> _Ran:
    """Evaluate ``trial``'s candidate over the whole dataset, lockbox included: once (R54-10).

    Raises:
        ExperimentError: If ``spec`` declares no lockbox, or its lockbox has been
            read and ``spec`` is not a child experiment that states why it reads
            it again.
    """

    lockbox = spec.validation.lockbox
    if lockbox is None:
        raise ExperimentError(f"Experiment {spec.name} declares no lockbox to read.")
    lockbox_id = _digest_text(
        {"dataset": spec.dataset.digest, "start": lockbox.start, "scheme": "alphalab.lockbox.v1"}
    )
    if ledger.lockbox_reads(lockbox_id) and lockbox.reread is None:
        raise ExperimentError(
            f"The lockbox from {lockbox.start} has been read. A second read is a child "
            "experiment's that states why (Lockbox.reread), and it is counted."
        )
    whole = replace(spec, validation=FullSample(None))
    ran = run_trial(whole, registry.runner(runner), trial)
    ledger.keep_lockbox_read(
        LockboxRead(spec.experiment_id, lockbox_id, trial.trial_id, lockbox.reread)
    )
    return ran


# --- a record reproduced ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrialRerun:
    """A recorded trial run again, and whether it left the record the ledger holds.

    Attributes:
        trial_id: The trial.
        held: The ``record_id`` of the record the ledger holds.
        rerun: The ``record_id`` of the record the rerun left, or ``None`` when
            the rerun was interrupted and left none.
    """

    trial_id: str
    held: str
    rerun: str | None

    @property
    def reproduced(self) -> bool:
        """Whether the rerun left the held record, every identity it covers included."""

        return self.rerun == self.held


def rerun_trial(
    spec: ExperimentSpec,
    registry: RunnerRegistry,
    runner: str,
    ledger: TrialLedger,
    trial_id: str,
    executor: TrialExecutor,
) -> TrialRerun:
    """Run one recorded trial again through ``executor``, and hold it to its record.

    A rerun files nothing: it checks a record. Through a :class:`SpawnExecutor`
    it runs in a fresh interpreter, away from the process that made the record.
    The record's ``record_id`` covers the run's ``result_id``, ``trace_id`` and
    ``provenance_id``, the series' identity and the outcome, and not the
    diagnostics. So a record is reproduced when the trial runs to the same
    result, captured alike, whatever an interpreter's error wording.

    Raises:
        ExperimentError: If ``trial_id`` is not one of ``spec``'s trials, or the
            ledger holds no record of it.
    """

    trial = next((each for each in spec.trials() if each.trial_id == trial_id), None)
    if trial is None:
        raise ExperimentError(f"{trial_id} is not a trial of experiment {spec.name}.")
    held = ledger.find(trial_id)
    if held is None:
        raise ExperimentError(f"The ledger holds no record of trial {trial_id} to rerun.")
    left = list(executor.execute(spec, registry, runner, [trial]))
    return TrialRerun(trial_id, held.record_id, left[0].record.record_id if left else None)


# --- the tracker link (D-54-3) -----------------------------------------------------------

#: The tag a tracker run cites an experiment by (D-54-3). ``experiment_tracking``
#: stays the governance registry, and its runs are linked to the ledger's
#: experiments by ``experiment_id`` in their ``tags``, until a typed field
#: replaces the tag at 5.0.
EXPERIMENT_TAG: Final = "alphalab.experiment_id"


def experiment_tags(spec: ExperimentSpec) -> dict[str, str]:
    """The tags a tracker run cites ``spec`` by (D-54-3).

    ``start_run(tracker, spec.name, parameters, timestamp, tags=experiment_tags(spec))``
    records a run as the experiment's, and :func:`tracked_runs` finds it. A
    run's own tags are merged in by the caller; the tag enters its ``run_id``,
    as every tag does.
    """

    return {EXPERIMENT_TAG: spec.experiment_id}


def tracked_runs(tracker: ExperimentTracker, experiment_id: str) -> tuple[ExperimentRun, ...]:
    """The tracker's runs that cite ``experiment_id``, in the order they were started.

    Raises:
        ExperimentError: If ``experiment_id`` is not an experiment's identity.
    """

    _require_hex(experiment_id, "An experiment_id")
    return tuple(
        run for run in tracker.runs.values() if run.tags.get(EXPERIMENT_TAG) == experiment_id
    )
