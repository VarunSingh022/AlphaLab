# ADR-0054: Selection Statistics -- Measuring a Search over Backtests, Purely

## Status

**Accepted** (2026-11-14). This is the selection-statistics half of a draft ADR
("Research Validation, Experiment Management and Reproducibility", revision 2,
sha256 `083b725b…`). The v4.3.0 plan reviewed it and split it in two (section O,
G0 item 7):

- this record, the plan's 0054A, holds the statistics;
- ADR-0072, the plan's 0054B, holds the experiment and trial ledger, accepted
  before M4.

The owner delegated the decision to its milestone on 2026-11-10, and this record
is that decision (`docs/work/v4.3.0`). It applies the plan's revision R54-8 and
its decisions D-54-2, D-54-4 and D-54-12, and reverses no accepted decision.
Every numerical choice follows ADR-0058.

---

# Context

A researcher who tries many configurations and keeps the best is measuring the
search as much as the strategy. AlphaLab already answers part of that question:

- `research.sharpe_inference` has the probabilistic and deflated Sharpe ratios
  and the expected maximum of N Sharpe ratios. DSR refuses fewer than two trial
  Sharpe ratios, and an N above their count.
- `research.multiple_testing` has Bonferroni, Holm, Benjamini–Hochberg and
  Benjamini–Yekutieli, each with its assumption stated.
- `research.perturbation` has a moving-block bootstrap with a fixed block
  length, returning positions.

What a search over backtests needs beyond these is not there:

- the probability that the selection procedure overfits (PBO, by
  combinatorially symmetric cross-validation);
- White's Reality Check and Hansen's test of superior predictive ability, which
  control for every model searched;
- the minimum track-record length;
- a bootstrap that keeps a series' dependence without a fixed block;
- a probabilistic Sharpe ratio that does not assume independent returns.

The research layer is measured, not opinionated. There is no overfit score, no
default `OverfittingPolicy`, and no default purge horizon. These statistics keep
that stance.

# Decision

## 1. Pure functions over matrices the caller holds

The statistics live in `alphalab.research` and import `common` only:

- `research.selection`: `TrialMatrix`, `probability_of_backtest_overfitting`
  (CSCV), `minimum_track_record_length`, and the autocorrelation-adjusted
  probabilistic Sharpe ratio;
- `research.data_snooping`: `reality_check` and
  `superior_predictive_ability`;
- `research.perturbation`: `stationary_bootstrap_indices`, beside
  `block_bootstrap_indices`; both return positions.

They never import `backtesting`, `lifecycle` or `persistence`. A statistic
computes over the numbers it is given and does not know where they came from.
That is what lets ADR-0072's ledger, and the bridge over captured backtests
(M3), feed them, and lets a researcher use them on any matrix they hold.
`research` still imports only what the measured table says.

## 2. The trial matrix

A `TrialMatrix` is T periods by N trials of one per-period measure, over **one
common set of instants**, each column labelled by its trial. It refuses ragged
columns, non-finite values, a repeated label and fewer than two trials.

The matrix states its population and nothing else. Which trials belong in it,
and whether a failed trial is excluded, is decided by whoever assembles it, and
is never assumed here (D-54-4). ADR-0072's ledger is what makes the population
trustworthy.

## 3. Five statistics and one adjustment (D-54-2)

Each method answers its own question, and has its own function and result type.

| Method | Question | Inputs | Required, bounded (R54-8) |
| --- | --- | --- | --- |
| **PBO by CSCV** (Bailey, Borwein, López de Prado and Zhu) | How likely is the selection procedure to choose a configuration that is below the median out of sample? A property of the procedure, not of a strategy | The full matrix; S, the number of contiguous blocks; the selection measure | S even, 2 ≤ S ≤ T and S ≤ 20 (C(20, 10) = 184,756 combinations); trimming of T to a multiple of S declared, not silent |
| **White's Reality Check** (2000) | Does the best of K models beat a benchmark, given that all K were searched? | T × K performance relative to the benchmark; replications B; mean block length q; seed | q ≥ 1, B ≥ 1 and the seed, all required |
| **Hansen's SPA** (2005) | The same null, studentised, with poor models' influence removed | As for RC | As RC |
| **MinTRL** | How many observations would make an observed Sharpe ratio significantly above a benchmark? | Per-period SR̂, skewness, non-excess kurtosis, SR\*, α | Refused for SR̂ ≤ SR\*; per-period ratios only |
| **Stationary bootstrap** (Politis and Romano 1994) | Resample a dependent series, keeping its dependence | T, mean block length q, B, seed | All required |
| **Autocorrelation-adjusted PSR** (Lo 2002; Opdyke 2007) | PSR without assuming independent returns | One series; SR\*; the number of autocorrelation lags | The lag count is required |

