"""Global immutable state container for the graphical Workbench.

``tabs``, ``saved_layouts``, ``sessions`` and ``events`` use the canonical
containers from :mod:`alphalab.common` for the reason v2.1 and v2.2 introduced
them: growing a ``tuple`` with ``(*state.events, evt)`` or rebuilding a ``dict``
per transition copies the whole container each time, so ``N`` interactions cost
``O(N^2)``. ``tabs`` stays a tuple -- it is bounded by what a user has open, it
is ordered, and closing a tab removes from the middle, which is not an append.
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    saved_layouts: PersistentMap[str, WorkspaceLayout] = field(default_factory=PersistentMap)
    sessions: PersistentMap[str, WorkbenchSession] = field(default_factory=PersistentMap)
    events: AppendOnlyLog[WorkbenchEvent] = field(default_factory=AppendOnlyLog)
