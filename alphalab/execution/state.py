"""Global immutable state container for the Execution Engine."""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.execution.events import ExecutionEvent
from alphalab.execution.report import ExecutionReport


@dataclass(frozen=True, slots=True)
class ExecutionState:
    """Deterministic snapshot of execution history and generated reports.

    ``reports`` is a :class:`~alphalab.common.persistent_map.PersistentMap`
    rather than a ``dict``: the engine stored a report by rebuilding the whole
    mapping, so N fills copied O(N^2) entries -- the same defect the OMS order
    book had, on the same execution path. It is still an immutable ``Mapping``
    keyed by execution id, and it still serializes as the object it always did.
    """

    reports: PersistentMap[str, ExecutionReport] = field(default_factory=PersistentMap)
    history: AppendOnlyLog[ExecutionReport] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[ExecutionEvent] = field(default_factory=AppendOnlyLog)
