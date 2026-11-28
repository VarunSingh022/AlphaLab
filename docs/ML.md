# Machine Learning: A Trained Model Is Its Bytes

`alphalab.ml` (v4.3, ADR-0064) holds what supervised research needs:
- a protocol a model implements, and three reference models;
- the binding of a trained model to the artifact store that names it;
- labels that know when they became known, and point-in-time panels;
- weights by label uniqueness.

`alphalab.research` adds the searches, and `alphalab.lifecycle` runs them
through the trial ledger, so every configuration evaluated is counted.
Framework models -- scikit-learn, XGBoost and LightGBM -- come through the
optional distribution `alphalab-models` (ADR-0057). The core never imports a
framework.

Nothing here says a model predicts anything. A model fitted on what was knowable
and traded on what came after is a measurement; whether the measurement is
worth anything is the researcher's question, and the search that produced it is
part of the answer.

---

## Three rules that hold everywhere

**A trained model is its bytes.** A model's identity is not its training run --
a GPU, a thread pool or a framework's version can make two runs differ -- but
what it captures itself as. Those bytes are filed in an artifact store, named
by their digest, and declared by the strategy that trades them. Another model
is another artifact, and a run given another artifact is another run's inputs.

**Nothing known later enters.** Every label carries the instant its outcome
was realized, and a training set built at an instant holds only labels realized
by then. A panel built at an instant is the same whatever happens after it: the
counterfactual-future test, which every dataset builder here is held to.

**Every configuration searched is counted.** A search is a set of trials in the
ledger, each with a record, the failures included. A selection is judged
against the whole search, never against the survivors alone.

---

## The model protocol

A model implements `ml.Model`:

| Member | What it does |
| --- | --- |
| `model_kind` | The family and its implementation: `"alphalab:ridge"`, `"sklearn:GradientBoostingClassifier"` |
| `hyperparameters()` | Every choice the model was given, as parameter values |
| `fit(training, *, seed)` | A new fitted model; the model it is called on is unchanged |
| `predict(features)` | One value per row: a regression's value, or a classifier's probability of the label 1 |
| `capture()` | The fitted model's canonical bytes |
| `restore(artifact)` | The fitted model those bytes capture; anything else is refused |

A `TrainingSet` states its feature names, its rows, its labels and any positive
weights, and its `training_id` is derived from those values. A `FeatureMatrix`
names its columns, and a fitted model refuses a matrix that names other
features or the same in another order: a column in the wrong place would be a
prediction from the wrong feature.

```python
from alphalab.ml import FeatureMatrix, RidgeRegression, TrainingSet

training = TrainingSet.of(("momentum", "value"), rows, labels)
model = RidgeRegression(penalty=0.5, intercept=True, maximum_condition=1e8)
fitted = model.fit(training, seed=7)
predictions = fitted.predict(FeatureMatrix.of(("momentum", "value"), new_rows))
```

## The reference models

| Model | Fitted by | Numerical class |
| --- | --- | --- |
| `RidgeRegression` | Least squares by Householder QR, a ridge penalty on every coefficient but the intercept | 2: the same bytes on every host |
| `LogisticRegression` | The penalized likelihood by Newton's method, each step a weighted least-squares solve | 3: through `exp`, held across hosts by a tolerance |
| `GradientBoostedTrees` | Newton boosting on regression trees, squared or logistic loss | 2 with the squared loss, 3 with the logistic |

Every hyperparameter is stated, with no default. A logistic fit short of its
tolerance raises `ConvergenceError`; separated classes without a penalty are
refused rather than fitted to a coefficient still growing. A tree's split is
made only when it helps, ties going to the first feature and the lowest
threshold, and a subsample is drawn from SHA-256 of the seed, the same on every
interpreter. Each captures canonical JSON -- sorted keys, floats by their
shortest `repr` -- and a restore refuses bytes that parse but are not the
model's own capture, so one model has one artifact.

## Artifacts: filing, declaring, restoring

