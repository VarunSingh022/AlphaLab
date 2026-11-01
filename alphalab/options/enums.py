"""Enumerations for option contract classification.

Order side (buying or selling a contract) is deliberately not redefined here --
`alphalab.core.enums.Side` (BUY/SELL) already covers it and is reused directly by
`alphalab.options.strategy.OptionLeg`. Only concepts genuinely unique to options
(call vs. put, exercise style) get new enums.
"""

from enum import Enum, auto


class OptionType(Enum):
    """Whether a contract is a call or a put."""

    CALL = auto()
    PUT = auto()


class ExerciseStyle(Enum):
    """When a contract may be exercised."""

    AMERICAN = auto()
    EUROPEAN = auto()
    #: On the dates or in the windows the contract declares
    #: (:attr:`~alphalab.instrument.terms.OptionTerms.exercise_windows`, v4.2.0,
    #: ledger FUT-016). A lifecycle term: the analytical and lattice pricers model
    #: American and European exercise only, and
    #: :class:`~alphalab.options.contract.OptionContract` refuses a Bermudan one.
    BERMUDAN = auto()
