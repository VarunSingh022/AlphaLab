# Getting Started

Welcome to **AlphaLab**.

This guide walks you through setting up the development environment, running the framework, and understanding the project structure.

---

# Requirements

AlphaLab currently targets:

- Python 3.12+
- Git
- macOS, Linux or Windows
- Virtual Environment (recommended)

---

# Clone the Repository

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab
```

---

# Create a Virtual Environment

### macOS / Linux

```bash
python3 -m venv .venv
```

Activate

```bash
source .venv/bin/activate
```

### Windows

```powershell
python -m venv .venv

.venv\Scripts\activate
```

---

# Install AlphaLab

Install the package in editable mode.

```bash
pip install -e .
```

Editable mode allows changes to the source code without reinstalling the package.

---

# Verify Installation

Run the quality checks.

## Ruff

```bash
ruff check .
```

Expected

```text
All checks passed!
```

---

## MyPy

```bash
mypy .
```

Expected

```text
Success: no issues found ...
```

---

## PyTest

```bash
pytest
```

Expected

```text
414 passed
```

(The number of tests will increase as AlphaLab evolves.)

---

# Project Structure

```text
AlphaLab/

alphalab/
benchmarks/
tests/
docs/

README.md
ARCHITECTURE.md
ROADMAP.md
```

---

# Package Overview

```text
alphalab/

allocation/
analytics/
broker/
brokers/
core/
distributed/
events/
execution/
feed/
kernel/
live/
market/
oms/
optimizer/
persistence/
plugins/
portfolio/
replay/
reporting/
risk/
runtime/
scheduler/
strategy/
```

Each package is responsible for a single domain of the framework.

---

# Running the Test Suite

Run the complete test suite.

```bash
pytest
```

Run a specific package.

Example:

```bash
pytest tests/unit/replay
```

Example:

```bash
pytest tests/unit/optimizer
```

---

# Running Benchmarks

Benchmarks are available in the `benchmarks` directory.

Example:

```bash
python benchmarks/benchmark_optimizer.py
```

or

```bash
python benchmarks/benchmark_reporting.py
```

These benchmarks measure throughput and deterministic performance.

---

# Development Workflow

Typical workflow:

```text
Create feature branch

↓

Implement feature

↓

Run Ruff

↓

Run MyPy

↓

Run PyTest

↓

Commit

↓

Push

↓

Create Pull Request
```

---

# Code Quality

Every contribution must pass:

```bash
ruff check .

ruff format .

mypy .

pytest
```

before being committed.

---

# Architecture Principles

AlphaLab follows several engineering principles.

## Immutable State

State objects are never modified after creation.

Every engine returns a new immutable state.

---

## Pure Functions

Business logic is implemented using pure functions wherever possible.

---

## Deterministic Execution

Running the same input twice must produce the same output.

---

## Strong Typing

The project uses strict static typing throughout the codebase.

---

# Versioning

AlphaLab uses semantic versioning.

Example:

```text
v0.25.0
```

Major architectural milestones are tagged in Git.

---

# Learning Path

If you're new to AlphaLab, explore the modules in this order:

1. Core
2. Events
3. Strategy
4. Replay
5. Market
6. Portfolio
7. Analytics
8. Reporting
9. Optimizer
10. Live Market
11. Broker Connector

This progression mirrors the architecture of the framework.

---

# Next Reading

Continue with:

- `ARCHITECTURE.md` — System design and module responsibilities.
- `EXAMPLES.md` — Practical examples using AlphaLab.
- `CONTRIBUTING.md` — Development guidelines and coding standards.
- `ROADMAP.md` — Planned future development.

---

# Need Help?

If you encounter issues:

1. Ensure you are using Python 3.12 or newer.
2. Verify the virtual environment is activated.
3. Run:

```bash
ruff check .
mypy .
pytest
```

4. If problems persist, open an issue on the GitHub repository with:
   - Operating system
   - Python version
   - Error message
   - Steps to reproduce

Following these steps will help maintainers reproduce and resolve issues efficiently.