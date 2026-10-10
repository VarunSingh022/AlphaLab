# AlphaLab — Now and Future

**A long-term project reference, written at v3.0.0 and updated at v3.13.0.**

This document exists so that a future engineer — including a future version of
the person who wrote AlphaLab — can answer these questions without reconstructing
the whole history:

> What is this? Where does a new thing belong? Who owns this responsibility?
> How does it work? What can I safely change? What must I not change? What was
> deliberately deferred, and why? What is somebody else's to supply? How do I
> verify the project still works?

Everything here is traceable to source, to a test, to an ADR, or to behaviour
verified against the repository. Where something is genuinely unknown it says
**UNKNOWN**; where it is somebody else's to supply it says **EXTERNAL**; where it
is deliberately not part of AlphaLab it says **NON-GOAL**.

`README.md` is the introduction. `docs/ARCHITECTURE.md` is the architecture.
`CHANGELOG.md` is the history. This is the reference.

---

# 1. What AlphaLab is

A Python **library** for building deterministic quantitative research and
algorithmic trading components.

It is not an application. There is no server, no daemon, no scheduler process, no
CLI and no composition root — `tests/regression/test_shared_names_stay_distinct.py`
asserts that the package declares no entry point. The caller owns the process and
the event loop; AlphaLab supplies pure functions over immutable values.

It has **zero runtime dependencies**. `pyproject.toml` declares
`dependencies = []`, and everything — the HTTP transport, the RFC 6455 WebSocket
client, HMAC request signing, TLS policy, JSON serialization, the persistent
containers — is built on the standard library. This is load-bearing rather than
incidental: it is why `FileRunStateStore` can promise atomic writes without a
database, and why a security review of AlphaLab is a review of AlphaLab.

## Current identity

| | |
| --- | --- |
| Version | **3.13.0** |
| Python | 3.12+ |
| License | MIT |
| Author | Varun Kumar Singh |
| Repository | https://github.com/VarunSingh022/AlphaLab |
| Status | **Architecture frozen at v3.0.0; v3.1.0 through v3.9.0 are additive to it; v3.10.0 through v3.13.0 are the four pre-v4 releases, and v3.13.0 the last: nothing required is assigned to a later release (ADR-0048).** |

---

# 2. What v3.13.0 completes, what v3.12.0 hardens, what v3.11.0 adds, what v3.10.0 corrects, what v3.9.0 – v3.1.0 add, and what v3.0.0 means

## v3.13.0 — the final pre-v4 release

The last of the four pre-v4 releases, and the one that leaves nothing required
for later. It closes every item the ledger assigned to it and the four it had
assigned to v4.0 itself, re-reads every boundary and limitation against the
code, and adds what its own audit found. An American option is priced on a
Cox–Ross–Rubinstein lattice whose step count is part of the model, with
discrete dividends, and a surface is read between expiries in total variance
when asked to by name. An order can be split across venues at the least total
cost where each venue's cost is a fixed charge plus a convex function; an
Almgren–Chriss urgency is estimated, the shortfall its model expects is
computed with its variance, and a measured shortfall read beside it; iceberg
tranches vary from a seed and reproduce. A run is re-executed from its manifest,
refusing other inputs first, and a divergence located; a lock file is read into
the dependencies a fingerprint records. Cron timers read a stated zone's wall
clock; the liquidation price is solved for a stated maintenance basis;
exchange rates enter a factor model as factors and every factor's share of a
book's volatility is measured; a box uncertainty set takes a book that may
short; a factor model's covariance can be stated by its structure and
constructed over at 10,000 assets without writing n² values out (PRF-013);
checkpoint segments carry only the orders that changed. And the freeze: 52
shared public names at 3.12.0 are 31, each kept for a recorded reason; the
public API and every persisted enum name are data that tests hold; every
limitation an ADR states is held to a closed ledger entry; and a certificate
records what the build was checked to do, held to the engine source it names.
ADR-0048.

## v3.12.0 — the pre-v4 hardening release

The third of four pre-v4 releases. Numerical methods are right at the edges of
their range: R² of a constant series is undefined, the normal CDF is computed
from `erfc` so the lower tail keeps its precision, least squares runs by
Householder QR and refuses an ill-conditioned or rank-deficient design, and
theta uses the year the price uses. Durable state is durable — a rename is
flushed with its directory — and restores what was captured: an allocation
budget had lost its currency on every restore since v2.17, and every payload
v3.11.0 wrote is now frozen as a fixture the suite reads. Simulation reads its
venues' calendars, so a DAY order expires at its trading day's last close. A
strategy's capital can be capped; any classification dimension can bound a
bucket, counting working orders, reduce-only, with the bucket's gross kept by
the book; an observation reaches the strategies subscribed to it at the
instant it became knowable. A run can bound the history it keeps and
checkpoint incrementally. Evidence has a durable home, health a window, one
book a reconciliation against every account it is spread across. Trade prints
are read from declared columns; LSTMs and attention train; the v1 research
engine reports measurements under a stated policy. A stress program runs
10,000 assets, 1,000 strategies and 100 venues; it found a classification
limit summing its bucket for every order and every event asking every strategy
whether it subscribed, and both are fixed. `plugins` (whose `execute()` was a
placeholder), `optimizer` (a second parameter search beside the research
authority's) and the reporting dashboards leave the library. ADR-0047.

## v3.11.0 — the pre-v4 capability release

The second of four pre-v4 releases. It gives the canonical path what a strategy
needs before its API is frozen, and moves out of the library what belongs to
the application that uses it. An instrument declares its economics — multiplier,
settlement (fully paid, futures variation margin, option premium, perpetual),
lot, minimum notional, whether its price may be negative — and nothing assumes
a multiplier. An order carries its terms (limit, stop, stop-limit; IOC, FOK,
GTD, DAY, on-open, on-close) and rests across events. A strategy may state a
target position, measured against its own share of every fill and rounded
toward zero onto the lot; a sale commits no allocation budget. Subscriptions are
enforced, a slice delivers a complete instant, and the path tells a strategy of
its fills and orders. Research enters forward returns after a declared lag,
keeps delisted names, optimizes walk-forward without handing a selection its
test, and corrects for multiple testing. Construction shrinks covariance, pays
for trading exactly and rounds to lots. `studio`, `workbench`, `enterprise` and
the venue credentials are the host's. Every public entry point of a run
computes in the accounting context, and a run's cost is linear in its length
again: a slice of an append-only log had copied the whole log since v2.1, and
the pipeline takes two per fill (PRF-007). ADR-0046.

## v3.10.0 — the pre-v4 correctness release

The first of four releases the pre-v4 audit plans before the v4.0 freeze. It
adds almost no capability: it corrects the canonical path where the audit found
it computing wrong numbers, removes code that contradicts the library's
boundary, and makes persisted state upgradeable. Two packages removed (`feed`,
`live`), none added, no package edge added; snapshot schemas portfolio 4,
pipeline 4, run 2, with every v3.9 payload upgraded on read. ADR-0045; the plan
of record is `docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`.

### What it fixed

| Where | What v3.9 did | What v3.10 does |
| --- | --- | --- |
| Risk | charged buying power and exposure for every order whatever its side; refused liquidation in a drawdown; never maintained the daily loss; ignored working orders | judges every check on one projection of the post-trade book, working orders included; never refuses a reduction; maintains the daily loss in a declared zone |
| Allocation | refused every sale under long-only; defaulted a missing volatility to 1% | judges long-only against committed positions; refuses what it cannot size |
| Money | rounded every currency to a cent and every price to 1e-4 before computing money | rounds once, at each currency's minor unit (ISO 4217 or declared); keeps prices and quantities exact; one pinned context |
| Analytics | took returns per snapshot and annualized with 252 | takes returns per instant; annualization declared or observed, and recorded; `None` for undefined |
| Execution | filled an order at the event that decided it, silently optimistic; absorbed a failing strategy | offers `NEXT_EVENT` fills; records `ExecutionAssumptions`; reports strategy failures and can halt |
| Performance | re-marked and re-valued every position on every event | re-marks what was priced; reads exact per-currency totals — linear in the universe |
| Time | gave a bar's timestamp no meaning | stamps a bar at the end of its interval and requires the source's convention |
| Persistence | read exactly one schema version per subsystem | upgrades older payloads through explicit steps that refuse rather than invent |
| Boundary | shipped vendor clients (four stubs), `feed`, `live`, exchange symbol quirks and `"USD"` defaults | ships none of them |

### Found and corrected on the way

ALC-005 (a zero-quantity sale after integer rounding), NUM-010 (quotients
written as `1E+2`), NUM-011 (correlation wrong in the fourth digit near
`1e-160`), BND-004 (exchange symbol quirks), a hole in the defaults sweep
(`0.0 == False`), four v3.10 fixes that had no tests yet, and two examples
broken by v3.10's own refusals. Two regressions of v3.10 itself, caught by
comparing every example's output with v3.9's: DET-005 (two multi-strategy
books that compare equal had two identities once amounts stopped being rounded
a second time — identities render amounts by value now) and API-006 (a typed
severity in a string mapping). RES-001 — the v1 research engine's daily
assumption and uncalibrated scores — is recorded for v3.12, and DET-006 —
the other content identities, which render a declared `Decimal` by its text —
for v3.11, as is PRF-006: the per-operation constant rose (a one-asset
backtest takes about 1.4× as long as in v3.9, the OMS benchmark 2.2×), the
price of exact book totals and a compacting map, measured in the release
gates.

## v3.9.0 — the universal execution contract

The ninth capability release on the frozen architecture: the path from a
decision to a venue and back as one contract, whichever adapter an application
brings. No package and no package edge added, no snapshot schema touched, no
durable state added. ADR-0044.

### What it fixed

| Gap | What existed | What v3.9 adds |
| --- | --- | --- |
| What a venue can do | v3.5's broker-wide, two-valued `BrokerCapabilities`, with no account | `CapabilityDeclaration` at venue, market and account level, three-valued; `check_compatibility` per order; the v3.5 record projected from it |
| What a venue report means | a defined meaning for a fill only; two slightly different sets of lifecycle rules in the OMS and the mirror | `core.lifecycle.ORDER_TRANSITIONS`, read by both; `broker.apply_venue_event` gives every normalized `VenueEvent` one outcome |
| Retried requests | cancels and amendments with no identity | `CancelRequest` / `ModifyRequest` identified by content and sequence, issued against a `RequestLedger` |
| Working an order | an order went out whole | TWAP, VWAP, participation, slicing, iceberg-like; children sent for their parent and settled on it |
| Choosing a venue | nothing | `select_route` from supplied quotes, declarations, cost models and latencies, every venue explained |
| Measuring execution | v3.3's assumed costs only | implementation shortfall, slippage, fill quality, latency, rejection rate, venue quality, multi-currency reports |
| Mirror against venue | loose remote records with no instant | `reconcile_snapshot`: freshness first, then every order, fill, position and balance |

### No edge, measured

Every import the new modules make follows an edge that already existed. The
capability model and the transition table live in `core` because the OMS, the
broker boundary, `execution` and `lifecycle` all read them; `execution` reaches
currency conversion through `common.currency`, the protocols v3.8 put in
`allocation.capital`, moved down unchanged and re-exported as the same objects.
`test_v39_invariants.py` asserts the package set and every edge.

### One table, both sides

The OMS's methods and the venue boundary both ask `next_order_status`; a
reported status is judged by `classify_order_event` — transition, duplicate,
stale or conflict — and terminal statuses are absorbing. A fill during a pending
cancel lands and the cancel stays pending; a refused cancel returns the order to
the working status its fills imply. The quantities decide, not a fill's name: an
`ORDER_FILLED` delivered ahead of an earlier partial fill is applied by quantity
and the disagreement recorded, so out-of-order fills converge.

### Children stay the parent's

An algorithm's `ChildOrder` carries the parent's strategy contributions
unchanged. `runtime.route_child_order` sends it as a venue order *for* the
parent OMS order, which keeps the reservation; its fills settle on the parent
through `apply_broker_execution`. The many-to-one relation is a caller-held
`ChildOrderBindings`, rebuildable from the mirror; `ExternalOrderMap` stays
one-to-one.

### Found and corrected

The OMS and the mirror disagreed about a fill during a pending cancel;
`validate_cancel_request` left `EXPIRED` out; `replace_order` validated an
amendment as a cancel; `reconcile` collapsed duplicated remote records and
subtracted cash across currencies; the OMS order's fill arithmetic accepted a
`FILLED` order with quantity working and a negative remainder; and the strategy
split computed in the caller's decimal context. Two quadratic paths in the draft
— a declaration's identity rendered per check, and `list.count` duplicate
detection — were found by the complexity guards, each of which then failed
against the defect it guards.

## v3.8.0 — advanced portfolio and risk

The eighth capability release on the frozen architecture: portfolio
construction, risk budgets, multi-strategy books, cross-strategy risk and capital
allocation — what a desk running several strategies asks after it can research
one. No package added, three package edges added and measured, no snapshot
schema touched, no durable state added. ADR-0043.

### What it fixed

| Gap | What existed | What v3.8 adds |
| --- | --- | --- |
| A risk model | a covariance recomputed inside v3.3's functions, as a mapping with no currency, period or identity | `CovarianceMatrix`, `FactorLoadings`, `Classification` in `analytics.risk_model`, each with a derived identity; definiteness measured; regularization a recorded derivation |
| Construction | four closed forms and a clip-and-spread projection that ignored a sector cap | `construct`: minimum variance, mean-variance, maximum diversification, risk parity, robust mean-variance over stated constraints, one certified solver; `black_litterman` |
| Risk budgets | a decomposition by asset | Euler contributions of exposure lines by asset, strategy, sector, country and currency, each summing to the volatility; limits judged and reported |
| Several strategies in one portfolio | one `PortfolioState` per run | `MultiStrategyBook` of sleeves, holdings with every strategy's contribution, valued across currencies at recorded rates |
| Comparing strategies | nothing | return correlation with its basis, overlap of holdings, factor crowding within the portfolio, common exposures, capital concentration |
| Capital between runs | one run's `CapitalBudget` | capital plans across strategies, markets, brokers, accounts and currencies, allocated in each account's currency and reconciled exactly |

### Three edges, each measured

`portfolio_optimizer` imports the risk model in `analytics` (it still has no
importer, so it stays a standalone engine); `portfolio` imports `core` for the
canonical `StrategyContribution` a holding carries; `api` imports `instrument` to
read registry classifications. `analytics` still imports only `common` and
`core`; `allocation` reaches FX through `CurrencyConverter`, a structural
protocol `FxRates` satisfies, and never imports `portfolio`; `lifecycle` reaches
constructions and plans through protocols. `test_v38_invariants.py` asserts every
edge set and that the graph has no cycle.

### One arithmetic, and not one moved number

The sample covariance, the Euler decomposition, the factor exposure and the
Herfindahl index each have one implementation in `analytics.risk_model`. v3.3's
decomposition now calls them, written as the same expressions in the same
order, so every number it published is unchanged — pinned by a test that
recomputes the v3.3 expressions on generated books and compares floats exactly.

### A certified answer or a named conflict

`construct` returns weights only when the dual active-set solver stopped with
nothing violated and the KKT certificate verifies within the stated tolerances.
`INFEASIBLE` names the violated constraint and the active constraints that
exclude it, and carries no weights: nothing is relaxed and no fallback objective
is tried. The solver is checked against an exhaustive exact-rational enumeration
of every active set, in the unit suite and the invariant suite.

### Capital where it is

A plan allocates in each account's own currency and converts only plan-wide
figures; every account reconciles exactly; an oversubscribed account refuses the
plan unless `PRO_RATA` is stated, and then the scale is recorded; reserved
capital is read from ADR-0015's reservation ledger; and one account's allocation
becomes the `CapitalBudget` its run is given, which admits exactly the
allocation. A broker and an account are identifiers, never adapters.

### Found and corrected

Five defects in the v1 construction engine (a missing covariance or forecast
read as zero; a sector cap ignored; lower bounds forcing weights above the
target returned as if valid; the spread left out of the cost estimate; a
constant clipped amount) and three per-call rescans in the new code, found
while writing its complexity guards — each guard was then run against the
defect it guards, and failed. Section 17 of this document said
a future-dated FX rate was not refused; section 8 and `FxRates.convert` have
refused it since v3.4, and the stale sentence is gone.

## v3.7.0 — advanced quant research

The seventh capability release on the frozen architecture: event-driven
research, alternative data with provenance, point-in-time fundamentals, regime
detection and adaptive strategies, all resting on one statement of **when a
piece of information became knowable**. No package added, no boundary moved, no
snapshot schema touched, no durable state added. ADR-0042.

### What it fixed

| Gap | What existed | What v3.7 adds |
| --- | --- | --- |
| When information became knowable | `known_as_of`, which trusts a release date every record is assumed to have | `PointInTimeStamp` — observed, available, effective, ingested — with a basis (`DECLARED`, `DERIVED` by a named rule, `UNKNOWN`) and two visibility rules; `PointInTimeIndex` |
| Events | a shape per kind, none with an availability instant | one `InformationEvent`, an open dotted vocabulary |
| Alternative data | typed v1 categories and quality metadata, no identity | `ExternalObservation`, `ObservationSource`, versioned `ObservationSet`s with checked vintages and lineage |
| Fundamentals | a `FundamentalSnapshot` a caller assembled | `FundamentalObservation` keeping fiscal period, publication, availability and restatement apart; TTM, valuation, ratios, growth, restatement bias — all at an instant |
| Research on it | features over prices only | knowledge frames with a checked price join, event studies, regime detection |
| Strategies that learn | a mutable attribute no snapshot could see | the adaptive engine: immutable state with lineage, one pure update function, checkpoints, reprocessing, and the state in the run record |

### `alt_data` left the standalone list, and its edges are measured

v3.7 made `alphalab.alt_data` the point-in-time foundation for external
information. `research`, `factor_library` and `api` import it — so, exactly as
`factor_library` did in v3.2, it is now reached by the lifecycle path through
`research` — and it imports `alphalab.common` and nothing else in AlphaLab.
`test_v37_invariants.py` asserts that edge set, because a sentence in a document
is how v3.2's `factor_library` claim went stale (section 14, *the failure mode
to watch for*).

