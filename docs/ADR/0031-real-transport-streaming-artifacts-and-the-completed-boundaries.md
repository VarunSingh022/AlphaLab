# ADR-0031: Real Transport, Streaming, Artifact Bytes, and the Two Completed Boundaries

## Status

**Accepted and implemented in v2.15.0.** Five capabilities that had a contract
and no implementation now have both, and each of them landed on a boundary that
already existed rather than beside one.

* `alphalab.broker.transport` and `alphalab.broker.venue` reach a venue over
  authenticated HTTP. `RestVenueBroker` is a `BrokerProtocol`, so
  `runtime.broker_routing` routes to it without knowing which adapter it has.
* `alphalab.marketdata.websocket` is an RFC 6455 client, and
  `alphalab.market.stream.StreamingSource` is a `MarketDataSource` over it, so
  `TradingSession.run` drives a venue feed with the loop it drives a file with.
* `alphalab.model_registry.artifact_store` holds artifact bytes and produces the
  `ArtifactRef` the registry has recorded since v2.4.
* `alphalab.instrument.classification` gives a classification a source and an
  effective date, and `alphalab.instrument.snapshot` makes the resulting audit
  trail durable.
* `StrategyContext.history` and `.universe` are populated, closing the two
  fields ADR-0026 deferred.

Neither `RunEngine` nor `ExecutionPipeline` changed ownership of anything.
`_populate_context` gained two arguments and two overlaid fields; that is the
whole of the change to the execution step.

The evidence is in eight suites. `tests/integration/test_real_execution.py` (40)
and `tests/integration/test_streaming_market_data.py` (30) drive the real
transports into local servers **over real sockets**;
`tests/integration/test_streaming_session.py` (5) puts the stream on the
canonical run step; `tests/regression/test_artifact_store.py` (52) holds the
store's contract across both backends; `tests/regression/test_security_master.py`
(38) and `tests/regression/test_strategy_context_history_and_universe.py` (27)
hold the two completed boundaries; `tests/regression/test_streaming_does_not_contaminate_determinism.py`
(6) holds the line between live and deterministic; and
`tests/integration/test_v215_capabilities.py` (4) composes all five into one
lifecycle.

Depends on **ADR-0012** for the broker boundary the transport sits under,
**ADR-0011** and **ADR-0014** for the market-data boundary and the ordering rule
the stream declares against, **ADR-0029** for the storage shape the artifact
store copies, **ADR-0016** and **ADR-0027** for the identity and classification
this completes, **ADR-0026** for the two context fields it closes, and
**ADR-0030** for the driver model live work fits into.

Supersedes two earlier statements, and only those two:

* **ADR-0013 decision 6**, "Artifacts are referenced; bytes are not stored" —
  "AlphaLab never reads, writes or hashes those bytes: there is no object store
  here and this release does not pretend there is." It was true of v2.4 and is
  not true of v2.15; see decision 5. Everything else in ADR-0013 stands,
  including `ModelVersion.__serializable__` projecting a version to metadata
  plus a reference.
* **ADR-0027's** *Explicit non-goals* entry "Persisting the
  `InstrumentRegistry`" — see decision 7, which records why the premise changed
  rather than why the reasoning was wrong.

---

# Context

Five capabilities were described as existing and were not.

**Execution.** `ARCHITECTURE.md` said it outright: "AlphaLab does not support
live trading. It supports the adapter contract a live venue would be reached
through." `runtime.broker_routing` implemented and tested both directions of the
mapping, the pre-trade gates and idempotent submission; `broker.reconciliation`
answered every redelivery and break case. Everything was there except a way to
reach a venue. `PaperBroker` was the only adapter and is a simulation.

**Streaming.** `ProviderHistorySource` stated its own limits: "a *history*
source ... It does not poll, subscribe, reconnect or stream."
`binanceClient.subscribe` was a documented no-op —"this client polls REST
endpoints, it does not stream". `marketdata.transport` was GET-only. There was
no socket anywhere on the canonical path.

**Artifacts.** `ArtifactRef` recorded a `checksum` that nothing computed:
"AlphaLab stores no artifact bytes and implements no object store ... AlphaLab
never computes it, because it never reads the bytes." Its docstring claimed the
reference "lets a later reader detect that the file behind a version changed",
which nothing could do.

**Security master.** v2.11 delivered real identity and real classification, and
`classify_instrument` recorded a bare label — its own docstring says
reclassification happens "silently, with no refusal and no event". So the
registry could say *what* an instrument was classified as and never *who said
so, from what source, or as of when*, and a correction erased what it corrected.

