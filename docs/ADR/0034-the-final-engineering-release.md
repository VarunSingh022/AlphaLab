# ADR-0034: The Final Engineering Release

## Status

**Accepted and implemented in v2.17.0.** This ADR records the work that had to
happen *before* v3.0 could be declared stable, and its scope is set by one
sentence from ADR-0032:

> **The v3.0 condition.** As of v2.16 there is no unclassified known structural
> defect. "We could improve this later" appears only in category C, and category
> C is not a refactor — it is a list of mechanical changes that touch no
> boundary and can be made at any time without one.

v2.17 makes them. Every category C item is implemented, every deprecated surface
on the v3.0 removal schedule is removed, both pytest skips are gone and the
warning count is zero. ADR-0035 records the three *capabilities* v2.17 adds;
this one records the cleanup that makes v3.0 a release rather than a rewrite.

Depends on **ADR-0032** for the classifications it closes, **ADR-0029** and
**ADR-0030** for the deprecations it executes, and **ADR-0015** for the removal
schedule.

---

# Context

ADR-0032 classified twenty-one findings and deliberately implemented none of
category C, on the grounds that pulling it in for tidiness is the failure mode
that ADR exists to avoid. That was right for v2.16 and is wrong for the release
before an API freeze: a name that means two things, a mutable field on a frozen
value, and an `Any` on a public boundary all become permanent the moment v3.0
declares stability.

The same argument applies harder to the deprecations. Seven surfaces carried a
`DeprecationWarning` and the string "removed in v3.0". Carrying them *into* v3.0
and removing them there would make the stable release a breaking one; removing
them here makes v3.0 additive.

---

# Decision

## 1. The four category C items are implemented, and one was misdiagnosed

**C1 — quadratic state accumulation in ten standalone packages.** Two of the ten
(`integrations`, `production`) were removed instead, under decision 3. The other
eight are converted to `AppendOnlyLog` and `PersistentMap`, the containers
`alphalab.common` has had since v2.1 and v2.2. Measured over a 2,500 → 20,000
doubling sweep on the development machine:

| Package | v2.16 at 20,000 | v2.17 at 20,000 | Growth before | Growth after |
| --- | --- | --- | --- | --- |
| `scheduler` | 1.73s | 0.11s | 3.80x | 2.00x |
| `feature_store` | 7.79s | 0.18s | 3.98x | 2.06x |
| `distributed` | 38.38s | 0.18s | 4.14x | 2.05x |
| `plugins` | 12.60s | 0.26s | 3.91x | 2.05x |
| `reporting` | 2.17s | 0.14s | 3.92x | 2.08x |
| `optimizer` | 3.34s | 0.71s | 3.66x | 3.09x |
| `portfolio_optimizer` | 1.96s | 0.12s | 3.89x | 2.03x |
| `data` | 2.91s | 0.26s | 3.69x | 2.07x |

**The containers were not the whole story in three of them, and profiling before
changing anything is what found that.** ADR-0032 named the defect class
correctly and, for these three, incompletely:

| Package | The term that actually dominated | Share |
| --- | --- | --- |
| `distributed` | `JobQueue.submit` re-sorting the whole queue per submission | ~65% |
| `distributed` | `validate_job_submission` building a union of four containers per call | ~26% |
| `plugins` | `validate_registration` calling `metadata()` on every registered plugin | ~54% |
| `feature_store` | `check_dependencies_registered` scanning the whole registry per call | dominant |

Converting the containers *alone* would have left ~90% of `distributed`'s cost
in place, and in `feature_store` it made registration **four times slower** —
because a `PersistentMap` iterates more slowly than a `dict`, so converting the
container while leaving the full scan turned a term the conversion was never
going to fix into a worse one. Each is now answered by a **derived index**
carried on the state — `queued_ids`, `registered_feature_ids`, `registered_names`
— exactly as the OMS order book has carried its asset and strategy indexes since
v2.2. `tests/regression/test_standalone_state_scaling.py` asserts each index
agrees with what it indexes after every operation that can move it.

**Batch operations keep their local copies**, and that is a decision rather than
an omission. `JobScheduler.assign_jobs`, the two `cluster_scheduler` assigners
and `SchedulerEngine.advance_clock` each traverse a whole collection in **one
call**: one copy in and one immutable value out is O(collection) *per call*
rather than per element, and writing each element through `PersistentMap.set`
instead measured ~30% of the 100,000-timer scheduler benchmark. The four are
listed in that test with the reason.

