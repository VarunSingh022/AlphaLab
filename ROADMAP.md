# AlphaLab Roadmap

This document states what AlphaLab has delivered, what it deliberately does not
do, what it depends on from outside, and what remains genuinely open.

As of **v3.0.0** the architecture is frozen. That changes what a roadmap is for:
it is no longer a queue of structural work, because the v3.0 audit established
that there is no known internal problem requiring AlphaLab to be refactored. What
remains is classified below, and each class means something different.

| Class | Meaning |
| --- | --- |
| **Delivered** | Built, tested, and described by the documentation |
| **Deliberate boundary** | Not built, on purpose, with a reason and usually a regression test |
| **External dependency** | Not AlphaLab's engineering to do — data, credentials, a vendor's API |
| **Optional future evolution** | Could be built; no commitment; nothing depends on it |

Nothing in the last three classes is a defect, and none of them blocks a release.

---

# Delivered

## The engine series (v1.34.0 – v2.0.0)

Each shipped as its own release, and each package is a standalone, individually
tested engine. The scope notes are kept below as the historical record.

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

## The v2 line

Full detail is in `CHANGELOG.md`; the reasoning is in `docs/ADR/`. Each entry
names what the release established and the ADR that records the decision.

| Release | Established | ADR |
| --- | --- | --- |
| **v2.0.0** | Canonical execution domain models: one `Side`, one `OrderRequest`, one lifecycle `Order`, float timestamps. Four new standalone packages | ADR-0008, ADR-0009 |
| **v2.1.0** | Mark-to-market; the **exact** accounting identity and one rounding policy; O(1) amortized engine histories | — |
| **v2.2.0** | Backtest and replay on **one** step, so parity is structural; persistent containers; per-order reservation ledger; seeded identifiers | ADR-0010 |
| **v2.3.0** | One canonical market-data model over one wire record with an explicit normalization boundary; one broker boundary that `brokers` routes; four environments, one path | ADR-0011, ADR-0012 |
| **v2.4.0** | The model and strategy lifecycle: numbered `StrategyVersion`, typed refs, evidence with content-derived ids, a gated promotion, the deployment ledger as the one source of truth | ADR-0013 |
| **v2.5.0** | Typed `capture` / `restore` over typed decoders; `market.provider` connecting a provider's history to a session; explicit unordered-source semantics; partial fills terminate | ADR-0014 |
| **v2.6.0** | Allocation authority: outstanding capital enforced, terminal orders release what they hold, real strategy attribution replacing `"ALLOC-NETTED"` | ADR-0015 |
| **v2.7.0** | Instrument identity derived (`uuid5` over a canonical key) rather than asserted; dataset provenance derived from the run rather than claimed | ADR-0016, ADR-0017 |
| **v2.8.0** | One meaning per currency field; a valuation that refuses to span two currencies; a run that records why it produced no fills | ADR-0019 – ADR-0021 |
| **v2.9.0** | Deterministic identifier continuation; the execution path round-trips; a duplicate venue fill applies once; a working external order can be ended | ADR-0022 – ADR-0024 |
| **v2.10.0** | `StrategyStateProtocol` and a two-sided codec; `StrategyContext` populated from the **marked** portfolio, with contribution-based order shares | ADR-0025, ADR-0026 |
| **v2.11.0** | The security master's mechanism: `classify_instrument` writes a sector without touching identity, and the pipeline freezes it onto each fill | ADR-0027 |
| **v2.12.0** | The instrument registry becomes the **currency authority**. Two seams: `_process_requests` drops a foreign instrument, `_apply_report_to_portfolio` refuses a mismatched report. `STRICT_MATCH` and no policy object | ADR-0028 |
| **v2.13.0** | `RunStateStore` as the one owner of durable run state — payload-agnostic, one real file backend, one named double, no fallback. Cross-process continuation proven byte-identical | ADR-0029 |
| **v2.14.0** | Runtime unification: `RunEngine` over `RunState` owns the run; `ExecutionPipeline` keeps the step; the drivers hold no state | ADR-0030 |
| **v2.15.0** | Five capabilities that had a contract and nothing behind it: venue transport, WebSocket streaming, artifact bytes, classification provenance, the completed strategy context | ADR-0031 |
| **v2.16.0** | Three joins: the live driver, governance/RBAC/audit, and FX valuation — plus the refactor audit that classified twenty-one structural findings | ADR-0032, ADR-0033 |
| **v2.17.0** | Settlement-level multi-currency, the FX rate feed, the strategy-class registry; seven deprecated surfaces removed with no aliases; zero skips and zero warnings | ADR-0034, ADR-0035 |

## v3.0.0 — the stable release

v3.0.0 adds no capability and moves no boundary. It is the point at which:

- the architecture is **frozen** — the invariants, ownership boundaries and
  schema contracts in `nowandfuture.md` are the ones AlphaLab intends to keep;
- the repository **describes itself truthfully** — every current-facing document
  matches the code, and historical records are labelled as historical;
- the remaining open items are **classified** rather than queued, which is what
  the rest of this document is.

