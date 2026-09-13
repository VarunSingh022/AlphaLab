# ADR-0032: The Refactor Audit, and the Condition for v3.0

## Status

**Accepted and implemented in v2.16.0.** This ADR records a deliberate search
for the internal problems that would make AlphaLab worth refactoring
*immediately after* declaring v3.0 stable, the classification of every one of
them, and the six that were fixed here because leaving them would have made that
declaration untrue.

The audit's question was narrow and is worth restating exactly, because a wider
one produces cleanup rather than architecture:

> What known internal problem is **structurally required** to fix before
> AlphaLab can honestly declare v3.0.0 stable?

Twenty-one findings. Every one is classified as exactly one of **A — required before v3.0**,
**B — valid current design**, or **C — optional post-v3.0 evolution**. Nothing
is left as "maybe". The C list is deliberately *not* implemented here: pulling
it in for tidiness is the failure mode this ADR exists to avoid.

Depends on **ADR-0011** for the canonical market vocabulary, **ADR-0016** for
the layering that shapes decision 1, **ADR-0026** and **ADR-0031** for the
strategy context, **ADR-0009** for the integrated-path / standalone split, and
**ADR-0015** and **ADR-0030** for the deprecations it inspects.

---

# Context

v2.15 closed the last five capability gaps. What remained unknown was whether
the *structure* underneath them was finished, and the honest answer could only
come from looking rather than from the release notes. Two of the findings below
were created by v2.15 itself: it named a defect class precisely, fixed two
instances of it, and left four.

The audit read the repository for duplicate runtime mechanisms, duplicate domain
models, contradictory ownership, incomplete state transitions, decorative
abstractions, incorrect type contracts, inconsistent event semantics, broken
dependency direction, silent correctness failures, hot-path structural problems,
unfinished deprecations, orphan packages and unmanaged resources. Most of what
it turned up was already a decision. Six things were not.

---

# Decision

## 1. The strategy dispatcher identifies a market event exactly

`alphalab.strategy.dispatcher` selected four of its seven hooks with
`type(event).__name__ == "TickReceived"` and three siblings, under a comment
beginning "Assuming generic market events differentiate via class type or
structure". A bare class name is not a type, and this repository defines
`TickReceived`, `QuoteReceived` and `TradeReceived` in three packages.

The failure was demonstrable, not theoretical.
`alphalab.live.events.TickReceived` carries `provider_id` / `symbol` /
`tick_type` where the canonical event carries a `tick`. It was routed to
`on_tick`; the strategy read `event.tick`; the `AttributeError` landed in the
dispatcher's own handler and transitioned the strategy to **FAILED** — blamed
for a routing mistake the framework made.

**The fix is not `isinstance`, and that is the interesting part.** ADR-0016
decision 3 is normative: "`alphalab.strategy` acquires no dependency on
`alphalab.instrument` or `alphalab.market`". That layering is load-bearing — it
keeps `Intent.instrument` a bare unvalidated `str` and keeps the strategy
runtime importable without the identity authority — and
`test_instrument_identity_reaches_a_fill.py` enforces it. Importing the
canonical types into the dispatcher was tried, and the boundary test caught it.

What was actually wrong was the *precision* of the comparison, not the absence
of `isinstance`. A module and a name together identify a class exactly. The
dispatcher matches `alphalab.market.events` plus the name, so the live and
`marketdata` events no longer resolve. `alphalab.runtime.execution_pipeline`
sits above both layers, may import both, and does use `isinstance`;
`tests/regression/test_strategy_event_routing.py` checks the two mechanisms
agree class by class, so the name table cannot drift from the types it stands
for.

`BookUpdated` and `SnapshotCreated` route to no hook. That is now a stated
boundary rather than an accident of the list: `StrategyProtocol` declares no
depth hook, and delivering a snapshot to `on_quote` — whose every caller today
supplies a quote — would hand existing strategies a payload with no `quote`
attribute. `StrategyProtocol.on_quote`'s docstring claimed "Top-of-Book **or L2**
quote update" and was corrected to what is true.

## 2. Every `StrategyContext` surface declares what it supplies

