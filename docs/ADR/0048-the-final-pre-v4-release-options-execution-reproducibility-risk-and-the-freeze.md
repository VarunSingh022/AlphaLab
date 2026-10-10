# ADR-0048: The Final Pre-v4 Release — Options, Execution, Reproducibility, Risk and the Freeze

## Status

**Accepted and implemented in v3.13.0.**

The last of the four releases the pre-v4 audit plans before the v4.0 freeze
(`docs/audit/PRE_V4_MASTER_AUDIT.md`; the plan of record is
`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`). v3.10.0 made the canonical path
correct, v3.11.0 gave it what a strategy needs, and v3.12.0 hardened it. v3.13
closes every item the ledger assigned to it **and the four it had assigned to
v4.0.0 itself** — the shared names, the public API manifest, the persisted-name
contract and release certification — so that v4.0 has nothing left to build.
Every boundary and limitation the ledger holds was re-read against the code and
kept with its reason, and the ledger assigns nothing beyond v3.13.0.

It adds **no package**, removes none, and adds or removes **no package edge**.
Snapshot schemas: pipeline 6→7 (the strategy status's class name) and
checkpoint 1→2 (per-order state by its changes). Every older payload is
upgraded on read, and the payloads v3.12.0 wrote, its run store and a
checkpoint chain are frozen as fixtures (`tests/fixtures/snapshots/v3.12.0`).

Amends **ADR-0039** decision 14 (the liquidation price takes a stated
maintenance basis, quantity, fees and funding) and its options boundary
(American exercise is priced); **ADR-0041** decisions 4 and 5 (a lock file is
read into the declared dependencies; a run is re-executed from its manifest);
**ADR-0043** (a box uncertainty set takes a book that may short; a factor
model's volatility is divided among its factors; exchange rates enter as
factors; every known limitation is classified); **ADR-0044** (every item of its
DEFERRED row is delivered, and its known limitations are classified);
**ADR-0045** decision 8 (a persisted class name is part of the format, and is
pinned); and **ADR-0047** decisions 5 (a checkpoint segment carries the
per-order state that changed, not all of it) and 7 (a factor model is stated by
its structure, which construction takes without writing it out). Depends on ADR-0045, ADR-0046 and
ADR-0047 throughout.

---

# Context

The ledger assigned eleven items to v3.13: American options and a volatility
term structure (NUM-006, BDY-016, FEA-005, BDY-015), the optimal split
(BRK-005, OFE-024), estimated urgency and randomized icebergs (BRK-006,
OFE-025), the rerun harness (REP-003, OFE-020) and the lock-file reader
(OFE-019). It had assigned four to v4.0.0: API-001, API-002, PER-004 and
FEA-006. Its remaining boundary entries were `not_started` because nothing had
re-read them since the audit.

The fresh audit of the whole tree found twenty-two more, each recorded in the
ledger. Five are defects that shipped: the broker codec read a qualified enum
name by its member alone (PER-007, since 2.16); the liquidation price assumed
one venue's maintenance convention (NUM-014, since 1.38); importing the
research path loaded the market-data transports (BND-005, since 2.5); and the
v1 portfolio optimizer carried risk limits nothing read (RSK-007) and clipped a
portfolio nobody had constrained (OPT-001), both since v1. Three are costs:
checkpoint segments that grew with a run's orders (PRF-011, shipped in 3.12.0);
the one-asset paths' cost against 3.11 (PRF-012); and the public path to the
factor-structured solver, which had to write a factor model out as a dense
matrix first — O(n²), 34 s and 1.3 GB at 4,000 assets — so that the
10,000-asset solve v3.12 published was reachable only through an internal
function (PRF-013). Six are documentation and method (DOC-005, DOC-006,
DOC-007, DOC-008, TST-014, TST-016). And one is the inventory
itself (TST-015): the pre-v4 audit had read ROADMAP's boundaries and optional
list, not the "Known limitations" and "DEFERRED" lists of ADR-0042, ADR-0043
and ADR-0044. Of their 52 items, 23 had no ledger entry — two deferred
capabilities (FEA-007, FEA-008), one lifted limitation (FEA-009) and the rest
classified (LIM-001, LIM-002, LIM-003). BND-006 records the WebSocket client's
two stated omissions.

---

# Decision

## 1. American exercise on a lattice whose step count is part of the model

`options.BinomialLattice(steps, dividends)` prices on a Cox–Ross–Rubinstein
lattice with early exercise and discrete cash dividends (escrowed: the lattice
carries the spot less the present value of the dividends before expiry), and
`PricingModel.BINOMIAL_CRR`'s assumptions state the step count — a price on 5
steps and one on 500 are different numbers. A lattice too coarse to keep its
up-probability in (0, 1) is refused with the steps it needs; `MAX_STEPS` is
5,000. Implied volatility inverts through the lattice an American quote was
priced on. Hull's table for an American put is reproduced to the third decimal,
and release certification checks it (NUM-2).

## 2. Between expiries only by name, in total variance

`ExpiryInterpolation.TOTAL_VARIANCE_LINEAR` reads a surface between two quoted
expiries linearly in total variance. An expiry outside the quoted range is
refused rather than extrapolated, and total variance that falls with expiry is
refused as a calendar arbitrage rather than smoothed. A parametric fit (SVI,
SABR) stays out: a model with parameters somebody has to choose and defend.

## 3. The cheapest split, exact where it says it is

`SplitMethod.OPTIMAL` minimizes the total all-in cost over whole increments:
every set of fixed-charge venues is searched and the rest filled by marginal
cost, which is exact when each venue's cost is a fixed charge plus a convex
function of quantity — every cost model AlphaLab ships. A cost whose marginal
falls is refused, and so are more than ten fixed-charge venues. The greedy
sweep stays the default and a 3.12 policy keeps its identity.

## 4. Urgency estimated, tranches seeded, and the shortfall the model expects

`estimate_urgency` computes the discrete Almgren–Chriss curvature from risk
aversion, volatility and both impacts, each required. `Iceberg` tranches are
drawn from a SHA-256 counter stream of a seed, the parent and the tranche's
index, so a rerun shows the same tranches and two parents do not. The same
model gives a schedule's expected shortfall and its variance (`schedule_cost`,
`UrgencyEstimate.cost`), and `shortfall_against_model` reads a measured
shortfall — from arrival: trading and explicit costs — beside them, in standard
deviations. The model's parameters are the caller's; AlphaLab calibrates none.

