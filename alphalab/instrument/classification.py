"""What an instrument is classified as, who said so, and when it took effect.

v2.11 made classification writable: :func:`~alphalab.instrument.registry.classify_instrument`
sets ``InstrumentRecord.sector`` without touching the identity key, and the
pipeline freezes the label onto each ``TradeRecord.sector_id`` at fill time, so
a reclassification cannot rewrite history. Both of those are correct and are
unchanged here.

What v2.11 did not record is **who classified the instrument, from what source,
and as of when**. ADR-0027 decision 5 is titled "Provenance: two facts, two
owners, two tenses" and settles the two tenses -- present tense on the registry,
past tense frozen on the trade record -- but the present-tense side stores a
bare string. The function's own docstring says it: a reclassification happens
"silently, with no refusal and no event". So a registry could say
``sector == "Technology"`` and nothing could say whether that came from a vendor
file loaded last year, an analyst's correction last week, or a typo.

Three values, in increasing order of what they answer
-----------------------------------------------------

============================  =============================================
:class:`SectorClassification`  one act of classifying: the label, its source,
                               and the date it takes effect from
:class:`ClassificationHistory` every such act for one instrument, in order,
                               append-only
``InstrumentRecord.sector``    the label currently in effect -- unchanged
                               from v2.11, still a plain ``str | None``
============================  =============================================

``sector`` stays exactly where and what it was. Every existing reader, every
existing test and every direct ``InstrumentRecord(...)`` construction keeps
working, and ``sector`` remains outside the identity key. Provenance is recorded
*beside* it rather than inside it, which is what makes this additive.

Why the history is append-only
------------------------------
Because the alternative is the thing the architecture forbids. Overwriting a
classification in place is mutable historical attribution: after a
reclassification nobody can say what the registry previously held, or on whose
authority. An append-only log makes a correction a *new fact* rather than the
erasure of an old one, which is the same reasoning
:class:`~alphalab.common.append_log.AppendOnlyLog` is used for everywhere else
in AlphaLab and the reason ``deployment_manager``'s ledger is append-only.

Time, and the absence of a clock
--------------------------------
``as_of`` is **supplied by the caller and never read from a clock**. Nothing in
AlphaLab reads a wall clock outside the ingestion boundary (ADR-0030), and a
classification that stamped itself with ``time.time()`` would make a registry
non-reproducible and two processes building the same registry disagree.

``as_of=None`` is a first-class answer, not a missing value: it means the
operator declared a classification without saying when it takes effect, so as
far as this registry knows it has always been in effect. That is the "absent,
not fabricated" rule -- there is no epoch sentinel, because ``0.0`` would be a
date nobody chose.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Final

from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import normalize_sector_label

__all__ = [
    "OPERATOR",
    "ClassificationHistory",
    "SectorClassification",
]

#: Source recorded when a caller classifies without naming one.
#:
#: Not a sentinel for "unknown", which this package refuses elsewhere: it is an
#: accurate statement of what happened. Somebody called
#: :func:`~alphalab.instrument.registry.classify_instrument` with a label and
#: named no upstream authority, so the operator of this registry *is* the
#: source. A vendor-supplied classification names the vendor instead.
OPERATOR: Final = "operator"


@dataclass(frozen=True, slots=True)
class SectorClassification:
    """One act of classifying an instrument, and everything known about it.

    Attributes:
        sector: The label, or ``None`` when this act *un*classifies the
            instrument. Withdrawal is a classification decision like any other
            and is recorded rather than being a gap in the history.
        source: Who or what this classification came from -- a vendor name, a
            file, an analyst, a ticket. :data:`OPERATOR` when the caller named
            nothing. Never blank.
        as_of: Unix timestamp this classification takes effect from, or ``None``
            when the caller declared no effective date. Caller-supplied; nothing
            here reads a clock.

    Raises:
        InstrumentInputError: If ``source`` is blank or not a string, if
            ``sector`` is not a label
            :func:`~alphalab.instrument.record.normalize_sector_label` accepts,
            or if ``as_of`` is not a finite number.
    """

    sector: str | None
    source: str = OPERATOR
    as_of: float | None = None

    def __post_init__(self) -> None:
        if self.sector is not None:
            object.__setattr__(self, "sector", normalize_sector_label(self.sector))

        if not isinstance(self.source, str):
            raise InstrumentInputError(
                f"source must be a string, got {type(self.source).__name__}."
            )
        stripped = self.source.strip()
        if not stripped:
            raise InstrumentInputError(
                "source cannot be blank. A classification whose origin is not "
                f"recorded cannot be audited; pass {OPERATOR!r} to say that this "
                "registry's operator declared it directly."
            )
        if not stripped.isprintable():
            raise InstrumentInputError(
                f"source {self.source!r} contains a control character. Refused rather "
                "than escaped, following normalize_sector_label."
            )
        object.__setattr__(self, "source", stripped)

        if self.as_of is None:
            return
        if isinstance(self.as_of, bool) or not isinstance(self.as_of, int | float):
            raise InstrumentInputError(
                f"as_of must be a Unix timestamp or None, got {self.as_of!r}."
            )
        if self.as_of != self.as_of or self.as_of in {float("inf"), float("-inf")}:
            raise InstrumentInputError(f"as_of must be a finite timestamp, got {self.as_of!r}.")
        object.__setattr__(self, "as_of", float(self.as_of))

    @property
    def classified(self) -> bool:
        """Whether this act assigns a sector rather than withdrawing one."""

        return self.sector is not None

    def effective_at(self, timestamp: float) -> bool:
        """Whether this classification is in effect at ``timestamp``.

        An undated classification is in effect at every instant: it is the only
        thing the registry knows, and treating it as effective from nowhere
        would make it unreadable rather than undated.
        """

        return self.as_of is None or self.as_of <= timestamp


@dataclass(frozen=True, slots=True)
class ClassificationHistory:
    """Every classification one instrument has been given, in declaration order.

    Append-only. A correction is a new entry, never an edit, so the registry can
    always say what it previously held and on whose authority.

    Deliberately ordered by *declaration*, not by ``as_of``. An operator may
    backfill a classification with an earlier effective date than one already
    recorded, and re-sorting would lose the fact that the backfill came second
    -- which is exactly the fact an audit needs.

    Attributes:
        entries: The classifications, oldest declaration first.
    """

    entries: tuple[SectorClassification, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[SectorClassification]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def append(self, classification: SectorClassification) -> ClassificationHistory:
        """This history plus one more act. The original is unchanged."""

        return ClassificationHistory((*self.entries, classification))

    @property
    def current(self) -> SectorClassification | None:
        """The most recently declared classification, or ``None`` if never classified.

        "Most recently declared", not "latest effective date": the last thing
        the operator said is what the registry currently holds, which is what
        ``InstrumentRecord.sector`` reflects.
        """

        return self.entries[-1] if self.entries else None

    def at(self, timestamp: float) -> SectorClassification | None:
        """The classification in effect at ``timestamp``, or ``None``.

        The **last declared** entry among those effective at that instant. Last
        declared rather than latest-dated, so a correction supersedes the entry
        it corrects even when both carry the same effective date -- an operator
        fixing a mistake expects the fix to win.

        Linear in the history of one instrument, which is a handful of entries
        over the life of a registry. Deliberately not indexed: an index would be
        a second structure to keep true, for a scan that never runs on the
        execution path -- the pipeline reads
        :attr:`~alphalab.instrument.record.InstrumentRecord.sector`, which is
        O(1), and this answers an audit question instead.
        """

        applicable = [entry for entry in self.entries if entry.effective_at(timestamp)]
        return applicable[-1] if applicable else None

    def sector_at(self, timestamp: float) -> str | None:
        """The sector label in effect at ``timestamp``, or ``None``.

        ``None`` covers both "never classified by then" and "classification
        withdrawn by then", because both mean the same thing to a reader: this
        instrument had no sector at that instant. Which of the two it was is
        answerable from :meth:`at`.
        """

        entry = self.at(timestamp)
        return None if entry is None else entry.sector

    @property
    def sources(self) -> tuple[str, ...]:
        """Every distinct source that has classified this instrument, in first-seen order."""

        seen: dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.source, None)
        return tuple(seen)

    @classmethod
    def of(cls, classifications: Sequence[SectorClassification]) -> ClassificationHistory:
        """A history over ``classifications``, in the order given."""

        return cls(tuple(classifications))
