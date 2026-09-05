# ADR-0012: The Broker Adapter Boundary, and Parity Across Four Environments

## Status

Accepted (v2.3)

Extends ADR-0010's "one loop, two drivers" to four environments. Depends on
ADR-0011 for the canonical market record every environment consumes.

---

# Context

## Two broker packages, four duplicated concepts

`alphalab.broker` and `alphalab.brokers` each defined a broker order, an
execution, an account and a position — the same four concepts, eight types.
v2.0.0 had already made both agree on `alphalab.core.enums`, so the *enums*
matched (`tests/integration/test_domain_model_consistency.py` guards that), but
the dataclasses did not, and neither package could hand the other a value
without a copy.

The two order models differed in a way that mattered:

- `alphalab.broker.BrokerOrder` carried **both** `oms_order_id` and
  `broker_order_id`.
- `alphalab.brokers.BrokerOrder` carried a single `order_id` that could not say
  which of the two it held.

Neither package was reachable from the execution path. Nothing in
`alphalab.runtime` imported either.

## No route from a venue to the portfolio

`PaperBroker` maintained its own cash, positions and realized P&L. "Paper
trading" therefore meant a second set of books that could disagree with
`PortfolioEngine` about what a run was worth — exactly the duplication v2.2
removed from backtesting.

There was also no defined behaviour for what a broker connection actually does:
redeliver a fill after a reconnect, report fills out of order, report a fill for
an order this process never sent, or cross a cancel with a fill.

## The pipeline assumed the venue answers immediately

`ExecutionPipeline.process_market_event` submitted an order to the OMS and
executed it against the simulator within one step. A real venue answers later,
out of band, so no live session could use the path as it stood.

---

# Decision

## `alphalab.broker` is the canonical broker adapter boundary

It defines the vocabulary every adapter speaks and the contract
(`BrokerProtocol`) an adapter implements. `alphalab.brokers` becomes what it
should always have been — a router that answers *which broker, which account* —
and routes the canonical types.

`alphalab.broker` wins on one argument: **its order carries two identifiers.**
Telling AlphaLab's order apart from the venue's handle is not a naming
preference; it *is* the operation reconciliation performs. A single ambiguous
`order_id` makes reconciliation impossible.

Collapsed to one definition each: `BrokerOrder`, `BrokerExecution`
(`ExecutionReport`), `BrokerAccount` (`AccountSnapshot`), `BrokerPosition`
(`PositionSnapshot`), `BrokerOrderStatus` (the connector's `SUBMITTED` joins
`PENDING_SUBMIT` and `PENDING_CANCEL`), and `AssetClass`, which turned out to be
a fifth copy of `core.enums.AssetType`.

`PositionSnapshot.position_id` is deleted: it restated the
`"<account_id>:<symbol>"` key the state already stored the position under.

The routing events in `alphalab.brokers.events` follow the same rule as the
order model: `OrderSubmitted`, `OrderCancelled`, `OrderFilled` and
`ExecutionReceived` name their identifier `broker_order_id`. They always
carried the venue handle — `OrderManager` passed `order.broker_order_id` into
them — so this is a naming correction, not a change of content. Leaving the
field called `order_id` would have kept, inside the converged package, the very
ambiguity that decided canonicity above. `account_id` stays on these events and
is what makes them *routing* events: the single-broker equivalents in
`alphalab.broker.events` have no account to name.

Kept in `alphalab.brokers`, because they are routing and not vocabulary:
`BrokerConnection`, `BrokerType`, `BrokerRegistry`, `BrokerConnectorState`,
`BrokerStatistics`.

## Broker-local statuses stay broker-local

`PENDING_SUBMIT`, `SUBMITTED` and `PENDING_CANCEL` describe states that exist
between AlphaLab and a venue and nowhere else. They must not join
`core.enums.OrderStatus` — but there is now one set of them shared by every
adapter, rather than one per package.

## Reconciliation gets defined answers, not exceptions at the point of surprise

`alphalab.broker.reconciliation` treats the connection's failure modes as
normal, because they are:

- `ExternalOrderMap` holds `oms_order_id ↔ broker_order_id` and refuses to
  rebind either direction. Silently rebinding is how one order's fills land on
  another.
- `classify_execution` is **total**: every reported fill is `APPLIED`,
  `DUPLICATE`, `UNKNOWN_ORDER`, `TERMINAL_ORDER`, `OVERFILL` or `INVALID`.
  Nothing is silently dropped.
