"""Immutable layout configurations managing screen real estate."""

from dataclasses import dataclass, field

from alphalab.workbench.panels import Panel


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Immutable snapshot of the user's screen arrangement."""

    layout_id: str
    name: str
    panels: tuple[Panel, ...] = field(default_factory=tuple)
