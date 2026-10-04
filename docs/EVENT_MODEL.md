# AlphaLab Event Model

## Overview

AlphaLab follows an event-driven architecture.

Events describe completed business actions and provide an immutable record of state transitions throughout the platform.

Events do not contain business logic. They communicate what has already occurred.

**There is no event bus, and there is no `alphalab/events` package.** An event is
an immutable value carried on the state that produced it: an engine returns a new
state whose log has grown, and a caller reads the events off that state. Each
package defines its own in its own `events.py`; `alphalab.common.events` holds
only the shared `BaseEvent`. Nothing subscribes, nothing is published, and no
delivery can be lost or reordered — which is what makes the whole platform
replayable from a dataset rather than from a broker.

---

# Event Philosophy

Events should be:

- Immutable
- Deterministic
- Explicit
- Serializable
- Domain-specific

They should never modify application state.

---

# Event Lifecycle

```
Request
    │
    ▼
Validation
    │
    ▼
Business Logic
    │
    ▼
State Transition
    │
    ▼
Event Creation
    │
    ▼
Return Updated State
```

---

# Event Structure

Every event contains:

- Event identifier
- Timestamp
- Domain-specific payload

Events may also include metadata when required.

---

# Naming Convention

Events represent completed actions.

Examples:

- DatasetLoaded
- ResearchCompleted
- PortfolioOptimized
- OrderSubmitted
- OrderFilled
- BrokerConnected
- PipelineExecuted
- BacktestCompleted

Avoid imperative names such as `LoadDataset` or `ExecutePipeline`.

---

# Package Events

Each subsystem owns its own event types.

| Package | Examples |
| --- | --- |
| `market` | `QuoteReceived`, `TradeReceived`, `BarClosed`, `BookUpdated` |
| `strategy` | `LifecycleTransitioned`, `Intent`, `FillEvent`, `OrderEvent`, `TimerEvent` |
| `allocation` | `AllocationReservationReleased` |
| `oms` | `OrderSubmitted`, `OrderAccepted`, `OrderFilled`, `OrderCancelled`, `OrderRejected` |
| `portfolio` | `PositionOpened`, `PositionReduced`, `PositionClosed`, `MarketValueUpdated`, `CashConverted` |
| `broker` | `BrokerConnected`, `BrokerDisconnected`, `ExecutionReceived`, `Heartbeat` |
| `lifecycle` | promotion, deployment and approval records, each naming its actor |
| `reporting` / `scheduler` | `ReportGenerated`, `SessionStarted` (`studio` and `workbench`, which also published events, left the library in v3.11 — ADR-0046) |

Several event names appear in more than one package — `OrderSubmitted` in
`oms` and `broker`, `ReportGenerated` in `analytics` and `reporting` (and, until
v3.10 removed `alphalab.live`, `TickReceived` in `market` and `live`). They are
different classes with different payloads, and that is deliberate: an OMS order
event is about *my* order, a broker one about the venue's handle. Routing
therefore matches the **module and the name together**, never the bare name;
matching on the name alone was a real defect, fixed in v2.16 (ADR-0032). Each
such pair is listed, with its reason, in `docs/api/public_api.json` and pinned
by `tests/regression/test_public_api_manifest.py` (v3.13). The multi-broker
connector's events, which until v3.13 reused the boundary's names for classes
with an account on them, are named for what they are: `RoutedOrderSubmitted`,
`RoutedOrderFilled`, `RoutedOrderCancelled`, `RoutedExecutionReceived`, and
`RegisteredBrokerConnected`, `RegisteredBrokerDisconnected`,
`RegisteredBrokerHeartbeat` (ledger API-001).

**What a venue reports is not an engine event** (v3.9, ADR-0044).
`core.lifecycle.ExecutionEventKind` names twelve normalized things a venue can
say — an acknowledgement, a rejection, a partial or complete fill, a cancel, an
expiry, an amendment, a refused cancel, a position, a balance, a disconnect and
a reconnect — and `broker.lifecycle.VenueEvent` carries one. An adapter
translates its venue's messages into them; `broker.apply_venue_event` judges each
against the one order-transition table and applies it to the mirror, with
exactly one outcome. They are evidence from outside, not a record of a state
change inside AlphaLab: none is appended to `BrokerState.events`, whose event
types are a closed set its snapshot decodes, and the decisions about them are
the caller's to keep.

---

# Event Ordering

Events are created after successful validation and execution.

Ordering is deterministic and reflects the exact sequence of business operations.

---

# Replay

Because events are immutable and carried on state rather than delivered, they
support deterministic replay.

Historical execution is reproduced by driving the same records through the same
step in the same order: `alphalab.backtesting.replay.ReplayBacktest` walks
`alphalab.replay`'s cursor and calls the *same* `advance` a backtest does, so
parity is structural rather than a coincidence the tests happen to observe
(ADR-0010). With a seed, identifiers reproduce too, and a run that stops can
continue on the identifier stream it left off on.

---

# Best Practices

- Keep events immutable.
- Keep payloads minimal.
- Avoid embedding business logic.
- Use events to describe outcomes, not commands.