# ADR-0043: Advanced Portfolio and Risk — Construction, Risk Budgets, Multi-Strategy Books, Cross-Strategy Risk and Capital Allocation

## Status

**Accepted and implemented in v3.8.0.**

The eighth capability release on the architecture frozen at v3.0.0. It adds no
package. It adds modules to `alphalab.analytics` (`risk_model`, `risk_budget`,
`cross_strategy`), `alphalab.portfolio_optimizer` (`quadratic`, `construction`,
`risk_parity`, `black_litterman`), `alphalab.portfolio` (`multi_strategy`),
`alphalab.allocation` (`capital`) and `alphalab.factor_library` (`loadings`), and
extends `alphalab.api` and `alphalab.lifecycle`. Three package edges are added,
none of which closes a cycle; no snapshot schema changes, no durable state is
added, and every v3.1 through v3.7 invariant holds.

Amends **ADR-0005**'s dependency statement: `portfolio_optimizer` now imports
`common` **and the risk model in `analytics`** — the one covariance authority —
rather than `common` alone. Nothing on the execution path imports it, so it
remains a standalone engine under **ADR-0009**.

Depends on **ADR-0005** for the construction authority, **ADR-0009** for the
standalone-engine classification, **ADR-0015** for the reservation ledger,
`CapitalBudget` as a sizing parameter and the `StrategyContribution` record,
**ADR-0017** and **ADR-0036** for the derived-identity idiom and for derivations
recorded as new versions, **ADR-0019**, **ADR-0020** and **ADR-0028** for
currency roles, the mixed-currency boundary and the FX authority, **ADR-0027**
for sector provenance in the instrument registry, **ADR-0038** for risk
measurement in `analytics` and "absent is not zero", **ADR-0041** for
fingerprints, and **ADR-0042** for the research-settings precedent the lifecycle
integration follows.

**Amended by ADR-0045 (v3.10.0).** The multi-strategy book and valuation
identities render every amount by value (`canonical_text`), under the v2
schemes `alphalab.multi_strategy_book.v2` and `alphalab.book_valuation.v2`:
once money is rounded only by its producer, two books that compare equal must
not have two identities (ledger DET-005). Nothing else here changes.

---

# Context