**`OptimizerState.pending_trials` stays a tuple, and this is the one term v2.17
leaves super-linear.** It is not an accumulation path — it is the fixed work
list the caller supplied, consumed one trial per step with `pending[1:]`, which
copies. Python has no O(1) immutable sequence tail: the only structure that
answers "take from the front" with value semantics is a view with a start
offset, which is `AppendOnlyLog` plus one field. **That was implemented and
benchmarked, and it cost +3.9% on `benchmarks/benchmark_execution_pipeline.py`
across four interleaved runs.** Every log on the execution path would pay that,
on every event, so a standalone optimizer's work list could drain in linear
time. It is the same trade ADR-0028 decision 7 refused at +1.78%, and it is
refused here for the same reason. The cost that remains is bounded by the trial
count the caller chose, paid once per optimization rather than per event, and
dominated in every real use by the evaluator it gates.

**`AppendOnlyLog.__iter__` became `itertools.islice`**, which is the one change
this decision made to a shared container. Iterating a 10,000-element log cost
0.068s as a Python-level generator and 0.008s through `islice`, against 0.005s
for the `tuple` it replaced — an 8x constant every engine in the repository was
paying and which v2.17 was about to spread across nine more packages. It is
bounded by index rather than by `iter(self._buffer)`, so an append that grows
the shared buffer mid-iteration is still never yielded. Measured on the
execution pipeline the change is within noise; measured on
`cluster_scheduler.queue_position` it is the difference between 259,000 and
477,000 operations per second, which restores that read to its v2.16 figure.

## 2. `ResearchPayload.parameters` is immutable, and the annotation is not the fix

C2. The field was `dict[str, float]` on a `frozen=True` dataclass — the only
mutable field *type* on any frozen dataclass in the package. `frozen=True`
refuses `payload.parameters = {...}` and says nothing about
`payload.parameters["ma"] = 50`, and the caller's own dictionary stayed aliased
into it besides.

A `Mapping` annotation states the intent and is erased at runtime, so it closes
nothing. `__post_init__` copies what it is given into a `MappingProxyType`: the
copy severs the caller's reference and the proxy refuses every mutation. Both
routes, because the defect had both.

**Hashing is deliberately unchanged**, which is a decision and not an
oversight. A `MappingProxyType` is unhashable exactly as the `dict` was, so
`hash(payload)` raises today and raised before. Every frozen dataclass in the
package that holds a `Mapping` is unhashable — `PortfolioState`, `CashLedger`,
`StrategyDefinition` — and making this one hashable would make it the odd one
out rather than fix anything.

It also found a real defect in the codec: `MappingProxyType` is **not** a `dict`
subclass, so `dataclass_to_dict` did not recurse into it and the encoder
correctly refused the payload. `alphalab.common.serialization` now converts a
proxy as the mapping it stands for, which is the same shape as its existing
`AppendOnlyLog` rule.

## 3. One `BrokerProtocol`, and the connector gets the word its family already uses

C3. `alphalab.brokers.protocol.BrokerProtocol` is now
`BrokerConnectorProtocol`. The contracts were and remain genuinely different —
one takes a `BrokerState`, which is one broker; the other a
`BrokerConnectorState`, which is many brokers and many accounts, which is why
its queries take an `account_id` the boundary has no need of. What changed is
the *name*.

ADR-0032 classified this as "a breaking change to a public symbol for an
ergonomic gain" and deferred it. That trade inverts in the release before a
freeze: a frozen public API with two meanings for one name is a defect that
cannot be fixed afterwards without breaking someone.

The new name was not invented for the occasion. This package already spells the
concept out in `BrokerConnectorState`, `BrokerConnectorEngine` and
`BrokerConnectorError`; the protocol was the one member of that family not using
the family's word. **There is no alias** — an alias would leave one name meaning
two things at an import site, which is the whole defect.

## 4. `Dispatcher.dispatch_event` names the supertype, not the union

C4. ADR-0032 stated the problem and the two options it could see: narrowing to
the union requires the market types, which ADR-0016 decision 3 forbids
`alphalab.strategy` from importing, so "doing it properly means moving hook
selection up to `alphalab.runtime`, which changes a canonical public API".

**A third option exists and is taken: name the supertype.** Both families that
reach a hook — the three events in `alphalab.strategy.events` and the canonical
market vocabulary — derive from `alphalab.common.events.BaseEvent`, and
`alphalab.common` is below both. `StrategyInboundEvent` is that type.

What it buys, stated without overclaiming: a bare `object()`, a dict, a string
or an `Intent` at a call site is now a **static error**, which is the whole of
what the finding asked for. What it does not buy:
`alphalab.live.events.TickReceived` still type-checks, because what makes it the
wrong class is the module it is defined in and no static type can say that. The
exact check stays where it can be exact — `market_hook_for`, on `(module, name)`,
at runtime — and hook selection does **not** move, so no second dispatch
authority appears beside `MARKET_EVENT_HOOKS`.

