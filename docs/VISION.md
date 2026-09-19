# AlphaLab Vision

## Vision Statement

AlphaLab aims to become a comprehensive, open-source quantitative research and algorithmic trading platform built on modern software engineering principles.

The project combines deterministic execution, immutable state management, event-driven architecture, and modular design to provide a foundation suitable for both individual researchers and institutional teams.

Rather than being a collection of independent tools, AlphaLab is designed as a unified ecosystem where research, portfolio construction, execution, production systems, and future AI capabilities operate through a consistent architecture.

---

# Mission

AlphaLab exists to make institutional-quality quantitative infrastructure accessible to everyone.

The project emphasizes

- Deterministic execution
- Immutable state
- Strong typing
- Comprehensive testing
- Modular architecture
- Reproducible research
- Long-term maintainability

Every component should follow these principles.

---

# Current State

As of **v3.0.0** the architecture is frozen; **v3.1.0** is additive to it.
AlphaLab provides:

- **One execution path with one owner per tier.**
  `alphalab.runtime.ExecutionPipeline` owns the execution step (mark to market →
  strategy → allocation → risk → OMS → execution → portfolio → analytics) and
  `alphalab.runtime.run.RunEngine` owns the run. Four interchangeable drivers
  feed it — backtest, replay, paper and live — so all four produce the same
  orders, fills and accounting wherever the venue is the same.
- **One lifecycle path, joined to it.** `alphalab.lifecycle` takes a research
  candidate to a deployment and back, with governance on every act that changes
  what is live, and refuses a run that would serve a version the ledger does not
  name.
- **Durable, typed state.** Ten snapshot owners, ten schema constants, typed
  decoding that names the field it rejects, and a run that can stop in one
  process and finish byte-identically in another.
- **Real connectivity.** An HMAC-signed venue transport, an RFC 6455 WebSocket
  client, and a content-addressed artifact store — written to protocol and
  exercised over real sockets, and **not** verified against any commercial venue.
- **Money that refuses rather than guesses.** Per-currency settlement, a
  reporting currency converted on demand with every rate recorded, and no
  default, triangulated, inverted or stale rate anywhere.
- **Standalone engines** for reporting, portfolio optimization, the feature
  store, the factor library, alternative data, ML / deep learning / RL, options,
  futures, crypto, macro, cloud research, cluster scheduling and the Workbench.

Those standalone engines share the engineering model and are deliberately not
fused into a single runtime (ADR-0009). AlphaLab is a library, not a running
application: no server, no daemon, no CLI, and zero runtime dependencies.

---

# Guiding Principles

## Deterministic by Default

The same inputs should always produce the same outputs.

---

## Immutable State

State transitions produce new immutable objects rather than modifying existing state.

---

## Event-Driven Design

Subsystems communicate through explicit domain events instead of hidden side effects.

---

## Modular Architecture

Each package owns a single business capability and exposes a stable public API.

---

## Engineering Excellence

AlphaLab prioritizes correctness, clarity, and maintainability over unnecessary complexity.

---

# Long-Term Roadmap

The engine expansion planned after v1.0.0 is delivered (v1.34.0–v2.0.0), and so
is the integration work that followed it. The three items this section listed as
remaining — wiring `replay` into the execution path, mark-to-market repricing,
and consolidating the overlapping data surfaces — were delivered in **v2.2**,
**v2.1** and **v2.3** respectively, and the entry stood unchanged until v3.0.

What remains is not a missing layer. `../ROADMAP.md` classifies it as deliberate
boundaries that should stay, external dependencies that are somebody else's to
supply (FX data, classification data, a vendor's request shapes, credentials),
and optional evolution that nothing is waiting on.

---

# Community

AlphaLab welcomes contributions from researchers, engineers, students, and practitioners.

The project values thoughtful design, constructive collaboration, and high engineering standards.

---

# Looking Ahead

Version 1.0.0 established AlphaLab's architectural foundation; v1.34.0–v2.0.0
populated it with standalone quantitative engines; the v2 line integrated the
execution and lifecycle paths and made their state durable; v3.0.0 froze the
result; and v3.1.0 showed what the freeze permits, adding the universal
data-ingestion path inside one package without moving a boundary.

A frozen architecture is not a finished project. What it changes is the bar: a
contribution that moves an ownership boundary, a schema contract or a documented
invariant now needs an ADR and a major release, while a vendor adapter, a new
standalone engine, a strategy or a benchmark follows the ordinary workflow. The
principles that decide those calls — determinism, immutability, modularity,
production readiness, and refusing rather than guessing — are unchanged.