```python
from alphalab.ml import GradientBoostedTrees, restore_model, train_model
from alphalab.model_registry.artifact_store import FileArtifactStore

store = FileArtifactStore("models")
fitted, record = train_model(model, training, seed=7, store=store)
# record.artifact_id is "alphalab-artifact:sha256:<hex>": declare it among the
# strategy's parameters, so its fingerprint carries the model.
again = restore_model(GradientBoostedTrees, store, record.artifact_id)
```

The `TrainingRecord` keeps the seed, the hyperparameters and the training set's
identity beside the artifact, never in its name: two trainings that produce the
same bytes are one model. `restore_model` reads through the store's
verification, so bytes whose digest is not the id are refused before any model
reads them. A strategy that restores its model from a declared artifact id
needs no new hook: it computes its features from its own history, as example 73
does, and a rerun given another artifact is `INPUTS_DIFFER`.

## Labels that know when they became known

| Maker | The label | Ends at |
| --- | --- | --- |
| `FixedHorizon(horizon, threshold)` | The return `horizon` observations ahead, or its sign beyond a dead zone | That observation |
| `TripleBarrier(horizon, upper, lower, volatility, at_vertical)` | 1 or -1 for the first horizontal barrier touched; the vertical barrier's sign or 0 | The touch, or the vertical barrier |
| `meta_labels(prices, barrier, sides)` | Whether a primary rule's side reached its profit barrier first: 1 or 0 | The touch, or the vertical barrier |

The barriers sit at stated multiples of a declared volatility estimate,
`RollingVolatility(window)`: the sample standard deviation of the window's
simple returns, ending at the event. Every label carries its start, its end and
its return. An event that cannot be labelled is counted by reason -- warm-up,
flat, unresolved -- never invented. Labels are class 2.

`average_uniqueness` weighs each label by how much of what it spans it shares
with no other label. It is computed over the rows a training set holds, never
over a label set's later labels, whose ends are the future.

## Point-in-time panels

`point_in_time_panel(features, labels)` joins the factor library's feature
panels -- computed over prices or knowledge frames, so every value is what was
knowable at its instant -- to labels, event by event. An event a feature has no
value for at its start is counted in `missing`, never filled.

```python
from alphalab.ml import SampleWeights, point_in_time_panel
from alphalab.research import CVMethod, cross_validation_splits

panel = point_in_time_panel({"mom_5": momentum, "z_10": zscore}, labels)
known = panel.known_by(boundary)  # labels realized by the boundary
training = known.training_set(weights=SampleWeights.UNIQUENESS)
folds = cross_validation_splits(
    panel.instants, 5, CVMethod.PURGED, panel.purge_policy(embargo_seconds=0.0)
)
```

`known_by` keeps the rows whose label was realized by an instant. `at` keeps
the rows starting at a split's instants. `purge_policy` hands research's
splitters each instant's latest label end, so a panel is purged as any series
is: splits have one authority. A panel's identity names how its features and
labels were made, never the identity of a label set holding labels it does not.

Feature Store reads as of an instant too: `feature_store.value_as_of` returns
what a read at an instant would have returned, a backfill written late never
answering for the past, and `ml.build_dataset_as_of` builds a dataset from it.
`build_dataset_from_feature_store` reads each latest write and is not
point-in-time.

## Searching

A grid is the search when every combination is cheap. When it is not,
`research.random_space` and `research.latin_hypercube_space` draw a space over
`UniformAxis`, `LogUniformAxis`, `IntegerAxis` and `ChoiceAxis`. They draw from
SHA-256 of the seed, so a drawn space -- and every trial identity built on it --
is the same on every host.

`lifecycle.run_successive_halving` runs `research.SuccessiveHalving`:
- every configuration is evaluated with the first budget;
- the best fraction of them with the next, and so on;
- each round is a child experiment of the one before, ranked by the mean score
  cross-validation selects by.

Every configuration any round evaluated is a trial in the ledger. The
`HalvingRecord` keeps the configurations the search began with and every
round's counts, which is the size an assessment of the selection counts. Sobol
sequences need a published table, so they come through `alphalab-compute`.

## Framework models: alphalab-models

Three adapters wrap a framework's model in the protocol
(`distributions/alphalab-models/README.md`):

