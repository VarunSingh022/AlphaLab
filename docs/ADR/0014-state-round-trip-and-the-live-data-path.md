# ADR-0014: State Round-Trip and the Live Data Path

## Status

Accepted (v2.5.0).

Extends ADR-0011 (canonical market-data model) with the adapter that finally
consumes its normalization boundary, and generalises the snapshot pattern v2.2
introduced for `alphalab.oms` into a contract that other states share.

---

# Context

Three findings from the v2.5 archaeology drove this release, each verified
against v2.4.0 source rather than taken from a document.

**Serialization was one-way.** Eleven states serialize deterministically through
`alphalab.persistence.serialize`. Exactly one — `OMSState` — could be read back
into typed values. `deserialize()` returns `Any`, `PersistenceAdapter` has only
`to_stored_event` / `to_snapshot`, and nothing in `alphalab/` imported the
persistence package at all. A lifecycle that records who promoted what, and
cannot read it back, is not an audit trail.

**The v2.3 market-data boundary had no caller.** `normalize_wire_quote` and its
siblings were imported only by `alphalab.market.__init__` and their own test.
`SequenceSource` was the only `MarketDataSource`. The chain
provider client → wire record → normalization → `MarketRecord` → source →
`TradingSession` was complete except for one missing link, and so nothing used
any of it.

**Two behaviours were undefined rather than decided.** What a session does with a
source that declares `OrderingGuarantee.UNORDERED`, and what happens to the
unfilled remainder of a partially filled simulated order.

---

# Decision

## 1. Restore reconstructs semantics, not structure

`restore(capture(state)) == state` is the contract. The restored value compares
equal to the original; it does not share, and need not reproduce, internal
container lineage.

This is not a new position — it is the one the repository already holds. Every
persistent container defines value equality: `PersistentMap` inherits
`Mapping.__eq__`, `PersistentSet` inherits `Set.__eq__`, and `AppendOnlyLog`
compares `to_tuple()`. Version chains and buffer sharing are unobservable through
`==`. `oms.snapshot.restore` already rebuilds the order book's asset and strategy
indices by replaying `add`, and `test_restore_is_the_inverse_of_capture` asserts
equality against a state whose lineage is entirely different.

One rule, applied to every state. `PortfolioState` and `LifecycleState` do not get
different answers.

### Live objects are referenced, not reconstructed

A state may hold something that is not data: a trained model, a strategy
instance, a fill policy. A snapshot records what it was — a type name, a
reference — and `restore` requires the caller to supply the object itself.

`ModelVersion.model` is the case in this release. `ModelVersion.__serializable__`
already drops it in favour of `model_type` and `artifact`, because an arbitrary
Python object has no deterministic JSON form. So
`restore_lifecycle(snapshot, models={...})` takes the objects back from the
caller and **raises** when one is missing. It never substitutes `None`: a
registry silently holding nothing where a model belongs is worse than a refusal.

This is the same stance `ArtifactRef` takes — record the reference, do not
pretend to own the bytes.

### Schema version is explicit

`DEFAULT_SCHEMA_VERSION` existed in `common.constants` but nothing persisted used
it, and there is no compatibility mechanism to preserve. Every snapshot envelope
therefore carries `schema_version`, and decoding refuses a version it does not
know. There is no migration path in v2.5 because there is nothing yet to migrate
from; the field exists so that the first schema change is a decision rather than
a silent misread.

## 2. An `UNORDERED` source is honoured, not silently reordered

`MarketEngine.publish_quote` writes `latest_quotes[asset_id]` unconditionally, and
`ExecutionPipeline` marks the portfolio to whatever price it finds there. A record
arriving out of order therefore rewrites valuation backwards, and nothing in the
market layer notices — `market.validation` checks that a timestamp is *valid*,
never that it *advances*.

The architecture cannot consume unordered records safely, so v2.5 says so at the
boundary instead of guessing:

| Source declares | A record whose timestamp regresses |
| --- | --- |
| `CHRONOLOGICAL` | **Raises.** The source broke the promise it made |
| `UNORDERED` | **Skipped and recorded**, with the reason, on `SessionState.skipped` |

