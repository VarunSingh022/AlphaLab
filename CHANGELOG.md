# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

---

# [2.3.0] - 2026-09-05

## Overview

AlphaLab 2.3.0 is "Market Data + Broker/Live Execution". It is a connectivity
and convergence release: it establishes one canonical market-data model with an
explicit normalization boundary, one canonical broker adapter boundary, and
makes historical, replay, paper and live execution take the same canonical step
through the same engines.

It closes both items v2.2 deferred: market-data model convergence
(`data` / `market` / `marketdata` / `feed` / `live`, and the multiple `Bar`
types) and `broker` / `brokers` consolidation.

No new engine packages. Two new modules on the integrated path
(`alphalab.runtime.session`, `alphalab.runtime.broker_routing`) and three in the
market layer (`alphalab.market.record`, `.source`, `.normalization`).

**AlphaLab does not support live trading.** It supports the adapter contract a
live venue would be reached through; there is no connectivity to any real venue
in this repository. See `docs/ADR/0012` for the precise implemented /
adapter-only / absent breakdown.

See `docs/ADR/0011-canonical-market-data-model.md` and
`docs/ADR/0012-broker-boundary-and-environment-parity.md`.

---

## Added

### The market-data normalization boundary — `alphalab.market.normalization`

- `normalize_wire_quote` / `normalize_wire_trade` / `normalize_wire_bar` /
  `normalize_wire_book` lift `alphalab.data.feed` wire records into the
  canonical `alphalab.market` domain records the execution path consumes.
- Every number converts through `Decimal(str(value))`. `Decimal(0.1)` keeps the
  float's binary expansion; going through `str` keeps the number the provider
  wrote. That is what makes normalization deterministic.
- `NormalizationPolicy` supplies what the wire cannot carry (venue, currency,
  timeframe) and names an unattributed venue `"UNKNOWN"` rather than guessing.
  `SymbolMap` rewrites a provider symbol to an `asset_id`.
- Fields a wire record does not report — vwap, trade count, book order counts,
  trade direction — are documented as unreported, not invented.
- `is_stale` / `reject_stale` treat staleness as a caller decision, separate
  from validation: a stale record is well-formed, and how old is too old is a
  property of the strategy.

### The market-data adapter boundary — `alphalab.market.source`

- `MarketDataSource` yields canonical `MarketRecord`s and nothing else, so the
  execution path cannot tell a stored file from a socket.
- `SequenceSource` (finite, re-iterable, deterministic record ids),
  `OrderingGuarantee` (a source declares whether it can promise chronological
  order), `validate_ordering`.
- No provider API is modelled: no HTTP, no websockets, no vendor
  authentication, no reconnect loop.

### The canonical market record — `alphalab.market.record`

- `MarketInput` and `MarketRecord` moved here from
  `alphalab.backtesting.dataset` (re-exported unchanged), so a live feed
  adapter can produce a record without importing the backtesting package.
- `records_from_inputs` assigns deterministic, fixed-width record ids.

### Broker reconciliation — `alphalab.broker.reconciliation`

- `ExternalOrderMap` holds `oms_order_id ↔ broker_order_id` and refuses to
  rebind either direction.
- `classify_execution` is total: `APPLIED`, `DUPLICATE`, `UNKNOWN_ORDER`,
  `TERMINAL_ORDER`, `OVERFILL`, `INVALID`. Nothing is silently dropped.
- `apply_execution` is idempotent in `execution_id`; a refused fill leaves the
  state untouched.
- `ReconciliationLog` keeps every refusal, separating expected redelivery from
  a genuine break.
- `reconcile()` compares local state against a venue snapshot and produces
  `ReconciliationReport` — missing, unknown, divergent orders, divergent
  positions, cash difference. It states differences and does not resolve them.

### Trading sessions — `alphalab.runtime.session`

- `TradingSession` drives any `MarketDataSource` through
  `ExecutionPipeline.process_record`.
- `ExecutionMode` (`BACKTEST` / `REPLAY` / `PAPER` / `LIVE`) declares its own
  routing and whether its clock is moving.
- `max_market_data_age_seconds` gates stale records in a real-time session;
  skipped records are recorded with a reason rather than silently dropped.

### The venue boundary — `alphalab.runtime.broker_routing`

- `route_order` sends an accepted OMS order to a venue, with two pre-trade
  gates: never on a connection that is not `CONNECTED`, and never twice for one
  OMS order. The client order id is derived from the OMS order id, so a retry
  after a lost response addresses the same order.
- `apply_broker_execution` brings a venue fill back through
  `ExecutionPipeline.apply_execution_report` — the same function a simulated
  fill uses, so OMS, portfolio, allocation and analytics are identical either
  way.
- `routable()` projects an OMS order onto `broker.adapter.OMSOrderProtocol`,
  which the real `oms.order.Order` did not actually satisfy.

### The canonical step — `alphalab.runtime.execution_pipeline`

- `ExecutionPipeline.publish_record` and `process_record`. Every environment
  takes this step; `backtesting.engine.advance` delegates to it.
- `ExecutionPipeline.apply_execution_report` applies a report that did not come
  from the simulator.
- `ExecutionRouting` (`SIMULATED` / `EXTERNAL`) on `ExecutionPipelineConfig`.
  `EXTERNAL` leaves an accepted order working, invents no fill, and keeps its
  allocation reservation held.

### Benchmarks

- `benchmarks/benchmark_market_data.py` — normalization cost per record type
  and an ingestion sweep across universe size.

---

## Changed

### Market-data models converged

- `alphalab.marketdata.feed` re-exports `alphalab.data.feed`'s `Quote`, `Trade`,
  `Bar`, `OrderBookLevel` and `OrderBook`. They were field-for-field identical
  copies; they are now the same class objects.
- `alphalab.live.message.OrderBookLevel` is `alphalab.data.feed.OrderBookLevel`.
- `data.Bar` and `market.Bar` both remain, deliberately — different layers, not
  a duplicate. `tests/regression/test_market_model_convergence.py` asserts the
  distinction.

