# ADR-0017: Dataset Identity and Evidence Derivation

## Status

Accepted (v2.7.0).

Extends ADR-0013, which composed the model and strategy lifecycle, and ADR-0014,
which introduced the explicit `schema_version` "so that the first schema change
is a decision rather than a silent misread". ADR-0015 made that first change for
`PortfolioSnapshot` and listed "dataset provenance" among the things not in its
scope. This ADR supplies it, and does so without a second schema change.

---

# Context

`BacktestEngine.run(config, dataset, strategy_state, context_factory)` receives a
`MarketDataset` carrying a non-empty, validated `dataset_id`, iterates its
records, and calls `finalize(state)`. `BacktestResult` is `(config, state,
steps, records_processed, seed)`. The dataset identity is **not** among them.

`TradingSession.run(config, source, ...)` receives a `MarketDataSource` whose
protocol declares a `source_id` property — "Identifier for this stream, used to
derive record identities". The session reads it in exactly one place: composing
the error message for an `UNORDERED` source. `SessionState` does not record it.

`lifecycle.evidence` documents the consequence in its own docstring, accurately:

> `dataset_id`: The data it was measured over. Named by the caller: neither a
> `BacktestResult` nor a `ResearchState` carries the dataset it consumed, and
> inventing one would be a guess.

That is the defect. `evidence_id_for(method, subject, dataset_id, seed, metrics)`
hashes `dataset_id` into a SHA-256 digest, and `evaluate_policy` calls
`verify_evidence_id` and on mismatch reports that the evidence "was altered after
it was recorded". So the tamper-evidence is real for **metrics** and decorative
for **data identity**: two runs over genuinely different data can be handed the
same `dataset_id`, produce evidence that verifies cleanly, and pass the gate.

`deployment_manager.packaging` lists "dataset ids" among the component
references a release manifest carries, while `lifecycle.deployment.release_manifest`
emits only `strategy`, `model`, `experiment_run` and `evidence`. The manifest was
designed for a dataset reference nothing upstream can supply.

---

# Decision drivers

- **D1.** v2.6 evidence must remain verifiable.
  `tests/unit/lifecycle/test_lifecycle_snapshot.py` already pins
  `all(verify_evidence_id(item) for item in restored.evidence.values())`.
  Breaking the digest would make honest historical evidence report as tampered —
  a false accusation, and the worst possible failure mode for an audit trail.
- **D2.** No migration framework. `persistence.decode.require_schema_version`
  states the house rule: "There is no migration path; read it with the build
  that wrote it."
- **D3.** The v2.6 precedent for schema evolution is: give the subsystem its own
  constant, refuse the old version, invent nothing. `LIFECYCLE_SNAPSHOT_SCHEMA`
  still aliases `DEFAULT_SCHEMA_VERSION`, which is also the version of
  `CommonEvent` and `BaseEvent`, so bumping it would version every event in the
  system as a side effect.
- **D4.** Do not expand beyond the bounded finding.
- **D5.** Honesty over symmetry: where a producer genuinely cannot know its
  dataset, say so rather than fabricate one.

---

# Decision

A dataset identity is the name of the record stream a run consumed. It is
**propagated by the run and derived by the evidence**, not asserted by the
caller.

1. `BacktestResult` gains `dataset_id: str | None = None`. `BacktestEngine.run`
   and `ReplayBacktest.run` supply `dataset.dataset_id`.
2. `SessionState` gains `source_id: str | None = None`. `TradingSession.run`
   supplies `source.source_id`.
3. `evidence_from_backtest(result, subject, produced_at)` derives `dataset_id`
   from the result, and **refuses** a result whose `dataset_id` is `None` — in
   the same voice as the existing "the backtest compiled no performance report"
   refusal.
4. `evidence_from_research` keeps its caller-supplied `dataset_id`.
   `ResearchState` genuinely does not know its data, and inventing one would be
   the fabrication v2.6 removed.
5. `evidence_id_for` is **unchanged**, and `ValidationEvidence` gains **no
   fields**.

`None`, not `""`, is the absent value, following v2.6's `sector_id: str | None`
and `holding_period_seconds: float | None`: absent, not defaulted.

## What a dataset identity is, and is not

`dataset_id` remains a **caller-provided opaque identity**. It is not
registry-issued and not content-addressed. Content-addressing would require
hashing every record — a cost proportional to the dataset — and would change
every `dataset_id` value, hence every `evidence_id`, which is the exact break
this ADR exists to avoid.

