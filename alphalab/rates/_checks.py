"""The checks every rates value makes of what it is given (v4.3, ADR-0065)."""

from __future__ import annotations

import math
from datetime import date, datetime

from alphalab.conventions.daycount import DayCount, DayCountTerms, required_terms, year_fraction
from alphalab.conventions.exceptions import ConventionInputError
from alphalab.rates.exceptions import RatesInputError

__all__ = [
    "accrual",
    "period_fraction",
    "require_basis",
    "require_currency",
    "require_day",
    "require_finite",
    "require_line",
]


def require_day(value: object, what: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise RatesInputError(f"{what} must be a date, not a time, got {value!r}.")
    return value


def require_finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise RatesInputError(f"{what} is {value!r}; it must be a finite number.")
    return float(value)


def require_line(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\n\r"):
        raise RatesInputError(f"{what} must be a non-empty string on one line, got {value!r}.")
    return value


def require_currency(value: object, what: str) -> str:
    if not isinstance(value, str) or len(value) != 3 or not value.isalpha() or not value.isupper():
        raise RatesInputError(f"{what} is an ISO 4217 code such as 'USD', got {value!r}.")
    return value


def require_basis(value: object, what: str, *, terms_free: bool) -> DayCount:
    """A :class:`DayCount`; under ``terms_free``, one that needs nothing from an instrument."""

    if not isinstance(value, DayCount):
        raise RatesInputError(f"{what} must be a DayCount, got {value!r}.")
    if terms_free and required_terms(value):
        raise RatesInputError(
            f"{what} is {value.name}, which needs an instrument's "
            f"{', '.join(sorted(required_terms(value)))}; this one states none."
        )
    return value


def accrual(start: date, end: date, basis: DayCount, terms: DayCountTerms | None = None) -> float:
    """The year fraction of ``[start, end]`` under ``basis``, refused when it is not positive."""

    try:
        tau = year_fraction(start, end, basis, terms=terms)
    except ConventionInputError as exc:
        raise RatesInputError(str(exc)) from exc
    if tau <= 0.0:
        raise RatesInputError(
            f"{start} to {end} is {tau!r} years under {basis.name}: a period that accrues "
            "nothing has no rate."
        )
    return tau


def period_fraction(
    start: date, end: date, basis: DayCount, *, frequency: int | None, termination: date
) -> float:
    """A regular coupon period's year fraction, with the terms its basis needs from the leg.

    ACT/ACT (ICMA) and ACT/365L read the period and its frequency, and 30E/360
    (ISDA) the schedule's termination date: each is the leg's own, stated by its
    schedule. BUS/252 needs a business calendar no leg here states, and is
    refused.
    """

    needed = required_terms(basis)
    if not needed:
        return accrual(start, end, basis)
    if needed == frozenset({"termination"}):
        return accrual(start, end, basis, DayCountTerms(termination=termination))
    if needed == frozenset({"period_start", "period_end", "frequency"}):
        if frequency is None:
            raise RatesInputError(
                f"{basis.name} reads a coupon frequency, and a step that does not divide a year "
                "into whole periods has none."
            )
        return accrual(
            start,
            end,
            basis,
            DayCountTerms(period_start=start, period_end=end, frequency=frequency),
        )
    raise RatesInputError(
        f"{basis.name} needs {', '.join(sorted(needed))}, which a leg's schedule does not state."
    )
