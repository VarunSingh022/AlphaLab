# ADR-0058: The Numerical Foundation -- One Authority, Host Independence on the Record Path, and the Identity of an Estimate

## Status

**Accepted** (2026-11-14). It is the precision policy the v4.3.0 plan required
before M2's statistics fix any identity (plan change PC-02). It follows the
direction the owner approved for G0 item 24 on 2026-11-09:

- deterministic calculations first, and appropriate precision preserved;
- rounding only where numerical error analysis justifies it;
- cross-platform behaviour proved by reference tests;
- no arbitrary fixed rounding to conceal numerical differences.

Its other decisions were delegated to M2 on 2026-11-10, and were taken under
that delegation (`docs/work/v4.3.0`, TD-09). This ADR replaces one proposal of
the plan, section H.1: identity-bearing statistics recorded as decimal strings
rounded to twelve significant digits. Section 4 explains why. It reverses no
accepted ADR. It restates LIM-005 and REP-005 as the rule every new numerical
module follows.

---

# Context

Since v4.1 a run's record, and so its `result_id`, has been host-independent:
- money is `Decimal`;
- every float a run records comes from `+ - * /`, `sqrt`, `fsum` or a correctly
  rounded power (REP-005).

The standalone engines' transcendental functions (`exp`, `log`, `pow`, `erf`
through the host's libm) may round differently in the last bit on another host.
Their figures enter no run record, and that limitation is kept and stated
(LIM-005).

v4.3 adds numerics the engine has not had. These are the statistical
distributions and special functions; eigensystems and singular values with
vectors; optimisers, root finding and quadrature; and statistics that select
among backtests (ADR-0054). Their values will be recorded in evidence: run
provenance, experiment and trial records (ADR-0072), and fitted estimates. So
two questions need one answer before any of it is written:

- which values may enter an identity;
- and how precisely is a value recorded?

# Decision

## 1. One authority per numerical concept (AC-15)

| Concept | The authority | What happens to the others |
| --- | --- | --- |
| Distributions, special functions | `common.distributions` (new): normal (existing, from `common.statistics`), Student t, χ², F, gamma, beta, binomial, Poisson -- CDF, survival function, quantile; log-gamma, the regularised incomplete gamma and beta functions | `common.statistics` keeps its names and delegates |
| Linear algebra | `common.linalg`: the existing QR least squares and Jacobi singular values, plus a symmetric eigensystem **with vectors**, a thin SVD with vectors, Cholesky and a pseudo-inverse | Cholesky moves from `portfolio_optimizer`, which re-exports it (no name removed). `ml.linalg` delegates, its names deprecated for 5.0 (GAP-48) |
| Optimisation, roots | `common.optimize` (new): Nelder–Mead, BFGS, L-BFGS-B (bounds), Brent's root finder, golden section, numerical gradients | — |
| Quadrature | `common.quadrature` (new): adaptive Gauss–Kronrod | — |
| Splits | `research.walk_forward` and `research.time_series_cv` | `ml.cross_validation` delegates (GAP-48) |

`test_one_research_authority_per_concept.py` holds a second implementation out.

## 2. A tolerance that changes a result is the caller's; one that only reaches binary64 is stated (AC-12)

AC-12 says choices are declared, never defaulted. So every method whose
tolerance changes its answer takes **its tolerance and its iteration limit as
required arguments**, with no defaults. A tolerance nobody chose is a policy
nobody chose, as for `lifecycle.tolerance`. These are the optimisers, root
finders, quadrature, eigensystem and SVD iterations, and the inversion of a
function the caller supplies.

A distribution's own functions and the special functions under them involve no
such choice. These are a CDF, a survival function, a quantile, the incomplete
gamma and beta functions. They compute to the precision of binary64, by
algorithms whose criteria are **stated constants** of their module, documented
there with the error bound they carry. This is the plan's "stated tolerance
constants" for distribution quantiles (section H.1).

Either way, a method that does not converge within its limit raises
`ConvergenceError`, naming the tolerance, the limit and what was reached. It
never returns a silent best effort. Every result of a caller-tolerance method
carries its convergence report: iterations, the final residual or step, and the
criterion that stopped it.

## 3. Three classes of numerical result, and which may enter an identity

| Class | Arithmetic | Host-independent? | May enter an identity? |
| --- | --- | --- | --- |
| **1. Exact** | Integers, `Fraction`, `Decimal` under a stated context, hash-counter integers (SHA-256 of a seed and a counter, read as integers) | Yes | Yes |
| **2. Correctly rounded** | IEEE 754 binary64 `+ - * /` and `sqrt`; `math.fsum`; `math.fma` where available; conversions from exact values; a power with a small integer exponent by repeated multiplication | Yes, on every IEEE 754 host and every CPython this supports | Yes |
| **3. Host libm** | `exp`, `log`, `pow` with a non-integer exponent, trigonometric functions, `erf`, `erfc`, `lgamma`, anything iterating over them | Not to the last bit | **No** |

