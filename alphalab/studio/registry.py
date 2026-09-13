"""Stateless registry manipulations for Projects and Strategies."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.studio.events import ProjectCreated, StrategyRegistered
from alphalab.studio.project import Project
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.strategy import StrategyDefinition
from alphalab.studio.validation import validate_project_creation, validate_project_exists


class StudioRegistry:
    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def create_project(
        state: StrategyStudioState, project: Project, ts: float
    ) -> StrategyStudioState:
        validate_project_creation(state, project)

        mets = replace(state.metrics, total_projects=state.metrics.total_projects + 1)
        evt = ProjectCreated(StudioRegistry._create_id(), ts, project.project_id)

        return replace(
            state,
            projects=state.projects.set(project.project_id, project),
            metrics=mets,
            events=state.events.append(evt),
        )

    @staticmethod
    def register_strategy(
        state: StrategyStudioState, project_id: str, strategy: StrategyDefinition, ts: float
    ) -> StrategyStudioState:
        validate_project_exists(state, project_id)
        proj = state.projects[project_id]

        updated_proj = replace(proj, strategies=proj.strategies.append(strategy))

        mets = replace(state.metrics, total_strategies=state.metrics.total_strategies + 1)
        evt = StrategyRegistered(
            StudioRegistry._create_id(),
            ts,
            project_id,
            strategy.strategy_id,
        )

        return replace(
            state,
            projects=state.projects.set(project_id, updated_proj),
            metrics=mets,
            events=state.events.append(evt),
        )