### Broker models converged

- `alphalab.brokers` routes the canonical types from `alphalab.broker`:
  `BrokerOrder`, `BrokerExecution` (`ExecutionReport`), `BrokerAccount`
  (`AccountSnapshot`), `BrokerPosition` (`PositionSnapshot`),
  `BrokerOrderStatus` (`OrderStatus`), and `AssetClass`, which was a fifth copy
  of `core.enums.AssetType`.
- `BrokerProtocol` covers order status, execution reception, account and
  positions. `PaperBroker` implements all of it.
- `ConnectionStatus` gains `RECONNECTING` and `FAILED`, which call for
  different behaviour: hold orders versus refuse them.
- `BrokerOrderStatus` gains `SUBMITTED` from the connector package, so
  broker-local operational states are one shared set.
- The routing events in `alphalab.brokers.events` name their identifier
  `broker_order_id` rather than `order_id`. `OrderManager` always passed the
  venue handle into that field, so only the name changes — but leaving it
  called `order_id` would have preserved, inside the converged package, exactly
  the ambiguity that decided which broker order model was canonical.

### Moved (all re-exported, no import breaks)

- `id_scope` / `id_source` → `alphalab.common.ids`, so a session can mint
  reproducible identifiers without importing the backtesting engine.
- `UnsupportedRecordError` → `alphalab.market.exceptions`. It is no longer a
  subclass of `BacktestError`; four environments publish records now.

---

## Fixed

### The broker layer was quadratic

`BrokerState` and `BrokerConnectorState` rebuilt their order, execution,
position and account indexes with `dict(old)` and grew `events` with
`(*events, e)` on every transition, so a session copied O(N²). v2.3 routes
paper and live execution through those states, which would have made this the
slowest part of a long session.

Measured, v2.2.0 → this release:

| Benchmark | v2.2.0 | v2.3.0 | Change |
| --- | --- | --- | --- |
| `benchmark_broker` (100k orders) | 676.70s / 148 per sec | 4.65s / 21,485 per sec | **145×** |
| `benchmark_live` (100k ticks) | 219.02s / 457 per sec | 1.28s / 78,338 per sec | **172×** |
| `benchmark_feed` (100k events) | 42.95s / 2,328 per sec | 0.69s / 144,489 per sec | **62×** |
| `benchmark_brokers` (10k cycles) | 2.44s / 4,106 per sec | 0.34s / 29,465 per sec | **7.2×** |
| `benchmark_marketdata` (100k trades) | did not run | 0.57s / 174,454 per sec | — |
| `benchmarks_market_engine` (quotes) | 105,670 per sec | 147,727 per sec | 1.4× |

`MarketState`, `MarketDataState`, `LiveState`, `FeedState` and
`MarketDataCache` moved to the same persistent containers.

### Market-data ingestion cost scaled with the universe

`MarketEngine.publish_*` rebuilt the whole `latest_*` index on every publish,
so a publish cost O(universe): 20k quotes into a 20,000-instrument universe ran
at 22,688 per sec against 215,808 per sec into a one-instrument universe, a
9.5× penalty that grew with the universe. Ingestion is now flat — ~195,000 per
sec at every universe size measured, 1 through 20,000.

The one regression is ~8% at universe 1, where a persistent map costs more than
copying a one-key dict. That is the trade, and it is the right way round.

### `benchmark_marketdata.py` could not run

It connected through `YahooAdapter`, whose client raises `NotImplementedError`
because it used to return hardcoded fake data. It fails identically on v2.2.0.
It now uses an explicit in-benchmark test double, which is what a benchmark of
the *engine* should have depended on. No vendor connectivity is faked.

### `oms.order.Order` did not satisfy `broker.adapter.OMSOrderProtocol`

Its `order_id` is an `OrderId`, not a string, and it has no single `price`. The
protocol claimed to decouple the broker layer from the OMS while not actually
matching it. `broker_routing.routable()` does the translation explicitly, at
the adapter boundary where it belongs.

---

## Breaking changes

Confined to `alphalab.brokers`, whose types are now the canonical ones. No
public name was removed from any package, no module disappeared, and no enum
member was removed — the breaks below are all changes of *shape*, not of
availability.

### Dataclass shapes

- `AccountSnapshot` takes `cash` / `equity` / `available_funds` instead of
  `cash_balance`, and requires the fields a venue account actually reports.
  `broker_id` and `metadata` are optional.
- `ExecutionReport` and `BrokerOrder` name their order field
  `broker_order_id`; `ExecutionReport.account_id` moved after `timestamp` and
  now defaults. `BrokerOrder` additionally requires `oms_order_id`, because a
  single `order_id` could not say whether it held AlphaLab's identifier or the
  venue's.
- **`PositionSnapshot` changed shape, not just field membership.** It was nine
  required fields (`position_id`, `account_id`, `symbol`, `asset_class`,
  `quantity`, `average_price`, `market_price`, `unrealized_pnl`,
  `realized_pnl`); it is now six required plus three defaulted:

  | | v2.2.0 | v2.3.0 |
  | --- | --- | --- |
  | `position_id` | required | **removed** — it restated the `"<account_id>:<symbol>"` key the state already stores the position under |
  | `market_value` | absent | **required (new)** |
  | `symbol`, `quantity`, `average_price`, `unrealized_pnl`, `realized_pnl` | required | required |
  | `account_id` | required | optional, defaults to `""` |
  | `asset_class` | required | optional, defaults to `AssetType.EQUITY` |
  | `market_price` | required | optional, defaults to `Decimal("0")` |

  Because the arity and the order both changed, **positional construction
  breaks**: a v2.2 call passing nine positional arguments will raise, and a
  call passing six will silently bind them to different fields. Construct with
  keywords. `market_price` and `market_value` are both kept because a venue
  reports both and they are different numbers — the mark for one unit, and the
  mark for the whole holding.

