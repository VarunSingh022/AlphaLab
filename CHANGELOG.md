# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

---

# [2.5.0] - 2026-09-05

## Overview

AlphaLab 2.5.0 is "State Round-Trip and the Live Data Path". It takes three
capabilities that already existed, were already tested, and were unreachable,
and makes them reachable — plus it decides two behaviours that had never been
decided.

Every AlphaLab state has serialized deterministically since v2.1. Exactly one
could be read back. v2.3 built a market-data normalization boundary and an
adapter protocol, and nothing in the repository joined them. The replay cursor
carried an O(N²) on a path v2.2 had wired into execution, and the benchmark
that nominally covered replay was written to avoid measuring it.

No new engine packages. Two new modules (`alphalab.market.provider`,
`alphalab.persistence.decode`), two new snapshot modules
(`alphalab.portfolio.snapshot`, `alphalab.lifecycle.snapshot`).

See `docs/ADR/0014-state-round-trip-and-the-live-data-path.md`.

---

## Added

### Typed state round-trip — `capture` / `restore`

- `alphalab.portfolio.snapshot` and `alphalab.lifecycle.snapshot` join
  `alphalab.oms.snapshot`: `capture(state)` → serializable projection,
  `from_primitives(payload)` → typed snapshot, `restore(snapshot)` → typed state.
- The contract, applied identically to every state: `restore(capture(s)) == s`.
  The restored value **compares equal**; it does not reproduce internal container
  lineage, and nothing can observe the difference. That is the position the
  repository already held — `oms.snapshot.restore` rebuilds the order book's
  indices by replaying `add`, and its test asserts equality against a state whose
  lineage is entirely different.
- Every snapshot carries `schema_version` and refuses one it does not read.
  There is no migration path in v2.5 because there is nothing to migrate from;
  the field exists so the first schema change is a decision rather than a silent
  misread.

### Typed decoding — `alphalab.persistence.decode`

- `require`, `as_decimal`, `as_optional_decimal`, `as_float`, `as_int`,
  `as_bool`, `as_str`, `as_optional_str`, `as_mapping`, `as_sequence`,
  `as_str_mapping`, `as_decimal_mapping`, `as_named_enum`, `as_value_enum`,
  `require_schema_version`. Each raises the new `StateDecodeError` **naming the
  field**.
- Deliberately *not* a reflective object mapper. A domain package states field by
  field what it expects, because that is where a wrong type or a missing key is
  caught. The failure this repository already had once — v2.1's append-only logs
  persisted as `"AppendOnlyLog([...])"` — came from a layer that accepted
  anything.
- Two enum encodings, two decoders, no guessing: a `StrEnum` is written as its
  value, a plain `Enum` through `str()` as `"Cls.NAME"`. A bare `"STAGING"` is
  refused, because accepting a form the encoder never writes is how a format
  acquires two dialects.
- `Decimal(str(value))`, the same conversion `market.normalization` uses, so a
  price between cents comes back as the number that was written.

### `PersistenceAdapter.snapshot_payload`

- The read direction, so a stored `Snapshot` can reach a domain decoder. It stops
  at primitives: the dependency runs domain → persistence and not back, or the
  package would have to import half of AlphaLab to read anything.
- `alphalab.persistence` now has production consumers for the first time. At
  v2.4 nothing in `alphalab/` imported it.

### The live data path — `alphalab.market.provider`

- `ProviderHistorySource` turns a provider adapter's historical bars into
  canonical `MarketRecord`s through `market.normalization`, and satisfies
  `MarketDataSource`. This is the link v2.3 was missing: before it,
  `normalize_wire_*` had no production caller and `SequenceSource` was the only
  source in the repository.
- `BarHistoryProvider` is deliberately narrower than any provider adapter's
  surface — `request_history` is the whole contract — so a test double can be a
  source without implementing connect, subscribe, quotes and books too.
- `normalize_wire_bars` maps a sequence; every normalization rule stays where
  v2.3 put it.
- A **history** source: finite, closed range, re-iterable, deterministic record
  ids on the `"<source_id>-<index>"` scheme `SequenceSource` and `MarketDataset`
  already use. No polling, no subscription, no reconnect, no streaming.
- Several symbols are interleaved by timestamp with an `asset_id` tie-break — a
  merge of already-sorted inputs, not a reordering. `validate_ordering` runs over
  the result either way.

### Ordering semantics — `SessionConfig.ordering`

- `CHRONOLOGICAL` (default): a record whose timestamp regresses **raises**. The
  source broke the guarantee it declared.
- `UNORDERED`: such a record is **skipped and recorded** on
  `SessionState.skipped`, the machinery the staleness gate already established,
  so an `UNORDERED` source cannot silently produce a run that merely looks
  chronological.
- A source declaring `UNORDERED` handed to a `CHRONOLOGICAL` session is refused
  by `TradingSession.run` before any record is processed — a session that would
  abort partway should not start.
- Nothing is buffered, reordered or held back. `MarketEngine.publish_quote`
  writes `latest_quotes` unconditionally and the pipeline marks the portfolio to
  whatever it finds, so a late record rewrites valuation backwards; AlphaLab says
  so rather than guessing.

### Benchmarks

- `benchmarks/benchmark_replay_engine.py` now measures `step_one_event` — the API
  the integrated replay path uses — across three sizes, and keeps the batch drain
  as a second figure rather than the only one.

---

## Changed

### A partially filled simulated order withdraws its remainder

The pipeline has always stated its rule in `_close_unfilled_order`: it mints a
fresh order per market event and never re-works an existing one, so an unfilled
order is withdrawn rather than left open. Every non-trading outcome was withdrawn
under that rule. A partial fill was the one branch that skipped it, so the order
stayed `PARTIALLY_FILLED` forever, holding the reservation for a quantity nothing
would execute. `LiquidityCappedFill` (v2.2) makes partial fills routine.

The remainder is now cancelled and its residual reservation released.
`Order.cancel` is legal from `PARTIALLY_FILLED` and preserves `filled_quantity`
and `average_fill_price`.

**This changes bookkeeping, not economics.** No fill is created or destroyed, so
cash, positions, realized and unrealized P&L, the equity curve, the accounting
identity and every analytics figure are exactly what they were. See
**Breaking changes**.

---

## Fixed

### The replay cursor was the last quadratic on a wired path

`ReplayState.system_events` was a tuple, and `step_one_event` appended one
`ReplayAdvanced` per record by rebuilding it. `ReplayBacktest` drives that method
once per record, so a replay of N records copied O(N²) elements — the same defect
v2.1 removed from the risk engine, v2.2 from the OMS and v2.3 from the market and
broker layers, left behind on the one path v2.2 had just wired into execution.

It survived three releases because the benchmark measured the *other* API. Its
own comment said it used the batch drain "to avoid astronomical tuple copying on
O(1M) elements" — steering around the defect rather than measuring it.

Measured, at N=2000/4000/8000 (linear is ~×2.00 per doubling):

| | N=2,000 | N=4,000 | N=8,000 | Growth |
| --- | --- | --- | --- | --- |
| v2.4.0 | 0.0165s | 0.0513s | 0.1740s | **×3.11, ×3.39** |
| v2.5.0 | 0.0089s | 0.0178s | 0.0355s | ×2.01, ×2.00 |

4.9× faster at N=8,000 and no longer growing; throughput flat at ~225,000
events/sec across all three sizes. Replay semantics are untouched: ordering,
contents, cursor behaviour and backtest/replay parity are unchanged.

### ADR-0012 said every vendor client was a stub

It was wrong about Binance, from v2.3 until now. A real HTTP transport
(`marketdata.transport.HttpTransport`, a genuine `urlopen`) and a real Binance
market-data client parsing `/api/v3/klines`, `/bookTicker`, `/trades` and
`/depth` have existed since **v1.39.0** (`5bfb6fa`), with twelve tests over
realistic payloads. It has never been run against a live endpoint from this
environment, so it is unverified — but it is not a stub.

It remains true that **no broker adapter reaches any venue**. The `integrations`
clients (Alpaca, IB, Zerodha) are canned-response stubs, and that is what
"AlphaLab does not support live trading" means.

### Documentation that disagreed with the code

- `docs/README.md` declared "Version v2.1.0" and "`ExecutionPipeline` is the only
  wired-together path" — three releases stale, and it did not mention
  `alphalab.lifecycle` at all.
- `docs/SYSTEM_DESIGN.md` listed `replay` as standalone and not invoked by
  `ExecutionPipeline`, which has been false since v2.2 (ADR-0010).

---

## Breaking changes

Confined to the simulated execution path's *bookkeeping*. No public name was
removed and no module disappeared.

### A partially filled order ends `CANCELLED`, not `PARTIALLY_FILLED`

| | v2.4.0 | v2.5.0 |
| --- | --- | --- |
| Final order status | `PARTIALLY_FILLED` | `CANCELLED` |
| `filled_quantity` / `average_fill_price` | preserved | preserved |
| `remaining_quantity` | positive, never worked | positive, recorded on a closed order |
| Membership | `active_orders` | `completed_orders` |
| Residual reservation | held indefinitely | released |
| Cash, positions, P&L, equity curve | — | **unchanged** |

Code asserting that such an order stays `PARTIALLY_FILLED` after the event that
filled it needs updating; five tests in this repository did.

