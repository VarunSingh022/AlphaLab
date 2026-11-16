# ADR-0075: Statistical Regime Models -- Hidden Markov and Markov-Switching Estimation with an Identity

## Status

**Accepted** (2026-11-16). It decides G0's D-V3 for OFE-010, statistical regime
models, which the owner delegated to the milestones on 2026-11-10 ("each
reopening gets its own ADR before its milestone"). It is M6's, with ADR-0074
(plan section H.3's regimes row, workstream WS-07).

It depends on:
- ADR-0058: the identity of an estimate, which OFE-010 waited for;
- ADR-0074: the `econometrics` package it adds a module to.

It reverses no accepted ADR. `research.regimes`' declared rules are unchanged.

---

# Context

`research.regimes` classifies regimes by rules a caller declares: thresholds on
a signal, cut points on a trailing rank, persistence. It supplies the
machinery for the caller's own answer, and AlphaLab defines no taxonomy (v3.7).
v3.13 kept statistical regime models out as OFE-010: "estimation models with
fitting windows are research models outside the engine's declared-rule regime
layer". A fitted model then had no identity -- no record of what was fitted,
from what, and how.

ADR-0058's estimate supplies that record. A hidden Markov model is a standard
research tool for regimes the researcher cannot declare by rule: calm and
turbulent markets read from returns, or a beta that switches with them.

# Decision

## 1. OFE-010 reopened in `econometrics.regimes`

Two models, each fitted by expectation-maximisation:

| Model | What it states | Fitted by |
| --- | --- | --- |
| `GaussianHMM` | `K` hidden states, each with its own mean and variance; a first-order Markov chain between them | Baum–Welch, the scaled forward–backward recursions (Rabiner, 1989) |
| `MarkovSwitchingRegression` | A response on the caller's regressors, with coefficients and variance that switch with the state | Hamilton's filter and Kim's smoother (Hamilton, 1989; Kim, 1994), and weighted least squares per state |

A Markov-switching autoregression in the switching-intercept form is the
second model, with lagged responses among the regressors.

## 2. Every choice is stated

- **`K`:** at least two.
- **The start:** explicit parameters, or a named rule with its own stated
  choices. The quantile rule starts each state's mean at one of the data's
  `K` quantiles and every variance at the sample's, with a stated diagonal
  persistence for the chain.
- **The tolerance and the limit:** on the log-likelihood's change, and the
  iterations allowed.

EM finds a local maximum, and the estimate records the start that reached it.
The package never restarts from several points and keeps the best on its
own: that would be a search, and a search nobody counts. A caller who fits
from several starts makes several estimates.

## 3. What is refused, never repaired

- **A collapsing state.** A state whose variance collapses toward zero sends
  the likelihood to infinity. It is refused as a fit that did not converge
  (`ConvergenceError`), with the state named, and never floored silently.
- **Too little data.** A series too short for `K` states and the regressors
  is refused before the fit.
- **Non-convergence.** A fit that does not converge within its limit raises
  `ConvergenceError` with the likelihood it reached.

## 4. One labelling of the states

States are interchangeable: any permutation of the labels gives the same
likelihood. The estimate orders them by ascending mean (for the regression,
ascending variance), ties broken by variance, then by mean. So two fits that
reach the same maximum report the same states. This is a labelling convention,
stated, and never a choice that changes a fitted value.

## 5. What a fit returns

- **An `Estimate`.** It holds:
  - the state means, variances or coefficients;
  - the transition matrix and the initial distribution;
  - standard errors, from the observed information at the maximum (settled
    at implementation);
  - the start, the tolerance, the limit and the convergence report.
- **The filtered and smoothed state probabilities.** At each instant.
- **The most likely path.** By Viterbi's algorithm.

Every value passes through `exp` and `log`, class 3 (ADR-0058). So the
parameters, probabilities and path are recorded beside the identity. The
identity covers the problem: the data's identity, `K`, the model, the start,
the tolerance and the limit.

## 6. Declared rules stay the engine's regime layer

- **Labels for research.** A decoded path is a series of labels a caller may
  hand to research's conditioned analysis, as any labels, with the estimate's
  identity beside them.
- **Nothing on the execution path.** Nothing reads an HMM there. A strategy
  that uses a fitted model holds it as its own state, as it holds any model's.
- **Research's rules unchanged.** `research.regimes` keeps its declared rules,
  its definitions' identities and its transitions.

## 7. Validation

- **Simulated regime recovery.** Data drawn from a known chain recover its
  means, variances and transition probabilities within their standard
  errors, and the most likely path agrees with the true states on all but a
  stated fraction of instants.
- **Hand-computed references.** Small chains are checked against
  forward–backward probabilities computed by hand in exact fractions, where
  the emissions allow.

# Compatibility

- Additive: a module in ADR-0074's package.
- `research.regimes` and every identity are unchanged.
- The ledger records OFE-010 as reopened under this record, and closed when
  its estimators are delivered.

# Known limitations

**Known limitations**

- **Emissions are univariate.** Both models switch a single series: a
  `GaussianHMM` over several series at once, with a covariance matrix per
  state, is ledger FUT-052. A researcher with several series either models
  one at a time, or regresses one on the others with switching coefficients.

# Consequences

- A regime a researcher estimates, rather than declares, is a recorded estimate
  whose problem two hosts agree on.
- No regime model enters the execution path, and no taxonomy enters the engine.
