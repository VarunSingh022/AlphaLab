"""A registry supplied back to a restore, held to the terms its run recorded (FUT-041, phase 6).

A run names its instrument registry by type in its record (ADR-0023), and the
record is the run's identity, so the registry's terms cannot be added there
without moving every ``result_id``. A paused replay's and a live run's payload
record them instead, as :func:`~alphalab.instrument.snapshot.registry_digest`,
and so does a manifest asked to (REP-006); :func:`verify_registry` holds the
registry supplied back to that digest. A payload that recorded none -- written
before phase 6 -- is reported :attr:`RegistryVerification.UNRECORDED`: checked
by type alone, and never called verified.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from alphalab.persistence.exceptions import StateDecodeError

__all__ = ["RegistryVerification", "registry_verification", "verify_registry"]


class RegistryVerification(Enum):
    """How the instrument registry supplied to a restore was checked (FUT-041, phase 6)."""

    #: Its terms are the ones recorded: its digest is the recorded digest.
    VERIFIED = auto()
    #: The run held a registry and no digest of it was recorded -- a payload
    #: written before phase 6, or a bare run snapshot restored without one --
    #: so it was checked by type alone. Not a verification, and not reported
    #: as one.
    UNRECORDED = auto()
    #: The run held no registry.
    NONE_HELD = auto()


def verify_registry(
    held_type: str | None, recorded: str | None, supplied: Any
) -> RegistryVerification:
    """Check a registry supplied back against the digest a run recorded of its own.

    Args:
        held_type: The registry type the run recorded, ``None`` if it held none.
        recorded: The digest recorded of it
            (:func:`~alphalab.instrument.snapshot.registry_digest`), or ``None``.
        supplied: The registry supplied back.

    Raises:
        StateDecodeError: If a digest was recorded and the supplied registry's
            terms differ -- another borrow, margin, controls, settlement, tick
            or contract term -- or a digest is given for a run that held none.
    """

    from alphalab.instrument.snapshot import registry_digest

    if held_type is None:
        if recorded is not None:
            raise StateDecodeError(
                "A registry digest was given for a run that held no instrument registry."
            )
        return RegistryVerification.NONE_HELD
    if recorded is None or supplied is None:
        return RegistryVerification.UNRECORDED
    found = registry_digest(supplied)
    if found != recorded:
        raise StateDecodeError(
            f"The instrument registry supplied has terms {found}, and the run recorded "
            f"{recorded}: a registry of the right type with other instrument, borrow, margin, "
            "control, settlement or tick terms would continue the run as another one. Supply "
            "the registry the run held."
        )
    return RegistryVerification.VERIFIED


def registry_verification(snapshot: Any, registry: Any) -> RegistryVerification:
    """How a paused replay's or a live run's payload would hold ``registry`` on restore.

    ``VERIFIED`` when its terms are the recorded ones; ``UNRECORDED`` when the
    payload recorded none of a registry its run held -- written before phase 6
    -- so a restore checks it by type alone; ``NONE_HELD`` for a run that held
    none. A live restore declaring ``registry_changed`` is not held to it.

    Raises:
        StateDecodeError: If its terms are not the recorded ones.
    """

    return verify_registry(
        snapshot.run.pipeline.config.instruments_type, snapshot.instruments_digest, registry
    )