- A redelivered fill is a **no-op, not an error**. A reconnect makes redelivery
  routine, so treating it as a fault would make reconnecting a fault.
- **Fills are additive**, so applying two of them in either order yields the
  same order, position and cash. Out-of-order delivery therefore needs no
  special handling, and there is no `STALE` outcome to add. The property is
  asserted in `tests/regression/test_broker_reconciliation.py` rather than
  claimed in a docstring.
- **The cancel/fill race has both arrival orders defined.** Fill then cancel:
  the order fills and the cancel is refused. Cancel then fill: the fill is
  neither applied — which would resurrect a terminal order — nor discarded —
  which would hide a position the venue believes AlphaLab holds. It is recorded
  as a break for a `reconcile()` to resolve.
- `reconcile()` **states** differences and does not resolve them. Re-sending an
  order that actually exists would duplicate it, so that decision stays with
  the caller.

## One canonical step, four environments

`ExecutionPipeline.process_record` publishes a record and processes the
resulting event. `backtesting.advance` delegates to it rather than
reimplementing publish-then-process, and `runtime.session.TradingSession` drives
any `MarketDataSource` through it.

| Layer | Backtest | Replay | Paper | Live |
|---|---|---|---|---|
| Market record / event | same | same | same | same |
| Strategy → intents | same | same | same | same |
| Allocation → `OrderRequest` | same | same | same | same |
| Risk → `RiskDecision` | same | same | same | same |
| OMS order lifecycle | same | same | same | same |
| **Execution venue** | simulator | simulator | simulator | **broker** |
| `Fill` / portfolio / analytics | same | same | same | same |
| Record source | dataset | cursor | live | live |
| Clock | record ts | record ts | wall | wall |
| Staleness gate | none | none | optional | optional |

Paper is a backtest that reads from a live source. It needs no accounting,
order model or fill model of its own, and
`tests/regression/test_environment_parity.py` asserts backtest, replay and
paper produce byte-identical fills, trades, orders, cash, positions and equity
curve from one dataset.

## Live's one genuine difference is expressed in config

`ExecutionRouting.EXTERNAL` makes an accepted order stay working: no fill is
invented, the order is not closed out, and its allocation reservation stays
held — because the order is live and that capital is still committed. This is
an environmental difference living in configuration, which is where such a
difference belongs, rather than a second execution path.

`alphalab.runtime.broker_routing` is both directions of the venue boundary:

```
route_order()             OMS order  -> BrokerOrder at the venue
apply_broker_execution()  venue fill -> ExecutionReport -> Fill -> portfolio
```

The return leg goes through `ExecutionPipeline.apply_execution_report`, the
same function a simulated fill uses, so the OMS transition, the portfolio
accounting, the allocation reconciliation and the analytics record are
identical whether a fill was simulated or real.

## Two pre-trade gates

- An order is **never sent on a connection that is not `CONNECTED`**. A
  reconnecting adapter has not confirmed what the venue already holds.
- An OMS order **already bound to a venue handle is never sent again**. The
  client order id is *derived* from the OMS order id rather than minted, so a
  retry after a lost response addresses the same order instead of creating a
  second one. Determinism here is a safety property, not a convenience.

---

# Consequences

Benefits

- One vocabulary. A fill an adapter produces can be settled by the router
  without a copy, and `external_id` survives end to end.
- Paper trading has one set of books, and they are the portfolio engine's.
- The connection's real failure modes have defined, tested behaviour instead of
  an exception wherever they first surface.
- Live's boundary is implemented and tested in both directions, without
  fabricating vendor connectivity to make it look finished.
- `BrokerState` and `BrokerConnectorState` moved to persistent containers: the
  100k-order `PaperBroker` benchmark went from 676.70s to 4.65s (147.78 →
  21,485 orders/sec).

Trade-offs / breaking changes

- **`alphalab.brokers` public API changed.** `AccountSnapshot` takes
  `cash` / `equity` / `available_funds` rather than `cash_balance`, and requires
  the fields a venue account actually reports; `ExecutionReport` and
  `BrokerOrder` name their order field `broker_order_id`. Every assertion in
  the existing tests is preserved — only construction sites moved.
- **`alphalab.brokers` now depends on `alphalab.broker`.** A router over a
  boundary should depend on the boundary.
- **`BrokerProtocol` grew** to cover order status, execution reception, account
  and positions. `PaperBroker` implements all of it; a third-party adapter
  written against the v2.2 protocol will not satisfy the new one.
- **`ExecutionPipelineConfig` gained `routing`**, defaulting to `SIMULATED`,
  which is what every environment did before v2.3.
