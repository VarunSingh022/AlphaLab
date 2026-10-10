"""Each currency's minor unit: the ISO 4217 table, and the units a caller declares.

Why this exists
---------------
Until v3.10 every amount in every currency was rounded to ``0.01``.
``alphalab.portfolio.money`` said money was "exact at the currency's minor unit"
while holding one minor unit for all of them, so a yen book carried sen that do
not exist, a Kuwaiti dinar book lost its third decimal (the fils), and a
settlement asset such as USDT -- which has no ISO code at all -- was booked at a
cent because nothing asked what it was.

The rule now
------------
1. **ISO 4217 is the standard.** :data:`ISO_4217_MINOR_UNITS` is the minor-unit
   column of ISO 4217 list one: the codes in circulation, and the codes
   withdrawn recently enough to appear in historical data a research library is
   asked to read. JPY has 0 decimals, KWD 3, CLF 4, USD 2.
2. **Everything else is declared.** A crypto asset, a precious metal (ISO 4217
   lists ``XAU`` with *no* minor unit) or a withdrawn code not in the table has
   the units its caller declares in a :class:`CurrencyUnits`. There is no
   fallback: AlphaLab does not know how many decimals USDT has on a given venue
   and will not guess.
3. **A declaration cannot contradict the standard.** Declaring ``JPY`` with two
   decimals is refused. A book that rounds yen to sen is the defect this module
   removes, not a configuration.
4. **An unknown currency is refused.** Rounding an amount in a currency whose
   minor unit is neither in the table nor declared raises
   :class:`UnknownCurrencyUnitsError`, naming the currency and how to declare
   it.

Rounding is half to even, in :data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`,
and an amount is rounded to its currency's minor unit exactly once, by whoever
produces it (see :mod:`alphalab.portfolio.money`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from types import MappingProxyType
from typing import Final

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "ISO_4217_MINOR_UNITS",
    "ISO_4217_TABLE_NOTE",
    "MAX_MINOR_UNITS",
    "MINOR_UNIT_QUANTA",
    "STANDARD_CURRENCY_UNITS",
    "CurrencyUnits",
    "UnknownCurrencyUnitsError",
]

#: What :data:`ISO_4217_MINOR_UNITS` transcribes, stated so a reader can check it.
ISO_4217_TABLE_NOTE: Final = (
    "ISO 4217 list one (current funds), minor-unit column, as maintained by the "
    "ISO 4217 maintenance agency; transcribed for AlphaLab v3.10.0 including the "
    "codes introduced to 2025 (SLE, VED, ZWG, XCG) and retaining recently withdrawn "
    "codes (ANG, BGN, CUC, HRK, SLL, ZWL) that historical data still carries. "
    "Codes ISO lists with no minor unit (XAU, XAG, XPT, XPD, XDR, XBA-XBD, XSU, "
    "XUA, XTS, XXX) are deliberately absent: their units must be declared."
)

_TWO = 2
_ISO_4217: dict[str, int] = dict.fromkeys(
    [
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "ANG",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BGN",
        "BMD",
        "BND",
        "BOB",
        "BOV",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHE",
        "CHF",
        "CHW",
        "CNY",
        "COP",
        "COU",
        "CRC",
        "CUC",
        "CUP",
        "CVE",
        "CZK",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HRK",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IRR",
        "JMD",
        "KES",
        "KGS",
        "KHR",
        "KPW",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MXV",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLE",
        "SLL",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SVC",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "USD",
        "USN",
        "UYU",
        "UZS",
        "VED",
        "VES",
        "WST",
        "XCD",
        "XCG",
        "YER",
        "ZAR",
        "ZMW",
        "ZWG",
        "ZWL",
    ],
    _TWO,
)
_ISO_4217.update(
    dict.fromkeys(
        [
            "BIF",
            "CLP",
            "DJF",
            "GNF",
            "ISK",
            "JPY",
            "KMF",
            "KRW",
            "PYG",
            "RWF",
            "UGX",
            "UYI",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
        ],
        0,
    )
)
_ISO_4217.update(dict.fromkeys(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"], 3))
_ISO_4217.update(dict.fromkeys(["CLF", "UYW"], 4))

#: Currency code -> number of decimal places of its minor unit. Read-only.
ISO_4217_MINOR_UNITS: Final[Mapping[str, int]] = MappingProxyType(dict(sorted(_ISO_4217.items())))

#: The most decimals a declared currency may carry. 18 is ether's wei; nothing
#: settled on a ledger AlphaLab has met needs more.
MAX_MINOR_UNITS: Final = 18

#: ``Decimal(1).scaleb(-n)`` for every admissible ``n``, built once: the minor unit
#: is read on every money rounding, and a ``Decimal`` is immutable (PRF-006).
MINOR_UNIT_QUANTA: Final = tuple(Decimal(1).scaleb(-n) for n in range(MAX_MINOR_UNITS + 1))

#: A currency code: upper-case letters and digits, optionally with ``.``, ``_``
#: or ``-`` after the first character (``USDT``, ``1INCH``, ``USDC.E``).
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")


class UnknownCurrencyUnitsError(AlphaLabValidationError):
    """An amount was to be rounded in a currency whose minor unit nobody stated."""


def _require_code(currency: object) -> str:
    if not isinstance(currency, str) or not _CODE.match(currency):
        raise AlphaLabValidationError(
            f"{currency!r} is not a currency code: expected upper-case letters and digits "
            "(for example 'USD', 'JPY', 'USDT')."
        )
    return currency


def _require_units(currency: str, units: object) -> int:
    if isinstance(units, bool) or not isinstance(units, int):
        raise AlphaLabValidationError(
            f"The minor units declared for {currency} must be an integer, got {units!r}."
        )
    if not 0 <= units <= MAX_MINOR_UNITS:
        raise AlphaLabValidationError(
            f"The minor units declared for {currency} must be between 0 and "
            f"{MAX_MINOR_UNITS}, got {units}."
        )
    return units


class CurrencyUnits(Mapping[str, int]):
    """The minor units in force: ISO 4217, plus the currencies a caller declares.

    A ``Mapping`` from currency code to decimal places, covering every ISO code
    and every declared one. Immutable and serializable; carried by an
    :class:`~alphalab.portfolio.account.Account` for what it settles and by an
    :class:`~alphalab.portfolio.fx.FxRates` for what it converts into.

    Deliberately not a dataclass: ``dataclasses.asdict`` of a state holding one
    would recurse into its read-only declarations and fail to copy them. It is a
    value all the same -- equal, hashable, picklable and copyable by its
    declarations.

    Args:
        declared: The currencies outside ISO 4217 this value knows, with their
            decimal places. An ISO code may appear only with ISO's own value
            (redundant but harmless); any other value is refused.

    Raises:
        AlphaLabValidationError: If a code is malformed, a unit count is not an
            integer between 0 and :data:`MAX_MINOR_UNITS`, or a declaration
            contradicts ISO 4217.
    """

    __slots__ = ("_declared",)

    _declared: Mapping[str, int]

    def __init__(self, declared: Mapping[str, int] | None = None) -> None:
        checked: dict[str, int] = {}
        for currency, units in (declared or {}).items():
            code = _require_code(currency)
            count = _require_units(code, units)
            standard = ISO_4217_MINOR_UNITS.get(code)
            if standard is not None and standard != count:
                raise AlphaLabValidationError(
                    f"{code} has {standard} decimal places in ISO 4217; declaring {count} "
                    "would book it at a unit the currency does not have."
                )
            checked[code] = count
        object.__setattr__(self, "_declared", MappingProxyType(dict(sorted(checked.items()))))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"CurrencyUnits is immutable; cannot set {name!r}.")

    @property
    def declared(self) -> Mapping[str, int]:
        """The declarations, read-only, sorted by code."""

        return self._declared

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CurrencyUnits):
            return dict(self._declared) == dict(other._declared)
        return NotImplemented

    # -- Mapping ----------------------------------------------------------- #

    def __getitem__(self, currency: str) -> int:
        declared = self.declared.get(currency)
        if declared is not None:
            return declared
        return ISO_4217_MINOR_UNITS[currency]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted({*ISO_4217_MINOR_UNITS, *self.declared}))

    def __len__(self) -> int:
        return len({*ISO_4217_MINOR_UNITS, *self.declared})

    def __contains__(self, currency: object) -> bool:
        return currency in self.declared or currency in ISO_4217_MINOR_UNITS

    # -- Queries ------------------------------------------------------------ #

    def minor_units(self, currency: str) -> int:
        """Decimal places of ``currency``'s minor unit, or raise naming it."""

        declared = self.declared.get(currency)
        if declared is not None:
            return declared
        standard = ISO_4217_MINOR_UNITS.get(currency)
        if standard is not None:
            return standard
        raise UnknownCurrencyUnitsError(
            f"{currency!r} has no minor unit AlphaLab knows: it is not an ISO 4217 code "
            "with one, and it was not declared. Declare it, for example "
            f"CurrencyUnits({{{currency!r}: 8}}), on the Account that settles it (and on "
            "the FxRates that convert into it). No number of decimals is assumed."
        )

    def quantum(self, currency: str) -> Decimal:
        """The minor unit itself: ``Decimal('0.01')`` for USD, ``Decimal('1')`` for JPY."""

        return MINOR_UNIT_QUANTA[self.minor_units(currency)]

    def round(self, amount: Decimal, currency: str) -> Decimal:
        """``amount`` rounded half-even to ``currency``'s minor unit.

        Raises:
            UnknownCurrencyUnitsError: If ``currency`` has no known minor unit.
        """

        return amount.quantize(
            self.quantum(currency), rounding=ROUND_HALF_EVEN, context=ACCOUNTING_CONTEXT
        )

    def is_exact(self, amount: Decimal, currency: str) -> bool:
        """Whether ``amount`` is already a whole number of minor units."""

        return self.round(amount, currency) == amount

    def declaring(self, currency: str, units: int) -> CurrencyUnits:
        """A value that also declares ``currency`` with ``units`` decimals."""

        return CurrencyUnits({**self.declared, currency: units})

    def merged(self, other: CurrencyUnits) -> CurrencyUnits:
        """Both values' declarations; a currency declared twice must agree.

        Raises:
            AlphaLabValidationError: If the two declare one currency differently.
        """

        for currency, units in other.declared.items():
            mine = self.declared.get(currency)
            if mine is not None and mine != units:
                raise AlphaLabValidationError(
                    f"{currency} is declared with {mine} decimal places on one side and "
                    f"{units} on the other. Which is right is not decided by merging."
                )
        return CurrencyUnits({**self.declared, **other.declared})

    def __hash__(self) -> int:
        return hash(tuple(self.declared.items()))

    def __reduce__(self) -> tuple[type[CurrencyUnits], tuple[dict[str, int]]]:
        """Copy, deep-copy and pickle through the constructor.

        The declarations are held in a read-only ``MappingProxyType``, which
        neither ``copy`` nor ``pickle`` can reproduce; rebuilding from a plain
        ``dict`` re-runs the validation and gives an equal value.
        """

        return (CurrencyUnits, (dict(self.declared),))

    def __repr__(self) -> str:
        return f"CurrencyUnits({dict(self.declared)!r})"

    def __serializable__(self) -> dict[str, int]:
        """Serialize the declarations only: the ISO table is the build's, not the payload's."""

        return dict(self.declared)


#: ISO 4217 and nothing declared: what every book uses unless it settles a
#: currency outside the standard.
STANDARD_CURRENCY_UNITS: Final = CurrencyUnits()
