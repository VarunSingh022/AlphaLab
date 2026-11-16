# Econometrics: Time-Series Estimation with an Identity

`alphalab.econometrics` (v4.3, ADR-0074 and ADR-0075) is a pure library of
time-series tests and fits. It takes a researcher's series as finite floats
and returns recorded estimates. It reads no dataset, runs no strategy and never
touches the execution path. This guide covers what each estimator measures,
which choices it requires, how to read its result, and what it refuses.

Every result says what it was computed from and how. None of them says a
strategy will make money. A p-value that clears a threshold is a statement
about one test under its assumptions, and a search that ran many tests needs
the family correction described at the end.

---

## Three rules that hold everywhere

**Every choice is stated.** No argument that changes a result has a default.
These choices are always arguments:

- deterministic terms (`Deterministic.NONE`, `CONSTANT`, `CONSTANT_TREND`,
  `CONSTANT_QUADRATIC_TREND`);
- lags, a count (`FixedLags`) or a criterion with its maximum (`SelectedLags`);
- a long-run variance's bandwidth;
- the largest condition number a regression may have;
- an iterative fit's tolerance, iteration limit and start.

A call that leaves one out is a `TypeError`, not a silent default.

**What cannot be computed is refused.** A value that is not finite, a sample
too short, an ill-conditioned design, a start outside a model's region, or a
fit that does not converge each raise an error:

- `EconometricsInputError`;
- `IllConditionedError`;
- `ConvergenceError`, which names what was reached;
- `CollapsedStateError` for a regime model.

Nothing is floored, regularized or retried on the package's own initiative.

**A fit is an `Estimate`.** Every fit returns an
`alphalab.common.estimate.Estimate` (ADR-0058). It holds:

- `parameters` and `standard_errors`, by name;
- `method`, a versioned scheme such as
  `alphalab.econometrics.var.fit_var/ols.v1`;
- `data_id`, derived from the series' values, never typed;
- `settings`, every choice stated;
- `convergence`, for an iterative fit;
- `numerical_class`.

`estimate_id` is SHA-256 over all of it. It covers the fitted values only when
they are class 1 or 2: computed exactly, or by correctly rounded operations in
a fixed order, so every host gets the same bits. A class-3 value passes
through the host's `log` or `exp`. It is recorded beside the identity and
compared across hosts by a stated tolerance.

| Result | Class |
| --- | --- |
| Unit-root and Engle-Granger statistics; VAR, VECM and OU coefficients; Johansen's eigenvalues; Fama-MacBeth premiums; principal components | 2 |
| A Kalman filter's means and covariances for given parameters | 2 |
| MacKinnon's p-values; GARCH, state-space and regime fits; every likelihood | 3 |

---

## Unit roots

```python
from alphalab.econometrics import (
    Deterministic,
    LagCriterion,
    SchwertBandwidth,
    SelectedLags,
    augmented_dickey_fuller,
    kpss,
    phillips_perron,
)

adf = augmented_dickey_fuller(
    prices,
    deterministic=Deterministic.CONSTANT,
    lags=SelectedLags(LagCriterion.AIC, 8, None),
    maximum_condition=1e10,
)
adf.statistic, adf.p_value.value, adf.critical_values.values
```

- **Augmented Dickey-Fuller and Phillips-Perron** test the null of a unit
  root. A small p-value is evidence against a random walk.
- **KPSS** tests the opposite null, stationarity.
- **Running both.** A series both tests accept or both reject is the
  informative case, so read them together.

**Lags.** `SelectedLags` fits every candidate count on the sample the
maximum leaves, so the criteria compare like with like, then refits the
chosen count on all the data it leaves.

**Critical values and p-values.**

- The critical values are MacKinnon's (2010) response surfaces, generated
  from the paper.
- The p-values are MacKinnon's (1994) approximation.
- Beyond the table, a p-value is reported as a bound. `p_value.bound` says
  which side the true value lies on.

---

## Cointegration

### Engle and Granger's test: a pair and its hedge ratio

```python
from alphalab.econometrics import Deterministic, FixedLags, engle_granger

test = engle_granger(
    stock_a,
    [stock_b],
    deterministic=Deterministic.CONSTANT,
    lags=FixedLags(1),
    maximum_condition=1e10,
)
hedge_ratio = test.cointegrating.parameters["beta_1"]
test.statistic, test.p_value
```

The cointegrating regression is the combination tested. `beta_1` is the
pair's hedge ratio, and the order matters: `a` on `b` is not `b` on `a`, and
the two have different identities. The residuals are tested for a unit root
with MacKinnon's critical values for the number of series.

### Johansen's tests: a system's rank

`johansen(series, case=..., lags=FixedLags(k), tolerance=...,
max_iterations=..., maximum_condition=...)` returns, for each rank `r`:

