"""Domain exceptions for the Lifecycle."""

from alphalab.common.exceptions import AlphaLabError


class LifecycleError(AlphaLabError):
    """Base exception for all Lifecycle errors."""


class LifecycleInputError(LifecycleError):
    """Raised when a lifecycle operation's inputs are unknown or malformed."""


class LifecycleTransitionError(LifecycleError):
    """Raised when a lifecycle transition is refused.

    Separate from :class:`LifecycleInputError` because the two say different
    things: the inputs named something that does not exist, versus they named
    something real and the move it asked for is not one the lifecycle allows
    from where that thing currently is.
    """


class LifecyclePermissionError(LifecycleError):
    """Raised when the acting principal is not permitted a governed act.

    Answered by the caller's :class:`~alphalab.lifecycle.governance.PermissionAuthority`:
    AlphaLab asks whether an actor holds a permission and records who acted; who
    the actors are, and how they authenticate, is the application's.
    """
