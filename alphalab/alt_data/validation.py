"""The refusals every v3.7 alt-data record shares, spelled once.

Each record type validates its own shape, and several shapes need the same
three judgements: is this an identifier, is this a label, is this a number that
means something. Spelling them once is what keeps "``Revenue``" from being
refused by one record type and accepted by the next.

Identifiers are dotted lowercase (``earnings.release``, ``eps_diluted``)
because they are *vocabulary*: a category, a field, an event type. Two
spellings of one word would be two categories, silently, so the grammar is
narrow. Labels are what a caller names things by -- a symbol, an issuer, a
period label -- and are free text with no surrounding whitespace and no line
break, because ``"AAPL "`` and ``"AAPL"`` would otherwise be two subjects and a
line break would let a label impersonate a line of a canonical key.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Final

from alphalab.alt_data.exceptions import AltDataInputError

__all__ = [
    "IDENTIFIER_PATTERN",
    "require_finite_instant",
    "require_finite_value",
    "require_identifier",
    "require_label",
    "require_revision",
]

#: Dotted lowercase words: a letter first, then letters, digits and underscores.
IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def require_identifier(value: str, what: str) -> None:
    """Refuse anything that is not a dotted lowercase identifier."""

    if not isinstance(value, str) or not IDENTIFIER_PATTERN.match(value):
        raise AltDataInputError(
            f"{what} is {value!r}; expected a dotted lowercase identifier such as "
            "'earnings.release' or 'eps_diluted'. Vocabulary with two spellings becomes "
            "two categories without anyone deciding it should."
        )


def require_label(value: str, what: str) -> None:
    """Refuse a blank label, surrounding whitespace, or a line break."""

    if not isinstance(value, str) or not value.strip():
        raise AltDataInputError(f"{what} cannot be empty.")
    if value != value.strip():
        raise AltDataInputError(
            f"{what} {value!r} has surrounding whitespace, which would make it a different "
            "label from the same text without it."
        )
    if "\n" in value or "\r" in value:
        raise AltDataInputError(f"{what} {value!r} contains a line break.")


def require_finite_value(value: Decimal, what: str) -> None:
    """Refuse a value that is not a finite ``Decimal``.

    ``Decimal`` because every alt-data value in AlphaLab has been one since v1,
    and because a reported figure is an exact decimal quantity rather than the
    nearest binary fraction to one.
    """

    if not isinstance(value, Decimal):
        raise AltDataInputError(
            f"{what} is {value!r} ({type(value).__name__}); a reported value is a Decimal."
        )
    if not value.is_finite():
        raise AltDataInputError(f"{what} is {value!r}; a reported value must be finite.")


def require_finite_instant(value: float, what: str) -> None:
    """Refuse an instant that is not a finite number of seconds."""

    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise AltDataInputError(f"{what} is {value!r}; an instant is a finite number of seconds.")


def require_revision(value: int, what: str) -> None:
    """Refuse a revision number that is not a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AltDataInputError(
            f"{what} is {value!r}; a revision is 0 for the value as first published and "
            "counts up from there."
        )
