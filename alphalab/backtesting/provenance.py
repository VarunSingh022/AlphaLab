"""Run provenance: what a run was given that its record does not name (ADR-0053, v4.3).

A run's ``result_id`` is the digest of its complete record (ADR-0049), and the
record keeps the effects of the inputs a driver applied, not the inputs: a
:class:`~alphalab.backtesting.schedule.RunSchedule`'s rates leave no event of
their own, and the instrument registry is recorded by type (ADR-0023). Putting
either into the record would move every identity, so neither is put there. A
:class:`RunProvenance` records them beside the run instead: a content-addressed
record naming its subject -- a finished run by its ``result_id``, or a snapshot
or checkpoint payload by the digest of its own text -- and what the subject was
given, the effective schedule by its
:class:`~alphalab.runtime.schedule_check.ScheduleBinding` and the registry by
its digest. It is evidence, filed in an
:class:`~alphalab.model_registry.evidence.EvidenceStore` like a manifest, and
:func:`verify_provenance` re-derives every digest it states before saying what
it shows.

Provenance comes from plain functions -- :func:`run_provenance`,
:func:`payload_provenance` -- never from a driver flag: no driver's signature
or return type changes. They draw no identifier, read no clock, and never
serialize a run a second time: a result's id is taken from the caller where it
was already computed (``digest_run(result).result_id``), and a payload's digest
is of the payload's own text.

What it proves, and what it does not (answering ADR-0017)
---------------------------------------------------------
Provenance is tamper-evident, not substitution-proof. Attaching it to another
run or payload, supplying another schedule or another registry, or editing it
is detected, because each digest is recomputed and the record's own identity
covers its fields. Anyone who can run code can produce a consistent set of run,
schedule and provenance; only a rerun detects that, and a matching digest is
not a statement that the schedule is authentic or was independently verified.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from alphalab.alt_data.streaming import ObservationDelivery
from alphalab.backtesting.exceptions import BacktestError
from alphalab.backtesting.schedule import RunSchedule, ScheduleKind
from alphalab.backtesting.state import BacktestResult
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.persistence.decode import as_int, as_mapping, as_optional_str, as_str, require
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import serialize
from alphalab.persistence.upgrade import SchemaHistory
from alphalab.runtime.run import RunState
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.schedule_check import ScheduleBinding, ScheduleVerification

__all__ = [
    "RUN_PROVENANCE_HISTORY",
    "RUN_PROVENANCE_SCHEMA",
    "RUN_PROVENANCE_SCHEME",
    "ProvenanceError",
    "RunProvenance",
    "SubjectKind",
    "delivery_disagreement",
    "payload_provenance",
    "provenance_from_primitives",
    "run_provenance",
    "verify_provenance",
]

#: The identity scheme of a :class:`RunProvenance`, rendered first in its canonical key.
RUN_PROVENANCE_SCHEME: Final = "alphalab.run_provenance.v1"

#: Version of a stored provenance record's fields; the first, in v4.3.
RUN_PROVENANCE_SCHEMA: Final = 1

#: Every provenance version a release wrote, each read by this one.
RUN_PROVENANCE_HISTORY: Final = SchemaHistory("run provenance", RUN_PROVENANCE_SCHEMA)


class SubjectKind:
    """What a :class:`RunProvenance` is about. Plain strings, so the record stays JSON."""

    #: A finished run, by its ``result_id``.
    RESULT: Final = "result"
    #: A run snapshot's or a checkpoint's payload, by the SHA-256 of its exact text.
    PAYLOAD: Final = "payload"


_SUBJECT_KINDS: Final = frozenset({SubjectKind.RESULT, SubjectKind.PAYLOAD})


class ProvenanceError(BacktestError):
    """Provenance that does not describe the run, payload or schedule it is checked against.

    Nothing is changed by the refusal; supplying the right subject, schedule or
    provenance and checking again is always possible.
    """


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode(DEFAULT_ENCODING)).hexdigest()


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """What a run or a payload was given that its record does not name (ADR-0053).

    Attributes:
        subject_kind: ``"result"`` or ``"payload"`` (:class:`SubjectKind`).
        subject_digest: For a result, its ``result_id``; for a payload, the
            SHA-256 of its exact text -- for a finished run's snapshot the two
            are the same digest.
        schedule: The binding of the effective schedule the run was given --
            the one its drivers merge observations into -- or of none.
        instruments_digest: The digest of the instrument registry the run held
            (:func:`~alphalab.instrument.snapshot.registry_digest`), or ``None``
            for one that held none or a payload read without its registry.
        produced_at: When the record was made, as the caller says; kept and
            never part of the identity, which describes the run and not the
            moment it was described.
        scheme: The identity scheme, :data:`RUN_PROVENANCE_SCHEME`.
        schema_version: The stored record's version, :data:`RUN_PROVENANCE_SCHEMA`.
    """

    subject_kind: str
    subject_digest: str
    schedule: ScheduleBinding
    instruments_digest: str | None = None
    produced_at: float | None = field(default=None, compare=False)
    scheme: str = RUN_PROVENANCE_SCHEME
    schema_version: int = RUN_PROVENANCE_SCHEMA

    def __post_init__(self) -> None:
        if self.subject_kind not in _SUBJECT_KINDS:
            raise ValueError(
                f"A provenance subject is one of {sorted(_SUBJECT_KINDS)}, not "
                f"{self.subject_kind!r}."
            )
        for name in ("subject_digest", "instruments_digest"):
            value = getattr(self, name)
            if value is not None and (len(value) != 64 or value.strip("0123456789abcdef")):
                raise ValueError(f"{name} is a SHA-256 digest in lowercase hex, not {value!r}.")

    @property
    def canonical_key(self) -> str:
        """What :attr:`provenance_id` hashes: one ``label=value`` line per field, in order.

        ``produced_at`` is not in it; ``instruments`` appears only when a
        registry digest is recorded.
        """

        lines = [
            self.scheme,
            f"subject.kind={self.subject_kind}",
            f"subject.digest={self.subject_digest}",
            f"schedule.scheme={self.schedule.scheme}",
            f"schedule.digest={self.schedule.digest}",
        ]
        if self.instruments_digest is not None:
            lines.append(f"instruments={self.instruments_digest}")
        return "\n".join(lines)

    @property
    def provenance_id(self) -> str:
        """SHA-256 of :attr:`canonical_key`: the record's identity."""

        return _sha256(self.canonical_key)

    @property
    def evidence_kind(self) -> str:
        """What kind of evidence this is, for an evidence store."""

        return "run_provenance"

    @property
    def evidence_identity(self) -> str:
        """What identifies it there: its :attr:`provenance_id`."""

        return self.provenance_id


