# ADR-0065: Fixed Income and Rates -- Curves Under a Named Interpolation, Instruments by Declared Terms

## Status

**Accepted** (2026-11-23), under the owner's delegation of the programme's
decisions within the charter's principles (ADR-0056, 2026-11-10). D-V3 gave
each boundary reopening to the milestone that needs it, and this is M10's for
rates (plan section G.2, engine N13, gaps GAP-24 and GAP-51, workstream
WS-14, scenario S-06, acceptance GR-6).

It reverses one accepted boundary, narrowly: **BDY-014**, "no curve
bootstrapper; no fixed-income engine". It is reopened as a `rates` package
whose every modelling choice is the researcher's, stated and recorded. Exotic
rates options, CVA and XVA, and structured products stay out (plan
section Y).

---

# Context

**What exists.**
- `macro.bond` prices, yields, accrues and measures fixed-rate bonds.
- `macro.yield_curve` holds observed yields at observed tenors, and says why
  it is not a bootstrapped curve: an interpolation changes every forward read
  off the result, so a library that chose one silently would hide a market
  assumption.
- `conventions.daycount` has the world's bond and swap bases since M2
  (plan N21, GAP-36).
- `AssetType.BOND` is booked on the run's path.

**What a rates researcher cannot do (GAP-24, GAP-51).**
- Build a discount or projection curve from deposits, futures, FRAs and
  swaps, or discount at one curve while projecting from another (OIS
  discounting).
- Value or book a swap, an FRA, a floating-rate note, an inflation-linked
  bond or a repo.
- Run a duration-neutral book as an experiment (S-06).

**The boundary's own reason is the design constraint.** The objection was
never to curves. It was to a library choosing the interpolation. So the
interpolation is required and named, never defaulted, and recorded in the
curve's identity.

# Decision

## 1. A `rates` package, a library

- **`alphalab.rates`** (N13) holds curves, schedules, instruments' cash flows,
  bootstrapping and pricing. It imports `common` and `conventions`, and is
  imported by whatever books or values a rates instrument. Its edges are
  measured, as every package's are.
- **Every number it returns has an identity.** A curve, a bootstrap and a
  price are values with provenance: their inputs, the method, the
  interpolation and the conventions, as ADR-0058 gave estimates. A curve is
  computed only from what the caller supplies, and the engine ships none
  (AC-22).

## 2. Curves under a required, named interpolation

- **A curve** is a set of pillars, dates with discount factors, in a stated
  currency and day count. Between pillars it is read by a named
  `Interpolation`, which is required: no curve exists without one. At M10 the
  methods are log-linear on discount factors (piecewise-flat forwards) and
  linear on zero rates. Each is defined exactly, and others are added by
  name, never by default.
- **Extrapolation is refused unless stated.** A read beyond the last pillar
  is an error, unless the curve declares how it extends.
- **Multi-curve.** A projection curve for an index is bootstrapped against a
  stated discount curve. OIS discounting is a choice the researcher states,
  not one the engine makes.

## 3. Bootstrapping, certified

- **From quotes the researcher supplies**: deposits, FRAs, futures (with a
  stated convexity adjustment, or none, stated) and par swaps. Each quote
  carries its conventions.
- **Each pillar is solved in turn to a stated tolerance.** A quote that
  cannot be met, such as a negative discount factor or no root in range, is
  refused with the reason and is never silently skipped. The bootstrap
  returns each instrument's repricing error.
- **GR-6.** A curve bootstrapped under a named interpolation reproduces a
  published worked example, cited by source.
- **Budget.** A 30-instrument curve bootstraps within one second.

## 4. Instruments by declared terms

- **`SwapTerms`, `FraTerms`, `FloaterTerms`, `LinkerTerms` and `RepoTerms`**
  ride on `InstrumentRecord` outside its identity key, as `OptionTerms` do.
  Each states:
  - every leg's notional, currency and day count;
  - its frequency, business-day convention and calendars;
  - for a floating leg, its index, reset lag and fixing source;
  - for a linker, its inflation index, lag and base;
  - for a repo, its collateral, rate, dates and haircut.

  None has a default (AC-12).
