# alphalab-models

Framework adapters for AlphaLab's research model protocol (ADR-0057, ADR-0064).
An adapter wraps a framework's model so that AlphaLab can fit it on a
point-in-time training set, predict from named features, and capture it as
bytes an artifact store names. Those bytes are the trained model's identity: a
strategy declares them, and a backtest restores them byte for byte.

The core, `alphalab`, has no runtime dependency and never imports this
distribution (AC-38). You import an adapter and pass it in, as any other object.

## Install

AlphaLab is installed from its release's wheel, not from an index. Install the
core first, then this distribution beside it with the frameworks you use:

```bash
pip install "alphalab @ https://github.com/VarunSingh022/AlphaLab/releases/download/v4.3.0/alphalab-4.3.0-py3-none-any.whl"
pip install "alphalab-models[sklearn]"
```

From a checkout, install both in editable mode:

```bash
pip install -e . -e "distributions/alphalab-models[sklearn,dev]"
```

## Use

```python
from sklearn.ensemble import GradientBoostingClassifier

from alphalab.ml import restore_model, train_model
from alphalab_models.sklearn import SklearnModel

model = SklearnModel(GradientBoostingClassifier(n_estimators=200, max_depth=3))
fitted, record = train_model(model, training, seed=7, store=store)
# record.artifact_id names the bytes: declare it among the strategy's parameters.
again = restore_model(SklearnModel, store, record.artifact_id)
```

## Trust

Restoring a scikit-learn artifact unpickles it, and unpickling runs code.
Restore only artifacts you trust: ones your own runs filed, read back through an
artifact store, which refuses bytes whose digest is not the id a strategy
declares. The core never unpickles; this adapter does, when you ask it to.

## Compatibility

| alphalab-models | alphalab | Python | scikit-learn |
| --- | --- | --- | --- |
| 0.1.0 | >=4.3, <5 | 3.12, 3.13, 3.14 | >=1.7, <2 |

An artifact is restored only under the scikit-learn version that captured it,
because scikit-learn promises its pickles only within a version. A fit's seed
reaches an estimator's `random_state` modulo 2**32, the range scikit-learn
accepts: AlphaLab's trial ledger draws 64-bit seeds. The suite runs
in this distribution's own CI cells
(`.github/workflows/optional-distributions.yml`), never in the core's.
