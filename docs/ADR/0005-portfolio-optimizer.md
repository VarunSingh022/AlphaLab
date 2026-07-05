# ADR-0005: Portfolio Optimizer

## Status

Accepted

---

# Context

Portfolio construction is a distinct business capability from portfolio accounting.

AlphaLab already maintains the portfolio book of record through the Portfolio package.

Optimization algorithms should remain independent of portfolio storage and execution.

---

# Decision

Introduce a dedicated Portfolio Optimizer module.

The optimizer is responsible for

- Weight generation
- Constraint enforcement
- Risk-aware optimization
- Rebalancing logic
- Transaction cost estimation

The existing Portfolio package remains the authoritative portfolio record.

Optimization results are passed to downstream execution components through immutable state transitions.

---

# Consequences

Benefits

- Separation of concerns
- Easier extension with new optimization algorithms
- Independent testing
- Cleaner architecture

Trade-offs

- Additional module boundaries
- More explicit coordination between optimizer and portfolio components

---

# Alternatives Considered

Embedding optimization directly into the Portfolio package.

Rejected because portfolio accounting and optimization represent separate business responsibilities.