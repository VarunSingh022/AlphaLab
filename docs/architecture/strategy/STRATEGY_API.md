# Strategy API — Public Surface Design

**Subsystem:** Strategy Runtime
**Related:** `STRATEGY_RUNTIME_DESIGN.md`, `STRATEGY_LIFECYCLE.md`, `STRATEGY_CONTEXT.md`, `SIGNAL_MODEL.md`

> This document specifies method **contracts** — names, purpose, calling
> convention, what a strategy is and is not permitted to do inside each
> hook. It does not specify Python signatures, base classes, or
> implementation.

---

## 1. API Design Goals

Recall from `STRATEGY_RUNTIME_DESIGN.md` §1.1: this surface is effectively
permanent once published. The goals below are ordered by priority, and
where they conflict, higher items win:

1. **Minimality.** Every method on this surface is a promise maintained for
   years. A method not included today can be added later without breaking
   anyone. A method included today and later found wrong is a breaking
   change. When in doubt, leave it out.
2. **Uniformity.** Every hook has the same shape: `(context, event) →
   Iterable[Intent]` (or `None`). No hook is special-cased to have a
   different return type or a different way of producing effects. This is
   what keeps the Dispatcher simple and keeps strategy authors from having
   to learn N different calling conventions.
3. **No hidden side channels.** A strategy cannot place an order, log
   somewhere untracked, or read wall-clock time except through `context`.
   Everything a hook needs comes in through its two parameters.
4. **Fail-safe defaults.** Every hook is optional — a strategy that only
   implements `on_bar` and ignores everything else is valid and receives
   only bar events (because it subscribes only to bars; see §4).

---

## 2. Hook Inventory

All hooks share the calling convention `(context: StrategyContext, event:
EventT) -> Iterable[Intent]`, except the lifecycle hooks noted below which
receive no `event` (there is no market event corresponding to "start").

| Hook | Called when | Returns Intents? | Notes |
|---|---|---|---|
| `on_start(context)` | `Configured → Initialized` transition | No (side-effect-free by contract; see §3.1) | For declaring subscriptions/warmup needs, not for placing orders |
| `on_stop(context)` | Entering `Stopping`, before drain begins | No | Last chance to declare intent to flatten positions, e.g. via a returned `Iterable[Intent]` is *not* supported here — see §3.2 for why this is `on_shutdown`'s job instead |
| `on_shutdown(context)` | During `Stopping`, after `on_stop` | **Yes** | The one designated place a strategy may emit final flattening/cancel intents during shutdown |
| `on_tick(context, event: TickEvent)` | Subscribed tick event received | Yes | |
| `on_quote(context, event: QuoteEvent)` | Subscribed quote (bid/ask) event received | Yes | |
| `on_trade(context, event: TradeEvent)` | Subscribed trade-print event received | Yes | Distinct from `on_fill` — this is a *market* trade print, not the strategy's own fill |
| `on_bar(context, event: BarEvent)` | Subscribed bar (aggregated OHLCV) event received, per configured bar interval | Yes | |
| `on_fill(context, event: FillEvent)` | This strategy's own order was filled (in whole or part) | Yes | May return follow-up intents (e.g., a scale-in strategy reacting to its own partial fill) |
| `on_order(context, event: OrderEvent)` | This strategy's own order changed state: acked, rejected (by Risk or OMS), cancelled | Yes | Rejections are delivered here, not as exceptions — see `STRATEGY_RUNTIME_DESIGN.md` §6.3 |
| `on_timer(context, event: TimerEvent)` | A Scheduler-produced timer fires per this strategy's registered schedule | Yes | Uniform mechanism for minute/hour/session/cron/calendar bars — see `STRATEGY_RUNTIME_ADVANCED_TOPICS.md` §Scheduler |

### 2.1 Deliberately excluded from v1
- **`on_book_update`** as a *separate* hook from `on_quote`. Full order-book
  depth updates are modeled as a payload variant of `QuoteEvent` (a quote
  with depth > 1 level) rather than a tenth hook, to avoid the surface
  growing for every future market-data granularity. Market-making
  strategies needing full book access declare a `depth` requirement in
  subscription configuration; `on_quote` receives the richer event.
- **A generic `on_event(context, event)` catch-all.** Considered and
  rejected: it would let strategy authors bypass the typed hooks entirely,
  reintroducing exactly the kind of untyped dispatch the immutable,
  frozen-dataclass discipline elsewhere in AlphaLab is meant to prevent.
  Uniformity (goal 2) is better served by a closed, typed hook set.

---

## 3. What a Hook May and May Not Do

### 3.1 `on_start` is not a place to trade
`on_start` runs once, before the strategy is subscribed to anything (see
`STRATEGY_LIFECYCLE.md` — `Initialized` precedes `Subscribed`). It exists
for **declarative** setup: what instruments/event types/timers this
strategy needs, and any pure computation needed to warm up internal state
(e.g., loading historical bars via `context.history`, see
`STRATEGY_CONTEXT.md`). It does not return Intents. This ordering — declare
needs before receiving events — is what lets the Dispatcher build a
correct Subscription Index before any event can possibly be missed or
mis-routed.