A seeded run now mints one more identifier than before, because withdrawing
appends an `OrderCancelled` event. Quantities and money are unchanged, and
comparisons *between* two runs — backtest/replay parity, the deterministic
backtest regression — are unaffected.

### `SessionConfig` and `SessionState` gained fields

`SessionConfig.ordering` (defaults to `CHRONOLOGICAL`) and
`SessionState.last_record_timestamp` (defaults to `None`). Both are appended with
defaults, so positional construction is unaffected. A session fed records whose
timestamps go backwards now raises where it previously processed them and marked
the portfolio at a stale price.

### `ReplayState.system_events` is an `AppendOnlyLog`

It compares equal to the tuple it replaced, so `state.system_events == ()` still
holds and iteration is unchanged. A caller annotating the field type sees a
different one.

---

## Documentation

- `docs/ADR/0014-state-round-trip-and-the-live-data-path.md` — new; records the
  three design decisions and what was rejected.
- `docs/ADR/0012` — vendor-adapter table corrected, with the correction marked as
  such rather than quietly rewritten.
- `docs/ARCHITECTURE.md` — Implementation Status to v2.5; new sections on state
  round-trip, the live data path and partial-fill termination.
- `docs/README.md`, `docs/SYSTEM_DESIGN.md` — stale claims corrected.
- `README.md`, `ROADMAP.md` — v2.5 status and the remaining gaps.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **2008 passed** (1897 at v2.4.0) — 1698 unit, 109 integration, 201 regression |
| `mypy .` (strict) | clean, 915 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 959 files |
| `python -m build` + `twine check` | passing |

---

# [2.4.0] - 2026-09-05

## Overview

AlphaLab 2.4.0 is "Model + Strategy Lifecycle". Four packages each implemented
one stage of a lifecycle — `experiment_tracking` (v1.46.0), `model_registry`,
`research_assistant` and `deployment_manager` (all v2.0.0) — and none of them
met. Between them there was one link, `ModelVersion.run_id`, an unvalidated
string, and one convention: that a release component *might* say `"momentum@3"`,
which nothing produced and nothing parsed.

This release connects them, adds the three things that were missing at the
seams, and removes the quadratic behaviour all three stateful packages carried.

One new package, `alphalab.lifecycle`. It is an integration package, not a
fifth engine — the `alphalab.backtesting` pattern from v2.2. Each of the four
packages still works on its own and none of them changed shape to be composed.

**A deployment here is a lifecycle fact, not an operation on a machine.** It
records that an environment should be running a strategy version. It starts no
process, opens no connection and reaches no venue; AlphaLab still has no
transport to any real venue (ADR-0012 is unchanged). The registry references
artifacts and stores no bytes.

See `docs/ADR/0013-model-and-strategy-lifecycle.md`.

---

## Added

### The lifecycle package — `alphalab.lifecycle`

```text
research candidate → experiment run → validation evidence → model version
    → strategy version → promotion → deployment → rollback
```

- `LifecycleState` holds the four registries plus the evidence store, as one
  immutable, serializable value — the role `ExecutionPipelineState` plays for
  the execution path. Nothing is copied into it.
- Not to be confused with `alphalab.strategy.LifecycleState`, an enum naming
  the stages of a strategy *instance running inside a session*. A deployed
  strategy version is started and stopped many times without its stage
  changing.

### Strategy versions — `alphalab.lifecycle.strategy_version`

- `StrategyVersion` is the immutable, numbered record that did not exist.
  `studio.StrategyDefinition.version` was a free-form string, so nothing could
  be pointed at by a deployment or compared with the one before it.
- It carries the canonical `StrategyDefinition` rather than a second parameter
  format, so a candidate from `research_assistant.to_strategy_definition`
  reaches a strategy version with no shape in between.
- Four identities stay apart: the strategy line (`name`), the version, the
  `ModelRef` it runs, and the deployments it appears in. One version deployed
  to two environments is two deployments, which no field on the version could
  have said.
- `StrategyVersionRegistry` deliberately has **no** production index — see
  "one source of truth" below.

### Typed references — `alphalab.lifecycle.identity`

- `ModelRef`, `StrategyVersionRef`, `DeploymentRef`. A model version and a
  strategy version are both a name and a number, which is why they were passed
  as indistinguishable strings before; they are now different types that render
  the same way and do not compare equal.
- Rendering is `"name@version"` — the form `deployment_manager` already
  documented for release components. `parse_ref` reads it back, and a name
  containing `"@"` is refused at construction rather than producing a reference
  that parses to something else.

### Validation evidence — `alphalab.lifecycle.evidence`

- `ValidationEvidence` records what was measured, over what data, with what
  seed, and where the full report is. It computes nothing:
  `evidence_from_backtest` reads a run's `PerformanceReport` and
  `evidence_from_research` reads a `ResearchScore`. Both already existed and
  are referenced, not reimplemented.
- `evidence_id` is a SHA-256 digest of the evidence's own content — the
  construction `compute_checksum` already used for a release manifest. The same
  measurement identifies itself the same way without coordination, and
  `verify_evidence_id` detects numbers edited after the fact.
- `ValidationPolicy` states thresholds in advance. `evaluate_policy` names
  **every** failed check, not the first; a metric the policy asks for that the
  evidence does not carry is a failure, because an absent number is not a
  passing one; and evidence that no longer matches its own id fails before a
  single threshold is read.
- `required_method` lets a policy insist on `BACKTEST` evidence rather than
  numbers typed in by hand.
- **What a pass claims** is that the stated thresholds were met by the recorded
  numbers. Not statistical significance, not out-of-sample validity, and not a
  correction for how many candidates were searched first.

### The promotion gate — `alphalab.lifecycle.promotion`

- `promote_strategy_version` requires passing evidence, and requires the model
  version the strategy runs to be staged itself: a strategy cannot be more
  validated than the model inside it.
- It refuses to reach `PRODUCTION` at all. A strategy version goes live by
  being deployed, so the ledger is the only thing that ever puts one there.
- `retire_strategy_version` refuses to archive a version that is still active
  somewhere — taking down what is live is a rollback or a replacement, not a
  stage edit.
- Every accepted move appends a `StrategyPromotionRecord` carrying *why*, so a
  promoted version records what it passed rather than only that it passed.

### Checked references — `alphalab.lifecycle.registration`

- `register_model_version` and `register_strategy` verify that a cited
  experiment run exists and has **completed**, and that a cited model version is
  real. `ModelVersion.run_id` was an unvalidated `str | None`; neither package
  could check it alone without depending on the other.

### Deployment and rollback — `alphalab.lifecycle.deployment`

- `deploy_strategy_version` builds the release manifest from the version's typed
  references, makes it active in an environment, moves the version to
  `PRODUCTION`, and archives whichever version it displaced — unless that
  version is still active in another environment.
- One release package stands for one strategy version however many environments
  it reaches. A strategy version is immutable, so its manifest is fixed.
- `rollback_environment` restores the version the append-only ledger names as
  previously active, archives the one being taken down, and re-derives the
  model's deployment note. Deterministic: the same state and environment always
  roll back to the same place.
- Redeploying the version already running in an environment is refused, and
  nothing is registered before the refusal.

### Artifact references — `alphalab.model_registry.ArtifactRef`

- Where a version's trained bytes live, what they should hash to, and how big
  they are. AlphaLab never reads, writes or hashes those bytes: there is no
  object store here and this release does not pretend there is.
- `ModelVersion.__serializable__` projects a version to its metadata plus that
  reference, dropping the in-memory `model` object. An arbitrary object has no
  deterministic JSON form, and stringifying it would produce a payload that
  reads back as prose — the failure v2.1 removed from the append-only logs.

### Single-pass mapping views — `PersistentMap.items()` / `.values()`

`Mapping`'s default views iterate the keys and then index the map, resolving
every key twice. On a persistent map a resolution is a chain probe, not a hash
lookup, so the second one is real work. Iterating a 500-entry map's values is
1.7× faster, for every `PersistentMap` in the repository.

### The declared stage transitions — `alphalab.model_registry.stages`

- `LEGAL_TRANSITIONS` states every legal move once. `illegal_stage_move` is the
  pure table check, shared with `alphalab.lifecycle` so the table is not written
  twice.

### Benchmarks

- `benchmarks/benchmark_lifecycle.py` — three growth shapes (many versions of
  one line, many lines, a deep environment ledger) plus a full
  promote → deploy → rollback sweep.

---

## Changed

### One source of truth for what is live

`model_registry.DeploymentMetadata` was a hand-set blob claiming a version was
deployed somewhere; `deployment_manager`'s ledger recorded what actually was.
Both remain, but the integrated path now **derives** the note from the
deployment that happened rather than letting a caller assert one, and updates it
on rollback. `alphalab.lifecycle.views` answers "what is running here?" by
reading the ledger.

### `ParamValue` has one definition

`experiment_tracking.tracker.ParamValue` and `model_registry.registry.ParamValue`
were identical, and the registry's own docstring said consolidating them was
owed. Both now re-export `alphalab.common.types.ParamValue`. `MetadataValue` is
deliberately *not* the same alias: metadata admits `None`, a parameter does not.

---

## Fixed

### The lifecycle registries were quadratic

Every write copied the whole mapping or rebuilt the whole tuple, and three write
paths scanned the data they were writing to. Measured on one machine, per
doubling of the workload — linear is ~2x:

