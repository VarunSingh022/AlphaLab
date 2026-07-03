"""Immutable models defining workloads in the distributed cluster."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class JobType(Enum):
    """Explicit classifications for distributed workloads."""

    BACKTEST = auto()
    OPTIMIZATION = auto()
    REPLAY = auto()
    ANALYTICS = auto()
    REPORT = auto()
    PLUGIN_TASK = auto()


class JobStatus(Enum):
    """Explicit pure state machine stages for a Job."""

    PENDING = auto()
    ASSIGNED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class Job:
    """Immutable representation of a single unit of distributed work."""

    job_id: str
    job_type: JobType
    status: JobStatus
    priority: int
    created_timestamp: float
    started_timestamp: float = 0.0
    completed_timestamp: float = 0.0
    worker_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)
