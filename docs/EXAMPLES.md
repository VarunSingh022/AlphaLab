# AlphaLab Examples

## Overview

The examples directory contains complete, executable demonstrations of AlphaLab functionality.

Each example focuses on a single subsystem while following the same engineering principles used throughout the framework.

Examples are intended to be read sequentially by new users and used as reference implementations by contributors.

> Examples `01`–`10` were written for v1.0.0 and exercise the **standalone**
> engine APIs. `11_unified_backtest.py` (v2.2) drives the integrated execution
> path end to end, `12_model_lifecycle.py` (v2.4) drives the model and strategy
> lifecycle, `13_durable_run_state.py` (v2.13) stops a run, stores it, and
> finishes it in a different process, and `14_multi_currency_settlement.py`
> (v2.17) drives an FX feed into a run that settles two currencies and reports
> in one. `05_broker_connection.py` was rewritten in v2.17 against the canonical
> broker boundary, having used `alphalab.integrations` until that package was
> removed. None are part of the automated test suite, though all forty-nine run
> as a release gate.
> For the integrated market-to-analytics path see
> `alphalab.backtesting`, `alphalab.runtime.ExecutionPipeline`, and their tests
> under `tests/integration/` and `tests/regression/`.

---

# Learning Path

The `examples/` directory contains:

| File | Description |
|------|-------------|
| `01_research.py` | Research engine |
| `02_backtest.py` | Strategy Studio backtest bookkeeping |
| `03_replay.py` | Historical replay cursor |
| `04_market_data.py` | Market data providers |
| `05_broker_connection.py` | The two broker boundaries: one venue, or a registry of many |
| `06_portfolio_optimizer.py` | Portfolio construction |
| `07_universal_data.py` | Universal Data Engine: state, versions and the catalogue |
| `08_strategy_studio.py` | Strategy Studio orchestration |
| `09_workbench.py` | Workbench workspace |
| `10_complete_pipeline.py` | Multi-engine walkthrough |
| `11_unified_backtest.py` | Dataset → orders → fills → P&L → analytics, plus replay parity |
| `12_model_lifecycle.py` | Research candidate → deployment → rollback |
| `13_durable_run_state.py` | Stop a run, store it, continue it in another process |
| `14_multi_currency_settlement.py` | FX feed → two settlement currencies → one reported figure |
| `15_data_ingestion.py` | A **deliberately broken CSV** through detection, validation, an explicit cleaning policy and a quality report, out as a versioned canonical dataset |
| `16_research_from_dataset.py` | Research access by dataset version, point-in-time selection, and a backtest that names the exact bytes it read |

---

# Universal Data

Learn how AlphaLab transforms heterogeneous market data into canonical datasets.

Topics include

- CSV ingestion (`15_data_ingestion.py`)
- JSON ingestion, via `alphalab.api.ingest_rows` — a caller parses JSON with the
  standard library and hands the rows to the same pipeline a file takes
- Schema detection, and the ambiguities it refuses to resolve
- Column mapping through the one alias table
- Symbol normalization
- Timestamp normalization, with explicit timezones

---

# Research

Examples demonstrate

- Performance metrics
- Walk-forward analysis
- Bootstrap statistics
- Monte Carlo simulation
- Regime analysis
- Capacity estimation

---

# Portfolio Optimization

Examples include

- Equal Weight
- Risk Parity
- Maximum Sharpe
- Minimum Variance
- Constraint handling
- Rebalancing

---

# Replay

Replay examples demonstrate deterministic historical simulation.

Topics include

- Event replay
- Historical execution
- Performance validation

---

# Market Data

Examples demonstrate

- Yahoo Finance
- Polygon
- Databento
- Binance
- NSE

Provider output arrives as a **wire record** (`alphalab.data.feed` — `float`
prices keyed by a provider symbol) and is lifted into the canonical domain model
by `alphalab.market.normalization` (`Decimal` prices keyed by a derived
`asset_id`). Those are two layers on opposite sides of one explicit conversion,
not two copies of one thing — see ADR-0011.

---

# The broker boundaries

`05_broker_connection.py` shows the two boundaries and which is which:

