# Engineering Guidelines

## Code Style

Python 3.12

Strict typing.

Frozen dataclasses.

Slots enabled.

No mutable globals.

---

## Quality Gates

Every PR must pass

ruff check .

ruff format .

mypy .

pytest

before review.

---

## Design Rules

Prefer composition over inheritance.

Prefer immutable data.

Avoid hidden side effects.

Keep modules focused.

Avoid circular imports.

---

## Testing

Every new public module requires

- unit tests
- typing
- documentation

---

## Documentation

Every public API must contain docstrings.

Architecture documents must be updated whenever architecture changes.