**AlphaLab guarantees propagation, not uniqueness.**
`MarketDataset.dataset_id` is validated non-empty and nothing more, and this ADR
does not change that. Evidence now says "this run consumed the stream the caller
called X" — a materially stronger claim than v2.6's "someone asserted X while
recording this measurement", and one AlphaLab can actually keep. A dataset
registry issuing unique ids is real work, is outside the bounded scope, and
would change `dataset_id` values.

## `dataset_id` and `source_id` are distinct

A `dataset_id` names a finite, ordered, **validated** dataset:
`backtesting.dataset.validate_dataset` enforces chronological ordering, unique
record ids, and agreement between each record's timestamp and its payload's. A
`source_id` names a stream that may declare `OrderingGuarantee.UNORDERED` and may
be live. Same role, different guarantees.

`TradingSession` therefore records `source_id`, not `dataset_id`. Conflating
them would let a live session's provenance claim the guarantees a validated
dataset carries.

## Provenance field disposition

| Field | v2.7 disposition | Rationale |
| --- | --- | --- |
| `dataset_id` | **Mandatory** for `BACKTEST` evidence; propagated on `BacktestResult` | The bounded finding |
| `source_id` | **Recorded** on `SessionState`; **not** on evidence | Sessions do not produce evidence today |
| record count | **Already preserved** — `BacktestResult.records_processed` | Exists |
| seed | **Already preserved** — `BacktestResult.seed`, already a digest input | Exists |
| time coverage | **Derivable, deferred** | `MarketDataset.start_time` / `end_time` are computed properties; putting them on evidence enters the digest |
| normalization policy | **Deferred** | Same digest constraint; belongs with the ADR-0016 registry |
| provider identity | **Deferred** | Same |

Everything deferred lands together in one deliberate lifecycle schema bump in a
later release. Dribbling persisted-provenance fields across releases is exactly
the mistake the v2.6 portfolio precedent teaches against.

---

# Ownership

| Concept | Owner |
| --- | --- |
| Dataset identity value | `alphalab.backtesting.dataset.MarketDataset` — unchanged |
| Stream identity value | `alphalab.market.source.MarketDataSource` — unchanged |
| Propagation into a run | `alphalab.backtesting.engine`, `alphalab.runtime.session` |
| Derivation into evidence | `alphalab.lifecycle.evidence` |
| Digest construction | `alphalab.lifecycle.evidence.evidence_id_for` — **frozen for v2.7** |

---

# Data model changes

```text
BacktestResult    + dataset_id: str | None = None
SessionState      + source_id:  str | None = None

finalize(state, dataset_id: str | None = None)          # threading only

evidence_from_backtest(result, subject, produced_at)    # dataset_id param REMOVED

ValidationEvidence                                      # UNCHANGED
evidence_id_for                                         # UNCHANGED
LIFECYCLE_SNAPSHOT_SCHEMA                               # UNCHANGED (remains 1)
```

Both types gaining a field are **not persisted**. No `capture` / `restore`
covers `BacktestResult` or `SessionState`; the persisted snapshots are
`kernel`, `lifecycle`, `oms`, `portfolio`, `production` and `workbench`.
`studio.BacktestResult` is an unrelated summary type and is untouched.

---

# Boundary behavior

Identity is **captured** where the run receives its data —
`BacktestEngine.run`, `ReplayBacktest.run`, `TradingSession.run` — and
**enforced** where evidence is built, in `evidence_from_backtest`, which refuses
`dataset_id is None`.

A caller hand-driving `initialize` / `advance` / `finalize` without naming a
dataset gets `None`: honestly absent, and refused at the evidence boundary
rather than silently recorded as a claim.

---

# Persistence semantics

No persistence change whatsoever. `BacktestResult` and `SessionState` are not
persisted. `ValidationEvidence` is persisted inside `LifecycleSnapshot` and is
unchanged. `LIFECYCLE_SNAPSHOT_SCHEMA` stays at 1.
`tests/regression/test_snapshot_field_coverage.py` is unaffected, and
`require_schema_version` is unaffected.

**Can a restored v2.6 snapshot still verify its existing evidence? Yes.** The
digest function is byte-identical and no field is added to any persisted type.

One defensive change is recommended alongside this ADR: de-alias
`LIFECYCLE_SNAPSHOT_SCHEMA = DEFAULT_SCHEMA_VERSION` to the literal `1`.
Behaviour is identical today; it removes the trap that a future bump of the
shared constant silently versions the lifecycle snapshot — the exact trap v2.6
fixed for `PortfolioSnapshot` and left in place here. One line, plus one
assertion, with no semantic change.

---

# Testing invariants

1. `BacktestEngine.run` records the dataset's id on its result, and
   `ReplayBacktest.run` records the same id.