**Strategy context.** ADR-0026 populated four fields and deferred two with
stated reasons. `HistoryAccessorProtocol` and `UniverseProtocol` declared **no
methods at all**, and every construction site in the repository passed
`object()`.

The common shape: a contract with nothing behind it. That is the thing this ADR
exists to remove, and the reason every decision below is about *where* the
implementation goes rather than whether to write one.

---

# Decision drivers

1. **A capability lands on an existing boundary or it is not landed.** Every one
   of these could have been built as a new subsystem. Each would then have been
   a second execution path, a second market-data lifecycle, a second persistence
   owner, a second identity authority, or a second strategy boundary.
2. **No canned implementation counts.** A method returning `accepted` is not a
   transport. A class named `Stream` wrapping a polling loop is not a stream.
3. **No network egress exists here**, and no vendor credentials do either. What
   can still be proven is the protocol, against the other end of it.
4. **The deterministic engines must not move.** Backtest and replay are the
   product's foundation, and live code reads clocks and sockets.
5. **Secrets must have no path to disk, a log, or a traceback.**

---

# Decision

## 1. A venue transport is a separate seam from a market-data transport

`marketdata.transport.Transport` is `get(url, params)`: unauthenticated,
GET-only, returning bytes with no status. Order submission needs a body, a
method that is not GET, a signature, and the status code.

The status code is the load-bearing part. **A venue refusing an order and a
venue being unreachable are different facts and must never be conflated.** A
`422` is a decision about the order — it becomes an `OrderRejected` and a
`REJECTED` order, so the OMS retires it and releases its reservation. A socket
error is the *absence* of an answer, and only that raises.

Widening the market-data transport to carry all of it would have put order
authentication into the vocabulary of every price client, so
`alphalab.broker.transport` is its own module with its own protocol.

## 2. There is no canned venue transport in the package

`marketdata.transport` ships `StaticTransport` and `persistence.run_store` ships
`MemoryRunStateStore`, both deterministic doubles the caller constructs **by
name**. This module ships no such thing.

The reason is the asymmetry of the failure. A canned price is wrong data; a
canned `accepted` is an order the operator believes is at a venue and is not.
`marketdata.transport`'s own docstring names the failure it was built to avoid —
"silently fake data with no seam to ever make it real" — and for an order router
that failure is worse than useless. The tests run the real transport against a
real local HTTP server that verifies the signature, the timestamp window and the
idempotency key.

## 3. Orders are addressed by the client order id, and retries are narrow

Every request addresses an order by `broker_order_id`, which
`broker_routing.broker_order_id_for` derives from the OMS order id. A retry
after a lost response therefore reaches the order the first attempt may have
created, and **the venue deduplicates, not AlphaLab's memory of what it sent**.

`_send` retries only an unanswered request and a `5xx`/`429`. Every other status
is the venue deciding about this exact request, and repeating it would be
refused identically. When the attempt budget is spent the transport error
propagates: an order whose fate is unknown is surfaced, never assumed either
way.

`execution_id` is **the venue's own fill identifier, verbatim**, because it is
the deduplication key in `classify_execution` and has to survive redelivery
after a reconnect. `PaperBroker` mints one because it *is* the venue; an adapter
to someone else's venue must not.

## 4. A streaming source is a `MarketDataSource`, and nothing more

`MarketDataSource.records()` already returns an *iterator*. A generator pulling
a live socket satisfies it with no new abstraction, so the execution path cannot
tell a venue feed from a stored file and `TradingSession.run` needed no change.

`alphalab.live` and `alphalab.feed` contain connection- and
subscription-shaped code and are standalone, off-path engine libraries.
Building here instead of there is the difference between one market-data
lifecycle and two.

**The stream declares `UNORDERED`** and cannot declare anything else: a venue can
reorder, and a socket that reconnects mid-second will deliver a record older
than one already seen. ADR-0014's existing answer applies unchanged — a run over
this source sets `RunConfig.ordering` to `UNORDERED`, a regressing record is
skipped and recorded, and a run demanding `CHRONOLOGICAL` is refused before the
first record.

Sequence gaps are a *different question* from timestamp order and both are
answered: a message at or below the last sequence seen for its symbol is dropped
as a duplicate, and one that skips ahead is admitted with the gap counted. The
missing messages are not invented.

**Backpressure needs no buffer.** The source is a generator: it reads one
message when the consumer asks for one, so a slow strategy applies backpressure
through TCP's own receive window rather than through a queue in this process
that could grow without limit.

## 5. Artifact identity is the content, and the reference leaks no path

