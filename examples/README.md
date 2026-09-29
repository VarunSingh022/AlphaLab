# AlphaLab Examples

Sixty-five runnable scripts, each demonstrating one part of AlphaLab against
its real public API. Every one of them runs:

```bash
python examples/01_research.py
```

They are **not** part of the automated test suite — the suite covers the same
paths far more thoroughly under `tests/` — but all sixty-five are executed as a
release gate, and a change that breaks one is a change that breaks a documented
API.

---

## The examples

| # | File | What it shows |
|---|------|---------------|
| 01 | `01_research.py` | The research engine: a payload in, an immutable `ResearchState` and a score out |
| 02 | `02_backtest.py` | **Your first backtest**: a CSV ingested into a versioned dataset and run through the canonical path, the strategy stating the position it wants (`TARGET_QUANTITY`) |
| 03 | `03_replay.py` | The replay cursor: sessions, chronological validation, replay metrics |
| 04 | `04_market_data.py` | A host provider's wire bars through a normalization policy into canonical bars and market state |
| 05 | `05_broker_connection.py` | The **two** broker boundaries — one venue (`BrokerProtocol`), or a registry of many (`BrokerConnectorProtocol`) |
| 06 | `06_portfolio_optimizer.py` | Portfolio construction, constraints, exposure and rebalancing |
| 07 | `07_universal_data.py` | The Universal Data Engine: ingestion, schema, quality, catalogue |
| 08 | `08_strategy_studio.py` | Rebalancing a three-name book to target weights; a lot size declared on the instrument, and rounding toward zero |
| 09 | `09_workbench.py` | Cross-sectional decisions on complete instants (`on_slice`), orders resting to the next bar, and a two-step rotation in a cash account |
| 10 | `10_complete_pipeline.py` | **Research to a traded book**: walk-forward optimization of a signal, Ledoit-Wolf construction with transaction costs and whole shares, out-of-sample trading, and benchmark-relative statistics |
| 11 | `11_unified_backtest.py` | **The integrated execution path**: dataset → intents → orders → fills → P&L → analytics, and replay parity against the same dataset |
| 12 | `12_model_lifecycle.py` | **The lifecycle path**: research candidate → experiment run → evidence → model version → strategy version → gated promotion → deployment → rollback, with governance |
| 13 | `13_durable_run_state.py` | Stopping a seeded run, storing it with `FileRunStateStore`, continuing it in a **separate interpreter**, and comparing byte-for-byte against a run that never stopped |
| 14 | `14_multi_currency_settlement.py` | An FX feed's three rules, a run settling **two** currencies, one reported figure with every rate that produced it, and the refusals |
| 15 | `15_data_ingestion.py` | **The v3.1 data path**: a deliberately broken CSV through schema detection, validation, an explicit cleaning policy, a quality report, and out the other side as a canonical dataset with a derived, immutable version |
| 16 | `16_research_from_dataset.py` | Research access by dataset version, point-in-time selection, and a backtest whose result names the **exact bytes** it was measured on |
| 17 | `17_feature_engineering.py` | **The v3.2 feature framework**: typed definitions that default nothing, derived identity, trailing windows and the warmup each costs, missing data skipped or refused but never filled, lineage back to the dataset version, and the Feature Store seam |
| 18 | `18_factor_research.py` | Cross-sectional ranking with a stated tie method, three neutralizations that are **not** interchangeable, the information coefficient with its sample counts, decay across horizons, turnover under a named convention, and exposure by sector |
| 19 | `19_signal_diagnostics.py` | Forward-return analysis across five horizons, the quantile profile an IC alone hides, monotonicity as distinct from spread, and diagnostics conditioned on a volatility regime the example defines itself |
| 20 | `20_walk_forward_validation.py` | Train / validate / test / roll, rolling against expanding, exact fold boundaries printed and checked, purging on **both** sides, and a configuration that genuinely cannot be validated |
| 21 | `21_time_series_cross_validation.py` | Four schemes side by side; why an embargo only bites on a blocked fold; purging read off the actual series rather than subtracted from dates; and the refusals that keep each scheme's name true |
| 22 | `22_robustness_testing.py` | Parameter, data and signal perturbation, missing data by deletion, execution delay, a cost charged against turnover rather than against every return, block bootstrap and independent Monte Carlo paths — every one seeded |
| 23 | `23_overfitting_diagnostics.py` | A nine-window sweep that counts every trial, a cliff against a plateau at the same best score, out-of-sample degradation, period and symbol stability, and the Bonferroni threshold — with no score anywhere |
| 24 | `24_strategy_research_pipeline.py` | The complete research pipeline, ending in evidence and a promotion gate |
| 25 | `25_execution_simulation.py` | The six execution cost roles, the two settlements, and the stated ordering |
| 26 | `26_capacity_modelling.py` | Capacity as a liquidity question: what binds, and what moves the answer |
| 27 | `27_portfolio_attribution.py` | Nine attribution dimensions, reconciliation, and reporting what is unavailable |
| 28 | `28_risk_decomposition.py` | Three VaR methods, the Euler decomposition, and samples that refuse |
| 29 | `29_portfolio_stress_testing.py` | Synthetic and historical scenarios — and why 2008 ships as a contract |
| 30 | `30_scenario_engine.py` | One scenario contract applied to five different books |
| 31 | `31_global_market_conventions.py` | **The v3.4 convention authority**: three markets declared side by side, a tick *size* against a tick *value*, a tiered grid, rounding as a stated direction, a partial lot refused rather than rounded, the multiplier applied once, and quote against settlement currency |
| 32 | `32_exchange_calendars_and_sessions.py` | Four venues in four timezones disagreeing about one instant; an overnight session belonging to the day it opened; a lunch break as two windows and one envelope; daylight saving moving the UTC instant while the wall clock holds; and the settlement dates each venue's own trading days produce |
| 33 | `33_futures_contracts_and_rolls.py` | A contract chain and the three ways it refuses an incoherent one; three roll rules producing three schedules from one chain; which contract was front; contango, backwardation and the humped curve the endpoints get wrong; annualized roll yield; and margin as a published figure refused when it post-dates the research instant |
| 34 | `34_continuous_futures_research.py` | The four separate things a continuous series is built from, the segments and the real prints at each roll, three adjustment methods producing three series that agree on the newest bar, reproducibility, and a missing roll print refused rather than interpolated |
| 35 | `35_options_chains_and_greeks.py` | A chain by expiry, type and strike; Greeks beside the four things the model does not do; multipliers that are not 100 and payoffs that scale with them; net premium and net Greeks across legs that keep their direction; and expiry as exercised, assigned, abandoned or worthless with cash and underlying units as two signed quantities |
| 36 | `36_implied_volatility_surface.py` | The inversion round-tripping the volatility it was priced at; the **five** ways a quoted price has no implied volatility, each refused; a surface built from a chain that reports every refusal with its reason; the smile at one expiry; and a term structure that interpolates across none |
| 37 | `37_fx_research.py` | Quotation direction carried rather than inferred; a cross derived only through a **named** currency; covered-parity forwards with both deposit rates and the day count required; carry against forward points; the look-ahead guard in both directions of time; and currency attribution as an identity with no residual |
| 38 | `38_crypto_perpetuals_and_funding.py` | Two venues that disagree about funding interval, fees, price source and minimum size; funding instants following a venue's own anchor; accrual against the venue's mark with a missing mark refused; maker rebates in the same sign convention as funding; and a 24/7 clock measured against 24/7 data |
| 39 | `39_fixed_income_foundation.py` | Cash flows generated backwards from maturity; clean, dirty and accrued as three numbers with the identity checked; the yield inversion and the prices it refuses; duration in years against convexity in years squared; discount factors under a **named** compounding; and the boundary, stated |
| 40 | `40_multi_asset_portfolio.py` | Five asset classes, four venues, four currencies and one book: sessions read locally, the multiplier applied once, notional in the quote currency, settlement converted with a recorded rate, and the return split into what the assets did and what the currencies did |
| 41 | `41_strategy_lifecycle_progression.py` | **The v3.5 progression**: the eight stages from research to live money, every refusal and the reason for it, a pause that returns to the stage it interrupted, and the progression checked against the registry's own stage rather than replacing it |
| 42 | `42_deployment_specification.py` | What a strategy version needs to run **as it was researched**: dataset assumptions by derived identity, the risk limits the pre-trade gate enforces, capital, broker capabilities with no vendor anywhere, a content digest that stops verifying when edited, and the coherence a single field cannot see |
| 43 | `43_runtime_health.py` | Seven health categories from **supplied** observations: thresholds at, past and before the budget; a reconnecting adapter that warns against a dead one that breaches; and why a clean report with something unevaluated is `UNKNOWN` rather than `HEALTHY` |
| 44 | `44_expected_paper_live_comparison.py` | A backtest, a paper run and a venue's own records compared: declared alignment, stated tolerances, money per currency, a venue's unmeasured slippage staying **missing** instead of becoming zero, and three pairs so a divergence can be located |
| 45 | `45_broker_reconciliation.py` | AlphaLab's execution state against a normalized broker state: a lost fill, an order the venue never held, one nobody routed, a resized position, venue symbols joined through a supplied mapping, and a currency the account cannot speak about — unreconciled, not agreed |
| 46 | `46_strategy_fingerprints.py` | **The v3.6 fingerprint**: five defining inputs each changing the identity on its own, mapping order and name spelling that do not, a dependency record whose completeness is declared rather than assumed, and the same identity from a second interpreter with a different hash seed |
| 47 | `47_reproducible_research_artifacts.py` | What exact inputs produced this result: a manifest whose every identity is read from its owner, a rerun that reproduces, one of other inputs, one that diverges because a live object changed, an unseeded run refused, and a research study with its absent seed stated |
| 48 | `48_strategy_certification.py` | Eight machine-verifiable properties with four statuses and no score: evidence observed rather than asserted, leverage and drawdown read as the pre-trade gate reads them, resource figures that say how and where they were measured, and what each assessment does not establish |
| 49 | `49_strategy_portability.py` | One fingerprint across research, paper and two brokers declared as capabilities: eight requirements each satisfied, blocked, unverified or not applicable, a blocker named rather than worked around, and a deployment that quietly retunes the strategy caught |
| 50 | `50_event_driven_research.py` | **The v3.7 event model**: one canonical record for any release, four instants kept apart (occurred, knowable, in effect, ingested), where a release falls in the trading day, a feed that never recorded its delivery ingested and never visible, and an event study anchored at the first session the news could be traded — excluded events named, no p-value |
| 51 | `51_alternative_data_provenance.py` | Alternative data of any category with a source identity, a version and the exact bytes: availability declared, derived by a stated rule or honestly unknown, late-arriving data on two clocks, revisions read as vintages, lineage through restrictions, and a knowledge frame joined to prices by a checked join before an IC is measured |
| 52 | `52_fundamental_research.py` | Statements as they were knowable: a date-only filing read at the next open, a restatement invisible until published with the original still readable, trailing twelve months refused for a balance, valuation and ratios with every undefined figure explained, a look-ahead price refused, and a point-in-time value factor |
| 53 | `53_regime_detection.py` | Regimes from declared rules with the caller's labels: a threshold with persistence, a trailing quantile that reads only the past, composites, a macro regime from point-in-time prints, a detector resumed from its recorded state reproducing one pass exactly, and a diagnostic conditioned on the regime |
| 54 | `54_adaptive_strategy.py` | An adaptive strategy on the execution path: a learning rule of four pure functions, the backtest ending in exactly the state a research replay reaches, cadence and freezing, the learned state in the run snapshot restored exactly and an edit refused, and a fingerprint naming the starting state |
| 55 | `55_reproducible_adaptive_replay.py` | Checkpoints restored in a second interpreter and continued to the uninterrupted state, a late observation refused and reprocessed from the last checkpoint before it, replays assessed as reproduced, of other inputs or diverged, and an adaptive backtest's manifest reproduced by a rerun |
| 56 | `56_portfolio_construction.py` | **The v3.8 construction path**: one stated risk model; minimum variance, mean-variance, maximum diversification, risk parity (equal and stated budgets) and robust mean-variance over the same constraints; a momentum-neutral, sector-capped rebalance with gross, turnover and notional limits; an infeasible set with its conflict named; Black–Litterman; identities that ignore listing order |
| 57 | `57_risk_budgeting.py` | A three-currency book's risk along asset, strategy, sector (from the instrument registry), country and currency, each adding up to the same volatility; a budget judged with a stated tolerance; a euro covariance refused for a dollar book; a shrunk risk model recorded as derived |
| 58 | `58_multi_strategy_portfolio.py` | Three strategies' own books in dollars, euros and yen as one book: every holding with its owners, crossed and opposing positions visible, valued in dollars and reconciled to the cent, every conversion recorded; euros refused until a cross is derived by name; valuations refused without rates or with a rate from the future |
| 59 | `59_cross_strategy_risk.py` | Strategies correlated by returns with the basis stated, and mixed currencies refused; overlap of holdings; factor crowding from the factor library's panels; common exposures by instrument, currency and sector; capital concentration and shared pools |
| 60 | `60_capital_allocation.py` | Capital across strategies, markets, two brokers, three accounts and three currencies: weights from a risk-parity construction, every account reconciled exactly, limits on five dimensions, refusals and stated `PRO_RATA` scaling, the allocation becoming a run's budget, and a fingerprint naming the construction and the capital |
| 61 | `61_broker_capability_model.py` | **The v3.9 execution contract**: three adapters' capabilities declared at the venue, market and account level where each is true; SUPPORTED, UNSUPPORTED and UNDECLARED kept apart; what an order needs derived from the order; a report per venue with every check's level and reason; contradictory declarations refused; identities that ignore listing order; the v3.5 deployment record projected from the same declaration, and refused where it would have to invent an answer |
| 62 | `62_normalized_execution_lifecycle.py` | One transition table read by the OMS and the venue boundary; every venue report classified once — applied, duplicate, stale, conflict, unknown order or invalid — through a fill before its acknowledgement, an amendment, a fill crossing a cancel, a refused cancel and a fill after the cancel landed; cancels and amendments with identities so a retry is never a second request; fills out of order converging; a reconnect and the snapshot reconciliation it demands |
| 63 | `63_execution_algorithms.py` | TWAP and VWAP as one construction with a stated urgency and whole increments by largest remainder; an incomplete volume profile refused or replaced by time and recorded; a schedule worked with top-up releases that catch up an expired child; participation of observed volume with minimum and maximum children and a stated end-of-window rule; slicing and an iceberg-like tranche; strategies carried by every child; identities in the research record |
| 64 | `64_smart_routing.py` | A route chosen from supplied venue evidence — quote, declared capabilities, cost model, latency — with every venue judged and explained; lowest all-in cost against best quoted price; one venue, a re-priced split and a partial route only where allowed; stale, look-ahead and foreign-currency quotes, a latency cap, a limit and an undeclared capability; INFEASIBLE kept apart from INSUFFICIENT_EVIDENCE; decision identities that ignore listing order |
| 65 | `65_execution_analytics.py` | **The whole contract end to end**: two strategies netted into one parent, worked by a VWAP, each child capability-checked and sent, every venue report normalized and settled on the parent; the mirror reconciled against the venue and the book against the mirror; implementation shortfall with its components split between the strategies, slippage against three named references, fill quality, latency across two clocks, rejection rate and venue quality; two currencies converted only at a stated rate; every identity reproduced by a rerun |

