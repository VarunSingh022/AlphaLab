"""Which economics an instrument is booked by (ledger ACC-005).

The declaration itself -- :class:`~alphalab.conventions.economics.InstrumentEconomics`
and its :class:`~alphalab.conventions.economics.SettlementModel` -- is a market
convention and lives in :mod:`alphalab.conventions.economics`, where the
portfolio that books by it can read it. What this module adds is the rule for
an instrument that declares none, which needs the instrument's
:class:`~alphalab.core.enums.AssetType` and so belongs with the instrument.

Declared, or refused
--------------------

A record that declares no economics is read by :func:`economics_for` -- as a
cash equity with a multiplier of one and no grid when that is what its asset
type *is* (an equity, spot crypto, a currency, cash), which is exactly how every
such instrument was booked before v3.11; and refused for a future or an option,
whose multiplier and settlement nothing can supply. Until v3.11 those were
booked as cash equities too, wrong by their multiplier (ledger ACC-005).
"""

from __future__ import annotations

from typing import Final

from alphalab.conventions.economics import CASH_EQUITY, InstrumentEconomics, SettlementModel
from alphalab.core.enums import AssetType
from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "CASH_EQUITY",
    "InstrumentEconomics",
    "SettlementModel",
    "economics_for",
]

#: The asset types that *are* cash equities when nothing else is declared.
_FULLY_PAID: Final = frozenset(
    {AssetType.EQUITY, AssetType.CRYPTO, AssetType.FOREX, AssetType.CASH}
)


def economics_for(
    asset_type: AssetType, declared: InstrumentEconomics | None, name: str = "the instrument"
) -> InstrumentEconomics:
    """The economics an instrument is booked by: what it declared, or what its type is.

    Raises:
        InstrumentInputError: If nothing is declared for a future or an option,
            whose multiplier and settlement no default can supply.
    """

    if declared is not None:
        return declared
    if asset_type in _FULLY_PAID:
        return CASH_EQUITY
    raise InstrumentInputError(
        f"{name} is a {asset_type.value}, and declares no economics: its multiplier and "
        "settlement cannot be assumed, so it cannot be booked. Declare InstrumentEconomics "
        "on its InstrumentRecord."
    )
