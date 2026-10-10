# ADR-0049: The v4.0 Freeze — the History Classified, the Engine Re-audited and the Public Contract Held

## Status

**Accepted and implemented in v4.0.0.**

The release the four pre-v4 releases prepared for (ADR-0045 to ADR-0048). It adds
**no package**, removes none, and adds or removes **no package edge**. Snapshot
schemas: pipeline 7 -> 8 and run 4 -> 5, each recording how the run's live
objects were configured; every older payload is upgraded on read. Public API:
four names added to `alphalab.strategy`, none removed or rebound, and the
manifest extended to the modules the documentation names as public.

Amends **ADR-0029** (its non-goal "no live-object parameter persistence" is
withdrawn: decision 1), **ADR-0036** decision 1 (a row that does not become a
record is governed by the cleaning policy and rides into the provenance as a
transformation: decision 2), **ADR-0048** decision 15 (the ledger's limitations
are held to *every* section of history that states one, and accepted future work
is a closed classification: decision 5), and **ADR-0030** decision 10 (replay
resumability, deferred there, is tracked work with acceptance criteria:
decision 6).

---

# Context

v3.13 closed every item its ledger had assigned and declared nothing left for
later. A freeze certifies what it has read, so v4.0 re-read two things the
pre-v4 audit had not, and audited the canonical path as somebody who had not
written it.

**The history.** The pre-v4 ledger inventoried ROADMAP's boundaries and optional
list (v3.10) and the "Known limitations" and "DEFERRED" lists of ADR-0042 to
ADR-0044 (v3.13). It never read the v2-era ADRs' "Explicit non-goals", "Not in
scope" and "Consequences of deferring" sections, the CHANGELOG's "Known gaps",
"Known Limitations", "Not in scope", "Deferred, unchanged", "Recorded" and
"Still open" sections, or `nowandfuture.md`'s open questions: 257 items in 39
sections. Most were delivered by later releases, expired with the release they
fenced, or are boundaries the ledger already held. Two were live gaps --
ADR-0029's "no live-object parameter persistence", behind which a restore
accepted a simulator charging another commission, and the replay ADR-0029 and
ADR-0030 left non-resumable -- and one limitation, a certified identity's
dependence on the host's `libm`, was stated only in a CHANGELOG.

**The engine.** The audit built a strategy through the public API from the
documentation, as an outside developer would, checked every fill and the
accounting against arithmetic done by hand, fed it every kind of invalid input,
and ran a 1,000-run differential test of the canonical path's accounting
against a ledger kept independently of it. The path was sound: fills, cash,
positions, equity and the conservation of money exact on every run, replay
parity, determinism across seeds and processes. Four defects were found in its
edges: the restore above; ingestion that dropped unreadable rows under a
refusing policy, with `MissingValuePolicy` read by nothing; option pricing that
returned `NaN` for an input that is not a number; and fractional fills in a run
sized in whole units. No document said how to write a strategy.

---

# Decision

## 1. A restore continues with the objects the run was captured with

