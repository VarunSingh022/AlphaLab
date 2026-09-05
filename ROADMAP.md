# AlphaLab Roadmap

This document tracks the evolution of AlphaLab.

The roadmap is a guide rather than a strict schedule. Priorities may change based
on community feedback and project needs.

---

# Delivered

PR-034 through PR-050 are complete. Each PR shipped as its own minor/major release
and each package is a standalone, individually tested engine. The feature notes
below are kept as a historical record of scope.

| PR | Package | Shipped in |
|----|---------|------------|
| PR-034 | Feature Store | v1.34.0 |
| PR-035 | Factor Library | v1.35.0 |
| PR-036 | Options Engine | v1.36.0 |
| PR-037 | Futures Engine | v1.37.0 |
| PR-038 | Crypto Engine | v1.38.0 |
| PR-039 | Macro Engine | v1.39.0 |
| PR-040 | Alternative Data | v1.40.0 |
| PR-041 | Machine Learning | v1.41.0 |
| PR-042 | Deep Learning | v1.42.0 |
| PR-043 | Reinforcement Learning | v1.43.0 |
| PR-044 | Cloud Research | v1.44.0 |
| PR-045 | Cluster Scheduler | v1.45.0 |
| PR-046 | Experiment Tracking | v1.46.0 |
| PR-047 | Model Registry | v2.0.0 |
| PR-048 | AI Research Assistant | v2.0.0 |
| PR-049 | Deployment Manager | v2.0.0 |
| PR-050 | AlphaLab Enterprise | v2.0.0 |

v2.0.0 also unified the canonical execution-path domain models (R1–R4) and fixed
two portfolio/analytics defects (D1/D2). See `CHANGELOG.md`.

v2.1.0 — "Execution + Portfolio Correctness" — added mark-to-market to the
execution pipeline, separated the portfolio's cash / realized P&L / unrealized
P&L / commission accounting behind an explicit accounting identity, made
execution invariants (unpriced requests, terminal rejected orders, per-fill P&L
attribution) explicit, and fixed the O(N^2) event/history accumulation that had
prevented `benchmark_risk_engine.py` from completing. No new packages. See
`CHANGELOG.md`.

v2.2.0 — "Unified Backtesting + Replay" — turned the existing engines into one
deterministic dataset → analytics workflow, and closed the four v2.1 limitations
that blocked it:

- `alphalab.backtesting` composes `ExecutionPipeline` into a real backtest.
  There is no backtest-only order, fill or portfolio model.
- `alphalab.replay` now drives that same path, so a replay produces orders,
  fills and P&L identical to the equivalent backtest (ADR-0010).
- The OMS order book moved onto persistent containers: the 100k-order benchmark
  went from ~16 minutes to 7.5s, and from quadratic to linear.
- Allocation reservations became a per-order ledger, released exactly once.
- `OMSState` gained a complete, round-trippable snapshot projection.
- Identifiers became seedable, so a seeded run reproduces field for field.

One new package (`backtesting`), which is an integration package, not another
standalone engine. See `CHANGELOG.md`.

v2.3.0 — "Market Data + Broker/Live Execution" — closed both items v2.2
deferred, and is a connectivity/convergence release rather than a feature one:

- **One canonical market-data model.** `alphalab.market` is the domain model the
  execution path consumes; `alphalab.data.feed` is the one wire record, which
  `alphalab.marketdata.feed` and `alphalab.live.message` now re-export instead
  of redefining. `data.Bar` and `market.Bar` both remain, deliberately: they sit
  on opposite sides of a conversion (ADR-0011).
- **An explicit normalization boundary.** `alphalab.market.normalization` states
  its rules for precision, timestamps, symbol identity, and what happens to
  data that is invalid, unreported, or stale.
- **One canonical broker boundary.** `alphalab.broker` defines the vocabulary
  and the adapter contract; `alphalab.brokers` routes those types instead of
  redefining four of them. Reconciliation gives defined answers to duplicate
  fills, out-of-order fills, unknown fills, overfills and the cancel/fill race.
- **Four environments, one execution path.** Backtest, replay and paper produce
  byte-identical fills, orders, cash, positions and equity curve.
  `ExecutionRouting.EXTERNAL` is live's only genuine difference.
- **The broker and market-data layers stopped being quadratic.** The 100k-order
  PaperBroker benchmark went from 676.70s to 4.65s; market-data ingestion no
  longer slows down as the universe widens.

No new engine packages. See `CHANGELOG.md`, ADR-0011 and ADR-0012.

**v2.3 does not add live trading.** It adds the adapter contract a live venue
would be reached through. There is no connectivity to any real venue in this
repository, and every vendor client is a stub.

---

# Feature notes (delivered)

Scope notes for each delivered PR. See the table above for the release each shipped in.

## PR-034

Feature Store

Institutional feature engineering framework

Features

