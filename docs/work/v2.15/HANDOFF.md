# v2.15.0 Working Handoff

**Read this first after any context compaction.** It records the baseline, the
architecture findings that cost the most to establish, and the decisions that
must survive. Do not repeat the archaeology unless the repository has
materially changed.

---

## 1. Baseline

| Fact | Value |
| --- | --- |
| Branch | `main` |
| Baseline commit | `10126d4` (merge of `60a960d`, tag `v2.14.0`) |
| Baseline tree | identical to `v2.14.0` |
| Tests at baseline | **3335 passed** |
| Ruff / format / mypy | clean; mypy strict over 966 source files |

**A trap that cost real time, recorded so it is not re-entered.** At session
start the working tree was at `aa2b7c2` (`v2.13.0`) and `main` was *two commits
behind* `origin/main`. The v2.14.0 work lives on `feat/v2.14-runtime-unification`
and reaches `main` only through the merge `10126d4`. The tree was fast-forwarded
(local only, no push) after verifying `origin/main^{tree} == v2.14.0^{tree}`.
If the tree ever looks like v2.13 again, check this first.

**Known metadata defect inherited from v2.14.0:** `pyproject.toml` still declares
`version = "2.13.0"` at the v2.14.0 tag. `alphalab/common/version.py` is the
other half of that fact. Both are bumped as part of the v2.15.0 release step.

---

## 2. Architecture findings

These are read out of the code, not the README. Where a doc and the code
disagreed, the code won.

### The run runtime (v2.14, ADR-0030)

Two tiers, and v2.15 changes neither's ownership:

* `runtime.execution_pipeline.ExecutionPipeline` owns the **execution step**
  (market -> strategy -> allocation -> risk -> OMS -> execution -> portfolio ->
  analytics).
* `runtime.run.RunEngine` over `RunState` owns the **run** (cursor, skips,
  per-record steps, the identifier-stream scope a stopped run continues in).

A **driver** decides only which record comes next and what clock reading judges
it. `TradingSession`, `BacktestEngine`, `ReplayBacktest` are the three. ADR-0030
explicitly anticipates a streaming driver and a live driver as *later additions
requiring no change to `RunEngine`*. **v2.15's live and streaming work lands as
a driver, not as a second runtime.**

### Where each capability's seam already is

| Seam | Module | State |
| --- | --- | --- |
| Broker contract | `broker.protocol.BrokerProtocol` | Complete. Pure functional: `(state, args) -> (state, events)`. |
| Reference adapter | `broker.paper.PaperBroker` | Complete, a simulation. |
| Order out / fill back | `runtime.broker_routing` | Complete and tested, both directions, with pre-trade gates and derived client order id. |
| Reconciliation | `broker.reconciliation` | Complete: duplicate, unknown-order, terminal-order, overfill. |
| Market-data source | `market.source.MarketDataSource` | Complete. `records()` returns an *iterator* — a blocking generator over a socket satisfies it unchanged. |
| Provider -> path link | `market.provider.ProviderHistorySource` | Complete, but **history only**: finite, re-iterable, no subscription. |
| HTTP transport | `marketdata.transport` | Real (`HttpTransport`, stdlib urllib) but **GET-only, unauthenticated**. Cannot carry order submission. |
| Run-state durability | `persistence.run_store` | Complete (ADR-0029). **This is the template artifact storage must follow.** |
| Instrument authority | `instrument.registry.InstrumentRegistry` | Canonical identity boundary (ADR-0016). Classification landed v2.11 (ADR-0027). |
| Strategy context | `strategy.context.StrategyContext` + `runtime.context_views` | Four of nine fields populated by the pipeline (ADR-0026). |

### Ownership rules that must not be violated

* **One runtime owner**: `RunEngine`. Do not add a second run state.
* **One persistence owner** for run state: `RunStateStore`. The nine legacy
  `persistence` modules (`storage`, `engine`, `protocol`, ...) and the whole
  `alphalab.production` package are **deprecated, removed in v3.0** — build
  nothing on them.
* **One market-data domain model**: `alphalab.market`. `alphalab.live` and
  `alphalab.feed` are standalone off-path engine libraries. **Streaming must not
  be built there** — that would be a second market-data lifecycle.
* **One identity authority**: `InstrumentRegistry`. No second security master.

