"""What the data layer promises about bad data, pinned one defect at a time.

v3.1 makes AlphaLab's ingestion path answerable for what it does to a user's
data. The promise is not "the data will be clean" -- it is that **nothing
happens silently**: every row that does not become a record is reported with a
reason, every change is recorded as a transformation, and every decision that
could produce a plausible wrong number is refused rather than defaulted.

This file holds one section per defect the brief names. Every test asserts the
*exact* behaviour -- which finding, which count, which row, which message --
rather than that something raised. A test that only asserts an exception passes
just as well when the wrong exception is raised for the wrong reason, which is
how a quality gate becomes decorative.
"""

from __future__ import annotations

import pytest

from alphalab.data import (
    CleaningPolicy,
    CsvDialect,
    DataQualityError,
    DataValidationError,
    DateOnlyPolicy,
    DuplicatePolicy,
    FindingKind,
    InvalidRecordPolicy,
    MissingValuePolicy,
    OrderingPolicy,
    RecordType,
    Severity,
    TimestampFormat,
    clean_records,
    detect_schema,
    detect_timestamp_format,
    parse_timestamp,
    read_delimited,
    validate_records,
)
from alphalab.data.feed import Bar
from alphalab.data.validation import coerce_row

#: Every policy at its most permissive. Cleaning has no default policy, so a
#: test that wants cleaning to happen has to say so.
PERMISSIVE = CleaningPolicy(
    duplicates=DuplicatePolicy.KEEP_FIRST,
    ordering=OrderingPolicy.SORT,
    invalid_records=InvalidRecordPolicy.DROP,
    missing_values=MissingValuePolicy.DROP_ROW,
)

#: The position that nothing may be altered.
STRICT = CleaningPolicy(
    duplicates=DuplicatePolicy.REFUSE,
    ordering=OrderingPolicy.REFUSE,
    invalid_records=InvalidRecordPolicy.REFUSE,
    missing_values=MissingValuePolicy.REFUSE,
)


def _bar(symbol: str, timestamp: float, close: float = 10.0, volume: float = 100.0) -> Bar:
    """A bar that is valid in every respect, so a test perturbs exactly one thing."""

    return Bar(
        symbol=symbol,
        timestamp=timestamp,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=volume,
    )


def _coerce(csv_text: str, **kwargs: object):  # type: ignore[no-untyped-def]
    """Read a CSV and coerce every row, returning (records, rejections)."""

    table = read_delimited(csv_text, CsvDialect(","))
    detection = detect_schema(table).require()
    records, rejections = [], []
    for row in table.rows:
        record, findings = coerce_row(
            table,
            row,
            detection,
            kwargs.get("timezone_name"),  # type: ignore[arg-type]
            kwargs.get("date_policy"),  # type: ignore[arg-type]
            kwargs.get("default_symbol", "TEST"),  # type: ignore[arg-type]
        )
        if record is None:
            rejections.append((row.line_number, findings))
        else:
            records.append(record)
    return records, rejections


# --------------------------------------------------------------------------- #
# 1. Duplicates
# --------------------------------------------------------------------------- #


def test_a_duplicate_instant_is_found_and_named() -> None:
    findings = validate_records([_bar("AAPL", 1.0), _bar("AAPL", 1.0)], None)

    duplicates = [f for f in findings if f.kind is FindingKind.DUPLICATE_TIMESTAMP]
    assert len(duplicates) == 1
    assert duplicates[0].severity is Severity.ERROR
    assert "AAPL" in duplicates[0].detail


def test_two_instruments_at_one_instant_are_not_duplicates() -> None:
    """The defect this rule replaced: a three-symbol daily file collapsing to one."""

    findings = validate_records([_bar("AAPL", 1.0), _bar("MSFT", 1.0), _bar("SPY", 1.0)], None)

    assert [f for f in findings if f.kind is FindingKind.DUPLICATE_TIMESTAMP] == []


def test_refusing_duplicates_names_the_pair_and_the_policy() -> None:
    with pytest.raises(DataQualityError) as error:
        clean_records([_bar("AAPL", 1.0), _bar("AAPL", 1.0)], STRICT, [])

    message = str(error.value)
    assert "AAPL" in message, "the refusal names the instrument"
    assert "DuplicatePolicy.REFUSE" in message, "and the policy that refused"
    assert "KEEP_FIRST" in message and "KEEP_LAST" in message, "and how to resolve it"


