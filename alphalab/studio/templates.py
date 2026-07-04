"""Immutable blueprints for projects and pipelines."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectTemplate:
    template_id: str
    name: str
    description: str
    default_pipeline_steps: tuple[str, ...]