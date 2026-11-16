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

# Compatibility

- Additive: a module, `ml.protocol`, and new names.
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