def test_keep_first_and_keep_last_choose_different_rows_and_say_which() -> None:
    """Not merely 'a duplicate was removed': *which* copy survived is the decision."""

    first, second = _bar("AAPL", 1.0, close=10.0), _bar("AAPL", 1.0, close=99.0)

    kept_first = clean_records([first, second], PERMISSIVE, [])
    kept_last = clean_records(
        [first, second],
        CleaningPolicy(
            DuplicatePolicy.KEEP_LAST,
            OrderingPolicy.SORT,
            InvalidRecordPolicy.DROP,
            MissingValuePolicy.DROP_ROW,
        ),
        [],
    )

    assert [r.close for r in kept_first.records] == [10.0]  # type: ignore[attr-defined]
    assert [r.close for r in kept_last.records] == [99.0]  # type: ignore[attr-defined]

    record = kept_first.transformations[0]
    assert record.operation == "drop_duplicate"
    assert record.rows_affected == 1
    assert "KEEP_FIRST" in record.reason, "the transformation records which copy won"


# --------------------------------------------------------------------------- #
# 2. Missing data
# --------------------------------------------------------------------------- #


def test_a_missing_required_price_rejects_the_row_and_names_the_column() -> None:
    records, rejections = _coerce("timestamp,open,high,low,close\n1,1,2,0.5,\n")

    assert records == [], "a bar cannot be built without a close"
    assert len(rejections) == 1
    line, findings = rejections[0]
    assert line == 2, "the rejection points at the source line"
    assert findings[0].kind is FindingKind.MISSING_VALUE
    assert findings[0].column == "close"


def test_a_missing_optional_volume_does_not_reject_the_row() -> None:
    records, rejections = _coerce("timestamp,open,high,low,close,volume\n1,1,2,0.5,1.5,\n")

    assert rejections == []
    assert len(records) == 1
    assert records[0].volume == 0.0, "absent volume reads as zero, and the bar survives"


def test_there_is_no_policy_that_fills_a_missing_price() -> None:
    """Forward-fill invents a print that never happened, at any policy setting."""

    assert {member.name for member in MissingValuePolicy} == {"REFUSE", "DROP_ROW"}


# --------------------------------------------------------------------------- #
# 3. Malformed timestamps
# --------------------------------------------------------------------------- #


def test_an_unreadable_timestamp_rejects_that_row_and_keeps_the_others() -> None:
    """One bad value is a bad row, not an undecidable column.

    Refusing the whole file because a single cell says ``"N/A"`` would be
    unusable on real vendor exports; reading it as *something* would be silent.
    The column's format is decided from the values that parse, and the one that
    does not is rejected on its own with the offending value named.
    """

    records, rejections = _coerce(
        "timestamp,open,high,low,close\n"
        "2025-01-02T16:00:00Z,1,2,0.5,1.5\n"
        "not-a-date,1,2,0.5,1.5\n"
        "2025-01-03T16:00:00Z,1,2,0.5,1.5\n"
    )

    assert len(records) == 2, "the readable rows survive"
    assert len(rejections) == 1
    line, findings = rejections[0]
    assert line == 3, "and the rejection points at the source line"
    assert findings[0].kind is FindingKind.UNPARSEABLE_TIMESTAMP
    assert "not-a-date" in findings[0].detail, "naming the value that could not be read"


def test_the_unreadable_values_are_counted_in_the_detection_rationale() -> None:
    """Excluded from the decision, but never invisible."""

    detected, rationale = detect_timestamp_format(
        ["2025-01-02T16:00:00Z", "N/A", "2025-01-03T16:00:00Z"]
    )

    assert detected is TimestampFormat.ISO_8601_AWARE
    assert "1 of 3 values are unreadable" in rationale


def test_a_column_where_nothing_is_readable_is_refused() -> None:
    detected, rationale = detect_timestamp_format(["N/A", "missing", "--"])

    assert detected is None
    assert "none of the 3 values" in rationale


def test_a_column_mixing_timestamp_shapes_is_refused_rather_than_read_two_ways() -> None:
    detected, rationale = detect_timestamp_format(["2025-01-02T16:00:00Z", "2025-01-03"])

    assert detected is None
    assert "mixes" in rationale


