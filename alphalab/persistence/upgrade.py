"""Versioned schema upgrades: how a payload an older build wrote is read by this one.

Why this exists
---------------
Until v3.10 every snapshot subsystem read exactly the schema version its build
wrote, and :func:`~alphalab.persistence.decode.require_schema_version` said so:
"There is no migration path; read it with the build that wrote it." That was a
defensible rule while the schemas were young and every change was a decision
someone made in the same release. It stops being one for a library meant to be
frozen and used for years: the first fix that needs a new durable field would
make every earlier payload unreadable, which is a strong incentive not to make
the fix.

The rule now
------------
A subsystem declares a :class:`SchemaHistory`: its current version and one
:class:`SchemaStep` per earlier version, each taking a payload from version
``n`` to ``n + 1``. Steps are

* **explicit** -- one function per step, written and reviewed like any other
  code, with a sentence saying what changed;
* **pure** -- a step receives a deep copy of plain JSON primitives and returns
  new ones; it reads no clock, no registry and no global state;
* **composable** -- a version-1 payload read by a version-4 build passes through
  steps 1->2, 2->3 and 3->4 in order, and nothing else;
* **honest** -- a step supplies a value only when it is what the older payload
  already meant (a field its writer could not have had, whose absence *is* the
  value). When no honest value exists the step **refuses**, with
  :class:`SchemaUpgradeRefused` saying why, rather than inventing one. A step
  that must leave a recorded fact behind -- because the newer schema has no way
  to state it -- warns with :class:`SchemaUpgradeWarning`, naming what was not
  carried and why.

Upgrades run **before** typed decoding, on primitives, so a subsystem's decoder
only ever reads its current schema. Every historical version is kept readable
by golden payloads in ``tests/fixtures/snapshots``, written by the release that
wrote the version.

This is the replacement for the "No migration framework" boundary the roadmap
held until v3.10 (ledger items PER-001 and BDY-007).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from alphalab.persistence.exceptions import StateDecodeError

__all__ = [
    "SchemaHistory",
    "SchemaStep",
    "SchemaUpgradeRefused",
    "SchemaUpgradeWarning",
    "declared_version",
]

#: What an upgrade step does: a payload at version ``n`` in, version ``n + 1`` out.
Upgrade = Callable[[dict[str, Any]], dict[str, Any]]


class SchemaUpgradeRefused(StateDecodeError):
    """A payload's version is known, and no honest upgrade from it exists."""


class SchemaUpgradeWarning(UserWarning):
    """An upgrade could not carry a recorded fact into the newer schema.

    Raised as a warning rather than an error because the payload *is* readable
    and the newer state *is* what the older run did -- the classic case is a
    limit the older build accepted and never enforced -- but the fact that it
    was stated is not preserved, and the reader must be told.
    """


def declared_version(payload: Mapping[str, Any], subsystem: str) -> int:
    """The payload's ``schema_version``, or raise naming what is wrong with it."""

    if "schema_version" not in payload:
        raise StateDecodeError(f"{subsystem} snapshot payload is missing 'schema_version'")
    version = payload["schema_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise StateDecodeError(
            f"{subsystem} snapshot schema_version is not an integer: {version!r}"
        )
    return version


@dataclass(frozen=True, slots=True)
class SchemaStep:
    """One step of a subsystem's schema history: version ``from_version`` to the next.

    Attributes:
        from_version: The version this step reads.
        change: What the next version changed, in one sentence.
        upgrade: The pure function performing the step, or ``None`` when no
            honest upgrade exists.
        refusal: Why a payload at ``from_version`` cannot be upgraded. Required
            exactly when ``upgrade`` is ``None``.
    """

    from_version: int
    change: str
    upgrade: Upgrade | None = None
    refusal: str | None = None

    def __post_init__(self) -> None:
        if (self.upgrade is None) == (self.refusal is None):
            raise ValueError(
                f"Schema step from version {self.from_version} must have exactly one of an "
                "upgrade function and a refusal reason."
            )
        if not self.change.strip():
            raise ValueError(f"Schema step from version {self.from_version} states no change.")

    @property
    def to_version(self) -> int:
        """The version this step writes."""

        return self.from_version + 1


@dataclass(frozen=True, slots=True)
class SchemaHistory:
    """Every schema version a subsystem has written, and how to read each.

    Attributes:
        subsystem: Named in every refusal.
        current: The version this build writes and its decoder reads.
        steps: One step per version from :attr:`first_version` up to
            ``current - 1``, in order.
        first_version: The earliest version any release wrote.

    Raises:
        ValueError: If the steps do not cover every earlier version exactly
            once, in order.
    """

    subsystem: str
    current: int
    steps: tuple[SchemaStep, ...] = ()
    first_version: int = 1

    def __post_init__(self) -> None:
        expected = list(range(self.first_version, self.current))
        found = [step.from_version for step in self.steps]
        if found != expected:
            raise ValueError(
                f"The {self.subsystem} schema history must have one step for each of "
                f"versions {expected}, in order; it has {found}."
            )

    @property
    def readable(self) -> tuple[int, ...]:
        """Every version a payload may declare and still be read (or refused honestly)."""

        return tuple(range(self.first_version, self.current + 1))

    def read(self, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], int]:
        """``payload`` brought to :attr:`current`, and the version it declared.

        For a decoder whose reading of one field depends on where the payload
        started -- the pipeline's strategy records, where a version-1 payload's
        silence means "never asked" and a later one's means "malformed".
        """

        return self.upgrade(payload), declared_version(payload, self.subsystem)

    def upgrade(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """``payload`` brought to :attr:`current`, or raise.

        A payload already at :attr:`current` is returned as it is. Anything
        older is deep-copied and passed through each step in order; the input
        is never modified.

        Raises:
            StateDecodeError: If the payload declares no version, a non-integer
                one, or one outside :attr:`readable`.
            SchemaUpgradeRefused: If a step on the way has no honest upgrade.
        """

        version = declared_version(payload, self.subsystem)
        if version == self.current:
            return payload
        if version not in self.readable:
            known = ", ".join(str(item) for item in self.readable)
            raise StateDecodeError(
                f"{self.subsystem} snapshot declares schema version {version}, and this build "
                f"reads versions {known}. A version newer than this build is read by the "
                "build that wrote it; one older than the first release that wrote this "
                "subsystem never existed."
            )

        data: dict[str, Any] = copy.deepcopy(dict(payload))
        for step in self.steps[version - self.first_version :]:
            if step.upgrade is None:
                raise SchemaUpgradeRefused(
                    f"{self.subsystem} snapshot schema version {step.from_version} cannot be "
                    f"upgraded to {step.to_version} ({step.change}): {step.refusal}"
                )
            data = step.upgrade(data)
            data["schema_version"] = step.to_version
        return data