- **A terminal-order fill is surfaced, not applied.** A caller that ignores
  `ReconciliationLog.breaks` will under-report a position the venue believes it
  holds. Surfacing it is the honest answer; ignoring the surfaced break is a
  caller error the log makes visible.

---

# What is actually implemented

Stating this precisely, because "live trading support" is exactly the claim
that gets over-stated:

| | Status |
|---|---|
| Backtest, replay, paper | **Implemented**, run end to end, tested |
| Canonical broker vocabulary and `BrokerProtocol` | **Implemented** |
| `PaperBroker` | **Implemented** — a simulation, and the reference adapter |
| Order routing, fill return, reconciliation, gates | **Implemented and tested** |
| A live session driving a real venue | **Not implemented.** No connectivity to any real venue exists in this repository |
| Broker adapters (Alpaca, IB, Zerodha) in `alphalab.integrations` | **Stubs.** Deterministic canned responses -- `authenticate()` returns `True`, `account()` returns a fixed balance. None is wired to an endpoint |
| Market-data clients (Databento, NSE, Polygon, Yahoo) | **Stubs.** `NotImplementedError`, with the shape an implementation would take |
| Market-data client (Binance) | **Implemented**, and corrected here in v2.5. `alphalab.marketdata.binance` parses real `/api/v3/klines`, `/bookTicker`, `/trades` and `/depth` responses over `alphalab.marketdata.transport.HttpTransport`, which performs a real HTTP GET. It has never been run against a live endpoint from this environment, so treat it as unverified -- but it is not a stub, and this table said it was from v2.3 until v2.5 |
| A market-data client reaching the execution path | **Implemented in v2.5.** `alphalab.market.provider.ProviderHistorySource` normalizes a provider's historical bars into canonical records and satisfies `MarketDataSource`. Historical bars only: no polling, no streaming, no subscription |

> **Correction (v2.5).** The row above about vendor adapters said "every vendor
> client is a stub" from v2.3 until v2.5, and that was wrong about Binance: a
> real HTTP transport and a real Binance market-data client have existed since
> v1.39.0 (`5bfb6fa`). It remains true that no *broker* adapter reaches a venue,
> and that is what "AlphaLab does not support live trading" means.

AlphaLab does **not** support live trading. It supports the adapter contract a
live venue would be reached through, and an adapter plus its transport must be
supplied from outside this repository.

---

# Alternatives Considered

**Make `alphalab.brokers.BrokerOrder` canonical.** Rejected: its single
`order_id` cannot distinguish AlphaLab's order from the venue's, which is the
one distinction reconciliation is built on.

**Delete `alphalab.brokers` entirely.** Rejected: multi-broker registration,
per-broker connection state and per-account routing are real concerns a
single-broker adapter has no place for. What was wrong was that it also
redefined the vocabulary.

**Add the routing fields (`account_id`, `broker_id`, `asset_class`) to the
canonical types.** Accepted, as optional defaulted fields — the same modest
superset ADR-0008 accepted for `OrderRequest`. Rejected for `position_id`,
which restated a key.

**Apply a terminal-order fill anyway, because the venue is the authority.**
Rejected: it would move a terminal order back to working, and the OMS lifecycle
correctly forbids that. Surfacing the break and letting `reconcile()` resolve it
against the venue keeps the venue authoritative without corrupting local state.

**Silently drop a fill for an unknown order.** Rejected: it may belong to
another session, or AlphaLab may have lost an order it sent. Both need a human;
neither is served by silence.

**Give live its own pipeline, since the venue answers asynchronously.**
Rejected — that is the second-set-of-books mistake again. One config value
(`ExecutionRouting`) expresses the whole difference.

**Build a `PaperBroker`-driven paper mode.** Rejected: `PaperBroker`'s cash and
positions are the *venue's* books, useful for reconciliation to compare
against. Paper trading's accounting must be the portfolio engine's, which is
what routing paper through the simulator gives.

---

# Not in scope

Carried forward, unchanged by this ADR:

- Strategies still do not receive the marked portfolio; `StrategyContext` comes
  from the caller's `context_factory`. Investigated and confirmed not a blocker
  for the market-data or broker objective.
- The pipeline still mints a fresh order per market event rather than re-working
  a partially-filled one. Live routing does not depend on it: an externally
  routed order stays working and is filled through
  `apply_broker_execution`.
- An async live session loop, order-state polling, and reconnect scheduling.
  These need a transport that does not exist here.
