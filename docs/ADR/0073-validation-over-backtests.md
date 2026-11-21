# ADR-0073: Validation over Backtests -- Designs, Folds, a Declared Warm-up and Selection-Aware Evidence

## Status

**Accepted** (2026-11-15). It is the record the v4.3.0 plan needs before M5
(plan sections K.4 and K.3's statistics wiring, workstreams WS-24 and WS-06's
ledger part, engine N2). The plan's ADR programme assigns WS-24 no number, so it
is numbered on drafting (G0 item 16). The owner delegated the decision to its
milestone on 2026-11-10, and this record is that decision (`docs/work/v4.3.0`).

It depends on ADR-0054 (the selection statistics), ADR-0055 (capture), ADR-0058
(numerical identity) and ADR-0072 (the ledger, which it extends), and on the
research splitters it reuses unchanged (`research.walk_forward`,
`research.time_series_cv`, `research.purging`). It reverses no accepted ADR.

---

# Context

ADR-0072's experiment has one validation design: the full sample, with or
without a held-back lockbox. A search over it measures every candidate on the
same data it is selected by. AlphaLab's walk-forward and cross-validation
splitters partition timestamps, and `walk_forward_optimize` scores a caller's
objective over instants; none of them runs a strategy (GAP-02). So today:

- a walk-forward selection over backtests is written by hand, and parameters
  leak where it is: a loop that scores "the data up to now" includes the test
  window one off-by-one away;
- a strategy whose indicators need history either scores its warm-up -- days on
  which it could not yet decide -- or is cut short of it;
- nothing combines purged cross-validation's paths (CPCV) over backtests;
- the deflated Sharpe ratio, PBO and SPA read the ledger only by hand, and
  promotion has no evidence that knows a search was made.

# Decision

## 1. A design is part of the experiment

`ExperimentSpec.validation` is one of four designs, each stating every choice,
with no defaults:

| Design | States |
| --- | --- |
| `FullSample(lockbox)` | ADR-0072's: every candidate over the whole sample |
| `WalkForward(train, validation, test, mode, step, warm_up, refit, measure, purge, lockbox)` | Folds of train, validation and test, forward only; the selection rule and what the selection is refitted on |
| `CrossValidated(folds, method, warm_up, measure, purge, lockbox)` | Purged and embargoed time-series cross-validation |
| `CombinatorialPurged(groups, test_groups, warm_up, measure, purge, lockbox)` | CPCV: every choice of `test_groups` of `groups` |

Sizes are in the dataset's instants -- its distinct record timestamps before
the lockbox. `purge` states a label horizon in instants and an embargo in
seconds, from which `research.purging` builds its policy; `None` means none,
and a design whose name claims purging refuses it. The design's canonical form
enters the `experiment_id`.

## 2. Folds are research's

The folds are the research splitters' over the dataset's instants:
`walk_forward_splits`, `cross_validation_splits`, and a new
`combinatorial_purged_splits` in `research`, which also derives CPCV's paths.
The split mathematics stays research's authority; the ledger only runs what
it splits. The lockbox is held back before any split is drawn.

## 3. A trial is a candidate, a fold and a phase, over a window

| Phase | Design | The trial |
| --- | --- | --- |
| `SEARCH` | full sample | the whole sample (ADR-0072) |
| `SELECT` | walk-forward | the fold's validation window, every candidate |
| `REPORT` | walk-forward | the fold's test window, the candidate the fold selected |
| `VALIDATE` | cross-validated | the fold's validation window, every candidate |
| `TEST` | combinatorial | the split's test groups, every candidate |

A trial's seed and identity are ADR-0072's formulas over its fold and phase.
Its window -- the instants it is fitted on and the instants it is scored on --
follows from the design and the dataset, so it is fixed by the experiment's
identity, and a runner reads it from the `TrialSpec` to fit a model on what it
may see.

## 4. A run is restricted to its window, and its warm-up is never scored

The ledger runs each trial over the records from its warm-up's start to its
window's end, as it holds back a lockbox. The `warm_up` instants before the
window are run, so a strategy's state is formed, and are dropped from what the
trial scores: the trial's series is its window's returns alone. A warm-up may
reach into earlier windows' data, which the strategy would have seen; it never
reaches past the window's end.

## 5. The ledger selects, and every selection is recorded

