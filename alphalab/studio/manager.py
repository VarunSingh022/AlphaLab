"""Orchestration of Workspaces and Sessions."""

import uuid
from dataclasses import replace

from alphalab.studio.events import SessionStarted, WorkspaceSaved
from alphalab.studio.sessions import StudioSession
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.workspace import WorkspaceSnapshot


class StudioManager:
    @staticmethod
    def _create_id() -> str: return str(uuid.uuid4())

    @staticmethod
    def start_session(
        state: StrategyStudioState, session_id: str, user_id: str, project_id: str, ts: float
    ) -> StrategyStudioState:
        session = StudioSession(session_id, user_id, project_id, ts, ts)
        new_sessions = dict(state.sessions)
        new_sessions[session_id] = session
        
        evt = SessionStarted(StudioManager._create_id(), ts, session_id)
        return replace(state, sessions=new_sessions, events=(*state.events, evt))

    @staticmethod
    def save_workspace(
        state: StrategyStudioState, workspace_id: str, ts: float
    ) -> StrategyStudioState:
        project_ids = tuple(state.projects.keys())
        snapshot = WorkspaceSnapshot(workspace_id, ts, project_ids, (), ())
        
        new_workspaces = dict(state.workspaces)
        new_workspaces[workspace_id] = snapshot
        
        evt = WorkspaceSaved(StudioManager._create_id(), ts, workspace_id)
        return replace(state, workspaces=new_workspaces, events=(*state.events, evt))

    @staticmethod
    def load_workspace(
        state: StrategyStudioState, workspace_id: str, ts: float
    ) -> StrategyStudioState:
        # In a purely functional system, loading from an existing snapshot 
        # asserts the internal state already tracks it. 
        # In real I/O, this would deserialize. Here we just validate.
        if workspace_id not in state.workspaces:
            raise ValueError(f"Workspace {workspace_id} not found.")
        return state