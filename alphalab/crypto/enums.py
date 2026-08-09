"""Crypto-specific enumerations.

Side (long/short direction) is not redefined here -- `alphalab.core.enums.Side`
already covers it, same principle applied in `alphalab.options` and
`alphalab.futures`.
"""

from enum import Enum, auto


class InstrumentType(Enum):
    """The three instrument shapes this engine supports, per the roadmap."""

    SPOT = auto()
    FUTURE = auto()
    PERPETUAL = auto()
