"""Orchestration of pure UI transitions (Tabs, Projects, Views).

**The tab invariant.** While any tab is open, exactly one of them is active.
:meth:`WorkbenchManager.open_tab` establishes it and
:meth:`WorkbenchManager.close_tab` restores it after removing the active tab.

Until v2.16 :meth:`WorkbenchManager.close_project` broke it: it dropped every
unpinned tab, and when the active tab was among them the pinned survivors were
left with no active tab at all. Nothing raised, because nothing checked --
:mod:`alphalab.workbench.views` had no way to ask which tab was focused, so a
workbench with tabs and no focus was unobservable. ``active_tab`` now exposes
the invariant and this transition maintains it.
"""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.workbench.events import ProjectClosed, ProjectOpened, TabClosed, TabOpened
from alphalab.workbench.navigation import Tab
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.validation import validate_tab_operation


def _with_one_active(tabs: tuple[Tab, ...]) -> tuple[Tab, ...]:
    """Restore the invariant: while tabs remain, exactly one of them is active.

    The last tab takes focus when none of the survivors held it, which is what
    ``close_tab`` has always done with the tab it removed.
    """

    if not tabs:
        return tabs
    if sum(1 for tab in tabs if tab.is_active) == 1:
        return tabs
    focus = tabs[-1].tab_id
    return tuple(replace(tab, is_active=(tab.tab_id == focus)) for tab in tabs)


class WorkbenchManager:
    """Stateless mutator for GUI navigation and tab management."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def open_project(state: WorkbenchState, project_id: str, ts: float) -> WorkbenchState:
        # Note: We rely on the Engine facade to actually interact with Studio.
        # This purely updates the UI pointer.
        evt = ProjectOpened(WorkbenchManager._create_id(), ts, project_id)
        return replace(state, active_project_id=project_id, events=state.events.append(evt))

    @staticmethod
    def close_project(state: WorkbenchState, ts: float) -> WorkbenchState:
        if not state.active_project_id:
            return state

        evt = ProjectClosed(WorkbenchManager._create_id(), ts, state.active_project_id)
        # Close all tabs requiring a project, and leave the survivors focused.
        new_tabs = _with_one_active(tuple(t for t in state.tabs if t.is_pinned))

        return replace(
            state,
            active_project_id=None,
            tabs=new_tabs,
            events=state.events.append(evt),
        )

    @staticmethod
    def open_tab(
        state: WorkbenchState, tab_id: str, title: str, ref: str, ts: float
    ) -> WorkbenchState:
        if any(t.tab_id == tab_id for t in state.tabs):
            # Already open, just make active
            new_tabs = tuple(replace(t, is_active=(t.tab_id == tab_id)) for t in state.tabs)
            return replace(state, tabs=new_tabs)

        # Deactivate all others, append new active tab
        new_tabs = tuple(replace(t, is_active=False) for t in state.tabs)
        new_tab = Tab(tab_id, title, ref, is_active=True)

        evt = TabOpened(WorkbenchManager._create_id(), ts, tab_id, ref)
        return replace(state, tabs=(*new_tabs, new_tab), events=state.events.append(evt))

    @staticmethod
    def close_tab(state: WorkbenchState, tab_id: str, ts: float) -> WorkbenchState:
        validate_tab_operation(tab_id, state)
        new_tabs = _with_one_active(tuple(t for t in state.tabs if t.tab_id != tab_id))

        evt = TabClosed(WorkbenchManager._create_id(), ts, tab_id)
        return replace(state, tabs=new_tabs, events=state.events.append(evt))