### Keyword-parameter renames — `order_id` → `broker_order_id`

Positional callers are unaffected; keyword callers break. Every affected public
entry point:

| Callable | v2.2.0 | v2.3.0 |
| --- | --- | --- |
| `BrokerConnectorEngine.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `OrderManager.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `brokers.list_executions` | `(state, order_id)` | `(state, broker_order_id)` |
| `brokers.validate_execution` | `(state, execution_id, order_id)` | `(state, execution_id, broker_order_id)` |
| `brokers.validate_order_cancellation` | `(state, order_id)` | `(state, broker_order_id)` |

The routing events in `alphalab.brokers.events` are renamed for the same
reason: `OrderSubmitted`, `OrderCancelled`, `OrderFilled` and
`ExecutionReceived` name their identifier `broker_order_id` instead of
`order_id`. The *value* was always the venue handle — `OrderManager` has always
passed `order.broker_order_id` — so only the name changes, and field order is
unchanged, leaving positional construction working. Nothing in the codebase
read the field by name.

### Enum values

Both enums are now aliases of canonical types, so their `.value` changed.
**`.name` is unchanged in every case**, and nothing in AlphaLab reads `.value`
on either — but external code that persisted or transmitted a `.value` will
read it back differently.

| Member | v2.2.0 `.value` | v2.3.0 `.value` | `.name` |
| --- | --- | --- | --- |
| `AssetClass.EQUITY` | `1` (`Enum`, `auto()`) | `"equity"` (`StrEnum`) | unchanged |
| `AssetClass.FUTURE` / `OPTION` / `FOREX` / `CRYPTO` | `2`–`5` | `"future"` / `"option"` / `"forex"` / `"crypto"` | unchanged |
| `OrderStatus.SUBMITTED` | `1` | `2` | unchanged |

`AssetClass` is now `alphalab.core.enums.AssetType` and therefore also gains a
`CASH` member; `OrderStatus` is now `alphalab.broker.order.BrokerOrderStatus`
and gains `PENDING_SUBMIT` (which takes value `1`, shifting `SUBMITTED` to `2`)
and `PENDING_CANCEL`. Both are widenings: no member was removed.

### Protocols and state containers

- `BrokerProtocol` (both packages) gained methods; a v2.2 adapter will not
  satisfy the v2.3 protocol.
- `MarketState`, `BrokerState`, `BrokerConnectorState`, `MarketDataState`,
  `LiveState` and `FeedState` fields are `PersistentMap` / `AppendOnlyLog`, not
  `dict` / `tuple`. Reads are unchanged (both are `Mapping` / `Sequence`);
  constructing one with a plain `dict` is a type error.

Every assertion in the existing tests is preserved. Only construction sites
moved.

---

## Quality

- 1737 tests pass (1599 at v2.2.0).
- `ruff check`, `ruff format --check`, `mypy --strict` clean.
- `python -m build` and `twine check dist/*` clean.
- All 11 examples run.
- 47 of 48 benchmarks run. `benchmark_workbench.py` fails on an unrelated
  workbench tab-lifecycle assertion and fails identically on v2.2.0; it is not
  in v2.3's scope.

---

# [2.2.0] - 2026-09-05

## Overview

AlphaLab 2.2.0 is "Unified Backtesting + Replay". It turns the existing research,
market-data, execution, portfolio and analytics components into one deterministic
dataset → analytics workflow, and closes the four v2.1 limitations that stood in
its way: the super-linear OMS order book, the leaked allocation reservation on a
risk rejection, the unserializable `OMSState`, and a replay engine that never
reached the execution path.

One new package, `alphalab.backtesting`. It is an *integration* package: it adds
no engine and no domain model, and composes `ExecutionPipeline` instead. There is
no backtest-only order model, no backtest-only fill model and — the point of the
release — no second set of portfolio books.

See `docs/ADR/0010-unified-backtesting-and-replay.md`.

---

## Added

### Unified backtesting — `alphalab.backtesting`

- `MarketDataset` / `MarketRecord` — an ordered, validated sequence of canonical
  market inputs (`Quote` / `Bar` / `Tick`), each carrying the `event_id` and
  `timestamp` `replay.HistoricalEventProtocol` requires. One dataset type feeds
  both drivers.
- `backtesting.engine.advance` — the canonical step: publish one record to the
  market engine, hand the resulting event to
  `ExecutionPipeline.process_market_event`.
- `BacktestEngine.run(config, dataset, strategy_state, context_factory)` — walks
  a dataset through that step and returns a `BacktestResult` with the final
  pipeline state, per-record `BacktestStep`s, the equity curve, the valuation and
  the compiled performance report.
- `BacktestConfig` — the execution-path config, the fill policy, the seed, and
  the analytics parameters, as one value.
- Read-only views: `final_equity`, `final_cash`, `realized_pnl`,
  `unrealized_pnl`, `commission_paid`, `equity_values`, `submitted_orders`,
  `executed_fills`, `steps_with_fills`, `performance_report`.

### Replay on the execution path — `alphalab.backtesting.replay`

- `ReplayBacktest.run(...)` drives `ReplayEngine`'s cursor and calls the *same*
  `advance` for every event it yields, so backtest/replay parity is structural
  rather than a coincidence the tests happen to observe.
- `alphalab.replay` itself is unchanged in responsibility: it still owns the
  cursor, the replay clock, the session lifecycle and chronological validation.
- The replay clock is the record index, not wall time, so a replay's own state is
  a pure function of its dataset.

### Execution semantics — `alphalab.execution.policy`

- `FillPolicy` decides one order's outcome at one market event from a
  `LiquidityContext` (asset, side, requested quantity, event price, size shown)
  and returns a `FillDecision`.
- `ImmediateFill` (default, fills in full), `StaticFill` (the pre-v2.2 fixed
  `fill_status` argument, as a policy) and `LiquidityCappedFill` (fills up to a
  share of the size the event showed; partial when capped, no fill when it showed
  none).