- **Walk-forward.** After a fold's `SELECT` trials are recorded, the ledger
  selects by the design's `measure` over their scored series, ties to the
  first in the space's order, as `walk_forward_optimize` breaks them. A
  candidate whose trial did not complete is counted and never selected. The
  fold's `REPORT` trial is the selection on the test window, fitted per
  `refit`. A fold with no completed candidate selects nothing, and the record
  says so.
- **Cross-validated and combinatorial.** Every candidate is scored on every
  fold or path. A candidate's score is its mean over them, and the selection
  is the best by that score.

`run_experiment` runs the trials a design can name before any selection, then
those a selection names. A resume derives the second set again from the
recorded first, so it runs only what is missing, as before.

## 6. The record of a designed experiment

`ExperimentRecord` gains the selections (fold to candidate, or none) and, for
walk-forward, the identity of its out-of-sample series: the `REPORT` trials'
series in fold order. For a combinatorial design it gains each candidate's
paths. Each is written only when present, so a full-sample experiment's record,
and its pinned `experiment_record_id`, are ADR-0072's byte for byte.

## 7. The statistics read the ledger (WS-06)

`trial_matrix_of(ledger, record)` is the experiment's matrix of completed
trials. `assess_experiment(ledger, record, ...)` states every statistic's
choices and returns, together:

- PBO by CSCV over the matrix, with `S` required and bounded (ADR-0054);
- the deflated Sharpe ratio of the selection, with `N` the ledger's count of
  completed trials, or an explicit smaller `N` recorded with its reason;
- White's Reality Check and Hansen's SPA against a declared benchmark series.

No threshold decides anything (ADR-0054). The assessment has an identity over
the record's and every choice made.

## 8. Promotion's selection-aware evidence

