# Rates: Curves Under a Named Interpolation, Instruments by Declared Terms

Until v4.3 AlphaLab had no curve bootstrapper and no fixed-income engine
(BDY-014). The objection was never to curves. It was to a library choosing the
interpolation, which changes every forward read off the result. `alphalab.rates`
(v4.3, ADR-0065) reopens the boundary on that objection's own terms: the
interpolation is required and named, and every other choice is the
researcher's, stated and recorded.

None of this says a rates strategy works. It says what each curve is, what each
instrument pays and when, and what each position booked, so a rates book can be
read and reconciled.

---

## Three rules that hold everywhere

**Named, never defaulted.** A curve cannot exist without an `Interpolation`. A
read beyond its last pillar is refused unless it states an `Extrapolation`. An
instrument's day counts, steps, business-day conventions, calendars, reset
lags, indices and fixing sources are each stated, and none has a default
(AC-12).

**Supplied, never shipped.** The engine ships no curve, no quote, no fixing
and no index print (AC-22). Each is the researcher's, and a fixing a payment
needs that nobody supplies, or that was published only after the payment, is
refused.

**Identified by the problem, not by its last bits.** A curve's, a bootstrap's
and a valuation's identities cover their inputs, method, interpolation and
conventions, as ADR-0058 gave estimates. A solved discount factor passes
through `exp` and `log`, so it is recorded beside the identity and never in
it.

---

## Start here

```python
from datetime import date, timedelta

from alphalab.conventions import BusinessDayConvention, Compounding, DayCount, Tenor
from alphalab.rates import DepositQuote, Interpolation, SwapQuote, bootstrap_curve


class Weekdays:
    """A calendar is the caller's; this one is open Monday to Friday."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5


spot = date(2025, 1, 17)
quotes = [
    DepositQuote(spot, spot + timedelta(days=91), 0.0436, DayCount.ACT_360),
    *(
        SwapQuote(
            spot,
            Tenor.parse(f"{years}Y"),
            rate,
            Tenor.parse("1Y"),  # the fixed leg's step
            DayCount.THIRTY_360_US,
            Tenor.parse("3M"),  # the floating leg's
            BusinessDayConvention.MODIFIED_FOLLOWING,
            (Weekdays(),),
            end_of_month=False,
        )
        for years, rate in ((2, 0.0405), (5, 0.0418), (10, 0.0431))
    ),
]
result = bootstrap_curve(
    "USD-SWAP",
    "USD",
    date(2025, 1, 15),
    DayCount.ACT_365_FIXED,
    Interpolation.LOG_LINEAR_DISCOUNT,
    quotes,
    tolerance=1e-14,
    max_iterations=100,
)
curve = result.curve
print(curve.zero_rate(date(2030, 1, 17), Compounding.CONTINUOUS))
print(result.largest_error)  # every quote repriced, in its own units
```

---

## Curves

A `DiscountCurve` is its `Pillar`s, dates with discount factors, in a stated
currency, read on a stated day count's time axis from its anchor, where the
discount factor is one. A basis that needs an instrument's terms (ACT/ACT ICMA,
ACT/365L, 30E/360 ISDA, BUS/252) cannot be a curve's axis.

| Interpolation | Between pillars | Before the first |
| --- | --- | --- |
| `LOG_LINEAR_DISCOUNT` | `ln P` linear in time: piecewise-flat forwards | from `P = 1` at the anchor |
| `LINEAR_ZERO` | the continuously compounded zero rate linear in time | the first pillar's zero rate, flat |

| Extrapolation | Beyond the last pillar |
| --- | --- |
| none stated | refused (`CurveRangeError`) |
| `FLAT_FORWARD` | the last segment's continuously compounded forward continues |
| `FLAT_ZERO` | the last pillar's zero rate continues |

A pillar reads back its own discount factor, bit for bit. A curve answers
`discount`, `zero_rate` under a stated `Compounding`, `forward_rate` under a
stated basis, and `shifted`, which moves every zero rate by exactly its amount
under both interpolations: the bump a DV01 is measured by.

A projection curve for an index is a curve too: its pseudo-discount factors
are read only for forwards. Discounting at one curve and projecting from
another is a choice you state by passing both.

---

## Bootstrapping

`bootstrap_curve` solves each pillar in turn, in order of the last date each
quote reads, by Brent's method on `ln P`, to the tolerance you state, within
the iterations you state. Between the previous pillar and the new one, the
curve is read by its own interpolation while it is solved.

| Quote | States |
| --- | --- |
| `DepositQuote` | a simple rate from its start to its end, under its basis |
| `FraQuote` | an FRA's simple rate over its period |
| `FutureQuote` | a price, and its convexity adjustment: zero states none |
| `SwapQuote` | a par rate, each leg's schedule, the fixed leg's basis |
| `BondQuote` | a fixed-coupon bond's full price at its settlement |

- A quote no discount factor reprices, with the segment's forward anywhere from
  -100% to +1,000% a year, is refused with its residuals (`BootstrapError`).
- A pillar not solved within its iterations is refused too.
- Each quote's `Repricing` is read off the curve returned, and each pillar's
  convergence report is kept.

Given `discount=`, the bootstrap builds a projection curve against it: a
swap's cash flows are discounted at the given curve, and its forwards read off
the one being built. That is OIS discounting, stated.