- `ExecutionPipeline.process_market_event` / `process_quote` gained an optional
  `fill_policy` parameter, which takes precedence over `fill_status` /
  `fill_quantity`. Both previous arguments still work unchanged.

### Persistent containers — `alphalab.common.persistent_map`

- `PersistentMap` and `PersistentSet`: immutable `Mapping` / `Set` with O(1)
  amortized update and structural sharing, using the same "shared append-only
  storage plus copy on branch" idiom `AppendOnlyLog` established in v2.1. Older
  versions keep observing exactly what they observed before; iteration is in
  first-insertion order.

### Deterministic identifiers — `alphalab.common.ids`

- `DeterministicIdSource(seed)` and `use_id_source(source)` scope where
  identifiers come from. `BacktestConfig.seed` installs one for a run and records
  it on the result.
- All identifier factories on the execution path now route through `new_id()`,
  `alphalab.core.ids.new_uuid` included.

### OMS snapshots — `alphalab.oms.snapshot`

- `capture` / `restore` / `from_primitives`, plus `OMSSnapshot` and
  `OMSEventRecord`.
- `alphalab.common.serialization` recognises a `__serializable__()` projection,
  which is how a type whose in-memory shape has no JSON form declares one.

### Benchmarks

- `benchmarks/benchmark_backtesting.py` — backtest and replay throughput, the
  scaling factor across a 4x workload, the replay cursor's overhead, and a parity
  assertion at both sizes.

---

## Fixed

### The OMS order book was quadratic

`OrderBook.add` rebuilt the whole order `dict` and both index `frozenset`s,
`OrderBook.replace` rebuilt the order `dict`, and `OMSEngine._update_sets`
rebuilt both order-id `frozenset`s — once per stored order, and the OMS stores an
order on submit and again on every lifecycle transition. Submitting N orders
copied O(N²) entries. All five containers are now persistent.

### The execution report index was quadratic too

Found by running the whole benchmark suite after the order-book fix, which is
the only reason it was found at all: `benchmarks_execution.py` took 85s for
100k fills. `ExecutionEngine.execute` and `partial_fill` stored a report by
rebuilding the whole `ExecutionState.reports` dict -- the same defect as the
order book, on the same execution path, paid by every fill a backtest produces.
`ExecutionState.reports` is now a `PersistentMap`; it is still an immutable
`Mapping` keyed by execution id and still serializes as the JSON object it
always did.

### Measured

On the development machine, full history retained:

| Benchmark | v2.1 | v2.2 |
| --- | --- | --- |
| `benchmark_oms` (100k order lifecycles) | 26.3 min | 6.7s |
| `benchmark_oms` scaling (10k → 20k) | 4.70x | 2.06x |
| `benchmarks_execution` (100k fills) | 85.3s | 1.55s |
| `benchmark_execution_pipeline` (4000 events) | 1.79s | 1.03s |
| `benchmark_execution_pipeline` scaling (4x workload) | ~7.4x | ~4.4x |

The residual above 4.00x in the pipeline benchmark is the cyclic garbage
collector walking a growing live heap, not an algorithmic term: with the
collector paused the same path scales 2.06x and 2.08x per doubling.

### A risk-rejected request leaked its allocation reservation

`AllocationEngine.allocate` commits capital against every request it emits.
A request that risk refused was skipped with a bare `continue`, and a request
with no market price was skipped earlier still; neither released anything, so
`notional_allocated` over-reported for the rest of the run.

`AllocationState.reservations` is now a per-order ledger. The allocation engine
owns the amount, the pipeline owns the moment, and releasing an order that holds
no live reservation raises `UnknownReservationError` instead of silently
subtracting — which is what makes "released exactly once" checkable.

### `OMSState` could not be serialized as a whole state

`OrderBook` keys orders by `OrderId`, a dataclass, which JSON cannot use as an
object key. Rather than weakening the identifier, the state now declares an
explicit projection: orders serialize as an array in submission order, the
derived indices are omitted and rebuilt on restore, and every event carries an
`event_type` tag so the log reads back as typed events.
`restore(from_primitives(deserialize(serialize(state)))) == state`, and the
restored state is a working state the engine carries on from. Values without such
a projection are still rejected by the encoder rather than stringified.

### Replay produced nothing

`alphalab.replay` sequenced events and never reached a strategy, an order or a
portfolio. It now drives the real path through `alphalab.backtesting.replay`.

---

## Changed

- **Breaking:** `AllocationEngine.release_reservation(state, order_id, timestamp)`
  no longer takes the amount to release — the ledger owns it.
- `OMSState.active_orders` / `completed_orders` are `PersistentSet[OrderId]`
  rather than `frozenset[OrderId]`. They compare equal to a `frozenset` in both
  directions and support `in`, `len` and iteration as before; iteration is now in
  insertion order rather than hash order.
- `OrderBook.orders()` and `orders_for_asset` / `orders_for_strategy` return
  orders in submission order.
- `AllocationState` gained `reservations`; `AllocationEngine` gained
  `reserved_notional`, and `alphalab.allocation` gained the `reserved_for_order`
  and `open_reservations` views.
- `benchmarks/benchmark_oms.py` reports a scaling factor and fails if it exceeds
  3.00x. It and `tests/regression/test_oms_book_complexity.py` pause the cyclic
  collector around their timed sections, because otherwise the growth ratio
  measures the collector rather than the data structure.
- `benchmark_execution_pipeline`'s scaling ceiling tightened from 12.0x to 6.0x.

---

## Tests

1599 tests pass (1429 on v2.1.0). New:

| Area | File |
| --- | --- |
| Persistent containers | `tests/unit/common/test_persistent_map.py` |
| Reservation ledger | `tests/unit/allocation/test_reservations.py` |
| Fill policies | `tests/unit/execution/test_fill_policy.py` |
| OMS snapshots | `tests/unit/oms/test_oms_snapshot.py` |
| Dataset validation | `tests/unit/backtesting/test_dataset.py` |
| Backtest loop | `tests/unit/backtesting/test_engine.py` |
| OMS complexity | `tests/regression/test_oms_book_complexity.py` |
| Execution report index complexity | `tests/regression/test_execution_reports_complexity.py` |
| Reservation leak | `tests/regression/test_risk_reservation_leak.py` |
| Whole-state OMS serialization | `tests/regression/test_oms_state_snapshot.py` |
| Run-to-run determinism | `tests/regression/test_deterministic_backtest.py` |
| Full backtest path | `tests/integration/test_backtest_pipeline.py` |
| Backtest/replay parity | `tests/integration/test_backtest_replay_parity.py` |

`tests/regression/test_state_serialization.py` no longer pins `OMSState` as
unserializable; it pins the fix, and that a raw dataclass-keyed mapping is still
rejected.

---

## Not in this release

Deferred to v2.3: market-data model convergence (`data` / `marketdata` / `feed`,
and the three separate `Bar` types), `broker` / `brokers` consolidation, and live
broker connectivity into the execution path. See `docs/ARCHITECTURE.md`,
"Known gaps and deferred areas".

---

# [2.1.0] - 2026-09-04

## Overview

AlphaLab 2.1.0 is "Execution + Portfolio Correctness". It makes the existing
execution spine correct and fast rather than adding new packages: mark-to-market,
a single monetary precision policy with an exact accounting identity, explicit
execution invariants, correct persistence of engine histories, and the fix for
the O(N^2) event accumulation that stopped the risk benchmark from completing.

No new packages. No new domain models. `PortfolioState` remains the single
canonical portfolio state and `oms.order.Order` the single lifecycle order.

---

## Breaking Changes

Public symbols or behaviour changed. Import sites and callers may need updating.

- **Engine histories are `AppendOnlyLog`, not `tuple`.** `RiskState`,
  `MarketState`, `ExecutionState`, `OMSState`, `AllocationState`,
  `PortfolioState`, `TransactionLedger` and the `ExecutionPipelineState`
  accumulators now hold `alphalab.common.AppendOnlyLog`. It is an immutable
  `Sequence` and compares equal to tuples and lists, so `len()`, indexing,
  slicing, iteration, `in`, `reversed()` and `== (...)` are unchanged. Code that
  required a literal `tuple` (`isinstance(..., tuple)`, concatenation with `+`)
  must call `.to_tuple()`.
- **`PortfolioEngine.apply_fill` rejects malformed fills** with
  `InvalidTransactionError`: non-positive price, negative commission, and a
  quantity that is zero or rounds to zero at `SHARE_QUANT`. These previously
  produced an incoherent position, a fabricated `PositionClosed` event, or a
  ledger entry for a trade that did not happen.
- **Every non-trading execution outcome is terminal for the order.** A
  rejected, expired or unfilled execution moves the OMS order to `REJECTED` /
  `EXPIRED` / `CANCELLED` and out of `active_orders`; previously it stayed
  `ACCEPTED` and open forever. `Order.reject` accordingly accepts `ACCEPTED` in
  addition to `NEW` / `PENDING`; an order that has already traded still cannot
  be rejected.
- **One portfolio snapshot per market event**, not one per fill.
  `ExecutionPipelineState.portfolio_snapshots` now also has a point for events
  that only marked the book and did not trade.
- **`DeterministicEncoder` no longer stringifies unknown objects.** `Decimal`,
  dataclasses, `AppendOnlyLog`, `Enum` and `UUID` are handled by explicit
  branch; anything else raises `SerializationError` naming the type. Callers
  that relied on the previous silent `str()` fallback were receiving payloads
  that could not be read back.
- **`AnalyticsEngine.compile_report`, `AllocationEngine.allocate` and
  `calculate_attribution`** accept `Sequence` where they previously required
  `tuple`. Existing tuple callers are unaffected.

---

## Added

- **Mark-to-market.** `PortfolioEngine.update_market_prices` is wired into
  `ExecutionPipeline.process_market_event` and runs *before* any decision is
  taken on the event, so unrealized P&L and NAV reflect the current market and
  the risk state is resynced from the marked book. It moves unrealized P&L only
  -- cash, realized P&L, commissions and the ledger are untouched. Non-positive
  prices are rejected as invalid market data and unheld assets ignored; a
  position with no price keeps its previous mark. Emits `MarketValueUpdated`
  when something was actually re-marked.

  The marked portfolio reaches **risk** only. The strategy's `StrategyContext`
  comes from the caller's `context_factory`, which the pipeline does not
  populate, and allocation sizes from market prices and its capital budget.
- **`PortfolioValuation.snapshot` / `PortfolioValuationSnapshot`** -- the
  deterministic read model over `PortfolioState`: cash, long/short/positions
  value, unrealized and realized P&L, commissions, and equity. Carried on
  `ExecutionPipelineResult.valuation` and projected into the analytics
  `PortfolioSnapshot`. Not a second portfolio state.
- **`PortfolioState.realized_pnl` and `PortfolioState.commission_paid`** --
  cumulative account totals that survive a position being closed and dropped
  from `positions`.
- **`alphalab.portfolio.money`** -- the portfolio's single monetary precision
  policy (see *Monetary precision* below), and **`Position.cost_basis`**, the
  authoritative money figure it rests on.
- **`ExecutionPipelineResult.unpriced_requests`** -- order requests dropped
  because the pipeline had no market price for the asset.
- **`alphalab.common.AppendOnlyLog`** -- immutable append-only sequence with
  O(1) amortized append and copy-on-branch structural sharing.
- **`benchmarks/benchmark_execution_pipeline.py`** -- end-to-end pipeline
  throughput and scaling benchmark.
- Tests: `tests/unit/common/test_append_log.py`,
  `tests/unit/portfolio/test_portfolio_invariants.py`,
  `tests/unit/portfolio/test_monetary_precision.py`,
  `tests/integration/test_mark_to_market_pipeline.py`,
  `tests/regression/test_event_accumulation_complexity.py`,
  `tests/regression/test_state_serialization.py`.

