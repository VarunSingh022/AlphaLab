# ADR-0040: Strategy Execution and Production Intelligence

## Status

**Accepted and implemented in v3.5.0.**

The fifth capability release on the architecture frozen at v3.0.0. It deepens
one package — `alphalab.lifecycle` — and adds none. No ownership boundary moves,
no snapshot schema changes, and every v3.1, v3.2, v3.3 and v3.4 invariant holds.

Depends on **ADR-0013** for the model and strategy lifecycle this extends,
**ADR-0012** and **ADR-0031** for the broker boundary the reconciliation side
speaks through, **ADR-0017** for the derived-identity rule the deployment
specification follows, **ADR-0018** for the actor field a transition record
carries, **ADR-0019** and **ADR-0020** for the currency roles the comparison
obeys, **ADR-0022** for the seeded identifier stream the alignment rests on,
**ADR-0028** for the settlement boundary, **ADR-0030** for the rule that a value
a caller holds beats a field on a snapshotted state, and **ADR-0033 decision 10**
for the rule against inventing a policy nobody chose.

---

# Context

At v3.4 AlphaLab could research a strategy, measure it, promote it on evidence
and record that an environment should be running it. Everything after that was
outside the library.

Five things were missing, and each of them is a question somebody asks on the
day a strategy stops being research:

**1. There was no way to say where a strategy was.** AlphaLab had two state
machines with "lifecycle" in their name and neither answers it:

| Type | Question | Members |
| --- | --- | --- |
| `model_registry.ModelStage` | is this registered artifact promotable? | `NONE`, `STAGING`, `PRODUCTION`, `ARCHIVED` |
| `strategy.state.LifecycleState` | is this instance in a session running? | `CREATED` … `DISPOSED` |

Research, backtest and validation are all `NONE` to the registry: a strategy
nobody has measured and one that has passed a walk-forward are the same stage.
Paper and live are both `PRODUCTION`, separated only by the free-form
environment string a deployment named. And there is no member for paused at all,
because a registry entry does not pause.

**2. A deployment recorded *that* something should run, never what it needs.**
`deploy_strategy_version` makes a release active in an environment. Which data
the strategy assumes, how much capital it has, under which limits, on a broker
that can do what, against which freshness and latency budgets — all of it lived
in somebody's head, or in a `ReleasePackage`'s flat `config` mapping of strings.

**3. Health was a list of sentences.** `runtime.live.live_health` has answered
"should a human look at this?" since v2.16, and its own docstring is honest
about the form: "everything wrong with this live run right now, **in plain
sentences**". Nothing can count sentences by kind, act on the severe ones,
compare today's against yesterday's, or say what number was wrong and what
number would have been acceptable. It also reads a `LiveRunState` directly, so
it can only speak about a run this process is driving, and it knows no
thresholds because a run does not carry any.

**4. Nothing compared a backtest to what actually happened.** A backtest
produces trades, fills, P&L and exposure; so does a paper run; so does a live
account. All three were readable and none of them was comparable: nothing said
which backtested fill corresponds to which live one, and nothing said how far
apart two numbers may be before the difference means something.

**5. One reconciliation existed, for the other pair.**
`broker.reconciliation.reconcile` compares `BrokerState` — AlphaLab's *mirror of
a venue* — against records that venue reported. The pair nothing compared is
AlphaLab's *own execution state* — the OMS book, the portfolio, the fills it
applied — against that mirror. Those drift for different reasons: the venue and
the mirror drift because a message was lost, the mirror and the book drift
because a fill reached one and not the other.

---

# Decision

## 1. The five capabilities extend `alphalab.lifecycle`, and add no package

`alphalab.lifecycle` is documented as the package that "adds no engine… it
composes packages AlphaLab already had". Its charter is the path from a research
run to a rolled-back deployment; v3.5 carries that path past the deployment
record into the thing a deployment becomes.

Three properties make widening it safe, and all three are measured by
`tests/regression/test_v35_invariants.py`:

* **Nothing imports it.** `alphalab.lifecycle` has no in-repo consumers, so new
  edges out of it cannot close a package cycle — the invariant
  `test_import_graph_stays_acyclic.py` has held since v3.1.
* **It already sits above the execution path transitively**, through
  `alphalab.backtesting`. v3.5 makes edges to `runtime`, `broker`, `oms`,
  `portfolio`, `risk`, `execution`, `core` and `data` direct rather than implied.
* **It gains no durable state.** See decision 3.

## 2. `StrategyLifecycleStage` is a third axis, related to `ModelStage` and not replacing it

Eight members, the roadmap's, with a declared transition table
(`LEGAL_PROGRESSION_TRANSITIONS`) stated once and read by both the refusal
(`illegal_progression_move`) and the act (`advance_progression`).

Three decisions inside it are worth recording:

