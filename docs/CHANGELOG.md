# Changelog

All notable changes to AlphaLab are documented in this file.

The project follows Semantic Versioning for tagged releases.

---

# v0.25.0

## Broker Connector Framework

Added

- Broker connector infrastructure
- Account snapshots
- Broker connections
- Broker registry
- Order manager
- Execution reports
- Position snapshots
- Validation layer
- Broker protocol
- Broker adapter
- Comprehensive unit tests
- Benchmarks

This release introduces a broker-agnostic execution layer that enables future integrations with external broker APIs without modifying AlphaLab's core architecture.

---

# v0.24.0

## Live Market Infrastructure

Added

- Live market framework
- Provider abstraction
- Subscription management
- Tick normalization
- Market snapshots
- Connection management
- Registry
- Validation
- View layer
- Benchmarks
- Unit tests

This release establishes a vendor-independent foundation for integrating real-time market data providers.

---

# v0.23.0

## Distributed Research

Added

- Distributed execution infrastructure
- Worker registration
- Job scheduling
- Worker lifecycle
- Validation
- Registry
- Benchmarks
- Comprehensive unit tests

This release introduces the foundation for distributed quantitative research and computation.

---

# v0.22.0

## Plugin SDK

Added

- Plugin infrastructure
- Plugin protocol
- Validation
- Dynamic loading
- Extensible plugin architecture
- Benchmarks
- Unit tests

This release enables future extension of AlphaLab through modular plugins.

---

# v0.21.0

## Reporting Layer

Added

- Report engine
- Markdown export
- CSV export
- JSON export
- Dashboard generation
- Validation
- Reporting events
- Unit tests
- Benchmarks

This release introduces deterministic reporting and export capabilities.

---

# v0.20.0

## Optimizer

Added

- Optimization engine
- Search space abstraction
- Objectives
- Parameter validation
- Optimization state
- Benchmarks
- Unit tests

This release establishes the optimization framework for systematic strategy research.

---

# v0.19.0

## Persistence Layer

Added

- Snapshot persistence
- Event persistence
- Serialization
- Deserialization
- Validation
- Benchmarks
- Unit tests

This release introduces deterministic persistence for replayable research workflows.

---

# Earlier Development

Prior to version **0.19.0**, AlphaLab established the core architecture of the framework, including:

- Core domain models
- Event system
- Kernel
- Strategy engine
- Replay engine
- Scheduler
- Market engine
- Feed engine
- Portfolio engine
- Risk engine
- Order Management System (OMS)
- Execution engine
- Analytics
- Runtime supervision

These foundational components formed the basis for the modular architecture that subsequent releases continue to expand.

---

## Future

Planned future releases include:

- Strategy Validation Engine
- Production Runtime
- Paper Trading
- Broker API integrations
- Live execution
- Cloud deployment
- Advanced visualization
- Additional optimization techniques

Future milestones are documented in `ROADMAP.md`.