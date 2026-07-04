"""AlphaLab Workbench Graphical Interface Engine."""

from alphalab.workbench.adapter import WorkbenchAdapter
from alphalab.workbench.engine import WorkbenchEngine
from alphalab.workbench.events import (
    ActionDispatched,
    LayoutRestored,
    LayoutSaved,
    ProjectClosed,
    ProjectOpened,
    TabClosed,
    TabOpened,
    WorkbenchEvent,
    WorkbenchStarted,
)
from alphalab.workbench.exceptions import (
    InvalidWorkbenchStateError,
    WorkbenchError,
    WorkbenchValidationError,
)
from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.manager import WorkbenchManager
from alphalab.workbench.navigation import Tab
from alphalab.workbench.panels import Panel, PanelType
from alphalab.workbench.protocol import WorkbenchPluginProtocol
from alphalab.workbench.registry import WorkbenchRegistry
from alphalab.workbench.sessions import WorkbenchSession
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.themes import Theme
from alphalab.workbench.validation import validate_layout, validate_tab_operation
from alphalab.workbench.views import (
    active_layout,
    active_tabs,
    current_project,
    current_theme,
    saved_layouts,
)
from alphalab.workbench.workspace import WorkbenchConfig

__all__ = [
    "ActionDispatched",
    "InvalidWorkbenchStateError",
    "LayoutRestored",
    "LayoutSaved",
    "Panel",
    "PanelType",
    "ProjectClosed",
    "ProjectOpened",
    "Tab",
    "TabClosed",
    "TabOpened",
    "Theme",
    "WorkbenchAdapter",
    "WorkbenchConfig",
    "WorkbenchEngine",
    "WorkbenchError",
    "WorkbenchEvent",
    "WorkbenchManager",
    "WorkbenchPluginProtocol",
    "WorkbenchRegistry",
    "WorkbenchSession",
    "WorkbenchStarted",
    "WorkbenchState",
    "WorkbenchValidationError",
    "WorkspaceLayout",
    "active_layout",
    "active_tabs",
    "current_project",
    "current_theme",
    "saved_layouts",
    "validate_layout",
    "validate_tab_operation",
]