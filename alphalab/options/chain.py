"""Option chain: a snapshot of available contracts for an underlying."""

from dataclasses import dataclass

from alphalab.options.contract import OptionContract
from alphalab.options.enums import OptionType


@dataclass(frozen=True, slots=True)
class OptionChain:
    """An immutable snapshot of every contract available for one underlying.

    Attributes:
        underlying_asset_id: Identifier of the underlying asset.
        timestamp: Unix timestamp this snapshot is as-of.
        contracts: Every contract in the chain, in no particular order.
    """

    underlying_asset_id: str
    timestamp: float
    contracts: tuple[OptionContract, ...]


def calls(chain: OptionChain) -> tuple[OptionContract, ...]:
    """Returns only the call contracts in a chain."""
    return tuple(c for c in chain.contracts if c.option_type is OptionType.CALL)


def puts(chain: OptionChain) -> tuple[OptionContract, ...]:
    """Returns only the put contracts in a chain."""
    return tuple(c for c in chain.contracts if c.option_type is OptionType.PUT)


def expiries(chain: OptionChain) -> tuple[float, ...]:
    """Returns every distinct expiry timestamp present in a chain, ascending."""
    return tuple(sorted({c.expiry for c in chain.contracts}))


def by_expiry(chain: OptionChain, expiry: float) -> tuple[OptionContract, ...]:
    """Returns every contract in a chain matching a specific expiry timestamp."""
    return tuple(c for c in chain.contracts if c.expiry == expiry)


def strikes_for_expiry(chain: OptionChain, expiry: float) -> tuple[float, ...]:
    """Returns every distinct strike available at a given expiry, ascending."""
    return tuple(sorted({float(c.strike) for c in by_expiry(chain, expiry)}))
