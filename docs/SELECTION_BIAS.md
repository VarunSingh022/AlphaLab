# Reading the Selection Statistics

The best of many backtests looks better than the strategy behind it. Every
configuration tried is another draw, and keeping the best keeps its luck along
with its skill. AlphaLab's selection statistics (ADR-0054) measure how much of
a search's best is that luck, given the whole search. This guide says what
each one measures, what it assumes, and what it does not say.

None of them is a probability of losing money, and none says the selected
strategy will be profitable (risk RK-08). Each answers a narrower question,
stated below. A threshold on any of them is a policy's to state and the
researcher's to justify: AlphaLab sets none.

---

## The family comes first

Each statistic is computed over a **family**: the search's trials, side by
side over the same periods. Its answer is only as honest as the family is
complete.

- **Count every trial.** A configuration that failed, was refused or went
  insolvent was still tried. Dropping it makes the search look smaller than it
  was. The trial ledger records every trial (`docs/EXPERIMENTS.md`). The family
  count -- `ExperimentAssessment.family`, the evidence's `experiment.trials` --
  is every trial recorded, failures included.
- **Compare like with like.** A family is one extractor's series over one set
  of periods. `trial_matrix_of` refuses anything else, and refuses folds whose
  windows do not tile the sample.
- **State the selection measure.** The best by the mean and the best by the
  Sharpe ratio are different selections, with different biases.

## The probability of backtest overfitting (PBO)

**What it measures.** Combinatorially symmetric cross-validation (CSCV) cuts
the family's periods into `S` blocks. It takes every way of choosing half of
them as in sample, `C(S, S/2)` splits. In each split it finds the trial best in
sample, and asks where that trial ranks out of sample. PBO is the fraction of
splits in which it ranks at or below the median. It is exact: a fraction of a
count.

**How to read it.** PBO estimates how often *this selection procedure*, on this
family, picks a configuration that does no better than the median out of
sample.

- Near 0: the in-sample best tends to stay above the median.
- Near 0.5 or above: in-sample rank says little about out-of-sample rank.

**What it does not say.**

- It is not the probability that the selected strategy loses money.
- A PBO of 0 does not mean the strategy works. PBO ranks the trials against
  one another, and a family of uniformly poor configurations can have a PBO of
  0.
- It is not a property of the selected configuration alone, but of the whole
  search and the measure.

**Its choices.**

- `S`: even, at least 2, at most 20, at most the periods. Each block needs
  enough periods for its measure to mean something.
- `measure`.
- `trim`: which end's periods to drop when `T` is not a multiple of `S`. Dropping
  data is never done silently.

**What it assumes.** That the blocks are exchangeable: the same process
throughout the sample. A regime change inside the sample violates that.

## The deflated Sharpe ratio (DSR)

**What it measures.** The probabilistic Sharpe ratio (PSR) is the probability,
under its model, that the true Sharpe ratio exceeds a bar, given the estimate,
the sample length, and the returns' skewness and kurtosis. The DSR sets that bar
at the Sharpe ratio the best of `N` worthless trials would be expected to show,
given the variance of the trials' own Sharpe ratios. It is the selection's PSR
against what luck alone would have produced from a search of that size.

**How to read it.** A DSR near 1 says the selected Sharpe ratio is unlikely to
be the best of `N` trials with no skill, under the model's assumptions.

**What it does not say.**

- It is not the probability that the strategy is profitable.
- It is not the probability that the strategy will keep its Sharpe ratio.

**Its choices.** `N` is the effective number of *independent* trials. Correlated
configurations are fewer independent trials than their count.

- `assess_experiment` uses the family's members that have a Sharpe ratio,
  which bounds `N` from above when the trials are correlated: the conservative
  side.
- A researcher who has measured the search's effective size -- by clustering
  the trials' returns, say -- states it as `EffectiveTrials(count, reason)`.
  The assessment records the count with its reason, and its identity covers
  both.
- A member whose returns have no dispersion has no Sharpe ratio. It is left
  out, and named (`flat`).

**What it assumes.** Returns independent and identically distributed, with the
stated skewness and kurtosis. For serially dependent returns,
`autocorrelation_adjusted_probabilistic_sharpe_ratio` states its lag count.

**Its numerical class.** Its normal tail is host-libm arithmetic, so it is
reported and enters no identity (ADR-0058). Nor is it among selection-aware
evidence's metrics, which an evidence identity covers. A promotion policy
thresholds the statistics that are exact, and the deflated Sharpe ratio is
read from the assessment (ledger FUT-051).

## White's Reality Check and Hansen's SPA

**What they test.** Each tests one null hypothesis: no model in the family
beats the benchmark, once every model tried is allowed for. Each resamples the
periods by the stationary bootstrap and reports a p-value: the fraction of
bootstrap samples whose statistic reaches the observed one.

- **The Reality Check** takes the largest mean relative performance, under the
  least favourable null, every model's mean zero. Adding poor models to the
  family can only raise its p-value.
- **SPA** studentizes each model's mean by its bootstrap deviation. It
  recenters three ways and reports all three p-values: `p_lower`,
  `p_consistent` and `p_upper`, in that order of size. `p_consistent` is
  Hansen's recommended reading: a model far below the benchmark leaves it as
  it was.

**How to read them.** A small p-value says that relative performance this good
would be rare if no model beat the benchmark.

**What they do not say.**

- A p-value is not the probability that the null is true.
- It is not the probability that the best model beats the benchmark.
- It says nothing about how much the best model beats it by.

**The benchmark is a choice.** Zero is cash. A candidate's own returns, or the
market's, ask a different question. A member whose performance relative to the
benchmark is the same in every period cannot be studentized. SPA leaves it out
and names it (`constant`), because its constant is its own verdict. The Reality
Check keeps it.

**Their choices.** `replications`, `mean_block_length` -- the dependence the
bootstrap keeps -- and `seed`, all required.

**What they assume.** That relative performance is stationary and weakly
dependent (ledger LIM-011).

## The minimum track record length (MinTRL)

`minimum_track_record_length` is how many observations a Sharpe ratio needs
before its PSR reaches `1 - alpha` against a bar. It says how long to wait
before a Sharpe ratio is distinguishable from that bar. It is not a forecast of
when a strategy will pay.

## What none of them say

- How a strategy will perform out of the sample it was measured on.
- Anything the backtest did not model: costs, capacity or liquidity it left
  out are absent from every statistic computed over its returns.
- Anything across a regime change the sample does not contain.

## Report every choice

A statistic without its choices cannot be checked. `ExperimentAssessment`
states all of them:

- `assessment_id` digests the record, the family, the benchmark, every choice
  and the exact results;
- `ValidationMethod.EXPERIMENT` evidence carries the family count, and every
  policy refuses evidence that does not.

Read a lockbox once (`read_lockbox`): every look at held-back data is counted,
because a second look is a second trial.
