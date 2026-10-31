# AlphaLab Strategy Capability Matrix (v4.1.1 audit, v4.2.0 candidate)

**Baseline:** `v4.1.0`, commit `33e61d3edbd041bcd125d5dea913c00cdc9da613` (tag
and `main` identical at the start of this work; working tree clean, no stash).
**Working branch:** `work/v4.1.1-future-readiness`, uncommitted. The audit's
findings were then implemented on the same branch (the continuation, then the
FUT-009 and FUT-013 milestones); every row below is as of the FUT-013 milestone, and where a
row changed, what it said at the audit is kept in a phrase ("was ...").
**Companions:** [`STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md`](STRATEGY_RESEARCH_TO_PRODUCTION_GAPS.md)
(the strategy families), [`FUTURE_READINESS_V4_1_1.md`](FUTURE_READINESS_V4_1_1.md)
(what was done, measured and left), [`V4_HISTORICAL_INVENTORY.md`](V4_HISTORICAL_INVENTORY.md)
(every v2.0-v3.13 deferral, classified at v4.0).

Every status below was read from source, a test or an executed example in this
session. Names and documents alone were not taken as evidence: where a document
and the code disagreed, the code decided and the disagreement is listed as a
defect.

## How this was established

* **Import graph.** Built from the AST of every module under `alphalab/` (not
  grep): for each top-level package, the packages it imports and the packages
  that import it, plus how many test and example files import it. The script is
  reproduced in [`FUTURE_READINESS_V4_1_1.md`](FUTURE_READINESS_V4_1_1.md),
  section 3. Results are in the table in section 1.
* **Runtime paths.** Read from the drivers (`backtesting/engine.py`,
  `backtesting/replay.py`, `runtime/session.py`, `runtime/live.py`), the run
  (`runtime/run.py`) and the step (`runtime/execution_pipeline.py`), and
  confirmed by running a researcher-defined strategy through them
  (`examples/72_compose_a_multi_asset_strategy.py`).
* **Evidence.** A test is cited by file (and test name where one decides it).
  "Example gate" means CI runs the example under `python -W error` on every push
  but no test asserts its output.

## Status vocabulary

| Status | Meaning here |
| --- | --- |
| **VERIFIED** | IMPLEMENTED AND VERIFIED: implemented and exercised end to end through the path named, by a test that asserts the outcome. |
| **NOT E2E** | IMPLEMENTED BUT NOT END-TO-END VERIFIED: implemented and unit-tested; no test drives it through the path it is meant to serve. |
| **PARTIAL** | PARTIALLY IMPLEMENTED: part of the capability exists; what is missing is named. |
| **ISOLATED** | ISOLATED OR NOT CONNECTED TO A REQUIRED PATH: a working library no driver or facade reaches; a researcher can call it from strategy code. |
| **MISSING** | Not present. |
| **EXTERNAL** | EXTERNAL INTEGRATION REQUIRED: the contract is AlphaLab's, the provider, venue or data is the host's. |
| **BOUNDARY** | INTENTIONALLY UNSUPPORTED WITH A JUSTIFIED BOUNDARY: kept out on purpose, the reason recorded (ledger or ADR named). |
| **DEFECT** | DEFECT OR DOCUMENTATION CLAIM MISMATCH: the code or a document says something false. Each one found here is fixed in this candidate unless stated. |

"v4.1.1" against a status means this candidate changed it; the change and its
test are in [`FUTURE_READINESS_V4_1_1.md`](FUTURE_READINESS_V4_1_1.md).

---

## 1. Packages: what imports what, and which paths reach them

43 top-level packages and the `alphalab.api` module (44 entries). `in` counts the
other packages that import it; `tests` / `int` / `ex` count test files, of which
integration-or-regression, and example files that import it. "Spine" is the
execution path ADR-0009 and ADR-0030 name; "lifecycle" is the governance path
joined to it by `lifecycle.execution`.

| Package | Lines | in | Imported by | tests / int / ex | Reached by |
| --- | ---: | ---: | --- | --- | --- |
| `allocation` | 3,378 | 2 | reinforcement_learning, runtime | 48 / 40 / 17 | spine |
| `alt_data` | 4,378 | 5 | api, backtesting, factor_library, research, runtime | 14 / 5 / 4 | spine (observations, since v3.12), research, api |
| `analytics` | 6,633 | 5 | api, backtesting, factor_library, portfolio_optimizer, runtime | 33 / 14 / 8 | spine (finalize), research |
| `api` | 1,656+ | 0 | -- (the facade) | 21 / 14 / 29 | the application surface |
| `backtesting` | 1,653+ | 2 | api, lifecycle | 52 / 45 / 16 | spine driver |
| `broker` | 4,318 | 3 | brokers, lifecycle, runtime | 46 / 30 / 10 | live path |
| `brokers` | 952 | 0 | -- | 6 / 5 / 1 | standalone (many-venue registry; ADR-0012) |
| `cloud_research` | 436 | 0 | -- | 2 / 1 / 0 | standalone |
| `cluster_scheduler` | 265 | 0 | -- | 1 / 0 / 0 | standalone |
| `common` | 4,211 | 44 | every package | 134 / 82 / 19 | the leaf |
| `conventions` | 1,346 | 9 | allocation, api, futures, instrument, lifecycle, macro, portfolio, portfolio_optimizer, runtime | 13 / 6 / 8 | spine (instrument economics) |
| `core` | 2,252 | 14 | 14 packages | 111 / 61 / 29 | spine (domain models) |
| `crypto` | 1,084 | 0 | -- | 5 / 3 / 1 | standalone |
| `data` | 6,267 | 8 | api, factor_library, lifecycle, market, marketdata, risk, runtime, scheduler | 65 / 37 / 27 | ingestion -> api -> spine |
| `deep_learning` | 1,568 | 1 | reinforcement_learning | 4 / 0 / 0 | standalone |
| `deployment_manager` | 544 | 1 | lifecycle | 6 / 4 / 0 | lifecycle |
| `distributed` | 1,005 | 2 | cloud_research, cluster_scheduler | 5 / 2 / 0 | standalone |
| `execution` | 6,834 | 4 | backtesting, broker, lifecycle, runtime | 87 / 68 / 24 | spine |
| `experiment_tracking` | 437 | 2 | lifecycle, research_assistant | 12 / 6 / 1 | lifecycle |
| `factor_library` | 5,309 | 2 | api, research | 30 / 14 / 15 | research (study path) |
| `feature_store` | 982 | 1 | ml | 7 / 2 / 1 | standalone |
| `futures` | 1,344 | 1 | api | 7 / 4 / 2 | api (contract lift) only |
| `instrument` | 2,436 | 4 | api, market, risk, runtime | 37 / 31 / 14 | spine (identity, economics) |
| `lifecycle` | 14,316 | 0 | -- | 62 / 38 / 22 | lifecycle path |
| `macro` | 953 | 0 | -- | 6 / 3 / 1 | standalone |
| `market` | 2,287 | 6 | api, backtesting, factor_library, futures, reinforcement_learning, runtime | 81 / 66 / 20 | spine |
| `marketdata` | 750 | 1 | market | 10 / 10 / 1 | spine (transports for streaming) |
| `ml` | 770 | 2 | cloud_research, deep_learning | 3 / 1 / 0 | standalone |
| `model_registry` | 1,721 | 1 | lifecycle | 23 / 16 / 3 | lifecycle |
| `oms` | 1,738 | 3 | backtesting, lifecycle, runtime | 41 / 35 / 2 | spine |
| `options` | 2,733 | 2 | api, data | 11 / 4 / 3 | api (contract lift); pricing standalone |
| `persistence` | 1,408 | 9 | 9 packages | 87 / 74 / 5 | spine (codec, run store) |
| `portfolio` | 6,914 | 7 | backtesting, crypto, futures, lifecycle, options, reinforcement_learning, runtime | 73 / 52 / 25 | spine |
| `portfolio_optimizer` | 7,185 | 0 | -- | 16 / 6 / 4 | standalone (called by strategy code) |
| `reinforcement_learning` | 767 | 0 | -- | 3 / 2 / 0 | standalone (drives the real pipeline) |
| `replay` | 592 | 1 | backtesting | 5 / 3 / 1 | spine (the replay cursor) |
| `reporting` | 552 | 0 | -- | 2 / 1 / 0 | standalone |
| `research` | 6,819 | 4 | api, cloud_research, lifecycle, research_assistant | 28 / 13 / 11 | research, lifecycle |
| `research_assistant` | 572 | 0 | -- | 3 / 2 / 1 | standalone |
| `risk` | 1,577 | 3 | lifecycle, reinforcement_learning, runtime | 25 / 17 / 19 | spine (pre-trade gate) |
| `runtime` | 12,190+ | 4 | api, backtesting, lifecycle, reinforcement_learning | 107 / 97 / 19 | spine |
| `scenario` | 1,218 | 0 | -- | 4 / 3 / 2 | standalone by design (imports only `common`) |
| `scheduler` | 1,077 | 0 | -- | 6 / 3 / 1 | standalone (timers reach the spine through `RunEngine.fire_timer`) |
| `strategy` | 4,830+ | 7 | allocation, api, backtesting, lifecycle, reinforcement_learning, research_assistant, runtime | 97 / 73 / 23 | spine |

