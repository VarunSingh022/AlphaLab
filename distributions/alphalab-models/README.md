# alphalab-models

Framework adapters for AlphaLab's research model protocol (ADR-0057, ADR-0064).
An adapter wraps a framework's model so that AlphaLab can fit it on a
point-in-time training set, predict from named features, and capture it as
bytes an artifact store names. Those bytes are the trained model's identity: a
strategy declares them, and a backtest restores them byte for byte.

The core, `alphalab`, has no runtime dependency and never imports this
distribution (AC-38). You import an adapter and pass it in, as any other object.

| Adapter | Wraps | Its artifact's payload |
| --- | --- | --- |
| `alphalab_models.sklearn.SklearnModel` | Any scikit-learn estimator | scikit-learn's pickle |
| `alphalab_models.xgboost.XGBoostModel` | `XGBClassifier`, `XGBRegressor` | XGBoost's JSON model |
| `alphalab_models.lightgbm.LightGBMModel` | `LGBMClassifier`, `LGBMRegressor` | LightGBM's model text |

Each artifact is one line of canonical JSON naming the scheme, the model's
kind, its features, the framework's and the adapter's versions and the
payload's SHA-256, then the payload. A classifier predicts the probability of
the label 1, from labels 0 and 1. A restored model captures as the very
artifact it came from, and predicts what the fitted model did.

## Install

AlphaLab is installed from its release's wheel, not from an index. Install the
core first, then this distribution beside it with the frameworks you use, each
an extra:

```bash
pip install "alphalab @ https://github.com/VarunSingh022/AlphaLab/releases/download/v4.3.0/alphalab-4.3.0-py3-none-any.whl"
pip install "alphalab-models[sklearn]"
pip install "alphalab-models[xgboost]"
pip install "alphalab-models[lightgbm]"
```

The `xgboost` and `lightgbm` extras bring scikit-learn too: the adapters wrap
those frameworks' scikit-learn estimators. From a checkout, install both in
editable mode:

```bash
pip install -e . -e "distributions/alphalab-models[sklearn,xgboost,lightgbm,dev]"
```

## Use

```python
from xgboost import XGBClassifier

from alphalab.ml import restore_model, train_model
from alphalab_models.xgboost import XGBoostModel

model = XGBoostModel(XGBClassifier(n_estimators=200, max_depth=3, subsample=0.8, n_jobs=1))
fitted, record = train_model(model, training, seed=7, store=store)
# record.artifact_id names the bytes: declare it among the strategy's parameters.
again = restore_model(XGBoostModel, store, record.artifact_id)
```

`SklearnModel` and `LightGBMModel` are used the same way.

## Trust

- **scikit-learn.** Restoring a scikit-learn artifact unpickles it, and
  unpickling runs code. Restore only artifacts you trust: ones your own runs
  filed, read back through an artifact store, which refuses bytes whose digest
  is not the id a strategy declares.
- **XGBoost and LightGBM.** A restore parses the framework's own model format
  and unpickles nothing. The estimator is constructed from its parameters,
  stated in the header as values. A parameter that is code -- a callable
  objective, a callback -- or a mapping such as `class_weight` is refused when
  the model is made, because bytes cannot carry it.

The core never unpickles; the scikit-learn adapter does, when you ask it to.

## What the bytes record

- **The seed.** A fit's seed reaches the estimator's `random_state` in the
  framework's own range, since AlphaLab's trial ledger draws 64-bit seeds:
  modulo 2**32 for scikit-learn and XGBoost, and modulo 2**31 for LightGBM,
  whose seed is a signed 32-bit integer.
- **XGBoost** records no seed. A fit that draws nothing -- no subsample, no
  column sample -- gives the same bytes whatever the seed.
- **LightGBM** writes its training parameters into its model text, the seed and
  the thread count among them. State `n_jobs`: an unstated one records the
  cores of the host that ran the fit, so the same training would be other bytes
  on another host.

## Compatibility

| alphalab-models | alphalab | Python | scikit-learn | XGBoost | LightGBM |
| --- | --- | --- | --- | --- | --- |
| 0.1.0 | >=4.3, <5 | 3.12, 3.13, 3.14 | >=1.7, <2 | >=3.0, <4 | >=4.6, <5 |

An artifact is restored only under the framework version that captured it, and
by the adapter version that captured it:
- scikit-learn promises its pickles only within a version;
- XGBoost and LightGBM read their models across versions, but a model's
  predictions are its version's.

The suite runs in this distribution's own CI cells
(`.github/workflows/optional-distributions.yml`), never in the core's. Each
cell runs the latest of every framework, on CPython 3.12, 3.13 and 3.14, and
the lowest of each declared on 3.12.
