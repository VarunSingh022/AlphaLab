"""Orchestration of pure UI transitions (Tabs, Projects, Views)."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.workbench.events import ProjectClosed, ProjectOpened, TabClosed, TabOpened
from alphalab.workbench.navigation import Tab
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.validation import validate_tab_operation


class WorkbenchManager:
    """Stateless mutator for GUI navigation and tab management."""

    @staticmethod
    def _create_id() -> str: return str(new_id())

    @staticmethod
    def open_project(
        state: WorkbenchState, project_id: str, ts: float
    ) -> WorkbenchState:
        # Note: We rely on the Engine facade to actually interact with Studio.
        # This purely updates the UI pointer.
        evt = ProjectOpened(WorkbenchManager._create_id(), ts, project_id)
        return replace(state, active_project_id=project_id, events=(*state.events, evt))

    @staticmethod
    def close_project(state: WorkbenchState, ts: float) -> WorkbenchState:
        if not state.active_project_id:
            return state
            
        evt = ProjectClosed(WorkbenchManager._create_id(), ts, state.active_project_id)
        # Close all tabs requiring a project
        new_tabs = tuple(t for t in state.tabs if t.is_pinned)
        
        return replace(
            state, active_project_id=None, tabs=new_tabs, events=(*state.events, evt)
        )

    @staticmethod
    def open_tab(
        state: WorkbenchState, tab_id: str, title: str, ref: str, ts: float
    ) -> WorkbenchState:
        if any(t.tab_id == tab_id for t in state.tabs):
            # Already open, just make active
            new_tabs = tuple(
                replace(t, is_active=(t.tab_id == tab_id)) for t in state.tabs
            )
            return replace(state, tabs=new_tabs)
            
        # Deactivate all others, append new active tab
        new_tabs = tuple(replace(t, is_active=False) for t in state.tabs)
        new_tab = Tab(tab_id, title, ref, is_active=True)
        
        evt = TabOpened(WorkbenchManager._create_id(), ts, tab_id, ref)
        return replace(state, tabs=(*new_tabs, new_tab), events=(*state.events, evt))

    @staticmethod
    def close_tab(state: WorkbenchState, tab_id: str, ts: float) -> WorkbenchState:
        validate_tab_operation(tab_id, state)
        new_tabs = tuple(t for t in state.tabs if t.tab_id != tab_id)
        
        # If we closed the active tab, activate the last one if it exists
        was_active = any(t.is_active for t in state.tabs if t.tab_id == tab_id)
        if was_active and new_tabs:
            last = new_tabs[-1]
            new_tabs = tuple(
                replace(t, is_active=(t.tab_id == last.tab_id)) for t in new_tabs
            )
            
        evt = TabClosed(WorkbenchManager._create_id(), ts, tab_id)
        return replace(state, tabs=new_tabs, events=(*state.events, evt))