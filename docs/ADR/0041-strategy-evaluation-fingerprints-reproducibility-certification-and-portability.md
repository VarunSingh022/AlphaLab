# ADR-0041: Strategy Evaluation — Fingerprints, Reproducibility, Certification and Portability

## Status

**Accepted and implemented in v3.6.0.**

The sixth capability release on the architecture frozen at v3.0.0. Like v3.5 it
deepens one package — `alphalab.lifecycle` — and adds none. No ownership
boundary moves, no snapshot schema changes, no durable state is added, and every
v3.1 through v3.5 invariant holds.

Depends on **ADR-0017** and **ADR-0036** for the derived dataset identity every
manifest names, **ADR-0022** for the seeded identifier stream a run's record
depends on, **ADR-0023** and **ADR-0030** for the rule that live objects are
recorded by type, **ADR-0029** for the byte-identical run record a result's
identity is the digest of, **ADR-0035** for the class registry a code identity
reads its entry point from, **ADR-0037** for the study identity a research
configuration names, **ADR-0039** for the market conventions portability
compares, and **ADR-0040** for the deployment specification, the runtime health
evaluator and the capability declarations all four capabilities are built on.

---

# Context

An application that evaluates strategies it did not write — to admit one, compare
two, or list them for somebody else — needs four things AlphaLab could not give
it at v3.5. The motivating consumer is a research marketplace such as RedDesk;
the contracts are generic, and nothing in the package knows who calls it.

**1. There was no identity for a strategy version.** AlphaLab identified almost
everything around a strategy and nothing identified the strategy itself:

| Identity | Identifies |
| --- | --- |
| `StrategyVersionRef` | `name@N` — registration order in one registry |
| `specification_id` | what a *deployment* needs |
| `evidence_id` | one *measurement* |
| `study_id` | one *experiment* |
| `StrategyRegistration.qualified_name` | a factory's *name*, never its code |

None said which code, with which dependencies, configured how, researched how,
on which engine.

**2. A result could not say everything it was made from.** A `BacktestResult`
carries its dataset, seed and configuration and nothing about the code, its
dependencies or the engine. `ValidationEvidence` cannot grow: its digest is
frozen (ADR-0017).

**3. Nothing stated a strategy's verifiable properties.** `evaluate_policy` gates
a promotion on metric thresholds; `validate_specification` checks a
specification's coherence; `evaluate_health` judges one observation. None says,
per property and with its evidence, whether a strategy is deterministic,
reproducible, inside its limits, and at what leverage, in which markets, on
which data, at what cost, and how it behaved.

**4. Nothing said whether a strategy could move.** AlphaLab's four environments
already share one strategy code path (ADR-0012), so a strategy's logic never
*has* to change between them — but nothing checked a target environment's
capabilities against what the strategy needs.

---

# Decision

## 1. Four modules in `alphalab.lifecycle`, and no package

`fingerprint`, `reproducibility`, `certification` and `portability`. The
reasons ADR-0040 decision 1 gave for widening this package hold unchanged and
are re-measured by `tests/regression/test_v36_invariants.py`: nothing imports
it, it already sits above the execution path, and it gains no durable state.

Two new package edges, both to packages that import nothing above themselves:
`alphalab.strategy` (a code identity reads its entry point from the class
registry) and `alphalab.conventions` (portability compares contract terms).

**One module-level rule constrained the placement.** `alphalab.api` is the only
module that imports both `alphalab.data` and `alphalab.research`
(`test_one_research_authority_per_concept.py`). The first draft broke it twice.
The fix is the pattern ADR-0016 decision 3 and v3.4's conventions package use: a
structural protocol instead of an import. `fingerprint` reaches a study through
`StudyIdentity` (one property, `study_id`); `reproducibility` reaches a dataset
through `VersionedDataset` (one method, `require_provenance`). `Dataset`,
`ResearchStudy` and `StudyResult` satisfy them as they stand.

## 2. The AlphaLab / application boundary

AlphaLab **provides** machine-verifiable contracts: fingerprints, reproducibility
manifests and their assessments, certification reports, portability reports —
each a frozen value with a derived identity and a deterministic JSON form
through `alphalab.persistence.serialize`.