A snapshot records each live object -- the sizing model, the simulator, the fill
policy -- by its type **and its configuration**, written by
`alphalab.runtime.assumptions.describe`: the object's parameters, recursively,
with no memory address, and a mapping or a set by its contents sorted by their
own descriptions, so that equal containers describe alike in any process
(PER-009: the first v4.0 candidate wrote a container by its type name, and a
sizing model's per-asset volatilities escaped the comparison). `restore` refuses an object whose description differs,
naming both. The description enters the recorded configuration, so a
reproducibility manifest's `configuration_id` distinguishes two runs that differ
only in a cost. A payload written before v4.0 recorded the type alone and is
upgraded to "not recorded"; its objects are checked by type alone, as the
release that wrote it did. The instrument registry is not described: ADR-0027
decision 5 says restore must not compare it, and that stands. A strategy's
parameters are its own: they are not described, because a strategy's attributes
include the memory it accumulates, and its durable state is declared through
`StrategyStateProtocol`.

## 2. Ingestion governs every row by the cleaning policy

A row that cannot become a record is a defect like any other. A missing value is
governed by `MissingValuePolicy` and anything else by `InvalidRecordPolicy`.
`REFUSE` stops the ingestion, naming the policy and the first row; a dropping
policy drops the row, reports it as before, and records a `TransformationRecord`
in the provenance, so the dataset version says the source was altered.
`InvalidRecordPolicy.KEEP` keeps a record that fails a set-level check, and
cannot keep a row that never became one: it drops and records it.

## 3. Numbers that are not numbers are refused, and whole units fill in whole units

Every option-pricing entry point refuses a non-finite spot, rate, volatility or
market price before any arithmetic reads it. In a run that sizes orders in whole
units, a partial fill is floored to whole units and a cap below one unit is no
fill; the fill policy is unchanged.

## 4. The public path is documented, demonstrated and held

`docs/GETTING_STARTED.md` walks a strategy from data to a reproduced result, and
`examples/70_build_a_strategy.py` does it, checked by an end-to-end test and run
from each clean install. Two conveniences remove the boilerplate every example
wrote: `start_strategy`, the supervisor's four transitions in order, refusing an
identity already registered and drawing nothing from the caller's identifier
stream; and `context_factory(clock, logger)`, with `FixedClock` and
`DiscardingLogger` as the plain choices. Neither is a second authority. The
README shows only excerpts of the example, and a test holds every line of each
excerpt to it.

## 5. Every section of history that defers something is held to the ledger

`docs/audit/scripts/historical_inventory_v4.py` classifies every item those
sections state -- delivered, fixed in v4, boundary, limitation, external,
removed, expired, superseded or future -- each with its evidence, and writes the
`HIS-xxx` ledger entries and `docs/audit/V4_HISTORICAL_INVENTORY.md`.
`tests/regression/test_every_adr_deferral_is_classified.py` reads the ADRs and
the CHANGELOG and requires every item to match exactly one classification, and
the generated files to be current. An entry may be left for later only as
**accepted future work**: it states its limitation, impact, reason, interim
behaviour, horizon, dependencies and completion criteria, and ROADMAP's Future
work lists it; nothing else may stay open.

## 6. One item is future work

A resumable replay: `ReplayBacktest`'s cursor and its second identifier stream
have no snapshot. It is a new capability, additive to the driver, not a defect:
a replay is a pure function of its dataset and seed and restarts
deterministically, and a backtest of the same dataset produces the same orders,
fills and P&L and resumes byte for byte. Target v4.1 (FUT-001).

## 7. The public API is stable from here

From v4.0 a minor release adds and corrects but does not break; an incompatible
change waits for 5.0 with an ADR and a migration; no alias is introduced for a
rename; a persisted format moves only by a schema step that reads every older
payload (`docs/api/PUBLIC_API.md`). The manifest of v4.0.0 is kept in
`docs/api/history/` and the next release's CHANGELOG is checked against it.

The surface frozen is the one the documentation teaches. The manifest walked
packages only, so `alphalab.api` -- the module a host application imports --
and each durable state's snapshot module -- `capture`, `from_primitives`,
`restore`, how a run is stopped and continued -- were outside it (API-007).
They are recorded now, and the list is held to `docs/STATE_MODEL.md`'s
durability table, so the freeze covers 2,617 names in 44 packages and 11
modules. The release also freezes what v3.13.0 wrote: its payloads are
fixtures every later build reads (`tests/fixtures/snapshots/v3.13.0`).

---

# Consequences

* A run configured as in 3.13 computes what it computed in 3.13, except where
  3.13 was wrong: a restore with a differently configured object, an ingestion
  under a refusing policy with an unreadable row, a non-finite option input and
  a whole-unit partial fill each now refuse or round, as the CHANGELOG's
  migration table lists.
* Every `configuration_id` and `result_id` changes, because the recorded
  configuration now carries each live object's description; the certificate is
  regenerated and its evidence moves for exactly that reason.
* The ledger holds 272 entries: every one implemented, kept with its reason, or
  -- one -- accepted future work.

**Known limitations**

- **A strategy's configuration is persisted as JSON reads it.** A `Decimal` in
  `configure`'s value reads back as a string; nothing on the execution path reads
  it, and a run's identity is unaffected.
- **Certified identities are per host class.** A float computed through another
  `libm` can differ in its last bit; the certificate records the host, and
  `--check` names what moved.
- **The operating envelope is measured, not unbounded.** Beyond the stress
  program's workloads is extrapolation, and a run is single-threaded by design.
- **Only CPython 3.12 is tested.** `requires-python` admits later interpreters,
  which nothing has run; v4.1 adds them to CI.

**Deferred** — to v4.1, with acceptance criteria in ROADMAP: a resumable replay.

---

# Rejected alternatives

* **Describing the instrument registry and the strategies on restore.** The
  registry is configuration restore must not compare (ADR-0027); a strategy's
  attributes include its accumulated memory, so a description of a fresh
  instance would never equal a captured one, and every honest restore would be
  refused.
* **Refusing a pre-v4.0 payload because it cannot say how its objects were
  configured.** It would make every snapshot v3.13 wrote unreadable to obtain a
  check the payload never had the information for; reading it as "not recorded"
  is what it already meant.
* **Keeping an unreadable row under `InvalidRecordPolicy.KEEP`.** There is no
  record to keep; inventing one is the repair the cleaning module refuses.
* **Rounding a whole-unit partial fill to the nearest unit.** It could fill more
  than the liquidity the policy allowed; flooring never does.
* **Building replay resumability into the freeze.** A new envelope in the
  release that freezes the formats is the wrong place to add one; the interim
  behaviour loses nothing.
* **A `TODO` list in ROADMAP.** Accepted future work carries the same fields a
  ledger entry does, and a test holds the two together.