### 3.2 Why `on_stop` cannot emit Intents but `on_shutdown` can
Two hooks exist around shutdown, not one, because "stop accepting new
work" and "wind down existing work" are different concerns with different
safety properties:

- `on_stop` is the strategy's notification that shutdown has begun. It is
  the place to flip internal flags, cancel timers it owns, etc. It runs
  **before** the drain deadline starts counting, and specifically **cannot**
  emit Intents — this prevents a shutdown-triggered code path from
  opening *new* risk (e.g., an author naively adding "close all positions"
  logic in the very first hook that fires on shutdown, then also
  duplicating it in `on_shutdown`, double-submitting flatten orders).
- `on_shutdown` is the single, unambiguous place where final orders (e.g.,
  flatten remaining positions, cancel resting orders) are permitted, and it
  runs against the drain deadline (`STRATEGY_LIFECYCLE.md` §4.2:
  `Stopping → Stopped` vs. `Stopping → Failed` on deadline overrun).

This two-hook split is a small surface-area cost (one extra method) in
exchange for removing an entire category of "did I already submit my
flatten order" bugs at shutdown — exactly the kind of institutional-grade
defensiveness this framework should encode structurally rather than leave
to strategy-author discipline.

### 3.3 No hook may block indefinitely
Every hook invocation is wrapped by the Runtime Supervisor in a timeout
(configurable per-strategy, sane default e.g. tens of milliseconds for
tick/quote hooks, generous for `on_timer`/`on_bar` hooks that may run
heavier computation). Exceeding it is treated identically to an unhandled
exception (`STRATEGY_LIFECYCLE.md` §4.2, `Running → Failed`). This is a
direct consequence of G2 (isolation) — a strategy that blocks the calling
thread indefinitely is functionally identical to one that corrupts shared
state, from the runtime's perspective.

### 3.4 No hook may read time, randomness, or I/O outside `context`
Enforced partly by convention (documented here, and in contributor
guidelines) and partly by tooling: the plugin loader (see
`STRATEGY_RUNTIME_ADVANCED_TOPICS.md` §Plugin System) runs a static-analysis
pass over registered strategy modules flagging direct use of
`datetime.now`, `time.time`, `random`, `os.environ`, or network/socket
imports outside of explicitly whitelisted adapter code. This is advisory
(Python cannot fully sandbox this) but is treated as a hard CI gate for any
strategy submitted to the shared registry — a strategy that fails this
check cannot be marked "backtest-verified."

---

## 4. Subscriptions

