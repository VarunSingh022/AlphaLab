<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-3.6.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-5669%20Passing-success)]()
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
- **The lifecycle path.** `alphalab.lifecycle` composes experiment tracking, the model registry, the deployment manager, `studio`'s strategy definitions, `enterprise`'s RBAC and `research`/`backtesting`'s reports into one flow: research candidate → experiment run → validation evidence → model version → strategy version → promotion → deployment → rollback. Every act that changes what is live names its principal. As of v2.16 it is **joined** to the execution path: `run_plan` resolves what an environment has live and `authorize_run` refuses a run that would serve anything else. As of v2.17 `alphalab.strategy.registry` supplies the other half of that join — the identity a deployment names, mapped to the code a run executes. As of v3.5 the same package carries past the deployment record into the thing a deployment becomes: a strategy's progression from research to live money, the specification of what it needs to run as it was researched, structured runtime health from supplied observations, an expected/paper/live comparison, and deterministic reconciliation against a normalized broker state. As of v3.6 it also makes a strategy version evaluable by somebody else: an immutable fingerprint of its code, dependencies, parameters, research configuration and engine; a manifest from which a result can be recreated; machine-verifiable certification properties, each with its evidence; and a portability check against declared environment capabilities.
- **Standalone engine libraries.** The remaining packages (portfolio optimizer, reporting, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, workbench, and the rest) are independent, deterministic, individually tested libraries reached by neither path. They share the engineering model and are **not** fused into a single runtime. That is a decision, not a gap — see ADR-0009.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v3.6.0 — strategy evaluation and research-marketplace infrastructure, on the v3.0 frozen architecture**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 3.6.0 |
| Runtime dependencies | **None** (standard library only) |
| Tests | **5669 Passing, 0 skipped, 0 warnings** |
| Static Typing | **Strict MyPy** (1080 source files, repository-wide) |
| Linting | **Ruff Clean** |
| Benchmarks | **55 / 55 Passing** |
| Examples | **49 / 49 Passing** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

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

> **What connectivity means here.** `alphalab.broker.transport.HttpVenueTransport`
> signs and sends orders over authenticated HTTP, `alphalab.broker.venue.RestVenueBroker`
> is a full `BrokerProtocol` over it, `alphalab.market.stream.StreamingSource`
> consumes a push feed through an RFC 6455 WebSocket client, and
> `alphalab.runtime.live.LiveSession` drives the settle/advance/route cycle. All of
> it is exercised end to end over real sockets against local servers that verify
> signatures, timestamp windows, idempotency keys and WebSocket accept tokens.
>
> What is **not** here: verification against any commercial venue — this
> environment has no network egress and holds no vendor credentials — and any
> named vendor's request shapes. Read the transports as written-to-protocol and
> unverified-against-a-vendor, which is what their own docstrings say. See
> `docs/ADR/0012-broker-boundary-and-environment-parity.md` and
> `docs/ADR/0031-real-transport-streaming-artifacts-and-the-completed-boundaries.md`.
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
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market.normalization import NormalizationPolicy

