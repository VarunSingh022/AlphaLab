# ADR-0064: The Research Model Protocol and Its Artifacts -- A Trained Model Is Its Bytes

## Status

**Accepted** (2026-11-16). It is the record the v4.3.0 plan needs before M7
(plan section I.2, engine N8, workstream WS-08). The owner delegated the
programme's decisions within the charter's principles on 2026-11-10 (ADR-0056),
and this record is that decision for N8.

It depends on ADR-0053 (run and input provenance), ADR-0057 (optional
distributions) and ADR-0058 (the identity of an estimate). It reverses no
accepted ADR.

---

# Context

**A fitted model already reaches the execution path, by hand.** Example 73
fits a linear model on samples knowable before a boundary and trades it after.
The coefficients are the strategy's declared parameters, so they are in its
fingerprint, captured and restored exactly
(`tests/integration/test_model_on_the_execution_path.py`). Nothing generalises
this. A gradient-boosted ensemble has no coefficients to declare, and a
framework's model is an object whose identity is its serialisation.

**What a model's identity cannot be: the training run.** A GPU, a thread pool
or a framework's version can make two runs differ in their last bits. Two
trainings that produce different models are two models, and asking which of
them is "the" model of a training run has no answer.

**What `ml` holds today.** QR-ridge linear regression and gradient-descent
logistic regression, splits and metrics, and no common interface. Its dataset
builder reads `feature_store.latest_value`, so it is not point-in-time
(GAP-13).

# Decision

## 1. `ml.protocol.Model`

A research model implements one protocol:

| Member | Contract |
| --- | --- |
| `model_kind` | A name for the family and its implementation, such as `"alphalab:ridge"` or `"sklearn:GradientBoostingClassifier"` |
| `hyperparameters()` | Every choice the model was given, declared and typed as parameter values |
| `fit(training, *, seed)` | Pure: returns a new fitted model, and never mutates this one |
| `predict(features)` | One prediction per row, from a fitted model only |
| `capture()` | The fitted model's canonical bytes |
| `restore(artifact)` | A fitted model from its bytes, refused when the bytes are not one |

- **A training set states its features.** It carries its feature names, its
  rows, its labels and any sample weights, and its identity is derived from
  those values. A fitted model records the feature names it was fitted on,
  and refuses rows that name others, or name them in another order.
- **A prediction** is the model's value for a regression and the probability
  of the label 1 for a binary classifier.
- **Hyperparameters are parameter values**, so an experiment's candidate can
  carry them and a search over them is a `ParameterSpace` (section 6).

## 2. A trained model is its artifact

- **Its identity is its bytes.** `capture()`'s bytes are filed in an
  `ArtifactStore` and named `alphalab-artifact:sha256:<hex>`. A retrain that
  produces other bytes is another model, whatever its seed.
- **The strategy declares it.** A strategy that holds a model declares the
  model's artifact id among its parameters. So the id is in the strategy's
  fingerprint and the run's configuration, and a rerun given another artifact
  is `INPUTS_DIFFER` (ADR-0053). A restore from bytes whose digest is not the
  declared id is refused.
- **The training is recorded beside it.** The seed, the hyperparameters and
  the training set's identity are recorded with the artifact, never in its id.

## 3. Reference implementations in the core

The core's models are pure Python:

- ridge regression on M2's least squares;
- logistic regression by Newton's method, each step a weighted least-squares
  solve;
- a small tree ensemble, gradient boosting on regression trees. It is the
  protocol's oracle for what an adapter's boosting model must satisfy.

They capture canonical JSON: sorted keys, and every float rendered by its
shortest `repr`, under a scheme tag. A model fitted by class-2 arithmetic
(ADR-0058) -- the ridge and the squared-loss ensemble -- is the same bytes on
every host. A model fitted through `exp` and `log` is held across hosts by a
stated tolerance, and its bytes are its own.

## 4. Adapters live outside the core

`alphalab-models` (ADR-0057) wraps scikit-learn, XGBoost, LightGBM and
PyTorch models. Each adapter defines its canonical capture: the framework's
own serialisation, wrapped with the framework's and the adapter's versions,
which are checked on restore. The core never imports a framework and never
unpickles: an artifact is bytes to it. Each adapter documents the trust its
framework's deserialisation needs.

## 5. Inference inside a run needs no new hook

A strategy holds a restored model and computes its features from its own
point-in-time history, as example 73 does (AC-35).

## 6. Datasets, labels and search come with the protocol