---

## 3. The five capabilities: actual status at v2.14.0

Legend: **A** exists · **B** foundational only · **C** production-capable · **D** missing.

### 1. Real execution — **B, transport missing**

Everything above the wire is done and tested: canonical vocabulary,
`BrokerProtocol`, routing, reconciliation, idempotent submission, pre-trade
gates, the fill return leg through `ExecutionPipeline.apply_execution_report`.

Missing (**D**): *any* transport to a venue. `ARCHITECTURE.md` states it
outright — "AlphaLab does not support live trading"; vendor adapters are "stubs
— canned responses or `NotImplementedError`". `session.py` states a live session
"produces working orders and stops".

**Gap:** an authenticated, effectful venue transport and a `BrokerProtocol`
implementation over it, plus the live driver that turns working orders into
routed orders and venue executions into canonical fills.

### 2. Streaming market data — **D**

`ProviderHistorySource` is explicitly "a *history* source ... It does not poll,
subscribe, reconnect or stream". `binanceClient.subscribe` is a documented
no-op. `marketdata.transport` is GET-only. There is no socket, no frame codec,
no subscription lifecycle, no reconnect anywhere on the canonical path.

**Gap:** a genuine streaming transport and a `MarketDataSource` over it carrying
the full lifecycle, feeding the canonical run step.

### 3. Artifact storage — **B, bytes never held**

`ArtifactRef` exists (`model_registry.registry`) and records uri / media_type /
checksum / size. Its own docstring: "AlphaLab stores no artifact bytes and
implements no object store ... AlphaLab never computes it, because it never
reads the bytes."

**Gap:** a real store that holds bytes, addresses them by digest, verifies on
read, and *produces* the `ArtifactRef` the registry already records.

### 4. Security master — **C for identity and classification; provenance missing**

Genuinely done in v2.11 and **must be preserved**: derived immutable `asset_id`
(uuid5 over a canonical key), strict key-field normalization, refusal-not-
overwrite registration, alias indexing, `classify_instrument`, and — importantly
— *historical attribution is already structurally correct*: the sector is read
once and frozen onto `TradeRecord.sector_id` at fill time, so reclassification
cannot rewrite history.

**Gap:** classification carries **no provenance**. `classify_instrument` records
a bare label — "mints no identifier and emits no event" — so the registry cannot
say who classified an instrument, from what source, or as of when, and a
reclassification leaves no trace at the registry. There is also no way to
capture/restore the registry, so the authority cannot be durably kept.

### 5. Strategy context — **B, two surfaces decorative**

v2.10/ADR-0026 populates `portfolio`, `orders`, `risk_view`, `market` from the
marked locals, read-only, with cross-strategy visibility structurally refused.
That part is real and must be preserved.

**Gap:** `history` and `universe` are **decorative** — their protocols
(`HistoryAccessorProtocol`, `UniverseProtocol`) declare *no methods*, and every
construction site in the repository passes `object()`. ADR-0026 deferred both
deliberately: history "requires a clock-bounded accessor whose bound is enforced
at construction"; universe "requires deciding whether membership is
configuration, instrument-registry state, or a risk control".

---

## 4. Decisions

Recorded because they are load-bearing and expensive to re-derive.

1. **Live and streaming land as a driver over `RunEngine`**, not a new runtime.
   ADR-0030 named this shape; taking it costs no change to `RunEngine`.

2. **A streaming source is a `MarketDataSource`.** `records()` already returns an
   iterator; a generator pulling a live connection satisfies the protocol with
   no new abstraction and no second lifecycle.

3. **Streaming declares `OrderingGuarantee.UNORDERED`.** A venue can reorder, and
   `RunConfig.ordering` already has the honest answer for it. Nothing is
   buffered or reordered — the existing rule, unchanged.

4. **Artifact storage copies `run_store.py`'s shape exactly**: a narrow protocol,
   one real file backend, one deterministic in-memory double constructed *by
   name*, a digest envelope verified on read, atomic writes, refusal not repair.
   It produces `ArtifactRef` — **no second reference type**.

5. **Artifact identity is content-addressed**, and the `uri` is an opaque
   `alphalab-artifact:sha256:<hex>` — never a filesystem path. `ArtifactRef.uri`
   is already documented as opaque, so no path leaks to a caller.

