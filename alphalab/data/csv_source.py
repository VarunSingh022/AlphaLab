"""Reading delimited text into raw rows, without repairing anything.

CSV is the format users actually have. It is also the format with the least
agreement about what it is: the delimiter varies, the header may be absent, a
row may carry more fields than the header promised, and a numeric column may
hold ``"N/A"`` in the one row that matters.

This module's whole job is to turn bytes into rows **while preserving every
discrepancy it finds**. Nothing here coerces a value, fills a gap, pads a short
row or drops a long one. A row whose field count disagrees with the header is
recorded as :class:`MalformedRow` with the reason, and it is the ingestion
report -- not this module, and not silence -- that decides what happens next.

Why not ``csv.Sniffer``
-----------------------

The stdlib sniffer is a heuristic whose reasoning is not reportable: it returns
a dialect, not a rationale, and it can be confidently wrong on a file with
commas inside quoted text. Phase 4's rule is that heuristics must not be
invisible, so :func:`detect_delimiter` implements an explicit one -- a candidate
is accepted only when it splits *every* sampled line into the same number of
fields, that number is greater than one, and no other candidate also does -- and
returns the reason alongside the answer. When two candidates both fit, it says
so and picks neither.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import StringIO
from typing import Final

from alphalab.data.exceptions import DataValidationError

__all__ = [
    "DELIMITER_CANDIDATES",
    "CsvDialect",
    "MalformedRow",
    "RawRow",
    "RawTable",
    "decode_text",
    "detect_delimiter",
    "read_delimited",
]

#: Delimiters considered, in the order they are reported. Deliberately short:
#: each additional candidate makes ambiguity more likely, and these four cover
#: comma-separated exports, European semicolon exports, TSV and pipe-delimited
#: broker files.
DELIMITER_CANDIDATES: Final[tuple[str, ...]] = (",", ";", "\t", "|")

#: How many lines :func:`detect_delimiter` inspects. Bounded so detection cost
#: does not scale with file size, and large enough that a single anomalous row
#: cannot carry the decision.
_SAMPLE_LINES: Final = 50


@dataclass(frozen=True, slots=True)
class CsvDialect:
    """The reading decisions a delimited file is parsed under.

    Attributes:
        delimiter: The field separator.
        quote_char: The quoting character.
        has_header: Whether the first line names the columns. When ``False``,
            columns are named ``column_0``, ``column_1``, ... so that a
            headerless file still has stable, referable field names.
    """

    delimiter: str
    quote_char: str = '"'
    has_header: bool = True

    def __post_init__(self) -> None:
        if len(self.delimiter) != 1:
            raise DataValidationError(
                f"A delimiter must be exactly one character, got {self.delimiter!r}."
            )
        if len(self.quote_char) != 1:
            raise DataValidationError(
                f"A quote character must be exactly one character, got {self.quote_char!r}."
            )


@dataclass(frozen=True, slots=True)
class RawRow:
    """One well-formed line: its values, and where it came from.

    ``line_number`` is 1-based and counts lines in the source file, header
    included, so that a finding can be reported at the place a person will
    look for it in their own editor.
    """

    line_number: int
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MalformedRow:
    """A line that could not be read as a row, kept rather than discarded."""

    line_number: int
    values: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RawTable:
    """Rows as they were written, with every discrepancy preserved."""

    columns: tuple[str, ...]
    rows: tuple[RawRow, ...]
    malformed: tuple[MalformedRow, ...]
    dialect: CsvDialect

    def column_index(self, name: str) -> int:
        """Position of ``name``, or ``-1`` when the table has no such column."""

        try:
            return self.columns.index(name)
        except ValueError:
            return -1

    def column_values(self, name: str) -> tuple[str, ...]:
        """Every value in one column, in row order.

        Raises:
            DataValidationError: If the table has no column of that name.
        """

        index = self.column_index(name)
        if index < 0:
            raise DataValidationError(
                f"No column named {name!r}; this table has {list(self.columns)}."
            )
        return tuple(row.values[index] for row in self.rows)

    def as_mapping(self, row: RawRow) -> Mapping[str, str]:
        """One row keyed by column name."""

        return dict(zip(self.columns, row.values, strict=True))

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[str, object]]) -> RawTable:
        """Build a table from rows already in memory.

        The one place dictionaries become a :class:`RawTable`, so that rows
        handed to :func:`alphalab.api.ingest_rows` and rows handed to
        :func:`~alphalab.data.parser.parse_raw_rows` are read by exactly the
        same code as rows read from a file.

        Columns are the union of the rows' keys in first-seen order, normalized
        the same way a header is. A key a row does not carry reads as empty --
        which is *absent*, and is what a missing required field is reported as
        -- and so does an explicit ``None``. Every other value is rendered with
        ``str``.

        Line numbers are assigned as if the rows had been written to a file with
        a header on line 1, so a finding about the third row reads the same
        whichever way those rows arrived.

        Raises:
            DataValidationError: If ``rows`` is empty, or if the keys normalize
                to a blank or duplicated column name.
        """

        if not rows:
            raise DataValidationError("No rows to read; an empty sequence has no columns.")

        keys: list[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)

        columns = _normalize_header([str(key) for key in keys], line_number=1)
        built = []
        for index, row in enumerate(rows):
            values = tuple("" if row.get(key) is None else str(row.get(key)) for key in keys)
            built.append(RawRow(line_number=index + 2, values=values))

        return cls(
            columns=columns,
            rows=tuple(built),
            malformed=(),
            dialect=CsvDialect(","),
        )


def decode_text(payload: bytes, encoding: str) -> str:
    """Decode source bytes, refusing rather than replacing undecodable ones.

    ``errors="replace"`` would put U+FFFD into a symbol or a price and carry it
    silently into a dataset. An encoding that does not fit the content is a
    fact about the ingestion, so it is raised.
    """

    try:
        text = payload.decode(encoding)
    except (UnicodeDecodeError, LookupError) as error:
        raise DataValidationError(
            f"Content could not be decoded as {encoding!r}: {error}. Name the encoding the "
            "file was written in rather than having undecodable bytes replaced."
        ) from error
    return text.lstrip("﻿")


def detect_delimiter(text: str) -> tuple[str | None, str]:
    """Decide which delimiter a delimited file uses.

    Returns:
        ``(delimiter, rationale)``. ``delimiter`` is ``None`` when no candidate
        fits or when more than one does; the rationale says which, in words
        meant for whoever has to resolve it.
    """

    lines = [line for line in text.splitlines() if line.strip()][:_SAMPLE_LINES]
    if not lines:
        return None, "the content has no non-empty lines"

    sample = "\n".join(lines)
    fits: list[tuple[str, int, float]] = []
    for candidate in DELIMITER_CANDIDATES:
        widths = [len(row) for row in csv.reader(StringIO(sample), delimiter=candidate) if row]
        if not widths:
            continue
        modal = max(sorted(set(widths)), key=widths.count)
        if modal > 1:
            fits.append((candidate, modal, widths.count(modal) / len(widths)))

    if not fits:
        return None, (
            "no candidate delimiter "
            f"({', '.join(repr(c) for c in DELIMITER_CANDIDATES)}) splits any sampled line "
            "into more than one field, so the content is not delimited by any of them"
        )

    best_share = max(share for _, _, share in fits)
    leaders = [entry for entry in fits if entry[2] == best_share]
    if len(leaders) > 1:
        described = ", ".join(f"{delim!r} ({width} fields)" for delim, width, _ in leaders)
        return None, (
            f"more than one delimiter fits equally well -- {described}, each consistent across "
            f"{best_share:.0%} of sampled lines -- so choosing one would be a guess; declare "
            "the delimiter instead"
        )

    delimiter, width, share = leaders[0]
    agreement = (
        f"all {len(lines)} sampled lines"
        if share == 1.0
        else f"{share:.0%} of {len(lines)} sampled lines (the rest are ragged and are reported "
        "row by row)"
    )
    return delimiter, (
        f"{delimiter!r} is the only candidate that splits {agreement} into the same number of "
        f"fields ({width})"
    )


def read_delimited(text: str, dialect: CsvDialect | None) -> RawTable:
    """Read delimited text into rows, recording what did not fit.

    Args:
        text: The decoded content.
        dialect: How to read it. ``None`` asks for the delimiter to be detected,
            which refuses when detection is ambiguous rather than picking.

    Raises:
        DataValidationError: If the content is empty, or if ``dialect`` is
            ``None`` and the delimiter cannot be determined unambiguously.
    """

    if not text.strip():
        raise DataValidationError("Empty content cannot be read as a delimited table.")

    resolved = dialect
    if resolved is None:
        delimiter, rationale = detect_delimiter(text)
        if delimiter is None:
            raise DataValidationError(f"Could not determine the delimiter: {rationale}.")
        resolved = CsvDialect(delimiter=delimiter)

    reader = csv.reader(StringIO(text), delimiter=resolved.delimiter, quotechar=resolved.quote_char)
    lines = [(number, values) for number, values in enumerate(reader, start=1) if values]
    if not lines:
        raise DataValidationError("The content contains no readable rows.")

    if resolved.has_header:
        header_line, header_values = lines[0]
        columns = _normalize_header(header_values, header_line)
        body = lines[1:]
    else:
        columns = tuple(f"column_{index}" for index in range(len(lines[0][1])))
        body = lines

    rows: list[RawRow] = []
    malformed: list[MalformedRow] = []
    width = len(columns)
    for number, values in body:
        if len(values) == 1 and not values[0].strip():
            continue
        if len(values) != width:
            malformed.append(
                MalformedRow(
                    line_number=number,
                    values=tuple(values),
                    reason=(
                        f"row has {len(values)} fields but the header declares {width}; "
                        "padding or truncating it would invent or discard a value"
                    ),
                )
            )
            continue
        rows.append(RawRow(line_number=number, values=tuple(values)))

    return RawTable(columns=columns, rows=tuple(rows), malformed=tuple(malformed), dialect=resolved)


def _normalize_header(values: Sequence[str], line_number: int) -> tuple[str, ...]:
    """Strip and lower-case column names, refusing duplicates and blanks.

    Case and surrounding whitespace are not meaningful in a CSV header -- every
    export tool spells them differently -- so they are normalized. A *duplicate*
    name is meaningful and is refused: two columns called ``close`` make every
    later reference to ``close`` ambiguous, and picking the first silently
    discards the second.
    """

    names = [value.strip().lower() for value in values]
    blanks = [index for index, name in enumerate(names) if not name]
    if blanks:
        raise DataValidationError(
            f"Header line {line_number} has an unnamed column at position {blanks[0]}; "
            "a column with no name cannot be referred to."
        )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise DataValidationError(
            f"Header line {line_number} names {', '.join(repr(d) for d in duplicates)} more "
            "than once; every later reference to it would be ambiguous."
        )
    return tuple(names)