The return type was `tuple[..., tuple[Any, ...]]` and is now typed as the
`LifecycleTransitioned` events it actually returns.

## 5. Seven deprecated surfaces are removed, not carried into v3.0

ADR-0015, ADR-0029 and ADR-0030 scheduled these for v3.0. Removing them there
would make the stable release a breaking one.

| Surface | Deprecated | Production importers when removed |
| --- | --- | --- |
| `alphalab.kernel` | v2.6 | zero |
| `alphalab.integrations` | v2.6 | zero |
| `alphalab.common.CommonEvent` | v2.6 | zero |
| `alphalab.core.events` | v2.6 | zero |
| `alphalab.persistence` store (9 modules) | v2.13 | zero |
| `alphalab.runtime` orphan lifecycle (10 modules) | v2.14 | zero |
| `alphalab.production` | v2.14 | zero |

**The codec spine is not the store, and survives.** `serialize`, `decode` and
the typed errors are imported by every snapshot module in the repository and are
untouched; that distinction is why the store's notice was PEP 562 rather than an
import-time warning, and it is the line the removal had to find precisely.
`RunStateStore` is likewise untouched.

**No compatibility aliases.** No removed name is re-exported, redirected or
served by a `__getattr__`. `tests/regression/test_removed_surfaces_stay_removed.py`
asserts each name is absent from its package *and* from `__all__`, that no
surviving module defines a module-level `__getattr__`, and — the check that
matters most — that no module re-exports a removed identity under a *different*
spelling, which would preserve the ambiguous architecture while passing every
other assertion.

Four tests used the removed persistence store as a *vehicle* for testing
something else. They were migrated to `RunStateStore`, which is an improvement:
they now assert that the bytes a run actually persists decode to the state it
captured, digest-verified, rather than that a dead adapter round-tripped.

## 6. Both skips are gone, and they were a gap rather than a duplicate

`tests/regression/test_strategy_context_contracts.py` carried `None` for the
`history` and `universe` views in its `SURFACES` table, which made one
parametrized case skip with "history and universe are pinned by
test_strategy_context_history_and_universe".

That file pins their *behaviour* — the look-ahead bound, what an unconfigured
universe means — and never asserted the structural property this table exists
for. Two of the six context surfaces therefore had **no structural check at
all**, and the suite reported two skips rather than a gap. `HistoryView` and
`UniverseView` satisfy their protocols exactly; the table now names them, and a
new test reads `_populate_context`'s own source to assert the table names the
classes the pipeline actually overlays, so the two cannot drift.

## 7. Zero warnings, proven by absence rather than by configuration

v2.16 reported `3767 passed, 2 skipped, 94 warnings`. All 94 were
`DeprecationWarning`s from six test files exercising the surfaces decision 5
removes. No `filterwarnings` was added, no pytest configuration changed, and
nothing is suppressed: the warnings are gone because the code that emitted them
is gone and the tests that called it were migrated or removed.

The gate is `pytest -q -W error::DeprecationWarning`, plus a test that imports
every module in the package tree in a fresh interpreter with the warning fatal —
which proves no *module* warns, something a suite-level run cannot distinguish
from no *test* touching one.

---

# Findings not fixed, and why

ADR-0032's category B is unchanged and its test still pins it, with two entries
retired because the thing they described is gone:

* **B6 (`kernel` has zero consumers)** and **B7 (`production` and `integrations`
  have zero consumers)** are removed with their packages. The surviving test
  asserts the stronger property: every package with no importer is now a
  standalone engine by design, and the removed three stay removed.
* **B4 (`broker` / `brokers` overlap)** stands. The overlap was never the
  defect; the shared *name* was, and decision 3 closes that.

One new finding is recorded rather than fixed:

**`PersistentMap` lookup is ~1.7x a raw `dict` lookup.** Measured on
`benchmarks/benchmark_plugins.py`: 7.4M operations per second before the
conversion, 4.2M after. This is the documented cost of the container and the
same cost the OMS order book has paid since v2.2, against a 28x improvement in
registration throughput and linear scaling. `_lookup` already carries the fast
path for the common case. **Category B — valid current design.**

---

# Ownership

| Concept | Owner |
| --- | --- |
| Append-only accumulation | `alphalab.common.append_log.AppendOnlyLog` — **unchanged** |
| Keyed accumulation | `alphalab.common.persistent_map.PersistentMap` — **unchanged** |
| Market event → strategy hook routing | `alphalab.strategy.dispatcher.MARKET_EVENT_HOOKS` — **unchanged** |
| What the dispatcher accepts | `alphalab.strategy.events.StrategyInboundEvent` |
| The canonical venue boundary | `alphalab.broker.protocol.BrokerProtocol` — **unchanged** |
| The multi-broker routing contract | `alphalab.brokers.protocol.BrokerConnectorProtocol` — renamed |
| Deterministic JSON | `alphalab.persistence.serializer` — **unchanged** |
| Durable run state | `alphalab.persistence.run_store.RunStateStore` — **unchanged** |