6. **Universe membership is the instrument registry.** It is already on
   `ExecutionPipelineConfig.instruments`, already read-only, already the ADR-0016
   identity authority. This answers ADR-0026's deferred semantic question
   without inventing a configuration surface or a second authority.

7. **The caller keeps `clock`; the pipeline owns the look-ahead bound.**
   ADR-0026 recorded caller-owned `clock` as a decision, and
   `test_strategy_context_visibility.py` pins `context.clock is _CLOCK`.
   Overlaying it would weaken a passing test to satisfy a checklist. The real
   time-semantics requirement is look-ahead safety, so the authoritative event
   time is expressed where it actually binds — on the history view, enforced at
   construction and exposed as `as_of`.

8. **Allocation visibility is NOT added.** ADR-0026 refuses it permanently and
   with a reason: showing a strategy the capital its own intent will reserve
   invites it to pre-size, duplicating `AllocationEngine`'s authority
   (ADR-0015). The architecture does not require it; adding it to complete a
   checklist would be the "arbitrary strategy API" the release contract forbids.

9. **`MarketView` gains no history.** `test_the_market_view_exposes_no_history`
   pins `not hasattr(view, "history")`. History is a separate context surface,
   which is what ADR-0026 intended.

10. **Real credentials never enter the repository.** No secret in source, tests,
    fixtures, snapshots, logs or config. The transport takes credentials as a
    caller-supplied value and redacts them from every error and log path.

11. **Protocol-faithful local servers prove the transports.** No network egress
    exists here, so genuine socket-level integration tests run against local
    servers that speak the real protocols. A test double that returns "accepted"
    is not a transport and is not counted as one.

---

## 5. Status

**Phase: complete. All five capabilities implemented, integrated, tested, documented and certified.**

| Capability | State |
| --- | --- |
| Real execution | **done** -- transport, adapter, tests over a real socket |
| Streaming market data | **done** -- RFC 6455 client, source, canonical-path tests |
| Artifact storage | **done** -- content-addressed store, verification, registry integration |
| Security master | **done** -- provenance, append-only history, as-of, durable snapshot |
| Strategy context | **done** -- history + universe, look-ahead safe, cross-engine parity |
| Cross-capability integration | **done** -- all five in one lifecycle test |
| Documentation | **done** -- ADR-0031, ARCHITECTURE, README, CHANGELOG |
| Certification | **done** -- every gate green, build at 2.15.0 |

### Files added

Production:
- `alphalab/broker/transport.py` -- `VenueTransport`, `HttpVenueTransport`,
  `VenueCredentials` (redacting), `VenueResponse`, error types.
- `alphalab/broker/venue.py` -- `RestVenueBroker`, a `BrokerProtocol` over the
  transport: submit/cancel/replace/poll/fetch, retries, recovery.
- `alphalab/marketdata/websocket.py` -- RFC 6455 client (handshake with
  verified accept token, framing, masking, fragmentation, ping/pong, close).
- `alphalab/market/stream.py` -- `StreamingSource`, a `MarketDataSource` over
  the socket with the full lifecycle.

Artifact storage + security master:
- `alphalab/model_registry/artifact_store.py` -- `ArtifactStore` protocol,
  `FileArtifactStore`, `MemoryArtifactStore`, content addressing, verification.
  **Lives in `model_registry`, not `persistence`**: `ArtifactRef` does, and
  `persistence`'s every other module imports nothing but `common` and its own
  siblings, so putting it there would have inverted the dependency direction.
- `alphalab/instrument/classification.py` -- `SectorClassification` (label +
  source + as_of), `ClassificationHistory` (append-only).
- `alphalab/instrument/registry.py` -- `classify_instrument` gains optional
  `source`/`as_of`; new `classification_of`, `classification_history`,
  `sector_as_of`. `InstrumentRegistry` gains a `classifications` index.
- `alphalab/instrument/snapshot.py` -- capture/restore for the registry.
  Deliberately **not** exported from `instrument/__init__` (it imports the
  persistence codec spine, and `alphalab.market` imports `instrument` on the hot
  normalization path) -- the convention `portfolio` and `allocation` follow.

Tests:
- `tests/integration/venue_server.py` -- real HTTP venue: verifies signature,
  timestamp window, idempotency key.
