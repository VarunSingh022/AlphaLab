"""Immutable representation of quantitative strategies."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """Metadata and parameter bounds defining a systematic strategy."""
    strategy_id: str
    name: str
    version: str
    author: str
    description: str
    parameters: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)