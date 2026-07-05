# ADR-0006: Strategy Studio

## Status

Accepted

---

# Context

As AlphaLab expanded, research workflows began spanning multiple independent subsystems.

Users required a unified environment capable of coordinating datasets, strategies, pipelines, reports, and experiments.

Managing these workflows through individual engines alone reduced usability.

---

# Decision

Introduce Strategy Studio as the orchestration layer for research workflows.

Strategy Studio coordinates existing subsystems without replacing their responsibilities.

Typical workflows include

- Data preparation
- Strategy configuration
- Backtesting
- Replay
- Reporting
- Pipeline execution

Strategy Studio owns workflow orchestration while preserving subsystem independence.

---

# Consequences

Benefits

- Unified research experience
- Clear workflow abstraction
- Improved reproducibility
- Reduced orchestration complexity

Trade-offs

- Additional orchestration layer
- More event coordination

---

# Alternatives Considered

Embedding orchestration logic inside Research or Workbench.

Rejected because orchestration represents its own business capability and should remain independent.