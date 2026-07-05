"""Immutable workspace configurations."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StudioConfig:
    """Immutable environment configuration for Strategy Studio."""

    studio_id: str
    workspace_dir: str
    auto_save: bool = True
    default_currency: str = "USD"
    metadata: Mapping[str, str] = field(default_factory=dict)
