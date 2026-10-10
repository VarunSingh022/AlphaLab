"""Immutable domain events describing the Distributed Engine lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class DistributedEvent(BaseEvent):
    """Base class for all Distributed system events."""

    pass


@dataclass(frozen=True, slots=True)
class JobSubmitted(DistributedEvent):
    job_id: str
    job_type: str
    priority: int


@dataclass(frozen=True, slots=True)
class JobAssigned(DistributedEvent):
    job_id: str
    worker_id: str


@dataclass(frozen=True, slots=True)
class JobStarted(DistributedEvent):
    job_id: str
    worker_id: str


@dataclass(frozen=True, slots=True)
class JobCompleted(DistributedEvent):
    job_id: str
    worker_id: str
    execution_time: float


@dataclass(frozen=True, slots=True)
class JobFailed(DistributedEvent):
    job_id: str
    worker_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class JobCancelled(DistributedEvent):
    """A job withdrawn before it ran: from the queue, or assigned and not yet started.

    Since v3.12 (ledger SCF-003). Until then a cancellation was stored among the
    failed jobs and recorded no event, so the log could not say it had happened.
    """

    job_id: str
    worker_id: str


@dataclass(frozen=True, slots=True)
class WorkerRegistered(DistributedEvent):
    worker_id: str
    capacity: int


@dataclass(frozen=True, slots=True)
class WorkerRemoved(DistributedEvent):
    worker_id: str