| Operation | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `log_metric` (2k → 4k → 8k) | 0.0091 → 0.0313 → 0.1226s (**3.4x, 3.9x**) | 0.0094 → 0.0179 → 0.0357s (1.9x, 2.0x) |
| `start_run` (2k → 4k → 8k) | 0.0165 → 0.0508 → 0.1738s (**3.1x, 3.4x**) | 0.0114 → 0.0196 → 0.0428s (1.7x, 2.2x) |
| `register_model`, one name (2k → 4k → 8k) | 0.0120 → 0.0442 → 0.1592s (**3.7x, 3.6x**) | 0.0073 → 0.0145 → 0.0310s (2.0x, 2.2x) |
| `register_model`, many names (2k → 4k → 8k) | 0.0113 → 0.0371 → 0.1341s (**3.3x, 3.6x**) | 0.0079 → 0.0190 → 0.0374s (2.4x, 2.0x) |
| `promote` (1k → 2k → 4k) | 0.0525 → 0.2000 → 0.7769s (**3.8x, 3.9x**) | 0.0126 → 0.0251 → 0.0543s (2.0x, 2.2x) |
| `register_release` (1k → 2k → 4k) | 0.0041 → 0.0124 → 0.0423s (**3.0x, 3.4x**) | 0.0044 → 0.0089 → 0.0181s (2.0x, 2.0x) |
| `deploy` (0.5k → 1k → 2k) | 0.0077 → 0.0261 → 0.0975s (**3.4x, 3.7x**) | 0.0056 → 0.0109 → 0.0222s (2.0x, 2.0x) |

The containers are the ones v2.2 introduced: `PersistentMap` where a key is
rewritten, `AppendOnlyLog` where a history only grows.

End to end on the benchmark suites, v2.3.0 → this release:

| Benchmark | v2.3.0 | v2.4.0 | Change |
| --- | --- | --- | --- |
| `benchmark_model_registry` rollback + re-promote (1k) | 3.45s / 290 per sec | 0.03s / 30,301 per sec | **104×** |
| `benchmark_deployment_manager` `active_release` (50k) | 2.99s / 16,727 per sec | 0.03s / 1,523,693 per sec | **91×** |
| `benchmark_model_registry` `production_version` (50k) | 1.78s / 28,038 per sec | 0.03s / 1,923,545 per sec | **69×** |
| `benchmark_model_registry` promote (2k over 20k versions) | 2.68s / 746 per sec | 0.04s / 50,456 per sec | **66×** |
| `benchmark_deployment_manager` rollback + re-deploy (1k) | 1.24s / 1,618 per sec | 0.03s / 73,482 per sec | **45×** |
| `benchmark_deployment_manager` deploy (5k) | 0.52s / 9,549 per sec | 0.03s / 154,437 per sec | **16×** |
| `benchmark_model_registry` `register_model` (20k) | 0.97s / 20,673 per sec | 0.08s / 248,404 per sec | **12×** |
| `benchmark_deployment_manager` `register_release` (10k) | 0.25s / 39,871 per sec | 0.04s / 226,184 per sec | **5.6×** |
| `benchmark_experiment_tracking` `log_metric` (10k) | 0.19s / 52,802 per sec | 0.04s / 262,332 per sec | **5.0×** |
| `benchmark_experiment_tracking` `best_run` (10k × 501 runs) | 0.56s / 17,736 per sec | 2.48s / 4,030 per sec | **0.23× — slower** |

### The one regression: readers that scan a whole map

`best_run` compares every run in a tracker on every call, and resolving a key in
a `PersistentMap` is a chain probe rather than a hash lookup. Scanning 501 runs
ten thousand times therefore costs about 4× what it did against plain dicts.
Both are O(runs) per call; only the constant changed.

That is the trade, and it is the right way round: the writers stopped being
quadratic and the readers stayed linear. It is the same trade v2.3 documented
for market-data ingestion at universe 1, one order of magnitude further along.

`ExperimentRun.metrics` in particular is a persistent map and not a plain dict
because a run has **two** growth axes: the values logged to a metric, and the
number of distinct metric names. A run logging a value per feature, per asset
or per layer has thousands of the latter. A draft of this release made `metrics`
a dict to buy back 25% of the `best_run` constant, and made logging distinct
metric names quadratic (3.8× per doubling) to do it. The regression test now
holds both axes.

`PersistentMap.items()` and `.values()` are new, and recover part of it.
`Mapping`'s default views resolve every key twice — once to decide it is present
and once to read it — which on a persistent map means two chain probes.
Iterating a 500-entry map's values is 1.7× faster as a result, for every reader
in the repository, not only these.

Three scans needed indexes, which no container change would have fixed:

- `promote()` called `production_version()`, which scanned every version of the
  model. `ModelRegistry` now carries `production` (name → current production
  version) and `production_line` (name → the versions that have held it, in
  order). Both are O(1).
- `rollback()` rebuilt the filtered promotion history to find the version to
  return to. It is now `production_line[-2]`.
- `deploy()` called `active_release()`, which scanned the whole ledger
  backwards. `DeploymentManager` now indexes the same records by environment.

`tests/regression/test_lifecycle_registry_complexity.py` guards both growth axes
— versions per name *and* number of names. An early draft of this release fixed
the first and made the second 50× slower and still quadratic, by inspecting
every entry of the container inside `__post_init__`, which runs on every write.

### A promotion could move a version anywhere

`promote()` refused only a move to `NONE` and a move to the stage the version
was already in. See **Breaking changes**.

---

## Breaking changes

Confined to `alphalab.model_registry` and `alphalab.experiment_tracking`. No
public name was removed, no module disappeared, and no enum member was removed.

### Two stage transitions are now refused

| Move | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `PRODUCTION → STAGING` | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version never in production | allowed | **refused** |
| `ARCHIVED → PRODUCTION`, version is the rollback target | allowed | allowed |
| `NONE → PRODUCTION` | allowed | allowed |
| `ARCHIVED → STAGING` | allowed | allowed |

A live version leaves production by being archived or replaced; a quiet
demotion leaves the model with nothing live and no record that anything was
taken down. An archived version that was never live returning to production is a
resurrection, not a restore — and if it were allowed, "roll back" would stop
being a distinguishable operation.

`NONE → PRODUCTION` stays legal at the registry level deliberately: the registry
is mechanism, and requiring evidence is policy, which lives in
`alphalab.lifecycle`.

### Container types on three state classes

Fields that were `dict` / `tuple` are now persistent containers. Both compare
equal to what they replaced — `run.metrics["loss"] == (0.5, 0.3)` and
`registry.promotions == ()` still hold — and `__post_init__` converts a
hand-built plain mapping, so runtime construction keeps working. A caller that
*annotates* one of these field types sees a different one.

| Field | v2.3.0 | v2.4.0 |
| --- | --- | --- |
| `ExperimentTracker.runs` | `Mapping[str, ExperimentRun]` | `PersistentMap[str, ExperimentRun]` |
| `ExperimentRun.metrics` | `Mapping[str, tuple[float, ...]]` | `PersistentMap[str, AppendOnlyLog[float]]` |
| `ModelRegistry.versions` | `Mapping[str, tuple[ModelVersion, ...]]` | `PersistentMap[str, PersistentMap[int, ModelVersion]]` |
| `ModelRegistry.promotions` | `tuple[PromotionRecord, ...]` | `AppendOnlyLog[PromotionRecord]` |
| `DeploymentManager.releases` | `Mapping[str, tuple[ReleasePackage, ...]]` | `PersistentMap[str, AppendOnlyLog[ReleasePackage]]` |
| `DeploymentManager.deployments` | `tuple[DeploymentRecord, ...]` | `AppendOnlyLog[DeploymentRecord]` |

**`ModelRegistry.versions[name]` changed indexing.** It was a positional tuple,
so `registry.versions["alpha"][0]` was version 1; it is now keyed by version
number, so that expression raises `KeyError` and `registry.versions["alpha"][1]`
is version 1. Use `list_versions(registry, name)` — the documented accessor,
whose return type is unchanged — or `get_version(registry, name, version)`,
which is now O(1).

### New fields on two state classes

`ModelRegistry` gained `production` and `production_line`; `DeploymentManager`
gained `environments`. They are indexes, maintained by their packages' own
functions the way `oms.book.OrderBook` maintains its own. Positional
construction of either class is unaffected (the new fields are appended and
default to empty), but a registry built by hand from `versions` alone will
report no production version until one is promoted — build through
`register_model` and `promote`.

---

## Documentation

- `docs/ADR/0013-model-and-strategy-lifecycle.md` — new.
- `docs/ARCHITECTURE.md` — Implementation Status updated to v2.4; the four
  lifecycle packages move out of the standalone list.
- `README.md`, `ROADMAP.md` — v2.4 status, capabilities and remaining gaps.
- `examples/12_model_lifecycle.py` — new: a research candidate through a real
  backtest to a deployment and back, including the refusal of a promotion that
  no evidence supports.

---

## Quality gates

Measured on the release commit, not carried over:

| Gate | Result |
| --- | --- |
| `pytest -q` | **1897 passed** (1756 at v2.3.0) |
| `mypy .` (strict) | clean, 905 source files |
| `ruff check .` | clean |
| `ruff format --check .` | clean, 948 files |

---

# [2.3.0] - 2026-09-05

## Overview

AlphaLab 2.3.0 is "Market Data + Broker/Live Execution". It is a connectivity
and convergence release: it establishes one canonical market-data model with an
explicit normalization boundary, one canonical broker adapter boundary, and
makes historical, replay, paper and live execution take the same canonical step
through the same engines.

