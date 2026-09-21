"""How close is close enough -- stated once, and never assumed.

Three v3.5 capabilities compare a number AlphaLab expected against a number
something else reported: runtime health checks an observation against a
threshold, the comparison layer checks a paper fill against a backtested one,
and reconciliation checks a position against the broker's. All three need the
same decision -- *is this difference material?* -- and all three would otherwise
have answered it with a literal.

The rule this module exists to keep
------------------------------------

**A tolerance is supplied or the comparison does not happen.** There is no
default tolerance and no implicit exact-match rule, because both are a policy
nobody chose:

* a hidden ``== 0`` says a fill one cent away from the backtest is a break,
  which is true of no live venue that has ever existed;
* a hidden ``0.01`` says a cent is fine, which is false for a position count.

So :class:`Tolerance` has no usable default -- one that bounds nothing is
refused at construction, exactly as
:class:`~alphalab.lifecycle.evidence.MetricThreshold` is -- and a metric with no
tolerance stated is reported as *not comparable* rather than as matching. That
distinction is the whole point: "nobody said how close counts" and "they agree"
are different facts, and only one of them is evidence.

Zero expected values
--------------------

A relative tolerance is a fraction of ``|expected|``, so at ``expected == 0`` it
permits nothing. That is deliberate and is not a special case bolted on: the
difference between zero and 0.4 is unbounded in relative terms, and a relative
rule that silently became an absolute one at the origin would pass exactly the
comparison a reader most wants refused. State an absolute tolerance as well when
zero is a value the metric actually takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.lifecycle.exceptions import LifecycleInputError

__all__ = ["Tolerance", "ToleranceOutcome"]


class ToleranceOutcome(Enum):
    """How two numbers that were both supplied relate to one another.

    Deliberately three values and not two. ``EXACT`` and ``WITHIN_TOLERANCE``
    both mean "no action", and they are still different facts: a backtest and a
    paper run that agree exactly are running the same arithmetic, and two that
    agree to within a cent are not. Collapsing them hides the moment the first
    becomes the second.
    """

    #: The two values are equal. No tolerance was needed.
    EXACT = auto()

    #: They differ by no more than the stated tolerance permits.
    WITHIN_TOLERANCE = auto()

    #: They differ by more than the stated tolerance permits.
    MATERIAL = auto()


@dataclass(frozen=True, slots=True)
class Tolerance:
    """The permitted difference between an expected and an observed number.

    Attributes:
        absolute: Largest permitted ``|observed - expected|``, in the metric's
            own units. ``None`` means no absolute bound is stated.
        relative: Largest permitted ``|observed - expected| / |expected|``, as a
            fraction. ``None`` means no relative bound is stated.

    At least one must be set, and when both are the *larger* allowance wins: a
    caller stating ``absolute=0.01, relative=0.001`` means "a cent, or a tenth of
    a percent, whichever is more forgiving", which is how a tolerance that has to
    cover both small and large values is actually written.

    Raises:
        LifecycleInputError: If neither bound is set, or either is negative. A
            tolerance that bounds nothing would make every comparison pass and
            read as a check that happened -- the failure
            :class:`~alphalab.lifecycle.evidence.MetricThreshold` refuses for the
            same reason.
    """

    absolute: Decimal | None = None
    relative: Decimal | None = None

    def __post_init__(self) -> None:
        if self.absolute is None and self.relative is None:
            raise LifecycleInputError(
                "A Tolerance must state an absolute bound, a relative bound or both; "
                "one that bounds nothing would pass every comparison and read as a "
                "check that happened."
            )
        if self.absolute is not None and self.absolute < Decimal("0"):
            raise LifecycleInputError(
                f"Tolerance.absolute is {self.absolute}; a negative allowance would "
                "refuse a difference of zero."
            )
        if self.relative is not None and self.relative < Decimal("0"):
            raise LifecycleInputError(
                f"Tolerance.relative is {self.relative}; a negative allowance would "
                "refuse a difference of zero."
            )

    def allowance(self, expected: Decimal) -> Decimal:
        """The largest difference from ``expected`` this tolerance permits.

        The maximum of the two bounds that were stated, which is what makes
        stating both mean "whichever is more forgiving". A relative bound is
        measured against ``|expected|`` and therefore permits nothing at zero;
        see the module docstring.
        """

        bounds = [bound for bound in (self.absolute,) if bound is not None]
        if self.relative is not None:
            bounds.append(self.relative * abs(expected))
        return max(bounds)

    def outcome(self, expected: Decimal, observed: Decimal) -> ToleranceOutcome:
        """Classify one expected/observed pair.

        Pure and total: two supplied numbers always produce exactly one outcome.
        Missing values are **not** handled here -- an absent observation is not a
        number this can be asked about, and the caller reports it as missing
        rather than passing a zero in its place.
        """

        if expected == observed:
            return ToleranceOutcome.EXACT
        difference = abs(observed - expected)
        if difference <= self.allowance(expected):
            return ToleranceOutcome.WITHIN_TOLERANCE
        return ToleranceOutcome.MATERIAL
