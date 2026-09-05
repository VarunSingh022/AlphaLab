# ADR-0013: The Model and Strategy Lifecycle

## Status

Accepted (v2.4.0).

Extends ADR-0009 (integrated path vs. standalone engines) for four packages:
`experiment_tracking`, `model_registry`, `deployment_manager` and
`research_assistant` are now composed by `alphalab.lifecycle`, in the way
ADR-0010 did for `replay`. Every other package listed there is unaffected.

---

# Context

Four packages each implemented one stage of a model/strategy lifecycle, and
none of them met.

| Package | Owned | Shipped in |
| --- | --- | --- |
| `experiment_tracking` | runs, parameters, metric histories, lineage | v1.46.0 |
| `model_registry` | model versions, stages, promotion, rollback | v2.0.0 |
| `research_assistant` | deterministic grid search, ranking, reports | v2.0.0 |
| `deployment_manager` | release packages, an environment ledger, rollback | v2.0.0 |

Between them there was exactly one link — `ModelVersion.run_id`, an
unvalidated `str | None` — and one convention: `ReleasePackage.components` was
documented as possibly holding `"momentum@3"`, a string nothing produced and
nothing parsed.

Reading the code rather than the docs turned up five gaps and one measurement.

**There was no strategy version.** `studio.StrategyDefinition.version` is a
free-form string. Nothing in the repository was a numbered, immutable record a
deployment could point at, or compare against the one before it. The four
things a lifecycle has to keep apart — the strategy line, a version of it, the
model version it runs, and a deployment of it — were not distinguishable.

**There was no validation evidence.** `ModelVersion.metrics` was
`Mapping[str, float]` captured at registration, with no statement of what
produced it, over what data, with what seed, or whether it met anything.

**Promotion had no preconditions.** `promote()` refused only a move to `NONE`
and a move to the stage the version was already in. Any registered version
could go straight to `PRODUCTION` with no evidence at all, and an `ARCHIVED`
version that had never been live could be sent there too — which made "roll
back to the previous version" indistinguishable from "promote something old".

**The deployment concept existed twice.** `model_registry.DeploymentMetadata`
was a hand-set blob claiming a version was deployed somewhere;
`deployment_manager`'s ledger recorded what actually was. Two sources of truth
for one question.

**Nothing was serialization-tested,** and `ModelVersion.model: object` made a
registry unserializable in general.

**Every write path was quadratic.** Measured, per doubling of the workload
(linear is ~2x): `log_metric` 3.4x/3.9x, `start_run` 3.1x/3.4x,
`register_model` 3.7x/3.6x, `promote` 3.8x/3.9x, `register_release` 3.0x/3.4x,
`deploy` 3.4x/3.7x. The same defect v2.1, v2.2 and v2.3 removed from the risk
engine, the OMS, the market layer and the broker layer.

---

# Decision

## 1. A composition package, not a fifth engine

`alphalab.lifecycle` composes the four packages. It defines no state any of
them already defines, and none of them changed shape to be composed. This is
the `alphalab.backtesting` pattern from v2.2: an integration package whose
value is the seam, not new machinery.

Each of the four remains usable on its own. `alphalab.lifecycle` is where they
are joined, and the only place that depends on all of them.

## 2. One stage vocabulary, reused

`ModelStage` (`NONE` → `STAGING` → `PRODUCTION` → `ARCHIVED`) stages strategy
versions too. A second enum spelling the same four stages for a different
artifact would be the duplication this ADR exists to remove, and the sequence
already maps onto the candidate → validated → deployed → retired progression a
lifecycle needs.

Its name is historical — it predates strategy versioning — and renaming it
would break `alphalab.model_registry`'s public API for no correctness gain.

It is deliberately unrelated to `strategy.LifecycleState`
(`CREATED` … `DISPOSED`), which tracks a strategy *instance running inside a
session*. A deployed strategy version is started and stopped many times
without its stage changing at all. Those are two axes, not two answers.

## 3. The transition table is declared, and gains two rules

`model_registry.stages.LEGAL_TRANSITIONS` states every legal move once.

| From | Can reach |
| --- | --- |
| `NONE` | `STAGING`, `PRODUCTION`, `ARCHIVED` |
| `STAGING` | `PRODUCTION`, `ARCHIVED` |
| `PRODUCTION` | `ARCHIVED` |
| `ARCHIVED` | `STAGING`, `PRODUCTION`\* |

Two moves that used to be allowed are not:

- **`PRODUCTION → STAGING`** is refused. A live version leaves production by
  being archived, or by being replaced by another promotion, which archives it.
  A quiet demotion leaves the model with nothing in production and no record
  that anything was taken down.
- **`ARCHIVED → PRODUCTION`** (\*) is refused unless the version is the one
  `previous_production_version` names. That is what rollback restores. Any
  other archived version reaching production is a resurrection whose retirement
  was undone with no evidence it was ever fit to be live.