An external application **consumes** them. Listing, publishing, purchase,
payment, subscription, ranking, search, seller and buyer accounts, licensing and
tenancy are all the application's. No v3.6 module defines any of them —
`test_v36_contains_no_marketplace_operation` sweeps names — and none names a
consumer: the raw-text vendor sweep now covers the four modules *including* the
word "marketplace".

## 3. A fingerprint is five inputs and nothing environmental

`derive_strategy_fingerprint` is `"<name>@<sha256>"` over
`canonical_fingerprint_key`: the line name and `StrategyDefinition.strategy_id`,
then **code** (`CodeIdentity`: package, version, entry point, source digest),
**dependencies** (`DependencyManifest`), **parameters**, **research
configuration** (`ResearchConfiguration`: declared settings and an optional
derived study id), and **engine** (`EngineIdentity`).

Decisions inside it:

- **The version number is not an input.** `@N` is registration order;
  identical content registered twice identifies alike.
- **The name and strategy id are.** Two strategies renamed are two strategies —
  the rule `derive_dataset_version` and `derive_study_id` follow — and the
  strategy id is what lets a manifest or a certification check that a run
  executed *this* strategy.
- **Nothing environmental enters it** — no mode, broker, venue, account,
  deployment or dataset — so it is the identity a strategy carries unchanged
  from research to live. Portability depends on that.
- **The engine enters it; a dataset's version does not include the engine.**
  Not a contradiction: a dataset version identifies *content*, so
  re-ingesting under a new AlphaLab must not re-identify it
  (`DatasetProvenance.engine_version` is recorded and not hashed). A strategy
  fingerprint identifies the *conditions* a strategy was researched under,
  and a result from another engine is a result of something else.
  `test_shared_names_stay_distinct.py` section 26 pins the pair.
- **Every caller-supplied string and number is rendered with `repr`.** A value
  can then never be read as a separator, a section header or an absent value —
  `version='none'` and `version=None` are different lines — without refusing any
  character a package name or a setting may contain. Open sections (pins,
  parameters, settings) are sorted.
- **A number's spelling is kept.** `10` and `10.0` identify differently, exactly
  as `specification_id_for` renders them. `differing_parameters` is the one
  comparison every v3.6 module uses, so a fingerprint, a certification and a
  portability check cannot disagree about whether two strategies are
  configured alike.
- **Parameters are derived** from the registered version by
  `fingerprint_for_version` — ADR-0017's lesson.
- **The source digest names files, never a machine.** `source_digest` hashes
  relative POSIX paths sorted, each file identified by
  `model_registry.artifact_store.compute_digest` — the identity AlphaLab
  already gives an artifact's bytes. Absolute paths, home directories, drive
  letters, backslashes and `.`/`..` segments are refused. Nothing reads a file.

## 4. Dependency identity is declared, and says how complete it is

AlphaLab has no resolver, reads no installed environment and declares no runtime
dependency of its own. So `DependencyManifest` is a declaration — typically a
lock file's contents — carrying a `DependencyCompleteness`:

| | |
| --- | --- |
| `EXACT_CLOSURE` | every package, directly or transitively, at one exact version. Empty means "nothing beyond AlphaLab and the standard library" |
| `DIRECT_ONLY` | direct dependencies pinned; the closure not recorded |
| `UNDECLARED` | nothing declared — not the same as "no dependencies" |