def test_an_epoch_column_that_could_be_milliseconds_is_refused() -> None:
    """Both readings produce a valid instant, centuries apart. Nothing in the
    data distinguishes them, so detection declines to pick."""

    detected, rationale = detect_timestamp_format(["1735689600000", "1735776000000"])

    assert detected is None
    assert "milliseconds" in rationale
    # And the caller can still say which it is.
    assert (
        parse_timestamp("1735689600000", TimestampFormat.EPOCH_MILLISECONDS, None, None)
        == 1735689600.0
    )


def test_a_malformed_row_is_reported_rather_than_padded() -> None:
    table = read_delimited("sym,px\nAAPL,10\nMSFT,20,EXTRA\n", CsvDialect(","))

    assert [row.values for row in table.rows] == [("AAPL", "10")]
    assert len(table.malformed) == 1
    assert table.malformed[0].line_number == 3
    assert table.malformed[0].values == ("MSFT", "20", "EXTRA")
    assert "invent or discard" in table.malformed[0].reason


# --------------------------------------------------------------------------- #
# 4. Timezone conversions
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_refused_until_a_zone_is_named() -> None:
    """Assuming UTC is how a US- or India-centric default gets encoded, and it
    produces a number rather than an error."""

    with pytest.raises(DataValidationError) as error:
        parse_timestamp("2025-01-02 16:00:00", TimestampFormat.ISO_8601_NAIVE, None, None)

    assert "wall clock" in str(error.value)


def test_one_wall_clock_reading_is_two_instants_in_two_markets() -> None:
    mumbai = parse_timestamp(
        "2025-01-02 16:00:00", TimestampFormat.ISO_8601_NAIVE, "Asia/Kolkata", None
    )
    new_york = parse_timestamp(
        "2025-01-02 16:00:00", TimestampFormat.ISO_8601_NAIVE, "America/New_York", None
    )

    assert new_york - mumbai == 10.5 * 3600.0, "IST is UTC+5:30, EST is UTC-5"


def test_an_offset_bearing_timestamp_refuses_a_zone_rather_than_ignoring_it() -> None:
    """Accepting and discarding it would leave a caller believing it applied."""

    with pytest.raises(DataValidationError) as error:
        parse_timestamp("2025-01-02T16:00:00Z", TimestampFormat.ISO_8601_AWARE, "Asia/Tokyo", None)

    assert "no effect" in str(error.value)


def test_equal_instants_written_two_ways_parse_equal() -> None:
    assert parse_timestamp(
        "2025-01-02T16:00:00Z", TimestampFormat.ISO_8601_AWARE, None, None
    ) == parse_timestamp("2025-01-02T11:00:00-05:00", TimestampFormat.ISO_8601_AWARE, None, None)


def test_a_bare_date_needs_a_policy_because_midnight_is_a_convention() -> None:
    with pytest.raises(DataValidationError) as error:
        parse_timestamp("2025-01-02", TimestampFormat.DATE_ONLY, "UTC", None)
    assert "DateOnlyPolicy" in str(error.value)

    start = parse_timestamp(
        "2025-01-02", TimestampFormat.DATE_ONLY, "UTC", DateOnlyPolicy.START_OF_DAY
    )
    end = parse_timestamp("2025-01-02", TimestampFormat.DATE_ONLY, "UTC", DateOnlyPolicy.END_OF_DAY)
    assert end - start == pytest.approx(86400.0 - 1e-6)


# --------------------------------------------------------------------------- #
# 5. Out-of-order records
# --------------------------------------------------------------------------- #


def test_records_out_of_order_are_found_per_instrument() -> None:
    findings = validate_records([_bar("AAPL", 2.0), _bar("AAPL", 1.0)], None)

    out_of_order = [f for f in findings if f.kind is FindingKind.OUT_OF_ORDER]
    assert len(out_of_order) == 1
    assert out_of_order[0].severity is Severity.ERROR


def test_interleaved_instruments_are_not_out_of_order() -> None:
    """A file sorted by instrument then date is ordered, per instrument."""

    findings = validate_records(
        [_bar("AAPL", 1.0), _bar("AAPL", 2.0), _bar("MSFT", 1.0), _bar("MSFT", 2.0)], None
    )

    assert [f for f in findings if f.kind is FindingKind.OUT_OF_ORDER] == []


