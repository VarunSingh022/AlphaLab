# AlphaLab Pre-v4 Master Audit

**Baseline:** AlphaLab v3.9.0, commit `c616978a9eeab39479c54cd3ff60e9331658a8aa` (annotated tag
`v3.9.0`, verified peeled to HEAD), working tree clean at the start of the audit.
**Scope:** the whole repository — 732 source modules (110,745 lines) in 50 top-level packages
plus `alphalab/api.py`, 321 test modules (103,350 lines), 65 examples, 59 benchmarks, CI,
packaging and 44 ADRs.
**Machine-readable companion:** [`PRE_V4_COMPLETION_LEDGER.yaml`](PRE_V4_COMPLETION_LEDGER.yaml)
— every actionable finding below has exactly one ledger entry, and the finding table at the end
of this document is generated from the same source.
**Status at v3.11.0:** the findings below describe v3.9.0 and are not rewritten. Every item
assigned to v3.10.0 and v3.11.0 is implemented, each with the tests that pin it recorded in its
ledger entry (`test_status`, `v310_outcome`, `v311_outcome`). v3.10 found fifteen more, listed
at the end of section Y; v3.11 found seventeen more, listed at the end of section Z — fifteen
fixed in it, two (EXE-010, TST-011) scheduled for v3.12.0. The *Status* column of the master table reads the
ledger.

Method. Documentation was read as a statement of intent and then checked against the code, the
import graph and runtime behaviour. Where a finding says **verified**, a probe was run against the
v3.9.0 tree (scripts listed in section V/W) and the observed output is quoted. Where it says
**code review**, the behaviour follows from the quoted source and was not executed. Nothing here
is inferred from a file name.

---

## A. Executive summary

AlphaLab v3.9.0 is a large, carefully documented, zero-dependency library whose v3.x additions
(research methodology, point-in-time data, lifecycle evidence, the v3.8 risk model and the v3.9
execution contract) are generally rigorous. The audit nevertheless found that **the canonical
execution path — the part every backtest, replay, paper and live run takes — is not correct
enough to freeze**, and that several documents describe properties the code does not have.

The most consequential findings, all reproduced on the v3.9.0 tree:

1. **A fully invested account cannot sell.** Pre-trade risk charges buying power, gross
   exposure, leverage and margin for every order regardless of side or position, so reducing
   trades are refused at a limit and a breached drawdown blocks liquidation (RSK-001..004,
   verified). Three further limits are inert (KD-001..003, already self-reported in ROADMAP).
2. **Money is rounded to two decimals in every currency and prices to four decimals.** A
   1e9-unit purchase at 0.00001234 is booked for free; a EUR/USD fill of 1,000,000 at 1.08345 is
   booked $50 short; JPY carries fractional yen; a 50-satoshi venue fill cannot be booked at all
   (ACC-001..003, verified). The module's "exact at the currency's minor unit" claim is false.
3. **The canonical path is quadratic in universe size.** 160 assets × 50 daily bars take 55 CPU
   seconds and 337 MB; a 500-stock, 10-year daily backtest extrapolates to ~7.5 CPU-hours and
   ~170 GB (PRF-001, verified). Every "linear" claim was measured with one asset.
4. **The performance report's headline numbers are wrong for most runs**: returns are taken per
   market record and annualized as if daily; CAGR assumes every run lasted a year; win rate counts
   opening fills as losses (ANA-001..004, verified).
5. **Research-integrity gaps on the canonical path**: simulated fills execute at the price of the
   event the strategy just observed; bar timestamps have no defined convention; a crashing
   strategy yields a "successful" backtest; delisted symbols' terminal returns vanish from factor
   studies (EXE-001, DAT-001, EXE-006, DAT-002).
6. **Boundary violations**: vendor market-data stubs (four raise `NotImplementedError`),
   credential-bearing venue transport, SaaS tenant management (`enterprise`) and UI state
   (`workbench`) live inside the engine (BND-001..003, BRK-007).
7. **Capability gaps for broad use**: only market orders exist on the canonical path; derivatives
   are booked as fully paid equities (no multiplier, no margin); no corporate actions or cash flows
   on positions; no schema evolution for durable state (EXE-003, ACC-005/006, PER-001).

The ledger records **155 findings**: 6 critical, 34 high, 68 medium, 47 low; 59 allocated to
v3.10.0, 33 to v3.11.0, 23 to v3.12.0, 11 to v3.13.0, 4 to v4.0.0 and 25 dispositioned as kept
boundaries, external responsibilities or accepted limitations with reasons.

**Verdict at baseline:** not freezable. After v3.10–v3.13 as planned in sections Y–AB, the
remaining items are boundaries and external dependencies only.

---

## B. Current architecture

AlphaLab is, in its own words and in fact, **two wired paths plus standalone engines**
(ADR-0009):

* **The execution path.** `runtime.execution_pipeline.ExecutionPipeline` owns one step (market
  record → mark → strategy → allocation → risk → OMS → fill → portfolio → valuation);
  `runtime.run.RunEngine` owns the run (cursor, skips, steps, identifier stream); four stateless
  drivers (`TradingSession`, `BacktestEngine`, `ReplayBacktest`, `LiveSession`) choose the next
  record and the clock. Verified: the drivers hold no fields.
* **The lifecycle path.** `alphalab.lifecycle` composes registration, evidence, promotion,
  deployment, governance, progression, specification, health, comparison, reconciliation,
  fingerprints, manifests, certification and portability, joined to the execution path by
  `authorize_run` and the strategy-class registry.
* **Leaves and standalone engines.** `common` (bottom), `conventions` and `alt_data` (leaves over
  `common`); asset-class analytics (`options`, `futures`, `crypto`, `macro`); learning (`ml`,
  `deep_learning`, `reinforcement_learning`); construction (`portfolio_optimizer`); scale-out
  (`cloud_research`, `distributed`, `cluster_scheduler`); and a set of v1 "engine series"
  packages whose status is assessed in section C.

The package graph has **no import cycles** (Tarjan SCC over 50 packages). One module-level cycle
exists, `oms.state` ↔ `oms.snapshot`, broken by a function-local import (low severity).
`common` imports nothing else in `alphalab`; `strategy` imports only `common`; `conventions` and
`alt_data` import only `common` — the stated layering holds.

---

## C. Package inventory

Classification is by what the package does at runtime and who imports it, not by its docstring.
"No consumer" means no other `alphalab` package imports it.

| Package | Modules | LOC | Imports (alphalab) | Imported by | Classification |
| --- | ---: | ---: | --- | --- | --- |
| `(root)` | 1 | 6 | common | - (no consumer) | package root |
| `allocation` | 15 | 2,383 | common, core, persistence, strategy | reinforcement_learning, runtime | spine |
| `alt_data` | 18 | 3,852 | common | api, factor_library, research | PIT leaf (v3.7) |
| `analytics` | 19 | 5,130 | common, core | api, backtesting, factor_library, portfolio_optimizer, runtime | spine (reports) + risk model |
| `api` | 1 | 1,540 | alt_data, analytics, backtesting, common, conventions, data, factor_library, futures, instrument, market, options, research, runtime, strategy | - (no consumer) | application-facing facade (top) |
| `backtesting` | 7 | 771 | analytics, common, core, execution, market, oms, portfolio, replay, runtime, strategy | api, lifecycle | spine driver |
| `broker` | 20 | 5,241 | common, core, persistence | brokers, lifecycle, runtime | spine (venue boundary) - contains credential transport (BRK-007) |
| `brokers` | 16 | 1,041 | broker, common, core | - (no consumer) | multi-venue connector over broker |
| `cloud_research` | 6 | 357 | common, distributed, ml | - (no consumer) | standalone (process-pool execution; DET-001) |
| `cluster_scheduler` | 5 | 268 | common, distributed | - (no consumer) | standalone (queue policies) |
| `common` | 19 | 2,341 | - | (root), allocation, alt_data, analytics, api, backtesting, broker, brokers, cloud_research, cluster_scheduler, conventions, core, crypto, data, deep_learning, deployment_manager, distributed, enterprise, execution, experiment_tracking, factor_library, feature_store, feed, futures, instrument, lifecycle, live, macro, market, marketdata, ml, model_registry, oms, optimizer, options, persistence, plugins, portfolio, portfolio_optimizer, reinforcement_learning, replay, reporting, research, research_assistant, risk, runtime, scenario, scheduler, strategy, studio, workbench | foundation |
| `conventions` | 8 | 1,080 | common | api, futures, lifecycle, macro, portfolio | leaf (v3.4) |
| `core` | 10 | 2,246 | common | allocation, analytics, backtesting, broker, brokers, execution, futures, instrument, lifecycle, oms, options, portfolio, risk, runtime | spine (canonical domain) |
| `crypto` | 9 | 1,065 | common, portfolio | - (no consumer) | standalone asset-class analytics |
| `data` | 32 | 5,635 | common, options | api, factor_library, lifecycle, live, market, marketdata | data engine (wire + ingestion) |
| `deep_learning` | 9 | 935 | common, ml | reinforcement_learning | standalone; LSTM/attention forward-only (SCF-004) |
| `deployment_manager` | 6 | 550 | common | lifecycle | lifecycle path |
| `distributed` | 15 | 969 | common | cloud_research, cluster_scheduler | standalone job-table scaffold (SCF-003) |
| `enterprise` | 9 | 788 | common | lifecycle | lifecycle governance + SaaS tenant mgmt (BND-002) |
| `execution` | 19 | 5,805 | common, core | backtesting, runtime | spine (+v3.9 algorithms/routing/quality) |
| `experiment_tracking` | 6 | 514 | common, studio | lifecycle, research_assistant | lifecycle path |
| `factor_library` | 28 | 4,838 | alt_data, analytics, common, data, market | api, research | research engine |
| `feature_store` | 17 | 1,000 | common | ml | standalone (feature registry) |
| `feed` | 14 | 653 | common, market | - (no consumer) | v1 provider-surface scaffold; USD defaults (SCF-002) |
| `futures` | 8 | 1,343 | common, conventions, core, market, portfolio | api | standalone asset-class analytics |
| `instrument` | 7 | 1,491 | common, core, persistence | api, market, runtime | spine (identity) |
| `lifecycle` | 23 | 12,891 | backtesting, broker, common, conventions, core, data, deployment_manager, enterprise, experiment_tracking, model_registry, oms, persistence, portfolio, research, risk, runtime, strategy, studio | - (no consumer) | lifecycle path |
| `live` | 17 | 790 | common, data | - (no consumer) | v1 provider-surface scaffold (SCF-002) |
| `macro` | 9 | 962 | common, conventions | - (no consumer) | standalone (foundation fixed income/macro) |
| `market` | 19 | 2,046 | common, data, instrument, marketdata | api, backtesting, factor_library, feed, futures, reinforcement_learning, runtime | spine (canonical market model) |
| `marketdata` | 48 | 2,342 | common, data | market | transports + wire re-export; vendor stubs & v1 engine scaffold (BND-001, SCF-002) |
| `ml` | 8 | 743 | common, feature_store | cloud_research, deep_learning | standalone |
| `model_registry` | 8 | 1,355 | common, persistence | lifecycle | lifecycle path |
| `oms` | 12 | 1,564 | common, core, persistence | backtesting, lifecycle, runtime | spine |
| `optimizer` | 14 | 855 | common | - (no consumer) | standalone scaffold; wall clock (DET-002, SCF-003) |
| `options` | 12 | 1,613 | common, core, portfolio | api, data | standalone asset-class analytics |
| `persistence` | 6 | 1,046 | common | allocation, broker, instrument, lifecycle, model_registry, oms, portfolio, runtime | foundation |
| `plugins` | 14 | 625 | common | - (no consumer) | scaffold; NotImplementedError placeholder (SCF-003) |
| `portfolio` | 23 | 5,038 | common, conventions, core, persistence | backtesting, crypto, futures, lifecycle, options, reinforcement_learning, runtime | spine (accounting) |
| `portfolio_optimizer` | 26 | 4,790 | analytics, common | - (no consumer) | standalone (v1 closed forms + v3.8 certified QP) |
| `reinforcement_learning` | 7 | 773 | allocation, common, deep_learning, market, portfolio, risk, runtime, strategy | - (no consumer) | standalone (drives real pipeline) |
| `replay` | 11 | 603 | common | backtesting | spine cursor |
| `reporting` | 13 | 673 | common | - (no consumer) | standalone scaffold (dashboards) (SCF-003) |
| `research` | 30 | 5,408 | alt_data, common, factor_library | api, lifecycle | research (v3.2/v3.7 methodology) |
| `research_assistant` | 7 | 526 | common, experiment_tracking, studio | - (no consumer) | standalone grid-search driver (SCF-003) |
| `risk` | 13 | 715 | common, core | lifecycle, reinforcement_learning, runtime | spine |
| `runtime` | 12 | 7,231 | allocation, analytics, broker, common, core, execution, instrument, market, oms, persistence, portfolio, risk, strategy | api, backtesting, lifecycle, reinforcement_learning | spine (step/run/drivers) |
| `scenario` | 6 | 1,224 | common | - (no consumer) | standalone (v3.3 stress contract) |
| `scheduler` | 13 | 632 | common | - (no consumer) | scaffold; silent CRON no-op, UTC weekends (DAT-006) |
| `strategy` | 16 | 3,591 | common | allocation, api, backtesting, lifecycle, reinforcement_learning, runtime | spine |
| `studio` | 24 | 877 | common | experiment_tracking, lifecycle, research_assistant, workbench | lifecycle path (StrategyDefinition) + fake orchestration scaffold (SCF-001) |
| `workbench` | 17 | 722 | common, studio | - (no consumer) | UI state - boundary violation (BND-003) |

**Summary of classes.** Execution spine: 16 packages. Lifecycle path: 8. Leaves: 3. Genuine
standalone engines with a coherent capability: `portfolio_optimizer`, `options`, `futures`,
`crypto`, `macro`, `ml`, `reinforcement_learning`, `scenario`, `feature_store`, `cloud_research`,
`cluster_scheduler`. **Scaffolding or duplicates** (recommended for removal or consolidation):
`feed`, `live`, the `marketdata` engine layer and its five vendor subpackages, `workbench`,
`studio` orchestration, `plugins`, `scheduler`, `reporting` dashboards, `optimizer`,
`research_assistant`, `distributed` (kept only as the job table under `cloud_research`).
**Boundary violations:** `enterprise` (tenant/user management), `workbench` (UI), vendor
market-data clients, the credential-bearing REST venue transport in `broker`.

---

## D. Module inventory

An AST inventory of all 732 modules (classes, functions, `__all__`, imports) was built
(`scratchpad/audit/inventory.json`, not committed). Salient facts:

* 253 modules declare `__all__` (4,050 names); 479 do not. Package-level `__all__` exports 2,589
  names (2,450 unique).
* Empty or near-empty modules: `feed/feed.py` (4 lines, "satisfies the specific structural
  requirements"), `marketdata/client.py` (`BaseClient: pass`), `marketdata/{binance,…}/__init__`
  (6 lines each), many 11–20-line "engine" facades in v1 packages.
* Largest modules: `runtime/execution_pipeline.py` (2,215), `portfolio_optimizer/construction.py`
  (1,949), `runtime/snapshot.py` (1,771), `lifecycle/certification.py` (1,621).
* Four marketdata vendor clients consist entirely of methods raising `NotImplementedError`.

---

## E. Single-authority map

| Concept | Canonical authority | Other implementations found | Decision |
| --- | --- | --- | --- |
| Side | `core.enums.Side` | re-exported by `oms`, `brokers` (same object) | keep |
| Proposed order | `core.order_request.OrderRequest` | re-exported by `allocation`, `risk` (same object) | keep; add typed instructions (EXE-003) |
| Lifecycle order | `oms.order.Order` | `broker.BrokerOrder` (venue mirror, deliberate), `execution.OrderInstruction`, `execution.algorithms.ChildOrder` | keep; documented pairs |
| Order transitions | `core.lifecycle.ORDER_TRANSITIONS` | none (v3.9 unified) | keep |
| Fill | `core.fill.Fill` | `broker.BrokerExecution`, `execution.ExecutionReport`, `strategy.FillEvent`; **`brokers.ExecutionReport` is `BrokerExecution`** | rename the `brokers` alias (API-001) |
| Trade | `core.trade.Trade` | **`data.feed.Trade` is a market print** | rename wire type (API-001) |
| Position / Account | `portfolio.Position` / `portfolio.Account` | `broker.BrokerPosition` / `BrokerAccount` (mirror, deliberate) | keep |
| Quote / Bar / Tick / MarketRecord | `market.*` | `data.feed.*` (wire, deliberate); `live.message.*` (scaffold); **`runtime.snapshot.MarketRecord` (snapshot record)** | remove `live`; rename snapshot record |
| Instrument identity | `instrument.InstrumentRecord` + `derive_asset_id` (uuid5) | per-asset-class contract types (`OptionContract`, `FutureContract`, `CryptoInstrument`, `data.assets`) not tied to identity | link via instrument economics (ACC-005) |
| Asset classification | `core.enums.AssetType` | `data.DataAssetClass` (renamed, deliberate), **`live.AssetClass`, `marketdata.AssetClass` (identical duplicates)**, `crypto.InstrumentType` | remove duplicates (SCF-002) |
| Currency / minor unit | none (bare `str`; `portfolio.money` hard-codes 0.01) | — | new currency-units authority (ACC-001) |
| FX | `portfolio.fx.FxRates` | `fx_feed` (boundary), `fx_research` (derivations) | keep |
| Calendar / session | `data.calendar.MarketCalendar` | **`scheduler.TradingCalendar` (UTC weekends)**, two structural `SessionCalendar` protocols (`alt_data`, `futures`), `conventions` calendar protocol | remove scheduler calendar (DAT-006); merge protocols (API-001) |
| Session driver | `runtime.session.TradingSession` | **`scheduler.session.TradingSession`** (window model) | rename/remove (API-001) |
| Strategy definition | `studio.strategy.StrategyDefinition` | `research_assistant.StrategyCandidate` | move to `strategy` (SCF-001) |
| Strategy lifecycle state | three deliberate machines (instance, registry, progression) + `model_registry.ModelStage` | — | keep (pinned) |
| Capabilities | `core.capabilities.CapabilityDeclaration` | `lifecycle.BrokerCapabilities` (projected) | keep |
| Capital budget / reservation | `allocation` | `allocation.capital` plans, `portfolio_optimizer.CapitalAllocation` (v1) | keep (documented) |
| Risk limit | `risk.RiskLimits` | `analytics.RiskBudget/BudgetLimit`, `portfolio_optimizer.RiskConstraints` | keep (documented) |
| Portfolio target | none on the execution path | `portfolio_optimizer.TargetWeights`, `allocation.PlacementWeights` | add target intents (FEA-001) |
| Optimization / parameter search | `portfolio_optimizer.construct` (weights) | **four parameter searches**: `optimizer`, `research.overfitting` sweeps, `cloud_research.sweep`, `research_assistant` | consolidate (SCF-003) |
| NAV / valuation | `portfolio.valuation.PortfolioValuation` | `portfolio.nav.NAVCalculator` (same rule) | keep; remove currency defaults |
| Exposure | per-measure authorities (risk market value, contract exposure, factor exposure) | `portfolio.exposure.ExposureEngine` (v1, unmultiplied) | keep documented; remove v1 engine at API freeze |
| Attribution | `analytics.attribution` | `portfolio.fx_research` currency attribution (deliberate) | keep |
| Statistics | `common.statistics` | legacy copies removed in v3.2 | keep; make total (NUM-001) |
| Point-in-time | `common.point_in_time` | legacy `known_as_of` (macro/alt_data) | keep; legacy documented |
| Provenance | `data.provenance.DatasetProvenance` | `alt_data.ObservationSource`, `alt_data.DataProvenance` | keep (deliberate) |
| Persistence | `persistence.RunStateStore` + per-subsystem snapshots | `model_registry.artifact_store` (bytes, deliberate) | add schema evolution (PER-001) |
| Routing decision | `execution.routing.select_route` | `runtime.broker_routing.RoutingDecision` (send) | keep; names documented |
| Execution cost | `execution.costs.ExecutionCostModel` | legacy commission/slippage models, `crypto.venue.FeeSchedule`, `portfolio_optimizer.costs` | legacy lifted into the model (keep) |

---

## F. Dependency graph

Package edges (from the AST import graph; `common` omitted where it is the only edge):

* Spine: `runtime → allocation, analytics, broker, core, execution, instrument, market, oms,
  persistence, portfolio, risk, strategy`; `backtesting → analytics, core, execution, market, oms,
  portfolio, replay, runtime, strategy`; `market → data, instrument, marketdata`; `portfolio →
  conventions, core, persistence`; `allocation → core, persistence, strategy`; `broker → core,
  persistence`; `execution → core`; `analytics → core`; `oms → core, persistence`.
* Lifecycle: `lifecycle →` 18 packages (backtesting, broker, conventions, core, data,
  deployment_manager, enterprise, experiment_tracking, model_registry, oms, persistence,
  portfolio, research, risk, runtime, strategy, studio).
* Research: `research → alt_data, factor_library`; `factor_library → alt_data, analytics, data,
  market`.
* Suspicious edges examined: `data → options` (one leaf enum; documented), `experiment_tracking →
  studio` and `research_assistant → studio, experiment_tracking` (scaffold bridges; resolved by
  SCF-001), `reinforcement_learning → runtime, allocation, …` (drives the real pipeline; correct).
* No package cycles. No low-level module imports an application concern except the boundary
  violations in section U.

---

## G. Runtime / call-flow map

Execution step (`ExecutionPipeline.process_market_event`), verified by reading:

1. update `market_prices` with the event's price (quote → midpoint, bar → close, tick → price);
2. mark every held position (`PortfolioEngine.update_market_prices`) — **all positions, every
   event** (PRF-001);
3. resync risk from the marked book (exposure, NAV, margin; conversions with `as_of=None`,
   EXE-008);
4. build each running strategy's context (marked portfolio, orders slice, risk, market, history
   bounded at the event instant, universe) and dispatch the event to **every** running strategy
   (subscriptions ignored, EXE-007); exceptions fail the strategy silently (EXE-006);
5. allocation sizes and nets **deltas** (not targets, ALC-002) and reserves capital;
6. per request: drop unpriced → settlement-currency check → risk (EXE: RSK-001..004) → OMS
   submit+accept → **MARKET order only** (EXE-003) → under SIMULATED routing, fill decided by the
   fill policy **at this event's price** (EXE-001) → portfolio (per-currency cash, P&L; money and
   prices quantized, ACC-001..003) → analytics trade record → partial remainder cancelled;
7. one portfolio snapshot per record (analytics periods, ANA-001) and a valuation.

Live cycle (`LiveSession.advance`): settle venue fills (broker mirror then canonical pipeline;
no FX rates, EXE-009) → advance the canonical step → route working orders through
`route_order`/`route_child_order` behind capability gates.

Lifecycle path: candidate → run → evidence (content-derived) → model version → strategy version →
promotion gate → deployment ledger (governance required at every live-changing entry point) →
`run_plan`/`authorize_run` → strategy-class registry → execution path.

---

## H. Deferred / future-work inventory

Every item listed as "optional future evolution" in ROADMAP.md/nowandfuture.md, and every v3.9
deferred item, has a ledger entry with a decision (OFE-001..026, BRK-002..006). Summary:

* **Implement before v4:** schema evolution; venue sequence numbers; persisted child bindings
  and request ledger; multi-broker book-to-mirror reconciliation; optimal split; estimated
  urgency; randomized iceberg tranches; pipeline-driven fill/order/timer hooks; richer
  construction (estimated shrinkage, EWMA, factor-model covariance, linear costs in the
  objective); multivariate neutralization by QR; Holm/BY corrections and probabilistic/deflated
  Sharpe; Newey–West IC standard errors; execution-path delivery of point-in-time observations;
  streaming observation sets; classification dimensions beyond sector; per-strategy capital
  ceilings; a content-addressed evidence store; windowed health; lock-file reader; rerun harness.
* **Keep as boundary:** fitted decay half-life, statistical regime models, per-strategy
  sub-ledgers, derived comparison alignment, host-process concerns (hook timeouts, hot reload,
  threading).
* **External:** vendor adapters.

The source contains no `TODO`/`FIXME`/`XXX`/`HACK` markers; unfinished work is expressed as
documented deferrals and as `NotImplementedError` stubs (four vendor clients, `plugins`).

---

## I. Known-limitations inventory

The 50 "deliberate boundaries" were re-examined (BDY-001..026). 22 are kept as stated. Changed:
**no migration framework** (replaced, PER-001), **no durable state for v3.5/v3.6 values**
(evidence store), **no American pricing** (implement), **no trade ingestion** (declared trade
prints), **no cross-expiry interpolation** (named total-variance method). Two boundaries are
**contradicted by the code** and must be enforced rather than restated: no credential handling
(BRK-007, BND-002) and no vendor data feed (BND-001).

Limitations accepted with reasons: float-second timestamps (~0.24 µs resolution, DAT-008); tz
database dependence (DAT-007); class-name coupling of persisted enums (PER-004); host
supervision, credentials, vendor protocols and all market/reference data remain external.

---

## J. Bugs discovered

Critical and high correctness defects (all in the ledger with evidence):

| ID | Defect | Evidence |
| --- | --- | --- |
| RSK-001 | Fully invested account cannot sell (buying power charged on sells) | probe p1, verified |
| RSK-002 | Exposure/leverage/margin checks refuse de-risking at a limit | probe p1, verified |
| RSK-003 | Drawdown/daily-loss breach refuses liquidation | probe p1, verified |
| RSK-004 | Working orders ignored by pre-trade limits | code review |
| KD-001..003 | Position limit unit mismatch; daily loss never enforced; net exposure unread | ROADMAP self-report + code |
| ALC-001 | Long-only constraint refuses closing sells | probe p1, verified |
| ALC-003 | Missing volatility silently sized at 1% | code review |
| ACC-001 | Every currency rounded to 0.01 | probe, verified (JPY 1000000.50) |
| ACC-002 | Prices quantized to 4 dp (free purchases, FX mis-booking) | probe, verified |
| ACC-003 | Quantities quantized to 1e-6 (crypto fills unbookable) | probe, verified |
| ACC-004 | Accounting arithmetic in the ambient decimal context | code review |
| ANA-001..004 | Per-record returns annualized as daily; CAGR over a fixed year; win rate counts openings as losses; undefined → 0.0 | probe p3, verified |
| EXE-006 | Crashing strategy produces a "successful" backtest | code review |
| REL-001 | Socket leaked on every stream reconnect | pytest -W error, verified |
| BRK-001 | Numerically equal fills reported as reconciliation breaks | probe p4, verified |
| DAT-006 | CRON/session timers fire once and vanish; UTC-weekend calendar | probe, verified |
| DET-001/002 | Nondeterministic aggregate state in cloud_research; wall clock in optimizer | code review |

---

## K. Silent incomplete implementations

* Vendor market-data clients that raise `NotImplementedError` for every call (BND-001).
* `plugins.BasePlugin.execute` — "Placeholder for domain-specific implementation logic".
* `scheduler` CRON / SESSION_OPEN / SESSION_CLOSE / BAR_BOUNDARY schedule types (DAT-006).
* `deep_learning` LSTM and attention without backpropagation (SCF-004).
* `studio`/`workbench` "run" methods that store supplied metrics instead of running (SCF-001).
* `marketdata.ProviderMetrics` never updated; `RECOVERING`/`ProviderRecovered` never produced.
* Strategy subscriptions recorded and never read (EXE-007); lifecycle hooks declared and never
  invoked (EXE-005); `Intent.execution_directive` never read (EXE-003).
* Risk limits that exist and are never checked (KD-002, KD-003, `PositionLimit.max_notional`).

---

## L. Numerical findings

