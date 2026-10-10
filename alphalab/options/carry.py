"""What holding the underlying earns or costs: the carry an option is priced under.

Until v3.11 :mod:`alphalab.options.pricing` took one rate and treated every
underlying as a stock that pays nothing (ledger NUM-005). That misprices the
three underlyings most listed options are written on -- an index that pays
dividends, a currency that earns its own interest rate, a futures contract that
costs nothing to hold -- and biases every volatility implied from them.

Generalized Black-Scholes-Merton
--------------------------------
The underlying grows, under the pricing measure, at its **cost of carry** ``b``,
and the same closed form prices all three (Haug, *The Complete Guide to Option
Pricing Formulas*, ch. 1)::

    d1 = (ln(S/K) + (b + sigma^2/2) T) / (sigma sqrt(T)),  d2 = d1 - sigma sqrt(T)
    call = S e^((b-r)T) N(d1) - K e^(-rT) N(d2)
    put  = K e^(-rT) N(-d2) - S e^((b-r)T) N(-d1)

==============================  =====================  ============================
Underlying                      Carry                  ``b``
==============================  =====================  ============================
Stock or index, yield ``q``     :func:`dividend_yield`  ``r - q`` (Merton 1973)
A stock paying nothing          ``dividend_yield(0)``   ``r`` (Black-Scholes 1973)
Currency, foreign rate ``r_f``  :func:`foreign_rate`    ``r - r_f`` (Garman-Kohlhagen)
Futures contract                :data:`FUTURES_CARRY`   ``0`` (Black 1976); ``S`` is
                                                        the futures price
==============================  =====================  ============================

The carry is **required** at every call: a dividend-free stock is
``dividend_yield(0.0)``, said out loud, because a default of "pays nothing" is
exactly the silent assumption that mispriced an index option.

What a carry is not
-------------------
A continuous yield. A known discrete dividend -- a stock going ex-dividend by a
fixed amount before expiry -- is not a yield, and approximating one by a yield
misstates the price near the ex-date. The closed form does not model one; the
lattice does, as a :class:`~alphalab.options.binomial.CashDividend` escrowed out
of the spot (since v3.13).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

from alphalab.options.exceptions import OptionInputError

__all__ = ["FUTURES_CARRY", "Carry", "CarryKind", "dividend_yield", "foreign_rate"]


class CarryKind(Enum):
    """What the carry's yield is, which decides how it moves with the rate."""

    #: A continuous dividend (or lease) yield on a stock, an index or a
    #: commodity. ``b = r - q``.
    DIVIDEND_YIELD = auto()

    #: A currency's own interest rate: the underlying is one unit of a foreign
    #: currency. ``b = r - r_f``.
    FOREIGN_RATE = auto()

    #: A futures contract, which costs nothing to hold. ``b = 0``, whatever the
    #: rate.
    FUTURES = auto()


@dataclass(frozen=True, slots=True)
class Carry:
    """The carry of one underlying: its kind and its continuous yield.

    Attributes:
        kind: What the yield is.
        yield_rate: The continuous annual yield as a decimal fraction -- ``q``
            for a dividend yield, ``r_f`` for a foreign rate. Zero for a
            futures contract, which has none. May be negative: a currency with a
            negative interest rate, a commodity whose storage costs exceed its
            convenience yield.
    """

    kind: CarryKind
    yield_rate: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CarryKind):
            raise OptionInputError(f"Carry.kind must be a CarryKind, got {self.kind!r}.")
        if isinstance(self.yield_rate, bool) or not isinstance(self.yield_rate, int | float):
            raise OptionInputError(f"Carry.yield_rate must be a number, got {self.yield_rate!r}.")
        if not math.isfinite(self.yield_rate):
            raise OptionInputError(f"Carry.yield_rate must be finite, got {self.yield_rate}.")
        if self.kind is CarryKind.FUTURES and self.yield_rate != 0.0:
            raise OptionInputError(
                f"A futures contract has no yield; its carry is zero by construction, and "
                f"a yield of {self.yield_rate} would be a second, contradictory statement."
            )
        object.__setattr__(self, "yield_rate", float(self.yield_rate))

    def cost_of_carry(self, risk_free_rate: float) -> float:
        """``b`` for this underlying at ``risk_free_rate``."""

        if self.kind is CarryKind.FUTURES:
            return 0.0
        return risk_free_rate - self.yield_rate

    @property
    def moves_with_rate(self) -> bool:
        """Whether ``b`` moves one-for-one with the rate, which is what rho holds fixed.

        A dividend yield or a foreign rate is held fixed while the domestic rate
        moves, so ``b`` moves with it; a futures contract's ``b`` is zero at any
        rate.
        """

        return self.kind is not CarryKind.FUTURES

    @property
    def identity(self) -> str:
        """A stable rendering for a report line: ``DIVIDEND_YIELD:0.015``."""

        return f"{self.kind.name}:{self.yield_rate!r}"


def dividend_yield(q: float) -> Carry:
    """A stock, an index or a commodity yielding ``q`` continuously; ``0.0`` for none."""

    return Carry(CarryKind.DIVIDEND_YIELD, q)


def foreign_rate(rate: float) -> Carry:
    """One unit of a foreign currency earning ``rate`` continuously (Garman-Kohlhagen)."""

    return Carry(CarryKind.FOREIGN_RATE, rate)


#: A futures contract: no yield, a cost of carry of zero (Black 1976). The spot a
#: pricer is given is the futures price.
FUTURES_CARRY: Final = Carry(CarryKind.FUTURES, 0.0)
