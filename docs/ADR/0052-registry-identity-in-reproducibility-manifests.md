# ADR-0052: The Instrument Registry in a Default Reproducibility Manifest

## Status

**Accepted** (2026-11-10): **option C, opt-in, in v4.3; the default flip held
for 5.0** -- the v4.3.0 plan's decision D-F42, which the owner delegated to its
milestone and which was taken under that delegation (`docs/work/v4.3.0`). Since
v4.3 `manifest_for_run(identity_version=2)` makes a version-2 manifest, as
ADR-0053 section 6 states; version 1 stays the default, so every manifest made
before, and every default manifest made since, keeps its identity, its
verification and its assessment. Until this record was decided it read:
"Proposed -- awaiting the maintainer's decision", with nothing implemented
beyond `manifest_for_run(instruments=True)` (REP-006). The options below are as
they were weighed.

---

# Context

**What a manifest identifies.** A run manifest names the dataset (version and
content digest), the recorded configuration (`configuration_id`), the seed,
the engine and build, the strategy fingerprint, the schedule and execution
plan digests, the queue model -- and the result, `result_id`, the SHA-256 of
the run's complete record. `manifest_id` is the SHA-256 of a canonical key
over all of these (`canonical_manifest_key`), and a field added since v4.0 is
rendered into the key **only when present**, so a manifest of an earlier kind
keeps the identity it had.

**What it does not.** The run's configuration record names the instrument
registry by type (`ConfigRecord.instruments_type`, ADR-0023). The registry
holds terms that decide what a run does -- contract, settlement, margin,
borrow and tick terms, the margin model and its surfaces, the venues' bands,
halts and auctions -- so two runs given different terms whose records agree
share a default manifest (`tests/integration/test_registry_identity.py`).
Terms that change the run change `result_id` under any manifest; the default
manifest only fails to say *why* two results differ, and fails to tell apart
two experiments whose terms did not bind.

**What exists today.**
- `manifest_for_run(instruments=True)` records `instruments_digest`, the digest
  of the registry's complete snapshot (`registry_digest`), read off the run;
  `verify_manifest` recomputes it, and `assess_reproducibility` compares it.
- A default manifest of a run that held a registry gets an assessment
  *finding* (REP-007) -- not a gap, so no outcome moves.
- Since FUT-041 a paused replay's and a live run's payload record the same
  digest, and a restore holds the registry to it; `rerun_from_manifest` names
  the registry on the rerun's manifest exactly when the original's does
  (REP-008).
- Certification passes `REPRODUCIBLE` only for a rerun `REPRODUCED` with no
  manifest gap.

**Why it is a decision, not a fix.** Every option that makes the registry part
of a default manifest changes what an existing call returns for the same run:
a different `manifest_id`, or a certification that passed no longer passing.
Under `docs/api/PUBLIC_API.md` that is the maintainer's to authorise.

---

# Options

## A. Keep the registry opt-in (`instruments=True`), as now

- **Historical manifests.** Unchanged: every one verifies and keeps its id.
- **Certification.** Unchanged. A default manifest of a registry-holding run
  still passes `REPRODUCIBLE` if its rerun reproduces; the finding sits among
  its limitations.
- **Identities.** No `result_id`, `configuration_id` or `manifest_id` moves.
- **Migration.** None. A producer that wants the terms identified asks for
  them.
- **Reruns without registry information.** A default manifest records none,
  so a rerun under other terms whose record agrees is `REPRODUCED`, with the
  finding saying the terms were not compared.
- **Honest claim.** "Reproduced from the recorded inputs; the instrument terms
  were not among them unless named." True, and stated -- but the default is
  the weaker claim, and a reader who skips the limitations reads the stronger.

## B. Make the registry mandatory in every new manifest

`manifest_for_run` records `instruments_digest` by default for a run holding a
registry (with `instruments=False` to opt out, or none at all), and
`manifest_gaps` reports its absence.

- **Historical manifests.** Each still verifies -- the key renders the digest
  only when present -- but now carries a gap, so its assessment is
  `metadata_complete=False`.
- **Certification.** Every earlier `REPRODUCIBLE` certification of a
  registry-holding run that is *re-assessed* moves from `PASS` to
  `INSUFFICIENT_EVIDENCE`; already-written certificates are not rewritten, but
  re-certifying the same evidence gives another answer.
