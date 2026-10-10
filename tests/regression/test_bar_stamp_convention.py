"""A bar is stamped at the end of its interval, and a source says what it did (DAT-001).

A bar summarizes an interval; its close is knowable only when the interval ends.
Until v3.10 the canonical bar's timestamp had no defined meaning and was taken
as the instant the bar was knowable -- so a source that stamps bars at the
*start* of their interval, as most vendors' intraday bars are, reported every
close at its opening instant: a whole bar of look-ahead, silently.

The canonical bar is now stamped at the end of its interval. Ingestion and wire
normalization require the source's convention, move start-stamped bars by the
declared interval, and record the move; with no declaration they refuse.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.data import CsvDialect, ingest_table, read_delimited
from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar as WireBar
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.time import BarStamp, DateOnlyPolicy, TimeFrequency
from alphalab.market import MarketValidationError, NormalizationPolicy, normalize_wire_bar
from alphalab.market.bar import TimeFrame
from tests.unit.test_api import _request

CSV = (
    "symbol,timestamp,open,high,low,close,volume\n"
    "AAPL,2025-01-02T14:30:00Z,100,101,99,100.5,10\n"
    "AAPL,2025-01-02T14:31:00Z,100.5,102,100,101.5,12\n"
)
DATES = "symbol,date,open,high,low,close,volume\nAAPL,2025-01-02,100,101,99,100.5,10\n"


def _ingest(stamp: BarStamp | None, content: str = CSV, **overrides: object):  # type: ignore[no-untyped-def]
    source = raw_source_from_bytes(
        SourceKind.IN_MEMORY, "bars.csv", content.encode("utf-8"), 1.0, "text/csv"
    )
    fields: dict[str, object] = {"frequency": TimeFrequency.MINUTE, "bar_stamp": stamp}
    fields.update(overrides)
    request = _request("SAMPLE", source=source, **fields)
    return ingest_table(read_delimited(content, CsvDialect(",")), request)


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_start_stamped_bars_are_moved_to_the_end_of_their_interval() -> None:
    result = _ingest(BarStamp.INTERVAL_START)

    stamps = [record.timestamp for record in result.dataset.records]
    assert stamps == [1735828260.0, 1735828320.0]  # 14:31 and 14:32 UTC
    (moved,) = [t for t in result.transformations if t.operation == "stamp_bars_at_interval_end"]
    assert moved.rows_affected == 2
    assert "60s later" in moved.reason


def test_end_stamped_bars_are_taken_as_they_are() -> None:
    result = _ingest(BarStamp.INTERVAL_END)

    assert [record.timestamp for record in result.dataset.records] == [1735828200.0, 1735828260.0]
    assert not [t for t in result.transformations if t.operation == "stamp_bars_at_interval_end"]


def test_the_two_conventions_are_two_datasets() -> None:
    start = _ingest(BarStamp.INTERVAL_START).dataset.dataset_id
    end = _ingest(BarStamp.INTERVAL_END).dataset.dataset_id

    assert start != end


def test_timed_bars_with_no_declared_convention_are_refused() -> None:
    with pytest.raises(DataValidationError, match="start or the end"):
        _ingest(None)


def test_a_bare_date_takes_its_instant_from_the_date_policy_not_a_bar_stamp() -> None:
    with pytest.raises(DataValidationError, match="date_policy"):
        _ingest(
            BarStamp.INTERVAL_START,
            DATES,
            frequency=TimeFrequency.DAILY,
            date_policy=DateOnlyPolicy.END_OF_DAY,
            timezone_name="America/New_York",
        )

    dated = _ingest(
        None,
        DATES,
        frequency=TimeFrequency.DAILY,
        date_policy=DateOnlyPolicy.END_OF_DAY,
        timezone_name="America/New_York",
    )
    assert len(dated.dataset.records) == 1


def test_a_dated_bar_is_not_stamped_at_the_midnight_its_day_begins() -> None:
    with pytest.raises(DataValidationError, match="END_OF_DAY"):
        _ingest(
            None,
            DATES,
            frequency=TimeFrequency.DAILY,
            date_policy=DateOnlyPolicy.START_OF_DAY,
            timezone_name="America/New_York",
        )


def test_start_stamped_bars_of_no_fixed_interval_are_refused() -> None:
    with pytest.raises(DataValidationError, match="no fixed interval"):
        _ingest(BarStamp.INTERVAL_START, frequency=TimeFrequency.MONTHLY)


# --------------------------------------------------------------------------- #
# Wire normalization
# --------------------------------------------------------------------------- #

WIRE = WireBar("AAPL", 1_000.0, 1.0, 2.0, 0.5, 1.5, 10.0)


def test_a_start_stamped_wire_bar_is_moved_by_its_timeframe() -> None:
    policy = NormalizationPolicy(timeframe=TimeFrame.M5, bar_stamp=BarStamp.INTERVAL_START)

    assert normalize_wire_bar(WIRE, policy).timestamp == 1_300.0
    assert normalize_wire_bar(
        WIRE, replace(policy, bar_stamp=BarStamp.INTERVAL_END)
    ).close == Decimal("1.5")


def test_a_wire_bar_with_no_declared_convention_is_refused() -> None:
    with pytest.raises(MarketValidationError, match="start or the end"):
        normalize_wire_bar(WIRE, NormalizationPolicy(timeframe=TimeFrame.M1))


def test_a_start_stamped_monthly_wire_bar_is_refused() -> None:
    policy = NormalizationPolicy(timeframe=TimeFrame.MN1, bar_stamp=BarStamp.INTERVAL_START)

    with pytest.raises(MarketValidationError, match="no fixed length"):
        normalize_wire_bar(WIRE, policy)