## Reading order

`01`–`10` date from v1.0.0 and exercise the **standalone** engine APIs, which is
the gentler introduction. `11`–`14` drive the wired paths and are where the
architecture actually shows:

- start at **11** for the execution path,
- **12** for the lifecycle path and how it joins the first,
- **13** for durability,
- **14** for money,
- **15** for where data comes from, and **16** for how it reaches a run.

`15` and `16` are the v3.1 pair and read best together: `15` ends with a dataset
version, and `16` starts from one and carries it into a `BacktestResult`.

`17`–`24` are the v3.2 sequence and are meant to be read in order, each building
on the last:

- **17** is the feature framework, and the one to read first.
- **18** adds the cross-section: ranking, neutralization, IC, decay, turnover,
  exposure.
- **19** asks whether the signal predicts anything, and at what horizon.
- **20** and **21** are validation — walk-forward, then cross-validation. Read
  20 first: 21 assumes purging is already familiar.
- **22** perturbs, **23** looks for overfitting, and **24** runs the whole thing
  once as a single reproducible study ending in evidence.

All eight ingest the **same** committed panel, so their numbers are directly
comparable with each other.

`46`–`49` are the v3.6 sequence and read in order: **46** gives the strategy an
identity, **47** records what one of its results was made from, **48** states
what is verifiably true of it, and **49** asks where it can run unchanged. Each
uses the identities the one before it produced.