**GR-6.** The bootstrap reproduces Hull's Treasury bootstrap (*Options,
Futures, and Other Derivatives*, 8th edition, Tables 4.3 and 4.4) to the three
decimals published, under both interpolations. It also reproduces his two-year
swap zero rate of 4.953% (`tests/unit/rates/test_bootstrap.py`).

---

## Instruments by declared terms

| Record type | Terms | One unit held |
| --- | --- | --- |
| `SWAP` | `SwapTerms` | receives the fixed leg, pays the floating; a negative quantity pays fixed |
| `FRA` | `FraTerms` | receives the fixing, pays the contract rate, settled at the start |
| `BOND` | `FloaterTerms` | a floating leg's coupons and a redemption |
| `BOND` | `LinkerTerms` | real coupons and principal scaled by an index ratio |
| `REPO` | `RepoTerms` | one unit of cash lent at a rate against collateral less a haircut |

A bond declaring no terms is a fixed-rate bond, as it always was. Each leg is
a `FixedLeg` or a `FloatingLeg` on a `LegSchedule`: a step, a convention,
calendars by id, an end-of-month rule. A schedule is regular. A length that is
not a whole number of steps would need a stub, and where it goes is the
instrument's choice, so it is refused.

A swap's and an FRA's economics settle as `cash_equity` or
`futures_variation`, and allow a negative price: a unit is worth its value per
unit of notional. A bond's and a repo's settle as `cash_equity`.

A linker reads its reference index from the index's monthly prints, by its
lag in months and its `Indexation`:

- `MONTHLY`: the print of the month `lag` months before;
- `DAILY_INTERPOLATED`: towards the next month's print by the day of the
  month, as US TIPS and post-2005 index-linked gilts do.

---

## Valuing

`value_swap`, `value_fra`, `value_floater`, `value_linker`, `value_repo` and
`value_flows` each value one unit as of the curves' date and return a
`Valuation`: the value, each leg, what is accrued, a par rate where there is
one, and its `valuation_id`.

- A period paid on or before the date is not valued.
- A rate fixed before it is read from the fixings you supply, and refused when
  absent.
- A rate fixing on or after it is projected over the period itself.

A swap struck at a quoted par rate is worth nothing on the curve bootstrapped
from that quote: parity at inception, on one curve and against a stated OIS
curve. A linker is valued off a real curve, at today's index ratio times its
real flows. Its deflation floor's option value is not modelled.

Credit:

- `spread_curve` builds a curve of spread factors, and `spread_value` prices
  flows over it.
- `hazard_curve` builds a `CreditCurve` of survival probabilities with a
  stated recovery, and `risky_value` prices flows on a notional. A default's
  recovery is paid on the payment grid.

Default events are not simulated.

---

## On the run's path

`backtesting.rates_payments` turns a rates record's terms into the schedule's
own entries:

- each coupon is a per-unit `ScheduledDistribution` at its payment instant,
  your `PaymentTime`. A swap's legs are netted on a common date.
- the final payment is a `ScheduledSettlement` closing every position at the
  price its terms state: an FRA's settlement, a repo's repayment, a floater's
  redemption, a linker's indexed principal, a swap at nothing.

Fixings and prints arrive as `PublishedFixing` and `PublishedPrint`, each with
the instant it was published. A payment reads only what was published by its
own instant.

```python
from datetime import time

from alphalab.backtesting import RunSchedule
from alphalab.backtesting.rates_payments import PaymentTime, rates_payments

payments = rates_payments(
    swap_record,  # a SWAP record declaring SwapTerms and its last trading instant
    {"USNY": Weekdays()},
    PaymentTime("America/New_York", time(17)),
    start=first_instant,
    end=last_instant,
    fixings=published,  # each fixing with the instant it was published
)
schedule = RunSchedule.of(distributions=payments.distributions, settlements=payments.settlements)
```

A coupon's cash is booked at its payment date to whoever holds the instrument
then, as a bond's coupon is. What a position has accrued is stated by its
conventions (`Valuation.accrued`, `swap_accrued`) rather than posted day by day
(ADR-0065 settled point 28). A position is marked by the prices the run is
given, which you may compute off curves. The cash ledger stays the book of
record.

---

## S-06, end to end

`examples/85_trade_a_duration_neutral_curve_book.py` holds a two-year Treasury
note against a ten-year payer swap through a synthetic 2025.

- Each week a swap curve and a Treasury curve are bootstrapped under a named
  interpolation, the notes at ACT/ACT (ICMA).
- Each instrument is marked off its own curve.
- Every coupon is booked by the run from fixings as they were published.

It runs as an experiment of two trials. One sizes the swap to a zero parallel
DV01 at inception. The other sizes it to the note's face, which leaves the
book short the level. `tests/integration/test_rates_end_to_end.py` reconciles
the duration book's cash to the cent.

---

## Not here

- Exotic rates options, CVA and XVA, structured products, and default
  simulation (BDY-014, narrowed).
- A stub in a schedule; a payment lag or a payment calendar other than the
  accrual's (ADR-0065 known limitation 4); a bond coupon's payment adjusted to
  a business day (known limitation 1).
- A reset lag or an index tenor other than the period, in a quoted swap (known
  limitation 2).
- A linker's floor's option value, and inflation projection curves (known
  limitation 3).
- Non-local interpolations, such as splines, whose pillars would be solved
  together rather than in turn.
