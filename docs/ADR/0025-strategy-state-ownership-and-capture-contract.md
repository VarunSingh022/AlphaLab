# ADR-0025: Strategy State Ownership and the Capture Contract

## Status

**Accepted and implemented in v2.10.0.** `alphalab.strategy.protocol` ships
`StrategyStateProtocol`; `alphalab.runtime.snapshot` ships the declaration
check, the capture-time encoder validation, `StrategyStateRecord`, the
three-valued `StrategyRecord.state`, `NOT_ASKED`, `READABLE_PIPELINE_SCHEMAS`
and `PIPELINE_SNAPSHOT_SCHEMA = 2`. `StrategyProtocol`, `StrategyState`,
`DeterministicEncoder` and every other schema constant are unchanged, as
decisions 8 and 12 require.

Unusually for this repository, the decision was written *before* the
implementation, because its central question — what a strategy is permitted to
hand across the serialization boundary — is expensive to change once payloads
exist. The implementation that followed changed none of it. One thing the
implementation added that the decision only implied: decision 3's encoder check
happens *at capture*, so an unencodable state can never reach a snapshot that
looks valid in memory, and the same step normalizes the payload to the
JSON-decoded primitives decision 3 promises `restore_state` receives.

Closes the one precondition ADR-0023 decision 8 left open. Applies ADR-0014's
rule — "live objects are referenced, not reconstructed" — to the state a
strategy keeps in its own attributes, which is the one thing on the execution
path that rule has never covered. Depends on ADR-0022 for the identifier
position a captured state carries, and on ADR-0023 for the envelope the state is
carried in and for the step boundary at which it is taken.

`StrategyContext` population is **not** decided here. It is deferred to
ADR-0026, and the two are separable: this decision is about what a strategy
tells the runtime, and that one is about what the runtime tells a strategy.

---

# Context

v2.9.0 ships a durable-continuation guarantee with four preconditions. Three —
a seeded run, the same records in the same order, the same supplied runtime
objects — are the caller's and can be met. The fourth is stated in ADR-0023
decision 8 as "strategy-internal state restored by the caller", and it cannot
be met by anyone, because there is no protocol through which a caller could
express it.

The gap is narrow and exactly located. `StrategyRecord` carries `strategy_id`,
`status`, `config`, `subscriptions`, `last_error` and `instance_type`.
`_restore_strategies` rebuilds a `StrategyState` from those six values and a
caller-supplied instance. A strategy holding a rolling window, a counter, a
fitted model or its own belief about a position holds state that is captured by
nothing, restored by nothing, and — this is what makes it dangerous — **detected
by nothing**. Restore succeeds, every Class-1 value compares equal, and the run
diverges from the uninterrupted control on the next event with nothing raising
at any layer. That is the same shape as the v2.8 identifier defect v2.9 was
built to close, one layer up.

## What the repository already does with values it cannot carry

Three mechanisms exist, and this decision reuses rather than extends them.

- **A live object is recorded by type and supplied back.** `ModelVersion.model`
  (ADR-0014), then `sizing_model`, `simulator`, `instruments`, `fill_policy` and
  each `StrategyProtocol` instance (ADR-0023 decision 3). `_require_object`
  raises when one is missing and raises on a type mismatch; it never substitutes
  and never imports from a recorded name.
- **A value whose in-memory shape has no JSON form declares a projection.**
  `SupportsSerializable.__serializable__`, in `alphalab/common/serialization.py`.
  `OrderBook` keys orders by an `OrderId` dataclass that JSON cannot use as a
  key, so it declares an ordered array instead of weakening its typed identifier.
  `PersistentMap` and `PersistentSet` do the same.
- **Each domain package decodes its own types, field by field.**
  `alphalab.persistence.decode` holds primitives only, and says why: it is
  "deliberately *not* a generic object mapper: there is no reflection over
  dataclass fields and no 'decode whatever this looks like'". The failure it
  names is v2.1's, when append-only logs were persisted as the string
  `"AppendOnlyLog([...])"` by a layer that accepted anything rather than refusing
  what it did not understand.