def test_sorting_is_refused_by_default_and_recorded_when_permitted() -> None:
    unordered = [_bar("AAPL", 2.0), _bar("AAPL", 1.0)]

    with pytest.raises(DataQualityError) as error:
        clean_records(unordered, STRICT, [])
    assert "OrderingPolicy.REFUSE" in str(error.value)

    outcome = clean_records(unordered, PERMISSIVE, [])
    assert [r.timestamp for r in outcome.records] == [1.0, 2.0]
    sort = next(t for t in outcome.transformations if t.operation == "sort_chronologically")
    assert sort.rows_affected == 2, "the count is measured, not asserted"


# --------------------------------------------------------------------------- #
# 6. Invalid OHLC, prices and volumes
# --------------------------------------------------------------------------- #


def test_high_below_low_is_invalid_and_says_so() -> None:
    broken = Bar("AAPL", 1.0, open=10.0, high=8.0, low=9.0, close=10.0, volume=1.0)

    findings = [f for f in validate_records([broken], None) if f.kind is FindingKind.INVALID_OHLC]
    assert len(findings) == 1
    assert "high=8.0" in findings[0].detail and "low=9.0" in findings[0].detail


def test_a_close_outside_the_high_low_range_is_invalid() -> None:
    """The check v3.0 did not make: high >= low held, and the close was impossible."""

    broken = Bar("AAPL", 1.0, open=10.0, high=12.0, low=9.0, close=99.0, volume=1.0)

    findings = [f for f in validate_records([broken], None) if f.kind is FindingKind.INVALID_OHLC]
    assert len(findings) == 1
    assert "outside the range" in findings[0].detail


def test_a_non_positive_price_is_invalid_and_names_the_field() -> None:
    broken = Bar("AAPL", 1.0, open=0.0, high=12.0, low=0.0, close=10.0, volume=1.0)

    findings = [
        f for f in validate_records([broken], None) if f.kind is FindingKind.NON_POSITIVE_PRICE
    ]
    assert {f.column for f in findings} == {"open", "low"}


def test_a_negative_volume_is_invalid() -> None:
    broken = Bar("AAPL", 1.0, open=10.0, high=11.0, low=9.0, close=10.0, volume=-5.0)

    findings = [
        f for f in validate_records([broken], None) if f.kind is FindingKind.NEGATIVE_VOLUME
    ]
    assert len(findings) == 1
    assert findings[0].column == "volume"


def test_an_impossible_bar_is_refused_rather_than_repaired() -> None:
    """Clamping it to a consistent range produces a bar the source never reported."""

    broken = Bar("AAPL", 1.0, open=10.0, high=8.0, low=9.0, close=10.0, volume=1.0)
    findings = validate_records([broken], None)

    with pytest.raises(DataQualityError) as error:
        clean_records([broken], STRICT, findings)
    assert "InvalidRecordPolicy.REFUSE" in str(error.value)

    dropped = clean_records([broken], PERMISSIVE, findings)
    assert dropped.records == (), "DROP removes it; nothing repairs it"
    assert dropped.transformations[0].operation == "drop_invalid_record"


def test_keep_carries_the_defect_rather_than_hiding_it() -> None:
    broken = Bar("AAPL", 1.0, open=10.0, high=8.0, low=9.0, close=10.0, volume=1.0)
    keep = CleaningPolicy(
        DuplicatePolicy.KEEP_FIRST,
        OrderingPolicy.SORT,
        InvalidRecordPolicy.KEEP,
        MissingValuePolicy.DROP_ROW,
    )

    outcome = clean_records([broken], keep, validate_records([broken], None))

    assert outcome.records == (broken,)
    assert outcome.transformations == (), "keeping a record is not a transformation"


# --------------------------------------------------------------------------- #
# 7. Ambiguous schemas
# --------------------------------------------------------------------------- #


def test_close_and_adj_close_is_ambiguous_and_refused() -> None:
    """The canonical case: both are 'the close', and they are different data."""

    table = read_delimited(
        "date,open,high,low,close,adj close,volume\n2025-01-02,1,2,0.5,1.5,1.4,100\n",
        CsvDialect(","),
    )
    detection = detect_schema(table)

    assert detection.is_resolved is False
    ambiguous = detection.ambiguous[0]
    assert set(ambiguous.columns) == {"close", "adj close"}

    with pytest.raises(DataValidationError) as error:
        detection.require()
    assert "CLOSE is ambiguous" in str(error.value)