`ValidationMethod` gains `EXPERIMENT`, appended, so every existing
`evidence_id` is unchanged, as `STUDY`'s addition left them.
`evidence_from_experiment` makes `EXPERIMENT` evidence from an assessed
experiment. Its metrics always carry the family count -- the trials counted --
beside the statistics. A policy check refuses `EXPERIMENT` evidence that does
not state its family count. So nothing is promoted on a selection that hides
the search behind it (M5's exit gate: the promotion refusal).

## 9. Leaks are mutants

The plan's four leaks are each a mutation of M5's series, and each must be
caught by a test:
- a purge boundary off by one;
- the embargo off;
- the warm-up scored;
- the lockbox reused.

## 10. The exit gate is S-13

An event-driven alternative-data strategy is validated walk-forward over
backtests in a ledger:
- observations delivered at their availability, with vintages (example 72's
  delivery);
- assessed with SPA and PBO;
- promoted only on selection-aware evidence.

A late revision changes no earlier decision, and the ledger counts every
event-window variant.

# Settled at implementation (2026-11-15)

Building M5 settled these points, each held by a test
(`tests/unit/lifecycle/test_validation_designs.py`,
`tests/unit/research/test_combinatorial_cv.py`,
`tests/integration/test_walk_forward_over_backtests.py`,
`tests/integration/test_cv_and_cpcv_over_backtests.py`,
`tests/integration/test_experiment_assessment.py`,
`tests/unit/lifecycle/test_assessment_members.py`,
`tests/integration/test_selection_aware_promotion.py`,
`tests/integration/test_event_driven_alt_data.py`).

1. **A dataset's identity carries its instants.** `DatasetIdentity` gains
   `instants`, its records' distinct timestamps in order: what a design splits.
   The digest already covers them, so they enter no identity, and a full-sample
   experiment's `experiment_id` is unchanged.
2. **The designs live in `lifecycle.designs`.** The module holds what a trial's
   window is, apart from the ledger that runs it. The lockbox and the
   full-sample design live there too, and `lifecycle.experiments` re-exports
   them.
3. **A window too near the sample's start takes its warm-up from itself.** A
   blocked scheme's first block has nothing before it. A window short of its
   warm-up runs its own first instants as warm-up and scores the rest. One
   left with fewer than two instants to score, a return's minimum, is refused.
4. **A CPCV split's groups are trials of their own.** A backtest over
   non-contiguous groups would carry its positions across the gaps. So each
   test group of each split is a trial: the fold is the split, and the phase
   names the group (`test:3`). The trial's identity is ADR-0072's formula.
5. **CPCV's split mathematics is research's.**
   `research.combinatorial_purged_splits` derives the splits and their paths.
   It requires a purge policy, as `CVMethod.PURGED` does, because the method's
   name claims purging.
6. **A window's series is the periods that end inside it.** The ledger keeps
   the capture's points from the instant just before the window, so the
   series' first return is the period ending at the window's first instant.
   Every period ending in the warm-up is dropped. Consecutive test windows'
   series join seamlessly, at the instant one ends and the next begins. A
   trial that went insolvent in its warm-up keeps a one-point series at that
   instant.
7. **A walk-forward record joins its selections' test windows.** When every
   fold's selection completed and the test windows tile the sample, the
   joined series is filed in the ledger, and its identity is the record's
   `out_of_sample`. A gap, a fold that selected nothing, or a selection that
   did not complete leaves it `None`.
8. **A design's folds share no one matrix.** Each fold's trials have their own
   windows, so a designed experiment's `matrix_id` is `None`. Its statistics
   are read per fold, or over the joined series (section 7).
9. **A worker finds a test-window trial by its identity.** A spawned worker
   cannot know which candidate the parent's ledger selected. It holds every
   test-window trial the experiment could name -- each fold and each candidate,
   `every_trial` -- and finds the one a request names, so a request still
   carries identities only.
10. **Cross-validated and combinatorial selections are the experiment's.**
    * **The selection.** A cross-validated or CPCV experiment selects once, by
      a candidate's mean score over every fold or path. Only a candidate that
      completed in all of them is eligible. The record's `selected` holds it.
    * **The paths.** A CPCV record's `paths` holds each candidate's paths: the
      series of its test groups, joined in group order, each a history of the
      whole sample. A path a trial left incomplete is `None`.
    * **The scores.** Fold and path scores use the design's measure, as the
      walk-forward selection does.
11. **The statistics read an experiment's family.** A record holds
    identities only, so `trial_matrix_of(spec, ledger, record, path=...)`
    takes the experiment's specification too. The family is one column per
    candidate that completed everywhere it was scored, and at least two:
    * **A full-sample experiment:** its completed trials. Its identity is the
      record's `matrix_id`.
    * **A walk-forward or cross-validated experiment:** each candidate's
      `SELECT` or `VALIDATE` series, joined over the folds. Windows that leave
      gaps or overlap join into no one history, and are refused.
    * **A CPCV experiment:** each candidate's series on one path, which the
      caller names.
    The assessment's `selected` is the family's best by the measure the
    assessment states, ties to the first: the candidate the deflated Sharpe
    ratio deflates.
12. **`N` is the family's members with a Sharpe ratio.** Section 7's "count of
    completed trials" is the family's columns for a full-sample experiment. A
    designed experiment's folds are the same candidates' trials again, not
    further independent ones, so `N` is the family's columns there too. A
    member whose returns have no dispersion has no Sharpe ratio: research's
    `per_period_sharpe` refuses it. It is left out of the trial Sharpe ratios
    and of `N` (`flat`). The family count, every trial the ledger recorded,
    stands beside it (`family`) for section 8. A researcher who has measured
    the search's effective size states a smaller `N` with its reason,
    `EffectiveTrials(count, reason)`. It is at most the members with a Sharpe
    ratio, and the assessment records both.
13. **A statistic never invents a member's dispersion.** Two statistics
    divide by a member's dispersion, and a member can lack one: a candidate
    that never trades has none, and a benchmark that is a candidate's own
    returns leaves that candidate none relative to it.
    * **The deflated Sharpe ratio** leaves the `flat` members out. It is
      `None` if the selection is one, or fewer than two members remain.
    * **SPA** studentizes each mean by its bootstrap deviation. It tests the
      members whose relative performance varies and names the rest
      (`constant`). Each of those has its constant as its verdict: it beats the
      benchmark by that much in every period, or by nothing. SPA is `None` if
      fewer than two members vary, research's least matrix.
    * **The Reality Check** divides by nothing and takes the whole family.
    Rejecting the assessment instead would refuse every search that holds a
    candidate that never trades, measured against cash. It would also refuse
    the benchmark section 7 names: a candidate's own returns.
14. **The assessment's identity.** It is digested under
    `alphalab.experiment_assessment.v1`, over:
    * the record, the path, the family matrix and the benchmark;
    * every choice: the blocks, measure, trim, replications, block length,
      seed, and a stated `N` with its reason;
    * the selection, the exact results (PBO's fraction and the Reality Check's
      and SPA's p-values) and the members each statistic left out.
    The deflated Sharpe ratio is libm-bound and enters no identity (ADR-0058).
    The reference assessments' identities are pinned, and CPython 3.12, 3.13
    and 3.14 agree on them. The pins were re-made once, before any release,
    when the stated `N` joined the identity at M5's close. Scheme v1 is the one
    v4.3.0 releases.
15. **Every policy refuses `EXPERIMENT` evidence without its family count.**
    * **The evidence.** `evidence_from_experiment(spec, record, assessment,
      subject, produced_at)` checks that the record is the experiment's and the
      assessment the record's. Its metrics, under the `experiment.` prefix, are:
      `trials` (the family count, `EXPERIMENT_FAMILY_METRIC`), `pbo`,
      `reality_check_p`, SPA's three p-values, the selection's Sharpe ratio,
      and the `N` its deflation took. Each is class 1 or 2 (ADR-0058), as a
      backtest's metrics are, so an evidence identity is the same on every
      host. The deflated Sharpe ratio is class 3, and is read from the
      assessment (below, Known limitations).
    * **What is derived.** The dataset is the experiment's `dataset_id`,
      spelled as a backtest over it spells it, and the seed is the
      experiment's. `source_id` is the assessment's identity.
    * **A statistic not computed.** One the assessment could not compute is
      left out, as an undefined backtest metric is: a threshold on it fails for
      want of it.
    * **The refusal.** `evaluate_policy` fails `EXPERIMENT` evidence that does
      not state the count as a whole number of trials, at least one. It does
      so under every policy, whatever its thresholds and its required method.
      Promotion runs through `evaluate_policy`, so nothing is promoted on such
      evidence.
    * **Why there, and not at construction.** Evidence is a record of what was
      measured, and a hand-built one is representable. A policy that names no
      family count still cannot pass it.
16. **The leaks are mutants of M5's series.** VD01 is the lockbox reused,
    split into folds. VD02 is the embargo off. VD03 and VD14 are the warm-up
    scored: a window's own warm-up, and a trial's series. VD10 is a purge
    boundary off by one: a CPCV test group's span taken from its second
    instant. Research's own boundary, `label_end >= start`, is v3.12's mutation
    already, so M5's is the boundary its own code draws. Each is caught by the
    test the progress register names, in M5's evidence.
17. **S-13 shows that a late revision changes no earlier decision, by running
    the experiment twice.** The two runs differ only in the late vintage: one
    flips the surprise's sign, one confirms it.
    * **Two experiments.** The information set enters the experiment's
      identity through its schedule, so the runs are different experiments.
      Their trials are matched by candidate, fold and phase.
    * **What must be equal.** Every decision, every period's return and every
      selection made before the revision's publication.
    * **What differs.** Only the selection of the fold whose window holds the
      revision.
    * **History.** A trial's run delivers what was published before it at its
      start. Whether that history is traded is the strategy's own rule, and
      S-13's strategy never trades it.
18. **WS-06's budget is a benchmark.** `benchmarks/benchmark_selection_statistics.py`
    runs PBO with `S = 16` over 100 trials of 2,520 periods, the oldest eight
    trimmed, against the plan's 60 seconds. It is judged by the stabilized
    method (TST-011), and the figures are recorded in the progress register.

# Compatibility

- Additive.
  - New designs, phases and functions.
  - A new `ValidationMethod` member, appended.
  - New fields on `ExperimentRecord`, written only when present.
- A full-sample experiment's identities and records are ADR-0072's: the
  reference experiment's pinned identities do not move.
- `walk_forward_optimize`, `parameter_sweep` and the research splitters are
  unchanged (R54-5).

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below it, as ADR-0072's were.

**Known limitations**

- **A policy cannot threshold the deflated Sharpe ratio.** (Ledger FUT-051.)
  The ratio runs through the normal distribution's CDF and quantile, which are
  host-libm arithmetic (class 3). `EXPERIMENT` evidence's identity covers its
  metrics, which ADR-0058 keeps class 1 or 2. So the ratio is reported in the
  assessment, beside the evidence that names it as `source_id`, and a gate on
  it is the researcher's to apply. The evidence carries every exact statistic,
  the selection's Sharpe ratio and its `N`. A class-2 normal CDF and quantile
  would bring the ratio into the evidence.

# Consequences

- A strategy is validated as it would be run: by backtests restricted to each
  window, warmed up and never scored on what it could not yet have decided.
- Every selection a design makes is a recorded fact, and every trial behind it
  is counted.
- Promotion can require evidence that knows the size of the search it came from.
