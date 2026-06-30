# State Model

AlphaLab uses immutable state.

Every state transition follows

```
Old State

+

Event

=

New State
```

Previous state is never modified.

This guarantees

- deterministic replay
- debugging
- simulation branching
- reproducibility

The root state object is SystemState.

It contains

- MarketState
- PortfolioState
- RuntimeState
- ConfigurationState

Each subsystem owns only its own state.