ACC-001..004 (money/price/quantity precision, ambient context), NUM-001 (non-finite statistics
inputs), NUM-002 (correlation under/overflow), NUM-003 (R² of a constant), NUM-004 (normal CDF
tail precision; mixed year bases), NUM-005 (no carry yield in Black–Scholes), NUM-006 (no American
pricing), NUM-007 (normal-equation regression), NUM-008 (4-dp legacy cost models), ANA-005 (VaR
rounding). The v3.8 QP solver (Goldfarb–Idnani dual active set with a KKT certificate) and the
v3.9 execution arithmetic (pinned 34-digit contexts) were reviewed and are sound; the solver's
scale envelope is a performance item (PRF-005).

## M. Determinism findings

DET-001 (as_completed ordering), DET-002 (optimizer wall clock), DET-003 (seed sign aliasing),
DET-004 (default seeds), ACC-004 (ambient decimal context on the accounting path). The execution
path itself reads no wall clock (verified by grep: `time.time` appears only in the optimizer,
scheduler's SystemClock, the Binance stub, the HMAC transport and the streaming staleness gate).
Identifier streams are seeded and resumable (ADR-0022) and cross-process byte identity of a
continued run is tested (`test_durable_run_state_cross_process.py`).

## N. Reproducibility findings

REP-001 (engine version from installed metadata with a hard-coded fallback), REP-002 (engine
identity is a version string only; tz database unrecorded), REP-003 (no rerun harness), KD-004
(row-ingested dataset identity), EXE-002 (cost model and fill policy absent from results).

## O. Persistence findings

PER-001 (no schema evolution — required before v3.10's own durable additions), PER-002 (non-strict
JSON for NaN/inf), PER-003 (directory not fsynced after rename), PER-004 (enum class-name
coupling), PRF-004 (unbounded history in every durable state and checkpoint).

## P. Performance findings

PRF-001 (quadratic in universe size; verified numbers in section V), PRF-002 (PersistentMap
iteration O(keys ever written); verified), PRF-003 (quadratic ExternalOrderMap; verified),
PRF-004 (unbounded state and full checkpoints), PRF-005 (QP scale envelope unmeasured beyond 50
assets), EXE-007 (every strategy receives every event).

## Q. Research-integrity findings

EXE-001 (same-event fills), EXE-002 (optimistic defaults unrecorded), EXE-006 (silent strategy
failure), DAT-001 (undefined bar timestamp convention), DAT-002 (delisting returns dropped),
DAT-003 (no implementation lag in forward returns), DAT-004 (static symbology), EXE-008 (FX
look-ahead guard bypassed on the risk path), ANA-001..004. The v3.2 purging/embargo and v3.7
point-in-time layers were reviewed and are correct as specified (label-window purging, inclusive
visibility, UNKNOWN availability never visible); defect injection confirms the suite pins them.

## R. Global-market findings

ACC-001 (minor units), ACC-002/003 (price and quantity precision), ACC-005 (derivatives
economics), ACC-006 (corporate actions and cash flows), ACC-007 (negative prices, rebates),
NUM-005 (carry), DAT-005 (closed timeframe set), DAT-006 (UTC-weekend scheduler calendar),
DAT-004 (point-in-time symbology), EXE-003 (auction and TIF instructions). `data.calendar.MarketCalendar`
was reviewed and handles lunch breaks, overnight sessions, half days, 24/7 venues, non-Saturday/
Sunday weekends and DST correctly by construction (endpoints resolved per local date).

## S. API findings

API-001 (72 distinct objects sharing public names), API-002 (no public API manifest), API-003
(remaining USD defaults; sweep gaps), API-004 (valuation view without rates), ALC-002 (Intent
contract misdescribed), EXE-005 (dead hooks), RSK-006 (stringly-typed severity).

## T. Documentation findings

DOC-001 (claims contradicted by the code: linearity, zero skips/warnings, exact minor units, no
credentials, no venue named, Intent as target), DOC-002 (required work filed as optional; some
boundary reasons that do not hold).

## U. Security / boundary findings

BND-001 (vendor clients with API-key fields), BRK-007 (credential-bearing HMAC transport and a
made-up REST protocol), BND-002 (tenant/user/session/secret management), BND-003 (UI state),
REL-001 (fd leak). `common.tls` (TLS 1.2 floor, verification unchanged) was reviewed and is sound.
`cloud_research.resolve_task` imports arbitrary dotted paths from job payloads (trust boundary to
be documented). No secret values are logged (VenueCredentials redacts).

---

## V. Stress tests

All timings are CPU seconds (`time.process_time`) on the audit container (4 vCPU, Python 3.12.3);
they describe this machine and this workload only.

**Canonical backtest, N assets × 50 daily bars, buy-and-hold one unit of each**
(`scratchpad/probes/p2_multiasset_scale.py`):

| N | records | CPU s | peak MB | logged marks |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 500 | 0.56 | 2.5 | 4,945 |
| 20 | 1,000 | 1.58 | 7.4 | 19,790 |
| 40 | 2,000 | 4.74 | 24.6 | 79,180 |
| 80 | 4,000 | 14.77 | 88.2 | 316,760 |
| 160 | 8,000 | 54.83 | 336.8 | 1,267,120 |

Growth per doubling approaches 4× in both time and memory: quadratic in N.

**Containers** (`scratchpad/probes/p4_broker_misc.py`): `ExternalOrderMap.bind` for 2k/4k/8k
orders 0.033/0.092/0.338 s (quadratic); iterating a one-key `PersistentMap` after 10k/20k/40k
set/delete churn 1.3/2.9/6.1 ms per iteration (linear in history).

**Baseline gates (v3.9.0 as tagged):** `pytest -W error`: 7,379 passed, 6 failed, 1 error,
1 skipped (165.7 s) — the failures are the REL-001 socket leak (5 + 1 via GC) and one timing
guard under load (TST-001); the skip is root-dependent (TST-002). Without `-W error` the same
tree passes. `mypy .`: 1,183 files clean. Ruff lint and format clean. Examples 65/65 (38 s).
Benchmarks 59/59 (505 s).

Not stress-tested at baseline (planned in v3.12): 10,000-asset universes (infeasible before
PRF-001), 1,000 strategies, 100 venues, very large event streams on the live path, and the QP at
100–800 assets.

### V.2 Re-run against v3.11.0

The same measurement on the v3.11.0 release tree (`scratchpad/stress311/stress_v311.py`): CPU
seconds with the collector running and `tracemalloc` tracing, which inflates allocation-heavy
code two- to four-fold, as the baseline table above was also measured.

**Run length** — one asset, the backtesting benchmark's strategy, which trades every record:

| records | CPU s | per record | peak MB | growth per doubling |
| ---: | ---: | ---: | ---: | ---: |
| 5,000 | 16.21 | 3.24 ms | 49.3 | |
| 10,000 | 32.93 | 3.29 ms | 98.6 | 2.03x |
| 20,000 | 68.00 | 3.40 ms | 197.2 | 2.07x |
| 40,000 | 135.72 | 3.39 ms | 394.9 | 2.00x |

Linear in time and in memory (a run keeps its whole history: every fill, order, event and
equity point). Until PRF-007 was fixed in this release, a run's cost per record grew with its
length.

**Universe** — N assets × 50 daily bars, one unit of each bought on the first bar and held:

| N | records | CPU s | peak MB | growth per doubling |
| ---: | ---: | ---: | ---: | ---: |
| 50 | 2,500 | 2.02 | 4.6 | |
| 100 | 5,000 | 4.22 | 9.0 | 2.09x |
| 200 | 10,000 | 7.95 | 18.0 | 1.89x |
| 400 | 20,000 | 17.44 | 36.0 | 2.19x |
| 800 | 40,000 | 35.62 | 71.8 | 2.04x |

Linear in N, where v3.9.0 was quadratic: 160 assets took 54.83 CPU seconds and 336.8 MB there,
and 800 take 35.62 s and 71.8 MB here (PRF-001, v3.10). The 10,000-asset, 1,000-strategy and
100-venue cases remain v3.12.0's, as planned.

### V.3 The v3.12.0 stress program

`docs/audit/scripts/stress_v3_12.py` (in the repository since v3.12), run on the release tree:
CPU seconds (`time.process_time`) with the collector running, no tracing; peak RSS from the
operating system. Each scenario asserts what it measures.

**10,000 assets** — a registry of 10,000 instruments in 11 sectors and 37 countries and 100
exchanges, sector limits at half of NAV and a country limit, every asset bought on its first bar
and held:

| Universe | Records | Before PRF-009 | After PRF-009 |
| ---: | ---: | ---: | ---: |
| 400 | 1,200 | 366 µs a record | 381 µs a record |
| 10,000 | 30,000 | 1,225 µs a record | 498 µs a record |
| growth at 25x the universe | | 3.34x | **1.31x** |

The same run with no classification limit grew 1.23x. Before the fix every order judged against
a limit summed its bucket's members (PRF-009); the book now keeps each limited bucket's gross.
Peak RSS 280 MB at 10,000 assets.

**1,000 strategies** — one pipeline, a ceiling per strategy enforced, 100 assets, 20 days of
bars (2,000 records), 1,665 fills:

| Subscriptions | CPU | per record |
| --- | ---: | ---: |
| every strategy on every bar (`"bars"`) | 13.54 s | 6.8 ms |
| each strategy on its own asset (`"bars:<asset>"`) | 2.15 s | 1.1 ms |

The same 1,665 fills either way. A probe holding the reached strategies at ten and varying the
others found each strategy costing about 0.34 µs on every record whatever it subscribed to:
665 µs a record alone, 1,012 beside 1,000 others, 4,097 beside 10,000 (PRF-010). With the routing
index: 667, 629 and 661.

**100 venues** — 100 calendars in ten zones: 12,000 day-order expiries in 0.76 s; one book of
10,000 orders and fills reconciled across 100 accounts in 0.09 s, no mismatch, nothing
unassigned; 100 session timers through 30 days, 2,200 firings, in 0.24 s.

**Construction** — 10,000 assets, five factors, budget, long-only and a 5% cap (20,001 rows):
OPTIMAL in 1.51 s, 28 steps, 9,996 constraints binding (the dense solver took 134.6 s at 800
assets; PRF-005's envelope).

**Checkpoints** — 20,000 records with retention at 2,000 and a checkpoint every 1,000: 6.76 s for
the run and its 20 checkpoints; base 1.57 MB, last segment 1.96 MB — a segment carries the whole
order book, which no retention bounds (stated in PRF-004; the v3.13 audit classifies it). The
chain reads back as the full capture.

### V.4 The v3.13.0 stress program

`docs/audit/scripts/stress_v3_13.py`, run on the release candidate on a quiet machine: v3.12's
scenarios re-run as they were written, and v3.13's own. CPU seconds (`time.process_time`) with
the collector running; memory by `tracemalloc`, traced apart from the timing; peak RSS from the
operating system. Each scenario asserts what it measures, and every one passed.

**v3.12's scenarios.** 10,000 assets under sector and country limits: 365 µs a record at 400
assets, 440 at 10,000 — **1.21x** the per-record cost at 25x the universe (1.31x at v3.12), peak
RSS 278 MB. 1,000 strategies with ceilings: 7.3 ms a record with every strategy on every bar, 1.0
with each on its own asset, the same 1,665 fills. 100 venues: 12,000 day-order expiries in 0.70 s;
10,000 orders and fills reconciled across 100 accounts in 0.07 s, no mismatch; 2,200 session-timer
firings in 0.19 s. The internal factor-structured solver at 10,000 assets: OPTIMAL in 1.46 s, 28
steps. 20,000 records with retention and a checkpoint every 1,000: base 1.57 MB, **last segment
1.61 MB** — at v3.12 it was 1.96 MB and grew with every order the run had placed (PRF-011).

**Per-order state and checkpoints** (PRF-011). With every log under retention, a record with no
order adds nothing (−19 bytes a record), and a record with an order holds 2,658 bytes more — the
order book, the reports by order and the routed and settled orders, kept as a stated limitation.
Over 20,000 records with an order every ten, the segments at 2, 10 and 20 links are 1.34, 1.34
and 1.33 MB against a 1.33 MB base, and the chain reads back as the capture.

**The lattice** (NUM-006). An American put at S = K = 50, r = 10%, σ = 40%: 4.2836, 4.2840 and
4.2841 at 1,000, 2,500 and 5,000 steps (`MAX_STEPS`), in 0.10, 0.61 and 2.46 s; an implied
volatility of 0.3000 through 500 steps in 1.16 s and 35 bisections.

**The optimal split at its ceiling** (BRK-005): ten fixed-charge venues beside ten free ones. 1,000
units: the greedy sweep and the optimal split agree, one leg, 100,009.00, the optimal search in
13.71 s. 20,000 units: the greedy sweep takes four legs and 2,000,711.00; the optimal split five
and 2,000,708.92, in 19.71 s. The search is exponential in the fixed-charge venues, which is why
ten is the stated ceiling.

**A factor model, written out and stated by its structure** (FEA-007, PRF-013), k = 5:

| Assets | Written out (dense) | Traced at its peak | Construction over it |
| ---: | ---: | ---: | ---: |
| 1,000 | 1.30 s | 83 MB | 0.27 s |
| 2,000 | 5.64 s | 329 MB | 1.04 s |
| 4,000 | 29.26 s | 1,317 MB | 4.34 s |

| Assets | Stated by its structure | Traced at its peak | `factor_risk` | Construction over it |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.01 s | 0.6 MB | 0.002 s | 0.12 s |
| 4,000 | 0.05 s | 2.3 MB | 0.007 s | 0.73 s |
| 10,000 | 0.12 s | 6.0 MB | 0.017 s | 1.85 s |

Every construction OPTIMAL under a budget, long-only and a 5% cap, and every `factor_risk`
residual within 1e-12 of the volatility.

**The long-short box set** (FEA-009): 200 assets with weights in [−5%, 10%], OPTIMAL in 1.56 s,
292 steps. **Cron** (DAT-006): the longest search an accepted expression makes — the next 29
February from 1 March 2097 — found 2104-02-29 in 0.001 s (2100 is no leap year).

## W. Defect-injection tests

A scratch copy of the tree (`git archive HEAD`) had one mutation applied at a time and the full
suite run with `-x`. "Caught by" names the first failing test; a mutation caught only by a
timing guard is recorded as not reliably caught.

| Mutation | Result | Caught by | Seconds |
| --- | --- | --- | ---: |
| M01 risk order-size > to >= | **NOT CAUGHT** (confirmed with timing guards excluded) | `-` | 130.2 |
| M02 duplicate fill applied (remove DUPLICATE check) | caught | `tests/integration/test_integrated_runtime.py::test_a_deployment_decision_reaches_a_fill_at_a_venue` | 3.9 |
| M03 to_money rounds down | caught (re-run with timing guards excluded) | `tests/unit/portfolio/test_portfolio_snapshot.py::test_exact_money_survives_the_round_trip` | 124.8 |
| M04 partial close relieves wrong basis | caught | `tests/integration/test_mark_to_market_pipeline.py::test_partial_close_realizes_only_the_closed_portion` | 5.0 |
| M05 fill during cancel-pending drops pending | caught | `tests/unit/core/test_order_lifecycle_table.py::test_fills_are_only_legal_once_the_order_is_working_at_the_venue` | 159.8 |
| M06 FX future-dated rate accepted | caught | `tests/regression/test_v34_invariants.py::test_an_fx_rate_from_the_future_is_refused_in_both_directions_of_time` | 101.6 |
| M07 risk rejection does not release reservation | caught | `tests/integration/test_backtest_pipeline.py::test_a_request_risk_refuses_never_reaches_the_oms` | 3.4 |
| M08 quote price uses bid not mid | caught | `tests/integration/test_execution_pipeline.py::test_quote_drives_full_end_to_end_execution_pipeline` | 3.4 |
| M09 sample variance divides by n | caught | `tests/unit/analytics/test_analytics.py::test_annualized_volatility - a...` | 163.7 |
| M10 purge boundary >= to > | caught | `tests/integration/test_v32_capabilities.py::test_walk_forward_folds_over_the_real_index_never_contaminate` | 12.6 |
| M11 forward return look-ahead shift | caught | `tests/unit/factor_library/test_factor_research.py::test_a_forward_return_looks_exactly_the_horizon_ahead` | 164.8 |
| M12 PIT visibility strict < | caught (re-run with timing guards excluded) | `tests/unit/alt_data/test_fundamentals.py::test_a_quarter_is_invisible_until_it_is_published` | 125.2 |
| M13 stale venue status applied | caught (re-run with timing guards excluded) | `tests/regression/test_v39_invariants.py::test_replaying_any_stream_changes_nothing_and_breaks_nothing` | 115.5 |
| M14 out-of-order record accepted | caught | `tests/integration/test_provider_source_session.py::test_an_unordered_session_skips_the_regressing_record_and_records_it` | 5.4 |
| M15 run store skips digest check | caught | `tests/regression/test_run_state_store.py::test_an_altered_payload_fails_its_digest` | 87.7 |
| M16 OMS overfill accepted | caught | `tests/unit/core/test_order_lifecycle_table.py::test_a_complete_fill_must_complete_the_order_exactly` | 160.0 |
| M17 pearson uses population denominators inconsistently | caught | `tests/unit/analytics/test_cross_strategy.py::test_return_correlation_is_pearson_of_the_series_with_its_basis` | 158.5 |
| M18 contribution split last share off by quantum | caught | `tests/integration/test_v33_capabilities.py::test_attribution_reconciles_to_the_portfolio_s_realized_pnl` | 12.7 |
| M19 capability UNDECLARED treated as SUPPORTED | caught | `tests/regression/test_v39_invariants.py::test_compatible_means_every_check_supported_and_nothing_else_does` | 150.9 |
| M20 unpriced request not dropped (price 0 order) | caught | `tests/integration/test_mark_to_market_pipeline.py::test_request_for_an_asset_with_no_market_price_never_reaches_the_oms` | 5.5 |
| M21 dataset duplicate row accepted | caught | `tests/regression/test_data_quality_contracts.py::test_a_duplicate_instant_is_found_and_named` | 15.6 |
| M22 FX staleness check disabled | caught | `tests/regression/test_fx_rate_feed.py::test_the_feed_does_not_decide_whether_a_rate_is_too_old_to_use` | 20.5 |
| M23 allocation budget check ignores outstanding | caught | `tests/regression/test_outstanding_capital_guard.py::test_six_external_events_no_longer_commit_five_times_the_budget` | 73.6 |
| M24 moving average window includes next value | caught | `tests/regression/test_research_cannot_see_the_future.py::test_no_feature_kind_changes_when_the_future_is_removed[FeatureKind.ROLLING_MEAN]` | 78.6 |

### W.2 Re-run against v3.10.0

The same method on a copy of the final v3.10.0 working tree, with every wall-clock guard deselected —
a timing test failing is not a detection — and an unmutated baseline run first, which must pass.
The original twenty-four mutations, five re-targeted where v3.10 moved the code they mutate, plus
eighteen mutations of v3.10's own behaviour (V01–V18).

The guards are deselected test by test (a test in a complexity or scaling file whose own source
reads a clock or asserts a growth bound), not file by file. An earlier run ignored those files
whole, and V01 — incremental marking that forgets the positions a fill priced — appeared to
survive it, although the suite catches it: the structural equivalence test that fails is in
`test_universe_scaling.py`, beside that file's timing guard. That run caught the other 41 of 42.
The table below is the re-run against the final, uncommitted release-candidate tree.

Baseline (unmutated): passed — 7520 passed, 76 deselected in 115.48s (0:01:55). 42 of 42 mutations caught by the harness run.

| Mutation | Result | Caught by | Seconds |
| --- | --- | --- | ---: |
| M01 risk order-size > to >= | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_quantity_is_approved` | 23.1 |
| M02 duplicate fill applied (remove DUPLICATE check) | caught | `tests/integration/test_integrated_runtime.py::test_a_deployment_decision_reaches_a_fill_at_a_venue` | 3.6 |
| M03 to_money rounds down | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_with_commission_and_slippage` | 2.9 |
| M04 partial close relieves wrong basis | caught | `tests/integration/test_mark_to_market_pipeline.py::test_partial_close_realizes_only_the_closed_portion` | 4.5 |
| M05 fill during cancel-pending drops pending | caught | `tests/unit/core/test_order_lifecycle_table.py::test_fills_are_only_legal_once_the_order_is_working_at_the_venue` | 110.0 |
| M06 FX future-dated rate accepted | caught | `tests/regression/test_v34_invariants.py::test_an_fx_rate_from_the_future_is_refused_in_both_directions_of_time` | 79.1 |
| M07 risk rejection does not release reservation | caught | `tests/integration/test_backtest_pipeline.py::test_a_request_risk_refuses_never_reaches_the_oms` | 3.0 |
| M08 quote price uses bid not mid | caught | `tests/integration/test_execution_pipeline.py::test_quote_drives_full_end_to_end_execution_pipeline` | 3.3 |
| M09 sample variance divides by n | caught | `tests/unit/analytics/test_analytics.py::test_annualized_volatility - a...` | 108.8 |
| M10 purge boundary >= to > | caught | `tests/integration/test_v32_capabilities.py::test_walk_forward_folds_over_the_real_index_never_contaminate` | 11.3 |
| M11 forward return look-ahead shift | caught | `tests/unit/factor_library/test_factor_research.py::test_a_forward_return_looks_exactly_the_horizon_ahead` | 111.2 |
| M12 PIT visibility strict < | caught | `tests/unit/alt_data/test_fundamentals.py::test_a_quarter_is_invisible_until_it_is_published` | 109.2 |
| M13 stale venue status applied | caught | `tests/regression/test_v39_invariants.py::test_replaying_any_stream_changes_nothing_and_breaks_nothing` | 101.0 |
| M14 out-of-order record accepted | caught | `tests/integration/test_provider_source_session.py::test_an_unordered_session_skips_the_regressing_record_and_records_it` | 4.6 |
| M15 run store skips digest check | caught | `tests/regression/test_run_state_store.py::test_an_altered_payload_fails_its_digest` | 67.5 |
| M16 OMS overfill accepted | caught | `tests/unit/core/test_order_lifecycle_table.py::test_a_complete_fill_must_complete_the_order_exactly` | 106.3 |
| M17 pearson uses population denominators inconsistently | caught | `tests/regression/test_numeric_refusals.py::test_correlation_is_defined_at_any_representable_magnitude[tiny]` | 27.0 |
| M18 contribution split last share off by quantum | caught | `tests/integration/test_v33_capabilities.py::test_attribution_reconciles_to_the_portfolio_s_realized_pnl` | 11.4 |
| M19 capability UNDECLARED treated as SUPPORTED | caught | `tests/regression/test_v39_invariants.py::test_compatible_means_every_check_supported_and_nothing_else_does` | 99.3 |
| M20 unpriced request not dropped (price 0 order) | caught | `tests/integration/test_mark_to_market_pipeline.py::test_request_for_an_asset_with_no_market_price_never_reaches_the_oms` | 4.4 |
| M21 dataset duplicate row accepted | caught | `tests/regression/test_data_quality_contracts.py::test_a_duplicate_instant_is_found_and_named` | 13.6 |
| M22 FX staleness check disabled | caught | `tests/regression/test_fx_rate_feed.py::test_the_feed_does_not_decide_whether_a_rate_is_too_old_to_use` | 17.3 |
| M23 allocation budget check ignores outstanding | caught | `tests/regression/test_outstanding_capital_guard.py::test_six_external_events_no_longer_commit_five_times_the_budget` | 57.8 |
| M24 moving average window includes next value | caught | `tests/regression/test_research_cannot_see_the_future.py::test_no_feature_kind_changes_when_the_future_is_removed[FeatureKind.ROLLING_MEAN]` | 62.3 |
| V01 incremental marking forgets pending marks | caught | `tests/regression/test_universe_scaling.py::test_marking_one_change_equals_marking_every_price` | 75.3 |
| V02 NEXT_EVENT fills at the deciding event | caught | `tests/regression/test_fill_timing.py::test_next_event_leaves_the_order_working_and_fills_it_at_the_next_price` | 16.9 |
| V03 ingested start-stamped bars not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_start_stamped_bars_are_moved_to_the_end_of_their_interval` | 12.7 |
| V04 wire start-stamped bar not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_a_start_stamped_wire_bar_is_moved_by_its_timeframe` | 12.8 |
| V05 long-only ignores working orders | caught | `tests/regression/test_allocation_semantics.py::test_a_working_sale_counts_against_long_only` | 12.5 |
| V06 buying power charged on reductions | caught | `tests/regression/test_risk_projection.py::test_a_fully_invested_account_can_sell_what_it_holds` | 63.7 |
| V07 bool seed accepted | caught | `tests/regression/test_id_stream_is_pinned.py::test_a_seed_that_is_not_a_non_negative_integer_is_refused[True]` | 17.7 |
| V08 statistics accept non-finite input | caught | `tests/regression/test_numeric_refusals.py::test_every_statistic_refuses_a_non_finite_observation_by_position[mean-nan]` | 25.5 |
| V09 normalization currency defaults to USD | caught | `tests/unit/market/test_normalization.py::test_a_quote_or_a_trade_is_refused_by_a_policy_that_names_no_currency` | 109.9 |
| V10 pipeline currency not the account's | caught | `tests/regression/test_v34_invariants.py::test_the_currency_roles_are_named_not_defaulted` | 77.3 |
| V11 misspelled risk severity accepted | caught | `tests/regression/test_risk_projection.py::test_a_severity_is_a_member_and_a_misspelled_one_is_refused` | 66.1 |
| V12 execution fields compared as text | caught | `tests/regression/test_reconciliation_compares_numbers.py::test_a_fill_reported_with_other_exponents_is_not_a_mismatch` | 59.0 |
| V13 daily loss never maintained | caught | `tests/regression/test_risk_projection.py::test_the_daily_loss_is_measured_from_the_trading_days_start` | 65.5 |
| V14 analytics returns per snapshot, not per instant | caught | `tests/unit/analytics/test_analytics.py::test_the_report_takes_one_equity_point_per_instant` | 105.7 |
| V15 position book keeps a replaced position's totals | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_at_non_round_prices` | 2.7 |
| V16 cluster outcomes applied out of submission order | caught | `tests/regression/test_cluster_outcomes_are_ordered.py::test_outcomes_are_recorded_in_submission_order_whatever_finishes_first` | 13.2 |
| V17 halt on strategy failure ignored | caught | `tests/regression/test_strategy_failures_are_reported.py::test_a_run_configured_to_halt_stops_after_the_failing_record` | 68.5 |
| V18 order-size limit exclusive (M01 again) | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_notional_is_approved` | 21.4 |

---

### W.3 Re-run against v3.11.0

The v3.10 method on a copy of the final v3.11.0 working tree: the forty-two mutations of W.2, five
of them re-pointed where v3.11 moved the code they mutate (M07, M11, V02, V05 and V15), plus
thirty-seven mutations of v3.11's own behaviour (W01–W37), one at a time, the whole suite with `-x`
and the wall-clock guards deselected.

Baseline (unmutated): passed — 8137 passed, 76 deselected in 131.85s (0:02:11). **77 of 79 mutations caught by the
full run.** Two survived: W08 (a buy limit filling at a bar's open above its limit) and W09 (a
resting sell limit crossing on a bid below it). The rules were right and no test pinned them;
TST-012 added one test for each, and both mutants, re-run against them, fail.

| Mutation | Result | Caught by | Seconds |
| --- | --- | --- | ---: |
| M01 risk order-size > to >= | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_quantity_is_approved` | 25.9 |
| M02 duplicate fill applied (remove DUPLICATE check) | caught | `tests/regression/test_ambient_decimal_context.py (collection error)` | 1.4 |
| M03 to_money rounds down | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_with_commission_and_slippage` | 3.3 |
| M04 partial close relieves wrong basis | caught | `tests/integration/test_mark_to_market_pipeline.py::test_partial_close_realizes_only_the_closed_portion` | 5.1 |
| M05 fill during cancel-pending drops pending | caught | `tests/unit/core/test_order_lifecycle_table.py::test_fills_are_only_legal_once_the_order_is_working_at_the_venue` | 124.0 |
| M06 FX future-dated rate accepted | caught | `tests/regression/test_v34_invariants.py::test_an_fx_rate_from_the_future_is_refused_in_both_directions_of_time` | 95.1 |
| M07 risk rejection does not release reservation | caught | `tests/integration/test_backtest_pipeline.py::test_a_request_risk_refuses_never_reaches_the_oms` | 3.9 |
| M08 quote price uses bid not mid | caught | `tests/integration/test_execution_pipeline.py::test_quote_drives_full_end_to_end_execution_pipeline` | 3.8 |
| M09 sample variance divides by n | caught | `tests/unit/analytics/test_analytics.py::test_annualized_volatility` | 129.4 |
| M10 purge boundary >= to > | caught | `tests/integration/test_v32_capabilities.py::test_walk_forward_folds_over_the_real_index_never_contaminate` | 13.1 |
| M11 forward return look-ahead shift | caught | `tests/integration/test_v32_capabilities.py::test_a_delisting_set_must_be_the_one_the_study_names` | 13.4 |
| M12 PIT visibility strict < | caught | `tests/unit/alt_data/test_fundamentals.py::test_a_quarter_is_invisible_until_it_is_published` | 128.1 |
| M13 stale venue status applied | caught | `tests/regression/test_v39_invariants.py::test_replaying_any_stream_changes_nothing_and_breaks_nothing` | 123.5 |
| M14 out-of-order record accepted | caught | `tests/integration/test_provider_source_session.py::test_an_unordered_session_skips_the_regressing_record_and_records_it` | 5.7 |
| M15 run store skips digest check | caught | `tests/regression/test_run_state_store.py::test_an_altered_payload_fails_its_digest` | 82.3 |
| M16 OMS overfill accepted | caught | `tests/unit/core/test_order_lifecycle_table.py::test_a_complete_fill_must_complete_the_order_exactly` | 130.3 |
| M17 pearson uses population denominators inconsistently | caught | `tests/regression/test_numeric_refusals.py::test_correlation_is_defined_at_any_representable_magnitude[tiny]` | 33.4 |
| M18 contribution split last share off by quantum | caught | `tests/integration/test_instrument_economics.py::test_a_split_changes_the_count_and_not_the_value` | 4.2 |
| M19 capability UNDECLARED treated as SUPPORTED | caught | `tests/regression/test_v39_invariants.py::test_compatible_means_every_check_supported_and_nothing_else_does` | 122.6 |
| M20 unpriced request not dropped (price 0 order) | caught | `tests/integration/test_mark_to_market_pipeline.py::test_request_for_an_asset_with_no_market_price_never_reaches_the_oms` | 5.3 |
| M21 dataset duplicate row accepted | caught | `tests/regression/test_data_quality_contracts.py::test_a_duplicate_instant_is_found_and_named` | 17.5 |
| M22 FX staleness check disabled | caught | `tests/regression/test_fx_rate_feed.py::test_the_feed_does_not_decide_whether_a_rate_is_too_old_to_use` | 21.1 |
| M23 allocation budget check ignores outstanding | caught | `tests/regression/test_outstanding_capital_guard.py::test_six_external_events_no_longer_commit_five_times_the_budget` | 74.1 |
| M24 moving average window includes next value | caught | `tests/regression/test_research_cannot_see_the_future.py::test_no_feature_kind_changes_when_the_future_is_removed[FeatureKind.ROLLING_MEAN]` | 76.6 |
| V01 incremental marking forgets pending marks | caught | `tests/regression/test_universe_scaling.py::test_marking_one_change_equals_marking_every_price` | 90.3 |
| V02 NEXT_EVENT fills at the deciding event | caught | `tests/integration/test_order_terms.py::test_market_orders_are_worked_exactly_as_before_under_either_timing` | 6.0 |
| V03 ingested start-stamped bars not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_start_stamped_bars_are_moved_to_the_end_of_their_interval` | 15.5 |
| V04 wire start-stamped bar not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_a_start_stamped_wire_bar_is_moved_by_its_timeframe` | 16.6 |
| V05 long-only ignores working orders | caught | `tests/regression/test_allocation_semantics.py::test_a_working_sale_counts_against_long_only` | 15.6 |
| V06 buying power charged on reductions | caught | `tests/regression/test_risk_projection.py::test_a_fully_invested_account_can_sell_what_it_holds` | 78.3 |
| V07 bool seed accepted | caught | `tests/regression/test_id_stream_is_pinned.py::test_a_seed_that_is_not_a_non_negative_integer_is_refused[True]` | 21.2 |
| V08 statistics accept non-finite input | caught | `tests/regression/test_numeric_refusals.py::test_every_statistic_refuses_a_non_finite_observation_by_position[mean-nan]` | 31.5 |
| V09 normalization currency defaults to USD | caught | `tests/unit/market/test_normalization.py::test_a_quote_or_a_trade_is_refused_by_a_policy_that_names_no_currency` | 133.1 |
| V10 pipeline currency not the account's | caught | `tests/regression/test_v34_invariants.py::test_the_currency_roles_are_named_not_defaulted` | 92.7 |
| V11 misspelled risk severity accepted | caught | `tests/regression/test_risk_projection.py::test_a_severity_is_a_member_and_a_misspelled_one_is_refused` | 78.2 |
| V12 execution fields compared as text | caught | `tests/regression/test_reconciliation_compares_numbers.py::test_a_fill_reported_with_other_exponents_is_not_a_mismatch` | 73.7 |
| V13 daily loss never maintained | caught | `tests/regression/test_risk_projection.py::test_the_daily_loss_is_measured_from_the_trading_days_start` | 77.9 |
| V14 analytics returns per snapshot, not per instant | caught | `tests/unit/analytics/test_analytics.py::test_the_report_takes_one_equity_point_per_instant` | 123.9 |
| V15 position book keeps a replaced position's totals | caught | `tests/regression/test_prf006_fast_paths.py::test_a_position_re_marked_on_its_side_keeps_fresh_sum_totals[USD]` | 72.9 |
| V16 cluster outcomes applied out of submission order | caught | `tests/regression/test_cluster_outcomes_are_ordered.py::test_outcomes_are_recorded_in_submission_order_whatever_finishes_first` | 16.2 |
| V17 halt on strategy failure ignored | caught | `tests/regression/test_strategy_failures_are_reported.py::test_a_run_configured_to_halt_stops_after_the_failing_record` | 86.6 |
| V18 order-size limit exclusive (M01 again) | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_notional_is_approved` | 25.7 |
| W01 a reduction commits its whole notional (ALC-007) | caught | `tests/unit/allocation/test_budget_commitment.py::test_a_fully_invested_book_can_rotate_in_one_batch` | 128.0 |
| W02 reductions not sent first (ALC-007) | caught | `tests/unit/allocation/test_budget_commitment.py::test_a_fully_invested_book_can_rotate_in_one_batch` | 128.7 |
| W03 target rounded to the nearest unit (ALC-006) | caught | `tests/integration/test_target_intents.py::test_whole_units_round_a_target_toward_zero_not_to_the_nearest_unit` | 12.0 |
| W04 lot count divided in the caller's context (NUM-013) | caught | `tests/unit/conventions/test_conventions.py::test_rounding_down_to_a_lot_never_rounds_up_in_a_low_precision_context[3]` | 128.7 |
| W05 LiveSession.settle unpinned (NUM-012) | caught | `tests/regression/test_ambient_decimal_context.py::test_every_entry_point_of_a_run_is_pinned[LiveSession]` | 15.4 |
| W06 ExecutionPipeline.process_record unpinned (NUM-012) | caught | `tests/regression/test_ambient_decimal_context.py::test_every_entry_point_of_a_run_is_pinned[ExecutionPipeline]` | 15.5 |
| W07 a log slice copies the whole log (PRF-007) | caught | `tests/regression/test_log_slicing_complexity.py::test_a_tail_slice_does_not_copy_the_log` | 26.1 |
| W08 a buy limit fills at an open above its limit (EXE-003) | **survived**; caught after TST-012 | `tests/integration/test_order_terms.py::test_a_bar_fills_a_resting_buy_limit_at_its_limit_or_at_a_better_open` | 142.4 |
| W09 a sell limit crosses on a lower bid (EXE-003) | **survived**; caught after TST-012 | `tests/integration/test_order_terms.py::test_a_resting_sell_limit_fills_only_once_the_bid_reaches_it` | 140.7 |
| W10 a buy stop triggers below its stop (EXE-003) | caught | `tests/integration/test_order_terms.py::test_a_stop_waits_for_its_price_then_takes_the_market` | 6.6 |
| W11 subscriptions not enforced (EXE-007) | caught | `tests/integration/test_slices.py::test_a_strategy_not_subscribed_to_slices_is_not_called` | 8.6 |
| W12 a slice closed twice (EXE-004) | caught | `tests/integration/test_slices.py::test_a_slice_is_closed_once_even_across_a_restart` | 8.1 |
| W13 on_start never delivered (EXE-005) | caught | `tests/unit/strategy/test_subscriptions_and_start.py::test_a_strategy_receives_only_what_it_subscribed_to` | 142.6 |
| W14 forward returns ignore the lag (DAT-003) | caught | `tests/integration/test_v32_capabilities.py::test_the_lag_is_part_of_the_identity_and_of_the_numbers` | 13.3 |
| W15 a delisting return dropped (DAT-002) | caught | `tests/unit/factor_library/test_v311_research_integrity.py::test_a_delisted_symbol_realizes_its_terminal_return` | 134.0 |
| W16 a stale venue sequence applied (BRK-002) | caught | `tests/unit/broker/test_venue_sequence.py::test_numbered_amendments_are_ordered_by_the_venue_not_by_delivery` | 132.3 |
| W17 a paper sale's commission credited (BRK-009) | caught | `tests/unit/broker/test_paper_costs.py::test_cash_charges_leave_the_account_on_both_sides` | 130.1 |
| W18 variation margin never paid (ACC-005) | caught | `tests/integration/test_instrument_economics.py::test_a_future_pays_no_notional_and_settles_every_mark_as_variation_margin` | 3.7 |
| W19 a split keeps the mark (ACC-006) | caught | `tests/integration/test_instrument_economics.py::test_a_split_changes_the_count_and_not_the_value` | 4.1 |
| W20 a non-positive price admitted (ACC-007) | caught | `tests/unit/portfolio/test_portfolio_invariants.py::test_malformed_fills_are_rejected[quantity4-price4-commission4-must be positive]` | 144.1 |
| W21 walk-forward selects on the test instants (FEA-003) | caught | `tests/unit/research/test_walk_forward_optimization.py::test_no_selection_call_holds_an_instant_of_its_test_window_or_later` | 140.6 |
| W22 Holm becomes Bonferroni (OFE-005) | caught | `tests/unit/research/test_multiple_testing.py::test_adjusted_p_values_match_r[Correction.HOLM-expected1]` | 134.7 |
| W23 an uncertified cost orthant returned (OFE-002) | caught | `tests/unit/portfolio_optimizer/test_costs_and_lots.py::test_the_costed_optimum_is_the_best_of_every_orthant[9]` | 135.5 |
| W24 Newey-West lag zero (OFE-006) | caught | `tests/unit/factor_library/test_v311_research_integrity.py::test_the_t_statistic_uses_the_overlap_lag_and_is_reported_with_it` | 129.6 |
| W25 tracking error annualized by periods (FEA-002) | caught | `tests/unit/analytics/test_benchmark_statistics.py::test_the_information_ratio_is_active_return_over_tracking_error` | 126.1 |
| W26 carry adds the yield (NUM-005) | caught | `tests/unit/options/test_carry.py::test_merton_1973_dividend_yield_reference` | 127.3 |
| W27 identities render a Decimal by its text (DET-006) | caught | `tests/integration/test_live_requests_and_children.py::test_an_amendment_reaches_the_venue_once_however_it_is_spelled_on_retry` | 3.8 |
| W28 later aliases not restored (INS-001) | caught | `tests/regression/test_dated_aliases.py::test_dated_and_later_aliases_survive_a_round_trip` | 17.0 |
| W29 a held order routed whole (LIV-001) | caught | `tests/integration/test_live_requests_and_children.py::test_a_held_parent_is_never_sent_whole_even_before_its_first_child` | 4.0 |
| W30 issued requests not restored (BRK-003) | caught | `tests/integration/test_live_requests_and_children.py::test_a_restarted_run_still_recognises_a_retried_request` | 3.9 |
| W31 a dated alias claims its end instant (DAT-004) | caught | `tests/regression/test_dated_aliases.py::test_a_reused_ticker_names_the_new_company_after_its_reissue` | 16.7 |
| W32 a minute is 61 seconds (DAT-005) | caught | `tests/regression/test_bar_stamp_convention.py::test_a_start_stamped_wire_bar_is_moved_by_its_timeframe` | 15.7 |
| W33 the engine build left out of the manifest (REP-002) | caught | `tests/unit/lifecycle/test_engine_build.py::test_the_build_enters_the_manifest_identity_only_when_recorded` | 145.5 |
| W35 a same-side re-mark leaves the book's totals (PRF-006) | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_at_non_round_prices` | 4.7 |
| W36 an older view reads a write made after it (PRF-008) | caught | `tests/regression/test_ambient_decimal_context.py (collection error)` | 2.0 |
| W37 a rebase keeps a deleted key (PRF-008) | caught | `tests/regression/test_contributions_are_retired.py::test_unpriced_requests_retire_their_contributions` | 16.9 |
| W34 an upgraded OMS payload discarded (OMS-001) | caught | `tests/regression/test_oms_snapshot_schema.py::test_a_version_one_order_is_a_day_order_with_no_expiry_or_trigger` | 34.2 |

### W.4 Run against v3.12.0

The method of W.2 and W.3, now kept in the repository (`docs/audit/scripts/mutation_v3_12.py`):
`git archive HEAD` unpacked into three scratch copies, one mutation at a time on each, the whole
suite with `-x` and the tests that read a clock deselected test by test, an unmutated baseline
first. The table: the seventy-nine mutations of W.3 (M21 re-pointed where v3.12 moved the
duplicate check) and forty-seven of v3.12's own behaviour (X01–X47).

**First run**, on the tree before the routing index (commit `d710da4`): baseline passed — 8,526
passed, 89 deselected (170.7 s). **117 of 123 caught.** Six survived. Five were rules no test
pinned — valuing a run in a currency other than its own (X06), a reduction that leaves its bucket
over a classification limit (X13; the one reduction tested landed exactly on the cap), an evidence
value refused before any byte is written (X19), and the research policy's windows and ruin bound
reaching their reports (X39, X40; the only test policy used the values the mutants hard-coded).
TST-013 added a test for each. The sixth, X17, is **equivalent**: it removes the clause that a
share change applies only once knowable, and `ShareCountChange` refuses a change known after it
takes effect, so the clause is implied by the change's own validation and no input distinguishes
the mutant. The clause is kept as the rule's statement.

**Final tree** (commit `c182af7`): baseline passed — 8,547 passed, 90 deselected (172.9 s). Every
mutation whose file changed after the first run (the execution pipeline and the strategy runtime,
for PRF-010), the three routing-index mutations (X45–X47), and the six survivors: **21 of 22
caught**; X17 survives, as it must.

**Release tree** (commit `b775830`, after `plugins`, `optimizer` and the reporting dashboards were
removed): every pattern still applies exactly once (`--check`), and every test an earlier run
credited with a catch still exists. Baseline passed — 8,495 passed, 88 deselected (266.5 s). All
126 mutations ran: **125 of 126 caught**; X17 survives, as it must. The whole run took 13,086
mutation-seconds on three copies; the longest single mutation, 260 s.

| Mutation | Result | Caught by | Seconds |
| --- | --- | --- | ---: |
| M01 risk order-size > to >= | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_quantity_is_approved` | 32.4 |
| M02 duplicate fill applied (remove DUPLICATE check) | caught | `tests/regression/test_ambient_decimal_context.py` | 3.1 |
| M03 to_money rounds down | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_with_commission_and_slippage` | 14.2 |
| M04 partial close relieves wrong basis | caught | `tests/integration/test_mark_to_market_pipeline.py::test_partial_close_realizes_only_the_closed_portion` | 15.6 |
| M05 fill during cancel-pending drops pending | caught | `tests/unit/core/test_order_lifecycle_table.py::test_fills_are_only_legal_once_the_order_is_working_at_the_venue` | 135.1 |
| M06 FX future-dated rate accepted | caught | `tests/integration/test_external_information.py::test_a_conversion_refuses_what_has_no_currency_and_a_rate_from_the_future` | 6.7 |
| M07 risk rejection does not release reservation | caught (and on the final tree) | `tests/integration/test_backtest_pipeline.py::test_a_request_risk_refuses_never_reaches_the_oms` | 4.2 |
| M08 quote price uses bid not mid | caught (and on the final tree) | `tests/integration/test_execution_pipeline.py::test_quote_drives_full_end_to_end_execution_pipeline` | 15.8 |
| M09 sample variance divides by n | caught | `tests/unit/analytics/test_analytics.py::test_annualized_volatility` | 133.3 |
| M10 purge boundary >= to > | caught | `tests/integration/test_v32_capabilities.py::test_walk_forward_folds_over_the_real_index_never_contaminate` | 15.5 |
| M11 forward return look-ahead shift | caught | `tests/integration/test_v32_capabilities.py::test_a_delisting_set_must_be_the_one_the_study_names` | 15.8 |
| M12 PIT visibility strict < | caught | `tests/unit/alt_data/test_fundamentals.py::test_a_quarter_is_invisible_until_it_is_published` | 136.7 |
| M13 stale venue status applied | caught | `tests/regression/test_v39_invariants.py::test_replaying_any_stream_changes_nothing_and_breaks_nothing` | 130.2 |
| M14 out-of-order record accepted | caught | `tests/integration/test_provider_source_session.py::test_an_unordered_session_skips_the_regressing_record_and_records_it` | 9.2 |
| M15 run store skips digest check | caught | `tests/regression/test_run_state_store.py::test_an_altered_payload_fails_its_digest` | 92.3 |
| M16 OMS overfill accepted | caught | `tests/unit/core/test_order_lifecycle_table.py::test_a_complete_fill_must_complete_the_order_exactly` | 138.2 |
| M17 pearson uses population denominators inconsistently | caught | `tests/regression/test_numeric_refusals.py::test_correlation_is_defined_at_any_representable_magnitude[tiny]` | 40.5 |
| M18 contribution split last share off by quantum | caught | `tests/integration/test_instrument_economics.py::test_a_split_changes_the_count_and_not_the_value` | 6.8 |
| M19 capability UNDECLARED treated as SUPPORTED | caught | `tests/regression/test_v39_invariants.py::test_compatible_means_every_check_supported_and_nothing_else_does` | 127.4 |
| M20 unpriced request not dropped (price 0 order) | caught (and on the final tree) | `tests/integration/test_mark_to_market_pipeline.py::test_request_for_an_asset_with_no_market_price_never_reaches_the_oms` | 17.0 |
| M21 dataset duplicate row accepted | caught | `tests/integration/test_trade_prints.py::TestTwoPrintsAtOneInstant::test_one_identifier_twice_is_a_duplicate_refused_or_resolved_by_policy` | 15.3 |
| M22 FX staleness check disabled | caught | `tests/regression/test_fx_rate_feed.py::test_the_feed_does_not_decide_whether_a_rate_is_too_old_to_use` | 28.6 |
| M23 allocation budget check ignores outstanding | caught | `tests/regression/test_outstanding_capital_guard.py::test_six_external_events_no_longer_commit_five_times_the_budget` | 76.9 |
| M24 moving average window includes next value | caught | `tests/regression/test_research_cannot_see_the_future.py::test_no_feature_kind_changes_when_the_future_is_removed[FeatureKind.ROLLING_MEAN]` | 81.6 |
| V01 incremental marking forgets pending marks | caught | `tests/regression/test_universe_scaling.py::test_marking_one_change_equals_marking_every_price` | 99.3 |
| V02 NEXT_EVENT fills at the deciding event | caught (and on the final tree) | `tests/integration/test_order_terms.py::test_market_orders_are_worked_exactly_as_before_under_either_timing` | 8.4 |
| V03 ingested start-stamped bars not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_start_stamped_bars_are_moved_to_the_end_of_their_interval` | 18.2 |
| V04 wire start-stamped bar not moved | caught | `tests/regression/test_bar_stamp_convention.py::test_a_start_stamped_wire_bar_is_moved_by_its_timeframe` | 17.4 |
| V05 long-only ignores working orders | caught (and on the final tree) | `tests/regression/test_allocation_semantics.py::test_a_working_sale_counts_against_long_only` | 16.3 |
| V06 buying power charged on reductions | caught | `tests/regression/test_risk_projection.py::test_a_fully_invested_account_can_sell_what_it_holds` | 86.5 |
| V07 bool seed accepted | caught | `tests/regression/test_id_stream_is_pinned.py::test_a_seed_that_is_not_a_non_negative_integer_is_refused[True]` | 28.4 |
| V08 statistics accept non-finite input | caught | `tests/regression/test_numeric_refusals.py::test_every_statistic_refuses_a_non_finite_observation_by_position[mean-nan]` | 38.9 |
| V09 normalization currency defaults to USD | caught | `tests/unit/market/test_normalization.py::test_a_quote_or_a_trade_is_refused_by_a_policy_that_names_no_currency` | 135.9 |
| V10 pipeline currency not the account's | caught (and on the final tree) | `tests/regression/test_v34_invariants.py::test_the_currency_roles_are_named_not_defaulted` | 96.7 |
| V11 misspelled risk severity accepted | caught | `tests/regression/test_risk_projection.py::test_a_severity_is_a_member_and_a_misspelled_one_is_refused` | 86.1 |
| V12 execution fields compared as text | caught | `tests/regression/test_reconciliation_compares_numbers.py::test_a_fill_reported_with_other_exponents_is_not_a_mismatch` | 78.3 |
| V13 daily loss never maintained | caught | `tests/regression/test_risk_projection.py::test_the_daily_loss_is_measured_from_the_trading_days_start` | 85.0 |
| V14 analytics returns per snapshot, not per instant | caught | `tests/unit/analytics/test_analytics.py::test_the_report_takes_one_equity_point_per_instant` | 128.3 |
| V15 position book keeps a replaced position's totals | caught | `tests/regression/test_prf006_fast_paths.py::test_a_position_re_marked_on_its_side_keeps_fresh_sum_totals[USD]` | 76.2 |
| V16 cluster outcomes applied out of submission order | caught | `tests/regression/test_cluster_outcomes_are_ordered.py::test_outcomes_are_recorded_in_submission_order_whatever_finishes_first` | 22.9 |
| V17 halt on strategy failure ignored | caught | `tests/regression/test_strategy_failures_are_reported.py::test_a_run_configured_to_halt_stops_after_the_failing_record` | 89.4 |
| V18 order-size limit exclusive (M01 again) | caught | `tests/regression/test_mutation_pins.py::test_an_order_of_exactly_the_permitted_notional_is_approved` | 32.6 |
| W01 a reduction commits its whole notional (ALC-007) | caught | `tests/unit/allocation/test_budget_commitment.py::test_a_fully_invested_book_can_rotate_in_one_batch` | 127.1 |
| W02 reductions not sent first (ALC-007) | caught | `tests/unit/allocation/test_budget_commitment.py::test_a_fully_invested_book_can_rotate_in_one_batch` | 127.5 |
| W03 target rounded to the nearest unit (ALC-006) | caught | `tests/integration/test_target_intents.py::test_whole_units_round_a_target_toward_zero_not_to_the_nearest_unit` | 13.9 |
| W04 lot count divided in the caller's context (NUM-013) | caught | `tests/unit/conventions/test_conventions.py::test_rounding_down_to_a_lot_never_rounds_up_in_a_low_precision_context[3]` | 129.9 |
| W05 LiveSession.settle unpinned (NUM-012) | caught | `tests/regression/test_ambient_decimal_context.py::test_every_entry_point_of_a_run_is_pinned[LiveSession]` | 17.5 |
| W06 ExecutionPipeline.process_record unpinned (NUM-012) | caught (and on the final tree) | `tests/regression/test_ambient_decimal_context.py::test_every_entry_point_of_a_run_is_pinned[ExecutionPipeline]` | 16.3 |
| W07 a log slice copies the whole log (PRF-007) | caught | `tests/regression/test_checkpoints.py::test_a_chain_reads_back_as_the_full_capture[1-retained]` | 19.3 |
| W08 a buy limit fills at an open above its limit (EXE-003) | caught (and on the final tree) | `tests/integration/test_order_terms.py::test_a_bar_fills_a_resting_buy_limit_at_its_limit_or_at_a_better_open` | 8.1 |
| W09 a sell limit crosses on a lower bid (EXE-003) | caught (and on the final tree) | `tests/integration/test_order_terms.py::test_a_resting_sell_limit_fills_only_once_the_bid_reaches_it` | 8.0 |
| W10 a buy stop triggers below its stop (EXE-003) | caught (and on the final tree) | `tests/integration/test_order_terms.py::test_a_stop_waits_for_its_price_then_takes_the_market` | 8.2 |
| W11 subscriptions not enforced (EXE-007) | caught (and on the final tree) | `tests/integration/test_external_information.py::test_a_subscription_can_name_one_subject` | 6.3 |
| W12 a slice closed twice (EXE-004) | caught | `tests/integration/test_external_information.py::test_an_observation_arrives_between_the_records_it_falls_between` | 6.3 |
| W13 on_start never delivered (EXE-005) | caught | `tests/unit/strategy/test_subscriptions_and_start.py::test_a_strategy_receives_only_what_it_subscribed_to` | 161.8 |
| W14 forward returns ignore the lag (DAT-003) | caught | `tests/integration/test_v32_capabilities.py::test_the_lag_is_part_of_the_identity_and_of_the_numbers` | 15.1 |
| W15 a delisting return dropped (DAT-002) | caught | `tests/unit/factor_library/test_v311_research_integrity.py::test_a_delisted_symbol_realizes_its_terminal_return` | 134.0 |
| W16 a stale venue sequence applied (BRK-002) | caught | `tests/unit/broker/test_venue_sequence.py::test_numbered_amendments_are_ordered_by_the_venue_not_by_delivery` | 130.3 |
| W17 a paper sale's commission credited (BRK-009) | caught | `tests/unit/broker/test_paper_costs.py::test_cash_charges_leave_the_account_on_both_sides` | 129.1 |
| W18 variation margin never paid (ACC-005) | caught | `tests/integration/test_instrument_economics.py::test_a_future_pays_no_notional_and_settles_every_mark_as_variation_margin` | 6.3 |
| W19 a split keeps the mark (ACC-006) | caught | `tests/integration/test_instrument_economics.py::test_a_split_changes_the_count_and_not_the_value` | 6.0 |
| W20 a non-positive price admitted (ACC-007) | caught | `tests/unit/portfolio/test_portfolio_invariants.py::test_malformed_fills_are_rejected[quantity4-price4-commission4-must be positive]` | 135.7 |
| W21 walk-forward selects on the test instants (FEA-003) | caught | `tests/unit/research/test_walk_forward_optimization.py::test_no_selection_call_holds_an_instant_of_its_test_window_or_later` | 161.3 |
| W22 Holm becomes Bonferroni (OFE-005) | caught | `tests/unit/research/test_multiple_testing.py::test_adjusted_p_values_match_r[Correction.HOLM-expected1]` | 159.3 |
| W23 an uncertified cost orthant returned (OFE-002) | caught | `tests/unit/portfolio_optimizer/test_costs_and_lots.py::test_the_costed_optimum_is_the_best_of_every_orthant[9]` | 151.1 |
| W24 Newey-West lag zero (OFE-006) | caught | `tests/unit/factor_library/test_v311_research_integrity.py::test_the_t_statistic_uses_the_overlap_lag_and_is_reported_with_it` | 131.5 |
| W25 tracking error annualized by periods (FEA-002) | caught | `tests/unit/analytics/test_benchmark_statistics.py::test_the_information_ratio_is_active_return_over_tracking_error` | 128.6 |
| W26 carry adds the yield (NUM-005) | caught | `tests/unit/options/test_carry.py::test_merton_1973_dividend_yield_reference` | 134.1 |
| W27 identities render a Decimal by its text (DET-006) | caught | `tests/integration/test_live_requests_and_children.py::test_an_amendment_reaches_the_venue_once_however_it_is_spelled_on_retry` | 6.5 |
| W28 later aliases not restored (INS-001) | caught | `tests/regression/test_dated_aliases.py::test_dated_and_later_aliases_survive_a_round_trip` | 23.7 |
| W29 a held order routed whole (LIV-001) | caught | `tests/integration/test_live_requests_and_children.py::test_a_held_parent_is_never_sent_whole_even_before_its_first_child` | 6.3 |
| W30 issued requests not restored (BRK-003) | caught | `tests/integration/test_live_requests_and_children.py::test_a_restarted_run_still_recognises_a_retried_request` | 6.8 |
| W31 a dated alias claims its end instant (DAT-004) | caught | `tests/regression/test_dated_aliases.py::test_a_reused_ticker_names_the_new_company_after_its_reissue` | 23.2 |
| W32 a minute is 61 seconds (DAT-005) | caught | `tests/regression/test_bar_stamp_convention.py::test_a_start_stamped_wire_bar_is_moved_by_its_timeframe` | 17.5 |
| W33 the engine build left out of the manifest (REP-002) | caught | `tests/unit/lifecycle/test_engine_build.py::test_the_build_enters_the_manifest_identity_only_when_recorded` | 136.1 |
| W34 an upgraded OMS payload discarded (OMS-001) | caught | `tests/regression/test_oms_snapshot_schema.py::test_a_version_one_order_is_a_day_order_with_no_expiry_or_trigger` | 39.1 |
| W35 a same-side re-mark leaves the book's totals (PRF-006) | caught | `tests/integration/test_backtest_pipeline.py::test_the_accounting_identity_holds_at_non_round_prices` | 4.6 |
| W36 an older view reads a write made after it (PRF-008) | caught | `tests/regression/test_ambient_decimal_context.py` | 1.4 |
| W37 a rebase keeps a deleted key (PRF-008) | caught | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_every_order_reads_the_gross_the_member_sum_gives[0]` | 4.4 |
| X01 a constant series explains all of nothing: r-squared zero, not undefined (NUM-003) | caught | `tests/unit/common/test_statistics.py::test_r_squared_of_a_constant_series_is_undefined_not_zero` | 130.1 |
| X02 the normal CDF by 1 + erf, which underflows in the lower tail (NUM-004) | caught | `tests/unit/options/test_carry.py::test_a_zero_yield_reproduces_the_plain_formula_bit_for_bit` | 132.5 |
| X03 theta on a 365-day year against a 365.25-day pricing year (NUM-004) | caught | `tests/unit/options/test_carry.py::test_greeks_match_central_differences[85-OptionType.CALL-no-yield]` | 132.4 |
| X04 an ill-conditioned design solved anyway (NUM-007) | caught | `tests/unit/common/test_linalg.py::test_a_rank_deficient_design_is_refused` | 129.2 |
| X05 a directory never flushed after a rename (PER-003) | caught | `tests/regression/test_durable_writes.py::test_a_checkpoint_and_its_directories_survive_a_crash` | 25.3 |
| X06 a backtest valued in its own currency whatever was asked (API-004) | **survived**; caught after TST-013 | `tests/regression/test_backtest_result_valuation.py::test_valuation_in_values_in_the_currency_it_is_asked_for` | 17.0 |
| X07 a duplicate intent sized twice (ALC-004) | caught | `tests/unit/allocation/test_intent_intake.py::test_a_duplicate_intent_is_counted_once_and_the_duplicate_is_recorded` | 126.5 |
| X08 an instant's resolution reported as exact (DAT-008) | caught | `tests/unit/common/test_time_precision.py::test_an_instant_resolves_to_a_quarter_microsecond_at_current_epochs` | 127.3 |
| X09 a day order expires at its first window's close, before lunch (EXE-010) | caught | `tests/integration/test_day_order_sessions.py::test_a_day_order_is_good_for_the_whole_trading_day_not_the_window_in_progress` | 5.9 |
| X10 strategy capital ceilings never enforced (OFE-003) | caught | `tests/integration/test_strategy_ceilings.py::test_an_intent_over_its_strategys_ceiling_is_refused_and_another_strategys_is_not` | 10.0 |
| X11 a budget's currency dropped on restore (PER-006) | caught | `tests/integration/test_strategy_ceilings.py::test_the_ledger_and_the_budget_currency_survive_an_allocation_snapshot` | 9.9 |
| X12 classification limits never checked (OFE-001) | caught | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_every_order_reads_the_gross_the_member_sum_gives[0]` | 4.9 |
| X13 a reduction refused while its bucket is over the limit (OFE-001) | **survived**; caught after TST-013 | `tests/integration/test_classification_limits.py::test_a_reduction_that_leaves_its_bucket_over_the_limit_still_passes` | 5.9 |
| X14 a labelled limit ignored for its own bucket (OFE-001) | caught | `tests/integration/test_classification_limits.py::test_a_labelled_limit_replaces_the_dimensions_for_its_bucket` | 6.3 |
| X15 an observation delivered with the records of its own instant (OFE-009) | caught | `tests/integration/test_external_information.py::test_one_known_at_a_records_instant_comes_after_it_and_before_its_slice` | 6.3 |
| X16 a share change before publication applied again (OFE-011) | caught | `tests/integration/test_external_information.py::test_a_split_before_publication_or_of_another_issuer_is_not_applied` | 6.6 |
| X17 a share change applied before it was knowable (OFE-011) | **survived** — equivalent (see below) |  | 157.1 |
| X18 a revision published ahead of the figure it revises accepted (OFE-011) | caught | `tests/integration/test_external_information.py::test_a_stream_refuses_what_a_set_refuses_and_is_left_unchanged` | 6.6 |
| X19 evidence under an identity overwritten by another value (OFE-016) | **survived**; caught after TST-013 | `tests/regression/test_evidence_store.py::test_a_refused_value_leaves_no_bytes_behind` | 25.2 |
| X20 a health window includes its open start (OFE-017) | caught | `tests/unit/lifecycle/test_health.py::TestHealthWindow::test_only_observations_inside_the_window_are_judged` | 134.2 |
| X21 an over-fixed point certified (PRF-005) | caught | `tests/unit/portfolio_optimizer/test_factor_quadratic.py::test_the_solver_agrees_with_the_dense_method[329]` | 157.7 |
| X22 a checkpoint chain's links not verified (PRF-004) | caught | `tests/regression/test_checkpoints.py::test_an_altered_checkpoint_breaks_the_next_link` | 22.5 |
| X23 history beyond the retention window answered short (PRF-004) | caught | `tests/regression/test_retention.py::test_a_question_older_than_the_window_is_refused_not_answered_short` | 85.7 |
| X24 retention keeps one entry fewer than declared (PRF-004) | caught | `tests/regression/test_checkpoints.py::test_every_cut_log_sits_where_the_checkpoint_says` | 17.4 |
| X25 an order bound at another account than declared not reported (BRK-004) | caught | `tests/unit/lifecycle/test_reconcile_accounts.py::TestAssignment::test_an_order_bound_at_an_account_it_was_not_declared_for` | 133.0 |
| X26 positions compared against one account, not the sum (BRK-004) | caught | `tests/unit/lifecycle/test_reconcile_accounts.py::TestAgreement::test_a_book_spread_across_two_accounts_reconciles_in_one_pass` | 132.6 |
| X27 a filled order no account was declared for passed over (BRK-004) | caught | `tests/unit/lifecycle/test_reconcile_accounts.py::TestAssignment::test_fills_of_an_undeclared_unbound_order_are_unassigned` | 132.7 |
| X28 trade prints deduplicated by instant (FEA-004) | caught | `tests/integration/test_trade_prints.py::TestADeclaredTableOfPrints::test_every_print_is_read_with_its_identifier_and_its_flag` | 14.5 |
| X29 a print's aggressor dropped at normalization (FEA-004) | caught | `tests/integration/test_trade_prints.py::TestThePrintsReachTheExecutionPath::test_normalization_carries_the_identifier_and_the_flag` | 14.4 |
| X30 a v3.11 tick read with no aggressor field (FEA-004 upgrade) | caught | `tests/integration/test_trade_prints.py::test_a_version_5_tick_is_read_as_unflagged` | 14.5 |
| X31 every quote internally consistent (DAT-009) | caught | `tests/integration/test_trade_prints.py::TestTwoPrintsAtOneInstant::test_a_quote_reported_invalid_is_dropped_as_invalid_too` | 14.6 |
| X32 the cell gradient not gated by the forget gate (SCF-004) | caught | `tests/unit/deep_learning/test_sequence_backprop.py::test_lstm_parameter_gradients_match_central_differences` | 129.9 |
| X33 the softmax Jacobian without its weights (SCF-004) | caught | `tests/unit/deep_learning/test_sequence_backprop.py::test_attention_gradients_match_central_differences[queries]` | 131.5 |
| X34 a sweep that minimizes picks the largest score (SCF-003) | caught | `tests/unit/research/test_signal_and_robustness.py::test_a_sweep_selects_the_lowest_score_when_lower_is_better` | 159.8 |
| X35 a cancelled assigned job keeps its worker's slot (SCF-003) | caught | `tests/unit/distributed/test_distributed.py::test_cancelling_an_assigned_job_frees_its_worker_slot` | 132.8 |
| X36 session timers fire at the opposite boundary (SCF-003) | caught | `tests/unit/scheduler/test_session_timers.py::test_a_session_open_timer_fires_at_each_trading_days_open_and_skips_the_weekend` | 159.5 |
| X37 a report's Decimal written as a float (SCF-003) | caught | `tests/unit/reporting/test_reporting.py::test_export_json` | 159.4 |
| X38 the research Sharpe ignores the risk-free rate (RES-001) | caught | `tests/unit/research/test_research.py::test_annualization_uses_the_periods_it_is_given` | 162.0 |
| X39 the research walk-forward ignores the policy's windows (RES-001) | **survived**; caught after TST-013 | `tests/unit/research/test_research.py::test_the_policy_reaches_every_report_it_bounds` | 157.1 |
| X40 the research ruin threshold ignores the policy (RES-001) | **survived**; caught after TST-013 | `tests/unit/research/test_research.py::test_the_policy_reaches_every_report_it_bounds` | 156.4 |
| X41 a group's gross not moved by a re-mark (v3.12 stress finding) | caught | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_every_order_reads_the_gross_the_member_sum_gives[0]` | 4.6 |
| X42 an emptied group keeps a zero entry (v3.12 stress finding) | caught | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_every_order_reads_the_gross_the_member_sum_gives[3]` | 6.4 |
| X43 a kept bucket gross leaves out working orders (v3.12 stress finding) | caught (and on the final tree) | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_every_order_reads_the_gross_the_member_sum_gives[0]` | 4.5 |
| X44 totals kept for one registry read under another (v3.12 stress finding) | caught (and on the final tree) | `tests/integration/test_bucket_gross_kept_by_the_book.py::test_a_registry_other_than_the_one_grouped_by_is_summed_instead` | 5.9 |
| X45 an event misses the strategies subscribed to everything (PRF-010) | caught (final tree) | `tests/regression/test_ambient_decimal_context.py` | 1.3 |
| X46 overlapping subscriptions reach a strategy twice, out of order (PRF-010) | caught (final tree) | `tests/unit/strategy/test_routing_index.py::test_the_index_reaches_exactly_the_strategies_that_accept_in_their_order[0]` | 162.4 |
| X47 an index handed on when a strategy's subscriptions changed (PRF-010) | caught (final tree) | `tests/unit/strategy/test_routing_index.py::test_an_evolution_that_changes_subscriptions_builds_its_own_index` | 164.5 |

### W.5 Run against v3.13.0

`docs/audit/scripts/mutation_v3_13.py` loads v3.12's harness and runs it unchanged: `git
archive HEAD` unpacked into three scratch copies, one mutation at a time on each, the whole
suite with `-x` and the tests that read a clock deselected test by test, an unmutated baseline
first. The table: v3.12's 126 mutations (M01–M24, V01–V18, W01–W37, X01–X47; every pattern still
applies exactly once to the v3.13 tree) and fifty-six of v3.13's own behaviour (Y01–Y56): the
lattice, the term structure, the optimal split, urgency, the schedule's cost and the shortfall
read against it, iceberg tranches, the rerun harness, the lock-file reader, cron, the
liquidation price, checkpoint segments, the broker codec's qualifier, exchange-rate factors,
factor structures and a covariance's bulk checks, the long-short box, the v1 optimizer, the
research path's imports and the intent contract.

**First run**, the v3.13 mutations only, on commit `90a66e5`: the harness refused to start on the
commit before it — an unmutated tree that fails means no detection does, and that tree's
committed certificate still recorded the API before the factor structure joined it. Baseline
passed — 8,934 passed, 88 deselected (183.7 s). **49 of 56 caught.** The seven that survived were
each a rule no test pinned, now each pinned (TST-016): a rerun over other bytes under the
recorded dataset version (Y23) or on another engine source (Y24); the rerun's limit of ten
differences, which the tests read from the constant (Y27); the position's fees in the
mark-notional liquidation price (Y37); and a checkpoint segment over an order entry replaced
(Y40), lost or reordered (Y41, Y43) since the last link — the canonical path never removes an
order, so the last two are reached by a state built by hand. Each was re-checked by applying it
to the working tree against its new test before the release run.

**Release tree** (commit `b6a0fef`; nothing after it changed code or a test, only documents):
every pattern applied exactly once, none refused. Baseline passed — 8,947 passed, 88 deselected
(184.2 s). All 182 mutations ran: **181 of 182 caught**; X17 survives, as it must. v3.12's 126:
125 caught, as on v3.12.0's release tree. v3.13's 56: all caught, the seven that survived the
first run each by the test TST-016 added for it. The whole run took 16,441 mutation-seconds on
three copies; the longest single mutation, 183 s (Y35). Y01 and Y05 change prices the release
certificate records, and its check, which the suite reaches before the unit tests, is the first
to fail.

| Mutation | Result | Caught by | Seconds |
| --- | --- | --- | ---: |
| Y01 an American option never exercised early (NUM-006) | caught | `tests/regression/test_release_certification.py::test_the_build_matches_its_committed_certificate` | 84.5 |
| Y02 the lattice's spot not net of the dividends before expiry (NUM-006) | caught | `tests/unit/options/test_binomial.py::test_an_escrowed_dividend_prices_a_european_option_as_the_spot_net_of_it` | 147.4 |
| Y03 the exercise value forgets the dividends still to come (NUM-006) | caught | `tests/unit/options/test_binomial.py::test_a_dividend_just_before_expiry_makes_early_exercise_of_a_call_pay` | 146.4 |
| Y04 a lattice too coarse for a probability not refused (NUM-006) | caught | `tests/unit/options/test_binomial.py::test_a_lattice_too_coarse_for_its_carry_and_volatility_is_refused_not_clamped` | 149.9 |
| Y05 the up branch not discounted (NUM-006) | caught | `tests/regression/test_release_certification.py::test_the_build_matches_its_committed_certificate` | 86.4 |
| Y06 the lattice's step ceiling not enforced (NUM-006) | caught | `tests/unit/options/test_binomial.py::test_a_step_count_outside_its_range_is_refused[5001]` | 148.6 |
| Y07 a calendar arbitrage interpolated rather than refused (FEA-005) | caught | `tests/unit/options/test_binomial.py::test_falling_total_variance_is_a_calendar_arbitrage_and_is_refused` | 146.8 |
| Y08 an expiry beyond the last quoted one extrapolated (FEA-005) | caught | `tests/unit/options/test_binomial.py::test_an_expiry_outside_the_quoted_range_is_not_extrapolated[1.5]` | 145.1 |
| Y09 expiries joined in volatility, not total variance (FEA-005) | caught | `tests/unit/options/test_binomial.py::test_between_expiries_the_total_variance_is_linear_in_maturity` | 145.1 |
| Y10 a falling marginal cost not refused by the optimal split (BRK-005) | caught | `tests/unit/execution/test_optimal_split.py::test_a_cost_whose_marginal_falls_is_refused` | 140.0 |
| Y11 fixed-charge venues tried only all at once (BRK-005) | caught | `tests/unit/execution/test_optimal_split.py::test_the_optimal_split_is_the_cheapest_of_every_allocation[buy]` | 140.6 |
| Y12 the ceiling on fixed-charge venues not enforced (BRK-005) | caught | `tests/unit/execution/test_optimal_split.py::test_too_many_venues_with_a_fixed_charge_are_refused` | 142.2 |
| Y13 a fixed-charge venue chosen in may take nothing (BRK-005) | caught | `tests/unit/execution/test_optimal_split.py::test_the_optimal_split_is_the_cheapest_of_every_allocation[buy]` | 140.4 |
| Y14 urgency from the volatility, not the variance (BRK-006) | caught | `tests/unit/execution/test_schedule_cost.py::test_no_schedule_of_the_same_intervals_costs_less[0.000002]` | 141.6 |
| Y15 urgency ignores the permanent impact's share of an interval (BRK-006) | caught | `tests/unit/execution/test_schedule_cost.py::test_no_schedule_of_the_same_intervals_costs_less[0.000002]` | 141.7 |
| Y16 a schedule's temporary cost at eta rather than eta-tilde (FEA-008) | caught | `tests/unit/execution/test_schedule_cost.py::test_a_straight_schedule_costs_what_hand_arithmetic_says` | 139.9 |
| Y17 a schedule's variance counts each holding before its trade (FEA-008) | caught | `tests/unit/execution/test_schedule_cost.py::test_a_straight_schedule_costs_what_hand_arithmetic_says` | 140.2 |
| Y18 a schedule's permanent cost not halved (FEA-008) | caught | `tests/unit/execution/test_schedule_cost.py::test_a_straight_schedule_costs_what_hand_arithmetic_says` | 141.2 |
| Y19 two parents draw the same tranches (BRK-006) | caught | `tests/unit/execution/test_urgency_estimate_and_randomized_icebergs.py::test_two_parents_worked_with_one_seed_draw_differently` | 141.1 |
| Y20 every tranche of a parent the same draw (BRK-006) | caught | `tests/unit/execution/test_urgency_estimate_and_randomized_icebergs.py::test_randomized_tranches_stay_within_the_spread_and_sum_to_the_parent` | 141.5 |
| Y21 a measured shortfall read without its explicit costs (FEA-008) | caught | `tests/unit/execution/test_schedule_cost.py::test_a_measured_shortfall_is_read_from_arrival_beside_the_model` | 140.7 |
| Y22 an unfinished order compared with a completed schedule (FEA-008) | caught | `tests/unit/execution/test_schedule_cost.py::test_a_shortfall_unlike_the_models_is_refused` | 140.5 |
| Y23 a rerun over other dataset bytes not refused (REP-003) | caught (survived the first run; TST-016) | `tests/unit/lifecycle/test_rerun_harness.py::test_each_recorded_input_is_compared_on_its_own_and_named` | 143.6 |
| Y24 a rerun on another engine source not refused (REP-003) | caught (survived the first run; TST-016) | `tests/unit/lifecycle/test_rerun_harness.py::test_each_recorded_input_is_compared_on_its_own_and_named` | 145.7 |
| Y25 an original result the manifest does not identify accepted (REP-003) | caught | `tests/unit/lifecycle/test_rerun_harness.py::test_what_cannot_be_rerun_is_refused` | 144.0 |
| Y26 a rerun of other inputs run anyway (REP-003) | caught | `tests/unit/lifecycle/test_rerun_harness.py::test_another_dataset_or_engine_is_refused_before_anything_runs` | 143.4 |
| Y27 more differences reported than the stated limit (REP-003) | caught (survived the first run; TST-016) | `tests/unit/lifecycle/test_rerun_harness.py::test_a_divergence_is_reported_with_where_the_records_part` | 144.2 |
| Y28 a requirements file read as the whole closure (OFE-019) | caught | `tests/unit/lifecycle/test_lock_file_reader.py::test_a_file_without_the_compilers_header_is_not_read_as_a_closure` | 144.1 |
| Y29 a digest kept from a pin listing several artifacts (OFE-019) | caught | `tests/unit/lifecycle/test_lock_file_reader.py::test_a_pip_compile_lock_is_an_exact_closure_with_its_single_hashes` | 143.7 |
| Y30 an uncompiled file read as a pip-compile lock (OFE-019) | caught | `tests/unit/lifecycle/test_lock_file_reader.py::test_a_file_without_the_compilers_header_is_not_read_as_a_closure` | 144.8 |
| Y31 a lock for several environments accepted (OFE-019) | caught | `tests/unit/lifecycle/test_lock_file_reader.py::test_one_distribution_at_two_versions_is_refused` | 145.6 |
| Y32 both day fields restricted: and, not Vixie cron's or (DAT-006) | caught | `tests/unit/scheduler/test_cron.py::test_two_restricted_day_fields_fire_on_either` | 176.2 |
| Y33 a minute inside a spring-forward gap fires (DAT-006) | caught | `tests/unit/scheduler/test_cron.py::test_a_minute_inside_the_spring_forward_gap_does_not_fire` | 179.9 |
| Y34 a repeated minute fires at its second occurrence (DAT-006) | caught | `tests/unit/scheduler/test_cron.py::test_a_repeated_minute_fires_once_at_its_first_occurrence` | 179.4 |
| Y35 a day of month given by a step from * read as restricted (DAT-006) | caught | `tests/unit/scheduler/test_cron.py::test_a_stepped_star_counts_as_unrestricted` | 182.8 |
| Y36 funding left out of the entry-notional liquidation price (NUM-014) | caught | `tests/unit/crypto/test_crypto.py::test_fees_bring_liquidation_closer_and_received_funding_pushes_it_away` | 142.1 |
| Y37 fees left out of the mark-notional liquidation price (NUM-014) | caught (survived the first run; TST-016) | `tests/unit/crypto/test_crypto.py::test_at_the_price_returned_equity_is_exactly_the_requirement[PositionSide.LONG-MaintenanceBasis.MARK_NOTIONAL]` | 141.3 |
| Y38 a long no fall liquidates given a price at or below zero (NUM-014) | caught | `tests/unit/crypto/test_crypto.py::test_a_long_at_low_leverage_has_no_liquidation_price` | 143.0 |
| Y39 a position liquidated the moment it opens not refused (NUM-014) | caught | `tests/unit/crypto/test_crypto.py::test_a_position_already_at_its_maintenance_margin_is_refused` | 144.1 |
| Y40 a replaced order entry not written to the segment (PRF-011) | caught (survived the first run; TST-016) | `tests/regression/test_checkpoints.py::test_an_order_that_changed_between_checkpoints_is_written_by_its_change` | 21.5 |
| Y41 a moved entry merged by key rather than written whole (PRF-011) | caught (survived the first run; TST-016) | `tests/regression/test_checkpoints.py::test_a_map_that_lost_or_reordered_an_entry_is_written_whole` | 22.7 |
| Y42 a segment's stated map size not checked (PRF-011) | caught | `tests/regression/test_checkpoints.py::test_a_tampered_map_header_is_refused[1-<lambda>-a change was lost]` | 21.8 |
| Y43 a map that shrank merged rather than written whole (PRF-011) | caught (survived the first run; TST-016) | `tests/regression/test_checkpoints.py::test_a_map_that_lost_or_reordered_an_entry_is_written_whole` | 22.3 |
| Y44 a qualified enum name read by its member alone (PER-007) | caught | `tests/regression/test_persisted_enum_names.py::test_the_broker_decoder_reads_a_qualified_name_only_under_its_own_class` | 81.9 |
| Y45 the reporting currency given an exchange rate of its own (FEA-007) | caught | `tests/unit/analytics/test_factor_risk.py::test_an_exchange_rate_carries_its_share_of_a_two_currency_book` | 139.0 |
| Y46 factor risk drops the factors' covariances with each other (FEA-007) | caught | `tests/unit/analytics/test_factor_risk.py::test_the_contributions_sum_to_the_volatility_of_the_dense_matrix[0]` | 139.9 |
| Y47 a decomposition through the factors drops the specific term (PRF-013) | caught | `tests/unit/analytics/test_factor_structure_as_a_covariance.py::test_the_euler_decomposition_through_the_factors_is_the_dense_one[0]` | 140.1 |
| Y48 a non-finite cell read in bulk as a number (PRF-013) | caught | `tests/unit/analytics/test_risk_model.py::test_malformed_matrices_are_refused[rows2-finite]` | 139.9 |
| Y49 an asymmetric matrix passed by the bulk check (PRF-013) | caught | `tests/unit/analytics/test_risk_model.py::test_malformed_matrices_are_refused[rows0-symmetric]` | 137.6 |
| Y50 a structure solved densely answers as the matrix's problem (PRF-013) | caught | `tests/unit/portfolio_optimizer/test_construction_over_a_factor_structure.py::test_a_method_that_needs_the_dense_values_is_solved_over_the_matrix` | 164.5 |
| Y51 a box set on a book that may short solved as long-only (FEA-009) | caught | `tests/unit/portfolio_optimizer/test_robust_box_with_shorts.py::test_the_robust_optimum_with_shorts_is_the_best_of_every_orthant[22]` | 177.8 |
| Y52 a portfolio nobody constrained clipped by defaults (OPT-001) | caught | `tests/unit/portfolio_optimizer/test_portfolio_optimizer.py::test_an_unconstrained_portfolio_holds_the_optimizers_own_weights` | 174.2 |
| Y53 a negative v1 risk limit accepted (RSK-007) | caught | `tests/unit/portfolio_optimizer/test_portfolio_optimizer.py::test_risk_constraints_state_only_the_limits_that_are_checked` | 175.5 |
| Y54 the research path loads the market-data transports (BND-005) | caught | `tests/regression/test_v313_invariants.py::test_the_research_path_loads_no_network_code` | 104.9 |
| Y55 an intent not refused where it is emitted (API-001) | caught | `tests/regression/test_strategy_failures_are_reported.py::test_an_invalid_intent_is_reported_as_a_failure` | 99.4 |
| Y56 a shorthand refused without the fields it stands for (DAT-006) | caught | `tests/unit/scheduler/test_cron.py::test_a_shorthand_is_refused_with_the_fields_it_stands_for[@daily-'0 0 * * *']` | 182.4 |

## X. New feature proposals

Proposed only where the gap blocks broad, serious use; each is justified in its ledger entry with
problem, user, ownership, why not iluvtrade, risk if omitted, implementation, tests, performance
and API impact (FEA-001..006, plus capability items ACC-005/006/007, EXE-003/004, NUM-005/006,
DAT-002/004, PER-001, REP-003).

| ID | Feature | Classification |
| --- | --- | --- |
| FEA-001 | Target-quantity/target-weight intents and a deterministic rebalancer | REQUIRED FOR V4 |
| FEA-002 | Benchmark-relative statistics (active return, TE, IR, alpha/beta, capture) | STRONGLY RECOMMENDED |
| FEA-003 | Walk-forward optimization harness | STRONGLY RECOMMENDED |
| FEA-004 | Declared trade-print ingestion | STRONGLY RECOMMENDED |
| FEA-005 | Total-variance interpolation across expiries | STRONGLY RECOMMENDED |
| FEA-006 | Engine certification report generated in CI | REQUIRED FOR V4 |
| ACC-005 | Instrument economics on the canonical path (multiplier, futures variation, option premium, perpetual funding) | REQUIRED FOR V4 |
| ACC-006 | Corporate actions and cash flows on positions | REQUIRED FOR V4 |
| EXE-003 | Typed order instructions, resting orders, TIF/auction | REQUIRED FOR V4 |
| PER-001 | Versioned schema upgrades | REQUIRED FOR V4 |

Deliberately **not** proposed: an LLM or AI-service integration, vendor adapters, a UI, user or
tenant management, a CLI/server/daemon, fixed-income curve bootstrapping, parametric volatility
fits, statistical regime models, cardinality-constrained optimization.

---

## Y. v3.10.0 plan — complete known foundations

Dependency order inside the release:

1. **Foundations:** pinned accounting decimal context (ACC-004); currency minor-unit authority and
   exact prices/quantities (ACC-001..003, NUM-008); statistics totality (NUM-001/002); persistent
   container compaction (PRF-002) and persistent broker maps (PRF-003); versioned schema upgrades
   (PER-001); strict JSON (PER-002).
2. **Canonical path correctness:** position- and side-aware pre-trade risk with reduce-only
   breach semantics, working orders included, daily loss enforced against a declared day, net
   exposure checked, notional position limit read (RSK-001..006, KD-001..003); position-aware
   long-only allocation and truthful Intent contract (ALC-001/002); refusing sizing defaults
   (ALC-003); FX `as_of` on the risk path (EXE-008).
3. **Scale:** incremental marking and aggregates on the canonical path (PRF-001) with a
   universe-size complexity guard (TST-006).
4. **Research integrity:** explicit fill timing recorded in results and fingerprints (EXE-001),
   cost model and fill policy recorded (EXE-002), strategy failures surfaced (EXE-006), bar
   timestamp convention declared and enforced (DAT-001), row-ingested dataset identity (KD-004).
5. **Analytics:** period basis, derived elapsed years, closed-trade statistics, `None` for
   undefined (ANA-001..005).
6. **Determinism/reproducibility:** DET-001..004, REP-001.
7. **Reliability and tests:** REL-001, TST-001..007, BRK-001, DAT-006.
8. **Boundary/duplicates:** remove vendor market-data subpackages and the `feed`/`live`/`marketdata`
   engine scaffolding (BND-001, SCF-002, OFE-012); remaining USD defaults (API-003, ACC-008).
9. **Documentation truth** (DOC-001/002) and ADR-0045.

### Outcome

Every item above is implemented in the v3.10.0 working tree; the ledger records, per item, the
tests that pin it and any deviation from its wording. The deviations: `fill_timing` lives on
`ExecutionPipelineConfig` (EXE-001); execution assumptions and strategy failures are derived from
the recorded configuration and events rather than added to the persisted `PerformanceReport`
(EXE-002, EXE-006); the version file is `alphalab/common/_version.py` (REP-001); the
reproducibility manifest still refuses empty-payload datasets (KD-004); benchmarks run weekly at
full size (TST-003).

Found while building it, and fixed in it: ALC-005 (a zero-quantity sale after integer rounding),
ACC-012 (a deposit or withdrawal that moves no money), ACC-013 (a fill in another currency booked
into an open position), ACC-014 (a sale floored at one cent), NUM-009 (overflow near the float
limit), NUM-010 (exponent notation in exact quotients), NUM-011 (correlation of subnormal
spreads), PER-005 (a latent version comparison), BND-004 (exchange symbol quirks) and TST-008
(four fixes that had no tests yet). Two were regressions of v3.10 itself, caught by comparing
every example's output with v3.9's and fixed before release: DET-005 (equal multi-strategy books
with two identities, once amounts stopped being rounded a second time) and API-006 (a typed
severity leaking into a string mapping). Found and scheduled: RES-001 (the v1 research engine's
daily assumption and uncalibrated scores, v3.12.0), DET-006 (identities that render a declared
`Decimal` by its text, v3.11.0) and PRF-006 (the per-operation constant v3.10 raised, measured in
its gates: a one-asset backtest 1.37x and the OMS benchmark 2.2x v3.9's time, v3.11.0).

### Release audit (v3.10.0, before release)

Run against the final working tree, which is uncommitted: nothing was committed, tagged or pushed.

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | clean / 1,220 files formatted |
| `mypy .` (strict, cold cache) | no issues in 1,140 source files |
| `pytest -W error` | 7,596 passed (4,053 unit, 398 integration, 3,145 regression); 0 failed, 0 skipped, 0 warnings |
| Examples, `-W error`, from the repository root | 65 / 65 |
| Benchmarks, `-W error`, 900 s each | 57 / 57 |
| `git diff --check`, plus every new file | clean |
| `python -m build`; `twine check --strict` | both distributions built; both PASSED |
| Clean Python 3.12 environments, wheel and sdist, `tests/installed_smoke.py 3.10.0` from outside the checkout | both pass: 665 modules, `py.typed`, example 11 end to end |
| Determinism | 60 of 65 examples byte-identical across two runs under different hash seeds; the other five print a random run or order id, a process id or CPU time, and did in v3.9 |
| Public API against v3.9.0 | 7 packages and 47 names removed, 25 added, 74 signatures changed; every break in the CHANGELOG, the breaking ones in its migration table |
| Mutation harness | section W.2 |

The audit compared every example's output with v3.9.0's (run-varying values and digests masked):
49 print what they printed, and each of the 16 that differ is explained in the CHANGELOG. That
comparison found two regressions of v3.10 itself, fixed before release (DET-005, API-006). The
benchmark comparison, run side by side with v3.9.0, found the per-operation cost recorded as
PRF-006. Reading every identity renderer found DET-006. It also corrected documentation that
claimed more than was measured: "each with a test that fails without the fix" became "each pinned
by the tests its ledger entry names"; "bit-identical to v3.9 on single-currency books" became the
property that was tested, that incremental marking equals re-marking every position; the
examples paragraph that said only two examples changed now lists all sixteen; and the claim that
the regression suite is the largest was false (it is 3,145 tests to the unit suite's 4,053).

## Z. v3.11.0 plan — broad quant-research completeness

Order instructions and resting simulated orders (EXE-003); target intents and rebalancer
(FEA-001); slice event, lifecycle hooks and subscriptions (EXE-004/005/007); instrument economics
for derivatives (ACC-005); corporate actions and cash flows (ACC-006); negative prices and rebates
(ACC-007); generalized Black–Scholes–Merton (NUM-005); delisting returns, implementation lag,
point-in-time symbology, interval type (DAT-002..005); venue sequence numbers and persisted
bindings/ledger (BRK-002/003); boundary removals — credential transport, enterprise, workbench,
studio scaffold (BRK-007, BND-002/003, SCF-001); paper-broker costs (BRK-008); multi-currency live
settlement (EXE-009); construction estimators (OFE-002), multivariate neutralization, multiple-
testing corrections, Newey–West IC (OFE-004..006); benchmark statistics and walk-forward
optimization (FEA-002/003); engine digest and tz version in manifests (REP-002); every content
identity rendering a declared `Decimal` by value, one scheme bump each (DET-006, found in v3.10);
an optimization pass on one-asset paths against a v3.9-relative budget (PRF-006, found in v3.10).

### Outcome

Every item above is implemented in v3.11.0; the ledger records, per item, the tests that pin it
(`test_status`) and what was built (`v311_outcome`). Where the build departs from the wording
above: the credential transport and venue adapter moved to the test tree as a worked reference
adapter rather than to another package (BRK-007); `enterprise` was replaced by a
`PermissionAuthority` the application supplies (BND-002); walk-forward optimization takes the
study's seed and is named as one of the study's inputs instead of taking its own seed (FEA-003);
construction stops at linear costs and lot rounding after the solve, with cardinality, joint
lots, CVaR and drawdown objectives and multi-period construction recorded as explicit boundaries
(OFE-002, ADR-0046 decision 10); and a simulated DAY order must state when it expires, because
simulation reads no exchange calendar yet (EXE-010, v3.12.0).

Found while building it, and fixed in it: NUM-012 (the run drivers computed in the caller's
decimal context — v3.10's ACC-004 had pinned the engines only), NUM-013 (lot rounding could round
up, since v3.4), ALC-007 (a fully invested book could not rotate: the budget counted a sale's
notional as a commitment and dropped the batch with it), BRK-009 (the paper broker credited a
sale's commission and booked shorts wrongly), INS-001 (aliases added after construction lost on
a snapshot round trip), LIV-001 (a parent order worked in children could also be routed whole),
OMS-001 (a snapshot upgrade computed and then ignored; latent), ALC-006 (target rounding that
overshot; introduced and caught in this release), EXM-001 (an example that printed steps it
never ran, and three that demonstrated removed packages), TST-009 (no test pinned what a sale
commits), DOC-003 (current-state documents that still described the packages v3.10 and v3.11
removed) and PRF-007 (a slice of an append-only log copied the whole log, so the two slices the
pipeline takes per fill made a run quadratic in its length; shipped since v2.1.0) and PRF-008
(a persistent map left a pair per write, and a rebase a list and a pair per key, for the
collector to walk: the OMS benchmark's accept-and-fill stage spent half its time in the collector;
shipped in v3.10.0) and TST-010 (the institutional benchmark judged its scaling ceilings on one
sample with the collector running, and failed the release gate on a collection) and TST-012 (two
resting-limit fill rules no test pinned, found by the defect-injection run). ALC-006 and
ALC-007 were found by rewriting the examples against the canonical path; DOC-003 by the release
audit's reading of every current-state document; PRF-007 by profiling the release benchmarks
when a re-measurement did not match the one before it, PRF-008 when the OMS benchmark measured
over its budget, TST-010 by the benchmark gate and TST-012 by the mutation harness. Found and scheduled: EXE-010 (exchange
calendars inside simulation) and TST-011 (the five other benchmarks whose ceilings are judged on
one sample), both v3.12.0.

### Release audit (v3.11.0, before release)

Run against the final working tree before it was committed.

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | clean / 1,203 files formatted |
| `mypy .` (strict, cold cache) | no issues in 1,122 source files |
| `pytest -W error` | 8,215 passed (4,415 unit, 516 integration, 3,284 regression); 0 failed, 0 skipped, 0 warnings |
| Examples, `-W error`, from the repository root | 65 / 65 |
| Benchmarks, `-W error`, 900 s each | 54 / 54 on the final tree (415 s, beside the mutation harness). The first run failed `benchmark_institutional`'s scenario ceiling at 6.05x — TST-010, fixed and re-run |
| `git diff --check`, plus every new file | clean |
| `python -m build`; `twine check --strict` | both distributions built; both PASSED |
| Clean Python 3.12 environments, wheel and sdist, `tests/installed_smoke.py 3.11.0` from outside the checkout | both pass: 626 modules, `py.typed`, example 11 end to end |
| Determinism | 61 of 65 examples byte-identical across two runs under different hash seeds; the other four print a random run or order id, a process id or CPU time, as in v3.10 |
| Example output against v3.10.0 | 48 print what they printed; 4 rewritten; 13 differ, each explained in the CHANGELOG (identities, versions, a named lag, a stated carry, more metrics) — with identities masked, no price, quantity, P&L or statistic moved |
| Public API against v3.10.0 | 3 packages and 3 names removed, 75 names added, 56 signatures changed; the 18 that break a caller (a new required parameter or a removed one) and the removals are each in the CHANGELOG's migration table |
| Performance against v3.9.0 and v3.10.0 | five interleaved rounds: OMS 0.83x v3.9, one-asset backtest and replay 1.05x, pipeline 1.01x, portfolio micro-benchmark 1.69x; every round within PRF-006's budget and faster than v3.10 |
| Mutation harness | section W.3: 77 of 79 caught by the full run; the two survivors pinned by TST-012 and caught on re-run |
| Stress | section V.2 |

The gates found six things, each recorded in the ledger and fixed before release: PRF-007 (a
log slice copied the whole log — found when a re-measurement disagreed with the one before it
and profiling showed per-event cost rising with run length), PRF-008 (collector pressure from
persistent-map chains — found when the OMS benchmark measured 1.12x v3.9 against a 1.1x budget),
TST-010 (the institutional benchmark's single-sample scaling guard failed the gate), TST-012
(two resting-limit fill rules no test pinned — the mutation harness's two survivors), DOC-003
(current-state documents describing removed packages — found by searching every current-state
document for each removed name; the README had also lost its v3.10.0 section while v3.11.0's was
written) and a stale editable install in the audit environment, which
`tests/unit/test_package_metadata.py` reported as designed (the environment was reinstalled; no
code changed). The public API comparison found four kinds of break the migration table did not
yet list — option pricing's required `carry`, `TimeFrame` no longer an enum, result records with
new required fields, and the studio bridge's two names — and a row was added for each.

## AA. v3.12.0 plan — deep correctness, performance, hardening

Bounded state and incremental checkpoints (PRF-004); QP scale envelope and factor-structured
construction (PRF-005); multi-broker reconciliation (BRK-004); package consolidation (SCF-003,
OFE-013); LSTM/attention backpropagation (SCF-004); numerical items NUM-003/004/007; directory
fsync (PER-003); classification dimensions, per-strategy ceilings, alt-data on the execution path,
streaming observation sets, evidence store, windowed health (OFE-001/003/009/011/016/017);
trade-print ingestion (FEA-004); large-scale stress suite (10,000 assets, 1,000 strategies, 100
venues) with recorded results; float-time precision documentation (DAT-008); exchange calendars
inside simulation, so a DAY order expires at its session's close without the caller computing it
(EXE-010, found in v3.11); every benchmark ceiling judged by the stabilized method (TST-011, found
in v3.11).

### Outcome

Every item above is implemented in v3.12.0; the ledger records, per item, the tests that pin it
(`test_status`) and what was built (`v312_outcome`). **SCF-003** was decided per package:
`alphalab.plugins` (an `execute()` that was a placeholder) and `alphalab.optimizer` (a second
parameter search beside `research.parameter_sweep`) are removed, with the reporting dashboards
(presentation); distributed execution is kept and fixed; the scheduler is reduced to
deterministic timers over `MarketCalendar`; the parameter search is consolidated into the research
authority; and reporting writes exact numbers. **OFE-013** is resolved by the optimizer's removal,
as planned. The removal was first refused by the permission policy of the session that built the
release, as irreversible; the maintainer authorized it, and it was made with `git rm`. DAT-008 is
kept as a stated limitation, as planned, and BDY-018 changed in part (trade prints declared; depth
stays out). Where the build departs from the wording above: the scheduler's `CRON` and
`BAR_BOUNDARY` timers stay refused at registration (a stated limitation, not a placeholder); a
segment of an incremental checkpoint carries the whole order book, which no retention bounds
(stated in PRF-004, for the v3.13 audit to classify); and TST-011's module list was wrong — the
ceilings are in six benchmarks, `benchmark_multi_asset` has none.

Found while building, stressing and auditing it, and fixed in it: PER-006 (an allocation snapshot
dropped its budget's currency on restore, since v2.17 — checked against a payload v3.11.0 wrote),
DAT-009 (cleaning kept the quotes validation refused), ANA-006 (a report wrote exact money as a
binary float), PRF-009 (a classification limit summed its bucket for every order; introduced with
OFE-001 and never shipped) and PRF-010 (every event asked every strategy whether it subscribed;
shipped since v3.11). PRF-009 and PRF-010 were found by the stress program (V.3); PER-006 while
adding OFE-003's fields to the allocation snapshot; DAT-009 while extending the consistency
predicate to trade prints; ANA-006 while consolidating the reporting package. Two gaps of method were
closed too: TST-013 (five rules no test pinned — the defect-injection run's survivors, W.4) and
DOC-004 (current-state documents that still described what v3.11 removed, found by the release's
documentation pass).

### Release audit (v3.12.0, before release)

Run against the final tree: the working tree for the fast gates, the examples and the
distributions, and its commit (`b775830`, the removals) for the defect-injection run, which
archives `HEAD`.

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | clean / 1,205 files formatted |
| `mypy .` (strict, cold cache) | no issues in 1,122 source files (1,158 before the removals took 36) |
| `pytest -W error` | 8,583 passed (4,578 unit, 649 integration, 3,356 regression); 0 failed, 0 skipped, 0 warnings |
| Examples, `-W error`, from the repository root | 65 / 65, twice |
| Benchmarks, `-W error`, 900 s each | 53 / 53 (697 s in all, on a quiet machine after the defect-injection run) |
| `git diff --check` | clean |
| `python -m build`; `twine check --strict` | both distributions built; both PASSED; neither carries a removed module |
| Clean Python 3.12 environments, wheel and sdist, `tests/installed_smoke.py 3.12.0` from outside the checkout | both pass: 605 modules, `py.typed`, example 11 end to end |
| Determinism | 61 of 65 examples byte-identical across two runs under different hash seeds; the other four (`12`, `13`, `45`, `48`) print a random run or order id, a process id or CPU time, as in v3.11 |
| Example output against v3.11.0 | 55 print what they printed; `01` rewritten; 9 differ, each explained in the CHANGELOG — with identities and versions masked, no price, quantity, P&L or statistic moved. Removing `plugins`, `optimizer` and the dashboards changed no example's output |
| Public API against v3.11.0 | 2 packages and 12 names removed, 88 names added, 53 signatures changed; the removals and every change that breaks a caller are in the CHANGELOG's migration table |
| Performance against v3.9.0 and v3.11.0 | five interleaved rounds, medians: OMS 0.87x v3.9 (0.98x v3.11), one-asset backtest 1.15x (1.03x), replay 1.10x (1.07x), pipeline 1.03x (1.00x), portfolio micro-benchmark 1.51x (1.02x); every round within PRF-006's budget. A five-round run of the tree before the removals measured 0.83x, 1.11x, 1.16x, 1.11x and 1.74x against v3.9 — the spread between the two runs is the measurement's own |
| Defect injection | section W.4: on the release tree, 125 of 126 mutations caught; X17, the equivalent mutant, survives |
| Stress | section V.3 |

The gates found four things, each fixed before release. **Current-state documents** describing
what this release removed: `docs/ARCHITECTURE.md` (its Plugins and Plugin Architecture sections, a
list of registries and a layer diagram) and `docs/SYSTEM_DESIGN.md` described `plugins` as current,
and the architecture document still called the optimizer's queue a term left super-linear —
found by searching every current-state document for each removed name, and recorded under DOC-004.
**The migration table**, checked against the public API comparison with 3.11.0: one row named
`cloud_research.sweep_space`, which 3.11 did not have, for what was `submit_parameter_sweep`'s
change (submission order by axis name, values limited to finite numbers and strings, a repeated
value refused), and no row covered research reports built by hand, `ResearchCompleted.overall_score`
or the reporting state's dashboard fields; each is stated now. **The ledger**: DET-002's pin named
`test_optimizer_is_reproducible.py`, deleted with the optimizer — found by checking that every
test the ledger names exists (200 references, none missing after DET-002 was restated as
superseded). **Counts**: the README gave 1,158 type-checked files, the count before the removals
took 36, and `nowandfuture.md` gave 3.11.0's test and benchmark counts under a header saying it
was last updated at 3.11. Removing two packages also restated two invariant tests, as it must: the package count (43)
and the optimizer's super-linear queue, now asserted gone.

## AB. v3.13.0 plan — final feature completion

American option pricing (NUM-006); rerun harness (REP-003); optimal split (BRK-005); estimated
urgency and randomized icebergs (BRK-006); lock-file reader (OFE-019); total-variance
interpolation (FEA-005); then the fresh pre-v4 audit (Phase 26) and every finding it produces.

### Outcome

Every item assigned to v3.13.0 is closed, and so are the four the ledger had assigned to v4.0.0
itself — the shared names (API-001), the public API manifest (API-002), the persisted-name
contract (PER-004) and release certification (FEA-006) — so that nothing required is left for a
later release. Every boundary and limitation the ledger holds was re-read against the code and
kept with its reason (`v313_outcome` on each), and the ledger assigns nothing beyond v3.13.0:
217 entries, each implemented or kept. ADR-0048 records the decisions.

The fresh audit found twenty-three things, each recorded in the ledger:

* **Defects**: the broker codec read a qualified enum name by its member alone (PER-007, since
  2.16); the liquidation price assumed one venue's maintenance convention and ignored fees and
  funding (NUM-014, since 1.38); importing the research path loaded the market-data transports
  (BND-005, since 2.5); the v1 portfolio manager clipped a portfolio nobody had constrained
  (OPT-001, since v1), and its `RiskConstraints` carried three limits nothing read (RSK-007).
* **Scale**: checkpoint segments carried every order a run had placed (PRF-011, shipped in
  3.12.0), now carrying only what changed; the per-order state itself is kept as a stated
  limitation. The one-asset paths' cost against 3.11 is classified as the price of the
  capabilities on the one canonical path (PRF-012). The 10,000-asset construction v3.12
  published was the solver's alone: through the public API a factor model had first to be
  written out as a dense matrix, O(n²) — 29 s and 1.3 GB at 4,000 assets — so the structure is
  now a covariance construction takes and never writes out (PRF-013).
* **Documentation and method**: three durability tables were stale (DOC-005); a docstring said
  a delivered rate source "has not arrived" (DOC-007); `nowandfuture.md`'s identity table still
  read 3.9.0, `docs/ARCHITECTURE.md`'s Implementation Status called three delivered things
  deferred, and no version marker but the CHANGELOG's was tested (DOC-008); nothing checked a
  release's migration table against its API (DOC-006) or the ledger's cited tests against the
  suite (TST-014); and the release's own defect-injection run found seven v3.13 rules no test
  pinned (TST-016, section W.5).
* **The inventory itself** (TST-015): the pre-v4 audit had inventoried ROADMAP's boundaries and
  optional list, not the "Known limitations" and "DEFERRED" lists of ADR-0042, ADR-0043 and
  ADR-0044. Of their 52 items, 23 had no ledger entry: two deferred capabilities, now
  implemented — exchange-rate return factors with a factor risk decomposition (FEA-007) and
  implementation shortfall against an impact model (FEA-008) — and 21 limitations, of which one
  is lifted (a box uncertainty set on a book that may short, FEA-009), one hid the defect above
  (RSK-007, whose classification found OPT-001), and the rest are kept with their reasons
  (LIM-001, LIM-002, LIM-003). A test now holds every ADR's limitations and deferrals to closed
  ledger entries.
* **Boundaries**: the WebSocket client's two stated omissions are recorded as kept (BND-006).
* **The certificate's identity** (REP-004, found by the release gates): the certificate names
  the engine source it certified, and `--check` compared what the build computes, not which
  build it is — on the release candidate (`b6a0fef`) the committed certificate named the source
  as it stood before two commits that changed only docstrings, and every gate passed, the
  defect-injection baseline included. `--check` now holds the name to the build. And because
  the digest is over bytes, `.gitattributes` checks every Python file out with LF: a checkout
  whose line endings Git rewrote was other engine source, to the certificate and to a rerun.

### Release audit (v3.13.0, before release)

Run against the final tree: the working tree for the fast gates, the examples and the
distributions, and its commit for the defect-injection run, which archives `HEAD`.

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | clean / 1,240 files formatted |
| `mypy .` (strict, cold cache) | no issues in 1,152 source files (1,122 at v3.12) |
| `pytest -W error` | 9,041 passed (4,806 unit, 649 integration, 3,586 regression); 0 failed, 0 skipped, 0 warnings (226 s) |
| Examples, `-W error`, from the repository root | 69 / 69 under each of two hash seeds, nothing on stderr |
| Benchmarks, `-W error`, 900 s each | 53 / 53 (455 s in all, on a quiet machine) |
| `git diff --check` | clean |
| `python -m build`; `twine check --strict` | both distributions built and both PASSED: the wheel (1.70 MB) carries the 605 modules of `alphalab` and `py.typed`, nothing else; the sdist (3.85 MB) carries exactly the tracked files of the directories it includes |
| Clean Python 3.12 environments, wheel and sdist, `tests/installed_smoke.py 3.13.0` from outside the checkout | both pass: version 3.13.0, 605 modules, `py.typed`, example 11 end to end |
| Determinism | 65 of 69 examples byte-identical across two runs under different hash seeds; the other four (`12`, `13`, `45`, `48`) print a random run or order id, a process id or CPU time, as in v3.12 |
| Example output against v3.12.0 | 56 of the 65 examples both releases have print what they printed; 9 differ, each explained in the CHANGELOG — with identities and versions masked, only `38` moved, by NUM-014's liquidation price |
| Public API against v3.12.0 | 44 packages either side; 2,455 exports → 2,479: 25 removed, 49 added, 29 rebound or re-signed; 52 shared names → 31. Every removed or rebound name is in the CHANGELOG's v3.13.0 section (`test_api_changes_are_in_the_changelog.py`) |
| Performance against v3.9.0, v3.11.0 and v3.12.0 | the CHANGELOG's table: five interleaved rounds, every median against 3.12 between 0.98x and 1.03x, PRF-006's budget held in every round |
| Release certificate | eleven checks, each PASSED; `certify_release.py --check` passes on the release tree |
| Defect injection | section W.5: on the release tree, 181 of 182 mutations caught; X17, the equivalent mutant, survives |
| Stress | section V.4 |

The gates found what the CHANGELOG lists under "Found during this release" as caught within it:
the state-scaling table still naming the `risk_limits` map RSK-007 removed; two tests reading a
matrix's attributes from a construction's covariance, refused by strict mypy once the field could
hold a factor structure; a committed certificate that still recorded the API before the
structure, which the defect-injection harness refused to start on; seven v3.13 rules no test
pinned (TST-016); current-state documents that had drifted (DOC-008); and a certificate that
named other engine source than the build's, which no gate compared (REP-004).

## AC. v4.0.0 freeze requirements

* Every ledger entry is `done`, `REMOVE`d, `EXTERNAL`, `KEEP_BOUNDARY` or `KEEP_LIMITATION`
  with a reason; no `v4_required` entry open.
* Public API manifest and import regression test (API-001/002); serialization stability contract
  (PER-004).
* Gates: ruff, format, strict mypy, `pytest -W error` with zero skips on any user, examples,
  benchmarks, wheel+sdist clean installs, cross-process/cross-hash-seed determinism, stress suite,
  defect-injection harness with every mutation caught, certification report generated in CI
  (FEA-006).
* ROADMAP lists only boundaries and external dependencies.

---

## Master finding table

| ID | Finding | Severity | Area | Evidence | Required action | Release | Test | Status | V4 disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KD-001 | Pre-trade position limit adds an asset's notional exposure (market value) to an order quantity (units) and compares the sum with PositionLimit.max_quantity. | high | risk | Code review; ROADMAP.md 'Known defects'; nowandfuture.md s20. PositionLimit.max_notional is additionally read by no check. | Evaluate the projected post-trade position in quantity against max_quantity and in value against max_notional, reading the current signed quantity from the portfolio (the position authority) at evaluation time rather than from risk state. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| KD-002 | DailyLossLimit is never enforced: RiskState.daily_loss is never maintained on the execution path, so the check always reads zero. | high | risk | Code review: nothing assigns RiskState.daily_loss; ROADMAP.md 'Known defects'. | Maintain daily loss from the portfolio equity history against a declared trading-day boundary (IANA zone), with no invented default zone; refuse a finite limit whose day is undeclared. | v3.10.0 | tests/regression/test_risk_projection.py; tests/regression/test_schema_upgrades.py | implemented (v3.10.0) | FIX |
| KD-003 | ExposureLimit.max_net_exposure is read by no pre-trade check and its sign convention is defined nowhere. | high | risk | grep: no reader; ROADMAP.md 'Known defects'. | Define net exposure = long market value + short market value (short negative) in the base currency; check \|projected net\| <= max_net_exposure on the projected post-trade book. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| KD-004 | A dataset ingested from in-memory rows is identified by whatever RawSource the caller hands over; rows recorded with an empty payload share one dataset version whatever they contain. | medium | data | derive_dataset_version keys on request.source.content_hash only; ROADMAP.md 'Known defects'. | Derive the identity of row-ingested datasets from the canonical rendering of the rows themselves (records digest) in addition to the declared source, so different content can never share a version. | v3.10.0 | tests/regression/test_row_ingested_identity.py | implemented (v3.10.0) | FIX |
| RSK-001 | Buying power is checked against the full notional of every order regardless of side: a fully invested account cannot sell to reduce a position. | critical | risk | probes/p1_risk_sell.py: buy 990 @100 with 100k cash, then SELL 500 -> no order (rejected). Verified. | Charge buying power only for the part of an order that increases exposure (buying beyond covering a short, or selling short beyond the long); a reducing trade consumes none. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| RSK-002 | Gross exposure, leverage and margin checks add the order notional regardless of side and current position, so risk-reducing trades are refused at a limit. | critical | risk | probes/p1_risk_sell.py: at the gross limit a SELL 50 of a 99 long is rejected. Verified. | Project the post-trade book (current signed position plus the signed order) and check the projected gross/net/leverage/margin; a trade whose projection is no worse than the current state on the limited measure is not refused by that limit. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| RSK-003 | A breached drawdown (or daily-loss) limit refuses every order, including liquidation. | high | risk | probes/p1_risk_sell.py: in drawdown breach a full liquidation SELL is rejected. Verified. | In breach, refuse only orders that increase absolute exposure (reduce-only semantics), and report the breach on every decision. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| RSK-004 | Pre-trade checks ignore working orders: under EXTERNAL routing several working orders can jointly breach position, exposure and buying-power limits. | high | risk | Code review: checks read RiskState exposure from filled positions only. | Include the signed remaining quantity of working orders (from an OMS open-order index) in the projection. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| RSK-005 | No test pins the order-size limit boundary: mutating `quantity > max_quantity` to `>=` is not detected by the 7,387-test suite. | medium | risk | Defect injection M01: 7386 passed, 1 skipped with the mutation applied. | Boundary tests for every risk limit (equal-to-limit allowed, one quantum over refused). | v3.10.0 | tests/regression/test_risk_projection.py; tests/regression/test_mutation_pins.py | implemented (v3.10.0) | FIX |
| RSK-006 | RiskViolation.severity is a free string; the margin model is 'full notional requires margin' against cash with utilization computed only when available margin > 0 (vacuous pass at zero cash). | medium | risk | Code review. | Typed severity enum; margin utilization undefined (refuse increase) when available margin is zero; asset-class-aware margin via declared margin specs (futures ContractMarginSpec) in v3.11. | v3.10.0 | tests/regression/test_risk_projection.py | implemented (v3.10.0) | FIX |
| ALC-001 | AllocationConstraints(allow_shorting=False) refuses any negative net delta, including a sale that closes an existing long, because allocation nets deltas without seeing positions. | high | allocation | probes/p1_risk_sell.py (long-only close refused); examples/11_unified_backtest.py comment admits it. | Pass current signed positions (as a Mapping[str, Decimal] of numbers, preserving the allocation->portfolio boundary) and check the projected post-trade position. | v3.10.0 | tests/regression/test_allocation_semantics.py | implemented (v3.10.0) | FIX |
| ALC-002 | Intent is documented as a 'target position/weight' but allocation treats Intent.target as a signed order DELTA; TargetWeightSizing re-buys on every emission. | high | strategy/allocation | IntentAllocator docstring ('Deltas'); examples name the field 'delta'. | v3.10: correct the contract (documented delta semantics, explicit IntentKind.DELTA). v3.11: add position-aware TARGET_QUANTITY and TARGET_WEIGHT intents computed against current positions and working orders. | v3.10.0 | tests/regression/test_allocation_semantics.py | implemented (v3.10.0) | FIX |
| ALC-003 | A missing volatility silently defaults to 1% (vols.get(asset, 0.01)) and a non-positive one is replaced by 1%, producing oversized positions; FixedDollar/TargetWeight/EqualWeight sizing return 0 for a non-positive price, hiding the request from unpriced-asset reporting. | high | allocation | Code review. | Refuse sizing with a missing or non-positive volatility; never size against a non-positive price (surface as unpriced). | v3.10.0 | tests/regression/test_allocation_semantics.py | implemented (v3.10.0) | FIX |
| ALC-004 | Exact-duplicate intents are silently dropped with an O(n^2) membership test and no event; programming errors inside validation are caught by `except Exception` and recorded as rejections. | low | allocation | Code review. | Keep duplicates (two identical intents are two requests) or record a rejection event; catch only AllocationValidationError. | v3.12.0 | tests/unit/allocation/test_intent_intake.py | implemented (v3.12.0) | FIX |
| ACC-001 | Every currency is rounded to 0.01. JPY/KRW (0 decimals), KWD/BHD/OMR/JOD/TND (3), CLF (4) and crypto settlement assets are wrong; the module's claim 'exact at the currency's minor unit' is false. | critical | portfolio | Probe: JPY account deposit of 1000000.5 is stored as 1000000.50 JPY. Verified. | Per-currency minor units: the ISO 4217 minor-unit table as a stated standard, plus caller-declared units for non-ISO currencies; refuse settlement in a currency with no known minor unit. to_money takes the currency. | v3.10.0 | tests/unit/portfolio/test_monetary_precision.py; tests/unit/portfolio/test_portfolio.py | implemented (v3.10.0) | FIX |
| ACC-002 | Every fill and mark price is quantized to 4 decimal places before money is computed: a 1e9-unit purchase at 0.00001234 is booked at cost 0.00 with cash unchanged; EURUSD 1,000,000 @ 1.08345 books 1,083,400.00 instead of 1,083,450.00. | critical | portfolio | Probe (portfolio engine): SHIB-like fill basis 0.00; FX fill basis 1083400.00. Verified. | Keep venue/market prices exact; money is rounded once at the currency minor unit (ACC-001). Derived per-unit figures (average cost) computed in a pinned context. | v3.10.0 | tests/unit/portfolio/test_monetary_precision.py | implemented (v3.10.0) | FIX |
| ACC-003 | Fill quantities are quantized to 1e-6; a real venue fill of 0.0000005 BTC raises InvalidTransactionError and cannot be booked; other quantities are silently rounded away from the venue's. | high | portfolio | Probe: apply_fill(0.0000005 BTC) -> 'A fill must have a non-zero quantity'. Verified. | Book the venue quantity exactly; lot-size rounding belongs to sizing (conventions.lot), never to accounting. | v3.10.0 | tests/unit/portfolio/test_monetary_precision.py | implemented (v3.10.0) | FIX |
| ACC-004 | Monetary rounding (quantize without explicit rounding) and VWAP divisions run in the caller's ambient decimal context; v3.9 fixed only split_by_contribution. | high | portfolio/oms/broker/core | Code review: to_money uses amount.quantize(CURRENCY_QUANT) with context rounding; average fill price divisions use context precision. | One pinned accounting context (prec=34, ROUND_HALF_EVEN) used by every accounting/execution arithmetic site; a regression sweep for ambient-context arithmetic on those paths. | v3.10.0 | tests/unit/portfolio/test_monetary_precision.py; tests/regression/test_ambient_decimal_context.py | implemented (v3.10.0) | FIX |
| ACC-005 | The canonical path books every fill as a fully paid cash equity: no contract multiplier (Position has none by invariant 30), no futures margin/variation settlement, no option premium x multiplier; derivatives packages are analytics-only. | high | portfolio/runtime | Code review of Position/PortfolioEngine.apply_fill and portfolio/contracts.py. | Instrument economics on the canonical path: an InstrumentEconomics declaration (multiplier, settlement style: CASH_EQUITY \| FUTURES_VARIATION \| OPTION_PREMIUM \| PERPETUAL) supplied through the instrument registry; valuation and cash impact go through conventions.contract_notional (still the single multiplying site). | v3.11.0 | tests/integration/test_instrument_economics.py | implemented (v3.11.0) | IMPLEMENT |
| ACC-006 | No position-level corporate actions or cash flows on the canonical path: splits, cash/stock dividends, short borrow fees, margin/cash interest and perpetual funding cannot be booked; raw-price backtests mis-state P&L at splits. | high | portfolio | Code review: PortfolioEngine offers deposit, withdrawal, conversion and fill only. | PortfolioEngine.apply_corporate_action (split/stock dividend adjusts quantity and basis exactly) and apply_cash_flow (dividend, interest, fee, funding, with attribution and currency) with events and ledger entries. | v3.11.0 | tests/integration/test_instrument_economics.py | implemented (v3.11.0) | IMPLEMENT |
| ACC-007 | Prices must be positive and commissions non-negative everywhere: negative prices (WTI April 2020, power, spreads) and maker rebates are unrepresentable; a non-positive mark is silently ignored. | medium | core/portfolio | Fill.__post_init__, apply_fill, update_market_prices (skips price <= 0). | Allow negative prices for instruments whose economics declare it; allow negative commission (rebates) as a signed cost; never silently skip a mark. | v3.11.0 | tests/integration/test_instrument_economics.py | implemented (v3.11.0) | IMPLEMENT |
| ACC-008 | base_currency='USD' defaults remain on valuation helpers; PortfolioValuation.cash_value returns 0.00 for a book holding no USD instead of refusing (exempted from the sweep as 'refuses', which it does not). | medium | portfolio | Code review of the PERMITTED map in test_no_silent_financial_defaults.py. | Remove currency defaults from every valuation helper; the pipeline always passes the account currency. | v3.10.0 | tests/regression/test_no_silent_financial_defaults.py | implemented (v3.10.0) | FIX |
| ANA-001 | Returns are taken between consecutive portfolio snapshots (one per market record, several per instant in multi-asset runs), labelled daily_returns and annualized with 252 periods; Sharpe, volatility, Sortino and VaR are wrong for any non-daily or multi-asset run. | critical | analytics | probes/p3_analytics.py: a 3-second quote run reports an annualized Sharpe of 9.17 from per-record returns. | Collapse snapshots to one equity point per instant; compute returns on a declared periodicity (or the observed one) and annualize with periods-per-year derived from elapsed time or declared; record the basis in the report. | v3.10.0 | tests/unit/analytics/test_analytics.py; tests/regression/test_schema_upgrades.py | implemented (v3.10.0) | FIX |
| ANA-002 | years_elapsed defaults to 1.0, so CAGR and Calmar are computed as if every run lasted one year. | high | analytics/runtime | probes/p3_analytics.py: a 3-second run reports CAGR == total return. | Derive elapsed years from the equity curve's first and last instants (seconds / 365.25 days), or take an explicit override. | v3.10.0 | tests/unit/analytics/test_analytics.py | implemented (v3.10.0) | FIX |
| ANA-003 | Trade metrics count every fill's TradeRecord, including opening fills (realized 0) which are classified as losses: one winning round trip reports a 50% win rate. | high | analytics | probes/p3_analytics.py: win_rate 0.5, loss_rate 0.5 for one winning round trip. Verified. | Compute trade statistics over fills that realized P&L (reducing/closing fills) only; report the count basis. | v3.10.0 | tests/unit/analytics/test_analytics.py | implemented (v3.10.0) | FIX |
| ANA-004 | Undefined statistics are reported as 0.0 (Sharpe of a constant series, Sortino with no downside, Calmar with no drawdown, VaR of nothing, CAGR of a non-positive capital) and profit_factor can be inf, contradicting invariant 21. | medium | analytics | probes/p3_analytics.py: Sortino 0.0, Calmar 0.0. | Return None for undefined statistics (typed float \| None) and never inf. | v3.10.0 | tests/unit/analytics/test_analytics.py | implemented (v3.10.0) | FIX |
| ANA-005 | VaR and CVaR round to 6 decimals inside the computation and the CVaR tail uses the rounded threshold. | low | analytics | Code review. | Compute unrounded; round only for presentation. | v3.10.0 | tests/unit/analytics/test_analytics.py | implemented (v3.10.0) | FIX |
| EXE-001 | A simulated order decided on an event is filled at that same event's price (the bar close or quote mid the strategy just observed): close-to-close 'cheat-on-close' optimism with no first-class next-event execution. | high | runtime/execution | Code review; probes/p1: fill at 100.000 at the deciding quote's instant. | RunConfig.fill_timing (SAME_EVENT \| NEXT_EVENT), recorded in the run, its snapshot and the fingerprint's research configuration; NEXT_EVENT keeps the order working until the asset's next event. | v3.10.0 | tests/regression/test_fill_timing.py | implemented (v3.10.0) | FIX |
| EXE-002 | The default ExecutionSimulator is frictionless (zero commission, zero slippage) and market orders on quotes fill at the midpoint; RunConfig.fill_policy defaults to unlimited liquidity. A default backtest is optimistic and does not say so. | medium | execution | Code review; probe fill at mid. | Record the cost model and fill policy in every result and fingerprint; name the frictionless configuration explicitly (FREE) and mark results produced under it. | v3.10.0 | tests/regression/test_execution_assumptions.py | implemented (v3.10.0) | FIX |
| EXE-003 | The canonical path can only create MARKET orders: OrderRequest has no order type, limit/stop price or time in force, Intent.execution_directive is never read, and no order rests across events in simulation. | high | runtime/oms/execution | _oms_order hard-codes OrderType.MARKET; grep shows execution_directive unused. | Typed order instructions on Intent/OrderRequest (type, limit, stop, TIF incl. GTD/IOC/FOK, auction OPG/CLS); resting orders with deterministic fill rules in simulation; capability checks already exist at the venue. | v3.11.0 | tests/integration/test_order_terms.py | implemented (v3.11.0) | IMPLEMENT |
| EXE-004 | No cross-sectional (time-slice complete) event: multi-asset strategies are dispatched once per asset record with a partially updated market, so a cross-sectional rebalance must buffer by hand. | medium | runtime/strategy | Code review. | An on_slice hook dispatched after every record sharing an instant has been published (opt-in). | v3.11.0 | tests/integration/test_slices.py | implemented (v3.11.0) | IMPLEMENT |
| EXE-005 | StrategyProtocol declares on_start/on_stop/on_shutdown (never invoked) and on_fill/on_order/on_timer (routed but never constructed by the pipeline): a strategy on the canonical path never learns of its own fills. | medium | strategy | nowandfuture.md s11; code review. | Invoke lifecycle hooks from the run (start/finalize) and deliver fill/order events after the step for strategies that implement them (ordering documented; parity baselines updated deliberately). | v3.11.0 | tests/integration/test_strategy_feedback.py; tests/unit/strategy/test_subscriptions_and_start.py | implemented (v3.11.0) | IMPLEMENT |
| EXE-006 | A strategy hook that raises is caught, the strategy moves to FAILED and the run continues; BacktestResult has no view of it, so a crashing strategy yields a 'successful' backtest. | high | strategy/backtesting | Code review of Dispatcher.dispatch_event and BacktestResult. | RunState/BacktestResult expose strategy failures (id, instant, error); RunConfig.halt_on_strategy_failure to stop the run; analytics report records it. | v3.10.0 | tests/regression/test_strategy_failures_are_reported.py | implemented (v3.10.0) | FIX |
| EXE-007 | Strategy subscriptions are recorded but never read: every running strategy receives every market event for every asset. | medium | strategy | grep: subscriptions set by RuntimeSupervisor.subscribe, read nowhere. | Route by declared subscription (event kinds and optional asset set) with an explicit 'all' form. | v3.11.0 | tests/unit/strategy/test_subscriptions_and_start.py; tests/regression/test_schema_upgrades.py | implemented (v3.11.0) | FIX |
| EXE-008 | The per-event risk resync converts foreign positions and cash with as_of=None, bypassing the future-dated-rate and staleness guards that FxRates.convert enforces for every other conversion. | medium | runtime | Code review: rates.convert(..., None), cash_in(..., None), NAVCalculator as_of default None. | Thread the event instant as as_of through every risk-path conversion. | v3.10.0 | tests/integration/test_v217_capabilities.py | implemented (v3.10.0) | FIX |
| EXE-009 | LiveSession.settle applies venue fills without FX rates, so a multi-currency live run cannot settle a foreign fill through the canonical path. | medium | runtime/live | Code review: apply_broker_execution called without rates. | Thread rates through settle/advance. | v3.11.0 | tests/integration/test_live_requests_and_children.py | implemented (v3.11.0) | FIX |
| PRF-001 | The canonical path is quadratic in universe size: every event re-marks and logs every held position, recomputes exposure/NAV/valuation over all positions (twice) and copies the whole price map. | critical | runtime/portfolio | probes/p2_multiasset_scale.py: N=10..160 assets x 50 bars -> cpu 0.56s, 1.58s, 4.74s, 14.8s, 54.8s; peak memory 2.5MB..337MB; 1.27M logged marks at N=160. Extrapolated 500 stocks x 10y daily: ~27,000 cpu-s and ~170 GB. | Mark only the asset the event re-priced; maintain exact incremental aggregates (long/short value per currency, gross/net exposure); log only changed marks; persistent price map; equity curve bit-identical to v3.9 on single-asset runs. | v3.10.0 | tests/regression/test_universe_scaling.py; tests/unit/portfolio/test_position_book.py; tests/unit/portfolio/test_mixed_book_valuation.py; benchmarks/benchmark_universe_scaling.py | implemented (v3.10.0) | FIX |
| PRF-002 | PersistentMap never reclaims superseded versions: iteration costs O(keys ever written) and memory grows with total writes; order_shares_by_strategy iterates allocation.contributions every event, which is O(orders ever placed) under EXTERNAL routing. | high | common | probes/p4_broker_misc.py: 1 live key after 10k/20k/40k churn -> 0.133s/0.291s/0.611s per 100 iterations (linear in history). | Amortized compaction: when dead entries exceed live entries a write rebases onto a fresh store holding only live keys (older views keep their store); iteration O(live). | v3.10.0 | tests/regression/test_persistent_map_compaction.py | implemented (v3.10.0) | FIX |
| PRF-003 | ExternalOrderMap.bind copies both dicts per bind (quadratic over a session) and ReconciliationLog.record concatenates tuples, keeping every duplicate forever. | medium | broker | probes/p4: bind 2k/4k/8k -> 0.033s/0.092s/0.338s. | PersistentMap-backed maps; append-only logs. | v3.10.0 | tests/regression/test_broker_containers_complexity.py | implemented (v3.10.0) | FIX |
| PRF-004 | Every durable state keeps its complete event history in memory and every checkpoint serializes all of it; long live sessions grow without bound and periodic checkpoints cost O(N) each (O(N^2) total). | high | runtime/persistence | Code review of snapshot records (market history, risk history, execution history, portfolio events, steps). | Declared retention policies for derived histories (bounded market history window for HistoryView; step log retention), and incremental checkpoints (base snapshot + appended segments) with digest chaining. | v3.12.0 | tests/regression/test_retention.py; tests/regression/test_checkpoints.py; tests/unit/common/test_append_log.py | implemented (v3.12.0) | IMPLEMENT |
| PRF-005 | The dual active-set QP is dense, pure Python and cubic in the universe; measured only to 50 assets (nowandfuture s20 'UNKNOWN'). | medium | portfolio_optimizer | Documentation; code review. | Measure 100/200/400/800; exploit factor-model covariance structure (B F B' + D) in construction for large universes; document the measured envelope. | v3.12.0 | tests/unit/portfolio_optimizer/test_factor_quadratic.py; tests/unit/portfolio_optimizer/test_construction_factor_path.py | implemented (v3.12.0) | IMPLEMENT |
| NUM-001 | The statistics authority accepts NaN and infinity: ranks([1, nan, 0.5, 2]) returns (1, 2, 3, 4) (wrong and input-order dependent), percentile is order-dependent with NaN, mean/variance/correlation propagate NaN silently. | high | common | Probe: ranks and percentile results vary with input order. Verified. | Refuse non-finite inputs in every statistics function with AlphaLabValidationError naming the position. | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| NUM-002 | pearson_correlation raises ZeroDivisionError for tiny magnitudes (1e-160) and OverflowError for huge ones (1e160) because variance_x*variance_y under/overflows; the result is not clamped to [-1, 1]. | medium | common | Probe. Verified. | Guard the pathological path (product 0 or inf -> sqrt(vx)*sqrt(vy)) so ordinary inputs stay bit-identical; clamp to [-1, 1]. | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| NUM-003 | r_squared is reported as 0.0 for a constant y (0/0 undefined). | low | common | Code review. | None for undefined R^2 (typed float \| None). | v3.12.0 | tests/unit/common/test_statistics.py | implemented (v3.12.0) | FIX |
| NUM-004 | _norm_cdf = 0.5*(1+erf(x/sqrt2)) loses relative precision in the lower tail (deep out-of-the-money values); time to expiry uses 365.25 days while theta divides by 365. | medium | options | Code review (standard numerical analysis). | Use 0.5*erfc(-x/sqrt2) for x < 0; one stated year basis recorded in ModelAssumptions. | v3.12.0 | tests/unit/options/test_carry.py; tests/unit/options/test_implied_and_expiration.py | implemented (v3.12.0) | FIX |
| NUM-005 | Black-Scholes has no dividend/cost-of-carry yield: index options (dividends), FX options (foreign rate, Garman-Kohlhagen) and options on futures (Black-76) are mispriced and their implied volatilities biased. | high | options | Code review: d1 uses rate only; ModelAssumptions.models_dividends False. | Generalized Black-Scholes-Merton with an explicit carry (b = r - q; FX q = r_foreign; futures b = 0), required in the model assumptions; implied volatility inverts the same formula. | v3.11.0 | tests/unit/options/test_carry.py | implemented (v3.11.0) | IMPLEMENT |
| NUM-006 | No American option pricing (ROADMAP 'deliberate boundary'), although listed US equity options are American; implied vols from American quotes are computed with a European model. | medium | options | ROADMAP.md deliberate boundaries; pricing.py docstring. | Cox-Ross-Rubinstein binomial lattice with discrete-dividend support and early exercise, deterministic step count stated in assumptions; IV inversion through it. | v3.13.0 | planned | not_started | IMPLEMENT |
| NUM-007 | Linear regression solves the normal equations with a Gauss-Jordan inverse (squares the condition number); no rank or conditioning diagnostics. | medium | ml | Code review. | Householder QR (or Cholesky with ridge) with a rank-deficiency refusal naming the condition estimate. | v3.12.0 | tests/unit/ml/test_ml.py | implemented (v3.12.0) | FIX |
| NUM-008 | Legacy slippage/commission models quantize to 4 dp (per-unit slippage for low-priced instruments rounds to zero) and MarketImpactSlippage divides by a magic 1000. | medium | execution | Code review. | Compute exactly in the pinned context; impact scale an explicit parameter. | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| DET-001 | Job outcomes are applied in as_completed (OS) order: the results mapping order, the distributed event log order and seeded event identifiers differ run to run. | medium | cloud_research | Code review. | Collect all outcomes, then apply them in submission order. | v3.10.0 | tests/regression/test_cluster_outcomes_are_ordered.py | implemented (v3.10.0) | FIX |
| DET-002 | The optimizer stamps its state with time.time() and records perf_counter evaluation times inside trial results. | medium | optimizer | grep time.time/perf_counter. | Caller-supplied instants; elapsed measurement optional and excluded from results' identity. | v3.10.0 | n/a since v3.12: the optimizer and its pin (test_optimizer_is_reproducible.py) were removed (SCF-003) | implemented (v3.10.0); superseded by the optimizer's removal in v3.12 | FIX |
| DET-003 | random.Random seeds by absolute value, so seeds s and -s mint identical identifier streams; getrandbits stream stability is not formally guaranteed by the Python documentation (only random()). | low | common | CPython seeding semantics. | Refuse negative seeds; pin the stream with a cross-version golden test. | v3.10.0 | tests/regression/test_id_stream_is_pinned.py | implemented (v3.10.0) | FIX |
| DET-004 | Stochastic initializers default seed=42 (dense, conv1d, lstm, random search), contradicting invariant 20 (every stochastic step takes an explicit seed, no default). | medium | deep_learning/optimizer | TOC scan of signatures. | Seed required. | v3.10.0 | tests/regression/test_seeds_are_never_defaulted.py | implemented (v3.10.0) | FIX |
| REP-001 | The engine version comes from installed distribution metadata with a hard-coded fallback, so a stale editable install reports a version different from the source actually imported; fingerprints and dataset provenance inherit it. | medium | common/lifecycle | Code review. | Single source of truth in alphalab/_version.py read by hatch (dynamic version) and by the package; a regression test asserts the three agree. | v3.10.0 | tests/regression/test_one_version_source.py; tests/unit/test_package_metadata.py | implemented (v3.10.0) | FIX |
| REP-002 | Engine identity is a version string only; two builds of the same version with different code (local patches, commits between releases) are indistinguishable, and the time-zone database version that decides every local-time computation is not recorded. | medium | lifecycle | Code review. | Record an engine source digest (sorted .py files of the imported package) and the tz database version where discoverable in manifests (not in the strategy fingerprint key). | v3.11.0 | tests/unit/lifecycle/test_engine_build.py | implemented (v3.11.0) | IMPLEMENT |
| REP-003 | No rerun harness: a manifest states what a rerun needs but nothing re-executes a run from a manifest and its supplied inputs and compares the result identity (ROADMAP optional evolution). | medium | lifecycle | ROADMAP.md. | rerun_from_manifest(manifest, inputs) that rebuilds the run through the canonical drivers and returns REPRODUCED/DIVERGED with the differing fields. | v3.13.0 | planned | not_started | IMPLEMENT |
| DAT-001 | The canonical bar's timestamp has no defined meaning (interval start or end). The engine treats a bar as knowable and tradable at its timestamp, so start-stamped vendor bars leak the close into the bar's opening instant (look-ahead). | high | market/data | Code review: no convention anywhere; BarClosed dispatched at bar.timestamp. | Canonical convention: a bar is stamped at the end of its interval (the instant it is knowable). Ingestion and normalization require the source's convention (START or END) and shift START-stamped bars by the declared interval; refuse when undeclared. | v3.10.0 | tests/regression/test_bar_stamp_convention.py | implemented (v3.10.0) | FIX |
| DAT-002 | A symbol's final horizon observations are dropped as 'unrealized'; for a delisted symbol this silently excludes the terminal (often catastrophic) return, a survivorship bias in IC and quantile studies. No delisting-return input exists. | high | factor_library/research | Code review. | Accept declared terminal events (delisting with a terminal return or value) per symbol; forward returns spanning a delisting realize the terminal return; unrealized vs delisted reported separately. | v3.11.0 | tests/unit/factor_library/test_v311_research_integrity.py | implemented (v3.11.0) | IMPLEMENT |
| DAT-003 | Forward returns start at the same close the factor was computed from; there is no implementation-lag parameter, so every IC implicitly assumes trading at the observed close. | medium | factor_library | Code review. | forward_returns(frame, horizon, lag) with lag required (0 allowed, stated) and carried on the panel and every diagnostic. | v3.11.0 | tests/unit/factor_library/test_v311_research_integrity.py; tests/regression/test_study_lag_identity.py | implemented (v3.11.0) | FIX |
| DAT-004 | Provider aliases are static and identity is keyed on the symbol: point-in-time symbology (valid-from/valid-to mappings, ticker reuse after delisting, ticker changes) cannot be expressed. | medium | instrument | Code review. | Dated provider aliases (valid_from/valid_to) resolved at the record's instant; guidance to use permanent identifiers as the canonical symbol. | v3.11.0 | tests/regression/test_dated_aliases.py | implemented (v3.11.0) | IMPLEMENT |
| DAT-005 | TimeFrame is a closed enum (1m,5m,15m,1h,4h,1d,1w,1M; no 30m, 2h, seconds, custom) and NormalizationPolicy silently labels bars M1 by default; normalize_wire_bar writes vwap=0 and trade_count=0 as 'not reported' sentinels. | medium | market | Code review. | Interval as a value type (count + unit) with the existing members as constants; timeframe required on the policy; None for unreported vwap/trade_count. | v3.11.0 | tests/regression/test_interval_value_type.py | implemented (v3.11.0) | FIX |
| DAT-006 | scheduler.TradingCalendar decides weekends in UTC with Saturday/Sunday hard-coded and aligns sessions to UTC midnight (wrong for Asia and the Middle East), duplicating data.calendar.MarketCalendar; CRON/SESSION_OPEN/SESSION_CLOSE/BAR_BOUNDARY timers fire once and silently vanish (cron expressions are never parsed). | medium | scheduler | Probe: CRON timer fired once, 0 timers remain. Verified. | Remove the scheduler's calendar in favour of MarketCalendar; implement or remove the unimplemented schedule types (refuse at registration until implemented). | v3.10.0 | tests/regression/test_every_schedule_type_is_kept.py | implemented (v3.10.0) | FIX |
| DAT-007 | Local-time computations depend on the host's IANA tz database version (and on the optional tzdata package on Windows); two machines with different tz versions can disagree on session bounds without any record of which database was used. | low | data | Code review. | Record the tz database version in manifests (REP-002) and document the dependency. | v3.11.0 | tests/unit/lifecycle/test_engine_build.py | implemented (v3.11.0) | KEEP_LIMITATION |
| DAT-008 | All instants are float Unix seconds: resolution is about 0.24 microseconds at current epochs, so nanosecond feeds cannot be represented exactly and sub-microsecond ordering relies on record order. | low | core | IEEE 754 double spacing at 1.7e9 is 2.4e-7. | Document the precision envelope and require sequence-based ordering for same-instant events; no representation change (fundamental and frozen). | v3.12.0 | tests/unit/common/test_time_precision.py | kept as a stated limitation (v3.12.0) | KEEP_LIMITATION |
| BRK-001 | reconcile_snapshot compares fill quantity, price and commission as strings, so numerically equal Decimals with different exponents (100 vs 100.00) are reported as EXECUTION_MISMATCH; snapshot identities render Decimals by str, so equal evidence gets different ids. | medium | broker | probes/p4_broker_misc.py: 3 false divergences for an identical fill. Verified. | Compare numerically; render Decimals in a canonical normalized form inside identities. | v3.10.0 | tests/regression/test_reconciliation_compares_numbers.py | implemented (v3.10.0) | FIX |
| BRK-002 | v3.9 deferred: no venue sequence number; amendments, positions and balances are absolute and applied in delivery order, so out-of-order delivery leaves an older value. | medium | broker | ADR-0044; broker/lifecycle.py docstring. | IMPLEMENT: optional venue sequence on VenueEvent and last-applied sequence per order/position/balance in the mirror; a lower sequence is STALE. Requires the schema-evolution mechanism (PER-001) for BROKER_SNAPSHOT_SCHEMA 2. | v3.11.0 | tests/unit/broker/test_venue_sequence.py | implemented (v3.11.0) | IMPLEMENT |
| BRK-003 | v3.9 deferred: child-order bindings and the request ledger are caller-held values, not persisted with the live envelope. | medium | runtime/broker | ADR-0044. | IMPLEMENT: include both in the live-run envelope (LIVE_SNAPSHOT_SCHEMA 2 via PER-001) so a restarted process resumes algorithm children and retry recognition exactly. | v3.11.0 | tests/integration/test_live_requests_and_children.py | implemented (v3.11.0) | IMPLEMENT |
| BRK-004 | v3.9 deferred: book-to-mirror reconciliation compares one pipeline book with one broker mirror; a book spread across several brokers' accounts cannot be reconciled in one pass. | medium | lifecycle | ADR-0044; ROADMAP. | IMPLEMENT: reconcile_execution_state over a mapping account->mirror with a declared order-to-account assignment; unassigned orders reported. | v3.12.0 | tests/unit/lifecycle/test_reconcile_accounts.py | implemented (v3.12.0) | IMPLEMENT |
| BRK-005 | v3.9 deferred: split routing is a greedy sweep by all-in price, not optimal under per-trade fixed fees or non-linear impact. | medium | execution | routing.py docstring. | IMPLEMENT: exact split for linear per-unit costs plus fixed fees by subset enumeration (bounded venue count, refusal beyond), and marginal-cost equalization for convex impact; greedy kept as a named objective. | v3.13.0 | planned | not_started | IMPLEMENT |
| BRK-006 | v3.9 deferred: urgency is stated, never estimated; iceberg tranches are fixed, never randomized. | low | execution | ADR-0044. | IMPLEMENT: urgency estimated from stated risk aversion, volatility and impact (Almgren-Chriss kappa, documented formula, pinned context); seeded randomized tranche sizes with the seed in the configuration identity. | v3.13.0 | planned | not_started | IMPLEMENT |
| BRK-007 | The library holds venue credentials (api_key, api_secret) and a concrete HMAC-signed REST transport for a made-up /v1/orders protocol, contradicting ADR-0018 ('no credential handling') and the AlphaLab/iluvtrade boundary (vendor credentials and broker-specific transport are the application's). | high | broker | Code review. | REPLACE: keep BrokerProtocol, the canonical vocabulary, PaperBroker and the normalized event contract; move the reference REST adapter and its credentials to tests/examples as a reference adapter (not importable from alphalab). | v3.11.0 | tests/regression/test_removed_surfaces_stay_removed.py | implemented (v3.11.0) | REMOVE |
| BRK-008 | PaperBroker charges zero commission ('Simplification for paper broker'), so paper P&L is biased against live in the expected/paper/live comparison. | medium | broker | paper.py:156. | PaperBroker takes an ExecutionCostModel (the v3.3 authority) with no default. | v3.11.0 | tests/unit/broker/test_paper_costs.py | implemented (v3.11.0) | FIX |
| PER-001 | Each snapshot subsystem reads exactly one schema version and 'there is no migration path'. After a long-lived v4 freeze, any fix that needs a new durable field makes every earlier payload unreadable; several deferred items (BRK-002/003) are blocked on it. | high | persistence | nowandfuture.md s9 and s14 invariant 2. | Explicit, versioned, tested upgrade functions per subsystem (vN -> vN+1, pure, composable), invoked before typed decoding; every historical version kept readable by golden payload fixtures. | v3.10.0 | tests/regression/test_schema_upgrades.py; tests/fixtures/snapshots/v3.9.0 | implemented (v3.10.0) | IMPLEMENT |
| PER-002 | serialize() claims strict JSON but uses json.dumps(allow_nan=True): NaN/Infinity become non-standard tokens that strict parsers reject and that break equality on round trip; Decimals serialize by str so numerically equal values can render differently. | medium | persistence | Code review. | allow_nan=False (refuse) and document the Decimal exponent rule for identities. | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| PER-003 | Atomic writes fsync the file but not the containing directory after os.replace, so a crash can lose a rename that was reported durable. | low | persistence | POSIX durability rules. | fsync the directory after rename where the platform supports it. | v3.12.0 | tests/regression/test_durable_writes.py | implemented (v3.12.0) | FIX |
| PER-004 | Plain Enum members persist as 'ClassName.MEMBER', coupling every stored payload to Python class names. | low | persistence | Code review. | Document class-name stability as part of the v4 serialization contract and pin with golden payloads. | v4.0.0 | planned | not_started | KEEP_LIMITATION |
| BND-001 | Vendor-named market-data clients in the library: four raise NotImplementedError ('Not yet implemented'), Binance speaks Binance's endpoint shapes and stamps quotes with time.time(); configs carry api_key fields; class names are lowercase. | high | marketdata | Code review. | REMOVE the five vendor subpackages (vendor adapters are the application's). | v3.10.0 | tests/regression/test_no_vendor_in_the_library.py; tests/regression/test_market_model_convergence.py | implemented (v3.10.0) | REMOVE |
| BND-002 | alphalab.enterprise implements principals, sessions with TTL, workspaces, secret references with rotation and compliance reports - SaaS user/tenant management that belongs to the application; lifecycle governance imports it for RBAC. | high | enterprise/lifecycle | Code review. | REPLACE: a PermissionAuthority protocol (permission check + actor id) in lifecycle.governance with a minimal deterministic reference implementation; remove sessions, workspaces, secrets and compliance. | v3.11.0 | tests/regression/test_removed_surfaces_stay_removed.py; tests/regression/test_lifecycle_governance.py | implemented (v3.11.0) | REPLACE |
| BND-003 | alphalab.workbench is UI state (themes, panels with width ratios, tabs, layouts, animation flags, frontend sessions) whose backtest delegation records caller-supplied 'simulated_metrics'. | medium | workbench | Code review. | REMOVE. | v3.11.0 | tests/regression/test_removed_surfaces_stay_removed.py | implemented (v3.11.0) | REMOVE |
| SCF-001 | Strategy Studio 'runs' backtests and pipelines by storing caller-supplied simulated metrics; carries users, sessions and workspaces; StudioConfig defaults currency USD and workspace '/workspace'; ResearchResult has an overall_score. Its one canonical type, StrategyDefinition (ADR-0035), only admits float parameters. | medium | studio | Code review. | Move StrategyDefinition to alphalab.strategy (typed ParamValue parameters, re-exported during v3.x) and remove the orchestration scaffold. | v3.11.0 | tests/regression/test_removed_surfaces_stay_removed.py | implemented (v3.11.0) | REPLACE |
| SCF-002 | Three v1 provider-surface packages with no consumer duplicate the canonical market model: feed.normalization hard-codes currency='USD', uses the provider symbol as asset_id and defaults unknown timeframes to M1 (a second, unsafe normalization authority); live.LiveAdapter emits untyped dicts; marketdata's engine carries api_key/api_secret configs, an empty BaseClient and metrics never updated. | high | feed/live/marketdata | Code review; grep shows market imports only marketdata.feed, marketdata.timeframe and marketdata.websocket. | REMOVE feed and live; reduce marketdata to its transports (HTTP, RFC 6455 WebSocket) and the wire re-export used by market. | v3.10.0 | tests/regression/test_market_model_convergence.py; tests/regression/test_v39_invariants.py; tests/integration/test_provider_source_session.py | implemented (v3.10.0) | REMOVE |
| SCF-003 | Several v1 'engine series' packages are bookkeeping shells or duplicates: plugins (execute() is a NotImplementedError placeholder), scheduler (see DAT-006), distributed (a job table that executes nothing; cancel stores a cancelled job as failed without an event), reporting (dashboards = presentation), optimizer and research_assistant (two of four parameter-search implementations). | medium | plugins/scheduler/distributed/reporting/optimizer/research_assistant | Code review. | Per-package decision recorded in an ADR: consolidate parameter search into one research authority; keep distributed+cloud_research as the local parallel-execution capability (fixed); remove plugins and reporting dashboards; scheduler reduced to deterministic timers over MarketCalendar or removed. | v3.12.0 | tests/regression/test_removed_surfaces_stay_removed.py; tests/regression/test_shared_names_stay_distinct.py; tests/unit/research/test_signal_and_robustness.py; tests/unit/research_assistant/test_research_assistant.py; tests/unit/cloud_research/test_cloud_research.py; tests/unit/distributed/test_distributed.py; tests/unit/scheduler/test_session_timers.py; tests/regression/test_every_schedule_type_is_kept.py; tests/unit/reporting/test_reporting.py | implemented (v3.12.0) | REPLACE |
| SCF-004 | LSTM and attention are forward-only (no backpropagation), so the documented 'LSTM, transformers' capability cannot train. | medium | deep_learning | Module docstrings. | IMPLEMENT backpropagation through time for LSTM and the attention backward pass, gradient-checked against numerical gradients. | v3.12.0 | tests/unit/deep_learning/test_sequence_backprop.py | implemented (v3.12.0) | IMPLEMENT |
| API-001 | 72 distinct objects share a public name across packages (e.g. brokers.ExecutionReport is broker.BrokerExecution, not execution.ExecutionReport; core.Trade vs data.Trade; runtime.TradingSession vs scheduler.TradingSession; runtime.snapshot.MarketRecord vs market.MarketRecord; two SessionCalendar protocols; RouteDecision vs RoutingDecision; three AssetClass enums). | medium | repository | Runtime identity comparison over every package __all__. | Remove accidental duplicates, rename collisions, keep only documented deliberate pairs (pinned by test_shared_names_stay_distinct), and publish a V4 public API manifest with an import regression test. | v4.0.0 | planned | not_started | FIX |
| API-002 | 2,589 package-level exported names (2,450 unique) and 479 modules without __all__; no statement of which surfaces are frozen. | medium | repository | AST inventory. | V4 public API manifest (docs/api/PUBLIC_API_V4.md + machine-readable list) and a regression test that fails on any unlisted addition or removal. | v4.0.0 | planned | not_started | IMPLEMENT |
| API-003 | Silent 'USD' defaults remain on configuration dataclasses (RoutingConfig.currency, ExecutionPipelineConfig.currency, NormalizationPolicy.currency/venue/timeframe, venue broker currency, RL environment, StudioConfig), and the defaults sweep misses dataclass fields outside its v3.4 scope, call-site keyword literals and .get() fallbacks. | medium | repository | grep for "USD"; test_no_silent_financial_defaults.py scope. | Remove currency defaults from configuration; extend the sweep to dataclass fields, call-site literals of financial keywords and mapping .get defaults on financial names. | v3.10.0 | tests/regression/test_no_silent_financial_defaults.py; tests/regression/test_v34_invariants.py; tests/regression/test_currency_authority.py; tests/unit/market/test_normalization.py | implemented (v3.10.0) | FIX |
| API-004 | BacktestResult.valuation takes no FX rates, so it raises for every multi-currency run. | low | backtesting | Code review. | valuation_in(currency, rates) method; the property documented as single-currency only. | v3.12.0 | tests/regression/test_backtest_result_valuation.py | implemented (v3.12.0) | FIX |
| REL-001 | On a failed connection the reconnect path drops the old WebSocket without closing its socket: every reconnect leaks a file descriptor until garbage collection (ResourceWarning). | high | market | pytest -W error: 5 streaming tests fail and one unrelated test errors when GC finalizes the leaked socket. Passes without -W error, so CI is blind to it. | Close the transport's socket on every failure path (idempotent close) and run the suite with ResourceWarning as an error in CI. | v3.10.0 | tests/regression/test_websocket_releases_its_socket.py | implemented (v3.10.0) | FIX |
| TST-001 | Seven complexity guards time the wall clock with the garbage collector running and fail under load (baseline run: replay cursor 12x for 4x input under a concurrent mypy; defect-injection run: scheduler scaling failed under load). | medium | tests | nowandfuture.md s20 'KNOWN CAVEAT'; observed failures during this audit. | Move every guard to the stabilized method (CPU time, collector off, interleaved sizes, median of repeats) already used by v3.8/v3.9 guards. | v3.10.0 | tests/regression/_timing.py and every *_complexity.py guard | implemented (v3.10.0) | FIX |
| TST-002 | One test is skipped when the suite runs as root ('root bypasses directory permissions'), so the '0 skipped' claim depends on the environment. | low | tests | Baseline run in this container: 1 skipped. | Exercise the refusal through a condition root cannot bypass (e.g. a root path that is a file / a read-only mount simulated by a non-directory), keeping zero skips everywhere. | v3.10.0 | tests/regression/test_run_state_store.py; tests/regression/test_the_suite_reports_nothing_deferred.py | implemented (v3.10.0) | FIX |
| TST-003 | CI does not run pytest with warnings as errors (ResourceWarnings invisible), does not run examples or benchmarks, and does not install the built wheel into a clean environment. | medium | ci | ci.yml. | CI: pytest -W error; examples; wheel + sdist clean-venv install and import smoke; benchmarks smoke (reduced sizes) on a schedule. | v3.10.0 | tests/regression/test_release_gates_are_wired.py; tests/installed_smoke.py | implemented (v3.10.0) | FIX |
| TST-004 | pre-commit pins ruff v0.12.0 and mypy v1.16.0 (pyproject requires ruff 0.16 / mypy 2.x) and runs mypy without pytest installed, so the hooks disagree with CI. | low | tooling | Config review. | Align hook revisions with pyproject and add pytest to the mypy hook's additional_dependencies. | v3.10.0 | tests/regression/test_release_gates_are_wired.py | implemented (v3.10.0) | FIX |
| TST-005 | Defect injection found mutations the suite does not catch (see the master audit, section W, for the full table); every undetected mutation needs a pinning test. | medium | tests | scratch defect-injection harness over 24 mutations. | A regression test per undetected mutation, then re-run the harness. | v3.10.0 | tests/regression/test_mutation_pins.py; the v3.10 harness re-run (master audit, section W) | implemented (v3.10.0) | FIX |
| TST-006 | Every 'linear' performance claim on the canonical path was measured with a single asset; there is no guard on growth in universe size. | high | tests | PRF-001 probe. | Complexity guard over assets x bars (stabilized method) and a multi-asset benchmark; defect-inject the old per-event full re-mark and prove the guard fails. | v3.10.0 | tests/regression/test_universe_scaling.py | implemented (v3.10.0) | FIX |
| TST-007 | Inconsistent benchmark file names (typo 'schedular', 'benchmarks_' prefix). | low | benchmarks | ls benchmarks. | Rename to benchmark_scheduler_engine.py, benchmark_execution.py, benchmark_market_engine.py. | v3.10.0 | tests/regression/test_release_gates_are_wired.py | implemented (v3.10.0) | FIX |
| DOC-001 | Current-facing documents state capabilities the code does not have: 'canonical path is linear' (single-asset only), '0 skipped, 0 warnings' (environment-dependent; ResourceWarnings hidden), 'exact at the currency's minor unit', 'no credential handling', 'names no venue', Intent as a target, and list scenario nowhere among standalone engines in nowandfuture s4. | medium | docs | Cross-check against code and probes in this audit. | Correct every statement as the fixes land; each release's documentation checked against the ledger. | v3.10.0 | docs review | implemented (v3.10.0) | FIX |
| DOC-002 | Several items classed 'optional future evolution' are required for a complete v4 (schema evolution, order types, derivatives accounting, multi-broker reconciliation, rerun harness, v3.9 deferred execution items), and some 'deliberate boundaries' state reasons that do not hold (e.g. Holm's step-down needs no assumption beyond Bonferroni's; Newey-West corrects IC overlap). | medium | docs | This audit's re-classification. | Re-classify per the ledger; at v4 the roadmap lists only boundaries and external dependencies. | v3.10.0 | docs review | implemented (v3.10.0) | FIX |
| OFE-001 | Optional future evolution: classification dimensions beyond sector; sector-based pre-trade limits | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: arbitrary named classification dimensions (industry, country, issuer, rating) with provenance in the registry; optional classification-bucket pre-trade limits. | v3.12.0 | tests/integration/test_classification_limits.py; tests/integration/test_bucket_gross_kept_by_the_book.py; tests/unit/portfolio/test_book_groups.py; tests/regression/test_sector_classification_reaches_attribution.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-002 | Optional future evolution: richer portfolio construction (estimated shrinkage, EWMA, factor-model covariance, cardinality/lot constraints, costs in objective, CVaR/drawdown objectives, multi-period) | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: Ledoit-Wolf estimated shrinkage and EWMA covariance as recorded derivations; factor-model covariance (B F B' + D); transaction costs (linear) in the objective; lot-size rounding as a post-solve refusal-reporting step. KEEP_BOUNDARY: cardinality (non-convex, needs MIQP), CVaR/drawdown objectives and multi-period construction are out of scope for a certified convex solver and are named as such. | v3.11.0 | tests/unit/analytics/test_covariance_estimators.py; tests/unit/portfolio_optimizer/test_costs_and_lots.py; tests/unit/conventions/test_conventions.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-003 | Optional future evolution: per-strategy capital ceilings on the execution path | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: CapitalBudget.strategy_budgets enforced as ceilings (reservations per strategy) when declared enforceable. | v3.12.0 | tests/integration/test_strategy_ceilings.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-004 | Optional future evolution: neutralization against several continuous exposures | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: multivariate neutralization by QR with explicit rank/condition refusal (the stated reason for not offering it is a numerical-stability concern, which QR with a refusal answers). | v3.11.0 | tests/unit/factor_library/test_v311_research_integrity.py; tests/unit/common/test_linalg.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-005 | Optional future evolution: deflated Sharpe ratio; corrections beyond Bonferroni | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: Holm step-down (same assumptions as Bonferroni, uniformly more powerful), Benjamini-Yekutieli FDR (valid under arbitrary dependence), probabilistic and deflated Sharpe ratios (Bailey & Lopez de Prado; skew/kurtosis-adjusted, iid assumption stated). | v3.11.0 | tests/unit/research/test_multiple_testing.py; tests/unit/research/test_sharpe_inference.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-006 | Optional future evolution: t-statistic on an information coefficient | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: Newey-West HAC standard error with lag = horizon - 1 (the standard correction for overlapping forward returns), reported with its lag. | v3.11.0 | tests/unit/common/test_statistics_v311.py; tests/unit/factor_library/test_v311_research_integrity.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-007 | Optional future evolution: half-life fitted to a decay profile | low | roadmap | ROADMAP.md / nowandfuture.md s17. | KEEP_BOUNDARY: a fitted functional form is a model choice; the profile and first-negative horizon stay the measurement. | none | planned | not_started | KEEP_BOUNDARY |
| OFE-008 | Optional future evolution: a vendor adapter package | low | roadmap | ROADMAP.md / nowandfuture.md s17. | EXTERNAL: vendor adapters belong to the application (iluvtrade). | none | planned | not_started | EXTERNAL |
| OFE-009 | Optional future evolution: execution-path delivery of external information | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: point-in-time observations delivered to strategies as execution-path events at their knowledge instant (merged with market records by instant, availability rule applied), with snapshot support through PER-001. | v3.12.0 | tests/integration/test_external_information.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-010 | Optional future evolution: statistical regime models (hidden Markov, Markov switching) | low | roadmap | ROADMAP.md / nowandfuture.md s17. | KEEP_BOUNDARY: estimation models with fitting windows are research models outside the engine's declared-rule regime layer; ML package covers supervised estimation. | none | planned | not_started | KEEP_BOUNDARY |
| OFE-011 | Optional future evolution: streaming observation set; split-adjusted per-share fundamentals; converted-currency figures | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: incremental ObservationSet appends with vintage checks (needed by OFE-009); per-share fundamentals adjusted through the corporate-action authority; currency conversion of fundamentals through FxRates with recorded rates. | v3.12.0 | tests/integration/test_external_information.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-012 | Optional future evolution: consolidating the two identical AssetClass enums in live.provider and marketdata.symbols | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | REMOVE: both packages' provider scaffolding is removed (SCF-002); core.enums.AssetType is the one taxonomy. | v3.10.0 | tests/regression/test_market_model_convergence.py | implemented (v3.10.0) | REMOVE |
| OFE-013 | Optional future evolution: a start offset on AppendOnlyLog (OptimizerState.pending_trials super-linear) | low | roadmap | ROADMAP.md / nowandfuture.md s17. | REPLACE: optimizer consolidation (SCF-003) removes the super-linear term; no AppendOnlyLog change. | v3.12.0 | tests/regression/test_removed_surfaces_stay_removed.py; tests/regression/test_standalone_state_scaling.py | implemented (v3.12.0) | REPLACE |
| OFE-014 | Optional future evolution: pipeline-driven on_fill / on_order / on_timer | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (EXE-005). | v3.11.0 | tests/integration/test_strategy_feedback.py; tests/unit/strategy/test_subscriptions_and_start.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-015 | Optional future evolution: per-strategy sub-ledgers in PortfolioEngine | low | roadmap | ROADMAP.md / nowandfuture.md s17. | KEEP_BOUNDARY: the contribution ledger and multi-strategy books answer attribution without a second book of record. | none | planned | not_started | KEEP_BOUNDARY |
| OFE-016 | Optional future evolution: a durable home for a StrategyProgression / fingerprints / manifests / reports | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: a generic content-addressed evidence store over the existing artifact store (values serialized deterministically, addressed by their own identities); LifecycleState unchanged. | v3.12.0 | tests/regression/test_evidence_store.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-017 | Optional future evolution: health evaluated over a window | low | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: evaluate_health_window over a supplied series of observations with a stated window; pure, no clock. | v3.12.0 | tests/unit/lifecycle/test_health.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-018 | Optional future evolution: derived alignment for a comparison | low | roadmap | ROADMAP.md / nowandfuture.md s17. | KEEP_BOUNDARY: fuzzy matching would report a pairing as a measurement. | none | planned | not_started | KEEP_BOUNDARY |
| OFE-019 | Optional future evolution: a lock-file reader | low | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT: read pip-compile/uv/poetry lock text into a DependencyManifest (EXACT_CLOSURE only when the lock is complete). | v3.13.0 | planned | not_started | IMPLEMENT |
| OFE-020 | Optional future evolution: a rerun harness | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (REP-003). | v3.13.0 | planned | not_started | IMPLEMENT |
| OFE-021 | Optional future evolution: venue sequence number (v3.9) | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (BRK-002). | v3.11.0 | tests/unit/broker/test_venue_sequence.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-022 | Optional future evolution: persisting child bindings and request ledger (v3.9) | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (BRK-003). | v3.11.0 | tests/integration/test_live_requests_and_children.py | implemented (v3.11.0) | IMPLEMENT |
| OFE-023 | Optional future evolution: book-to-mirror reconciliation across brokers (v3.9) | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (BRK-004). | v3.12.0 | tests/unit/lifecycle/test_reconcile_accounts.py | implemented (v3.12.0) | IMPLEMENT |
| OFE-024 | Optional future evolution: optimal split (v3.9) | medium | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (BRK-005). | v3.13.0 | planned | not_started | IMPLEMENT |
| OFE-025 | Optional future evolution: estimated urgency and randomized iceberg tranches (v3.9) | low | roadmap | ROADMAP.md / nowandfuture.md s17. | IMPLEMENT (BRK-006). | v3.13.0 | planned | not_started | IMPLEMENT |
| OFE-026 | Optional future evolution: hook timeout, plugin static analysis, independently versioned strategy-api, hot reload, threading model (docs/architecture/strategy) | low | roadmap | ROADMAP.md / nowandfuture.md s17. | KEEP_BOUNDARY: process supervision, hot reload and threading are the host application's; strategy API versioning is covered by the v4 API manifest. | none | planned | not_started | KEEP_BOUNDARY |
| FEA-001 | Target-position and target-weight intents with a deterministic rebalancer | high | proposal | Gap: Intent is a delta; construction ends at weights ('the caller's decision'). | IntentKind {DELTA, TARGET_QUANTITY, TARGET_WEIGHT}; allocation computes delta = target - position - working, rounds to the instrument's lot (conventions.lot) and min notional, refuses rather than scales. | v3.11.0 | tests/integration/test_target_intents.py; tests/unit/allocation/test_budget_commitment.py | implemented (v3.11.0) | IMPLEMENT |
| FEA-002 | Benchmark-relative performance statistics | medium | proposal | Gap: Only portfolio_beta exists (decomposition). | benchmark_statistics(portfolio, benchmark, basis) with None for undefined values. | v3.11.0 | tests/unit/analytics/test_benchmark_statistics.py | implemented (v3.11.0) | IMPLEMENT |
| FEA-003 | Walk-forward optimization harness | medium | proposal | Gap: Splits and sweeps exist separately; nothing composes them with fold-level evidence. | walk_forward_optimize(study, space, objective, splits, seed) returning per-fold selections, OOS metrics and a study result with identity. | v3.11.0 | tests/unit/research/test_walk_forward_optimization.py | implemented (v3.11.0) | IMPLEMENT |
| FEA-004 | Trade-print ingestion from flat files | medium | proposal | Gap: RecordType has BAR and QUOTE only. | RecordType.TRADE with a required declared schema (price, size, instant, optional trade id/aggressor); the ambiguity is resolved by declaration, never inference. | v3.12.0 | tests/integration/test_trade_prints.py | implemented (v3.12.0) | IMPLEMENT |
| FEA-005 | Total-variance interpolation across expiries on a volatility surface | low | proposal | Gap: Surface refuses any unquoted expiry. | Named method TOTAL_VARIANCE_LINEAR, refusing calendar-arbitrage inputs (decreasing total variance) and extrapolation. | v3.13.0 | planned | not_started | IMPLEMENT |
| FEA-006 | Engine-parity certification report for a release | low | proposal | Gap: Evidence scattered across tests. | Generate docs/audit/V4_RELEASE_CERTIFICATION.md evidence from a certification script run in CI. | v4.0.0 | planned | not_started | IMPLEMENT |
| BDY-001 | Deliberate boundary re-examined: No single runtime spanning all engines | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP: ADR-0009 holds; standalone engines stay standalone where they are coherent. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-002 | Deliberate boundary re-examined: No allocation visibility inside StrategyContext | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-003 | Deliberate boundary re-examined: No depth hook on StrategyProtocol | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP for v4; depth reaches strategies through MarketView history. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-004 | Deliberate boundary re-examined: No per-environment promotion policy | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-005 | Deliberate boundary re-examined: No remediation in health or reconciliation | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP: detection only; remediation is the caller's. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-006 | Deliberate boundary re-examined: No default tolerance anywhere | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-007 | Deliberate boundary re-examined: No migration framework | high | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | CHANGE: replaced by explicit versioned upgrade functions (PER-001). | v3.10.0 | tests/regression/test_schema_upgrades.py | implemented (v3.10.0) | REPLACE |
| BDY-008 | Deliberate boundary re-examined: No durable state for the v3.5/v3.6 values | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | CHANGE: content-addressed evidence store (OFE-016); LifecycleState unchanged. | v3.12.0 | tests/regression/test_evidence_store.py | implemented (v3.12.0) | REPLACE |
| BDY-009 | Deliberate boundary re-examined: No SettlementPolicy object; no triangulation/implicit inversion/default rate | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-010 | Deliberate boundary re-examined: No authentication, credential handling, IAM or federation | high | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP and ENFORCE: remove the credential-bearing transport (BRK-007) and enterprise sessions/secrets (BND-002), which contradict it. | v3.11.0 | tests/regression/test_removed_surfaces_stay_removed.py; tests/regression/test_lifecycle_governance.py | implemented (v3.11.0) | KEEP_BOUNDARY |
| BDY-011 | Deliberate boundary re-examined: No supervised live process; no CLI/server/daemon/event bus | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-012 | Deliberate boundary re-examined: No way to fill a missing price; no repair of an impossible bar | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-013 | Deliberate boundary re-examined: No holiday data, corporate-action feed, exchange registry, tick table, venue list | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP (EXTERNAL data). ISO 4217 minor units are a published standard, not market data, and are shipped as such (ACC-001). | none | planned | not_started | EXTERNAL |
| BDY-014 | Deliberate boundary re-examined: No curve bootstrapper; no fixed-income engine | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP: fixed income remains a foundation; out of the engine's systematic-trading mission. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-015 | Deliberate boundary re-examined: No volatility-surface fit; no interpolation across expiries | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | PARTIAL CHANGE: parametric fits (SVI/SABR) stay out; named total-variance interpolation added (FEA-005). | v3.13.0 | planned | not_started | KEEP_BOUNDARY |
| BDY-016 | Deliberate boundary re-examined: No American option pricing | medium | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | CHANGE: implement (NUM-006). | v3.13.0 | planned | not_started | REPLACE |
| BDY-017 | Deliberate boundary re-examined: No inferred roll rule | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-018 | Deliberate boundary re-examined: No trade or depth ingestion from a flat file | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | PARTIAL CHANGE: declared trade-print ingestion (FEA-004); depth stays out. | v3.12.0 | tests/integration/test_trade_prints.py | kept as a boundary, partly changed (v3.12.0) | KEEP_BOUNDARY |
| BDY-019 | Deliberate boundary re-examined: No marketplace logic; no overall certification score; no verdict accepted as evidence | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-020 | Deliberate boundary re-examined: No dependency resolver and no environment snapshot | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP; the lock-file reader (OFE-019) reads a declared file and resolves nothing. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-021 | Deliberate boundary re-examined: No adaptation for portability | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-022 | Deliberate boundary re-examined: No default availability; no hindsight vintage policy; no forward fill; no regime taxonomy; no p-value in event studies | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-023 | Deliberate boundary re-examined: No vendor data feed of any kind; no LLM/AI-service/OpenBB/RedDesk integration | high | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP and ENFORCE: remove the vendor market-data stubs that contradict it (BND-001). | v3.10.0 | tests/regression/test_no_vendor_in_the_library.py | implemented (v3.10.0) | KEEP_BOUNDARY |
| BDY-024 | Deliberate boundary re-examined: No silent scaling of capital; no unconstrained answer behind an infeasible one; no default risk aversion/tau/tolerance/shrinkage | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-025 | Deliberate boundary re-examined: No enforcement in a risk budget; no broker adapter in capital allocation | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| BDY-026 | Deliberate boundary re-examined: No capability discovery; no vendor inside the execution contract; no venue event in the broker log; no retry of a venue refusal; no conversion inside routing; no default urgency/quote age/tolerance; no best-execution claim | low | roadmap | ROADMAP.md 'Deliberate boundaries'; nowandfuture.md s15. | KEEP. | none | planned | not_started | KEEP_BOUNDARY |
| ALC-005 | enforce_integer_quantities rounded after the long-only check, so a sale of 0.4 became a zero-quantity SELL order that risk then refused by raising. | high | allocation | tests/regression/test_allocation_semantics.py | Round (nearest, ties to even) before the checks; skip zero. | v3.10.0 | tests/regression/test_allocation_semantics.py | implemented (v3.10.0) | FIX |
| ACC-012 | A zero or negative deposit or withdrawal was booked (a negative deposit is a withdrawal booked as a deposit), and one that rounds to nothing at the currency's minor unit was accepted. | medium | portfolio | tests/regression/test_v310_found_defects.py | Refuse any movement that is not a finite positive amount at the minor unit. | v3.10.0 | tests/regression/test_v310_found_defects.py | implemented (v3.10.0) | FIX |
| ACC-013 | A fill settling in one currency was booked into an open position held in another, mixing two currencies in one basis. | high | portfolio | tests/regression/test_v310_found_defects.py | Refuse a fill whose currency differs from its open position's. | v3.10.0 | tests/regression/test_v310_found_defects.py | implemented (v3.10.0) | FIX |
| ACC-014 | A sale's price was floored at 0.01: a sub-penny instrument was sold at a cent the market never showed, and a concession exceeding the price produced a one-cent fill instead of a refusal. | high | execution | tests/regression/test_v310_found_defects.py | No floor; refuse a concession that takes a sale to zero or below. | v3.10.0 | tests/regression/test_v310_found_defects.py | implemented (v3.10.0) | FIX |
| NUM-009 | Near the float limit the statistics authority either raised a bare OverflowError (variance, regression) or returned infinity for a finite answer (the mean, median and a percentile of values near 1e308). | low | common | tests/regression/test_v310_found_defects.py | Overflow-safe mean/median/percentile; a named refusal when the answer itself is out of range. | v3.10.0 | tests/regression/test_v310_found_defects.py | implemented (v3.10.0) | FIX |
| NUM-010 | Exact quotients (average cost, VWAP, an inverse rate) were written with positive exponents, e.g. 1E+2. | low | portfolio/oms/broker | golden example comparison | Positional notation for exact quotients. | v3.10.0 | tests/unit/portfolio/test_monetary_precision.py | implemented (v3.10.0) | FIX |
| NUM-011 | The correlation of series near 1e-160 was wrong in the fourth digit (0.71809 for 0.71818): their squared deviations are subnormal. | low | common | tests/regression/test_numeric_refusals.py | Correlate at the series' own scale when a spread is below sqrt(float_info.min). | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| PER-005 | The strategy-record decoder compared a payload's version with the current schema rather than with the version that introduced 'state', so any schema bump would refuse every earlier payload that carried state. | low | runtime | code review while implementing PER-001 | Compare with the introducing version. | v3.10.0 | covered by the upgrade path; no separate pin (unreachable) | implemented (v3.10.0) | FIX |
| BND-004 | to_exchange_symbol / parse_exchange_symbol knew Binance, Coinbase and Kraken by name (Kraken's XBT alias): vendor quirks, and a second answer to which instrument a provider symbol denotes beside the instrument registry's aliases. | medium | crypto | tests/regression/test_no_vendor_in_the_library.py | Remove; a venue's spelling is declared as an instrument alias by the host. | v3.10.0 | tests/unit/crypto/test_crypto.py; tests/regression/test_no_vendor_in_the_library.py | implemented (v3.10.0) | REMOVE |
| RES-001 | The v1 research engine (ResearchEngine.run_full_research) assumes a daily return series (calculate_sharpe annualizes with a hard-coded 252) and scores with uncalibrated constants (capacity decay 0.01/0.05/0.15 per 10,000 trades, bias thresholds, an overall score), duplicating the v3.2 validation authority. | medium | research | found by the v3.10 defaults sweep; code review | Consolidate into the one research authority with SCF-003: remove the heuristic scores or restate them over the v3.2 methodology; no default annualization. | v3.12.0 | tests/unit/research/test_research.py; tests/regression/test_no_silent_financial_defaults.py | implemented (v3.12.0) | REPLACE |
| DET-005 | A v3.10 regression, caught before release: once CurrencyAmounts stopped rounding every amount to a cent (and prices and quantities stopped being quantized), the multi-strategy book and valuation identities -- which hashed each Decimal's text -- gave two books that compare equal ('250000' vs '250000.00' of unassigned capital; a position of 100 at 10 vs 100.0 at 10.00) two identities. v3.9 gave them one. | medium | portfolio | golden example comparison (example 58); probe; tests/regression/test_identity_is_by_value.py fails with the pre-fix rendering | Render every amount in an identity by value. | v3.10.0 | tests/regression/test_identity_is_by_value.py | implemented (v3.10.0) | FIX |
| DET-006 | Most content identities render a caller-declared Decimal with str() or repr(), so one declaration written '0.5' and '0.50' (a weight, a limit, a rate) gets two identities. Deterministic for identical inputs and unchanged since v3.9 -- these values were never rounded -- but not identity by value. | low | cross-cutting | review of every digest renderer while fixing DET-005 | Render every Decimal in every content identity with canonical_text, bumping each scheme once, in one release. | v3.11.0 | tests/regression/test_identity_by_value.py | implemented (v3.11.0) | FIX |
| API-006 | A v3.10 regression, caught before release: once RiskViolation.severity became a RiskSeverity member, a health finding's detail (Mapping[str, str]) carried the enum member rather than its string, so the 'machine-readable' detail printed as <RiskSeverity.CRITICAL: 'CRITICAL'>. | low | lifecycle | golden example comparison (example 43); tests/unit/lifecycle/test_health.py | Carry the string value. | v3.10.0 | tests/unit/lifecycle/test_health.py::TestRiskBreach::test_a_violation_is_carried_rather_than_re_decided | implemented (v3.10.0) | FIX |
| PRF-006 | v3.10 raised the per-operation constant: run side by side on one machine, v3.9.0 -> v3.10.0 benchmark wall time is 2.2x for the OMS (12.0 -> 26.4 s; accept-and-fill 21.6k -> 6.1k/s at 40k orders), 1.95x for the portfolio engine (3.7 -> 7.2 s; 28.2k -> 14.2k fills/s), 1.37x for a one-asset backtest (7.8 -> 10.7 s) and 1.36x for the execution pipeline (3.9 -> 5.3 s). By profile and by elimination: PositionBook upkeep on every fill (PRF-001's exact totals), PersistentMap compaction bookkeeping and rebase copies (PRF-002; in isolation, inserting is 1.7x, draining 3.75x and overwriting 3.2x slower, reading unchanged), the OMS per-asset working index (PRF-001), and the pinned context and per-currency unit lookups on every fill. | medium | portfolio/oms/common | side-by-side benchmark runs and cProfile of apply_fill and the OMS accept-and-fill stage, v3.9.0 vs v3.10.0, in the v3.10 release gates | An optimization pass with a v3.9-relative budget on one-asset paths: a cheaper rebase copy (a newest-view fast path measured 15-20% on draining), no default set built per OMS update and no write for an unchanged index (about 8% on accept-and-fill), and PositionBook totals updated without re-deriving both entries per fill. | v3.11.0 | tests/regression/test_prf006_fast_paths.py | implemented (v3.11.0) | FIX |
| TST-008 | Four v3.10 fixes (NUM-001, NUM-002, NUM-008, PER-002) had landed in the working tree without tests, and a full `mypy .` had not been run after two sessions' test additions (34 strict errors). | medium | tests | evidence assembly for the v3.10 CHANGELOG; mypy run | Every ledger item's evidence names its test; the release gate runs mypy over the whole tree. | v3.10.0 | tests/regression/test_numeric_refusals.py | implemented (v3.10.0) | FIX |
| NUM-012 | The run drivers were not pinned to ACCOUNTING_CONTEXT, only the engines: ExecutionPipeline negated a sale's quantity in the caller's context (unary minus rounds a Decimal), exposure sums ran there too, and LiveSession raised InvalidOperation from the venue mirror under a low precision. | high | runtime | Probe: under decimal precision 5 a sale of 185.295944 booked 185.30. The v3.10 oracle missed it: its figures had at most 8 significant digits and its lowest precision was 9. | Pin every public entry point of ExecutionPipeline, RunEngine and LiveSession, strategies dispatched from them included; hold the set with a structural test and a long-digit oracle at several precisions. | v3.11.0 | tests/regression/test_ambient_decimal_context.py | implemented (v3.11.0) | FIX |
| NUM-013 | Lot arithmetic ran in the caller's decimal context: round_down_to_lot divided before flooring, so it could round a quantity UP -- 12345.9 at precision 5 returned 12346, and 0.(31 nines) at precision 28 returned 1. | medium | conventions | Probe against the v3.10.0 tree (both values reproduced). | Compute lot counts, remainders and rounded quantities exactly (Inexact trapped) at the accounting precision; refuse what cannot be counted exactly. | v3.11.0 | tests/unit/conventions/test_conventions.py | implemented (v3.11.0) | FIX |
| ALC-006 | With enforce_integer_quantities the netted order was rounded to the nearest unit after the target delta, so a target could be overshot (297.6 shares toward a target became 298). | medium | allocation | Found by the rewritten example 08 in the v3.11 release gates. | Round a target's delta toward zero onto whole units before the lot. | v3.11.0 | tests/integration/test_target_intents.py | implemented (v3.11.0) | FIX |
| ALC-007 | The allocation budget pre-check counted every order's whole notional, a sale as much as a purchase: a fully invested book could not rotate -- a sale and a purchase in one batch committed twice the capital used, BudgetExceeded was raised and the whole batch, the sale included, was dropped. | high | allocation | Found by the rewritten example 09: five rotations produced no sale. Reproduced at the unit level (tests/unit/allocation/test_budget_commitment.py fails 3 of 6 on the v3.10 code). | Commit only the exposure an order adds to the account's committed position; send reductions before additions; pass committed positions for every run. | v3.11.0 | tests/unit/allocation/test_budget_commitment.py | implemented (v3.11.0) | FIX |
| BRK-009 | PaperBroker added a sale's commission to cash instead of charging it, and booked short positions (average, cover P&L, crossing through flat) wrongly. | high | broker | Found while implementing BRK-008. | One position_after_fill for the paper book; commissions always charged. | v3.11.0 | tests/unit/broker/test_paper_costs.py | implemented (v3.11.0) | FIX |
| INS-001 | Aliases registered after an instrument registry was constructed (register_alias) were lost on a snapshot round trip. | medium | instrument | Found while implementing DAT-004. | Persist later aliases (and dated aliases) in the instrument snapshot. | v3.11.0 | tests/regression/test_dated_aliases.py | implemented (v3.11.0) | FIX |
| LIV-001 | LiveRunState.unrouted_orders ignored child-order bindings, so LiveSession.advance could route a parent order whole while it was also worked in children -- and a new parent was routed at the end of the step that created it, before any child could be bound. | high | runtime/live | Found while implementing BRK-003 (a v3.9 composition defect). | Count bound children; let a strategy hold an order from direct routing, persisted. | v3.11.0 | tests/integration/test_live_requests_and_children.py | implemented (v3.11.0) | FIX |
| OMS-001 | OMS snapshot decoding discarded the upgraded payload and decoded the original. | low | oms | Found while adding OMS schema 2. | Decode the upgraded payload. | v3.11.0 | tests/regression/test_schema_upgrades.py | implemented (v3.11.0) | FIX |
| EXE-010 | Simulation does not read exchange calendars: a simulated DAY order must state its expire_at (MarketCalendar.next_close) because the pipeline holds no calendar. | medium | runtime | Design decision recorded while implementing EXE-003. | Give simulation a declared calendar per venue so DAY orders expire at the session close without the caller computing it. | v3.12.0 | tests/integration/test_day_order_sessions.py; tests/unit/scheduler/test_session_timers.py | implemented (v3.12.0) | IMPLEMENT |
| EXM-001 | Example 10 printed check marks for steps it never ran; examples 02, 08 and 09 demonstrated packages the library no longer holds. | medium | examples | Release-gate reading of every example. | Rewrite the four against the canonical path, printing only computed values. | v3.11.0 | examples gate (65/65) | implemented (v3.11.0) | FIX |
| TST-009 | No test exercised a batch that sells one holding and buys another from a full book; ALC-007 survived because the only end-to-end rotation was in an example that did not exist until v3.11. | medium | allocation | The full suite passed unchanged before and after the ALC-007 fix. | Unit tests of what an order commits and of batch ordering. | v3.11.0 | tests/unit/allocation/test_budget_commitment.py | implemented (v3.11.0) | FIX |
| DOC-003 | Current-state documents described packages v3.10 and v3.11 removed. The README's package tree listed feed/ and live/ (removed in v3.10) and counted 50 packages (48 at v3.10.0, 45 at v3.11.0); EVENT_MODEL named alphalab.live; the README's connectivity note, ARCHITECTURE's live table, broker stage and adapter section, docs/README and EXAMPLES presented RestVenueBroker and HttpVenueTransport as shipped; the lifecycle descriptions named studio's definitions and enterprise's RBAC; STATE_MODEL, EVENT_MODEL, GETTING_STARTED and SYSTEM_DESIGN listed studio and workbench; nowandfuture gave StrategyDefinition's home as studio and Governance's first argument as enterprise; docs/README and ARCHITECTURE gave portfolio_optimizer's edge set without conventions; and the README had lost its v3.10.0 section when v3.11.0's was written. | medium | docs | v3.11 release audit: every current-state document searched for each removed package, module and class name; the README package tree compared with the package directories by script (tree and count wrong in the v3.10.0 README, verified against c93e917). | Correct every current-state passage; keep release history as history. | v3.11.0 | release audit: scripted README package-tree check (45 of 45, none extra); removed-name search of the current-state documents | implemented (v3.11.0) | FIX |
| PRF-007 | A slice of an AppendOnlyLog copied the whole view before slicing it. The execution pipeline slices the execution history (history[before:]) and the portfolio's events (events[before:]) on every fill, and the trade records per step, so each fill cost the length of the run so far and a run was quadratic in its length. | high | common | Found profiling the v3.11 release benchmarks. With the garbage collector paused, the pipeline benchmark's cost per event was 754, 952 and 1,136 microseconds at 2k, 8k and 16k events; with the fix 664, 678 and 651 (16k events: 18.2 s -> 10.4 s). The benchmarks' comments attributed everything above linear to the collector. | Copy only the requested entries: bound the slice by the view's length and slice the buffer directly. | v3.11.0 | tests/regression/test_log_slicing_complexity.py | implemented (v3.11.0) | FIX |
| PRF-008 | A persistent map's chain held a (version, value) pair per write, and a rebase (PRF-002, v3.10) copied every live key into a new list and a new pair. A rebase of a large map promoted that copy into the collector's oldest generation at once and triggered full collections: the OMS benchmark's accept-and-fill stage spent 2.7 s of 5.6 s in the cyclic collector (1,592 / 145 / 3 collections of generations 0 / 1 / 2, against v3.9's 594 / 54 / 1), while with the collector paused it matched v3.9 (2.56 s against 2.43 s). | medium | common | Found when the v3.11 release measurement put the OMS benchmark at 1.12x v3.9 (median of five interleaved rounds), over the 1.1x budget PRF-006 states; measured with gc.callbacks. | Keep a chain as one flat list, [version, value, version, value, ...]: a write appends in place and a rebase allocates one list per live key. | v3.11.0 | tests/regression/test_persistent_map_gc_pressure.py | implemented (v3.11.0) | FIX |
| TST-010 | The institutional benchmark judged its scaling ceilings on one wall-clock sample of each size with the collector running -- the defect class TST-001 removed from the test suite in v3.10 and never from the benchmarks. Its scenario guard failed the v3.11 release gate at 6.05x against a 6.00x ceiling; the scenario code is unchanged since v3.10, and five runs of each tree read 3.8x-5.2x (v3.10) and 3.0x-5.1x (v3.11). | low | benchmarks | v3.11 benchmark gate; ten reruns side by side. | Judge the ceilings by the suite's stabilized method; keep the throughput lines as one run with the collector on. | v3.11.0 | benchmark_institutional passes 5 of 5 reruns; scenario 4.37x-4.78x | implemented (v3.11.0) | FIX |
| TST-011 | Five more benchmarks judge a scaling ceiling on one sample of each size -- four with the collector running, as their comments say they choose to. They passed the v3.11 gate, but the method is the one that failed in TST-010. | low | benchmarks | Reading every benchmark with a ceiling after TST-010. | Move each ceiling to the stabilized method, keeping the collector-on throughput as the reported figure. | v3.12.0 | tests/regression/test_benchmark_ceilings.py | implemented (v3.12.0) | FIX |
| TST-012 | No test pinned two of the resting limit order's fill rules: a buy limit resting against a bar that opens above it fills at its limit (or at an open below it), and a resting sell limit fills only once the bid reaches it. Mutating either -- a buy limit filling at the open above it, a sell limit crossing on a lower bid -- passed the whole suite in the v3.11 defect-injection run (W08, W09). | medium | runtime | v3.11 mutation harness: 79 mutations, 77 caught; W08 and W09 survived. | A test for each rule, failing on each mutant. | v3.11.0 | tests/integration/test_order_terms.py (test_a_bar_fills_a_resting_buy_limit_at_its_limit_or_at_a_better_open, test_a_resting_sell_limit_fills_only_once_the_bid_reaches_it) | implemented (v3.11.0) | FIX |
| PER-006 | The allocation snapshot decoder ignored the budget's currency, which every capture has written since v2.17: restore(capture(state)) != state for a budget in a named currency, and re-capturing a restored state wrote the currency as empty. | medium | allocation | Found while adding OFE-003's fields to the allocation snapshot; confirmed against a payload the v3.11.0 tag wrote (tests/fixtures/snapshots/v3.11.0/allocation_eur.json). | Decode the currency; upgrade a pre-v2.17 payload with none to an empty currency explicitly. | v3.12.0 | tests/integration/test_strategy_ceilings.py; tests/regression/test_schema_upgrades_v3_11.py | implemented (v3.12.0) | FIX |
| DAT-009 | Cleaning judged every quote internally consistent while validation reported a non-positive bid or ask, or a negative size, as an error: InvalidRecordPolicy.DROP kept the quote REFUSE refused, and the quality report counted it valid. | medium | data | Found while extending the predicate to trade prints (FEA-004). | Judge a quote by the rules validation applies (a crossed quote stays a warning). | v3.12.0 | tests/integration/test_trade_prints.py | implemented (v3.12.0) | FIX |
| PRF-009 | Every order judged against a classification limit summed its bucket's members: a rebalance of N names under sector limits cost N^2 over the number of sectors. At 10,000 assets the per-record cost was 3.34x that at 400 (1,225 against 366 us), where the same run without limits grew 1.23x. | medium | runtime/portfolio | The v3.12 stress run (docs/audit/scripts/stress_v3_12.py assets). | Keep each limited bucket's gross with the book, exactly, on every change; read it and add what working orders commit; sum the members only where the two could differ. | v3.12.0 | tests/integration/test_bucket_gross_kept_by_the_book.py; tests/unit/portfolio/test_book_groups.py; tests/regression/test_sector_classification_reaches_attribution.py | implemented (v3.12.0) | FIX |
| PRF-010 | Every event asked every registered strategy whether it subscribed: each strategy cost about 0.34 us on every record whatever its subscriptions. Ten strategies trading one asset took 665 us a record alone, 1,012 beside 1,000 strategies trading other assets and 4,097 beside 10,000. | medium | strategy | The v3.12 stress run's 1,000-strategy scenario, then a probe holding the reached strategies fixed and varying the others. | Route from an index of the subscriptions, in registration order, kept with the runtime state. | v3.12.0 | tests/unit/strategy/test_routing_index.py; tests/regression/test_strategy_routing_complexity.py | implemented (v3.12.0) | FIX |
| ANA-006 | A report's JSON wrote a Decimal as a binary float and any value the encoder did not know as str(value), which can carry a memory address: exact money left the library approximately, and an export was not deterministic. | low | reporting | Found while consolidating the reporting package (SCF-003). | Write a Decimal as its exact text; refuse an unknown value. | v3.12.0 | tests/unit/reporting/test_reporting.py | implemented (v3.12.0) | FIX |
| TST-013 | Five v3.12 rules had no test that pinned them: valuing a run in a currency other than its own (API-004), a reduction that leaves its bucket still over a classification limit (OFE-001's reduce-only rule; the one reduction tested landed exactly on the cap), an evidence value refused before any byte is written (OFE-016), and the research policy's walk-forward windows and ruin bound reaching their reports (RES-001; the only test policy used the values the mutants hard-coded). Mutating each passed the whole suite. | medium | tests | v3.12 defect-injection harness (docs/audit/scripts/mutation_v3_12.py): 123 mutations, 117 caught; X06, X13, X19, X39 and X40 survived, and X17 survived as an equivalent mutant. | A test for each rule, failing on each mutant. | v3.12.0 | tests/regression/test_backtest_result_valuation.py::test_valuation_in_values_in_the_currency_it_is_asked_for; tests/integration/test_classification_limits.py::test_a_reduction_that_leaves_its_bucket_over_the_limit_still_passes; tests/regression/test_evidence_store.py::test_a_refused_value_leaves_no_bytes_behind; tests/unit/research/test_research.py::test_the_policy_reaches_every_report_it_bounds | implemented (v3.12.0) | FIX |
| DOC-004 | Current-state documents still described what v3.11 had removed: docs/EXAMPLES.md had sections for the Strategy Studio and the Workbench, and nowandfuture.md counted 48 packages (45 at v3.11.0). The v3.12 documentation pass also caught its own omission: the research package's description in nowandfuture.md and docs/ARCHITECTURE.md still named the ResearchScore and compute_overall_score that RES-001 removed. After the v3.12 removals the same search found docs/ARCHITECTURE.md (its Plugins and Plugin Architecture sections, a list of registries and a layer diagram) and docs/SYSTEM_DESIGN.md (its audience and extension model) describing plugins as current, and the architecture document stating the optimizer's queue as a term still left super-linear. | low | docs | v3.12 documentation pass: every removed package and every removed v3.12 name searched for across the current-state documents. | Correct each passage; search every current-state document for every removed name at each release that removes one. | v3.12.0 | n/a (documentation) | implemented (v3.12.0) | FIX |
