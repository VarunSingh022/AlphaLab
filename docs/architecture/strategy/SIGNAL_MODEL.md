# Signal Model — Design

**Subsystem:** Strategy Runtime
**Related:** `STRATEGY_RUNTIME_DESIGN.md`, `STRATEGY_API.md`, `STRATEGY_CONTEXT.md`, `STRATEGY_RUNTIME_ADVANCED_TOPICS.md` §Multi-Strategy

---

## 1. The Question

What object does a strategy hook return to express "I want something to
happen"? The brief lists five candidates. This document compares them and
commits to one (layered) model.

| Candidate | Description |
|---|---|
| **Signal** | A weak, abstract expression of view — e.g., direction + strength/score for an instrument, with no sizing or execution detail. |
| **OrderRequest** | A concrete, ready-to-route order: instrument, side, quantity, order type, price/limit, time-in-force. |
| **Intent** | A statement of *desired outcome* (e.g., target position/weight for an instrument) without prescribing the mechanics of how to get there. |
| **Recommendation** | An advisory object intended for human review before any action is taken (human-in-the-loop). |
| **ExecutionRequest** | A request parameterized for a specific execution algorithm (e.g., TWAP over 30 minutes, participation rate 10%) — execution-strategy-specific, not just order-specific. |

---

## 2. Comparative Analysis

| Criterion | Signal | OrderRequest | Intent | Recommendation | ExecutionRequest |
|---|---|---|---|---|---|
| **Coupling to OMS/execution mechanics** | None — fully decoupled | High — strategy must know order types, TIF conventions, lot sizing | Low — decoupled from *how* to execute, coupled only to *what outcome* is wanted | None | Very high — strategy must know execution-algo parameters |
| **Testability in isolation (G4)** | Easy — pure scoring function | Harder — must also get order mechanics right to test meaningfully | Easy — target state is simple to assert against | Easy, but tests nothing about actual trading behavior | Hard — must reason about algo parameters, which are really an execution-layer concern |
| **Multi-strategy netting friendliness (§ Multi-Strategy)** | Poor — a "score" from one strategy isn't directly combinable with a "score" from a differently-calibrated strategy without an extra normalization step the model doesn't specify | Poor — concrete orders from 1000 strategies for the same instrument must be un-done and re-derived to net, since the *concrete order* already baked in decisions that should have been made post-netting | **Good** — target positions/weights across strategies are directly combinable/aggregatable (weighted sum of target exposures) before any order is derived | N/A — no automated netting possible by definition | Poor, same reason as OrderRequest, worse — algo parameters from different strategies for the same instrument are not composable at all |
| **Replay determinism (G1)** | Fine — pure data | Fine, but see coupling note | Fine | Fine (but see below) | Fine, but see coupling note |
| **Fit for backtest/live symmetry (G5)** | Fine | Requires the backtest execution simulator to accept the exact same order mechanics as the live broker, which is achievable but means every strategy author must correctly model TIF/lot semantics themselves for the backtest to be meaningful | Fine — the translation from Intent to OrderRequest happens in one place (the allocation layer), so backtest/live symmetry only needs to be correct in *that one place*, not in every strategy | Doesn't apply — a human-in-the-loop step is not compatible with automated backtesting at all, which conflicts directly with G1/G5 as core requirements | Requires backtest simulator to model specific execution algorithms faithfully, a materially harder simulation problem than plain order fills |
| **Strategy author cognitive load** | Low | High — must think like an execution trader, not just a signal generator | **Low-to-moderate** — "I want to be 3% long AAPL" is the natural unit most quant strategies actually reason in | Low, but shifts the real decision-making burden to a human, which conflicts with the framework's automated, deterministic design | High |
| **Expressiveness for advanced use (market making needing precise order placement)** | Poor — too abstract for a market maker who genuinely needs to place/cancel specific resting quotes | Good — market making needs exactly this level of control | Poor on its own | N/A | Good for algo-driven execution strategies specifically |
| **Institutional precedent** | Common in signal-research contexts (alpha scoring), rare as the *sole* interface to a live trading system | Common in simpler single-strategy retail/prop frameworks | **Standard in institutional multi-strategy platforms** — portfolio construction/allocation as a distinct layer between "alpha" and "execution" is close to universal at multi-strategy shops | Used in advisory/compliance-gated contexts (e.g., broker-dealer retail advice tools), not typically in an automated multi-strategy engine | Standard, but as an *execution-layer* concept, not typically what alpha-generating strategies themselves emit directly |

## 3. Decision: A Layered Model — `Intent` is what strategies emit; `OrderRequest` and `ExecutionRequest` are downstream, derived artifacts

### 3.1 The chosen pipeline

```mermaid
flowchart LR
    STRAT[Strategy hooks] -->|emits| INTENT[Intent]
    INTENT --> ALLOC[Allocation / Netting Layer]
    ALLOC -->|derives| OR[OrderRequest]
    OR --> RISK[Risk Engine]
    RISK -->|approved| OMS[OMS]
    OMS -->|selects algo, if applicable| ER[ExecutionRequest]
    ER --> EXEC[Execution Engine]
```

- **Strategies emit `Intent`** — a statement of desired outcome: target
  position/weight for an instrument, a confidence/strength value, a time
  horizon, and metadata (strategy id, correlation id, timestamp, tags).
  Never raw scores (too abstract to act on), never concrete orders (too
  coupled to execution mechanics), never a human-review object
  (incompatible with G1/G5).