- Feature registry
- Versioning
- Metadata
- Feature validation
- Feature caching

---

## PR-035

Factor Library

Reusable quantitative factors

Examples

- Momentum
- Value
- Quality
- Carry
- Volatility
- Liquidity

---

## PR-036

Options Engine

Support for

- Option chains
- Greeks
- Volatility surfaces
- Pricing models
- Strategy simulation

---

## PR-037

Futures Engine

Support for

- Continuous contracts
- Rolls
- Curve analysis
- Calendar spreads

---

## PR-038

Crypto Engine

Support for

- Spot
- Futures
- Perpetuals
- Funding rates
- Exchange normalization

---

## PR-039

Macro Engine

Support for

- Economic indicators
- Central bank events
- Yield curves
- Inflation
- GDP

---

## PR-040

Alternative Data

Examples

- News
- Sentiment
- Satellite
- Shipping
- ESG
- Credit cards

---

## PR-041

Machine Learning

Features

- Feature pipelines
- Training
- Cross validation
- Prediction
- Evaluation

---

## PR-042

Deep Learning

Support for

- LSTM
- Transformers
- CNN
- Sequence models

---

## PR-043

Reinforcement Learning

Features

- Trading environments
- Policy optimization
- Agent evaluation

---

## PR-044

Cloud Research

Distributed quantitative research

Features

- Remote execution
- Worker pools
- Cluster management

---

## PR-045

Cluster Scheduler

Support for

- Job scheduling
- Queue management
- Distributed orchestration

---

## PR-046

Experiment Tracking

Features

- Experiment history
- Metrics
- Parameters
- Versioning

---

## PR-047

Model Registry

Features

- Versioning
- Promotion
- Rollback
- Deployment metadata

---

## PR-048

AI Research Assistant

Capabilities

- Strategy generation
- Research automation
- Report generation
- Workflow orchestration

---

## PR-049

Deployment Manager

Features

- Packaging
- Release management
- Rollbacks
- Production deployment

---

## PR-050

AlphaLab Enterprise

Enterprise capabilities

- Authentication
- RBAC
- Audit logging
- Collaboration
- Multi-user workspaces
- Compliance
- Secrets management

---

# Not yet addressed

The engine packages exist and are individually tested. The following integration
and consolidation work has **not** been done:

- **Live venue connectivity** (v2.4+): v2.3 built the adapter contract, the
  routing gates, reconciliation and the fill-return path, and tested all of
  them. What does not exist is a transport to any real venue. Every vendor
  client in `alphalab.marketdata.*` and `alphalab.integrations.*` is a stub —
  canned responses or `NotImplementedError`. An async live session loop,
  order-state polling and reconnect scheduling all wait on that transport.
- A single integrated runtime spanning *all* engines. Today `ExecutionPipeline`
  (`alphalab.runtime`), `alphalab.backtesting` and
  `alphalab.runtime.session`, which drive it, are the wired-together paths;
  research, reporting, feature store and the rest remain standalone libraries.
- Resolution of `kernel` and `core/events` (currently unused by the execution path).
- Strategies still do not see the marked portfolio: `StrategyContext` comes from
  the caller's `context_factory`.
- Multi-currency valuation (`PortfolioValuation` / `NAVCalculator` value the base
  currency only).
- `_trade_record` attribution in `execution_pipeline` still hard-codes
  `sector_id="UNCLASSIFIED"` / `holding_period_seconds=0.0` ("D3").

- `benchmark_workbench.py` fails on a tab-lifecycle assertion in
  `alphalab.workbench`. Pre-existing at v2.2.0 and unrelated to the execution
  path.

Delivered since this list was written: mark-to-market position repricing (v2.1),
`alphalab.replay` integration with the execution path (v2.2), and market-data /
broker convergence with paper execution on the canonical path (v2.3).

---

# Long-Term Vision

AlphaLab aims to become a complete quantitative research platform covering market
data, feature engineering, research, portfolio construction, machine learning,
production trading, cloud infrastructure, and enterprise deployment — while
preserving its core engineering principles of determinism, immutability, modular
architecture, event-driven design, and production readiness.

Reaching that vision requires the integration work listed under **Not yet
addressed**, not just additional engine packages.

---

# Versioning

Major releases introduce architectural milestones or breaking public API changes
(v2.0.0 unified the canonical execution domain models).

Minor releases may still make small, documented breaking changes to a narrow
public API where correctness requires it: v2.2.0 changed
`AllocationEngine.release_reservation` to take no amount, because the reservation
ledger owns it, and v2.3.0 changed `alphalab.brokers`' account, order and
execution field names so both broker packages speak one vocabulary.

Minor releases introduce new capabilities (v1.34.0–v1.46.0 each added one engine).

Patch releases focus on stability, bug fixes, and performance improvements.

---

# Community

The roadmap will continue evolving as AlphaLab grows.

Community feedback and contributions will play an important role in shaping future development.