"""Global immutable state container for the Distributed Research framework."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.distributed.events import DistributedEvent
from alphalab.distributed.job import Job
from alphalab.distributed.node import WorkerNode


@dataclass(frozen=True, slots=True)
class DistributedStatistics:
    """Immutable tracking metrics for the cluster."""

    total_jobs_submitted: int = 0
    total_jobs_completed: int = 0
    total_jobs_failed: int = 0
    total_jobs_cancelled: int = 0


@dataclass(frozen=True, slots=True)
class DistributedState:
    """Deterministic snapshot of the Distributed Engine."""

    cluster_id: str
    workers: Mapping[str, WorkerNode] = field(default_factory=dict)
    queued_jobs: tuple[Job, ...] = field(default_factory=tuple)
    running_jobs: Mapping[str, Job] = field(default_factory=dict)
    completed_jobs: Mapping[str, Job] = field(default_factory=dict)
    failed_jobs: Mapping[str, Job] = field(default_factory=dict)
    statistics: DistributedStatistics = field(default_factory=DistributedStatistics)
    events: tuple[DistributedEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
