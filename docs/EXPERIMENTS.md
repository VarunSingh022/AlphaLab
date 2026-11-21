# Experiments and the Trial Ledger

A researcher who tries many configurations and keeps the best is measuring the
search as much as the strategy. The statistics that measure a search -- the
probability of backtest overfitting, the deflated Sharpe ratio, White's Reality
Check and Hansen's SPA -- all need its whole population: every trial, the
failed ones included. Since v4.3, `alphalab.lifecycle.experiments` records it
(ADR-0072). This guide shows how to state an experiment, run it, resume it,
reproduce a record and read held-back data once. Sections 8 to 11 cover
validating a strategy over backtests (ADR-0073): walk-forward,
cross-validation and CPCV; the search's statistics; and promotion on evidence
that states the search behind it. `SELECTION_BIAS.md` says how to read the
statistics.

`examples/80_a_ledgered_sweep.py` does all of it end to end:

```bash
python examples/80_a_ledgered_sweep.py
```

---

## 1. State the experiment

An `ExperimentSpec` has no defaults. Everything a trial's result turns on is
stated, and every trial is held to it:

| Field | What it states |
| --- | --- |
| `name`, `hypothesis` | What the experiment is and what it tests; the hypothesis's digest enters the identity |
| `strategy` | The strategy's fingerprint, `name@sha256` |
| `configuration_type`, `space` | The dataclass every candidate is typed as, and the candidates, in order |
| `dataset` | `dataset_identity(dataset)`: its id and the digest of its records |
| `schedule`, `instruments` | The schedule's binding and the registry's digest (or `None`) |
| `execution` | `execution_assumptions(pipeline, fill_policy)` |
| `capture`, `retention` | How each run's returns are captured (ADR-0055) and what each run keeps |
| `validation` | The design: `FullSample(lockbox)`, every candidate over the whole sample, or one of section 8's designs over backtests |
| `seed`, `parent_id`, `limits` | The experiment's seed, the experiment it continues, and a trial's time limit |

A candidate that is not of the configuration type -- a `2.0` where the type
declares an `int` -- is refused before any trial runs.

The research assistant's candidates become an experiment's trials through the
one search space: `candidate_space(generate_candidates(template, axes))` is the
space of exactly the candidates generated, in their order.

## 2. Register the runner

A runner builds one trial's inputs from its typed configuration: a
`TrialInputs` of the run's configuration, dataset, strategy, contexts, schedule
and observations. It is registered by name in a `RunnerRegistry`, built in code:

```python
registry = RunnerRegistry().register("ma-cross", build)
```

A name is looked up, never resolved to code: a name the registry does not hold
is refused before anything is imported (ledger BDY-028). The ledger then sets
what the experiment fixes -- the trial's seed and the experiment's retention --
and refuses inputs that are not the experiment's: another dataset, schedule,
registry or execution.

## 3. Run it

```python
record = run_experiment(spec, registry, "ma-cross", TrialLedger(store), executor)
```

The executor decides where trials run, and never what they record: the
experiment's record is the same under each.

| Executor | Where each trial runs |
| --- | --- |
| `SequentialExecutor()` | In this process, one after another |
| `SpawnExecutor(definition, workers, trials_per_worker)` | In interpreters AlphaLab spawns, which refuse the network before they import the researcher's code. `trials_per_worker=1` gives each trial a fresh interpreter, the most isolated; `None` keeps one per worker, its start paid once |
| `ClusterExecutor(definition, cluster, assign)` | As a job on a `cloud_research` cluster, placed by its scheduler or a `cluster_scheduler` policy, in a worker of its own |

A spawned worker imports the experiment from an `ExperimentDefinition` of the
spec and registry, defined at the top level of a module and named in code as
`"package.module:NAME"`. A request to a worker carries identities only.

**What it costs.** A worker's start is about half a second: an interpreter,
AlphaLab's imports and the experiment. A trial's request and answer cost a few
milliseconds. On one reused worker the executor costs about 2 percent of the
trials' time on deliberately short trials (`benchmarks/benchmark_experiments.py`).
With more workers, each adds a start.

**What isolates a trial.** The audit hook refuses `socket`, `ssl`,
`http.client` and `urllib` events. It is a guard, and a separate process is
not a sandbox (ledger BDY-029, `SECURITY.md`): isolating untrusted research
code is the host's.

## 4. Read the outcome

Every trial has an outcome, and five of the six are recorded:

| Outcome | Meaning | Recorded |
| --- | --- | --- |
| `COMPLETED` | The trial ran to its end and its series was captured | Yes |
| `INSOLVENT` | Its equity reached zero or below; a series, if captured, runs to that instant | Yes |
| `STRATEGY_FAILED` | The researcher's strategy raised | Yes |
| `REFUSED` | AlphaLab refused the trial's inputs | Yes |
| `FAILED` | The runner, or anything else, failed | Yes |
| *interrupted* | A time limit, a dead worker or a cancellation: infrastructure's | No |

`ExperimentRecord` holds the records' ids in submission order, the counts by
outcome (`LedgerCount`) and the completed trials' `matrix_id`, which the
selection statistics read. A `TrialRecord` holds the run's `result_id`,
`trace_id` and `provenance_id`, the capture's identity and the outcome -- never
the run's result. Its `record_id` covers everything but the diagnostics, so an
interpreter's own error wording moves nothing. An experiment draws no
identifier from the scope it is run in.

## 5. Resume, and reproduce

A resume runs only the trials with no record:

```python
record = run_experiment(spec, registry, "ma-cross", ledger, executor)  # again: runs what is missing
```

A trial that runs again and leaves another record is refused
(`NondeterministicTrialError`): the experiment is nondeterministic, and the
ledger says where. `rerun_trial` runs one recorded trial again through an
executor -- a `SpawnExecutor`, for a fresh interpreter -- and reports whether it
left the record the ledger holds. It files nothing.

## 6. The lockbox, once

`FullSample(Lockbox(start, None))` holds the data from `start` back from every
trial. `read_lockbox` evaluates one candidate over the whole sample, lockbox
included, once. A second read is refused, unless a child experiment
(`parent_id`) states why it reads it again (`Lockbox(start, reread)`), and that
read is counted too: every look at held-back data is.

## 7. Link the tracker

`experiment_tracking` stays the governance registry. A tracker run cites an
experiment by tag, `start_run(..., tags=experiment_tags(spec))`, and
`tracked_runs(tracker, spec.experiment_id)` finds the runs that cite it
(`EXPERIMENT_TAG`, until a typed field at 5.0).

## 8. Validate over backtests

A design is part of the experiment, and every choice it takes is stated. Sizes
are in the dataset's instants, its distinct record timestamps, before the
lockbox:

| Design | Its trials |
| --- | --- |
| `FullSample(lockbox)` | One a candidate, over the whole sample (`SEARCH`) |
| `WalkForward(train, validation, test, mode, step, warm_up, refit, measure, purge, lockbox)` | Every candidate on each fold's validation window (`SELECT`); then the fold's selection on its test window (`REPORT`) |
| `CrossValidated(folds, method, warm_up, measure, purge, lockbox)` | Every candidate on each fold's validation block (`VALIDATE`) |
| `CombinatorialPurged(groups, test_groups, warm_up, measure, purge, lockbox)` | Every candidate on each test group of each of CPCV's `C(groups, test_groups)` splits (`test:g`) |

The folds are research's: `walk_forward_splits`, `cross_validation_splits` and
`combinatorial_purged_splits`, unchanged.

**A trial's window.** Each trial carries a `TrialWindow`. Its `fit` holds the
instants a runner may fit a model on, and its `evaluate` the instants the trial
is scored on. Its `start` is where the run begins. A runner reads
`trial.window`, and fits only on `fit`.

**The warm-up.** The ledger runs each trial over the records from its
warm-up's start to its window's end, and nothing past it. The `warm_up`
instants before the window are run, so that a strategy's state forms, and are
never scored. The trial's series is the periods that end inside its window. A
window too near the sample's start takes the warm-up it lacks from its own
first instants. One left with fewer than two instants to score is refused.

**Purge and embargo.** `PurgeSpec(horizon, embargo_seconds)` builds research's
purge policy. Training instants whose label, `horizon` instants ahead, reaches a
scored window are purged. Those within the embargo after one are removed.
`CVMethod.PURGED` takes a zero embargo, `CVMethod.EMBARGOED` a positive one,
and CPCV requires a `PurgeSpec`: a design whose name claims purging refuses to
run without it.

**The lockbox.** A design's lockbox is held back before any fold is drawn, so
no fold's window reaches it. `read_lockbox` reads it once, under any design:
it evaluates one trial's candidate over the whole dataset.

**The selection.** The ledger selects, and records what it selected:

* **Walk-forward.** Each fold selects the best completed candidate by the
  design's `measure` over its validation series, ties to the first in the
  space's order. A candidate that did not complete is counted and never
  selected. The selection then runs on the fold's test window, fitted as
  `refit` says. `ExperimentRecord.selections` holds each fold's choice.
  `out_of_sample` is the test windows' series, joined, when they tile the
  sample.