**Backward moves are legal; forward skips are not.** A paper run that behaves
unlike its backtest sends the strategy back to `VALIDATION`. Refusing that would
mean the only way to record a step backwards is to archive the version and
register another, which loses the line between what was learned and what
replaced it.

**A pause returns to the stage it interrupted, and the target is derived from
the history.** Only `PAPER`, `PRODUCTION_CANDIDATE` and `LIVE` can be paused —
pausing research means nothing. `resume_target` reads the last transition *into*
`PAUSED`, so a paper strategy that pauses cannot resume into `LIVE`. Promotion
through a pause is the one way this vocabulary could have been used to skip a
stage, and it is refused by name. The target is derived rather than stored
because the history already records it and a second copy is a second thing to
keep true.

**The progression names no environment.** `ROADMAP.md` records a deliberate
boundary — "a strategy version has one stage across all environments… a policy
that differs between `paper` and `live-eu` is not expressible, and making it so
would put a second source of truth beside the deployment ledger." A
`StrategyProgression` is keyed by `StrategyVersionRef` and carries exactly three
fields. `PAPER` and `LIVE` are maturity, not addresses.

`PROGRESSION_MODEL_STAGES` states, totally and in the open, which `ModelStage`
values each progression stage is consistent with, and
`progression_conflicts` **reports** a disagreement rather than resolving it —
the position `run_plan` already takes when a version's stage and the deployment
ledger disagree.

## 3. Nothing v3.5 adds is a field on `LifecycleState`

`LifecycleState` has one snapshot owner and one module-local schema literal
(`LIFECYCLE_SNAPSHOT_SCHEMA = 2`), and AlphaLab has **no migration framework**:
each snapshot subsystem supports exactly one schema version and refuses any
other. A new field there would make every payload written before v3.5
unreadable, to serve a value the caller can simply hold.

So a progression, a specification, a health report and a reconciliation are all
values produced by a function and recorded by whoever asked for it — the shape
`RunAuthorization` already has, for the reason ADR-0030 decision 2 gives about
`RunState`. v3.5 adds no `capture`, no `restore` and no schema constant, and a
regression test asserts the absence.

## 4. A deployment specification distinguishes four things that are not the same

| | |
| --- | --- |
| **Research assumption** | what a measurement was taken over. A `DatasetAssumption` names a *derived dataset version*, so it identifies exact bytes under an exact configuration. |
| **Deployment requirement** | what the environment must supply. `BrokerRequirements`, `MarketRequirements`, `RuntimeRequirements`, `RiskLimits`, `CapitalPolicy`. |
| **Runtime observation** | what was seen. Owned by `lifecycle.health`. |
| **Current runtime state** | what is running. Owned by the execution path and the deployment ledger. |

`specification_for_version` **derives** the reference and the parameters from
the registered `StrategyVersion` rather than accepting them, which is ADR-0017's
lesson about `evidence_from_backtest`: a value a caller types is hashed into the
digest and is only as trustworthy as the typing.

`dataset_assumption_from` goes through `Dataset.require_provenance()`, so a
dataset assembled from rows already in memory is refused rather than recorded
with an invented identity.

`RiskLimits` is carried whole. A specification describing limits in its own
shape would be describing something the pre-trade gate does not enforce.

**Identity is a content digest**, the fourth use of the construction
`evidence_id_for`, `compute_checksum` and `derive_dataset_version` share:
`label=value` lines joined with newlines, open key sets sorted, closed field sets
in declared order, every number rendered with `repr`.
`DEPLOYMENT_SPECIFICATION_SCHEME` tags it, so a future change to how
specifications are identified is deliberate and old ids stay recognisable.

`validate_specification` **reports** rather than refuses. Each constructor
already refuses a value wrong on its own; validation is for pairs of values that
are each individually fine and cannot both be true, and a half-drafted
specification must stay representable.

## 5. Health is total over its categories, and a missing observation is not a healthy one

Every field of a `RuntimeObservation` except `observed_at` is optional, and
`None` means **not observed** — never "nothing happened". An empty tuple means
the opposite, and the two produce different reports.

`evaluate_health` is total over `HealthCategory`: each of the seven is either
evaluated or listed in `HealthReport.unevaluated` with the reason it could not
be. `HealthStatus.UNKNOWN` exists so that a report with no findings and an
unevaluated category cannot read as `HEALTHY`. That is the missing-data-as-
success reading this release exists to make unspellable, and a regression test
sweeps every subset of the seven to prove it.

`observed_at` is supplied; nothing in the module reads a clock. A broker
disconnect reaches a report through `ConnectionStatus`, the normalized state an
adapter already reports — never by adding a broker client.

`observation_from_live_run` is the bridge from the live driver, so the two
health surfaces cannot disagree about a run they can both see. It deliberately
derives **only** what the run actually holds, and leaves the feed's newest stamp,
the expected positions, the risk violations and the state expectations to the
caller: supplying only the observed side of a comparison would make every
comparison pass.

