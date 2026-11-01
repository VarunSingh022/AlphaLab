# Strategy Families: From Research to Production (v4.1.1 audit, v4.2.0 candidate)

What each family of quantitative strategy can do in AlphaLab today, judged from
source, tests and executed runs, and what stands between it and each stage. The
component-level evidence is [`STRATEGY_CAPABILITY_MATRIX.md`](STRATEGY_CAPABILITY_MATRIX.md)
(section numbers below refer to it); the work that would close each remaining
gap is a ledger entry and a ROADMAP "Future work" item. The audit raised
FUT-003 to FUT-012; its continuation delivered FUT-003 to FUT-008, FUT-010 and
FUT-012 and raised FUT-013; the FUT-009 milestone delivered FUT-009; the FUT-013
milestone delivered FUT-013 and raised FUT-014 to FUT-017; the v4.2.0 milestones
delivered FUT-011, FUT-002 (which predates the audit, by declaration), FUT-014,
FUT-016, FUT-015 and FUT-017, the margin milestone raising FUT-018 and FUT-019;
the v4.2.0 global gap audit delivered EXE-012 (a strategy withdraws its own
working orders) and raised FUT-020 and FUT-021; and the phase that followed
delivered FUT-018 (financing and short borrow), FUT-019 (portfolio margin) and
FUT-020 (venue price limits and halts) and FUT-021 (holding exchanges). Every
entry the program accepted is delivered. The phase 2 global gap audit that
followed (section *The phase 2 global gap audit*, below) fixed three gaps --
EXE-013, NUM-016, and a strategy's view of borrow terms under FUT-018 -- and
raised FUT-022 to FUT-029, which phase 3 delivers in turn (FUT-022, a
lender's recall bought in, FUT-023, daily accrual, FUT-025, an entitlement paid later, FUT-026, options repriced in
margin scenarios, FUT-024, holder elections, FUT-028, reference-price bands, FUT-027, a reopening auction, and FUT-029, a strategy's amend: all eight). Rows below are as
of the FUT-011 milestone.

## Classes

| Class | Meaning |
| --- | --- |
| **A** | Supported end to end and evidenced: a test or CI-run example drives a strategy of this kind through the canonical path. |
| **B** | Supported by existing components but missing a connection: the parts exist, something must still be wired (named). |
| **C** | Feasible through the generic strategy and data contracts, but needs external data or infrastructure, or a model the researcher supplies -- AlphaLab bundles none, by design. |
| **D** | Requires new engine capability (named). |
| **E** | Cannot reasonably be guaranteed by the engine itself (reason given). |

A family is never marked A because one helper exists. Where its parts sit in
different classes, each is stated. "Researcher code" means a strategy written
against the documented contracts ([`../COMPOSING_STRATEGIES.md`](../COMPOSING_STRATEGIES.md))
using what the context shows it -- no internal import, no engine change.

## Stages

R research (study, validation) - B backtest - Re replay - P paper - L live.
For every family: L against a real venue is **EXTERNAL** -- AlphaLab ships no
venue adapter; the contract is tested against `PaperBroker` and a reference
adapter over real HTTP (matrix 2.24). This is not repeated in each row.

---

## 1. Price and systematic signals

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Trend following, breakout | **A** | A moving-average crossover from ingestion to a hand-checked, continued result (`tests/integration/test_strategy_building_end_to_end.py`); stop and stop-limit terms on the path (`test_order_terms.py`). Breakout levels are researcher code over `context.history`. |
| Time-series and cross-sectional momentum | **A** | Cross-sectional decisions on complete instants (`test_slices.py`); research to a traded book (`examples/10_complete_pipeline.py`, example gate); momentum in example 72 (`test_multi_asset_composition.py`). |
| Mean reversion, statistical reversal | **A** | An adaptive reversion strategy, backtest and replay agreeing (`test_v37_capabilities.py::test_backtest_and_replay_agree_for_an_adaptive_strategy`). |
| Calendar and seasonal effects | **A** | Calendars and sessions are on the path (matrix 2.10); timers are scheduled inputs on both historical drivers, fired after the records of their instant (`test_scheduled_inputs.py::test_a_timer_fires_after_its_instants_records_and_its_order_fills_at_the_next_price`). A seasonal rule itself is researcher code, not separately evidenced. |
| Technical indicators, custom transformations | **A** | Researcher code over bars and history (example 72's features); `alphalab.factor_library` primitives for research. |
| Volatility-scaled, regime-conditioned | **A** for regime conditioning (example 72's inflation regime; `test_v37_capabilities.py::test_regimes_are_detected_from_a_feature_and_condition_a_diagnostic`). Volatility scaling is researcher code over the same contracts, not separately evidenced. Statistical regime *models* (HMM, Markov switching) are a stated boundary (OFE-010): a researcher's model, fitted with its own window. |

## 2. Quantitative and relative value

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Equity factor models; value, quality, growth, profitability, low volatility | **A** (research and construction) | Factor computation and studies with lineage (`test_v32_capabilities.py`, examples 17-24); point-in-time fundamentals with restatements (`test_v37_capabilities.py::test_fundamentals_are_read_as_knowable_and_restated_only_once_published`); a factor-neutral, sector-capped rebalance (`test_v38_capabilities.py`). A strategy evaluates the study's own `FeatureDefinition`s and equals the study panel at every instant (`test_model_on_the_execution_path.py::test_the_strategy_computes_the_studys_features_exactly`). |
| Statistical arbitrage, pairs, cointegration | **C** | Several instruments per strategy, slices, shorting and per-strategy targets are on the path; AlphaLab has no cointegration test or hedge-ratio estimator -- the researcher's model. Borrow availability and locate are external; a borrow fee is a `ScheduledCashFlow` on the run's schedule. |
| Residual, basket, cross-asset relative value | **C** | Neutralization (`factor_library`), multi-instrument intents; residual models are researcher code. Cross-currency baskets convert at scheduled rates (matrix 2.11). |
| Lead-lag, spread signals | **C** | Asynchronous series keep their own instants in a combined dataset (matrix 2.13); the lead-lag estimator is researcher code. |
| Market-neutral, factor-neutral construction | **A** | `construct` with constraints; factor-neutral rebalance (`test_v38_capabilities.py`). |

## 3. Macro, fundamental and event-driven

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Global macro, systematic asset allocation | **A** | In one settlement currency, example 72 (an equity and an index future hedged on CPI); across currencies, a two-currency book funded and traded through the driver at the rate in force at each step (`test_scheduled_inputs.py::test_a_two_currency_book_is_funded_and_traded_through_the_driver`, `::test_the_driver_equals_a_loop_that_states_every_rate_by_hand`). |
| Economic releases, surprises, revisions, vintages | **A** | Period, release instant and vintage kept apart; every decision used the vintage published by then (`test_multi_asset_composition.py::test_every_decision_used_the_cpi_vintage_published_by_then`); actual-against-consensus as event measurements (example 50). |
| Inflation, growth, policy, yield-curve signals | **A** | From released figures, and from `alphalab.macro` indicator series lifted by `api.observations_from_indicator` with their vintages (`tests/unit/test_api_macro_lift.py`, checked against `macro.known_as_of`). Derived figures (a curve built from several released rates, a real rate) are researcher code over the lifted releases. |
| FX, futures, commodity and rates carry | Futures and commodity carry **A**: booking, chains, roll schedules and `factor_library.carry`; the roll is the strategy's trade, and a contract held to its last trading instant is cash-settled (`test_contract_expiry.py::test_a_future_held_to_expiry_is_closed_at_its_final_settlement`). A physically delivered future delivers its deliverable at its final price (`test_option_lifecycle.py::test_a_physically_settled_future_delivers_against_its_final_price`). FX carry **A** for the mechanics (scheduled rates and a stated interest `ScheduledCashFlow`), not separately evidenced as a strategy. Rates carry **A** for fixed-rate bonds (`test_bonds_on_the_path.py`). |
| Financial statements, valuation | **A** | Point-in-time statements, restatements, per-share restatement across splits (`test_external_information.py`), value and quality factors. |
| Earnings drift, analyst revisions, corporate actions | **A** for events delivered at availability and event studies anchored where they could first be traded (`test_v37_capabilities.py::test_events_are_anchored_where_they_could_first_be_traded`); splits, dividends and fees booked (`test_instrument_economics.py`), and applied from the run's schedule -- a per-share dividend on the position held at its ex-date, short or long (`test_scheduled_inputs.py::test_a_dividend_declared_per_share_pays_the_position_held_on_its_ex_date`). |
| News, event studies, event-driven | **A** | `on_observation` with subject subscriptions (`test_external_information.py`); `research.event_study`. |
| Regime identification, changing regimes | **A** for declared-rule regimes (research and trading); statistical regime models: boundary OFE-010 (researcher-supplied). |

## 4. Alternative data

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Social-media sentiment | **A** as an input stream | Posts with a stated collection instant, a two-week-late post never used early, an undated post never delivered (`test_multi_asset_composition.py`). Scoring text is EXTERNAL: no NLP model, LLM or social API is in the engine. |
| News and textual sentiment | **A** for scores; **C** for text | As above; turning text into a score is the host's or the researcher's. |
| Entity-level sentiment and attention | **A** | Observations are keyed by the source's subject; subscriptions can name one (`observations:<subject>`). Mapping an entity to an instrument is the researcher's (by design: observations are not `asset_id`-keyed). |
| Fundamentals, web activity, alternative datasets | **A** | The observation vocabulary is open (`category`, `metric`); availability rules state when each row became knowable. |
| Satellite, weather, shipping, supply chain | **C** | Typed v1 records and the generic form exist; the data, its licensing and its availability semantics are the provider's. |
| Several external modalities combined | **A** | Example 72 combines sentiment and macro releases with two price series. |

## 5. Derivatives and specialised markets

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Options volatility, skew, dispersion, Greeks | **A** in research (pricing, Greeks, IV, surfaces, American lattice: examples 35, 36, 66; Hull's references certified); **A** for trading cash-settled options to expiry -- premium times multiplier booked, exercise or assignment at intrinsic value, worthless expiry (`test_contract_expiry.py::test_an_option_is_settled_at_its_intrinsic_value`); **A** for options whose terms are declared -- American and European, cash or physical, exercised early in part, assigned by a supplied input, auto-exercised by a declared threshold, an adjusted deliverable, an option on a future (`test_option_lifecycle.py`, example 75); **D** for baskets, cash in lieu, quanto and Bermudan (FUT-016). Volatility and dispersion strategies themselves are researcher code over these. |
| Futures spreads, curve models, rolls, transitions | **A** in research (chains, roll schedules, continuous contracts: examples 33, 34); legs trade as instruments; **A** for cash-settled contract transitions on the path -- records after the last trading instant skipped, requests refused, positions settled and attributed (`test_contract_expiry.py`); **A** for physical delivery of one deliverable (`test_option_lifecycle.py`). |
| Fixed income, rates, credit, duration | **A** for fixed-rate bonds | `AssetType.BOND` booked by declared economics; `api.bond_schedule` turns `macro.Bond` terms into coupons and a redemption the run applies to whoever holds (`test_bonds_on_the_path.py`); analytics (yield, duration, convexity) in `alphalab.macro.bond`. Accrual is carried by trading full (dirty) prices; no separate accrued-interest entry (ADR-0051). Floating-rate and callable bonds are researcher-built schedules (**C**); credit models are excluded on purpose (ROADMAP boundaries). |
| Cross-currency strategies | **A** | Scheduled rates on both historical drivers and the facade; a missing rate is refused, never assumed (`test_scheduled_inputs.py`). |
| Crypto spot, perpetual funding, basis, cross-venue relative value | Spot **A** (a fully paid unit); perpetuals **A** for economics (`test_instrument_economics.py::test_a_perpetual_settles_like_a_future_and_pays_funding_as_a_cash_flow`), funding from a schedule **A** (`test_scheduled_inputs.py::test_a_perpetuals_funding_is_charged_on_the_position_at_the_runs_mark`); basis **C**; cross-venue **D** for realism -- two listings are two instruments, but one simulated run has one cost model, so venue-specific fees and latency are not modelled. |
| DeFi / AMM | **E** | No protocol, pool or on-chain execution model, and none is in scope: AMM pricing, gas, MEV and settlement finality are the protocol's and the host's. |

## 6. Market microstructure and execution

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Market making, inventory control | **C** | Quotes, resting limits, positions and costs are on the path; a declared queue model (`PriceTimeQueue`, FUT-011) fills a resting order only once the displayed queue ahead of it has traded or left, so passive fills are no longer optimistic by construction. Depth beyond the top of the book and pro-rata books are not modelled. |
| Order-flow imbalance, liquidity, spread signals | **A** from top of book and trade prints (`on_quote`, `on_trade`; `test_trade_prints.py`); depth-driven signals are a boundary (BDY-003). |
| Queue-aware execution, fill probability | **A** for queue position, **C** for fill probability | A resting limit order's place in a price-time queue decides its passive fills on both drivers, replay and pause included (`test_queue_model.py`, FUT-011). A fill-probability model is a model the researcher supplies: `QueueModel` is a protocol, and AlphaLab ships only the deterministic price-time one. |
| Slicing: TWAP, VWAP, participation, slicing, iceberg | **A** | On the live and paper path (`test_v39_capabilities.py`) and, since the FUT-009 milestone, on both historical drivers: each child filled by the run's fill policy and cost model and settled on one parent order, its implementation shortfall measurable (`test_algorithms_on_the_historical_path.py`, example 74). Participation that completes after its window is refused. |
| Smart order routing across venues | **A** on the live and paper path (`select_route`, `test_v39_capabilities.py`); **D** historically -- a run simulates one venue and one cost model, so a choice between venues is not evaluated in a backtest. |
| Latency-sensitive strategies | **E** | The engine is single-threaded and driven by its host (LIM-006); co-location, kernel-bypass feeds and venue latency are infrastructure no library can guarantee. |
| Arbitrage constrained by fees, borrow, settlement, capital, liquidity | **B/C** | Fees, liquidity caps, strategy capital ceilings and settlement conventions are on the path; borrow availability is external; multi-currency capital converts at scheduled rates. |

## 7. Statistical learning and adaptation

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Classical statistical models | **C** | Regression, shrinkage covariance and declared-rule regimes are bundled; other models are the researcher's. |
| Supervised and unsupervised learning | **A** supervised: a linear model fitted on purged labels known before a boundary, carried as declared parameters and state, traded after it and reproduced (`examples/73_train_and_trade_a_model.py`, `test_model_on_the_execution_path.py`); **C** unsupervised (none bundled). |
| Feature selection, ensembles | **C** | Researcher code; multiple-testing corrections and deflated Sharpe exist to judge the selection (matrix 2.18). |
| Deep learning, sequence models | **B** (dense, conv1d, LSTM and attention with checked gradients); a trained network is held by a strategy as example 73 holds its linear model, but no test trades one; large or GPU models **E** for the engine (pure Python, `dependencies = []`, OFE-026). |
| Reinforcement learning | **A** for the environment over the real pipeline (`tests/unit/reinforcement_learning/`); **B** for evaluating a trained agent with the research methodology. |
| Online learning, adaptive strategies | **A** | Immutable learned state with lineage, identical in research replay and on the path (`test_v37_capabilities.py`). |
| Researcher-defined algorithms and combinations | **A** | Example 72: a strategy no template anticipated, built from public contracts. |

## 8. Portfolio and meta-strategy design

| Family | Class | Evidence / what is missing |
| --- | --- | --- |
| Risk parity, robust optimisation | **A** | `test_v38_capabilities.py::test_risk_parity_across_strategies_equalizes_their_risk`. |
| Minimum variance, maximum diversification | **A** | `construct` with certified statuses (examples 56, 10). |
| Black-Litterman, constrained optimisation | **A** | `test_v38_capabilities.py::test_black_litterman_on_the_universe_with_a_view`. |
| Dynamic risk and capital allocation | **A** between runs (`allocation.capital`, `test_v38_capabilities.py::test_each_run_was_funded_with_exactly_its_allocation`); within one run a strategy's budget is configuration -- re-allocating mid-run is a meta-strategy's own sizing (**C**). |
| Strategy ensembles, regime-conditioned allocation | **A** for several strategies netted into one book with attribution (`test_v39_capabilities.py`); regime-conditioned allocation is a meta-strategy (**C**). |
| Cross-strategy exposure, concentration, attribution | **A** | `analytics.cross_strategy`; `test_v38_capabilities.py`. |
| Tail-risk protection, hedging | **A** for futures hedges (example 72) and scenario stress (`alphalab.scenario`, examples 29, 30); cash-settled option protection held through expiry **A** (`test_contract_expiry.py`); physically settled protection **A** (`test_option_lifecycle.py::test_an_exercised_put_delivers_shares_held_and_receives_the_strike`). |

---

## Combinations

The families are not independent, and the acceptance scenario exists to show
that the seams hold: example 72 combines a price signal (1), a macro regime
(3), social sentiment (4), a futures hedge (5), a researcher-defined algorithm
(7) and position sizing with a risk gate (8), through one run, one replay and
one continuation, with the time semantics of each input preserved. What breaks
a combination today is a gap named on the ROADMAP, and never a missing strategy
template; how its
orders are executed is now part of what a backtest measures (FUT-009). Example 72 now carries every timed input
on one schedule, and its replay pauses through a payload.

## What would move the most families

| Work | Families it moves | Release class |
| --- | --- | --- |

Delivered by the continuation, with the release class each took: FUT-004
scheduled inputs, FUT-012 cash settlement at expiry, FUT-005 the schedule in the
manifest (additive: manifests built without one keep their ids), FUT-006 and
FUT-007 features and a model on the path, FUT-008 macro series, FUT-010 bonds
(a new persisted enum member, additive), FUT-003 durable replay pause (replay
snapshot schema 2, with an upgrade), and, by the FUT-009 milestone, execution
algorithms on the historical drivers (a new routing member, additive; the
algorithm book is driver state, so no run's record moved), and, by the FUT-013
milestone, the derivative lifecycle (instrument terms in the unreleased schema 4,
an `AssetType` member, schedule kinds), and, by the v4.2.0 milestones, FUT-011,
FUT-002, FUT-014, FUT-016, FUT-015 and FUT-017 (record fields outside the
identity key, transaction metadata, enum members and event tags, each written
only when used, so no earlier run's record moved) -- all minor.

## The phase 2 global gap audit

Read after FUT-018 to FUT-021 were delivered, against the objective rather than
the FUT list: the brief's areas -- research, data, markets and instruments,
execution and risk, reliability -- through the code, each checked against what
the matrix claims. Classes: 1 correctness-critical; 2 a material engine
capability gap; 3 an explicit external boundary; 4 an accepted limitation; 5 a
future enhancement; 6 already implemented or resolved.

| Finding | Class | Disposition |
| --- | --- | --- |
| A strategy could read a lender's later borrow entries through `context.universe.instrument(...)`: information before its instant | 1 | **Fixed** (FUT-018): the view shows the series only as far as in effect; `context.universe.borrow` gives the entry in effect now |
| A declared tick grid was read by nothing that placed an order: an off-grid limit or stop could fill | 2 | **Fixed** (EXE-013): refused at placement; a band's cap lands on the grid |
| A day count's year was chosen by elimination, so a basis added later would divide by 365 | 5 | **Fixed** (NUM-016): each year stated, an unstated one refused |
| Borrow recall and buy-in | 2 | FUT-022, **delivered in phase 3** |
| Interest and fees on a period's daily balances | 5 | FUT-023, **delivered in phase 3** |
| Holder elections; corporate-action and adjustment announcement instants | 2 | FUT-024, **delivered in phase 3** |
| A corporate action's consideration paid after its instant | 5 | FUT-025, **delivered in phase 3** |
| Options repriced from their underlying in margin scenarios | 2 | FUT-026, **delivered in phase 3** |
| A halt's reopening auction; auction imbalance | 2 | FUT-027, **delivered in phase 3**; imbalance arrives as the caller's observations |
| Bands computed from a reference price | 5 | FUT-028, **delivered in phase 3** |
| A strategy's amend in place, keeping priority | 5 | FUT-029, **delivered in phase 3** |
| Opening and closing auction orders | 6 | `OrderTerms.at_open` and `at_close`, filled at a session bar's open or close |
| An operator's amend at a live venue | 6 | `LiveSession.amend` |
| Lots, minimum notional, negative prices, multipliers | 6 | `InstrumentEconomics`, read at allocation and liquidation |
| Calendars, zones and DST, T+n settlement, FX timing, scheduled rates | 6 | matrix section 2.27; `test_lifecycle_conventions.py` |
| Data revisions, vintages, late, duplicate and undated observations | 6 | matrix sections 2.13 to 2.15; example 72 |
| Point-in-time features and models on the execution path | 6 | FUT-006, FUT-007; example 73 |
| Deterministic replay, pause and resume, snapshot compatibility, manifests, the public API | 6 | the release certificate; the schema-upgrade suites; `docs/api` |
| Hard-coded regional conventions in the v4.2.0 modules | 6, none found | the literals in `borrow`, `controls`, `margin`, `financing` and `corporate_actions` are docstrings |
| Market depth beyond the top of book, real venues and data, reference data, sentiment models, hosted isolation of strategy code | 3 | unchanged (BDY-003, BDY-013, matrix 2.15, 2.24) |
| An insolvent run's analytics; the feature store and portfolio construction off the drivers | 4 | LIM-008; matrix 2.17, 2.20 |

Nothing here claims global completeness: what is declared is applied, what is
not is refused or stated, and the rules, data and venues of any real market are
the host's.

## The phase 3 global gap audit

Read after FUT-022 to FUT-029 were delivered, against the brief's areas --
microstructure, order types, borrow and financing, derivatives, corporate
actions, margin and collateral, settlement and reconciliation, point-in-time
integrity, alternative data and models, multi-asset composition, calendars and
conventions, the public API -- and against each other, since several defects
appear only where two capabilities meet. Same classes as above.

| Finding | Class | Disposition |
| --- | --- | --- |
| A results retention bound could trim a fill still unsettled, and a restore lost it | 1 | **Fixed** (ACC-015) |
| A strategy's resting bid deferred a margin call indefinitely; a forced trade could be absorbed by it, or read through the sizing model | 1 | **Fixed** (ACC-016) |
| A purchase its own currency could not pay stopped a multi-currency run at the fill | 1 | **Fixed** (ACC-017): refused when placed |
| An entitlement's EUR could have paid a EUR purchase before it was paid | 1 | **Fixed** with ACC-017 |
| The capability matrix did not list holding exchanges | documentation | corrected |
| Elections split between holders; proration | 2 | FUT-030 (v4.4) |
| A replacement risk refuses leaves the original withdrawn | 5 | FUT-031 (v4.3) |
| A dated volatility surface; scenario horizons | 5 | FUT-032 (v4.4) |
| An auction uncross from a book; extensions; a retention-proof first event | 5 | FUT-033 (v4.4) |
| Failed or corrected corporate-action payments; legs delivered later | 5 | FUT-034 (v4.4) |
| A buy-in premium; borrow recalled per strategy | 5 | FUT-035 (v4.4) |
| A capital ceiling's reservation on an in-place amend | 4, conservative | FUT-036 (v4.3) |
| Opening and closing auction orders, an operator's live amend, imbalance as observations | 6 | already implemented |
| Depth beyond the top of book, real venues and data, reference data | 3 | unchanged (BDY-003, BDY-013) |

Nothing here claims global completeness: what is declared is applied, what is
not is refused or stated, and the rules, data and venues of any real market are
the host's.

## Markets

No family above is tied to a market. A market is a declared calendar, a
settlement rule, a currency and its contracts' terms; matrix section 2.27 sets
out what the engine does generically, what each market must declare, and where
the boundary is. Nothing here claims a market's actual rules are shipped.