- `tests/integration/stream_server.py` -- real RFC 6455 server.
- `tests/integration/test_real_execution.py` (40)
- `tests/integration/test_streaming_market_data.py` (30)
- `tests/integration/test_streaming_session.py` (5)
- `tests/regression/test_streaming_does_not_contaminate_determinism.py` (6)
- `tests/regression/test_artifact_store.py` (52, both backends)
- `tests/integration/test_artifact_lifecycle.py` (7)
- `tests/regression/test_security_master.py` (38)
- `tests/regression/test_strategy_context_history_and_universe.py` (27)
- `tests/integration/test_v215_capabilities.py` (4) -- all five in one lifecycle

Strategy context:
- `alphalab/runtime/context_views.py` -- `HistoryView` (clock-bounded, O(1) to
  build, backwards walk with early stop), `UniverseView` (over the registry).
- `alphalab/strategy/context.py` -- both protocols typed; `NoHistory`/`NoUniverse`
  defaults so a caller omits what the pipeline owns.
- `alphalab/runtime/execution_pipeline.py` -- `_populate_context` overlays six
  fields instead of four. **This is the entire change to the execution step.**

Documentation:
- `docs/ADR/0031-...md` -- 13 decisions, ownership table, boundary-behaviour
  table, testing invariants, non-goals.
- `docs/ARCHITECTURE.md` -- live/artifacts/streaming sections and the known-gaps
  list truthed up.
- `README.md` -- the "does not support live trading" claim replaced with what is
  and is not true.
- `CHANGELOG.md` -- v2.15.0, plus a reconstructed v2.14.0 entry (see below).

### Tests run

`pytest -q` -> **3541 passed** (baseline 3335), stable over six consecutive
runs. `ruff check`, `ruff format --check`, `mypy .` (983 files) all clean. All
13 examples run.

### Performance against v2.14 (same machine, same benchmarks)

| Benchmark | v2.14 | v2.15 | Delta |
| --- | --- | --- | --- |
| execution_pipeline, 1,000 events | 0.2511s | 0.2527s | +0.6% |
| execution_pipeline, 4,000 events | 1.1995s | 1.1815s | -1.5% |
| backtesting, 1,000 records | 0.2416s | 0.2370s | -1.9% |
| backtesting, 4,000 records | 1.2054s | 1.1983s | -0.6% |
| runtime, 1,000 records | 0.2347s | 0.2355s | +0.3% |
| runtime, 4,000 records | 1.0829s | 1.1160s | +3.1% |

All within run-to-run noise. The two extra context views cost two references per
event, as ADR-0026 decision 7 requires -- nothing is scanned or copied to build
them, and a strategy that never reads history pays nothing at all.

### ADR-0027 non-goal revisited, with the reason

ADR-0027 listed "persisting the `InstrumentRegistry`" as an explicit non-goal,
and was right for what the registry then held: pure configuration, every field
re-derivable from the operator's declaration file. v2.15 changes that premise --
the classification history is a record of a *sequence of acts* and cannot be
re-derived by re-running the declarations. An audit trail that does not survive
the process is not an audit trail, so the registry now has a snapshot. Nothing
else in ADR-0027 is touched: a run's own attribution is still frozen per fill,
and run `restore` still asks the caller for the registry object.

### A flaky test, diagnosed rather than silenced

`test_logging_distinct_metrics_stays_linear_in_the_names_already_logged` began
failing intermittently once the suite grew. It was **not** a regression: measured
directly, the path is flat at ~4us/item from 1,000 to 32,000 names (~2.05x per
doubling). The test took a *single* 4ms sample and compared a ratio against an
8x bar, so one garbage collection could tip it.

Fixed by measuring best-of-3 in all seven timing tests in that file -- the
standard technique, since noise only ever makes a sample slower. **No threshold
was changed and nothing was weakened**: a deliberately quadratic implementation
still measures 13.9x and fails the 8.0x bar decisively (verified). Six
consecutive full-suite runs are now green.

Separately, the new socket tests were slow for a silly reason:
`ThreadingHTTPServer.serve_forever` polls at 0.5s by default, which `shutdown()`
waits on, so every venue test paid half a second of pure latency. Dropping the
poll interval took the real-execution suite from 16.5s to 1.0s and the streaming
suite from 14s to 3s.

