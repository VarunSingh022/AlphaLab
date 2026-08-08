"""Immutable Feature Store domain events."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class FeatureStoreEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class FeatureRegistered(FeatureStoreEvent):
    feature_id: str
    version: int


@dataclass(frozen=True, slots=True)
class FeatureDeprecated(FeatureStoreEvent):
    feature_id: str
    version: int


@dataclass(frozen=True, slots=True)
class FeatureValueWritten(FeatureStoreEvent):
    decision_id: str
    feature_id: str
    version: int
    asset_id: str | None


@dataclass(frozen=True, slots=True)
class FeatureValueRejected(FeatureStoreEvent):
    decision_id: str
    feature_id: str
    version: int
    reason: str


@dataclass(frozen=True, slots=True)
class CacheHit(FeatureStoreEvent):
    feature_id: str
    version: int


@dataclass(frozen=True, slots=True)
class CacheMiss(FeatureStoreEvent):
    feature_id: str
    version: int


@dataclass(frozen=True, slots=True)
class CacheInvalidated(FeatureStoreEvent):
    feature_id: str
    version: int
