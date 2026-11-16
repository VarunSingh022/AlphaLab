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

# Compatibility

- Additive: a new package, and in `research`, a new function.
- No existing identity, schema or result moves. `factor_library.decay` is
  unchanged.
- The import graph gains `econometrics`, which imports only `common`, and
  `research`'s edge to it. The measured dependency table records both.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below it.

# Consequences

- A researcher's time-series model is a recorded estimate: its problem, its
  choices and its data have an identity, and a fit two hosts agree on is one
  they can compare.
- A half-life becomes reportable without being mistaken for a measurement.
- S-02's screen counts every pair it tests, so a pair is never selected from a
  search nobody can see.