WS-08 builds these beside the protocol at M7, and their details are settled
below:

- point-in-time panels from `factor_library`'s knowledge frames;
- label makers: fixed horizon, triple barrier and meta-labels;
- sample weights by label uniqueness;
- samplers on `research.ParameterSpace`, each evaluated configuration a trial
  in the ledger.

# Settled at implementation (2026-11-17)

Building the protocol settled these points, each held by a test
(`tests/unit/ml/test_model_protocol.py`, `tests/unit/ml/test_reference_models.py`,
`tests/regression/test_model_artifacts_are_pinned.py`).

1. **Two modules.** `ml.protocol` holds the protocol, `FeatureMatrix`,
   `TrainingSet`, the canonical capture and the artifact binding. `ml.models`
   holds the three reference models. A model's fit is a public attribute,
   `fitted`: a `LinearModelFit` or a `TreeEnsembleFit`, or `None` before a fit.
2. **A training set's identity.** `training_id` is the SHA-256 of the scheme
   tag `alphalab.training_set.v1`, the feature names as JSON, and one line per
   row: its values, its label and any weight, each by its shortest `repr`. An
   integer enters as the float it is. An integer no float holds exactly is
   refused. A weight is positive: a row weighted at zero is a row to leave out.
3. **Canonical capture.** `alphalab.model.v1` holds exactly five keys: the
   scheme, the model kind, the hyperparameters, the feature names and the
   state. The keys are sorted, the text is ASCII with no whitespace, floats
   are rendered by their shortest `repr`, and a value that is not finite is
   refused when writing and when reading. A float is read only from a JSON
   float, never from an integer. A restore captures what it read again and
   refuses bytes that differ, so one model has one artifact.
4. **A fit its hyperparameters could not have produced is refused** at
   construction: an intercept on a model stated without one, a tree count
   other than the one stated, a tree deeper than the stated depth.
5. **The binding.** `train_model` fits, files and returns the fitted model
   with its `TrainingRecord`: the artifact id, the kind, the hyperparameters,
   the seed and the training set's identity. `file_model` files under the
   generic media type: the store does not sniff bytes, and a media type for
   one kind would be a guess for an adapter's. `restore_model(kind, store, id)`
   reads through the store's verification, so altered bytes are refused before
   any model reads them.
6. **The ridge.** Each row is scaled by `sqrt(w)`, a `sqrt(penalty)` row is
   added for every coefficient but the intercept, and each column is scaled to
   unit length by `sqrt(fsum)` before M2's QR, under a stated
   `maximum_condition`. A prediction is the `fsum` of the intercept and the
   products. Every step is correctly rounded: class 2.
7. **Logistic regression** maximizes the weighted log-likelihood less
   `(penalty / 2) ||b||^2`, by Newton steps solved as weighted least squares.
   It stops when no coefficient moves more than `tolerance`, and raises
   `ConvergenceError` past `max_iterations`. A probability that rounds to 0 or
   1 refuses the fit: without a penalty, the classes are separated or a
   feature is too large. It is class 3.
8. **The tree ensemble** boosts by Newton steps: the residuals with unit
   Hessians for the squared loss, and `y - p` with `p (1 - p)` for the
   logistic loss.
   - **Splits.** Exact greedy splits over every boundary between distinct
     values maximize `G_L^2 / H_L + G_R^2 / H_R - G^2 / H`. Ties go to the
     first feature, then the lowest threshold, and a split is made only when
     its gain is positive.
   - **Thresholds.** A threshold is the largest value on the left, never a
     midpoint, which can round onto the next value.
   - **Leaves and predictions.** A leaf adds `learning_rate * G / H`, and a
     prediction adds the trees in their order, as training did.
   - **Subsampling.** A subsample is the rows ranked first by SHA-256 of the
     seed, the tree and the row. That is the same on every interpreter, unlike
     `random.sample`, whose algorithm is CPython's.
   - **Class.** The ensemble is class 2 with the squared loss and class 3 with
     the logistic loss.
9. **`BoostingLoss`'s values are a persisted format.** Every capture states
   the loss, so its values are pinned beside the other enums a decoder reads
   (`tests/regression/test_persisted_enum_names.py`).
10. **Names that mean one thing.** The fitted linear state is
    `LinearModelFit`, not `LinearFit`: `common.statistics` already exports a
    `LinearFit`, a different object. A tree's nodes are `TreeSplit` and
    `TreeLeaf`, not `Split`: v3.13 renamed `ml.Split`, a cross-validation
    index split, to `TrainTestSplit` without an alias (ledger API-001), and
    the removed name stays removed.

