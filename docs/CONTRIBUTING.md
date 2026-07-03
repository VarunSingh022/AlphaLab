# Contributing to AlphaLab

Thank you for your interest in contributing to AlphaLab.

AlphaLab is an institutional-grade quantitative trading framework built around immutable architecture, deterministic execution, and strong engineering practices. Every contribution should preserve these principles.

---

# Development Philosophy

AlphaLab values:

- Correctness over convenience
- Simplicity over cleverness
- Immutability over mutation
- Pure functions over hidden side effects
- Strong typing over implicit behavior
- Deterministic execution over non-determinism
- Maintainability over premature optimization

Every contribution should align with these goals.

---

# Development Setup

Clone the repository.

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab
```

Create a virtual environment.

```bash
python -m venv .venv
```

Activate it.

### macOS / Linux

```bash
source .venv/bin/activate
```

### Windows

```powershell
.venv\Scripts\activate
```

Install AlphaLab.

```bash
pip install -e .
```

---

# Repository Structure

```text
alphalab/
benchmarks/
configs/
docs/
examples/
tests/
```

Documentation is located inside the `docs` directory.

Examples are located inside the `examples` directory.

---

# Development Workflow

Typical workflow:

```
Fork

↓

Create Feature Branch

↓

Implement

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

Open Pull Request
```

---

# Code Style

AlphaLab follows a strict coding style.

## Formatting

Use Ruff.

```bash
ruff format .
```

---

## Linting

```bash
ruff check .
```

---

## Static Type Checking

```bash
mypy .
```

All code must pass MyPy.

---

## Testing

Run the complete test suite.

```bash
pytest
```

New functionality should include corresponding unit tests.

---

# Architecture Guidelines

## Immutable State

State objects must be immutable.

Use frozen dataclasses whenever appropriate.

Avoid mutable shared state.

---

## Pure Functional Engines

Engines should not mutate existing objects.

Instead they should return new immutable state.

Example:

```python
new_state = Engine.process(old_state, event)
```

---

## Deterministic Behavior

Given identical input, AlphaLab should always produce identical output.

Avoid:

- randomness
- hidden global state
- implicit ordering
- non-deterministic behavior

unless explicitly documented.

---

## Strong Typing

All public APIs should be fully typed.

Avoid `Any` whenever possible.

Prefer:

- Protocols
- Typed dataclasses
- Explicit return types

---

## Documentation

Every public module should include:

- module docstring
- class docstrings
- function docstrings

Large architectural changes should also update the documentation inside `docs/`.

---

# Testing Guidelines

Unit tests should focus on:

- correctness
- edge cases
- validation
- deterministic behavior
- state transitions
- error handling

Regression tests should accompany bug fixes whenever practical.

---

# Benchmarks

Performance-sensitive changes should include benchmark updates when appropriate.

Benchmarks are located in:

```text
benchmarks/
```

---

# Commit Messages

Use descriptive commit messages.

Examples:

```text
feat(replay): implement timestamp stepping

fix(analytics): correct sortino ratio calculation

refactor(runtime): simplify heartbeat validation

docs: update architecture guide

test(optimizer): add edge case coverage
```

---

# Pull Requests

Before opening a pull request, verify:

- Ruff passes
- MyPy passes
- PyTest passes
- Documentation is updated
- New functionality includes tests

Checklist:

- [ ] Ruff
- [ ] MyPy
- [ ] PyTest
- [ ] Documentation updated
- [ ] Tests added
- [ ] Benchmarks updated (if applicable)

---

# Reporting Issues

When reporting bugs, include:

- Operating System
- Python version
- AlphaLab version
- Steps to reproduce
- Expected behavior
- Actual behavior
- Relevant logs or tracebacks

---

# Feature Requests

Feature requests should include:

- Motivation
- Proposed solution
- Expected behavior
- Possible alternatives

---

# Code of Conduct

Please remain respectful and constructive.

AlphaLab welcomes contributions from developers of all experience levels.

Professional communication and thoughtful collaboration are expected.

---

Thank you for contributing to AlphaLab.