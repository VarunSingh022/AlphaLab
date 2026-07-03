# AlphaLab Documentation

Welcome to the AlphaLab documentation.

This directory contains the technical documentation for AlphaLab, including architecture, engineering principles, design decisions, developer guides, and project roadmap.

---

# Documentation

## Getting Started

Begin here if you are new to AlphaLab.

- [Getting Started](GETTING_STARTED.md)

---

## Architecture

Understand how AlphaLab is designed.

- [Architecture Overview](ARCHITECTURE.md)
- [System Design](SYSTEM_DESIGN.md)
- [State Model](STATE_MODEL.md)
- [Event Model](EVENT_MODEL.md)

---

## Engineering

Development standards and best practices.

- [Engineering Guidelines](ENGINEERING_GUIDELINES.md)
- [Architecture Decision Records](ADR/)

---

## Examples

Practical examples demonstrating framework usage.

- [Examples Guide](EXAMPLES.md)

Runnable examples are located in the repository's `examples/` directory.

---

## Project

Project planning and long-term direction.

- [Vision](VISION.md)
- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)

---

## Contributing

Interested in contributing?

- [Contributing Guide](CONTRIBUTING.md)

---

# Repository Structure

```text
AlphaLab/

README.md

docs/

examples/

benchmarks/

tests/

alphalab/
```

---

# Documentation Philosophy

The documentation is organized into focused guides rather than one large manual.

Each document has a single responsibility:

| Document | Purpose |
|----------|---------|
| GETTING_STARTED.md | Installation and setup |
| ARCHITECTURE.md | High-level architecture |
| SYSTEM_DESIGN.md | Technical design |
| STATE_MODEL.md | Immutable state architecture |
| EVENT_MODEL.md | Event-driven design |
| ENGINEERING_GUIDELINES.md | Coding standards |
| EXAMPLES.md | Framework examples |
| ROADMAP.md | Future milestones |
| CHANGELOG.md | Release history |
| CONTRIBUTING.md | Contribution workflow |

---

# Architecture Decision Records (ADR)

The `ADR/` directory documents important architectural decisions made during AlphaLab's development.

Each ADR explains:

- The problem
- The decision
- The rationale
- The consequences

This provides historical context for the evolution of the framework.

---

For project overview and installation instructions, return to the repository's main [README](../README.md).