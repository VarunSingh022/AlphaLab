# ADR-0010: Unified Backtesting and Replay

## Status

Accepted (v2.2)

Supersedes the "replay is standalone" position of ADR-0009 for `replay` only.
Every other package listed there remains standalone.

---

# Context

ADR-0009 recorded that `alphalab.runtime.ExecutionPipeline` is the one
integrated path and that everything else — `replay` explicitly included — is a
standalone library. That was accurate, and it left two things unbuilt:

- **There was no backtest.** `examples/02_backtest.py` recorded metrics computed
  elsewhere into Strategy Studio; nothing in the codebase turned a dataset into
  orders, fills and P&L. A caller wanting one had to write the loop.
- **Replay produced nothing.** `alphalab.replay` sequenced historical events,
  tracked progress and enforced chronological order. It never reached a
  strategy, an order or a portfolio, so "deterministic replay" meant only that
  the same events came out in the same order.

Three v2.1 limitations blocked closing that gap honestly:

1. The OMS order book copied its whole order `dict` and both index `frozenset`s
   per stored order, so the execution path was still super-linear.
2. A risk-rejected request kept its allocation reservation forever.
3. `OMSState` could not be JSON-serialized as a whole state, because
   `OrderBook` keys orders by the `OrderId` dataclass.

A backtest that cannot run a large dataset, over-reports committed capital, and
cannot snapshot its own order state is not a backtest worth shipping.

---

# Decision

**Build one loop and drive it two ways.**

`alphalab.backtesting.engine.advance` is the canonical step: publish one dataset
record to the market engine, then hand the resulting market event to
`ExecutionPipeline.process_market_event`. `BacktestEngine` walks a
`MarketDataset` calling it; `ReplayBacktest` walks the same dataset through
`ReplayEngine`'s cursor calling the same function. Parity is therefore
structural — the two drivers cannot disagree about execution semantics, because
there is only one implementation of them to disagree about.

Supporting decisions:

- **One dataset type.** `MarketRecord` pairs a canonical market input (`Quote` /
  `Bar` / `Tick`) with the `event_id` and `timestamp` that
  `HistoricalEventProtocol` requires, so a `MarketDataset` satisfies both the
  backtest loop and the replay cursor. Neither driver has its own input model.
- **Fill decisions become a policy, not a call argument.** `FillPolicy` reads a
  `LiquidityContext` (what was asked, at what price, against what the event
  showed) and returns a `FillDecision`. `ExecutionPipeline` gained an optional
  `fill_policy` parameter; the previous fixed `fill_status` / `fill_quantity`
  arguments still work and are expressible as `StaticFill`.
- **Persistent containers, not copied ones.** `PersistentMap` / `PersistentSet`
  replace the order book's `dict`/`frozenset` copying, using the same
  "shared append-only storage plus copy on branch" idiom `AppendOnlyLog`
  established in v2.1.
- **A reservation ledger, not a running total.** `AllocationState.reservations`
  records committed capital per order id, so a release is attributable and a
  second release raises rather than silently double-subtracting.
- **An explicit snapshot projection, not weakened identifiers.** `OrderId` stays
  a dataclass in memory; `OMSState` declares a serializable projection in which
  orders are an array and each event carries an `event_type` tag.
- **One seeded identifier source.** `alphalab.common.ids.use_id_source` scopes
  where identifiers come from; a seeded run reproduces field for field, and the
  seed is recorded on `BacktestConfig`.

---

# Consequences

Benefits

- A backtest exists, runs the production execution path, and reuses the
  production portfolio accounting — there is no second set of books.
- Replay is a real feature: same orders, same fills, same P&L as the backtest.
- The execution path is linear in the number of events; the 100k OMS benchmark
  runs in seconds rather than minutes.
- `OMSState` snapshots round-trip, which is what persistence and replay of a
  partially-run session need.
- Committed capital is attributable and released exactly once.

Trade-offs

- **`alphalab.backtesting` deliberately depends on many packages.** It is an
  integration package, not another standalone engine; that is the point, and it
  is the only package with that licence.
- **The identifier source is ambient.** `use_id_source` binds a `ContextVar`
  rather than threading an id-source parameter through every engine method. The
  alternative would put a reproducibility argument on APIs that have nothing to
  do with reproducibility. The scope is explicit, nested, restored on exit, and
  the seed is recorded on the run.
- **`AllocationEngine.release_reservation` changed signature.** It no longer
  takes the amount to release — the ledger owns it. This is a breaking change to
  a public API, and it is what makes exactly-once releasable.
- **Persistent containers cost memory.** A key's write chain grows with the
  number of writes to it, the same profile the v2.1 append-only histories
  already accepted.
- **`OrderBook` and `PersistentSet` iterate in insertion order**, where
  `frozenset` iterated in hash order. This is stricter, not looser, but it is an
  observable change.

---

# Alternatives Considered

**A separate backtest engine with its own portfolio model.** Rejected: it would
have produced a second set of accounting rules to keep in step with
`PortfolioEngine`, which is exactly the "add another isolated package" pattern
this release set out to stop.

**Give replay its own execution loop.** Rejected for the same reason at smaller
scale: two loops mean two sets of semantics and a parity test that pins a
coincidence rather than a guarantee.

**A hash array mapped trie for the order book.** Rejected as premature. The
engines produce strictly linear state histories, for which the
`AppendOnlyLog`-style versioned store is O(1) amortized, is ~200 lines less
code, and matches an idiom already in the codebase. A HAMT would only pay off
under heavy branching, which nothing on the execution path does.

**An external persistent-map dependency (`immutables`).** Rejected: AlphaLab has
no runtime dependencies, and this problem did not require breaking that.

**Making the order book's keys strings so JSON accepts them.** Rejected: that
weakens a typed identifier to satisfy a serialization boundary. The projection
lives at the boundary instead.

---

# Not in scope

Deferred to v2.3, unchanged by this ADR:

- Market-data model convergence (`data` / `marketdata` / `feed`, and the three
  separate `Bar` types).
- `broker` / `brokers` consolidation and live broker connectivity into the
  execution path.
- Resolution of `kernel` and `core/events`, which the execution path still does
  not use.