def provenance_from_primitives(payload: Mapping[str, Any]) -> RunProvenance:
    """The provenance record ``serialize`` wrote, read back and checked field by field.

    Raises:
        StateDecodeError: If a field is missing or of the wrong type, the
            version is one this build does not read, or a value is not one a
            :class:`RunProvenance` can hold.
    """

    payload = RUN_PROVENANCE_HISTORY.upgrade(as_mapping(payload, "run provenance"))
    schedule = as_mapping(require(payload, "schedule"), "schedule")
    produced = require(payload, "produced_at")
    if produced is not None and (
        isinstance(produced, bool) or not isinstance(produced, int | float)
    ):
        raise StateDecodeError(f"produced_at is a number or null, not {produced!r}.")
    try:
        return RunProvenance(
            subject_kind=as_str(require(payload, "subject_kind"), "subject_kind"),
            subject_digest=as_str(require(payload, "subject_digest"), "subject_digest"),
            schedule=ScheduleBinding(
                as_str(require(schedule, "scheme"), "schedule.scheme"),
                as_optional_str(require(schedule, "digest"), "schedule.digest"),
            ),
            instruments_digest=as_optional_str(
                require(payload, "instruments_digest"), "instruments_digest"
            ),
            produced_at=None if produced is None else float(produced),
            scheme=as_str(require(payload, "scheme"), "scheme"),
            schema_version=as_int(require(payload, "schema_version"), "schema_version"),
        )
    except ValueError as refused:
        raise StateDecodeError(f"run provenance: {refused}") from None


def _effective(
    schedule: RunSchedule | None, observations: Iterable[ObservationDelivery[Any]]
) -> RunSchedule | None:
    """The schedule a driver applies: ``schedule`` with ``observations`` merged in."""

    added = tuple(observations)
    if schedule is None:
        return RunSchedule.of(observations=added) if added else None
    return schedule.with_observations(added)


