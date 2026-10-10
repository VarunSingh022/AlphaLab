<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-3.11.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-8215%20Passing-success)]()
[![Typing](https://img.shields.io/badge/MyPy-Strict-blue)]()
[![Style](https://img.shields.io/badge/Ruff-Clean-red)]()

*A modular Python library for quantitative research, systematic strategy development, portfolio optimization, market simulation, broker integration, and production tooling.*

</div>

---

# What is AlphaLab?

AlphaLab is an open-source Python **library** for building deterministic quantitative research and algorithmic trading components.

It is a library, not a running application: there is no server, daemon, scheduler process, or CLI, and it declares **zero runtime dependencies** — the standard library is the whole of it. You import the packages you need and call their pure, immutable engine APIs from your own code.

AlphaLab ships three kinds of package:

- **The integrated execution path.** `alphalab.runtime.ExecutionPipeline` is the one spine that wires several domain engines together — market data → strategy → allocation → risk → OMS → execution simulator → portfolio → analytics — as a chain of pure functions over one immutable `ExecutionPipelineState`. `alphalab.runtime.run.RunEngine` owns the *run* over it, and four interchangeable drivers feed it: `TradingSession`, `BacktestEngine`, `ReplayBacktest` and `LiveSession`. Because all four call the same step, a backtest, a replay, a paper run and a live run of one dataset produce identical orders, fills and P&L wherever the venue is the same.
- **The lifecycle path.** `alphalab.lifecycle` composes experiment tracking, the model registry, the deployment manager, strategy definitions (`alphalab.strategy.definition`), governance against a permission authority the host application supplies, and `research`/`backtesting`'s reports into one flow: research candidate → experiment run → validation evidence → model version → strategy version → promotion → deployment → rollback. Every act that changes what is live names its principal. As of v2.16 it is **joined** to the execution path: `run_plan` resolves what an environment has live and `authorize_run` refuses a run that would serve anything else. As of v2.17 `alphalab.strategy.registry` supplies the other half of that join — the identity a deployment names, mapped to the code a run executes. As of v3.5 the same package carries past the deployment record into the thing a deployment becomes: a strategy's progression from research to live money, the specification of what it needs to run as it was researched, structured runtime health from supplied observations, an expected/paper/live comparison, and deterministic reconciliation against a normalized broker state. As of v3.6 it also makes a strategy version evaluable by somebody else: an immutable fingerprint of its code, dependencies, parameters, research configuration and engine; a manifest from which a result can be recreated; machine-verifiable certification properties, each with its evidence; and a portability check against declared environment capabilities.
- **Standalone engine libraries.** The remaining packages (portfolio optimizer, reporting, feature store, ML / deep learning / RL, options / futures / crypto / macro, cloud research, cluster scheduler, and the rest) are independent, deterministic, individually tested libraries reached by neither path. They share the engineering model and are **not** fused into a single runtime. That is a decision, not a gap — see ADR-0009. The factor library (since v3.2) and alternative data (since v3.7) are no longer on that list: `research` imports both, so the lifecycle path reaches them.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v3.13.0 — the final pre-v4 release: American options and a volatility term structure, the optimal split of an order, an urgency estimated rather than assumed and the shortfall its model expects, a rerun that says where it diverged, cron timers, an exact liquidation price, exchange-rate risk as factors, checkpoints that no longer grow with a run's orders — and the freeze: one name for one contract, the public API and every persisted name recorded as data and held by tests, and a certificate of what the build was checked to do**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 3.13.0 |
| Runtime dependencies | **None** (standard library only) |
| Tests | **@@GATE@@ Passing, 0 skipped, 0 warnings** |
| Static Typing | **Strict MyPy** (@@GATE@@ source files, repository-wide) |
| Linting | **Ruff Clean** |
| Benchmarks | **@@GATE@@ Passing** |
| Examples | **69 / 69 Passing** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| Release Certification | ✅ 11 / 11 checks ([certificate](docs/audit/RELEASE_CERTIFICATION.md)) |
| License | MIT |

## What v3.13.0 is

The last of four releases the **pre-v4 audit** plans before the v4.0 freeze, and
the one that leaves nothing required for later. Every item the ledger assigned
to v3.13 is closed, and so are the four it had assigned to v4.0 itself — the
shared names, the public API manifest, the persisted names and release
certification. Every boundary and limitation the ledger holds was re-read
against the code and kept with its reason, and a fresh audit found twenty-one
more things, each fixed, implemented, replaced or stated. ADR-0048.

**Options that can be exercised early.** An American option is priced on a
Cox–Ross–Rubinstein lattice with discrete cash dividends, its step count part
of the model's stated assumptions; Hull's table for an American put is
reproduced to the third decimal. Implied volatility inverts through the lattice
an American quote was priced on, and a surface is read between expiries,
linearly in total variance, only when asked to by name.

**Execution that costs what it says.** The optimal split finds the cheapest
allocation of an order across venues when each venue's cost is a fixed charge
plus a convex function of quantity — every cost model AlphaLab ships — and
refuses a cost it cannot call optimal. An Almgren–Chriss urgency is estimated
from risk aversion, volatility and impact; the same model gives a schedule's
expected shortfall and its variance, and a measured shortfall is read beside
them. Iceberg tranches vary, from a seed, and reproduce.

**Reproducibility that is demonstrated.** A run is re-executed from its
manifest — other inputs are refused before anything runs — and a divergence is
located by the first paths at which the two records part. A lock file is read
into the dependencies a fingerprint records, resolving nothing.

**Risk, timers, margin, persistence.** Exchange rates enter a factor model as
factors, and every factor's share of a book's volatility is measured; a box
uncertainty set takes a book that may short. A factor model is a covariance in
its own right, stated by its structure: construction over 10,000 assets no
longer writes 100 million values out first (0.12 s and 6 MB to state the
model, 2.15 s to construct over it; 4,000 assets written out took 34 s and
1.3 GB before the first step). Cron timers read a
stated zone's wall clock across daylight saving. The liquidation price is
solved for a stated maintenance basis, with fees and funding. Checkpoint
segments carry only the orders that changed.

**The freeze.** 52 public names bound to two different objects at 3.12.0 are
31, each kept for a recorded reason; the rest were renamed without aliases,
merged or removed. The public API is recorded as data per release, and a test
requires every removed or changed name in the CHANGELOG. Every persisted enum
name is pinned. Every limitation and deferral an ADR states is held to a closed
ledger entry. A release certificate — determinism, parity, reproducibility,
published numerical references, the API — is generated by a script CI runs.

**This release breaks things on purpose**: v3.13 renames rather than aliases,
and each break is listed, with what to do instead, in the
[CHANGELOG](CHANGELOG.md)'s migration table.

---

## What v3.12.0 is

The third of four releases the **pre-v4 audit** plans before the v4.0 freeze.
v3.10 made the canonical path correct and v3.11 gave it what a strategy needs;
v3.12 hardens it and builds what the audit deferred here. Every item the ledger
assigns to v3.12 is closed, each pinned by the tests its entry names, and the
five defects building, stressing and auditing it found are fixed — four had
shipped. Two packages leave the library: `plugins`, whose `execute()` was a
placeholder, and `optimizer`, a second parameter search beside the research
authority's; the reporting dashboards go with them. ADR-0047.

**Right at the edges of the range.** R² of a constant series is undefined, not
zero; the normal CDF keeps its precision deep in the lower tail, so far
out-of-the-money options price and invert; least squares runs by Householder QR
and refuses an ill-conditioned or rank-deficient design; theta uses the year
the price uses.

**Durable, and restored as captured.** A rename is flushed with its directory;
an allocation snapshot keeps its budget's currency (it had been dropped since
v2.17). Every payload v3.11.0 wrote is frozen as a fixture and read by the
suite. A run can bound the history it keeps — a strategy asking for more is
refused rather than answered short — and checkpoint incrementally, as a base
and a chain of segments verified link by link.

**Capital, classification and information on the path.** Simulated DAY orders
expire at their venue's last close; a strategy's capital can be capped; any
classification dimension — issuer, country, rating — can bound a bucket's
gross or share, counting working orders, reduce-only; a point-in-time
observation reaches a strategy at the instant it became knowable.

**Scale, measured.** A stress program runs 10,000 assets under classification
limits, 1,000 strategies with ceilings and 100 venues' calendars, accounts and
session timers. It found two costs that grew faster than the work, both fixed:
a classification limit summed its bucket for every order, and every event asked
every strategy whether it subscribed. Construction on a factor-model covariance
is solved in O(n k²) per step: 10,000 assets in 1.6 s, where the dense solver
took 135 s at 800 — the solve alone: through the public API the covariance was
first written out, O(n²), until v3.13 stated it by its structure (PRF-013). Side by side with v3.9.0 on one machine, the OMS benchmark
takes 0.87x its time, a one-asset backtest 1.15x, replay 1.10x and the pipeline
1.03x, within the budget v3.11 set; against v3.11.0, 0.98x to 1.07x.

**Multi-account, prints, sequence models.** One book reconciles against every
account it is spread across; trade prints are read from declared columns with
their venue identifiers and aggressor sides; LSTMs and attention train by
backpropagation checked against central differences; the v1 research engine
reports measurements instead of 0–100 scores, with every bound stated.

**This release breaks things on purpose**: each break is listed, with what to
do instead, in the [CHANGELOG](CHANGELOG.md)'s migration table.

---

## What v3.11.0 is

The second of four releases the **pre-v4 audit** plans before the v4.0 freeze
([master audit](docs/audit/PRE_V4_MASTER_AUDIT.md); the plan of record is the
[completion ledger](docs/audit/PRE_V4_COMPLETION_LEDGER.yaml)). v3.10 made the
canonical path correct; v3.11 gives it what a strategy needs before its API is
frozen. Every item the ledger assigns to v3.11 is closed, each pinned by the
tests its entry names, and the nine defects building and auditing them found
are fixed — seven had shipped. ADR-0046.

**Instruments that are not shares.** An instrument declares its economics —
multiplier, settlement (fully paid, futures variation margin, option premium,
perpetual), lot, minimum notional, whether its price may go negative. A
future's gain settles in cash at every mark; nothing assumes a multiplier; an
undeclared future is refused. Dividends, interest, fees, funding and splits
reach positions through the path, and a venue's rebate is a negative
commission.

**Orders with terms; positions as targets.** Limit, stop and stop-limit
orders, IOC, FOK, GTD, DAY, on-open and on-close, resting across events and
filled as makers or takers. A strategy may state the position it wants — a
quantity or a weight — measured against its *own* position and rounded toward
zero onto the lot, so it is approached and never overshot; a sale commits no
budget, so a fully invested book can rotate.

**Complete instants, and a strategy that hears back.** Subscriptions are
enforced; a slice delivers every asset of an instant at once; `on_start`,
`on_stop` and fill and order feedback are delivered by the path.

**Research that cannot leak.** Forward returns enter after a declared
implementation lag, delisted names keep their final return, and walk-forward
optimization selects on validation and reports on test without ever handing
the selection a test instant. Holm, Benjamini-Hochberg and -Yekutieli
corrections, the deflated Sharpe ratio, Newey-West IC inference, multivariate
neutralization and benchmark-relative statistics.

**Construction that answers in quantities.** Ledoit-Wolf, EWMA and
factor-model covariances; mean-variance that pays for trading, solved exactly;
weights rounded to whole lots toward zero, with what rounding left out
reported.

**The application's packages leave the library.** Identity and RBAC
(`enterprise`), UI state (`workbench`), project management (`studio`) and venue
credentials are the host's; a strategy's definition moved to
`alphalab.strategy`, permissions to a `PermissionAuthority` the application
supplies.

**Every run entry point computes exactly; a run costs what its length does.**
Every public entry of the pipeline, the run and the live session is pinned to
the accounting context — a caller's low precision had changed booked
quantities. A slice of an append-only log copied the whole log, so a run had
been quadratic in its length since v2.1; it is linear again. Measured side by
side with v3.9.0, the OMS benchmark now takes 0.83x its time, a one-asset
backtest 1.05x and the execution pipeline 1.01x; the portfolio-engine
micro-benchmark, which does exact per-currency accounting on every fill, 1.69x.
Every one is faster than v3.10's.

**This release breaks things on purpose**: each break is listed, with what to
do instead, in the [CHANGELOG](CHANGELOG.md)'s migration table.

---

## What v3.10.0 is

The first of four releases the **pre-v4 audit** plans before the v4.0 freeze
([master audit](docs/audit/PRE_V4_MASTER_AUDIT.md); the item-by-item plan of
record is the [completion ledger](docs/audit/PRE_V4_COMPLETION_LEDGER.yaml)).
v3.1–v3.9 each added a capability; v3.10 adds almost none. It corrects the
canonical path where the audit found it computing numbers a user reads first
wrongly, removes code that contradicts the library's boundary, and makes
persisted state upgradeable — so that what is frozen at v4 is correct, not
merely stable. Every item the ledger assigns to v3.10 is closed, each pinned by
the tests its ledger entry names. ADR-0045.

**Risk on the projected book.** Every pre-trade check reads one projection of
the book after the order, counting working orders. Buying power is charged only
for what grows a position, so a fully invested account can sell; a trade that
reduces exposure is never refused by the limit it reduces; a drawdown or
daily-loss breach refuses only what grows exposure. The daily loss limit is
maintained — it never fired before — in the IANA zone its trading day is kept
in, and the net exposure limit is enforced.

**Money exact at each currency's minor unit.** ISO 4217's table (JPY 0, KWD 3)
plus the units a book declares for what ISO 4217 does not list; prices and
quantities kept exactly as given; money rounded once, half to even, in one
pinned decimal context.

**Analytics that say what they assumed.** One equity point per instant;
annualization declared or observed, never assumed, and recorded; undefined
statistics are `None`; trade statistics count the fills that realized P&L.

**Execution realism, stated.** `FillTiming.NEXT_EVENT` fills an order at its
asset's next event; every run and result carries `ExecutionAssumptions`,
listing what is optimistic about it; strategy failures are reported, and a run
can halt on one.

**Linear in the universe; bars stamped at their close.** A market event
re-marks what it priced and what a fill priced, reading exact per-currency
totals — 0.85–1.24× the per-record cost across an 8× universe, and on a
single-currency book exactly what re-marking every position gives. The price
is a higher constant: a one-asset backtest takes about 1.4× as long as in
v3.9, and a 10-asset one already runs faster (PRF-006, planned for v3.11 and delivered there). A
bar is stamped at the end of its interval, and a source must say which end it
stamps.

**Upgradeable state; no vendor code; no silent defaults.** Every snapshot
subsystem upgrades older payloads through explicit, pure steps that refuse
rather than invent. The vendor market-data clients (four were stubs), the
`feed` and `live` packages and exchange symbol quirks are removed — a provider
is the host application's, and AlphaLab asks one method of it. The last `"USD"`
configuration defaults are gone, and the defaults sweep that had missed them is
tightened.

**This release breaks things on purpose**: each break replaces a silent wrong
answer with a correct one or a refusal that names what to supply. The
[CHANGELOG](CHANGELOG.md) has the full list and a migration table.

---

## What v3.9.0 is

The ninth capability release on the frozen architecture. v3.8 decided what to
own and how much capital each strategy gets; v3.9 is the path from that
decision to a venue and back — **one execution contract, whichever adapter an
application brings**: what a venue can do, what its reports mean, how an order is
worked and where it is sent, and what the execution cost.

No package and no package edge is added. `alphalab.core` gains the capability
model and the canonical order-transition table; `alphalab.broker` gains
normalized venue events, request identities and snapshot reconciliation;
`alphalab.execution` gains algorithms, route selection and execution quality;
`alphalab.runtime` sends algorithm children for their parent; and `lifecycle`
gains the joins. No snapshot schema changes and no durable state is added.
Every v3.1 through v3.8 invariant holds. ADR-0044.

**Capabilities declared where they are true.** An adapter declares its venue
features, each market it offers (order types, times-in-force, short sales,
fractional quantities, extended hours, brackets) and each account's permissions
(asset classes, margin, short sales). Every answer is `SUPPORTED`,
`UNSUPPORTED` or `UNDECLARED`, and a check is `COMPATIBLE` only when every
requirement is supported — an answer nobody gave makes it `UNDETERMINED`, never
compatible. What an order needs is derived from the order: a sale beyond the
position is a short sale. The v3.5 deployment record is projected from the same
declaration, so an application declares once.

**One lifecycle, read by both sides.** Twelve normalized event kinds and one
table of legal transitions, read by the OMS and by the venue boundary. Every
venue report gets exactly one outcome — applied, duplicate, stale, conflict,
unknown order or invalid: a redelivered fill and a late acknowledgement change
nothing and are not faults; a fill against a cancelled order is a break for a
reconciliation to settle. Fills converge in any delivery order. Cancels and
amendments carry identities, so a retry is never a second request.

**Execution algorithms, stated completely.** TWAP and VWAP on one trajectory
with a stated urgency, apportioned to whole increments that sum exactly;
participation of the volume actually observed; slicing; an iceberg-like tranche.
Every release tops up to the trajectory, and every child carries the strategies
that asked for its parent. Children are sent for the parent, and their fills
settle on it through the canonical execution path.

**Routing from supplied evidence, explained.** Each venue's quote, declared
capabilities, cost model and latency; the lowest all-in cost or the best quoted
price; one venue, a re-priced split or a partial route only where allowed. Every
venue is judged and the reason recorded, and "no route exists" is kept apart
from "the evidence could not show one".

**Execution measured against named references.** Implementation shortfall
split into delay, trading, explicit and opportunity costs and divided between
the strategies that asked for the order; slippage against decision, arrival or
interval VWAP; fill quality; latency across two clocks; rejection rate; venue
quality. Per-currency totals always, and one reporting-currency total only at a
stated, recorded rate.

**Two reconciliations.** The mirror against the venue's own snapshot —
freshness judged first, then every order, fill, position and balance — and the
book against the mirror, with a parent worked in children joined to them.

```text
  strategies --> allocation --> OrderRequest (contributions) --> OMS parent (EXTERNAL)
                                                                     |
     CapabilityDeclaration --> check_compatibility --+                v
                                                     |      TWAP / VWAP / participation / slicing / iceberg
     VenueQuote + VenueProfile --> select_route -----+----->  ChildOrder --> route_child_order --> venue
                                                                                                   |
     VenueEvent --> apply_venue_event (one outcome each) --> mirror --> apply_broker_execution <---+
                                                                |            (fills on the parent)
                           reconcile_snapshot (mirror vs venue) +  reconcile_execution_state (book vs mirror)
                                                                |
            implementation shortfall, slippage, fill quality, latency, rejection rate, venue quality
```

AlphaLab names no vendor, holds no credential and opens no connection on any of
these paths: an adapter is the application's, and AlphaLab's side of it is the
contract. Full detail in
[ADR-0044](docs/ADR/0044-universal-execution-contract-capabilities-lifecycle-algorithms-routing-and-execution-analytics.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.8.0 is

The eighth capability release on the frozen architecture. v3.7 let research use
information other than prices without looking ahead; v3.8 answers what a desk
running **several strategies** asks next — what to own, where the risk comes
from, what the strategies have in common, and how much capital each one gets —
with every answer's inputs, units and currency stated.

No package is added. The risk model becomes values with identities in
`alphalab.analytics`; the construction authority `alphalab.portfolio_optimizer`
gains one solver, five constrained objectives and Black–Litterman;
`alphalab.portfolio` gains multi-strategy books; `alphalab.allocation` gains
capital plans; and `factor_library`, `api` and `lifecycle` gain the joins. Three
package edges are added, none a cycle; no snapshot schema changes and no durable
state is added. Every v3.1 through v3.7 invariant holds. ADR-0043.

**One risk model.** A `CovarianceMatrix` says what its returns were measured in,
per what period, from what source and over how many observations, and whether it
is positive definite — measured by a rank-revealing Cholesky, never assumed.
Regularizing it derives a new matrix that names its parent. Factor loadings and
classifications refuse holes: an absent loading is not zero, and an unclassified
asset is not put in "unknown". v3.3's decomposition now calls the same arithmetic
and publishes the same numbers, bit for bit.

**Construction over stated constraints.** Minimum variance, mean-variance,
maximum diversification, risk parity with equal or stated budgets, and robust
mean-variance over an ellipsoidal or box uncertainty set — each solved *over*
its constraints (bounds, concentration, gross, sector/country/currency groups,
factor-neutral or banded exposures, turnover, notional caps in money, a
volatility cap) by one dual active-set solver that certifies an optimum by its
KKT conditions and proves infeasibility by naming the constraints that conflict.
Black–Litterman turns a supplied equilibrium prior and views with stated
confidence into a posterior that mean-variance reads. There is no default risk
aversion, view confidence or market portfolio.

**Risk budgets along five dimensions.** Each exposure line's Euler contribution
is computed once, and asset, strategy, sector, country and currency are
groupings of the same lines — so every dimension adds up to the same volatility.
Limits with a maximum, minimum or target are judged under a stated tolerance; a
breach is reported, not enforced.

**Many strategies, one book, several currencies.** Each strategy's own accounting
state becomes a sleeve. The book aggregates every instrument with each
strategy's contribution kept — crossed and opposing positions visible, never
netted away — and values everything in one reporting currency at recorded rates,
reconciling per strategy, per instrument and per currency to the cent.

**Cross-strategy risk with a basis.** Return correlation (currency, period,
sample and source stated), overlap of holdings, factor crowding within the
portfolio, common exposures and capital concentration — each measurement says
what it compares, and "correlation" never means two things.

**Capital allocated where it is.** A plan divides capital across strategies,
markets, brokers, accounts and currencies in each account's own currency,
reconciles every account exactly, records every conversion, refuses what cannot
be met — or scales under a stated `PRO_RATA` and says by how much — reads
committed capital from the execution path's reservation ledger, and becomes each
run's budget. A broker is an identifier, never an adapter.

```text
  returns --> CovarianceMatrix --+--> construct(): min-var, MV, max-div, risk parity,
  (currency, period, source)     |    robust MV, Black-Litterman; constraints; KKT-certified
                                 |                 |
                                 |           weights (result_id)
                                 |                 v
                                 |    PlacementWeights --> allocate_capital --> capital_budget --> run
                                 |                         (per-account currency, reconciled, limits)
                                 v
  strategy states --> sleeves --> MultiStrategyBook --> value_book (FX recorded) --> exposure lines
                                                                                          |
                       +----------------------------+-----------------------------------+
                       v                            v                                   v
              evaluate_risk_budget        overlap, crowding,                  common exposures,
              (asset, strategy, sector,   return correlation                  capital concentration
               country, currency)
```

Full detail in [ADR-0043](docs/ADR/0043-advanced-portfolio-and-risk-construction-risk-budgets-multi-strategy-books-cross-strategy-risk-and-capital-allocation.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.7.0 is

The seventh capability release on the frozen architecture. v3.6 made a strategy
version evaluable by somebody else; v3.7 lets research use **information other
than prices without looking ahead**, and lets a strategy **learn without
becoming irreproducible**. Everything rests on one statement AlphaLab could not
make before: when a piece of information became knowable.

No package is added. The point-in-time core in `alphalab.common` is extended,
`alphalab.alt_data` becomes the point-in-time foundation for external
information — a leaf over `common` that the factor library and the research
layer read — and new modules land in `factor_library`, `research`, `strategy`,
`lifecycle` and `api`. No boundary moves, no snapshot schema changes and no
durable state is added. Every v3.1 through v3.6 invariant holds. ADR-0042.

**Four instants, and a basis.** Every external record carries when it was
observed, when it became knowable, when it takes effect and when this system
received it, and whether the availability instant was declared by the source,
derived by a named rule, or never established — in which case research never
reads it and every selection counts it. Visibility is asked of the world's clock
(publication) or this system's (ingestion), so a backfill is visible from its
arrival.

**Events, alternative data and fundamentals, each one canonical record.** An
`InformationEvent` for any occurrence — earnings, a CPI print, a split, a
headline — an `ExternalObservation` for any measured value, whatever its
category, and a `FundamentalObservation` that keeps fiscal period, publication,
availability and restatement apart. Revisions are vintages, read `AS_KNOWN` or
`ORIGINAL` — never today's restatement at a past instant. Sets have derived
versions naming the source, its version and the bytes; availability is declared
at ingestion by an explicit rule (a column, a delivery lag, the next session
open after a date-only filing, or nothing — `UNKNOWN`).

**Research on what was knowable.** Knowledge frames give the v3.2 feature engine
the latest knowable figure per subject on the price clock — not a forward fill —
and a checked join to the prices, so an information coefficient names both
sources. Event studies anchor at the first observation at or after the instant
an event could be *traded*, not when it happened, exclude corrections and
unknown deliveries by name, and report clustering instead of a p-value.
Trailing twelve months, valuation and ratios are point in time, explain every
undefined figure, and refuse a price observed after the research instant.
Regimes come from declared rules with the caller's labels, persistence, and a
state that resumes to exactly the one-pass result.

**Adaptive strategies that replay exactly.** Configuration, observation, learned
state, update and decision are separate immutable values; one pure function moves
a state, whose identity is a hash chain over every update. Cadence, ordering,
warmup, freezing, checkpoints and reprocessing are explicit. An adaptive
strategy's backtest ends in exactly the state its research replay reaches, its
learned state is in the run snapshot and the run's digest, its fingerprint names
its learning configuration and starting state, and two replays are assessed
`REPRODUCED`, `INPUTS_DIFFER` or `DIVERGED`.

```text
   vendor rows + bytes --> ingest (explicit availability rule) --> versioned set
                                                                        |
        +------------------------------+-------------------------+-----+
        v                              v                         v
   event study                  knowledge frame            fundamentals
   (anchored where              (latest knowable,          (as knowable; TTM,
    it could be traded)          checked price join)        valuation, ratios)
                                       |
                    features, IC, regimes, conditional diagnostics
                                       |
   research replay  <==== same apply_update ====>  adaptive strategy backtest
        |                                                    |
   checkpoint / restore / reprocess             run snapshot, digest, fingerprint
        +------------> assess_adaptive_replay / manifest + rerun <-------+
```

Full detail in [ADR-0042](docs/ADR/0042-point-in-time-research-events-alternative-data-fundamentals-regimes-and-adaptive-strategies.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.6.0 is

The sixth capability release on the frozen architecture. v3.5 carried a strategy
from research to live money; v3.6 makes a strategy version **evaluable by
somebody else** — identified immutably, its results recreatable from an explicit
manifest, its machine-verifiable properties stated with their evidence, and its
ability to move between environments checked against what each one declares.

One package is deepened — `alphalab.lifecycle` — and no boundary moves, no
ownership changes, no package is added and no durable state is added. Every
v3.1 through v3.5 invariant holds. ADR-0041.

**AlphaLab provides; an application consumes.** These are the evidence contracts
a research marketplace such as RedDesk builds on. AlphaLab is not a marketplace:
it lists, publishes, sells, prices, ranks and licenses nothing, holds no seller
or buyer accounts, and does not know who is calling it. Everything below is a
frozen value from a pure function, with a derived identity and a deterministic
JSON form.

**A fingerprint for a strategy version.** `name@N` is registration order, and
nothing said which code ran. `StrategyFingerprint` is derived from five inputs —
the **code** (entry point read from the class registry, and a digest of the
source files by relative path), the **dependencies** (exact pins, with a
declared completeness: `EXACT_CLOSURE`, `DIRECT_ONLY` or `UNDECLARED` — AlphaLab
resolves nothing and reads no installed environment), the **parameters** (read
from the registered version), the **research configuration** and the **engine
version**. Each changes the identity on its own; mapping order, file order and a
distribution name's spelling do not; nothing environmental enters it. The same
fingerprint comes out of a fresh interpreter with a different hash seed.

**A result that says what it was made from.** A `ReproducibilityManifest` names
the dataset version and the digest of its bytes, the fingerprint, the run's own
recorded configuration, the seed and what it does, the engine, and the result —
the digest of the run's complete canonical record. Every field is read from the
authority that owns it. An unseeded run is refused rather than called
reproducible; a study with no stochastic step records its seed as absent. Four
questions stay apart: does the identity recompute, is anything approximate, did
a rerun reproduce it — `REPRODUCED`, `DIVERGED`, or `INPUTS_DIFFER` when the rerun
was of something else — and what a rerun needs that AlphaLab does not hold.

**Certification as eight properties, not a score.** Deterministic, reproducible,
risk limits, maximum leverage, supported markets, required data, resource usage
and runtime behaviour, each `PASS`, `FAIL`, `NOT_ASSESSED` or
`INSUFFICIENT_EVIDENCE`, with its methodology, machine-readable evidence and
what it does *not* establish. Evidence is observed, never asserted: a caller
hands over runs, manifests, runtime observations and measurements, and every
verdict is derived inside. Leverage and drawdown are read exactly as the
pre-trade gate reads them; a CPU or memory figure says how and where it was
measured, and an estimate never meets a budget.

**Portability without adaptation.** One fingerprint against environments that
declare their capabilities — research, paper, live, and "broker A" and
"broker B" as two capability declarations, never two adapters. Eight
requirements — strategy logic, execution, market, data, contracts, runtime, risk
and capital — each satisfied, blocked, unverified or not applicable. A missing
capability is a named blocker, never a substitution, and a deployment that
retunes the fingerprinted parameters is caught.

```text
   dataset ---> research study ---> research configuration
      |                                     |
      |          code + dependencies + parameters + engine
      |                                     v
      +--> backtest ---------------> strategy fingerprint ----------+
      |       |                             |                        |
      |       v                             v                        v
      |  reproducibility manifest     certification report     portability report
      |  (+ a rerun: REPRODUCED /     (8 properties,           (research, paper,
      |   DIVERGED / INPUTS_DIFFER)    no score)                live, broker A / B)
      |
      +--> paper and live runs --> runtime observations --> runtime behaviour
```

Full detail in [ADR-0041](docs/ADR/0041-strategy-evaluation-fingerprints-reproducibility-certification-and-portability.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.5.0 is

The fifth capability release on the frozen architecture, and the bridge between
research and real trading. v3.1 gave AlphaLab a dataset it could trust, v3.2
research methodology, v3.3 the institutional answers and v3.4 what an
instrument's numbers mean; v3.5 answers the questions asked *after* a strategy
is deployed.

One package is deepened — `alphalab.lifecycle` — and no boundary moves, no
ownership changes and no package is added. Every v3.1 through v3.4 invariant
holds. ADR-0040.

**A progression, not a third status flag.** `StrategyLifecycleStage` names the
eight stages the roadmap asks for — research, backtest, validation, paper,
production candidate, live, paused, archived — with a declared transition table,
an append-only history and an explicit relation to the registry's own
`ModelStage`. It replaces neither of the two state machines AlphaLab already
had, because neither can express it: research, backtest and validation are all
`NONE` to the registry, paper and live are both `PRODUCTION`, and a registry
entry does not pause. It names **no environment** — what is live *where* stays
the deployment ledger's single answer.

**A deployment specification that can reproduce its own assumptions.** Strategy
version, parameters read from the registered version, dataset assumptions by
*derived* identity, the `RiskLimits` the pre-trade gate actually enforces, a
capital policy, and typed broker, market and runtime requirements. It identifies
itself by the same SHA-256 content digest `evidence_id_for` uses, so an edited
specification stops verifying.

**Runtime health that cannot read a missing observation as a healthy one.**
Seven categories — stale data, abnormal execution, unexpected position, risk
breach, heartbeat loss, broker disconnect, divergence from expected state —
evaluated from **supplied** observations against the budgets a specification
declares. Every category is either judged or reported as unevaluated, and a
report with nothing wrong and something unevaluated is `UNKNOWN`, never
`HEALTHY`. `live_health` is unchanged and still reports the live driver's own
aggregate in sentences.

**Expected against paper against live.** Trades, fills, slippage, P&L, exposure
and execution latency, with alignment declared rather than guessed and every
tolerance stated — a metric with no tolerance is reported not-comparable, not
matching. Money is compared per currency and never summed across two. A venue's
unmeasured slippage stays missing instead of becoming zero, which
`execution_report_from_broker` has called "absent, not zero" since v2.3.

**Reconciliation of the pair nothing compared.** `broker.reconcile` compares
AlphaLab's mirror of a venue against the venue's records and is unchanged.
`reconcile_execution_state` compares AlphaLab's *own* execution state — the OMS
book, the portfolio, the fills it applied — against that mirror, across fourteen
mismatch classes. Neither side is declared authoritative, nothing is mutated,
and repeating it over the same pair returns an equal report.

```text
   research  ->  backtest  ->  validation  ->  paper  ->  candidate  ->  live
                                   |                                       |
                                   v                                       v
                        deployment specification              runtime health
                    (datasets, risk, capital, broker,        (supplied observations
                     market, runtime -- with a digest)        vs declared budgets)
                                   |                                       |
                                   +------------> comparison <-------------+
                                        expected / paper / live
                                                   |
                                                   v
                                      reconciliation against the
                                      normalized broker state
```

Full detail in [ADR-0040](docs/ADR/0040-strategy-execution-and-production-intelligence.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.4.0 is

The fourth capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust, v3.2 research methodology and v3.3 the institutional
answers; v3.4 makes it say what an instrument's numbers *mean* outside the
market whose conventions had been written into the defaults.

Six of those defaults were US or Binance conventions presented as universals — a
futures contract's currency, an option's multiplier and exercise style, a
funding interval, a crypto contract size. Each produced a number rather than an
error when it was wrong, and each was invisible to the sweep that has looked for
exactly that shape since v2.17, because the sweep reads function parameters and
these are dataclass fields. All six are now required, and the sweep reads both.

**One convention authority.** `alphalab.conventions` holds `MarketConvention` —
venue, calendar id, quote *and* settlement currency, multiplier, tick schedule,
lot specification, settlement rule — with every field required. It imports
`alphalab.common` and nothing else in `alphalab`, which is what lets `options`,
`futures`, `crypto`, `portfolio`, `data` and `api` all use it: the package graph
already runs `data → options → portfolio`, so an edge into any of those would
close a cycle.

**The multiplier, multiplied once.** `Position.market_value` is
`quantity * market_price` and always will be — exactly right for a share, a
thousand times wrong for a contract on a thousand barrels. `contract_notional`
is the only site in AlphaLab that applies a multiplier, and a regression test
reads every module's source to keep a second from appearing.

**Reproducible continuous futures.** A series is reproducible from four stated
things: the contract chain, the roll policy, the observations, and the
adjustment method. `roll_schedule` records when each handover happened and why;
a rule refuses the input it needs rather than approximating it; a missing print
at a roll raises rather than being interpolated.

**An implied volatility that refuses.** Five cases where a quoted price has no
answer, each raising rather than returning a clamp or a fallback.
`surface_from_chain` returns the surface **and every refusal with its reason**,
and the two account for every contract in the chain.

**Both directions of time on an FX rate.** `max_age_seconds` has bounded how
*old* a rate may be since v2.16; a rate dated *after* the conversion instant is
now refused too. Applying it found a genuine look-ahead in the repository's own
settlement fixture, invisible for four releases because nothing checked.

**A fixed-income foundation, and it says foundation.** Bond cash flows, accrued
interest, clean and dirty price, the yield inversion, duration in years and
convexity in years squared. No credit, no optionality, no floating coupons, and
no curve bootstrapper — each needs a model whose choice is the researcher's.

```text
                     alphalab.conventions  (leaf, over common)
                  venue · calendar id · currencies · multiplier
                     tick schedule · lot spec · settlement
                                    |
        +---------------+-----------+-----------+---------------+
        |               |           |           |               |
     futures         options      crypto      macro        portfolio
   chain · roll   IV · surface   venue ·     bonds ·      contracts ·
   continuous ·   expiry ·       funding ·   curves       fx research
   curve · margin Greeks         coverage                  attribution
```

Full detail in [ADR-0039](docs/ADR/0039-global-markets-conventions-and-the-multi-asset-boundary.md)
and [CHANGELOG.md](CHANGELOG.md).

---

## What v3.3.0 is

The third capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust and v3.2 gave it research methodology; v3.3 gives it the
questions an institution asks before allocating to a strategy — what it costs to
trade, how much it can carry, where the P&L came from, where the risk comes
from, and what a crisis would do to it.

Two packages are deepened — `alphalab.execution` and `alphalab.analytics` — one
is added (`alphalab.scenario`), and one module is extended
(`alphalab.common.statistics`). No boundary moves, no ownership changes, and
every v3.1 and v3.2 invariant holds. ADR-0038.

**Execution costs, itemized and separated by how they settle.** Six named roles
— spread, slippage, impact, commission, fee, tax — where there were two numbers.
Costs that move the fill price are kept apart from costs debited to cash,
because collapsing them double-counts. The application ordering is stated in the
module and asserted in tests, and the itemization behind a report recomputes
exactly from the run's own configuration.

**Capacity, as a liquidity question.** `CapacityModel` connects capital,
position size, ADV, turnover, participation and impact, and reports the capital
at which a *named* constraint binds and the asset that bound it. It reads the
same impact model a fill is priced with, so a capacity study and a backtest
cannot disagree.

**Attribution across nine dimensions, with availability reported.** Strategy,
asset, sector, country, currency, venue, broker, factor and execution. A
dimension nothing was supplied for comes back **empty and labelled**, never as
one `UNKNOWN` bucket holding the whole P&L. Currency deliberately does not
total, because its buckets are in different currencies and AlphaLab does not
invent a rate.

**Risk decomposition, under a method you named.** `VaRPolicy` carries the method
and confidence together — historical, Gaussian or Cornish-Fisher — so a figure
cannot travel without the assumptions that produced it. Risk contributions sum
to portfolio volatility exactly; that is what makes it a decomposition rather
than a list.

**One scenario contract, reusable by every portfolio class.** Price, volatility,
FX and liquidity shocks, scoped to assets, sectors or currencies. Applying
returns a new state and never mutates the one it was given. Identity is derived
from content, so a stress result is reproducible in any process.

**Historical scenarios ship as contracts, not as numbers.** `CRISIS_2008`,
`COVID_CRASH_2020`, `RATES_REPRICING_2022` and `COMMODITY_SHOCK_2022` each name
their window and the observations they need, and refuse to apply until a caller
supplies them from a real dataset. AlphaLab ships no market data and **invents
no historical move**: a hard-coded figure would look measured, would not be, and
would be wrong by however much your universe differed from whatever index it was
lifted from.

```
market data -> strategy -> orders -> execution simulation -> fills -> portfolio
                                                                        |
                        attribution  <-  risk decomposition  <-  scenario / stress
```

---

## What v3.2.0 is

The second capability release on the frozen architecture. v3.1 gave AlphaLab a
dataset it could trust; v3.2 gives it the methodology that turns one into a
research result nobody has to take on trust. Two packages are deepened —
`alphalab.factor_library` and `alphalab.research` — and one module is added to
`alphalab.common`. No boundary moves, no ownership changes, and every v3.1
invariant holds.

The path it adds, end to end:

```
canonical dataset  ->  observation frame   (one field, carrying the version)
                   ->  feature panel       (identity derived from dataset + definition)
                   ->  forward returns     (the one quantity that looks ahead)
                   ->  diagnostics         (with the sample counts attached)
                   ->  walk-forward / CV   (purged, embargoed, every fold inspectable)
                   ->  robustness          (seeded, one change at a time)
                   ->  overfitting         (measurements, thresholds, findings)
                   ->  StudyResult         (identity derived from the numbers)
                   ->  ValidationEvidence  (the frozen digest, unchanged)
```

Six properties are the point of it:

1. **A feature is a specification before it is a number.** `FeatureDefinition`
   states the field, the window *in periods*, the parameters and the
   missing-data policy, and defaults none of them. A kind that reads a window
   and was given none raises; a parameter the kind does not read is refused
   rather than ignored, because an unread parameter would still change the
   derived identity.
2. **Missing values are never invented.** `MissingPolicy` has `REFUSE` and
   `SKIP` and no `FILL`. This is v3.1's rule one layer up: a forward fill is a
   statement that yesterday's value was still true today, which is a claim
   about the world rather than an arithmetic convenience.
3. **Nothing looks ahead.** Every window is trailing and inclusive of the
   current observation. The property is asserted for *every* feature kind by
   recomputing on a truncated series and requiring the overlapping values to be
   identical — a feature that peeked would change when the future was removed.
4. **Purging is defined by information windows, not by subtracting dates.**
   `label_ends_from_horizon` reads the actual series: twenty observations later
   means twenty observations later, whether that is twenty-eight calendar days
   across a holiday or twenty. A `PurgePolicy` has no default horizon and
   cannot be built without one, because a default would make an unpurged split
   report that it had been purged.
5. **An unmeasurable quantity is `None`, never `0.0`.** A zero information
   coefficient means "measured, and unrelated", which is a finding. An absent
   one means "not measured", which is not. Every diagnostic carries the sample
   it rests on.
6. **There is no score.** No overfit score, no signal score, no research grade.
   A blended number would have to weight its components, the weights would be a
   judgement nobody could inspect, and the figure would be unfalsifiable.
   `OverfittingReport` keeps measurements, the caller's stated thresholds, and
   findings naming which bound each measurement crossed, in separate fields.

A `ResearchStudy` derives an identity from its description and a `StudyResult`
derives one from the numbers it produced, so a result is tamper-evident.
`run_study` compares the dataset it is handed against the one the study names
and refuses a mismatch — the substitution ADR-0017 closed for evidence, closed
for studies. `evidence_from_study` records the result as
`ValidationMethod.STUDY`, and **`evidence_id_for` did not move**, so every
promotion recorded since v2.6 still verifies.

See [`ADR-0037`](docs/ADR/0037-strategy-research-features-validation-and-overfitting.md),
and `examples/17_feature_engineering.py` through
`examples/24_strategy_research_pipeline.py`.

## What v3.1.0 is

The first release after the v3.0 architecture freeze, and a **capability**
release confined to one package. `alphalab.data` gains the ingestion,
validation, cleaning, provenance and identity machinery it was named for and did
not have. No boundary moves, no ownership changes, and nothing outside
`alphalab.data` is redesigned.

The path it adds, end to end:

```
raw source  ->  schema detection  ->  validation  ->  cleaning (under a policy)
            ->  quality report    ->  canonical dataset
            ->  provenance + a derived, immutable version
            ->  a backtest whose result names the exact bytes it read
```

Four properties are the point of it:

1. **Nothing happens silently.** Every row that does not become a record is
   returned with the reason and the source line; every change that *is* made is
   returned as a `TransformationRecord` with its count and its reason. Both ride
   into the dataset's provenance. The promise is not that the data will be
   clean — it is that a user can always reconstruct what AlphaLab did to it.
2. **The policy is the caller's.** `CleaningPolicy` has four required fields and
   no defaults, because whether a duplicate instant is fatal or routine depends
   on the desk and the dataset. There is **no way to fill a missing price** —
   `MissingValuePolicy` has `REFUSE` and `DROP_ROW` and no `FILL`, because
   forward-fill invents a print that never happened and the invention is
   invisible by the time it reaches an equity curve.
3. **Ambiguity is refused, not resolved.** A Yahoo export carrying both `close`
   and `adj close` is reported ambiguous, because choosing is the difference
   between a backtest on raw prices and one on adjusted prices. So is a
   delimiter two candidates fit, a naive timestamp with no zone named, and a
   numeric column that reads as a valid instant in both seconds and
   milliseconds.
4. **A dataset version is derived and immutable.** The identity is a SHA-256
   over the content hash, schema, zone, calendar, frequency, basis, policy and
   every transformation — so two ingestions of the same bytes agree with no
   shared state. Cleaning *derives* a new version rather than editing one, and
   both stay in the catalogue. That closes a hole under ADR-0017: evidence
   hashed `dataset_id`, but cleaning used to replace the data behind it, so the
   digest still verified while the numbers had changed.

The derived version reaches `MarketDataset`, `RunState.source_id`,
`BacktestResult.dataset_id` and `ValidationEvidence` unchanged — and **the
evidence digest did not move**, so every promotion recorded since v2.6 still
verifies.

Global time is explicit throughout: `MarketCalendar` expresses a venue's
timezone, sessions, lunch break, overnight session, half days and holidays, with
India, the US, Europe, Japan, Hong Kong, Singapore, Australia and 24/7 crypto
all expressible and none privileged. **No holiday data ships**, for the reason
no taxonomy shipped in v2.11 and no FX rate in v2.17.

See [`ADR-0036`](docs/ADR/0036-universal-data-ingestion-provenance-and-the-dataset-version.md),
and `examples/15_data_ingestion.py` / `examples/16_research_from_dataset.py`.

## What v3.0.0 was

v3.0.0 added no capability. It is the release in which AlphaLab's architecture
was **frozen** and the repository made to describe itself truthfully. It remains
the baseline: v3.1 is additive to it, and the invariants below are unchanged.

Three things were established before it could be declared:

1. **A whole-repository architecture audit** — every major responsibility mapped
   to exactly one owner, every state to one snapshot owner and one schema
   constant, the import graph checked for cycles at package and module level,
   and every financial default swept for. The conclusion it had to reach, and
   did: **there is no known internal problem that would require AlphaLab to be
   refactored immediately after declaring it stable.**
2. **The engineering work that would otherwise have made v3.0 a breaking
   release** — done in v2.17 instead, so that v3.0 is additive. Seven deprecated
   surfaces removed with no compatibility aliases, all four of ADR-0032's
   category C items implemented, zero skipped tests and zero warnings.
3. **A documentation truth freeze** — this release. Every current-facing
   document now describes the architecture that actually exists. Historical
   records stay historical and are labelled as such.

What "architecture-frozen" means in practice is written down in
[`nowandfuture.md`](nowandfuture.md): the invariants that must not be changed
casually, the boundaries that are deliberate, what is external, and what is a
non-goal.

## The two paths, and where they meet

| Path | Owner | Answers |
| --- | --- | --- |
| Execution | `runtime.ExecutionPipeline` for the step, `runtime.run.RunEngine` for the run | what happens to one market event |
| Lifecycle | `alphalab.lifecycle` | which strategy version an environment should be running, and why |

A deployment names what should run; running it is the execution path's job.
`lifecycle.execution` joins them as a query with a refusal — it builds no state
and starts nothing — and `strategy.registry` turns the named identity into
executable code, deterministically and with a refusal. Neither is a second
runtime.

## Release history in brief

The full record is in [`CHANGELOG.md`](CHANGELOG.md); the decisions behind it are
in [`docs/ADR/`](docs/ADR). In outline:

| Release | What it established |
| --- | --- |
| **v1.0.0** | Architectural foundation: core domain models, strategy runtime, replay engine, portfolio optimizer, Strategy Studio, Workbench |
| **v1.34.0 – v1.46.0** | The engine series — feature store, factor library, options, futures, crypto, macro, alternative data, ML, deep learning, RL, cloud research, cluster scheduler, experiment tracking |
| **v2.0.0** | Model registry, research assistant, deployment manager, Enterprise; canonical execution domain models unified (ADR-0008) |
| **v2.1.0** | Mark-to-market; the exact accounting identity; O(1) amortized engine histories |
| **v2.2.0** | Unified backtesting and replay on one step (ADR-0010); persistent containers; seeded identifiers |
| **v2.3.0** | One canonical market-data model and one broker boundary (ADR-0011, ADR-0012); four environments, one path |
| **v2.4.0** | The model and strategy lifecycle (ADR-0013) |
| **v2.5.0** | Typed state round-trip and the provider → source link (ADR-0014) |
| **v2.6.0** | Allocation authority and attribution truth (ADR-0015) |
| **v2.7.0** | Instrument identity and dataset provenance (ADR-0016, ADR-0017) |
| **v2.8.0** | Currency roles and run outcomes (ADR-0019 – ADR-0021) |
| **v2.9.0** | Durable run state and deterministic identifier continuation (ADR-0022 – ADR-0024) |
| **v2.10.0** | The strategy boundary — durable strategy state, populated `StrategyContext` (ADR-0025, ADR-0026) |
| **v2.11.0** | Instrument classification and sector provenance (ADR-0027) |
| **v2.12.0** | The currency authority and the settlement boundary (ADR-0028) |
| **v2.13.0** | The run-state store and the durability boundary (ADR-0029) |
| **v2.14.0** | Runtime unification: one `RunEngine`, four drivers (ADR-0030) |
| **v2.15.0** | Real venue transport, streaming market data, artifact bytes, classification provenance, the completed strategy context (ADR-0031) |
| **v2.16.0** | The live driver, governance/RBAC/audit, FX valuation — and the refactor audit (ADR-0032, ADR-0033) |
| **v2.17.0** | Settlement-level multi-currency, the FX rate feed, the strategy-class registry, and the removal of seven deprecated surfaces (ADR-0034, ADR-0035) |
| **v3.0.0** | Architecture frozen; documentation truth freeze. No capability added |
| **v3.1.0** | Universal data ingestion: CSV, schema detection, structured validation, explicit cleaning policies, market calendars, multi-asset semantics, provenance and the derived dataset version (ADR-0036) |
| **v3.2.0** | Strategy research and validation: typed features with derived identity and lineage, factor research, signal diagnostics, walk-forward, purged and embargoed cross-validation, robustness perturbation, overfitting diagnostics, and the reproducible study contract (ADR-0037) |
| **v3.3.0** | Institutional backtesting and portfolio intelligence: itemized execution costs, capacity modelling, nine-dimension attribution, risk decomposition with named VaR methodology, and a reusable scenario/stress contract (ADR-0038) |
| **v3.4.0** | Global markets and multi-asset research: one market-convention authority, reproducible continuous futures, implied volatility with five refusals, FX cross rates and a look-ahead guard, crypto venue metadata and 24/7 coverage, and a fixed-income foundation (ADR-0039) |
| **v3.5.0** | Strategy execution and production intelligence: the research-to-live progression, deployment specifications with a derived identity, structured runtime health from supplied observations, expected/paper/live comparison, and AlphaLab-to-broker reconciliation (ADR-0040) |
| **v3.6.0** | Strategy evaluation: immutable strategy fingerprints over code, dependencies, parameters, research configuration and engine; reproducibility manifests with separate identity, completeness, rerun and external-dependency answers; eight machine-verifiable certification properties with no overall score; and portability checked against declared environment capabilities (ADR-0041) |
| **v3.7.0** | Advanced quant research: one point-in-time statement of when information became knowable, canonical events, alternative data with source identity and versioned sets, point-in-time fundamentals, knowledge frames with a checked price join, event studies anchored where news could be traded, regime detection from declared rules, and adaptive strategies whose learned state replays exactly and reaches the run record (ADR-0042) |
| **v3.8.0** | Advanced portfolio and risk: one risk model with stated currency, period and definiteness; constrained construction — minimum variance, mean-variance, maximum diversification, risk parity, robust, Black–Litterman, factor-neutral — by one certified solver that names conflicts; risk budgets along five dimensions; multi-strategy books with provenance across currencies; cross-strategy correlation, overlap and crowding; and capital allocation across strategies, markets, brokers, accounts and currencies (ADR-0043) |
| **v3.9.0** | The universal execution contract: broker capabilities declared at venue, market and account level and checked three-valued; one order-transition table read by the OMS and the venue boundary, with every venue report given one outcome and cancels and amendments given identities; TWAP, VWAP, participation, slicing and iceberg-like algorithms whose children stay their parent's; route selection from supplied venue evidence, explained; implementation shortfall, slippage, fill quality, latency, rejection rate and venue quality; and snapshot reconciliation (ADR-0044) |
| **v3.10.0** | The first pre-v4 release: risk judged on the projected book, never refusing a reduction; money exact at each currency's minor unit; analytics per instant with declared or observed annualization; next-event fills and recorded execution assumptions; a canonical path linear in the universe; bars stamped at their close; upgradeable snapshots; vendor code and silent defaults removed (ADR-0045) |
| **v3.12.0** | The third pre-v4 release: numerics right at the edges of their range; durable writes and exact restores; exchange calendars inside simulation; strategy capital ceilings; classification limits along any dimension; external information on the execution path; retention and incremental checkpoints; an evidence store; multi-account reconciliation; declared trade prints; LSTM and attention backpropagation; factor-structured construction; a stress program at 10,000 assets, 1,000 strategies and 100 venues (ADR-0047) |
| **v3.13.0** | The final pre-v4 release: American options on a lattice and a volatility term structure; the optimal split; an estimated urgency, the shortfall its model expects and seeded iceberg tranches; a rerun from a manifest and a lock-file reader; cron timers; an exact liquidation price; exchange-rate risk as factors; a box set on a book that may short; checkpoint segments that carry only what changed; one name for one contract, the public API and persisted names as data, every ADR limitation held to the ledger, and a release certificate (ADR-0048) |
| **v3.11.0** | The second pre-v4 release: instrument economics, corporate actions and negative prices; order terms and resting orders; target positions; slices, subscriptions and feedback; leak-proof research, walk-forward optimization and multiple-testing corrections; construction with costs and lots; `studio`, `workbench`, `enterprise` and venue credentials moved to the application; every run entry point pinned; v3.10's performance cost paid back (ADR-0046) |

> **What connectivity means here.** `alphalab.broker.BrokerProtocol` and the
> normalized venue events are the contract a venue adapter implements;
> `alphalab.broker.PaperBroker` is one in memory; `alphalab.market.stream.StreamingSource`
> consumes a push feed through an RFC 6455 WebSocket client; and
> `alphalab.runtime.live.LiveSession` drives the settle/advance/route cycle.
> Since v3.11 the library holds no venue credentials and signs no requests: an
> adapter that authenticates to a venue belongs to the host application.
> `tests/reference_adapter` keeps the signed-REST adapter that used to ship
> (`alphalab.broker.transport` / `alphalab.broker.venue`) as a worked example, and
> the tests drive it end to end over real sockets against local servers that
> verify signatures, timestamp windows and idempotency keys.
>
> What is **not** here: verification against any commercial venue, and any named
> vendor's request shapes. See
> `docs/ADR/0012-broker-boundary-and-environment-parity.md`,
> `docs/ADR/0031-real-transport-streaming-artifacts-and-the-completed-boundaries.md`
> and `docs/ADR/0046-the-pre-v4-capability-release-instruments-orders-targets-slices-construction-and-the-application-boundary.md`.
>
> **A deployment is a lifecycle fact, not an operation on a machine.** It records
> that an environment *should* be running a strategy version. It starts no
> process, opens no connection and reaches no venue.

---

# Core Principles

AlphaLab is built around a consistent engineering philosophy.

- Immutable domain models
- Deterministic execution
- Event-driven architecture
- Pure functional engine APIs
- Strict static typing
- Modular package boundaries
- Production-oriented design
- Reproducible research workflows

---

# Architecture

## The integrated execution path

`alphalab.runtime.ExecutionPipeline` is the concrete, wired-together spine. One
market event flows through each stage as a pure function over an immutable
`ExecutionPipelineState`:

```text
Market event (Quote / Bar / Tick)
        │
        ▼
Mark to market  →  positions re-priced, risk resynced from the marked book
        │
        ▼
Strategy   →  Intents                        (sees the marked portfolio)
        │
        ▼
Allocation →  sized OrderRequests            (core.OrderRequest, core.enums.Side)
        │
        ▼
Risk       →  RiskDecision (approve / reject)
        │
        ▼
OMS        →  Order lifecycle                (oms.order.Order — canonical)
        │
        ▼
Execution  →  ExecutionReport                (simulator, or a venue fill returning)
        │
        ▼
Portfolio  →  cash, positions, realized P&L  (per settlement currency)
        │
        ▼
Analytics  →  PerformanceReport (compiled on demand)
```

The caller owns the process. A **driver** decides which record comes next and
what clock reading judges it, and holds no state of its own:

| Driver | Cursor |
| --- | --- |
| `runtime.session.TradingSession` | a `MarketDataSource` |
| `backtesting.BacktestEngine` | a `MarketDataset` |
| `backtesting.replay.ReplayBacktest` | `alphalab.replay`'s cursor |
| `runtime.live.LiveSession` | a venue, through the settle/advance/route cycle |

With routing `EXTERNAL` an accepted order stays working in the OMS, holding its
capital, until a venue reports on it. Since v3.9 that path is one contract
(ADR-0044): a capability report gates what is sent; `runtime.route_order` sends
an order whole, or an execution algorithm's children go out through
`route_child_order` for their parent; every venue report is applied to the
mirror by `broker.apply_venue_event` with exactly one outcome; and a venue fill
reaches the book through `apply_broker_execution` — the same step a simulated
fill takes. `reconcile_snapshot` and `reconcile_execution_state` check the
mirror against the venue and the book against the mirror.

## Instrument identity on the production path

Everything reaching the execution path crosses `alphalab.market.normalization`,
and that boundary resolves identity through an authority rather than passing a
provider symbol through:

```text
(provider, symbol)  →  InstrumentRegistry  →  canonical asset_id (deterministic UUID)
```

An `asset_id` is opaque and *derived*, not minted — `uuid5` over a canonical key
(`asset_type`, `exchange`, `symbol`, `currency`) under a frozen namespace — so
two independently configured environments agree on the identity of one
instrument with no shared database. Registration is explicit: derivation alone
would turn every typo into a new instrument.

```python
from alphalab.core.enums import AssetType
from alphalab.data.time import BarStamp
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market.bar import TimeFrame
from alphalab.market.normalization import NormalizationPolicy

instruments = register_instrument(
    InstrumentRegistry(),
    InstrumentRecord(
        "BTCUSDT", AssetType.CRYPTO, "BINANCE", "USDT", aliases={"my-provider": "BTCUSDT"}
    ),
)
policy = NormalizationPolicy(
    venue="BINANCE",
    currency="USDT",
    timeframe=TimeFrame.M1,
    identity=instruments,
    provider="my-provider",
    bar_stamp=BarStamp.INTERVAL_START,  # this provider stamps a bar when it opens
)
```

The policy states everything a wire record cannot: its currency (a quote or
trade is refused without one), its timeframe (a bar is refused without one),
whose symbols these are, and which end of its interval a bar's timestamp
names — a start-stamped bar is moved to its end, the instant its close is
known, and an undeclared one is refused (ADR-0045).

**A production provider → execution path requires `InstrumentRegistry`-backed
resolution.** An unregistered `(provider, symbol)` is refused at the boundary
with `InstrumentResolutionError`, naming both — not carried onward to fail at the
first fill.

The second mode, `UnresolvedIdentity`, keeps the wire → canonical lift testable
without a registry. **It is not a production execution configuration**: it yields
provider symbols, which `core.Fill` and `core.Trade` refuse, and
`ProviderHistorySource.of` rejects it before calling the provider. See ADR-0016.

The registry is read a second time on the **fill** path: `ExecutionPipeline` asks
it what sector the filled asset is classified as and freezes that answer onto the
fill's `TradeRecord.sector_id`, and asks it what currency the instrument trades in
to decide whether this run may trade it at all. Only `record_for` is ever called —
the pipeline resolves no provider symbol, derives no `asset_id` and classifies
nothing. See ADR-0027 and ADR-0028.

## Money: settlement truth and reporting truth

A run settles the currencies `ExecutionPipelineConfig.also_settles` names — empty
by default, which is the single-currency pipeline. `PortfolioState.realized_pnl`
and `commission_paid` are per-currency, so a EUR fill accrues EUR P&L permanently
and nothing is ever summed across two. A valuation names one reporting currency
and converts into it **on demand**, recording every rate it used.

Every rate is *supplied* and carries its source and the instant it was true.
There is no default, no fallback of 1.0, **no triangulation and no implicit
inversion**, a stale rate is refused rather than used, and a run that settles a
currency it has not funded is refused rather than financed.
`alphalab.portfolio.fx_feed` is the boundary rates arrive across — it adds a
contract, and **AlphaLab ships no FX data**. See ADR-0035.

v3.8's multi-strategy book valuation and capital allocation read the same
authority: a book is valued in one reporting currency with every conversion kept,
and capital is allocated in each account's own currency, with only plan-wide
figures translated. A cross rate is derived only when the caller asks for one,
naming the currency it goes through. See ADR-0043.

## Standalone engine libraries

Everything below is importable, deterministic, and independently tested, but is
**not** wired into `ExecutionPipeline` or into the lifecycle path:

| Area | Packages |
|---|---|
| Reporting | `reporting` |
| Portfolio construction | `portfolio_optimizer` |
| Feature registry | `feature_store` |
| Learning | `ml`, `deep_learning`, `reinforcement_learning` |
| Asset classes | `options`, `futures`, `crypto`, `macro` |
| Market conventions | `conventions` — a leaf over `common`, imported *by* `data`-side and `portfolio`-side packages rather than reached from a run |
| Scale-out | `cloud_research`, `cluster_scheduler`, `distributed` |
| Workflow | `research_assistant` |
| Provider surfaces | `brokers` |
| Infrastructure | `scheduler` — deterministic timers, session boundaries from a `MarketCalendar` |

Several packages that are often described as standalone are **not**, and the
import graph is the authority:

- `data`, `marketdata`, `market`, `instrument`, `broker` and `persistence` are
  each reached from the execution path. `data.feed` and `marketdata.feed` supply
  the wire records `market.normalization` lifts; `marketdata` is consumed by
  `market`; `broker` is reached through `runtime.broker_routing`; `persistence`
  supplies the codec spine and the `RunStateStore` every snapshot owner writes
  through.
- `research`, `experiment_tracking`, `model_registry`,
  `deployment_manager` and `backtesting` are imported by `alphalab.lifecycle`.
- `factor_library` (since v3.2) and `alt_data` (since v3.7) are imported by
  `research`, so the lifecycle path reaches them. `alt_data` imports
  `alphalab.common` and nothing else, and `test_v37_invariants.py` measures it.
- `portfolio_optimizer` reads the risk model in `analytics` since v3.8
  (ADR-0043) and is still reached by neither path: a construction answers what
  to own, and turning it into orders is the caller's decision.
- `research_assistant` is the one package the lifecycle *names* without
  importing: it produces a candidate and `to_strategy_definition` lifts it into
  the canonical `StrategyDefinition` the lifecycle takes. The dependency runs
  through the definition, not through the package.

---

# Getting Started

## Installation

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git
cd AlphaLab
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

AlphaLab itself needs nothing beyond the standard library; the `[dev]` extra
installs the lint, type-check, test and build toolchain.

---

# Learn AlphaLab

The recommended way to learn the framework is through the curated examples.

| Example | File | Description |
|---------|------|-------------|
| 01 | `01_research.py` | Research engine |
| 02 | `02_backtest.py` | Your first backtest: a CSV through the canonical path, the strategy stating target quantities |
| 03 | `03_replay.py` | Historical replay cursor |
| 04 | `04_market_data.py` | Provider wire bars → normalization → canonical market state |
| 05 | `05_broker_connection.py` | The two broker boundaries: one venue, or a registry of many |
| 06 | `06_portfolio_optimizer.py` | Portfolio construction |
| 07 | `07_universal_data.py` | Universal Data Engine: state, versions and the catalogue |
| 08 | `08_strategy_studio.py` | Rebalancing to target weights, with a lot size declared on the instrument |
| 09 | `09_workbench.py` | Cross-sectional decisions on complete instants (slices), and a cash-account rotation |
| 10 | `10_complete_pipeline.py` | Research to a traded book: walk-forward optimization, construction with costs and lots, out-of-sample trading, benchmark statistics |
| 11 | `11_unified_backtest.py` | Dataset → orders → fills → P&L → analytics, plus replay parity |
| 12 | `12_model_lifecycle.py` | Research candidate → model → strategy version → deploy → rollback |
| 13 | `13_durable_run_state.py` | Stop a run, store it, continue it in another process |
| 14 | `14_multi_currency_settlement.py` | FX feed → two settlement currencies → one reported figure |
| 15 | `15_data_ingestion.py` | **A broken CSV** through detection, validation, an explicit cleaning policy and a quality report, out as a versioned canonical dataset |
| 16 | `16_research_from_dataset.py` | Research access by dataset version, point-in-time selection, and a backtest that names the exact bytes it read |
| 17 | `17_feature_engineering.py` | **Typed feature definitions** with derived identity, trailing windows and warmup, missing data skipped or refused, and lineage back to the exact dataset version |
| 18 | `18_factor_research.py` | Ranking, three neutralizations that are not interchangeable, the information coefficient, decay, turnover and exposure |
| 19 | `19_signal_diagnostics.py` | Forward-return analysis across horizons, quantile profiles, monotonicity, and regime-conditioned diagnostics |
| 20 | `20_walk_forward_validation.py` | Train / validate / test / roll, exact fold boundaries, and purging on both sides |
| 21 | `21_time_series_cross_validation.py` | Rolling, expanding, purged blocked k-fold and embargoed — and why an embargo only bites on a blocked scheme |
| 22 | `22_robustness_testing.py` | Parameter, data and signal perturbation, missing data, delay, cost, block bootstrap and Monte Carlo — every one seeded |
| 23 | `23_overfitting_diagnostics.py` | Parameter sweeps that count every trial, cliffs against plateaus, out-of-sample degradation, stability, and multiple-testing risk |
| 24 | `24_strategy_research_pipeline.py` | **The complete v3.2 path**: dataset → features → diagnostics → validation → robustness → overfitting → study result → evidence → promotion gate |
| 25 | `25_execution_simulation.py` | The six execution cost roles, the two settlements, and the stated ordering |
| 26 | `26_capacity_modelling.py` | Capacity as a liquidity question: what binds, and what moves the answer |
| 27 | `27_portfolio_attribution.py` | Nine attribution dimensions, reconciliation, and reporting what is unavailable |
| 28 | `28_risk_decomposition.py` | Three VaR methods, the Euler decomposition, and samples that refuse |
| 29 | `29_portfolio_stress_testing.py` | Synthetic and historical scenarios — and why 2008 ships as a contract |
| 30 | `30_scenario_engine.py` | One scenario contract applied to five different books |
| 31 | `31_global_market_conventions.py` | **The v3.4 convention authority**: tick size against tick value, lots, rounding, the multiplier applied once |
| 32 | `32_exchange_calendars_and_sessions.py` | Four venues in four timezones, overnight sessions, lunch breaks, daylight saving and settlement dates |
| 33 | `33_futures_contracts_and_rolls.py` | Contract chains, three roll rules, curve shape, roll yield and published margin |
| 34 | `34_continuous_futures_research.py` | Continuous series from four stated inputs, three adjustments, and a missing roll print refused |
| 35 | `35_options_chains_and_greeks.py` | Chains, Greeks, multipliers that are not 100, and expiry outcomes |
| 36 | `36_implied_volatility_surface.py` | Implied volatility with five refusals, surfaces and term structure |
| 37 | `37_fx_research.py` | Quotation direction, named crosses, covered-parity forwards and the look-ahead guard |
| 38 | `38_crypto_perpetuals_and_funding.py` | Two venues disagreeing about funding, fees and price source, on a 24/7 clock |
| 39 | `39_fixed_income_foundation.py` | Cash flows, clean/dirty/accrued, yield inversion, duration and convexity |
| 40 | `40_multi_asset_portfolio.py` | Five asset classes, four venues, four currencies and one book |
| 41 | `41_strategy_lifecycle_progression.py` | **The v3.5 progression** from research to live, with every refusal and why |
| 42 | `42_deployment_specification.py` | What a strategy version needs to run as it was researched, with a digest |
| 43 | `43_runtime_health.py` | Seven health categories from supplied observations, and why incomplete is `UNKNOWN` |
| 44 | `44_expected_paper_live_comparison.py` | A backtest, a paper run and a venue's records compared with declared alignment and tolerances |
| 45 | `45_broker_reconciliation.py` | AlphaLab's execution state against a normalized broker state |
| 46 | `46_strategy_fingerprints.py` | **The v3.6 fingerprint**: five defining inputs, each changing the identity alone, reproduced in a second interpreter |
| 47 | `47_reproducible_research_artifacts.py` | A manifest of everything a result was made from; a rerun that reproduces, one that diverges, and an unseeded run refused |
| 48 | `48_strategy_certification.py` | Eight machine-verifiable properties, four statuses, no score, and evidence that is observed rather than asserted |
| 49 | `49_strategy_portability.py` | One fingerprint across research, paper and two brokers declared as capabilities; blockers named, nothing adapted |
| 50 | `50_event_driven_research.py` | **The v3.7 event model**: four instants kept apart, releases placed in the trading day, and an event study anchored where the news could be traded |
| 51 | `51_alternative_data_provenance.py` | Alternative data with source identity, version and bytes; availability declared, derived or unknown; revisions as vintages; a checked join to prices |
| 52 | `52_fundamental_research.py` | Statements as they were knowable: a date-only filing at the next open, a restatement only once published, TTM, valuation and a PIT value factor |
| 53 | `53_regime_detection.py` | Regimes from declared rules and the caller's labels, with persistence, a resumable state and a regime-conditioned diagnostic |
| 54 | `54_adaptive_strategy.py` | An adaptive strategy whose backtest ends in its research replay's state; cadence, freezing, snapshot restore and a fingerprint naming its start |
| 55 | `55_reproducible_adaptive_replay.py` | Checkpoints continued in a second interpreter, a late observation reprocessed, replays assessed, and an adaptive run's manifest reproduced |
| 56 | `56_portfolio_construction.py` | **The v3.8 construction path**: one stated risk model; minimum variance, mean-variance, maximum diversification, risk parity and robust construction over the same constraints; a factor-neutral, sector-capped rebalance; an infeasible set named; Black–Litterman |
| 57 | `57_risk_budgeting.py` | A three-currency book's risk along asset, strategy, sector, country and currency, each adding up to the same volatility, judged against a budget |
| 58 | `58_multi_strategy_portfolio.py` | Three strategies' own books as one, every holding with its owners, valued in dollars and in euros with every conversion recorded — and refused without rates |
| 59 | `59_cross_strategy_risk.py` | Strategies compared by returns (with the basis stated), by holdings, by factor crowding, by common exposures and by the capital they share |
| 60 | `60_capital_allocation.py` | Capital across strategies, markets, two brokers, three accounts and three currencies, from a risk-parity construction to each run's budget and a fingerprint |
| 61 | `61_broker_capability_model.py` | **The v3.9 execution contract**: capabilities declared at venue, market and account level, three-valued, checked per venue with every reason; contradictions refused; the v3.5 record projected from the same declaration |
| 62 | `62_normalized_execution_lifecycle.py` | One transition table for the OMS and the venue; every venue report classified once; a fill crossing a cancel, amendments and retries with identities; out-of-order fills converging; a snapshot reconciliation |
| 63 | `63_execution_algorithms.py` | TWAP and VWAP with a stated urgency, an incomplete profile refused or planned on time, top-up releases, participation of observed volume, slicing and an iceberg-like tranche |
| 64 | `64_smart_routing.py` | A route chosen from supplied venue evidence with every venue explained; all-in cost against quoted price; a re-priced split; stale, look-ahead and foreign quotes; INFEASIBLE against INSUFFICIENT_EVIDENCE |
| 65 | `65_execution_analytics.py` | Strategy → order → VWAP → capability check → normalized venue reports → reconciliation → implementation shortfall, slippage, latency, rejections and venue quality, in two currencies |
| 66 | `66_american_options.py` | An American put on a lattice converging to Hull's table; early exercise on a call made worth something by a dividend; lattice Greeks; implied volatility through the lattice; a volatility term structure linear in total variance |
| 67 | `67_optimal_split_and_urgency.py` | The greedy sweep against the optimal split across three venues; a falling marginal cost refused; an urgency estimated from stated inputs; iceberg tranches varied from a seed and reproduced |
| 68 | `68_rerun_from_a_manifest.py` | A lock file read into a dependency manifest; a backtest re-executed from its manifest — REPRODUCED, DIVERGED and located, INPUTS_DIFFER with nothing run |
| 69 | `69_cron_timers.py` | Cron expressions on a stated zone's wall clock across both daylight-saving transitions; refusals when written; a cron timer fired and rescheduled on the engine |

Run any example:

```bash
python examples/01_research.py
```

Examples `01`–`10` date from v1.0.0 and exercise the standalone engine APIs;
`11` (v2.2) drives the integrated execution path, `12` (v2.4) the lifecycle path,
`13` (v2.13) durable run state across two processes, `14` (v2.17)
settlement-level multi-currency, `15`–`16` (v3.1) universal data ingestion and
research from a canonical dataset, `17`–`24` (v3.2) the research and validation
path, `25`–`30` (v3.3) institutional backtesting, `31`–`40` (v3.4) global
markets, `41`–`45` (v3.5) strategy execution, `46`–`49` (v3.6) strategy
evaluation, `50`–`55` (v3.7) point-in-time research and adaptive strategies,
`56`–`60` (v3.8) portfolio construction, risk budgets, multi-strategy books,
cross-strategy risk and capital allocation, `61`–`65` (v3.9) the execution
contract — capabilities, the normalized lifecycle, algorithms, routing and
execution analytics — and `66`–`69` (v3.13) American options, the optimal
split, reruns from a manifest and cron timers.
None are part of the automated test suite, though all sixty-nine run as a
release gate; `17`–`24` all ingest the same committed panel in
`examples/data/research_panel.csv` so their numbers are comparable with each
other.

---

# Documentation

The complete documentation is available in the `docs/` directory.

| Document | Description |
|----------|-------------|
| `docs/GETTING_STARTED.md` | Installation and first steps |
| `docs/ARCHITECTURE.md` | Framework architecture, and what is actually built |
| `docs/SYSTEM_DESIGN.md` | Internal design and subsystem interaction |
| `docs/STATE_MODEL.md` | Immutable state, snapshots and schemas |
| `docs/EVENT_MODEL.md` | Event-driven architecture and lifecycle |
| `docs/ADR/` | Architectural Decision Records — 44 of them |
| `docs/EXAMPLES.md` | Example walkthroughs |
| `docs/ENGINEERING_GUIDELINES.md` | Engineering standards |
| `nowandfuture.md` | The long-form project reference: ownership, invariants, boundaries, what must not change casually |
| `ROADMAP.md` | What is delivered, what is deliberate, what remains optional |

---

# Repository

```text
alphalab/
├── Execution spine (wired by runtime.ExecutionPipeline / runtime.run.RunEngine)
│   core/          Canonical domain models — Side, OrderRequest, Fill, Trade, ids,
│                  and the v3.9 capability model and order-transition table (ADR-0044)
│   runtime/       ExecutionPipeline, RunEngine, drivers, broker routing (v3.9: children
│                  sent for their parent), snapshots
│   strategy/      Strategy protocol, dispatcher, supervisor, class registry,
│                  and the v3.7 adaptive engine (ADR-0042)
│   allocation/    Intent sizing / netting → OrderRequest, reservations, contributions,
│                  and v3.8 capital plans across accounts and currencies (ADR-0043)
│   risk/          Pre-trade risk checks and limits
│   oms/           Order lifecycle (oms.order.Order is canonical; reads core's table)
│   execution/     Deterministic execution simulator, commission, fill policies, and
│                  the v3.9 algorithms, routing and execution quality (ADR-0044)
│   portfolio/     Cash ledger, positions, NAV, per-currency P&L, FX, FX feed,
│                  and v3.8 multi-strategy books (ADR-0043)
│   analytics/     Performance report, attribution, and the v3.8 risk model,
│                  risk budgets and cross-strategy risk (ADR-0043)
│   market/        Canonical market model, normalization, sources, streaming
│   instrument/    Canonical instrument identity, registry, classification
│   common/        Version, events, serialization, ids, containers, TLS
│   conventions/   Market conventions — settlement, ticks, lots, multipliers,
│                  day counts, compounding. A leaf over common (ADR-0039)
│   persistence/   Codec spine, RunStateStore, typed decoding
│   backtesting/   Dataset → execution path → analytics (backtest + replay drivers)
│   replay/        Deterministic replay cursor
│
├── Lifecycle path (composed by alphalab.lifecycle)
│   lifecycle/     Research → model → strategy version → promotion → deployment,
│                  and the v3.6 fingerprints, manifests, certification, portability
│   experiment_tracking/  model_registry/  deployment_manager/
│   research/      Run evaluation, the v3.2 study methodology, and v3.7
│                  event studies and regime detection
│   factor_library/  The computation engine: features, factors, diagnostics,
│                  and v3.7 knowledge frames over point-in-time information
│
├── Data surfaces
│   data/          Wire records, and the universal data engine:
│                  ingestion, schema detection, validation, cleaning,
│                  calendars, corporate actions, provenance, `api`
│   alt_data/      Point-in-time external information — observations, events,
│                  fundamentals, versioned sets, sessions. A leaf over common
│                  (ADR-0042)
│   marketdata/    HTTP transport, RFC 6455 WebSocket client, wire records
│                  (the vendor clients were removed in v3.10, BND-001)
│
├── Broker surfaces
│   broker/        The canonical single-venue boundary (BrokerProtocol), and the
│                  v3.9 normalized venue events, request identities and snapshot
│                  reconciliation (ADR-0044)
│   brokers/       The many-venue connector framework (BrokerConnectorProtocol)
│
└── Standalone engines
    reporting/
    portfolio_optimizer/  Construction: v1 closed forms, and v3.8 constrained
                   construction and Black–Litterman over the analytics risk
                   model (ADR-0043)
    feature_store/
    ml/  deep_learning/  reinforcement_learning/
    options/  futures/  crypto/  macro/
    cloud_research/  cluster_scheduler/  distributed/
    research_assistant/  scheduler/
    scenario/       Price, volatility, FX and liquidity shocks (ADR-0038)
```

All 43 top-level packages are accounted for above. (v3.11 removed `studio`,
`workbench` and `enterprise`; v3.12 removed `plugins` and `optimizer`.)

Additional directories:

```text
docs/          Documentation and ADRs
examples/      69 runnable examples
benchmarks/    @@GATE@@ performance benchmarks
tests/         @@GATE@@ tests — unit, integration, regression
configs/       Reference configuration files
```

---

# Quality Assurance

AlphaLab is continuously validated through automated tooling.

@@GATE@@ (quality assurance bullets: tests, mypy, ruff, benchmarks/examples, mutation harness, stress)
- ✅ Source distribution, wheel and `twine check` validation, and each
  distribution installed into a clean environment and exercised from outside
  the checkout (`tests/installed_smoke.py`)

Neither the zero skips nor the zero warnings can be satisfied by configuration:
`tests/regression/test_the_suite_reports_nothing_deferred.py` reads the collected
items rather than the summary line, refuses any `skipif` it does not list (it
lists none — the suite skips nothing whoever runs it, root included), and spawns
a **fresh interpreter** with `-W error::DeprecationWarning` to import every
module in the package tree. CI runs the suite with `-W error`, so a
`ResourceWarning` fails it too.

---

# What is deliberate, what is external, what is optional

The v3.0 audit classified everything that remains, and the pre-v4 audit
re-examined each classification (ADR-0045). Nothing below is a defect.

## Deliberate design — pinned by regression tests

These look like duplication or a layer violation and are not. A future
"unification" has to break an assertion and read a reason first; the reasons live
in `tests/regression/test_shared_names_stay_distinct.py` and
`test_venue_concepts_stay_distinct.py`.

- **Two `OrderBook`s** — `oms.book.OrderBook` holds *my* working orders;
  `data.feed.OrderBook` is a venue depth snapshot. They share no operation.
- **Two `PortfolioEngine`s** — `portfolio` does accounting, `portfolio_optimizer`
  does construction. Only the accounting one is reachable from the execution path.
- **Parameter search and portfolio construction** — `research.parameter_sweep`
  searches parameters, `portfolio_optimizer` sets weights. Until v3.12 a second
  package, `optimizer`, searched parameters too; it was removed (SCF-003).
- **`broker` vs `brokers`** — one venue versus many venues and many accounts.
  The connector package routes the canonical types under their canonical names
  (since v3.13; its historical aliases were removed, API-001); the identities
  are asserted.
- **A wire record and a domain record** — `data.feed` is `float`/`symbol`,
  `alphalab.market` is `Decimal`/`asset_id`. They sit on opposite sides of an
  explicit conversion (ADR-0011).
- **Three things called a venue** — listing exchange, market-data attribution and
  execution venue. None derives from another (ADR-0019).
- **One calendar** — `MarketCalendar` answers sessions, with holidays and a
  timezone (ADR-0039); v3.4's two one-method protocols are not calendars. The
  scheduler's own `TradingCalendar`, which decided weekends in UTC, was removed
  in v3.10 (ADR-0045).
- **Three margins** — an account calculation, a liquidation *price*, and a
  clearing house's *published* figure. None can produce either of the others.
- **Two exposures** — `ExposureEngine` reads `Position.market_value` and means
  shares; `contract_exposures` takes the convention that supplies a multiplier.
  The second is not a better version of the first; it answers a question the
  first cannot express.
- **Two currency breakdowns** — `AttributionDimension.CURRENCY` buckets realized
  P&L by settlement currency and deliberately has no total;
  `currency_attribution` decomposes a reporting-currency return and has one,
  because it was given the rates the other was not.
- **Five content identities** — a specification id (what a deployment needs),
  an evidence id (one measurement), a study id (one experiment), a strategy
  fingerprint (one strategy version) and a manifest id (one result's inputs).
  None can stand in for another (ADR-0041).
- **Three ways of checking a strategy** — `evaluate_policy` gates a promotion,
  `validate_specification` checks a specification's coherence, and a
  certification report states eight properties with no verdict.
- **Two ways of reading "as of"** — `known_as_of` trusts a record's release
  date and is unchanged; v3.7's `PointInTimeIndex` reads a stamp that can say
  the availability was never established. **Engine events and information
  events** — `BaseEvent` says a state changed inside AlphaLab;
  `InformationEvent` is data about the world. **Two regime tools** — v1's
  `analyze_regimes` scores a return series; v3.7's `classify_regimes` applies a
  declared rule (ADR-0042).
- **A projection and an optimization** — v1's `apply_weight_constraints` clips a
  closed-form answer and spreads the excess; v3.8's `construct` solves *over*
  the constraints. With only the budget binding the two minimum variances agree;
  add a cap and the projection is feasible and worse, which a regression test
  shows numerically (ADR-0043).
- **Budgets and limits** — `CapitalBudget` bounds the capital a run deploys,
  `risk.RiskLimits` bounds an order pre-trade, and v3.8's `RiskBudget` — whose
  limits are `BudgetLimit`s, named apart from `RiskLimits` on purpose — bounds
  where a finished book's volatility comes from, and only reports.
- **Two target weights** — v1's `portfolio_optimizer.TargetWeights` are a
  portfolio's asset weights; v3.8's `allocation.PlacementWeights` are a capital
  plan's fractions per placement, named apart before release because the two
  meet in the construction-to-capital workflow.
- **Correlation of returns, similarity of holdings** — `CorrelationMatrix` and
  `StrategyCorrelation` always name their currency, period and sample;
  `ExposureSimilarity` compares what strategies hold and has neither.
- **Two capability shapes** — v3.5's `BrokerCapabilities` is a deployment's
  broker-wide, two-valued summary; v3.9's `CapabilityDeclaration` is scoped to
  venue, market and account and three-valued. The first is *projected* from the
  second, and the projection refuses what was never declared (ADR-0044).
- **An assumption and a measurement** — `ExecutionReport.slippage` and
  `ExecutionCosts` are what a cost model *assumed* for a fill; v3.9's
  `measure_slippage` and `implementation_shortfall` measure what an order
  achieved against a named reference. A routing decision's expected price is a
  prediction of the same venue; neither is substituted for the other.
- **Choosing a route and sending one** — `execution.select_route` decides where;
  `runtime.route_order` and `route_child_order` send; an adapter connects.
- **A child order is an instruction** — an algorithm's `ChildOrder` is sent as a
  venue order *for* its parent OMS order, which keeps the reservation and the
  strategies' contributions; the OMS never holds a child.
- **Three reconciliations, two pairs** — `broker.reconcile` (the mirror against
  loose venue records) and v3.9's `reconcile_snapshot` (against a whole,
  dated snapshot) compare the same pair at two depths of evidence;
  `lifecycle.reconcile_execution_state` compares the book with the mirror.
- **Standalone engines with no in-repo consumer** — that is ADR-0009, not an
  orphan.
- **No marketplace logic.** AlphaLab provides the evidence contracts — a
  fingerprint, a manifest, a certification report, a portability report — and
  an application such as RedDesk consumes them. Listing, publishing, payment,
  ranking, licensing and tenancy are the application's; a regression test sweeps
  the v3.6 modules for them (ADR-0041).

## External dependencies — not internal engineering

- **Verification against a commercial venue.** No network egress, no vendor
  credentials. The protocols are proven against local servers.
- **Named vendor request shapes.** Each belongs to a vendor adapter.
- **FX data.** v2.17 ships the rate-feed *boundary* and not a single rate.
- **Classification data.** v2.11 ships the security master's *mechanism* and
  v2.15 its provenance; AlphaLab ships no taxonomy and no reference-data feed.
- **Market conventions.** v3.4 ships the convention *contract* and not one
  venue's values: no holiday list, no tick table, no lot schedule, no venue
  registry.
- **Deposit and discount curves.** Both rate inputs to a covered-parity forward
  are required arguments; AlphaLab bootstraps no curve.
- **Futures margin figures and crypto venue specifications.** A clearing house
  and an exchange publish them. AlphaLab holds no adapter, client or
  credential.
- **What a rerun needs, and what an environment offers.** A reproducibility
  manifest identifies the dataset bytes, the strategy code, the dependency set
  and the engine; AlphaLab stores none of them. A dependency closure is a
  caller's declaration (a lock file) — AlphaLab resolves nothing and reads no
  installed environment. Every `TargetEnvironment`, runtime observation and
  resource measurement is supplied by whoever knows it.
- **Alternative data, events and fundamentals.** v3.7 ships the point-in-time
  *contract* — records, sets, ingestion rules, queries — and no vendor, feed,
  file or line-item taxonomy. The rows, the bytes and a price for valuation are
  the caller's.
- **Portfolio and risk inputs.** v3.8 estimates a sample covariance and nothing
  else: expected-return forecasts, market values for an equilibrium prior,
  views and their confidence, countries, the operator's sector data, FX rates,
  account balances, and the mapping from a broker or account identifier to an
  adapter and its credentials are all the caller's.
- **Venues.** v3.9 ships the execution *contract* — capability declarations,
  normalized venue events, request identities, algorithms, routing and
  measurement — and no venue: the declaration for a real venue, its quotes,
  volume profiles, printed volume and benchmark prices, and the adapter that
  translates its messages into `VenueEvent`s are the application's.

## Planned before v4 — the pre-v4 ledger

Until v3.10 this section listed "optional future evolution". The pre-v4 audit
found that several of those items are required for a complete v4, and
re-classified every one; each below is a ledger item with a release
(`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`).

- **v3.11** — *done* (ADR-0046): estimated shrinkage, EWMA and factor-model
  covariance (OFE-002); neutralization against several continuous exposures
  (OFE-004); the deflated Sharpe ratio and multiple-testing corrections beyond
  Bonferroni (OFE-005); a t-statistic on an information coefficient (OFE-006);
  pipeline-driven `on_fill` / `on_order` / `on_timer` (OFE-014); a venue
  sequence number and persisted child bindings and request ledger (OFE-021,
  OFE-022).
- **v3.12** — *done* (ADR-0047): exchange calendars inside simulation
  (EXE-010); classification dimensions beyond sector and limits on their
  buckets (OFE-001); per-strategy capital ceilings on the execution path
  (OFE-003); execution-path delivery of external information (OFE-009);
  streaming observation sets, split-adjusted and converted fundamentals
  (OFE-011); a durable evidence store (OFE-016); health over a window
  (OFE-017); book-to-mirror reconciliation across brokers (OFE-023); the
  optimizer's super-linear pending trials, gone with the optimizer (OFE-013).
- **v3.13** — *done* (ADR-0048): a lock-file reader (OFE-019); a rerun
  harness (OFE-020); an optimal split, estimated urgency and randomized iceberg
  tranches (OFE-024, OFE-025) — and, from the ledger's v4.0.0 column, the shared
  names, the public API manifest, the persisted-name contract and release
  certification (API-001, API-002, PER-004, FEA-006).

Nothing is assigned to a later release: every item is delivered or kept as a
boundary or a stated limitation, with its reason.

## Deliberate boundaries — kept at v4

- A depth hook on `StrategyProtocol`. `BookUpdated` and `SnapshotCreated` reach
  no hook; delivering a snapshot to `on_quote` would hand existing strategies a
  payload with no `quote`, and a new hook is a strategy-API decision needing its
  own evidence.
- Allocation visibility inside `StrategyContext`. Reservations and contributions
  are post-intent facts; showing a strategy the capital its own intent will
  reserve invites it to pre-size, duplicating the allocation engine's authority.
- Per-environment promotion policy. A strategy version has one stage across all
  environments.
- A single integrated runtime spanning *all* engines. Reporting, the feature
  store, the factor library and the rest remain standalone by design (ADR-0009).
- Statistical regime models (hidden Markov, Markov switching). v3.7's regimes
  are declared rules; an estimated model needs an identity for its fit (OFE-010).
- Per-strategy sub-ledgers inside the accounting engine. A multi-strategy book
  is assembled from each strategy's own state; the contribution ledger answers
  attribution without a second book of record (OFE-015).
- A half-life fitted to a decay profile, and derived alignment for a
  comparison: each would report a fit or a pairing as a measurement (OFE-007,
  OFE-018).
- Process supervision, hot reload, hook timeouts and a threading model: the
  host application's (OFE-026).
- **Persisted state is no longer frozen at its writer's schema**: the roadmap's
  "no migration framework" boundary was replaced in v3.10 by explicit, tested
  schema upgrades (ADR-0045).

The full list, with the reasoning behind each, is in
[`nowandfuture.md`](nowandfuture.md) and `ROADMAP.md`.

---

# Contributing

Contributions are welcome.

Please read:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`

before submitting issues or pull requests.

---

# License

Released under the MIT License.

See `LICENSE` for details.

---

<div align="center">

**AlphaLab v3.11.0**

Building deterministic infrastructure for quantitative research.

</div>