## The encoder is generic; the decoder is deliberately not

This asymmetry is the whole of decision 3, so it is worth stating exactly, as
measured on the v2.9.0 tree.

`DeterministicEncoder` has an explicit branch for `Decimal`, any dataclass
instance, `AppendOnlyLog`, `Enum` and `UUID`, plus JSON's own natives, and
raises `SerializationError` on everything else. `dataclass_to_dict` recurses
generically. So the *write* direction already accepts a plausible strategy-state
dataclass with no new machinery.

The *read* direction does not exist, and the round trip is therefore not an
identity. Measured at `5f921a0`:

```text
@dataclass(frozen=True)
class Window:
    lookback: int
    threshold: Decimal
    samples: tuple[Decimal, ...]

serialize(Window(3, Decimal("1.25"), (Decimal("100.10"), Decimal("101.20"))))
  -> {"lookback":3,"samples":["100.10","101.20"],"threshold":"1.25"}

deserialize(...)
  -> {'lookback': 3, 'samples': ['100.10', '101.20'], 'threshold': '1.25'}
```

`Decimal` returns as `str`; `tuple` returns as `list`. Neither is recoverable
from the payload, because JSON records no type and the encoder writes a
`Decimal` and a `str` identically. `set`, `frozenset`, `bytes` and any dict key
that is not a JSON scalar are refused outright. This is not a defect in the
encoder — writing a `Decimal` as a string is what makes the payload
deterministic — it is the reason a decoder has to be told what it is reading.

## The same lossiness already exists on the adjacent field, and stays there

This subsection is archaeology, not scope. It is recorded because it measures
what the `Any` contract costs on a field that already has it, which is the
evidence decision 3 turns on. It creates no obligation in this ADR — see
decision 4, which declines to act on it.

