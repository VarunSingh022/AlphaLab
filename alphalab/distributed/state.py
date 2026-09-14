"""Global immutable state container for the Distributed Research framework.

The keyed indexes and the histories use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them.
:class:`~alphalab.common.append_log.AppendOnlyLog`,
:class:`~alphalab.common.persistent_map.PersistentMap` and
:class:`~alphalab.common.persistent_map.PersistentSet` are immutable, define
value equality, and iterate deterministically.

What was actually quadratic here, and what was not
--------------------------------------------------

ADR-0032 category C finding 1 named the defect class -- ``state.events`` grown
with tuple splats, indexes grown with ``dict()`` copies -- and this package had
both. It also had two larger terms that the finding did not name, and profiling
the submit path before changing anything is what found them. At 8,000
submissions the event splat was not measurable beside:

============================================ ====== ==============================
Term                                          Share  Why
============================================ ====== ==============================
``JobQueue.submit`` re-sorting the whole      ~65%   32,004,000 key-function calls
queue on every submission                            for 8,000 submissions
``validate_job_submission`` building a        ~26%   a set of *every* job id the
union set of four containers per call                cluster has ever seen, per call
============================================ ====== ==============================

Converting the containers alone would have left ~90% of the cost in place, which
is exactly the failure ADR-0032 warned against: "do not blindly refactor packages
where the apparent pattern is not actually responsible for quadratic behavior."

So:

* ``queued_jobs`` stays an **ordered sequence** -- it is a public,
  priority-ordered value that ``alphalab.cluster_scheduler`` and
  ``views.queue_length`` read positionally -- and becomes an ``AppendOnlyLog``.
  :meth:`~alphalab.distributed.queue.JobQueue.submit` appends in O(1) when the
  new job sorts at the tail and rebuilds only when it must go earlier. The
  ordering is identical either way, because a stable sort places a new element
  after every element whose key compares equal.
* ``queued_ids`` is the index that makes the duplicate check O(1). It is
  **derived** -- it is always exactly ``{job.job_id for job in queued_jobs}`` --
  and is carried rather than recomputed for the same reason the OMS order book
  carries its asset and strategy indexes since v2.2.
  ``tests/regression/test_standalone_state_scaling.py`` asserts the invariant
  after every operation that touches the queue, so the two cannot drift.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
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
    """Deterministic snapshot of the Distributed Engine.

    Attributes:
        cluster_id: Identifier for this cluster.
        workers: Registered compute nodes, keyed by ``node_id``.
        queued_jobs: Pending jobs, in priority order: descending ``priority``,
            then ascending ``created_timestamp``.
        queued_ids: The ``job_id`` of every job in ``queued_jobs``. A derived
            index; see the module docstring.
        running_jobs: Assigned or executing jobs, keyed by ``job_id``.
        completed_jobs: Successfully finished jobs, keyed by ``job_id``.
        failed_jobs: Failed and cancelled jobs, keyed by ``job_id``.
        statistics: Cluster-wide counters.
        events: Everything that has happened, in order.
        metadata: Cluster-specific attributes with no canonical field.
    """

    cluster_id: str
    workers: PersistentMap[str, WorkerNode] = field(default_factory=PersistentMap)
    queued_jobs: AppendOnlyLog[Job] = field(default_factory=AppendOnlyLog)
    queued_ids: PersistentSet[str] = field(default_factory=PersistentSet)
    running_jobs: PersistentMap[str, Job] = field(default_factory=PersistentMap)
    completed_jobs: PersistentMap[str, Job] = field(default_factory=PersistentMap)
    failed_jobs: PersistentMap[str, Job] = field(default_factory=PersistentMap)
    statistics: DistributedStatistics = field(default_factory=DistributedStatistics)
    events: AppendOnlyLog[DistributedEvent] = field(default_factory=AppendOnlyLog)
    metadata: Mapping[str, str] = field(default_factory=dict)
