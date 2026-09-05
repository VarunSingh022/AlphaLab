# ADR-0011: Canonical Market-Data Model and the Normalization Boundary

## Status

Accepted (v2.3)

---

# Context

ADR-0009 and ADR-0010 both closed by naming market-data model convergence as
deferred work. By v2.2.0 five market-data surfaces coexisted, and it was not
possible to answer "what is a Bar?" without asking which package you were in:

| Surface | Shape | On the execution path? |
|---|---|---|
| `alphalab.market` | `Decimal`, `asset_id`, venue / currency / timeframe / sequence | **Yes** — the only one |
| `alphalab.data.feed` | `float`, `symbol`, `CanonicalRecord` base | No |
| `alphalab.marketdata.feed` | `float`, `symbol`, no base class | No |
| `alphalab.live.message` | `float`, `symbol`, `provider_id` tag | No |
| `alphalab.feed.normalization` | raw dicts → `alphalab.market` | No |

Two of those were not a distinction at all. `alphalab.marketdata.feed` defined
`Quote`, `Trade`, `Bar`, `OrderBookLevel` and `OrderBook` **field for field
identically** to `alphalab.data.feed`, and `alphalab.live.message` defined a
third identical `OrderBookLevel`. Two identical definitions of one concept is
drift waiting to happen: a provider adapter and the data engine could not
exchange a bar without a copy, and a fix applied to one copy would silently
miss the other.

The remaining difference — `data.Bar` versus `market.Bar` — was real, and the
brief singled it out. `market.Bar` carries `Decimal` OHLCV, an `asset_id`, a
timeframe, a vwap and a trade count, and is validated. `data.Bar` carries
`float` OHLCV and a provider `symbol`, and carries none of the rest.

There was also no boundary between the two. `alphalab.feed.normalization` lifted
raw *dicts* into `alphalab.market` types, but nothing lifted the typed wire
records, and nothing stated what normalization guaranteed — about precision,
timestamps, symbol identity, or what happens to a record that is invalid,
missing a field, or simply too old to act on.

---

# Decision

**One canonical domain model, one canonical wire record, and an explicit
boundary between them.**

## The canonical domain model is `alphalab.market`

| Concept | Canonical type |
|---|---|
| Top of book | `alphalab.market.quote.Quote` |
| Trade print | `alphalab.market.tick.Tick` |
| OHLCV bar | `alphalab.market.bar.Bar` |
| Depth book | `alphalab.market.snapshot.OrderBookSnapshot` |
| Book level | `alphalab.market.level.OrderBookLevel` |
| Stream record | `alphalab.market.record.MarketRecord` |

It wins on evidence, not preference:

- It is the only surface the integrated execution path consumes —
  `runtime.execution_pipeline`, `backtesting`, and replay through it.
- It uses `Decimal`, which is what every other money value on the execution
  path uses (`Order`, `Fill`, `Trade`, the portfolio).
- It keys on `asset_id`, which is what `OrderRequest`, `Order` and `Fill` key
  on.
- `feed.normalization` already normalized *into* it, so it was already the
  target of the one conversion that existed.

`MarketInput` and `MarketRecord` move from `alphalab.backtesting.dataset` to
`alphalab.market.record`, re-exported unchanged. A live feed adapter must be
able to produce a record without importing the backtesting package.

## The canonical wire record is `alphalab.data.feed`

`alphalab.marketdata.feed` re-exports it; the classes are now identical
objects, not merely equal shapes. `alphalab.live.message.OrderBookLevel` is the
same class.

## `data.Bar` and `market.Bar` both stay, because they are not duplicates

They sit on opposite sides of a conversion:

- A **wire record** is what a provider can fill in without knowing anything
  about AlphaLab. It is deliberately lossy: no venue, no currency, no
  timeframe, `float` prices keyed by a provider symbol.
- A **domain record** is what the execution path consumes. It knows its venue,
  its currency, its timeframe, and its precision.

Collapsing them would force one of the two to lie — either the wire record
claims a timeframe the provider never sent, or the domain record gives up
`Decimal` and `asset_id`. Keeping both and making the conversion explicit is
the honest answer, and `tests/regression/test_market_model_convergence.py`
asserts the distinction rather than leaving it to a comment.

`alphalab.live.message`'s provider-tagged messages stay for the same reason:
the `provider_id` is load-bearing for a layer that routes and validates by
provider.

## The boundary is `alphalab.market.normalization`

Everything that reaches the execution path crosses it, and it states its rules:

- **Precision.** Every number converts through `Decimal(str(value))`.
  `Decimal(0.1)` is `0.1000000000000000055511151231257827…`;
  `Decimal(str(0.1))` is `Decimal("0.1")`. Going through `str` reproduces the
  number the provider meant rather than the binary approximation that reached
  memory, which is what makes normalization deterministic.
