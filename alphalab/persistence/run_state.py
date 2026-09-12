"""The identity a stored run state is addressed by, and the envelope around it.

:class:`RunStateRef` is an **identity, not a location**. It says which run and
which checkpoint, and it says nothing about where the bytes are, what encodes
them, or which backend holds them. That is the whole of ADR-0029 decision 4, and
the reason it is worth stating twice: a URI on this type would put one backend's
addressing into every caller's vocabulary, and would have to be redesigned the
first time a second backend existed.

Two families of reference
-------------------------
Surveying the repository finds ``*Ref`` used for two different jobs:

============================================  =====================================
:class:`~alphalab.model_registry.registry.ArtifactRef`,   bytes AlphaLab **never holds** --
:class:`~alphalab.enterprise.models.SecretRef`            an address plus a digest.
                                                          ``SecretRef`` says it outright:
                                                          *an address, not a value*.
:class:`~alphalab.lifecycle.identity.ModelRef`,           a typed identity for something
``StrategyVersionRef``, ``DeploymentRef``                 AlphaLab **does hold**, so two
                                                          identical ``(str, int)`` pairs
                                                          cannot be confused.
============================================  =====================================

A run-state store holds the bytes, so this is the second family, and it takes
that family's shape: validated at construction, rendered ``run_id@sequence``, and
carrying nothing a backend could disagree with.

The ``"@"`` rule and its reason are
:func:`alphalab.lifecycle.identity._validate`'s, unchanged -- a ``run_id``
containing the separator would render to a reference that parses back as a
different one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from alphalab.persistence.exceptions import PersistenceValidationError

__all__ = ["RUN_STATE_ENVELOPE_SCHEMA", "RunStateRef"]

#: Schema version of the envelope a run-state store writes *around* a payload.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.9 for the pipeline: that constant also
#: versions ``CommonEvent`` and ``BaseEvent``, so bumping it would version every
#: event in the system as a side effect of one subsystem's change.
#:
#: It versions what the store records *about* a payload -- the run it belongs
#: to, its position in that run, and the digest of its bytes -- and never the
#: payload itself. A ``PipelineSnapshot`` inside this envelope is still
#: validated by ``PIPELINE_SNAPSHOT_SCHEMA`` and decoded by the module that owns
#: it, which is the churn confinement ADR-0023 decision 1 bought and ADR-0029
#: decision 2 keeps.
#:
#: New in v2.13, so it has nothing to be compatible with: no legacy shape, no
#: migration, and no "missing means 1". See ADR-0029 decision 9.
RUN_STATE_ENVELOPE_SCHEMA: Final = 1

_SEPARATOR: Final = "@"


@dataclass(frozen=True, slots=True)
class RunStateRef:
    """Which run, and which checkpoint of it.

    Attributes:
        run_id: The run this state belongs to. **Caller-supplied and opaque**
            (ADR-0029 decision 3): AlphaLab mints no run identity, reads no
            meaning from this string, and never writes it into a captured state.
            It is the same kind of value
            :attr:`~alphalab.backtesting.state.BacktestResult.dataset_id` is, and
            ADR-0017's treatment of that value is why this one is not invented
            here.
        sequence: Which checkpoint of the run, counting from zero. Chosen by the
            caller, so what a checkpoint number *means* -- every Nth record, every
            session, once at the end -- is the caller's to decide and the store's
            only to order by.

    Raises:
        PersistenceValidationError: If ``run_id`` is empty or blank, if it
            contains ``"@"``, or if ``sequence`` is negative.
    """

    run_id: str
    sequence: int

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise PersistenceValidationError("RunStateRef.run_id cannot be empty.")
        if _SEPARATOR in self.run_id:
            raise PersistenceValidationError(
                f"RunStateRef.run_id {self.run_id!r} cannot contain {_SEPARATOR!r}; it is "
                "the separator a reference renders with, and a run_id containing it would "
                "parse back to a different reference."
            )
        # ``bool`` is an ``int``, and ``RunStateRef(run, True)`` naming checkpoint
        # 1 is a caller error worth reporting rather than silently accepting.
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise PersistenceValidationError(
                f"RunStateRef.sequence must be an integer, got {self.sequence!r}."
            )
        if self.sequence < 0:
            raise PersistenceValidationError(
                f"RunStateRef.sequence cannot be negative, got {self.sequence}."
            )

    def __str__(self) -> str:
        return f"{self.run_id}{_SEPARATOR}{self.sequence}"
