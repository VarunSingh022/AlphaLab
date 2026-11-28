"""What bootstrapping a curve costs at the plan's scale (v4.3, ADR-0065 section 3).

The plan budgets one second, in pure Python, for a 30-instrument curve (plan
WS-14 item 15). The curve here is a dollar curve of that shape, synthetic and
labelled so: four deposits, three FRAs and three futures over the first
eighteen months, and twenty par swaps from two years to fifty, annual fixed
against quarterly floating, under modified following on a weekday calendar.
It is bootstrapped under each interpolation to a tolerance of 1e-14 on each
pillar's log discount factor.

Each figure is judged through ``_stable_timing``: CPU time, collector paused,
fastest of several.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.conventions import BusinessDayConvention, DayCount, Tenor
from alphalab.rates import (
    CurveBootstrap,
    DepositQuote,
    FraQuote,
    FutureQuote,
    Interpolation,
    RateQuote,
    SwapQuote,
    bootstrap_curve,
)

#: The plan's budget for a 30-instrument curve, in CPU seconds.
BUDGET_SECONDS = 1.0
ANCHOR = date(2025, 1, 15)
SPOT = date(2025, 1, 17)


@dataclass(frozen=True)
class Weekdays:
    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5


def _quotes() -> list[RateQuote]:
    quotes: list[RateQuote] = [
        DepositQuote(SPOT, SPOT + timedelta(days=days), 0.0430 + 0.0001 * i, DayCount.ACT_360)
        for i, days in enumerate((1, 7, 30, 61))
    ]
    starts = [date(2025, 4, 17), date(2025, 7, 17), date(2025, 10, 17)]
    quotes += [
        FraQuote(start, start + timedelta(days=91), 0.0425 - 0.0005 * i, DayCount.ACT_360)
        for i, start in enumerate(starts)
    ]
    futures = [date(2026, 1, 21), date(2026, 4, 15), date(2026, 7, 15)]
    quotes += [
        FutureQuote(
            start, start + timedelta(days=91), 96.0 + 0.03 * i, DayCount.ACT_360, 0.0001 * i
        )
        for i, start in enumerate(futures)
    ]
    calendars = (Weekdays(),)
    for years in [*range(2, 16), 20, 25, 30, 35, 40, 50]:
        quotes.append(
            SwapQuote(
                SPOT,
                Tenor.parse(f"{years}Y"),
                0.0380 + 0.0002 * min(years, 20),
                Tenor.parse("1Y"),
                DayCount.THIRTY_360_US,
                Tenor.parse("3M"),
                BusinessDayConvention.MODIFIED_FOLLOWING,
                calendars,
                end_of_month=False,
            )
        )
    return quotes


def run_benchmark() -> int:
    quotes = _quotes()
    print(f"Starting Rates Benchmark (a {len(quotes)}-instrument curve)...")
    failures = 0
    for interpolation in Interpolation:

        def bootstrap(interpolation: Interpolation = interpolation) -> CurveBootstrap:
            return bootstrap_curve(
                "USD-SYNTHETIC",
                "USD",
                ANCHOR,
                DayCount.ACT_365_FIXED,
                interpolation,
                quotes,
                tolerance=1e-14,
                max_iterations=100,
            )

        result = bootstrap()
        (seconds,) = fastest([bootstrap])
        verdict = "ok" if seconds <= BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
        failures += verdict != "ok"
        largest = result.largest_error
        print(
            f"  {interpolation.value}: {seconds:.3f} CPU s (budget {BUDGET_SECONDS:.0f} s), "
            f"largest repricing error {largest:.1e} {verdict}"
        )
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