A strategy does not implicitly receive every event type it happens to
implement a hook for. Subscriptions are **explicit**, declared during
`on_start` (e.g., "subscribe to ticks for instruments X, Y; subscribe to
5-minute bars for instrument Z; subscribe to an hourly timer"). This
matters for two reasons:

- **Performance at scale (G6):** with 1000+ strategies, the Dispatcher must
  be able to route in O(subscribers-to-this-event), not O(all-strategies)
  per event. Implicit "if you implement `on_tick` you get all ticks" would
  make every strategy pay dispatch cost for every event in the system.
- **Correctness:** a strategy that implements `on_bar` for testing purposes
  but never subscribes to any bar interval should not silently start
  receiving bars the moment some *other* strategy configures a matching
  interval — subscriptions are per-strategy-instance, not global.

---

## 5. ABC vs. Protocol vs. Composition

This is the most consequential single decision in this document, because
it determines how strategy authors' code is *shaped* for a decade.

### 5.1 The three options

| | Description |
|---|---|
| **ABC (Abstract Base Class)** | Strategies inherit from a `Strategy` base class; hooks are `@abstractmethod` or have default no-op implementations strategies override. |
| **Protocol (structural typing, `typing.Protocol`)** | No inheritance required; any class implementing the right method names/signatures satisfies the type. Runtime uses `isinstance` against the Protocol (with `@runtime_checkable`) or duck-typing at registration time. |
| **Composition** | Strategies are built by composing small, independent pieces (e.g., a `SignalGenerator` + `SubscriptionSpec` + optional `Lifecycle` handlers) passed to a generic runtime-provided `StrategyAdapter`, rather than a strategy author writing one class that does everything. |

### 5.2 Trade-off Analysis

| Criterion | ABC | Protocol | Composition |
|---|---|---|---|
| Discoverability for new authors (IDE autocomplete, "what do I implement") | **Strong** — inheriting shows exactly what to override | Weak — nothing to inherit from means nothing to autocomplete against unless authors already know the shape | Moderate — depends on how well the composable pieces are documented |
| Forces authors into a class hierarchy | Yes — can conflict with authors wanting multiple inheritance or their own base classes (e.g., an RL-agent base class from another framework) | No | No |
| Enables partial implementation (only override hooks you need) | Yes, via default no-op methods | Yes, if Protocol methods are given defaults via a companion mixin, but pure Protocols can't provide default implementations | Yes, naturally — you only compose the pieces you need |
| Runtime type-checking cost | Cheap — `isinstance` against ABC is fast and standard | Slightly more expensive if `@runtime_checkable` Protocol `isinstance` checks are used at hot-path registration (not hook-invocation) time — acceptable since this only happens at registration, not per-tick | N/A — composition is checked at construction |
| Testability in isolation | Good | Good | **Best** — individual pieces (e.g., just the `SignalGenerator`) are unit-testable with zero runtime scaffolding |
| Long-term API evolution (G9) | Adding a new hook = adding a new method with a default no-op to the ABC; low risk | Adding a new hook to the Protocol is a breaking structural change for any strategy that was relying on `@runtime_checkable` exhaustiveness, though optional hooks mitigate this | Adding new *capability* = adding a new optional composable piece; existing strategies unaffected — **best evolution story** |
| Fit for heterogeneous strategy types (simple signal strategies vs. RL agents vs. market makers with very different internal architectures) | Poor — one base class shape is a poor fit for an RL agent whose natural interface is `observe/act`, forcing an awkward adapter anyway | Poor for the same reason, but slightly more flexible since no inheritance is forced | **Best** — an RL agent can be wrapped by a thin composed adapter that maps `observe/act` onto the same `Intent`-producing contract, without pretending it's a "normal" hook-based strategy |

### 5.3 Decision: Hybrid — Protocol-defined contract, ABC convenience base, composition for cross-cutting concerns

- The **canonical contract** is a `typing.Protocol` (`StrategyProtocol`)
  defining the hook shapes in §2. This is the actual type the runtime
  depends on — satisfying it is what makes something "a strategy," full
  stop, regardless of how it's built.
- AlphaLab additionally ships an **optional ABC** (`BaseStrategy`) that
  implements `StrategyProtocol` with sensible no-op defaults for every
  hook, purely as an ergonomic convenience for the common case (goal:
  discoverability from §5.2). Authors are free to ignore it and implement
  the Protocol directly.
- **Composition is used for cross-cutting concerns that are not naturally
  per-hook**, e.g., a reusable `IndicatorMixin` for computing rolling
  windows, or a `RiskAwareMixin` that pre-filters intents against
  `context.risk_view` before returning them. These compose *with* either
  the Protocol-direct or ABC-based strategy, rather than being baked into
  the base class itself — keeping `BaseStrategy` minimal (goal 1).
- Non-hook-shaped strategy types (RL agents, market makers with
  fundamentally different internal loops) are supported via **adapter
  composition**: a thin adapter class satisfies `StrategyProtocol` on the
  outside while delegating to an arbitrary internal architecture on the
  inside. This is the mechanism that satisfies G8 (extensibility without
  core modification) for strategy shapes not yet anticipated.

**Why not pure ABC:** would force every future strategy shape (RL agents
included) through one inheritance hierarchy, which is the single biggest
long-term extensibility risk given the explicit requirement to support RL
agents and market makers alongside simple signal strategies.

**Why not pure Protocol:** loses the ergonomic "here's what to override"
discoverability that matters enormously for an eventually open-source
project with a wide contributor base of varying experience levels.

**Why not pure composition (no Protocol/ABC at all):** without a nameable
contract type, the runtime has nothing concrete to validate against at
registration time, and "what is a strategy" becomes implicit/undocumented
— unacceptable for G9 (API longevity) and G7 (observability — the runtime
needs a stable type to introspect for telemetry/tooling purposes).

---

## 6. Error Handling Contract

- A hook that raises an unhandled exception → `Running/Paused → Failed`
  (`STRATEGY_LIFECYCLE.md` §4.2). The exception and the triggering event
  are captured into telemetry; **other strategies are unaffected** (G2).
- A hook that exceeds its timeout → treated identically to an exception.
- A hook that returns a malformed `Intent` (fails `Intent`'s own schema
  validation, defined in `SIGNAL_MODEL.md`) → the malformed Intent is
  dropped, a telemetry warning is emitted, but the strategy itself is
  **not** failed for a single malformed Intent — only repeated malformed
  output within a rolling window triggers `Failed`, to avoid one noisy
  Intent taking down an otherwise healthy strategy. Threshold is
  configurable per-strategy.

---

## 7. Versioning Strategy

- `StrategyProtocol` is versioned independently of the rest of AlphaLab
  (`strategy-api` version, distinct from the overall package version).
  Backward-compatible additions (new *optional* hooks) bump the minor
  version; any change to an existing hook's contract (parameter meaning,
  Intent semantics) bumps the major version and requires a documented
  migration guide.
- The plugin manifest (`STRATEGY_RUNTIME_ADVANCED_TOPICS.md` §Plugin
  System) records which `strategy-api` major version a strategy was
  written against, so the runtime can refuse to load a strategy against an
  incompatible major version rather than failing confusingly at first
  event dispatch.
