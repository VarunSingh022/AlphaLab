"""Immutable workspace configurations."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StudioConfig:
    """Immutable environment configuration for Strategy Studio.

    Until v3.10 it also carried ``default_currency="USD"``, which nothing read:
    a currency a configuration assumes on its caller's behalf, kept only to be
    wrong later (ledger API-003). A backtest names its currency where it is
    configured.
    """

    studio_id: str
    workspace_dir: str
    auto_save: bool = True
    metadata: Mapping[str, str] = field(default_factory=dict)
