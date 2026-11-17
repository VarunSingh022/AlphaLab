"""alphalab-models: framework adapters for AlphaLab's research model protocol (ADR-0057, ADR-0064).

Each adapter wraps a framework's model in :class:`alphalab.ml.protocol.Model`,
so it fits on an :class:`~alphalab.ml.protocol.TrainingSet`, predicts from named
features, and is captured as bytes an artifact store names: the trained model's
identity, which a strategy declares and a backtest restores byte for byte.

The core never imports this distribution, and this distribution never changes
the core: a researcher imports an adapter and passes it in, as any other object.

* :mod:`alphalab_models.sklearn` -- scikit-learn estimators
  (``pip install "alphalab-models[sklearn]"``).

Restoring an adapter's artifact unpickles it; restore only artifacts you trust.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: The distribution's version.
__version__ = "0.1.0"
