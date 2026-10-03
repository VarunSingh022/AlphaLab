"""A bar's interval is a value, and what a wire bar does not report is absent (DAT-005).

Until v3.10 ``TimeFrame`` was a closed enumeration of eight intervals, a
normalized wire bar carried ``vwap=0`` and ``trade_count=0`` for "not
reported", and a provider was asked for bars in a second, unrelated vocabulary
(``marketdata.Timeframe``). These tests pin the replacement: any positive count
of a unit, one code per interval, ``None`` for an unreported field, one interval
type from the request to the snapshot, and version-4 snapshots still readable.
"""

import copy
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.data.time import BarStamp
from alphalab.market.bar import Bar, IntervalUnit, TimeFrame
from alphalab.market.engine import MarketEngine
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import NormalizationPolicy, normalize_wire_bar
from alphalab.marketdata.feed import Bar as WireBar
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.persistence.upgrade import SchemaUpgradeWarning
from alphalab.runtime.snapshot import capture, from_primitives
from tests.regression.test_pipeline_snapshot import _state

# --------------------------------------------------------------------------- #
# The value
# --------------------------------------------------------------------------- #


def test_the_former_members_are_constants_that_equal_their_spelling() -> None:
    assert TimeFrame(1, IntervalUnit.MINUTE) == TimeFrame.M1
    assert TimeFrame(4, IntervalUnit.HOUR) == TimeFrame.H4
    assert {tf.code for tf in (TimeFrame.M1, TimeFrame.M5, TimeFrame.M15)} == {"1m", "5m", "15m"}
    assert (TimeFrame.H1.code, TimeFrame.D1.code, TimeFrame.W1.code) == ("1h", "1d", "1w")
    assert TimeFrame.MN1.code == "1M"
    assert hash(TimeFrame(1, IntervalUnit.DAY)) == hash(TimeFrame.D1)


@pytest.mark.parametrize(
    ("code", "seconds"),
    [("30m", 1800.0), ("2h", 7200.0), ("10s", 10.0), ("3d", 259200.0), ("2w", 1209600.0)],
)
def test_any_interval_has_a_code_and_a_length(code: str, seconds: float) -> None:
    interval = TimeFrame.parse(code)
    assert interval.code == code
    assert str(interval) == code
    assert interval.seconds == seconds


def test_a_month_has_no_length() -> None:
    assert TimeFrame.MN1.seconds is None
    assert TimeFrame.parse("3M").seconds is None


@pytest.mark.parametrize("code", ["", "0m", "-1m", "1.5h", "1y", "m", "01m", " 1m", 5])
def test_a_malformed_code_is_refused(code: object) -> None:
    with pytest.raises(MarketValidationError, match="interval code"):
        TimeFrame.parse(code)  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [0, -2, True, 1.0])
def test_a_malformed_count_is_refused(count: object) -> None:
    with pytest.raises(MarketValidationError, match="positive whole number"):
        TimeFrame(count, IntervalUnit.MINUTE)  # type: ignore[arg-type]


def test_a_unit_must_be_a_unit() -> None:
    with pytest.raises(MarketValidationError, match="IntervalUnit"):
        TimeFrame(1, "m")  # type: ignore[arg-type]


def test_the_interval_serializes_as_its_code() -> None:
    assert serialize(TimeFrame.parse("30m")) == '"30m"'


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def _policy(interval: TimeFrame, stamp: BarStamp) -> NormalizationPolicy:
    return NormalizationPolicy(timeframe=interval, bar_stamp=stamp)


def test_a_thirty_minute_start_stamped_bar_moves_by_thirty_minutes() -> None:
    wire = WireBar("AAPL", 1_000.0, 10.0, 12.0, 9.0, 11.0, 500.0)
    bar = normalize_wire_bar(wire, _policy(TimeFrame.parse("30m"), BarStamp.INTERVAL_START))
    assert bar.timestamp == 1_000.0 + 1800.0
    assert bar.timeframe == TimeFrame.parse("30m")
    assert bar.vwap is None
    assert bar.trade_count is None


def test_a_start_stamped_monthly_bar_cannot_be_moved() -> None:
    wire = WireBar("AAPL", 1_000.0, 10.0, 12.0, 9.0, 11.0, 500.0)
    with pytest.raises(MarketValidationError, match=r"1M bars.*months"):
        normalize_wire_bar(wire, _policy(TimeFrame.MN1, BarStamp.INTERVAL_START))


