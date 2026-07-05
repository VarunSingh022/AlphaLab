"""Immutable metrics tracking Studio orchestration activity."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StudioMetrics:
    total_projects: int = 0
    total_strategies: int = 0
    pipelines_executed: int = 0
    backtests_run: int = 0
    reports_generated: int = 0