- `alphalab.broker` — **one** venue. `BrokerProtocol` is the canonical adapter
  contract; `RestVenueBroker` and `PaperBroker` implement it, and
  `runtime.broker_routing` and `LiveSession` speak it.
- `alphalab.brokers` — **many** venues and many accounts.
  `BrokerConnectorProtocol` routes over a `BrokerConnectorState`, which is why
  its queries take an `account_id` the single-venue boundary has no need of. Its
  domain values *are* the `alphalab.broker` types.

The named vendor adapters (Alpaca, Interactive Brokers, Zerodha) lived in
`alphalab.integrations` as canned-response stubs and were removed in v2.17; see
ADR-0034.

---

# Strategy Studio

Examples demonstrate complete research workflows.

Typical pipeline

```

Acquire Data

↓

Normalize Dataset

↓

Research

↓

Portfolio Optimization

↓

Replay

↓

Reporting

```

---

# Workbench

Workbench examples illustrate

- Projects
- Sessions
- Pipelines
- Reports
- Dashboards

---

# Research and validation (v3.2)

Examples `17`–`24` cover the strategy research path added in v3.2. All eight
read the same committed panel, `examples/data/research_panel.csv`, so their
numbers are comparable with each other.

| # | Shows |
|---|---|
| 17 | Typed feature definitions, derived identity, warmup, missing-data policy, lineage, and the Feature Store seam |
| 18 | Ranking, three neutralizations, the information coefficient, decay, turnover, exposure |
| 19 | Forward returns across horizons, quantile profiles, monotonicity, regime conditioning |
| 20 | Walk-forward: train / validate / test / roll, and purging on both sides |
| 21 | Rolling, expanding, purged and embargoed cross-validation |
| 22 | Seeded robustness perturbations |
| 23 | Parameter sweeps, sensitivity, degradation, stability, multiple testing |
| 24 | The complete pipeline, ending in evidence and a promotion gate |
| 25 | Execution simulation: six cost roles, two settlements, a stated ordering |
| 26 | Capacity modelling: the binding constraint, and the sensitivities |
| 27 | Portfolio attribution: nine dimensions, reconciled, with availability reported |
| 28 | Risk decomposition: named VaR methods and contributions that sum |
| 29 | Portfolio stress testing: synthetic shocks, and historical scenarios as contracts |
| 30 | The scenario engine: one contract, many portfolio shapes |

Read them in order: each assumes the one before it.

---

# Global markets and multi-asset research (v3.4)

Examples `31`–`40` cover the market-convention and multi-asset surfaces added in
v3.4. Each declares its own calendars, conventions, prices and rates — AlphaLab
ships none of them — and none uses a random number generator, so each prints the
same figures on every machine and every run.

| # | Shows |
|---|---|
| 31 | Market conventions: tick size against tick value, lot grids, the multiplier applied once, quote against settlement currency |
| 32 | Four venues in four timezones: local days, overnight sessions, lunch breaks, daylight saving, and the settlement dates they produce |
| 33 | Contract chains, three roll rules producing three schedules, curve shape, and margin as a published figure |
| 34 | The four things a continuous series is built from, three adjustment methods, and a missing roll print refused |
| 35 | Chains, Greeks and the four things the model does not do; multipliers that are not 100; expiry as exercised, assigned, abandoned or worthless |
| 36 | The implied-volatility round trip, the five ways a quote has no answer, and a surface that reports every refusal |
| 37 | Quotation direction, cross rates through a named currency, covered-parity forwards, the look-ahead guard, and currency attribution |
| 38 | Two crypto venues that disagree about everything; funding accrual against the venue's mark; a 24/7 clock against 24/7 data |
| 39 | Bond cash flows, clean against dirty, the yield inversion, duration in years and convexity in years squared — and the boundary |
| 40 | Five asset classes, four venues, four currencies, one book: every seam checked |

`31`, `32` and `37` stand alone. `33`–`34` and `35`–`36` are pairs, and `40`
assumes `31`, `33` and `37`.

---

# Strategy execution and production intelligence (v3.5)

Examples `41`–`45` cover what happens after a strategy is deployed. `42`
ingests its own rows so the dataset assumption it records names bytes that
actually exist; `44` runs a real backtest and a real paper run through the same
canonical step; `45` reconciles a real backtest's execution state. The broker
states in `44` and `45` are deterministic fixtures in the files — none of the
five opens a connection, holds a credential or names a venue.

