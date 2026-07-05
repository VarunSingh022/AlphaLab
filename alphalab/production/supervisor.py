"""Deterministic state transitions for managed subsystems."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.production.events import ModuleRestarted, ModuleStarted, ModuleStopped
from alphalab.production.exceptions import InvalidRuntimeStateError
from alphalab.production.process import ManagedProcess, ProcessState
from alphalab.production.state import ProductionState
from alphalab.production.validation import validate_module_registration


class Supervisor:
    """Stateless mutator tracking subsystem states."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def register_module(
        state: ProductionState, module_id: str, timestamp: float
    ) -> ProductionState:
        validate_module_registration(state, module_id)
        new_procs = dict(state.processes)
        new_procs[module_id] = ManagedProcess(module_id, ProcessState.STOPPED, 0, timestamp)
        return replace(state, processes=new_procs)

    @staticmethod
    def start_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        if module_id not in state.processes:
            raise InvalidRuntimeStateError(f"Module '{module_id}' not found.")

        proc = state.processes[module_id]
        if proc.state == ProcessState.RUNNING:
            return state

        new_procs = dict(state.processes)
        new_procs[module_id] = replace(
            proc, state=ProcessState.RUNNING, last_state_change=timestamp
        )
        evt = ModuleStarted(Supervisor._create_id(), timestamp, module_id)

        return replace(state, processes=new_procs, events=(*state.events, evt))

    @staticmethod
    def stop_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        if module_id not in state.processes:
            raise InvalidRuntimeStateError(f"Module '{module_id}' not found.")

        proc = state.processes[module_id]
        new_procs = dict(state.processes)
        new_procs[module_id] = replace(
            proc, state=ProcessState.STOPPED, last_state_change=timestamp
        )
        evt = ModuleStopped(Supervisor._create_id(), timestamp, module_id)

        return replace(state, processes=new_procs, events=(*state.events, evt))

    @staticmethod
    def restart_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        if module_id not in state.processes:
            raise InvalidRuntimeStateError(f"Module '{module_id}' not found.")

        proc = state.processes[module_id]
        new_attempt = proc.restart_count + 1

        new_procs = dict(state.processes)
        new_procs[module_id] = replace(
            proc, state=ProcessState.RUNNING, restart_count=new_attempt, last_state_change=timestamp
        )
        evt = ModuleRestarted(Supervisor._create_id(), timestamp, module_id, new_attempt)

        return replace(state, processes=new_procs, events=(*state.events, evt))

    @staticmethod
    def fail_module(state: ProductionState, module_id: str, timestamp: float) -> ProductionState:
        if module_id not in state.processes:
            return state
        proc = state.processes[module_id]
        new_procs = dict(state.processes)
        new_procs[module_id] = replace(proc, state=ProcessState.FAILED, last_state_change=timestamp)
        return replace(state, processes=new_procs)
