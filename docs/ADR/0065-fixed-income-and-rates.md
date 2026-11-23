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

# Compatibility

- Additive. A package, terms, appended `AssetType` members, and cash-flow
  booking for records that declare rates terms.
- No identity, digest or persisted byte moves for any run, registry or
  record that declares none. `macro.bond` and `macro.yield_curve` are
  unchanged.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- A curve is bootstrapped under a stated interpolation and reproduces a
  published worked example (GR-6).
- Swaps and FRAs book by their terms, and a swap's present value at
  inception is zero by parity.
- A duration-neutral curve trade is run as an experiment (S-06), and rates
  hedges join a cross-market book (S-14).
- BDY-014 is narrowed in the ledger to what stays out: exotic rates options,
  CVA and XVA, structured products and default simulation.