- the trace statistic, which tests rank at most `r` against `p`;
- the maximum-eigenvalue statistic, which tests `r` against `r + 1`;
- the critical values and p-values of MacKinnon, Haug and Michelis (1996),
  decoded from the paper.

The case is one of Osterwald-Lenum's five (`JohansenCase`).

**The critical values are asymptotic.** In small samples the tests
over-reject. With a restricted trend, 150 observations read a 5 percent test
at 7 to 10 percent (FUT-053).

### The VECM of a stated rank

`fit_vecm(series, rank=..., case=..., lags=..., ...)` takes the relations from
Johansen's eigenvectors, normalized so the first `r` series' block is the
identity. It fits each equation's adjustment `alpha`, short-run `Gamma` and
deterministic terms by least squares, with standard errors.

---

## Mean reversion: the Ornstein-Uhlenbeck fit

```python
from alphalab.econometrics import fit_ornstein_uhlenbeck

fit = fit_ornstein_uhlenbeck(spread, interval=1.0, maximum_condition=1e10)
fit.process.parameters["half_life"], fit.process.standard_errors["half_life"]
```

`dX = theta (mu - X) dt + sigma dW` is fitted by its exact discretisation, an
AR(1) by least squares. The result holds two estimates:

- the regression;
- the process: `theta`, `mu`, `sigma` and the half-life `ln 2 / theta`, each
  with its delta-method standard error.

A coefficient outside `(0, 1)` measures no mean reversion and is refused:
there is no half-life to report. A half-life is quoted here only as an
estimate of this named model (OFE-007). The factor library's decay profile
stays the measurement it was.

---

## ARMA models

```python
from alphalab.econometrics import (
    ArmaMethod,
    ArmaStart,
    Deterministic,
    LagCriterion,
    fit_arma,
    select_arma,
)

fit = fit_arma(
    spread,
    order=(1, 1),
    deterministic=Deterministic.CONSTANT,
    method=ArmaMethod.EXACT_LIKELIHOOD,
    start=ArmaStart.ZEROS,
    tolerance=1e-8,
    max_iterations=200,
    step=1e-6,
)
fit.estimate.parameters["ar_1"], fit.estimate.standard_errors["ma_1"], fit.bic
```

There are two estimators.

- **Conditional least squares** takes the first `p` values as given and the
  errors before them as zero, then minimises the residuals' sum of squares. It
  is class 2: two hosts get the same bits.
- **The exact likelihood** filters every value from the model's stationary
  covariance. It is class 3.

Both move in partial autocorrelations, so every estimate is stationary and
invertible; a start outside that region is refused. `select_arma` chooses the
order by AIC or BIC over a grid. Under least squares, every candidate is
scored on the same residuals, and every host makes the same choice.

---

## Vector autoregressions

`fit_var(series, deterministic=..., lags=..., maximum_condition=...)` fits
each equation by least squares. The lags are a fixed count, or AIC or BIC
choosing among candidates fitted on one sample. The criterion's
log-determinant uses `Decimal` logarithms, so every host chooses the same
order. The residual covariance is over `T`.

---

## Volatility: GARCH, GJR and EGARCH

```python
from alphalab.econometrics import VolatilityModel, VolatilityStart, fit_volatility

fit = fit_volatility(
    returns,
    model=VolatilityModel.GJR,
    start=VolatilityStart.MOMENTS,
    tolerance=1e-6,
    max_iterations=200,
    step=1e-5,
)
fit.persistence, fit.variances[-1]
```

Each model is (1, 1) with a constant mean and Gaussian errors, fitted by
likelihood. BFGS moves in coordinates that map onto the admissible region
only, so a persistence at or above one cannot be reached. A start outside the
region is refused. The standard errors invert the likelihood's curvature. A
curvature that is not positive definite is refused, as no maximum to report.

---

## State space: the Kalman filter, the smoother and two models

**The filter and the smoother.** `kalman_filter(model, observations,
observation_matrices)` filters a `LinearGaussianModel`: `F`, `Q`, `R`, `m_0`
and `P_0`, with `H_t` given at every observation.

- The covariance update takes Joseph's form, which stays symmetric and
  positive semi-definite under rounding.
- `rts_smoother(model, filtered)` smooths back to the initial state.
- For given parameters every mean and covariance is class 2.

**The two models.**

- `fit_local_level(values, ...)` fits a level that wanders under noise.
- `fit_time_varying_beta(response, regressors, intercept=..., ...)` fits
  regression coefficients that each drift as a random walk: a beta, or a
  pair's hedge ratio, that moves.

Both maximise the exact diffuse likelihood. The first state is conditioned on
the first `k` observations, with no prior to choose.