It closes both items v2.2 deferred: market-data model convergence
(`data` / `market` / `marketdata` / `feed` / `live`, and the multiple `Bar`
types) and `broker` / `brokers` consolidation.

No new engine packages. Two new modules on the integrated path
(`alphalab.runtime.session`, `alphalab.runtime.broker_routing`) and three in the
market layer (`alphalab.market.record`, `.source`, `.normalization`).

**AlphaLab does not support live trading.** It supports the adapter contract a
live venue would be reached through; there is no connectivity to any real venue
in this repository. See `docs/ADR/0012` for the precise implemented /
adapter-only / absent breakdown.

See `docs/ADR/0011-canonical-market-data-model.md` and
`docs/ADR/0012-broker-boundary-and-environment-parity.md`.

---

## Added

### The market-data normalization boundary — `alphalab.market.normalization`

- `normalize_wire_quote` / `normalize_wire_trade` / `normalize_wire_bar` /
  `normalize_wire_book` lift `alphalab.data.feed` wire records into the
  canonical `alphalab.market` domain records the execution path consumes.
- Every number converts through `Decimal(str(value))`. `Decimal(0.1)` keeps the
  float's binary expansion; going through `str` keeps the number the provider
  wrote. That is what makes normalization deterministic.
- `NormalizationPolicy` supplies what the wire cannot carry (venue, currency,
  timeframe) and names an unattributed venue `"UNKNOWN"` rather than guessing.
  `SymbolMap` rewrites a provider symbol to an `asset_id`.
- Fields a wire record does not report — vwap, trade count, book order counts,
  trade direction — are documented as unreported, not invented.
- `is_stale` / `reject_stale` treat staleness as a caller decision, separate
  from validation: a stale record is well-formed, and how old is too old is a
  property of the strategy.

### The market-data adapter boundary — `alphalab.market.source`

- `MarketDataSource` yields canonical `MarketRecord`s and nothing else, so the
  execution path cannot tell a stored file from a socket.
- `SequenceSource` (finite, re-iterable, deterministic record ids),
  `OrderingGuarantee` (a source declares whether it can promise chronological
  order), `validate_ordering`.
- No provider API is modelled: no HTTP, no websockets, no vendor
  authentication, no reconnect loop.

### The canonical market record — `alphalab.market.record`

- `MarketInput` and `MarketRecord` moved here from
  `alphalab.backtesting.dataset` (re-exported unchanged), so a live feed
  adapter can produce a record without importing the backtesting package.
- `records_from_inputs` assigns deterministic, fixed-width record ids.

### Broker reconciliation — `alphalab.broker.reconciliation`

- `ExternalOrderMap` holds `oms_order_id ↔ broker_order_id` and refuses to
  rebind either direction.
- `classify_execution` is total: `APPLIED`, `DUPLICATE`, `UNKNOWN_ORDER`,
  `TERMINAL_ORDER`, `OVERFILL`, `INVALID`. Nothing is silently dropped.
- `apply_execution` is idempotent in `execution_id`; a refused fill leaves the
  state untouched.
- `ReconciliationLog` keeps every refusal, separating expected redelivery from
  a genuine break.
- `reconcile()` compares local state against a venue snapshot and produces
  `ReconciliationReport` — missing, unknown, divergent orders, divergent
  positions, cash difference. It states differences and does not resolve them.

### Trading sessions — `alphalab.runtime.session`

- `TradingSession` drives any `MarketDataSource` through
  `ExecutionPipeline.process_record`.
- `ExecutionMode` (`BACKTEST` / `REPLAY` / `PAPER` / `LIVE`) declares its own
  routing and whether its clock is moving.
- `max_market_data_age_seconds` gates stale records in a real-time session;
  skipped records are recorded with a reason rather than silently dropped.

### The venue boundary — `alphalab.runtime.broker_routing`

- `route_order` sends an accepted OMS order to a venue, with two pre-trade
  gates: never on a connection that is not `CONNECTED`, and never twice for one
  OMS order. The client order id is derived from the OMS order id, so a retry
  after a lost response addresses the same order.
- `apply_broker_execution` brings a venue fill back through
  `ExecutionPipeline.apply_execution_report` — the same function a simulated
  fill uses, so OMS, portfolio, allocation and analytics are identical either
  way.
- `routable()` projects an OMS order onto `broker.adapter.OMSOrderProtocol`,
  which the real `oms.order.Order` did not actually satisfy.

### The canonical step — `alphalab.runtime.execution_pipeline`

- `ExecutionPipeline.publish_record` and `process_record`. Every environment
  takes this step; `backtesting.engine.advance` delegates to it.
- `ExecutionPipeline.apply_execution_report` applies a report that did not come
  from the simulator.
- `ExecutionRouting` (`SIMULATED` / `EXTERNAL`) on `ExecutionPipelineConfig`.
  `EXTERNAL` leaves an accepted order working, invents no fill, and keeps its
  allocation reservation held.

### Benchmarks

- `benchmarks/benchmark_market_data.py` — normalization cost per record type
  and an ingestion sweep across universe size.

---

## Changed

### Market-data models converged

- `alphalab.marketdata.feed` re-exports `alphalab.data.feed`'s `Quote`, `Trade`,
  `Bar`, `OrderBookLevel` and `OrderBook`. They were field-for-field identical
  copies; they are now the same class objects.
- `alphalab.live.message.OrderBookLevel` is `alphalab.data.feed.OrderBookLevel`.
- `data.Bar` and `market.Bar` both remain, deliberately — different layers, not
  a duplicate. `tests/regression/test_market_model_convergence.py` asserts the
  distinction.

### Broker models converged

- `alphalab.brokers` routes the canonical types from `alphalab.broker`:
  `BrokerOrder`, `BrokerExecution` (`ExecutionReport`), `BrokerAccount`
  (`AccountSnapshot`), `BrokerPosition` (`PositionSnapshot`),
  `BrokerOrderStatus` (`OrderStatus`), and `AssetClass`, which was a fifth copy
  of `core.enums.AssetType`.
- `BrokerProtocol` covers order status, execution reception, account and
  positions. `PaperBroker` implements all of it.
- `ConnectionStatus` gains `RECONNECTING` and `FAILED`, which call for
  different behaviour: hold orders versus refuse them.
- `BrokerOrderStatus` gains `SUBMITTED` from the connector package, so
  broker-local operational states are one shared set.
- The routing events in `alphalab.brokers.events` name their identifier
  `broker_order_id` rather than `order_id`. `OrderManager` always passed the
  venue handle into that field, so only the name changes — but leaving it
  called `order_id` would have preserved, inside the converged package, exactly
  the ambiguity that decided which broker order model was canonical.

### Moved (all re-exported, no import breaks)

- `id_scope` / `id_source` → `alphalab.common.ids`, so a session can mint
  reproducible identifiers without importing the backtesting engine.
- `UnsupportedRecordError` → `alphalab.market.exceptions`. It is no longer a
  subclass of `BacktestError`; four environments publish records now.

---

## Fixed

### The broker layer was quadratic

`BrokerState` and `BrokerConnectorState` rebuilt their order, execution,
position and account indexes with `dict(old)` and grew `events` with
`(*events, e)` on every transition, so a session copied O(N²). v2.3 routes
paper and live execution through those states, which would have made this the
slowest part of a long session.

Measured, v2.2.0 → this release:

| Benchmark | v2.2.0 | v2.3.0 | Change |
| --- | --- | --- | --- |
| `benchmark_broker` (100k orders) | 676.70s / 148 per sec | 4.65s / 21,485 per sec | **145×** |
| `benchmark_live` (100k ticks) | 219.02s / 457 per sec | 1.28s / 78,338 per sec | **172×** |
| `benchmark_feed` (100k events) | 42.95s / 2,328 per sec | 0.69s / 144,489 per sec | **62×** |
| `benchmark_brokers` (10k cycles) | 2.44s / 4,106 per sec | 0.34s / 29,465 per sec | **7.2×** |
| `benchmark_marketdata` (100k trades) | did not run | 0.57s / 174,454 per sec | — |
| `benchmarks_market_engine` (quotes) | 105,670 per sec | 147,727 per sec | 1.4× |

`MarketState`, `MarketDataState`, `LiveState`, `FeedState` and
`MarketDataCache` moved to the same persistent containers.

### Market-data ingestion cost scaled with the universe

`MarketEngine.publish_*` rebuilt the whole `latest_*` index on every publish,
so a publish cost O(universe): 20k quotes into a 20,000-instrument universe ran
at 22,688 per sec against 215,808 per sec into a one-instrument universe, a
9.5× penalty that grew with the universe. Ingestion is now flat — ~195,000 per
sec at every universe size measured, 1 through 20,000.

The one regression is ~8% at universe 1, where a persistent map costs more than
copying a one-key dict. That is the trade, and it is the right way round.

### `benchmark_marketdata.py` could not run

It connected through `YahooAdapter`, whose client raises `NotImplementedError`
because it used to return hardcoded fake data. It fails identically on v2.2.0.
It now uses an explicit in-benchmark test double, which is what a benchmark of
the *engine* should have depended on. No vendor connectivity is faked.

### `oms.order.Order` did not satisfy `broker.adapter.OMSOrderProtocol`

