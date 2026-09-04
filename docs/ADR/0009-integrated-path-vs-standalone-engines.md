# ADR-0009: Integrated Execution Path vs. Standalone Engines

## Status

Accepted (v2.0.0) — describes the state of the codebase, not a new commitment.

---

# Context

AlphaLab grew by adding one self-contained engine package per PR. By v2.0.0 that
produced roughly fifty packages under `alphalab/`: the execution core plus
research, replay, portfolio optimization, reporting, feature store, factor
library, alternative data, ML / deep learning / RL, options / futures / crypto /
macro, cloud research, cluster scheduler, experiment tracking, model registry,
deployment manager, research assistant, Strategy Studio, Workbench, and
Enterprise.

The earlier architecture documents describe all of this as one cohesive
platform with a single Workbench → Studio → engines → brokers → live-markets
flow. That is a design target. What was actually built is:

- **One integrated path.** `alphalab.runtime.ExecutionPipeline` wires ten
  packages together (`core`, `runtime`, `strategy`, `allocation`, `risk`, `oms`,
  `execution`, `portfolio`, `analytics`, `market`) as pure functions over one
  immutable `ExecutionPipelineState`, one market event at a time.
- **Everything else is standalone.** Each other engine is deterministic, has its
  own state type, its own tests, and its own benchmark, but is not invoked by
  `ExecutionPipeline` and does not invoke other engines. `replay`, in
  particular, is not wired into the execution path.

Documenting this split explicitly prevents the docs from over-claiming and gives
contributors a clear rule for where new work belongs.

---

# Decision

Treat the two categories as distinct, and say so in the documentation.

- `ExecutionPipeline` is **the** integrated spine. Changes to how strategy,
  allocation, risk, OMS, execution, portfolio, and analytics compose go through
  it, and must preserve the canonical domain models (ADR-0008).
- Every other package is a **standalone library**. It may be composed by user
  code, but AlphaLab does not ship a runtime that chains it to others. New engine
  packages follow this pattern unless a specific decision (and ADR) wires them in.
- Documentation must distinguish "target architecture" from "implemented". The
  `Implementation Status` section of `docs/ARCHITECTURE.md` is the source of
  truth for what is built.

---

# Consequences

Benefits

- Documentation matches the code; no implied integration that does not exist.
- The execution path stays small, reviewable, and deterministic.
- New engines can land without destabilising the spine.

Trade-offs

- Users wanting an end-to-end research-to-execution flow must compose engines
  themselves; only the market-to-analytics segment is pre-wired.
- The gap between the documented vision and the implementation is now explicit,
  which is accurate but less impressive.
- Deferred integration work (replay ↔ execution, mark-to-market, data-surface
  consolidation, a cross-engine runtime) is tracked in `ROADMAP.md` under
  "Not yet addressed" rather than implied as done.

---

# Alternatives Considered

**Rewrite the architecture docs to describe only `ExecutionPipeline`.** Rejected:
the standalone engines are real, tested, and useful; the target architecture is
worth keeping as a roadmap. The fix is to label each clearly, not to delete one.

**Build a minimal cross-engine runtime for v2.0.0 so the docs become true.**
Rejected as out of scope for a documentation release and a much larger design
question than this ADR should force.