---

## Monetary precision

`alphalab.portfolio.money` holds one rounding policy for the whole portfolio:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter, and both the cash movement and
   the position's cost basis are derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6).

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between money in and money out; unrealized P&L is `market_value - basis`.
Splits (partial close, reversal) round one part and derive the other by
subtraction, so the parts always sum to the exact whole. `average_cost` is
derived from the basis and keeps its meaning; a `Position` constructed without a
`cost_basis` derives one from `average_cost * |quantity|`.

The accounting identity is therefore **exact** -- an identity over exact Decimal
values for any price and quantity the engine accepts, not an approximation that
happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

---

## Fixed

- **O(N^2) event/history accumulation.** Every engine grew its append-only
  history with `(*state.events, event)`, rebuilding the whole tuple on each
  transition; N transitions copied O(N^2) elements. Measured on the development
  machine, with full history retained in every case:

  | Benchmark | v2.0.0 | v2.1.0 |
  | --- | --- | --- |
  | `benchmark_risk_engine` (100k evaluations) | 285.7s | **1.4s** |
  | `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | **~167,000 ops/sec** |
  | `benchmarks_market_engine` (100k books) | 640 ops/sec | **~165,000 ops/sec** |
  | `benchmark_portfolio_engine` (20k fills) | 1.90s | **0.22s** |
  | `benchmark_execution_pipeline` (4000 events) | 6.76s | **~1.8s** |

  `benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0.
  `benchmark_portfolio_engine`'s full 100k-fill workload could not complete on
  v2.0.0 at all; it now runs in ~1.9s. End-to-end, the pipeline benchmark's
  scaling across a 4x workload dropped from ~17x to ~7.5x.
- **The accounting identity was not exact.** The cash ledger rounded
  `quantity * price + commission` while the position independently rounded
  `(exit_price - average_cost) * quantity`. Two roundings of one economic event
  disagreed by up to half a cent each and the error accumulated: an ordinary
  penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the identity out
  by $0.01, and randomized multi-asset portfolios drifted by up to $0.05. See
  *Monetary precision* above.
