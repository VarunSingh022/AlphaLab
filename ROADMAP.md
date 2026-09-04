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
and consolidation work has **not** been done and is not currently scheduled:

- A single integrated runtime spanning all engines. Today `ExecutionPipeline`
  (`alphalab.runtime`) is the only wired-together path — market → strategy →
  allocation → risk → OMS → execution simulator → portfolio → analytics.
- `alphalab.replay` integration with the execution path. Replay is a standalone
  engine and does not drive `ExecutionPipeline`.
- Mark-to-market position repricing.
- Consolidation of the overlapping data surfaces (`data` / `marketdata` / `feed`),
  the separate `data.feed.Bar` vs `market.bar.Bar` types, and `broker` / `brokers`.
- Live broker connectivity into `ExecutionPipeline`.
- Resolution of `kernel` and `core/events` (currently unused by the execution path).
- `_trade_record` attribution in `execution_pipeline` still hard-codes
  `sector_id="UNCLASSIFIED"` / `holding_period_seconds=0.0` ("D3").

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

Minor releases introduce new capabilities (v1.34.0–v1.46.0 each added one engine).

Patch releases focus on stability, bug fixes, and performance improvements.

---

# Community

The roadmap will continue evolving as AlphaLab grows.

Community feedback and contributions will play an important role in shaping future development.