def test_a_table_that_is_both_bars_and_quotes_needs_a_declaration() -> None:
    text = "timestamp,open,high,low,close,bid,ask\n1,1,2,0.5,1.5,1.4,1.6\n"
    table = read_delimited(text, CsvDialect(","))

    assert detect_schema(table).record_type is None
    assert detect_schema(table, RecordType.BAR).record_type is RecordType.BAR
    assert detect_schema(table, RecordType.BAR).is_resolved is True


def test_the_refusal_lists_every_problem_at_once() -> None:
    """A caller fixing a header wants the whole list, not one item per attempt."""

    table = read_delimited(
        "date,open,high,low,close,adj close\n2025-01-02,1,2,0.5,1.5,1.4\n", CsvDialect(",")
    )

    with pytest.raises(DataValidationError) as error:
        detect_schema(table).require()

    message = str(error.value)
    assert "CLOSE is ambiguous" in message
    assert "record type could not be determined" in message


def test_two_columns_of_the_same_name_are_refused_at_read_time() -> None:
    with pytest.raises(DataValidationError) as error:
        read_delimited("close,close\n1,2\n", CsvDialect(","))

    assert "more than once" in str(error.value)


def test_an_ambiguous_delimiter_is_refused_rather_than_guessed() -> None:
    with pytest.raises(DataValidationError) as error:
        read_delimited("a,b\tc\n1,2\t3\n", None)

    assert "more than one delimiter" in str(error.value)


# --------------------------------------------------------------------------- #
# 8. Deterministic cleaning
# --------------------------------------------------------------------------- #


def test_cleaning_the_same_input_twice_produces_the_same_output() -> None:
    records = [_bar("AAPL", 2.0), _bar("AAPL", 1.0), _bar("AAPL", 1.0)]

    first = clean_records(records, PERMISSIVE, [])
    second = clean_records(records, PERMISSIVE, [])

    assert first.records == second.records
    assert first.transformations == second.transformations


def test_the_order_of_operations_is_fixed_and_observable() -> None:
    """Drop invalid, then deduplicate, then order -- so a duplicate of an
    invalid record cannot survive by being the copy that was kept."""

    broken = Bar("AAPL", 1.0, open=10.0, high=8.0, low=9.0, close=10.0, volume=1.0)
    good = _bar("AAPL", 1.0)
    findings = validate_records([broken, good], None)

    outcome = clean_records([broken, good], PERMISSIVE, findings)

    assert outcome.records == (good,), "the invalid copy went first, the valid one survived"
    assert [t.operation for t in outcome.transformations] == ["drop_invalid_record"]


def test_every_change_is_recorded_and_a_no_op_records_nothing() -> None:
    clean = [_bar("AAPL", 1.0), _bar("AAPL", 2.0)]

    assert clean_records(clean, PERMISSIVE, []).transformations == ()


# --------------------------------------------------------------------------- #
# 9. Neither door onto the parser can lose a row
# --------------------------------------------------------------------------- #
#
# ``alphalab.data`` has exactly one parsing implementation -- ``coerce_row`` --
# and two doors onto it:
#
#   * ``parse_raw_rows``  (via ``UniversalDataEngine.load``) returns bars, and
#     **refuses** if any row cannot become one;
#   * ``ingest_rows``     (the canonical v3.1 path) returns a dataset *and* a
#     ``DataQualityReport``, so it can ingest the good rows while **reporting**
#     the bad ones.
#
# Before v3.1 the first door dropped unusable rows and returned the rest with no
# indication that anything had gone. These tests hold both doors to the rule
# that neither may lose a row quietly, and to agreeing about which rows those
# are -- because two doors onto one parser that disagreed would mean two
# different datasets from one input, with only one of them able to explain
# itself.


def _rows_in(*, bad: int) -> list[dict[str, str]]:
    """Five bar rows, ``bad`` of which cannot be translated."""

    rows = [
        {
            "timestamp": str(1000 + index),
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "volume": "100",
        }
        for index in range(5)
    ]
    for index in range(bad):
        rows[index + 1]["close"] = ["", "not-a-number", "inf"][index % 3]
    return rows


