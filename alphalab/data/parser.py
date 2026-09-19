"""The dictionary entry point, on the canonical coercion path.

:func:`parse_raw_rows` maps loosely-keyed dictionaries onto canonical wire bars.
It is the surface ``UniversalDataEngine.load`` has had since v1, and it keeps
its signature and its return type.

What changed in v3.1 is that it no longer has a parser of its own. It builds a
:class:`~alphalab.data.csv_source.RawTable` and hands it to
:func:`~alphalab.data.schema.detect_schema` and
:func:`~alphalab.data.validation.coerce_row` -- the same detection and the same
coercion a CSV goes through. There is one parsing implementation in this
package, and this is a second door onto it rather than a second copy of it.

It refuses rather than dropping
-------------------------------

Until v3.1 a row that could not be translated was **dropped, and nothing was
returned to say so**. A caller handed AlphaLab ten rows, got back seven bars,
and had no way to learn that three had gone or why. Every figure computed
downstream was computed on data the caller had not seen.

That is the one thing this package is not allowed to do, so it now raises. The
refusal names the offending rows and their reasons, and points at
:func:`alphalab.api.ingest_rows` for the case this function cannot serve: a
partial load, where some rows are unusable and the rest are still wanted. That
path returns a :class:`~alphalab.data.quality.DataQualityReport` carrying every
rejected row -- the malformed rows stay observable, and the valid ones stay
usable, without either being decided silently here.

Rows are still returned in chronological order, which reorders nothing away and
loses nothing; only the dropping was a loss.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.data.csv_source import RawTable
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar
from alphalab.data.schema import RecordType, detect_schema
from alphalab.data.validation import RowRejection, coerce_row

__all__ = ["parse_raw_rows"]

#: How many rejected rows a refusal describes before summarising the rest. A
#: caller fixing a feed wants the shape of the problem, not ten thousand lines.
_REPORTED_REJECTIONS = 5


def parse_raw_rows(symbol: str, raw_rows: Sequence[Mapping[str, Any]]) -> tuple[Bar, ...]:
    """Translate dynamically keyed external data into canonical wire bars.

    Args:
        symbol: Instrument name for rows that do not carry one.
        raw_rows: Rows keyed by any recognised spelling of the canonical
            fields. Every row must be translatable.

    Returns:
        One bar per row, in chronological order.

    Raises:
        DataValidationError: If the rows are empty, if their columns do not
            supply every field a bar needs, or if **any** row cannot be
            translated. Nothing is dropped: use
            :func:`alphalab.api.ingest_rows` when some rows are expected to be
            unusable and the rest are still wanted.
    """

    table = RawTable.from_rows(raw_rows)
    # BAR is *declared*, not inferred. This function's return type is
    # ``tuple[Bar, ...]``, so the shape is its contract rather than a guess --
    # and declaring it makes the refusal name the fields that are missing
    # instead of reporting that no shape could be determined.
    detection = detect_schema(table, RecordType.BAR).require()

    parsed: list[Bar] = []
    rejected: list[RowRejection] = []
    for row in table.rows:
        record, findings = coerce_row(
            table,
            row,
            detection,
            timezone_name=None,
            date_policy=None,
            default_symbol=symbol,
        )
        if record is None:
            rejected.append(
                RowRejection(line_number=row.line_number, values=row.values, findings=findings)
            )
        elif isinstance(record, Bar):  # pragma: no branch - BAR was declared above
            parsed.append(record)

    if rejected:
        raise DataValidationError(_refusal(rejected, len(table.rows)))

    parsed.sort(key=lambda bar: bar.timestamp)
    return tuple(parsed)


def _refusal(rejected: Sequence[RowRejection], total: int) -> str:
    """The message a caller needs: what failed, where, and what to do instead."""

    described = [
        f"row {rejection.line_number - 1}: "
        + (rejection.findings[0].detail if rejection.findings else "no reason was recorded")
        for rejection in rejected[:_REPORTED_REJECTIONS]
    ]
    if len(rejected) > _REPORTED_REJECTIONS:
        described.append(f"... and {len(rejected) - _REPORTED_REJECTIONS} more")

    return (
        f"{len(rejected)} of {total} rows could not be translated into a bar, and "
        "parse_raw_rows will not discard them:\n  - "
        + "\n  - ".join(described)
        + "\nUse alphalab.api.ingest_rows to ingest the translatable rows and receive a "
        "DataQualityReport naming every row that was rejected and why."
    )