### Unknown is not "available at observation"

A record whose availability was never established is ingested, identified,
counted as `unverified` in every selection — and never read. Assuming it was
available when it was observed is precisely how period-end-stamped
fundamentals look ahead.

### Two joins that were not loosened

v3.2's diagnostics refuse a factor and returns from different datasets. A
knowledge frame's identity records the price clock it was sampled on, and
`align_prices` joins the two by a checked, jointly derived identity; the guard is
unchanged. The data/research join rule held too: ingestion lives in `alphalab.api`,
and `alt_data` reaches a calendar through a structural protocol.

### The learned state is in the evidence

An adaptive strategy hands its state to the run snapshot, so `digest_run` — and
every v3.6 manifest built on it — commits to every update. Its fingerprint names
its learning configuration and starting state through the existing research
settings, so the fingerprint key is unchanged and every v3.6 fingerprint still
verifies.

## v3.6.0 — strategy evaluation and research-marketplace infrastructure

The sixth capability release on the frozen architecture. One package deepened
(`alphalab.lifecycle`), none added, nothing changed, no snapshot schema touched,
no durable state added. ADR-0041.

### What it fixed

| Gap | What existed | What v3.6 adds |
| --- | --- | --- |
| Which strategy this is | `name@N`, registration order; a factory's *name* | `StrategyFingerprint` over code, dependencies, parameters, research configuration and engine |
| What a result was made from | a run's dataset, seed and configuration, and nothing about code or engine | `ReproducibilityManifest`, every identity read from its owner |
| What is verifiably true of a strategy | a promotion gate on metric thresholds | eight certification properties, each with its evidence, and no score |
| Whether it can move | nothing | `evaluate_portability` against declared environment capabilities |

### Who provides and who consumes

AlphaLab **provides** the contracts — a fingerprint, a manifest and its
assessment, a certification report, a portability report — each a frozen value
with a derived identity and a deterministic JSON form. An external application
**consumes** them; a research marketplace such as RedDesk is the motivating one.
Listing, publishing, payment, ranking, licensing and tenancy are the
application's. **NON-GOAL**, pinned by `test_v36_invariants.py`, which sweeps the
four modules for marketplace operations and for any consumer's name.

### Five content identities

```text
specification_id_for        what a deployment needs          (v3.5)
evidence_id_for             one measurement                  (v2.4, frozen)
derive_study_id             one experiment                   (v3.2)
derive_strategy_fingerprint one strategy version             (v3.6)
derive_manifest_id          one result's inputs              (v3.6)
```

None can stand in for another; section 26 of `test_shared_names_stay_distinct.py`
pins it. The engine version is *recorded* on a dataset's provenance and kept out
of its identity (content does not change with the engine), and is *inside* a
strategy fingerprint (a result from another engine is a result of something
else). Both are deliberate.

### Evidence, never a verdict

Certification takes runs, repeated runs, datasets, manifests, runtime
observations and resource measurements, and derives every judgement itself
through the authority that owns it. A supplied measurement claiming to be
`COUNTED` is refused. Leverage and drawdown are the pre-trade gate's own
readings. `NOT_ASSESSED` and `INSUFFICIENT_EVIDENCE` are never `PASS`.

### Where v3.6 met pre-existing defects

Building real evidence surfaced three properties of the pre-trade gate — a
position check that adds a notional to a quantity, a daily-loss limit that is
never maintained, a net-exposure limit no check reads — and one ingestion
caveat: `ingest_rows` identifies whatever source its caller records. None is
changed; each is stated where v3.6 meets it. See section 20.

### The data/research join rule held

`alphalab.api` is still the only module importing both `alphalab.data` and
`alphalab.research`. The first v3.6 draft broke that twice and the guard caught
it; the fix is two one-member structural protocols, `StudyIdentity` and
`VersionedDataset`.

## v3.5.0 — strategy execution and production intelligence

The fifth capability release on the frozen architecture. One package deepened
(`alphalab.lifecycle`), none added, nothing changed, and no snapshot schema
touched. ADR-0040.

### What it fixed

Everything after "this environment should be running that strategy version" was
outside the library. Five specific gaps:

| Gap | What existed | What v3.5 adds |
| --- | --- | --- |
| Where a strategy is | `ModelStage` calls research, backtest and validation all `NONE`, paper and live both `PRODUCTION`, and has no member for paused | `StrategyLifecycleStage`, a third axis with a declared transition table |
| What a deployment needs | a `ReleasePackage`'s flat mapping of strings | `DeploymentSpecification`, typed and self-identifying |
| Whether it is healthy | `live_health`, a tuple of sentences, no thresholds, only for a run this process drives | `evaluate_health` over supplied observations and declared budgets |
| Whether it is doing what it should | nothing | `compare_runs` / `compare_expected_paper_live` |
| Whether the book matches the venue | `broker.reconcile`, which compares the *mirror* to the venue | `reconcile_execution_state`, which compares the *book* to the mirror |

### The three lifecycle state machines

Kept apart on purpose, and pinned as a triple in
`test_shared_names_stay_distinct.py`:

```text
ModelStage              promotability of a registered artifact   (unchanged)
StrategyLifecycleStage  maturity of a strategy                   (v3.5)
strategy.state.
  LifecycleState        an instance inside one session           (unchanged)
```

`PROGRESSION_MODEL_STAGES` states which `ModelStage` values each progression
stage is consistent with, totally and in the open.
`progression_conflicts` **reports** a disagreement rather than resolving it —
the position `run_plan` already takes when a version's stage and the deployment
ledger disagree. A progression names **no environment**, so the "no
per-environment promotion policy" boundary is unchanged.

### Where the v3.5 values live, and why it is nowhere

Not on `LifecycleState`. That state has one snapshot owner and one module-local
schema literal, AlphaLab has no migration framework, and a new field there would
make every payload written before this release unreadable — to serve a value the
caller can simply hold. It is the same decision ADR-0030 decision 2 records for
`RunState`, and the same shape `RunAuthorization` already has.

So v3.5 adds no `capture`, no `restore` and no `SNAPSHOT_SCHEMA`, and
`test_v35_invariants.py` asserts the absence.

### Missing data, three times over

The rule the release is built around, stated once per capability:

* **Health.** Every field of a `RuntimeObservation` except `observed_at` is
  optional; `None` means *not observed* and an empty tuple means *observed and
  empty*. A category that could not be judged is listed in
  `HealthReport.unevaluated`, and `HealthStatus.UNKNOWN` exists so a
  clean-but-incomplete report is never `HEALTHY`.
* **Comparison.** `MISSING_EXPECTED` and `MISSING_OBSERVED` are outcomes, not
  zeros. A metric with no tolerance is `NOT_COMPARABLE`, not a match. A venue's
  unmeasured slippage stays `None`.
* **Reconciliation.** `reconciled` says the two sides agree about what was
  compared; `fully_reconciled` also requires that nothing was skipped. A
  currency the broker account cannot speak about is an `UnreconciledArea`.

### Units

Every v3.5 duration names its unit in the field. The comparison layer carries
latency as `Decimal` seconds because every other quantity it compares is an
exact `Decimal`; `lifecycle.health` keeps its budgets as `float` seconds because
it subtracts float timestamps and compares them directly, with no tolerance
arithmetic. A sweep in `test_v35_invariants.py` enforces the naming.

### What it did not add

No broker client, credential, adapter or vendor name. No remediation: health and
reconciliation detect and report, and neither declares a side authoritative. No
supervised live process. No durable state. No package.

## v3.4.0 — global markets and multi-asset research

The fourth capability release on the frozen architecture. One leaf package
added, five deepened, six defaults made required. ADR-0039.

### What it fixed

Six defaults were one market's convention presented as a universal:

| Where | Was | Right in |
| --- | --- | --- |
| `FutureContract.currency` | `"USD"` | the US |
| `OptionContract.multiplier` | `100` | US single-stock options |
| `OptionContract.style` | `AMERICAN` | US single-stock options |
| `data.assets.OptionSpec.style` | `AMERICAN` | the same |
| `FundingRate.interval_hours` | `8` | most perpetual venues, not all |
| `CryptoInstrument.contract_size` | `Decimal("1")` | spot only |

Each produced a number rather than an error when wrong.
`test_no_silent_financial_defaults.py` has swept for exactly that shape since
v2.17 and found none of them, because it sweeps **function parameters** and
every one of these is a **dataclass field**. `test_v34_invariants.py` now sweeps
both, with each surviving default listed beside the reason a wrong value there
cannot produce a number — and the exemptions verified by exercising the refusal
rather than asserted.

### `alphalab.conventions` — one authority, and why it is a leaf

`MarketConvention` carries venue, calendar id, quote **and** settlement
currency, multiplier, tick schedule, lot specification and settlement rule, with
**every field required**.

It imports `alphalab.common` and nothing else in `alphalab`. That is
load-bearing rather than tidy: the graph already runs
`data → options → portfolio`, so a convention authority reaching into any of
those could not also be used *by* them without closing a package-level cycle.

Settlement counts trading days over a **structural protocol**
(`TradingDayCalendar`, one method) that `MarketCalendar` already satisfies, so
the calendar authority does not move and the calendar is passed in. The
precedent is `common.point_in_time.PointInTimeRecord`.

### The multiplier

`contract_notional` is the **one site in AlphaLab** that multiplies a contract
count by a multiplier, and `test_v34_invariants.py` reads every module's source
to keep a second from appearing. `ContractNotional` reports money and underlying
units as separate fields.

`Position` is unchanged and still carries no multiplier. `portfolio.contracts`
pairs one with its convention and is a *different measurement* from
`ExposureEngine`, not a better one — the 1,000x gap between them is
demonstrated in `test_shared_names_stay_distinct.py`.

### Futures, options, FX, crypto, rates

| Capability | Shape |
| --- | --- |
| Continuous futures | Reproducible from chain + policy + observations + adjustment method, and nothing else |
| Roll rules | Three triggers, no default; each refuses the input it needs rather than approximating it |
| Roll prices | The prints at the roll instant on both contracts; a missing one raises |
| Implied volatility | Inverts the same expression the pricer rounds; refuses in five cases |
| Surfaces | `surface_from_chain` returns the surface **and every refusal with its reason** |
| Greeks | Carry `ModelAssumptions`: the four things the model does not do |
| Expiry | Exercised / assigned / abandoned / worthless, with cash and units as two signed quantities |
| Cross rates | Derived only on request, only through a **named** currency, marked `derived` |
| FX time | A rate dated *after* the conversion instant is now refused, not just a stale one |
| Currency attribution | A return decomposition with no residual; imports nothing from `analytics` |
| Crypto venues | Funding interval, fees, price source, minimum notional — declared, nothing defaulted |
| 24/7 coverage | Measured against a theoretical clock; gaps are counts of absences, never rows |
| Fixed income | A **foundation**: exact arithmetic only, no credit, no optionality, no bootstrapper |

### What it did not add

**No durable state.** Every type is a frozen value or a pure function. No new
snapshot owner, no new schema constant, no migration, and every v3.3 payload
round-trips unchanged. A test asserts the absence.

---

## v3.3.0 — institutional backtesting and portfolio intelligence

v3.3.0 is a **capability** release confined to two existing packages, one new
package, and one function added to `alphalab.common.statistics`. It answers the
questions an institution asks before allocating to a strategy. No boundary
moves, no ownership changes, and every v3.1 and v3.2 invariant holds. ADR-0038.

Six properties, and each is an invariant now (section 14, items 23-28):

1. **Execution costs are itemized and separated by how they settle.** Six named
   roles — spread, slippage, impact, commission, fee, tax — and two settlements.
   `PRICE_EMBEDDED` costs move the fill price; `CASH_CHARGED` costs are debited.
   A cost is never both, which is the double-count the separation exists to
   prevent, and the two totals a report carries are exactly the six items.
2. **The itemization is derived, never stored.** `ExecutionReport` is persisted
   under ADR-0023 and was not widened. `simulate_costs` is a pure function of
   the configuration, so any fill's breakdown recomputes exactly and for ever —
   and there is no second copy to disagree with the first.
3. **A capacity figure names what binds, and never travels without its
   assumptions.** Portfolio capacity is the minimum over names, not a mean;
   `binding_asset_id` says which one. There is no default participation limit,
   turnover or impact budget, and `impact_budget=None` reports the constraint as
   *unevaluated* rather than evaluating it against an invented number.
4. **Missing attribution metadata is reported, never fabricated.** Each of the
   nine dimensions carries an `Availability`. A dimension nothing was supplied
   for comes back empty and labelled `NO_METADATA` — never one `UNKNOWN` bucket
   holding the whole P&L. Country and broker have no source in AlphaLab and are
   caller-supplied or absent; `venue` is the execution venue and is not read as
   a broker.
5. **A VaR figure states its method.** `VaRPolicy` carries method and confidence
   together, and three legitimate methods are offered because they disagree most
   exactly where it matters. Risk contributions sum to portfolio volatility
   exactly; that is what makes the decomposition one. Degenerate samples refuse,
   because a risk report that turned an undefined measurement into `0.0` would
   report a riskless portfolio.
6. **Historical scenarios are contracts, not numbers.** `CRISIS_2008`,
   `COVID_CRASH_2020`, `RATES_REPRICING_2022` and `COMMODITY_SHOCK_2022` name
   their window and the observations they need and refuse until a caller
   supplies them. AlphaLab ships no market data and **invents no historical
   move** — the rule `NO_RATES` already applies to FX, applied to history.

