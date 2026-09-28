"""The one decimal context AlphaLab's accounting and execution arithmetic runs in.

Why a pinned context
--------------------
``Decimal`` arithmetic rounds to the precision of the *thread's current context*,
and so does ``quantize`` unless it is told otherwise. Every ``a * b``, ``a + b``
and ``a / b`` in the accounting path therefore used to depend on whatever the
caller -- or any library the caller imported -- had set with
``decimal.getcontext()``. Measured at v3.9.0 on one round trip (buy 7 at
100.005, sell 3 at 101.115): under the default context the book ended with cash
``999603.30`` and realized P&L ``3.32``; after ``getcontext().rounding =
ROUND_DOWN`` -- or ``ROUND_HALF_UP`` -- the same fills booked ``999603.31`` and
``3.33``. Nothing refused; the books were simply different books. A caller that
lowered ``getcontext().prec`` below the digits of an amount got an
``InvalidOperation`` from deep inside a fill instead.

v3.9 pinned the three execution modules it added (algorithms, routing,
execution quality) and ``split_by_contribution``. v3.10 pins the rest of the
accounting and execution path to the one context defined here, and a regression
test runs a whole backtest under a hostile ambient context and requires the
result to be byte-identical to the run under the default one.

The context
-----------
34 significant digits -- IEEE 754 decimal128, the precision a general-purpose
ledger needs for amounts up to 10**30 at a millionth -- rounding half to even
(the rounding ISO 4217 amounts are conventionally settled with, and the one that
does not bias a long sum), and traps on the three conditions that mean the
arithmetic has no answer: an invalid operation, a division by zero and an
overflow. Inexact and rounded results are *not* trapped: a price times a
quantity is routinely inexact at the currency's minor unit, which is what
:func:`alphalab.portfolio.money.to_money` exists to decide.

How it is used
--------------
Leaf computations call the context's methods directly
(``ACCOUNTING_CONTEXT.multiply(quantity, price)``), which touches no thread
state. Entry points whose bodies do a great deal of arithmetic -- a fill applied
to a portfolio, a report applied to an order -- run inside
:func:`accounting_context`, which installs a *copy* for the duration of the call
and restores the caller's context afterwards, so the caller never observes a
change to its own.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from functools import wraps
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "ACCOUNTING_CONTEXT",
    "ACCOUNTING_PRECISION",
    "accounting_context",
    "canonical_text",
    "in_accounting_context",
    "plain",
    "require_finite",
]

#: Significant digits of every accounting and execution computation.
ACCOUNTING_PRECISION: Final = 34

#: The pinned context. Never mutate it; :func:`accounting_context` installs a copy.
ACCOUNTING_CONTEXT: Final = Context(
    prec=ACCOUNTING_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def accounting_context() -> AbstractContextManager[Context]:
    """Run a block in a private copy of :data:`ACCOUNTING_CONTEXT`.

    The caller's own context is restored when the block exits, whatever the
    block raised.
    """

    return localcontext(ACCOUNTING_CONTEXT)


def in_accounting_context[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Decorate ``function`` so its whole body runs in :func:`accounting_context`."""

    @wraps(function)
    def pinned(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(ACCOUNTING_CONTEXT):
            return function(*args, **kwargs)

    return pinned


_ONE: Final = Decimal(1)


def plain(value: Decimal) -> Decimal:
    """``value``, never written with a positive exponent: ``1E+2`` becomes ``100``.

    An exact quotient takes the exponent its operands imply, so a basis of
    ``1000.00`` over a quantity of ``10.000000`` is ``Decimal('1E+2')`` -- the
    right number, reported as notation. Only the representation changes. A value
    too large to write out within :data:`ACCOUNTING_PRECISION` digits is
    returned as it is.
    """

    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0 and value.adjusted() < ACCOUNTING_PRECISION:
        return value.quantize(_ONE, context=ACCOUNTING_CONTEXT)
    return value


def canonical_text(value: Decimal) -> str:
    """One text per number: equal values render alike whatever their exponent.

    ``100``, ``100.00`` and ``1E+2`` all render ``100``, and a zero of either sign
    renders ``0``. Plain positional notation, never an exponent, and never rounded:
    every significant digit ``value`` has is kept.

    This is the rendering for an identity derived from a value, where two amounts
    that compare equal must not give two identities. ``str`` keeps the exponent,
    which is right for storage -- a round trip must give back the representation
    it was given -- and wrong for identity.

    Raises:
        AlphaLabValidationError: If ``value`` is NaN or infinite, which has no
            positional text.
    """

    if not value.is_finite():
        raise AlphaLabValidationError(f"{value} has no canonical text; it is not a number.")
    if value.is_zero():
        return "0"
    # A context exactly as precise as the value, so normalizing strips trailing
    # zeros and never rounds a digit away.
    exact = Context(prec=len(value.as_tuple().digits), Emin=-999999, Emax=999999)
    return format(value.normalize(exact), "f")


def require_finite(
    value: Decimal,
    field_name: str,
    *,
    exception_type: type[Exception] = AlphaLabValidationError,
) -> Decimal:
    """Return ``value``, or raise if it is NaN or infinite.

    A ``Decimal('NaN')`` compares as unordered -- ``NaN <= 0`` *signals* in the
    default context -- and ``Decimal('Infinity')`` passes every ``> 0`` check a
    price or quantity is given, so a range check alone admits both.
    """

    if not isinstance(value, Decimal) or not value.is_finite():
        raise exception_type(f"{field_name} must be a finite Decimal, got {value!r}")
    return value
