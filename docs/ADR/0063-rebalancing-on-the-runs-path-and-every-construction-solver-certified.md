# ADR-0063: Rebalancing on the Run's Path, and Every Construction Solver Certified

## Status

**Accepted** (2026-11-22). It is the record the v4.3.0 plan needs before M9
(plan sections H.5 and P.2, engines N19 and N22, gaps GAP-20 and GAP-37,
workstream WS-12). The owner delegated the programme's decisions within the
charter's principles on 2026-11-10 (ADR-0056), and D-V3's boundary reopenings
to the milestone that needs each. This record decides M9's two.

It reverses two accepted decisions, each narrowly, as plan section P.2 states:

- **ADR-0043's non-goal "order generation from a construction"** is reopened
  narrowly. The optimizer still never generates an order; a separate
  `rebalancing` package may turn a certified construction into a strategy's
  targets.
- **"One solver"** -- ADR-0046 decision 10's "CVaR and drawdown objectives need
  a linear program over scenarios, which the quadratic solver is not",
  invariant AC-27 and `nowandfuture.md`'s "Do not add a second solver" -- is
  reopened as **every solver certified**, not one solver. Integer programs,
  exact cardinality and multi-period construction stay boundaries.

---

# Context

**Construction is standalone (GAP-20).** `portfolio_optimizer` has no
importer. Example 10 bridges by hand: a construction, `round_to_lots`, and
target intents through the canonical path. A researcher cannot backtest a
construction rule as a strategy, so construction research stays disconnected
from the evidence every other strategy produces.

**Objectives are closed, and one kind of program is solved (GAP-37).**
`ConstructionObjective` is a union of five classes. Behind
`construct(ConstructionProblem)` there are two methods for one kind of
program: Goldfarb and Idnani's dense dual active-set method and, since v3.12,
a factor-structured interior point (PRF-005). Both solve quadratic programs.
CVaR and drawdown need a linear program over scenarios (ADR-0046 decision 10).

**What makes an answer an answer.** A method returns weights only when a
certificate it checked says they are optimal within the problem's stated
`SolverSettings`. Otherwise it returns a status -- `INFEASIBLE`,
`ITERATION_LIMIT` or `NUMERICAL_FAILURE` -- with no weights. Every operation
is IEEE-754 correctly rounded, so one problem gives the same bits on every
host. The suite checks the dense method against exact rational arithmetic.
That contract, not the count of solvers, is what AC-27 and
`nowandfuture.md` protect.

**What the run already offers a rebalancer.** A strategy that defines
`on_slice` decides when an instant is complete, every price known. Its
context shows its book and the market's recent records. Target intents
(`TARGET_QUANTITY`, `TARGET_WEIGHT`, FEA-001) state the position wanted; the
run turns them into orders through allocation, risk and the OMS. A strategy
with memory keeps it through `StrategyStateProtocol`, which a paused run
captures and restores.

**Layering.** `strategy` imports only `common` (ADR-0016). `portfolio_optimizer`
imports `common`, `analytics` and `conventions`, and nothing on the path
imports it (`test_v38_invariants.py`). A bridge from one to the other
therefore needs a package of its own, above both (N19).

**What a pure-Python linear program costs.** WS-12's budget asks for a
construction at 1,000 assets with CVaR over 2,520 scenarios within 60 seconds
in pure Python, "or via the backend cross-check". That is a proposed design,
not a measured fact. The kernels it needs were measured first, on the reference
machine:
- a product of the 2,520 x 1,000 scenario matrix with a vector: 0.08 s;
- a normal matrix over 300 of its rows: 1.5 s;
- a dense Cholesky factorization at 1,000: about 6 s, extrapolated from 400.

An interior point that forms the full normal equations pays about 42 s an
iteration to form them, about 20 minutes in all. One that works on a
restricted set of scenarios, checking the rest at each round, is estimated at
two to five minutes.

# Decision

## 1. Construction stays one authority, and every method behind it is certified

- **`construct` stays the one entry point** (AC-15). The authority is its
  certification contract: a method returns weights only with a certificate it
  has verified within the problem's `SolverSettings`, and otherwise a status
  with no weights. The four statuses are unchanged.
- **Three methods stand behind it:** the dense quadratic method, the
  factor-structured interior point, and a certified linear program (section 3).
  Which one solves a problem follows from what the problem declares (section
  2). A problem's identity names its method whenever the method is not the
  dense one, as `solver=factor-structured` already does.