| # | Shows |
|---|---|
| 41 | The eight stages from research to archived, every refusal and its reason, a pause that returns to the stage it interrupted, and the progression checked against the registry's own stage rather than replacing it |
| 42 | What a strategy version needs to run **as it was researched**: dataset assumptions by derived identity, the risk limits the pre-trade gate enforces, broker capabilities with no vendor anywhere, a content digest that stops verifying when edited, and the coherence a single field cannot see |
| 43 | Seven health categories from **supplied** observations: at, past and before the threshold; a reconnecting adapter warning where a dead one breaches; and why a clean report with something unevaluated is `UNKNOWN` |
| 44 | A backtest, a paper run and a venue's records compared: declared alignment, stated tolerances, money per currency, a venue's unmeasured slippage staying missing, and three pairs so a divergence can be located |
| 45 | Fourteen mismatch classes between the book and the mirror: a lost fill, an order the venue never held, one nobody routed, a resized position, venue symbols joined through a supplied mapping, and a currency the account cannot speak about |

`41` assumes `12`. `42` assumes `15` and `41`. `43` assumes `42`. `44` assumes
`11`, `25` and `43`, and `45` assumes `05`, `11` and `44`.

---

# Strategy evaluation (v3.6)

Examples `46`–`49` make one strategy version evaluable by somebody who did not
write it — the evidence contracts an external research marketplace consumes,
with none of its logic in AlphaLab. All four evaluate the **same** strategy,
set up once in `examples/_strategy_evidence.py`: a parameter-driven strategy
registered in a real `StrategyClassRegistry`, and a dataset ingested from rows
written in the file with their own bytes recorded as its source. None opens a
connection, holds a credential, names a venue or reads the installed
environment to decide an identity.

| # | Shows |
|---|---|
| 46 | A strategy fingerprint over five defining inputs — code, dependencies (with a declared completeness), parameters, research configuration, engine — each changing the identity on its own; presentation that does not; a path naming a machine refused; and the same fingerprint from a second interpreter with another hash seed |
| 47 | A reproducibility manifest naming everything a result was made from; a rerun that reproduces it, one of other inputs, and one whose simulator cost changed without the record noticing; an unseeded run refused; and a study with its absent seed stated |
| 48 | Eight certification properties from no evidence (all `NOT_ASSESSED`) to complete evidence (all earned), then `FAIL` and `INSUFFICIENT_EVIDENCE` each for a reason — with CPU and memory measured on the running machine and labelled so |
| 49 | One fingerprint across research, paper and two brokers declared as capabilities: every requirement satisfied, blocked, unverified or not applicable; a retuned deployment caught; and the same code filling the same way in two environments |

`46` assumes `12` and `42`. `47` assumes `16`, `24` and `46`. `48` assumes
`42`, `43`, `46` and `47`, and `49` assumes `11`, `42` and `46`.

---

# Additional engines

The feature store, machine learning, cloud research, enterprise, and other
engines added in v1.34.0–v2.0.0 do not yet have dedicated example scripts.
Their usage is covered by the unit tests under `tests/unit/<package>/` and by
the benchmarks under `benchmarks/`.

The **factor library** did not have one either until v3.2, which gave it
examples `17` and `18`.

v3.3 added examples `25`–`30` for the institutional surfaces: the execution cost
contract, capacity, attribution, risk decomposition, stress testing and the
scenario engine. They build their data in memory and use no RNG, so each prints
the same figures on every machine.

v3.4 added `31`–`40` for the global-market surfaces, on the same terms. The
**conventions** package, added in that release, has examples `31` and `40`; the
**futures**, **options**, **crypto** and **macro** engines gained their first
dedicated examples there too, having been covered only by tests and benchmarks
since the v1 engine series.

v3.5 added `41`–`45` for the production surfaces, all inside
`alphalab.lifecycle`, which had one example (`12`) and now has six.

v3.6 added `46`–`49` for strategy evaluation, again all inside
`alphalab.lifecycle`, which now has ten.

---

# Philosophy

Examples are intentionally concise.

They demonstrate recommended usage patterns rather than every available API.

They should remain synchronized with the public interfaces of AlphaLab.