`50`–`55` are the v3.7 sequence. **50** and **51** are the foundation — what an
event and an external observation are, and when each became knowable — and are
best read first. **52** applies the same rules to financial statements, **53**
detects regimes over any of it, and **54** and **55** are a pair: **54** builds
an adaptive strategy and runs it, **55** stops, moves, corrects and reruns it.

`56`–`60` are the v3.8 sequence and read in order: **56** constructs portfolios
from one stated risk model, **57** asks where a multi-strategy book's risk comes
from, **58** is that book — three strategies' own books valued as one across
three currencies — **59** compares the strategies with each other, and **60**
divides capital between them and hands each run its budget.

`61`–`65` are the v3.9 sequence and read in order: **61** states what a venue
can do, **62** what a venue's reports mean, **63** how a parent order is worked
in children, **64** where each one should go, and **65** runs all of it end to
end — strategy, order, algorithm, capability check, normalized venue reports,
reconciliation — before measuring what the execution cost. Read **65** last: it
is the one that shows the pieces are one contract.

`05_broker_connection.py` was rewritten in v2.17 against the canonical broker
boundary, having used `alphalab.integrations` until that package was removed
(ADR-0034).

---

## Datasets

`01`–`10` are supported by the small synthetic CSV files in `examples/data/`;
`02`, `07`, `08` and `10` read them directly. `11`–`14` build their own in-memory
data and use none of them. `15` reads `messy_ohlcv.csv`, which is broken on
purpose, and `16` reads `sample_ohlcv.csv`.

