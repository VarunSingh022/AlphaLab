"""The schedule a continued run is held to, and how honestly it was checked (ADR-0053, v4.3).

A backtest's :class:`~alphalab.backtesting.schedule.RunSchedule` -- its rates,
funding, cash flows, corporate actions and delivered observations -- is a driver
input, and a run's record keeps the effects of its entries, not the entries
(ADR-0051). So the record cannot say which schedule produced it, and a run
continued from a snapshot or a checkpoint chain could be continued under
another schedule with nothing to refuse it (ledger FUT-046). A paused replay
already holds its schedule by digest; this module gives every other
continuation the same check, as :mod:`~alphalab.runtime.registry_check` does
for the instrument registry (FUT-041).

A :class:`ScheduleBinding` names a schedule by the digest of its formula
(``RUN_SCHEDULE_DIGEST_SCHEME`` in :mod:`alphalab.backtesting.schedule`), or
says the run was given none. :class:`ScheduleVerification` says how far a check
went, in the vocabulary ``RegistryVerification`` uses, and never claims more
than was checked: a matching digest shows that the schedule supplied is the
one bound, not that the schedule is authentic. Substituting a whole consistent
set -- run, schedule and binding -- is detected only by rerunning the run
(ADR-0053, answering ADR-0017).

Nothing here mints an identifier, reads a clock or knows what a schedule holds:
the binding is computed by :mod:`alphalab.backtesting`, which owns the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from alphalab.persistence.exceptions import StateDecodeError

__all__ = ["ScheduleBinding", "ScheduleVerification", "verify_schedule_binding"]


@dataclass(frozen=True, slots=True)
class ScheduleBinding:
    """The schedule a run was given, by its digest's scheme and value (ADR-0053).

    Attributes:
        scheme: The formula ``digest`` was computed by --
            ``"alphalab.run_schedule.v1"``, the formula of every digest any
            release has recorded. The scheme names the formula and is never
            part of the digest, so a digest recorded before it was named stays
            valid.
        digest: The schedule's digest, or ``None`` when the run was given no
            schedule, or an empty one -- which is how a manifest has always
            recorded "no schedule".
    """

    scheme: str
    digest: str | None

    def __post_init__(self) -> None:
        if not self.scheme:
            raise ValueError("A schedule binding names the scheme of its digest.")
        if self.digest is not None and (
            len(self.digest) != 64 or self.digest.strip("0123456789abcdef")
        ):
            raise ValueError(
                f"A schedule digest is 64 lowercase hexadecimal characters, not {self.digest!r}."
            )


class ScheduleVerification(Enum):
    """How the schedule a run continues or is checked under was held to its binding (ADR-0053).

    Each value says what was checked, and none claims more.
    """

    #: The schedule's digest is the one bound, and the run's own record of what
    #: it was delivered -- how many observations, the last of them, every
    #: surface and auction it kept (REP-009) -- agrees with the schedule.
    VERIFIED = auto()
    #: The schedule's digest is the one bound; nothing in the run was checked
    #: against the schedule itself.
    DECLARED = auto()
    #: Nothing bound a schedule -- a payload written before v4.3, or a result
    #: given no provenance -- so the schedule supplied was not checked. Not a
    #: verification, and never reported as one.
    UNRECORDED = auto()
    #: The binding says the run was given no schedule, and none was supplied.
    NONE_GIVEN = auto()


def verify_schedule_binding(
    recorded: ScheduleBinding | None, supplied: ScheduleBinding
) -> ScheduleVerification:
    """Hold the binding of the schedule supplied to the one a run recorded.

    Args:
        recorded: What the run's payload or provenance bound, or ``None`` when
            it bound nothing.
        supplied: The binding of the schedule the run is to continue under.

    Returns:
        ``UNRECORDED`` when nothing was recorded; ``NONE_GIVEN`` when the run
        was given no schedule and none is supplied; ``DECLARED`` when the
        digests agree. Never ``VERIFIED``: a digest alone does not check the
        run's own record against the schedule.

    Raises:
        StateDecodeError: If the schemes differ, or the digests do -- another
            schedule, none where the run was given one, or one where it was
            given none. Nothing is changed by a refusal.
    """

    if recorded is None:
        return ScheduleVerification.UNRECORDED
    if supplied.scheme != recorded.scheme:
        raise StateDecodeError(
            f"The run's schedule was bound under {recorded.scheme!r} and the schedule supplied "
            f"is digested under {supplied.scheme!r}; the two digests cannot be compared."
        )
    if supplied.digest == recorded.digest:
        return (
            ScheduleVerification.NONE_GIVEN
            if recorded.digest is None
            else ScheduleVerification.DECLARED
        )
    if recorded.digest is None:
        raise StateDecodeError(
            f"The run was given no schedule, and schedule {supplied.digest} was supplied; "
            "applying it would continue the run as another one."
        )
    if supplied.digest is None:
        raise StateDecodeError(
            f"The run was given schedule {recorded.digest}, and none was supplied: continuing "
            "without it would lose every input it still had to apply."
        )
    raise StateDecodeError(
        f"The run was given schedule {recorded.digest}, and schedule {supplied.digest} was "
        "supplied; continuing would apply other inputs than the run was given."
    )