**The intercept beside a price.** An intercept's drift and a beta's are hard
to tell apart, and the likelihood may not manage. When BFGS drives a variance
toward zero, the fit is refused, naming the variances reached. State that
intercept `Intercept.FIXED`.

```python
from alphalab.econometrics import Intercept, fit_time_varying_beta

fit = fit_time_varying_beta(
    stock_a,
    [stock_b],
    intercept=Intercept.FIXED,
    start={"beta_1_drift_variance": 1e-4, "observation_variance": 0.25},
    tolerance=1e-6,
    max_iterations=200,
    step=1e-5,
    maximum_condition=1e8,
)
hedge_ratios = [state[1] for state in fit.smoothed_states]
```

---

## Regimes: the hidden Markov model and the Markov-switching regression

ADR-0075 decides these, and they close OFE-010.

- `fit_gaussian_hmm(values, states=K, start=..., ...)` fits `K` states, each
  with its own mean and variance.
- `fit_markov_switching_regression(response, regressors, intercept=...,
  states=K, ...)` lets coefficients and variance switch with the state. A
  switching autoregression passes the response's own lags as regressors.

Both are fitted by EM, through Hamilton's filter and Kim's smoother.

```python
from alphalab.econometrics import QuantileStart, fit_gaussian_hmm

fit = fit_gaussian_hmm(
    returns,
    states=2,
    start=QuantileStart(0.95),
    tolerance=1e-8,
    max_iterations=500,
    step=1e-5,
)
fit.estimate.parameters["transition_2_2"], fit.smoothed[-1], fit.path[-20:]
```

**The labels.** The states are ordered by ascending mean; the regression's
are ordered by ascending variance. `path` is Viterbi's most likely sequence,
numbered as the estimate's states.

**A local maximum.** EM finds a local maximum, and the estimate records the
start that reached it. The package never restarts on its own, which would be
a search nobody counts. A researcher who fits from several starts makes
several estimates.

**Refusals.**

- A state whose variance collapses to zero raises `CollapsedStateError`, a
  `ConvergenceError` naming the state.
- A transition the data do not show leaves the information singular. The fit
  names it, to be stated as a structural zero (`0.0`) in an explicit start.
  EM keeps a structural zero, and it has no standard error.

---

## Factor research: Fama-MacBeth and principal components

These two live in `factor_library` and `analytics` (WS-25).

**Fama-MacBeth.** `factor_library.fama_macbeth(factors, returns,
intercept=..., minimum_assets=..., newey_west_lag=..., maximum_condition=...)`
regresses the forward returns across assets at each instant, then averages
the coefficients. Each factor's premium comes with Newey and West's standard
error at the stated lag. Use `horizon - 1` for overlapping forward returns.
Cross-sections too small or ill-conditioned are skipped and counted, by
reason.

**Principal components.** `analytics.principal_component_factors(covariance,
factors=k, ...)` keeps a positive definite covariance's `k` largest components
as a `FactorStructure`. Construction and the risk decomposition read it as any
factor model. A singular covariance is refused. Derive a recorded
regularization first (`with_ridge`, `with_diagonal_shrinkage`, `ledoit_wolf`).

---

## Screening many pairs: count the family

A pair screen that tries two hundred pairs runs two hundred tests. The best
of them clears 5 percent by luck alone about ten times over.
`research.cointegration_screen` runs Engle and Granger's test on every
candidate over one stated sample, and corrects the whole family by
`research.multiple_testing`:

```python
from alphalab.econometrics import Deterministic, FixedLags
from alphalab.research import Correction, cointegration_screen

screen = cointegration_screen(
    closes,
    candidate_pairs,
    start=0,
    stop=252,
    deterministic=Deterministic.CONSTANT,
    lags=FixedLags(1),
    maximum_condition=1e10,
    correction=Correction.HOLM,
    alpha=0.05,
)
screen.family.trials, [p.hypothesis for p in screen.selected]
```

**Every candidate is counted.** A pair the test refuses enters the family
with a p-value of one. So does a p-value bounded only from below. A bound
from above enters as its bound. Every value entered is at least the true
one, so the correction keeps its promise.

**Holm or Benjamini-Hochberg.** Holm bounds the chance of any false selection.
Benjamini-Hochberg bounds the expected share of them. `docs/SELECTION_BIAS.md`
and `research.multiple_testing`'s docstring say when each holds.

**Through the ledger.** A selected pair is a candidate for the next stage,
not a strategy. Run the candidates as a ledgered experiment
(`docs/EXPERIMENTS.md`), each trial fitting its pair on its own window, so
that a selected pair's evidence carries the whole search behind it.
`tests/integration/test_pairs_screen_through_the_ledger.py` does this for 200
pairs.