`RunStateRef` is addressed by a caller-supplied `(run_id, sequence)` (ADR-0029
decision 3). An artifact is addressed by the SHA-256 of its bytes, and the
difference follows from what each reference answers: one says *which checkpoint
of which run*, the other says *these exact bytes*.

Content addressing makes four things true at once: storing identical bytes twice
is one artifact; a reference cannot name bytes that hash to something else, so
verification is a tautology the store checks rather than a claim it trusts; two
environments that never shared a database agree on identity, exactly as
`derive_asset_id` makes them agree on an instrument's; and an artifact cannot be
silently replaced, because replacing the bytes changes the name.

The URI is `alphalab-artifact:sha256:<hex>` and deliberately not `file://`:
`ArtifactRef.uri` is documented as opaque, and a filesystem path would bind a
reference to the machine that wrote it.

## 6. The artifact store lives in `model_registry`, not `persistence`

Every module in `alphalab.persistence` imports nothing but `alphalab.common` and
its own siblings. `ArtifactRef` lives in `model_registry`, so a store whose
whole job is to produce and resolve that reference belongs beside it; putting it
under `persistence` would have made that package depend on a domain package —
the dependency direction inverted.

The healthy direction is the only import: `PersistenceValidationError` and
`StorageError` come from the codec spine, which is ADR-0029's own reasoning
applied again — the store needed **no new exception type**.

It is **not a second persistence owner**. `RunStateStore` owns run state,
addressed by run and checkpoint, holding a `str`; this owns artifact bytes,
addressed by content, holding `bytes`. Neither can answer the other's question.
Both follow the shape `marketdata.transport` established: a narrow protocol, one
real backend, one deterministic double constructed by name, a digest verified
before anything is returned, and refusal rather than repair.

## 7. A classification records its provenance, and corrections are appended

`InstrumentRecord.sector` stays exactly where and what it was — a plain
`str | None`, outside the identity key, read by the pipeline in O(1). Provenance
is recorded *beside* it: `SectorClassification` carries the label, its `source`
and its `as_of`, and `InstrumentRegistry.classifications` holds an append-only
`ClassificationHistory` per instrument.

Append-only, because the alternative is the thing the architecture forbids.
Overwriting a classification in place is mutable historical attribution: after a
correction nobody can say what the registry previously held or on whose
authority. An append-only log makes a correction a *new fact* rather than the
erasure of an old one.

**`as_of` is caller-supplied and no clock is read.** A registry that stamped
itself with `time.time()` would not be reproducible, and two processes building
one from the same declaration file would disagree. `as_of=None` means *undated*,
not epoch — there is no `0.0` sentinel, because that would be a date nobody
chose.

A sector set by constructing an `InstrumentRecord` directly has **no** recorded
provenance, and `classification_of` answers `None` for it. Inventing `OPERATOR`
there would claim provenance the registry does not have.

### The ADR-0027 non-goal this supersedes, and why

ADR-0027 refused to persist the registry, and was right for what it then held:
pure configuration, every field re-derivable from the operator's declaration
file, so a snapshot would have been a second copy of something they already had.

**v2.15 changes that premise.** The classification history is the record of a
*sequence of acts*; re-running the declarations produces a history with one
entry where the real one has four. An audit trail that does not survive the
process is not an audit trail, so `alphalab.instrument.snapshot` exists.

Nothing else in ADR-0027 moves. A run's attribution is still frozen onto
`TradeRecord.sector_id` at fill time and is never resolved back through a
registry; `restore` of a *run* still asks the caller to supply the registry
object and still does not check that it classifies as the captured run did.

`sector_as_of` answers what the *reference data* said at an instant — a
data-quality or restatement question — and is deliberately not what portfolio
attribution reads.

## 8. The universe is the instrument registry

ADR-0026 deferred this field because it "requires deciding whether membership is
configuration, instrument-registry state, or a risk control. A semantic
decision, not a wiring one."

It is the instrument registry. ADR-0016 already makes `InstrumentRegistry` the
authority on what an instrument *is*; it is already on
`ExecutionPipelineConfig.instruments`, already read-only, and already what
decides whether an asset id names a real instrument. A separate universe list
would be a second place to declare membership; a risk control would make "what
exists" depend on "what is currently permitted".

A run without a registry has an **empty** universe, and `configured` says why —
the registry is absent, not empty. Substituting the assets the run happens to
have priced would answer a different question: "what have I seen a price for" is
`MarketView.assets`, and giving it this name would make two concepts one word.

## 9. History is bounded at the event, and the bound is enforced twice

ADR-0026 deferred history because it "requires a clock-bounded accessor whose
bound is enforced at construction, plus a look-ahead regression suite."

