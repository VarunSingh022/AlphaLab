# ADR-0037: Strategy Research — Features, Validation, and Overfitting

## Status

**Accepted and implemented in v3.2.0.**

The second capability release on the architecture frozen at v3.0.0. It deepens
two packages rather than adding one: `alphalab.factor_library` becomes the
feature and factor research engine it was designated to be, and
`alphalab.research` gains the validation methodology. One module is added to
`alphalab.common`. No boundary moves, no ownership changes, and every v3.1
invariant holds.

Depends on **ADR-0036** for the dataset version this builds lineage on,
**ADR-0017** for the substitution guarantee it extends to studies,
**ADR-0016** for the identity-derivation scheme it copies, **ADR-0034** for the
`MappingProxyType` immutability idiom, and **ADR-0019**, **ADR-0020** and
**ADR-0033 decision 10** for the rule against invented defaults.

---

# Context

v3.1 gave AlphaLab a dataset it could trust: versioned, provenanced, cleaned
under a stated policy, with every rejected row and every applied change
recorded. What it could not do was answer the question research starts from —
**does this signal predict anything, and would the answer survive contact with
data it was not fitted on?**

`alphalab.research` existed and did something else. It consumes a
`ResearchPayload` — a *completed run's* returns, trades and parameters — and
scores it. That is a useful layer and it is unchanged by this ADR. It is also
not research methodology: everything it touches has already happened, so there
is nothing it could leak and no decision it could contaminate.

Concretely, before v3.2 the repository held:

* **No computational feature definition.** `FeatureMetadata` is a catalogue
  record — owner, description, dependency tags — and carries no field, no
  window and no parameters. It describes a registration, not a computation.
* **No feature computation at all.** Feature Store's own docstring says it
  computes nothing and names "a future Factor Library" as the consumer that
  will. Factor Library held six single-asset style factors and no framework.
* **No cross-sectional machinery.** No ranking, no neutralization, no forward
  returns, no information coefficient, no decay, no turnover, no exposure.
* **No split generation of any kind**, and therefore no purging and no embargo.
* `walk_forward_analysis`, which cuts a finished return series into equal
  chunks and reports Sharpe consistency across them. A reasonable diagnostic,
  and not walk-forward validation.
* `parameter_robustness`, which derives an instability index from the *number*
  of parameters. It perturbs nothing and evaluates nothing; its docstring says
  so.
* **No correlation, rank, quantile or z-score anywhere**, and five private
  copies of the unbiased sample variance.

The last one matters more than it looks. Five copies of one estimator agree on
every test value and diverge the moment somebody changes one of them, and a
release that added a sixth while building a statistics layer on top would be
compounding the problem it was meant to solve.

---

# Decision

## 1. The computation engine is Factor Library, because the architecture already said so

Feature Store owns registration, versioning, metadata, validation and caching,
and **does not compute**. That is not an accident of v2; it is stated in the
package docstring, and `FeatureValueProtocol` is the seam it was designed to be
written through. `FactorResult` already satisfies that protocol structurally,
with neither package importing the other.

So the v3.2 feature framework goes into `alphalab.factor_library`. The
alternatives were both worse:

* **Putting computation in Feature Store** would give one package two jobs and
  make "what a feature is" have two owners.
* **A new `alphalab.features` package** would be a second authority for a
  concept that already has one, which the release's own success criteria make
  release-blocking.

`to_factor_results` is the join, and it is the only place the two vocabularies
meet. The structural decoupling is now asserted in both directions by
`tests/regression/test_one_research_authority_per_concept.py`.

## 2. A feature definition defaults nothing

`FeatureDefinition` carries the kind, the source field, the window **in
periods**, the parameters and the missing-data policy. Every one of them is
stated, and the ones that could plausibly be defaulted are the ones most
carefully refused:

* A kind that reads a window and was given none **raises**. A default lookback
  is a research decision made by the library.
* A kind that takes no window and was given one **raises**, rather than
  accepting and ignoring it. A parameter that is silently discarded leaves a
  caller believing they set something — the rule ADR-0017 applied to
  `evidence_from_backtest`'s removed `dataset_id`.
* A parameter the kind does not read **raises**. This one is subtler: an unread
  parameter changes nothing about the computation and *does* change the derived
  feature version, which would give one computation two identities.

Windows are counts of observations rather than spans of time. A scheme
expressed in days gives a fold spanning a holiday week fewer observations than
its neighbour, and the two are then not comparable.

## 3. Missing values are never invented, one layer up

`MissingPolicy` has two members and neither fills anything. `REFUSE` raises;
`SKIP` emits no point and the series is shorter and says so.

