# ADR-0072: Experiments and the Trial Ledger -- Every Trial Counted, Identified and Resumable

## Status

**Accepted** (2026-11-15). It is the record the v4.3.0 plan needs before M4
(plan sections K.3 and O, workstream WS-05): the plan's ADR-0054B, numbered
0072 when drafted (G0 item 16). ADR-0054 split the draft ADR-0054 (revision 2)
in two; ADR-0054 is its statistics, and this is its experiments and ledger. It
applies the plan's revisions R54-1 to R54-7, R54-9 and R54-10 (R54-8 is
ADR-0054's), and the decisions the plan allocates here: D-54-1, D-54-3 and
D-54-5 to D-54-11, with the counts of D-54-4 and D-54-12, whose definitions are
ADR-0054's. The owner delegated the decision to its milestone on 2026-11-10, and
this record is that decision (`docs/work/v4.3.0`).

It depends on DET-007 (M0), ADR-0053's provenance (M1), ADR-0054's statistics
(M2), and ADR-0055's capture and its bridge (M3). It reverses no accepted ADR.

---

# Context

A researcher who tries many configurations and keeps the best is measuring the
search as much as the strategy, and ADR-0054's statistics measure it -- given
the population. Nothing yet records the population. Today:

- `research.overfitting.parameter_sweep` and
  `research.walk_forward_optimization.walk_forward_optimize` run candidates
  through an objective in memory and keep what it returns. A trial that raised
  is gone; a search stopped half way starts again; nothing says which
  candidates were tried.
- `research_assistant.generate_candidates` enumerates a space into candidates,
  and a sweep over them counts what it is given.