- **The rule, restated.** No solver whose answer cannot be certified, and no
  fallback that returns an answer its certificate cannot verify. `construct`
  may hand a problem one method cannot certify to another that can, as it
  does today. Integer programs, exact cardinality and multi-period
  construction stay boundaries.

## 2. The objective protocol

- **An objective states what it is.** It renders its identity lines, and
  declares its class:
  - `LINEAR`: a cost on each weight and on each variable it introduces;
  - `CONVEX_QUADRATIC`: a positive semidefinite curvature and a linear term;
  - `CONVEX`: a convex objective with a method of its own, as risk parity,
    maximum diversification and the robust objective have;
  - `NONCONVEX`.

  A constraint states its rendering, its class -- linear, or convex quadratic
  as the volatility cap is -- and its rows.
- **Dispatch by class.** `construct` sends a problem to the method its classes
  admit. A class no certified method takes is refused before anything is
  solved, the refusal naming it. A `NONCONVEX` objective is always refused.
- **The five objectives the package ships declare theirs** and keep their
  methods, their renderings and every identity. `ConstructionObjective` stays
  the union of the shipped objectives, and a problem accepts any objective of
  the protocol.
- **Nothing is defaulted** (AC-12): no scenario set, confidence level, limit
  or tolerance.

## 3. A certified linear program

- **The program:** minimize `c'x` subject to `A x = b`, `G x <= h` and
  `l <= x <= u`. Here `x` is the weights and every variable an objective or a
  constraint introduces.
- **The method:** Mehrotra's predictor-corrector interior point, then an exact
  finish, as the factor-structured method does. From the converged iterate,
  the variables strictly inside their bounds and the inequalities strictly
  slack are read off. The rest is solved directly for a vertex and its
  multipliers, and the certificate is checked:
  - every primal residual within `feasibility_tolerance`;
  - every reduced cost of the right sign within it;
  - complementary slackness;
  - a relative duality gap within `convergence_tolerance`.

  `OPTIMAL` only when every part verifies. A wrong partition is corrected and
  the finish repeated a bounded number of times. `INFEASIBLE` only with a
  verified Farkas certificate: a ray of the dual proving that no feasible
  point exists. Otherwise `ITERATION_LIMIT` or `NUMERICAL_FAILURE`, with no
  weights.
- **No unbounded program.** A linear objective requires finite bounds on every
  weight, so its feasible set is bounded: it has an optimum or is infeasible.
  One without them is refused before solving.
- **Arithmetic and identity as the quadratic methods'.** Only `+ - x /` and
  `sqrt`, each correctly rounded, so the same problem gives the same bits on
  every host; `solver=linear` names the method in the problem's identity.
- **Verified as the dense method is.** The suite checks it against an exact
  rational reference on generated programs. A simplex in exact arithmetic
  confirms each optimal value, and confirms infeasibility by an exact Farkas
  ray.

## 4. Scenario risk, linear once lifted

- **A scenario set is supplied, never generated.** It holds each scenario's
  return for every asset of the universe, in the covariance's currency and
  period, and has an identity derived from its values. Historical, simulated
  or stressed, it is the researcher's choice.
- **CVaR** (Rockafellar and Uryasev, 2000). At a stated level `beta`, the CVaR
  of the portfolio's loss over `S` scenarios is the least value over `alpha`
  of `alpha + sum_s max(0, -r_s'w - alpha) / ((1 - beta) S)`. It is linear once
  lifted by `alpha` and one `u_s >= 0` per scenario with
  `u_s >= -r_s'w - alpha`. A minimum-CVaR objective minimizes it; a CVaR limit
  bounds it.
- **Drawdown** (Chekhlov, Uryasev and Zabarankin, 2005). Over a declared path
  of scenario returns, a drawdown limit bounds the conditional drawdown, at a
  stated level, of the cumulative sum of the portfolio's returns. It is lifted
  by one running peak and one excess per step; at level one it is the maximum
  drawdown. The sum is linear in the weights; compounding is not, so a
  compounded drawdown is not offered.
- **A quadratic objective under a scenario limit is refused at M9.** The dense
  method needs strict convexity in every variable, and the lifting's variables
  have none. The structured method would carry one coupling row per scenario.
  The refusal names why.

## 5. Cardinality, relaxed, with its bounds stated

A cardinality limit -- at most `k` names -- is solved in two certified steps:

1. The problem without the limit. Its optimum bounds every solution that
   keeps the limit.
2. The same problem restricted to the `k` largest weights in magnitude, ties
   broken by asset id, with every other weight fixed at zero.

