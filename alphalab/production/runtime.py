"""Low-level mutators supporting the Production Engine facade."""

import uuid
from dataclasses import replace

from alphalab.production.checkpoint import Checkpoint
from alphalab.production.events import (
    CheckpointCreated,
    CheckpointRestored,
    HealthUpdated,
    HeartbeatGenerated,
    RuntimeStarted,
    RuntimeStopped,
)
from alphalab.production.exceptions import CheckpointError
from alphalab.production.health import SystemHealth, compute_health_score
from alphalab.production.heartbeat import HeartbeatRecord, HeartbeatStatus
from alphalab.production.logging import LogEntry
from alphalab.production.state import ProductionState
from alphalab.production.validation import validate_start, validate_stop


class RuntimeOperations:
    """Core state modifiers."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def start(state: ProductionState, timestamp: float) -> ProductionState:
        validate_start(state)
        evt = RuntimeStarted(RuntimeOperations._create_id(), timestamp, state.runtime_id)
        return replace(
            state, 
            is_running=True, 
            start_time=timestamp, 
            last_tick=timestamp,
            events=(*state.events, evt)
        )

    @staticmethod
    def stop(state: ProductionState, reason: str, timestamp: float) -> ProductionState:
        validate_stop(state)
        evt = RuntimeStopped(RuntimeOperations._create_id(), timestamp, state.runtime_id, reason)
        return replace(
            state, 
            is_running=False, 
            events=(*state.events, evt)
        )

    @staticmethod
    def heartbeat(
        state: ProductionState, module_id: str, latency: float, timestamp: float
    ) -> ProductionState:
        new_hbs = dict(state.heartbeats)
        current = state.heartbeats.get(
            module_id, HeartbeatRecord(module_id, HeartbeatStatus.ALIVE, timestamp)
        )
        
        new_hbs[module_id] = replace(
            current, status=HeartbeatStatus.ALIVE, last_ping_time=timestamp
        )
        
        evt = HeartbeatGenerated(RuntimeOperations._create_id(), timestamp, module_id, latency)
        mets = replace(state.metrics, heartbeats_received=state.metrics.heartbeats_received + 1)
        
        return replace(state, heartbeats=new_hbs, metrics=mets, events=(*state.events, evt))

    @staticmethod
    def checkpoint(
        state: ProductionState, cp: Checkpoint, timestamp: float
    ) -> ProductionState:
        if any(c.checkpoint_id == cp.checkpoint_id for c in state.checkpoints):
            raise CheckpointError(f"Duplicate checkpoint ID '{cp.checkpoint_id}'.")
            
        evt = CheckpointCreated(RuntimeOperations._create_id(), timestamp, cp.checkpoint_id)
        mets = replace(state.metrics, total_checkpoints=state.metrics.total_checkpoints + 1)
        
        return replace(
            state, 
            checkpoints=(*state.checkpoints, cp), 
            metrics=mets, 
            events=(*state.events, evt)
        )

    @staticmethod
    def restore(
        state: ProductionState, checkpoint_id: str, timestamp: float
    ) -> ProductionState:
        if not any(c.checkpoint_id == checkpoint_id for c in state.checkpoints):
            raise CheckpointError(f"Checkpoint '{checkpoint_id}' not found.")
            
        evt = CheckpointRestored(RuntimeOperations._create_id(), timestamp, checkpoint_id)
        return replace(state, events=(*state.events, evt))

    @staticmethod
    def update_health(
        state: ProductionState, 
        cpu: float, 
        mem: float, 
        backlog: int, 
        b_up: bool, 
        m_up: bool, 
        ts: float
    ) -> ProductionState:
        score = compute_health_score(cpu, mem, backlog, b_up, m_up)
        health = SystemHealth(score, cpu, mem, backlog, 100.0, b_up, m_up, score > 80.0)
        evt = HealthUpdated(RuntimeOperations._create_id(), ts, score)
        return replace(state, health=health, events=(*state.events, evt))
        
    @staticmethod
    def log(state: ProductionState, entry: LogEntry) -> ProductionState:
        return replace(state, logs=(*state.logs, entry))