Its `order_id` is an `OrderId`, not a string, and it has no single `price`. The
protocol claimed to decouple the broker layer from the OMS while not actually
matching it. `broker_routing.routable()` does the translation explicitly, at
the adapter boundary where it belongs.

---

## Breaking changes

Confined to `alphalab.brokers`, whose types are now the canonical ones. No
public name was removed from any package, no module disappeared, and no enum
member was removed — the breaks below are all changes of *shape*, not of
availability.

### Dataclass shapes

- `AccountSnapshot` takes `cash` / `equity` / `available_funds` instead of
  `cash_balance`, and requires the fields a venue account actually reports.
  `broker_id` and `metadata` are optional.
- `ExecutionReport` and `BrokerOrder` name their order field
  `broker_order_id`; `ExecutionReport.account_id` moved after `timestamp` and
  now defaults. `BrokerOrder` additionally requires `oms_order_id`, because a
  single `order_id` could not say whether it held AlphaLab's identifier or the
  venue's.
- **`PositionSnapshot` changed shape, not just field membership.** It was nine
  required fields (`position_id`, `account_id`, `symbol`, `asset_class`,
  `quantity`, `average_price`, `market_price`, `unrealized_pnl`,
  `realized_pnl`); it is now six required plus three defaulted:

  | | v2.2.0 | v2.3.0 |
  | --- | --- | --- |
  | `position_id` | required | **removed** — it restated the `"<account_id>:<symbol>"` key the state already stores the position under |
  | `market_value` | absent | **required (new)** |
  | `symbol`, `quantity`, `average_price`, `unrealized_pnl`, `realized_pnl` | required | required |
  | `account_id` | required | optional, defaults to `""` |
  | `asset_class` | required | optional, defaults to `AssetType.EQUITY` |
  | `market_price` | required | optional, defaults to `Decimal("0")` |

  Because the arity and the order both changed, **positional construction
  breaks**: a v2.2 call passing nine positional arguments will raise, and a
  call passing six will silently bind them to different fields. Construct with
  keywords. `market_price` and `market_value` are both kept because a venue
  reports both and they are different numbers — the mark for one unit, and the
  mark for the whole holding.

### Keyword-parameter renames — `order_id` → `broker_order_id`

Positional callers are unaffected; keyword callers break. Every affected public
entry point:

