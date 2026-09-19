"""Immutable definitions for asset classifications.

``DataAssetClass`` is the data layer's deliberate rename of
:class:`~alphalab.core.enums.AssetType`, which is AlphaLab's canonical asset
taxonomy. The rename is recorded in ``ROADMAP.md`` and is not an oversight: the
data engine classifies *datasets*, which include things that are not tradable
instruments at all -- a fundamentals feed, an economic release calendar, a
sentiment series -- and widening the canonical trading taxonomy to admit them
would put non-tradable categories in front of every order-side consumer.

v3.1 adds ``INDEX``, ``RATE`` and ``COMMODITY`` for the same reason: each names
a kind of series research consumes and no order is ever placed in. An index
level is not an instrument, a rate is quoted in percent rather than currency,
and a commodity series may be a spot assessment with no contract behind it.
:mod:`alphalab.data.assets` carries the semantics that make each interpretable.
"""

from enum import Enum, auto

__all__ = ["DataAssetClass"]


class DataAssetClass(Enum):
    EQUITY = auto()
    ETF = auto()
    FUTURE = auto()
    OPTION = auto()
    FOREX = auto()
    CRYPTO = auto()
    FUNDAMENTAL = auto()
    ECONOMIC = auto()
    ALTERNATIVE = auto()
    INDEX = auto()
    RATE = auto()
    COMMODITY = auto()