**No owner was created and no boundary moved.** Three derived indexes were added
to three standalone states, which is the v2.2 OMS pattern rather than a new one.

---

# Testing invariants

* `tests/regression/test_standalone_state_scaling.py` — the canonical container
  is declared *and* defaulted on every converted field; no converted package
  still splats a state tuple or copies a state index per transition (read from
  the AST, so a docstring describing the removed pattern is not an offence);
  each derived index agrees with what it indexes after every operation; priority
  ordering, duplicate refusal, history retention, value equality and
  serialization shape are unchanged; a doubling sweep costs ≤ 3.0x per doubling
  for every converted package; and the optimizer's remaining term is recorded
  rather than hidden.
* `tests/regression/test_research_payload_is_immutable.py` — neither route can
  change `parameters`; it is enforced rather than annotated; **no frozen
  dataclass anywhere in the package declares a mutable container type**; reading,
  equality, serialization and both construction sites are unaffected.
* `tests/regression/test_shared_names_stay_distinct.py` — exactly one module
  exports `BrokerProtocol`; the rename is not aliased back; the two contracts are
  still different shapes.
* `tests/regression/test_dispatcher_event_typing.py` — the parameter is not
  `Any`; `BaseEvent` is the tightest shared supertype, asserted as a property
  rather than by hand; hook selection did not move and exactly one module holds a
  hook table; `alphalab.strategy` imports no market type; routing is unchanged
  class by class; a same-named impostor still reaches no hook.
* `tests/regression/test_removed_surfaces_stay_removed.py` — every removed
  package, module and name; no `__getattr__` hook; no re-export under a new
  spelling; every canonical neighbour survived; nothing in the tree warns.
* `tests/regression/test_strategy_context_contracts.py` — zero skips, and the
  table is checked against `_populate_context`'s own source.

---

# Explicit non-goals

* **Starting v3.0's identity or documentation freeze.** That is v3.0's work.
* **Optimizing `PersistentMap` lookup.** Recorded above as category B.
* **Reopening ADR-0032's category B.** Those classifications stand.
* **Adding `filterwarnings` anywhere.** The warnings are gone, not silenced.
* **Keeping a deprecated module alive because its own test still used it.**

---

# Consequences

**What is now true.** Every finding ADR-0032 classified is either fixed or
recorded as intentional with its measurement. No surface in the public API
carries a deprecation notice. The suite reports zero skips and zero warnings,
and the warning gate is enforced at the module level rather than the test level.

**Costs.** Seven public surfaces are removed and one public name is renamed;
both are breaking, and both are stated in the release notes. Three state
dataclasses gained a derived index field. `PersistentMap` lookup is slower than
the `dict` it replaced in eight standalone packages, which is the price of their
scaling.

**What v3.0 no longer has to do.** Remove anything, rename anything, or fix a
known structural defect.

---

# Release impact

Breaking:

| Change | Who sees it |
| --- | --- |
| `alphalab.kernel`, `alphalab.integrations`, `alphalab.production`, `alphalab.core.events` removed | anyone importing them; all had zero production importers |
| `alphalab.common.CommonEvent` removed | use `alphalab.common.events.BaseEvent` |
| The nine-module `alphalab.persistence` store removed | use `alphalab.persistence.run_store.RunStateStore` |
| The ten-module orphan `alphalab.runtime` lifecycle removed | use `RunEngine` / `ExecutionPipeline` / `alphalab.strategy` |
| `alphalab.brokers.BrokerProtocol` → `BrokerConnectorProtocol` | anyone implementing the multi-broker routing contract |
| `ResearchPayload.parameters` is a read-only `Mapping` | anyone mutating it after construction |
| `Dispatcher.dispatch_event` / `StrategyEngine.process_event` take `BaseEvent` | anyone passing a non-event; it was accepted and routed nowhere |
| Eight standalone states hold `AppendOnlyLog` / `PersistentMap` | anyone *constructing* one with a `dict` or `tuple` literal for a converted field. Reading is unchanged — both are `Mapping` and `Sequence` |

Additive: `DistributedState.queued_ids`, `FeatureStoreState.registered_feature_ids`,
`PluginState.registered_names`; `CapitalBudget.currency` and `in_currency`;
`StrategyProtocol` is `runtime_checkable`; `alphalab.strategy.events.StrategyInboundEvent`.