The v2.17 release exists so that this one could be additive: everything that
would have been a breaking removal in v3.0 was taken a release early.

---

# Deliberate boundaries

Not built, on purpose. Each has a reason, and most have a regression test that a
future "simplification" would have to break first —
`tests/regression/test_shared_names_stay_distinct.py` and
`test_venue_concepts_stay_distinct.py` hold the reasons.

- **No single runtime spanning all engines.** The run layer has one owner
  (`RunEngine` over `ExecutionPipeline`, four drivers), and `alphalab.lifecycle`
  is joined to it by a query with a refusal. Reporting, the feature store, the
  factor library and the rest stay standalone: ADR-0009 is a description of the
  codebase that became a rule, and chaining them would create a runtime nobody
  asked for.
- **No allocation visibility inside `StrategyContext`.** Reservations and
  contributions are *post*-intent facts. Showing a strategy the capital its own
  intent will later reserve invites it to pre-size, duplicating the allocation
  engine's authority (ADR-0015). The contribution ledger is read for attribution
  only.
- **No depth hook on `StrategyProtocol`.** `BookUpdated` and `SnapshotCreated`
  reach no hook. Delivering an `OrderBookSnapshot` to `on_quote` would hand
  existing strategies a payload with no `quote`, and the canonical record path
  cannot produce either event in any case. A stated boundary, pinned by the
  routing test (ADR-0032).
- **No per-environment promotion policy.** A strategy version has one stage
  across all environments and `PRODUCTION` means "live somewhere". A policy that
  differs between `paper` and `live-eu` is not expressible, and making it so
  would put a second source of truth beside the deployment ledger.
- **No `SettlementPolicy` object.** `STRICT_MATCH` is the only settlement rule
  because no alternative exists: a permissive mode could only book honestly —
  making the book mixed, which the next valuation refuses without rates — or
  convert, which is FX and is a deliberate act with a recorded rate (ADR-0028,
  ADR-0035).
- **No triangulation, no implicit inversion, no default rate.** A configured rate
  is an invented one. `with_inverses()` will mint the opposite direction, and
  marks what it mints as `derived` (ADR-0020, ADR-0035).
- **No migration framework.** Each snapshot subsystem supports exactly one schema
  version and refuses any other, naming the build that wrote it. The field exists
  so the first schema change is a decision rather than a silent misread.
- **No authentication, credential handling, IAM or federation.** Permanently out
  of scope per ADR-0018. `alphalab.enterprise` models principals and roles; it
  accepts and stores no credentials.
- **No supervised live *process*.** Restart policy, alerting and scheduling are
  an operator's concern. `live_health` answers "should a human look at this?";
  acting on the answer is the caller's.
- **No CLI, no server, no daemon, no event bus.** AlphaLab is a library with no
  composition root and no declared entry point, and a test asserts it.

---

# External dependencies

Real, and not AlphaLab's engineering to complete.

- **Verification against a commercial venue.** The venue transport and the
  WebSocket client are written to protocol and exercised end to end over real
  sockets against local servers that verify signatures, timestamp windows,
  idempotency keys and accept tokens. They have never been pointed at a
  commercial venue: this environment has no network egress and holds no vendor
  credentials. Both transports say so in their own docstrings.
- **Named vendor request shapes.** Pointing a transport at a named venue needs
  that venue's request shapes, which differ per venue and belong to an adapter.
- **FX data.** v2.17 adds the rate-feed *boundary* — ordering, deduplication,
  conflict refusal, provenance and staleness — and **not a single rate**.
- **Classification data.** v2.11 supplies the security master's *mechanism* and
  v2.15 its provenance. AlphaLab ships no taxonomy and no reference-data feed, so
  a sector breakdown requires an operator who declares one. Sector is also the
  only dimension: industry, country, issuer and rating are each a separate
  decision with their own consumers.

---

# Optional future evolution

Could be built. Nothing depends on any of it, and no commitment is made here.

- **Classification dimensions beyond sector**, and sector-based risk limits.
  `sector_exposure` is visibility; no risk check reads a sector.
- **A vendor adapter package** implementing one named venue's request shapes over
  the existing transport, which is the smallest step from connectivity to
  integration.
- **Consolidating the two identical provider-vocabulary `AssetClass` enums** in
  `live.provider` and `marketdata.symbols`. Neither is on the canonical path and
  neither is persisted; the canonical asset taxonomy is `core.enums.AssetType`,
  which `brokers` aliases and `data` deliberately renames `DataAssetClass`.
- **A look-ahead guard on FX rates.** `FxRates.max_age_seconds` bounds how *old*
  a rate may be at the instant it is used. A future-dated rate — one whose
  `as_of` is after the conversion instant — is not refused. The feed path cannot
  produce one, because a quote older than what is held is `SUPERSEDED`, and every
  conversion records the rate's `as_of` and source, so the fact is visible. A
  caller supplying a table by hand is trusted with its contents.
- **A start offset on `AppendOnlyLog`**, which is what
  `OptimizerState.pending_trials` would need to stop being super-linear. It was
  implemented, measured at **+3.9%** on `benchmark_execution_pipeline`, and
  refused on that evidence — the same trade ADR-0028 refused at +1.78%.
  `alphalab.optimizer` is a standalone package with no in-repo consumer, so the
  term is off every canonical path.
