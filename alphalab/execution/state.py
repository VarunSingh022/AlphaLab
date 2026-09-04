"""Global immutable state container for the Execution Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.execution.events import ExecutionEvent
from alphalab.execution.report import ExecutionReport


@dataclass(frozen=True, slots=True)
class ExecutionState:
    """Deterministic snapshot of execution history and generated reports."""

    reports: Mapping[str, ExecutionReport] = field(default_factory=dict)
    history: AppendOnlyLog[ExecutionReport] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[ExecutionEvent] = field(default_factory=AppendOnlyLog)
