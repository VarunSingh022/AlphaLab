"""
AlphaLab Examples
=================

Example 04 : Market Data

Difficulty : Beginner

Estimated Time : 5 minutes

Topics
------

• A provider is the host application's; its output is wire records
• The instrument registry as the one identity authority
• A normalization policy that states what the wire cannot: venue, currency,
  timeframe, whose symbols these are, and when a bar is stamped
• Start-stamped bars moved to the end of their interval, where a close is known
• A provider history source, and the canonical market state it feeds

What this shows
---------------

AlphaLab ships no vendor clients. Fetching bars from a venue -- its endpoints,
its symbols, its credentials -- is the host application's job, and the whole
contract between the two is one method, ``request_history``, returning wire
bars: ``float`` prices under the provider's symbol. Everything after that is
AlphaLab's: the wire bars are lifted into canonical ``Decimal`` bars keyed by a
registered ``asset_id``, and the policy that does it has to say what the wire
bar cannot. This provider stamps each bar at the *start* of its minute, as most
vendors' intraday bars are; the canonical bar is stamped at the minute's end,
because that is when its close is known.

Run

    python examples/04_market_data.py
"""

from datetime import UTC, datetime

from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.data.time import BarStamp
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market import (
    Bar,
    MarketEngine,
    MarketState,
    NormalizationPolicy,
    ProviderHistorySource,
    TimeFrame,
    latest_bar,
)
from alphalab.marketdata import Bar as WireBar
from alphalab.marketdata import Timeframe

#: 2025-01-02 14:30:00 UTC, the first minute of the regular session.
SESSION_OPEN = 1_735_828_200.0

#: One minute of AAPL per row: open, high, low, close, volume.
MINUTES = (
    (243.85, 244.10, 243.60, 244.02, 18_250.0),
    (244.02, 244.40, 243.95, 244.31, 12_730.0),
    (244.31, 244.35, 243.80, 243.88, 15_110.0),
)


class ExampleVendorBars:
    """A host application's provider: the one method AlphaLab asks of it.

    A real one would make a request here; this one answers from ``MINUTES``.
    Its timestamps are the vendor's convention -- the start of each minute.
    """

    def request_history(
        self, symbol: str, timeframe: Timeframe, start: float, end: float
    ) -> tuple[WireBar, ...]:
        if timeframe is not Timeframe.MINUTE:
            return ()
        return tuple(
            WireBar(symbol, SESSION_OPEN + 60.0 * index, *row)
            for index, row in enumerate(MINUTES)
            if start <= SESSION_OPEN + 60.0 * index < end
        )


def _clock(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%H:%M UTC")


def main() -> None:
    """Take a provider's bars into the canonical market model."""

    # ------------------------------------------------------------
    # Step 1 : The instrument, and who calls it what
    # ------------------------------------------------------------

    aapl = InstrumentRecord(
        symbol="AAPL",
        asset_type=AssetType.EQUITY,
        exchange="XNAS",
        currency="USD",
        aliases={"example-vendor": "AAPL"},
    )
    registry = register_instrument(InstrumentRegistry(), aapl)

    # ------------------------------------------------------------
    # Step 2 : What the wire cannot say, stated once
    # ------------------------------------------------------------

    policy = NormalizationPolicy(
        venue="XNAS",
        currency="USD",
        timeframe=TimeFrame.M1,
        identity=registry,
        provider="example-vendor",
        bar_stamp=BarStamp.INTERVAL_START,
    )

    # ------------------------------------------------------------
    # Step 3 : Provider -> wire bars -> canonical records
    # ------------------------------------------------------------

    source = ProviderHistorySource.of(
        ExampleVendorBars(),
        ["AAPL"],
        Timeframe.MINUTE,
        SESSION_OPEN,
        SESSION_OPEN + 3 * 60.0,
        "EXAMPLE-AAPL-1M",
        policy,
    )

    # ------------------------------------------------------------
    # Step 4 : Canonical records -> market state
    # ------------------------------------------------------------

    with id_scope(4):
        state = MarketState()
        for record in source.records():
            assert isinstance(record.payload, Bar)
            state = MarketEngine.publish_bar(state, record.payload)

    print("=" * 60)
    print("AlphaLab Example 04")
    print("Market Data")
    print("=" * 60)
    print()

    print(f"Instrument        : {aapl.symbol} on {aapl.exchange} -> {aapl.asset_id}")
    print(f"Provider bars     : {len(MINUTES)}, stamped at the start of each minute")
    print(f"Canonical records : {len(source)}")
    print()

    print(f"{'vendor stamp':>13}  {'canonical stamp':>15}  {'close':>8}  {'volume':>8}")
    for index, record in enumerate(source.records()):
        bar = record.payload
        assert isinstance(bar, Bar)
        vendor_stamp = SESSION_OPEN + 60.0 * index
        print(
            f"{_clock(vendor_stamp):>13}  {_clock(bar.timestamp):>15}  "
            f"{bar.close:>8}  {bar.volume:>8}"
        )
    print()

    last = latest_bar(state, aapl.asset_id, TimeFrame.M1.value)
    assert last is not None
    print(f"Latest bar        : close {last.close} {policy.currency} at {_clock(last.timestamp)}")
    print(f"Market events     : {len(state.events)}")
    print(f"Record identities : {[record.event_id for record in source.records()]}")


if __name__ == "__main__":
    main()
