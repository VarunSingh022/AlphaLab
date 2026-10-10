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

BrokerConnectorState
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
Lifecycle

↓

Research
```

Not allowed

```
Research

↓

Lifecycle
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
- `pytest -q -W error` passes, reporting **0 skipped and 0 warnings** — what CI
  runs, so a `ResourceWarning` fails it
- Every example in `examples/` runs with `-W error`
- Every benchmark in `benchmarks/` runs
- `python -m build` and `twine check dist/*` pass, and each distribution,
  installed into a clean environment, passes `tests/installed_smoke.py`
- `git diff --check` is clean
- `CHANGELOG.md` leads with an entry for the release (a test reads it)
- The public API manifest is regenerated (`python docs/api/generate_public_api.py`)
  and the release's copy kept as `docs/api/history/<version>.json`; every name
  removed or rebound since the previous release is named in the release's
  CHANGELOG section (a test reads both)
- The release certificate is regenerated **last**
  (`python -W error docs/audit/scripts/certify_release.py`), and `--check`
  passes on the tree that is tagged (CI runs it). Since v3.13 `--check` also
  fails when the certificate names other engine source than the build's, so
  a commit that changes `alphalab/` carries its re-certification

**The version is declared once** (since v3.10): `alphalab/common/_version.py`,
read by the build (`[tool.hatch.version]`) and by the package. Before v3.10 it
appeared in three places and drifted twice — v2.14.0 shipped with
`pyproject.toml` still declaring `2.13.0`. Change the declaration and
`tests/unit/test_package_metadata.py`'s assertion together;
`tests/regression/test_one_version_source.py` holds the rest, including that
the CHANGELOG leads with this version.

**Five documents carry a current-state claim and drift independently.** Touch
them together as well: `README.md`'s badges and status table,
`docs/ARCHITECTURE.md`'s *Implementation Status* section, `docs/README.md`'s
*Version* block, `ROADMAP.md`'s classification, and `nowandfuture.md`'s identity
table. `tests/regression/test_version_markers_agree.py` holds the version each
of them states to the package's (since v3.13, when `nowandfuture.md` was found
still reading 3.9.0); the prose is still yours to re-read.

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