## 5. A rerun checks its inputs first, and says where it diverged

`rerun_from_manifest` refuses to run another dataset, strategy, engine or build
(`INPUTS_DIFFER`), runs the caller's callable once, assesses the rerun's own
manifest and, given the original result, reports the first ten paths at which
the two canonical records part. `read_lock_file` reads a lock's text into the
dependency manifest a fingerprint records and resolves nothing: an exact
closure only when the lock is one, an artifact digest only where it names one
artifact.

## 6. Cron on a stated zone's wall clock; no bar-boundary timer

`CronSchedule(expression, zone)` reads five fields on an IANA zone's wall clock,
with Vixie cron's rule for the two day fields; a minute that does not exist that
day does not fire, and a repeated one fires once, at its first occurrence. An
expression that cannot fire, a shorthand and syntax outside the grammar are
refused when written. `ScheduleType.BAR_BOUNDARY` is removed: a bar's arrival is
the event, and a timer restating where a bar's boundary falls could only
disagree with the data.

## 7. The liquidation price for a stated maintenance basis

`compute_liquidation_price` solves the isolated-margin equation for
`MaintenanceBasis.ENTRY_NOTIONAL` or `MARK_NOTIONAL`, with quantity, fees and
funding, in the accounting context; a long no fall can liquidate returns
`None`. A venue's tiered rates, smoothed mark, insurance fund and
auto-deleveraging stay the venue's.

## 8. Checkpoints write what changed; per-order memory is stated

A checkpoint segment writes the order-book, completed-order and report entries
added or replaced since the mark before it, and the reader merges them by key
and checks each map's size; a mark that cannot say what changed writes them
whole, as 3.12 did. The in-memory per-order state itself is kept, as an
explicit limitation: exactly-once handling of a venue's late or repeated report
needs the order it names.

## 9. One name, one contract

A public name bound to two different objects is renamed — without an alias, so
that v4 freezes one spelling — merged, or kept with a recorded reason. 52 such
names at 3.12.0 are 31, each listed with its reason in the API manifest.

## 10. The API and the persisted names are data

`docs/api/public_api.json` records every export of every package with its
binding, regenerated with each release and kept per release in
`docs/api/history/`; a test fails on any change not regenerated, and another on
any removed or rebound name the release's CHANGELOG section does not name. A
plain enum persists as `ClassName.MEMBER`, so its class and member names are
pinned for every decoder and every event log; a rename is a schema step.

## 11. A certificate of what the build was checked to do

`certify_release.py` writes eleven checks — determinism, parity,
reproducibility, published numerical references and the public API — with
their evidence; CI runs it with `--check`, which fails on a failed check or on
evidence that moved.

## 12. Exchange rates as factors, and every factor's share

`currency_loadings` loads an asset on `FX:<code>` by its denomination, to first
order, and `factor_risk` divides a factor-model covariance's volatility into
each factor's Euler term and each asset's specific term. The risk budget's
`CURRENCY` dimension keeps its meaning — the risk of holdings denominated in a
currency — and the exchange rate's own share is now measurable beside it. The
rates' covariance is the caller's (ADR-0020).

