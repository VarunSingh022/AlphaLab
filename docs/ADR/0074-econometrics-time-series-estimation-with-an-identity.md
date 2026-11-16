# ADR-0074: Econometrics -- Time-Series Estimation with an Identity, and the Fitted Half-Life

## Status

**Accepted** (2026-11-16). It is the record the v4.3.0 plan needs before M6
(plan section H.3, workstream WS-07, engine N6, GAP-11). The plan's ADR
programme assigns econometrics no number, so it is numbered on drafting (G0
item 16).

It also decides, for one boundary, G0's D-V3, which the owner delegated to the
milestones on 2026-11-10 ("each reopening gets its own ADR before its
milestone"). That boundary is OFE-007, a fitted half-life, reopened in section
7. OFE-010, statistical regime models, is decided by ADR-0075.

It depends on:
- ADR-0058: the numerical classes and the identity of an estimate;
- ADR-0054: the selection statistics, and `research.multiple_testing`'s
  corrections;
- ADR-0072 and ADR-0073: the ledger, and validation over backtests.

It reverses no accepted ADR. WS-25's Fama–MacBeth and statistical factors are
ordinary additions to `factor_library` and `analytics` (plan WS-25 item 8), and
need no record of their own.

---

# Context

AlphaLab has no unit-root test, no cointegration test, no ARMA, VAR or GARCH
model and no state-space filter (GAP-11). A researcher who screens pairs for
statistical arbitrage, models volatility or estimates a time-varying beta
leaves the engine to do it. The result then has no identity, no stated
choices, and no place in the evidence the rest of AlphaLab keeps.

M2 delivered what such models need:
- least squares by Householder QR;
- a symmetric eigensystem;
- optimisers with required tolerances and limits;
- the t, χ², F and normal distributions;
- the `Estimate` type, whose identity covers the estimation problem always and
  the fitted values only when they are exact or correctly rounded
  (ADR-0058 sections 3 and 6).

Two boundaries were kept waiting for that identity:
- **OFE-007, a fitted half-life.** `factor_library.decay` reports a decay
  profile, and not a half-life, because a half-life assumes a functional form
  and is then "quoted as though it were measured".
- **OFE-010, statistical regime models.** ADR-0075 decides it.

# Decision

## 1. A pure package: `alphalab.econometrics`

A new package imports `alphalab.common` and the standard library, and nothing
else. It takes the caller's series as finite floats, and never reads a dataset,
runs a strategy or touches the execution path (AC-11). Its groups:

| Module | Methods |
| --- | --- |
| `series` | A series' identity and validation |
| `critical_values` | MacKinnon's response surfaces and p-value approximations, and the other published tables, each with its source |
| `unit_root` | Augmented Dickey–Fuller, KPSS, Phillips–Perron |
| `cointegration` | Engle–Granger; Johansen's trace and maximum-eigenvalue tests; the VECM |
| `arma` | ARMA by conditional least squares or Gaussian likelihood; information criteria |
| `var` | VAR by least squares; lag selection |
| `volatility` | GARCH(1, 1), GJR and EGARCH by Gaussian quasi-likelihood |
| `state_space` | The Kalman filter and RTS smoother; local-level and time-varying-beta models |
| `mean_reversion` | The Ornstein–Uhlenbeck fit and its half-life (section 7) |

What consumes it lives above it. `research` may import `econometrics`, as it
imports `common`, but `econometrics` imports nothing of `research`'s.

## 2. A fit is an `Estimate`; a test is a result with its regression

- **A fit** returns an `Estimate` (ADR-0058 section 6):
  - its parameters and standard errors;
  - its method under a versioned scheme of its own,
    `"alphalab.econometrics.<module>.<method>/<variant>.v1"`;
  - the data's identity, every choice it was given and, when it iterates, its
    convergence report.
- **A test** returns a result type of its own, holding:
  - the statistic and the lags it used;
  - the critical values, with their source and the sample size they are for;
  - the p-value, with how it was computed;
  - the `Estimate` of the regression the statistic came from.

## 3. The data's identity is derived, never typed

`series_identity(values)` is SHA-256 over the values' shortest `repr`s under a
scheme of its own, and a system's identity covers each series in order. A
caller cannot name the data an estimate was fitted on, as ADR-0017 holds for
evidence: an identity typed by hand is only as true as the typing. A caller
who holds a richer identity -- a capture's `series_id`, a dataset version --
keeps the correspondence, which the values themselves fix.

## 4. Every choice is stated

Nothing in the package has a default that changes a result:

- **Deterministic terms:** none, a constant, a constant and a trend, or a
  constant and a quadratic trend, named by an enum.
- **Lags:** a fixed count, or a criterion (AIC, BIC, or the t-statistic of the
  last lag) with its maximum.
- **Long-run variance:** a kernel and a bandwidth, the bandwidth a count or a
  named rule with its constant.
- **Iterative fits:** the optimiser, tolerance, limit and starting point. The
  start is either explicit values or a named rule that computes them from the
  data.

The package refuses rather than guesses:
- a series too short for the statistic it is asked for;
- a value that is not finite;
- a regression the least-squares solver finds ill-conditioned;
- a fit that does not converge, which raises `ConvergenceError` with what it
  reached;
- parameters outside the model's admissible region. GARCH's persistence is
  below one by the parameterisation, and stated as such.

## 5. Numerical classes (ADR-0058)

| Result | Class | In the identity? |
| --- | --- | --- |
| ADF, Phillips–Perron and KPSS statistics, Engle–Granger's regressions, VAR coefficients, the AR(1) beneath an OU fit | 2: least squares by Householder QR, sums and square roots | Yes |
| Response-surface critical values: polynomials in `1 / T` | 2 | Yes |
| Johansen's eigenvalues, by M2's symmetric eigensystem | 2 | Yes |
| Johansen's statistics, `-T sum ln(1 - λ)` | 3: `log` | Beside |
| p-values through the normal distribution (MacKinnon's approximation) | 3 | Beside |
| ARMA by conditional least squares, minimised by a class-2 optimiser | 2 | Yes |
| ARMA, GARCH, GJR and EGARCH by likelihood | 3 | Beside |
| A Kalman filter with given parameters | 2 | Yes |
| Its likelihood, and parameters maximising it | 3 | Beside |
| An OU fit's θ, μ and σ, and its half-life | 3: `log` | Beside |

A class-3 value is recorded at full precision beside its identity, and compared
across hosts by the tolerance its error analysis states (ADR-0058 section 4).

## 6. Critical values and p-values are published constants, held to their tables

- **MacKinnon (2010) response surfaces.** The critical values of the unit-root
  and Engle–Granger tests at 1, 5 and 10 percent:
  `c(T) = β∞ + β1 / T + β2 / T² + β3 / T³`, for each deterministic case and
  each number of variables. Tests reproduce the paper's published values to
  its three decimals.
- **MacKinnon (1994) p-values.** The approximate asymptotic distribution
  functions, which approximate p-values through the normal distribution
  within the published range of the statistic. Beyond that range the p-value
  is the range's bound, and the result says so.
- **KPSS (1992) critical values.** Kwiatkowski, Phillips, Schmidt and Shin's
  table. A p-value is interpolated within the table only; beyond it, a bound
  is stated, not extrapolated.
- **Johansen's tests.** Published asymptotic critical values, with their
  source named beside the table (settled at implementation).

These are statistical constants, like a day count's definition. They are not
reference data, and BDY-013's packs do not carry them.

## 7. OFE-007 reopened: a fitted half-life is an estimate of a named model

`mean_reversion.fit_ornstein_uhlenbeck(values, interval)` fits the
Ornstein–Uhlenbeck process by its exact discretisation:
`X_{t+Δ} = μ(1 − b) + b X_t + ε`, with `b = e^{−θΔ}`.

- **Two estimates.** It returns the AR(1) regression's estimate (class 2, in
  its identity) and the continuous-time estimate derived from it (class 3).
  The latter holds θ, μ, σ and the half-life `ln 2 / θ`, each with a
  delta-method standard error.
- **No mean reversion, no half-life.** It refuses a coefficient `b` outside
  `(0, 1)`: no mean reversion is measured, so no half-life exists to report.
- **The profile stays the measurement.** `factor_library.decay` is unchanged:
  the profile and its first negative horizon remain the measurement of a
  factor's decay. A half-life is now quoted only as an estimate of a stated
  model, with its standard error, its identity and the model's name, which is
  what OFE-007's boundary asked for.

The ledger records OFE-007 as reopened under this record, and closed when its
estimator is delivered.

## 8. The screening stage of S-02

M6's exit gate includes S-02's screening stage: 200 candidate pairs screened
with a family correction. It holds as follows.

- **Engle–Granger with MacKinnon's p-values.** The screen's p-values match the
  published tables.
- **The screen.** `research.cointegration_screen` tests every candidate pair
  on a stated sample, applies a stated correction
  (`research.multiple_testing`), and counts every pair. It returns every
  pair's result. Its identity covers the pairs, the series' identities, the
  choices and the class-2 statistics.
- **Through the ledger.** The pairs are a ledgered experiment's candidates
  (ADR-0072). Each trial's runner fits the pair's hedge ratio and test on its
  own window only (ADR-0073), so every pair tried is counted, and a selected
  pair's evidence carries the search behind it.
- **Out of M6.** The combo order the selected pair trades as is M8's (ADR-0062).

## 9. Budgets and validation

- **Recovery.** Every model recovers known parameters from data it simulated,
  within tolerances its own standard errors justify.
- **Refusals.** Every refusal of section 4 is tested.
- **Performance.** A GARCH fit on 2,520 points takes at most 5 seconds, and
  Johansen with five series at most 2, in pure Python (plan WS-07 item 15),
  each judged by a benchmark.

# Settled at implementation (2026-11-16)

Building M6 settled these points, each held by a test
(`tests/unit/econometrics/`).

1. **MacKinnon (2010)'s tables are the paper's.**
   * **Generated, not typed.** The response surfaces were extracted from the
     paper's text and generated into the module, never typed by hand.
   * **Checked twice.** They were checked row by row against an independent,
     widely used transcription. That transcription differs from the paper in
     four rows of the constant case: a β2 printed -33.527 for -22.527 (N = 2,
     1 percent); β1 a digit off twice (N = 3 and 5, 5 percent); and N = 7's
     10 percent row repeating N = 6's β2 and β3.
   * **Pinned.** The tests pin those rows to the paper, the paper's worked
     example (-4.89111) and the textbook asymptotic values.
2. **MacKinnon (1994)'s coefficients are held by their own consistency.** The
   paper is not freely available. At the 2010 asymptotic critical values, its
   approximations give 0.01, 0.05 and 0.10 within 0.6 percent, for every case
   and every `N` from 1 to 6. Its two branches meet at `τ*` within 0.004. In
   the extreme right tail (p within 3e-6 of 1) the quadratic-trend
   approximation for `N = 5` is not monotone; it is applied as published.
3. **`T` is the test regression's observations.** A Dickey-Fuller regression of
   fewer than 20 is refused: MacKinnon's surfaces were fitted from samples of 20
   or more, and a smaller sample would read them where they were never estimated.
4. **The trend is `t / T`.** Rescaling a deterministic term changes only its own
   coefficient, never a statistic, and keeps the design well conditioned.
5. **Lags are selected on one sample, the same on every host.**
   * **One sample.** Every candidate is fitted on the sample the maximum lag
     leaves, so their criteria compare. The chosen lag is then fitted on all
     the data it leaves.
   * **Comparisons.** AIC and BIC compare by `Decimal`'s correctly rounded
     logarithm.
   * **The t-statistic rule's threshold** is stated, with no conventional
     default.
6. **The Schwert bandwidth is exact.** `⌊c (T / 100)^{1/4}⌋` is the largest `m`
   with `100 m⁴ <= c⁴ T`, by integer arithmetic.
7. **A p-value beyond its table is a bound.** It is reported at the table's or
   approximation's end, with `PBound` saying on which side the true value lies,
   and is never extrapolated.
8. **The largest condition number is an argument.** Every regression takes it,
   as M2's least squares does. Classical standard errors come from `X'X`,
   summed exactly and inverted through its Cholesky factor (class 2).
9. **Engle and Granger's test has a constant at least.**
   * **Two regressions.** The cointegrating regression puts the dependent
     series on its deterministic terms and one to eleven other series. The
     residuals' Dickey-Fuller regression has no deterministic terms, because
     the first regression already removed them.
   * **The tables.** MacKinnon tabulates the residual test by the number of
     series in the first regression and by its deterministic terms. He
     publishes the no-constant case for one series only, so a cointegrating
     regression without a constant is refused.
   * **The p-value.** MacKinnon (1994)'s approximation covers up to six
     series. From seven to twelve, the critical values stand alone and the
     p-value is `None`.
   * **Identities.** The cointegrating regression's identity is the series',
     in order: y on x is not x on y. The residual regression's identity is the
     residuals'.
   * **A dependent regressor is refused as ill-conditioned.** Building this
     test found that M2's least squares raised a non-convergence for a
     regressor beside a multiple of itself, where its contract promises
     `IllConditionedError`. That was fixed in M2's one-sided Jacobi, with every
     converging input unchanged (ledger NUM-018).
10. **The same bits on every host.** Every statistic above is class 2, and the
    tests pin the statistics, hedge ratios, critical values and estimate
    identities, computed identically under CPython 3.12, 3.13 and 3.14. The
    pinned series are built by integer arithmetic and correctly rounded
    operations, never by a seeded `gauss`, whose logarithm and cosine are the
    host's (`tests/regression/test_econometrics_values_are_pinned.py`).
    MacKinnon's p-values pass through the normal distribution (class 3), so
    they are held by a tolerance instead.
11. **Johansen's tables are the paper's, held three ways.**
    * **The source.** MacKinnon, Haug and Michelis's working paper (York
      University, 1996, the version of their 1999 Journal of Applied
      Econometrics article that is freely available). Its Tables 1 to 5 give
      the trace and maximum-eigenvalue critical values at 1, 2, 5, 10 and 20
      percent, for `p - r = 1` to `12`, in each of Osterwald-Lenum's five
      cases.
    * **Generated, not typed.** The tables were decoded from the paper's own
      text, font by font, and generated into the module.
    * **What could not be compared.** The article's tables could not be read
      to compare. The authors' replication archive refuses automated readers,
      and the program it holds was not run.
    * **Three checks.** The tables fall with the test size and rise with the
      dimension. Where the theory makes the last statistic chi-square with
      one degree of freedom (an unrestricted constant or trend, one
      dimension), the printed rows are those quantiles to their two
      decimals. And the authors' program's own values, as a widely used
      four-decimal transcription prints them for three of the cases, are
      within 0.035 of every one of the paper's 216 values they share.
12. **Johansen's statistics are class 2.** Section 5 put them in class 3,
    because `ln` is the host's.
    * **The logarithm.** `ln(1 - λ)` is taken in `Decimal`, correctly rounded
      at 34 digits, and the statistic is summed and scaled there and rounded
      once.
    * **The eigenvalues.** They come from M2's symmetric eigensystem, class
      2, so a statistic is the same bits on every host. The estimate carries
      the eigensystem's convergence report.
13. **Johansen's reduction.**
    * **Partialling out.** One Householder QR of the lagged differences and
      the unrestricted deterministic terms is applied to every target at
      once: M2's reflections, with every inner product a `fsum`. It partials
      them out of `ΔX_t` and of `X_{t-1}`. A restricted constant or trend
      joins `X_{t-1}`.
    * **The eigenvalues.** The Cholesky factors of the two residual moment
      matrices whiten them, and the eigenvalues are the squared canonical
      correlations. `β` is normalized so that `β' S11 β = I`.
    * **Choices.** The lags are a fixed count: choosing them is a VAR's order
      selection. The trend is `t / T`, which changes no eigenvalue.
    * **Refusals.** A system of fewer than two series or more than twelve is
      refused, as are fewer than 20 observations, ill-conditioned or
      rank-deficient residuals, and a canonical correlation of one.
14. **Held by exact arithmetic and by the null.** Every case's eigenvalues and
    statistics, with 0, 1 and 2 lags, are held to exact rational projections
    and a 50-digit root. Under each case, with data that has what the case
    assumes, the trace test rejects a true null at its nominal rate. A table
    applied to another case's data rejects at 0 to 79 percent instead.
15. **The Ornstein-Uhlenbeck fit is class 2, and OFE-007 is closed.**
    `mean_reversion.fit_ornstein_uhlenbeck(values, interval=...,
    maximum_condition=...)` fits the exact discretisation by least squares and
    reads `θ`, `μ`, `σ` and the half-life off it. Section 5 put these in class
    3, because of `log`.
    * **Class 2.** Each logarithm is `Decimal`'s, correctly rounded, and the
      rest is `+ - * /` and `sqrt`, so the process's parameters are the same
      bits on every host, like the regression.
    * **Standard errors.** Each is the delta method's, from the regression's
      covariance `s² (X'X)⁻¹` and `Var s² = 2 s⁴ / (n - 2)`.
    * **Refusals.** A coefficient outside `(0, 1)` is refused, as section 7
      states, as are fewer than 20 transitions and an interval that is not a
      finite positive number.
    * **Held by tests.** Recovery of known parameters, the 95 percent
      intervals' coverage over 60 samples, exact rational arithmetic for
      every value, and a finite difference for the delta method.
    * **OFE-007 closed.** The fit delivers what OFE-007's reopening asked for.
      `factor_library.decay` is unchanged.
16. **The VECM is Johansen's two steps, every value class 2.**
    `cointegration.fit_vecm(series, rank=...)` reads its relations from
    Johansen's first `r` eigenvectors, normalized so that the first `r`
    series' block is the identity. That block is set to the identity exactly.
    * **The second step.** Each series' difference is regressed by least
      squares on the error-correction terms, the lagged differences and the
      unrestricted deterministic terms, with `β` taken as given. `β` is
      super-consistent, so `α`, `Γ` and the terms carry classical standard
      errors. The residual covariance is over `T`.
    * **Refusals.** A rank of 0 is a VAR in differences and full rank a
      stationary VAR, so both are refused. So is a block the first series
      cannot normalize. One reduction serves the test and the fit, so they
      read the same relations.
17. **A VAR is least squares by equation, and its lags are chosen for the
    system.** `var.fit_var(series, deterministic=..., lags=...,
    maximum_condition=...)` regresses each series on its deterministic terms
    and `p` lags of every series (class 2).
    * **Choosing the lags.** A fixed count of one at least, or AIC or BIC
      among 1 to a maximum. The t-statistic rule, which chooses one
      equation's lags, is refused. Every candidate is fitted on the sample
      the maximum leaves, and the chosen count on all the data it leaves, as
      the unit-root tests do (settled point 5).
    * **The criterion.** `T ln det Σ + penalty`, with `ln det Σ` the sum of
      `Decimal`'s logarithms of `Σ`'s Cholesky pivots, so every host chooses
      alike. The residual covariance is over `T`.

# Compatibility

- Additive: a new package, and in `research`, a new function.
- No existing identity, schema or result moves. `factor_library.decay` is
  unchanged.
- The import graph gains `econometrics`, which imports only `common`, and
  `research`'s edge to it. The measured dependency table records both.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below it.

**Known limitations**

- **Johansen's critical values are asymptotic.** The tests over-reject in
  small samples: with a restricted trend, 150 observations read the 5 percent
  test at 7 to 10 percent. No small-sample correction is applied, such as
  Reinsel and Ahn's degrees of freedom or MacKinnon, Haug and Michelis's
  finite-sample response surfaces (FUT-053).

# Consequences

- A researcher's time-series model is a recorded estimate: its problem, its
  choices and its data have an identity, and a fit two hosts agree on is one
  they can compare.
- A half-life becomes reportable without being mistaken for a measurement.
- S-02's screen counts every pair it tests, so a pair is never selected from a
  search nobody can see.