### Three defects found and fixed while building

* `StreamingSource._record_from` caught `TypeError`/`ValueError` but not
  `KeyError`, so a venue message *missing* a field would propagate out of the
  generator and kill a live session holding positions. Now counted as malformed
  like every other unusable message.
* The venue test server emitted fills with an empty `symbol`, which the adapter
  correctly refused. The server was wrong -- a venue always reports what it
  filled -- and now takes the symbol from the order.
* Recording provenance initially made `classify_instrument(reg, id, None)` on an
  *unclassified* instrument stop being a no-op, putting a "sector withdrawn"
  entry in the audit trail of an instrument that never had one. Caught by the
  existing v2.11 test; the no-op is preserved explicitly.

## 6. Two v2.14 release-hygiene gaps found and closed

Recorded because they show the release checklist has a hole worth watching:

1. **v2.14.0 never bumped its version.** `pyproject.toml`,
   `alphalab/common/version.py` and `tests/unit/test_package_metadata.py` all
   still said `2.13.0` at the v2.14.0 tag. All three now say `2.15.0`.
2. **v2.14.0 has no CHANGELOG entry.** The file jumped 2.13.0 -> 2.15.0. A
   concise v2.14.0 entry was reconstructed from the tag, ADR-0030 and the
   release diff, and is marked as reconstructed.

## 7. Release-acceptance review: one blocker found and fixed

The final acceptance review found that the v2.15 documentation truth-up was
**incomplete**. ARCHITECTURE.md and README's live-trading note had been
corrected, but six further places still asserted claims v2.15 falsifies — five
of them in *production docstrings*, where a user of those modules would read
them:

| Where | Claim that had become false |
| --- | --- |
| `runtime/session.py` | "AlphaLab contains no connectivity to any real venue" |
| `runtime/broker_routing.py` | "Not implemented anywhere in AlphaLab: a transport to any real venue" |
| `model_registry/registry.py` | "AlphaLab never computes it, because it never reads the bytes" |
| `model_registry/__init__.py` | "AlphaLab never reads, writes or hashes those bytes" |
| `lifecycle/__init__.py` | "there is no object store here" |
| `README.md` "Not yet addressed" | four present-tense entries: execution connectivity, streaming, artifact storage, `StrategyContext.history`/`.universe` |

All corrected — docstrings and prose only, no behaviour touched. ADR-0031 now
also records that it supersedes **ADR-0013 decision 6**, which is where the
"no object store" statement originates; historical ADRs are otherwise left
exactly as written.

This was a genuine blocker under the release criterion "no misleading
live-trading documentation": shipping a venue transport while the session
driver's own docstring says there is no connectivity is the contradiction that
criterion exists to catch.

## 8. Pre-existing defect found during certification, NOT fixed here

`benchmarks/benchmark_workbench.py` crashes with
`WorkbenchValidationError: Tab 'bt-BT-0' is not open.` It does so identically at
the **v2.14.0 tag** (verified by stashing), so it is not a v2.15 regression.
`alphalab.workbench` is a standalone presentation-layer package, off the
execution path and untouched by this release, so fixing it here would be the
unrelated refactor the release contract forbids. The other 49 benchmarks pass.

Left for whoever owns `workbench` next. It is either the benchmark closing a tab
it never opened, or `close_tab` refusing a tab it should hold.

## 9. Risks

* **Weakening a test to fit a checklist.** Two assertions in
  `test_strategy_context_visibility.py` pin `history` and `universe` as
  *deferred* (`type(...) is object`). Closing the deferral requires updating
  exactly those two, and nothing else in that file.
* **A second lifecycle by accident.** `alphalab.live`, `alphalab.feed`,
  `alphalab.brokers` and the deprecated `alphalab.production` all contain
  connection/subscription-shaped code that is off the canonical path. Building
  on any of them would create the competing subsystem the contract forbids.
* **Determinism contamination.** Live components read wall clocks and sockets.
  Backtest and replay must stay byte-identical; the id stream must not be drawn
  from by any new storage or transport code (ADR-0029 decision 7 — a complete
  artifact put/get must advance `draws` by zero, exactly as run-state does).
* **Blocking I/O in a generator.** A streaming source that blocks forever cannot
  be shut down; the lifecycle needs an explicit stop that unblocks the reader.
