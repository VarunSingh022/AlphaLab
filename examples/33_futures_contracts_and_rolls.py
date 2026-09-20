"""
AlphaLab Examples
=================

Example 33 : Futures Contracts and Roll Rules

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)
✓ Example 32 (exchange calendars and sessions)

Topics
------

• A contract chain, and the three ways it refuses an incoherent one
• Three roll triggers, and the three different schedules they produce
• Which contract was front, at any instant
• The curve: contango, backwardation, and the humped curve that is neither
• Margin as a published figure, refused when it is not supplied

What this shows
---------------

Every futures research result depends on the roll rule, and a continuous series
whose rule was assumed is a result nobody can reproduce. This example rolls one
chain three ways and prints three different schedules from the same contracts.

Run

    python examples/33_futures_contracts_and_rolls.py
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal

from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.futures import (
    ContractChain,
    ContractMarginSpec,
    FutureContract,
    FuturesCurve,
    FuturesCurvePoint,
    FuturesInputError,
    RollPolicy,
    RollTrigger,
    active_contract_at,
    contract_tick_value,
    curve_shape,
    curve_slope,
    futures_symbol,
    position_margin,
    roll_schedule,
    roll_yield,
)
from alphalab.market.bar import Bar, TimeFrame

DAY = 86400.0
CME = MarketCalendar(
    calendar_id="XCME",
    timezone_name="America/Chicago",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(17, 0), time(16, 0)),)),
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def contract(month: int, *, multiplier: int = 1000, currency: str = "USD") -> FutureContract:
    return FutureContract(
        underlying_asset_id="CL",
        contract_month=datetime(2026, month, 1, tzinfo=UTC).timestamp(),
        expiry=datetime(2026, month, 20, tzinfo=UTC).timestamp(),
        multiplier=multiplier,
        tick_size=Decimal("0.01"),
        currency=currency,
    )


def bar(symbol: str, timestamp: float, close: str, volume: str) -> Bar:
    price = Decimal(close)
    return Bar(
        symbol, timestamp, price, price, price, price, Decimal(volume), price, 1, TimeFrame.D1
    )


def main() -> None:
    print("=" * 68)
    print("Example 33 : Futures Contracts and Roll Rules")
    print("=" * 68)

    march, june, september = contract(3), contract(6), contract(9)
    chain = ContractChain("CL", (march, june, september))

    # ----------------------------------------------------------------- #
    rule("The chain")

    for index, month in enumerate(chain.contracts):
        expiry = datetime.fromtimestamp(month.expiry, tz=UTC).date()
        tick = contract_tick_value(month)
        print(
            f"  {index}  {futures_symbol(month):<12} expires {expiry}  "
            f"multiplier {month.multiplier}  tick {tick.tick_size} "
            f"= {tick.amount} {tick.currency}"
        )

    # ----------------------------------------------------------------- #
    rule("A chain asserts the months are one instrument")

    for label, bad in (
        ("two multipliers", (march, contract(6, multiplier=500))),
        ("two currencies", (march, contract(6, currency="EUR"))),
        ("out of order", (june, march)),
    ):
        try:
            ContractChain("CL", bad)
            print(f"  {label:<18} NOT REFUSED")
        except FuturesInputError as error:
            print(f"  {label:<18} refused: {str(error).split('.')[0][:52]}")

    # ----------------------------------------------------------------- #
    rule("Three roll rules, three schedules, one chain")

    observations = {
        futures_symbol(march): tuple(
            bar(futures_symbol(march), i * DAY, "70.00", "100" if i < 3 else "5") for i in range(10)
        ),
        futures_symbol(june): tuple(
            bar(
                futures_symbol(june), i * DAY, "72.00", "50" if i < 3 else ("200" if i < 7 else "5")
            )
            for i in range(10)
        ),
        futures_symbol(september): tuple(
            bar(futures_symbol(september), i * DAY, "74.00", "10" if i < 7 else "300")
            for i in range(10)
        ),
    }

    schedules = {
        "5 calendar days before expiry": roll_schedule(
            chain, RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5)
        ),
        "5 trading days before expiry": roll_schedule(
            chain, RollPolicy(RollTrigger.TRADING_DAYS_BEFORE_EXPIRY, 5), CME
        ),
        "volume crossover": roll_schedule(
            chain, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
        ),
    }
    for label, events in schedules.items():
        print(f"  {label}")
        for event in events:
            moment = datetime.fromtimestamp(event.timestamp, tz=UTC)
            print(
                f"    {moment:%Y-%m-%d %H:%M}  {futures_symbol(event.outgoing)} -> "
                f"{futures_symbol(event.incoming)}"
            )
    print()
    print("  Same chain, three answers. That is why the policy is an argument and")
    print("  has no default: a series whose rule was assumed cannot be reproduced.")

    # ----------------------------------------------------------------- #
    rule("A rule refuses the input it needs and was not given")

    try:
        roll_schedule(chain, RollPolicy(RollTrigger.VOLUME_CROSSOVER))
        print("  NOT REFUSED")
    except FuturesInputError as error:
        print(f"  volume crossover, no bars: {str(error)[:62]}")
    try:
        roll_schedule(chain, RollPolicy(RollTrigger.TRADING_DAYS_BEFORE_EXPIRY, 5))
        print("  NOT REFUSED")
    except FuturesInputError as error:
        print(f"  trading days, no calendar: {str(error)[:62]}")

    # ----------------------------------------------------------------- #
    rule("Which contract was front")

    rolls = schedules["volume crossover"]
    for day in (0, 2, 3, 6, 7, 9):
        front = active_contract_at(chain, rolls, day * DAY)
        print(f"  day {day}  ->  {futures_symbol(front)}")

    # ----------------------------------------------------------------- #
    rule("The curve: three shapes, and the one the endpoints get wrong")

    curves = {
        "rising": ("70.00", "71.00", "72.00"),
        "falling": ("72.00", "71.00", "70.00"),
        "humped": ("70.00", "68.00", "71.00"),
    }
    print(f"  {'curve':<10} {'prices':<26} {'endpoint slope':>16}  pairwise shape")
    print("  " + "-" * 66)
    for label, prices in curves.items():
        curve = FuturesCurve(
            "CL",
            0.0,
            tuple(
                FuturesCurvePoint(float(index) * 90 * DAY, Decimal(price))
                for index, price in enumerate(prices)
            ),
        )
        print(
            f"  {label:<10} {', '.join(prices):<26} {curve_slope(curve):>16.6f}  "
            f"{curve_shape(curve).name}"
        )
    print()
    print("  The humped curve slopes up between its endpoints and is not in")
    print("  contango. A spread trades the near pair, which is backwardated.")

    # ----------------------------------------------------------------- #
    rule("Roll yield, annualized")

    backwardated = FuturesCurve(
        "CL",
        0.0,
        (
            FuturesCurvePoint(0.0, Decimal("72.00")),
            FuturesCurvePoint(90 * DAY, Decimal("70.00")),
        ),
    )
    quarterly = roll_yield(backwardated, 0.0, 90 * DAY)
    print(f"  long the front against the 3-month: {quarterly:.4f} per year")
    print("  Positive, because the near month rolls up the curve toward the far")
    print("  one as it ages. Annualized, so a one-month and a six-month pickup")
    print("  are comparable rather than merely both being '2%'.")

    # ----------------------------------------------------------------- #
    rule("Margin is a published figure, not a computed one")

    try:
        position_margin(march, Decimal("3"), {}, as_of=1.0)
        print("  NOT REFUSED")
    except FuturesInputError as error:
        print(f"  with no specification : {str(error)[:60]}")

    spec = ContractMarginSpec(
        contract_symbol=futures_symbol(march),
        initial=Decimal("6270"),
        maintenance=Decimal("5700"),
        currency="USD",
        as_of=datetime(2026, 1, 2, tzinfo=UTC).timestamp(),
    )
    posture = position_margin(
        march,
        Decimal("-3"),
        {spec.contract_symbol: spec},
        as_of=datetime(2026, 2, 1, tzinfo=UTC).timestamp(),
    )
    print(
        f"  short {posture.contracts} contracts : initial {posture.initial} "
        f"{posture.currency}, maintenance {posture.maintenance} {posture.currency}"
    )
    print("  A short is margined exactly like a long; the sign does not reduce it.")

    published_later = ContractMarginSpec(
        futures_symbol(march),
        Decimal("6270"),
        Decimal("5700"),
        "USD",
        as_of=datetime(2026, 6, 1, tzinfo=UTC).timestamp(),
    )
    try:
        position_margin(
            march,
            Decimal("1"),
            {published_later.contract_symbol: published_later},
            as_of=datetime(2026, 2, 1, tzinfo=UTC).timestamp(),
        )
        print("  NOT REFUSED")
    except FuturesInputError as error:
        print(f"  published in June, read in February : {str(error)[:52]}")

    print("\n" + "=" * 68)
    print("Example 33 complete.")
    print(f"(Calendar declared here for {CME.calendar_id}; AlphaLab ships no holiday data.)")
    print(f"(Curve date basis: {date(2026, 1, 1)} onward, all figures stated in this file.)")


if __name__ == "__main__":
    main()
