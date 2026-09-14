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
| `studio` / `workbench` | `SessionStarted`, `ReportGenerated`, `ProjectOpened` |

Several event names appear in more than one package — `OrderSubmitted` in both
`oms` and `broker`, `TickReceived` in both `market` and `live`. They are
different classes with different payloads, and that is deliberate: an OMS order
event is about *my* order, a broker one about the venue's handle. Routing
therefore matches the **module and the name together**, never the bare name;
matching on the name alone was a real defect, fixed in v2.16 (ADR-0032).

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