**The rule.** A value that enters an identity is computed on class 1 or 2 paths
only. Identities include `result_id`, `manifest_id`, `provenance_id`, an
experiment or trial record id, and an estimate's id. Recorded figures, the
statistics among them, are class 2 at worst wherever they can be. Where an
identity needs what is naturally a transcendental, the library computes it on
a class 1 or 2 path instead. The stationary bootstrap does this: its geometric
block lengths are decided by integer comparison of hash-counter draws, never
by `log` (ADR-0054). A class-3 value is recorded beside an identity, never in
it, and is compared across hosts by a stated `Tolerance` (section 4).

## 4. Precision: preserved, and rounded only where error analysis says so (G0 item 24)

- **Every value is recorded at full precision**: the shortest `repr` that
  round-trips, or a `Decimal` exactly. Nothing is rounded to make two hosts
  agree.
- **Rounding cannot make two hosts agree, so it is not used to.** Values a
  last bit apart round to different strings whenever they straddle a rounding
  boundary, at any number of digits. The plan's twelve-significant-digit
  proposal would have hidden a difference in nearly every case and silently
  failed in the rest.
- **A class-3 comparison states its tolerance from the method's error
  analysis.** For example, the forward error bound of a quantile inversion is
  its stated tolerance plus the CDF's error times the density's reciprocal.
  The comparison goes through `lifecycle.tolerance`. A value with no tolerance
  stated is reported as not comparable, as that module already does.
- **Rounding is presentation.** A report rounds for reading. When a
  computation can show its own error bound is far below a representation's
  resolution, that representation may be recorded as well, with the bound
  stated. It never replaces the full-precision value.

## 5. Cross-host behaviour is proved, not assumed

Every identity-bearing numerical result has a **pinned reference test** that
CI runs on Linux and macOS under CPython 3.12, 3.13 and 3.14, as run
provenance's S-11 does (ADR-0053). Every function is held to **published
reference values** (Abramowitz and Stegun's tables, closed forms, known
optima), to the digits its error analysis supports. Each also has properties
that need no reference: inverse identities, monotonicity, reconstruction and
orthogonality bounds.

## 6. The identity of an estimate

A fit returns an `Estimate`. It carries:

- the parameters, at full precision;
- their standard errors;
- the method, under its own scheme;
- the identity of the data it was fitted on;
- the tolerances and limits it was given;
- its convergence report;
- the class of its values.

Its **identity covers the estimation problem**: the data's identity, the model
and method, the starting point, the tolerances and limits. It covers the fitted
values too **only when they are class 1 or 2**. Least squares by Householder QR
is class 2, so its coefficients are in the identity. A likelihood maximised
through `log` is class 3, so its parameters are recorded beside it and compared
by tolerance. An estimate is research evidence that says what was estimated,
from what and how, and two hosts that fit the same problem agree on it.

This is the "estimation step with an identity" that OFE-007 (a fitted
half-life) and OFE-010 (estimated regime models) were kept waiting for. Reopening
them is WS-07's, at M6, each recorded in the ledger when it is delivered. This
record supplies the identity they need; it does not deliver them.

## 7. Random numbers

Research randomness is drawn from a seeded `random.Random`, one per declared
purpose, with its seed recorded. It is never the identifier stream (ADR-0022).
Randomness whose outcome enters an identity, such as bootstrap positions, comes
from a hash-counter stream (class 1). `random.Random`'s integers are
host-independent, but its derived floats such as `gauss` call libm and are
class 3.

# Known limitations

**Known limitations**

- **A class-3 statistic is compared by tolerance, not by identity.** A p-value
  through `erf`, or a likelihood maximised through `log`, agrees across hosts
  only to the tolerance its error analysis states. That is LIM-005's
  limitation, made a rule rather than an accident.
- **Pure-Python numerics are slow at scale.** An eigensystem of a 500 × 500
  matrix by cyclic Jacobi takes tens of seconds, and the plan's budget is 30.
  The core takes no array dependency. Speed for large problems is the optional
  compute backends' (ADR-0057, M11), each held to this record's identities by a
  cross-check.

# Consequences

- New modules `common.distributions`, `common.optimize` and `common.quadrature`,
  and `common.linalg` grows. All are additive.
- `ConvergenceError` and the `Estimate` type are new public names.
- `portfolio_optimizer` and `ml.linalg` keep every name.
- No existing identity, figure or tolerance moves. REP-005's class-2 run record
  is unchanged.
- LIM-005 stays a stated limitation. Its class-3 figures are now a named class
  with a rule. They are not removed.