Two structural consequences. `alphalab.scenario` is a new package that imports
`alphalab.common` and nothing else in AlphaLab, which is what makes one scenario
contract usable by every portfolio class rather than by one. And the execution
pipeline now forwards the market event's quote and shown size to the cost model;
without that seam the liquidity-aware roles would have been reachable from a
library call and not from the canonical execution path.

One fix worth naming: `DeterministicLatency` drew its latency from
`hash(order_id)`, which PEP 456 salts per interpreter. It reproduced within a
run and differed on the next, so fill timestamps did not reproduce across
processes while the class name said they did. It now uses a stable digest, and
the assertion spawns a **separate interpreter** because within one process a
salted hash is perfectly stable and the failure is invisible.

## v3.2.0 — strategy research and validation

v3.2.0 is a **capability** release confined to two packages and one new module.
`alphalab.factor_library` becomes the computation engine the architecture always
designated it to be, and `alphalab.research` gains the validation methodology.
`alphalab.common.statistics` is added. No boundary moves, no ownership changes,
and nothing outside those three is redesigned. ADR-0037.

v3.1 gave AlphaLab a dataset it could trust. v3.2 gives it the path from one to
a research result nobody has to take on trust:

```
Dataset -> ObservationFrame -> FeaturePanel -> forward returns
        -> diagnostics -> folds -> robustness -> overfitting
        -> StudyResult -> ValidationEvidence
```

Six properties, and each is an invariant now (section 14, items 17-22):

1. **A feature is a specification before it is a number.** `FeatureDefinition`
   states the field, the window in *periods*, the parameters and the
   missing-data policy, and defaults none of them. A parameter the kind does
   not read is refused rather than ignored, because an unread parameter would
   still change the derived identity and give one computation two names.
2. **Missing values are never invented.** `MissingPolicy` has `REFUSE` and
   `SKIP` and no `FILL` — v3.1's rule one layer up.
3. **Nothing looks ahead**, asserted for every kind by truncation rather than
   by inspection.
4. **Purging is defined by information windows.** `label_ends_from_horizon`
   reads the actual series; a `PurgePolicy` has no default horizon; a scheme
   whose name is a claim refuses to be built without the thing that makes the
   claim true.
5. **An unmeasurable statistic is `None`, never zero**, and every diagnostic
   carries the sample it rests on.
6. **There is no score.** Measurements, the caller's stated thresholds, and
   findings naming which bound each measurement crossed are separate fields.

`ResearchStudy` derives an identity from its description; `StudyResult` derives
one from the numbers, which makes it tamper-evident; `run_study` compares the
dataset it is handed against the one the study names. That closes, at the study
level, the substitution ADR-0017 closed for evidence. `evidence_from_study`
records a result as `ValidationMethod.STUDY`, and **`evidence_id_for` did not
move** — a new enum member changes no existing digest, so every promotion
recorded since v2.6 still verifies.

One structural consequence: `factor_library` gained importers and is therefore
no longer a standalone engine. `feature_store` did not, which is the
compute/registry seam working as designed.

## v3.1.0 — the first release on the frozen architecture

v3.1.0 is a **capability** release confined to one package. `alphalab.data`
gains the ingestion, validation, cleaning, provenance and identity machinery it
was named for and did not have. No boundary moves, no ownership changes, no
schema constant moves, and nothing outside `alphalab.data` is redesigned. It is
what "frozen" is meant to permit. ADR-0036.

Four properties, and each is an invariant now (section 14):

1. **Nothing is altered silently.** Every rejected row is returned with its
   reason and source line; every applied change is a `TransformationRecord`
   with its count and reason; both ride into the dataset's provenance.
2. **The cleaning policy is the caller's, and has no default.** ADR-0033
   decision 10's rule, applied to data: a default either way is an invented
   policy presented as an architectural one. There is **no way to fill a
   missing price** — the absence is structural, not a member that raises.
3. **Ambiguity is refused rather than resolved.** `close` and `adj close`
   together, a delimiter two candidates fit, a naive timestamp with no zone, a
   numeric column that reads as a valid instant in both seconds and
   milliseconds.
4. **A dataset version is derived and immutable.** The identity hashes the
   content, schema, zone, calendar, frequency, basis, policy and every
   transformation. Cleaning *derives* a new version; both stay in the
   catalogue, and `UniversalDataState.lineage` records which came from which.

The fourth closes a hole under ADR-0017. Evidence hashed `dataset_id` so that a
promotion could not be pointed at different data after the fact — but cleaning
used to replace `state.datasets[id]` in place, so the digest still verified
while the numbers behind it had changed. The tamper-evidence was real for
metrics and decorative for data. The derived version now reaches
`MarketDataset`, `RunState.source_id`, `BacktestResult.dataset_id` and
`ValidationEvidence` unchanged, and **`evidence_id_for` did not move** — every
promotion recorded since v2.6 still verifies.

## What v3.0.0 means

v3.0.0 adds no capability, moves no ownership boundary, changes no schema and
removes no public name. Three things make it the stable release:

1. **A whole-repository architecture audit** established that there is **no known
   internal problem that would require AlphaLab to be refactored immediately
   after declaring it stable.** Everything that remains is classified as
   deliberate design, an external dependency, or optional evolution.
2. **The work that would have made v3.0 breaking was taken in v2.17 instead** —
   seven deprecated surfaces removed with no compatibility aliases, all four of
   ADR-0032's category C items implemented, zero skipped tests, zero warnings.
3. **A documentation truth freeze** aligned every current-facing document with the
   code.

**"Frozen" does not mean finished.** It means the bar for a particular kind of
change is now an ADR and a major release. Section 14 lists exactly what is
frozen; section 17 lists what can still be added freely.

---

# 3. The shape of the system

AlphaLab is **two wired paths plus standalone engines**. This is the single most
important thing to understand about it, and it is ADR-0009.

```
                    ┌─────────────────────────────────┐
                    │  THE LIFECYCLE PATH             │
   research         │  alphalab.lifecycle             │
   candidate ──────►│  candidate → run → evidence     │
                    │  → model version                │
                    │  → strategy version             │
                    │  → promotion → deployment       │
                    └───────────────┬─────────────────┘
                                    │ authorize_run  (a query with a refusal)
                                    │ strategy.registry (identity → factory)
                                    ▼
                    ┌─────────────────────────────────┐
   market record ──►│  THE EXECUTION PATH             │──► orders, fills,
                    │  RunEngine owns the run         │    positions, P&L,
                    │  ExecutionPipeline owns the step│    analytics
                    │  4 drivers own only the cursor  │
                    └─────────────────────────────────┘

   everything else: standalone engines, reached by neither path
```

## Two tiers, one owner each

| Tier | Owner | State | Owns |
| --- | --- | --- | --- |
| The **step** | `runtime.execution_pipeline.ExecutionPipeline` | `ExecutionPipelineState` (16 fields) | what happens to one market event |
| The **run** | `runtime.run.RunEngine` | `RunState` (8 fields) | how far it has read, what it skipped, what each record produced, the identifier scope a stopped run continues in |

**Drivers hold no state.** `TradingSession`, `BacktestEngine`, `ReplayBacktest`
and `LiveSession` decide only which record comes next and what clock reading
judges it. None is a dataclass; none defines `__init__`. This is what makes
backtest, replay, paper and live *one* loop rather than four, and it is why
backtest/replay parity is structural rather than a coincidence the tests observe.

Verified at v3.0: none of the four drivers holds a field.

---

# 4. Package ownership

All 43 packages, and which path reaches each, with the `api` module above them
all. (v3.10 removed `feed` and `live`; v3.11 `studio`, `workbench` and
`enterprise`; v3.12 `plugins` and `optimizer`.)

## The execution spine

| Package | Owns |
| --- | --- |
| `core` | The canonical execution domain models: `Side`, `OrderRequest`, `Fill`, `Trade`, `StrategyContribution`, `AssetType`, `OrderType`, `TimeInForce`, and the id validators. Since v3.9 also the capability model (`core.capabilities`), the normalized execution events and the one order-transition table (`core.lifecycle`), and the strategy split (`split_by_contribution`) |
| `runtime` | The execution step, the run, the four drivers, broker routing, and four snapshot modules. Since v3.9 broker routing also sends an algorithm's children for their parent (`route_child_order`, `ChildOrderBindings`) and can gate on a capability report. Since v3.12 it reads venue calendars (`runtime.calendars`, over `data`'s `MarketCalendar`), delivers point-in-time observations (`alt_data`), bounds what a run keeps (`runtime.retention`) and checkpoints incrementally (`runtime.checkpoint`) |
| `strategy` | What a strategy *is*: `StrategyProtocol`, `StrategyStateProtocol`, `StrategyContext`, the `Dispatcher`, the `RuntimeSupervisor`, and the strategy-class registry. Since v3.7 also the adaptive engine — configuration, observation, immutable learned state with lineage, `apply_update`, replay, checkpoint and restore — three rules, and `AdaptiveStrategy`. Since v3.11 also `StrategyDefinition` (`strategy.definition`, moved from the removed `studio`). Still imports only `common` |
| `allocation` | Intent sizing and netting into `OrderRequest`, the capital budget, the per-order reservation ledger and the contribution ledger. Since v3.8 also capital plans across strategies, markets, brokers, accounts and currencies (`allocation.capital`), reading reserved capital from the reservation ledger and producing each run's budget; FX reaches it through a structural protocol, and it still does not import `portfolio` |
| `risk` | Pre-trade checks and limits. Since v3.10 every check reads one projection of the book after the order, working orders included (`risk.projection`), and a limit never refuses a trade that reduces what it limits. Since v3.12 classification limits (`ClassificationLimit`), whose dimension and label are normalized by `instrument`'s rules — names only: the gate is handed each bucket's exposure and never reads the registry |
| `oms` | The order lifecycle. `oms.order.Order` is *the* lifecycle order; since v3.9 its methods read `core.lifecycle.ORDER_TRANSITIONS` rather than their own guards |
| `execution` | The deterministic execution simulator, commission models, fill policies, slippage, latency. Since v3.9 also execution algorithms (`algorithms`), route selection (`routing`) and execution quality (`quality`) — all over the canonical `OrderRequest` and `ExecutionReport`, importing only `common` and `core` beyond itself |
| `portfolio` | Cash, positions, the transaction ledger, NAV, per-currency P&L, valuation, margin, exposure, FX and the FX feed. Since v3.4 also FX research (cross rates, covered-parity forwards, carry, hedging, currency attribution) and contract-aware exposure. Since v3.8 also multi-strategy books (`portfolio.multi_strategy`) — sleeves of canonical positions, never a second book of record — which is why it now imports `core`. Since v3.10 `PositionBook` keeps exact per-currency totals and a market event re-marks only what was priced (`pending_marks`) |
| `analytics` | Performance reports and attribution. Its `CURRENCY` dimension buckets realized P&L per currency and has no total; the *return* decomposition that does is `portfolio.fx_research` and neither derives the other. Since v3.8 the risk model (`risk_model`: covariance, correlation, factor loadings, classifications, Euler contributions), risk budgets (`risk_budget`) and cross-strategy risk (`cross_strategy`). Imports only `common` and `core` |
| `market` | The canonical market-data model, the normalization boundary, market sources, streaming |
| `instrument` | Canonical instrument identity, the registry, classification and its provenance |
| `common` | Version, `BaseEvent`, deterministic serialization, the seeded identifier source, `AppendOnlyLog` / `PersistentMap` / `PersistentSet`, TLS policy, and the point-in-time core: `known_as_of`, and since v3.7 `PointInTimeStamp`, `AvailabilityBasis`, `VisibilityRule` and `PointInTimeIndex`. Since v3.9 the currency-conversion protocols (`common.currency`), moved down from `allocation.capital`. Since v3.10 the one version declaration (`common._version`), each currency's minor unit (`common.currency_units`) and the pinned accounting context (`common.arithmetic`) |
| `persistence` | The codec spine (`serialize`, typed `decode`, exceptions), `RunStateStore`, and since v3.10 versioned schema upgrades (`persistence.upgrade`) |
| `backtesting` | The dataset type and the two drivers over it. Since v3.12 a backtest merges point-in-time observations (`alt_data`) with its records |
| `replay` | The deterministic replay cursor, clock and session lifecycle |
| `broker` | **One** venue: `BrokerProtocol`, the canonical broker vocabulary, reconciliation, `PaperBroker` (the HMAC transport and `RestVenueBroker` left in v3.11 with their credentials, BRK-007). Since v3.9 also normalized venue events and their application to the mirror (`broker.lifecycle`), cancel and amend request identities (`broker.requests`) and snapshot reconciliation; it re-exports the capability model |
| `data` | The canonical **wire** record, and the Universal Data Engine: source provenance, delimited reading, schema detection, timestamps and frequency, validation findings, cleaning policy, quality reporting, asset-class semantics, market calendars, corporate-action basis, and the derived dataset version. Its only outward edges are `common` and `options` (one leaf enum), which is what keeps the package graph acyclic |
| `marketdata` | The HTTP and WebSocket transports and the wire records it re-exports from `data.feed` (`Timeframe` was removed in v3.11: the interval is `market.bar.TimeFrame`). Until v3.10 also vendor clients (four of five were stubs) and a v1 provider engine, removed: a provider is the host application's |
| `api` | **The top of the graph** (v3.1). The application-facing Python API joining the data layer to the execution path: `ingest_csv`, `select`, `to_market_dataset`, `backtest`, `replay`. Since v3.7 also point-in-time ingestion of observations, events and fundamentals with an explicit availability rule, and the lifting of single-timestamp wire records. Since v3.8 sector and currency classifications read from the instrument registry. Nothing imports it, which is what lets it depend on both `data` and `market` without closing a cycle |

## The lifecycle path

| Package | Owns |
| --- | --- |
| `lifecycle` | The composition: registration, evidence, promotion, deployment, rollback, governance, and the join to the execution path. Since v3.5 also the strategy progression, the deployment specification, runtime health, the expected/paper/live comparison and the AlphaLab-to-broker reconciliation. Since v3.6 also strategy fingerprints, reproducibility manifests, certification reports and portability reports — values, never stored. Since v3.7 an adaptive strategy's configuration and starting state in its fingerprint, study inputs as external requirements, and the adaptive replay assessment. Since v3.8 a construction's and a capital plan's identities in its fingerprint, through protocols rather than imports. Since v3.9 the capability projection onto the v3.5 record, children in the book-to-mirror reconciliation, and algorithm and routing identities in a fingerprint |
| `experiment_tracking` | Experiment runs, parameters, metric history |
| `model_registry` | Model versions, stages, promotion, `ArtifactRef`, the content-addressed artifact store |
| `deployment_manager` | Release packages and the append-only environment ledger |
| `research` | Research workflows and the v1 run evaluation — restated in v3.12 as measurements under a stated `ResearchPolicy` (`research_metrics`), which validation evidence extracts from, and the one parameter search (`parameter_sweep`). Since v3.2 the study methodology; since v3.7 event studies and regime detection, reading `alt_data` and never `data` |
| `factor_library` | The computation engine (v3.2): features, factors, cross-sectional research, signal diagnostics, validation. Since v3.7 knowledge frames over point-in-time information and point-in-time fundamental snapshots. Since v3.8 factor loadings read from its panels into the risk model's type. Reached through `research`; imports neither `research`, `lifecycle` nor `api` |
| `alt_data` | Point-in-time external information (v3.7): `ExternalObservation`, `InformationEvent`, `FundamentalObservation`, `ObservationSource`, versioned `ObservationSet`s and their views, session placement, point-in-time fundamentals; the v1 typed categories and `DataProvenance` stay. Reached through `research` and `factor_library`. Its only outward edge is `common`; a calendar reaches it through `SessionCalendar`, a structural protocol |