ADR-0031 named the defect exactly — a field whose protocol "declares no methods,
and every construction site in the repository passes `object()`" — and fixed two
of the six instances of it. `PortfolioSnapshotProtocol`, `MarketViewProtocol`,
`RiskViewProtocol` and `OrderFacadeProtocol`, the four the pipeline had been
populating since v2.10, were left exactly as they were: empty.

This was not cosmetic. AlphaLab ships `py.typed`, so the declared type *is* the
contract a downstream strategy is checked against. Under `mypy --strict` a
strategy could write `context.history.bars(asset)` and could not write
`context.portfolio.cash("USD")` — *"PortfolioSnapshotProtocol has no attribute
cash"* — for the one surface ADR-0026 exists to supply.

The four protocols now declare what `alphalab.runtime.context_views` implements.
Payload types stay `Any` for the same reason as decision 1: a quote, a bar, an
instrument record and a risk limit cannot be named inside `alphalab.strategy`.
`Decimal` can be, and is — it is stdlib, it is the canonical monetary type
(ADR-0008), and money is where a wrong type costs something.

`NoPortfolio`, `NoMarket`, `NoRiskView` and `NoOrders` replace the `object()`
placeholders, following `NoHistory` / `NoUniverse` exactly: they answer
"nothing" and say so through `available`, so *no portfolio was supplied* stays
distinguishable from *the book is empty*. They are not defaulted onto the
dataclass, because the four fields precede `clock`, `logger` and `config` —
which are the caller's and have no null value — and reordering the fields would
break every positional construction.

## 3. The Workbench can name the tab it opened

`benchmarks/benchmark_workbench.py` crashed on its first iteration with
`WorkbenchValidationError: Tab 'bt-BT-0' is not open.`, identically at every tag
back to v2.14. The benchmark was wrong, and it had no better option.

`WorkbenchEngine.run_backtest` opens a tab named after the `result_id` Strategy
Studio *mints*, not the `backtest_id` the caller supplied. `Tab.is_active`
existed and every transition in `WorkbenchManager` maintained it, but
`alphalab.workbench.views` exposed no way to read it — so the only way to learn
the identifier was to re-implement the facade's own event scan.
`views.active_tab` is that missing surface, and the benchmark now asks the
Workbench which tab it rendered.

Fixing it exposed a fourth defect that had been unobservable for the same
reason: `WorkbenchManager.close_project` dropped every unpinned tab and, when
the active tab was among them, left the pinned survivors with no active tab at
all. The invariant — while any tab is open, exactly one is active — is now
maintained by every transition and is checkable, because something can finally
ask.

## 4. The delegation reads the events it produced, not the whole log

`WorkbenchEngine.run_backtest` found Studio's result by filtering the *entire*
Studio event log and taking the last match. Two defects in one line: the scan
is O(events) per delegation, and the filter compared a class name.

A delegation appends its own events to the end of the log and knows where the
log ended before the call, so the events it produced are the ones past that
mark. The read is bounded by the call and discriminated by type.

## 5. Studio and Workbench accumulate the canonical way

`(*state.events, evt)`, `dict(state.backtest_results)` and
`(*proj.backtests, config)` each rebuilt their whole container per call — three
quadratic terms in one loop. Measured, Workbench throughput halved on every
doubling of the workload, so the benchmark could not have completed its declared
100,000 iterations even with the tab identity fixed.

`alphalab.common` has had the answer since v2.1 and v2.2. `AppendOnlyLog` and
`PersistentMap` are what the execution path uses and what these two packages
never took. **No new mechanism was introduced** — that is the point. The
benchmark now completes 100,000 cycles in ~3.1s at ~2.1x per doubling, with full
history retained, and `benchmark_strategy_studio` went from 0.76s to 0.12s.

The other ten standalone packages that still accumulate with tuples and dicts
are **category C**, recorded below with their measurements. They are not on the
execution path and their benchmarks complete.

## 6. A portfolio optimizer refuses inconsistent inputs

Three ways of passing inconsistent arguments to
`alphalab.portfolio_optimizer.optimizer` produced a portfolio rather than an
error:

* `optimize_maximum_sharpe(("A","B","C"), (0.1, 0.2), cov_3x3)` returned weights
  for all three assets. `_matrix_vector_multiply` iterates the *vector*, so the
  third expected return and the third covariance column were dropped and C came
  out at exactly zero weight — an allocation decision made by a length mismatch
  nobody was told about.
* `optimize_inverse_volatility` read `volatilities.get(symbol, 1.0)`, so an asset
  absent from the mapping was sized as though its volatility were 1.0. Beside
  10%-vol assets that is a tenth of its proper weight.
* A covariance matrix of the wrong shape raised `IndexError` out of the
  inversion rather than this package's `OptimizationError`, so a caller handling
  the documented error type did not catch it.

A wrong allocation that looks like a right one is the worst failure this module
can have. Every one is now a refusal that names the mismatch.

**The inversion itself was examined and deliberately left alone.** It is
Gauss-Jordan elimination without partial pivoting, which reads like a defect
beside `alphalab.ml.linalg.matrix_inverse`, which pivots. It is not one:
elimination without pivoting is the standard backward-stable choice for
symmetric positive-definite matrices, which is what a covariance matrix is. A
search over 20,000 near-degenerate covariance matrices found no case where the
pivoting routine was materially more accurate, and an exactly singular one is
already refused. See decision B5.

---

# Findings not fixed, and why

## B — valid current design

Each of these is pinned by `tests/regression/test_shared_names_stay_distinct.py`,
so a future "unification" has to break an assertion and read a reason first.

| Finding | Why it stays |
| --- | --- |
| **B1. Two `OrderBook`s.** `oms.book.OrderBook` and `data.feed.OrderBook`. | One holds *my* working orders, the other *the market's* resting depth. They share no operation. Both spellings are standard in their own domain; renaming either makes one surface read wrongly. `alphalab.market` already avoids the collision by saying `OrderBookSnapshot`. |
| **B2. Two `PortfolioEngine`s.** `portfolio` and `portfolio_optimizer`. | One answers "what do I own and what is it worth", the other "what should I own". They share no operation, and neither claims the other's authority — a shared name across two packages is an import-site ambiguity, not an ownership conflict. Only the accounting engine is reachable from the execution path. |
| **B3. `optimizer` and `portfolio_optimizer`.** | Parameter search over trials, and asset weights under constraints. Their public surfaces have no name in common and neither imports the other. |
| **B4. `broker` / `brokers` overlap.** | Closed in v2.3. `brokers` routes the canonical types; `AccountSnapshot`, `PositionSnapshot`, `ExecutionReport` and `OrderStatus` *are* the `alphalab.broker` classes. The two `BrokerProtocol`s differ in the state they carry: one broker, or a registry of many. `ARCHITECTURE.md` still listed this as an open gap "deferred to v2.3", which v2.16 corrected. |
| **B5. Two matrix inversions.** | `ml.linalg` pivots because a design matrix can be arbitrarily ill-conditioned; `portfolio_optimizer` does not because a covariance matrix is SPD. Sharing one would join two packages the architecture keeps independent, to save about thirty lines, and would push the stricter routine's cost onto the caller that does not need it. |
| **B6. `kernel` has zero consumers.** | Deprecated, removed in v3.0, with an import-time notice. It is also not the duplicate it looks like: its `PortfolioState` and `PositionState` *are* the canonical `alphalab.portfolio` types. Only `MarketState` and `SystemState` are its own, and they hold `float` prices and no money. |
| **B7. `production` and `integrations` have zero consumers.** | Both deprecated and removed in v3.0, `production` through PEP 562 for the reason `test_deprecation_notices.py` records. |
| **B8. No CLI, no composition root.** | `ARCHITECTURE.md` opens with "AlphaLab is a library". Every engine is a pure function over immutable state and the caller owns the wiring, which `examples/` demonstrates thirteen times. A CLI would have to invent a configuration format, a run directory and a process lifecycle the library has no opinion about; a half-built one is worse than none. |
| **B9. No vectorized numerical layer.** | The library declares zero runtime dependencies, so there is no NumPy to vectorize onto. `ml.linalg` says so in its own docstring and bounds its claim to "small-to-moderate feature counts". |
| **B10. `data.feed.Bar` and `market.bar.Bar` are separate.** | Wire record and canonical domain record. v2.3 collapsed the *identical* pair (`marketdata.feed`) and kept this one; `test_market_model_convergence.py` asserts both halves. |
| **B11. `lifecycle` and `experiment_tracking` import `studio`**, which the layer sketch places above them. | The alternative is a second strategy-declaration type, so a candidate from `research_assistant` would need translating before it could reach a strategy version — which is the defect this repository keeps removing. `register_strategy`'s docstring already says so. What makes it safe is that `StrategyDefinition` is a frozen dataclass importing nothing but the standard library, so taking it drags no Studio machinery along; the two `studio_bridge` modules are named for the seam. |

