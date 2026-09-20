<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-3.3.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-4548%20Passing-success)]()
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
- **The lifecycle path.** `alphalab.lifecycle` composes experiment tracking, the model registry, the deployment manager, `studio`'s strategy definitions, `enterprise`'s RBAC and `research`/`backtesting`'s reports into one flow: research candidate → experiment run → validation evidence → model version → strategy version → promotion → deployment → rollback. Every act that changes what is live names its principal. As of v2.16 it is **joined** to the execution path: `run_plan` resolves what an environment has live and `authorize_run` refuses a run that would serve anything else. As of v2.17 `alphalab.strategy.registry` supplies the other half of that join — the identity a deployment names, mapped to the code a run executes.
- **Standalone engine libraries.** The remaining packages (portfolio optimizer, reporting, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, workbench, and the rest) are independent, deterministic, individually tested libraries reached by neither path. They share the engineering model and are **not** fused into a single runtime. That is a decision, not a gap — see ADR-0009.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v3.3.0 — institutional backtesting and portfolio intelligence, on the v3.0 frozen architecture**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 3.3.0 |
| Runtime dependencies | **None** (standard library only) |
| Tests | **4764 Passing, 0 skipped, 0 warnings** |
| Static Typing | **Strict MyPy** (999 source files) |
| Linting | **Ruff Clean** |
| Benchmarks | **51 / 51 Passing** |
| Examples | **30 / 30 Passing** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

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

Run any example:

```bash
python examples/01_research.py
```

Examples `01`–`10` date from v1.0.0 and exercise the standalone engine APIs;
`11` (v2.2) drives the integrated execution path, `12` (v2.4) the lifecycle path,
`13` (v2.13) durable run state across two processes, `14` (v2.17)
settlement-level multi-currency, `15`–`16` (v3.1) universal data ingestion and
research from a canonical dataset, and `17`–`24` (v3.2) the research and
validation path. None are part of the automated test suite, though all
twenty-four run, and `17`–`24` all ingest the same committed panel in
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
| `docs/ADR/` | Architectural Decision Records — 37 of them |
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
│   persistence/   Codec spine, RunStateStore, typed decoding
│   backtesting/   Dataset → execution path → analytics (backtest + replay drivers)
│   replay/        Deterministic replay cursor
│
├── Lifecycle path (composed by alphalab.lifecycle)
│   lifecycle/     Research → model → strategy version → promotion → deployment
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
```

All 48 top-level packages are accounted for above.

Additional directories:

```text
docs/          Documentation and ADRs
examples/      24 runnable examples
benchmarks/    50 performance benchmarks
tests/         4548 tests — unit, integration, regression
configs/       Reference configuration files
```

---

# Quality Assurance

AlphaLab is continuously validated through automated tooling.

- ✅ **4548 passing tests** (2030 unit, 263 integration, 2255 regression) — **0 skipped, 0 warnings**
- ✅ Strict MyPy type checking (975 source files)
- ✅ Ruff linting and formatting
- ✅ 50 / 50 benchmarks, 24 / 24 examples
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
- **Standalone engines with no in-repo consumer** — that is ADR-0009, not an
  orphan.

## External dependencies — not internal engineering

- **Verification against a commercial venue.** No network egress, no vendor
  credentials. The protocols are proven against local servers.
- **Named vendor request shapes.** Each belongs to a vendor adapter.
- **FX data.** v2.17 ships the rate-feed *boundary* and not a single rate.
- **Classification data.** v2.11 ships the security master's *mechanism* and
  v2.15 its provenance; AlphaLab ships no taxonomy and no reference-data feed.

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

**AlphaLab v3.3.0**

Building deterministic infrastructure for quantitative research.

</div>
