# ADR-0053: Run and Input Provenance -- the Schedule a Run Was Given, Held Outside Its Record

## Status

**Accepted** (2026-11-10), revision 3. The draft's revision 2 (sha256
`e490e6b8…`, kept outside the repository) was reviewed by the approved v4.3.0
development plan (section N), which accepted its option C-e and required eight
revisions, R53-1 to R53-8, and a compatibility review, CR-53-1 to CR-53-9,
before it was accepted. The owner delegated the decision to its milestone on
2026-11-10 (G0 item 6); this record is that decision, taken under the delegation
and recorded in `docs/work/v4.3.0/PROGRESS.md`. It implements ledger FUT-046
with the completion criteria amended as the plan's D-53-7 directs. It also
delivers FUT-042's goal by provenance and as ADR-0052's option C, opt-in, with
the default unchanged. It reverses no accepted decision and answers ADR-0017.

---

# Context

A backtest's `RunSchedule` -- its rates, funding, cash flows, corporate actions,
timers and delivered observations -- is a driver input. Each driver merges the
observations into it and applies the result through one `ScheduleCursor`. The
run's record keeps the entries' effects, not the entries: a rate leaves no event
of its own (ADR-0051). `result_id` is the digest of that record (ADR-0049), and
`BacktestResult` is the run alone (ADR-0051 decisions 13 and 15, pinned by
`test_run_runtime_boundary.py`).

`RunSchedule.digest` -- SHA-256 over the rate table's maximum age and one line
per entry -- is recorded in two places. A paused replay's payload records it, and
its restore refuses another schedule. A manifest records it when its producer
passes `schedule=`.

That leaves two problems (FUT-046):

1. **A manifest can name less than the run was given.** `manifest_for_run`
   holds a named schedule to what the run recorded of it: its observations, its
   surfaces and its auctions (REP-009). A run given rates or funding records
   nothing that says so. Its manifest can name no schedule, or another, and still
   verify.
2. **A backtest continued from a snapshot or a checkpoint chain is not held to
   its schedule.** The continuation is a hand loop over `ScheduleCursor.start`,
   and nothing recorded refused another schedule there.

# Decision

## 1. The schedule digest's formula is named, not changed (R53-6)

`RUN_SCHEDULE_DIGEST_SCHEME = "alphalab.run_schedule.v1"` names the existing
formula. It is never rendered into the digest, so every digest a paused replay
or a manifest has recorded stays valid. M0's golden digests
(`test_schedule_digest_pins.py`) pin it.

A change to the formula, to an entry kind's persisted value, or to an item's
encoded fields is a new scheme, and needs an ADR.

## 2. A binding, and a vocabulary that never claims more than was checked (R53-4)

`alphalab.runtime.schedule_check` holds both values, beside `RegistryVerification`.
They sit in `runtime` because a checkpoint, which `runtime` writes, records a
binding, and `backtesting` already imports `runtime`.

- `ScheduleBinding(scheme, digest)`: `digest` is `None` for no schedule or an
  empty one, as a manifest has always recorded it. `RunSchedule.binding` and
  `ScheduleCursor.binding` give it.
- `ScheduleVerification` has four values:
  - **`VERIFIED`**: the digest is the one bound, and the run's own delivery
    record (REP-009) agrees with the schedule.
  - **`DECLARED`**: the digest is the one bound, and nothing more was checked.
  - **`UNRECORDED`**: nothing bound a schedule. This covers an older payload and
    a result given no provenance. It is never reported as verified.
  - **`NONE_GIVEN`**: the run was given no schedule, and none was supplied.

## 3. A continuation is held to a supplied binding (R53-1)

- `ScheduleCursor.start(schedule, after, *, expected=None)` refuses a schedule
  whose binding is not `expected` before any entry is applied. Not given, nothing
  is checked, as before.
