"""The structural check of an :class:`~alphalab.strategy.events.Intent`.

One check, wherever an intent arrives: the strategy runtime runs it on every
intent a hook returns, and allocation on every intent it is handed. Until v3.13
each package kept its own ``validate_intent`` under the one public name, and
the two disagreed (ledger API-001): the strategy's never asked whether
``target`` was a finite number or ``kind`` an
:class:`~alphalab.strategy.events.IntentKind`, and a ``NaN`` strength escaped
it as a bare :class:`decimal.InvalidOperation`; allocation's never asked
whether ``terms`` were :class:`~alphalab.common.order_terms.OrderTerms`. An
intent one of them passed could be refused by the other, for a reason the first
did not know existed.
"""

import math
from decimal import Decimal

from alphalab.common.order_terms import OrderTerms
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.exceptions import InvalidIntentError

__all__ = ["validate_intent"]

_ZERO = Decimal("0")
_ONE = Decimal("1")


def validate_intent(intent: Intent) -> None:
    """Refuse an intent that is not structurally one, naming the first rule it breaks.

    Malformed intents are dropped: by the strategy runtime, which fails the
    strategy that emitted one, and by allocation, which records the refusal and
    sizes the rest of its batch.

    Raises:
        InvalidIntentError: If the intent has no strategy or instrument, its
            ``kind`` is not an :class:`~alphalab.strategy.events.IntentKind`,
            its ``target`` is not a finite :class:`~decimal.Decimal`, its
            ``strength`` is not a finite number in ``[0, 1]``, its timestamp is
            not a finite, non-negative instant, or its ``terms`` are not
            :class:`~alphalab.common.order_terms.OrderTerms`.
    """

    if not intent.strategy_id:
        raise InvalidIntentError("Intent must specify a strategy_id.")
    if not intent.instrument:
        raise InvalidIntentError("Intent must specify an instrument.")
    if not isinstance(intent.kind, IntentKind):
        raise InvalidIntentError(f"Intent kind must be an IntentKind, got {intent.kind!r}.")
    if not isinstance(intent.target, Decimal) or not intent.target.is_finite():
        raise InvalidIntentError(f"Intent target must be a finite Decimal, got {intent.target!r}.")
    strength = intent.strength
    if not (isinstance(strength, Decimal) and strength.is_finite() and _ZERO <= strength <= _ONE):
        raise InvalidIntentError(f"Intent strength must be between 0.0 and 1.0, got {strength}.")
    if isinstance(intent.timestamp, bool) or not (
        isinstance(intent.timestamp, int | float)
        and math.isfinite(intent.timestamp)
        and intent.timestamp >= 0
    ):
        raise InvalidIntentError(
            f"Intent timestamp must be a finite, non-negative instant, got {intent.timestamp!r}."
        )
    if not isinstance(intent.terms, OrderTerms):
        raise InvalidIntentError(
            f"Intent terms must be OrderTerms, got {type(intent.terms).__name__}."
        )