## Leaf libraries — imported by other packages, reached from neither path

| Package | Owns |
| --- | --- |
| `conventions` | Market conventions (v3.4): the settlement rule and its basis, the tick schedule and tick value, the lot specification, the contract multiplier and `contract_notional`, day counts and compounding. Its only outward edge is `common`, which is what lets both sides of the `data → options → portfolio` chain use it. A calendar reaches it through a one-method structural protocol, never an import |

## Standalone engines — reached by neither path

`portfolio_optimizer`, `reporting`, `feature_store`,
`ml`, `deep_learning`, `reinforcement_learning`,
`options`, `futures`, `crypto`, `macro`, `cloud_research`, `cluster_scheduler`,
`distributed`, `research_assistant`, `brokers`,
`scheduler`, `scenario`. (`scenario`, added in v3.3, imports only `common` and
was missing from this list until v3.10; `live` and `feed` were removed in v3.10,
and `workbench`, `studio` and `enterprise` in v3.11 — ADR-0046.)

Each is deterministic, individually tested and individually benchmarked. **A
package with no in-repo consumer is a standalone engine by design, not an
orphan** — pinned by
`test_every_zero_consumer_production_package_is_a_standalone_engine`.

`portfolio_optimizer` stayed on this list in v3.8 while gaining one edge — the
risk model in `analytics` — because nothing on either path imports it: a
construction answers what to own, and turning weights into orders stays the
caller's decision. `test_v38_invariants.py` asserts its edge set.

`factor_library` left this list in v3.2 and `alt_data` in v3.7: `research`,
which `lifecycle` imports, imports both. Each edge runs one way and each is
measured — `test_one_research_authority_per_concept.py` for the first,
`test_v37_invariants.py` (which asserts `alt_data` imports only `common`) for the
second.

**v3.12 adds eight package edges, each one way and none a cycle**:
`runtime → data` and `scheduler → data` (a `MarketCalendar` for venue calendars
and session timers), `runtime → alt_data` and `backtesting → alt_data`
(observations delivered on the path), `cloud_research → research` and
`research_assistant → research` (one parameter-search authority, SCF-003),
`lifecycle → execution` (multi-account reconciliation reads `ExecutionReport`),
and `risk → instrument` (a classification limit normalizes its dimension and
label by the registry's own rules; the gate still never reads the registry,
which `test_sector_classification_reaches_attribution.py` asserts).
`test_import_graph_stays_acyclic.py` asserts there is no cycle.

`conventions` (v3.4) is not on this list and is not on either path either. It is
a **leaf library imported by other packages** — `macro` and `portfolio` today,
and available to `options`, `futures`, `crypto`, `data` and `api` — rather than
one reached from a run. Its edge set is asserted: `alphalab.common` and nothing
else in `alphalab`.

## One composition claim that was wrong for thirteen releases

`alphalab.lifecycle` does **not** import `research_assistant`. `README.md`,
`docs/README.md`, `docs/ARCHITECTURE.md` and ADR-0013 said it "composes" it from
v2.4 until v3.0. What actually happens:
`research_assistant` produces a candidate, `to_strategy_definition` lifts it into
the canonical `StrategyDefinition`, and the lifecycle takes *that*. The dependency
runs through the definition, not the package. Read the import graph, not the prose.

---

# 5. Canonical domain models

One name, one meaning, one definition. Changing any of these is a major release.

| Concept | Canonical type | ADR |
| --- | --- | --- |
| Order direction | `core.enums.Side` | ADR-0008 |
| Proposed order | `core.order_request.OrderRequest` | ADR-0008 |
| Lifecycle order | `oms.order.Order` | ADR-0008 |
| Fill / trade | `core.fill.Fill`, `core.trade.Trade` — `float` Unix timestamps | ADR-0008 |
| Strategy attribution | `core.contribution.StrategyContribution` | ADR-0015 |
| Order-lifecycle transitions | `core.lifecycle.ORDER_TRANSITIONS` | ADR-0044 |
| What a venue reported | `broker.lifecycle.VenueEvent`, kind `core.lifecycle.ExecutionEventKind` | ADR-0044 |
| What a venue can do | `core.capabilities.CapabilityDeclaration` | ADR-0044 |
| Instrument identity | `asset_id` — `uuid5` over `(asset_type, exchange, symbol, currency)` under a frozen namespace | ADR-0016 |
| Top of book | `market.quote.Quote` | ADR-0011 |
| Trade print | `market.tick.Tick` | ADR-0011 |
| Bar | `market.bar.Bar` | ADR-0011 |
| Depth | `market.snapshot.OrderBookSnapshot` | ADR-0011 |
| Stream record | `market.record.MarketRecord` | ADR-0011 |
| Wire record | `data.feed.*` — `float` prices keyed by provider symbol | ADR-0011 |
| What a strategy emits | `strategy.events.Intent` | ADR-0008, `SIGNAL_MODEL.md` |
| What a strategy is | `strategy.definition.StrategyDefinition` (in `studio` until v3.11) | ADR-0035, ADR-0046 |
| Money | `Decimal`, exact at the currency minor unit | ADR-0008 |

## The wire/domain split

The single most misread part of the model. `data.feed.Bar` and `market.bar.Bar`
both exist **on purpose**: one is what a provider can fill in knowing nothing
about AlphaLab (`float`, provider symbol, lossy), the other is what the execution
path consumes (`Decimal`, `asset_id`, venue, currency, timeframe). They sit on
opposite sides of one explicit conversion, `market.normalization`.

Collapsing them would force one to lie. `marketdata.feed` re-exports the *same
class objects* from `data.feed`, so there is exactly one wire record per
concept, and `test_market_model_convergence.py` asserts it — and, since v3.10,
that the second normalization authority `alphalab.feed` had been (with a
hard-coded `"USD"` and a one-minute default timeframe) stays removed.

The policy doing the lifting states what the wire cannot, and since v3.10 has
no defaults for it: a quote or trade is refused without a currency, a bar
without a timeframe or without the source's `BarStamp` — which end of its
interval the bar's timestamp names. A start-stamped bar is moved to its end,
the instant its close is known.

---

# 6. The execution path, in order

```
market record
  → publish to the market engine
  → mark to market                 positions re-priced; ONLY unrealized P&L moves
  → resync risk from the marked book
  → strategy dispatch              sees the MARKED portfolio (v2.10)
  → Intents
  → allocation                     sizes, nets, reserves capital per order
  → drop requests with no price    recorded on unpriced_assets
  → settlement authority check     drops an instrument this run may not trade
  → risk                           approve / reject; a rejection releases
  → OMS                            order lifecycle
  → fill policy                    what the venue does with this order now
  → execution                      ExecutionReport (simulated) — or a venue fill returning
  → settlement integrity check     refuses a report in the wrong currency
  → portfolio                      cash, positions, per-currency realized P&L
  → valuation snapshot             one per market event
  → analytics                      on demand
```

Two rules that are easy to break and expensive to re-derive:

- **Mark before decide.** The portfolio is marked and risk resynced *before* the
  strategy is dispatched and before risk evaluates. Otherwise a strategy reads a
  book priced at the previous event and risk evaluates the resulting order
  against a different one.
- **A fresh order per market event.** `ExecutionPipeline` mints a new order per
  event and never re-works an existing one. A partially filled order is cancelled
  and its residual reservation released (v2.5). A strategy wanting to finish a
  large order keeps expressing the intent.
- **With `EXTERNAL` routing the order stays working**, and since v3.9 an
  execution algorithm may work it in children *outside* the step: children go
  out through `route_child_order` and their fills come back through
  `apply_broker_execution`. The step itself still mints a fresh order per event
  and never re-works one.

---

# 7. Accounting

The identity, exact over `Decimal` for any price and quantity the engine accepts:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

Verified at v3.0 through the real pipeline at 500 / 1k / 2k / 4k / 8k records.

## The rounding policy — `portfolio.money` (restated in v3.10)

1. **Money is exact at its currency's minor unit.** The minor unit is a property
   of the currency: ISO 4217's table (`common.currency_units`, JPY 0, USD 2,
   KWD 3, CLF 4), plus the units a book declares for what ISO 4217 does not
   list (`Account.currency_units`). A currency with neither is refused.
   `to_money(amount, currency)` is the *only* place rounding happens, half to
   even. Until v3.10 every currency was rounded to `0.01`.
2. **Rounding happens once, at entry.** `apply_fill` rounds the notional and
   commission as they enter; the cash movement *and* the cost basis derive from
   those same rounded values.
3. **Prices and quantities are exact inputs, not money.** They are kept as the
   venue or the market gave them and become money only when multiplied into an
   amount. Until v3.10 they were quantized to 1e-4 and 1e-6 first, which booked
   EUR/USD 1,000,000 @ 1.08345 at 1,083,400.00 and could not book a 0.0000005
   BTC fill.
4. **A split rounds one part and derives the other by subtraction**, so the parts
   always sum to the exact whole.
5. **One decimal context.** Every accounting and execution computation runs in
   `ACCOUNTING_CONTEXT` (precision 34, half-even, trapping), never in the
   caller's thread's context.

Before this policy the ledger and the position rounded the same economic event
independently and drifted by up to five cents on randomized portfolios. Do not
add a second rounding site.

## What is stored and what is derived

- **Stored:** cash (`CashLedger`, keyed by currency), positions, the transaction
  ledger, the event log, and cumulative `realized_pnl` / `commission_paid` — both
  `CurrencyAmounts`, currency to exact amount.
- **Derived, never stored:** unrealized P&L and equity, by
  `PortfolioValuation.snapshot`, which is a **read model** over `PortfolioState`,
  not a second state.

**Realized P&L is not a cash movement.** It is already implicit in the entry cost
and the exit proceeds. Adding it to cash was the D1 defect fixed in v2.0.0.

---

# 8. Money in more than one currency

The hardest part of the system to reason about, and the part most likely to be
broken by a well-meaning simplification.

## Settlement truth and reporting truth are different numbers

- **Settlement truth** is what was earned, in the currency it was earned in,
  permanently. `realized_pnl` and `commission_paid` are per-currency, so a EUR
  fill accrues EUR P&L and **nothing is ever summed across two currencies**.
- **Reporting truth** is one figure in one named currency, converted **on
  demand**, recording every rate used.

Translating at fill time would have been the smaller change and is wrong: it
destroys the only record of what was actually earned and bakes one instant's rate
into a cumulative figure.

## Four currency roles, and they are not one thing

| Role | Where | Authority over |
| --- | --- | --- |
| Instrument currency | `InstrumentRecord.currency` | whether this run may trade the instrument at all |
| Settlement currency | `ExecutionPipelineConfig.currency` + `.also_settles` | what a fill may be denominated in |
| Reporting currency | the argument to a valuation | the one figure a human reads |
| Market-data attribution | `NormalizationPolicy.currency` | what a quote is *labelled* with — **never** accounting |

`ExecutionPipelineConfig.also_settles` is **empty by default**, which is the
single-currency pipeline every run had before v2.17 and is byte-identical to it.

## Two seams, because there are two questions (ADR-0028)

| | Seam 1 — authority | Seam 2 — integrity |
| --- | --- | --- |
| Question | *May this run trade this instrument?* | *Is this report denominated in what this pipeline settles?* |
| Site | `_process_requests`, before the OMS | `_apply_report_to_portfolio` |
| Needs a registry | yes | no |
| Disposition | **drops** the request, retiring both ledgers | **raises** |

The dispositions differ because the vocabularies differ: the pipeline may decline
to create an order; it may not decline a fill that already happened at a venue.
Neither seam subsumes the other, and both were shown necessary.

## FX: every rate is supplied

There is **no default rate, no fallback of 1.0, no implicit triangulation and no
implicit inversion.** Verified behaviour, at v3.0 and as extended by v3.4:

| Asked for | Answer |
| --- | --- |
| A pair the table does not hold | `MissingRateError` |
| USD→EUR from a EUR/USD rate | **refused** — a real quote has two sides |
| EUR→JPY from EUR/USD and USD/JPY | **refused** — that is a rate nobody quoted |
| A rate older than `max_age_seconds` | `StaleRateError` |
| A zero or negative rate | refused at construction |
| A rate with no `source` | refused at construction — an unattributed rate is the configured rate ADR-0020 rejected |
| Two rates for one pair | refused — which is right is not a question a table answers by picking |
| `with_inverses()` | mints the opposite direction, marked `derived=True`, never replacing a real quote |
| `cross_rate(base, quote, via=...)` | derives one through a **named** currency, marked `derived=True`, taking the older leg's `as_of` (v3.4) |
| A rate dated **after** the conversion instant | `FutureDatedRateError` — a look-ahead, refused since v3.4 |
| Same-currency conversion | identity, marked `source="identity"` |

Every conversion records the rate, its `as_of` and its source.

## The feed boundary (v2.17)

`portfolio.fx_feed` is a **boundary, not data**. Three rules, one refusal:

| A quote that is… | …is |
| --- | --- |
| newer than what is held for its pair | `APPLIED` |
| byte-identical to what is held | `DUPLICATE` |
| **older** than what is held | `SUPERSEDED`, and **not applied** |
| the same instant, a different rate or source | **refused**, not ranked |

The third is the one that matters: accepting it would move the book's view of the
market backwards because two packets arrived out of order.

**AlphaLab ships no FX rate.** EXTERNAL.

## Many strategies, several currencies (v3.8)

A multi-strategy book keeps every sleeve's cash, realized P&L and commissions per
currency, and `value_book` converts each native figure once, through
`FxRates.convert`, keeping every conversion — so the per-strategy, per-instrument
and per-currency breakdowns reconcile to the cent, and a missing, stale or
future-dated rate refuses exactly as it does everywhere else. A capital plan
allocates in each **account's** currency — settlement truth — and translates only
what is expressed against the whole plan; the exact identity is the per-account
one, `available = reserved + allocated + unallocated`. The risk model's
currency is the currency its returns were measured in, and every consumer
refuses a mismatch rather than rescaling.

---

# 9. State, snapshots and durability

Ten durable states. Each has **one** snapshot owner, **one** schema constant and
**one** typed decoder.

| State | Snapshot module | Constant | Value |
| --- | --- | --- | --- |
| `OMSState` | `oms.snapshot` | `OMS_SNAPSHOT_SCHEMA` | 2 |
| `PortfolioState` | `portfolio.snapshot` | `PORTFOLIO_SNAPSHOT_SCHEMA` | 5 |
| `LifecycleState` | `lifecycle.snapshot` | `LIFECYCLE_SNAPSHOT_SCHEMA` | 2 |
| `AllocationState` | `allocation.snapshot` | `ALLOCATION_SNAPSHOT_SCHEMA` | 3 |
| `ExecutionPipelineState` | `runtime.snapshot` | `PIPELINE_SNAPSHOT_SCHEMA` | 7 |
| `RunState` | `runtime.run_snapshot` | `RUN_SNAPSHOT_SCHEMA` | 4 |
| `InstrumentRegistry` | `instrument.snapshot` | `INSTRUMENT_SNAPSHOT_SCHEMA` | 3 |
| `BrokerState` | `broker.snapshot` | `BROKER_SNAPSHOT_SCHEMA` | 2 |
| `LiveRunState` | `runtime.live_snapshot` | `LIVE_SNAPSHOT_SCHEMA` | 2 |
| `FxFeedState` | `portfolio.fx_feed` | `FX_FEED_SNAPSHOT_SCHEMA` | 1 |

Plus `persistence.run_state.RUN_STATE_ENVELOPE_SCHEMA = 1`, which versions what
the *store* records about a payload and nothing inside it, and the envelopes of
an incremental checkpoint (`runtime.checkpoint.CHECKPOINT_SCHEMA = 2`; v3.12, and
2 since v3.13 writes per-order state by its changes) and of the evidence store
(`model_registry.evidence.EVIDENCE_SCHEMA = 1`, v3.12). The
values are v3.13's, and `tests/regression/test_documented_schemas_are_current.py`
keeps them so.

