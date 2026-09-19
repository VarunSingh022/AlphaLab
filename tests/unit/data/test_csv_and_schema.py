"""Reading real CSV files, and working out what is in them.

CSV is the format users actually have, and the format with the least agreement
about what it is. These tests cover the variation a vendor export actually
shows -- delimiters, casing, header spellings, quoting, encodings, headerless
files -- and the line past which AlphaLab stops guessing.
"""

from __future__ import annotations

import pytest

from alphalab.data import (
    COLUMN_ALIASES,
    CsvDialect,
    DataValidationError,
    FieldRole,
    RecordType,
    TimeFrequency,
    TimestampFormat,
    canonical_field,
    decode_text,
    detect_delimiter,
    detect_schema,
    frequency_seconds,
    infer_frequency,
    read_delimited,
)

BAR_CSV = "timestamp,open,high,low,close,volume\n1700000000,10,12,9,11,100\n"


# --------------------------------------------------------------------------- #
# Delimiters
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a,b,c\n1,2,3\n", ","),
        ("a;b;c\n1;2;3\n", ";"),
        ("a\tb\tc\n1\t2\t3\n", "\t"),
        ("a|b|c\n1|2|3\n", "|"),
    ],
)
def test_each_supported_delimiter_is_detected(text: str, expected: str) -> None:
    delimiter, rationale = detect_delimiter(text)

    assert delimiter == expected
    assert repr(expected) in rationale, "the rationale names the character it chose"


def test_a_ragged_row_does_not_prevent_detecting_the_delimiter() -> None:
    """'Which delimiter is this?' and 'is this file well-formed?' are different
    questions. A file with one bad row is still obviously comma-delimited."""

    delimiter, rationale = detect_delimiter("sym,px\nAAPL,10\nMSFT,20,EXTRA\nGOOG,30\n")

    assert delimiter == ","
    assert "ragged" in rationale, "and the raggedness is still reported"


def test_a_file_delimited_by_nothing_recognised_is_refused() -> None:
    delimiter, rationale = detect_delimiter("just one column\nand another row\n")

    assert delimiter is None
    assert "not delimited" in rationale


def test_a_declared_dialect_overrides_detection_entirely() -> None:
    table = read_delimited("a;b\n1;2\n", CsvDialect(";"))

    assert table.columns == ("a", "b")
    assert table.dialect.delimiter == ";"


# --------------------------------------------------------------------------- #
# Headers and quoting
# --------------------------------------------------------------------------- #


def test_header_case_and_surrounding_space_are_not_meaningful() -> None:
    table = read_delimited("  Symbol , TIMESTAMP ,Close \nAAPL,1,10\n", CsvDialect(","))

    assert table.columns == ("symbol", "timestamp", "close")


def test_a_quoted_delimiter_stays_inside_its_field() -> None:
    table = read_delimited('sym,name\nAAPL,"Apple, Inc."\n', CsvDialect(","))

    assert table.as_mapping(table.rows[0])["name"] == "Apple, Inc."


def test_a_headerless_file_gets_stable_positional_names() -> None:
    table = read_delimited("1,2,3\n4,5,6\n", CsvDialect(",", has_header=False))

    assert table.columns == ("column_0", "column_1", "column_2")
    assert len(table.rows) == 2


def test_a_blank_column_name_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        read_delimited("a,,c\n1,2,3\n", CsvDialect(","))

    assert "cannot be referred to" in str(error.value)


def test_blank_lines_are_skipped_rather_than_rejected() -> None:
    table = read_delimited("a,b\n1,2\n\n3,4\n", CsvDialect(","))

    assert len(table.rows) == 2
    assert table.malformed == ()


def test_a_byte_order_mark_does_not_become_part_of_the_first_column_name() -> None:
    text = decode_text("﻿symbol,close\nAAPL,10\n".encode(), "utf-8")
    table = read_delimited(text, CsvDialect(","))

    assert table.columns == ("symbol", "close")


def test_content_that_cannot_be_decoded_is_refused_rather_than_mangled() -> None:
    """``errors='replace'`` would put U+FFFD into a symbol or a price."""

    with pytest.raises(DataValidationError) as error:
        decode_text(b"\xff\xfe\x00bad", "utf-8")

    assert "Name the encoding" in str(error.value)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(DataValidationError):
        read_delimited("   \n", CsvDialect(","))


# --------------------------------------------------------------------------- #
# Column aliases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("Date", "timestamp"),
        ("DATETIME", "timestamp"),
        ("Ticker", "symbol"),
        ("instrument", "symbol"),
        ("Adj Close", "close"),
        ("adj_close", "close"),
        ("AdjClose", "close"),
        ("Vol", "volume"),
        ("bid_price", "bid"),
        ("Offer", "ask"),
        ("asksize", "ask_size"),
        ("num_trades", "trade_count"),
    ],
)
def test_vendor_spellings_resolve_to_one_canonical_field(spelling: str, canonical: str) -> None:
    assert canonical_field(spelling) == canonical


