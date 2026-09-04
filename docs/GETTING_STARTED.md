# Getting Started with AlphaLab

Welcome to AlphaLab.

This guide will help you install the framework, understand its structure, and execute your first workflow.

By the end of this guide you will have

- Installed AlphaLab
- Verified your environment
- Explored the project structure
- Understood the architecture
- Run your first example
- Learned where to go next

---

# Prerequisites

AlphaLab currently requires

- Python 3.12+
- Git
- pip

A virtual environment is strongly recommended.

---

# Clone the Repository

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab
```

---

# Create a Virtual Environment

macOS / Linux

```bash
python -m venv .venv

source .venv/bin/activate
```

Windows

```powershell
python -m venv .venv

.venv\Scripts\activate
```

---

# Install AlphaLab

Install the project in editable mode.

```bash
pip install -e ".[dev]"
```

This installs AlphaLab together with development dependencies.

---

# Verify Installation

Run the validation suite.

```bash
ruff check .

mypy .

pytest
```

Expected output

```
All checks passed.

Success: no issues found.

All tests passed.
```

If all commands complete successfully, your environment is correctly configured.

---

# Repository Overview

```
AlphaLab/

alphalab/
benchmarks/
docs/
examples/
tests/
```

### alphalab/

Contains the framework source code.

---

### tests/

Contains the complete unit test suite.

---

### docs/

Contains technical documentation.

---

### examples/

Contains executable demonstrations.

---

### benchmarks/

Contains performance benchmarks.

---

# Understanding the Architecture

AlphaLab is a **library**. There is no server, daemon, or CLI — you import
packages and call their pure engine APIs.

Two kinds of package:

- **`alphalab.runtime.ExecutionPipeline`** — the one integrated path. It wires
  market data → strategy → allocation → risk → OMS → execution simulator →
  portfolio → analytics as pure functions over one immutable state snapshot.
- **Standalone engines** — `research`, `replay`, `portfolio_optimizer`, the
  learning and asset-class engines, `studio`, `workbench`, `enterprise`, and the
  rest. Each is deterministic and individually tested, but they are not chained
  together automatically.

```
Workbench   ─┐
Strategy Studio ─┤  standalone orchestration engines
Research     ─┤
Portfolio Optimizer ─┘

market event → strategy → allocation → risk → OMS → execution simulator
             → portfolio → analytics       ← alphalab.runtime.ExecutionPipeline
```

---

# First Example

Navigate to the examples directory.

```
examples/
```

Choose one of the introductory examples.

Example workflow

```
Load Dataset

↓

Run Research

↓

Optimize Portfolio

↓

Generate Report
```

Each example demonstrates one complete workflow.

---

# Understanding the Packages

Major packages include

```
research/

portfolio_optimizer/

data/

studio/

workbench/

production/

marketdata/

integrations/
```

Each package owns one business capability.

---

# Running Tests

Execute the full test suite.

```bash
pytest
```

Run a specific package.

```bash
pytest tests/unit/research
```

Run one file.

```bash
pytest tests/unit/research/test_research.py
```

---

# Code Quality

Run Ruff.

```bash
ruff check .
```

Automatically fix formatting.

```bash
ruff check . --fix
```

Run MyPy.

```bash
mypy .
```

All three commands should succeed before committing changes.

---

# Exploring Documentation

Recommended reading order

1. README.md
2. docs/GETTING_STARTED.md
3. docs/ARCHITECTURE.md
4. docs/SYSTEM_DESIGN.md
5. docs/ENGINEERING_GUIDELINES.md

---

# Common Development Workflow

Typical research workflow — you invoke each engine and pass its immutable output
to the next:

```
Acquire Data

↓

Normalize Dataset

↓

Run Research

↓

Optimize Portfolio

↓

Generate Report
```

Every stage is deterministic and uses immutable state. `replay` and the
deployment packages are separate engines; nothing chains them automatically.

---

# Learning by Examples

The fastest way to learn AlphaLab is by reading and running the examples.

Each example focuses on one subsystem.

Examples include

- Universal Data Engine
- Research Engine
- Portfolio Optimizer
- Market Data
- Broker Integrations
- Strategy Studio
- Workbench

---

# Running Benchmarks

Performance benchmarks are located in

```
benchmarks/
```

Benchmarks measure execution speed and scalability.

They complement—but do not replace—the unit test suite.

---

# Contributing

Interested in contributing?

Read

```
CONTRIBUTING.md
```

and

```
docs/ENGINEERING_GUIDELINES.md
```

before submitting changes.

---

# Getting Help

If you encounter issues

- Read the documentation
- Search existing GitHub Issues
- Open a Discussion
- Create an Issue

Please include enough information to reproduce the problem.

---

# Where to Go Next

After completing this guide, consider exploring

- Research Engine
- Universal Data Engine
- Portfolio Optimizer
- Strategy Studio
- Workbench
- Production Runtime

These modules form the core of AlphaLab.

---

# Next Steps

Congratulations!

You have successfully set up AlphaLab and are ready to begin building quantitative research workflows.

As the platform evolves, additional guides and examples will be added to demonstrate more advanced capabilities such as machine learning, distributed research, cloud execution, and enterprise deployment.

Welcome to AlphaLab.