`17`–`24` all read `research_panel.csv` — ten names over two hundred daily
sessions, with weekends absent so the series is genuinely unevenly spaced — and
ingest it through the shared helper in `examples/_research_panel.py`. That
helper exists so the eight examples are about research rather than about
repeating eight lines of ingestion configuration; it fetches nothing and names
one file with one stated cleaning policy.

`25`–`30` build their own deterministic, hand-written data in memory and read no
file at all. That is deliberate: an execution cost, a capacity ceiling, an
attribution reconciliation and a scenario are all statements about numbers the
example states outright, so the reader can check the arithmetic rather than
trust a CSV. None of them uses a random number generator, so each prints the
same figures on every machine and every run.

`31`–`40` build their own data too, and go further: each **declares its own
calendars, conventions, venue specifications and FX rates**, because AlphaLab
ships none of them. A calendar in example 32 is a calendar that example 32
wrote; the one holiday it uses to move a settlement date is declared three lines
above the line that uses it. That is not a limitation of the examples — it is
the boundary the library draws, made visible.

`41`–`45` follow the same rule for the production side. `42` ingests its own
rows so the dataset assumption it records names bytes that actually exist;
`43`'s observations are readings the file states outright, because AlphaLab
observes nothing on its own; and the broker states in `44` and `45` are
deterministic fixtures standing in for whatever an application's adapter
produces. None of the five opens a connection, holds a credential or names a
venue.

