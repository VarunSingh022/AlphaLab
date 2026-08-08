"""Decoupling interface between Feature Store and feature computation.

Feature Store never imports a computation engine (e.g. a future Factor Library).
Instead, computed values satisfy `FeatureValueProtocol` structurally, the same
decoupling pattern `alphalab.broker.adapter.OMSOrderProtocol` uses to keep the broker
layer independent of a strict OMS import.
"""

from typing import Any, Protocol


class FeatureValueProtocol(Protocol):
    """Generic interface for an externally computed feature value.

    Any object with these properties -- regardless of which package produced it --
    can be written into the Feature Store without Feature Store importing that
    package's types.
    """

    @property
    def feature_id(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def asset_id(self) -> str | None: ...

    @property
    def value(self) -> Any: ...

    @property
    def timestamp(self) -> float: ...
