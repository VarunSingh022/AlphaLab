"""Domain exceptions for the Live Trading Runtime.

:class:`~alphalab.runtime.run.StrategyFailedError` is defined beside the
:class:`~alphalab.runtime.run.RunState` it carries, in :mod:`alphalab.runtime.run`,
so that it can name that type without an import cycle.
"""

from alphalab.common.exceptions import AlphaLabError


class AlphaLabRuntimeError(AlphaLabError):
    """Base exception for all Runtime orchestration errors."""


class RuntimeValidationError(AlphaLabRuntimeError):
    """Raised when runtime configurations or parameters are invalid."""


class HistoryNotRetainedError(AlphaLabRuntimeError):
    """Raised when a strategy asks for market history older than the run keeps.

    A run's :class:`~alphalab.runtime.retention.RetentionPolicy` bounds how many
    market events a strategy's history view reads. An answer that would need an
    older one is refused rather than shortened (v3.12, ledger PRF-004).
    """
