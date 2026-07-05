"""Pure queries exposing transparent Graphical Interface State access."""

from collections.abc import Sequence

from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.navigation import Tab
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.themes import Theme


def active_tabs(state: WorkbenchState) -> Sequence[Tab]:
    return state.tabs


def active_layout(state: WorkbenchState) -> WorkspaceLayout | None:
    return state.active_layout


def saved_layouts(state: WorkbenchState) -> Sequence[WorkspaceLayout]:
    return tuple(state.saved_layouts.values())


def current_theme(state: WorkbenchState) -> Theme:
    return state.config.default_theme


def current_project(state: WorkbenchState) -> str | None:
    return state.active_project_id