- **New `AssetType` members, appended**: `SWAP`, `FRA` and `REPO` (GAP-51).
  A floater and a linker are bonds with declared coupon terms. Members are
  appended, so every name an earlier release wrote reads back unchanged
  (PER-004).

## 5. On the run's path, by declared terms

- **A rates position books its cash flows**, each on its payment date in its
  currency: a fixed leg's coupons, a floating leg's from fixings, a linker's
  indexed coupons, a repo's interest and return. Fixings and index values
  arrive as observations on the run's schedule. A missing fixing is refused
  when a payment needs it, never assumed.
- **Accrual by convention.** What a position has accrued between payments is
  stated by its day count and terms, as an accrual (`ACCRUAL`, phase 3).
- **Marks are the researcher's.** A position is marked by a stated price
  source or by a curve the run supplies. A curve-based mark is a valuation,
  recorded with the curve's identity, and the cash ledger stays the book of
  record.

## 6. Credit, basic and reduced form

- A **credit spread curve** over a discount curve, or a **hazard-rate curve**
  with a stated recovery, prices risky cash flows. Both are inputs the
  researcher supplies or bootstraps from spreads they quote. Default events
  are not simulated at M10.

# Settled at implementation (2026-11-28): curves and the bootstrap

Building sections 1 to 3 settled ten points. Each is held by a test in
`tests/unit/rates`.

1. **The package's edges.** `alphalab.rates` imports `alphalab.common` and
   `alphalab.conventions`, and nothing else in AlphaLab.
   `tests/regression/test_documented_dependencies_are_measured.py` reads its
   row of `docs/ARCHITECTURE.md`'s table against its imports.
2. **The two interpolations, exactly.** A date's time is its year fraction
   from the curve's anchor under the curve's day count. A basis that needs an
   instrument's terms cannot be that axis, so it is refused.
   - `LOG_LINEAR_DISCOUNT`: `ln P` is linear between pillars, from `P = 1` at
     the anchor.
   - `LINEAR_ZERO`: the continuously compounded zero rate is linear between
     pillars, and flat at the first pillar's from the anchor.

   A pillar reads back its own discount factor, bit for bit. Two pillars at
   one time on the axis, such as 30 and 31 March under 30E/360, are refused.
3. **Extrapolation, when stated.** `FLAT_FORWARD` continues the last
   segment's continuously compounded forward rate, and `FLAT_ZERO` the last
   pillar's zero rate. A curve that states neither refuses a read beyond its
   last pillar (`CurveRangeError`), and every curve refuses a read before its
   anchor.
4. **Identity.** A curve's `curve_id` covers its name, currency, anchor, axis,
   interpolation, extrapolation and stated pillars. A computed curve names
   its computation's identity in place of its discount factors: a
   bootstrap's, or a parallel shift's (`shifted`). A solved discount factor
   passes through `exp` and `log`, numerical class 3 (ADR-0058 section 3), so
   two hosts that solved one problem may differ in its last bits while their
   curves keep one identity. A bootstrap's `bootstrap_id` is the problem's:
   - the curve's name, currency, anchor, axis, interpolation and
     extrapolation;
   - the tolerance, the iteration limit and the search range;
   - the discount curve's identity;
   - each quote in pillar order, with its dates as its conventions placed
     them.
5. **The bootstrap.** The quotes are taken in order of the last date each
   reads. Each pillar's `ln P` is solved by Brent's method, the library's one
   root finder, to the stated tolerance within the stated iterations, both
   required. It is sought where the new segment's continuously compounded
   forward rate lies between -100% and +1,000% a year (`FORWARD_SEARCH`).
   Each refusal is a `BootstrapError` with its reason:
   - a quote no discount factor in that range reprices, refused with its
     residual at both ends;
   - a pillar not solved within its iterations.

   Both interpolations are local, so a later pillar never moves an earlier
   segment. Each quote's repricing is read off the curve returned.
6. **A bond's price is a quote too.** Section 3 lists deposits, FRAs, futures
   and par swaps. GR-6's published example bootstraps zero rates from bond
   prices, so `BondQuote` is a fifth quote type: a fixed-coupon bond's full
   price at its settlement, a zero-coupon bond's included.
   - Its coupons step back from maturity, each counted from it.
   - Each coupon pays its period's year fraction under the bond's basis.
     ACT/ACT (ICMA) reads each period's own terms.
   - It is refused for a projection curve, since its price states discount
     factors, not an index's forwards.