`46`–`49` evaluate one strategy, set up once in `examples/_strategy_evidence.py`
for the reason `_research_panel.py` exists: so four examples are about
fingerprints, manifests, certification and portability rather than about
repeating setup. The strategy is registered in a real `StrategyClassRegistry`
and reads its sizes from its declared parameters, so the parameters a
fingerprint hashes are the ones that drive its orders; its dataset is ingested
from rows written in the file, with those rows' bytes recorded as the source so
its derived version names them. `47` also reads the committed research panel for
its study. Example 48's CPU and memory figures are measured on the machine that
runs it and are labelled `MEASURED` with that machine's description — every
other number in the four is deterministic.

`50`–`55` share one small world, written out in `examples/_point_in_time.py`:
six names trading on New York's real sessions in spring 2024 (with the Good
Friday holiday declared by the example), bars stamped at the 16:00 close, and
the external information around them — earnings releases, a daily news score,
quarterly statements and a restatement — as the rows a vendor file would hold,
each ingested with the bytes it stands for. Nothing is fetched and no vendor is
named. `54` and `55` run one adaptive strategy, set up once in
`examples/_adaptive_evidence.py` so the rerun in `55` builds exactly the
strategy `54` built. `55` writes its checkpoints to a temporary directory it
removes, and starts one second interpreter. Every figure the six print is
deterministic.

