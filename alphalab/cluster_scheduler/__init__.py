"""AlphaLab Cluster Scheduler.

Job scheduling, queue management, and distributed orchestration -- built entirely
on top of alphalab.distributed, not a reimplementation of it.

Two things this package initially assumed were missing turned out to already
exist in alphalab.distributed, caught by checking JobQueue directly rather than
trusting an earlier, incomplete read of the package: priority ordering
(JobQueue.submit already sorts the queue by (-priority, created_timestamp) on
every submission) and cancellation (DistributedEngine.cancel_job already exists
and is already exported). Neither is duplicated here.

What genuinely was missing, and is what this package actually provides:
- Aging-aware assignment (assign_jobs_with_aging): JobQueue.submit's priority sort
  is evaluated once at submission and never revisited, so a continuous stream of
  high-priority jobs can starve an old low-priority one indefinitely. Aging fixes
  that by growing effective priority with wait time.
- Tag-based worker affinity (assign_jobs_with_affinity): neither Job.metadata nor
  WorkerNode.metadata (both already present on their respective types) is
  consulted anywhere in alphalab.distributed for routing.
- queue_position: genuinely absent from alphalab.distributed, including its own
  views.py.
"""

from alphalab.cluster_scheduler.affinity_scheduler import assign_jobs_with_affinity
from alphalab.cluster_scheduler.exceptions import ClusterSchedulerError, ClusterSchedulerInputError
from alphalab.cluster_scheduler.priority_scheduler import assign_jobs_with_aging
from alphalab.cluster_scheduler.queue_manager import queue_position

__all__ = [
    "ClusterSchedulerError",
    "ClusterSchedulerInputError",
    "assign_jobs_with_affinity",
    "assign_jobs_with_aging",
    "queue_position",
]