The contract is `restore(capture(s)) == s` — semantic equality, not container
lineage.

## Rules that must not be relaxed

- **Every constant is a module-local literal.** None aliases
  `DEFAULT_SCHEMA_VERSION`, because that constant also versions `BaseEvent`:
  bumping it would version every event in the system as a side effect of one
  subsystem's change. Four regression tests pin the de-aliasing, and each snapshot
  module carries a comment saying why.
- **Older payloads are upgraded, explicitly and honestly (v3.10).** Each
  subsystem declares a `SchemaHistory` (`persistence.upgrade`): one pure step per
  earlier version, run on primitives before typed decoding, so a decoder only
  ever reads its current schema. A step supplies a value only when it is what
  the older payload already meant; otherwise it refuses
  (`SchemaUpgradeRefused`), and where a recorded fact cannot be carried it warns
  (`SchemaUpgradeWarning`). Payloads written by v3.9.0 itself are golden
  fixtures (`tests/fixtures/snapshots/v3.9.0`). This replaced the rule "one
  readable version per subsystem, no migration framework"; `OMS_SNAPSHOT_SCHEMA`
  still also reads one exact legacy unversioned key set.
- **Envelopes nest, never merge.** The live envelope carries a run snapshot and a
  broker snapshot, each versioned by its own constant, which is why adding venue
  durability in v2.16 moved no schema a backtest writes.
- **Live objects are referenced, not reconstructed.** A strategy instance, a
  simulator, a sizing model, a fill policy and an instrument registry are recorded
  *by type*; `restore` requires them back from the caller and **raises** on a
  missing or mistyped one. Never substituted.
- **Derived indexes are rebuilt, not stored.** The order book's asset and strategy
  indexes, and the instrument registry's provider index. One fact, one home.
- **Identity is re-derived, never read.** `restore` recomputes every `asset_id`
  from its declaration, so a payload cannot assert an identity.
- **Two snapshots deliberately drop fields**, and each says so in its own
  docstring: `LiveRunSnapshot` omits `routed` / `settled` (derivable from the two
  halves — carrying them would give one fact two homes that could disagree), and
  `InstrumentRegistrySnapshot` omits `by_provider` (a rebuilt index).
- **A dataset version is never overwritten** (v3.1). Cleaning and resampling
  derive a new version through `derive_transformed_version`; `DataManager`
  refuses a version it already holds, and `UniversalDataState.lineage` records
  the parent. A dataset's identity is derived from its content and
  configuration, never minted — the property `derive_asset_id` gives
  instruments, for the same reason.
- **Provenance may be absent, and says so.** A dataset built from rows already
  in memory carries `provenance=None`; `require_provenance()` refuses rather
  than manufacturing a record, so an unverifiable dataset can never look like a
  verified one. Same rule as `RunState.source_id` being `None` for a
  hand-driven run.

## Where a payload goes

`persistence.RunStateStore` — four methods over `(run_id, sequence) → payload`.
**Payload-agnostic**: it moves a `str`, imports no snapshot type and decodes
nothing, which is what keeps it correct across schema changes.
`FileRunStateStore` writes atomically and verifies a SHA-256 digest **before any
decoder runs**; `MemoryRunStateStore` is an explicitly named double that nothing
selects automatically and that the file store never degrades into.

A seeded run can stop in one process and finish in another, producing a
**byte-identical** payload — proven across a real interpreter boundary, not
asserted.

## Determinism

`RunConfig.seed` installs a `DeterministicIdSource` for the run. A seed is a
non-negative integer (`bool` refused), and the identifier stream is pinned by a
golden test (v3.10); every other stochastic step — initializers, bootstraps,
searches — takes its seed explicitly, with no default.
`IdStreamPosition` is `(seed, draws)` — two readable integers, not a serialized
generator state, so nothing in the persisted format pins a PRNG implementation.
Without a seed, identifiers stay on `uuid4` and only the economics reproduce;
that is deliberate and the source of nondeterminism is visible on the config.

---

# 10. Identity and classification

**An `asset_id` is derived, not minted**: `uuid5` over
`(asset_type, exchange, symbol, currency)` under a frozen namespace, so two
independently configured environments agree with no shared database. Registration
is still required — derivation alone would turn every typo into a new instrument.

`NormalizationPolicy` resolves identity in exactly two named modes, never `None`:

| Mode | Behaviour | Permitted use |
| --- | --- | --- |
| `InstrumentRegistry` | refuses an unregistered `(provider, symbol)` at the boundary | the only mode that may reach a `Fill` |
| `UnresolvedIdentity` | passes the provider symbol through | testing the wire→canonical lift **only** |

