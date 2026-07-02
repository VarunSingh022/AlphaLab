# Strategy Context — Design

**Subsystem:** Strategy Runtime
**Related:** `STRATEGY_RUNTIME_DESIGN.md`, `STRATEGY_API.md`, `SIGNAL_MODEL.md`

---

## 1. What Context Is

`StrategyContext` is the single object through which a strategy observes
the outside world. It is constructed fresh (or reused from a pool, §6) by
the Context Factory for each hook invocation and handed in as the first
argument to every hook (`STRATEGY_API.md` §2).

It is **frozen** and **`slots=True`**, consistent with the rest of
AlphaLab's immutable-kernel discipline. This is not a style choice local to
this document — it is the mechanism that makes G3 (immutability at the
boundary, `STRATEGY_RUNTIME_DESIGN.md` §2) an enforced invariant rather
than a convention strategy authors are trusted to respect.

### 1.1 Why a single object, not N separate parameters
A hook signature of `(context, event)` rather than
`(portfolio, market_data, clock, risk, config, ..., event)` is deliberate:

- **API stability (G9):** adding a new piece of read-only information to
  Context (e.g., a future `context.universe`) does not change any hook's
  signature — it is purely additive on a field of an already-passed object.
  Adding a new bare parameter to every hook would be a breaking change to
  `StrategyProtocol` itself.
- **Uniform construction point:** the Context Factory is the *one* place
  that assembles "the state of the world as of this event," which is
  exactly the invariant that needs the most scrutiny for correctness
  (§5) and performance (§6). Scattering that assembly across N parameters
  would scatter that scrutiny too.

---

## 2. What Belongs in Context

| Field | Type of access | Why it's needed | Why immutable/read-only |
|---|---|---|---|
| **Portfolio** | Read-only snapshot (`PortfolioSnapshot`) | Strategies routinely need current position/cash/P&L to decide whether to add, reduce, or ignore a signal (e.g., "don't add if already at target"). | Portfolio truth is owned by the Portfolio Engine, which updates itself from `ExecutionReport`s (`STRATEGY_RUNTIME_DESIGN.md` §6.6). If Context exposed a mutable handle, a strategy could desync Portfolio's view of itself from reality — a direct violation of "OMS/Portfolio is sole source of truth." |
| **Market Data** | Read-only accessor (latest quote/trade/book for subscribed instruments, plus the triggering `event` itself) | Needed for cross-referencing ("what's the current spread" while reacting to a fill). | Same rationale — Market Data Engine is the source of truth; Context exposes a snapshot view, not a live mutable feed handle, so replay determinism (G1) holds even if the strategy queries market data multiple times during one hook call. |
| **Clock** | Read-only (`now()`, session calendar queries) | This is *the* mechanism that makes backtest/live symmetry (G5) possible — see `STRATEGY_RUNTIME_DESIGN.md` §6.2. Any strategy needing "what time is it" must go through here, never `datetime.now()`. | Clock is externally driven (virtual in backtest, monotonic in live); a strategy has no business advancing or setting it. |
| **Logger** | Write-only, structured, strategy-scoped | Strategies need to emit diagnostic information without reaching for `print` or an ungoverned logging singleton that would make output impossible to attribute back to a specific strategy instance at scale (1000+ strategies logging concurrently). | Not "immutable" in the read sense — it's a narrow, one-way, side-effect-isolated channel: writes are captured into strategy-scoped telemetry (`STRATEGY_RUNTIME_DESIGN.md` §5.7), never into shared mutable state the strategy could later read back and branch on (which would reintroduce non-determinism). |
| **Risk (RiskView)** | Read-only (current exposure, remaining limit headroom, per-instrument constraints) | Lets strategies self-limit proactively instead of only discovering limits via post-hoc `on_order` rejections — better capital efficiency and fewer wasted round-trips through Risk. | Risk's actual limit *enforcement* remains solely Risk Engine's job (`STRATEGY_RUNTIME_DESIGN.md` §6.3); Context exposes visibility, never a mutable handle a strategy could use to alter its own limits. |
| **Configuration** | Read-only, strategy-scoped, validated at `Configured` | Strategies need their own parameters (thresholds, lookback windows, instrument lists) without reaching into global config or environment variables (forbidden per `STRATEGY_API.md` §3.4). | Frozen at `Configured` (`STRATEGY_LIFECYCLE.md` §4.2) specifically so a strategy's behavior for a given event is fully determined by `(config, event-history)` — a prerequisite for deterministic replay. |
| **Execution Reports (query interface)** | Read-only, scoped to this strategy's own orders | Needed for reasoning beyond what the current hook's single `event` carries — e.g., "how many of my orders are currently open" inside `on_timer`. | Backed by OMS's authoritative order state (`STRATEGY_RUNTIME_DESIGN.md` §6.4); Context never lets a strategy see or affect another strategy's orders — see §4. |
| **Historical Data** | Read-only accessor (bounded lookback window/query) | Needed for warmup in `on_start` (`STRATEGY_API.md` §3.1) and for indicator computation that needs more than the single triggering event. | Historical queries are against immutable, already-settled data — no risk of a strategy "seeing the future," since the accessor is clock-bounded to `context.clock.now()` (critical for backtest correctness — see §5). |
| **Universe** | Read-only (the instrument set this strategy is permitted/configured to trade) | Strategies operating over a basket (stat-arb, index-tracking) need to enumerate their universe, not just react to whichever instrument happens to be in `event`. | Universe membership is a configuration-time decision (tied to Configuration above), not something a strategy should be able to expand at runtime without going through reconfiguration — this is itself a risk control (a strategy cannot silently start trading an instrument it was never approved for). |
| **Order API (constrained facade)** | Write-only, but not "place order" — see §3 | This is *not* a direct order-placement API. It is included in Context as the object a strategy uses to construct `Intent`s in a type-safe, pre-validated shape (e.g., helper constructors, not a submission mechanism). | Genuinely constrained: it has no method that results in an order actually reaching OMS. See §3 for why this distinction matters. |