The existing PSR, DSR, expected maximum Sharpe ratio and multiple-testing
corrections are unchanged, and so is everything they compute.

## 4. Corrections the draft's review made exact

- **PBO's ties.** With discrete ranks, the selected configuration can sit
  exactly at the out-of-sample median when N is odd, so its logit is 0. PBO
  counts λ ≤ 0, as the paper integrates the logit density up to and including
  0. The convention is stated in the result and tested.
- **SPA's recentering.** The bootstrap statistic is Z\*ₖ = f\*ₖ − g(f̄ₖ), with
  three recenterings:
  - upper, gᵤ(x) = x: the least favourable case, the studentised RC;
  - consistent, g꜀(x) = x · 1{x ≥ −ω̂ₖ · √(2 log log T / T)};
  - lower, gₗ(x) = max(x, 0).

  So p_lower ≤ p_consistent ≤ p_upper always, and all three are reported.
- **Kurtosis.** PSR, its adjustment and MinTRL use the non-excess kurtosis,
  which is 3 for a normal series.

## 5. No thresholds, and DSR's N stated (D-54-4, D-54-12)

- **No thresholds.** No function decides "overfit" or "significant". A result
  is a number with its inputs and assumptions, and a policy that judges it is
  the caller's, as `OverfittingPolicy` is.
- **No failure policy by default (D-54-4).** A statistic counts only what it is
  given.
- **DSR's N (D-54-12).** The number of effective trials is the caller's
  statement, which DSR already requires. ADR-0072's ledger supplies a
  conservative upper bound: the trials completed. An explicit smaller number is
  recorded with its reason. Failed trials count in a multiple-testing family,
  where they were tests attempted. They do not count in DSR's N, whose variance
  is over trials that have a Sharpe ratio. Estimating N from correlation is a
  judgement, so it is reported, never used.

## 6. Host independence (ADR-0058)

- **Bootstrap positions are class 1.** They are drawn from a hash-counter
  stream: SHA-256 of the seed, the replication and the step, read as integers.
  Whether a block continues is an integer comparison, so the positions use no
  `log` and no `random` internals.
- **p-values are exact.** A p-value is a ratio of counts, held as an exact
  fraction until it is reported.
- **RC, SPA and PBO are class 2.** Their arithmetic is correctly rounded
  (`fsum`, `sqrt`, `+ - * /`), so they are host-independent. A pinned reference
  test on CI's Linux and macOS cells holds that.
- **PSR, DSR, MinTRL and the adjusted PSR are class 3.** They go through Φ and
  Φ⁻¹, so they are compared by tolerance and never enter an identity. Each
  result states its class.

# Known limitations

**Known limitations**

- **CSCV and the bootstraps assume stationarity.** CSCV treats blocks as
  exchangeable and ignores dependence across a block boundary. RC and SPA
  assume the performance series are stationary and weakly dependent. A regime
  change inside the sample violates both, and nothing here detects it. The
  result states the assumption and leaves the judgement to its reader.

# Consequences

New public names:

- `research.selection`: `TrialMatrix`, `PBOResult`,
  `probability_of_backtest_overfitting`, `MinTRLResult`,
  `minimum_track_record_length`,
  `autocorrelation_adjusted_probabilistic_sharpe_ratio`;
- `research.data_snooping`: `reality_check`, `RealityCheckResult`,
  `superior_predictive_ability`, `SPAResult`;
- `research.perturbation`: `stationary_bootstrap_indices`.

Every name is additive, and no identity, figure or signature moves.
