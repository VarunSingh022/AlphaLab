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
`tests/integration/test_walk_forward_over_backtests.py`).

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

# Consequences

- A strategy is validated as it would be run: by backtests restricted to each
  window, warmed up and never scored on what it could not yet have decided.
- Every selection a design makes is a recorded fact, and every trial behind it
  is counted.
- Promotion can require evidence that knows the size of the search it came from.