Building the point-in-time data settled these points
(`tests/unit/ml/test_labels.py`, `tests/unit/ml/test_point_in_time.py`).

11. **`ml.labels`: three makers, every choice stated.**
    - `FixedHorizon(horizon, threshold)` gives the return, or its sign beyond
      a dead zone.
    - `TripleBarrier(horizon, upper, lower, volatility, at_vertical)` takes a
      declared `RollingVolatility(window)`: the sample standard deviation of
      the window's simple returns, ending at the event.
    - `meta_labels` orients the barriers by a primary side.

    A barrier is touched when the return reaches it. Every label carries its
    start, its end and its return. An event that cannot be labelled is
    counted by reason: warm-up, flat or unresolved. Labels are class 2.
12. **Uniqueness among the rows taken.** `average_uniqueness` counts, at each
    observation, the rows on its subject that span it. A row's weight is the
    mean of one over that count across what it spans. It is computed over
    the rows a training set holds, never over a label set's later labels,
    whose ends are the future.
13. **`ml.point_in_time`, not `ml.datasets`.** The plan names
    `ml/datasets.py`, but `ml/dataset.py` already holds the feature-store
    builder, and two modules a letter apart would be read for each other. A
    `PointInTimePanel` row is an event's features at its start beside its
    label. An event a feature has no value for there is left out, kept in
    `dropped` and counted.
14. **A panel's identity never names the future.** Its lineage states each
    feature's definition and dataset version and the labels' method and
    source. It does not name a label set's identity, which covers labels
    that a panel known by an instant does not hold. So a panel known by an
    instant is the same panel whatever the prices after that instant (the
    counterfactual-future test).
15. **Splits have one authority.** `purge_policy` gives research's purging
    each instant's latest label end, and `at` takes a split's instants. So
    research's `cross_validation_splits` and `combinatorial_purged_splits`
    purge a panel as they purge any series.

Building the search settled these points (`tests/unit/research/test_sampling.py`,
`tests/integration/test_successive_halving_through_the_ledger.py`).

16. **Drawn spaces are the same everywhere.** `research.random_space` and
    `research.latin_hypercube_space` draw over `UniformAxis`,
    `LogUniformAxis`, `IntegerAxis` and `ChoiceAxis`.
    - Every coordinate is 53 bits of SHA-256 of the seed, the draw and the
      axis.
    - A log-uniform axis maps its coordinate through `decimal`'s correctly
      rounded `ln` and `exp`.
    - A `SampledSpace` keeps the method and the seed beside the space.
    - A random space passes over a repeated candidate. A Latin hypercube
      deals each axis's strata in an order ranked by SHA-256.
17. **Sobol comes through `alphalab-compute`.** Its direction numbers are a
    published table (Joe and Kuo's), which belongs where it can be verified
    against its source. The core writes none.
18. **Successive halving is rounds of experiments.**
    - `research.SuccessiveHalving` is the schedule: the budgets, the fraction
      kept, and the parameter the budget is passed as.
    - `lifecycle.run_successive_halving` runs each round as a child
      experiment of the round before it.
    - It ranks by `lifecycle.candidate_scores`: the mean score that
      cross-validation selects by.
    - Every configuration any round evaluated is a recorded, counted trial. A
      `HalvingRecord` keeps the configurations the search began with and
      every round's counts.
19. **Feature Store reads as of an instant** (`tests/unit/feature_store/test_value_as_of.py`).
    - `FeatureStoreState` keeps every approved write in a timeline, each
      value known from the later of the instant it was computed at and the
      instant it was written.
    - `feature_store.value_as_of` returns the last value known by an
      instant, a later write winning a tie, so a backfill written late never
      answers for an earlier instant.
    - `ml.build_dataset_as_of` reads through it.
    - `build_dataset_from_feature_store` is kept, reads each latest write,
      and is documented as not point-in-time.

    So GAP-13 closes both ways the plan offers: ML builds point-in-time panels
    from the factor library, and Feature Store reads as of an instant.

# Compatibility

- Additive: two modules, `ml.protocol` and `ml.models`, and new names.
- `build_dataset_from_feature_store` is kept, and documented as not
  point-in-time.
- No existing identity, signature or persisted format moves.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- A model trained anywhere trades in a backtest that restores it byte for byte,
  and a different model is recognised as one.
- An external framework is usable without the core depending on it.
