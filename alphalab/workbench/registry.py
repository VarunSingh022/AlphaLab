"""Stateless registry for saved layouts and UI themes."""

import uuid
from dataclasses import replace

from alphalab.workbench.events import LayoutRestored, LayoutSaved
from alphalab.workbench.layout import WorkspaceLayout
from alphalab.workbench.state import WorkbenchState
from alphalab.workbench.validation import validate_layout


class WorkbenchRegistry:
    @staticmethod
    def _create_id() -> str: return str(uuid.uuid4())

    @staticmethod
    def save_layout(
        state: WorkbenchState, layout_id: str, name: str, ts: float
    ) -> WorkbenchState:
        validate_layout(layout_id, state)
        
        if not state.active_layout:
            return state
            
        new_layout = WorkspaceLayout(layout_id, name, state.active_layout.panels)
        new_layouts = dict(state.saved_layouts)
        new_layouts[layout_id] = new_layout
        
        evt = LayoutSaved(WorkbenchRegistry._create_id(), ts, layout_id)
        return replace(state, saved_layouts=new_layouts, events=(*state.events, evt))

    @staticmethod
    def restore_layout(
        state: WorkbenchState, layout_id: str, ts: float
    ) -> WorkbenchState:
        if layout_id not in state.saved_layouts:
            raise ValueError(f"Layout '{layout_id}' not found.")
            
        layout_to_restore = state.saved_layouts[layout_id]
        evt = LayoutRestored(WorkbenchRegistry._create_id(), ts, layout_id)
        
        return replace(
            state, active_layout=layout_to_restore, events=(*state.events, evt)
        )