7. **A quoted swap.** Both legs run from its start for its length, each a
   whole number of its own steps, under one convention, calendar set and
   end-of-month rule.
   - A stub is refused, not placed.
   - The fixed leg accrues under its basis. A basis that needs a coupon
     period's terms reads each period's own, and 30E/360 (ISDA) reads the
     termination date.
   - A floating period's forward is read over the period itself, so a
     floating leg states no day count: nothing would read it.
   - On one curve the floating leg is `P(start) - P(end)`. Against a
     discount curve, each floating coupon is projected off the curve being
     built and discounted at the given one.
8. **A future's convexity.** A future states the forward
   `1 - price / 100 - convexity_adjustment`. The adjustment is required, and
   zero states that none is made.
9. **GR-6, the published worked examples.** John C. Hull, *Options, Futures,
   and Other Derivatives* (8th edition, Pearson, 2012):
   - chapter 4's bootstrap of Treasury zero rates from five bond prices,
     Tables 4.3 and 4.4, reproduced to the three decimals published (10.127%,
     10.469%, 10.536%, 10.681% and 10.808%) under both interpolations. Every
     coupon falls on a pillar, so the example needs none, as its text says.
   - The swap chapter's two-year zero rate of 4.953%, from zero rates of 4%,
     4.5% and 4.8% and a two-year swap at 5%.

   The example's times are in years, so the test's axis is 30/360 from 15
   January 2025, where each maturity's date is exactly its time.
10. **The budget.** `benchmarks/benchmark_rates.py` bootstraps a 30-instrument
    curve within the plan's one second: 0.008 CPU seconds under each
    interpolation on the reference machine.

# Settled at implementation (2026-11-28): swaps and FRAs

Building section 4 for swaps and FRAs settled seven more points.

11. **Terms live beside what values them.** `SwapTerms` and `FraTerms`, with
    their `FixedLeg`, `FloatingLeg` and `LegSchedule`, are declared in
    `alphalab.rates`. `instrument` imports them and carries them on the
    record, outside the identity key, written and read by its codec as kinds
    `swap` and `fra`. Calendars are named by id, as an FX deal's are, and
    supplied where the terms are used.
12. **A rates record.** `AssetType.SWAP` and `AssetType.FRA` are appended, so
    no written value moves. A record of either must declare its type's terms,
    in its own currency. Its economics are declared and must:
    - settle as `cash_equity`, the value paid at the trade, or as
      `futures_variation`, settled at every mark as a cleared swap is;
    - allow a negative price, since a unit is worth its value per unit of
      notional, which is negative whenever rates move against the holder.
13. **Sides.** One unit held receives a swap's fixed leg and pays its
    floating leg, and a negative quantity is the payer's side. One FRA unit
    receives the fixing and pays the contract rate.
14. **Periods and fixings.**
    - A leg's periods are its regular schedule from the instrument's start
      (settled point 7).
    - A floating period's fixing date is its reset lag in business days
      before its start, in its leg's calendars.
    - A valuation is as of the curves' date. A period already paid is not
      valued. A period fixed before that date reads its fixing from what the
      caller supplies and is refused when it is absent, never projected. A
      period fixing on or after it is projected over the period itself.
15. **Values and parity.** `value_swap` and `value_fra` return a `Valuation`:
    - the value to one unit, and each leg's;
    - what is accrued, by each leg's convention (`swap_accrued`);
    - the par rate and the annuity.

    Its `valuation_id` covers the terms, the periods as the calendars placed
    them, the curves' identities, every fixing read and the date. A swap
    struck at a quoted par rate is worth nothing on the curve bootstrapped
    from that quote, on one curve and against a stated OIS curve
    (`tests/unit/rates/test_swaps_and_fras.py`).
16. **An FRA's settlement.** `fra_settlement` is
    `notional * (fixing - rate) * tau / (1 + fixing * tau)`, the period's
    difference discounted to its start at the fixing. It is computed in
    `Decimal` under the accounting context, from the terms and the shortest
    `repr` of the period's year fraction, so it books exactly on every host.