instruments = register_instrument(
    InstrumentRegistry(),
    InstrumentRecord(
        "BTCUSDT", AssetType.CRYPTO, "BINANCE", "USDT", aliases={"binance": "BTCUSDT"}
    ),
)
policy = NormalizationPolicy(
    venue="BINANCE", currency="USDT", identity=instruments, provider="binance"
)
```

**A production provider → execution path requires `InstrumentRegistry`-backed
resolution.** An unregistered `(provider, symbol)` is refused at the boundary
with `InstrumentResolutionError`, naming both — not carried onward to fail at the
first fill.

The second mode, `UnresolvedIdentity`, keeps the wire → canonical lift testable
without a registry. **It is not a production execution configuration**: it yields
provider symbols, which `core.Fill` and `core.Trade` refuse, and
`ProviderHistorySource.of` rejects it before calling the provider. `DEFAULT_POLICY`
uses this mode and is therefore a testing default. See ADR-0016.

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

## Standalone engine libraries

Everything below is importable, deterministic, and independently tested, but is
**not** wired into `ExecutionPipeline` or into the lifecycle path:

| Area | Packages |
|---|---|
| Reporting | `reporting` |
| Portfolio construction | `portfolio_optimizer`, `optimizer` |
| Features & factors | `feature_store`, `factor_library`, `alt_data` |
| Learning | `ml`, `deep_learning`, `reinforcement_learning` |
| Asset classes | `options`, `futures`, `crypto`, `macro` |
| Market conventions | `conventions` — a leaf over `common`, imported *by* `data`-side and `portfolio`-side packages rather than reached from a run |
| Scale-out | `cloud_research`, `cluster_scheduler`, `distributed` |
| Workflow | `workbench`, `research_assistant` |
| Provider surfaces | `live`, `feed`, `brokers` |
| Infrastructure | `plugins`, `scheduler` |

Several packages that are often described as standalone are **not**, and the
import graph is the authority:

- `data`, `marketdata`, `market`, `instrument`, `broker` and `persistence` are
  each reached from the execution path. `data.feed` and `marketdata.feed` supply
  the wire records `market.normalization` lifts; `marketdata` is consumed by
  `market`; `broker` is reached through `runtime.broker_routing`; `persistence`
  supplies the codec spine and the `RunStateStore` every snapshot owner writes
  through.
- `research`, `studio`, `enterprise`, `experiment_tracking`, `model_registry`,
  `deployment_manager` and `backtesting` are imported by `alphalab.lifecycle`.
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
| 02 | `02_backtest.py` | Strategy Studio backtest bookkeeping |
| 03 | `03_replay.py` | Historical replay cursor |
| 04 | `04_market_data.py` | Market-data providers |
| 05 | `05_broker_connection.py` | The two broker boundaries: one venue, or a registry of many |
| 06 | `06_portfolio_optimizer.py` | Portfolio construction |
| 07 | `07_universal_data.py` | Universal Data Engine: state, versions and the catalogue |
| 08 | `08_strategy_studio.py` | Strategy Studio orchestration |
| 09 | `09_workbench.py` | Workbench workspace |
| 10 | `10_complete_pipeline.py` | Multi-engine walkthrough |
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
markets, `41`–`45` (v3.5) strategy execution, and `46`–`49` (v3.6) strategy
evaluation. None are part of the automated test suite, though all forty-nine run
as a release gate; `17`–`24` all ingest the same committed panel in
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
| `docs/ADR/` | Architectural Decision Records — 41 of them |
| `docs/EXAMPLES.md` | Example walkthroughs |
| `docs/ENGINEERING_GUIDELINES.md` | Engineering standards |
| `nowandfuture.md` | The long-form project reference: ownership, invariants, boundaries, what must not change casually |
| `ROADMAP.md` | What is delivered, what is deliberate, what remains optional |

---

# Repository

```text
alphalab/
├── Execution spine (wired by runtime.ExecutionPipeline / runtime.run.RunEngine)
│   core/          Canonical domain models — Side, OrderRequest, Fill, Trade, ids
│   runtime/       ExecutionPipeline, RunEngine, drivers, broker routing, snapshots
│   strategy/      Strategy protocol, dispatcher, supervisor, class registry
│   allocation/    Intent sizing / netting → OrderRequest, reservations, contributions
│   risk/          Pre-trade risk checks and limits
│   oms/           Order lifecycle (oms.order.Order is canonical)
│   execution/     Deterministic execution simulator, commission, fill policies
│   portfolio/     Cash ledger, positions, NAV, per-currency P&L, FX, FX feed
│   analytics/     Performance report, attribution
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
│   studio/        Strategy definitions and project orchestration
│   enterprise/    RBAC, principals, audit log (governance reads it)
│   research/      Run evaluation, and the v3.2 study methodology
│   factor_library/  The computation engine: features, factors, diagnostics
│
├── Data surfaces
│   data/          Wire records, and the universal data engine:
│                  ingestion, schema detection, validation, cleaning,
│                  calendars, corporate actions, provenance, `api`
│   marketdata/    Provider clients, transport, WebSocket
│   feed/  live/   Standalone provider-surface engines
│
├── Broker surfaces
│   broker/        The canonical single-venue boundary (BrokerProtocol)
│   brokers/       The many-venue connector framework (BrokerConnectorProtocol)
│
└── Standalone engines
    reporting/  portfolio_optimizer/  optimizer/
    feature_store/  alt_data/
    ml/  deep_learning/  reinforcement_learning/
    options/  futures/  crypto/  macro/
    cloud_research/  cluster_scheduler/  distributed/
    workbench/  research_assistant/  plugins/  scheduler/
    scenario/       Price, volatility, FX and liquidity shocks (ADR-0038)
