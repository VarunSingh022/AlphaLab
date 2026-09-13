"""Pure queries exposing transparent Graphical Interface State access.

``active_tab`` closes a gap that made a whole capability unreachable. ``Tab``
has carried ``is_active`` since the package was written and every transition in
:mod:`alphalab.workbench.manager` maintains it, but nothing exposed it -- so a
caller could not ask which tab it was looking at, and
:meth:`~alphalab.workbench.engine.WorkbenchEngine.run_backtest`, which opens a
tab named after an identifier Strategy Studio mints internally, gave its caller
no way to name the tab it had just opened. ``benchmarks/benchmark_workbench.py``
guessed ``bt-<backtest_id>``, was wrong, and had crashed on every run since the
benchmark was written.
"""

from collections.abc import Sequence

from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.navigation import Tab
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.themes import Theme


def active_tabs(state: WorkbenchState) -> Sequence[Tab]:
    """Every open tab, in the order it was opened."""

    return state.tabs


def active_tab(state: WorkbenchState) -> Tab | None:
    """The focused tab, or ``None`` when no tab is open.

    While any tab is open exactly one is active -- the invariant
    :mod:`alphalab.workbench.manager` maintains -- so this answers ``None`` only
    for an empty workbench.
    """

    for tab in state.tabs:
        if tab.is_active:
            return tab
    return None


def active_layout(state: WorkbenchState) -> WorkspaceLayout | None:
    return state.active_layout


def saved_layouts(state: WorkbenchState) -> Sequence[WorkspaceLayout]:
    return tuple(state.saved_layouts.values())


def current_theme(state: WorkbenchState) -> Theme:
    return state.config.default_theme


def current_project(state: WorkbenchState) -> str | None:
    return state.active_project_id