def _binding(schedule: RunSchedule | None) -> ScheduleBinding:
    return (schedule if schedule is not None else RunSchedule()).binding


def delivery_disagreement(run: RunState, schedule: RunSchedule | None) -> str | None:
    """Why ``schedule`` cannot be the one ``run`` was given, read from the run's own record.

    A run records how many observations it delivered and the last one's
    delivery id, and keeps every volatility surface and auction publication it
    was delivered, each by source and version (REP-009). A schedule claimed for
    it must hold every one of them. Nothing in a record says whether a run was
    given rates or settlements, so those are taken as given. ``None`` when the
    record and the schedule agree.
    """

    pipeline = run.pipeline
    kept = [
        (ScheduleKind.SURFACE, f"{o.source}|{o.version}|{underlying}")
        for underlying, items in pipeline.risk.surfaces.items()
        for o in items
    ] + [
        (ScheduleKind.AUCTION, f"{d.source}|{d.version}|{asset}")
        for asset, items in pipeline.market.auctions.items()
        for d in items
    ]
    if kept:
        if schedule is None:
            return (
                f"The run was delivered {len(kept)} surfaces or auction publications and no "
                "schedule was given: a manifest naming none would claim the run read no input "
                "beyond its dataset."
            )
        held = {(entry.kind, entry.identity) for entry in schedule.entries}
        missing = [identity for identity in kept if identity not in held]
        if missing:
            kind, identity = missing[0]
            return (
                f"The run was delivered {kind.value} {identity!r}, which the schedule does not "
                "hold; it is not the schedule this run was given."
            )
    if schedule is None:
        return None
    delivered = {
        entry.identity for entry in schedule.entries if entry.kind is ScheduleKind.OBSERVATION
    }
    if run.observations_delivered > len(delivered):
        return (
            f"The run delivered {run.observations_delivered} observations and the schedule "
            f"holds {len(delivered)}: a manifest naming it would claim inputs the run did "
            "not read."
        )
    last = run.last_observation
    if last is not None and last[1] not in delivered:
        return (
            f"The run's last delivery was {last[1]!r}, which the schedule does not hold; "
            "it is not the schedule this run was given."
        )
    return None


def _provenance_disagreement(run: RunState, schedule: RunSchedule | None) -> str | None:
    """Why ``schedule`` cannot be ``run``'s, as provenance reads it: stricter than a manifest.

    :func:`delivery_disagreement`, and one rule more. A run that delivered
    observations was given a schedule holding them, so a binding of none for it
    is refused. A manifest naming no schedule for such a run is not refused, as
    it never was in 4.x: refusing it would change what an existing call
    computes. Provenance is new, and claims no more than the run's record allows.
    """

    if (schedule is None or not schedule) and run.observations_delivered:
        return (
            f"The run delivered {run.observations_delivered} observations, and the provenance "
            "binds no schedule: they came from a schedule the run was given."
        )
    return delivery_disagreement(run, schedule)


def _registry_digest(run: RunState) -> str | None:
    from alphalab.instrument.snapshot import registry_digest

    registry = run.config.pipeline.instruments
    return None if registry is None else registry_digest(registry)


def run_provenance(
    result: BacktestResult,
    *,
    schedule: RunSchedule | None,
    observations: Iterable[ObservationDelivery[Any]] = (),
    result_id: str | None = None,
    produced_at: float | None = None,
) -> RunProvenance:
    """The provenance of a finished run: its schedule and registry, bound to its ``result_id``.

    Args:
        result: The finished run.
        schedule: The schedule the run was given, or ``None``. Bound as the
            drivers apply it, with ``observations`` merged in (ADR-0053 D-53-4).
        observations: The observations passed to the driver beside the
            schedule, if any.
        result_id: The run's id where it was already computed --
            ``digest_run(result).result_id`` -- so the run is serialized once
            (R53-7). Computed here when not given. Not checked here:
            :func:`verify_provenance` recomputes it.
        produced_at: When the record is made, kept and never hashed.

    Raises:
        ProvenanceError: If the run's own record contradicts the schedule --
            observations, a last delivery, a surface or an auction publication
            it does not hold. Provenance never records a schedule its run
            refutes.
    """

    effective = _effective(schedule, observations)
    disagreement = _provenance_disagreement(result.run, effective)
    if disagreement is not None:
        raise ProvenanceError(disagreement)
    subject = result_id if result_id is not None else _sha256(serialize(capture_run(result.run)))
    return RunProvenance(
        subject_kind=SubjectKind.RESULT,
        subject_digest=subject,
        schedule=_binding(effective),
        instruments_digest=_registry_digest(result.run),
        produced_at=produced_at,
    )