The result carries both optima. Their difference bounds how far the answer can
be from the best that keeps the limit; it is never claimed to be zero. Exact
cardinality is an integer program and stays a boundary.

## 6. The rebalancing package

- **`alphalab.rebalancing` (N19)** imports `common`, `conventions`, `strategy`,
  `analytics` and `portfolio_optimizer`. Nothing imports it, and a regression
  test pins both.
- **`RebalancingStrategy` decides at a slice's close**, when every price of
  the instant is known. Its triggers state every parameter (AC-12):
  - a calendar trigger: the first slice of each day, week or month, by a
    declared zone, or by the sessions of a declared calendar. The calendar is
    taken by protocol, so `data`'s calendars serve without an import;
  - a threshold trigger: a weight farther than a stated distance from its last
    target;
  - a drift trigger: the book's total distance from its last target beyond a
    stated amount.

  It rebalances when any of them fires.
- **Construction from the run's state.** At a rebalance the strategy reads
  its book and prices from its context. The researcher's model -- a protocol
  -- turns what the strategy has observed into the problem's risk model,
  expected returns and scenarios. The book enters the problem's costs and
  turnover as the current portfolio. The engine estimates nothing on the
  researcher's behalf.
- **Certified answers only.** A construction that is not `OPTIMAL` trades
  nothing. The strategy keeps its book and records why: the status and the
  problem's identity.
- **Targets, never orders.** The weights are rounded to lots by
  `round_to_lots` against the strategy's capital at the slice, and stated as
  `TARGET_QUANTITY` intents for every asset of the universe, zero for one it
  leaves. The run's target path turns them into orders through allocation,
  risk and the OMS, as for any strategy. The optimizer generates no order.
- **Every rebalance is recorded** with its problem's and result's identities,
  so a run's rebalances reconcile to its fills. The strategy's memory -- when
  it last rebalanced, and to what -- is its declared state, captured and
  restored with a paused run.
- **The standalone rules are superseded.** `portfolio_optimizer.rebalance`
  keeps `RebalanceTrigger`, `check_schedule_rebalance`, whose month is thirty
  days, and `check_threshold_rebalance`, whose threshold has a default. They
  stay unchanged until 5.0, documented as superseded by the package's
  triggers; no warning is raised.

## 7. What is recorded

- **Identities.** Every existing construction problem and result keeps its
  identity. Lines are added only for what a problem newly declares: an
  objective, a constraint, the linear method. A run with no rebalancing
  strategy is unchanged.
- **Performance, measured.** WS-12's budget is measured on the implementation,
  not assumed from this record's probe. The pure-Python linear program is the
  reference. If it cannot meet 60 seconds at the budget's size, the budget is
  met by the compute backend (ADR-0057, WS-15, M11), held to the reference by
  a cross-check, as the plan provides. The figure measured is recorded, and an
  unmet budget stays open in the register until then.
- **The ledger.** OFE-002 records CVaR and drawdown as boundaries of a
  certified convex solver. The linear program lifts that boundary when it
  lands; cardinality's and multi-period's stay.

# Settled at implementation (2026-11-22)

1. **The objective protocol.**
   - **Where it lives.** `Convexity`, `Objective`, `QuadraticObjective` and
     `QuadraticForm` are in `alphalab.portfolio_optimizer.construction`,
     exported from the package. `ConstructionProblem.objective` is annotated
     `Objective`; `ConstructionObjective` stays the union of the shipped five.
   - **A quadratic form's curvature is the problem's own covariance.** A
     `QuadraticForm` states a positive multiple of the covariance and a linear
     term over exactly the universe, not an arbitrary matrix. A problem then
     holds one risk model, both certified quadratic methods apply -- the
     factor-structured one included, at its threshold -- and minimum variance,
     mean-variance and a tracking error are each expressible. Its scale is
     finite and positive, and its terms are refused unless they cover the
     universe.
   - **Admitted before anything is solved.** `construct` checks an objective
     that is not one of the package's at its start: one stating no
     `Convexity` and rendering, a `NONCONVEX` one, a `CONVEX` one without a
     method of its own, a `LINEAR` one, a quadratic one without its form, and
     a quadratic one under a volatility cap are each refused with the reason.
     The volatility cap is refused because only minimum variance's check and
     mean-variance's bisection on its aversion take one. A `LINEAR` objective
     waits for the linear program.
   - **Identity.** A shipped objective's lines are its rendering alone, so its
     problem keeps the identity it had. Another objective's lines are its
     class's qualified name, its `Convexity`, then its rendering, so two
     objectives that render alike are two problems.
   - **Tests.** `tests/unit/portfolio_optimizer/test_objective_protocol.py`:
     - the five shipped objectives' classes, and their problems' identities
       pinned as the tree before the protocol computed them;
     - a tracking error whose feasible benchmark is its own optimum, and one
       held to the exact rational optimum under a cap;
     - the dense and factor-structured methods agreeing on a tilted minimum
       variance at 120 assets, identified apart;
     - identity by class and kind, and every refusal.