* **Cross-validated and CPCV.** The selection is the candidate with the best
  mean score over every fold or path, among those that completed in all of
  them (`selected`). A CPCV record's `paths` holds each candidate's paths:
  its test groups' series joined, each a history of the whole sample.

A resume derives the test-window trials from the recorded selections again,
so it runs only what is missing. A designed experiment's record has no one
`matrix_id`, because its folds share no one set of periods.

**The leaks this rules out.** Each of the following is a mutation that M5's
tests catch (ADR-0073 section 9, the progress register's M5 evidence):

| Leak | What prevents it |
| --- | --- |
| Selecting on the test window | Walk-forward selects on validation windows; the test window waits on the selection |
| A purge boundary off by one | A label ending at a scored window's first instant is purged |
| The embargo off | Training after a scored window, within the embargo, is removed |
| The warm-up scored | A trial's series starts at its window; the warm-up is run and dropped |
| Running past the window | A trial's run ends at its window's last instant |
| The lockbox reused | It is held back before any fold, and read once |

## 9. Read the search's statistics

`trial_matrix_of(spec, ledger, record, path=...)` reads the family, one column
per candidate that completed everywhere it was scored, and at least two:

* a full-sample experiment's completed trials, whose identity is the record's
  `matrix_id`;
* a walk-forward or cross-validated experiment's fold series, joined, when the
  windows tile the sample;
* one CPCV path, which the caller names.

`assess_experiment` returns an `ExperimentAssessment` over it, every choice
required: PBO (`blocks`, `measure`, `trim`), the deflated Sharpe ratio of the
family's best, and the Reality Check and SPA against a declared `benchmark` --
zero for cash, or a candidate's own -- with the bootstrap's `replications`,
`mean_block_length` and `seed`. The deflation's `N` is the members with a
Sharpe ratio, unless `effective_trials` states a smaller one with its reason
(`EffectiveTrials(count, reason)`). `family` is the family count: every trial
the ledger recorded, failures included.

A member with no dispersion is never given one:

* the deflation leaves out the members whose returns have none (`flat`);
* SPA leaves out the members whose performance relative to the benchmark is
  constant (`constant`).

Both are named in the assessment. The Reality Check takes the whole family.
`assessment_id` digests the record, every choice, the exact results and the
members left out. The deflated Sharpe ratio is libm-bound and enters no
identity.

## 10. Promote on selection-aware evidence

`evidence_from_experiment(spec, record, assessment, subject, produced_at)`
records the assessment as `ValidationMethod.EXPERIMENT` evidence for the
strategy version that runs the selection. Its metrics carry the family count,
`EXPERIMENT_FAMILY_METRIC` (`"experiment.trials"`), beside `experiment.pbo`,
`experiment.reality_check_p`, SPA's three p-values, the selection's Sharpe
ratio and the `N` its deflation took. A statistic the assessment could not
compute is left out, so a threshold on it fails. The deflated Sharpe ratio is
read from the assessment: its normal tail is host-libm arithmetic, and an
evidence identity covers only values that are the same on every host
(ADR-0058, ledger FUT-051).

Every policy refuses `EXPERIMENT` evidence that does not state its family count
as a whole number of trials, whatever its thresholds. So nothing is promoted
on a selection that hides the search behind it:

```python
evidence = evidence_from_experiment(spec, record, assessment, str(ref), produced_at)
policy = ValidationPolicy(
    "selection-aware",
    (MetricThreshold("experiment.pbo", maximum=0.5),),
    required_method=ValidationMethod.EXPERIMENT,
)
state = promote_strategy_version(
    record_evidence(state, evidence),
    governance,
    ref.name,
    ref.version,
    policy,
    evidence.evidence_id,
    timestamp,
)
```

## 11. Alternative data

A runner gives observations to a trial through its schedule:

```python
TrialInputs(..., schedule=RunSchedule.of(observations=deliveries), observations=())
```

The same deliveries given both ways are refused. The experiment's `schedule` is
that schedule's binding, so the information set is part of the experiment's
identity. Each observation reaches the strategy at the instant it became
knowable, and never before. One published before a trial's run begins is
delivered at its start, and whether that history is traded is the strategy's
own rule. A revision is a later vintage, delivered when it was published.

`tests/integration/test_event_driven_alt_data.py` (S-13) validates an
event-driven strategy walk-forward this way, over eight event-window variants,
and holds a late revision to change no earlier decision.