- `restore_checkpoints(..., *, schedule_binding=None)` holds the binding of the
  schedule supplied to the one its chain recorded (section 4). It refuses another
  schedule, none where the run had one, or one where it had none, before any
  live object is touched. A chain that recorded a binding is refused if none is
  supplied.
- `checkpoint_schedule(payloads)` returns a chain's recorded binding.
  `chain_schedule_verification(payloads, binding)` says how a chain would hold
  it.

## 4. A checkpoint chain binds in its envelope (R53-2)

`checkpoint(state, previous, *, schedule=None)` records the binding it is given
in the envelope, as version 3, `CHECKPOINT_SCHEMA = 3`. Given none, the payload
is version 2's, byte for byte, so every chain written before v4.3, or written
without a binding, reads and restores exactly as it did. Its schedule is
`UNRECORDED`. Every checkpoint of one chain that records a binding records the
same one.

## 5. Provenance is a record beside the run, from plain functions (R53-3, R53-5, R53-7)

`alphalab.backtesting.provenance.RunProvenance` is the general input-provenance
record. It records only what the run's record and manifest do not identify:

- its subject: a result by its `result_id`, or a payload (a bare run snapshot or
  a checkpoint) by the SHA-256 of its text;
- the effective schedule's binding;
- the digest of the instrument registry the run held, by default (FUT-042's
  goal).

The datasets, the code and the engine are the manifest's, and provenance does
not repeat them. `produced_at` is kept but is never part of the identity.
`provenance_id` hashes a pinned canonical key under `RUN_PROVENANCE_SCHEME`. The
record files in an `EvidenceStore` as kind `run_provenance` and versions its
fields with `RUN_PROVENANCE_SCHEMA = 1`. `provenance_from_primitives` reads it
back.

Three functions issue and check it:

- `run_provenance(result, *, schedule, observations=(), result_id=None)`
  refuses a schedule the run's own record contradicts.
- `payload_provenance(payload, *, schedule, ...)` binds a payload by its own
  text.
- `verify_provenance(subject, provenance, schedule)` recomputes every digest
  before it answers.

**No driver flag, and no return-type change:** `BacktestEngine.run`'s
overloads, `ReplayBacktest.run` and the `api` facade are unchanged. Creating
provenance draws no identifier. It never serialises a run a second time: a
result's id is taken where the caller already computed it
(`digest_run(result).result_id`), and a payload is hashed as it is stored. A
second serialisation would cost 62-69 % of the run (plan R53-7).

## 6. A manifest is checked against provenance, and may record it (D-53-3, FUT-042)

`manifest_for_run(..., provenance=None, identity_version=1)` behaves as follows.

- **Given provenance**, it refuses each of these, none of which was ever valid,
  because the caller holds evidence that contradicts the claim:
  - provenance of another run;
  - another schedule;
  - no schedule named when the provenance binds one;
  - a schedule named when the provenance binds none.

  Under identity version 1 the manifest's identity is unchanged.
- **Not given provenance**, it behaves exactly as before. No default refusal
  is added in 4.x (D-53-3).
- **`identity_version=2`** is ADR-0052's option C, opt-in:
  - The key renders `identity.version=2`.
  - A run that held a registry has it recorded.
  - Provenance, when given, is cited by its `provenance_id`.
  - `manifest_gaps` reports a version-2 manifest of a registry-holding run that
    lacks the registry.

  Version-1 manifests keep their identities, verification and assessments.
  Making version 2 the default is a 5.0 change (plan D-F42).

## 7. What provenance proves, and what it does not (R53-8, answering ADR-0017)

ADR-0017 rejected provenance held outside a digest as silently untamper-evident.
A `RunProvenance` is not unprotected metadata. It is a record with its own
content identity, and every digest it states is re-derived at every check. It
is **tamper-evident, not substitution-proof**:

| Attack or accident | Detection |
| --- | --- |
| Provenance attached to another run or payload | The subject's digest is recomputed and compared |
| Another schedule supplied with the right provenance | The schedule's binding is recomputed and compared |
| Another registry, or provenance edited | `provenance_id` changes, and the registry's digest is recomputed |
| A chain continued under another schedule | The envelope's binding is compared before anything is restored |
| A consistent forgery of run, schedule and provenance | **Not detected.** These are integrity digests, not signatures. Authenticity is the host's, and only a rerun detects substitution |

# Departures from the plan's text, and why

- **No `schedule_digest` on `run_snapshot.restore`.** R53-1 named both restores.
  A bare run snapshot records no binding, so the parameter there would have
  nothing to check, and a caller could believe a continuation was held to its
  schedule when it was not. A bare snapshot's continuation is held by the
  cursor, given the binding its provenance records. A chain's continuation is
  held by its envelope (section 3).
- **A binding, not a bare digest.** `restore_checkpoints` takes
  `schedule_binding: ScheduleBinding`, so the scheme is compared as well as the
  value.
- **`VERIFIED` needs a result.** A payload's or a chain's record is not decoded
  to be read against the schedule, so those checks answer `DECLARED`.

# Compatibility (CR-53-1 to CR-53-9)

| # | Check | How it is held |
| --- | --- | --- |
| CR-53-1 | The drivers and the `api` facades are unchanged | `test_public_api_manifest.py`: nothing on them rebound |
| CR-53-2 | Only keyword-only parameters defaulting to `None` were added to existing functions | The manifest's diff: `checkpoint`, `restore_checkpoints`, `ScheduleCursor.start`, `manifest_for_run` |
| CR-53-3 | Checkpoint schema 3 reads every version-1 and version-2 chain | `test_checkpoints.py`; the frozen v4.2.1 chain resumes to its `result_id` |
| CR-53-4 | `RunSchedule.digest` is byte-identical | M0's golden digests |
| CR-53-5 | No `RunState`, pipeline or `BacktestResult` field is added | `test_run_runtime_boundary.py`, unedited |
| CR-53-6 | Creating provenance draws no identifier | ADR-0029 D6's pattern, in `test_run_provenance.py` |
| CR-53-7 | `manifest_id` is unchanged unless identity version 2 is asked for | Golden manifests and the certificate |
| CR-53-8 | Certificate evidence is unchanged | `certify_release.py --check` |
| CR-53-9 | Mutations for: the binding not checked, the digest not pinned, VERIFIED without the run's record | M1's mutation series |

# Known limitations

**Known limitations**

- **A matching digest is evidence of integrity, not of authenticity.** Anyone
  who can run code can produce a consistent run, schedule and provenance, and
  only a rerun detects the substitution. The record says so, and the vocabulary
  never calls a digest match more than `DECLARED`, or more than `VERIFIED` once
  the run's own record has been read.
- **Only a run given its provenance is held to its schedule.** A result,
  snapshot or chain without provenance or a recorded binding keeps v4.2's
  guarantee, and is reported `UNRECORDED`. Putting the binding into the hashed
  record would hold every run by default, and moves every scheduled run's
  identity, which only a major release may do.
- **A default manifest still names the registry by type.** Identity version 2
  records it, opt-in; making version 2 the default changes `manifest_id` for
  every registry-holding run's default manifest.

# Consequences

New public names:

- `alphalab.backtesting`: `RUN_SCHEDULE_DIGEST_SCHEME`, `RUN_PROVENANCE_SCHEME`,
  `RUN_PROVENANCE_SCHEMA`, `RunProvenance`, `ProvenanceError`, `run_provenance`,
  `payload_provenance`, `verify_provenance`, `provenance_from_primitives`.
- `alphalab.runtime`: `ScheduleBinding`, `ScheduleVerification`,
  `checkpoint_schedule`, `chain_schedule_verification`.

Each name and parameter is additive. A host that records `result_id`s,
manifests and checkpoint chains today keeps every one, valid and verified as
before. Provenance for a run made before v4.3 is obtained only by rerunning it
under v4.3, never by editing what was recorded.