def test_an_unrecognised_column_keeps_its_own_name_rather_than_disappearing() -> None:
    assert canonical_field("  Some Vendor Column ") == "some vendor column"


def test_the_alias_table_is_the_only_one() -> None:
    """Schema detection reads ``COLUMN_ALIASES`` rather than keeping a copy,
    which is what kept the parser and the detector from drifting apart."""

    assert COLUMN_ALIASES["date"] == "timestamp"
    assert COLUMN_ALIASES["adj close"] == "close"

    table = read_delimited("Date,Open,High,Low,Close\n1,1,2,0.5,1.5\n", CsvDialect(","))
    detection = detect_schema(table)

    assert detection.column_for(FieldRole.TIMESTAMP) == "date"


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_single_letter_columns_resolve_to_a_bar() -> None:
    table = read_delimited("t,o,h,l,c,v\n1700000000,1,2,0.5,1.5,100\n", CsvDialect(","))
    detection = detect_schema(table)

    assert detection.record_type is RecordType.BAR
    assert detection.is_resolved is True
    assert detection.column_for(FieldRole.OPEN) == "o"


def test_a_bid_ask_table_resolves_to_a_quote() -> None:
    table = read_delimited(
        "timestamp,bid,ask,bid_size,ask_size\n1700000000,10.0,10.1,5,7\n", CsvDialect(",")
    )
    detection = detect_schema(table)

    assert detection.record_type is RecordType.QUOTE
    assert detection.column_for(FieldRole.BID_SIZE) == "bid_size"
    assert "bid and ask both resolved" in detection.record_type_rationale


def test_a_bar_missing_a_price_names_the_role_that_is_unresolved() -> None:
    table = read_delimited("timestamp,open,high,close\n1,1,2,1.5\n", CsvDialect(","))
    detection = detect_schema(table, RecordType.BAR)

    assert detection.is_resolved is False
    assert detection.unresolved == (FieldRole.LOW,)


def test_every_binding_carries_the_reason_it_was_made() -> None:
    """Heuristics must not be invisible."""

    table = read_delimited(BAR_CSV, CsvDialect(","))
    detection = detect_schema(table)

    for binding in detection.bindings:
        assert binding.rationale, f"{binding.role.name} was bound with no stated reason"
        assert binding.column in binding.rationale


def test_the_settled_schema_records_which_column_filled_each_role() -> None:
    from alphalab.data import DatasetSchema

    table = read_delimited("Date,O,H,L,C\n1700000000,1,2,0.5,1.5\n", CsvDialect(","))
    schema = DatasetSchema.from_detection(detect_schema(table), "UTC", table.columns)

    assert schema.data_type == "BAR"
    assert schema.bindings["CLOSE"] == "c"
    assert schema.timestamp_format == TimestampFormat.EPOCH_SECONDS.name
    assert schema.timezone == "UTC"
    assert schema.source_columns == ("date", "o", "h", "l", "c")


# --------------------------------------------------------------------------- #
# Frequency
# --------------------------------------------------------------------------- #


def test_a_regular_series_reports_its_frequency_and_the_share_that_agrees() -> None:
    frequency, rationale = infer_frequency([0.0, 86400.0, 172800.0])

    assert frequency is TimeFrequency.DAILY
    assert "100%" in rationale


def test_a_series_with_weekend_gaps_still_reports_daily_with_a_lower_share() -> None:
    """Which spacing is 'the' frequency is a judgement, so the share is
    reported and the caller decides whether it is convincing."""

    frequency, rationale = infer_frequency([0.0, 86400.0, 172800.0, 432000.0])

    assert frequency is TimeFrequency.DAILY
    assert "67%" in rationale


def test_a_spacing_matching_no_named_frequency_is_custom() -> None:
    frequency, _ = infer_frequency([0.0, 7.0, 14.0])

    assert frequency is TimeFrequency.CUSTOM


def test_a_single_timestamp_has_no_frequency() -> None:
    frequency, rationale = infer_frequency([1.0])

    assert frequency is None
    assert "fewer than two" in rationale


def test_monthly_has_no_fixed_length_and_says_so() -> None:
    """A month is 28 to 31 days; one number for it would be an invented average
    that silently mis-bins every calendar month."""

    assert frequency_seconds(TimeFrequency.MONTHLY) is None
    assert frequency_seconds(TimeFrequency.TICK) is None
    assert frequency_seconds(TimeFrequency.CUSTOM) is None
    assert frequency_seconds(TimeFrequency.WEEKLY) == 604800.0