`HistoryView.as_of` is the **event's** timestamp, never a wall clock, which is
what makes a backtest and a live session bound history identically.
Look-ahead safety rests on a structural fact first and a check second:
`MarketState.history` holds only events already published, and the context is
assembled after publishing the current event — so a future event does not exist
in the log to leak. Every accessor nevertheless refuses an event later than
`as_of`, because a filter that can be tested is better than an invariant that
can only be argued.

**Constructing the view is O(1)**, as ADR-0026 decision 7 requires, and a
strategy that never asks for history pays nothing. Reading it costs what it
returns: each accessor walks the log *backwards* and stops at `limit`, so
`bars(asset, 20)` is O(20) rather than O(history). Asking without a limit on
every event makes a run quadratic in its own length — a real cost, documented
rather than hidden.

## 10. The caller keeps `clock`; the pipeline owns the look-ahead bound

ADR-0026 recorded caller-owned `clock` as a decision rather than an oversight,
and `test_strategy_context_visibility.py` pins `context.clock is _CLOCK`.
Overlaying it would have weakened a passing test to satisfy a checklist.

The time semantic that actually needed settling is look-ahead safety, and that
is expressed where it binds — on the history view, enforced at construction and
readable as `as_of`.

## 11. Allocation visibility is still refused

ADR-0026 refuses it permanently and with a reason: showing a strategy the
capital its own intent will later reserve invites it to pre-size, duplicating
`AllocationEngine`'s authority (ADR-0015). v2.15 does not add it. A strategy sees
its *share* of live orders through `orders`, which is attribution, and no
allocation decision is exposed.

## 12. Credentials are held by one type that will not render them

`VenueCredentials.api_secret` is excluded from `repr`, from `str` and from
equality, and is read by `sign` and by nothing else in AlphaLab. Errors name the
key truncated to four characters and never the secret, and never the request
body, which carries order details. No credential appears in source, tests,
fixtures, snapshots or configuration anywhere in this repository.

## 13. Live code draws no identifier from the run's stream

`alphalab.common.ids` routes every identifier through one ambient `ContextVar`,
so anything drawing from it inside a run's `id_scope` consumes the *run's* own
identifiers and shifts the identity of every order and fill after it. That is
the defect ADR-0029 decision 7 found in the legacy persistence store.

Constructing a `StreamingSource`, an `HttpVenueTransport` or a `RestVenueBroker`
advances `draws` by exactly zero, as does a complete artifact `put`/`get` cycle,
and a seeded backtest is byte-identical with all of it imported. Broker *events*
draw via `new_id` exactly as `PaperBroker` does — two adapters at one contract
behaving differently in their event stream is the divergence the boundary exists
to prevent.

---

# Ownership

| Concept | Owner | Not |
| --- | --- | --- |
| Venue wire protocol | `broker.transport` | `marketdata.transport`, which stays GET-only |
| Venue adapter | `broker.venue.RestVenueBroker` | a second execution engine; it is a `BrokerProtocol` |
| Order out / fill back | `runtime.broker_routing`, unchanged | the adapter |
| WebSocket framing | `marketdata.websocket` | `alphalab.live`, which is off-path |
| Streaming lifecycle | `market.stream.StreamingSource` | a second `MarketDataSource` boundary |
| Artifact bytes | `model_registry.artifact_store` | `persistence`, which owns run state |
| Artifact identity | the content's SHA-256 | a caller-supplied id |
| Classification provenance | `instrument.classification` | `TradeRecord.sector_id`, which is the past tense |
| Universe membership | `InstrumentRegistry` | configuration or a risk control |
| Look-ahead bound | `HistoryView.as_of`, from the event | `StrategyContext.clock`, still the caller's |
| The run | `RunEngine`, unchanged | any of the above |

---

# Boundary behavior