This is ADR-0036's rule applied above the data layer. `alphalab.data` has no
way to fill a missing price because the absence is structural; a feature layer
that forward-filled would reintroduce exactly the bias the data layer refuses
to create. A forward fill is a statement that yesterday's value was still true
today, which is a claim about the world rather than an arithmetic convenience.

## 4. Feature identity is derived; feature *lineage* is where the dataset meets it

`derive_feature_version` hashes a canonical rendering of the definition,
following `derive_dataset_version` line for line: a scheme tag on the first
line, `label=value` lines joined by newlines, a fixed order for the closed
fields, sorted order for the open mapping, SHA-256 over the result.

A feature version deliberately **excludes** the dataset. A definition is
reusable across datasets — that is the point of having one — so binding a
dataset into it would make "the same feature on two universes" two features.

The join is `FeatureSeries`, which carries both identities plus the symbol and
the zone, and derives a `lineage_id` from the four. So the property the release
requires holds by construction: the same dataset version and the same
definition produce the same lineage id on any machine with no shared state.

`require_lineage()` refuses a series computed from a dataset with no
provenance, matching `Dataset.require_provenance()`. An untraceable series must
never be indistinguishable from a traceable one.

The identity is over **inputs**, not values. It has to be derivable before the
computation runs, so that a cache key, a fold's provenance and a study's
configuration can name a feature that has not been materialized. "Did two runs
agree?" is a different question, answered on the study result, whose identity
*is* content-derived.

## 5. Three neutralizations, not one function with a `method` argument

"Neutralize the factor" names three different operations, and a single function
would be claiming they are the same:

* `neutralize_mean` removes the level.
* `neutralize_group` removes what the groups explain. For a design matrix of
  mutually exclusive indicators the least-squares fit *is* the group mean, so
  this is the exact residual of a cross-sectional regression on group dummies,
  computed with no matrix to invert.
* `neutralize_beta` removes what one continuous exposure explains, through
  ordinary least squares with an intercept.

A factor that is sector-neutral is not market-neutral, and neither is
dollar-neutral. Each records what it did on the panel's transform chain.

**Neutralization against several continuous exposures at once is not offered.**
It needs a general least-squares solve, and a rank-deficient or
nearly-collinear design — which factor exposures routinely are — produces
residuals that look like a result and are numerically meaningless. The
release's own instruction is to implement only methods that can be made
deterministic and well-defined; a solver whose failure mode is a plausible
wrong answer does not meet that bar. Composing two neutralizations is
supported, and the transform chain records that this is what was done.

## 6. Purging is defined by information windows, not by subtracting dates

Splitting a series at an instant does not separate it, and two things cross the
cut in opposite directions. A training row's **label reaches forward**: a target
that is a 20-period forward return depends on prices up to `t + 20`. A training
row after the validation block has a **feature reaching backward**: a 60-period
moving average depends on prices inside it.

Purging removes the first; embargo removes the second. Both remove from the
*training* set; the validation window is never touched, because shrinking it
would change what is being measured.

The implementation is set membership rather than arithmetic.
`label_ends_from_horizon` reads the actual series and reports, for each instant,
the instant its label is realized — twenty observations later means twenty
observations later, whether that is twenty-eight calendar days across a holiday
or twenty. "Drop the last N days" is right only if every observation's label
spans exactly N days, and quietly wrong for any series with gaps.

Two consequences follow and both are deliberate:

* The final `horizon` instants of a series have **no realized label** and map to
  infinity, so they are purged from every training set. Keeping them means
  training on rows whose target had to be invented.
* `PurgePolicy` **has no default horizon and cannot be built without one**. A
  default of zero would make every purged split silently identical to an
  unpurged one while reporting that purging had been applied — worse than not
  purging at all.

## 7. A scheme's name is a claim, and is enforced

`CVMethod.PURGED` and `CVMethod.EMBARGOED` require a policy. `EMBARGOED`
additionally requires a positive embargo, and `PURGED` **refuses** a policy
that states one — applying it would produce exactly `EMBARGOED`'s folds under
the other name, and ignoring it would leave the caller believing an embargo was
in force.

`PURGED` and `EMBARGOED` use blocked k-fold, where each block in turn is the
validation set and training data exists on **both** sides of it. That is what
makes the backward leak possible and therefore what makes an embargo a real
protection rather than a ceremonial one. On the forward-only schemes an embargo
removes nothing, and the report reads zero so a caller can see that rather than
believing a protection was applied.

A blocked fold is **not** forward-only and says so through
`TimeSplit.is_forward_only`. It is a k-fold estimate of generalization, not a
simulation of trading: a trader does not have next year's data. Both are
offered because they answer different questions.