---

## 3. Why "Order API" in Context Is Not an Order-*Placement* API

This is worth calling out explicitly because it's the single easiest place
for the design to accidentally leak a side channel. If `context.orders`
had a `.submit(order)` method that directly reached OMS, it would:

1. Bypass the Signal Router / allocation layer entirely
   (`SIGNAL_MODEL.md`), breaking multi-strategy netting and capital
   allocation.
2. Bypass Risk validation ordering — a strategy could theoretically place
   an order before Risk ever sees it, or make it ambiguous whether Risk
   review happened.
3. Reintroduce a mutable side effect reachable from *inside* a
   nominally-pure hook, undermining G1 (determinism) and G3 (immutability
   at the boundary) simultaneously.

Instead, `context.orders` (naming provisional) is purely a **builder/query
facade**: convenience constructors for well-formed `Intent` objects
(`SIGNAL_MODEL.md`), and read-only queries against this strategy's own
resting orders. The *only* way an effect leaves a strategy is the
`Iterable[Intent]` a hook returns (`STRATEGY_API.md` §1, goal 3). This
keeps exactly one audited channel for "a strategy wants something to
happen," which is what makes G7 (observability) and G2 (isolation) hold
without exception.

---

## 4. Strict Exclusions — What Must Never Be in Context

| Excluded | Why |
|---|---|
| Other strategies' internal state | Strategies must be independently reasoned about, tested, and replayed (G4). Visibility into another strategy's state creates implicit coupling that defeats isolation (G2) and makes 1000-strategy deployments impossible to reason about incrementally. |
| Other strategies' orders/positions | Same rationale; a strategy should only ever see *its own* sub-ledger view of Portfolio and OMS state (§2, Execution Reports row), never a peer's. |
| Direct references to concrete engine classes (the actual `RiskEngine`, `OMSEngine`, etc. instances) | Context must expose interfaces/read-only views, never concrete engine objects — otherwise a strategy could call an engine method the runtime never intended to expose (e.g., an internal admin method on the Risk Engine), which is both a safety hole and a hidden coupling that defeats the Dependency Graph rules (`STRATEGY_RUNTIME_DESIGN.md` §8). |
| Mutable collections of any kind | Even a "read-only" list, if it's a plain mutable `list`, can be mutated by a careless strategy without any error — Context must use immutable/frozen container types throughout, not just an immutable *top-level* object with mutable innards. |
| Wall-clock time, randomness, environment access | Already excluded via `context.clock` being the sole time source (§2) and the static-analysis enforcement in `STRATEGY_API.md` §3.4. |

