"""Global immutable state container for the Optimization Engine.

``completed_trials`` and ``events`` are :class:`~alphalab.common.append_log.AppendOnlyLog`
as of v2.17, for the reason v2.1 introduced it: both grew with ``(*state.x, y)``
per trial, so ``N`` trials copied ``O(N^2)`` entries. ADR-0032 category C finding
1, closed by ADR-0034. A log is immutable, defines value equality and is a
``Sequence``, so every reader is unchanged.

``pending_trials`` stays a ``tuple``, and that is a decision with a measurement
behind it rather than an omission.

It is not an accumulation path -- it is the fixed set of trials the caller asked
for, consumed one per step -- and ``step`` drops its head with ``pending[1:]``,
which copies the remainder. Draining ``N`` trials is therefore ``O(N^2)`` pointer
copies in total, and that is the one term v2.17 left super-linear here: measured
over a 2,500 -> 20,000 doubling sweep, this package grows at ~3.0x per doubling
where the other seven converted packages grow at ~2.05x.

**Making it linear needs a view, and the view was tried and rejected.** Python
has no O(1) immutable sequence tail: ``t[1:]`` copies, and so does ``t[:-1]``.
The only structure that answers "take from the front" in O(1) with value
semantics is a view over shared storage with a start offset -- which is
:class:`~alphalab.common.append_log.AppendOnlyLog` plus one field. That change
was implemented and benchmarked, and it cost **+3.9%** on
``benchmarks/benchmark_execution_pipeline.py``, consistent across four
interleaved runs. Every log on the execution path would pay it, on every event,
so that a standalone optimizer's work list could be drained in linear time. It
is the same trade ADR-0028 decision 7 refused at +1.78%, and it is refused here
for the same reason.

What makes that acceptable rather than a deferral is the shape of the cost. It is
bounded by the trial count the caller chose, it is paid once per optimization
rather than per event, and every trial it gates runs a user
:class:`~alphalab.optimizer.protocol.TrialEvaluatorProtocol` -- a real backtest
in any real use -- which dominates it by orders of magnitude. At 20,000 trials
the whole queue cost is ~0.5s against 20,000 backtests. See ADR-0034.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from alphalab.common.append_log import AppendOnlyLog
from alphalab.optimizer.events import OptimizerEvent
from alphalab.optimizer.objective import ObjectiveFunction
from alphalab.optimizer.results import TrialResult


class OptimizerStatus(Enum):
    """Explicit pure state machine stages for the Optimization Engine."""

    CREATED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class OptimizerState:
    """Deterministic snapshot of an active optimization run."""

    engine_id: str
    status: OptimizerStatus
    objective: ObjectiveFunction
    pending_trials: tuple[dict[str, Any], ...]
    completed_trials: AppendOnlyLog[TrialResult] = field(default_factory=AppendOnlyLog)
    best_trial: TrialResult | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    events: AppendOnlyLog[OptimizerEvent] = field(default_factory=AppendOnlyLog)