```

All 50 top-level packages are accounted for above.

Additional directories:

```text
docs/          Documentation and ADRs
examples/      49 runnable examples
benchmarks/    55 performance benchmarks
tests/         5669 tests — unit, integration, regression
configs/       Reference configuration files
```

---

# Quality Assurance

AlphaLab is continuously validated through automated tooling.

- ✅ **5669 passing tests** (2884 unit, 348 integration, 2437 regression) — **0 skipped, 0 warnings**
- ✅ Strict MyPy type checking, repository-wide exactly as CI runs it (`python -m mypy .`, 1080 source files)
- ✅ Ruff linting and formatting
- ✅ 55 / 55 benchmarks, 49 / 49 examples
- ✅ Source distribution, wheel and `twine check` validation

Neither the zero skips nor the zero warnings can be satisfied by configuration:
`tests/regression/test_the_suite_reports_nothing_deferred.py` reads the collected
items rather than the summary line, and spawns a **fresh interpreter** with
`-W error::DeprecationWarning` to import every module in the package tree.

---

# What is deliberate, what is external, what is optional

The v3.0 audit classified everything that remains. Nothing below is a defect.

## Deliberate design — pinned by regression tests

These look like duplication or a layer violation and are not. A future
"unification" has to break an assertion and read a reason first; the reasons live
in `tests/regression/test_shared_names_stay_distinct.py` and
`test_venue_concepts_stay_distinct.py`.

- **Two `OrderBook`s** — `oms.book.OrderBook` holds *my* working orders;
  `data.feed.OrderBook` is a venue depth snapshot. They share no operation.
- **Two `PortfolioEngine`s** — `portfolio` does accounting, `portfolio_optimizer`
  does construction. Only the accounting one is reachable from the execution path.
- **`optimizer` vs `portfolio_optimizer`** — two searches over two different
  subjects.
- **`broker` vs `brokers`** — one venue versus many venues and many accounts.
  The connector package routes the canonical types under its historical names;
  the identities are asserted.
- **A wire record and a domain record** — `data.feed` is `float`/`symbol`,
  `alphalab.market` is `Decimal`/`asset_id`. They sit on opposite sides of an
  explicit conversion (ADR-0011).
- **Three things called a venue** — listing exchange, market-data attribution and
  execution venue. None derives from another (ADR-0019).
- **Three calendar-shaped things** — `MarketCalendar` answers sessions,
  `scheduler.TradingCalendar` answers "should a job fire today", and v3.4's two
  one-method protocols are neither. There is one calendar with holidays,
  sessions and a timezone (ADR-0039).
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

## Optional future evolution — deliberately not built

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
- One measured trade left in place: `OptimizerState.pending_trials` stays
  super-linear because the fix was implemented, measured at **+3.9%** on the
  execution pipeline, and refused on that evidence. It is in a standalone package
  with no in-repo consumer.

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

**AlphaLab v3.6.0**

Building deterministic infrastructure for quantitative research.

</div>