Nothing is buffered, reordered or held back — that would be an ordering engine,
and this release is not the place to invent one. The behaviour reuses the
`SkippedRecord` machinery the staleness gate already established, so an
out-of-order record is visible on the session rather than dropped in silence. An
`UNORDERED` source therefore cannot produce a run that merely *looks*
chronological.

## 3. A partially filled simulated order terminates its remainder

The pipeline already states its own rule, in `_close_unfilled_order`:

> The pipeline mints a fresh order per market event and never re-works an
> existing one, so an unfilled order will not be worked again and is withdrawn
> rather than left open.

Every non-trading outcome was withdrawn under that rule. A *partial* fill was the
one branch that skipped it: the order stayed `PARTIALLY_FILLED` forever, was never
revisited by any later event, and held the reservation for its unfilled remainder
indefinitely. With `LiquidityCappedFill` — added in v2.2, and the policy that makes
partial fills routine — that is a monotonically growing set of orders in a state
nothing can ever leave.

So the remainder is cancelled at the end of the execution opportunity, and its
residual reservation is released. `Order.cancel` is legal from `PARTIALLY_FILLED`
and preserves `filled_quantity` and `average_fill_price`, so the fill that did
happen is untouched.

**This changes bookkeeping, not economics.** No fill is created or destroyed, so
cash, positions, realized and unrealized P&L, the equity curve and every analytics
figure are identical. What changes is the order's final status and the held
capital that used to leak.

The alternative — keep the residual working and re-offer it on later events —
was rejected. It contradicts the one-order-per-event invariant, requires a new
pipeline stage that iterates open orders, and would change fills and P&L in every
liquidity-capped backtest ever run against this repository.

## 4. One provider adapter, over the boundary that already existed

`alphalab.market.provider` turns a provider adapter into a `MarketDataSource` by
routing its wire records through `alphalab.market.normalization`. It adds no HTTP,
no vendor API and no second provider: the Binance client, `HttpTransport`,
`StaticTransport` and the four `normalize_wire_*` functions all already existed
and were all already tested. The missing piece was the adapter between them.

---

# Consequences

Benefits

- Two more states round-trip through typed decoders, and `alphalab.persistence`
  has its first production consumer.
- A malformed, truncated or wrongly-versioned snapshot fails with a typed error
  naming the field, instead of producing a plausible wrong state.
- The v2.3 normalization boundary is on a real path, exercised end to end from a
  provider client to a `TradingSession`.
- Two previously undefined behaviours are now decided, documented and tested.
- The replay cursor is linear.

Trade-offs

- `ExecutionPipelineState` and `SessionState` still cannot round-trip. They hold
  `StrategyProtocol` instances, an `ExecutionSimulator`, a `SizingModel` and a
  `FillPolicy` — four kinds of live object, not one. Restoring them means
  reconstructing the whole run configuration, which is a larger design than this
  release should absorb. Stated as a limitation rather than half-built.
- `restore_lifecycle` needs the caller to supply model objects, so a lifecycle
  snapshot is not self-sufficient. That is a property of what a model registry
  holds, not of the snapshot format.
- Partially filled orders now end `CANCELLED`. Code asserting they stay
  `PARTIALLY_FILLED` after the event that filled them will need updating; nothing
  in this repository did.

---

# Alternatives Considered

**Restore exact internal structure.** Rejected: nothing observes it. It would
force snapshots to encode `PersistentMap` version chains — implementation detail,
larger payloads, and a format coupled to a container's internals.

**A generic dataclass-driven decoder.** Rejected: it is the "silently stringify
anything" failure in a new costume. Field-level decoding is where a wrong type or
a missing field gets caught, and a reflective decoder catches neither.

**Buffer and reorder unordered records.** Rejected: a correct reorder needs a
watermark, a delay budget and a late-arrival policy — an ordering engine, and a
new source of nondeterminism if it guesses wrong.

**Reject `UNORDERED` sources outright.** Rejected: it makes the enum member
unusable and pushes the problem to callers, who would drop records with no
record of having done so.

**Keep partial residuals working.** Rejected: see decision 3 — it changes
economics and contradicts the pipeline's stated invariant.