## 13. A box set on a book that may short

The box counterpart `μ̂'w − δ'|w|` is solved by the `LinearCosts` orthant method
with the held weights at zero, certified by the true subgradient condition. A
long-only book keeps the shifted-returns identity.

## 14. The v1 optimizer states only what it checks, and applies only what is configured

`RiskConstraints` holds the three limits `validate_risk_constraints` checks,
each required; the three nothing read, and the state map nothing wrote, are
removed. `PortfolioEngine.optimize` applies the constraints configured for the
portfolio and no others.

## 15. Every ADR's limitations are held to the ledger

`tests/regression/test_every_adr_deferral_is_classified.py` reads every "Known
limitations" bullet and every deferred item an ADR states and requires each to
map to a closed ledger entry, and `test_nothing_is_left_for_later.py` requires
every ledger entry to be implemented or kept, none to be assigned beyond the
release, and no current-state document to call anything deferred — the claim
this release makes, held where it is written; the version every current-state
document states is held to the package's (`test_version_markers_agree.py`).
The limitations kept are kept for stated reasons: a maximum-diversification
change of variables that a turnover limit or a volatility cap does not survive;
risk parity's exact, long-only budgets, which define its one solution; an
Euler-contribution cap that is not convex; a VWAP's profile resolution; and the
conventions the ADRs state.

## 16. A factor model is a covariance, stated by its structure

`FactorStructure.of(loadings, factor_covariance, specific_variances)` builds a
factor model's covariance in O(n k²) and holds nothing O(n²).
`ConstructionProblem` takes it as its covariance, and `euler_decomposition` and
`factor_risk` decompose through its factors. The dense values are written out
only for a method that reads them — risk parity, a universe below the
structured method's minimum, a structure that cannot establish definiteness by
itself, a program the structured method does not certify — once per
construction, and the result is still the problem's as stated. A structure has
its own identity, derived from the factor covariance, the loadings and the
specific variances; `matrix()` writes it out as exactly the matrix
`CovarianceMatrix.factor_model` builds, identity included. A problem over a
matrix keeps the identity and the result 3.12 gave it.

---

# Consequences

* The ledger's 216 entries are each implemented or kept with a reason; none is
  assigned to a later release.
* A run configured as in 3.12 behaves as in 3.12, except where 3.12 was wrong,
  each listed in the CHANGELOG — among them the liquidation price, the broker
  codec's qualifier, an intent refused where it is emitted rather than only where
  it is allocated, and the v1 manager's clipping of an unconstrained portfolio.
* Breaking changes are renames, removals and required inputs, each in the
  CHANGELOG's migration table and checked against the API's own diff.
* The one-asset paths cost between nothing and 11% more than 3.11's, by
  benchmark — the per-record checks of the capabilities v3.12 put on the one
  canonical path (PRF-012) — inside PRF-006's budget in every round; v3.13 adds
  nothing measurable to 3.12.
* Per-order state grows with the orders a run places (PRF-011); checkpoints no
  longer pay for it.
* Construction over a factor model reaches 10,000 assets through the public API
  (PRF-013); writing one out as a dense matrix costs what it did in 3.12.

# Rejected alternatives

* **Aliases for the renamed names.** v4 would freeze two spellings of one
  contract, and a caller could not tell which was canonical.
* **The optimal split by default.** It would change a 3.12 policy's decisions
  and identity; a method that searches is named, not assumed.
* **A default urgency, risk aversion or randomization.** Each is a choice with
  no neutral value (BDY-024, BDY-026).
* **Reading `@daily`.** One schedule would have two spellings that compare
  unequal; the refusal names the five fields it stands for.
* **Firing a repeated wall-clock minute twice, or a skipped one at the end of
  the gap.** Implementations differ; one rule is stated, and an interval timer
  counts elapsed time for whoever needs it.
* **Dropping per-order state to bound memory.** Exactly-once handling of a late
  or repeated venue report would go with it.
* **A specialized path per configuration to recover PRF-012's cost.** A second
  path can drift from the first; the cost is stated and bounded instead.
* **Approximating a non-convex cost, a parametric surface, or an exchange
  rate's covariance.** Each would report a model's guess as a measurement.
* **A dense matrix written out lazily, inside the frozen value.** Every v3.8
  value is changed only while it is constructed, and a hidden cache is a change;
  a structure that is itself the covariance needs none.
* **An identity for `factor_model`'s matrix derived from its structure.** It
  would have made the dense build cheaper by changing every identity 3.12
  released for a factor-model covariance and the problems built on one.