## C — optional post-v3.0 evolution

Recorded so they are known, and deliberately **not** implemented in v2.16.

1. **Ten standalone packages still accumulate quadratically.** `scheduler`,
   `feature_store`, `integrations`, `distributed`, `plugins`, `reporting`,
   `optimizer`, `data`, `cluster_scheduler` and `portfolio_optimizer` grow
   `state.events` with tuple splats and their indexes with `dict()` copies.
   Measured: registering timers in `alphalab.scheduler` grows at ~3.2–3.8x per
   doubling from 2,500 to 20,000. None is on the execution path, every one of
   their benchmarks completes, and `ARCHITECTURE.md`'s v2.1 conversion list has
   always enumerated exactly what was converted — so no documented claim is
   false. The fix is mechanical when it is wanted.
2. **`ResearchPayload.parameters` is a `dict` on a frozen dataclass**
   (`alphalab/research/protocol.py`). The only mutable field type on any frozen
   dataclass in the package; a `Mapping` annotation would close it.
3. **`brokers.protocol.BrokerProtocol` shares a name with the canonical
   boundary.** See B4 — the contracts are genuinely different. A rename would be
   a breaking change to a public symbol for an ergonomic gain.
4. **`Dispatcher.dispatch_event` takes `event: Any`.** Narrowing it to the union
   it routes would let a caller's mistake be a static error. It cannot be done
   inside `alphalab.strategy` while ADR-0016 decision 3 stands, because naming
   the union requires the market types; doing it properly means moving hook
   selection up to `alphalab.runtime`, which changes a canonical public API.
   Worth deciding deliberately, not as a side effect.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Canonical market event vocabulary | `alphalab.market.events` (ADR-0011) — unchanged |
| Market event → strategy hook routing | `alphalab.strategy.dispatcher.MARKET_EVENT_HOOKS` |
| What a strategy may observe | `alphalab.strategy.context` protocols (ADR-0026) |
| What is overlaid onto them | `alphalab.runtime.context_views` (ADR-0031) — unchanged |
| "Nothing was supplied" | the `No*` null objects in `alphalab.strategy.context` |
| The focused workbench tab | `alphalab.workbench.views.active_tab` |
| Append-only accumulation | `alphalab.common.append_log.AppendOnlyLog` (v2.1) — unchanged |
| Keyed accumulation | `alphalab.common.persistent_map.PersistentMap` (v2.2) — unchanged |

**No new owner was created and no boundary moved.** Every fix above routes work
to an authority that already existed; that is the test each of them had to pass.

---

# Testing invariants

* `tests/regression/test_strategy_event_routing.py` — every canonical market
  event routes where it is documented to; the module is part of the match; the
  name table agrees with the canonical types class by class; a foreign class
  sharing a name reaches no hook and does not fail the strategy.
* `tests/regression/test_strategy_context_contracts.py` — no context protocol is
  decorative; the overlaid view and the null object both satisfy every declared
  member; no construction site in the repository passes a bare `object()`; the
  context still refuses an allocation surface (ADR-0026 decision 8).
