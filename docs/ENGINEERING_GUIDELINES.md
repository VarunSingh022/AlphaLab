# AlphaLab Engineering Guidelines

## Purpose

This document defines the engineering standards used throughout AlphaLab.

Every contribution should follow these guidelines to ensure the project remains consistent, maintainable, deterministic, and production ready.

These guidelines apply to both core maintainers and external contributors.

---

# Engineering Philosophy

AlphaLab is designed around a small number of engineering principles.

- Simplicity
- Determinism
- Immutability
- Explicitness
- Testability
- Composability
- Consistency

When choosing between multiple implementations, prefer the one that best preserves these principles.

---

# Code Quality Standards

Every contribution should satisfy the following requirements.

- Ruff passes without warnings.
- MyPy passes without errors.
- Pytest passes completely.
- Public APIs remain typed.
- Code is formatted consistently.
- No dead code.
- No unused imports.

The repository should always remain in a releasable state.

---

# Python Version

AlphaLab targets

```
Python 3.12+
```

Contributors should avoid introducing compatibility code for unsupported Python versions.

---

# Type Hints

All public functions must be fully typed.

Example

```python
def calculate_sharpe(
    returns: Sequence[float],
) -> float:
```

Avoid

```python
def calculate_sharpe(returns):
```

---

# Dataclasses

Immutable dataclasses are preferred.

Example

```python
@dataclass(
    frozen=True,
    slots=True,
)
class ResearchResult: ...
```

Benefits include

- immutability
- memory efficiency
- safer concurrency

---

# Mutable State

Avoid mutable global state.

Instead

```python
new_state = Engine.run(
    state,
    payload,
)
```

State transitions should always be explicit.

---

# Functions

Functions should

- perform one task
- remain deterministic
- avoid hidden side effects
- return explicit values

Large functions should be decomposed into smaller reusable helpers.

---

# Engines

Engines expose the public API.

They should contain minimal business logic.

Preferred

```
Engine

↓

Manager

↓

State
```

Avoid placing implementation details directly inside engine classes.

---

# Managers

Managers implement business logic.

Responsibilities include

- orchestration
- state creation
- event generation
- coordination

Managers remain internal implementation details.

---

# Validation

Validation should occur before business logic.

Preferred

```
Validation

↓

Execution

↓

State

↓

Events
```

Avoid validating the same inputs multiple times.

---

# Exceptions

Each package should define package-specific exceptions.

Example

```
ResearchValidationError

OptimizationError

BrokerValidationError

MissingRateError
```

Avoid raising generic exceptions where a domain-specific error communicates intent more clearly.

**Refuse rather than default.** Where a value cannot be known honestly, raise
rather than substituting one. AlphaLab refuses a missing or stale FX rate, an
instrument in a currency the run does not settle, an unknown strategy identity, a
snapshot whose schema this build does not read, and a budget whose currency is
unstated in a multi-currency run. A wrong number that looks authoritative is
worse than an error, and `tests/regression/test_no_silent_financial_defaults.py`
sweeps the whole package for the shape.

---

# Events

Events should represent completed actions.

Examples

```
DatasetLoaded

PortfolioOptimized

OrderFilled
```

Avoid imperative names such as

```
LoadDataset

OptimizePortfolio
```

Events describe what happened, not what should happen.

---

# State Objects

Each package owns one immutable state object.

State should

- be frozen
- be serializable
- contain no business logic
- contain no mutable collections

---

# Public APIs

Expose only stable interfaces through

```
__init__.py
```

Avoid exposing internal implementation modules.

---

# Naming Conventions

## Packages

```
snake_case
```

---

## Modules

```
snake_case.py
```

---

## Classes

```
PascalCase
```

---

## Functions

```
snake_case
```

---

## Constants

```
UPPER_SNAKE_CASE
```

---

## Events

```
SomethingHappened
```

Examples

```
OrderFilled

ResearchCompleted

PortfolioOptimized
```

---

## States

```
SomethingState
```

Examples

```
ResearchState

PortfolioState

ExecutionPipelineState

StrategyStudioState
```

---

## Engines

```
SomethingEngine
```

Examples

```
ResearchEngine

PortfolioEngine

OMSEngine
```

---

# Imports

Imports should

- be grouped
- remain alphabetical
- avoid circular dependencies

Preferred

```python
from dataclasses import dataclass

from alphalab.research.state import ResearchState
```

---

# Documentation

Every public class and function should include concise docstrings.

Docstrings should explain

- purpose
- parameters
- return value

Avoid documenting implementation details.

---

# Comments

Comments should explain

**why**

rather than

**what**

Avoid

```python
# Increment i
i += 1
```

Prefer

```python
# Preserve deterministic ordering across executions.
```

---

# Testing

Every new feature should include tests.

Tests should verify

- correctness
- edge cases
- invalid inputs
- deterministic behavior

Aim for behavior-driven tests rather than implementation-specific tests.

---

# Benchmarking

Performance-sensitive components should include benchmarks.

Benchmarks belong in

```
benchmarks/
```

Benchmarks complement tests but never replace them.

---

# Backward Compatibility

Public APIs should remain stable whenever possible.

Breaking changes should be introduced only in major releases.

Deprecated APIs should provide a transition path.

---

# Dependency Rules

Higher-level packages should never be imported into lower-level packages.

Example

Allowed

```
Studio

↓

Research
```

Not allowed

```
Research

↓

Workbench
```

These rules preserve architectural integrity.

---

# Pull Requests

Every pull request should

- focus on one logical change
- include tests
- update documentation if required
- pass Ruff
- pass MyPy
- pass Pytest

Large unrelated changes should be split into multiple pull requests.

---

# Release Checklist

Before creating a release, verify

- `ruff check .` and `ruff format --check .` pass
- `mypy .` passes — repository-wide, exactly as CI runs it, not `mypy alphalab`
- `pytest -q` passes, reporting **0 skipped and 0 warnings**
- `pytest -q -W error::DeprecationWarning` passes
- All 49 examples run
- All 55 benchmarks run
- `python -m build` and `twine check dist/*` pass
- `git diff --check` is clean
- `CHANGELOG.md` has an entry for the release

**The version appears in three places and they drift.** It has happened twice:
v2.14.0 shipped with `pyproject.toml` still declaring `2.13.0`, and the README's
status table was stale from v2.13 through v2.15. Update all three together:

```
pyproject.toml                      version = "X.Y.Z"
alphalab/common/version.py          the PackageNotFoundError fallback
tests/unit/test_package_metadata.py both assertions
```

**Four documents carry a current-state claim and drift independently.** Touch
them together as well: `README.md`'s badges and status table,
`docs/ARCHITECTURE.md`'s *Implementation Status* section, `docs/README.md`'s
*Version* block, and `ROADMAP.md`'s classification.

**After v3.0.0 the architecture is frozen.** A change that moves an ownership
boundary, a schema contract, or a documented invariant in `nowandfuture.md` needs
an ADR and a major release. Everything else follows the ordinary workflow.

---

# Continuous Improvement

Engineering guidelines evolve alongside AlphaLab.

Contributors are encouraged to improve these guidelines as the project grows, provided changes preserve the project's core principles of determinism, immutability, modularity, and maintainability.

---

# Summary

AlphaLab prioritizes correctness over cleverness.

Consistent engineering practices make the platform easier to understand, easier to maintain, and more reliable in production.

Every contribution should leave the codebase cleaner than it was found.