- **The Allocation/Netting Layer** (`STRATEGY_RUNTIME_ADVANCED_TOPICS.md`
  §Multi-Strategy) consumes `Intent`s from potentially many strategies for
  the same instrument, applies capital allocation and netting, and derives
  concrete `OrderRequest`s. This is the *only* place `Intent → OrderRequest`
  translation happens.
- **`OrderRequest`** flows to Risk exactly as described in
  `STRATEGY_RUNTIME_DESIGN.md` §6.3 — concrete, validated, ready to route.
- **`ExecutionRequest`** is an OMS/Execution Engine-internal concept for
  *how* an approved `OrderRequest` gets worked in the market (TWAP, POV,
  iceberg, etc.), selected either by OMS-level execution-algo policy or, for
  strategies sophisticated enough to want the control, expressible as
  additional metadata on the `Intent` itself ("prefer TWAP over 30 min")
  that the Allocation Layer passes through as a *hint*, not a guarantee —
  keeping execution-algo selection ultimately an execution-layer decision.

### 3.2 Why not `Signal` alone
Rejected as the sole model: too abstract to reach OMS without an
under-specified translation step. `Intent` was chosen over bare `Signal`
specifically because "target weight/position" is directionally scored
*and* sized in one object, whereas `Signal` alone punts sizing to an
unspecified later step. In practice `Intent` subsumes what `Signal` would
have given you (direction, strength) plus the sizing information actually
needed to make netting well-defined (§2, multi-strategy netting row).

### 3.3 Why not `OrderRequest` directly from strategies
Rejected as the sole model: forces every strategy author to reason about
order mechanics (goal: low cognitive load, `STRATEGY_API.md` §1) and, more
importantly, makes multi-strategy netting close to intractable — netting
concrete orders after the fact means un-deriving decisions (lot sizing,
order type) that were premature at the point the strategy made them.
**Exception:** market-making and other execution-sensitive strategy types
genuinely need finer control than "target weight" affords. This is handled
via a documented escape hatch (§3.4), not by making `OrderRequest` the
universal model.

### 3.4 Escape hatch for market making / execution-sensitive strategies
A strategy category that legitimately needs to place/amend/cancel specific
resting orders (market making) is supported by allowing `Intent` to carry
an optional, strongly-typed `execution_directive` payload (e.g., "place
these two-sided quotes at these exact prices/sizes") that the Allocation
Layer passes through to `OrderRequest` construction with minimal
transformation, bypassing target-weight netting logic for that specific
instrument/strategy pair (since a market maker's own resting quotes should
generally not be netted against another strategy's directional position in
the same name without explicit configuration). This is scoped narrowly and
documented as an advanced/opt-in path — the default, common-case path for
the vast majority of signal-driven strategies remains plain `Intent`.

### 3.5 Why not `Recommendation`
Rejected outright for the automated pipeline: a human-in-the-loop step is
fundamentally incompatible with G1 (determinism, in the sense of a fully
specified, replayable pipeline) and G5 (backtest/live symmetry — there is
no meaningful way to "backtest" a human's future decisions). `Recommendation`
remains a *legitimate* concept for a separate, explicitly human-facing
tool (e.g., a research/advisory dashboard) but that tool is out of scope
for the Strategy Runtime and would consume the same underlying `Intent`
stream read-only, the same way Analytics does
(`STRATEGY_RUNTIME_DESIGN.md` §6.7) — never feed back into the automated
pipeline.

---

## 4. `Intent` — Conceptual Data Model

(Conceptual fields only, per the "no code" constraint — this is not a
class definition.)

| Field | Purpose |
|---|---|
| `strategy_id` | Attribution — required for allocation, netting, and audit. |
| `instrument` | Opaque identifier from Core Domain's instrument model. |
| `target` | The desired outcome: either a target weight (fraction of allocated capital) or a target quantity — one canonical representation, chosen at strategy-configuration time, so the Allocation Layer doesn't have to guess which one a given `Intent` means. |
| `strength` / `confidence` | Optional scalar (e.g., 0–1) used by the Allocation Layer when multiple strategies disagree on the same instrument — see aggregation in `STRATEGY_RUNTIME_ADVANCED_TOPICS.md` §Multi-Strategy. |
| `horizon` | Expected holding period / urgency — informs execution-algo hinting (§3.1) and helps the Allocation Layer distinguish a fleeting scalping intent from a multi-day position build. |
| `execution_directive` (optional) | The market-making/advanced escape hatch (§3.4). |
| `correlation_id` | Links this Intent back to the triggering event, for full audit traceability from tick → Intent → OrderRequest → Fill. |
| `timestamp` | From `context.clock`, never wall-clock (G1, G5). |
| `metadata` / `tags` | Free-form, strategy-author-supplied, never interpreted by the runtime itself — purely for downstream analytics/debugging. |

`Intent` is a frozen, `slots=True` dataclass, consistent with every other
object crossing the strategy boundary (`STRATEGY_RUNTIME_DESIGN.md` §9).

---

## 5. Summary Decision Table

| Question | Answer |
|---|---|
| What do strategies emit? | `Intent` |
| What does Risk validate? | `OrderRequest` (derived) |
| What does Execution work? | `ExecutionRequest` (derived, OMS/Execution-internal) |
| Where does netting happen? | Allocation Layer, operating on `Intent`s, before `OrderRequest` derivation |
| Is `Signal` used anywhere? | Only as an internal, strategy-private concept (e.g., a raw alpha score a strategy computes for itself before converting to a sized `Intent`) — never a runtime-level type |
| Is `Recommendation` used anywhere? | Only in a separate, out-of-scope human-facing research tool, consuming `Intent`s read-only |