* `tests/regression/test_workbench_delegation.py` — the rendered tab is
  reachable and closable; it is named after the minted result and not the
  backtest id; the facade reads only the events its own call appended and holds
  no class-name comparison; neither event log branches; the history is retained
  in full and in order; an 8x workload costs under 24x; the one-active-tab
  invariant holds across every transition.
* `tests/regression/test_optimizer_inputs_are_refused.py` — every shape mismatch
  raises `OptimizationError`; a missing volatility is refused rather than
  defaulted; consistent inputs and the empty universe are unaffected; a singular
  covariance is refused and a near-degenerate one still inverts accurately;
  weights are permutation-stable.
* `tests/regression/test_shared_names_stay_distinct.py` — the eleven B findings.
* `tests/regression/test_instrument_identity_reaches_a_fill.py` — unchanged, and
  the reason decision 1 took the shape it did.

---

# Explicit non-goals

* **Renaming a public symbol to resolve a name collision.** B1, B2 and C3 are
  ambiguities at an import site, not ownership conflicts. v3.0 is the window for
  such a change if it is ever wanted; v2.16 is not.
* **Converting the ten remaining packages to the canonical containers.** C1.
  Nothing documented is false and nothing on the execution path is affected.
* **Removing anything already deprecated.** `kernel`, `production`,
  `integrations`, `persistence.store`, `core.events`, `CommonEvent` and the
  orphan runtime lifecycle go in v3.0, on the schedule ADR-0015 and ADR-0030 set.
* **Routing depth events to a strategy hook.** Decision 1 states the boundary
  instead; giving `StrategyProtocol` a depth hook is a strategy-API decision that
  needs its own evidence.
* **Weakening `benchmark_workbench.py` to make it exit zero.** It runs its full
  declared 100,000-iteration workload and measures the same thing it always
  claimed to.

---

# Consequences

**What is now true.** Every structural finding the audit produced is classified.
Six were required and are fixed, each at an authority that already existed. Eleven
are recorded as intentional with a test that will fail if someone merges them
without reading why. Four are recorded as future evolution with the measurement
that would justify doing them.

**What a reader should take from the two half-finished items.** Decisions 1 and
2 both existed because a previous release named a defect class correctly and
fixed a subset of its instances. That is the pattern most worth watching for:
not an unknown problem, but a known one applied unevenly. Where ADR-0031 wrote
"every construction site in the repository passes `object()`", the repository
still contained fifteen of them, on four other fields of the same object.

**The v3.0 condition.** As of v2.16 there is no unclassified known structural
defect. "We could improve this later" appears only in category C, and category C
is not a refactor — it is a list of mechanical changes that touch no boundary
and can be made at any time without one.

---

# Release impact

Behavioural changes, all of them refusals or corrections of a wrong answer:

| Change | Who sees it |
| --- | --- |
| A class merely *named* `TickReceived` / `QuoteReceived` / `TradeReceived` no longer reaches a strategy hook | anyone dispatching `alphalab.live` or `alphalab.marketdata` events into `StrategyEngine` — previously their strategy failed with `HookExecutionError` |
| `optimize_maximum_sharpe` / `optimize_minimum_variance` / `optimize_inverse_volatility` raise `OptimizationError` on a shape mismatch or a missing volatility | anyone who was receiving a silently wrong portfolio, or catching `IndexError` |
| `WorkbenchManager.close_project` leaves a surviving pinned tab focused | anyone reading `Tab.is_active` |
| `StrategyStudioState`, `Project` and `WorkbenchState` hold `PersistentMap` / `AppendOnlyLog` | anyone who *constructs* one of those with a `dict` or `tuple` literal for a converted field, or appends to one by hand. Reading is unchanged — both are `Mapping` and `Sequence` — and every engine entry point still takes and returns the same types |

Additive: `alphalab.workbench.views.active_tab`; `NoPortfolio`, `NoMarket`,
`NoRiskView`, `NoOrders`, `NoHistory` and `NoUniverse` exported from
`alphalab.strategy`; members on four `StrategyContext` protocols.

Nothing is removed. `RunEngine`, `ExecutionPipeline`, the broker boundary, the
market-data boundary and the persistence boundary are untouched.