17. **A corporate action passes over a swap.** A split and a holding exchange
    check every contract held or worked for a deliverable they change. They
    now pass over every terms type but an option's, since an FX deal, a swap
    and an FRA deliver no instrument. With a swap working at the venue, the
    check had read an option's adjustments from it, and the action failed
    (`tests/unit/instrument/test_rates_records.py`).

# Settled at implementation (2026-11-28): floaters, linkers, repos and credit

18. **A floater and a linker are bonds.** `FloaterTerms` and `LinkerTerms`
    ride on a `BOND` record as its coupon terms, settled as `cash_equity`. A
    bond declaring neither is a fixed-rate bond, as it always was.
    `AssetType.REPO` is appended for `RepoTerms`, which a repo's record must
    declare, settled as `cash_equity`.
19. **A linker's reference index.** It is read from the index's monthly
    prints, each by the first day of the month it measures, in `Decimal`:
    - `MONTHLY`: the print of the month `lag_months` before the date's;
    - `DAILY_INTERPOLATED`: from that print towards the next month's by the
      day of the month, `I(m - lag) + (d - 1) / D * (I(m - lag + 1) -
      I(m - lag))`, the method of US TIPS and of gilts issued since 2005.

    A print it needs and nobody supplies is refused. Its principal is
    `notional * I(T) / base`, or the notional when `floored` and the ratio
    is below one.
20. **A linker is valued off a real curve**: today's index ratio times its
    real coupons and principal at real rates. Projecting the index under an
    inflation curve is another model, not built at M10. The floor's option
    value is not modelled (known limitation 3).
21. **A repo.** One unit held is one unit of cash lent, repaid `1 + rate *
    tau` at its end, in `Decimal`. Its haircut is in `[0, 1)`: cash of `q`
    needs collateral worth `q / (1 - haircut)`. Before it starts, its par rate
    is the rate that values it at zero.
22. **Credit, by the curve kinds that exist.** A spread curve is a curve of
    factors `exp(-s * t)`, read by the interpolation it states. A hazard
    curve's survival probabilities come from hazards held constant between
    its dates, so it is read log-linearly. A `CreditCurve` refuses a survival
    probability that rises and a recovery outside `[0, 1)`. `risky_value`
    recovers `R` of the notional at the date of the flow in whose interval a
    default falls: a recovery on the payment grid, stated. At zero recovery a
    hazard prices exactly as the same spread does.
23. **A coupon paid on the valuation date is paid, not valued**, for every
    instrument: a period paying on or before the curves' date is left out.
    A floater valued on a payment date is worth par again
    (`tests/unit/rates/test_securities_and_credit.py`).

# Compatibility

- Additive. A package, terms, appended `AssetType` members, and cash-flow
  booking for records that declare rates terms.
- No identity, digest or persisted byte moves for any run, registry or
  record that declares none. `macro.bond` and `macro.yield_curve` are
  unchanged.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

1. **A bond's coupon is discounted at its unadjusted date** (settled point 6).
   A coupon paid on the next business day after a holiday is discounted at the
   holiday itself, a day or two early. At a 5% rate a day's discounting is
   about 0.014% of the coupon. Payment adjustment for a bond quote is not built
   at M10.
2. **A quoted swap's floating forward is its accrual period's** (settled
   point 7). A reset lag, and an index tenor that differs from the period, are
   not modelled in a quote. They are the instrument's declared terms when it is
   booked (section 4).
3. **A linker's floor has no option value here** (settled point 20). A
   linker is valued as its real flows at real rates, and its deflation floor
   is applied only where a principal is paid. Pricing the floor needs an
   inflation volatility model, which is not built at M10.

# Consequences

- A curve is bootstrapped under a stated interpolation and reproduces a
  published worked example (GR-6).
- Swaps and FRAs book by their terms, and a swap's present value at
  inception is zero by parity.
- A duration-neutral curve trade is run as an experiment (S-06), and rates
  hedges join a cross-market book (S-14).
- BDY-014 is narrowed in the ledger to what stays out: exotic rates options,
  CVA and XVA, structured products and default simulation.
