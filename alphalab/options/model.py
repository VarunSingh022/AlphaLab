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

Nothing here changes a number. It describes the one model this package
implements, and names the four things that model does not do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

__all__ = ["BLACK_SCHOLES_MERTON", "ModelAssumptions", "PricingModel"]


class PricingModel(Enum):
    """Which model produced a price or a Greek.

    One member, because one model is implemented. It is an enum rather than a
    string so that a second model added later is a decision with a name, and so
    that a caller comparing two figures can compare the models by identity
    rather than by spelling.
    """

    #: Closed-form European pricing on a lognormal underlying with a constant
    #: volatility and a constant continuously-compounded rate.
    BLACK_SCHOLES = auto()


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
            before expiry. ``False`` here: an American contract priced by this
            model is priced as though it were European, which understates a deep
            in-the-money American put.
        models_dividends: Whether a dividend or carry yield on the underlying is
            an input. ``False`` here: there is no dividend-yield argument, so a
            dividend-paying underlying is priced as though it paid none.
        models_volatility_smile: Whether volatility varies by strike within the
            model. ``False`` here: one volatility is an input per call. A smile
            is expressed by supplying a different volatility per strike, which
            is what :class:`alphalab.options.volatility_surface.VolatilitySurface`
            holds.
        note: One sentence for a report or a refusal message.
    """

    model: PricingModel
    year_basis_days: float
    prices_early_exercise: bool
    models_dividends: bool
    models_volatility_smile: bool
    note: str

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
            )
            if present
        )
        return f"{self.model.name}/{self.year_basis_days:g}d/{flags or 'none'}"


#: What :func:`alphalab.options.pricing.black_scholes_price` and
#: :func:`~alphalab.options.pricing.black_scholes_greeks` actually assume.
#:
#: The 365.25 matches ``_SECONDS_PER_YEAR`` in that module, which is the number
#: the code uses rather than the one a docstring claims.
BLACK_SCHOLES_MERTON: Final = ModelAssumptions(
    model=PricingModel.BLACK_SCHOLES,
    year_basis_days=365.25,
    prices_early_exercise=False,
    models_dividends=False,
    models_volatility_smile=False,
    note=(
        "European closed form on a lognormal underlying. An American contract is priced as "
        "though it were European, a dividend-paying underlying as though it paid none, and "
        "one volatility applies at every strike."
    ),
)
