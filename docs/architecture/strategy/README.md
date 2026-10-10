# Strategy Runtime — Architecture Documentation

## What this directory is

Six documents written during the **design phase** of the AlphaLab Strategy
Runtime, before it was implemented. They record how each decision was reached —
the options considered, the trade-offs weighed, and the reason one was chosen.

They are kept because that reasoning is the expensive part and is not recoverable
from the code. They are **not** a description of current behaviour.

| Document | Question it answers |
| --- | --- |
| `STRATEGY_RUNTIME_DESIGN.md` | What the runtime is for, its goals, and its components |
| `STRATEGY_API.md` | What a strategy implements, and what a hook may and may not do |
| `SIGNAL_MODEL.md` | What a strategy emits — and why it is `Intent`, not an order |
| `STRATEGY_CONTEXT.md` | What a strategy is allowed to see, and what it must never see |
| `STRATEGY_LIFECYCLE.md` | The strategy lifecycle state machine |
| `STRATEGY_RUNTIME_ADVANCED_TOPICS.md` | Multi-strategy, scheduling, plugins, threading, failure recovery |

---

## Design versus implementation

**Where these documents and the code differ, the code is authoritative.** The
differences below are deliberate, and each is recorded here so a reader of the
design documents is not misled.

### What shipped as designed

- **`Intent` is what a strategy emits**, with exactly the nine fields
  `SIGNAL_MODEL.md` §4 specifies, and `alphalab.allocation` is the single place
  `Intent → OrderRequest` translation happens (`SIGNAL_MODEL.md` §3.1).
- **The hybrid type decision** (`STRATEGY_API.md` §5.3): `StrategyProtocol` is
  the canonical `typing.Protocol` and is `runtime_checkable`; `BaseStrategy` is
  the optional convenience ABC.
- **`StrategyContext` has the nine fields** `STRATEGY_CONTEXT.md` §2 lists, and
  all nine are populated by the pipeline (v2.10 for six, v2.15 for `history` and
  `universe`, v2.16 for the four protocols that were still method-less).
- **`context.orders` is not an order-placement API** (`STRATEGY_CONTEXT.md` §3).
  The only channel of effect is the `Iterable[Intent]` a hook returns.
- **Look-ahead safety** (`STRATEGY_CONTEXT.md` §5): `HistoryView` is bounded at
  the dispatched event's timestamp, taken from the **event** and never a wall
  clock, and the bound is enforced at construction.
- **`Resumed` is a transition, not a resting state** (`STRATEGY_LIFECYCLE.md`
  §4.1). `alphalab.strategy.state.StrategyStatus` has the ten members the design
  chose, and no `RESUMED`.
- **The lifecycle is a pure state machine**, `(State, Event) → State`, evaluated
  by `RuntimeSupervisor` and never a mutable field flipped in place.

### What shipped differently, and why

