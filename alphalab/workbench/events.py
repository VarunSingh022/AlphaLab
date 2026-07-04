"""Immutable domain events describing the Workbench UI lifecycle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkbenchEvent:
    event_id: str
    timestamp: float

@dataclass(frozen=True, slots=True)
class WorkbenchStarted(WorkbenchEvent):
    workbench_id: str

@dataclass(frozen=True, slots=True)
class ProjectOpened(WorkbenchEvent):
    project_id: str

@dataclass(frozen=True, slots=True)
class ProjectClosed(WorkbenchEvent):
    project_id: str

@dataclass(frozen=True, slots=True)
class TabOpened(WorkbenchEvent):
    tab_id: str
    content_ref: str

@dataclass(frozen=True, slots=True)
class TabClosed(WorkbenchEvent):
    tab_id: str

@dataclass(frozen=True, slots=True)
class LayoutSaved(WorkbenchEvent):
    layout_id: str

@dataclass(frozen=True, slots=True)
class LayoutRestored(WorkbenchEvent):
    layout_id: str

@dataclass(frozen=True, slots=True)
class ActionDispatched(WorkbenchEvent):
    action: str
    target_id: str