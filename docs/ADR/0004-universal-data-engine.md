# ADR-0004: Universal Data Engine

## Status

Accepted

---

# Context

AlphaLab integrates data from multiple providers, including market data vendors, brokers, CSV files, and future alternative data sources.

Each provider exposes different schemas, timestamp formats, symbol conventions, metadata, and quality guarantees.

Allowing downstream components to interact directly with provider-specific formats would significantly increase coupling and duplicate normalization logic across the platform.

---

# Decision

Introduce a Universal Data Engine responsible for canonical data ingestion and normalization.

The Universal Data Engine acts as the single entry point for all datasets entering AlphaLab.

Its responsibilities include:

- Data ingestion
- Schema inference
- Column mapping
- Symbol normalization
- Timestamp normalization
- Metadata extraction
- Dataset validation

Downstream modules consume only canonical datasets produced by the Universal Data Engine.

---

# Consequences

Benefits

- Single normalization pipeline
- Consistent dataset representation
- Simplified downstream modules
- Easier provider integration
- Deterministic preprocessing

Trade-offs

- Additional preprocessing stage
- Slight increase in ingestion latency

These trade-offs are acceptable in exchange for architectural consistency.

---

# Alternatives Considered

Provider-specific adapters directly consumed by each subsystem.

Rejected because it duplicates normalization logic and increases coupling between data providers and research components.