| Design says | What shipped |
| --- | --- |
| An **event bus** the Dispatcher consumes and the Scheduler produces onto (`DESIGN` §5.1, §5.2) | **No bus.** Events are immutable values carried on the state that produced them; `Dispatcher` is a pure routing function. This is what lets a whole run be replayed from a dataset, and it is why nothing can be lost or reordered in delivery. |
| Depth updates arrive as a **richer `QuoteEvent`** (`API` §2.1) | **No depth reaches a strategy at all.** `BookUpdated` and `SnapshotCreated` reach no hook: delivering a snapshot to `on_quote` would hand existing strategies a payload with no `quote`, and `publish_record` refuses a record that is not a quote, bar or tick. A stated boundary, pinned by the routing test (ADR-0032). |
| The **Scheduler** injects `TimerEvent`s into the runtime's event stream (`DESIGN` §5.2, `ADVANCED` §2) | **The driver fires a timer; there is no stream.** Since v3.11 `RunEngine.fire_timer` delivers a `TimerEvent` to the strategies subscribed to `timers`, and what they ask for rests until each asset's next event (ledger EXE-005). *When* a timer is due is the driver's decision, as a record's arrival is; v3.13's cron timers compute it on a stated zone's wall clock (example `69`). Until v3.11 `on_timer` was routed and never reached. |
| `on_start` / `on_stop` / `on_shutdown` run at lifecycle transitions (`API` §2, §3.1, §3.2) | **As designed, since v3.11** (ledger EXE-005): `on_start` before a strategy's first dispatch, and `on_shutdown` then `on_stop` when the run stops it, the orders `on_shutdown` asks for going through allocation, risk and the OMS. `RuntimeSupervisor` still only moves the lifecycle state and calls no hook; v4.0's `start_strategy` runs its four transitions in order and is not a second authority. Until v3.11 these were declared and never invoked. |
| `on_fill` / `on_order` are driven by OMS feedback (`DESIGN` §6.4, §7 step 10) | **Delivered after the step, since v3.11** (ledger EXE-005): a `FillEvent` carrying the strategy's attributed quantity and an `OrderEvent` for each order the step touched or dropped, to each strategy that contributed to it; what they ask for rests until the asset's next event, so a step's intent ordering is unchanged. The parity baselines moved deliberately in v3.11. |
| Every hook is wrapped in a **timeout** (`API` §3.3) | **No timeout.** A hook that raises is isolated and transitions that strategy to `FAILED`; a hook that blocks blocks the caller's own loop. AlphaLab is single-threaded and the caller owns the process. |
| The plugin loader runs a **static-analysis pass** flagging `datetime.now`, `random`, `os.environ` (`API` §3.4) | **Not implemented.** The rule stands as a documented contract; nothing enforces it mechanically. |
| `StrategyProtocol` carries an independently versioned **`strategy-api` version** (`API` §7) | **Not implemented.** The protocol is versioned with the package. What *is* versioned independently is a strategy's own durable state, through `StrategyStateProtocol.strategy_state_version` (ADR-0025). |
| Portfolio keeps **per-strategy sub-ledgers** (`ADVANCED` §1.2) | **One portfolio book.** Per-strategy attribution is the allocation *contribution ledger*: two strategies whose intents net into one order each see their own share and neither claims sole ownership (ADR-0015, ADR-0026). A regression test asserts no second portfolio model exists. Since v3.8, `portfolio.multi_strategy` holds several strategies' *own* books side by side as sleeves of one portfolio — each still its strategy's own `PortfolioState`, never a second book of record (ADR-0043). |
| Capital allocated by a **fixed, risk-parity or performance-based policy** (`ADVANCED` §1.1) | **Fixed and weighted, explicitly; no performance-based rule.** v3.8's `allocation.capital` divides capital across strategies, markets, brokers, accounts and currencies by `FixedAmounts`, `PlacementWeights` (which a risk-parity construction over strategy returns can supply, with its identity as the source) or `EqualWeights`, and refuses a plan it cannot fund rather than scaling it. No rule reallocates on realized performance (ADR-0043). |
| **Context pooling** for allocator pressure (`CONTEXT` §6.2) | **Not implemented.** Context construction is reference assembly over state the pipeline already holds, which measured at nothing. |
| **Hot reload**, dependency injection and a plugin manifest (`ADVANCED` §3) | **Not implemented.** `alphalab.strategy.registry` (v2.17) is the mapping from a declared identity to executable code: a caller registers the class it already has, and the registry never resolves a name to a module. |
| A **threading model** with sharded workers (`ADVANCED` §8) | **Not implemented.** AlphaLab is single-threaded and deterministic; the caller owns the process and may shard runs itself. |

`ADR-0016` decision 3 is normative and constrains all of the above:
`alphalab.strategy` acquires **no** dependency on `alphalab.market` or
`alphalab.instrument`. That is what keeps `Intent.instrument` a bare `str` and
keeps the strategy runtime importable without the identity authority — and it is
why event routing matches module-and-name rather than using `isinstance`.

---

## Subsystem documentation elsewhere

Other subsystems are documented in the top-level `docs/` set and in the ADRs
rather than in per-subsystem directories:

| Subsystem | Where |
| --- | --- |
| Execution path and run runtime | `docs/ARCHITECTURE.md`, ADR-0030 |
| Market data | ADR-0011, ADR-0014, ADR-0031 |
| Instrument identity and classification | ADR-0016, ADR-0027 |
| Broker boundary | ADR-0012, ADR-0031 |
| Portfolio, currency and FX | ADR-0019, ADR-0020, ADR-0028, ADR-0035 |
| Allocation, and capital allocation across strategies (v3.8) | ADR-0015, ADR-0021, ADR-0043 |
| Portfolio construction and risk | ADR-0005, ADR-0038, ADR-0043 |
| Lifecycle and governance | ADR-0013, ADR-0018, ADR-0033 |
| State, snapshots and durability | `docs/STATE_MODEL.md`, ADR-0014, ADR-0023, ADR-0029 |

---

## Relationship to other documentation

Recommended reading order:

1. `README.md`
2. `docs/GETTING_STARTED.md`
3. `docs/ARCHITECTURE.md`
4. `docs/SYSTEM_DESIGN.md`
5. `docs/ENGINEERING_GUIDELINES.md`
6. These design records
7. `nowandfuture.md` — the long-form reference for what is frozen and why

---

## Contributing

These six documents are a **historical record** and should not be rewritten to
match the code. If the implementation changes, record the change in an ADR and
update the divergence table above.

As of v3.0.0 the architecture is frozen: a change to the strategy boundary — the
hook set, what a context may expose, or where `Intent → OrderRequest` translation
happens — needs an ADR and a major release.