## 8. Walk-forward purges twice

The training set is purged against the validation span, and the **validation
set is purged against the test span**.

The second is usually left out. Without it, a parameter can be chosen using an
outcome that overlaps the test window, and the test score is contaminated by
the *selection* rather than by the fit — a subtler leak, and one that survives
every check that only looks at the training set.

A consequence worth stating: a 20-period label horizon with a 20-period
validation window leaves nothing to select on, and `walk_forward_splits`
refuses rather than carrying on. That is not a corner case; it is a
configuration that cannot be validated.

## 9. A fold carries its instants, not its bounds

`TimeSplit` holds the timestamps of each part. That costs memory — a split over
a million observations holds a million floats — and buys two properties the
release makes release-blocking:

* **Every fold is independently inspectable.** A leakage test can assert the
  exact membership rather than that a boundary function returned what it was
  asked for.
* **Purging can be a set operation.** An interval cannot express "everything up
  to here except the last eleven observations, whose labels reach into the
  validation window".

`benchmarks/benchmark_research_validation.py` reports what it costs at each
size.

## 10. There is no overfit score, and there will not be one

A single number blending sensitivity, degradation and the size of the search
would have to weight them; the weights would be a judgement nobody could
inspect; and the figure would be **unfalsifiable** — there is no experiment
that shows an overfit score of 63 to be wrong.

`OverfittingReport` keeps three kinds of thing in separate fields:

* **Measured quantities** — facts about the runs that were performed.
* **Thresholds** — bounds the caller stated in advance. AlphaLab ships no
  default policy, and an `OverfittingPolicy` that bounds nothing is refused,
  the same reasoning `ValidationPolicy` applies in `lifecycle.evidence`.
* **Findings** — one sentence per crossed bound, naming the value and the
  bound, so each can be checked.

A bound stated for a measurement that was not taken produces a finding rather
than silently passing. An absent measurement is not a passing one.

**Bonferroni is the only correction offered**, because it is the only one whose
assumption fits in a line: for `k` configurations tried, a nominal alpha
becomes `alpha / k`. It is conservative when trials are correlated — which
neighbouring parameter values strongly are — and the report says so. Šidák and
false-discovery-rate corrections need distributional assumptions this package
cannot check; a deflated Sharpe ratio needs the variance of the trial
statistics *and* normality that daily returns do not satisfy.

`trials` counts configurations **evaluated**, not reported. A sweep that tried
two hundred windows and wrote up the best one has two hundred trials, and
`parameter_sweep` counts them by construction.

## 11. No p-value on an information coefficient

The standard `t = IC_mean / (IC_std / sqrt(n))` treats per-instant ICs as
independent. Overlapping forward-return windows make consecutive ICs strongly
autocorrelated by construction — a horizon of 20 on daily data shares 19 of its
20 days with the next instant's — so the statistic is inflated by a factor that
depends on the overlap.

`InformationCoefficient` reports the per-instant series, the sample counts and
a hit rate that makes no claim to be a test. A caller who can model the overlap
has what they need; the package does not pretend to have done it.

## 12. Stochastic means seeded, with no default

Every function in `alphalab.research.perturbation` that draws a random number
takes an explicit `seed` with no default, and `Perturbation` refuses a
stochastic kind carrying no seed. It also refuses a *deterministic* kind
carrying one: a seed recorded on a run that drew no random number suggests a
reproducibility guarantee it has nothing to make.

`random.Random` is reproducible across Python versions for a given seed, the
same guarantee `DeterministicIdSource` rests on, and
`tests/regression/test_research_is_reproducible.py` proves it across a real
interpreter boundary rather than asserting it.

Two constructions differ deliberately from their v2 namesakes:

* `block_bootstrap_indices` draws **contiguous blocks**, because an IID
  bootstrap destroys the serial dependence a time-series result rests on.
  `research.bootstrap.bootstrap_statistics` resamples a completed run's returns
  independently, which is right there and wrong here.
* `monte_carlo_orders` draws each path **afresh from the original order**, so a
  path is a function of the seed and its index alone.
  `research.montecarlo.monte_carlo_simulation` shuffles one list repeatedly in
  place, which is fine for a summary over a thousand paths and unusable if
  somebody wants to reproduce path 500.

Both pairs are recorded in `tests/regression/test_shared_names_stay_distinct.py`.

## 13. A study names its dataset, and `run_study` checks