2. `TradingSession.run` records the source's `source_id`.
3. `evidence_from_backtest` refuses a result whose `dataset_id` is `None`,
   naming the reason.
4. A caller cannot make `BACKTEST` evidence claim a dataset the run did not
   consume — the parameter no longer exists.
5. Two runs over datasets with different ids produce evidence with different
   `evidence_id`s.
6. **Digest stability.** `evidence_id_for` applied to fixed inputs produces a
   pinned, hard-coded hex digest: a golden-value test that fails if anyone
   changes the construction.
7. **Restore compatibility.** Evidence built under the v2.6 construction still
   satisfies `verify_evidence_id` and still passes `evaluate_policy`.
8. `LIFECYCLE_SNAPSHOT_SCHEMA == 1`, and a v2.6 lifecycle payload restores
   unchanged.

Suites: `tests/regression/test_dataset_identity_survives_a_run.py`,
`tests/regression/test_evidence_digest_is_frozen.py`, and an extension of
`tests/unit/lifecycle/test_identity_and_evidence.py`.

---

# Migration and compatibility

**No data migration, and no migration framework.**

The only source-level break is the removal of the `dataset_id` parameter from
`evidence_from_backtest` — a compile-time break with an obvious fix, in a minor
release, on a v2.4 API. Deprecating it via an ignored optional parameter was
considered and rejected: a parameter that is accepted and silently discarded is
precisely the asserted identity this ADR removes.

---

# Explicit non-goals

- Content-addressed or registry-issued dataset ids.
- A dataset registry, or any uniqueness guarantee.
- Adding fields to `ValidationEvidence`, `ExperimentRun` or `ResearchState`.
- Adding a dataset component to `release_manifest()`.
- Any lifecycle schema version movement.
- Wiring `alphalab.data.DatasetMetadata` to the execution path, or merging the
  `Dataset` concepts.
- Giving `ResearchState` a dataset reference.

---

# Consequences

Benefits. Evidence names data the run actually consumed. `evidence_id` becomes
meaningfully tamper-evident over data identity, because the input is no longer a
free-form claim. Two runs over different data can no longer collide. There is
zero persistence risk. The `BACKTEST` / `RESEARCH` asymmetry is explicit and
honest rather than hidden.

Costs. `evidence_from_backtest` breaks at the source level. `RESEARCH` evidence
remains caller-asserted, which is a real and named limitation. Uniqueness of
`dataset_id` is still the caller's responsibility. Richer provenance is
postponed to one future schema bump.

---

# Alternatives Considered

The evidence-compatibility question was the whole difficulty, and five
strategies were compared against D1-D3.

**Schema-versioned evidence ids.** Add a version to the digest input or beside
it, and branch verification on it. Rejected: it splits `verify_evidence_id` into
two permanent code paths, and the version must itself be protected or it becomes
the tamper vector.

**Backward-compatible dual verification.** Have `verify_evidence_id` return true
if the id matches either the old or the new construction. Rejected, and the most
dangerous of the five: an attacker, or an accident, can craft new-form evidence
that validates under the old rule. Accepting two answers to "is this authentic"
weakens the invariant it exists to provide.

**Migration or rewrite of stored evidence ids.** Recompute and overwrite ids in
existing snapshots. Rejected: rewriting a tamper-evidence digest destroys the
property being asserted. `record_evidence` already refuses an id held by
different content, precisely to prevent this.

**Preserve the old digest and apply the new construction only to future
evidence.** Rejected as a distinct option, because it is the first option
wearing different clothes: two constructions coexist, and something must decide
which applies.

**Do not change the digest at all.** Selected. The v2.7 archaeology flagged this
as a stop condition on the assumption that the digest's *input set* would
change. Re-reading the code shows it need not: `evidence_id_for` already takes
`dataset_id`, so deriving that value from the run instead of from a parameter
leaves the digest computation byte-identical. The function's body, signature and
canonical rendering are untouched.

The consequence is a binding constraint, and it is accepted deliberately:
**v2.7 adds no field to `ValidationEvidence`.** Any richer provenance would
either enter the digest, breaking every existing id, or sit outside it,
silently untamper-evident. Both are worse than deferring.

Also rejected: **putting `dataset_id` on `BacktestConfig`**. Config and the
passed dataset could then disagree, creating two sources of truth for one fact,
and `BacktestConfig`'s own docstring scopes it to "everything that decides what
a run produces" — which a dataset argument is not.

---

# Release impact

Minor-version feature. One source-compatibility break in `alphalab.lifecycle`.
No persisted format change: v2.6 snapshots restore and verify unchanged. Closes
the disconnect `lifecycle.evidence` documents about itself.