def test_a_reported_vwap_outside_the_bar_is_refused() -> None:
    from alphalab.market.validation import validate_bar

    bar = Bar(
        "A",
        1.0,
        Decimal("10"),
        Decimal("12"),
        Decimal("9"),
        Decimal("11"),
        Decimal("5"),
        Decimal("13"),
        4,
        TimeFrame.M1,
    )
    with pytest.raises(MarketValidationError, match="outside its own range"):
        validate_bar(bar)
    validate_bar(replace(bar, vwap=Decimal("10.5")))
    validate_bar(replace(bar, vwap=None, trade_count=None))


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #


def _bar(asset: str, stamp: float, interval: TimeFrame, vwap: Decimal | None) -> Bar:
    return Bar(
        asset,
        stamp,
        Decimal("10"),
        Decimal("12"),
        Decimal("9"),
        Decimal("11"),
        Decimal("100"),
        vwap,
        None if vwap is None else 7,
        interval,
    )


def _state_with_bars() -> Any:
    state, _, _ = _state()
    market = MarketEngine.publish_bar(
        state.market, _bar("BAR-A", 8.0, TimeFrame.parse("30m"), None)
    )
    market = MarketEngine.publish_bar(market, _bar("BAR-B", 9.0, TimeFrame.D1, Decimal("10.5")))
    return replace(state, market=market)


def test_a_bar_round_trips_with_its_interval_and_its_absences() -> None:
    state = _state_with_bars()
    payload = dict(deserialize(serialize(capture(state))))
    assert payload["market"]["latest_bars"]["BAR-A_30m"]["timeframe"] == "30m"
    assert payload["market"]["latest_bars"]["BAR-A_30m"]["vwap"] is None

    decoded = from_primitives(payload)
    bars = decoded.market.latest_bars
    assert bars["BAR-A_30m"] == state.market.latest_bars["BAR-A_30m"]
    assert bars["BAR-B_1d"].vwap == Decimal("10.5")
    assert bars["BAR-B_1d"].trade_count == 7


def _as_version_4(payload: dict[str, Any]) -> dict[str, Any]:
    """What the v3.10 encoder wrote for the same state: enum names, zeros."""

    legacy = {"30m": None, "1d": "TimeFrame.D1", "1m": "TimeFrame.M1"}
    old = copy.deepcopy(payload)

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            for key, item in value.items():
                value[key] = rewrite(item)
            if value.get("timeframe") in legacy:
                name = legacy[value["timeframe"]]
                value["timeframe"] = "TimeFrame.M1" if name is None else name
                value["vwap"] = "0" if value.get("vwap") is None else value["vwap"]
                value["trade_count"] = (
                    0 if value.get("trade_count") is None else value["trade_count"]
                )
        elif isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    rewrite(old["market"])
    # Version 4 kept no record of on_start, which v3.10 never delivered.
    for record in old["strategy"]:
        del record["started"]
    old["schema_version"] = 4
    return old


def test_a_version_4_payload_is_upgraded_to_interval_codes() -> None:
    payload = dict(deserialize(serialize(capture(_state_with_bars()))))
    old = _as_version_4(payload)
    assert old["market"]["latest_bars"]["BAR-B_1d"]["timeframe"] == "TimeFrame.D1"
    # The event log carries the bars in the version-4 spelling as well; a single
    # one left unconverted would be refused by the version-5 decoder.
    assert "TimeFrame.D1" in serialize(old["market"]["events"])

    with pytest.warns(SchemaUpgradeWarning, match=r"subscriptions \['quotes'\]"):
        decoded = from_primitives(old)
    bars = decoded.market.latest_bars
    assert bars["BAR-B_1d"].timeframe == TimeFrame.D1
    # A version-4 zero is carried as written: the payload cannot say whether it
    # meant "not reported".
    assert bars["BAR-A_30m"].timeframe == TimeFrame.M1
    assert bars["BAR-A_30m"].vwap == Decimal("0")
    assert decoded.schema_version == 6  # read through every upgrade to the current version


def test_a_version_4_spelling_in_a_version_5_payload_is_refused() -> None:
    payload = dict(deserialize(serialize(capture(_state_with_bars()))))
    payload["market"]["latest_bars"]["BAR-B_1d"]["timeframe"] = "TimeFrame.D1"
    with pytest.raises(StateDecodeError, match="interval code"):
        from_primitives(payload)