- **Append-only histories serialized as a repr string.** `dataclasses.asdict`
  recurses into tuples but deep-copies anything else, so an `AppendOnlyLog`
  reached `DeterministicEncoder`, whose `str()` fallback wrote
  `"events": "AppendOnlyLog([...])"` instead of a JSON array. It raised nothing
  and passed snapshot validation, so `PersistenceAdapter.to_snapshot` of a
  migrated state silently persisted unreadable history.
  `alphalab.common.dataclass_to_dict` now does its own recursion -- `asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced.
- **Realized P&L was discarded when a position closed.** Closing removes the
  position from `positions`, taking its `realized_pnl` with it, so account-level
  realized P&L was unrecoverable after a round trip. It now accumulates on
  `PortfolioState`.
- **Analytics trade records could be credited with another fill's P&L.**
  `ExecutionPipeline._trade_record` scanned the whole portfolio history in
  reverse for the asset's last realized-P&L event, so an opening fill inherited
  an earlier close's P&L and the performance report overstated realized P&L on
  every re-entry. It now reads only the events the current fill produced.
- **An order for an asset with no market price raised `KeyError`.** Allocation
  prices unknown assets at `0.00`; the pipeline now drops such requests before
  the OMS and reports them on `unpriced_requests`. The condition is per-event --
  a later quote makes the asset tradeable.
- **`FillStatus.NO_FILL` left the order open.** It now moves the order to
  `CANCELLED`, removes it from `active_orders`, and releases the allocation
  reservation exactly once -- fabricating no fill, trade or position.
- **Venue-rejected orders stayed open forever** in `oms.active_orders`, so open
  orders never reconciled with fills.
- **Positions were never repriced between fills**, so unrealized P&L, NAV, risk
  NAV and the equity curve were stale until the next trade.
- **`benchmarks/benchmarks_market_engine.py` could never run.** Its first quote
  carried timestamp `0.0`, which `market.timestamp.is_valid_timestamp` rejects
  as not strictly positive, so the benchmark raised `MarketValidationError`
  immediately. The sequence now starts at `1.0`. (Pre-existing on v2.0.0.)

---

## Not Changed

- The D1 close-fill cash accounting fix from 2.0.0 stands: realized P&L is still
  never added to cash on top of the trade proceeds.
- Commissions still stay out of a position's cost basis; `average_cost` remains
  a clean per-unit price.
- No package was added, removed, or merged. The standalone-engine /
  integrated-path split from ADR-0009 is unchanged.

---

## Known Limitations

- **The OMS order book is the execution path's remaining super-linear term.**
  `OrderBook.add` / `.replace` copy the whole order dict and
  `OMSEngine._update_sets` copies both order-id frozensets, once per stored
  order. This is a persistent-map problem, not event accumulation, and was
  deliberately left out of scope. `benchmarks/benchmark_execution_pipeline.py`
  measures it.
- **`OMSState` cannot be JSON-serialized as a whole state**, on 2.1.0 exactly as
  on 2.0.0: `OrderBook` keys orders by the `OrderId` dataclass, which neither
  `asdict` nor `json.dumps` accepts as a mapping key. Its history logs serialize
  correctly; the limitation is the typed identifier, not the log.
- **A risk-rejected request does not release its allocation reservation**, so
  `notional_allocated` over-reports after a risk rejection. Pre-existing; it
  does not gate trading, because the budget check reads
  `available_global_capital`.
- `dataclasses.asdict` cannot be extended, so it still returns an
  `AppendOnlyLog` for a history field. `alphalab.common.dataclass_to_dict` and
  `alphalab.persistence.serialize` are the supported serialization boundary.
- **Valuation is single-currency.** `PortfolioValuation` and `NAVCalculator`
  value the base currency only.
- **`_trade_record` still hard-codes** `sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` ("D3", deferred).

---

## Quality Gates

`ruff check`, `ruff format --check`, `mypy` (strict, 843 source files), `pytest`
(1429 tests), `git diff --check`, `python -m build`, and `twine check dist/*`
all pass.

---

# [2.0.0] - 2026-09-04

## Overview

AlphaLab 2.0.0 is the v2 release line. It consolidates the v1.34.0–v1.46.0 engine
series, adds four new standalone packages, unifies the canonical execution-path
domain models, and fixes two portfolio/analytics defects found by an end-to-end
trading-research validation.

AlphaLab remains a library: there is no server, daemon, scheduler process, or CLI.
`alphalab.runtime.ExecutionPipeline` is the only spine that wires domain engines
together (market → strategy → allocation → risk → OMS → execution simulator →
portfolio → analytics); every other package is an independent, individually tested
engine that is not fused into a single runtime.

---

## Breaking Changes

Public symbols removed or changed. Import sites must be updated.

- **`alphalab.core.OrderRequest` is now the single proposed-order DTO.** The
  independent `alphalab.allocation.request.OrderRequest` /
  `alphalab.allocation.request.OrderSide` and the independent
  `alphalab.risk.models.OrderRequest` / `alphalab.risk.models.OrderSide` are
  removed. `alphalab.allocation.request` no longer exists. `alphalab.risk.models`
  now contains only `RiskViolation`. `OrderSide` is no longer part of the
  `alphalab.allocation` or `alphalab.risk` public API — use
  `alphalab.core.enums.Side`.
- **`alphalab.core.enums.Side` is the canonical order direction** everywhere on
  the execution path (`OrderRequest`, `oms.order.Order`, `Fill`, `Trade`, risk
  and allocation checks). `BUY`/positive and `SELL`/negative semantics are
  unchanged.
- **`alphalab.oms.order.Order` is the canonical lifecycle order.** The
  `alphalab.core.order` module and the `alphalab.core.Order` re-export are
  removed, along with `oms.order.Order`'s unused adapter surface
  (`to_core_order`, `from_core_order`, `canonical_order`). Every
  `oms.order.Order` field, property, and lifecycle transition is unchanged.
- **`Fill.filled_at` and `Trade.executed_at` are now `float`** (Unix seconds),
  matching every other timestamp on the execution path. They were previously
  timezone-aware `datetime`; the tz-aware `__post_init__` guard is removed. In
  `dataclasses.asdict` output these fields are now numbers, not `datetime`
  objects.
- **Removed proven-dead `alphalab.core` symbols:** `OrderCompat`, `Event`
  (`core/event.py`), `Signal` (`core/signal.py`), and the
  `alphalab.core.portfolio` / `alphalab.core.position` re-export shims. Use
  `alphalab.portfolio.engine.PortfolioState` and
  `alphalab.portfolio.position.Position` directly. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is unaffected.
- **Package version** is `2.0.0`; the `alphalab.common.version` fallback and the
  `Development Status` classifier (`4 - Beta`) are updated to match.

---

## Added

### New packages (PR047–PR050)

- **Model Registry** (`alphalab.model_registry`) — versioned registration of
  trained model artifacts, `NONE → STAGING/PRODUCTION/ARCHIVED` promotion with an
  immutable promotions log, production rollback, and deployment metadata. Links
  to `alphalab.experiment_tracking` runs by id. State threaded functionally
  through immutable `ModelRegistry` values; no on-disk serialization.
- **AI Research Assistant** (`alphalab.research_assistant`) — deterministic,
  offline (no LLM, no network) grid-search research driver: candidate generation
  over a parameter grid, evaluation via a caller-supplied evaluator, best-first
  ranking, Markdown reporting, a one-call `run_research_workflow`, and a bridge
  that lifts a chosen candidate into an `alphalab.studio` `StrategyDefinition`.
- **Deployment Manager** (`alphalab.deployment_manager`) — packaging of
  strategy/model stacks into versioned, SHA-256-checksummed `ReleasePackage`s
  (manifest only, no artifact bytes), an append-only ledger of which release is
  active per environment, and previous-release rollback.
- **AlphaLab Enterprise** (`alphalab.enterprise`) — deterministic in-memory
  governance layer over one immutable `EnterpriseState`: session-lifecycle
  identity (no credentials accepted or stored), RBAC, append-only audit log,
  multi-user workspaces, secret *references* + rotation metadata (no secret
  values), and a compliance snapshot.

### Engine series (v1.34.0 – v1.46.0, PR034–PR046)

Delivered on `main` before the v2 line and included in 2.0.0: feature store,
factor library, options engine, futures engine, crypto engine, macro engine,
alternative data engine, machine learning engine, deep learning engine,
reinforcement learning engine, cloud research engine, cluster scheduler, and
experiment tracking. Each is a standalone deterministic package with its own
tests and benchmark.

---

## Changed — canonical execution domain models (R1–R4)

- **R1** — unified allocation/risk `Side` and `OrderRequest` into
  `alphalab.core` (see Breaking Changes). `alphalab.runtime.execution_pipeline`
  no longer converts requests field-by-field across the allocation → risk → OMS
  boundary; the `_risk_request` / `_core_side` bridges and
  `execution_adapters.core_side_from_oms` are deleted.
- **R2** — removed the five proven-dead `alphalab.core` symbols (see Breaking
  Changes). Implementations deleted outright; no aliases left behind.
- **R3** — retitled `alphalab.oms.order` as the canonical lifecycle order and
  removed its dormant adapter hooks; deleted `alphalab.core.order`. The order the
  execution pipeline produces is byte-for-byte identical.
- **R4** — `Fill.filled_at` / `Trade.executed_at` changed from `datetime` to
  `float`. `execution_adapters.canonical_execution_from_report` now passes
  `report.timestamp` straight through instead of
  `datetime.fromtimestamp(..., tz=UTC)`.

---

## Fixed

- **D1 (critical) — portfolio close/reduce cash accounting.**
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill` computed
  `cash_impact = -(quantity * price) - commission + pnl`. On a closing or
  reducing fill the `+ pnl` term double-counted realized P&L into cash —
  overstating cash / NAV / `PerformanceReport.ending_capital` /
  `RiskState.current_nav` on wins and understating them on losses. The `+ pnl`
  term is removed; position cost-basis math and the
  `PositionReduced` / `PositionClosed` event payloads are unchanged. Guarded by
  new unit tests (winning, losing, and partial-reduction round-trips) and
  `tests/regression/test_close_fill_cash_accounting.py`, which drives the real
  `ExecutionPipeline` open → hold → close and asserts
  `NAV == ending_capital == risk.current_nav == starting_cash + realized_pnl - commissions`.
- **D2 (medium) — `PerformanceReport` serialization.**
  `alphalab.analytics.attribution.calculate_attribution` wrapped
  `AttributionMetrics` fields in `types.MappingProxyType`, which
  `alphalab.persistence` (via `dataclasses.asdict`) cannot copy
  (`cannot pickle 'mappingproxy' object`). It now returns ordinary `dict`s, like
  every other frozen dataclass in AlphaLab. Field names and the
  `Mapping[str, Decimal]` annotations are unchanged.

---

## Build / Packaging

- The release build and distribution validation were aligned with **Core
  Metadata 2.5 / Twine 7**. Hatchling 1.30 emits `Metadata-Version: 2.5`
  (PEP 639), which Twine 6 rejects; the dev toolchain pin is now
  `twine>=7.0,<8`, and the redundant unpinned `pip install build twine` step
  was dropped from the CI and release workflows so they use the pinned
  toolchain. `python -m build` and `twine check dist/*` pass for the 2.0.0
  wheel and sdist. Hatchling is unchanged; no application dependency changed
  (`dependencies = []`).

---

## Known gaps (unchanged in 2.0.0)

- `alphalab.runtime.execution_pipeline._trade_record` still attributes realized
  P&L by scanning portfolio events in reverse and hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` (deferred — "D3").
- Mark-to-market position repricing is not implemented.
- `alphalab.replay` is a standalone engine; it does not drive `ExecutionPipeline`.
- `alphalab.data.feed.Bar` and `alphalab.market.bar.Bar` are separate,
  incompatible models (a third `Bar` lives in `alphalab.marketdata.feed`).
- The `broker` / `brokers`, `marketdata` / `data` / `feed`, `kernel`, and
  `core/events` areas remain intentionally unresolved / product-surface
  decisions.

---

## Quality gates

- ruff check, ruff format --check, mypy --strict (833 source files) — clean
- pytest — 1221 passed (1207 unit, 4 integration, 10 regression)
- `git diff --check` — clean

---

# [1.0.0] - 2026-07-05

## First Stable Release

AlphaLab 1.0.0 is the first stable public release of the framework.

This release establishes the core architecture for deterministic quantitative research, systematic strategy development, portfolio optimization, historical replay, production runtime management, and institutional research workflows.

---

## Added

### Core Framework

- Immutable domain models
- Deterministic engine APIs
- Event-driven architecture
- Shared validation utilities
- Shared registry utilities
- Common infrastructure package
- Python 3.12 support
- Strict static typing throughout the framework

### Research

- Research Engine
- Statistical research workflows
- Strategy evaluation
- Research payload validation

### Strategy Runtime

- Strategy lifecycle management
- Event dispatch
- Runtime supervision
- Context abstraction
- Intent validation

### Universal Data Engine

- Canonical datasets
- Dataset metadata
- Schema validation
- Data quality reporting
- Timeframe conversion
- Dataset cataloguing

### Replay Engine

- Historical event replay
- Deterministic market simulation
- Timeline reconstruction

### Portfolio Optimizer

- Capital allocation
- Equal Weight optimization
- Minimum Variance optimization
- Maximum Sharpe optimization
- Inverse Volatility optimization
- Portfolio constraints
- Exposure analysis
- Transaction cost estimation
- Portfolio rebalancing

### Broker Integrations

- Provider abstraction
- Paper Trading
- Alpaca integration architecture
- Interactive Brokers integration architecture
- Zerodha integration architecture
- Authentication workflows
- Connection management

### Production Runtime

- Runtime supervision
- Health monitoring
- Checkpointing
- Recovery workflows
- Runtime metrics

### Strategy Studio

- Project management
- Strategy registration
- Research sessions
- Pipelines
- Reports
- Workspace management
- Backtest orchestration

### AlphaLab Workbench

- Unified workspace
- Project management
- Research orchestration
- Dataset management
- Dashboard infrastructure

---

## Documentation

Added comprehensive documentation including:

- README
- Getting Started Guide
- Architecture Guide
- System Design
- Engineering Guidelines
- Architectural Decision Records (ADRs)
- Examples documentation
- Contributing Guide

---

## Examples

Added ten fully synchronized runnable examples covering:

1. Research Engine
2. Strategy Runtime
3. Replay Engine
4. Market Data
5. Broker Integrations
6. Portfolio Optimizer
7. Universal Data Engine
8. Strategy Studio
9. Workbench
10. Complete end-to-end workflow

---

## Engineering

Improved overall project quality through:

- Shared validation infrastructure
- Shared registry utilities
- Common package refactoring
- Consistent immutable APIs
- Packaging improvements
- Version alignment
- Example synchronization
- Release engineering

---

## Quality Assurance

Validated with:

- ✅ 583 passing unit tests
- ✅ Strict MyPy (631 source files)
- ✅ Ruff clean
- ✅ Python package build
- ✅ Wheel validation
- ✅ Source distribution validation
- ✅ Twine package verification

---

## Packaging

AlphaLab 1.0.0 is distributed as:

- Source Distribution (`sdist`)
- Universal Python Wheel (`py3-none-any`)

---

## Notes

This release establishes the stable architectural foundation of AlphaLab.

Future releases will expand the framework with additional quantitative research capabilities while maintaining backward compatibility wherever practical.