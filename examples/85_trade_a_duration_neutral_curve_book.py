"""
AlphaLab Examples
=================

Example 85 : Trade a Duration-Neutral Curve Book for a Year

Difficulty : Advanced

Estimated Time : 20 minutes

Prerequisites
-------------

✓ Example 80 (a ledgered sweep)
✓ Example 84 (carry a book of FX forwards)

Topics
------

• Curves bootstrapped every week under a named interpolation: a swap curve
  from a deposit and par swaps, a Treasury curve from par notes at ACT/ACT
  (ICMA)
• A two-year Treasury note held against a ten-year payer swap, each marked
  off its own curve, the swap's coupons booked from fixings as they were
  published
• Run as an experiment: two ways to size the swap against the note, each a
  trial counted in the ledger

What this shows
---------------

Plan scenario S-06, rates relative value, and WS-14's end to end (v4.3,
ADR-0065). The experiment is ``_rates_book``'s, which keeps its identity
wherever it is run from. The book buys a million dollars of face of a
two-year note and pays fixed on a ten-year swap:

* ``duration``: the swap's notional makes the book's value unmoved by a
  parallel basis point on both curves at inception, so the book trades the
  curve's shape and the swap spread;
* ``notional``: the swap's notional is the note's face, so the book is short
  the level of rates, a ten-year's duration against a two-year's.

Every payment is booked by the run: the note's coupon, and the swap's coupons
netted on each date from the three-month fixings published two business days
before each period. The markets are **synthetic**, random walks, so nothing
here says whether the curve steepens. ``tests/integration/test_rates_end_to_end.py``
holds it.

Run

    python examples/85_trade_a_duration_neutral_curve_book.py
"""

from __future__ import annotations

from _rates_book import (
    COUPON,
    FIRST,
    LAST,
    NOTE_DV01,
    OBSERVED,
    SPEC,
    STATES,
    SWAP_DV01,
    SWAP_RATE,
    growth,
    ledger,
    run,
    swap_curve,
    swap_notional,
    treasury_curve,
)

from alphalab.lifecycle.experiments import TrialOutcome


def main() -> None:
    print("Example 85: a duration-neutral curve book, for a year\n")
    swaps, treasuries = swap_curve(FIRST), treasury_curve(FIRST)
    method = swaps.curve.interpolation.value
    print(f"{len(OBSERVED)} weekly curves from {FIRST} to {LAST}, each under {method}")
    print(f"  the swap curve reprices its 7 quotes to {swaps.largest_error:.1e}")
    print(f"  the Treasury curve reprices its 6 notes to {treasuries.largest_error:.1e} per 100\n")
    matures = FIRST.replace(year=FIRST.year + 2)
    print(f"the note: {COUPON:.3%} to {matures}, a DV01 of {NOTE_DV01 * 1e6:,.0f} a million")
    print(f"the swap: {SWAP_RATE:.3%} for ten years, a DV01 of {SWAP_DV01 * 1e6:,.0f} a million\n")

    trials = SPEC.trials()
    print(f"experiment {SPEC.name}: {len(trials)} trials, experiment_id {SPEC.experiment_id[:16]}…")
    where = ledger()
    record = run(where)
    print(f"recorded {record.counts.recorded}: {record.counts.completed} completed\n")

    first, last = STATES[FIRST], STATES[LAST]
    level, slope, spread = ((late - early) * 1e4 for early, late in zip(first, last, strict=True))
    print(
        f"over the year the level moved {level:+.0f} bp, the slope {slope:+.1f} bp and the "
        f"swap spread {spread:+.1f} bp\n"
    )
    for trial in trials:
        held = where.find(trial.trial_id)
        assert held is not None and held.outcome is TrialOutcome.COMPLETED
        notional = swap_notional(trial.configuration.rule)
        book = float(1_000_000) * NOTE_DV01 - float(notional) * SWAP_DV01
        print(f"sized by {trial.configuration.rule}: pays fixed on {notional:,} of the swap")
        print(f"  the book's DV01 at inception: {book:+,.2f} a basis point")
        print(f"  its captured returns compound to {growth(where, trial):+.2%} over the year\n")

    print("Nothing here says whether the curve steepens: the markets are random walks.")


if __name__ == "__main__":
    main()
