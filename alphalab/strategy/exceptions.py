"""Domain exceptions for the Strategy Runtime."""

from alphalab.common.exceptions import AlphaLabError


class StrategyRuntimeError(AlphaLabError):
    """Base exception for all Strategy Runtime errors."""


class InvalidTransitionError(StrategyRuntimeError):
    """Raised when an invalid lifecycle transition is attempted."""


class InvalidIntentError(StrategyRuntimeError):
    """Raised when a strategy emits a malformed Intent."""


class HookExecutionError(StrategyRuntimeError):
    """Raised when a strategy hook throws an unhandled exception or exceeds timeout."""


class AdaptiveStateError(StrategyRuntimeError):
    """Raised when adaptive state, configuration or a rule's output is not usable.

    A configuration that does not match the state it is applied to, a rule whose
    identity differs from the configuration's, a payload the deterministic
    encoder cannot carry, a checkpoint whose identity does not recompute: each
    would let an adaptive strategy's behaviour stop being reconstructable, so
    each is refused rather than worked around.
    """


class AdaptiveOrderingError(AdaptiveStateError):
    """Raised when an observation arrives out of order or late.

    Adaptive state only moves forwards. An observation at or before the last one
    a state consumed cannot be folded in without rewriting history; the
    deterministic way to include it is to replay from a checkpoint taken before
    its position.
    """
