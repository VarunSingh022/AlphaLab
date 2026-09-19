"""What a table of raw rows turns out to contain, stated explicitly.

Schema detection is where an ingestion decides that *this* column is the
timestamp and *that* one is the close. Getting it wrong does not raise --- it
produces a dataset that backtests cleanly against the wrong numbers, which is
the most expensive failure mode this package has.

So the rules here are: detection **proposes**, it never assumes, and everything
it worked out is returned rather than applied. A :class:`SchemaDetection`
carries the bindings it resolved, the roles it could not fill, the roles it
could fill *two* ways, the columns it did not recognise at all, and every
assumption it made along the way. :meth:`SchemaDetection.require` is the only
thing that turns one into a usable schema, and it refuses whenever anything is
unresolved or ambiguous.

Why detection lives here and not in the backtest engine
-------------------------------------------------------

A backtest that guessed at its own input's shape would make the guess once per
run, invisibly, with no record of what it decided. The schema is a property of
the *dataset*, settled once at ingestion and carried in its provenance, so that
two runs over the same dataset cannot read it two different ways.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto

from alphalab.data.csv_source import RawTable
from alphalab.data.exceptions import DataValidationError
from alphalab.data.formats import canonical_field
from alphalab.data.time import TimestampFormat, detect_timestamp_format

__all__ = [
    "AmbiguousField",
    "DatasetSchema",
    "FieldBinding",
    "FieldRole",
    "RecordType",
    "SchemaDetection",
    "detect_schema",
]


class FieldRole(Enum):
    """What one column contributes to a canonical record."""

    TIMESTAMP = auto()
    SYMBOL = auto()
    OPEN = auto()
    HIGH = auto()
    LOW = auto()
    CLOSE = auto()
    VOLUME = auto()
    TRADE_COUNT = auto()
    BID = auto()
    ASK = auto()
    BID_SIZE = auto()
    ASK_SIZE = auto()


class RecordType(Enum):
    """Which canonical wire record a row becomes.

    Only the two shapes a delimited file can be recognised as without a
    declaration. :class:`~alphalab.data.feed.Trade` and
    :class:`~alphalab.data.feed.OrderBook` are deliberately absent: a
    ``price``/``size`` pair is indistinguishable from a partially populated bar,
    and a depth book is not a flat table at all. Both remain ingestible by a
    caller that builds the records itself; what is not offered is a *guess*.
    """

    BAR = auto()
    QUOTE = auto()


#: Canonical field name to the role it plays in a record.
#:
#: Lives here rather than in :mod:`alphalab.data.formats`, beside the enum it
#: names: ``schema`` reads ``formats`` for the alias table, so ``formats``
#: cannot read ``schema`` back without closing a module cycle.
_ROLES: Mapping[str, FieldRole] = {
    "timestamp": FieldRole.TIMESTAMP,
    "symbol": FieldRole.SYMBOL,
    "open": FieldRole.OPEN,
    "high": FieldRole.HIGH,
    "low": FieldRole.LOW,
    "close": FieldRole.CLOSE,
    "volume": FieldRole.VOLUME,
    "trade_count": FieldRole.TRADE_COUNT,
    "bid": FieldRole.BID,
    "ask": FieldRole.ASK,
    "bid_size": FieldRole.BID_SIZE,
    "ask_size": FieldRole.ASK_SIZE,
}

#: Roles a record of each type cannot be built without.
_REQUIRED: Mapping[RecordType, tuple[FieldRole, ...]] = {
    RecordType.BAR: (
        FieldRole.TIMESTAMP,
        FieldRole.OPEN,
        FieldRole.HIGH,
        FieldRole.LOW,
        FieldRole.CLOSE,
    ),
    RecordType.QUOTE: (FieldRole.TIMESTAMP, FieldRole.BID, FieldRole.ASK),
}

#: Roles a record of each type may carry, beyond the required ones.
_OPTIONAL: Mapping[RecordType, tuple[FieldRole, ...]] = {
    RecordType.BAR: (FieldRole.SYMBOL, FieldRole.VOLUME, FieldRole.TRADE_COUNT),
    RecordType.QUOTE: (FieldRole.SYMBOL, FieldRole.BID_SIZE, FieldRole.ASK_SIZE),
}


@dataclass(frozen=True, slots=True)
class FieldBinding:
    """One column, bound to the role it fills, and why."""

    role: FieldRole
    column: str
    rationale: str


@dataclass(frozen=True, slots=True)
class AmbiguousField:
    """A role two or more columns could fill, which detection will not resolve."""

    role: FieldRole
    columns: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SchemaDetection:
    """Everything detection worked out, including what it could not.

    Attributes:
        bindings: Roles that resolved to exactly one column.
        ambiguous: Roles more than one column could have filled.
        unresolved: Roles the declared record type requires and nothing filled.
        unmapped_columns: Columns matching no known field. Carried, not
            discarded: they are the dataset's own metadata columns.
        record_type: The shape detected, or declared by the caller.
        record_type_rationale: Why that shape, in words.
        timestamp_format: How the timestamp column is to be read.
        timestamp_rationale: Why, in words.
        assumptions: Conventions applied that a caller may wish to override.
    """

    bindings: tuple[FieldBinding, ...]
    ambiguous: tuple[AmbiguousField, ...]
    unresolved: tuple[FieldRole, ...]
    unmapped_columns: tuple[str, ...]
    record_type: RecordType | None
    record_type_rationale: str
    timestamp_format: TimestampFormat | None
    timestamp_rationale: str
    assumptions: tuple[str, ...] = ()

    @property
    def is_resolved(self) -> bool:
        """Whether this detection is complete enough to ingest from."""

        return (
            self.record_type is not None
            and self.timestamp_format is not None
            and not self.ambiguous
            and not self.unresolved
        )

    def column_for(self, role: FieldRole) -> str | None:
        """The column bound to ``role``, or ``None`` if none is."""

        for binding in self.bindings:
            if binding.role is role:
                return binding.column
        return None

    def require(self) -> SchemaDetection:
        """Return this detection, or refuse with everything left undecided.

        The refusal lists every unresolved and ambiguous field at once rather
        than failing on the first, because a caller fixing a header wants the
        whole list, not one item per attempt.
        """

        if self.is_resolved:
            return self

        problems: list[str] = []
        if self.record_type is None:
            problems.append(f"record type could not be determined: {self.record_type_rationale}")
        if self.timestamp_format is None:
            problems.append(f"timestamp format could not be determined: {self.timestamp_rationale}")
        problems += [
            f"{item.role.name} is ambiguous between {', '.join(repr(c) for c in item.columns)}: "
            f"{item.reason}"
            for item in self.ambiguous
        ]
        problems += [
            f"{role.name} is required but no column supplies it" for role in self.unresolved
        ]

        raise DataValidationError(
            "This table cannot be ingested without resolving:\n  - " + "\n  - ".join(problems)
        )


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    """The settled shape of a canonical dataset.

    ``fields`` and ``data_type`` are the original v1 shape and keep their
    positions and meaning. The rest record *how* that shape was arrived at, so
    a dataset can answer "which column was the close?" long after the file that
    produced it is gone.
    """

    fields: tuple[str, ...]
    data_type: str  # e.g., 'BAR', 'TRADE'
    bindings: Mapping[str, str] = field(default_factory=dict)
    timestamp_format: str | None = None
    timezone: str | None = None
    source_columns: tuple[str, ...] = ()

    @classmethod
    def from_detection(
        cls, detection: SchemaDetection, timezone_name: str | None, source_columns: Sequence[str]
    ) -> DatasetSchema:
        """Settle a resolved detection into a dataset's schema.

        Raises:
            DataValidationError: If the detection is not resolved.
        """

        resolved = detection.require()
        record_type = resolved.record_type
        timestamp_format = resolved.timestamp_format
        if record_type is None or timestamp_format is None:  # pragma: no cover - require() guards
            raise DataValidationError("A resolved detection always has both.")
        return cls(
            fields=tuple(binding.role.name.lower() for binding in resolved.bindings),
            data_type=record_type.name,
            bindings={binding.role.name: binding.column for binding in resolved.bindings},
            timestamp_format=timestamp_format.name,
            timezone=timezone_name,
            source_columns=tuple(source_columns),
        )


def detect_schema(
    table: RawTable, declared_record_type: RecordType | None = None
) -> SchemaDetection:
    """Work out what ``table`` contains, reporting everything undecided.

    Args:
        table: The rows as read, with their original column names.
        declared_record_type: The shape, when the caller knows it. A declaration
            always wins over inference -- that is what makes an otherwise
            ambiguous file ingestible -- and the rationale records that it was
            declared rather than found.
    """

    roles = _ROLES
    by_field: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for column in table.columns:
        canonical = canonical_field(column)
        if canonical in roles:
            by_field.setdefault(canonical, []).append(column)
        else:
            unmapped.append(column)

    bindings: list[FieldBinding] = []
    ambiguous: list[AmbiguousField] = []
    for canonical, columns in by_field.items():
        role = roles[canonical]
        if len(columns) == 1:
            bindings.append(
                FieldBinding(
                    role=role,
                    column=columns[0],
                    rationale=(
                        f"column {columns[0]!r} is the only one naming the canonical field "
                        f"{canonical!r}"
                    ),
                )
            )
            continue
        ambiguous.append(
            AmbiguousField(
                role=role,
                columns=tuple(columns),
                reason=(
                    f"each names the canonical field {canonical!r}, and nothing in the header "
                    "says which one the dataset should carry -- an adjusted and an unadjusted "
                    "column are the usual case, and they are different data"
                ),
            )
        )

    bound_roles = {binding.role for binding in bindings}
    record_type, record_rationale = _resolve_record_type(bound_roles, declared_record_type)

    unresolved: tuple[FieldRole, ...] = ()
    if record_type is not None:
        unresolved = tuple(
            role
            for role in _REQUIRED[record_type]
            if role not in bound_roles and role not in {item.role for item in ambiguous}
        )

    timestamp_format: TimestampFormat | None = None
    timestamp_rationale = "no column was bound to the timestamp role"
    assumptions: list[str] = []
    timestamp_column = next((b.column for b in bindings if b.role is FieldRole.TIMESTAMP), None)
    if timestamp_column is not None:
        timestamp_format, timestamp_rationale = detect_timestamp_format(
            table.column_values(timestamp_column)
        )
        if timestamp_format is TimestampFormat.EPOCH_SECONDS:
            assumptions.append(
                "numeric timestamps are read as Unix seconds, which is AlphaLab's canonical "
                "unit; declare EPOCH_MILLISECONDS if this column is in milliseconds"
            )
        if timestamp_format is TimestampFormat.DATE_ONLY:
            assumptions.append(
                "the timestamp column holds bare dates, so a DateOnlyPolicy and a timezone "
                "must be named before each one denotes an instant"
            )
        if timestamp_format is TimestampFormat.ISO_8601_NAIVE:
            assumptions.append(
                "the timestamp column carries no UTC offset, so a timezone must be named "
                "before each reading denotes an instant"
            )

    if FieldRole.SYMBOL not in bound_roles and record_type is not None:
        assumptions.append(
            "no column identifies the instrument, so every row is taken to describe the one "
            "symbol the caller names for the dataset"
        )

    return SchemaDetection(
        bindings=tuple(bindings),
        ambiguous=tuple(ambiguous),
        unresolved=unresolved,
        unmapped_columns=tuple(unmapped),
        record_type=record_type,
        record_type_rationale=record_rationale,
        timestamp_format=timestamp_format,
        timestamp_rationale=timestamp_rationale,
        assumptions=tuple(assumptions),
    )


def _resolve_record_type(
    bound: set[FieldRole], declared: RecordType | None
) -> tuple[RecordType | None, str]:
    """Decide the record shape from what resolved, or take the declaration."""

    if declared is not None:
        return declared, f"{declared.name} was declared by the caller rather than inferred"

    has_bar = {FieldRole.OPEN, FieldRole.HIGH, FieldRole.LOW, FieldRole.CLOSE} <= bound
    has_quote = {FieldRole.BID, FieldRole.ASK} <= bound

    if has_bar and has_quote:
        return None, (
            "the table carries a full OHLC set and a bid/ask pair, so it could be read as bars "
            "or as quotes; declare which"
        )
    if has_bar:
        return RecordType.BAR, "open, high, low and close all resolved, which is a bar"
    if has_quote:
        return RecordType.QUOTE, "bid and ask both resolved, which is a quote"
    return None, (
        "neither a full OHLC set nor a bid/ask pair resolved, so the table matches no "
        "record shape AlphaLab can build without being told which it is"
    )