- `cloud_research.resolve_task` turns a dotted name into code to run
  (ledger BDY-028, the v4.3 review's SEC-001): a payload can name any
  importable callable.
- `experiment_tracking` records runs by name, now with derived identifiers
  (DET-007, D-54-7), but holds no specification, no outcome a statistic can
  count, and no series.
- A run whose equity falls below zero has no analytics (LIM-008), so a search
  that keeps only analysed runs silently drops its insolvent trials -- the ones
  a selection statistic most needs to count.

A search over backtests needs four things these do not give: a specification
that names everything a trial's result turns on; an identity for each trial
that the same specification always gives; an outcome for every trial,
including the ones that failed, refused or went insolvent; and a record of each
that is small enough to keep for thousands of trials and still lets a reader
check it.

# Decision

## 1. The ledger lives in `lifecycle.experiments` (D-54-1, D-54-9)

The ledger composes backtesting, capture, provenance, the evidence store and
the statistics, which only `lifecycle` may import together. Its executors use
the standard library's process spawning, so no package gains an edge. The
researcher's facade over it is `alphalab.lab` (ADR-0068), a separate top that
needs nothing to import `lifecycle` (R54-6); `lifecycle.experiments` is a
documented public module from M4.

## 2. An experiment states everything a trial turns on

`ExperimentSpec` has no defaults. It holds:

| Field | What it fixes |
| --- | --- |
| `name` | What the researcher calls it |
| `hypothesis` | What is being tested, as text, hashed into the identity (R54-9) |
| `strategy` | The strategy's fingerprint, `name@sha256` (ADR-0041) |
| `space` | The `ParameterSpace` of candidates, in its order |
| `configuration_type` | The strategy configuration type every candidate is typed as (R54-3, FUT-002) |
| `dataset` | The dataset's version and content hash, as a manifest records them |
| `schedule` | The `ScheduleBinding` (ADR-0053) |
| `instruments` | The registry digest, when a registry is given |
| `execution` | The execution assumptions' description (`describe()`, PER-008) |
| `capture` | The `CaptureSpec`: the extractor every trial is read by, and the trace (ADR-0055) |
| `retention` | The `RetentionPolicy` every trial runs under |
| `validation` | The `ValidationDesign`: in M4 the full sample, with or without a lockbox; M5 adds walk-forward, purged CV and CPCV (WS-24) |
| `seed` | The experiment's seed |
| `parent_id` | The experiment this one continues, or `None` |
| `limits` | The resources a trial may use: a time limit, and whether it may touch the network (it may not) |

A `TrialSpec` is one candidate rendered by the space, typed as the
experiment's `configuration_type`, with its fold and phase, and its
`trial_seed`: the existing `walk_forward_optimization._seed` formula over the
experiment's seed, the fold, the phase and the rendered candidate, so a sweep
and a ledgered experiment draw the same seeds.

## 3. Identities, each derived and none drawn (D-54-8)

| Identity | SHA-256 of | Scheme |
| --- | --- | --- |
| `experiment_id` | the specification's canonical key | `alphalab.experiment.v1` |
| `trial_id` | `experiment_id`, the rendered candidate, the fold and the phase | `alphalab.trial.v1` |
| `trial_record_id` | the trial record's canonical text | `alphalab.trial_record.v1` |
| `matrix_id` | the trial matrix's canonical text | `alphalab.trial_matrix.v1` |
| `experiment_record_id` | the experiment record's canonical text | `alphalab.experiment_record.v1` |

Beside them every trial record carries ADR-0055's `extractor_id`, `series_id`
and `trace_id`, ADR-0053's `provenance_id`, and the run's `result_id`.
`experiment_id` is a sixth content identity, distinct from `study_id`: a study
describes a research analysis, an experiment a search over backtests. Every
value an identity covers is class 1 or 2 under ADR-0058, so
`experiment_record_id` is the same on every host (plan O.3), and CI holds it on
Linux and macOS.

## 4. Every trial has an outcome, and five of six are recorded

| Outcome | Meaning | Recorded |
| --- | --- | --- |
| `COMPLETED` | The trial ran to its end, and its series was captured | Yes |
| `INSOLVENT` | Its equity reached zero or below; its series runs to that instant (LIM-008) | Yes |
| `STRATEGY_FAILED` | The researcher's strategy raised | Yes |
| `REFUSED` | AlphaLab refused the trial's inputs | Yes |
| `FAILED` | Anything else went wrong in the trial | Yes |
| `INTERRUPTED` | Infrastructure only: a worker died, a time limit, a cancellation. Retryable | No |

An interruption is not a property of the trial, so it writes nothing and a
resume runs the trial again. Every other outcome is a fact about the trial and
is counted: a selection statistic is given the trials that completed, and a
multiple-testing family every trial attempted (D-54-12).

## 5. A trial record holds digests and the series, never the result (R54-2)

`TrialRecord` holds `trial_id`, the outcome, `result_id`, `trace_id`,
`provenance_id`, `extractor_id`, `series_id` and the diagnostics -- for a
failure, its type and message. The series is filed beside it in the evidence
store under `series_id`. A `BacktestResult` is never kept: a ten-year trial's
is about 4.8 MB without retention, and a trial runs under the experiment's.

## 6. Executors run registry runners, and record in submission order (R54-7, D-54-11)

- A **runner** is a function registered by name in a `RunnerRegistry` the
  researcher builds. A trial names its runner; the name is looked up in the
  registry and never resolved to code (BDY-028). One registry serves the
  executors and `cloud_research`, whose `resolve_task` is replaced by it and
  kept until 5.0 (D-54-6).
- `SequentialExecutor` runs trials in this process.
- `SpawnExecutor` runs them in spawned processes. A worker receives the
  trial's JSON payload and its runner's name. Before it imports anything of the
  researcher's, it installs an audit hook that refuses the network -- the
  mechanism the installed smoke test uses -- so a runner's module that reaches
  for the network when it is imported is refused as surely as one that does so
  while it runs (M4's exit gate: spy-on-import refusal). The trial fails, and
  says so.
- Results are recorded in **submission order**, whatever order the workers
  finish in, so an experiment's record is the same under either executor (M4's
  exit gate: spawn = sequential).

## 7. Resume runs only what is missing, and refuses what changed

Trial records are filed in an `EvidenceStore` under their `trial_id`.
Resuming an experiment runs only the trials with no record. A trial that ran
again and produced another record is refused by the store's conflicting-value
check: the experiment is nondeterministic, and the ledger says where.

## 8. The lockbox is read once (R54-10)

A `ValidationDesign` may hold back a lockbox: an interval no trial sees. It is
read once, by a final evaluation the ledger records. A second read refuses,
unless a child experiment (`parent_id`) declares that it reads the lockbox
again, and why: every look at held-out data is counted.

## 9. The statistics read the ledger

`ExperimentRecord` holds the trial record ids in submission order, the counts
by outcome (`LedgerCount`), the `matrix_id` of the completed trials' matrix
(the M3 bridge), and the statistics over it: PBO, DSR with N the completed
trials' count, and the Reality Check and SPA against a declared benchmark.
An explicit smaller N is recorded with its reason. No threshold decides
anything (ADR-0054). Promotion's selection-aware `ValidationMethod`, which leaves every
`evidence_id` unchanged as `STUDY` did, and certification over experiment
records are M5's (WS-24).

## 10. What already exists stays, and consumes the ledger (R54-5)

| Existing code | In v4.3 |
| --- | --- |
| `parameter_sweep`, `walk_forward_optimize` | Unchanged, and pinned |
| `research_assistant` | `generate_candidates` feeds `TrialSpec`s |
| `cloud_research` | Its sweep runs through the ledger's executors and runner registry |
| `experiment_tracking` | The governance registry, linked to experiments by `experiment_id` in its `tags` (D-54-3; a typed field at 5.0) |
| `distributed`, `cluster_scheduler` | Folded into the executor: one executor path (GAP-31, M4) |

Nothing is removed before 5.0.

# Settled at implementation (2026-11-15)

Building M4 settled thirteen points, each held by a test
(`tests/integration/test_trial_ledger.py`, `tests/integration/test_spawned_trials.py`,
`tests/integration/test_cluster_executor.py`, `tests/integration/test_ledger_consumers.py`,
`tests/integration/test_ledger_reproduces.py`,
`tests/unit/cloud_research/test_registered_tasks.py`,
`tests/regression/test_one_executor_path.py`).

1. **A dataset's identity is what every trial can be held to.**
   `dataset_identity` is the dataset's id and `dataset_digest`, SHA-256 over its
   records in their canonical form: the fingerprint a paused replay holds its
   input to. A manifest's dataset version and content hash describe source
   bytes that a `MarketDataset` need not carry.
2. **Diagnostics enter no identity.** A record's `record_id`, the experiment's
   `experiment_record_id` and the nondeterminism check cover everything but the
   diagnostics. An interpreter's own error text differs between its versions,
   and the same trial failing the same way is one record on every host. The
   first record's diagnostics are kept.
3. **Which outcome a trial has.**
   * A runner that raises AlphaLab's own error is `REFUSED`, and any other
     exception is `FAILED`.
   * Inputs that are not the experiment's are `REFUSED`, saying which part.
   * A run that recorded strategy failures is `STRATEGY_FAILED`, with its
     result's identities, since it ran.
   * A capture refused at insolvency, or a series ended there, is `INSOLVENT`.
4. **The ledger sets what the experiment fixes.** It gives each run the trial's
   seed and the experiment's retention, whatever the runner built.
5. **`TrialLimits` holds a time limit only, and it times the trial.** A trial
   never touches the network (section 6), so there is nothing to choose. The
   limit is timed by the worker, from the moment it has loaded the experiment.
   * **Why the worker.** An interpreter's start and its imports are never the
     trial's. M4's first mutation run found the parent's timer counting them:
     under its four parallel suites, a worker's start alone outran a
     two-second limit, and every trial of the time-limit tests was
     interrupted, whatever the mutation.
   * **How it stops.** A trial still running at the limit ends its process
     (`_trial_worker.TIMED_OUT`), and is interrupted.
   * **The backstop.** The parent stops a worker that has not reported within
     the limit and a fixed start allowance, as dead.
6. **The statistics over the ledger are M5's.** The plan's milestone table
   places the ledger's DSR, SPA and PBO at M5 (WS-06, WS-24). M4's
   `ExperimentRecord` holds the counts and the completed trials' `matrix_id`,
   which those statistics read. Section 9 is read accordingly.
7. **A lockbox is identified by the data it holds back.** That is the dataset's
   digest and the lockbox's start. Its reads are counted under that identity
   whichever experiment made them, and a second read needs a child experiment
   stating `Lockbox.reread`.
8. **A spawned worker is a fresh interpreter AlphaLab starts, not a
   `multiprocessing` child.** A `multiprocessing` spawn imports the parent's
   main module before any of the worker's code runs, so a spy there would run
   before the hook. The worker is `python -m alphalab.lifecycle._trial_worker`:
   AlphaLab's own module runs first, refuses the network, and only then imports
   the experiment's definition.
   * **The definition.** An `ExperimentDefinition` of spec and registry, named
     in the executor's own configuration as `"module:NAME"`; a request carries
     identities only.
   * **When the definition does not load.** That is the researcher's code
     failing, and each trial is recorded `FAILED`, saying why.
   * **Interruptions.** A time limit or a dead worker interrupts the trial.
   * **Another experiment.** A definition that is not the submitted experiment
     is refused outright.

   Spawned and sequential runs of one experiment are equal record for record
   (`tests/integration/test_spawned_trials.py`).
9. **The one registry is `alphalab.common`'s.** `cloud_research` may not
   import `lifecycle`, which composes it, so D-54-11's one registry,
   `RunnerRegistry`, lives in `alphalab.common.registry` and
   `alphalab.lifecycle` re-exports it. It refuses with
   `AlphaLabRegistryError`.
   * **The cycle.** `run_cluster_cycle(..., registry=...)` looks each job's
     `task_path` up in it by name. A name it does not hold fails the job before
     anything is imported or submitted, and nothing is resolved as a path.
   * **The sweep.** The job keeps its `task_path`: a registered name when the
     cycle has a registry, a dotted path otherwise. So `submit_parameter_sweep`
     and `collect_sweep` run through the registry unchanged.
   * **Superseded.** Without a registry the cycle resolves the dotted path as
     it did; `resolve_task` stays until 5.0 (D-54-6).
10. **The cluster is one of the ledger's executors (GAP-31).**
    `ClusterExecutor` submits each trial to a `cloud_research` cluster as a
    registered task, and runs the cycle until every trial is done:
    * `distributed`'s job lifecycle tracks each trial;
    * the cycle's `assign` places it -- the cluster's own scheduler, or
      `cluster_scheduler`'s aging or affinity policy;
    * the task spawns the trial's worker, as `SpawnExecutor` does.

    The three orchestration layers are one executor path. `lifecycle` imports
    `cloud_research` and `distributed`, and `cluster_scheduler` joins without
    an edge: its policies are what `assign` is given.
    * **Cycles.** A cycle runs as many trials as the workers have room for and
      waits for the slowest, so trials run batch by batch, not as a continuous
      pool.
    * **An idle cluster.** A cycle runs every job it assigns, so the cluster
      starts with none queued or running.
    * **No placement.** A policy that places none of the trials left is refused,
      not waited on.
    * **Interruptions.** One fails the trial's job and records nothing.
    * **The cluster's state.** It stays the executor's; the ledger records the
      trials.
11. **The consumers reach the ledger without importing it.**
    * **The research assistant.** It may not import `lifecycle`, so its
      candidates reach the ledger through the one search space.
      `candidate_space` is the `ParameterSpace` of exactly the candidates
      `generate_candidates` made, of one template, in their order. An
      experiment over it runs trial `i` as candidate `i`, and a limited
      enumeration counts only what it generated.
    * **The tracker** (D-54-3). The link is a tag, `EXPERIMENT_TAG`
      (`alphalab.experiment_id`), namespaced so that it collides with no tag of
      the researcher's. `experiment_tags` cites the experiment and
      `tracked_runs` reads the citation back. Like every tag, it enters the
      tracker run's `run_id`.
12. **A record is reproduced by running its trial again** (GAP-01's acceptance).
    `rerun_trial` runs one recorded trial through the executor it is given and
    holds what it left to the record, as a `TrialRerun`. Through a
    `SpawnExecutor` that happens in a fresh interpreter. It files nothing: a
    rerun is a check, and the ledger would refuse a second record anyway. The
    record's `record_id` covers the run's identities, the series' and the
    outcome, so a match is the trial reproduced; an interrupted rerun says so.
13. **An experiment draws no identifier from the scope around it** (ADR-0029
    D6, ADR-0054's review). The ledger's own steps draw none, and the two
    things that did are now kept apart:
    * **The researcher's runner.** It mints from a stream seeded by its trial
      (the trial's formula, in a phase no trial has), so a runner mints the
      same identifiers in any process and in any order.
    * **A cluster's events.** They are stamped from a stream of their own. A
      scope is entered around each of the cluster's calls, never across a
      yield.

# Departures from the plan's text, and why

- **Statistics in identities are full precision, not rounded strings** (plan
  O.3, written before ADR-0058). ADR-0058 replaced H.1's rounding (PC-02): a
  value enters an identity when it is class 1 or 2, at full precision.
- **Adaptive search, CPCV and deep learning in a trial come later in the
  programme** (D-54-5 as the plan revised it): CPCV at M5, Bayesian search at
  M7, a deep-learning trial at M12.
- **`distributed` and `cluster_scheduler` are folded in by binding, not
  deprecated.** WS-05's scope reads "folding `distributed` and
  `cluster_scheduler` (deprecation notes)". The cluster executor runs on them:
  `distributed`'s job lifecycle and `cluster_scheduler`'s policies are the one
  path's scheduling layer, not a duplicate of the executor (point 10).
  Deprecating packages the executor imports would contradict the fold, and
  nothing in them is superseded. Whether either is ever removed is a 5.0
  decision of its own.
- **M4's 48 trials are one full-sample fold.** WS-05's end-to-end workflow is
  S-01's experiment core, 16 candidates in 3 folds. Folds are validation
  designs, which are M5's (WS-24). So M4's exit gate counts 48 candidates,
  their failures and insolvencies included, in one full-sample fold. M5 adds
  the folds.

# Compatibility

- Additive: a new module, `lifecycle.experiments`, the runner registry in
  `common.registry`, `research_assistant.candidate_space`, and two keyword-only
  parameters of `cloud_research.run_cluster_cycle`, `registry` and `assign`,
  whose defaults are its 4.2.1 behaviour. No existing function's behaviour or
  identity changes.
- `resolve_task` stays, documented as superseded, until 5.0.

# Known limitations

**Known limitations**

- **A worker's isolation is the host's.** The audit hook refuses the network
  inside Python, and a separate process alone is not a secure sandbox: code
  that escapes Python, a C extension, is the host's to contain (ADR-0056,
  principle 3).

# Consequences

- A search is a recorded population: every trial identified, every outcome
  counted, insolvent trials included, and every record checkable from its
  digests.
- A stopped search resumes where it stopped, and a nondeterministic one is
  caught by the record it could not reproduce.
- ADR-0054's statistics are fed from the ledger, with N counted, not chosen.