Counts are v4.1.0's (a `+` marks a package this candidate extends). The graph is
acyclic at the package level (`tests/regression/test_import_graph_stays_acyclic.py`
measures it) and stays so after this candidate: `alphalab.backtesting` and
`alphalab.runtime` already imported `alphalab.alt_data`.

**Not "all 43 are integrated".** Twelve packages have no in-repository consumer
other than the facade (`brokers`, `cloud_research`, `cluster_scheduler`,
`crypto`, `lifecycle` -- which is a top-level composition --, `macro`,
`portfolio_optimizer`, `reinforcement_learning`, `reporting`,
`research_assistant`, `scenario`, `scheduler`), and four more are reached only
by another standalone package (`deep_learning`, `distributed`, `feature_store`,
`ml`). That is mostly deliberate (ADR-0009: standalone engines are libraries a
strategy or host calls), and where it leaves a capability unconnected that a
researcher would expect to be connected, the entry below says so.

---

## 2. Capabilities

Each entry answers the twelve questions in order: **(1)** historical intent and
release; **(2)** source; **(3)** canonical implementation and authoritative
types; **(4)** public API; **(5)** what it depends on; **(6)** what consumes it;
**(7)** which paths it is on (R research, B backtest, Re replay, P paper, L live);
**(8)** status; **(9)** evidence; **(10)** risks; **(11)** fixes and their
dependencies; **(12)** acceptance criteria for "ready".

### 2.1 The strategy contract