- **Identities.** `result_id` and `configuration_id` do not move. The default
  manifest of a registry-holding run gets a **new `manifest_id`** -- the same
  run under the same inputs now has two manifest identities, the old and the
  new, and nothing in either says which contract it was made under.
- **Migration.** An evidence store keyed by `manifest_id` holds both ids for
  one experiment; joining them needs the result and the inputs, and an old
  manifest cannot be upgraded without the registry, which it never recorded.
- **Reruns without registry information.** An old manifest cannot be compared
  on the registry: its rerun is assessed with a gap, never as complete.
- **Honest claim.** Stronger for new manifests; but the change is silent about
  which manifests were made under which rule, so two manifests of one run
  disagree with no field to explain it.

## C. A versioned manifest identity

Add `ReproducibilityManifest.identity_version` (`1` for every manifest made
today, rendered into the key only when it is not `1`), and define version `2`:
the default manifest of a run holding a registry records `instruments_digest`,
and a version-2 manifest lacking it is a gap. `manifest_for_run` makes
version 2 from the release that adopts it; version 1 remains readable,
verifiable and assessable by its own rules.

- **Historical manifests.** Every version-1 manifest verifies with its id and
  is assessed as before -- no new gap, no certification moved -- and says
  which contract it was made under.
- **Certification.** Version-1 evidence certifies as it did; version-2
  evidence certifies under the stronger rule. A certificate states the
  version it assessed.
- **Identities.** `result_id` and `configuration_id` do not move. A version-2
  manifest has a different `manifest_id` from a version-1 manifest of the
  same run *by design*, and the difference is named in the manifest itself.
- **Migration.** Stores keep their keys; a version-1 manifest may be
  re-issued as version 2 only by re-deriving it from the run with its
  registry (`manifest_for_run(..., instruments=True)` on the stored result),
  never by editing the old one; `assess_reproducibility` compares a version-1
  original with a version-2 rerun by the original's rules and says so.
- **Reruns without registry information.** A version-1 original records
  none: its rerun is assessed by version-1 rules, with REP-007's finding, and
  is never called verified on the registry.
- **Honest claim.** Each manifest says what it identifies; the strong claim is
  made only where the evidence supports it, and an old manifest is never
  presented as making it.

---

# Recommendation

**Option C**, adopted in a minor release as an addition: `identity_version`
appended last with `1` as its default, rendered only when not `1`, so every
existing manifest -- its id, its verification, its assessment, its
certification -- is exactly what it was, and the stronger contract is opt-in
by version until the maintainer makes version 2 the default. Making version 2
the default of `manifest_for_run` is the behaviour change, and belongs to a
release that may change a default (v5.0 under the current policy, or a minor
release with the change stated, if the maintainer judges a default's change
additive because no existing manifest's identity or assessment moves).

Option A is the right answer only if the maintainer prefers the registry to
remain the caller's to name; Option B is not recommended, because it changes
the meaning of existing evidence without recording that it did.

## Migration plan (Option C)

1. Add `identity_version: int = 1` to `ReproducibilityManifest`, appended last;
   `canonical_manifest_key` renders `identity.version=<n>` only when `n != 1`;
   `verify_manifest` and the manifest decoder read a missing field as `1`.
2. `manifest_for_run(identity_version=2)`: records the registry of a run that
   held one; refuses `instruments=False` with it.
3. `manifest_gaps`: under version 2, a run manifest of a registry-holding run
   without `instruments_digest` is a gap; under version 1, REP-007's finding,
   unchanged.
4. `assess_reproducibility(original, rerun)`: compares by the original's
   version; a version-2 rerun of a version-1 original is assessed by version-1
   rules and says so in its detail.
5. Certification states the identity version it assessed in its evidence.
6. Tests: every committed manifest fixture and certificate unchanged under
   version 1; version-2 manifests refusing a missing registry; a mixed-version
   rerun; the frozen v4.1.0 fixtures unchanged.
7. Then, in the release the maintainer chooses: `manifest_for_run` defaults to
   version 2, with the CHANGELOG stating that a default manifest of a
   registry-holding run gets a new `manifest_id`.

---

# The decision required

**Should a default reproducibility manifest identify the instrument registry,
and if so, under which identity contract and from which release?** Concretely:
choose A (no), or C (yes, versioned -- and the release whose `manifest_for_run`
defaults to version 2). B is listed for completeness and not recommended.

Until then the behaviour is A, with REP-007's finding stating the limitation
on every affected assessment.
