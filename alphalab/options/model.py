"""What the pricer assumes, written down so a Greek can travel with it.

A delta is not a property of a contract. It is the output of a model evaluated
under assumptions, and the same contract at the same spot has a different delta
under Black-Scholes than under a binomial tree with the same inputs -- more so
the further the contract is from European and dividend-free.

:mod:`alphalab.options.pricing` has said this in prose since v1: "a
European-style closed-form model; it does not account for early exercise premium
on American contracts". :class:`ModelAssumptions` makes it a value instead, so
it can be carried onto a report, compared between two figures, and refused when
it does not match. The precedent is
:class:`alphalab.analytics.decomposition.VaRPolicy`, which carries the method and
the confidence together for the same reason: "a figure cannot travel without the
assumptions that produced it".

Nothing here changes a number. It describes the models this package
implements, and names what each does and does not do. Since v3.11 the closed
form takes the underlying's carry (a dividend yield, a foreign rate or a futures
contract's zero carry; ledger NUM-005), so ``models_dividends`` is ``True``.
Since v3.13 there is a second model, a Cox-Ross-Rubinstein lattice
(:mod:`alphalab.options.binomial`, ledger NUM-006), which prices early exercise
and cash dividends; its assumptions name its step count, because the step count
moves the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

__all__ = ["BLACK_SCHOLES_MERTON", "ModelAssumptions", "PricingModel"]


class PricingModel(Enum):
    """Which model produced a price or a Greek.

    An enum rather than a string so that a caller comparing two figures can
    compare the models by identity rather than by spelling. The second member
    arrived in v3.13 as the decision with a name that the first one's docstring
    said a second model would be.
    """

    #: Closed-form European pricing on a lognormal underlying with a constant
    #: volatility, a constant continuously-compounded rate and a stated
    #: continuous carry -- generalized Black-Scholes-Merton.
    BLACK_SCHOLES = auto()

    #: A Cox-Ross-Rubinstein binomial lattice of a stated number of steps, on
    #: the same lognormal underlying and carry, with escrowed cash dividends and
    #: early exercise wherever the contract allows it.
    BINOMIAL_CRR = auto()


@dataclass(frozen=True, slots=True)
class ModelAssumptions:
    """The stated conditions a price or Greek was computed under.

    Attributes:
        model: Which model.
        year_basis_days: How many days the model calls a year when it converts
            an expiry to a maturity. Recorded because 365, 365.25 and a
            business-day count are all in use and a vega quoted under one is
            not the vega quoted under another.
        prices_early_exercise: Whether the model values the right to exercise
            before expiry. ``False`` for the closed form: an American contract
            priced by it is priced as though it were European, which understates
            a deep in-the-money American put. ``True`` for the lattice.
        models_dividends: Whether a dividend or carry yield on the underlying is
            an input. ``True`` since v3.11: every call states a
            :class:`~alphalab.options.carry.Carry` -- a continuous dividend
            yield, a foreign rate or a futures contract.
        models_volatility_smile: Whether volatility varies by strike within the
            model. ``False`` here: one volatility is an input per call. A smile
            is expressed by supplying a different volatility per strike, which
            is what :class:`alphalab.options.volatility_surface.VolatilitySurface`
            holds.
        note: One sentence for a report or a refusal message.
        models_discrete_dividends: Whether a known *cash* dividend, paid on a
            date, is an input. ``False`` for the closed form; ``True`` for the
            lattice, which escrows them.
        steps: A lattice's step count, or ``None`` for a closed form. Part of
            the assumptions because it moves the answer: the same contract on
            200 and on 2,000 steps prices differently.
    """

    model: PricingModel
    year_basis_days: float
    prices_early_exercise: bool
    models_dividends: bool
    models_volatility_smile: bool
    note: str
    models_discrete_dividends: bool = False
    steps: int | None = None

    @property
    def identity(self) -> str:
        """A short, stable rendering for a report line or a cache key.

        Deterministic and derived from the fields, so two figures computed under
        the same assumptions produce the same string in any process.
        """

        flags = "".join(
            letter
            for letter, present in (
                ("E", self.prices_early_exercise),
                ("D", self.models_dividends),
                ("S", self.models_volatility_smile),
                ("C", self.models_discrete_dividends),
            )
            if present
        )
        rendered = f"{self.model.name}/{self.year_basis_days:g}d/{flags or 'none'}"
        return rendered if self.steps is None else f"{rendered}/n={self.steps}"


#: What :func:`alphalab.options.pricing.black_scholes_price` and
#: :func:`~alphalab.options.pricing.black_scholes_greeks` actually assume.
#:
#: The 365.25 matches ``_SECONDS_PER_YEAR`` in that module, which is the number
#: the code uses rather than the one a docstring claims.
BLACK_SCHOLES_MERTON: Final = ModelAssumptions(
    model=PricingModel.BLACK_SCHOLES,
    year_basis_days=365.25,
    prices_early_exercise=False,
    models_dividends=True,
    models_volatility_smile=False,
    note=(
        "European closed form on a lognormal underlying with a stated continuous carry. An "
        "American contract is priced as though it were European, a discrete dividend is not "
        "modelled, and one volatility applies at every strike."
    ),
)