| | |
| --- | --- |
| (1) | v1.x strategy runtime design; context populated v2.10 (ADR-0026); `on_fill`/`on_order`/`on_timer` and `on_start` delivered v3.11 (EXE-005, OFE-014); `on_slice` v3.11 (EXE-004); `on_observation` v3.12 (OFE-009); class registry v2.17 (ADR-0035). |
| (2) | `alphalab/strategy/protocol.py`, `context.py`, `dispatcher.py`, `events.py`, `registry.py`, `state.py`, `supervisor.py`; context views in `alphalab/runtime/context_views.py`. |
| (3) | `StrategyProtocol` (ten hooks) + `SliceStrategyProtocol` + `ObservationStrategyProtocol` + `StrategyStateProtocol`; `BaseStrategy`; `StrategyContext` (portfolio, market, history bounded at the event, universe, risk view, orders, clock, logger, config); `Intent` with `IntentKind.DELTA` / `TARGET_QUANTITY` / `TARGET_WEIGHT` and `OrderTerms`. |
| (4) | `alphalab.strategy` (103 names): `BaseStrategy`, `Intent`, `IntentKind`, `StrategyContext`, `start_strategy`, `context_factory`, `StrategyClassRegistry`, `StrategyDefinition`, `runtime_for`. |
| (5) | `alphalab.common` only (pinned by `tests/regression/test_v37_invariants.py`). |
| (6) | runtime, allocation, backtesting, api, lifecycle, reinforcement_learning, research_assistant. |
| (7) | R (adaptive research replay), B, Re, P, L -- one dispatcher on every path. |
| (8) | **VERIFIED**, with one **DEFECT fixed in v4.1.1**: `context.market.bar(asset_id)` returned `None` for every asset since v2.10 -- `MarketView.bar` looked up the bare asset id in an index keyed `"<asset_id>_<interval>"`, and no test read a bar through the context. |
| (9) | `tests/integration/test_strategy_building_end_to_end.py` (example 70); `test_slices.py`; `test_external_information.py`; `test_strategy_feedback.py`; `tests/regression/test_strategy_context_visibility.py`, `test_strategy_context_history_and_universe.py`; v4.1.1: `tests/regression/test_strategy_context_reads_bars.py` (fails on v4.1.0's lookup), `tests/integration/test_multi_asset_composition.py`. |
| (10) | No finite list of strategy types exists anywhere: the registry maps an identity to *any* factory returning a `StrategyProtocol` (`registry.py`), which is what makes the universe open. Hooks run in-process with the caller's code: hosting untrusted strategies is the host's isolation problem (BOUNDARY, `docs/INTEGRATION.md`). `MarketView.bar` is linear in the latest-bar index (same order as `prices`). |
| (11) | `MarketView.bar` fixed (v4.1.1). A strategy's own working orders could not be withdrawn in a historical run, so re-targeting traded against them: **MISSING** until v4.2.0, now **VERIFIED** -- `IntentKind.CANCEL` withdraws what the strategy alone has working, and a cancel with a new limit replaces a quote (EXE-012, `test_strategy_cancels_orders.py`). |
| (12) | A researcher's class, unknown to AlphaLab, trades several instruments from several input kinds through the public API and is reproduced -- met by example 72 and its test. |

### 2.2 Strategy state, capture and restore

| | |
| --- | --- |
| (1) | ADR-0025 (v2.10); run snapshot envelope ADR-0023; one `RunState` ADR-0030. |
| (2) | `alphalab/strategy/protocol.py:StrategyStateProtocol`, `alphalab/runtime/snapshot.py`, `run_snapshot.py`. |
| (3) | `capture_state` / `restore_state` / `strategy_state_version`, carried in the pipeline snapshot; `RunObjects` supplies live objects back, checked against the recorded configuration (PER-008). |
| (4) | `alphalab.runtime.run_snapshot.capture`, `from_primitives`, `restore`; `BacktestEngine.resume`. |
| (5) | persistence codec. |
| (6) | every driver; FileRunStateStore. |
| (7) | B, Re (pause/resume, v4.1), P, L. |
| (8) | **VERIFIED**. A strategy that declares its configuration's type reads it back typed (v4.2.0 candidate, FUT-002); an undeclared configuration's `Decimal`/tuple reads back as JSON reads it: **LIMITATION** LIM-004. |
| (9) | `tests/regression/test_durable_run_continuation.py`, `test_replay_pause_and_resume.py` (135), `test_restore_requires_the_captured_configuration.py`; v4.1.1: example 72 stopped at **every** record boundary, stored, restored with a registry-built instance and continued to the identical `result_id` (`test_multi_asset_composition.py::test_a_backtest_stored_at_any_record_and_continued_is_the_same_result`). |
| (10) | A strategy that does not declare its state continues only conditionally (documented in ADR-0025). |
| (11) | none. |
| (12) | met. |

### 2.3 Drivers: backtest, replay, paper, live

| | |
| --- | --- |
| (1) | Backtest v2.2 (ADR-0010); replay through the pipeline v2.2, resumable v4.1 (FUT-001); paper/live drivers over one run v2.14 (ADR-0030); `LiveSession` v2.16 (ADR-0033). |
| (2) | `alphalab/backtesting/engine.py`, `replay.py`, `replay_snapshot.py`; `alphalab/runtime/session.py`, `live.py`, `run.py`. |
| (3) | `RunEngine` owns the run; drivers hold no state. |
| (4) | `BacktestEngine.run`, `ReplayBacktest.run/resume`, `TradingSession.run`, `LiveSession.*`, `alphalab.api.backtest/replay/resume_replay`. |
| (5) | runtime, market, replay cursor, broker (live). |
| (6) | api, lifecycle (certification, evidence). |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED** for market records and for every timed input. Was, at the audit: `ReplayBacktest.run` took no observations, the facade took none, `LiveSession` could not deliver one, a durable paused replay with deliveries outstanding was refused (FUT-003), and no historical driver took rates, funding, cash flows, timers or settlements (FUT-004). Now one `RunSchedule` (`alphalab.backtesting.schedule`) carries observations, rates, funding, cash flows, per-unit distributions, funding rates, splits, timers and contract settlements, applied by `BacktestEngine.run`, `ReplayBacktest.run` and the facade through one `ScheduleCursor`; a paused replay's payload (replay schema 2) records the schedule's digest and resumes durably; `LiveSession.deliver_observation` delivers and routes. Inputs dated after the data are not applied. `TradingSession.run` (paper over a source) takes no schedule -- a host applies inputs with the `RunEngine` calls between records. |
| (9) | `tests/integration/test_backtest_replay_parity.py`, `test_paper_and_live_sessions.py`, `test_live_session.py`, `test_streaming_session.py`; v4.1.1: `tests/regression/test_heterogeneous_inputs_on_every_driver.py` (82, a payload round trip at every boundary among them), `tests/integration/test_scheduled_inputs.py` (34). |
| (10) | A schedule is fixed before the run; a host whose inputs arrive during a run uses `LiveSession` or the `RunEngine` calls. |
| (11) | Done (OFE-027, FUT-003, FUT-004). |
| (12) | Every driver delivers the same information at the same step; a paused replay with information outstanding round-trips durably. |

### 2.4 Allocation, multi-strategy netting and strategy attribution

| | |
| --- | --- |
| (1) | ADR-0015 (v2.6); target intents v3.11 (FEA-001); strategy ceilings v3.12 (OFE-003); capital allocation v3.8 (ADR-0043). |
| (2) | `alphalab/allocation/` (engine, budget, sizing, capital, snapshot). |
| (3) | `AllocationEngine`, `CapitalBudget`, sizing models, reservation and contribution ledgers. |
| (4) | `alphalab.allocation` (60 names). |
| (5) | common, core, conventions, strategy, persistence. |
| (6) | runtime, reinforcement_learning. |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED**. |
| (9) | `tests/integration/test_target_intents.py`, `test_strategy_ceilings.py`, `test_v39_capabilities.py::test_two_strategies_net_into_one_parent_that_keeps_both`, `test_v38_capabilities.py::test_strategy_pnl_attribution_is_each_run_s_own_and_sums_to_the_book`. |
| (10) | Allocation visibility in the strategy context is withheld on purpose (BDY-002). |
| (11) | none. |
| (12) | met. |

### 2.5 Pre-trade risk

| | |
| --- | --- |
| (1) | v1 risk engine; defects KD-001-003 fixed v3.10; classification-bucket limits v3.12 (OFE-001). |
| (2) | `alphalab/risk/`. |
| (3) | `RiskEngine`, `RiskLimits` (order size, position, exposure, leverage, margin, daily loss, drawdown), `RiskDecision` with `violations`; since v4.2.0 (FUT-017) `MarginCall`, and the pipeline's margin checks: an order growing a margined position held to the initial requirement, none while in breach, and a margined future charged its initial margin of buying power. |
| (4) | `alphalab.risk` (53 names). |
| (5) | common, core, data, instrument. |
| (6) | runtime, lifecycle, reinforcement_learning. |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED**. Risk *measurement* of a held book is `alphalab.analytics`, not this (pinned distinct). |
| (9) | `tests/integration/test_classification_limits.py`; `test_backtest_pipeline.py::test_a_request_risk_refuses_never_reaches_the_oms`; v4.1.1: example 72's order-size refusals, each in `result.state.risk.history` with its violation and never filled. |
| (10) | The position check adds notional exposure to order quantity (recorded in ADR-0041; example comments warn). |
| (11) | none. |
| (12) | met. |

### 2.6 OMS and order lifecycle

| | |
| --- | --- |
| (1) | v1 OMS; persistent maps v2.x; resting orders and time in force v3.11 (EXE-003). |
| (2) | `alphalab/oms/`. |
| (3) | `OMSEngine`, `Order`, `OrderStatus`, `OrderBook`, snapshot. |
| (4) | `alphalab.oms`. |
| (5) | common, core, persistence. |
| (6) | runtime, backtesting, lifecycle. |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED**. Since v4.2.0 a strategy withdraws its own working orders (`IntentKind.CANCEL`, EXE-012); a bracket, a one-cancels-other pair and a trailing stop are composed from it over `on_fill`, `on_order` and `on_quote`. |
| (9) | `tests/integration/test_order_terms.py`, `test_day_order_sessions.py`, `test_strategy_cancels_orders.py`, `tests/regression/test_v39_*`. |
| (10) | Per-order memory grows with orders (LIMITATION PRF-011). Amending a working order in place, and cancelling part of a netted order, are not modelled; a venue's price limits and halts are FUT-020. |
| (11) | none. |
| (12) | met. |

### 2.7 Execution simulation, costs, liquidity and capacity

| | |
| --- | --- |
| (1) | v1 simulator; v3.3 cost model, capacity, liquidity-capped fills (ADR-0038); fill timing v3.10 (EXE-002). |
| (2) | `alphalab/execution/simulator.py`, `costs.py`, `policy.py`, `capacity.py`, `slippage.py`, `commission.py`. |
| (3) | `ExecutionSimulator`, `ExecutionCostModel` (spread, slippage, impact, commission, fee, tax), `FillTiming`, `LiquidityCappedFill`, `ExecutionAssumptions` (each optimistic default named on the result). |
| (4) | `alphalab.execution` (141 names). |
| (5) | common, core. |
| (6) | runtime, broker, backtesting, lifecycle. |
| (7) | B, Re, P (simulated venue). |
| (8) | **VERIFIED**. A declared queue model decides passive fills on both historical drivers (v4.2.0 candidate, FUT-011): **VERIFIED** for price-time priority over displayed liquidity (`PriceTimeQueue`); a fill-probability model: not shipped (section 2.18). |
| (9) | `tests/integration/test_v33_capabilities.py`, `tests/regression/test_v33_invariants.py`; v4.1.1: example 72 runs with `NEXT_EVENT` timing, a 10% participation cap and a six-role cost model, and `execution_assumptions.optimistic == ()`. |
| (10) | Defaults are optimistic and are *stated*, not hidden (`result.execution_assumptions`). |
| (11) | none. |
| (12) | met for bar- and quote-driven simulation. |

### 2.8 Execution algorithms, routing and execution quality

| | |
| --- | --- |
| (1) | v3.9 universal execution contract (ADR-0044). |
| (2) | `alphalab/execution/algorithms.py`, `routing.py`, `quality.py`; `alphalab/runtime/live.py` (`hold`, `route_child`); since the FUT-009 milestone `alphalab/backtesting/algorithmic.py` and `ExecutionPipeline.work_child`. |
| (3) | TWAP / VWAP / participation / slicing / iceberg, `ChildOrder`, `AlgorithmState`, `RouteDecision`, implementation shortfall; on the historical path `ExecutionPlan`, `AlgorithmBook`, `ExecutionRouting.ALGORITHMIC`. |
| (4) | `alphalab.execution`; `LiveSession.hold/route_child/cancel/amend`; `BacktestEngine.run(..., execution=)`, `ReplayBacktest.run(..., execution=)`, `alphalab.api.backtest/replay(..., execution=)`. |
| (5) | common, core; the historical path also runtime (the pipeline's fill policy, simulator, OMS, portfolio and allocation). |
| (6) | runtime.live; backtesting (both historical drivers). |
| (7) | B, Re, L (and paper through `PaperBroker`). Paper over a source (`TradingSession`) routes to its broker, not to an algorithm book. |
| (8) | **VERIFIED** on the live path and, since the FUT-009 milestone, on both historical drivers (was **PARTIAL**: a backtest filled every order whole). Each parent is one OMS order, held under `ALGORITHMIC` routing -- risk judged it whole, allocation reserved it -- and worked in children at its asset's events; each child is filled by the run's own fill policy and cost model (`work_child`) and settled on the parent through the canonical report path, so the OMS, portfolio, reservation and attributed trade records see one order. TWAP, VWAP, slicing, iceberg and participation (`LEAVE_UNFILLED`) are evidenced; participation with `COMPLETE_AT_END` is refused by the plan. |
| (9) | `tests/integration/test_algorithms_on_the_historical_path.py` (57: quantities reconcile, nothing overfills, partial fills and catch-up, no liquidity, rejection, a venue declaration refusing a child, a split cancelling the parent, incomplete at the data's end, both fill timings, no look-ahead in price or volume, costs per fill, attribution of a netted parent, replay parity, a replay paused at every boundary resumed from its payload, the same children the live route sends a `PaperBroker`); `tests/integration/test_execution_algorithm_example.py`; `examples/74_evaluate_an_execution_algorithm.py`; `test_v39_capabilities.py` for the live path. Eight mutants of the guards, each caught. |
| (10) | Passive (limit) children fill by the simulator's resting rule: a queue model governs a run's own orders, not an execution plan's children, and is refused beside a plan (FUT-011). A child's fill reaches its strategy through the portfolio, not `on_fill`, as a venue fill does live. A `PaperBroker` fills a market child at the parent's reference price, so the backtest equals the paper route child for child, not price for price (ADR-0051 decision 13). |
| (11) | Done (FUT-009). |
| (12) | Met: an algorithm's children are evaluated by `BacktestEngine` and `ReplayBacktest` with the run's own fills, child for child the ones the live route sends. |

### 2.9 Portfolio accounting and instrument economics (multi-asset)

| | |
| --- | --- |
| (1) | v2.1 mark-to-market; ADR-0028 currency authority (v2.12); multi-currency settlement ADR-0035 (v2.17); instrument economics on the canonical path v3.11 (ACC-005/006/007). |
| (2) | `alphalab/portfolio/engine.py`, `position.py`, `contracts.py`, `corporate_actions.py`; `alphalab/instrument/`; `alphalab/conventions/economics.py`. |
| (3) | `PortfolioEngine` (one book of record), `InstrumentRecord` + `InstrumentEconomics` (multiplier, settlement model, lot, minimum notional, negative prices), `CashFlow`, `Split`. |
| (4) | `alphalab.portfolio`, `alphalab.instrument`, `alphalab.conventions`. |
| (5) | common, core, conventions, persistence. |
| (6) | runtime and everything on the spine. |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED** for cash equities, futures (variation margin), options (premium x multiplier), perpetuals (funding as a cash flow), splits, dividends, fees; a future or option with no declared economics is refused before an order exists. |
| (9) | `tests/integration/test_instrument_economics.py`; `test_mark_to_market_pipeline.py`; `tests/regression/test_close_fill_cash_accounting.py`; v4.1.1: example 72's index future held in whole contracts, notional never paid, accounting identity exact. |
| (10) | Borrow availability is not modelled -- a borrow *fee* is a stated `CashFlow` (BOUNDARY: the host's data). |
| (11) | none. |
| (12) | met. |

### 2.10 Instrument identity, calendars and conventions

| | |
| --- | --- |
| (1) | ADR-0016 (v2.7), classification ADR-0027 (v2.11), conventions and exchange calendars v3.4 (ADR-0039), calendars inside simulation v3.12 (EXE-010), dated aliases v3.11 (DAT-004). |
| (2) | `alphalab/instrument/`, `alphalab/data/calendar.py`, `alphalab/runtime/calendars.py`, `alphalab/conventions/`. |
| (3) | derived `asset_id`, `InstrumentRegistry`, `MarketCalendar`, `VenueCalendars`, `MarketConvention`, `SettlementRule`, `TickSchedule`, `LotSpecification`. |
| (4) | as named. |
| (5) | common, core, conventions. |
| (6) | market normalization, runtime, api. |
| (7) | B, Re, P, L. |
| (8) | **VERIFIED**. AlphaLab ships no holiday or reference data: **EXTERNAL** (BDY-013). |
| (9) | `tests/integration/test_day_order_sessions.py`, `tests/regression/test_instrument_identity_reaches_a_fill.py`; example 72 declares two venues' instants; since FUT-013 `tests/integration/test_lifecycle_conventions.py` -- four synthetic profiles (a zone without daylight saving, two that change on different dates, a Sunday-to-Thursday market with a lunch break and a holiday) through `session_close` and `settles_at` and the same option lifecycle, no market named in the engine. |
| (10) | Holidays are the caller's declaration; a missing one is an extra session. A lifecycle date the market does not trade on is refused rather than moved: which way it moves is the contract's convention. Since v4.2.0 a declared settlement lag is booked on each fill and gates buying power (FUT-015, `test_settlement_date_accounting.py`). |
| (11) | none (FUT-015 delivered). |
| (12) | met. |

### 2.11 FX rates, conversion and multi-currency runs

| | |
| --- | --- |
| (1) | ADR-0019/0020 (v2.8), conversion with stated rates ADR-0033 (v2.16), the feed boundary ADR-0035 (v2.17), look-ahead refusal v3.4. |
| (2) | `alphalab/portfolio/fx.py`, `fx_feed.py`, `fx_research.py`; `ExecutionPipeline.fund/convert_cash`. |
| (3) | `FxRate(as_of, source)`, `FxRates` (one rate per pair; `FutureDatedRateError`, `StaleRateError`), `FxFeed`. |
| (4) | `alphalab.portfolio`, `alphalab.portfolio.fx_feed`. |
| (5) | common. |
| (6) | runtime (every step takes `rates`). |
| (7) | B, Re (rates and `Funding` on the run's schedule), P, L (stepped by the host). |
| (8) | **VERIFIED** (was **PARTIAL**: step-level only, FUT-004). A book settling two currencies is funded and traded through `BacktestEngine.run` and `ReplayBacktest.run`, each step valued at the rate in force at it; funding a currency with no rate yet quoted is refused. No FX data is shipped (EXTERNAL). |
| (9) | `tests/integration/test_scheduled_inputs.py::test_a_two_currency_book_is_funded_and_traded_through_the_driver`, `::test_each_step_is_valued_at_the_rate_in_force_at_it_and_never_a_later_one`, `::test_the_driver_equals_a_loop_that_states_every_rate_by_hand`; `test_v217_capabilities.py`; `examples/14_multi_currency_settlement.py`. |
| (10) | Rates are the host's; a stale rate is refused only when `rate_max_age_seconds` is declared on the schedule. |
| (11) | Done (FUT-004). |
| (12) | A backtest of a book settling two currencies, driven by `BacktestEngine.run`, applying each rate at its `as_of`, equal to the hand loop. |

### 2.12 Data ingestion, validation, provenance and dataset identity

| | |
| --- | --- |
| (1) | Universal data engine v3.1 (ADR-0036); rows identified by content v3.10 (KD-004). |
| (2) | `alphalab/data/` (source, csv, schema, time, validation, cleaning, quality, provenance, dataset, ingestion). |
| (3) | `Dataset` with `DatasetProvenance` (source bytes digest, schema, policy, transformations -> derived `dataset_version`). |
| (4) | `alphalab.api.ingest_csv`, `ingest_rows`, `inspect_csv`, `validate_dataset`, `clean_dataset`, `select`, `to_market_dataset`. |
| (5) | common, options (one enum). |
| (6) | api, factor_library, lifecycle, market, risk, runtime. |
| (7) | R, B, Re (P/L read live sources instead). |
| (8) | **VERIFIED**. One dataset is one series kind (`IngestionRequest.asset_class`) from one source. |
| (9) | `tests/integration/test_v32_capabilities.py`, `tests/unit/test_api.py`, `examples/15_data_ingestion.py`. |
| (10) | -- |
| (11) | none. |
| (12) | met. |

### 2.13 Several datasets in one run (multiple providers, multiple asset classes)

| | |
| --- | --- |
| (1) | Not addressed before v4.1.1. |
| (2) | v4.1.1: `alphalab/api.py` (`CombinedDataset`, `CombinedProvenance`, `combine_datasets`, `combined_market_dataset`). |
| (3) | `CombinedDataset` satisfies the manifest's `VersionedDataset` protocol; identity `combined-<sha256 of the parts' versions, in order>`. |
| (4) | `alphalab.api`. |
| (5) | data, market normalization, backtesting dataset. |
| (6) | any driver (a `MarketDataset`); `lifecycle.manifest_for_run`. |
| (7) | B, Re. |
| (8) | Was **MISSING**: a run could name one dataset, so a two-venue or two-provider run carried an id its author typed and no manifest could check it. **VERIFIED (v4.1.1)**. |
| (9) | `tests/unit/test_api_combined_datasets.py` (8); `tests/integration/test_multi_asset_composition.py::test_the_manifest_names_the_combined_datasets_and_checks_them`. |
| (10) | Records are not aligned or forward-filled (by design); two parts' prices for one instrument at one instant are refused. The manifest records each part (`ReproducibilityManifest.dataset_parts`), checked to derive the combined identity, and `external_requirements` lists them one by one, in order. |
| (11) | Done (FUT-005). |
| (12) | met. |

### 2.14 Point-in-time external information (alternative data, events, fundamentals)

| | |
| --- | --- |
| (1) | Typed v1 categories (PR-040); the v3.7 point-in-time foundation (ADR-0042); delivery on the execution path v3.12 (OFE-009); streams OFE-011. |
| (2) | `alphalab/alt_data/` (observation, information, fundamentals, observation_set, source, streaming, sessions, provenance; v1: sentiment, news, satellite, shipping, esg, credit_card); `alphalab/common/point_in_time.py`. |
| (3) | `PointInTimeStamp` (observed, effective, available, ingested; basis; rule), `VisibilityRule` (PUBLICATION, INGESTION), `ExternalObservation` (open category/metric vocabulary, `ReferencePeriod`, `revision`), `InformationEvent`, `FundamentalObservation`, versioned `ObservationSet`, `DeliverySchedule`. |
| (4) | `alphalab.api.ingest_observations`, `ingest_events`, `ingest_fundamentals`, `observation_source`, `AvailabilityFromColumn` / `AfterLag` / `AtNextOpen` / `NotDeclared`; `alphalab.alt_data.delivery_schedule`. |
| (5) | common only. |
| (6) | api, research, factor_library, backtesting, runtime. |
| (7) | R; B (v3.12); Re, api facade, L wrapper (v4.1.1). |
| (8) | **VERIFIED**. A row whose availability is not stated is `UNKNOWN`, counted and never delivered; an exact repeat or unreadable row is rejected by line; a revision published ahead of the figure it revises is refused. |
| (9) | `tests/integration/test_external_information.py`, `test_v37_capabilities.py`; `tests/unit/alt_data/`; v4.1.1: `test_multi_asset_composition.py` (late post, unknown availability, duplicate, unreadable, out-of-order file, and a leaking control). |
| (10) | The ingestion instant is never substituted for availability (a test proves a leaky schedule is caught). Licensing of any dataset is the host's (EXTERNAL). |
| (11) | none. |
| (12) | met. |

### 2.15 Social-media and news sentiment

| | |
| --- | --- |
| (1) | PR-040 typed `NewsSentiment`/aggregated sentiment; v3.7 generic observations. |
| (2) | `alphalab/alt_data/sentiment.py`, `news.py` (typed v1); `ExternalObservation(category="sentiment")`. |
| (3) | As 2.14. AlphaLab computes no sentiment from text: scores are inputs. |
| (4) | As 2.14. |
| (5)-(6) | As 2.14. |
| (7) | R, B, Re, L. |
| (8) | **VERIFIED** as an input stream (v4.1.1 acceptance). Text-to-score models and any social API are **EXTERNAL** / BOUNDARY (no vendor SDK or LLM dependency in the engine). The typed v1 records (`NewsSentiment`, ...) are read through `known_as_of` and are **ISOLATED** from the delivery path (they are not `SetRecord`s); the generic form is the one that reaches a strategy. |
| (9) | `tests/integration/test_multi_asset_composition.py`; `examples/51_alternative_data_provenance.py`. |
| (10) | Entity mapping from a post's ticker to an instrument is the caller's key (`subject`), by design (observations are not `asset_id`-keyed). |
| (11) | none required. |
| (12) | met. |

### 2.16 Macroeconomic data: releases, vintages, revisions

| | |
| --- | --- |
| (1) | PR-039 Macro Engine (indicators with point-in-time correctness, central-bank events, yield curves, GDP; v3.4 bond analytics). |
| (2) | `alphalab/macro/` (standalone); on the information path through `ExternalObservation` with `ReferencePeriod` and `revision`. |
| (3) | For a strategy: `ExternalObservation` -- the period measured (`ReferencePeriod`), the release instant (`available_at`) and the vintage (`revision`) are three fields, never one. `alphalab.macro.indicator` keeps its own point-in-time model for analysis. |
| (4) | `alphalab.api.ingest_observations` with `ObservationColumns(period=..., revision=...)`; `alphalab.macro`. |
| (5) | macro: common, conventions. |
| (6) | macro: `alphalab.api.observations_from_indicator`, `bond_schedule`. |
| (7) | Information path: R, B, Re, L; `alphalab.macro` series reach it through the lift. |
| (8) | Releases/vintages to a strategy: **VERIFIED (v4.1.1 acceptance)**. `alphalab.macro`: **VERIFIED** on the path (was **ISOLATED**, FUT-008) -- `observations_from_indicator` lifts its releases into an `ObservationSet`: observed at the reference period, known at the release date, the vintage numbered by release order; a series whose revision flags contradict it is refused. Yield curves and other derived analytics stay R libraries. |
| (9) | `tests/integration/test_multi_asset_composition.py::test_every_decision_used_the_cpi_vintage_published_by_then`; `tests/unit/test_api_macro_lift.py` (8, against `macro.known_as_of`); `tests/unit/macro/`. |
| (10) | The lift is one-way and per indicator. |
| (11) | Done (FUT-008). |
| (12) | A macro-engine series can be scheduled for delivery without re-ingesting it. |

### 2.17 Features, factors and signal research

| | |
| --- | --- |
| (1) | Feature Store PR-034; Factor Library PR-035; v3.2 study path (ADR-0037). |
| (2) | `alphalab/factor_library/`, `alphalab/feature_store/`, `alphalab/research/`. |
| (3) | `FeatureDefinition` -> `FeaturePanel`; style factors; IC, decay, turnover, neutralization, carry, value, quality, volatility, liquidity; `ResearchStudy`/`StudyResult`. |
| (4) | `alphalab.api.run_study`, `study_panels`, `observe`; `alphalab.factor_library`, `alphalab.research`. |
| (5) | common, data, alt_data, analytics, market. |
| (6) | api, research, lifecycle (study manifests). |
| (7) | R. Features reach a *trading* strategy only as code the strategy runs, or as values a study computed and the strategy reads -- there is no feature-serving component on the execution path. |
| (8) | Research: **VERIFIED**. Feature Store: **ISOLATED** (consumed only by `ml`). |
| (9) | `tests/integration/test_v32_capabilities.py`, `test_v37_capabilities.py::test_knowledge_frames_feed_the_feature_engine_with_lineage`; examples 17-24. |
| (10) | Was: nothing checked a study's features against a strategy's (FUT-006). Now a strategy evaluates the study's own `FeatureDefinition`s with `compute_feature` over the history it has been shown and equals the study panel at every instant (`examples/73_train_and_trade_a_model.py`; `tests/integration/test_model_on_the_execution_path.py::test_the_strategy_computes_the_studys_features_exactly`, 111 instants). There is no incremental evaluator: each evaluation recomputes its window (ADR-0051 known limitation). |
| (11) | Done as evidence (FUT-006). |
| (12) | The same `FeatureDefinition` gives the same value in a study panel and in a strategy at the same instant. |

### 2.18 Validation methodology

| | |
| --- | --- |
| (1) | v3.2 (ADR-0037): walk-forward, time-series CV with purging and embargo, robustness, overfitting diagnostics; multiple testing and Sharpe inference v3.12. |
| (2) | `alphalab/research/` (walk_forward, walk_forward_optimization, time_series_cv, purging, splits, overfitting, multiple_testing, sharpe_inference, bootstrap, montecarlo, sensitivity, perturbation, bias, regime, regimes, event_study, stress, capacity). |
| (3) | As named; deflated Sharpe, probabilistic Sharpe, Holm/Bonferroni, survivorship checks in `bias.py`. |
| (4) | `alphalab.research` (139 names). |
| (5) | common, analytics, alt_data. |
| (6) | api, lifecycle, research_assistant, cloud_research. |
| (7) | R. |
| (8) | **VERIFIED** on research payloads. |
| (9) | examples 20-23; `tests/unit/research/`; `tests/integration/test_v32_capabilities.py`. |
| (10) | Walk-forward over a *backtest* (re-running the execution path per fold) is user code; `walk_forward_optimization` evaluates a supplied function. |
| (11) | none required. |
| (12) | met. |

### 2.19 Machine learning, deep learning, reinforcement learning, adaptive strategies

| | |
| --- | --- |
| (1) | PR-041 ML, PR-042 DL (backprop verified; LSTM/attention gradients v3.12, SCF-004), PR-043 RL; adaptive strategies v3.7 (ADR-0042). |
| (2) | `alphalab/ml/`, `alphalab/deep_learning/`, `alphalab/reinforcement_learning/`, `alphalab/strategy/adaptive*.py`. |
| (3) | linear/logistic regression, k-fold and walk-forward splits; dense/conv1d/LSTM/attention; tabular Q-learning, REINFORCE, a trading environment over the real pipeline; `AdaptiveStrategy` with an immutable learned state and hash-chained lineage. |
| (4) | `alphalab.ml`, `alphalab.deep_learning`, `alphalab.reinforcement_learning`, `alphalab.strategy`. |
| (5) | ml: common, feature_store; dl: common, ml; rl: runtime, strategy, risk, allocation, portfolio, market, deep_learning. |
| (6) | ml: cloud_research, deep_learning; dl: rl; rl: nobody. |
| (7) | Adaptive: R, B, Re, P, L. ML: R, B (example 73). DL: R. RL: its own loop over the pipeline step. |
| (8) | Adaptive: **VERIFIED**. ML: **VERIFIED** (was **NOT E2E**, FUT-007) -- example 73 fits a linear model only on samples whose label was known before a boundary, declares it as the strategy's parameters, trades it after the boundary, restores it exactly from a stored run, and reproduces it under two hash seeds; a counterfactual on every price after the boundary changes neither the model nor an earlier decision. DL: **NOT E2E** -- a network is held the same way, but no test trades one. RL: **VERIFIED** for its environment; a **DEFECT fixed in v4.1.1**: its package and module docstrings still described a `PortfolioEngine.apply_fill` defect as unfixed, fixed in v2.0.0 (CHANGELOG D1) and asserted correct by its own test. |
| (9) | `tests/integration/test_model_on_the_execution_path.py` (12); `tests/integration/test_v37_capabilities.py::test_backtest_and_replay_agree_for_an_adaptive_strategy`; `tests/unit/ml/`, `tests/unit/deep_learning/`, `tests/unit/reinforcement_learning/test_reinforcement_learning.py::test_environment_round_trip_realizes_correct_pnl`. |
| (10) | Pure-Python linear algebra (no numpy, `dependencies = []`): models are small and slow by design; a GPU or large-model workflow is the host's (BOUNDARY OFE-026). |
| (11) | Done for ML (FUT-007). |
| (12) | Met for a linear model: fitted only on data before a boundary, it drives orders after it, its parameters captured and restored exactly, the run reproduced. |

### 2.20 Portfolio construction, risk budgets, multi-strategy books

| | |
| --- | --- |
| (1) | ADR-0005 v1 optimizers; v3.8 `construct` and Black-Litterman, risk budgets, multi-strategy books, capital allocation (ADR-0043). |
| (2) | `alphalab/portfolio_optimizer/`, `alphalab/analytics/risk_budget.py`, `cross_strategy.py`, `alphalab/portfolio/multi_strategy.py`, `alphalab/allocation/capital.py`. |
| (3) | `construct` (min variance, mean-variance, max diversification, risk parity, robust MV) with certified statuses; `black_litterman`. |
| (4) | `alphalab.portfolio_optimizer`, `alphalab.analytics`, `alphalab.portfolio`, `alphalab.allocation`. |
| (5) | analytics, conventions, common. |
| (6) | portfolio_optimizer: nobody (standalone). |
| (7) | R; reaches B/L when a strategy calls it and emits `TARGET_WEIGHT`/`TARGET_QUANTITY` intents. |
| (8) | **VERIFIED** as research and as a strategy-called library (`examples/10_complete_pipeline.py`, example gate; `test_v38_capabilities.py`). **ISOLATED** from the drivers by design. |
| (9) | `tests/integration/test_v38_capabilities.py` (risk parity across strategies, factor-neutral sector-capped rebalance, Black-Litterman, multi-account allocation). |
| (10) | Dense solver is cubic (LIMITATION PRF-005/013, measured). |
| (11) | none required. |
| (12) | met. |

### 2.21 Options, futures, crypto, fixed income

| | |
| --- | --- |
| (1) | PR-036/037/038 engines; v3.4 IV inversion, chains, rolls, curves, funding; American options v3.13. |
| (2) | `alphalab/options/`, `alphalab/futures/`, `alphalab/crypto/`, `alphalab/macro/bond.py`. |
| (3) | Black-Scholes, Greeks, IV, surfaces, binomial American; contract chains, roll schedules, continuous contracts, calendar spreads; perpetual funding; fixed-rate bond cash flows, yield, duration, convexity. |
| (4) | as named; `alphalab.api.future_contract_from_spec`, `option_contract_from_spec`. |
| (5) | portfolio, conventions, common. |
| (6) | api (contract lift, `bond_schedule`), data (one enum), runtime (`last_trade_at`, settlement, exercise, assignment, expiry, delivery). |
| (7) | Booking, expiry, settlement, funding and coupons on the spine (B, Re; P, L stepped by the host); pricing, rolls and curves are R libraries. |
| (8) | Options and futures: **VERIFIED** as booking, analytics and expiry (was **MISSING**: a contract held past expiry stayed marked at its last price, FUT-012). `InstrumentRecord.last_trade_at` (instrument schema 4): records after it are skipped, requests refused before an order exists, and a `ContractSettlement` closes every position at the final price -- a future's last variation, an option exercised or assigned at intrinsic value, or expiring worthless -- cancelling working orders and attributing the result; a run that ends holding an expired contract is refused. A roll is the strategy's trade. Exercise, assignment and delivery: **VERIFIED** since the FUT-013 milestone (was **MISSING**). `InstrumentRecord.terms` declares `OptionTerms` (American or European with its window, cash or physical, one `Deliverable` in any quantity, an automatic-exercise threshold or none) or `FutureTerms` (cash, or delivery); `Exercise`, `Assignment` and `OptionExpiry` on the schedule move cash, contracts and the deliverable through the portfolio's fill authority and each strategy's share -- a cash payoff `max(S x units x m_deliverable / m - K, 0)` per unit of price, a physical delivery against `strike x multiplier` marked at the market's price, a physical future delivered at its final price; an option on a future delivers a futures position. Assignment before expiry is an input; at expiry the declared rule assigns. `AssetType.INDEX` is the reference a cash option settles against. Baskets, cash in lieu, a consideration in another currency (quanto) and Bermudan windows: **VERIFIED** since the FUT-016 milestone (was **MISSING**). Margin calls and forced liquidation: **VERIFIED** since the FUT-017 milestone (was **MISSING**) -- an instrument declares `MarginTerms`; an account below maintenance is called (`MarginCall`) after a market step and liquidated through allocation, risk and the OMS at the asset's next quote; a delivery the cash cannot pay is financed when every deliverable declares margin terms, refused otherwise. Crypto perpetuals: **VERIFIED**, funding from a `ScheduledFundingRate` at the run's mark. Fixed income: **VERIFIED** for fixed-rate bonds (was **PARTIAL**, FUT-010) -- `AssetType.BOND` booked by declared economics, `bond_schedule` applying coupons and redemption; accrual is carried by full prices, not booked separately. |
| (9) | `tests/integration/test_option_lifecycle.py` (58), `test_lifecycle_conventions.py` (9), `test_lifecycle_example.py` (4), `tests/integration/test_contract_expiry.py` (25), `test_bonds_on_the_path.py` (8), `test_scheduled_inputs.py::test_a_perpetuals_funding_is_charged_on_the_position_at_the_runs_mark`, `test_instrument_economics.py`, `test_v34_capabilities.py`; examples 33-39, 66. |
| (10) | The final price and an exercise's settlement value are the host's (the exchange's); the engine checks they are applied in the contract's window and are not negative, not that they are the exchange's. A delivery the book cannot pay for is financed only when every deliverable declares margin terms, and an instructed exercise only within the initial requirement; otherwise it stops the run, as does one that would go short without permission. Margin is each position's own, summed: portfolio margin is FUT-019. Since FUT-018 a fully paid instrument declaring `purchase_on_margin` is bought on margin and financed, a period's interest is charged on a debit by declared tiers, and a short is sold only against declared borrow, paying its fee (`test_financing_and_borrow.py`). |
| (11) | Done (FUT-004, FUT-010, FUT-012, FUT-013, and FUT-014: a strategy exercises its own contracts from its hooks, `IntentKind.EXERCISE`, `tests/integration/test_strategy_directed_exercise.py`); FUT-016 (richer terms: `test_advanced_contract_terms.py`); FUT-017 (margin: `test_margin_and_liquidation.py`, `test_financed_delivery.py`); FUT-018 (financing and borrow: `test_financing_and_borrow.py`); open: FUT-019. |
| (12) | Met for cash settlement, exercise, assignment and single-deliverable physical delivery, funding and coupons, applied from a schedule by `BacktestEngine.run` and `ReplayBacktest.run` and through a pause. |

### 2.22 Market microstructure

| | |
| --- | --- |
| (1) | Order-book snapshots in the market model (ADR-0011); no depth hook (ADR-0032 decision 1). |
| (2) | `alphalab/market/book.py`, `snapshot.py`, `events.py`. |
| (3) | `OrderBookSnapshot` published to market state. |
| (4) | `alphalab.market`. |
| (5)-(6) | market. |
| (7) | Depth reaches market state; **not** a strategy hook (BOUNDARY BDY-003). Quotes and trades do. |
| (8) | Quote/trade-driven strategies (spread, imbalance from top of book, inventory via positions): supported through `on_quote`/`on_trade`. Depth-driven strategies: **BOUNDARY** BDY-003. Queue position: **VERIFIED** with a declared `PriceTimeQueue` (FUT-011) -- top of book and signed prints, not depth beyond it; fill probability: not modelled. Latency-sensitive trading: **BOUNDARY** (single-threaded, host-driven; LIM-006). |
| (9) | `tests/integration/test_trade_prints.py`, `test_streaming_session.py`. |
| (10) | Without a queue model a market-making backtest fills resting limits when the market reaches them -- optimistic, and stated by `execution_assumptions.passive_fills`; with one, only what reached the front of the level fills, and the model is named on the result and the manifest (`tests/integration/test_queue_model.py`). |
| (11) | none (FUT-011 delivered); pro-rata books, depth-driven queues and a fill probability would be further queue models. |
| (12) | -- |

### 2.23 Reproducibility, lineage and research-result identity

| | |
| --- | --- |
| (1) | Fingerprints, manifests, certification, portability v3.6 (ADR-0041); engine build v3.11 (REP-002); host-independent records v4.1 (REP-005). |
| (2) | `alphalab/lifecycle/fingerprint.py`, `reproducibility.py`, `certification.py`. |
| (3) | `StrategyFingerprint`, `ReproducibilityManifest`, `RunDigest` (`result_id` over the whole recorded run), `EngineBuild`. |
| (4) | `alphalab.lifecycle`. |
| (5) | backtesting, data protocol, persistence. |
| (6) | -- (top of the graph). |
| (7) | R, B, Re, P, L. |
| (8) | **VERIFIED**. Input lineage: **VERIFIED** (was **PARTIAL**, FUT-005) -- `manifest_for_run(..., schedule=)` checks the schedule holds the run's deliveries (their count and its last delivery) and records its digest in the manifest identity; a combined dataset's parts are recorded and checked; `external_requirements` lists both. Both enter the identity only when present, so every v4.1 manifest keeps its id. A run's own record still names only its last delivery (ADR-0051 known limitation): the check is as strong as that record. |
| (9) | `tests/integration/test_v36_capabilities.py`; `tests/regression/test_run_record_is_host_independent.py`; `docs/audit/scripts/certify_release.py --check`; v4.1.1: example 72 reproduced in fresh interpreters under two hash seeds. |
| (10) | Certified identities are per host class for the standalone engines (LIM-005). |
| (11) | Done (FUT-005). |
| (12) | Met: a manifest of a run that read observations lists its schedule as an external requirement, checked against the run. |

### 2.24 Lifecycle, promotion, deployment and the live boundary

| | |
| --- | --- |
| (1) | ADR-0013 (v2.4), governance actors ADR-0018 (v2.16), lifecycle-to-execution `authorize_run` (v2.16), deployment manager PR-049. |
| (2) | `alphalab/lifecycle/`, `alphalab/model_registry/`, `alphalab/deployment_manager/`, `alphalab/experiment_tracking/`. |
| (3) | `LifecycleState`, `register_strategy`, `promote_strategy_version`, `approve_deployment`, `deploy_strategy_version`, `authorize_run`, evidence store. |
| (4) | `alphalab.lifecycle` (238 names). |
| (5) | 17 packages. |
| (6) | -- |
| (7) | Governs B -> P -> L; constructs nothing. |
| (8) | **VERIFIED** for the engine's contract. Real venues and providers: **EXTERNAL** (adapters are the host's; `tests/reference_adapter` is a test-tree example over a real socket). |
| (9) | `tests/integration/test_model_strategy_lifecycle.py`, `test_v35_capabilities.py`, `test_v36_capabilities.py`, `test_real_execution.py`, `test_live_session.py`. |
| (10) | Authentication, credentials, tenancy, RBAC: **BOUNDARY** (BDY-010, BND-002). |
| (11) | none. |
| (12) | met within the boundary. |

### 2.25 Streaming market data and transports

| | |
| --- | --- |
| (1) | ADR-0031 (v2.15). |
| (2) | `alphalab/marketdata/transport.py`, `websocket.py`; `alphalab/market/stream.py`, `source.py`. |
| (3) | RFC 6455 client, HTTP transport, `MarketDataSource`. |
| (4) | `alphalab.marketdata`, `alphalab.market`. |
| (5) | common, data. |
| (6) | market, runtime sessions. |
| (7) | P, L. |
| (8) | **VERIFIED** against local protocol servers; against a commercial endpoint **EXTERNAL**. |
| (9) | `tests/integration/test_streaming_market_data.py`, `test_streaming_session.py`. |
| (10) | No permessage-deflate (BND-006). |
| (11) | none. |
| (12) | met within the boundary. |

### 2.27 Global market conventions, by capability (FUT-013 audit)

Generic support means the engine reads a declaration and has no branch for a
market; it is not a claim that any market's actual rules are shipped. AlphaLab
ships no calendar, holiday, contract specification or settlement price for any
market (BDY-013): each is the host's declaration, and a missing one is refused
where it is needed.

| Capability | Generic engine support | What a market must declare | Verified by | Limitation |
| --- | --- | --- | --- | --- |
| Time zones, sessions, breaks, holidays, early closes | `MarketCalendar` (IANA zone, weekly windows, holidays, special sessions); `VenueCalendars` per listing venue | the calendar | `test_lifecycle_conventions.py` (no-DST, two DST zones, Sunday-to-Thursday with a lunch break), `test_day_order_sessions.py` | overnight sessions spanning midnight are windows the caller states |
| Lifecycle instants | `session_close`, `settles_at` | the calendar and a `SettlementRule` | `test_lifecycle_conventions.py` | a date on a closed day is refused, not moved |
| Settlement lag (T+n) | `settlement_date`, `settles_at` compute it | the rule | `tests/unit/conventions/` | booked on each fill by the instrument's declared rule over its venue's calendar; unsettled proceeds kept out of buying power (FUT-015, `test_settlement_date_accounting.py`) |
| Currencies and FX timing | settlement per instrument currency, `also_settles`, scheduled rates applied at their `as_of` | each currency's rates | `test_lifecycle_conventions.py::test_an_option_settling_in_another_currency_is_valued_at_the_rate_in_force`, `test_scheduled_inputs.py` | a consideration in another currency converts at the exercise instant, at the run's rate or a quanto contract's fixed one (FUT-016); a package's components share one currency |
| Multipliers, lots, ticks, deliverable quantities | `InstrumentEconomics`, `LotSpecification`, `TickSchedule`, `Deliverable.units` | each contract's | `test_option_lifecycle.py` (multipliers 10 to 1,000, an adjusted deliverable of 150 against 100, an option on a future), `test_advanced_contract_terms.py` | baskets with a cash component and cash in lieu of fractions (FUT-016); a package's cost apportioned by observed values |
| Exercise styles and windows | `OptionTerms` (`AMERICAN`, `EUROPEAN`, `BERMUDAN` with `exercise_windows`, `exercisable_from`, `expires_at`) | the style and windows | `test_option_lifecycle.py`, `test_advanced_contract_terms.py` | the pricers model American and European exercise only |
| Automatic exercise and assignment | `AutomaticExercise` threshold, or none; assignment before expiry an input | the clearing house's rule | `test_option_lifecycle.py` | no probabilistic assignment model in the engine, by design |
| Cash and physical settlement | `SettlementStyle` on options and futures | the style and deliverable | `test_option_lifecycle.py`, `test_financed_delivery.py` | a shortfall is financed on declared margin terms and called (FUT-017); a debit accrues the declared interest for each scheduled period (FUT-018) |
| Corporate actions on deliverables | splits and dividends on the delivered position | the action | `test_scheduled_inputs.py`, example 75 | a contract's adjusted terms are declared with their effective instant (`TermsAdjustment`, `split_adjusted`); a split of a held contract's deliverable without one is refused (FUT-016) |
| Market-specific identifiers | provider aliases and dated aliases on `InstrumentRecord` | the provider's symbols | `test_instrument_identity_reaches_a_fill.py` | -- |

Required external data in every case: the calendar, contract specifications,
settlement prices and FX rates. Required execution-adapter capability on a live
path: a venue's own exercise, assignment and delivery reports, which arrive
through the broker boundary as fills and terminal outcomes; no adapter ships.
Acceptance to claim a market supported: its declared calendar and contract
terms, its settlement values, and a run reproducing a known lifecycle
end to end against them.

### 2.26 Standalone research infrastructure

| Package | Status | Note |
| --- | --- | --- |
| `scenario` | **VERIFIED**, standalone by design | Shocks any flat book; a test reads the source to keep it free of edges. |
| `scheduler` | **VERIFIED**; driver connection **VERIFIED** | Session and cron timers produce `TimerEvent`s; `RunSchedule.of(timers=...)` puts them on either historical driver (was a caller's `RunEngine.fire_timer`, FUT-004). |
| `distributed`, `cloud_research`, `cluster_scheduler` | **NOT E2E** for strategy work | Process-pool parameter sweeps; nothing joins them to a backtest's identity, though a sweep can call one per point. |
| `research_assistant` | **VERIFIED** as a deterministic grid driver | No LLM by design. |
| `reporting` | **VERIFIED** | Report exports; presentation is the host's. |
| `brokers` | **VERIFIED**, standalone | Many-venue registry, distinct from `broker` (pinned). |
| `feature_store` | **ISOLATED** | See 2.17. |

---

## 3. The specific questions the brief asks

| Question | Answer, with evidence |
| --- | --- |
| Research and factor composition | VERIFIED on the study path (2.17), and a strategy evaluating the study's definitions equals it (example 73). |
| Alternative- and fundamental-data integration | VERIFIED: ingestion with availability rules, sets, delivery to strategies on B (v3.12) and now Re/L/api (2.14). |
| Macro-data integration | VERIFIED as observations with period, release and vintage kept apart (2.16); `alphalab.macro` series lifted by `observations_from_indicator`. |
| External event delivery into backtest, replay and execution | B: VERIFIED since v3.12. Re, api, L: **closed in v4.1.1**, a durable paused replay included (replay schema 2). Paper over a source: host-delivered. |
| Social/news sentiment | VERIFIED as an input stream; scoring text is EXTERNAL (2.15). |
| Multiple asset classes in one strategy and portfolio | VERIFIED: equity + future in example 72; options and futures to expiry, perpetual funding, bonds and a two-currency book through the drivers (2.11, 2.21). Options exercised, assigned and delivered, and futures delivered, since the FUT-013 milestone (2.21). |
| ML/DL/RL training, validation, inference and persistence | Adaptive: VERIFIED. ML: VERIFIED end to end (example 73). DL: NOT E2E. RL: environment VERIFIED; stale defect claim fixed. |
| Multi-strategy allocation and attribution | VERIFIED (2.4, 2.20). |
| Execution algorithms evaluated historically | VERIFIED since the FUT-009 milestone: TWAP, VWAP, slicing, iceberg and participation work their children through both historical drivers (2.8). |
| Derivative lifecycle: exercise, assignment, delivery | VERIFIED since the FUT-013 milestone for American and European options, cash or one deliverable, and futures cash or delivered (2.21); the global-convention support boundary is 2.27. |
| Strategy state capture and restore | VERIFIED, at every record boundary of example 72 (2.2). |
| Research-to-strategy-to-execution data contracts | VERIFIED for prices and information; several datasets per run **added in v4.1.1** (2.13). |
| Strategy promotion, paper execution and production execution | Promotion and paper: VERIFIED. Production: the contract VERIFIED against a reference adapter; real venues EXTERNAL (2.24). |
| Streaming data and public API availability | VERIFIED locally (2.25); `alphalab.api` and documented modules are under the manifest (`docs/api/public_api.json`). |
| Actual broker/venue support versus adapter-only abstractions | **No vendor adapter ships.** `PaperBroker` is a deterministic local venue; `tests/reference_adapter` is a test-tree adapter over real HTTP; every named venue is the host's. |
