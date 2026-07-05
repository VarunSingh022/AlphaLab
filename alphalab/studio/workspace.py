"""Immutable representation of the physical studio workspace."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    workspace_id: str
    saved_at: float
    project_ids: tuple[str, ...] = field(default_factory=tuple)
    bookmarked_result_ids: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
