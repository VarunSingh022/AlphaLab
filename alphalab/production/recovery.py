"""Deterministic failure recovery leveraging checkpoints."""

import uuid
from dataclasses import replace

from alphalab.production.events import RecoveryCompleted, RecoveryStarted
from alphalab.production.exceptions import RecoveryError
from alphalab.production.state import ProductionState


class RecoveryEngine:
    """Restores cluster state from the last known good configuration."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def recover(state: ProductionState, reason: str, timestamp: float) -> ProductionState:
        if not state.checkpoints:
            raise RecoveryError("Cannot recover: No checkpoints exist.")

        # Latest checkpoint
        latest_cp = state.checkpoints[-1]

        start_evt = RecoveryStarted(RecoveryEngine._create_id(), timestamp, reason)
        comp_evt = RecoveryCompleted(
            RecoveryEngine._create_id(), timestamp, latest_cp.checkpoint_id
        )

        # Increment metric
        new_metrics = replace(state.metrics, total_recoveries=state.metrics.total_recoveries + 1)

        # State transitions safely restoring standard operation
        return replace(
            state, is_running=True, metrics=new_metrics, events=(*state.events, start_evt, comp_evt)
        )