| Situation | Answer |
| --- | --- |
| Venue answers `422` on submit | `OrderRejected`, order `REJECTED`. A fact, not a failure |
| Venue does not answer at all | `VenueTransportError` after the attempt budget. Outcome unknown, and said so |
| Same client order id submitted twice | One order at the venue; the second returns the existing one |
| Venue answers `5xx` / `429` | Retried up to `max_attempts` |
| Venue answers an unknown order status | `VenueProtocolError`. An unnameable state is not treated as working |
| Cancel the venue refuses | Order left exactly as it was; no local cancellation invented |
| Fill redelivered after reconnect | `DUPLICATE`, applied zero times (`broker.reconciliation`, unchanged) |
| Stream message with a seen sequence | Dropped, counted in `StreamStats.duplicates` |
| Stream message skipping a sequence | Admitted, gap counted; missing messages not invented |
| Malformed or unparseable stream message | Counted, session continues. One bad frame must not kill a run holding positions |
| Stream message for an unregistered symbol | Counted as malformed. A configuration fact, visible as a count |
| Venue silent past the liveness limit | Treated as a drop; reconnect and resubscribe |
| Reconnect budget exhausted | Iteration ends. Not an exception — an expected end of stream |
| Artifact bytes altered on disk | `StorageError`. Refused, never partly returned |
| Artifact reference from another store | `PersistenceValidationError`, naming the scheme this store resolves |
| Storing identical bytes twice | One artifact, equal references, no error |
| Reclassifying an instrument | New history entry; the previous one stays readable |
| Withdrawing a classification never given | No-op. Nothing to withdraw and nothing to supersede |
| Re-declaring a label from a new source | Recorded. Corroboration is a fact an audit needs |
| Strategy asks history for an unseen asset | `()`. Absent, not an error |
| Run with no instrument registry | `universe.configured is False`, not an empty universe presented as real |

---

# Testing invariants

1. Every transport test runs the **real** client against a local server over a
   real socket. No test replaces the thing under test.
2. The venue server verifies the HMAC signature, the timestamp window and the
   idempotency key, and the WebSocket server computes the accept token and
   speaks real frames.
3. The full streaming lifecycle — connect, subscribe, receive, process,
   disconnect, reconnect, resubscribe, continue, shutdown — runs end to end in
   one test, and each stage is pinned individually beside it.
4. A reconnect is asserted to have **resubscribed**, not merely reopened a
   socket. A reopened socket that was not resubscribed is silent forever, which
   is indistinguishable from a quiet market.
5. The artifact store's whole contract runs against **both** backends through
   the protocol. A double that does not behave like the real store is worse than
   no double.
6. Look-ahead safety is asserted over a run whose prices strictly increase, so a
   context that could see ahead would show a price above the one it is marked at.
7. Backtest/replay parity covers the new context surfaces.
8. A seeded backtest is byte-identical with every live module imported, and each
   live component is asserted to draw zero identifiers.
9. A reclassification is asserted not to move a finished run's `TradeRecord`.
10. No credential appears in any fixture, and the redaction is asserted on the
    error path.

---

# Explicit non-goals

- **A vendor adapter.** The transports are generic and protocol-faithful; a
  named venue's request shapes are that adapter's business.
- **Verification against a commercial endpoint.** Stated in both transports'
  docstrings rather than implied. No network egress exists here.
- **`permessage-deflate`**, or the server side of RFC 6455.
- **Allocation visibility in the strategy context** (decision 11).
- **Overlaying `StrategyContext.clock`** (decision 10).
- **Methods on `MarketViewProtocol`, `PortfolioSnapshotProtocol`,
  `RiskViewProtocol` or `OrderFacadeProtocol`.** They remain method-less, as
  v2.10 left them. Typing them is a change to every construction site and is not
  what this release is about; the two protocols that were *blocking a capability*
  are the two this ADR types.
- **Any classification dimension other than sector** — ADR-0027's non-goal,
  unchanged.
- **A second FX or currency behaviour.** Untouched.
- **Removing the deprecated `alphalab.production` and legacy `persistence`
  modules.** They are v3.0's.
- **An integrated live runtime process.** A live *driver* over `RunEngine` is
  what ADR-0030 anticipates; assembling one into a supervised process is not
  this release.

---

# Consequences

**Gained.** AlphaLab can reach a venue, consume a push feed, hold the bytes it
references, say who classified an instrument, and show a strategy the history
and universe it trades in. The five compose on one path, proven in one test.

**Cost.** Two more views are built per market event — two references, measured
at nothing against v2.14 across the execution, backtesting and runtime
benchmarks. A strategy that calls `history.quotes(asset)` with no limit on every
event makes its own run quadratic; the limit parameter exists for that and the
cost is documented on the method.

**Risk carried forward.** Both transports are unverified against a commercial
endpoint and say so in their own docstrings. That is a statement of what has and
has not been exercised, not a hedge: the protocol is proven, the vendor is not.

---

# Release impact

v2.15.0. Additive throughout. `StrategyContext.history` and `.universe` gained
defaults, so a caller constructing one by hand omits them rather than passing a
placeholder; every construction site in the repository was updated accordingly
and the two assertions in `test_strategy_context_visibility.py` that pinned the
*deferral* now pin what is supplied instead. `classify_instrument` and
`classify_instruments` gained optional `source` and `as_of` parameters and are
call-compatible. No existing behaviour was removed, relaxed or renamed.