| Callable | v2.2.0 | v2.3.0 |
| --- | --- | --- |
| `BrokerConnectorEngine.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `OrderManager.cancel_order` | `(state, order_id, timestamp)` | `(state, broker_order_id, timestamp)` |
| `brokers.list_executions` | `(state, order_id)` | `(state, broker_order_id)` |
| `brokers.validate_execution` | `(state, execution_id, order_id)` | `(state, execution_id, broker_order_id)` |
| `brokers.validate_order_cancellation` | `(state, order_id)` | `(state, broker_order_id)` |

The routing events in `alphalab.brokers.events` are renamed for the same
reason: `OrderSubmitted`, `OrderCancelled`, `OrderFilled` and
`ExecutionReceived` name their identifier `broker_order_id` instead of
`order_id`. The *value* was always the venue handle — `OrderManager` has always
passed `order.broker_order_id` — so only the name changes, and field order is
unchanged, leaving positional construction working. Nothing in the codebase
read the field by name.

### Enum values

Both enums are now aliases of canonical types, so their `.value` changed.
**`.name` is unchanged in every case**, and nothing in AlphaLab reads `.value`
on either — but external code that persisted or transmitted a `.value` will
read it back differently.

| Member | v2.2.0 `.value` | v2.3.0 `.value` | `.name` |
| --- | --- | --- | --- |
| `AssetClass.EQUITY` | `1` (`Enum`, `auto()`) | `"equity"` (`StrEnum`) | unchanged |
| `AssetClass.FUTURE` / `OPTION` / `FOREX` / `CRYPTO` | `2`–`5` | `"future"` / `"option"` / `"forex"` / `"crypto"` | unchanged |
| `OrderStatus.SUBMITTED` | `1` | `2` | unchanged |

`AssetClass` is now `alphalab.core.enums.AssetType` and therefore also gains a
`CASH` member; `OrderStatus` is now `alphalab.broker.order.BrokerOrderStatus`
and gains `PENDING_SUBMIT` (which takes value `1`, shifting `SUBMITTED` to `2`)
and `PENDING_CANCEL`. Both are widenings: no member was removed.

### Protocols and state containers

- `BrokerProtocol` (both packages) gained methods; a v2.2 adapter will not
  satisfy the v2.3 protocol.
- `MarketState`, `BrokerState`, `BrokerConnectorState`, `MarketDataState`,
  `LiveState` and `FeedState` fields are `PersistentMap` / `AppendOnlyLog`, not
  `dict` / `tuple`. Reads are unchanged (both are `Mapping` / `Sequence`);
  constructing one with a plain `dict` is a type error.

Every assertion in the existing tests is preserved. Only construction sites
moved.

---

## Quality

- 1737 tests pass (1599 at v2.2.0).
- `ruff check`, `ruff format --check`, `mypy --strict` clean.
- `python -m build` and `twine check dist/*` clean.
- All 11 examples run.
- 47 of 48 benchmarks run. `benchmark_workbench.py` fails on an unrelated
  workbench tab-lifecycle assertion and fails identically on v2.2.0; it is not
  in v2.3's scope.

---

# [2.2.0] - 2026-09-05

## Overview

AlphaLab 2.2.0 is "Unified Backtesting + Replay". It turns the existing research,
market-data, execution, portfolio and analytics components into one deterministic
dataset → analytics workflow, and closes the four v2.1 limitations that stood in
its way: the super-linear OMS order book, the leaked allocation reservation on a
risk rejection, the unserializable `OMSState`, and a replay engine that never
reached the execution path.

One new package, `alphalab.backtesting`. It is an *integration* package: it adds
no engine and no domain model, and composes `ExecutionPipeline` instead. There is
no backtest-only order model, no backtest-only fill model and — the point of the
release — no second set of portfolio books.

See `docs/ADR/0010-unified-backtesting-and-replay.md`.

---

## Added

### Unified backtesting — `alphalab.backtesting`

- `MarketDataset` / `MarketRecord` — an ordered, validated sequence of canonical
  market inputs (`Quote` / `Bar` / `Tick`), each carrying the `event_id` and
  `timestamp` `replay.HistoricalEventProtocol` requires. One dataset type feeds
  both drivers.
- `backtesting.engine.advance` — the canonical step: publish one record to the
  market engine, hand the resulting event to
  `ExecutionPipeline.process_market_event`.
- `BacktestEngine.run(config, dataset, strategy_state, context_factory)` — walks
  a dataset through that step and returns a `BacktestResult` with the final
  pipeline state, per-record `BacktestStep`s, the equity curve, the valuation and
  the compiled performance report.
- `BacktestConfig` — the execution-path config, the fill policy, the seed, and
  the analytics parameters, as one value.
- Read-only views: `final_equity`, `final_cash`, `realized_pnl`,
  `unrealized_pnl`, `commission_paid`, `equity_values`, `submitted_orders`,
  `executed_fills`, `steps_with_fills`, `performance_report`.

### Replay on the execution path — `alphalab.backtesting.replay`

- `ReplayBacktest.run(...)` drives `ReplayEngine`'s cursor and calls the *same*
  `advance` for every event it yields, so backtest/replay parity is structural
  rather than a coincidence the tests happen to observe.
- `alphalab.replay` itself is unchanged in responsibility: it still owns the
  cursor, the replay clock, the session lifecycle and chronological validation.
- The replay clock is the record index, not wall time, so a replay's own state is
  a pure function of its dataset.

### Execution semantics — `alphalab.execution.policy`

- `FillPolicy` decides one order's outcome at one market event from a
  `LiquidityContext` (asset, side, requested quantity, event price, size shown)
  and returns a `FillDecision`.
- `ImmediateFill` (default, fills in full), `StaticFill` (the pre-v2.2 fixed
  `fill_status` argument, as a policy) and `LiquidityCappedFill` (fills up to a
  share of the size the event showed; partial when capped, no fill when it showed
  none).
- `ExecutionPipeline.process_market_event` / `process_quote` gained an optional
  `fill_policy` parameter, which takes precedence over `fill_status` /
  `fill_quantity`. Both previous arguments still work unchanged.

### Persistent containers — `alphalab.common.persistent_map`

- `PersistentMap` and `PersistentSet`: immutable `Mapping` / `Set` with O(1)
  amortized update and structural sharing, using the same "shared append-only
  storage plus copy on branch" idiom `AppendOnlyLog` established in v2.1. Older
  versions keep observing exactly what they observed before; iteration is in
  first-insertion order.

### Deterministic identifiers — `alphalab.common.ids`

- `DeterministicIdSource(seed)` and `use_id_source(source)` scope where
  identifiers come from. `BacktestConfig.seed` installs one for a run and records
  it on the result.
- All identifier factories on the execution path now route through `new_id()`,
  `alphalab.core.ids.new_uuid` included.

### OMS snapshots — `alphalab.oms.snapshot`

- `capture` / `restore` / `from_primitives`, plus `OMSSnapshot` and
  `OMSEventRecord`.
- `alphalab.common.serialization` recognises a `__serializable__()` projection,
  which is how a type whose in-memory shape has no JSON form declares one.

### Benchmarks

- `benchmarks/benchmark_backtesting.py` — backtest and replay throughput, the
  scaling factor across a 4x workload, the replay cursor's overhead, and a parity
  assertion at both sizes.

---

## Fixed

### The OMS order book was quadratic

`OrderBook.add` rebuilt the whole order `dict` and both index `frozenset`s,
`OrderBook.replace` rebuilt the order `dict`, and `OMSEngine._update_sets`
rebuilt both order-id `frozenset`s — once per stored order, and the OMS stores an
order on submit and again on every lifecycle transition. Submitting N orders
copied O(N²) entries. All five containers are now persistent.

### The execution report index was quadratic too

Found by running the whole benchmark suite after the order-book fix, which is
the only reason it was found at all: `benchmarks_execution.py` took 85s for
100k fills. `ExecutionEngine.execute` and `partial_fill` stored a report by
rebuilding the whole `ExecutionState.reports` dict -- the same defect as the
order book, on the same execution path, paid by every fill a backtest produces.
`ExecutionState.reports` is now a `PersistentMap`; it is still an immutable
`Mapping` keyed by execution id and still serializes as the JSON object it
always did.

### Measured

On the development machine, full history retained:

| Benchmark | v2.1 | v2.2 |
| --- | --- | --- |
| `benchmark_oms` (100k order lifecycles) | 26.3 min | 6.7s |
| `benchmark_oms` scaling (10k → 20k) | 4.70x | 2.06x |
| `benchmarks_execution` (100k fills) | 85.3s | 1.55s |
| `benchmark_execution_pipeline` (4000 events) | 1.79s | 1.03s |
| `benchmark_execution_pipeline` scaling (4x workload) | ~7.4x | ~4.4x |

The residual above 4.00x in the pipeline benchmark is the cyclic garbage
collector walking a growing live heap, not an algorithmic term: with the
collector paused the same path scales 2.06x and 2.08x per doubling.

### A risk-rejected request leaked its allocation reservation

`AllocationEngine.allocate` commits capital against every request it emits.
A request that risk refused was skipped with a bare `continue`, and a request
with no market price was skipped earlier still; neither released anything, so
`notional_allocated` over-reported for the rest of the run.

`AllocationState.reservations` is now a per-order ledger. The allocation engine
owns the amount, the pipeline owns the moment, and releasing an order that holds
no live reservation raises `UnknownReservationError` instead of silently
subtracting — which is what makes "released exactly once" checkable.

### `OMSState` could not be serialized as a whole state

`OrderBook` keys orders by `OrderId`, a dataclass, which JSON cannot use as an
object key. Rather than weakening the identifier, the state now declares an
explicit projection: orders serialize as an array in submission order, the
derived indices are omitted and rebuilt on restore, and every event carries an
`event_type` tag so the log reads back as typed events.
`restore(from_primitives(deserialize(serialize(state)))) == state`, and the
restored state is a working state the engine carries on from. Values without such
a projection are still rejected by the encoder rather than stringified.

### Replay produced nothing

`alphalab.replay` sequenced events and never reached a strategy, an order or a
portfolio. It now drives the real path through `alphalab.backtesting.replay`.

---

## Changed

- **Breaking:** `AllocationEngine.release_reservation(state, order_id, timestamp)`
  no longer takes the amount to release — the ledger owns it.
- `OMSState.active_orders` / `completed_orders` are `PersistentSet[OrderId]`
  rather than `frozenset[OrderId]`. They compare equal to a `frozenset` in both
  directions and support `in`, `len` and iteration as before; iteration is now in
  insertion order rather than hash order.
- `OrderBook.orders()` and `orders_for_asset` / `orders_for_strategy` return
  orders in submission order.
- `AllocationState` gained `reservations`; `AllocationEngine` gained
  `reserved_notional`, and `alphalab.allocation` gained the `reserved_for_order`
  and `open_reservations` views.
- `benchmarks/benchmark_oms.py` reports a scaling factor and fails if it exceeds
  3.00x. It and `tests/regression/test_oms_book_complexity.py` pause the cyclic
  collector around their timed sections, because otherwise the growth ratio
  measures the collector rather than the data structure.
- `benchmark_execution_pipeline`'s scaling ceiling tightened from 12.0x to 6.0x.

---

## Tests

1599 tests pass (1429 on v2.1.0). New:

| Area | File |
| --- | --- |
| Persistent containers | `tests/unit/common/test_persistent_map.py` |
| Reservation ledger | `tests/unit/allocation/test_reservations.py` |
| Fill policies | `tests/unit/execution/test_fill_policy.py` |
| OMS snapshots | `tests/unit/oms/test_oms_snapshot.py` |
| Dataset validation | `tests/unit/backtesting/test_dataset.py` |
| Backtest loop | `tests/unit/backtesting/test_engine.py` |
| OMS complexity | `tests/regression/test_oms_book_complexity.py` |
| Execution report index complexity | `tests/regression/test_execution_reports_complexity.py` |
| Reservation leak | `tests/regression/test_risk_reservation_leak.py` |
| Whole-state OMS serialization | `tests/regression/test_oms_state_snapshot.py` |
| Run-to-run determinism | `tests/regression/test_deterministic_backtest.py` |
| Full backtest path | `tests/integration/test_backtest_pipeline.py` |
| Backtest/replay parity | `tests/integration/test_backtest_replay_parity.py` |

`tests/regression/test_state_serialization.py` no longer pins `OMSState` as
unserializable; it pins the fix, and that a raw dataclass-keyed mapping is still
rejected.

---

## Not in this release

Deferred to v2.3: market-data model convergence (`data` / `marketdata` / `feed`,
and the three separate `Bar` types), `broker` / `brokers` consolidation, and live
broker connectivity into the execution path. See `docs/ARCHITECTURE.md`,
"Known gaps and deferred areas".

---

# [2.1.0] - 2026-09-04

## Overview

AlphaLab 2.1.0 is "Execution + Portfolio Correctness". It makes the existing
execution spine correct and fast rather than adding new packages: mark-to-market,
a single monetary precision policy with an exact accounting identity, explicit
execution invariants, correct persistence of engine histories, and the fix for
the O(N^2) event accumulation that stopped the risk benchmark from completing.

No new packages. No new domain models. `PortfolioState` remains the single
canonical portfolio state and `oms.order.Order` the single lifecycle order.

---

## Breaking Changes

Public symbols or behaviour changed. Import sites and callers may need updating.

- **Engine histories are `AppendOnlyLog`, not `tuple`.** `RiskState`,
  `MarketState`, `ExecutionState`, `OMSState`, `AllocationState`,
  `PortfolioState`, `TransactionLedger` and the `ExecutionPipelineState`
  accumulators now hold `alphalab.common.AppendOnlyLog`. It is an immutable
  `Sequence` and compares equal to tuples and lists, so `len()`, indexing,
  slicing, iteration, `in`, `reversed()` and `== (...)` are unchanged. Code that
  required a literal `tuple` (`isinstance(..., tuple)`, concatenation with `+`)
  must call `.to_tuple()`.
- **`PortfolioEngine.apply_fill` rejects malformed fills** with
  `InvalidTransactionError`: non-positive price, negative commission, and a
  quantity that is zero or rounds to zero at `SHARE_QUANT`. These previously
  produced an incoherent position, a fabricated `PositionClosed` event, or a
  ledger entry for a trade that did not happen.
- **Every non-trading execution outcome is terminal for the order.** A
  rejected, expired or unfilled execution moves the OMS order to `REJECTED` /
  `EXPIRED` / `CANCELLED` and out of `active_orders`; previously it stayed
  `ACCEPTED` and open forever. `Order.reject` accordingly accepts `ACCEPTED` in
  addition to `NEW` / `PENDING`; an order that has already traded still cannot
  be rejected.
- **One portfolio snapshot per market event**, not one per fill.
  `ExecutionPipelineState.portfolio_snapshots` now also has a point for events
  that only marked the book and did not trade.
- **`DeterministicEncoder` no longer stringifies unknown objects.** `Decimal`,
  dataclasses, `AppendOnlyLog`, `Enum` and `UUID` are handled by explicit
  branch; anything else raises `SerializationError` naming the type. Callers
  that relied on the previous silent `str()` fallback were receiving payloads
  that could not be read back.
- **`AnalyticsEngine.compile_report`, `AllocationEngine.allocate` and
  `calculate_attribution`** accept `Sequence` where they previously required
  `tuple`. Existing tuple callers are unaffected.

---

## Added

- **Mark-to-market.** `PortfolioEngine.update_market_prices` is wired into
  `ExecutionPipeline.process_market_event` and runs *before* any decision is
  taken on the event, so unrealized P&L and NAV reflect the current market and
  the risk state is resynced from the marked book. It moves unrealized P&L only
  -- cash, realized P&L, commissions and the ledger are untouched. Non-positive
  prices are rejected as invalid market data and unheld assets ignored; a
  position with no price keeps its previous mark. Emits `MarketValueUpdated`
  when something was actually re-marked.

  The marked portfolio reaches **risk** only. The strategy's `StrategyContext`
  comes from the caller's `context_factory`, which the pipeline does not
  populate, and allocation sizes from market prices and its capital budget.
- **`PortfolioValuation.snapshot` / `PortfolioValuationSnapshot`** -- the
  deterministic read model over `PortfolioState`: cash, long/short/positions
  value, unrealized and realized P&L, commissions, and equity. Carried on
  `ExecutionPipelineResult.valuation` and projected into the analytics
  `PortfolioSnapshot`. Not a second portfolio state.
- **`PortfolioState.realized_pnl` and `PortfolioState.commission_paid`** --
  cumulative account totals that survive a position being closed and dropped
  from `positions`.
- **`alphalab.portfolio.money`** -- the portfolio's single monetary precision
  policy (see *Monetary precision* below), and **`Position.cost_basis`**, the
  authoritative money figure it rests on.
- **`ExecutionPipelineResult.unpriced_requests`** -- order requests dropped
  because the pipeline had no market price for the asset.
- **`alphalab.common.AppendOnlyLog`** -- immutable append-only sequence with
  O(1) amortized append and copy-on-branch structural sharing.
- **`benchmarks/benchmark_execution_pipeline.py`** -- end-to-end pipeline
  throughput and scaling benchmark.
- Tests: `tests/unit/common/test_append_log.py`,
  `tests/unit/portfolio/test_portfolio_invariants.py`,
  `tests/unit/portfolio/test_monetary_precision.py`,
  `tests/integration/test_mark_to_market_pipeline.py`,
  `tests/regression/test_event_accumulation_complexity.py`,
  `tests/regression/test_state_serialization.py`.

---

## Monetary precision

`alphalab.portfolio.money` holds one rounding policy for the whole portfolio:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter, and both the cash movement and
   the position's cost basis are derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6).

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between money in and money out; unrealized P&L is `market_value - basis`.
Splits (partial close, reversal) round one part and derive the other by
subtraction, so the parts always sum to the exact whole. `average_cost` is
derived from the basis and keeps its meaning; a `Position` constructed without a
`cost_basis` derives one from `average_cost * |quantity|`.

The accounting identity is therefore **exact** -- an identity over exact Decimal
values for any price and quantity the engine accepts, not an approximation that
happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

---

## Fixed

- **O(N^2) event/history accumulation.** Every engine grew its append-only
  history with `(*state.events, event)`, rebuilding the whole tuple on each
  transition; N transitions copied O(N^2) elements. Measured on the development
  machine, with full history retained in every case:

  | Benchmark | v2.0.0 | v2.1.0 |
  | --- | --- | --- |
  | `benchmark_risk_engine` (100k evaluations) | 285.7s | **1.4s** |
  | `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | **~167,000 ops/sec** |
  | `benchmarks_market_engine` (100k books) | 640 ops/sec | **~165,000 ops/sec** |
  | `benchmark_portfolio_engine` (20k fills) | 1.90s | **0.22s** |
  | `benchmark_execution_pipeline` (4000 events) | 6.76s | **~1.8s** |

  `benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0.
  `benchmark_portfolio_engine`'s full 100k-fill workload could not complete on
  v2.0.0 at all; it now runs in ~1.9s. End-to-end, the pipeline benchmark's
  scaling across a 4x workload dropped from ~17x to ~7.5x.
- **The accounting identity was not exact.** The cash ledger rounded
  `quantity * price + commission` while the position independently rounded
  `(exit_price - average_cost) * quantity`. Two roundings of one economic event
  disagreed by up to half a cent each and the error accumulated: an ordinary
  penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the identity out
  by $0.01, and randomized multi-asset portfolios drifted by up to $0.05. See
  *Monetary precision* above.
- **Append-only histories serialized as a repr string.** `dataclasses.asdict`
  recurses into tuples but deep-copies anything else, so an `AppendOnlyLog`
  reached `DeterministicEncoder`, whose `str()` fallback wrote
  `"events": "AppendOnlyLog([...])"` instead of a JSON array. It raised nothing
  and passed snapshot validation, so `PersistenceAdapter.to_snapshot` of a
  migrated state silently persisted unreadable history.
  `alphalab.common.dataclass_to_dict` now does its own recursion -- `asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced.
- **Realized P&L was discarded when a position closed.** Closing removes the
  position from `positions`, taking its `realized_pnl` with it, so account-level
  realized P&L was unrecoverable after a round trip. It now accumulates on
  `PortfolioState`.
- **Analytics trade records could be credited with another fill's P&L.**
  `ExecutionPipeline._trade_record` scanned the whole portfolio history in
  reverse for the asset's last realized-P&L event, so an opening fill inherited
  an earlier close's P&L and the performance report overstated realized P&L on
  every re-entry. It now reads only the events the current fill produced.
- **An order for an asset with no market price raised `KeyError`.** Allocation
  prices unknown assets at `0.00`; the pipeline now drops such requests before
  the OMS and reports them on `unpriced_requests`. The condition is per-event --
  a later quote makes the asset tradeable.
- **`FillStatus.NO_FILL` left the order open.** It now moves the order to
  `CANCELLED`, removes it from `active_orders`, and releases the allocation
  reservation exactly once -- fabricating no fill, trade or position.
- **Venue-rejected orders stayed open forever** in `oms.active_orders`, so open
  orders never reconciled with fills.
- **Positions were never repriced between fills**, so unrealized P&L, NAV, risk
  NAV and the equity curve were stale until the next trade.
- **`benchmarks/benchmarks_market_engine.py` could never run.** Its first quote
  carried timestamp `0.0`, which `market.timestamp.is_valid_timestamp` rejects
  as not strictly positive, so the benchmark raised `MarketValidationError`
  immediately. The sequence now starts at `1.0`. (Pre-existing on v2.0.0.)

---

## Not Changed

- The D1 close-fill cash accounting fix from 2.0.0 stands: realized P&L is still
  never added to cash on top of the trade proceeds.
- Commissions still stay out of a position's cost basis; `average_cost` remains
  a clean per-unit price.
- No package was added, removed, or merged. The standalone-engine /
  integrated-path split from ADR-0009 is unchanged.

---

## Known Limitations

- **The OMS order book is the execution path's remaining super-linear term.**
  `OrderBook.add` / `.replace` copy the whole order dict and
  `OMSEngine._update_sets` copies both order-id frozensets, once per stored
  order. This is a persistent-map problem, not event accumulation, and was
  deliberately left out of scope. `benchmarks/benchmark_execution_pipeline.py`
  measures it.
- **`OMSState` cannot be JSON-serialized as a whole state**, on 2.1.0 exactly as
  on 2.0.0: `OrderBook` keys orders by the `OrderId` dataclass, which neither
  `asdict` nor `json.dumps` accepts as a mapping key. Its history logs serialize
  correctly; the limitation is the typed identifier, not the log.
- **A risk-rejected request does not release its allocation reservation**, so
  `notional_allocated` over-reports after a risk rejection. Pre-existing; it
  does not gate trading, because the budget check reads
  `available_global_capital`.
- `dataclasses.asdict` cannot be extended, so it still returns an
  `AppendOnlyLog` for a history field. `alphalab.common.dataclass_to_dict` and
  `alphalab.persistence.serialize` are the supported serialization boundary.
- **Valuation is single-currency.** `PortfolioValuation` and `NAVCalculator`
  value the base currency only.
- **`_trade_record` still hard-codes** `sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` ("D3", deferred).

---

## Quality Gates

`ruff check`, `ruff format --check`, `mypy` (strict, 843 source files), `pytest`
(1429 tests), `git diff --check`, `python -m build`, and `twine check dist/*`
all pass.

---

# [2.0.0] - 2026-09-04

## Overview

AlphaLab 2.0.0 is the v2 release line. It consolidates the v1.34.0–v1.46.0 engine
series, adds four new standalone packages, unifies the canonical execution-path
domain models, and fixes two portfolio/analytics defects found by an end-to-end
trading-research validation.

AlphaLab remains a library: there is no server, daemon, scheduler process, or CLI.
`alphalab.runtime.ExecutionPipeline` is the only spine that wires domain engines
together (market → strategy → allocation → risk → OMS → execution simulator →
portfolio → analytics); every other package is an independent, individually tested
engine that is not fused into a single runtime.

---

## Breaking Changes

Public symbols removed or changed. Import sites must be updated.

- **`alphalab.core.OrderRequest` is now the single proposed-order DTO.** The
  independent `alphalab.allocation.request.OrderRequest` /
  `alphalab.allocation.request.OrderSide` and the independent
  `alphalab.risk.models.OrderRequest` / `alphalab.risk.models.OrderSide` are
  removed. `alphalab.allocation.request` no longer exists. `alphalab.risk.models`
  now contains only `RiskViolation`. `OrderSide` is no longer part of the
  `alphalab.allocation` or `alphalab.risk` public API — use
  `alphalab.core.enums.Side`.
- **`alphalab.core.enums.Side` is the canonical order direction** everywhere on
  the execution path (`OrderRequest`, `oms.order.Order`, `Fill`, `Trade`, risk
  and allocation checks). `BUY`/positive and `SELL`/negative semantics are
  unchanged.
- **`alphalab.oms.order.Order` is the canonical lifecycle order.** The
  `alphalab.core.order` module and the `alphalab.core.Order` re-export are
  removed, along with `oms.order.Order`'s unused adapter surface
  (`to_core_order`, `from_core_order`, `canonical_order`). Every
  `oms.order.Order` field, property, and lifecycle transition is unchanged.
- **`Fill.filled_at` and `Trade.executed_at` are now `float`** (Unix seconds),
  matching every other timestamp on the execution path. They were previously
  timezone-aware `datetime`; the tz-aware `__post_init__` guard is removed. In
  `dataclasses.asdict` output these fields are now numbers, not `datetime`
  objects.
- **Removed proven-dead `alphalab.core` symbols:** `OrderCompat`, `Event`
  (`core/event.py`), `Signal` (`core/signal.py`), and the
  `alphalab.core.portfolio` / `alphalab.core.position` re-export shims. Use
  `alphalab.portfolio.engine.PortfolioState` and
  `alphalab.portfolio.position.Position` directly. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is unaffected.
- **Package version** is `2.0.0`; the `alphalab.common.version` fallback and the
  `Development Status` classifier (`4 - Beta`) are updated to match.

---

## Added

### New packages (PR047–PR050)

- **Model Registry** (`alphalab.model_registry`) — versioned registration of
  trained model artifacts, `NONE → STAGING/PRODUCTION/ARCHIVED` promotion with an
  immutable promotions log, production rollback, and deployment metadata. Links
  to `alphalab.experiment_tracking` runs by id. State threaded functionally
  through immutable `ModelRegistry` values; no on-disk serialization.
- **AI Research Assistant** (`alphalab.research_assistant`) — deterministic,
  offline (no LLM, no network) grid-search research driver: candidate generation
  over a parameter grid, evaluation via a caller-supplied evaluator, best-first
  ranking, Markdown reporting, a one-call `run_research_workflow`, and a bridge
  that lifts a chosen candidate into an `alphalab.studio` `StrategyDefinition`.
- **Deployment Manager** (`alphalab.deployment_manager`) — packaging of
  strategy/model stacks into versioned, SHA-256-checksummed `ReleasePackage`s
  (manifest only, no artifact bytes), an append-only ledger of which release is
  active per environment, and previous-release rollback.
- **AlphaLab Enterprise** (`alphalab.enterprise`) — deterministic in-memory
  governance layer over one immutable `EnterpriseState`: session-lifecycle
  identity (no credentials accepted or stored), RBAC, append-only audit log,
  multi-user workspaces, secret *references* + rotation metadata (no secret
  values), and a compliance snapshot.

### Engine series (v1.34.0 – v1.46.0, PR034–PR046)

Delivered on `main` before the v2 line and included in 2.0.0: feature store,
factor library, options engine, futures engine, crypto engine, macro engine,
alternative data engine, machine learning engine, deep learning engine,
reinforcement learning engine, cloud research engine, cluster scheduler, and
experiment tracking. Each is a standalone deterministic package with its own
tests and benchmark.

---

## Changed — canonical execution domain models (R1–R4)

- **R1** — unified allocation/risk `Side` and `OrderRequest` into
  `alphalab.core` (see Breaking Changes). `alphalab.runtime.execution_pipeline`
  no longer converts requests field-by-field across the allocation → risk → OMS
  boundary; the `_risk_request` / `_core_side` bridges and
  `execution_adapters.core_side_from_oms` are deleted.
- **R2** — removed the five proven-dead `alphalab.core` symbols (see Breaking
  Changes). Implementations deleted outright; no aliases left behind.
- **R3** — retitled `alphalab.oms.order` as the canonical lifecycle order and
  removed its dormant adapter hooks; deleted `alphalab.core.order`. The order the
  execution pipeline produces is byte-for-byte identical.
- **R4** — `Fill.filled_at` / `Trade.executed_at` changed from `datetime` to
  `float`. `execution_adapters.canonical_execution_from_report` now passes
  `report.timestamp` straight through instead of
  `datetime.fromtimestamp(..., tz=UTC)`.

---

## Fixed

- **D1 (critical) — portfolio close/reduce cash accounting.**
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill` computed
  `cash_impact = -(quantity * price) - commission + pnl`. On a closing or
  reducing fill the `+ pnl` term double-counted realized P&L into cash —
  overstating cash / NAV / `PerformanceReport.ending_capital` /
  `RiskState.current_nav` on wins and understating them on losses. The `+ pnl`
  term is removed; position cost-basis math and the
  `PositionReduced` / `PositionClosed` event payloads are unchanged. Guarded by
  new unit tests (winning, losing, and partial-reduction round-trips) and
  `tests/regression/test_close_fill_cash_accounting.py`, which drives the real
  `ExecutionPipeline` open → hold → close and asserts
  `NAV == ending_capital == risk.current_nav == starting_cash + realized_pnl - commissions`.
- **D2 (medium) — `PerformanceReport` serialization.**
  `alphalab.analytics.attribution.calculate_attribution` wrapped
  `AttributionMetrics` fields in `types.MappingProxyType`, which
  `alphalab.persistence` (via `dataclasses.asdict`) cannot copy
  (`cannot pickle 'mappingproxy' object`). It now returns ordinary `dict`s, like
  every other frozen dataclass in AlphaLab. Field names and the
  `Mapping[str, Decimal]` annotations are unchanged.

---

## Build / Packaging

- The release build and distribution validation were aligned with **Core
  Metadata 2.5 / Twine 7**. Hatchling 1.30 emits `Metadata-Version: 2.5`
  (PEP 639), which Twine 6 rejects; the dev toolchain pin is now
  `twine>=7.0,<8`, and the redundant unpinned `pip install build twine` step
  was dropped from the CI and release workflows so they use the pinned
  toolchain. `python -m build` and `twine check dist/*` pass for the 2.0.0
  wheel and sdist. Hatchling is unchanged; no application dependency changed
  (`dependencies = []`).

---

## Known gaps (unchanged in 2.0.0)

- `alphalab.runtime.execution_pipeline._trade_record` still attributes realized
  P&L by scanning portfolio events in reverse and hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` (deferred — "D3").
- Mark-to-market position repricing is not implemented.
- `alphalab.replay` is a standalone engine; it does not drive `ExecutionPipeline`.
- `alphalab.data.feed.Bar` and `alphalab.market.bar.Bar` are separate,
  incompatible models (a third `Bar` lives in `alphalab.marketdata.feed`).
- The `broker` / `brokers`, `marketdata` / `data` / `feed`, `kernel`, and
  `core/events` areas remain intentionally unresolved / product-surface
  decisions.

---

## Quality gates

- ruff check, ruff format --check, mypy --strict (833 source files) — clean
- pytest — 1221 passed (1207 unit, 4 integration, 10 regression)
- `git diff --check` — clean

---

# [1.0.0] - 2026-07-05

## First Stable Release

AlphaLab 1.0.0 is the first stable public release of the framework.

This release establishes the core architecture for deterministic quantitative research, systematic strategy development, portfolio optimization, historical replay, production runtime management, and institutional research workflows.

---

## Added

### Core Framework

- Immutable domain models
- Deterministic engine APIs
- Event-driven architecture
- Shared validation utilities
- Shared registry utilities
- Common infrastructure package
- Python 3.12 support
- Strict static typing throughout the framework

### Research

- Research Engine
- Statistical research workflows
- Strategy evaluation
- Research payload validation

### Strategy Runtime

- Strategy lifecycle management
- Event dispatch
- Runtime supervision
- Context abstraction
- Intent validation

### Universal Data Engine

- Canonical datasets
- Dataset metadata
- Schema validation
- Data quality reporting
- Timeframe conversion
- Dataset cataloguing

### Replay Engine

- Historical event replay
- Deterministic market simulation
- Timeline reconstruction

### Portfolio Optimizer

- Capital allocation
- Equal Weight optimization
- Minimum Variance optimization
- Maximum Sharpe optimization
- Inverse Volatility optimization
- Portfolio constraints
- Exposure analysis
- Transaction cost estimation
- Portfolio rebalancing

### Broker Integrations

- Provider abstraction
- Paper Trading
- Alpaca integration architecture
- Interactive Brokers integration architecture
- Zerodha integration architecture
- Authentication workflows
- Connection management

### Production Runtime

- Runtime supervision
- Health monitoring
- Checkpointing
- Recovery workflows
- Runtime metrics

### Strategy Studio

- Project management
- Strategy registration
- Research sessions
- Pipelines
- Reports
- Workspace management
- Backtest orchestration

### AlphaLab Workbench

- Unified workspace
- Project management
- Research orchestration
- Dataset management
- Dashboard infrastructure

---

## Documentation

Added comprehensive documentation including:

- README
- Getting Started Guide
- Architecture Guide
- System Design
- Engineering Guidelines
- Architectural Decision Records (ADRs)
- Examples documentation
- Contributing Guide

---

## Examples

Added ten fully synchronized runnable examples covering:

1. Research Engine
2. Strategy Runtime
3. Replay Engine
4. Market Data
5. Broker Integrations
6. Portfolio Optimizer
7. Universal Data Engine
8. Strategy Studio
9. Workbench
10. Complete end-to-end workflow

---

## Engineering

Improved overall project quality through:

- Shared validation infrastructure
- Shared registry utilities
- Common package refactoring
- Consistent immutable APIs
- Packaging improvements
- Version alignment
- Example synchronization
- Release engineering

---

## Quality Assurance

Validated with:

- ✅ 583 passing unit tests
- ✅ Strict MyPy (631 source files)
- ✅ Ruff clean
- ✅ Python package build
- ✅ Wheel validation
- ✅ Source distribution validation
- ✅ Twine package verification

---

## Packaging

AlphaLab 1.0.0 is distributed as:

- Source Distribution (`sdist`)
- Universal Python Wheel (`py3-none-any`)

---

## Notes

This release establishes the stable architectural foundation of AlphaLab.

Future releases will expand the framework with additional quantitative research capabilities while maintaining backward compatibility wherever practical.