`ProviderHistorySource.of` refuses the second mode before calling a provider.
(Until v3.10 a `DEFAULT_POLICY` in that mode was every `normalize_wire_*`
function's default; there is no default policy now.)

**Two tenses, two owners.** The registry says what an instrument *is* classified
as; a `TradeRecord` says what it *was* classified as when the fill happened. A
later reclassification cannot rewrite a completed run, and a run reclassified
mid-run correctly splits across both sectors. Classification is append-only with
`source` and `as_of`, so a correction is a new fact and the entry it corrects
stays readable.

`sector` is deliberately **outside** the canonical key, so a classification can
never re-identify an instrument and orphan its fills — guaranteed three ways, and
ADR-0016's golden identifier is re-derived from a classified record.

**AlphaLab ships no taxonomy and no reference data.** EXTERNAL. Sector is also
the only dimension; industry, country, issuer and rating are each a separate
decision.

---

# 11. Strategies

## What a strategy is

`StrategyProtocol` — a `runtime_checkable` Protocol declaring ten hooks.
`BaseStrategy` is an optional convenience ABC. A strategy may implement the
Protocol directly.

**Of the ten hooks, seven are routed and three are not.** `Dispatcher` routes
`on_tick` / `on_quote` / `on_trade` / `on_bar` by **module and name together**
(never the bare name — three packages define a `TickReceived`, and matching the
name alone routed the wrong class and then blamed the strategy for the resulting
`AttributeError`), and routes `on_fill` / `on_order` / `on_timer` when a caller
supplies those events. `on_start` / `on_stop` / `on_shutdown` are declared and
**never invoked by the runtime**.

`BookUpdated` and `SnapshotCreated` reach no hook at all. NON-GOAL, stated and
pinned.

## Three registries, three different questions

Easy to confuse; they are not the same thing.

| | Answers | Lives in |
| --- | --- | --- |
| `strategy.StrategyDefinition` | *what is this strategy?* — author, parameters | `strategy` (`studio` until v3.11) |
| `lifecycle` registration | *which version of it should be live, and who said so?* | `lifecycle` |
| `strategy.registry.StrategyClassRegistry` | *which code runs it?* | `strategy` |

The class registry is deliberately **not** in `lifecycle`, which still constructs
nothing. It stores an identity and a factory and nothing about what a strategy
*is*. It **never resolves a name to code** — no `importlib`, no class-name
derivation, because a name is not a type. A duplicate registration is refused
naming both incumbent and challenger; an unknown identity is refused listing what
*is* registered; a factory returning something undispatchable is refused at
construction rather than at its first market event.

## What a strategy may see

`StrategyContext`, nine fields, all populated: `portfolio` (the **marked** book),
`market`, `clock`, `logger`, `risk_view`, `config`, `orders`, `history`,
`universe`. `history` is bounded at the dispatched event's timestamp, taken from
the **event** and never a wall clock, enforced at construction — that is the
look-ahead guard.

`context.orders` is a **builder and query facade**, not an order-placement API.
The only channel of effect is the `Iterable[Intent]` a hook returns.

A strategy sees its **share** of a netted order, from the allocation contribution
ledger — two strategies whose intents net into one `BUY 100` see 60 and 40, and
neither claims sole ownership.

## The layering rule that constrains everything above

**ADR-0016 decision 3: `alphalab.strategy` acquires no dependency on
`alphalab.market` or `alphalab.instrument`.** It is enforced by a test.

This is why `Intent.instrument` is a bare `str`, why event routing matches
module-and-name rather than `isinstance`, and why the strategy runtime is
importable without the identity authority. Importing the canonical market types
into the dispatcher was tried during v2.16 and the boundary test caught it.

---

# 12. The other subsystems, briefly

**Allocation.** Owns the amount; the pipeline owns the moment.
`release_reservation` takes no amount — it frees whatever the ledger holds, so a
release can neither free more than was reserved nor free it twice. Releasing an
order holding no live reservation **raises** rather than silently subtracting,
which is what makes "exactly once" checkable. `alphalab.allocation` knows nothing
about exchange rates, and that is measured: threading an `FxRates` into it pulled
all eighteen `portfolio` modules into a package that imported none of them.

**OMS.** The sole authority on order state. Every non-trading outcome is terminal
for the order.

**Broker.** Two boundaries: `BrokerProtocol` for one venue,
`BrokerConnectorProtocol` for many venues and many accounts. The connector routes
the canonical types under their canonical names — `brokers.BrokerExecution` **is**
`broker.BrokerExecution`, asserted by test; its historical aliases
(`brokers.ExecutionReport`, `brokers.OrderStatus`, ...) were removed in v3.13,
because each kept one name meaning two things (ledger API-001). Reconciliation is total: `APPLIED`,
`DUPLICATE`, `UNKNOWN_ORDER`, `TERMINAL_ORDER`, `OVERFILL`, `INVALID` — nothing
is silently dropped. Two pre-trade gates: never on a disconnected connection,
never twice for one OMS order, with the client order id **derived** from the OMS
order id so a retry after a lost response addresses the same order.

**Live.** `LiveSession` is settle → advance → route. The order is the contract: a
fill the venue already reported reaches the portfolio **before** the strategy is
dispatched. Two ledgers answer two different questions —
`BrokerState.executions` ("has the venue-side bookkeeping recorded this fill?")
and `ExecutionState.reports` ("has the *portfolio* booked it?") — and a fill in
one and not the other is the normal case. Gating the portfolio on the broker
layer's answer silently dropped every live fill in the first implementation.

**Governance.** `Governance(authority, actor_id, approval_required_in)` is the
**required** second argument of every entry point that changes what is live. Not
optional: an optional gate is one anyone bypasses by calling the function
directly. An approval names an exact `(name, version, environment)` and one
granted by the deployer does not count. An act no principal requested records
`actor_id=""`, which is the honest answer. A rollback needs no approval — a
control that stops a firm taking a bad release down is not a control.

**Artifacts.** Content-addressed: an artifact is the SHA-256 of its bytes, so
storing identical bytes twice is one artifact and a reference cannot name bytes
that hash to something else. The URI is `alphalab-artifact:sha256:<hex>`, never a
filesystem path. It is **not** a second persistence owner: `RunStateStore` owns
run state addressed by `(run_id, sequence)`; this owns bytes addressed by content.

**Streaming.** `market.stream.StreamingSource` **is** a `MarketDataSource` and
nothing more, because `records()` already returned an iterator. It declares
`UNORDERED`, and a regressing record is skipped-and-recorded rather than marking
the portfolio backwards.

**TLS.** One policy, `common.tls`, for all three outbound call sites. It states a
TLS 1.2 floor **explicitly** rather than inheriting whatever the host's OpenSSL
allows — a guarantee that holds because of how a machine happens to be configured
is not a guarantee the code has. No parameter lowers the floor; no
retry-on-older-protocol fallback exists.

---

# 13. Verification

```bash
ruff check .                              # lint
ruff format --check .                     # format
mypy .                                    # strict, 1,152 source files (what CI runs)
pytest -q -W error                        # 9,041 tests, 0 skipped, 0 warnings (what CI runs)
git diff --check
python -m build && twine check dist/*
for f in examples/[0-9]*.py; do python -W error "$f"; done    # 69
for f in benchmarks/benchmark_*.py; do python "$f"; done      # 53
python -W error docs/audit/scripts/certify_release.py --check # the release certificate
```

`make check` runs the first four. Since v3.10 CI also installs the wheel and
the sdist, each into a fresh environment, and runs `tests/installed_smoke.py`
against them from outside the checkout; the benchmarks run weekly.

**9,041 tests** — 4,806 unit, 649 integration, 3,586 regression. The
regression suite is nearly as large as the unit suite, deliberately: most of its
files pin a *decision* rather than a behaviour, so a future "simplification" has
to break an assertion and read a reason first.

Neither the zero skips nor the zero warnings can be satisfied by configuration:
`test_the_suite_reports_nothing_deferred.py` reads the **collected items** rather
than the summary line, refuses any `skipif` it does not list — it lists none
since v3.10, so the suite skips nothing whoever runs it, root included — and
spawns a **fresh interpreter** with `-W error::DeprecationWarning` to import
every module in the tree.

## The tests to read before changing anything

| File | Pins |
| --- | --- |
| `test_shared_names_stay_distinct.py` | Fifty sets of same-named things that are not one thing, including the three lifecycle state machines, the two reconciliations, the two health surfaces, the five content identities, the three ways of checking a strategy, v3.7's events, regimes, provenance records, fundamentals, as-of readers, states, observations and replays, v3.8's capital shapes, budgets and limits, factor exposures, projection versus optimization, correlation versus similarity, Euler weightings, Cholesky factorizations and target weights, and v3.9's two capability shapes, five execution-report-shaped things, assumed versus measured slippage and latency, route selection versus sending, a child order as an instruction, the order lifecycle as a fourth axis, and snapshot reconciliation as the same pair as `reconcile` |
| `test_venue_concepts_stay_distinct.py` | Listing exchange vs market-data attribution vs execution venue |
| `test_no_silent_financial_defaults.py` | An AST sweep of the whole package — parameter defaults, call-site literals and mapping fallbacks on financial names, with a default of zero counted as a default since v3.10; each exemption carries its reason |
| `test_snapshot_field_coverage.py` | Silent state loss when a state gains a field |
| `test_removed_surfaces_stay_removed.py` | That removed names stay gone, including under a different spelling |
| `test_currency_authority.py`, `test_settlement_multi_currency.py` | The currency roles and the two seams |
| `test_durable_run_state_cross_process.py` | Byte-identical continuation across a real process boundary |
| `test_instrument_identity_reaches_a_fill.py` | The `strategy` → `market` layering ban |
| `test_the_suite_reports_nothing_deferred.py` | Zero skips, zero warnings |
| `test_data_quality_contracts.py` | What ingestion does with bad data, one defect at a time |
| `test_dataset_provenance_and_immutability.py` | Provenance preserved, versions immutable, identity derived |
| `test_data_ingestion_complexity.py` | That the ingestion path stays linear in row count |
| `test_research_cannot_see_the_future.py` | That no feature kind reads ahead, and no fold's parts intersect |
| `test_research_is_reproducible.py` | That no research identity reads a clock and every stochastic step is seeded |
| `test_one_research_authority_per_concept.py` | One owner per research concept, and the import edges v3.2 added |
| `test_research_complexity.py` | That the feature and research paths stay near-linear |
| `test_v34_invariants.py` | One authority per concept, the dataclass-field default sweep, point-in-time guards, dimensional correctness, and that v3.4 added no durable state |
| `test_v34_complexity.py` | That roll selection, segment construction, chain inversion and contract exposure stay linear |
| `test_v35_invariants.py` | One authority per concept, cross-process determinism of a deployment identity, that a missing observation can never read as healthy, that nothing v3.5 added mutates state or persists any, and the vendor boundary |
| `test_v35_complexity.py` | That health evaluation, comparison, reconciliation and a progression's history stay near-linear |
| `test_v36_invariants.py` | One home per v3.6 concept; evidence that carries no verdict; the gate's own leverage and drawdown readings; no clock, entropy or environment on a v3.6 path; every v3.6 identity reproduced in two fresh interpreters with different hash seeds; nothing machine-local in an identity; no durable state; no marketplace operation |
| `test_v36_complexity.py` | That fingerprinting, source digests, run digests, certification and portability stay linear |
| `test_v37_invariants.py` | One home per v3.7 concept; `alt_data` a leaf over `common`, `strategy` still over `common` only, research never reading `data`; every selection checked against a brute-force reading of the visibility rule on generated histories, and no vintage, frame point, event anchor, trailing figure or regime label reaching past its instant; no clock, entropy or environment; every identity reproduced in fresh interpreters with different hash seeds and working directories; pre-v3.7 study and fingerprint identities unchanged; no durable state |
| `test_v37_complexity.py` | That set construction, visibility queries, single-figure reads, knowledge and fundamental frames, regime classification, adaptive replay and event studies stay near-linear |
| `test_v38_invariants.py` | One home per v3.8 concept; the edge sets of `portfolio_optimizer`, `analytics`, `allocation`, `portfolio`, `lifecycle` and `common`; v3.3's decomposition unchanged float for float; no clock, entropy, environment or platform-dependent transcendental on a v3.8 path; every identity reproduced in fresh interpreters with different hash seeds and working directories; constructions checked against constraints evaluated directly and against exhaustive exact-rational enumeration; risk budgets conserving volatility; books reconciling in every currency; capital reconciling exactly, never negative and never quietly scaled; no durable state, vendor, network or secret field |
| `test_v38_complexity.py` | That factor crowding, a risk budget with a limit per strategy, book valuation, capital allocation, common exposures and overlap stay near-linear — timed with the stabilized method, and each guard run against its defect |
| `test_v39_invariants.py` | One home per v3.9 concept; no package or edge added; the OMS and the venue boundary reading one table; terminal statuses absorbing; event streams idempotent under replay and convergent under reordering; compatibility true only when every check is; schedules summing exactly; routes selecting only eligible venues regardless of listing order; shortfall decomposing exactly; every injected snapshot divergence reported; identities reproduced across hash seeds and working directories; no clock, entropy, environment, transcendental or ambient decimal context; no vendor, network, secret field, snapshot owner or schema constant |
| `test_v39_complexity.py` | That capability checks, a venue event stream, a many-slice schedule, slicing by count, route selection, snapshot reconciliation and venue quality stay near-linear — stabilized method, each guard run against its defect |
| `test_risk_projection.py`, `test_allocation_semantics.py` | v3.10: risk judged on the projected book and never refusing a reduction; long-only against committed positions |
| `test_universe_scaling.py` | v3.10: the canonical path linear in the number of assets, and six injected regressions of incremental marking each failing it |
| `test_schema_upgrades.py` | v3.10: every v3.9.0 golden payload upgraded, or refused with its reason |
| `test_fill_timing.py`, `test_execution_assumptions.py`, `test_strategy_failures_are_reported.py` | v3.10: next-event fills, recorded execution assumptions, reported strategy failures |
| `test_bar_stamp_convention.py` | v3.10: a bar stamped at the end of its interval, and a source's convention required |
| `test_release_gates_are_wired.py`, `test_mutation_pins.py` | v3.10: CI, hooks and pyproject agree; every mutation the audit's harness let through is pinned |
| `test_public_api_manifest.py`, `test_api_changes_are_in_the_changelog.py` | v3.13: every export of every package recorded with its binding, regenerated with each release; a removed or rebound name is refused until the release's CHANGELOG section names it |
| `test_persisted_enum_names.py`, `test_release_certification.py` | v3.13: a persisted enum's class and member names are part of the format; the release certificate's checks pass, its evidence has not moved and the engine source it names is the build's; every Python file is checked out with LF |
| `test_every_adr_deferral_is_classified.py`, `test_ledger_references_exist.py`, `test_version_markers_agree.py`, `test_nothing_is_left_for_later.py` | v3.13: every limitation and deferral an ADR states maps to a closed ledger entry; every test the ledger cites exists; every document that states the version states the package's; every ledger entry is implemented or kept, none assigned beyond the release, and no current-state document calls anything deferred |

## Performance

The canonical path is **linear** in records — ~2.0× per doubling, measured
500 → 8,000 records at v3.0, with the accounting identity holding at every
size — and, since v3.10, **in the universe**: a market event re-marks what it
priced and reads exact per-currency totals, so the per-record cost across an
8× universe (50 → 400 assets) measured between 0.85× and 1.24× over four runs
in the release gates, 663–918 µs a record (it was quadratic until v3.10,
which the audit's probe measured at about 35× the time for 8× the assets). Engine
histories are `AppendOnlyLog` (O(1) amortized append) and keyed state is
`PersistentMap` / `PersistentSet` (O(1) amortized write, copy-on-branch).

**Ingestion is linear** in row count: measured at 1,000 / 10,000 / 100,000 /
1,000,000 rows (10.3×, 11.7× and 10.3× for each 10× of data, against a linear
prediction of 10×), ~9µs per row in pure Python with no dependencies. Duplicate
detection uses a hashed set and ordering a per-instrument high-water mark, both
pinned structurally by `test_data_ingestion_complexity.py`, because the obvious
implementation of either is a rescan.

**The v3.2 research paths are linear too**, measured at 1,000 / 10,000 /
100,000 / 1,000,000 observations in
`benchmarks/benchmark_feature_engineering.py`: reading a field costs ~9.6×,
~11.0× and ~11.3× per 10× of rows, and a fixed-window feature ~9.7×, ~9.6× and
~10.7×. A feature is `O(n * w)` rather than `O(n)` on purpose — an incremental
rolling sum accumulates floating-point drift across a million updates, so the
same window computed early and late in a long series would not agree.
`test_research_complexity.py` holds the growth ratios rather than the times, so
the assertion is about the algorithm rather than the machine.

**The v3.7 point-in-time paths are linear**, measured at two sizes each in
`benchmarks/benchmark_point_in_time_research.py` and
`benchmark_adaptive_research.py`: a knowledge frame samples ~3.5–4 million
subject-instants per second, ingestion reads ~100,000 rows per second, and an
adaptive step costs ~25–40 µs including its derived identities. Every
many-instant question is a bisection or an incremental timeline. The benchmark
found one that was not — `vintage_as_of` filtered its series' whole visible
history on every call — and it was fixed before release with a per-figure index;
`test_v37_complexity.py` holds it flat across sixteen times the history. Two
costs remain proportional by design: `select` returns everything visible, so it
costs the size of its answer, and the single-instant fundamental helpers
(`trailing_twelve_months`, `latest_fundamental`, `fundamental_inputs_as_of`) read
a series' visible history per call — the frames are the path for many instants.

**The v3.8 aggregations are linear**, measured at two sizes each in
`benchmarks/benchmark_portfolio_risk.py`: a risk budget, book valuation, factor
crowding, overlap and capital allocation each process roughly 110,000 to
650,000 lines, positions or placements a second at both sizes. Construction is
not linear and does not claim to be: the dual active-set solver is cubic in the
universe, a few milliseconds at twenty-five assets and on the order of ten at
fifty. Writing the complexity guards found three per-call rescans in the new
code before release — factor-loading and classification lookups that each built
a set of every asset, a risk-budget limit lookup that scanned buckets, and
notional limits located by a linear search — and each is now an index.

**The v3.9 paths are linear**, measured at two sizes each in
`benchmarks/benchmark_execution_contract.py`: compatibility checks run at about
120,000 a second against thousands of declared accounts, a venue event stream
at about 130,000 events a second, releases of every algorithm at about 57,000
children a second, route selection at about 53,000 venues judged a second,
snapshot reconciliation at about 480,000 records a second and venue quality at
millions of outcomes a second — flat across a fourfold size change. Schedule
planning with a non-zero urgency is slower per slice (about 37,000 slices a
second) because the trajectory is evaluated in 34-digit decimals, the price of a
schedule that is identical on every platform. Writing the guards found a
declaration identity rendered on every check and `list.count` duplicate
detection in the draft; both are gone.

**The v3.13 scales are measured** by `docs/audit/scripts/stress_v3_13.py`,
which also re-runs v3.12's 10,000-asset, 1,000-strategy and 100-venue
scenarios. A factor model stated by its structure is built, decomposed and
constructed over in O(n k²): 10,000 assets take 0.12 s and 6 MB to state and
1.85 s to construct over (PRF-013) — where writing the same model out as a
dense matrix is O(n²), 29 s and 1.3 GB at 4,000 assets. The optimal split
searches every set of fixed-charge venues, so its ceiling of ten such venues
costs seconds (14–20 s of CPU for an order across ten fixed-charge and ten free
venues); an American price on 5,000 steps, the lattice's ceiling, under three
seconds; a run's per-order state about 2.7 kB an order (PRF-011, kept).

The one term v3.10 left super-linear — the optimizer's pending trials — left
with the optimizer in v3.12 (OFE-013).

---

# 14. What must not be changed casually

**These are the frozen invariants.** Changing any of them is a major release with
an ADR.

1. **One owner per responsibility.** The step is `ExecutionPipeline`'s, the run is
   `RunEngine`'s, and drivers hold no state. Do not give a driver a field.
2. **One snapshot owner and one module-local schema literal per state.** Do not
   alias `DEFAULT_SCHEMA_VERSION`. Do not add a migration framework.
3. **The accounting identity, and one rounding site.** `to_money` is the only
   place rounding happens. Do not add a second.
4. **Settlement truth is per-currency and permanent; reporting converts on
   demand.** Do not translate at fill time.
5. **No invented financial value.** No default rate, no 1.0 fallback, no
   triangulation, no silent inversion, no assumed currency, no invented margin
   rate. Refuse instead.
6. **Identity is derived and frozen.** `asset_id` derivation, the canonical key,
   the namespace, and `sector`'s exclusion from the key.
7. **`alphalab.strategy` depends on neither `market` nor `instrument`.**
8. **`alphalab.common` imports nothing else in `alphalab`.** It is the bottom
   layer; the whole graph rests on that.
9. **Zero runtime dependencies.** `dependencies = []`.
10. **Zero package-level import cycles.**
11. **Live objects are referenced by type and required back from the caller**, never
    substituted on restore.
12. **Governance is required, not optional**, at every entry point that changes
    what is live.
13. **No wall clock on the execution path.** Every timestamp comes from the record
    or the event; the one clock in a run is the `now` argument to
    `RunEngine.advance`.
14. **Zero skipped tests and zero warnings.**
15. **A dataset version is never overwritten, and nothing is altered silently**
    (v3.1, ADR-0036). Cleaning derives a new version rather than editing one;
    every rejected row carries its reason and source line; every applied change
    is a recorded `TransformationRecord`. A cleaning policy is the caller's and
    has no default, and there is no way to fill a missing price.
16. **Ambiguity in ingestion is refused, not resolved.** An unresolved schema, a
    delimiter two candidates fit, a naive timestamp with no zone named, and a
    numeric column that reads as a valid instant in both seconds and
    milliseconds each raise rather than pick.
17. **No feature reads an observation after the one it is computed for**
    (v3.2, ADR-0037). Every window is trailing and inclusive of the current
    observation. Asserted for **every** `FeatureKind` by recomputing on a
    truncated series and requiring the overlapping values to be identical, in
    `tests/regression/test_research_cannot_see_the_future.py`.
18. **No fold's parts intersect, and purging is defined by information
    windows.** A `PurgePolicy` has no default horizon and cannot be built
    without one; `label_ends_from_horizon` reads the actual series rather than
    subtracting dates; a scheme named `PURGED` or `EMBARGOED` refuses to be
    built without the policy that makes the name true.
19. **A research identity is derived from content and configuration, never
    from a clock.** `feature_version`, `lineage_id`, `study_id` and `result_id`
    all reproduce across processes and machines. `produced_at` is recorded and
    never hashed, the rule `DatasetProvenance` applies to `retrieved_at`.
20. **Every stochastic research step takes an explicit seed**, with no default
    anywhere, and a `Perturbation` refuses both a stochastic kind with no seed
    and a deterministic kind with one.
21. **An unmeasurable statistic is `None`, never `0.0`**, and there is **no
    blended score** for overfitting, signal quality or research grade. A
    measurement, a threshold and an interpretation are separate fields.
22. **One statistics authority.** `alphalab.common.statistics` is the only home
    for the unbiased sample variance, correlation, ranking and quantile
    bucketing. v3.2 consolidated five private copies of the variance onto it;
    `tests/regression/test_one_research_authority_per_concept.py` reads the
    source to keep a sixth from appearing. v3.3 added `sample_covariance` to the
    same module, built from the same expression so that
    `sample_covariance(x, x)` is exactly `sample_variance(x)`.
23. **An execution cost is price-embedded or cash-charged, never both**
    (v3.3, ADR-0038). Spread, slippage and impact move the fill price;
    commission, fees and tax go down the one cash channel `apply_fill` takes.
    The two totals an `ExecutionReport` carries are exactly the six itemized
    components, asserted on every run in
    `tests/regression/test_v33_invariants.py`.
24. **The cost itemization is derived, not stored.** `ExecutionReport` is not
    widened; `simulate_costs` recomputes any fill's breakdown exactly from the
    run's configuration.
25. **A capacity figure carries its assumptions, and names what binds.** No
    default participation limit, turnover or impact budget exists anywhere.
26. **Missing attribution metadata is reported as unavailable, never
    fabricated.** No dimension invents an `UNKNOWN` bucket. Currency attribution
    does not total across currencies, because that would need a rate AlphaLab
    will not invent.
27. **A VaR or CVaR figure names its methodology**, and risk contributions sum
    to the volatility they decompose. An undefined statistic refuses rather than
    returning zero.
28. **A scenario returns a new state and never mutates the one it was given**,
    its identity is derived from content, and a shock the state cannot express
    raises rather than being skipped. Historical scenarios ship as contracts
    requiring supplied data; **no historical observation is invented.**

29. **A market convention is declared, never defaulted** (v3.4, ADR-0039).
    `MarketConvention` has no default on any field, and the six that had been
    defaulted to one market's value — a futures currency, an option multiplier
    and exercise style, a funding interval, a crypto contract size — are
    required. `test_v34_invariants.py` sweeps dataclass fields as well as
    function parameters, and each surviving default is listed with the reason a
    wrong value there cannot produce a number.
30. **A multiplier is multiplied in exactly one place.**
    `conventions.market.contract_notional`. `Position` carries no multiplier and
    never will. A regression test reads every module's source to keep a second
    site from appearing.
31. **`alphalab.conventions` imports `alphalab.common` and nothing else in
    `alphalab`.** The graph runs `data → options → portfolio`; an edge into any
    of those would close a package cycle and make the package unusable from the
    layers that need it. A calendar is reached through a one-method structural
    protocol, never an import.
32. **A continuous futures series is reproducible from four stated things** —
    chain, roll policy, observations, adjustment method — and nothing else. A
    roll rule refuses the input it needs rather than approximating it from
    another, and a missing print at a roll raises rather than being
    interpolated.
33. **An implied volatility that is not identifiable is refused**, in five named
    cases, and `surface_from_chain` accounts for every contract in the chain as
    either a point or a refusal with its reason.
34. **A rate dated after the instant it is read at is a look-ahead**, refused by
    `FxRates.convert`. `max_age_seconds` bounds the other direction. A cross
    rate is derived only on request and only through a named currency.
35. **A strategy fingerprint is derived from declared inputs and nothing
    environmental** (v3.6, ADR-0041). Code, dependencies with their stated
    completeness, parameters read from the registered version, research
    configuration and engine — never a mode, broker, venue, deployment,
    dataset, clock or path, and never the registration number. Every
    caller-supplied string and number is rendered with `repr`.
36. **A result's identity is the digest of its complete canonical record, and a
    reproducibility claim needs the seed.** An unseeded run is refused rather
    than called reproducible; an absent study seed is recorded, never replaced;
    a dataset whose provenance records no bytes is refused, and verifies no
    declared version in a certification.
37. **Certification derives every verdict from observed evidence** and accepts
    none. `NOT_ASSESSED` and `INSUFFICIENT_EVIDENCE` are never `PASS`, there is
    no overall score, and every assessment states what it does not establish.
38. **Portability adapts nothing.** A missing capability is a named blocker, an
    undeclared one is never satisfied, and the fingerprint evaluated is the one
    supplied.

39. **A record of unknown availability is never visible** (v3.7, ADR-0042). It
    is counted, never read, and never assumed available at its observation
    instant. Visibility is inclusive, asked under a named rule, and an effective
    date never makes a fact visible before it was known.
40. **A revision is a vintage, never an edit**, and no query reads a later
    revision at an earlier instant. `VintagePolicy` has `AS_KNOWN` and
    `ORIGINAL` and nothing else; hindsight has one named home, `restatements`.
41. **`alphalab.alt_data` imports `alphalab.common` and nothing else in
    `alphalab`**, and the research layer reads it and never `alphalab.data`.
42. **Availability is declared at ingestion, by an explicit rule** — a column, a
    stated lag, the next session open after a stated publication, or nothing
    (`UNKNOWN`). There is no default rule, calendar, staleness bound or vintage
    policy.
43. **A knowledge frame is not a forward fill**, and a factor built from
    external information meets its returns through `align_prices` — a checked
    join — never through a loosened dataset guard.
44. **An event is anchored where it could first be traded**: the first
    observation at or after the first session open at or after the instant it
    became knowable. A correction is not a second event, and no significance
    test is reported.
45. **A regime label reads only its past**, a definition's persistence is part
    of its identity, and resuming from a recorded state reproduces one pass
    exactly. AlphaLab ships no regime taxonomy.
46. **Adaptive state moves only through `apply_update`**, which is pure; a late
    observation is refused and included only by reprocessing from a checkpoint;
    a checkpoint whose identity does not recompute is refused; and the learned
    state reaches the run snapshot and its digest.

47. **One covariance authority** (v3.8, ADR-0043). A covariance is estimated by
    `common.statistics.sample_covariance`, applied pairwise in one loop in
    `analytics.risk_model`; every consumer reads a `CovarianceMatrix` and checks
    its currency and period, refusing a mismatch rather than rescaling.
48. **Nothing is constructed on a covariance that is not positive definite**,
    and nothing regularizes one on its own initiative: a ridge or a shrinkage is
    a derivation with its own identity that names its parent.
49. **A construction carries weights only when it is certified optimal.**
    `INFEASIBLE` names the conflict, `ITERATION_LIMIT` and `NUMERICAL_FAILURE`
    say why, and none of them carries a point; no constraint is relaxed and no
    fallback objective is tried. No risk aversion, `τ`, view confidence, market
    portfolio, tolerance or shrinkage intensity has a default.
50. **Absent is not zero in the risk model.** Factor loadings are a full
    rectangle; an asset without a classification is refused, never put in an
    "unknown" bucket.
51. **Every risk-budget dimension partitions the same exposure lines** and sums
    to the portfolio's volatility; a breach is reported, never enforced.
52. **A multi-strategy book never merges its sleeves.** Every aggregate carries
    each strategy's `StrategyContribution`, crossed quantity is reported rather
    than netted away, and `PortfolioState` stays the one book of record.
53. **Capital is allocated in the currency the account holds, and every account
    reconciles exactly**: `available = reserved + allocated + unallocated`. An
    oversubscribed account refuses the plan unless `PRO_RATA` is stated and
    recorded; a limit is never met by scaling; a broker is an identifier.
54. **v3.3's published decomposition numbers do not move.** They are
    recomputed with the v3.3 expressions and compared float for float.
55. **`analytics` imports only `common` and `core`, `allocation` never imports
    `portfolio`, and `portfolio_optimizer` imports only `common`, `analytics`
    and — since v3.11, to round to lots — `conventions`, which reads only
    `common`.** (This line named the first two until v3.13.)

56. **One statement of the order lifecycle** (v3.9, ADR-0044).
    `core.lifecycle.ORDER_TRANSITIONS` is read by the OMS and by the venue
    boundary; neither keeps rules of its own. Terminal statuses are absorbing,
    and a reported status is a transition, a duplicate, a stale report or a
    conflict — decided by the table's shape, never by extra rows.
57. **Silence is never support.** A capability nobody declared is `UNDECLARED`,
    a check that meets one is `UNDETERMINED`, and nothing `UNDETERMINED` is sent
    or projected as either answer.
58. **Every venue report gets exactly one outcome, and only `APPLIED` changes
    the mirror.** Applying an event twice is a duplicate; the quantities decide a
    fill, not its name; nothing is appended to `BrokerState.events`, and the
    broker snapshot schema does not move for the execution contract.
59. **A retry is not a second request.** A cancel or an amendment is identified
    by its content and its place in a sequence, never by when it was sent.
60. **A child order belongs to its parent.** It carries the parent's strategy
    contributions unchanged, is sent as a venue order for the parent, and
    settles on the parent; `ExternalOrderMap` stays one-to-one.
61. **A routing decision and an execution measurement are functions of their
    supplied evidence.** No discovery, clock or conversion inside either; a
    measurement whose evidence is missing is `None` with the reason, and money
    is never summed across currencies without a supplied, recorded rate.
62. **No vendor inside the contract.** AlphaLab defines what an adapter meets;
    it names no venue, holds no credential and defines no vendor message
    structure.

63. **A risk limit is judged on the projected book, and never refuses a
    reduction** (v3.10, ADR-0045). One projection — the portfolio's signed
    position, the order, the working orders — feeds every check; a breach
    refuses only what grows exposure.
64. **A currency's minor unit is the currency's.** ISO 4217 or a declaration,
    never a constant and never a guess; prices and quantities are exact; money
    is rounded once, in `ACCOUNTING_CONTEXT`.
65. **A statistic says how it was annualized, and an undefined one is
    `None`.** Returns are taken per instant; periods per year are declared or
    observed, never assumed.
66. **A bar is stamped at the end of its interval**, and a source says which end
    it stamps or is refused.
67. **The canonical path is linear in the universe.** A market event re-marks
    what it priced and what a fill priced since its last mark; valuation reads
    exact totals. `test_universe_scaling.py` measures it and fails on each of
    six injected regressions.
68. **An older payload is upgraded by an explicit step or refused with a
    reason** — never decoded by guesswork, never silently defaulted.
69. **No silent default for a currency, a rate, a zone, an annualization or a
    seed.** A default of zero is a default; a literal at a call site is a
    default; each exemption states its reason (`test_no_silent_financial_defaults.py`).
70. **A provider and a venue are the application's.** AlphaLab ships transports,
    the wire records and one method a provider implements
    (`BarHistoryProvider.request_history`); no vendor client and no exchange
    symbol spelling.

71. **One name, one contract** (v3.13, ADR-0048). A public name bound to two
    different objects is renamed — never aliased — merged, or kept with its
    reason in `docs/api/public_api.json`, which records every export and is
    regenerated with each release; a removed or rebound name is refused until
    the release's CHANGELOG section names it.
72. **A persisted enum's class and member names are part of the format.** A
    rename is a schema step with an upgrade, never a refactoring (PER-004).
73. **A price states its model, and a method states where it is exact.** A
    lattice price names its step count; a surface is read between expiries only
    by name and never extrapolated; the optimal split refuses a cost it cannot
    call optimal; a model's parameters — impact, risk aversion, a seed — are the
    caller's, with no default.
74. **What is not done is classified.** Every limitation and deferral an ADR
    states maps to a closed ledger entry, every test the ledger cites exists,
    and neither the release certificate's evidence nor the engine source it
    names moves unless the certificate is regenerated.

The decisions ADR-0046 and ADR-0047 record for v3.11 and v3.12 are frozen with
these; each is pinned by the regression test its ledger entry cites.

## The failure mode to watch for

The most expensive defects in AlphaLab's history were not unknown problems. They
were **known problems fixed unevenly** — a release naming a defect class
correctly and closing only some of its instances. ADR-0031 wrote "every
construction site in the repository passes `object()`" while fifteen remained on
four other fields of the same object. When you fix a class of defect, sweep for
the whole class, mechanically.

The second most expensive: **a document that was true when written and was never
re-read.** Hence section 13's release checklist and
`docs/ENGINEERING_GUIDELINES.md`'s note that the current-state claim lives in
four documents (the version, which lived in three places, is declared once
since v3.10).

v3.2 produced an instance worth recording. `docs/ARCHITECTURE.md` listed
`factor_library` among the packages "reached by **neither** wired path". That
was true at v3.1 and became false the moment `alphalab.research` imported it —
a one-line change in a different file, in a different package, with nothing
connecting the two but a sentence. The fix was not only to correct the sentence
but to measure it: `test_one_research_authority_per_concept.py` now asserts that
the computation engine *has* importers and that `feature_store` still does not.
A claim about the import graph belongs in a test that reads the import graph.

---

# 15. Deliberate design — B

Not defects. Each is a decision with a reason and, in most cases, a test that a
future "unification" must break first.

| Kept apart | Why |
| --- | --- |
| `oms.book.OrderBook` / `data.feed.OrderBook` | *My* working orders vs *the market's* resting size. They share no operation. Merging is a category error |
| `portfolio.PortfolioEngine` / `portfolio_optimizer.PortfolioEngine` | Accounting vs construction. Only the first is reachable from the execution path |
| `research.parameter_sweep` / `portfolio_optimizer` | Parameter search and weights: one home each since v3.12 removed `optimizer` |
| `broker` / `brokers` | One venue vs many venues and many accounts. Converged in v2.3; the connector routes the canonical types under their canonical names (its historical aliases were removed in v3.13, API-001) |
| `data.feed.Bar` / `market.bar.Bar` | Wire vs domain, opposite sides of one conversion |
| Three things called a venue | Listing exchange, market-data attribution, execution venue. None derives from another |
| `strategy.StrategyStatus` / `lifecycle.LifecycleState` | A strategy *instance's* stage vs the lifecycle registry. They shared the name `LifecycleState` until v3.13 (API-001) |
| `strategy.RuntimeState` | Holds strategy instances. Not a runtime-package state |
| Two matrix inversions | Different input classes; neither is a shared numerical layer |
| `AppendOnlyLog` batch operations keeping local copies | One copy in and one value out is O(collection) per *call*, not per element; writing each element through `PersistentMap.set` measured ~30% worse |
| Five content identities | A deployment's needs, a measurement, an experiment, a strategy version, a result's inputs. Each answers a question the others cannot (v3.6) |
| `evaluate_policy` / `validate_specification` / `certify_strategy` | A promotion gate, a coherence check, and eight properties with no verdict (v3.6) |
| Environment parity / portability | A property of AlphaLab (one strategy path everywhere) vs a property of a strategy against a declared environment (v3.6) |
| `BaseEvent` / `InformationEvent` | A state changed inside AlphaLab vs something happened in the world (v3.7) |
| `analyze_regimes` / `classify_regimes` / `FeatureKind.VOLATILITY_REGIME` | A score over a return series vs a declared-rule detector vs a feature a detector can read (v3.7) |
| `DatasetProvenance` / `DataProvenance` / `ObservationSource` | How a market series came to exist vs a vendor's quality vs a source's identity and bytes (v3.7) |
| `data.feed.FundamentalRecord` / `FundamentalObservation` / `FundamentalSnapshot` | A one-timestamp wire record vs a statement figure with its instants vs a factor input produced at an instant (v3.7) |
| `known_as_of` / `PointInTimeIndex` / `DataRequest.as_of` | A release date trusted vs a knowledge instant that can be unknown vs a timestamp cut on wire records (v3.7) |
| `portfolio_optimizer.CapitalAllocation` / `allocation.capital` / `CapitalBudget` | A float snapshot of one portfolio's capital vs capital divided between runs in each account's currency vs one run's ceiling (v3.8) |
| `CapitalBudget` / `risk.RiskLimits` / `RiskBudget` (with `BudgetLimit`) / `RiskConstraints` | Capital a run deploys vs what an order may be vs where a finished book's volatility comes from vs v1's post-construction checks (v3.8) |
| `portfolio_optimizer.TargetWeights` / `allocation.PlacementWeights` | A portfolio's asset weights vs a capital plan's fraction per placement, named apart before release (v3.8) |
| `apply_weight_constraints` / `construct` | A projection after the fact vs an optimization over the constraints; with a cap the projection is feasible and worse (v3.8) |
| `CorrelationMatrix` / `ExposureSimilarity` | A correlation of returns with its currency, period and sample vs a comparison of holdings with neither (v3.8) |
| Three factor exposures | v3.3's gross-weighted book exposure and v3.8's model exposure are one arithmetic; `factor_library.factor_exposure` is a mean weighting over instants (v3.8) |
| Two Cholesky factorizations | The risk model's pivoted, rank-revealing diagnosis vs the solver's natural-order factor of the Hessian it is handed (v3.8) |
| `BrokerCapabilities` / `CapabilityDeclaration` | A deployment's broker-wide, two-valued summary vs a venue's scoped, three-valued declaration; the first is projected from the second (v3.9) |
| `ExecutionReport.slippage` and `ExecutionCosts` / `measure_slippage` and `implementation_shortfall` | What a cost model assumed for a fill vs what an order achieved against a named reference (v3.9) |
| `select_route` / `route_order` and `route_child_order` | Choosing where vs sending; connecting is an adapter's (v3.9) |
| `broker.reconcile` / `reconcile_snapshot` / `reconcile_execution_state` | The mirror against loose venue records vs against a dated snapshot vs the book against the mirror (v3.9) |
| `ChildOrder` / `oms.order.Order` | An instruction an algorithm released for a parent vs the one lifecycle order the OMS holds (v3.9) |

Also deliberate: **no CLI, no server, no daemon, no event bus, no composition
root, no `SettlementPolicy` object, no per-environment promotion policy, no
supervised live process, no authentication, no marketplace logic, no overall
certification score, no dependency resolver or environment snapshot, no regime
taxonomy and no estimated regime model, no fitted half-life, no derived
alignment, no per-strategy sub-ledger, no p-value in an event study, no default
availability rule, no silent scaling of capital, no enforcement in a risk
budget, no broker adapter in capital allocation, no capability discovery, no
vendor message structure, no retry of a venue's refusal, no conversion inside a
routing decision, no best-execution claim.** Each is a NON-GOAL with a recorded
reason.

Two items this list carried until v3.10 are corrected rather than kept: **"no
migration framework"** was replaced by versioned schema upgrades (ADR-0045), and
**"no credential handling"** was not true — the HMAC venue transport holds an
API key and signing secret (`broker.transport.VenueCredentials`) and
`enterprise` kept secret references. Both left the library in v3.11 (BRK-007,
BND-002).

---

# 16. External dependencies — C

Real, and **not AlphaLab's engineering to complete.** None of these is unfinished
internal work.

| | Status |
| --- | --- |
| **Verification against a commercial venue** | EXTERNAL. The transports are written to protocol and exercised end to end over real sockets against local servers that verify signatures, timestamp windows, idempotency keys and accept tokens. This environment has no network egress and holds no vendor credentials |
| **Named vendor request shapes** | EXTERNAL. Each differs per venue and belongs to an adapter |
| **FX data** | EXTERNAL. v2.17 ships the rate-feed boundary and not a single rate |
| **Classification data** | EXTERNAL. v2.11 ships the mechanism and v2.15 its provenance; no taxonomy and no reference-data feed. v3.8's risk budgets and construction read any caller classification with a named source — a country file, say — and AlphaLab holds none |
| **What a rerun needs** | EXTERNAL. A reproducibility manifest identifies the dataset bytes, the strategy code, the dependency set, the engine and the live objects a run records by type; AlphaLab stores none of them (v3.6) |
| **What an environment offers** | EXTERNAL. Every `TargetEnvironment`, runtime observation and resource measurement is declared by whoever knows it (v3.6) |
| **Alternative data, events and fundamentals** | EXTERNAL. v3.7 ships the point-in-time contract — records, sets, ingestion rules, queries — and no vendor, feed, file, line-item taxonomy or price. The rows and their bytes are the caller's |
| **Portfolio and risk inputs** | EXTERNAL. v3.8 estimates a sample covariance and nothing else: expected-return forecasts, market values for an equilibrium prior, views and their confidence, factor data, FX rates, account balances, and the mapping from a broker or account identifier to an adapter and its credentials are the caller's |
| **Venues** | EXTERNAL. v3.9 ships the execution contract and not one venue: a real venue's capability declaration, its quotes, volume profiles, printed volume and benchmark prices, and the adapter that translates its messages into `VenueEvent`s are the application's |

The honest summary of connectivity: **the connectivity exists; the vendor
integration does not.**

---

# 17. Planned before v4 — D (was "optional future evolution")

Until v3.10 this section listed what could be built with no commitment. The
pre-v4 audit re-classified every item; the ledger
(`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`) holds each with its disposition
and release, and `ROADMAP.md` has the table. **Nothing is planned any more:
every item was delivered by v3.13 or is kept, with its reason.** In short:

- **Delivered in v3.11**: richer construction (OFE-002); multi-exposure
  neutralization (OFE-004); a deflated Sharpe ratio and corrections beyond
  Bonferroni (OFE-005); an overlap-corrected IC t-statistic (OFE-006);
  pipeline-driven `on_fill` / `on_order` / `on_timer` (OFE-014); a venue
  sequence number and persisted child bindings (OFE-021, OFE-022).
- **Delivered in v3.12**: classification beyond sector and limits on its
  buckets (OFE-001); per-strategy capital ceilings (OFE-003); execution-path
  delivery of external information (OFE-009); streaming observation sets and
  adjusted fundamentals (OFE-011); durable evidence for progressions,
  fingerprints, manifests and reports (OFE-016); health over a window
  (OFE-017); cross-broker book-to-mirror reconciliation (OFE-023); the
  optimizer's `pending_trials`, removed with the optimizer rather than patched
  (OFE-013, SCF-003).
- **Delivered in v3.13**: a lock-file reader (OFE-019); a rerun harness
  (OFE-020); an optimal split, estimated urgency and randomized icebergs
  (OFE-024, OFE-025); and, from the ledger's v4.0 column, the shared names,
  the public API manifest, the persisted-name contract and release
  certification (API-001, API-002, PER-004, FEA-006).
- **Kept as boundaries**: statistical regime models (OFE-010); a fitted
  half-life and derived alignment (OFE-007, OFE-018); per-strategy sub-ledgers
  (OFE-015); hook timeouts, plugin analysis, hot reload and a threading model
  (OFE-026) — see section 15.
- **External**: a vendor adapter package (OFE-008).
- **Done**: the two duplicate `AssetClass` enums were removed with `live` and
  `marketdata`'s provider engine in v3.10 (OFE-012).

---

# 18. Release history

| | |
| --- | --- |
| v1.0.0 | Architectural foundation |
| v1.34.0 – v1.46.0 | The engine series (13 standalone packages) |
| v2.0.0 | Canonical execution domain models; four more packages |
| v2.1.0 – v2.5.0 | Correctness and integration: mark-to-market, the exact identity, unified backtest/replay, the market-data and broker convergence, the lifecycle, typed state round-trip |
| v2.6.0 – v2.13.0 | Authority and durability: allocation authority, instrument identity, currency roles, identifier continuation, the strategy boundary, classification, the currency authority, the run-state store |
| v2.14.0 – v2.17.0 | Unification and completion: one `RunEngine`, real transports, the live driver and governance and FX, then settlement multi-currency and the final cleanup |
| **v3.0.0** | **Architecture frozen. Documentation true. No capability added** |
| **v3.1.0** | **Universal data ingestion: CSV, detection, validation, cleaning policy, calendars, provenance, the derived dataset version (ADR-0036)** |
| **v3.2.0** | **Strategy research and validation: typed features with derived identity and lineage, factor research, signal diagnostics, walk-forward, purged and embargoed CV, seeded robustness, transparent overfitting diagnostics, the reproducible study contract (ADR-0037)** |
| **v3.3.0** | **Institutional backtesting and portfolio intelligence: itemized execution costs, capacity modelling, nine-dimension attribution, risk decomposition with named VaR methodology, the reusable scenario/stress contract (ADR-0038)** |
| **v3.4.0** | **Global markets and multi-asset research: one market-convention authority as a leaf over `common`, reproducible continuous futures, implied volatility with five refusals, cross rates and the FX look-ahead guard, crypto venue metadata and 24/7 coverage, a fixed-income foundation, and six US/Binance defaults made required (ADR-0039)** |
| **v3.5.0** | **Strategy execution and production intelligence: the research-to-live progression as a third axis, deployment specifications with a derived identity, structured runtime health from supplied observations, the expected/paper/live comparison, and AlphaLab-to-broker reconciliation — all inside `alphalab.lifecycle`, with no package added and no snapshot schema touched (ADR-0040)** |
| **v3.6.0** | **Strategy evaluation: immutable strategy fingerprints, reproducibility manifests with four separate answers, eight certification properties from observed evidence with no score, and portability against declared capabilities — all inside `alphalab.lifecycle`, with no package added, no durable state and no marketplace logic (ADR-0041)** |
| **v3.7.0** | **Advanced quant research: a point-in-time statement of when information became knowable; canonical events, alternative data with source identity and versioned sets, and point-in-time fundamentals in `alt_data`, now a leaf over `common`; knowledge frames with a checked price join; event studies anchored where news could be traded; regime detection from declared rules; and adaptive strategies whose learned state replays exactly and reaches the run record — no package added, no snapshot schema touched (ADR-0042)** |
| **v3.8.0** | **Advanced portfolio and risk: the risk model as values with identities; constrained construction — minimum variance, mean-variance, maximum diversification, risk parity, robust — by one certified solver that names conflicts, and Black–Litterman; risk budgets along five dimensions; multi-strategy books across currencies; cross-strategy risk; and capital allocation across strategies, markets, brokers, accounts and currencies — no package added, three edges measured, no snapshot schema touched (ADR-0043)** |
| **v3.9.0** | **The universal execution contract: capabilities declared at venue, market and account level and checked three-valued; one order-transition table for the OMS and the venue, every venue report given one outcome, idempotent cancels and amendments; TWAP, VWAP, participation, slicing and iceberg-like algorithms whose children stay their parent's; explained routing from supplied evidence; execution analytics; snapshot reconciliation — no package or edge added, no snapshot schema touched (ADR-0044)** |
| **v3.10.0** | **The first pre-v4 release: risk on the projected book, never refusing a reduction; money exact at each currency's minor unit; analytics per instant with stated annualization; next-event fills and recorded execution assumptions; a canonical path linear in the universe; bars stamped at their close; upgradeable snapshots (portfolio 4, pipeline 4, run 2); vendor code, `feed`, `live` and silent defaults removed (ADR-0045)** |
| **v3.11.0** | **The second pre-v4 release: instrument economics, corporate actions and negative prices; order terms and resting orders; target positions against each strategy's own position; enforced subscriptions, slices and feedback; leak-proof research, walk-forward optimization and multiple-testing corrections; construction with costs and lots; `studio`, `workbench`, `enterprise` and venue credentials moved to the application; snapshots pipeline 5, run 3, portfolio 5 (ADR-0046)** |
| **v3.12.0** | **The third pre-v4 release: numerics right at the edges of their range; durable writes and exact restores; calendars inside simulation; strategy capital ceilings; classification limits along any dimension; external information on the execution path; retention and incremental checkpoints; an evidence store; multi-account reconciliation; declared trade prints; LSTM and attention backpropagation; factor-structured construction; a stress program at 10,000 assets, 1,000 strategies and 100 venues; snapshots pipeline 6, run 4, allocation 3, instrument 3 (ADR-0047)** |
| **v3.13.0** | **The final pre-v4 release: American options and a volatility term structure; the optimal split; an estimated urgency, the shortfall its model expects and seeded iceberg tranches; a rerun from a manifest and a lock-file reader; cron timers; an exact liquidation price; exchange-rate risk as factors; a factor model constructed over by its structure; checkpoint segments that carry only what changed; one name for one contract, the public API and persisted names as data, every ADR limitation held to the ledger, and a release certificate; snapshots pipeline 7, checkpoint 2 (ADR-0048)** |

48 ADRs, in `docs/ADR/`. Every supersession is stated explicitly in the
superseding ADR's Status block; read the Status block first.

---

# 19. How to extend this safely

**Adding a standalone engine.** Follow the existing shape: `__init__.py` with an
honest `__all__`, `engine.py`, `state.py`, `events.py`, `validation.py`,
`exceptions.py`, `views.py`. Immutable state, pure functions, its own tests under
`tests/unit/<package>/` and its own benchmark. It will have no in-repo consumer,
and that is correct.

**Adding a vendor adapter.** Implement `BrokerProtocol` (one venue) or a
`MarketDataSource` in your own package, and normalize into the canonical types on
the way out. Nothing above the boundary learns which vendor it was. Since v3.9 a
broker adapter also declares its venue's capabilities as a
`CapabilityDeclaration` (every answer it knows, and `UNDECLARED` for the rest),
translates every message into a `VenueEvent` of the twelve normalized kinds, and
delivers amendments, positions and balances in venue order; everything else —
what each event means, idempotency, reconciliation — is the contract's.

**Adding an execution algorithm.** State its objective, inputs, sizing, timing
and completion rule in its docstring; read time and volume only from arguments;
compute in the module's pinned decimal context; release by topping up to a
target; carry the parent's contributions on every child; and render every field
into its `configuration_id`. `test_v39_invariants.py` sweeps for clocks,
transcendental floats and ambient decimal contexts.

**Adding a strategy.** Implement `StrategyProtocol` or subclass `BaseStrategy`.
Return `Intent`s; never place orders. If it holds durable internal state,
implement `StrategyStateProtocol` too — both directions of the codec, because the
shared encoder writes a `Decimal` and a `str` identically and no generic decoder
can tell them apart afterwards.

**Adding a kind of external information.** Do not add a class: an
`ExternalObservation` with a new dotted category and metric, or an
`InformationEvent` with a new `event_type`, is the whole change. Ingest it through
`alphalab.api` with the availability rule the source actually supports — and
`AvailabilityNotDeclared` when it supports none, which is honest and makes the
data unusable for research until somebody establishes when it was knowable.

**Adding a construction method.** Add an objective type to
`portfolio_optimizer.construction` with a `rendering` that enters the problem's
identity, solve it through `quadratic.solve_quadratic_program` if it is a
quadratic program, and check it against the exact-rational reference in
`tests/unit/portfolio_optimizer/qp_reference.py`. Do not add a second solver or a
fallback: an answer the certificate cannot verify is not an answer.

**Adding a capital rule or dimension.** A rule is a frozen value the plan's
identity renders; allocate in the account's currency, convert only plan-wide
figures through the `CurrencyConverter`, and keep
`available = reserved + allocated + unallocated` exact — the invariant test
checks it on generated plans.

**Adding an adaptive rule.** Implement the four functions of `AdaptiveRule`,
keep everything the rule knows in the payload it is handed, and replay it twice:
`assess_adaptive_replay` reports `DIVERGED` if anything leaked outside.

**Changing something on the execution path.** Read the relevant ADR first, then
the regression test that pins it. If your change requires breaking an assertion
in `test_shared_names_stay_distinct.py` or `test_venue_concepts_stay_distinct.py`,
stop and read the reason in that test's docstring — it was written for you.

**Before any release**, run the full checklist in
`docs/ENGINEERING_GUIDELINES.md`. The current-state claim lives in four
documents and has drifted before; the version is declared once, in
`alphalab/common/_version.py`, and every document that restates it is held to
it by a test. Regenerate the API manifest (`docs/api/generate_public_api.py`)
and, last, the release certificate (`docs/audit/scripts/certify_release.py`).

---

# 20. Open questions

Genuinely unresolved, recorded so they are not rediscovered:

- **UNKNOWN: how the transports behave against a real venue.** They are correct
  against the protocols as written and against local servers that verify the hard
  parts. Whether a commercial venue's quirks break them cannot be known from here.
- **UNKNOWN: whether the linear scaling holds at a workload far beyond what is
  measured.** The stress programs (v3.12, re-run in v3.13) measure the canonical
  path at 10,000 assets, 1,000 strategies and 100 venues, checkpoint chains of
  20,000 records and construction at 10,000 assets; beyond that is
  extrapolation.
- **UNKNOWN: whether the single-threaded model is sufficient** for a deployment
  running many strategies at high event rates. The design anticipated sharding;
  nothing was built, and nothing has needed it.
- **FIXED in v3.10 (KD-001–003): the three pre-trade risk defects found in
  v3.6** — the position check comparing different units, the daily loss limit
  never maintained, the net exposure limit read by nothing. The known defects
  that remain are in the pre-v4 ledger and in `ROADMAP.md`, each with a release.
- **FIXED in v3.10 (TST-001): the timing backstops that timed the wall clock
  with the collector running.** Every complexity guard now uses the stabilized
  method. The history, kept because it explains the method: Found in v3.7 while fixing
  `test_lifecycle_registry_complexity.py`, which now times CPU time with the
  collector off and its two sizes interleaved (its `_timings` docstring holds
  the measurements). Run under load with a heap the size of the suite's, the
  others can fail on a linear implementation: `test_standalone_state_scaling.py`,
  which takes a single sample per size, failed 1 of 10 runs on a quiet machine
  and 6 of 10 with every core busy; `test_v37_complexity.py` 1 of 10 with every
  core busy; `test_research_complexity.py`, `test_replay_cursor_complexity.py`
  and `test_v34_complexity.py` through `test_v36_complexity.py` only when the
  machine was oversubscribed. Moving them to the same method is mechanical.
  Until then a failure in one of them is re-run once before it is read as a
  regression; one that reproduces is real. v3.8's `test_v38_complexity.py` uses
  the stabilized method from the start, and passed 30 of 30 comparisons with
  every core busy; v3.9's `test_v39_complexity.py` imports the same method and
  passed 5 of 5 runs with eight CPU-bound processes competing.
- **MEASURED since v3.12: how far the pure-Python construction solver
  scales.** The dense solver is cubic — 135 s at 800 assets. A factor model is
  solved in O(n k²) per step (v3.12), and since v3.13 stated by its
  structure and never written out (PRF-013): 10,000 assets through the public
  path, measured in the stress program.
- **UNKNOWN: how real venues order amendments, positions and balances relative
  to fills.** The execution contract orders status events by the lifecycle and
  fills by addition, and leaves absolute reports to the adapter's delivery
  order, with a snapshot reconciliation as the check (v3.9). Whether an
  adapter can always deliver them in venue order depends on the venue.
- **KNOWN CAVEAT: `ingest_rows` identifies what its caller's source says.** Rows
  recorded with an empty payload share one dataset version whatever they
  contain. A reproducibility manifest refuses such a dataset and a
  certification does not count it as verified data (v3.6); the ingestion
  contract is unchanged.

---

*Written at v3.0.0 and updated at each release since, through v3.13.0. At
v3.13.0 its identity table was found still reading 3.9.0 (DOC-008), and
`tests/regression/test_version_markers_agree.py` now holds it to the code. If you
are reading this long after, check the version in `alphalab/common/_version.py`
first: where this document and the code disagree, the code is right, and this
document has a bug worth fixing.*