## 6. Comparison declares its alignment and states every tolerance

`AlignmentKey` has exactly two members because there are exactly two honest
answers. `ORDER_ID` is correct when both runs were seeded from the same
`RunConfig.seed` and therefore draw the same identifier stream (ADR-0022);
`ASSET_AND_TIME` is correct otherwise. There is no third mode that infers one,
and no default: a wrong alignment compares a Tuesday's fill against a
Thursday's and reports the difference as slippage.

A metric with no `Tolerance` in the supplied mapping is reported
`NOT_COMPARABLE`, never as matching. A hidden `== 0` calls a one-cent difference
a break; a hidden `0.01` says a cent is fine for a position count. Both are
policies nobody chose.

`ComparisonOutcome` has six members and the last three are why it is not a
boolean: "no expected value", "no observed value" and "nobody stated a
tolerance" are each a third thing.

Money is compared **per currency**, walking the union of the currencies the two
sides hold and reading each with `CurrencyAmounts.of`. An absent currency inside
a *supplied* accumulation is a real zero, because that is what `CurrencyAmounts`
means; the accumulation itself being `None` is the missing case.

**Exposure is supplied, not computed here.** What a book means by exposure
depends on whether it holds shares or contracts, and a third exposure authority
in a comparison layer would be the one that forgot the multiplier. A regression
test reads the module's source to keep it from appearing.

Latency is `Decimal` seconds in the comparison layer, because every other
quantity it compares is an exact `Decimal` and a single float among them would
be the one value whose tolerance arithmetic is inexact. `lifecycle.health` keeps
its budgets as `float` seconds because it subtracts float timestamps and
compares them directly, with no tolerance arithmetic at all. Both name the unit
in the field, and a sweep enforces it.

## 7. Reconciliation compares the other pair, and declares neither side authoritative

`reconcile_execution_state` takes an `ExecutionPipelineState`, a `BrokerState`,
the `ExternalOrderMap` that already owns the OMS-order-to-venue-handle binding,
a `SymbolMapping` and a `ReconciliationTolerances`. Every value it compares is
read from an existing authority; it defines no order, fill, position or account
type of its own, and a regression test asserts that.

Four scoping decisions:

**Only orders the mapping binds are expected at the venue.** A working OMS order
with no venue handle was never routed, and its absence there is not a break —
that it is unrouted at all is a live-driver question `live_health` already
answers.

**Only the currency the broker account names is compared.** A `BrokerAccount` is
denominated in one currency; every other currency in AlphaLab's ledger is
reported as an `UnreconciledArea` rather than as a difference of zero. Hence two
properties, `reconciled` and `fully_reconciled`: agreeing about what was
compared and having compared everything are different facts.

**Instruments join only through a supplied `SymbolMapping`.**
`SymbolMapping.identity()` is a *named* choice a caller makes when the venue's
symbols already are asset ids — true of AlphaLab's own routing, where
`routable` sets `BrokerOrder.symbol` from `Order.asset_id`, and of nothing else.

**`BROKER_STATUS_EQUIVALENTS` states the relation between the two status
vocabularies once, in the open.** `BrokerOrderStatus` covers states that exist
between AlphaLab and a venue and nowhere else, so comparing them to OMS statuses
for equality would report every in-flight order as a break.

A `Mismatch` says what each side holds and stops. Deciding what to do about a
missing order is a policy question with no safe default — re-sending one that
actually exists would duplicate it — so that decision stays with the caller,
exactly as `broker.reconcile` already puts it.

---

# Consequences

**One new tolerance authority, deliberately.** `lifecycle.tolerance.Tolerance`
is shared by health, comparison and reconciliation. Three private copies of
"how close is close enough" is how one of them comes to differ from the other
two.

**Three lifecycle state machines now coexist**, pinned as a triple in
`tests/regression/test_shared_names_stay_distinct.py` along with the two
reconciliations and the two health surfaces. Each entry says what a merge would
have to break first.

**A defect found by the benchmark and fixed.**
`benchmarks/benchmark_strategy_execution.py` measured 50,000 pause/resume cycles
at 4,301 ops/sec: `resume_target` materialized the whole transition log before
reversing it, so a strategy that pauses daily cost time quadratic in its own
history. It walks the log backwards by index now — 457,897 ops/sec, and
`test_v35_complexity.py` holds both the per-read and the per-cycle growth.

**What this release does not add**, and each is unchanged from the v3.4
position:

* no broker connectivity, client, credential or vendor adapter;
* no automated control of anything at a venue;
* no supervised live *process* — restart policy, alerting and scheduling stay an
  operator's concern;
* no remediation: health and reconciliation detect and report, and mutate
  nothing;
* no production deployment infrastructure. A deployment remains a lifecycle
  fact, not an operation on a machine.
