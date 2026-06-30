# Architecture

AlphaLab follows a layered architecture.

```
Applications
│
Research
│
Strategies
│
Portfolio
│
Execution
│
Broker
│
Risk
│
Kernel
│
Event Pipeline
│
Core Domain
```

Dependencies always point downward.

Lower layers never import higher layers.

---

## Core

Defines immutable business objects.

Examples

- Orders
- Trades
- Signals
- Events

---

## Event Pipeline

Responsible for communication between components.

Every subsystem communicates through events.

---

## Kernel

Responsible for immutable state.

Every event produces a new state.

State is never modified in place.

---

## Portfolio

Responsible for financial accounting.

---

## Execution

Responsible for order execution.

---

## Broker

Responsible for interacting with exchanges.

---

## Live Trading

Responsible for real broker connectivity.