- **Reviving `on_fill` / `on_order` / `on_timer` as pipeline-driven hooks.**
  `StrategyProtocol` declares them and `Dispatcher` routes them, but nothing in
  `ExecutionPipeline` constructs the events that would reach them; a caller
  driving `StrategyEngine.process_event` directly can. Wiring them into the
  pipeline needs a second strategy dispatch per event and would change intent
  ordering and every parity baseline.
- **Per-strategy sub-ledgers in `PortfolioEngine`.** The strategy-runtime design
  anticipated them; what shipped instead is the allocation contribution ledger
  and `order_shares_by_strategy`, which answers the attribution question without
  a second book.

---

# Versioning

**Major releases** introduce architectural milestones or breaking public API
changes. v2.0.0 unified the canonical execution domain models; **v3.0.0 freezes
the architecture and is deliberately additive** — the removals that would have
made it breaking were taken in v2.17.

**Minor releases** introduce new capabilities, and have occasionally made small,
documented breaking changes to a narrow public API where correctness required it:
v2.2.0 changed `AllocationEngine.release_reservation` to take no amount because
the reservation ledger owns it; v2.3.0 changed `alphalab.brokers`' field names so
both broker packages speak one vocabulary; v2.4.0 refused two model-registry
stage transitions that made rollback indistinguishable from promoting something
old; v2.5.0 gave a partially filled simulated order a terminal state; v2.16.0
made `governance` a required argument at every governed entry point; and v2.17.0
required the margin rates and currencies that had been silently defaulted.

**Patch releases** focus on stability, bug fixes, and performance.

After v3.0.0, the bar for a change rises: the invariants listed in
`nowandfuture.md` are frozen, and a change to any of them is a major release with
an ADR.

---

# Feature notes (delivered)

Scope notes for each delivered PR, kept as the historical record of what each
engine was built to do. See the table at the top for the release each shipped in.

## PR-034 — Feature Store

Institutional feature engineering framework: feature registry, versioning,
metadata, validation, caching.

## PR-035 — Factor Library

Reusable quantitative factors: momentum, value, quality, carry, volatility,
liquidity.

## PR-036 — Options Engine

Option chains, Greeks, volatility surfaces, pricing models, strategy simulation.

## PR-037 — Futures Engine

Continuous contracts, rolls, curve analysis, calendar spreads.

## PR-038 — Crypto Engine

Spot, futures, perpetuals, funding rates, exchange normalization.

## PR-039 — Macro Engine

Economic indicators, central bank events, yield curves, inflation, GDP.

## PR-040 — Alternative Data

News, sentiment, satellite, shipping, ESG, credit cards.

## PR-041 — Machine Learning

Feature pipelines, training, cross validation, prediction, evaluation.

## PR-042 — Deep Learning

LSTM, transformers, CNN, sequence models.

## PR-043 — Reinforcement Learning

Trading environments, policy optimization, agent evaluation.

## PR-044 — Cloud Research

Distributed quantitative research: remote execution, worker pools, cluster
management.

## PR-045 — Cluster Scheduler

Job scheduling, queue management, distributed orchestration.

## PR-046 — Experiment Tracking

Experiment history, metrics, parameters, versioning.

## PR-047 — Model Registry

Versioning, promotion, rollback, deployment metadata.

## PR-048 — AI Research Assistant

Deterministic, offline grid-search research driver: candidate generation,
caller-supplied evaluation, best-first ranking, Markdown reporting, and the
bridge that lifts a chosen candidate into a canonical `StrategyDefinition`. No
LLM and no network.

## PR-049 — Deployment Manager

Packaging, release management, rollbacks, production deployment.

## PR-050 — AlphaLab Enterprise

Principals and sessions (no credentials accepted or stored), RBAC, append-only
audit log, multi-user workspaces, secret *references* and rotation metadata, and
a compliance snapshot.

---

# Long-Term Vision

AlphaLab set out to be a complete quantitative research platform covering market
data, feature engineering, research, portfolio construction, machine learning,
production trading, cloud infrastructure and enterprise deployment — while
preserving determinism, immutability, modular architecture, event-driven design
and production readiness.

The engine expansion planned after v1.0.0 is delivered. The integration work that
followed it is delivered too: the execution path is one spine with one run owner
and four drivers, the lifecycle path is joined to it, orders reach a real venue
and fills come back through the same accounting a simulated fill takes, and state
round-trips durably across processes.

What remains is not a missing layer. It is the three classes above — deliberate
boundaries that should stay, external dependencies that are somebody else's to
supply, and optional evolution that nothing is waiting on.

---

# Community

Contributions are welcome; see `CONTRIBUTING.md`.

Because the architecture is frozen, a contribution that changes an ownership
boundary, a schema contract or a documented invariant needs an ADR and a major
release. Everything else — a vendor adapter, a new standalone engine, a strategy,
a benchmark, a test, a documentation fix — follows the ordinary workflow.
