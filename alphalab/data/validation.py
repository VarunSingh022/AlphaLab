"""What is wrong with the data, stated one finding at a time.

Validation here never repairs and never decides. It reads rows -- or the
records built from them -- and returns :class:`ValidationFinding` s: what was
found, how bad it is, and exactly where. Acting on a finding is
:mod:`alphalab.data.cleaning`'s job, under a policy the caller chose, and
summarising them is :mod:`alphalab.data.quality`'s.

The split matters. A single "is this data good?" function would have to encode
a threshold, and a threshold is a policy: one desk's unusable file is another's
ordinary Monday. Findings are facts; policies act on them.

Two levels
----------

* **Row level** (:func:`coerce_row`) -- can these strings become a record at
  all? A price that will not parse, a timestamp in an unreadable shape, a
  missing required field. A row that fails here becomes a
  :class:`RowRejection` and never reaches the dataset.
* **Set level** (:func:`validate_records`) -- is the *series* coherent? Order,
  duplicates, OHLC relationships, frequency consistency. These need the whole
  sequence and cannot be seen one row at a time.

Validating a *state transition* -- may this dataset enter this catalogue? -- is
a different question about a different subject, and lives with the transitions
in :mod:`alphalab.data.manager`. Keeping it here would make this module import
``dataset`` and ``state``, which import back through ``quality``, closing a
module cycle for a function that is not about rows at all.

Every finding carries a :class:`FindingKind`, which is what lets a test assert
that a specific defect was detected rather than merely that something raised.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto
from itertools import pairwise

from alphalab.data.csv_source import RawRow, RawTable
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar, CanonicalRecord, Quote
from alphalab.data.schema import FieldRole, RecordType, SchemaDetection
from alphalab.data.time import DateOnlyPolicy, TimeFrequency, parse_timestamp

__all__ = [
    "FindingKind",
    "RowRejection",
    "Severity",
    "ValidationFinding",
    "coerce_row",
    "validate_records",
]


class Severity(Enum):
    """How much a finding matters.

    ``ERROR`` means the record cannot be trusted as it stands; ``WARNING``
    means it is usable but something about it is worth knowing. Neither implies
    an action -- a policy decides that.
    """

    WARNING = auto()
    ERROR = auto()


class FindingKind(Enum):
    """The specific defect found. One member per thing that can be wrong."""

    #: A required field held no value.
    MISSING_VALUE = auto()

    #: A numeric field held something that is not a number.
    NON_NUMERIC = auto()

    #: A numeric field held NaN or an infinity.
    NON_FINITE = auto()

    #: The timestamp could not be read in the declared format.
    UNPARSEABLE_TIMESTAMP = auto()

    #: The row's field count disagreed with the header.
    MALFORMED_ROW = auto()

    #: The instrument column was empty.
    EMPTY_SYMBOL = auto()

    #: Two records share an instrument and an instant.
    DUPLICATE_TIMESTAMP = auto()

    #: A record's timestamp precedes the one before it.
    OUT_OF_ORDER = auto()

    #: high < low, or open/close outside the high-low range.
    INVALID_OHLC = auto()

    #: A price was zero or negative.
    NON_POSITIVE_PRICE = auto()

    #: A volume or size was negative.
    NEGATIVE_VOLUME = auto()

    #: A quote's bid was at or above its ask.
    CROSSED_QUOTE = auto()

    #: The spacing between records is not consistent with one frequency.
    INCONSISTENT_FREQUENCY = auto()


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """One thing that is wrong, and where.

    Attributes:
        kind: Which defect.
        severity: How much it matters.
        line_number: 1-based source line, where the finding belongs to a row.
        column: Source column, where the finding belongs to a field.
        detail: The finding in words, including the offending value.
    """

    kind: FindingKind
    severity: Severity
    line_number: int | None
    column: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class RowRejection:
    """A row that could not become a record, kept with the reasons why."""

    line_number: int
    values: tuple[str, ...]
    findings: tuple[ValidationFinding, ...]


def coerce_row(
    table: RawTable,
    row: RawRow,
    detection: SchemaDetection,
    timezone_name: str | None,
    date_policy: DateOnlyPolicy | None,
    default_symbol: str | None,
) -> tuple[CanonicalRecord | None, tuple[ValidationFinding, ...]]:
    """Turn one raw row into a canonical wire record, or explain why not.

    Returns ``(record, findings)``. A ``None`` record means the row is
    rejected; the findings say what made it unusable. Findings may also be
    returned alongside a *successful* record -- a zero volume is worth knowing
    and does not stop the row becoming a bar.

    Args:
        table: The table the row came from, for column lookup.
        row: The row to read.
        detection: The resolved schema binding roles to columns.
        timezone_name: Zone for naive timestamps, or ``None`` where the format
            carries its own offset.
        date_policy: Which instant a bare date denotes, or ``None``.
        default_symbol: Instrument name for tables with no symbol column.
    """

    record_type = detection.record_type
    timestamp_format = detection.timestamp_format
    if record_type is None or timestamp_format is None:
        raise DataValidationError(
            "A row cannot be coerced under an unresolved schema; call SchemaDetection.require()."
        )

    findings: list[ValidationFinding] = []
    values = table.as_mapping(row)

    def read(role: FieldRole) -> str | None:
        column = detection.column_for(role)
        return None if column is None else values.get(column)

    timestamp_column = detection.column_for(FieldRole.TIMESTAMP) or "timestamp"
    raw_timestamp = read(FieldRole.TIMESTAMP)
    if raw_timestamp is None or not raw_timestamp.strip():
        findings.append(
            ValidationFinding(
                FindingKind.MISSING_VALUE,
                Severity.ERROR,
                row.line_number,
                timestamp_column,
                "the timestamp is empty, so the row names no instant",
            )
        )
        return None, tuple(findings)

    try:
        timestamp = parse_timestamp(raw_timestamp, timestamp_format, timezone_name, date_policy)
    except DataValidationError as error:
        findings.append(
            ValidationFinding(
                FindingKind.UNPARSEABLE_TIMESTAMP,
                Severity.ERROR,
                row.line_number,
                timestamp_column,
                f"{raw_timestamp!r} could not be read as {timestamp_format.name}: {error}",
            )
        )
        return None, tuple(findings)

    symbol_column = detection.column_for(FieldRole.SYMBOL)
    if symbol_column is not None:
        symbol = (values.get(symbol_column) or "").strip()
        if not symbol:
            findings.append(
                ValidationFinding(
                    FindingKind.EMPTY_SYMBOL,
                    Severity.ERROR,
                    row.line_number,
                    symbol_column,
                    "the instrument column is empty, so the row names no instrument",
                )
            )
            return None, tuple(findings)
    elif default_symbol is None or not default_symbol.strip():
        findings.append(
            ValidationFinding(
                FindingKind.MISSING_VALUE,
                Severity.ERROR,
                row.line_number,
                None,
                "the table has no instrument column and no symbol was named for the dataset",
            )
        )
        return None, tuple(findings)
    else:
        symbol = default_symbol.strip()

    numbers: dict[FieldRole, float] = {}
    required = _numeric_roles(record_type, required_only=True)
    optional = _numeric_roles(record_type, required_only=False)

    for role in required + optional:
        column = detection.column_for(role)
        if column is None:
            continue
        raw = (values.get(column) or "").strip()
        if not raw:
            if role in required:
                findings.append(
                    ValidationFinding(
                        FindingKind.MISSING_VALUE,
                        Severity.ERROR,
                        row.line_number,
                        column,
                        f"{role.name} is required for a {record_type.name} and is empty",
                    )
                )
                return None, tuple(findings)
            continue
        try:
            number = float(raw)
        except ValueError:
            findings.append(
                ValidationFinding(
                    FindingKind.NON_NUMERIC,
                    Severity.ERROR,
                    row.line_number,
                    column,
                    f"{raw!r} is not a number, and coercing it would invent a value",
                )
            )
            return None, tuple(findings)
        if not math.isfinite(number):
            findings.append(
                ValidationFinding(
                    FindingKind.NON_FINITE,
                    Severity.ERROR,
                    row.line_number,
                    column,
                    f"{raw!r} is not a finite number",
                )
            )
            return None, tuple(findings)
        numbers[role] = number

    if record_type is RecordType.BAR:
        record: CanonicalRecord = Bar(
            symbol=symbol,
            timestamp=timestamp,
            open=numbers[FieldRole.OPEN],
            high=numbers[FieldRole.HIGH],
            low=numbers[FieldRole.LOW],
            close=numbers[FieldRole.CLOSE],
            volume=numbers.get(FieldRole.VOLUME, 0.0),
        )
    else:
        record = Quote(
            symbol=symbol,
            timestamp=timestamp,
            bid=numbers[FieldRole.BID],
            ask=numbers[FieldRole.ASK],
            bid_size=numbers.get(FieldRole.BID_SIZE, 0.0),
            ask_size=numbers.get(FieldRole.ASK_SIZE, 0.0),
        )

    return record, tuple(findings)


def _numeric_roles(record_type: RecordType, required_only: bool) -> tuple[FieldRole, ...]:
    if record_type is RecordType.BAR:
        if required_only:
            return (FieldRole.OPEN, FieldRole.HIGH, FieldRole.LOW, FieldRole.CLOSE)
        return (FieldRole.VOLUME, FieldRole.TRADE_COUNT)
    if required_only:
        return (FieldRole.BID, FieldRole.ASK)
    return (FieldRole.BID_SIZE, FieldRole.ASK_SIZE)


def validate_records(
    records: Sequence[CanonicalRecord], expected_frequency: TimeFrequency | None
) -> tuple[ValidationFinding, ...]:
    """Check a record *series* for the defects only the series can show.

    Args:
        records: The records built from the source, in source order. Order is
            not assumed to be chronological -- detecting that it is not is one
            of the points of this function.
        expected_frequency: The frequency the caller believes the series has,
            if any. When given, spacings that disagree are reported; when
            ``None``, no frequency finding is produced, because a series with
            no declared frequency cannot be inconsistent with one.
    """

    findings: list[ValidationFinding] = []
    seen: set[tuple[str, float]] = set()
    previous: dict[str, float] = {}

    for record in records:
        key = (record.symbol, record.timestamp)
        if key in seen:
            findings.append(
                ValidationFinding(
                    FindingKind.DUPLICATE_TIMESTAMP,
                    Severity.ERROR,
                    None,
                    None,
                    f"{record.symbol} has more than one record at {record.timestamp!r}, and "
                    "nothing in the data says which is correct",
                )
            )
        seen.add(key)

        last = previous.get(record.symbol)
        if last is not None and record.timestamp < last:
            findings.append(
                ValidationFinding(
                    FindingKind.OUT_OF_ORDER,
                    Severity.ERROR,
                    None,
                    None,
                    f"{record.symbol} has a record at {record.timestamp!r} after one at {last!r}",
                )
            )
        previous[record.symbol] = (
            max(last, record.timestamp) if last is not None else (record.timestamp)
        )

        findings.extend(_validate_one(record))

    findings.extend(_validate_spacing(records, expected_frequency))
    return tuple(findings)


def _validate_one(record: CanonicalRecord) -> list[ValidationFinding]:
    """Per-record checks that need no neighbours."""

    findings: list[ValidationFinding] = []
    if isinstance(record, Bar):
        prices = {
            "open": record.open,
            "high": record.high,
            "low": record.low,
            "close": record.close,
        }
        for name, price in prices.items():
            if price <= 0.0:
                findings.append(
                    ValidationFinding(
                        FindingKind.NON_POSITIVE_PRICE,
                        Severity.ERROR,
                        None,
                        name,
                        f"{record.symbol} at {record.timestamp!r} has {name}={price!r}; a "
                        "tradable instrument does not print at or below zero",
                    )
                )
        if record.high < record.low:
            findings.append(
                ValidationFinding(
                    FindingKind.INVALID_OHLC,
                    Severity.ERROR,
                    None,
                    None,
                    f"{record.symbol} at {record.timestamp!r} has high={record.high!r} below "
                    f"low={record.low!r}",
                )
            )
        elif not (record.low <= record.open <= record.high) or not (
            record.low <= record.close <= record.high
        ):
            findings.append(
                ValidationFinding(
                    FindingKind.INVALID_OHLC,
                    Severity.ERROR,
                    None,
                    None,
                    f"{record.symbol} at {record.timestamp!r} has open={record.open!r} and "
                    f"close={record.close!r} outside the range [{record.low!r}, "
                    f"{record.high!r}]",
                )
            )
        if record.volume < 0.0:
            findings.append(
                ValidationFinding(
                    FindingKind.NEGATIVE_VOLUME,
                    Severity.ERROR,
                    None,
                    "volume",
                    f"{record.symbol} at {record.timestamp!r} has volume={record.volume!r}",
                )
            )
    elif isinstance(record, Quote):
        for name, price in (("bid", record.bid), ("ask", record.ask)):
            if price <= 0.0:
                findings.append(
                    ValidationFinding(
                        FindingKind.NON_POSITIVE_PRICE,
                        Severity.ERROR,
                        None,
                        name,
                        f"{record.symbol} at {record.timestamp!r} has {name}={price!r}",
                    )
                )
        if record.bid >= record.ask:
            findings.append(
                ValidationFinding(
                    FindingKind.CROSSED_QUOTE,
                    Severity.WARNING,
                    None,
                    None,
                    f"{record.symbol} at {record.timestamp!r} has bid={record.bid!r} at or "
                    f"above ask={record.ask!r}; real books cross briefly, so this is reported "
                    "rather than refused",
                )
            )
        for name, size in (("bid_size", record.bid_size), ("ask_size", record.ask_size)):
            if size < 0.0:
                findings.append(
                    ValidationFinding(
                        FindingKind.NEGATIVE_VOLUME,
                        Severity.ERROR,
                        None,
                        name,
                        f"{record.symbol} at {record.timestamp!r} has {name}={size!r}",
                    )
                )
    return findings


def _validate_spacing(
    records: Sequence[CanonicalRecord], expected_frequency: TimeFrequency | None
) -> list[ValidationFinding]:
    """Report spacings that disagree with a declared frequency."""

    from alphalab.data.time import frequency_seconds

    if expected_frequency is None:
        return []
    interval = frequency_seconds(expected_frequency)
    if interval is None:
        return []

    by_symbol: dict[str, list[float]] = {}
    for record in records:
        by_symbol.setdefault(record.symbol, []).append(record.timestamp)

    findings: list[ValidationFinding] = []
    for symbol, stamps in by_symbol.items():
        ordered = sorted(stamps)
        gaps = [later - earlier for earlier, later in pairwise(ordered) if later > earlier]
        offenders = [gap for gap in gaps if gap % interval != 0]
        if offenders:
            findings.append(
                ValidationFinding(
                    FindingKind.INCONSISTENT_FREQUENCY,
                    Severity.WARNING,
                    None,
                    None,
                    f"{symbol} has {len(offenders)} of {len(gaps)} intervals that are not a "
                    f"whole multiple of {expected_frequency.name} ({interval:g}s); the "
                    f"smallest is {min(offenders):g}s",
                )
            )
    return findings