`56`–`60` share one small world, written out in `examples/_portfolio_world.py`:
eight instruments on three markets — New York in dollars, Frankfurt in euros,
Tokyo in yen — with sectors registered in an instrument registry from a named
source and countries from a second named file; sixty daily returns per
instrument **measured in dollars**, produced by a Park–Miller integer recurrence
written out in the file so every figure is the same on every machine; FX rates
from a named desk, with inverse directions derived and marked as derived; and
three strategies whose books are kept by the canonical accounting engine.
Instruments are identified by ticker for legible tables, except in `57`, which
reads sectors from the registry by canonical `asset_id` and says where it
re-keys. `60` also fingerprints the strategy `_strategy_evidence.py` defines.
Nothing is fetched and no vendor is named.

`61`–`65` share one small world, written out in `examples/_execution_world.py`:
one instrument listed in New York in dollars; three venues reached through three
adapters labelled `adapter-north`, `adapter-south` and `adapter-east`, each with a
capability declaration, a cost model of the v3.3 kind and a latency; one morning
in eight half-hour intervals with the volume a desk's profile expects, the
volume the tape printed (one half hour's print lost, and `None` rather than
zero) and the midpoint at each interval's start; and a desk of two strategies on
the canonical execution path with routing left `EXTERNAL`. The venue itself is
`ScriptedVenue`, a deterministic stand-in at the external boundary that records
a submission and does nothing else on its own: every acknowledgement, fill,
expiry, cancel and rejection is handed to AlphaLab as a normalized event, as an
application's adapter would. `65`'s second-currency order has its fills written
out in the file. Nothing is fetched, no clock is read, no vendor is named and no
credential exists; every figure the five print is deterministic.

See `examples/data/README.md` for what each dataset contains.

---

## What the examples are not

They demonstrate recommended usage patterns rather than every available API, and
they are deliberately concise. They are not performance benchmarks — those are in
`benchmarks/` — and the datasets are not suitable for research or production
trading.