def _ingest(rows: list[dict[str, str]]):  # type: ignore[no-untyped-def]
    from alphalab.api import ingest_rows
    from alphalab.data import (
        DataAssetClass,
        IngestionRequest,
        PriceBasis,
        SourceKind,
        TimeFrequency,
        raw_source_from_bytes,
    )

    return ingest_rows(
        rows,
        IngestionRequest(
            name="DOORS",
            source=raw_source_from_bytes(SourceKind.IN_MEMORY, "fixture", b"rows", 1.0, "text/csv"),
            frequency=TimeFrequency.SECOND,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=PERMISSIVE,
            price_basis=PriceBasis.RAW,
            symbol="AAPL",
        ),
    )


def test_the_canonical_path_never_loses_a_row_without_reporting_it() -> None:
    """Every row is accounted for: it became a record, or it is in the report."""

    rows = _rows_in(bad=2)

    result = _ingest(rows)
    quality = result.quality

    assert quality.row_count == len(rows)
    assert quality.valid_rows + quality.rejected_count == quality.row_count, (
        "a row that is neither a record nor a rejection has been lost"
    )
    assert quality.valid_rows == 3
    assert quality.rejected_count == 2


def test_every_rejected_row_carries_its_position_values_and_reason() -> None:
    """'Observable' means a person can find the row in their own file."""

    result = _ingest(_rows_in(bad=2))

    for rejection in result.quality.rejected_rows:
        assert rejection.line_number > 0
        assert rejection.values, "the row's own values are kept, not just its index"
        assert rejection.findings, "and at least one reason"
        assert rejection.findings[0].detail

    kinds = {f.kind for r in result.quality.rejected_rows for f in r.findings}
    assert kinds == {FindingKind.MISSING_VALUE, FindingKind.NON_NUMERIC}


def test_the_valid_rows_survive_alongside_the_rejected_ones() -> None:
    """Reporting a bad row must not cost the good ones."""

    result = _ingest(_rows_in(bad=2))
    stamps = sorted(record.timestamp for record in result.dataset.records)

    assert stamps == [1000.0, 1003.0, 1004.0], "exactly the rows that were translatable"


def test_the_refusing_door_refuses_exactly_what_the_reporting_door_rejects() -> None:
    """The non-drift property, stated as an equivalence over several inputs.

    ``parse_raw_rows`` raises if and only if ``ingest_rows`` reports at least
    one rejection. One door cannot start accepting something the other refuses
    without breaking this.
    """

    from alphalab.data import parse_raw_rows

    for bad in (0, 1, 2, 3):
        rows = _rows_in(bad=bad)
        reported = _ingest(rows).quality.rejected_count

        refused = False
        try:
            parse_raw_rows("AAPL", rows)
        except DataValidationError:
            refused = True

        assert refused is (reported > 0), (
            f"with {bad} unusable rows the reporting door rejected {reported} "
            f"and the refusing door {'refused' if refused else 'accepted'}"
        )


def test_when_nothing_is_wrong_the_two_doors_produce_identical_bars() -> None:
    """Agreement is not only about failure: the records must match too."""

    from alphalab.data import parse_raw_rows

    rows = _rows_in(bad=0)

    from_parser = parse_raw_rows("AAPL", rows)
    from_pipeline = _ingest(rows).dataset.records

    assert from_parser == from_pipeline


def test_the_refusal_names_the_path_that_can_do_a_partial_load() -> None:
    """A refusal that leaves a caller stuck is a worse answer than a drop."""

    from alphalab.data import parse_raw_rows

    with pytest.raises(DataValidationError) as error:
        parse_raw_rows("AAPL", _rows_in(bad=1))

    assert "ingest_rows" in str(error.value)
    assert "DataQualityReport" in str(error.value)


def test_there_is_one_parsing_implementation_behind_both_doors() -> None:
    """The property that makes the agreement above structural rather than lucky.

    ``parse_raw_rows`` had its own float coercion, its own symbol fallback and
    its own row mapping until v3.1. Two implementations of one job drift; this
    asserts there is now one, reached through ``coerce_row``.
    """

    import inspect

    from alphalab.data import parser

    source = inspect.getsource(parser)
    assert "coerce_row" in source, "the v1 door delegates to the canonical coercion"
    assert "RawTable.from_rows" in source, "and to the canonical table construction"
    assert "float(" not in source, "and keeps no numeric coercion of its own"
