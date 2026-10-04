# AlphaLab Examples

## Overview

The examples directory contains complete, executable demonstrations of AlphaLab functionality.

Each example focuses on a single subsystem while following the same engineering principles used throughout the framework.

Examples are intended to be read sequentially by new users and used as reference implementations by contributors.

> Examples `01`–`10` were written for v1.0.0 and exercise the **standalone**
> engine APIs -- except `02` and `08`–`10`, rewritten in v3.11 against the
> canonical path after the packages they demonstrated (`alphalab.studio`,
> `alphalab.workbench`) left the library, and `10`, which until then printed
> check marks for steps it never ran. `11_unified_backtest.py` (v2.2) drives the integrated execution
> path end to end, `12_model_lifecycle.py` (v2.4) drives the model and strategy
> lifecycle, `13_durable_run_state.py` (v2.13) stops a run, stores it, and
> finishes it in a different process, and `14_multi_currency_settlement.py`
> (v2.17) drives an FX feed into a run that settles two currencies and reports
> in one. `05_broker_connection.py` was rewritten in v2.17 against the canonical
> broker boundary, having used `alphalab.integrations` until that package was
> removed. None are part of the automated test suite, though all sixty-five
> run as a release gate.
> For the integrated market-to-analytics path see
> `alphalab.backtesting`, `alphalab.runtime.ExecutionPipeline`, and their tests
> under `tests/integration/` and `tests/regression/`.

---

# Learning Path

The `examples/` directory contains:

| File | Description |
|------|-------------|
| `01_research.py` | Research engine |
| `02_backtest.py` | Your first backtest, through the canonical path with target quantities |
| `03_replay.py` | Historical replay cursor |
| `04_market_data.py` | Provider wire bars → normalization → canonical market state |
| `05_broker_connection.py` | The two broker boundaries: one venue, or a registry of many |
| `06_portfolio_optimizer.py` | Portfolio construction |
| `07_universal_data.py` | Universal Data Engine: state, versions and the catalogue |
| `08_strategy_studio.py` | Rebalancing to target weights, with a declared lot size |
| `09_workbench.py` | Cross-sectional decisions on complete instants (slices) |
| `10_complete_pipeline.py` | Research to a traded book, end to end |
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
- Regime analysis, and regime detection from declared rules (`53_regime_detection.py`)
- Event studies anchored where news could first be traded (`50_event_driven_research.py`)
- Capacity estimation

---

# Portfolio Optimization

`06_portfolio_optimizer.py` (v1) shows the closed forms — equal weight, inverse
volatility, minimum variance and maximum Sharpe — with the post-hoc weight
constraints, exposure, rebalancing and the cost estimate (which includes the
spread since v3.8). Earlier versions of this page listed risk parity here; the
v1 surface never had it.

`56_portfolio_construction.py` (v3.8) shows constrained construction: minimum
variance, mean-variance, maximum diversification, risk parity with equal and
stated budgets, robust mean-variance, a factor-neutral and sector-capped
rebalance with gross, turnover and notional limits, an infeasible constraint set
named, and Black–Litterman. See *Advanced portfolio and risk (v3.8)* below.

---

# Replay

Replay examples demonstrate deterministic historical simulation.

Topics include

- Event replay
- Historical execution
- Performance validation

---

# Market Data

No example calls a vendor, and AlphaLab ships no vendor client (ADR-0045).
`04_market_data.py` plays the part of a host application's provider: it
returns start-stamped one-minute bars from `request_history`, and AlphaLab
takes it from there.

Provider output arrives as a **wire record** (`alphalab.data.feed` — `float`
prices keyed by a provider symbol) and is lifted into the canonical domain model
by `alphalab.market.normalization` (`Decimal` prices keyed by a derived
`asset_id`). Those are two layers on opposite sides of one explicit conversion,
not two copies of one thing — see ADR-0011.

---

# The broker boundaries

`05_broker_connection.py` shows the two boundaries and which is which:

