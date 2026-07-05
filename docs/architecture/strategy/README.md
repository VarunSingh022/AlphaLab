# Architecture Documentation

## Overview

This directory contains subsystem-specific architecture documentation for AlphaLab.

While the top-level documentation explains the overall platform, the documents in this directory describe the internal design of individual subsystems.

---

# Purpose

Architecture documents are intended for contributors who want to understand how specific packages are implemented and how they interact with the rest of the platform.

---

# Current Subsystems

## Strategy

Location:

```
architecture/strategy/
```

Documentation includes:

- Strategy Context
- Strategy API
- Signal Model
- Strategy Lifecycle
- Runtime Design
- Advanced Runtime Topics

---

# Future Subsystems

As AlphaLab evolves, additional architecture documentation will be added for:

- Research
- Universal Data
- Market Data
- Portfolio Optimizer
- Production Runtime
- Integrations
- Strategy Studio
- AlphaLab Workbench

Each subsystem will follow a consistent documentation structure to simplify navigation and maintenance.

---

# Relationship to Other Documentation

Recommended reading order:

1. README.md
2. GETTING_STARTED.md
3. ARCHITECTURE.md
4. SYSTEM_DESIGN.md
5. ENGINEERING_GUIDELINES.md
6. Architecture Subsystem Documentation

Subsystem architecture documents provide implementation details and should be read after understanding the overall platform architecture.

---

# Contributing

When introducing a major subsystem or making significant architectural changes, contributors should update or add the corresponding architecture documentation in this directory.

Keeping architecture documentation synchronized with implementation is considered part of the development process.