2. **The certified linear program.**
   - **Where it lives.** `alphalab.portfolio_optimizer.linear`, beside the
     quadratic module: `LinearProgram` -- costs, rows as the quadratic
     module's `LinearConstraint`, and a finite range for every variable, so no
     program is unbounded -- `solve_linear_program`, `LinearSolution` and
     `LinearCertificate`. It imports `common` and its own package only, so the
     package's edges are unchanged. A solution's multipliers are by row
     position, since one constraint's label may name several rows.
   - **The method, as section 3 decides it, with what implementation
     settled:**
     - the bounded standard form. A fixed variable leaves the system. A row
       left with no variable holds or fails by its bound alone, and one that
       fails is its own Farkas certificate. A program left with no row is
       solved exactly, each variable at the bound its cost prefers;
     - Mehrotra's predictor-corrector from the box's centre. The normal
       equations are built from the rows' sparse terms and factored by
       `common.linalg.cholesky`, their diagonal lifted by `1e-12` of its
       largest entry so that dependent rows stay factorable, and each
       direction is refined against the unlifted matrix;
     - the exact finish. The basis is the basic variables when they are
       independent and as many as the rows; otherwise -- a degenerate vertex,
       or an optimal face -- the first independent columns, the basic ones
       first. A basic variable outside its range moves to the bound it
       crossed, and the basis is chosen again, three times at most;
     - **the certificate is a bound that holds for any multipliers.** For
       multipliers `y`, with `d = c - A'y`, every feasible objective is at
       least `b'y + sum_j min(d_j l_j, d_j u_j)`. A point is `OPTIMAL` when no
       row misses its bound by more than the feasibility tolerance -- in the
       row's own units, as construction's tolerance is -- and its objective is
       within the convergence tolerance of that bound. The vertex is checked
       with its own multipliers, then with the iterate's -- at a degenerate
       vertex the basis's multipliers may not prove what the iterate's do --
       and only then the iterate itself;
     - `INFEASIBLE` only by a Phase I Farkas certificate, verified over the
       box, whose margin exceeds the tolerance times its total weight: any
       point within the tolerance of every row would leave less, so none is.
       Otherwise `ITERATION_LIMIT` or `NUMERICAL_FAILURE`.
   - **Linear objectives in construction.** A `LinearObjective` states a
     `LinearForm`: a cost on each weight, over exactly the universe, and any
     `LinearVariable`s and `LinearRow`s its lifting adds. Each variable states
     the finite range it implies; none is supplied (AC-12). `MaximumReturn`
     is the package's own linear objective, and takes `LinearCosts` as
     mean-variance does.
     - **Every weight in a range the constraints imply.** A constraint on one
       weight with coefficient one -- a bound, a concentration or notional
       limit, a one-asset group -- is that weight's range. An absolute-sum
       limit `sum |w_k - c_k| <= L` implies `c_k - L <= w_k <= c_k + L`. A
       weight left without a finite range is refused before solving. Ranges
       that cross are `INFEASIBLE` before any solve, naming both sides.
     - **Limits and costs lifted by a split**, `w_k - c_k = p_k - q_k` with
       `p, q >= 0`: one row per term, where `t_k >= |w_k - c_k|` takes two. A
       limit bounds `sum (p_k + q_k)`. That sum is at least `sum |w_k - c_k|`
       and equals it for the least split, so the limit holds exactly as
       stated. A cost charges `rate_k (p_k + q_k)`, which the optimum makes
       the least split.
     - **A conflict names** the rows the Farkas certificate weights, and the
       ranges it reaches: each variable's upper bound where `(A'y)_j` is
       positive, its lower where negative.
     - **What stays refused.** A volatility cap under a linear objective: the
       cap is quadratic. And a weight that no constraint bounds, as above.
     - **Identity and the risk model.** `solver=linear` names the method in
       the problem's identity, so no existing identity moves. The linear
       program reads no curvature. A structure is never written out for it;
       its definiteness is read from the structure wherever the structured
       method would read it.
     - **An optimum need not be unique**, as under two assets with one
       expected return. The answer is the vertex the finish reads, the same
       on every host. It is certified optimal, not certified unique.
   - **The reference is an exact vertex enumeration, not a simplex.** Section
     3 names an exact simplex. Enumerating every vertex in rational
     arithmetic is simpler and shares nothing with the solver. Over a bounded
     box it proves an optimum by the least objective of all the vertices, and
     infeasibility by there being no feasible vertex. Every Farkas certificate
     the solver returns is verified exactly as well.
   - **Tests.**
     - `tests/unit/portfolio_optimizer/test_linear.py`: 60 generated programs
       against an exhaustive exact vertex enumeration
       (`tests/unit/portfolio_optimizer/lp_reference.py`, which shares no code
       with the solver), reaching both outcomes and both certificates, each
       Farkas certificate verified in rational arithmetic. Hand
       cases cover a degenerate vertex and an optimal edge, each finished
       exactly at a vertex; dependent rows, certified at the interior; a Farkas
       certificate naming its row; shifted and fixed bounds; no row; rows of
       fixed variables. Also CVaR at level 0.9, the bits pinned, and the
       refusals.
     - `tests/unit/portfolio_optimizer/test_linear_objectives.py`:
       `MaximumReturn` against the exact optimum of the same problem stated
       independently -- an absolute sum as its polytope, costs one orthant at
       a time -- over 40 seeds reaching caps, a gross limit, turnover, costs
       and infeasibility. A researcher's CVaR, lifted through the protocol, is
       held to the exact minimum of the largest mean loss over every `k`
       worst scenarios, a different program. Also binding constraints named,
       a trade that does not pay its cost left undone, crossed ranges and
       Farkas conflicts named, the identity pinned, a structure never written
       out, and the refusals.
   - **Performance, measured (section 7).** Construction by the linear
     program over a CVaR lifting, `n` assets and `round(2.52 n)` scenarios
     -- the budget's proportion -- long only, on CPython 3.12.4 on an arm64
     laptop on battery, with no other job of the programme running:

     | Assets | Scenarios | Iterations | Seconds |
     | ---: | ---: | ---: | ---: |
     | 10 | 25 | 11 | 0.017 |
     | 20 | 50 | 13 | 0.073 |
     | 40 | 101 | 14 | 0.428 |
     | 80 | 202 | 16 | 3.04 |
     | 160 | 403 | 17 | 22.7 |
     | 320 | 806 | 25 | 271 |

     The interior point is 99% of the time. Each doubling of the size
     multiplies an iteration's time by about eight, as forming and factoring
     the normal equations grow with the cube of the size, and the iterations
     grow slowly. At 1,000 assets and 2,520 scenarios that is about 320
     seconds an iteration, two and a half to three hours in all, extrapolated
     from these points. **WS-12's budget of 60 seconds is not
     met in pure Python.** It stays open in the register until M11, where
     the compute backend's cross-check meets it (ADR-0057, WS-15), as
     section 7 provides. A measurement at the budget's own size is taken
     before M9 closes.

# Compatibility

- Additive: a package, a solver module, objectives and constraints, and a
  protocol the shipped objectives satisfy. A problem accepts any objective of
  the protocol.
- Narrow reversals: ADR-0043's non-goal, since the optimizer still never
  generates orders; ADR-0046 decision 10's linear-program boundary; and AC-27
  with `nowandfuture.md`'s rule, restated as every solver certified.
- No identity, digest or persisted byte moves for a construction, or a run,
  that declares nothing new.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

- **WS-12's budget is deferred to M11** (settled point 2): construction at
  1,000 assets with CVaR over 2,520 scenarios takes far more than 60 seconds
  in pure Python. Section 7 provides that the compute backend's cross-check
  meets it; the register holds it open until then.

# Consequences

- A construction rule can be backtested as a strategy, with its rebalances
  reconciled to its fills. S-01's monthly, factor-neutral, sector-capped,
  CVaR-limited rebalance is expressible, and M9's exit gate is a rebalancing
  backtest with CVaR, certified.
- CVaR and drawdown construction are certified as quadratic construction is.
- Not here: exact integer programs, multi-period construction, and, at M9, a
  quadratic objective under a scenario limit.
- The plan's commit order follows: the objective protocol, the certified
  linear program, the rebalancing strategy and its triggers, then the example
  and the guide.
