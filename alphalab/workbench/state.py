"""Global immutable state container for the graphical Workbench."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.workbench.events import WorkbenchEvent
from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.navigation import Tab
from alphalab.workbench.sessions import WorkbenchSession
from alphalab.workbench.workspace import WorkbenchConfig


@dataclass(frozen=True, slots=True)
class WorkbenchState:
    """Deterministic snapshot of the entire AlphaLab Graphical User Interface."""
    workbench_id: str
    config: WorkbenchConfig
    active_project_id: str | None = None
    tabs: tuple[Tab, ...] = field(default_factory=tuple)
    active_layout: WorkspaceLayout | None = None
    saved_layouts: Mapping[str, WorkspaceLayout] = field(default_factory=dict)
    sessions: Mapping[str, WorkbenchSession] = field(default_factory=dict)
    events: tuple[WorkbenchEvent, ...] = field(default_factory=tuple)