- **No quantization.** The venue's own precision is preserved; rounding stays a
  downstream decision.
- **Identity.** A provider `symbol` becomes an `asset_id` verbatim unless a
  `SymbolMap` rewrites it.
- **What the wire cannot carry** — venue, currency, timeframe — comes from an
  explicit `NormalizationPolicy`, which names an unattributed venue `"UNKNOWN"`
  rather than guessing one.
- **What the wire does not report** — vwap, trade count, book order counts,
  trade direction — is documented as unreported rather than invented. A wire
  trade carries no aggressor flag, so no direction is inferred.
- **Invalid, missing and stale are three different failures.** Invalid raises
  (`MarketValidationError`) at the boundary rather than deeper in the path.
  Missing is defaulted and documented. Stale is neither — `is_stale` and
  `reject_stale` let the caller decide, because how old is too old is a
  property of the strategy, not of the data.

## The adapter boundary is `alphalab.market.source`

A `MarketDataSource` yields canonical `MarketRecord`s and nothing else. That is
the entire contract, so the execution path cannot tell a stored file from a
socket. `OrderingGuarantee` lets a source say whether it can promise
chronological order — a stored dataset can; a venue feed cannot, and saying so
is better than pretending.

No provider API is modelled: no HTTP, no websockets, no vendor authentication,
no reconnect loop. A vendor adapter implements the protocol in its own package
and normalizes on the way out.

---

# Consequences

Benefits

- One definition per concept. Five duplicate class definitions removed; the
  remaining distinctions are layer boundaries, and are tested as such.
- A provider adapter and the data engine exchange records without a copy.
- Normalization is deterministic and its rules are written down, so "what does
  this field mean when the venue did not send it?" has an answer.
- Historical, replay, paper and live all consume one record type, which is what
  makes the parity in ADR-0012 possible at all.
- `MarketState`'s indexes moved to `PersistentMap`, so publishing no longer
  costs O(universe): ingestion is flat at ~195k quotes/sec from a
  1-instrument universe to a 20,000-instrument one, against 22,688/sec at
  20,000 before.

Trade-offs

- **`alphalab.marketdata` now depends on `alphalab.data`.** One of the two had
  to depend on the other; the wire record is the more foundational concept and
  `data.feed` already had the shared `CanonicalRecord` identity base.
- **Two Bar types remain**, which reads like unfinished convergence until you
  read why. The regression test and this ADR are the answer.
- **`MarketState` fields are now `PersistentMap`, not `dict`.** Constructing
  one with a plain dict is a type error. Only the market engine constructs one.
- **`UnsupportedRecordError` moved** from `alphalab.backtesting.exceptions` to
  `alphalab.market.exceptions` and is re-exported. It is no longer a subclass
  of `BacktestError`; four environments publish records now, so an
  unpublishable input is not a backtest-specific fact.
- **Publishing into a one-instrument universe is ~8% slower**, where a
  persistent map costs more than copying a one-key dict. That is the trade, and
  it is the right way round.

---

# Alternatives Considered

**Promote `data.feed` to canonical and convert the execution path to floats.**
Rejected outright: `float` is the wrong type for money, and the execution path
is `Decimal` end to end. This would have propagated the lossy representation
into the OMS and the portfolio to spare one conversion at the edge.

**Collapse `market.Bar` and `data.Bar` into one superset type.** Rejected. The
union would carry a timeframe a provider never sent and a vwap it never
measured, so every wire bar would claim fields it does not have. ADR-0008
accepted a modest superset for `OrderRequest` because both shapes described the
same thing; these two do not.

**Make `alphalab.data` depend on `alphalab.marketdata` instead.** Rejected:
`data.feed` also holds corporate actions, dividends, splits, fundamentals and
economic releases, which are not market data and have no business living in a
vendor market-data package.

**Delete `alphalab.live.message` and use wire records.** Rejected: the
`provider_id` tag is what the live layer routes and validates by. Only the
untagged `OrderBookLevel` was a genuine duplicate, and only it was collapsed.

**Quantize prices during normalization.** Rejected: it would silently discard
precision a venue reported, and the correct number of decimal places is an
instrument property this boundary does not know.

**Treat stale data as a validation error.** Rejected: a stale record is
well-formed, and the threshold is a strategy decision. Making it an exception
would force every historical caller to opt out of a check that cannot apply to
them.

---

# Not in scope

- Vendor connectivity. Every provider client in `alphalab.marketdata.*` still
  raises `NotImplementedError` or depends on an injected `Transport`; none is
  wired to a real endpoint, and v2.3 does not add one.
- `alphalab.marketdata.symbols.AssetClass`, which has an `ETF` member
  `core.enums.AssetType` lacks, so it is not a provable duplicate.
- `alphalab.data`'s non-market records (corporate actions, fundamentals,
  economic releases, alternative data), which no execution path consumes.