- `alphalab.broker` — **one** venue. `BrokerProtocol` is the canonical adapter
  contract; `PaperBroker` implements it (an adapter that reaches a real venue
  is the host application's — `tests/reference_adapter` shows one), and
  `runtime.broker_routing` and `LiveSession` speak it.
- `alphalab.brokers` — **many** venues and many accounts.
  `BrokerConnectorProtocol` routes over a `BrokerConnectorState`, which is why
  its queries take an `account_id` the single-venue boundary has no need of. Its
  domain values *are* the `alphalab.broker` types.

The named vendor adapters (Alpaca, Interactive Brokers, Zerodha) lived in
`alphalab.integrations` as canned-response stubs and were removed in v2.17; see
ADR-0034.

---

# Targets, lots and complete instants (`08`, `09`)

Two examples kept the names of packages that left the library in v3.11 —
`alphalab.studio` and `alphalab.workbench`, which held project management and
UI state (ADR-0046) — so that links to them still resolve. Both were rewritten
against the canonical path:

- `08_strategy_studio.py` — a rebalancer that states target **weights**
  (ledger FEA-001): each strategy's own position kept by allocation, lot sizes
  declared on the instrument, and rounding toward zero so a target is
  approached and never overshot.
- `09_workbench.py` — cross-sectional decisions on **complete instants**:
  slices deliver every record of an instant once (EXE-004), subscriptions decide
  what a strategy is dispatched (EXE-007), and a ranking never mixes two
  instants.

Projects, sessions, pipelines and dashboards are the host application's; the
reporting package's dashboard layouts went the same way in v3.12 (ADR-0047).

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

# Point-in-time research and adaptive strategies (v3.7)

Examples `50`–`55` are about **when information became knowable** and what a
strategy that learns from it did with it. All six read one small world written
out in `examples/_point_in_time.py` — six names on New York's real sessions,
earnings releases, a daily news score and quarterly statements with a
restatement, each ingested with the bytes it stands for — and `54` and `55`
run one adaptive strategy set up once in `examples/_adaptive_evidence.py`.
None fetches anything, names a vendor, reads a clock or draws a random number;
every figure they print is the same on every machine.

| # | Shows |
|---|---|
| 50 | One canonical event record; occurred, knowable, in-effect and ingested instants kept apart; after-close and pre-market releases placed in the session they can first be traded; a delivery the feed never recorded ingested and never visible; an event study anchored at the first tradable session, excluded events named, and no p-value |
| 51 | Alternative data of any category with source identity, version and content digest; availability declared, derived by a stated rule, or unknown and never read; late-arriving data on the publication and ingestion clocks; revisions as vintages; lineage through restrictions; a wire record lifted only with its timestamp's meaning declared; and a knowledge frame joined to prices by a checked join |
| 52 | Statements by fiscal period, filing and availability; a date-only filing read at the next open; a restatement invisible until published with the original still readable; trailing twelve months, and its refusal for a balance; valuation and ratios with undefined figures explained; a look-ahead price refused; restatement bias by name; a point-in-time value factor |
| 53 | Regime rules as declared data with the caller's labels: threshold with persistence, trailing quantile, composite, a macro regime from point-in-time prints; transitions and durations; a detector resumed from its recorded state reproducing one pass; and a signal diagnostic conditioned on the regime |
| 54 | An adaptive strategy through the execution path, ending in exactly the state a research replay reaches; cadence that observes every bar and adapts every fifth; train-then-freeze; the learned state in the run snapshot, restored exactly, an edited checkpoint refused; and a fingerprint that names the starting state |
| 55 | Checkpoints every ten observations restored in a second interpreter with another hash seed and working directory, continued to the uninterrupted state; a late observation refused and reprocessed from the last checkpoint before it; replays assessed `REPRODUCED`, `INPUTS_DIFFER` and `DIVERGED`; and an adaptive backtest's manifest reproduced by a rerun |

`50` assumes `16` and `32`. `51` assumes `15` and `17`. `52` assumes `18` and
`51`. `53` assumes `17` and `19`. `54` assumes `11` and `46`, and `55` assumes
`13`, `47` and `54`.

---

# Advanced portfolio and risk (v3.8)

Examples `56`–`60` are about **several strategies in one portfolio**: what to
own, where the risk comes from, what the strategies share and how much capital
each gets. All five read one small world written out in
`examples/_portfolio_world.py` — eight instruments on three markets in dollars,
euros and yen, sixty daily returns measured in dollars from a written-out
integer recurrence, FX rates from a named desk, and three strategies whose books
are kept by the canonical accounting engine. `60` also fingerprints the strategy
`examples/_strategy_evidence.py` defines. None fetches anything, names a vendor
or reads a clock; every figure they print is the same on every machine.

| # | Shows |
|---|---|
| 56 | One risk model with its currency, period, source and definiteness; minimum variance, mean-variance, maximum diversification and risk parity over one constraint set, with binding constraints and risk shares; risk parity with stated budgets and a budget that does not add up refused; robust mean-variance over an ellipsoid and a box; a momentum-neutral, sector-capped rebalance with gross, turnover and notional limits; an infeasible set with its conflict named and no weights; Black–Litterman from an equilibrium prior and one view; and identities that ignore listing order |
| 57 | A three-currency book valued in dollars; a euro covariance refused for a dollar book; sectors from the instrument registry and countries from a file; risk along asset, strategy, sector, country and currency, each summing to the same volatility; exposure, capital and risk side by side; a budget of maxima, minima and targets judged with a stated tolerance; each currency's exposure in that currency; and a shrunk risk model recorded as derived |
| 58 | Three strategies' own accounting states in dollars, euros and yen; one book with an identity; every instrument aggregated with its owners, crossed and opposing positions visible; the book valued in dollars and reconciled to the cent; every conversion recorded; reporting in euros refused until a yen/euro cross is derived by name; valuations refused without rates or with a rate from after the valuation; and a strategy removed or replaced as a new book |
| 59 | Strategies correlated by returns with the basis stated, and returns in two currencies refused; overlap of holdings in the same and opposite directions; factor crowding from the factor library's panels; common exposures by instrument, currency and sector; capital concentration and shared pools |
| 60 | Accounts at two brokers in three currencies with reservations read from the ledger; weights from a risk-parity construction carried into a plan with its identity; every placement allocated in its account's currency and every account reconciled exactly; capital along five dimensions and limits on each; refusals, and `PRO_RATA` scaling that is stated; an account's allocation as its run's budget, admitting exactly the allocation; and a fingerprint naming the construction and the capital |

`56` assumes `06` and `28`. `57` assumes `28` and `56`. `58` assumes `14` and
`40`. `59` assumes `18` and `58`, and `60` assumes `11`, `46` and `56`.

---

# The execution contract (v3.9)

Examples `61`–`65` are about **how an order reaches a venue and what it cost**:
what a venue can do, what a venue's reports mean, how a parent order is worked
in children, where each child should go, and what the execution achieved. All
five read one small world written out in `examples/_execution_world.py` — one
instrument, three venues behind three adapters that are labels, a morning in
eight half-hour intervals with expected and printed volume, and a desk of two
strategies on the canonical execution path with routing left `EXTERNAL`. The
venue is `ScriptedVenue`, a deterministic stand-in at the external boundary:
every acknowledgement, fill, expiry, cancel and rejection reaches AlphaLab as a
normalized `VenueEvent`, the way an application's adapter would hand it over.
None fetches anything, names a vendor, holds a credential or reads a clock;
every figure they print is the same on every machine.

| # | Shows |
|---|---|
| 61 | Capabilities declared at the venue, market and account levels; SUPPORTED, UNSUPPORTED and UNDECLARED kept apart, and UNDECLARED never read as yes; short selling and fractional quantities derived from the order; a compatibility report per venue with every check's dimension, level and reason; one flat capability answered for a stated scope; contradictory declarations refused; identities that ignore listing order; and the v3.5 deployment record projected from the same declaration — refused where it would have to invent an answer |
| 62 | The canonical transition table and how a reported status is judged against a held one; one order's reports classified — a fill before its acknowledgement, the late acknowledgement, a redelivered fill, an amendment and its retry, a fill crossing a cancel, a refused cancel, a second cancel, a fill after the cancel landed and an event for an unknown order; positions, balances, a disconnect and a reconnect requiring a resync; fills out of order converging; a snapshot reconciliation that agrees, one that finds the contested fill, and one taken too early to compare; and the OMS refusing by the same table |
| 63 | TWAP straight and front-loaded and VWAP on a stated profile, apportioned by largest remainder; an incomplete profile refused, or planned on time and recorded as such; a VWAP worked with top-up releases that catch up an expired child; participation of observed volume with a withheld child, an unobserved interval and both end-of-window policies; slicing by size and by count; an iceberg-like tranche with its hidden remainder, cancelled with a crossing fill still counted; and identities in the research record |
| 64 | Each venue's quote, declaration, cost model and latency; a limit buy routed whole or split and re-priced; best quoted price with a tie broken by latency; a stale quote, an unprofiled venue quoting in euros, a look-ahead quote, a latency cap, a limit outside the touch and an undeclared short-sale capability, each explained; INFEASIBLE, PARTIAL and INSUFFICIENT_EVIDENCE told apart; decision identities that ignore listing order; and the policy in the research record |
| 65 | Two strategies netted into one parent under `EXTERNAL` routing; a VWAP working it; a capability refusal before anything is sent; each child routed and its venue reports applied to the mirror and settled on the parent, a partial expiry caught up and a redelivered fill booking nothing; a second order rejected by the venue and its reservation freed; the mirror reconciled against the venue's snapshot and the book against the mirror, children included, and a planted break found; implementation shortfall with delay, trading, explicit and opportunity components split between the strategies; slippage against three named references; fill quality; latency across two clocks; rejection rate and venue quality; a euro order's shortfall kept apart and converted only at a stated rate, sterling refused; and every identity reproduced by a rerun |

`61` assumes `05` and `42`. `62` assumes `05`, `45` and `61`. `63` assumes `25`,
`26` and `62`. `64` assumes `25` and `61`, and `65` assumes `45` and `61`–`64`.

---

# Additional engines

The feature store, machine learning, cloud research and other engines added
in v1.34.0–v2.0.0 do not yet have dedicated example scripts.
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

v3.7 added `50`–`55` for point-in-time research and adaptive strategies. The
**alternative data** package, which had none, has four (`50`–`53`); the
**strategy** package's adaptive engine has two (`54`, `55`).

v3.8 added `56`–`60` for portfolio construction and risk. The
**portfolio optimizer**, which had one example (`06`), has two; `analytics`,
`portfolio` and `allocation` each gained examples for their v3.8 surfaces.

v3.9 added `61`–`65` for the universal execution contract. The **execution**
package's algorithms, routing and quality measurements have their first
examples (`63`–`65`), and the **broker** boundary, which had `05` and `45`, gains
two (`61`, `62`) for its capability model, normalized lifecycle, request
identities and snapshot reconciliation.

---

# Philosophy

Examples are intentionally concise.

They demonstrate recommended usage patterns rather than every available API.

They should remain synchronized with the public interfaces of AlphaLab.