"""Records that a recovery was attempted. Restores no state.

**Nothing here reads a checkpoint's contents.** :meth:`RecoveryEngine.recover`
requires that at least one :class:`~alphalab.production.checkpoint.Checkpoint`
has been recorded, names the most recent one in a ``RecoveryCompleted`` event,
sets ``is_running`` back to ``True`` and increments a counter. No portfolio, no
order book, no position and no runtime state is rebuilt, because this package
holds none of them -- a ``Checkpoint``'s state fields are opaque strings it never
decodes.

Restoring an actual run means reading its payload back through
:class:`~alphalab.persistence.run_store.RunStateStore` and handing it to the
``from_primitives`` / ``restore`` pair of the module that captured it, then
continuing with ``TradingSession.resume`` or ``BacktestEngine.resume``. That
path exists as of v2.13 and is proven byte-identical across a process boundary;
see ADR-0029.
"""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.production.events import RecoveryCompleted, RecoveryStarted
from alphalab.production.exceptions import RecoveryError
from alphalab.production.state import ProductionState


class RecoveryEngine:
    """Restores cluster state from the last known good configuration."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

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
