# ADR-0056: The Product Charter -- A Universal Research Engine on a Stable Contract

## Status

**Accepted** (2026-11-10). The maintainer approved the charter revision -- G0 item 2,
decision D-V1, option (c) of the v4.3.0 development plan -- on 2026-11-09, asking
that the documents which state AlphaLab's direction be reconciled around one
authoritative product vision while the no-single-runtime boundary is preserved,
and on 2026-11-10 delegated the programme's decisions within the principles this
record restates. It supersedes no ADR; it decides how the direction documents read
ADR-0009, ADR-0034 and ADR-0049 together.

---

# Context

**The documents disagreed with each other and with the product's owner.**
`ROADMAP.md`'s *Long-Term Vision* said that at v4.0 "that engine is complete and
frozen". `nowandfuture.md` said "nothing is planned any more" and that a new
engine "will have no in-repo consumer, and that is correct".
`docs/SYSTEM_DESIGN.md` called wiring the standalone engines into an integrated
path "not pending work". `docs/VISION.md`'s current state named a transport the
library no longer ships. Read together they described a finished library, while
the owner's objective is a universal quantitative research engine that keeps
growing until a researcher anywhere can take an original hypothesis from data
to validated result (plan gap GAP-15).

**What the freeze was for.** v3.0.0 froze the architecture and v4.0.0 the public
contract (ADR-0034, ADR-0049) to stop two real failures: scope that grew without
a decision, and changes that silently moved recorded identities. Both reasons
stand. What the documents had added is a claim the freezes never made -- that
capability was finished.

**What must not be lost.** ADR-0009 records that AlphaLab's engines are not fused
into one runtime: the run has one owner (`RunEngine` over `ExecutionPipeline`,
four drivers), the lifecycle is joined to it by a query with a refusal, and the
other engines are composed by the code that uses them. AlphaLab is a library, not
a running application.

---

# Decision

## 1. The objective

AlphaLab is a **universal, strategy-agnostic quantitative research engine** for
**global markets**. The researcher brings the hypothesis; AlphaLab supplies a
deterministic, reproducible and auditable path from data to a validated result --
original strategy construction without a fixed template, the mathematical,
statistical and machine-learning methods the research needs, realistic execution,
systematic experimentation, statistical validation that accounts for selection,
and the reproduction of every result. No market, currency, calendar or
convention is a default the code assumes; each is declared.

## 2. What is stable: the contract

- The public API is held by `docs/api/PUBLIC_API.md`'s 4.x policy: a minor
  release adds or corrects, and removes or re-signs nothing incompatibly.
- Every recorded identity -- `result_id`, `configuration_id`, `manifest_id`,
  schedule and registry digests -- keeps its meaning. A run of earlier inputs
  has the identity it had.
- Every persisted format is read by every later build through its
  `SchemaHistory`, or refused for the reason its upgrade documents; payloads of
  each release are frozen under `tests/fixtures/snapshots/`.
- Determinism, immutability and refusing rather than guessing are unchanged.

## 3. What grows: capability

Capability is not frozen and the version number is not a ceiling on it. Engines
deepen; new engines, interfaces and integrations are added where research
requires them; defects and architectural weaknesses are corrected. A change that
would break the contract of section 2 is staged -- added beside what exists,
migrated explicitly, versioned -- or held for 5.0 with its migration; it is never
made for convenience.

## 4. Boundaries

Every boundary the documents state stands until its own ADR reopens it, recording
the problem, the alternatives, why the chosen one is better and what it does to
compatibility. These principles bind every such ADR:

1. The core keeps **zero mandatory runtime dependencies**.
2. Optional numerical, statistical, machine-learning and deep-learning
   dependencies integrate **separately, through stable core interfaces**.
3. **A separate process alone is not a secure sandbox**; the isolation of
   untrusted research code is enforced by the host or deployment environment,
   and AlphaLab provides the interfaces and orchestration around it.
4. **Numerical precision follows error analysis**, never an arbitrary fixed
   rounding that conceals a difference.
5. **No single runtime spans the engines** (ADR-0009): the run keeps one owner,
   engines are composed through explicit, tested bridges called by the code that
   uses them, and AlphaLab remains a library -- no server, no daemon, no CLI.
6. No historical `result_id`, `configuration_id`, schema or persisted format is
   changed silently.

## 5. How an engine joins the research path

A new or standalone engine is either **integrated through a tested bridge** --
into the canonical execution path or into a research workflow, with an
end-to-end test of the composition -- or **stays a library by stated choice**,
with the reason in an ADR. "No in-repo consumer" is no longer a sufficient
description of an engine's place.

## 6. The documents follow this record

`ROADMAP.md`, `docs/VISION.md`, `nowandfuture.md`, `docs/ARCHITECTURE.md` and
`docs/SYSTEM_DESIGN.md` cite this ADR where they state direction, and none says
the engine is complete. `tests/regression/test_charter_is_stated_consistently.py`
holds them to it.

---

# Consequences

- The direction documents are reconciled in one change. ROADMAP's *Long-Term
  Vision* states the objective of section 1; `nowandfuture.md`'s planned-work and
  extension sections point to the roadmap's future work and to section 5; its
  frozen invariants are kept, each standing until its own ADR changes it;
  SYSTEM_DESIGN states the integration rule instead of "not pending work";
  VISION's long-term direction states the objective.
- Reopening a boundary becomes a decision with a record rather than an argument
  with the documents. The v4.3.0 programme's reopenings -- curves and rates,
  volatility models, solvers beside the construction engine, historical
  universes and reference-data packs, depth -- each wait for their own ADR.
- Nothing in the engine changes, and no identity, schema or public name moves.