`NONE → PRODUCTION` stays legal **at the registry level**. The registry is
mechanism: it records what happened and refuses what is incoherent. Requiring
evidence before something goes live is *policy*, and policy is layered above it
in `alphalab.lifecycle` — which is why the low-level API did not have to
break for the gate to exist.

## 4. Evidence is recorded, not computed

`ValidationEvidence` says what was measured, over what data, with what seed,
and where the full report is. It computes nothing. AlphaLab already had two
deterministic producers and both are read rather than reimplemented:
`analytics.PerformanceReport` (via `BacktestResult`) and
`research.ResearchScore`.

Its id is a SHA-256 digest of its own content — the construction
`deployment_manager.compute_checksum` already used for a release manifest, and
for the same reason. The same measurement identifies itself the same way
without coordination, and numbers edited after the fact stop verifying;
`evaluate_policy` checks that before it reads a single threshold.

A `ValidationPolicy` states thresholds in advance; an outcome names *every*
failed check, not the first, and a metric the policy asks for that the evidence
does not carry is a failure. An absent number is not a passing one.

**What a passing outcome claims** is exactly that the stated thresholds were
met by the recorded numbers. Not statistical significance, not out-of-sample
validity, not a correction for how many candidates were searched before this
one. Saying so in the code is part of the decision.

## 5. The deployment ledger is the only source of truth for what is live

`promote_strategy_version` refuses to reach `PRODUCTION` at all. A strategy
version goes live by being deployed and leaves by being replaced or rolled
back, so `deployment_manager`'s append-only ledger is the only thing that ever
puts one there.

`StrategyVersionRegistry` therefore carries **no** production index, unlike
`ModelRegistry`. "Which environments is this running in?" is answered by
reading the ledger, and a second copy on the registry side would be a second
thing to keep true.

`model_registry.DeploymentMetadata` stays — it is v2.3 public API — but the
integrated path *derives* it from the deployment that actually happened rather
than letting a caller assert one. That is what closes the duplication without
removing anything.

## 6. Artifacts are referenced; bytes are not stored

`ArtifactRef` records a location, a media type, a checksum and a size.
AlphaLab never reads, writes or hashes those bytes: there is no object store
here and this release does not pretend there is.

`ModelVersion.__serializable__` projects a version to its metadata plus that
reference, dropping the in-memory `model` object. An arbitrary object has no
deterministic JSON form, and stringifying it would produce a payload that reads
back as prose — the failure v2.1 removed from the append-only logs.

## 7. Persistent containers throughout

`PersistentMap` where a key is rewritten, `AppendOnlyLog` where a history only
grows — the containers v2.2 introduced. Three write paths also *scanned* what
they were writing to, which no container change fixes, so `ModelRegistry` gained
a `production` pointer and a `production_line`, and `DeploymentManager` gained
an `environments` index. They are maintained by their packages' own functions,
as `oms.book.OrderBook` maintains its asset and strategy indexes.

---

# Consequences

Benefits

- The flow the four packages were each written for one stage of now exists, and
  one end-to-end test walks it over the real engines.
- Invalid transitions fail explicitly and name what failed. A promotion records
  what it passed, not only that it passed.
- There is one answer to "what is running in this environment".
- Every stateful lifecycle write is linear, with a regression test on both
  growth axes.
- The whole lifecycle state serializes, round-trips, and reproduces byte for
  byte under a seed.

Trade-offs

- Two narrow breaking changes to `model_registry`: `PRODUCTION → STAGING` and
  unrestricted `ARCHIVED → PRODUCTION` are refused, and `versions[name]` is now
  keyed by version number rather than a positional tuple. Documented in
  `CHANGELOG.md`, consistent with the release policy in `ROADMAP.md`.
- `ExperimentRun.metrics` and the registry/manager collections are persistent
  containers rather than `dict`/`tuple`. They compare equal to what they
  replaced and hand-built mappings are converted, but a caller annotating the
  field type sees a different one.
- `alphalab.lifecycle` depends on four packages plus `backtesting` and
  `research` (for evidence extraction). It is the only package that does, which
  is the point of putting the coupling in one place.

---

# Alternatives Considered

**Put the gate in `model_registry.promote`.** Rejected: it would force the
registry to depend on an evidence type, and would break every existing caller
that promotes without one. The registry states what is coherent; the lifecycle
states what is justified.

**Give strategy versions their own stage enum** (`candidate` / `validated` /
`approved` / `deployed` / `retired`). Rejected: it is the same four stages
under different names, and archaeology says to prefer the repository's existing
terminology over a fresh sequence.

**Give `StrategyVersionRegistry` a production index like `ModelRegistry`'s.**
Rejected: it would be a second answer to "what is live", and keeping it true
across deploy, replace and rollback is exactly the class of bug this release
removes elsewhere. The registry has no such index, deliberately.

**Store model artifacts.** Rejected: it would require an object store this
repository does not have, and would be the one part of the release that could
not be tested. A reference is honest and sufficient.

**Wire the lifecycle into `ExecutionPipeline`.** Rejected as out of scope. A
deployment names what should run; running it is the execution path's job. Both
exist and the caller joins them.
