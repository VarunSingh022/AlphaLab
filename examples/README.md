# AlphaLab Examples

Forty runnable scripts, each demonstrating one part of AlphaLab against
its real public API. Every one of them runs:

```bash
python examples/01_research.py
```

They are **not** part of the automated test suite — the suite covers the same
paths far more thoroughly under `tests/` — but all forty are executed as a
release gate, and a change that breaks one is a change that breaks a documented
API.

---

## The examples

| # | File | What it shows |
|---|------|---------------|
| 01 | `01_research.py` | The research engine: a payload in, an immutable `ResearchState` and a score out |
| 02 | `02_backtest.py` | Strategy Studio's backtest bookkeeping over the sample datasets |
| 03 | `03_replay.py` | The replay cursor: sessions, chronological validation, replay metrics |
| 04 | `04_market_data.py` | Registering market-data providers and reading provider state |
| 05 | `05_broker_connection.py` | The **two** broker boundaries — one venue (`BrokerProtocol`), or a registry of many (`BrokerConnectorProtocol`) |
| 06 | `06_portfolio_optimizer.py` | Portfolio construction, constraints, exposure and rebalancing |
| 07 | `07_universal_data.py` | The Universal Data Engine: ingestion, schema, quality, catalogue |
| 08 | `08_strategy_studio.py` | Strategy Studio orchestration: projects, strategies, pipelines, reports |
| 09 | `09_workbench.py` | The Workbench workspace: projects, tabs, dashboards |
| 10 | `10_complete_pipeline.py` | A multi-engine walkthrough, composing the standalone engines by hand |
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

See `examples/data/README.md` for what each dataset contains.

---

## What the examples are not

They demonstrate recommended usage patterns rather than every available API, and
they are deliberately concise. They are not performance benchmarks — those are in
`benchmarks/` — and the datasets are not suitable for research or production
trading.