`ResearchStudy` derives an identity from its description; `StudyResult` derives
one from the study **and** the numbers, which makes it tamper-evident.
`produced_at` is recorded and never hashed, following `DatasetProvenance`'s
treatment of `retrieved_at`: re-running the same study tomorrow must reproduce
the same identity.

`run_study` takes the dataset as an argument and **compares** it against the
study's recorded version rather than trusting either. This closes, at the study
level, the hole ADR-0017 closed for evidence: an identity that hashes a string
somebody typed is only as trustworthy as the typing.

`build_result` additionally refuses a result naming lineage for a feature the
study does not run.

## 14. `ValidationMethod.STUDY`, and the digest does not move

A study result becomes evidence through `evidence_from_study`, which derives
the dataset from the study rather than accepting one, and refuses a result that
no longer verifies.

`evidence_id_for` is **unchanged**. The rendering hashes `method.name`, so
adding an enum member changes no existing identity: every promotion recorded
since v2.6 still verifies. This is the same property v3.1 preserved through the
dataset-version change, and `tests/integration/test_v32_capabilities.py` pins
the digest against a literal.

## 15. One statistics authority, and five copies consolidated onto it

`alphalab.common.statistics` is the one home for deterministic statistics. It
lives in `common` for the reason `point_in_time` does: the arithmetic has no
domain in it, and both `factor_library` and `research` need it. A correlation
that meant two slightly different things in those two places would make a
diagnostic incomparable with the factor it was measured on.

Adding it made the pre-existing duplication release-blocking rather than
merely untidy, so the five private copies of the unbiased sample variance were
consolidated onto it — the same expression in the same order, so every
published number is unchanged. Each caller keeps its own guard: the shared
function raises on fewer than two observations, and the callers still return
`0.0`.

`analytics.metrics.sortino_ratio` deliberately does **not** delegate. Downside
semideviation divides the sum of squared negative excess returns by the count
of *all* returns, which is a different estimator rather than the same one
applied to a subset, and routing it through the shared function would quietly
change every Sortino ratio AlphaLab has reported.

Every function refuses an undefined statistic rather than returning a
placeholder. A placeholder is indistinguishable from a real measurement once it
has been rounded and put in a table. Callers that must *report* insufficiency
rather than fail check the sample first and say so in their own result: the
mathematics refuses, the diagnostic explains.

## 16. Applicability is advice, not a gate

`feature_applicability` answers, per feature and per `DataAssetClass`, whether a
computation means anything — with a reason rather than a boolean, because "an
index has no volume" and "a return on an interest rate means nothing" are true
for completely different reasons.

It is composed from two small tables rather than a matrix of nineteen kinds
against eleven fields against twelve classes, which would be two and a half
thousand cells, most of them copied, and stale within a release.

Nothing enforces it inside `compute_feature`. The computation layer refuses
what it cannot compute, mechanically; applicability is a *methodological*
judgement about whether a defined number is worth reading, and a library that
silently blocked a caller from computing one would be making a research
decision on their behalf. `require_applicable` exists for a caller who wants a
gate.

---

# Consequences

## What this makes possible

An external application can hand AlphaLab a dataset and a study description and
receive a result that names the exact bytes, the exact feature definitions, the
exact folds and the exact numbers — reproducible on any machine, tamper-evident,
and recordable as evidence against a promotion policy.

## What it costs

* **Memory.** A `SplitReport` over a long series holds every instant of every
  fold. Measured in `benchmark_research_validation.py`.
* **Time.** A fixed-window feature is `O(n * w)` rather than `O(n)`, because an
  incremental rolling sum accumulates floating-point drift across a million
  updates and the same window computed early and late in a long series would
  not agree. `EXPONENTIAL_MEAN` is incremental because an EWMA has no windowed
  form.
* **Surface.** `alphalab.factor_library` and `alphalab.research` both roughly
  quadruple in size. `tests/regression/test_one_research_authority_per_concept.py`
  is the standing answer to the risk that comes with that.

## What is deliberately still absent

Multi-regressor continuous neutralization; a deflated Sharpe ratio; Šidák and
FDR corrections; a t-statistic on an IC; a fitted half-life; and any blended
score for overfitting, signal quality or research grade. Each is in
`ROADMAP.md` under deliberate boundaries with the reason.

## The invariants this release adds

1. No feature reads an observation after the one it is computed for. Asserted
   for **every** kind by recomputing on a truncated series.
2. No fold's parts intersect, on any scheme.
3. Purging leaves a gap wider than the label horizon, on every scheme.
4. No research identity depends on a clock — asserted at runtime and read from
   the source.
5. Every stochastic entry point takes a seed, enumerated from the source.
6. One authority per research concept, measured on every run.
