"""
AlphaLab Examples
=================

Example 34 : Continuous Futures Research

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 33 (futures contracts and roll rules)

Topics
------

• The four separate things a continuous series is built from
• Segments: which bars each contract contributed, and the prices at each roll
• Three adjustment methods, three different series
• Reproducibility: the same four inputs give byte-identical output
• A missing print at a roll refused rather than interpolated

What this shows
---------------

v1 could splice segments a caller had already chosen. What it could not say was
*which* contract was front on any given day, *when* the roll happened, or *why*.
Two researchers with the same contracts and the same bars could produce
different series, both correct, differing only in a choice neither wrote down.

A continuous series here is reproducible from exactly four stated things:
the chain, the roll policy, the observations, and the adjustment method.
Change any one and the series changes, visibly.

Run

    python examples/34_continuous_futures_research.py
"""

from datetime import UTC, datetime
from decimal import Decimal

from alphalab.futures import (
    AdjustmentMethod,
    ContractChain,
    FutureContract,
    FuturesInputError,
    RollPolicy,
    RollTrigger,
    build_continuous_series,
    continuous_segments,
    futures_symbol,
    roll_schedule,
)
from alphalab.market.bar import Bar, TimeFrame

DAY = 86400.0


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def contract(month: int) -> FutureContract:
    return FutureContract(
        underlying_asset_id="CL",
        contract_month=datetime(2026, month, 1, tzinfo=UTC).timestamp(),
        expiry=datetime(2026, month, 20, tzinfo=UTC).timestamp(),
        multiplier=1000,
        tick_size=Decimal("0.01"),
        currency="USD",
    )


def bar(symbol: str, day: int, close: str, volume: str) -> Bar:
    price = Decimal(close)
    return Bar(
        symbol, day * DAY, price, price, price, price, Decimal(volume), price, 1, TimeFrame.D1
    )


def observations() -> dict[str, tuple[Bar, ...]]:
    """Three contracts, each trending gently, each taking over the volume lead.

    The gaps at the rolls are deliberate and unequal -- 70 -> 73 and then
    73 -> 76 -- so the three adjustment methods visibly disagree.
    """

    march, june, september = (futures_symbol(contract(m)) for m in (3, 6, 9))
    return {
        march: tuple(
            bar(march, day, f"{70 + day * 0.1:.2f}", "100" if day < 3 else "5") for day in range(9)
        ),
        june: tuple(
            bar(
                june, day, f"{73 + day * 0.1:.2f}", "50" if day < 3 else ("200" if day < 6 else "5")
            )
            for day in range(9)
        ),
        september: tuple(
            bar(september, day, f"{76 + day * 0.1:.2f}", "10" if day < 6 else "300")
            for day in range(9)
        ),
    }


def main() -> None:
    print("=" * 68)
    print("Example 34 : Continuous Futures Research")
    print("=" * 68)

    chain = ContractChain("CL", (contract(3), contract(6), contract(9)))
    bars = observations()
    policy = RollPolicy(RollTrigger.VOLUME_CROSSOVER)

    # ----------------------------------------------------------------- #
    rule("1. Raw contract observations")

    for symbol in chain.symbols:
        closes = ", ".join(str(b.close) for b in bars[symbol][:4])
        print(f"  {symbol:<12} {len(bars[symbol])} bars, first four closes: {closes}, ...")

    # ----------------------------------------------------------------- #
    rule("2. Roll events -- when, and why")

    rolls = roll_schedule(chain, policy, observations=bars)
    for event in rolls:
        print(f"  day {int(event.timestamp / DAY)}  {event.trigger.name}")
        print(f"          {event.reason}")

    # ----------------------------------------------------------------- #
    rule("3. Segments -- which bars each contract contributed")

    segments = continuous_segments(chain, rolls, bars)
    print(f"  {'contract':<12} {'bars':>5}  {'days':<10} {'out':>7} {'in':>7}  gap")
    print("  " + "-" * 58)
    for segment in segments:
        days = f"{int(segment.bars[0].timestamp / DAY)}-{int(segment.bars[-1].timestamp / DAY)}"
        out = segment.outgoing_roll_price
        into = segment.incoming_roll_price
        gap = "" if out is None or into is None else f"{into - out:+}"
        print(
            f"  {futures_symbol(segment.contract):<12} {len(segment.bars):>5}  {days:<10} "
            f"{out if out is not None else '-':>7} {into if into is not None else '-':>7}  {gap}"
        )
    print()
    print("  Each roll price is a real print at the roll instant, not two closes")
    print("  from days the contracts were never compared on.")

    # ----------------------------------------------------------------- #
    rule("4. Three adjustment methods, three series")

    series = {method: build_continuous_series(segments, method) for method in AdjustmentMethod}
    header = "  day  " + "".join(f"{method.name:>16}" for method in AdjustmentMethod)
    print(header)
    print("  " + "-" * (len(header) - 2))
    cent = Decimal("0.01")
    for index in range(len(series[AdjustmentMethod.UNADJUSTED])):
        row = f"  {index:>3}  "
        for method in AdjustmentMethod:
            # Quantized for the table only. The series itself keeps full
            # precision: a ratio adjustment is a multiplication, and rounding
            # it into the data would make the history depend on how it was
            # printed.
            row += f"{series[method][index].close.quantize(cent):>16}"
        print(row)

    print()
    print("  UNADJUSTED holds the true historical levels and jumps at every roll.")
    print("  BACK_ADJUSTED shifts the history onto the current contract, so dollar")
    print("  differences survive and early prices can go negative over many rolls.")
    print("  RATIO_ADJUSTED scales instead, so percentage returns survive and the")
    print("  absolute dollar P&L of the history does not.")

    # ----------------------------------------------------------------- #
    rule("The anchor is the current contract")

    for method in AdjustmentMethod:
        print(f"  {method.name:<16} last close {series[method][-1].close}")
    print()
    print("  All three agree on the newest bar. That is what makes the series")
    print("  usable today: only the history is restated.")

    # ----------------------------------------------------------------- #
    rule("Reproducibility")

    again = build_continuous_series(
        continuous_segments(chain, roll_schedule(chain, policy, observations=bars), bars),
        AdjustmentMethod.BACK_ADJUSTED,
    )
    identical = again == series[AdjustmentMethod.BACK_ADJUSTED]
    print(f"  same chain, policy, bars and method -> identical series: {identical}")

    other_policy = RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5)
    other_rolls = roll_schedule(chain, other_policy)
    print("  the same chain under a date-based policy rolls elsewhere:")
    for event in other_rolls:
        moment = datetime.fromtimestamp(event.timestamp, tz=UTC)
        print(
            f"    {moment:%Y-%m-%d}  {futures_symbol(event.outgoing)} -> "
            f"{futures_symbol(event.incoming)}   ({event.reason})"
        )
    print("  -- a different series from the same bars, and it says which rule.")

    # ----------------------------------------------------------------- #
    rule("A missing print at a roll is refused, never interpolated")

    without = dict(bars)
    june = futures_symbol(contract(6))
    without[june] = tuple(b for b in bars[june] if b.timestamp != rolls[0].timestamp)
    try:
        continuous_segments(chain, rolls, without)
        print("  NOT REFUSED")
    except FuturesInputError as error:
        print(f"  {str(error)[:66]}")
        print(f"  {str(error)[66:132]}")

    print("\n" + "=" * 68)
    print("Example 34 complete.")


if __name__ == "__main__":
    main()