At v3.7 AlphaLab could measure one book's risk (v3.3's decomposition) and
construct a portfolio four ways in closed form (v1's optimizers). A desk running
several strategies needs five things it could not do:

**1. Construct under constraints.** v1's minimum variance and maximum Sharpe are
matrix-inverse closed forms; equal weight and inverse volatility are formulas.
Constraints were applied *afterwards* by `apply_weight_constraints`, which clips
weights and spreads the excess evenly — a heuristic whose answer is not the
constrained optimum — and which ignored `max_sector_exposure` without saying so.
There was no risk parity (the documentation listed it; inverse volatility is not
it), no maximum diversification, robust or Black–Litterman construction, no
factor, group, turnover or gross constraint, and no notion of infeasibility.

**2. Budget risk.** v3.3 decomposes volatility by asset. Nothing grouped the
contributions by strategy, sector, country or currency, and nothing compared
them with limits.

**3. Hold several strategies in one book.** `PortfolioState` is one book of
record per run. Nothing combined several strategies' states while keeping each
one's positions, P&L and capital apart, or valued the combination across
currencies.

**4. Compare strategies.** Nothing correlated strategies, measured how much
they hold in common, how they lean on the same factors, or which capital they
share.

**5. Allocate capital between runs.** `CapitalBudget` is one run's ceiling.
Nothing divided capital across strategies, markets, brokers, accounts and
currencies.

Underneath all five, the covariance each would read existed only implicitly,
inside v3.3's functions, as a mapping with no currency, period, source or
identity.

The v3.8 audit of the v1 construction engine found five defects of the kind the
architecture exists to refuse: the adapter read a **missing covariance or
expected return as 0.0**, a measurement nobody made; `apply_weight_constraints`
ignored a sector cap, and returned weights summing **above** the target when
lower bounds forced it; the manager's cost estimate **left out the spread**
`CostModel.spread_rate` states; and `ConstraintViolated` reported the constant
`1.0` whatever was clipped. It also found `validate_risk_constraints`'
docstring implying six limits were checked when three are.

---

# Decision

## 1. Placement: the risk model is a value, and construction reads it

| Concern | Home | Why there |
| --- | --- | --- |
| Covariance, correlation, factor loadings, classifications, Euler contributions | `analytics.risk_model` | risk measurement lives in `analytics` (ADR-0038 §7); every consumer below reads one authority |
| Risk budgets along five dimensions | `analytics.risk_budget` | a measurement compared with limits; enforces nothing |
| Correlation, overlap, crowding, common exposure, capital concentration | `analytics.cross_strategy` | measurements over exposure lines and return series |
| The quadratic-program solver, construction, risk parity, Black–Litterman | `portfolio_optimizer.{quadratic, construction, risk_parity, black_litterman}` | the construction authority (ADR-0005) |
| Several strategies' sleeves in one book, valued across currencies | `portfolio.multi_strategy` | groups canonical `Position` values; the accounting engine stays the book of record |
| Capital divided between strategies, markets, brokers, accounts and currencies | `allocation.capital` | the capital authority (ADR-0015), composing with its ledger and budget |
| Loadings from factor-library panels | `factor_library.loadings` | the feature engine's panels, read into the risk model's type |
| Sector and currency classifications from the instrument registry | `api` | the application-facing module, which may join `instrument` to `analytics` — and `analytics` imports only `common` and `core` |
| Construction and capital identities in a fingerprint | `lifecycle.fingerprint` | the research-settings section, as ADR-0042 did for adaptive state |

The three new edges are `portfolio_optimizer → analytics` (the risk model),
`portfolio → core` (the canonical `StrategyContribution` a holding carries) and
`api → instrument` (registry classifications). `analytics` still imports only
`common` and `core`; `allocation` still does not import `portfolio` — it reaches
FX through `CurrencyConverter`, a structural protocol `FxRates` satisfies as it
stands; `lifecycle` reaches constructions and plans through two protocols and
imports neither package. `test_v38_invariants.py` pins every edge, and the
package graph has no cycle.

## 2. One implementation of the arithmetic

A covariance is estimated by `common.statistics.sample_covariance`, applied
pairwise in one loop; the Euler decomposition `CTR_i = w_i (C w)_i / σ` is one
function; a portfolio's factor exposure `Σ_i w_i β_if` is one function; the
Herfindahl index is one function. v3.3's `covariance_matrix`,
`correlation_matrix`, `portfolio_volatility`, `risk_contributions`,
`concentration` and `factor_exposure` now call them, written as the same
expressions in the same order, so **every number v3.3 published is unchanged
bit for bit** — `test_v38_invariants.py` recomputes the v3.3 expressions on
generated books and compares floats exactly. The v1 closed forms keep their
Gauss–Jordan inverse for the same reason.

## 3. A covariance is a claim, so it says what it claims

`CovarianceMatrix` carries its assets (sorted), values, the **currency** its
returns were measured in, the **period** each return spans, its source and
observation count, and — when derived — its parent and the rule. All of it
enters `covariance_id` (`alphalab.covariance.v1`). Every consumer that combines
a covariance with anything else checks currency and period and refuses a
mismatch rather than rescaling.

**Definiteness is measured**: a Cholesky factorization with diagonal pivoting
and the `n·ε·max(diag)` rank floor classifies a matrix as positive definite,
singular or indefinite and reports its rank, pivot ratio and any zero-variance
assets. **Construction requires positive definite**: every objective here has a
unique solution then, and may have infinitely many otherwise. Nothing
regularizes on its own initiative — `with_ridge(δ)` and
`with_diagonal_shrinkage(a)` derive a new matrix whose identity names the parent
and the rule, the way a cleaned dataset derives a new version (ADR-0036).

`FactorLoadings` (`alphalab.factor_loadings.v1`) must be a full rectangle, with a
lineage per factor; `Classification` (`alphalab.classification.v1`) names its
dimension and source and **refuses an unclassified asset** rather than putting
it in an "unknown" bucket. `factor_library.loadings_from_panels` builds loadings
from panels, with every transform in the lineage; `api.sector_classification`
reads the registry's current or historical sectors with every source named, and
`api.currency_classification` reads each record's currency.

## 4. Construction: a stated problem, one solver, a certified answer

`construct(ConstructionProblem)` takes a covariance, an objective, a
`ConstraintSet` and `SolverSettings`, and returns a `ConstructionResult`:

| Objective | Solved as |
| --- | --- |
| `MinimumVariance` | a quadratic program |
| `MeanVariance(μ, λ)` | a quadratic program; with `max_volatility`, by bisection on risk aversion, reported as `effective_risk_aversion` |
| `MaximumDiversification` | a quadratic program after the change of variables `y = w / σ'w`; long only; refuses turnover and a volatility cap, which are not invariant to that rescaling |
| `RiskParity(budgets)` | cyclical coordinate descent on `½y'Cy − Σ b_i ln y_i` (unique minimizer); budgets are exact `Decimal` shares summing to one; any other constraint is checked at the solution, and an unmet one is `INFEASIBLE` — budgets are never relaxed |
| `RobustMeanVariance(μ, λ, U)` | ellipsoidal `U`: alternating the program in `w` with the closed-form `η = √(w'Ωw)`; box `U`: mean-variance on `μ − h`, exact for long-only books and refused otherwise |

Black–Litterman is a model, not an objective: `black_litterman` turns an
`EquilibriumPrior` (reverse optimization from **supplied** market values and
risk aversion) or a `SuppliedPrior`, a required `τ` and views with stated
variances into a posterior mean, its uncertainty and the predictive covariance
(Woodbury form, exactly symmetric), and `MeanVariance` constructs from it. There
is no default risk aversion, `τ`, view confidence or market portfolio.

**Constraints** are explicit and inspectable: net exposure, per-asset bounds
(with overrides), concentration, gross exposure, group bounds over any
classification, factor bounds (a `neutral` bound is a factor-neutral book),
turnover from a stated current book, per-asset notional caps in money, and a
volatility cap. **Units are stated once**: a weight is a fraction of capital in
the covariance's currency; volatility and return are per its period; money
enters only through `NotionalLimits`, converted once in a 28-digit context.

**The solver** is Goldfarb and Idnani's dual active-set method, written once in
`quadratic` and used by every quadratic objective. It terminates at the exact
optimum of a strictly convex program; it **proves infeasibility** and names the
conflict — the violated constraint and the active constraints that jointly
exclude it; and turnover and gross-exposure limits
enter as their exact polyhedral facets, generated lazily — the one most violated
at a time — so no auxiliary variable makes the Hessian singular. Status is
decided by a post-hoc KKT certificate: `OPTIMAL` only when the method stopped
with nothing violated *and* stationarity and feasibility verify within the
stated tolerances; otherwise `INFEASIBLE`, `ITERATION_LIMIT` or
`NUMERICAL_FAILURE` — **with no weights**. Only `+ − × ÷` and `√` are used, each
correctly rounded under IEEE-754, and the dependence threshold is derived from
machine epsilon, so the same problem gives the same bits on every platform.

Every result carries diagnostics — method, iterations, binding constraints,
conflict, largest violation, stationarity, pivot ratio, variance, volatility,
expected and worst-case return, diversification ratio, net and gross exposure,
turnover, Euler contributions, factor and group exposures, risk-budget deviation
and effective risk aversion — and `problem_id` and `result_id`
(`alphalab.construction_problem.v1`, `alphalab.construction_result.v1`) derived
from every input that decides the answer and **not** from the order constraints
were listed in.

**Verification against an independent reference.** The unit suite checks the
solver on 320 generated programs, and the invariant suite checks 40 generated
constructions, against an exhaustive enumeration of every candidate active set
solved in exact rational arithmetic: infeasibility is confirmed by a proof that
no feasible point exists, and optimal weights agree to `1e-8` (solver level)
and `1e-12` (construction level). Black–Litterman is checked against the exact
precision form.

## 5. Risk budgets: one line model, five dimensions

`evaluate_risk_budget` reads **exposure lines** — one strategy's holding of one
asset, in the reporting currency — decomposes the book's volatility once, at the
line level, and groups the same lines by `ASSET`, `STRATEGY`, `CURRENCY` (carried
by the line), `SECTOR` and `COUNTRY` (from caller classifications). Because
volatility is homogeneous of degree one in the weights, **every dimension's
buckets sum to the same volatility**; `DimensionRisk.residual` reports what
floating point left, with `math.fsum`. Each bucket reports net and gross
exposure, capital share, contribution and share, so exposure, capital and risk
are never confused. A `BudgetLimit` has a dimension, a bucket, a basis
(`ABSOLUTE` contribution or `RELATIVE` share) and a maximum, minimum and/or
target; `RiskBudget.tolerance` has no default; verdicts are `WITHIN`,
`AT_LIMIT`, `BREACHED` or `BELOW_MINIMUM`. A breach is **reported, never
enforced**. The covariance must describe returns measured in the reporting
currency, so the `CURRENCY` dimension is the risk of holdings *denominated in*
each currency, measured in the reporting currency, with each bucket's native
exposure kept beside its translation.

## 6. A multi-strategy book: sleeves side by side, never merged

A `StrategySleeve` is one strategy's canonical positions, cash, realized P&L and
commissions, built from the strategy's own `PortfolioState`
(`from_portfolio_state`), so strategies researched and run independently combine
without being rewritten. A `MultiStrategyBook` (`alphalab.multi_strategy_book.v1`)
aggregates an instrument across sleeves with a canonical `StrategyContribution`
per strategy — long, short, net and **crossed** quantity, and whether strategies
stand on opposite sides — and nothing nets them away. `value_book` expresses the
book in one reporting currency through `FxRates.convert`, once per native figure,
keeping every conversion; every total is a sum of converted figures, so the
per-strategy, per-instrument and per-currency breakdowns reconcile **to the
cent**. A book with no rates, a missing pair or a rate dated after the valuation
is refused exactly as everywhere else. The accounting engine remains the one book
of record: a book applies no fill and keeps no ledger.

## 7. Cross-strategy risk: each measurement names its basis

Return correlation (`strategy_return_correlation`) is the risk model's sample
covariance and correlation of strategies' return series, which must share one
currency and period — mixed ones are refused, not converted. Exposure similarity
(`strategy_overlap`) compares **holdings**: shared instruments, Jaccard,
same-direction and opposing overlap, overlap shares, cosine and a Pearson over
exposure vectors, `None` where undefined. Factor crowding (`factor_crowding`)
reports per-factor alignment `|Σ|/Σ|·|`, Herfindahl concentration and the
strategies leaning with the portfolio, plus pairwise factor-exposure cosines.
`common_exposures` groups by instrument, currency or any classification;
`capital_concentration` and `capital_overlap` measure capital by bucket and the
pools strategies share. A correlation of returns and an exposure similarity never
share a type.

## 8. Capital allocation: allocated in the currency it is in, refused rather than scaled

A `CapitalAllocationPlan` (`alphalab.capital_plan.v1`) states accounts (each an
identifier with a vendor-neutral broker label, a currency, available and reserved
capital), placements (strategy, market, account), a rule (`FixedAmounts`,
`PlacementWeights` with a required source, or `EqualWeights` with a stated
fraction), limits per strategy, market, broker, account or currency, an
oversubscription rule and a granularity. `allocate_capital` allocates **in each
account's own currency**, converts only what is expressed against the whole plan
(weights, limit shares, totals) through the canonical FX authority, and records
every conversion. Every account reconciles exactly:
`available = reserved + allocated + unallocated`. An oversubscribed account
refuses the whole plan unless `PRO_RATA` is stated, and then the scale is
recorded and said in words; a limit is never met by scaling; allocations are
floored to the granularity and the residue stays unallocated.

It composes with the execution path rather than duplicating it: reserved capital
is read from the one reservation ledger (`reserved_capital`, which checks the
budget's currency and invariant I1), and one account's allocation becomes the
`CapitalBudget` its run is given (`capital_budget`), which admits exactly the
allocation and refuses one unit more (tested through `AllocationEngine`).

## 9. Lifecycle integration without changing a key

`research_configuration_with_portfolio` writes `portfolio.<name>.problem`,
`portfolio.<name>.result`, `capital.<name>.plan` and `capital.<name>.allocation`
into the existing research-settings section, so `canonical_fingerprint_key` is
unchanged, every earlier fingerprint still verifies, and a strategy researched as
a constructed, funded portfolio changes identity when its construction or plan
does. An infeasible construction or a refused plan identifies nothing and is
refused.

## 10. Names kept apart before release

Two v3.8 names were changed before shipping because they collided with existing
public names in the very workflow v3.8 adds: the capital rule is
`PlacementWeights`, not a second `TargetWeights` (v1's asset target weights), and
a risk-budget limit is `BudgetLimit`, not `RiskLimit` (one letter from the
pre-trade `RiskLimits`). `test_shared_names_stay_distinct.py` records both, in
eight new sets: the three capital shapes; budgets and limits; asset target
weights and placement weights; the three factor exposures; a projection versus a
constrained optimum; correlation versus exposure similarity; one Euler
arithmetic on two weightings; and the two Cholesky factorizations.

## 11. Performance is a property of the algorithm, pinned by ratio

Every aggregation is one pass over indexed data. `test_v38_complexity.py`
asserts growth ratios for factor crowding, a risk budget with a limit per
strategy, book valuation, capital allocation, common exposures and overlap,
measured with the stabilized method (process CPU time, the collector disabled,
interleaved minima), and **each guard was run against the defect it guards and
failed**. Writing those guards found three before release: `FactorLoadings.loading`
and `row`, and `Classification.label`, rebuilt a set of every asset on each call
— so a factor exposure over the whole universe cost the universe squared (20×
for a 4× input); a risk budget scanned a dimension's buckets per limit; and
notional limits located each asset by a linear search. Each is now an index.
The construction solver's cost grows with the cube of the universe
(`benchmark_portfolio_risk.py` prints it).

---

# Consequences

**No durable state, no store, no schema.** Every v3.8 value is frozen, produced
by a pure function, and serializes deterministically through
`alphalab.persistence.serialize`; a regression test asserts v3.8 added no
snapshot owner and no schema constant. Every identity is identical across hash
seeds and working directories, checked in fresh interpreters, and no v3.8 module
reads a clock, entropy or the environment.

**Classification of the scope**

| Capability | Status |
| --- | --- |
| Minimum variance, mean-variance, maximum diversification, risk parity (equal and stated budgets), robust mean-variance (ellipsoidal, box), Black–Litterman, factor-neutral and constrained construction | IMPLEMENTED |
| Risk budgets by asset, strategy, sector, country and currency | IMPLEMENTED |
| Multi-strategy books with provenance, valued across currencies | IMPLEMENTED |
| Return correlation, exposure overlap, factor crowding, common exposures, capital concentration and overlap | IMPLEMENTED |
| Capital allocation across strategies, markets, brokers, accounts and currencies | IMPLEMENTED |
| Covariance estimators beyond the sample covariance (estimated shrinkage intensity, EWMA, factor-model covariance); cardinality and lot-size constraints; transaction costs in the objective; CVaR or drawdown objectives; multi-period construction; per-strategy sub-ledgers inside the accounting engine; exchange-rate return factors; per-strategy capital ceilings enforced on the execution path | DEFERRED |
| Expected-return forecasts, market values for an equilibrium prior, views and their confidence, countries, sectors (the operator's registry), FX rates, account balances, and the mapping from broker and account identifiers to adapters and credentials | EXTERNAL |
| Broker-specific SDKs, APIs or credentials; RedDesk logic; marketplace ranking; licensing or payment logic; user or application identity; Quant-Mind or OpenBB integration; LLM dependencies; network-dependent portfolio or risk calculations; order generation from a construction | NON-GOAL |

**Known limitations**

- **Share semantics.** `Position` carries no multiplier (ADR-0039), so a
  contract enters a sleeve with its quantity already in underlying units.
- **The `CURRENCY` risk dimension is not exchange-rate risk alone**; that would
  need exchange-rate return series as factors (deferred).
- **Crowding is measured within the portfolio**; nothing here sees other
  investors' positions.
- **Return series must already be in one currency**; `strategy_return_correlation`
  refuses mixed series rather than converting them.
- **A box uncertainty set is long-only**; its counterpart for a book that may
  short is an L1 penalty, not offered.
- **Maximum diversification takes no turnover limit or volatility cap**, which
  are not invariant to its change of variables.
- **Risk parity takes exact budgets and is long-only**; other constraints are
  checked at its unique solution, never traded off against the budgets.
- **Risk budgets enter construction as exact budgets or a volatility cap.**
  `RiskParity` builds the portfolio whose contributions *are* the stated
  budgets, and `max_volatility` caps the whole portfolio's risk. A cap on one
  bucket's Euler contribution inside another objective is not a convex
  constraint and is not offered; a finished book is judged against such limits
  by `evaluate_risk_budget`.
- **No shrinkage-intensity estimator**: the intensity is the caller's, recorded
  in the identity.
- **`validate_risk_constraints` (v1) checks three of `RiskConstraints`' six
  fields**, and now says so.
- **Capital limit shares are measured against total free capital**, not against
  the capital allocated.
- **Base-currency capital figures are translations**, each rounded once by the
  converter; the exact identities are the per-account ones.
- **A per-strategy amount in an account's `CapitalBudget` is what weight-based
  sizing reads, not a separate ceiling**; the execution path enforces the
  account-level ceiling (ADR-0015 §1).
- **A netted run is not split into sleeves**: a book is assembled from each
  strategy's own state, and positions a single run netted across strategies have
  no per-strategy decomposition.
- **The solvers are pure Python**: exact and deterministic, and cubic in the
  universe; the benchmark prints construction times on the order of ten
  milliseconds at fifty assets.
- **The v1 cost estimate now includes the spread**, so a v1 rebalance's
  estimated cost rises by exactly that component (example 06 prints the new
  figure).

**What this release does not add:** no broker-specific SDK, API or credential
— "broker" is an allocation dimension and a vendor-neutral identifier, never an
adapter; no RedDesk logic, marketplace ranking, licensing or payment logic; no
user or application identity; no Quant-Mind or OpenBB integration; no LLM
dependency; no network-dependent calculation; no default risk aversion, `τ`,
view confidence, tolerance, shrinkage intensity or market portfolio.
