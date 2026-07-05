"""Immutable tracking of generic workspace settings."""

from dataclasses import dataclass

from alphalab.workbench.themes import Theme


@dataclass(frozen=True, slots=True)
class WorkbenchConfig:
    workbench_id: str
    default_theme: Theme = Theme.DARK
    enable_animations: bool = True
    auto_save_layout: bool = True
