"""Adapters converting specific AlphaLab tasks to generalized Jobs."""

from typing import Any

from alphalab.distributed.job import Job, JobStatus, JobType


class DistributedAdapter:
    """Stateless translator formatting platform workloads for distributed orchestration."""

    @staticmethod
    def create_optimization_job(
        job_id: str, parameters: dict[str, Any], priority: int, timestamp: float
    ) -> Job:
        """Converts an optimizer trial into a standard Job."""
        return Job(
            job_id=job_id,
            job_type=JobType.OPTIMIZATION,
            status=JobStatus.PENDING,
            priority=priority,
            created_timestamp=timestamp,
            payload=parameters,
        )

    @staticmethod
    def create_replay_job(
        job_id: str, start_time: float, end_time: float, priority: int, timestamp: float
    ) -> Job:
        """Converts a historical replay session into a standard Job."""
        payload = {"start_time": start_time, "end_time": end_time}
        return Job(
            job_id=job_id,
            job_type=JobType.REPLAY,
            status=JobStatus.PENDING,
            priority=priority,
            created_timestamp=timestamp,
            payload=payload,
        )

    @staticmethod
    def create_analytics_job(
        job_id: str, snapshot_ids: tuple[str, ...], priority: int, timestamp: float
    ) -> Job:
        """Converts an analytics calculation batch into a standard Job."""
        return Job(
            job_id=job_id,
            job_type=JobType.ANALYTICS,
            status=JobStatus.PENDING,
            priority=priority,
            created_timestamp=timestamp,
            payload={"snapshot_ids": list(snapshot_ids)},
        )