`StrategyRecord.config` is typed `Any` and decoded by
`config=require(payload, "config")` — the raw JSON value, with no decoding at
all. ADR-0023 anticipated one half of this ("a configuration the encoder cannot
write is refused at capture, by the encoder") and not the other: a configuration
the encoder *can* write may not read back as what it was.

Measured at `5f921a0`, on a pipeline state whose strategy config is
`{"threshold": Decimal("1.25"), "lookback": 3}`:

```text
restore(capture(state), objects) == state                       -> True
restore(from_primitives(deserialize(serialize(...))), ...) == s -> False

  original config: {'threshold': Decimal('1.25'), 'lookback': 3}
  restored config: {'threshold': '1.25',          'lookback': 3}
```

ADR-0023 testing invariant 1 asserts `restore(capture(s), objects) == s` and
holds; the across-JSON round trip the same ADR documents does not, for any
config carrying a `Decimal`. No test covers it, because every strategy config in
the suite is `{}` or a mapping of strings.

This is not a hypothetical about a protocol that does not exist yet. It is the
`Any` contract, already shipped, already lossy, on the record the strategy state
would be added to — which is why decision 3 does not repeat it for state.

It is also **not this ADR's to fix**. `config` semantics are pre-existing v2.9
behaviour with their own callers and their own compatibility surface; decision 4
records that they are left exactly as they are.

---

# Decision drivers

- **D1.** Reuse the accepted contracts. ADR-0014 settled how a live object
  crosses the boundary and ADR-0023 settled where the boundary is; this applies
  them rather than inventing a third rule.
- **D2.** A round trip that is not an identity is worse than a refusal. The
  failure being closed is silent divergence, so no part of this contract may
  produce a value that merely resembles what was captured.
- **D3.** Never substitute, and never construct from a recorded name.
- **D4.** Do not build the reflective decoder `alphalab.persistence.decode`
  exists to refuse.
- **D5.** A strategy author's schema change must not move an AlphaLab constant.
- **D6.** Confine schema churn to the envelope that actually changes shape
  (ADR-0023 decision 1).
- **D7.** Absence of a fact and a recorded negative fact are different, and a
  decoder must not conflate them.

---

# Decision

## 1. The strategy owns its state; the runtime asks for it

AlphaLab never introspects a strategy instance, never reflects over its
attributes, and never serializes an object it did not receive as data. A
strategy that has durable state describes it; a strategy that does not is
unaffected and unchanged.

This is ADR-0014's rule with the direction reversed. For `ModelVersion.model`
the framework records what a value *was* and requires the object back. Here the
framework cannot record what the value was, because it is not one value — so it
requires the *strategy* to say, and keeps the instance-by-type rule for the
instance itself (decision 10).

## 2. Capture is opt-in, declared structurally, and three-valued on the record

A strategy declares durable state by satisfying `StrategyStateProtocol`.
Detection is structural, against a `runtime_checkable` Protocol, exactly as
`SupportsSerializable` is detected in `alphalab/common/serialization.py`. No
registration, no configuration flag, no base class.

`StrategyRecord` gains one field, and it distinguishes **three** states, not two:

| On the record | Means |
| --- | --- |
| field absent | **Not asked.** Only legal in a schema-1 payload, written by a build that had no protocol. |
| field present, `null` | **Declared none.** The strategy does not satisfy the protocol, and this build asked and recorded that. |
| field present, an object | **Declared, and this is it.** An empty mapping is a legitimate value here and is *not* the same as `null`. |

The absent case is distinguished from the `null` case by `require`, which raises
on a missing key rather than returning a default — the rule
`alphalab.persistence.decode` already states as "a missing field is never filled
in with a default". At schema 2 the field is always written, so its absence is
never ambiguous.

## 3. Encodability: the strategy supplies both directions

**A strategy that declares state supplies an encode *and* a decode. Neither is
optional, and declaring one without the other is refused at capture.**

- **Encode** returns a value the *existing* `DeterministicEncoder` accepts. No
  new encoder branch is added, and a strategy needing one for a type of its own
  uses the mechanism that already exists for that — `__serializable__` — rather
  than a mechanism invented here. A value the encoder refuses raises
  `SerializationError` at capture, from the encoder, as it does for every other
  state in the repository.
- **Decode** receives the JSON-decoded primitives and the version that was
  written with them, and reconstructs the strategy's state.

This is the shape every domain package in the repository already has —
`capture` projects, a `_decode` function reads back, and the pair lives with the
type it serves — applied one level down and owned by the strategy author.

The rejected alternative is "restrict strategy state to values the strict
encoder supports", and it is rejected because **it is only half a contract**.
The measurements in Context show the write direction already works and the read
direction cannot be inferred: a payload carrying `"1.25"` does not say whether
it was a `Decimal` or a `str`, and `Decimal` is this repository's money type. A
strategy holding a price threshold would silently get a string back, compare it
against a `Decimal`, and either raise deep inside a hook or — worse — not raise.
Restricting the value space further does not help: the ambiguity is in JSON, not
in the space.

`Any` is not on the table at all. It is what `StrategyRecord.config` already
does, and Context measures what it costs.

## 4. `config` semantics are unchanged, and this ADR does not redefine them

**`StrategyRecord.config` keeps exactly its v2.9.0 behaviour.** It stays typed
`Any`, stays decoded by `config=require(payload, "config")`, and is untouched by
the encode/decode contract decision 3 establishes for strategy *state*. Nothing
in this ADR adds a v2.10 obligation, compatibility rule, migration or test
covering config round-trip fidelity.

The lossiness measured in Context is **pre-existing and separate**:

- the v2.9 generic encoder can write config values such as `Decimal` and
  `tuple`, and does so without complaint;
- JSON deserialization is type-lossy, so those values read back as `str` and
  `list`;
- both facts predate this ADR and are properties of the shared encoder and of
  JSON, not of anything decided here.

Why it is held apart rather than folded in. `config` is set at
`RuntimeSupervisor.configure` and read by `_capture_strategies`,
`_restore_strategies`, `_strategy_record` and the certification's `_class_one`
projection. It is carried for **every** registered strategy, so changing how it
serializes would change a persisted value for strategies that never declare
state, and would place a compatibility obligation on payloads written by v2.9.
Strategy state has no such surface: it is new, opt-in, and reaches only
strategies that ask for it. Deciding both in one ADR would bind a narrow new
contract to a broad existing one and make the release's compatibility story turn
on a field the release does not otherwise need to touch.

Note that `StrategyState.config` is not the value a strategy reads.
`ContextFactory` is `Callable[[str], StrategyContext]` and
`StrategyEngine.process_event` builds each context from the `strategy_id` alone,
so `StrategyContext.config` is whatever the caller's factory supplies and the two
are unrelated at v2.9.0. Whether they should be is a `StrategyContext` question,
and belongs to ADR-0026 rather than here.

**A separate decision is required before config serialization behaviour
changes.** That decision — not this one — would have to settle whether config is
typed, whether declaring and non-declaring strategies are treated alike, what
happens to a v2.9 payload whose config decoded as `str`, and whether ADR-0023's
testing invariant 1 is restated. Until it is taken, the behaviour above is the
documented behaviour, and implementers of this ADR must not alter it in passing.

## 5. Capture happens only at a completed pipeline step boundary

Between `ExecutionPipeline.process_record` calls, never inside one. This is
ADR-0023 decision 7, inherited rather than restated: it is the only point at
which ADR-0022's identifier position is accurate, and therefore the only point
at which a captured state is internally consistent.

A strategy is never asked for its state mid-hook, mid-event, or while any intent
it emitted is still in flight through allocation, risk, the OMS or execution.

## 6. Capture is a pure read

A strategy's encode must not mint an identifier, mutate the strategy, emit an
event, advance the ambient identifier source, or reach any runtime service. The
same rule `alphalab.runtime.snapshot.capture` already keeps: a state captured
while a different source happens to be installed still records its own position.

The guarantee is asserted, not requested: `current_id_position()` is unchanged
across a capture, and capturing twice returns equal payloads. A strategy whose
encode has side effects is a strategy whose snapshot is a function of when it
was taken, which makes the round trip untrustworthy in exactly the way ADR-0023
decision 4 describes.

**Restore is not symmetric, and this is deliberate.** Restoring state into a
supplied instance necessarily mutates that instance; that is what restoring
means. The bound is that it may mutate *only that instance's own state*, and
must not mint, emit or touch the runtime. The alternative — a constructor that
returns a fresh instance — is rejected under decision 10: the caller already
supplied the object, and the framework building a second one blurs who owns it.

## 7. A strategy's state version is the strategy author's, not AlphaLab's

The payload carries an integer version alongside it, written by the strategy and
handed back to the strategy on restore. AlphaLab carries it and compares nothing.

A strategy that changes its state shape bumps its own version and decides in its
own decode whether to read the older one, migrate it, or refuse it. That refusal
surfaces as a decode failure under decision 11 and is indistinguishable, at the
framework boundary, from any other.

The framework must not interpret this integer, because it cannot: it does not
know what the strategy's schema 2 means. A single shared constant would force
every strategy author's change through an AlphaLab release, which is D5.

## 8. `PIPELINE_SNAPSHOT_SCHEMA` moves 1 → 2

One constant moves, once, for the new field.

`SESSION_SNAPSHOT_SCHEMA` and `BACKTEST_SNAPSHOT_SCHEMA` **do not move**. They
nest the pipeline payload and its decoder validates its own version, so a
session or backtest envelope is unchanged by a change inside the core it
carries. `ALLOCATION_`, `OMS_`, `PORTFOLIO_` and `LIFECYCLE_SNAPSHOT_SCHEMA` do
not move either.

This is the first test of ADR-0023 decision 1, which separated the envelopes
specifically so that a change in one layer would not version the others. If the
two run constants have to move for this, that decision was wrong and should be
revisited rather than worked around.

## 9. A schema-1 pipeline payload is accepted, and every strategy in it is "not asked"

A payload written by v2.9 restores. Its strategies are restored with the field
absent, which decision 2 defines as *not asked* — a complete and true statement
about a payload written by a build with no protocol.

**The OMS precedent applies; the portfolio precedent does not.** The portfolio
refused schema 1 for a substantive reason: a v1 payload does not record
`Position.opened_at` and no honest value could be invented for it. A schema-1
pipeline payload is missing **nothing**: it records every field its writer knew
about, and "this run captured no strategy state" is not an invented value but an
accurate reading of it. This is the same reasoning ADR-0023 decision 5 used to
read a pre-v2.9 OMS payload rather than discard one that can be read perfectly.

The compatibility surface is bounded to exactly this: version 1 is read, version
2 is read, a missing `schema_version` is refused with no legacy shape rule, and
`schema_version >= 3` is refused. There is no migration framework, consistent
with `require_schema_version`'s house rule.

## 10. Restore uses the supplied instance, and never builds one

`StrategyRecord.instance_type` stays what it is: evidence, never an instruction
to import. Restore takes the instance from `RuntimeObjects.strategies`, checks it
under `_require_object`'s two rules — supplied, and of the recorded type — and
restores state *into* it.

Nothing in this decision introduces an import, a `getattr`, an `eval` or a
registry lookup keyed by a recorded name. The existing guard,
`test_no_module_constructs_anything_from_a_recorded_type_name`, extends to
whatever module implements this.

## 11. The four ways a restore is refused

Each raises before any state is returned. Nothing partial is returned by a
refused restore.

| Condition | Behaviour |
| --- | --- |
| **Declaring instance, schema-1 payload** | **Raise**, naming the strategy. The payload cannot say whether the strategy had state, and starting it empty invents the one fact that matters. This is the bounded exception to decision 9. |
| **Declaring instance, payload records `null`** | **Raise**, naming the strategy. The payload asserts the strategy declared nothing; the supplied instance says otherwise. One of them is wrong and the framework cannot tell which. |
| **Non-declaring instance, payload carries state** | **Raise**, naming the strategy. The instance cannot consume it, and discarding it silently loses exactly what the release exists to preserve. |
| **Strategy's own decode raises** | **Raise**, naming the strategy, with the original error chained. Refuses the **whole** restore, not that strategy. |

The last row is the one worth being explicit about: a corrupt payload for one
strategy in a ten-strategy run refuses the run. Restoring nine strategies and
skipping the tenth would produce a state that is internally consistent, compares
equal on every Class-1 value, and is wrong — which is the failure mode this
whole release exists to remove.

## 12. Error taxonomy

Every failure this decision introduces raises
`alphalab.persistence.exceptions.StateDecodeError`, naming the `strategy_id` and,
where the failure is inside a payload, the field — the message discipline
`persistence.decode` already keeps.

An exception raised by a strategy's own encode or decode is caught at the
framework boundary and re-raised as `StateDecodeError` with the original chained.
It is **not** routed through `alphalab.strategy.supervisor.RuntimeSupervisor.fail`
and does not transition the strategy to `FAILED`: `Dispatcher`'s isolation exists
so that one strategy's bad hook does not stop a run, and the opposite is required
here. A strategy that
cannot describe its state must not produce a snapshot claiming it did.

**`alphalab.oms.snapshot.SnapshotDecodeError` is untouched.** It subclasses
`OMSError` rather than `PersistenceError`, so a caller writing
`except StateDecodeError` around `session_from_primitives` already fails to catch
OMS corruption, and `test_a_nested_oms_failure_keeps_its_own_error_type` pins
that. Unifying the two is a breaking change to a public exception type with its
own deprecation path, and is not this decision's to make. The obligation here is
only that v2.10 **does not widen the split**: nothing introduced by this ADR
raises a new exception type.

## 13. What deterministic continuation then guarantees

For a **seeded** run, given the **same records in the same order** and the
**same supplied runtime objects**: capture → serialize → deserialize → restore →
continue is equivalent to uninterrupted execution across ADR-0023's Class-1
state, every deterministic identifier, **and the declared state of every
declaring strategy**.

ADR-0023's fourth precondition is discharged for declaring strategies and
retained verbatim for non-declaring ones, which keep exactly today's conditional
guarantee. The Class-1 table gains one row — declared strategy state — and
Classes 2 and 3 are unchanged.

Why this is safe rather than merely asserted, in the terms the guarantee is
made in:

- **The captured value is a pure function of the strategy at a step boundary**
  (decisions 5 and 6), so two captures of one state are equal and a capture
  cannot depend on when it was taken.
- **The payload is written by the deterministic encoder** — sorted keys, fixed
  separators — so one state has one byte sequence.
- **The decode is the strategy's own**, so the value that comes back is the
  value that went in rather than a JSON approximation of it. This is the whole
  of decision 3, and the reason the contract is two-sided.
- **Nothing is substituted anywhere** (decisions 10 and 11), so a continuation
  that would have been wrong is a continuation that does not start.
- **The identifier stream is unaffected**: capture mints nothing and restore
  installs nothing, so ADR-0022's position remains the only thing that decides
  what the continued run mints.

The residual is stated rather than hidden: a strategy whose own encode/decode
pair is not an identity produces a continuation that diverges, and AlphaLab
cannot detect that. The framework guarantees it will not corrupt what it is
given and will refuse what it cannot read; it cannot guarantee a strategy
described itself correctly. That is the same boundary ADR-0014 draws around a
supplied `ModelVersion.model`.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Strategy-internal state | the strategy |
| Its encode/decode pair | the strategy |
| Its state version | the strategy author |
| Whether a strategy declares state | the strategy, structurally |
| Recording "declared" / "declared none" / "not asked" | `StrategyRecord` |
| The step boundary capture happens at | `ExecutionPipeline` (ADR-0023 decision 7) |
| The strategy instance | the caller, via `RuntimeObjects` (ADR-0014, ADR-0023) |
| Envelope version | `PIPELINE_SNAPSHOT_SCHEMA` |
| Identifier position | `ExecutionPipelineState.id_position` (ADR-0022) |
| Refusing an unreadable payload | `alphalab.runtime.snapshot` |

---

# Data model changes

```text
+ StrategyStateProtocol            runtime_checkable; encode, decode, version
+ StrategyRecord.state             the declared payload, null, or absent
+ StrategyRecord.state_version     the strategy author's integer
  PIPELINE_SNAPSHOT_SCHEMA         1 -> 2

  SESSION_SNAPSHOT_SCHEMA          # UNCHANGED (1)
  BACKTEST_SNAPSHOT_SCHEMA         # UNCHANGED (1)
  ALLOCATION_SNAPSHOT_SCHEMA       # UNCHANGED (1)
  OMS_SNAPSHOT_SCHEMA              # UNCHANGED (1)
  PORTFOLIO_SNAPSHOT_SCHEMA        # UNCHANGED (2)
  LIFECYCLE_SNAPSHOT_SCHEMA        # UNCHANGED (1)
  StrategyProtocol                 # UNCHANGED -- ten hooks, none added
  StrategyState                    # UNCHANGED -- state lives on the instance
  DeterministicEncoder             # UNCHANGED -- no new branch
  SnapshotDecodeError              # UNCHANGED -- split not widened
```

`StrategyProtocol` does not change. A strategy declares state by satisfying a
*second*, separate protocol, so every existing strategy keeps compiling and
keeps its current — correct, conditional — continuation guarantee.

---

# Boundary behavior

Refuse an unknown envelope version → accept version 1 or 2 → require and
type-check every supplied live object → for each strategy, reconcile what the
payload records against what the supplied instance declares, refusing all four
mismatches in decision 11 → hand each declaring strategy its own payload and
version → re-assert construction-time invariants (ADR-0023 decision 4) → return
state.

Nothing partial is returned by a refused restore.

---

# Persistence semantics

One constant moves. Version 1 payloads are read, and read as "not asked" rather
than as "declared none" — the distinction decision 2 exists to keep. No
migration framework, no version-translation layer, and no legacy *shape* rule:
unlike the OMS case there is no unversioned pipeline payload in existence,
because `PIPELINE_SNAPSHOT_SCHEMA` was introduced with the module in v2.9.

A strategy's own version is carried and never interpreted.

---

# Testing invariants

1. A declaring strategy round-trips in memory and across JSON, including a
   `Decimal`, a nested mapping and a sequence — the three shapes the measurement
   in Context shows a generic decoder cannot recover.
2. Serializing one captured state twice produces identical bytes.
3. `current_id_position()` is unchanged across a capture, and a capture does not
   mutate the strategy or emit an event.
4. A run split at every record boundary reproduces the uninterrupted control
   across Class 1 **plus declared strategy state** — the existing `_class_one`
   helper extended with the declared payload beside its current
   `(status, config, subscriptions, last_error)` tuple.
5. The three record states are distinguishable: absent, `null`, and an empty
   mapping are three outcomes, and an empty mapping is not `null`.
6. Each of decision 11's four refusals raises, names the strategy, and returns
   nothing partial.
7. A schema-1 payload restores with every strategy "not asked"; a schema-1
   payload restored against a declaring instance raises.
8. `PIPELINE_SNAPSHOT_SCHEMA == 2` while the other six constants are unchanged —
   asserted directly, because decision 8 is a claim about the envelope split.
9. A strategy state the encoder refuses raises `SerializationError` at capture,
   from the encoder, naming the type.
10. The field-coverage guard extends to `StrategyRecord`, and a decoder-coverage
    guard matches `test_the_position_decoder_reads_every_position_field`.
11. No module constructs anything from a recorded type name — the existing guard,
    extended.
12. ADR-0023's invariant 9, which was never implemented: a **non-declaring**
    stateful strategy still diverges across a restore, and a test names that
    expectation rather than leaving it implicit.
13. `StrategyRecord.config` behaves exactly as it does at v2.9.0 — a guard that
    the implementation did not alter config serialization in passing
    (decision 4). No new config fidelity test is added, and none of the
    existing ones change.

---

# Migration and compatibility

No migration framework. Pipeline payloads at version 1 and 2 are both read; the
one incompatibility is deliberate and named in decision 11, row 1 — a v2.9
payload restored against a strategy that has since declared state raises rather
than starting it empty.

Every existing strategy is unaffected: `StrategyProtocol` is unchanged, and a
strategy that does not satisfy `StrategyStateProtocol` behaves exactly as it does
at v2.9.0, with the same guarantee stated the same way.

---

# Explicit non-goals

- **Arbitrary object pickling.** `pickle`, `dill`, `__reduce__` and every
  equivalent. They are not deterministic, not inspectable, not safe to read from
  an untrusted payload, and not readable by anything but the writing process.
- **Reflective object reconstruction.** No decoding a dataclass by walking its
  fields, no type registry keyed by a recorded name, no "decode whatever this
  looks like". `alphalab.persistence.decode` exists to refuse this and states
  the incident that motivated it.
- **A new encoder branch.** `DeterministicEncoder` is unchanged. A strategy
  needing a projection uses `__serializable__`, which already exists.
- **`StrategyContext` design or population.** Deferred to ADR-0026.
- **Reviving `on_fill`, `on_order`, `on_timer`, `on_start`, `on_stop` or
  `on_shutdown`.** `FillEvent` and `OrderEvent` are never constructed in
  production at v2.9.0, and delivering them means a second strategy dispatch per
  event, which changes intent ordering and every parity baseline. Not in a
  durability release.
- **Runtime unification.** `SessionState` and `BacktestState` stay as they are;
  decision 8 depends on their constants *not* moving.
- **Unifying `SnapshotDecodeError` with `StateDecodeError`.** Decision 12.
- **Redefining `StrategyRecord.config` semantics, or fixing its round-trip
  lossiness.** Decision 4. The behaviour is pre-existing v2.9 behaviour, the
  lossiness is a property of the shared encoder and of JSON rather than of
  anything decided here, and changing it would affect every strategy including
  those that never declare state. A separate decision is required first.
- **A capture cadence, a scheduler, or automatic snapshotting.** This decides
  what a capture contains, not when a caller takes one.
- **Cross-strategy state, shared state, or any state keyed by anything but
  `strategy_id`.**

---

# Consequences

Benefits. The v2.9 equivalence contract stops being conditional on something no
protocol could express. A stateful strategy becomes resumable, and — more
important than that — a strategy whose state *cannot* be restored correctly now
fails loudly at restore instead of quietly on the next event. The envelope split
ADR-0023 paid for is exercised and shown to work, with one constant moving and
six standing still. And the release stays narrow: no existing field changes
meaning, so a v2.9 payload's config reads exactly as it does today.

Costs. Strategy authors who want continuation must write an encode/decode pair,
which is more work than a decorator or an attribute list would be — and the
measurement in Context is the reason no cheaper option is honest. A second
protocol sits beside `StrategyProtocol`, so "what must a strategy implement" has
two answers. One compatibility incompatibility is taken deliberately (decision
11, row 1). `PIPELINE_SNAPSHOT_SCHEMA` acquires a legacy branch it did not have.
The `config` lossiness measured in Context is carried forward unfixed and now
sits beside a field that does not have it, so one record holds two treatments
until the separate decision in decision 4 is taken. And the residual in decision
13 is real: AlphaLab cannot verify that a strategy described itself correctly,
only that it described itself at all.

---

# Alternatives Considered

**Restrict strategy state to values the strict encoder supports.** The obvious
option and the one this ADR was expected to take. Rejected on measurement: it
specifies the write direction, which already works, and leaves the read
direction unspecified, which is the direction that is broken. A payload carrying
`"1.25"` cannot say whether it was a `Decimal` or a `str`; `Decimal` is this
repository's money type; and the alternative to guessing is a reflective decoder
D4 forbids. The precedent is not hypothetical — `StrategyRecord.config` is this
option, shipped, and Context measures what it returns.

**A narrower value space — JSON scalars only, no `Decimal`.** Makes the round
trip an identity by removing the ambiguous type. Rejected: it bans the one type
a trading strategy most needs for thresholds and sizes, and pushes every author
into hand-rolling `Decimal(str(...))` conversions at both ends — which is the
encode/decode pair from decision 3, written worse and without a version.

**Pickle the instance.** Solves everything in one line and is why it deserves a
mention. Rejected under D2 and the non-goals: not deterministic across
interpreter versions, not inspectable, not safe to load from a payload the
process did not write, and it would make the snapshot a Python artifact rather
than the JSON document every other state in the repository is.

**A declarative field list — the strategy names its stateful attributes.**
Attractive because it is less code for the author. Rejected: naming the
attributes still does not say what type they are, so the decoder is back to
guessing, and it forces the framework to reach into instance attributes, which
is the introspection decision 1 refuses.

**Version strategy state with `PIPELINE_SNAPSHOT_SCHEMA`.** One constant instead
of two. Rejected under D5: every strategy author's state change would move an
AlphaLab constant and force a release, and AlphaLab cannot validate a version
whose meaning it does not know.

**Skip the strategy that fails to decode and restore the rest.** More available,
and a run continues. Rejected under D2: it produces a state that is internally
consistent, compares equal on every Class-1 value, and is wrong — the exact
failure this release exists to remove.

**Refuse all schema-1 payloads.** The strict reading of the portfolio precedent.
Rejected under decision 9: the portfolio refused because it *could not read* a v1
payload honestly, and a v1 pipeline payload can be read perfectly. Applying the
letter of that precedent against its reason would discard readable payloads for
no informational gain — the same argument ADR-0023 decision 5 made for the OMS.

---

# Release impact

Minor-version feature. Additive: one new protocol, two new fields on an existing
record, one schema constant moved with a legacy branch. `StrategyProtocol`,
`StrategyState`, `DeterministicEncoder`, `PersistenceProtocol` and six of the
seven schema constants are unchanged, and every existing strategy keeps working
with its current guarantee stated unchanged.

One deliberate incompatibility, bounded and tested: a v2.9 payload restored
against a strategy that has since declared state is refused.