---

## 5. Historical Data and Look-Ahead Bias

The Historical Data accessor deserves special scrutiny because it is the
single most common source of a specific, insidious bug class in
quantitative research: **look-ahead bias**, where a backtest accidentally
lets a strategy see data that would not have been available at that point
in real time, producing backtest results that cannot be reproduced live.

Design requirement: every historical query made through `context.history`
is implicitly bounded by `context.clock.now()` at the moment of the call —
it is architecturally impossible to request a bar or tick timestamped
after "now," because the accessor is constructed by the Context Factory
already scoped to the current point in the replay/live stream, not handed
an open-ended handle to the full dataset. This is enforced at the Context
Factory level (§6), not left to the historical data store to self-police,
because the Context Factory is the one component that reliably knows "now"
for *this specific hook invocation*.

---

## 6. Context Construction: Performance

Recall the scale target: 1000+ strategies, 100,000 events/sec
(`STRATEGY_RUNTIME_DESIGN.md` §2, G6). If Context were freshly, deeply
constructed (new Portfolio snapshot copy, new RiskView copy, etc.) on
every single hook invocation, this becomes the dominant cost in the entire
pipeline well before Risk/OMS/Execution do any real work.

### 6.1 Design: Layered, Shared, Copy-on-Write-Free Snapshots
- `PortfolioSnapshot` and `RiskView` are **published once per underlying
  update** (i.e., once per `ExecutionReport` that actually changes
  Portfolio state, or once per Risk limit change) — not once per Context.
  Every Context built between two such updates shares the **same
  immutable snapshot object** by reference. Because the object is frozen,
  sharing it across 1000 concurrently-constructed Contexts is safe with no
  copying and no locking.
- Only the parts of Context that are genuinely per-invocation (the
  triggering `event`, and a narrow per-strategy view like "my open orders,"
  which changes independently of the global Portfolio snapshot) are
  actually allocated fresh.
- The Context Factory is therefore, in the common case, doing reference
  assembly of already-existing immutable objects plus one small
  strategy-scoped object — not deep copying — making Context construction
  O(1) relative to Portfolio/Universe size, not O(n) per invocation.

### 6.2 Pooling
For the strategy-scoped parts that *are* freshly allocated per invocation,
a Context object pool (keyed by strategy id) is used to avoid GC pressure
at 100k events/sec — reusing the frozen-dataclass "shell" is not directly
possible with true immutability (frozen instances can't be mutated for
reuse), so pooling here specifically targets the allocator-level cost
(pre-sized memory arenas / object reuse at the interpreter level) rather
than object mutation. This is flagged as an implementation-level
optimization to be validated against real profiling data once built —
included here so the performance requirement is visible at the design
stage rather than discovered as a bottleneck after the fact.

### 6.3 What this rules out
This design explicitly rules out a Context implementation that queries
Portfolio/Risk live (a method call into the engine) at construction time
for every single hook invocation — that reintroduces both a performance
problem (live cross-engine calls at 100k/sec) and a determinism problem
(two Contexts built microseconds apart could observe different answers to
the same "live" query if the underlying engine state ticks between them,
which is exactly the kind of non-reproducible read G1 is designed to
prevent).