def payload_provenance(
    payload: str,
    *,
    schedule: RunSchedule | None,
    observations: Iterable[ObservationDelivery[Any]] = (),
    instruments_digest: str | None = None,
    produced_at: float | None = None,
) -> RunProvenance:
    """The provenance of a run snapshot's or a checkpoint's payload, bound to its own text.

    For a payload stored beside its provenance -- a bare run snapshot, which
    records no schedule, or a checkpoint written without one. The subject
    digest is the SHA-256 of ``payload`` exactly as stored, so nothing is
    serialized again. ``instruments_digest`` is the held registry's digest, if
    the caller recorded it (FUT-041). The binding is given to
    :meth:`~alphalab.backtesting.schedule.ScheduleCursor.start` as ``expected``
    when the run is continued.
    """

    return RunProvenance(
        subject_kind=SubjectKind.PAYLOAD,
        subject_digest=_sha256(payload),
        schedule=_binding(_effective(schedule, observations)),
        instruments_digest=instruments_digest,
        produced_at=produced_at,
    )


def verify_provenance(
    subject: BacktestResult | str,
    provenance: RunProvenance | None,
    schedule: RunSchedule | None,
    *,
    observations: Iterable[ObservationDelivery[Any]] = (),
    result_id: str | None = None,
) -> ScheduleVerification:
    """Check ``provenance`` against its subject and the schedule supplied, and say what it shows.

    Args:
        subject: A finished run, or a payload's exact text.
        provenance: The record to check, or ``None`` for a subject that has
            none -- which is reported, never treated as checked.
        schedule: The schedule claimed for the subject, with ``observations``
            merged as the drivers merge them.
        observations: As for :func:`run_provenance`.
        result_id: A result's id where it was already computed; recomputed
            here when not given.

    Returns:
        ``UNRECORDED`` for no provenance. Otherwise, once the subject's digest
        and the schedule's binding are found to be the recorded ones:
        ``VERIFIED`` for a result whose own delivery record agrees with the
        schedule, ``DECLARED`` for a payload, whose record is not read here,
        and ``NONE_GIVEN`` for a subject bound to no schedule and given none.

    Raises:
        ProvenanceError: If the provenance is of another subject -- another
            kind, another run, another payload -- or binds another schedule, or
            none where one is supplied, or one where none is; or if a result's
            registry is not the one recorded, or its own record contradicts the
            schedule.
    """

    if provenance is None:
        return ScheduleVerification.UNRECORDED
    effective = _effective(schedule, observations)
    if isinstance(subject, str):
        kind, digest = SubjectKind.PAYLOAD, _sha256(subject)
    else:
        kind = SubjectKind.RESULT
        digest = (
            result_id if result_id is not None else _sha256(serialize(capture_run(subject.run)))
        )
    if provenance.subject_kind != kind or provenance.subject_digest != digest:
        raise ProvenanceError(
            f"The provenance is of {provenance.subject_kind} {provenance.subject_digest}, and "
            f"it was checked against {kind} {digest}: it describes another subject."
        )
    supplied = _binding(effective)
    if supplied != provenance.schedule:
        raise ProvenanceError(
            f"The provenance binds schedule {provenance.schedule.digest} under "
            f"{provenance.schedule.scheme!r}, and the schedule supplied is "
            f"{supplied.digest} under {supplied.scheme!r}: it is not the schedule the subject "
            "was given."
        )
    if isinstance(subject, str):
        return (
            ScheduleVerification.NONE_GIVEN
            if supplied.digest is None
            else ScheduleVerification.DECLARED
        )
    held = _registry_digest(subject.run)
    if held != provenance.instruments_digest:
        raise ProvenanceError(
            f"The provenance records registry {provenance.instruments_digest}, and the run "
            f"held {held}: it describes a run under other terms."
        )
    disagreement = _provenance_disagreement(subject.run, effective)
    if disagreement is not None:
        raise ProvenanceError(disagreement)
    return (
        ScheduleVerification.NONE_GIVEN
        if supplied.digest is None
        else ScheduleVerification.VERIFIED
    )
