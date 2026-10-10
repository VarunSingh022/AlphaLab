"""Domain exceptions for the Allocation Engine."""

from alphalab.common.exceptions import AlphaLabError


class AllocationError(AlphaLabError):
    """Base exception for all Allocation Engine errors."""

    pass


class AllocationValidationError(AllocationError):
    """Raised when an intent or allocation fails structural validation."""

    pass


class SizingRefusedError(AllocationValidationError):
    """A sizing model cannot size an intent from what it was given.

    Raised for an intent whose instrument has no positive price, or -- for
    :class:`~alphalab.allocation.sizing.VolatilityTargetSizing` -- no positive
    volatility. Until v3.10 the models returned a quantity of zero instead, or
    sized against an assumed volatility of 1%, and the intent vanished without a
    record or was sized from a number nobody supplied (ledger ALC-003).
    :meth:`~alphalab.allocation.engine.AllocationEngine.allocate` records each
    refusal as an ``AllocationRejected`` naming the intent, and sizes the rest.
    """

    pass


class BudgetExceededError(AllocationError):
    """Exported, and raised nowhere.

    ``AllocationEngine.allocate`` *returns* on a budget breach -- an empty tuple
    of orders plus a :class:`~alphalab.allocation.events.BudgetExceeded` event --
    rather than raising, because a refused batch is an ordinary outcome of a
    market event and not an error condition. This class has therefore never had
    a raise site.

    It is left in place in v2.6 rather than removed: deleting an exported name
    is a breaking change, and v2.6 is not the release for it. Scheduled for
    removal in v3.0. Do not start raising it -- that would change ``allocate``
    from returning to raising, which is a behavioural break dressed as a fix.
    """

    pass


class UnknownReservationError(AllocationError):
    """Raised when releasing capital an order does not hold.

    A reservation is released exactly once. A second release -- or a release of
    an order that was never allocated, or whose capital an execution already
    consumed -- is a lifecycle defect, so it raises rather than quietly
    subtracting from the running total.
    """

    pass