| Adapter | Wraps | The payload | A restore |
| --- | --- | --- | --- |
| `alphalab_models.sklearn.SklearnModel` | Any scikit-learn estimator | scikit-learn's pickle | Unpickles |
| `alphalab_models.xgboost.XGBoostModel` | `XGBClassifier`, `XGBRegressor` | XGBoost's JSON model | Parses it |
| `alphalab_models.lightgbm.LightGBMModel` | `LGBMClassifier`, `LGBMRegressor` | LightGBM's model text | Parses it |

- **Fitting.** Each fits a copy, giving it the seed in the framework's range as
  its `random_state`: modulo 2**32 for scikit-learn and XGBoost, 2**31 for
  LightGBM.
- **Capture.** Each captures the payload under a header of versions and a
  digest, written once at the fit, so a restored model keeps the artifact it
  came from. XGBoost's and LightGBM's headers also state the estimator's
  parameters exactly, and a restore constructs the estimator from them. A
  parameter that is code, such as a callable objective, is refused.
- **Prediction.** A classifier predicts the probability of the label 1. XGBoost's
  and LightGBM's predict through the booster their payload loads, after a fit
  as after a restore.
- **Refusals.** A restore under another framework version, or another adapter
  version, is refused.

**Restoring a scikit-learn artifact unpickles it, and unpickling runs code**:
restore only artifacts your own runs filed. XGBoost's and LightGBM's restores
unpickle nothing. The core never unpickles.

LightGBM writes its training parameters into the model, the thread count among
them: state `n_jobs`, or the same training is other bytes on a host with other
cores.

## End to end: S-07

`examples/81_search_train_and_trade_a_model.py` runs the whole path:
1. A momentum rule's side is meta-labelled by a triple barrier and joined to
   the features known at each event.
2. Successive halving searches a boosting classifier over purged
   cross-validated backtests, the lockbox held back from every trial.
3. The chosen configuration is trained on every label known before the
   lockbox, filed, traded by its artifact id over the lockbox and restored byte
   for byte.
4. Another artifact is `INPUTS_DIFFER`.

The same scenario runs in `alphalab-models`' suite with each framework's
boosting classifier: scikit-learn's, XGBoost's and LightGBM's.

The ledger holds the lockbox's prices back from every trial's backtest, but it
cannot see what a runner reads. **A runner fitting a model takes only labels
known before the lockbox.** The example's first runner fitted rows by their
start, some of whose labels were realized inside the lockbox, and its test
caught it.

## Deprecated in 4.3: ml's duplicate helpers and splitters

`ml` kept a second linear algebra and two index splitters beside the library's
authorities (GAP-48, ADR-0058). Each still returns what it did, and its
docstring names its replacement; 5.0 removes them. They do not warn: AlphaLab
raises no warning, so that a real notice is never lost among scheduled ones.

| Deprecated | Use instead |
| --- | --- |
| `ml.matrix_inverse` | Solve rather than invert: `common.linalg.least_squares`, or `common.linalg.cholesky` for a symmetric positive-definite system; `common.linalg.pseudo_inverse` where the inverse is wanted |
| `ml.transpose` | `tuple(zip(*matrix, strict=True))` |
| `ml.matmul` | Each entry `sum(x * y for x, y in zip(row, column))`, the same sums in the same order |
| `ml.identity` | `tuple(tuple(float(i == j) for j in range(n)) for i in range(n))` |
| `ml.walk_forward_split` | `research.walk_forward_splits`, or `research.cross_validation_splits` with `CVMethod.EXPANDING` or `CVMethod.ROLLING` |
| `ml.k_fold_split` | `research.cross_validation_splits` with `CVMethod.PURGED` |

The research splitters split instants into windows, not indices into tuples:
`PointInTimePanel.at` takes a window's instants, and `purge_policy` gives the
splitters each instant's label end.

## Not here yet

Deep-learning and reinforcement-learning research are M12's: an episode driver
and a PyTorch adapter (plan WS-16). Array backends and screening evaluators are
`alphalab-compute`'s, at M11.