Pins are exact (`>=`, `~=`, `*` refused), names are identified in PEP 503 normal
form (the packaging specification's own equivalence), and an optional
`artifact_sha256` tells two builds under one version apart. The completeness is
inside the fingerprint, so a weaker claim is a different identity. There is no
function that snapshots "whatever is installed here": an approximate
environment listing called exact would be the false reproducibility claim this
release exists to prevent. `running_engine()` reads this interpreter's AlphaLab
version when a caller asks; nothing calls it on a caller's behalf.

## 5. A reproducibility manifest reads every identity from its owner

`ReproducibilityManifest` stores no result — there is no result store — and
renders nothing its owners already render:

| Field | Read from |
| --- | --- |
| dataset version, content digest | `Dataset.require_provenance()`, checked against the version the result names |
| strategy | a verified `StrategyFingerprint` whose `strategy_id` the run executed |
| configuration | a run's own `RunSnapshot` (`runtime.run_snapshot.capture`); a study's `canonical_study_key` |
| seed | `RunConfig.seed` / `ResearchStudy.seed`, with a `SeedRole` |
| result | a run's complete canonical record digest; a study's `result_id` |
| engine | supplied by the producer |

**A run's identity is its complete record.** `digest_run` hashes
`persistence.serialize(capture(run))` — the payload ADR-0029 proves
byte-identical across a process boundary — so a rerun that differs anywhere
produces a different identity. It costs one canonical serialization, linear in
the run (~50 µs per record measured).

**The seed is load-bearing, so an unseeded run is refused.** Its identifiers come
from `uuid4` and no rerun can reproduce its record, however deterministic its
economics. A study with no seed is recorded `SeedRole.ABSENT` — its absence
stated, never replaced.

**Live objects are the documented limit.** A run records its simulator, sizing
model, fill policy, instrument registry and strategy instances by type only
(ADR-0023), so their parameters are not in the recorded configuration. They
enter an identity only through the declared research settings, and a rerun is
the evidence: example 47 shows a changed simulator cost producing an identical
configuration id and a `DIVERGED` result.

**A dataset whose provenance records no bytes is refused.** v3.6 found that
`ingest_rows` records the source a caller supplies, as given (ADR-0036), and
that the examples and tests had been supplying an empty payload — so different
rows under one name derived *one* version. The data layer is unchanged (its
callers' behaviour is theirs); a manifest refuses to name such a dataset,
certification's `REQUIRED_DATA` does not count one as verifying a declared
version, and every v3.6 fixture records the rows' own bytes.

**Four questions, answered separately** by `assess_reproducibility`: identity
(does it recompute?), metadata completeness (`manifest_gaps` — code identified
by name rather than content, dependencies short of an exact closure), rerun
(`RerunOutcome`: `NOT_ATTEMPTED`, `REPRODUCED`, `DIVERGED`, and `INPUTS_DIFFER`
for a rerun of other inputs, which establishes nothing either way), and external
dependencies (`external_requirements`, never empty: AlphaLab holds no dataset
bytes and no strategy code; a live run's venue executions are listed as not
recreatable at all). A rerun is compared only when **both** manifests verify:
a rerun matching a record altered to match it, or an altered rerun, is not
evidence of reproduction, so `assess_reproducibility` refuses the comparison,
and certification reports the same two cases as `FAIL` and
`INSUFFICIENT_EVIDENCE` rather than raising.

## 6. Certification states properties, observes evidence, and has no score

`certify_strategy` returns eight `PropertyAssessment`s — `DETERMINISTIC`,
`REPRODUCIBLE`, `RISK_LIMITS`, `MAX_LEVERAGE`, `SUPPORTED_MARKETS`,
`REQUIRED_DATA`, `RESOURCE_USAGE`, `RUNTIME_BEHAVIOR` — each with a status, a
stable methodology sentence, machine-readable evidence, findings and
limitations. There is no overall score: a blended figure would decide how many
failures one pass outweighs, which is a policy, and it would read as a
measurement (the v3.2 position on overfitting).

**Four statuses.** `PASS` and `FAIL` for what the evidence establishes;
`NOT_ASSESSED` when nothing was supplied; `INSUFFICIENT_EVIDENCE` when what was
supplied cannot settle the question. Neither of the last two is ever `PASS`.

**Evidence is observed, never asserted.** `CertificationEvidence` carries runs,
repeated runs, datasets, manifests, runtime observations and resource
measurements — and no verdict. Every judgement is derived inside, through the
authority that owns it: `digest_run`, `assess_reproducibility`,
`evaluate_health`. A caller cannot supply a `HealthReport` that says `HEALTHY`,
and a supplied measurement claiming to be `COUNTED` is refused.

**No second risk authority.** Limits are the specification's `RiskLimits`, the
type the gate enforces. Drawdown and leverage at every recorded snapshot are
read *as the gate reads them* — a `RiskState` built for the snapshot, asked for
`current_drawdown_pct` and `current_leverage` — so there is no new formula to
drift. Equity at or below zero with exposure is reported unbounded rather than
as the zero `current_leverage` returns there.

**Run-based properties require a run of this strategy alone**, because a shared
book's leverage is not one strategy's. A run is matched to the fingerprint by
the strategy id it records; the parameters its instance was built with are
recorded by type only (ADR-0023), so every assessment read off runs states that
the runs' configuration was the supplier's record rather than an observation.

**Resource usage names its basis.** `COUNTED` figures are read off runs
(records, orders, fills) and reproduce exactly; `MEASURED` figures carry their
method and environment; `ESTIMATED` figures never meet a budget. Budgets are the
caller's; AlphaLab declares none.

**The report identity is deterministic** (`report_id`, SHA-256 over a `repr`
rendering of every assessment), and `certify_strategy` refuses a fingerprint or
specification that does not verify, a specification for another line or other
parameters, and a progression of another version.

## 7. Portability checks declared capabilities and adapts nothing

`TargetEnvironment` is a declaration whose capability fields are the owning
types: `ExecutionMode` for the kind, the v3.5 `BrokerCapabilities` and
`MarketAvailability`, v3.4's `MarketConvention` for contract terms. "Broker A"
and "broker B" are two declarations. `evaluate_portability` checks eight
requirements — `STRATEGY_LOGIC`, `EXECUTION`, `MARKET`, `DATA`, `CONTRACTS`,
`RUNTIME`, `RISK`, `CAPITAL` — through the functions that already own them
(`unmet_broker_requirements`, `unmet_market_requirements`), each `SATISFIED`,
`BLOCKED` (with blockers), `NOT_VERIFIED` (the environment did not declare what
the check needs) or `NOT_APPLICABLE` (a freshness budget in a backtest, a
research dataset in a live feed — ADR-0040 decision 4).

`PORTABLE` requires every applicable requirement verified and met; contract
terms declared for only some of the traded instruments leave `CONTRACTS`
unverified, because the others could change multiplier unnoticed. Nothing is
substituted: a strategy that needs stop orders is `NOT_PORTABLE` on a broker
without them, never converted to limits under its own identity. A specification
that configures different parameters from the fingerprint is blocked at
`STRATEGY_LOGIC` — the silent-mutation case.

---

# Consequences

**No durable state, no store, no workflow.** Every v3.6 value is produced by a
pure function and recorded by whoever asked; `LifecycleState` and
`LIFECYCLE_SNAPSHOT_SCHEMA` (2) are unchanged, and a regression test asserts the
absence of any capture, restore or schema constant. A registered
`StrategyVersion` stores no fingerprint: a manifest or a certification report is
where a fingerprint and a version are recorded together.

**Pre-existing defects found, stated, and left for a decision of their own.**
Building real evidence exposed three properties of the pre-trade gate that
predate this release and sit on the canonical path. Fixing them needs position
quantities in the persisted risk state — a pipeline snapshot schema decision
outside this release's scope — so v3.6 changes none of them and says so in every
`RISK_LIMITS` assessment:

- `check_position_limit` adds the asset's *notional* exposure
  (`ExposureStatus.asset_exposure` holds market values) to the order's
  *quantity* before comparing with `PositionLimit.max_quantity`;
- `RiskState.daily_loss` is never maintained on the execution path, so
  `DailyLossLimit` is never enforced;
- `ExposureLimit.max_net_exposure` is read by no pre-trade check.

The examples use a position cap the first cannot reach, with a comment pointing
here.

**A benchmark measurement not acted on.** Certification over a run costs ~5 µs
per recorded snapshot, most of it constructing the gate's `RiskState` per
reading. `dataclasses.replace` from one base state measured no better
(2.3–2.7 µs either way), and the construction is what guarantees the reading is
the gate's own, so it stays. Every path is linear, and
`tests/regression/test_v36_complexity.py` holds the ratios.

**What this release does not add:** no marketplace, listing, publishing,
payment, ranking or tenancy; no vendor adapter, broker client or credential; no
dependency resolver and no environment discovery; no automatic resource
measurement inside the library; no persistence of fingerprints, manifests or
reports; no re-execution inside the library — a rerun is the caller's.
