# ADR-0008: Canonical Execution-Path Domain Models

## Status

Accepted (v2.0.0)

---

# Context

By v1.46.0 the execution path carried several near-duplicate domain types:

- `alphalab.allocation.request` and `alphalab.risk.models` each defined their own
  `OrderRequest` dataclass and their own `OrderSide(Enum)`. `runtime.execution_pipeline`
  converted a request field-by-field — and round-tripped the side through its
  string name — every time an intent crossed the allocation → risk → OMS boundary.
- Two "Order" models coexisted: `alphalab.core.order.Order` (a datetime-based,
  instruction-only record declared "canonical" in git history but never adopted)
  and `alphalab.oms.order.Order` (the lifecycle order the pipeline and OMS
  actually use). `oms.order.Order` carried a dormant adapter surface
  (`to_core_order` / `from_core_order` / `canonical_order`) with zero call sites.
- `core.Fill.filled_at` and `core.Trade.executed_at` were timezone-aware
  `datetime`, while every other timestamp on the path
  (`ExecutionReport.timestamp`, `oms.order.Order.created_at/updated_at`, all of
  `market` / `execution` / `analytics` / `portfolio`) was a `float` Unix second.
  The sole constructor was calling `datetime.fromtimestamp(report.timestamp, tz=UTC)`
  purely to satisfy the type, and nothing read the field except its own
  tz-aware `__post_init__` guard.
- `alphalab.core` also re-exported `OrderCompat`, `Event` (`core/event.py`),
  `Signal` (`core/signal.py`), and `core.portfolio` / `core.position` shims — all
  with zero production consumers.

The duplication produced real conversion code, invited drift between the two
copies of each type, and made "which type is canonical?" unanswerable.

---

# Decision

Establish exactly one canonical type for each concept on the execution path.

| Concept | Canonical type |
|---|---|
| Order direction | `alphalab.core.enums.Side` (`BUY` / `SELL`) |
| Proposed order (post-sizing, pre-OMS) | `alphalab.core.OrderRequest` |
| Lifecycle order | `alphalab.oms.order.Order` |
| Execution fill | `alphalab.core.Fill` (`filled_at: float`) |
| Executed trade | `alphalab.core.Trade` (`executed_at: float`) |

- `alphalab.core.OrderRequest` is a single frozen DTO whose `side` is
  `core.enums.Side`. It is a superset of both former shapes (risk's
  `notional_value` property plus allocation's `timestamp: float = 0.0`), so every
  existing call site is unchanged. `alphalab.allocation.request` is deleted;
  `alphalab.risk.models` keeps only `RiskViolation`.
- `alphalab.oms.order.Order` is the canonical lifecycle order. Its dormant
  adapter surface and `alphalab.core.order` are removed.
- `Fill.filled_at` / `Trade.executed_at` become `float`. The tz-aware
  `__post_init__` guard is dropped; `execution_adapters` passes
  `report.timestamp` straight through.
- The dead `core` symbols (`OrderCompat`, `Event`, `Signal`, `core.portfolio`,
  `core.position`) are deleted outright — no aliases left behind. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is retained.

These are the R1–R4 refactors in `CHANGELOG.md`.

---

# Consequences

Benefits

- One type per concept; `runtime.execution_pipeline` no longer converts requests
  or sides across the allocation → risk → OMS boundary.
- Timestamps are uniform `float` across the whole path — comparable and
  serializable without special-casing.
- Smaller `alphalab.core` public surface.

Trade-offs / breaking changes

- Removed public symbols: `alphalab.core.order`, `alphalab.core.event`,
  `alphalab.core.signal`, `alphalab.core.portfolio`, `alphalab.core.position`,
  `alphalab.core.OrderCompat`, `alphalab.allocation.request`,
  `OrderSide` from the `alphalab.allocation` and `alphalab.risk` public APIs.
- `Fill.filled_at` / `Trade.executed_at` change type (`datetime` → `float`);
  `dataclasses.asdict` output for these fields is now numeric.
- Shipped in a major release (v2.0.0) for this reason.

Numeric timestamp values, ordering, equality, and `BUY`/positive ↔ `SELL`/negative
semantics are preserved exactly.

---

# Alternatives Considered

**Keep `core.order.Order` as the canonical instruction model and adapt
`oms.order.Order` to it.** Rejected: `core.order.Order` had no production
consumers and no adapter call sites, while `oms.order.Order` is what the pipeline
and OMS already use. Promoting the unused type would have required rewriting the
working path to satisfy documentation.

**Leave `Fill` / `Trade` on `datetime` and convert at the boundary.** Rejected:
the conversion existed only to satisfy the type, added a UTC dependency, and made
these two fields the only non-`float` timestamps on the path.

**Deprecate the duplicate types with aliases instead of deleting them.**
Rejected for the proven-dead symbols — an alias for a symbol nothing imports adds
surface without adding value.
