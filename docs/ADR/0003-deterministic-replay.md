# ADR-0003

## Title

Deterministic Replay

## Status

Accepted

## Decision

The same sequence of events must always produce the same sequence of states.

Randomness must be explicitly seeded.

Time must be injected rather than read from the system clock.

## Consequences

Reproducible research.

Reliable testing.

Simulation consistency.