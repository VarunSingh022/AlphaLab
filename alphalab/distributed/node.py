"""Immutable models defining computation nodes in the distributed cluster."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto


class WorkerStatus(Enum):
    """Lifecycle states for a remote computation node."""

    IDLE = auto()
    BUSY = auto()
    OFFLINE = auto()


@dataclass(frozen=True, slots=True)
class WorkerNode:
    """Immutable representation of an execution node."""

    node_id: str
    hostname: str
    status: WorkerStatus
    capacity: int
    running_jobs: tuple[str, ...] = field(default_factory=tuple)
    completed_jobs: int = 0
    metadata: Mapping[str, str] = field(default_factory=dict)
