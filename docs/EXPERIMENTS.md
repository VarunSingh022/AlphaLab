# Experiments and the Trial Ledger

A researcher who tries many configurations and keeps the best is measuring the
search as much as the strategy. The statistics that measure a search -- the
probability of backtest overfitting, the deflated Sharpe ratio, White's Reality
Check and Hansen's SPA -- all need its whole population: every trial, the
failed ones included. Since v4.3, `alphalab.lifecycle.experiments` records it
(ADR-0072). This guide shows how to state an experiment, run it, resume it,
reproduce a record and read held-back data once.

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
| `validation` | `FullSample(lockbox)`: the whole sample, with or without a held-back lockbox |
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

## What is next

The selection statistics over a ledgered experiment -- the deflated Sharpe ratio
with its trial count from `LedgerCount`, PBO, the Reality Check and SPA -- and
validation designs over backtests (walk-forward, cross-validation and
combinatorially purged cross-validation, with a declared warm-up) are the
programme's next milestone (M5, WS-24), with promotion's selection-aware
validation method.
