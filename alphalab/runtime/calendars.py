"""The trading calendars a simulated run reads, one per listing venue (ledger EXE-010).

A day order is good until its trading day ends. A broker knows when that is; a
simulation knows only what it was told, and until v3.12 the execution pipeline
was told nothing -- it held no calendar. v3.11 therefore refused a simulated
resting day order that did not state its own close as ``expire_at``, and every
caller computed that close with :meth:`~alphalab.data.calendar.MarketCalendar.next_close`
-- which is the end of the *window* in progress, so a market with a lunch break
expired its day orders at noon.

:class:`VenueCalendars` is the declaration the pipeline reads instead:
``ExecutionPipelineConfig.calendars``. A simulated resting day order that states
no ``expire_at`` is given the last close of its trading day by its venue's
calendar (:meth:`~alphalab.data.calendar.MarketCalendar.day_order_expiry`), and
is refused, naming the venue, only when no calendar is declared for it.

Which venue
-----------
An instrument trades in its **listing** venue's sessions --
``InstrumentRecord.exchange`` -- not in the execution venue a simulated report
names (``ExecutionPipelineConfig.venue``, ``"SIM"``) and not in whatever venue a
feed attributed a quote to; the three are distinct
(``tests/regression/test_venue_concepts_stay_distinct.py``). The listing venue is
read through the run's instrument registry, by
:meth:`~alphalab.instrument.registry.InstrumentRegistry.record_for` -- still the
only method the pipeline calls on it. A run with no registry, or an asset the
registry does not hold, has no listing venue to look up, and reads
:attr:`VenueCalendars.default`.

AlphaLab still ships no holiday: every calendar here is the caller's
(:mod:`alphalab.data.calendar` says why).

Persistence
-----------
A calendar is data, so a pipeline snapshot carries it -- unlike the simulator or
the registry, which are live objects recorded by type name and supplied back.
:meth:`VenueCalendars.__serializable__` is its projection, and
:func:`venue_calendars_from_primitives` reads it back field by field, refusing
what it does not understand.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from types import MappingProxyType
from typing import Any

from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.data.exceptions import DataValidationError
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import normalize_key_field
from alphalab.persistence.decode import as_mapping, as_sequence, as_str, require
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.exceptions import RuntimeValidationError

__all__ = ["VenueCalendars", "venue_calendars_from_primitives"]


@dataclass(frozen=True, slots=True)
class VenueCalendars:
    """Which declared :class:`~alphalab.data.calendar.MarketCalendar` each listing venue trades in.

    Attributes:
        by_exchange: Listing venue -- an ``InstrumentRecord.exchange``,
            conventionally a MIC such as ``"XNYS"`` -- to its calendar. Keys are
            normalized as the record normalizes ``exchange`` (stripped,
            uppercased), so ``"xnys"`` names the same venue; two keys naming one
            venue are refused rather than one silently winning.
        default: The calendar of an instrument whose listing venue is not in
            ``by_exchange``, or is not known -- the run has no instrument
            registry, or the registry does not hold the asset. ``None`` (the
            default) gives such an instrument no calendar, and a simulated day
            order in it is refused rather than given a session somebody else
            declared. A single-market run needs only this.

    Raises:
        RuntimeValidationError: If a key is not a valid exchange, two keys name
            one venue, or a value is not a ``MarketCalendar``.
    """

    by_exchange: Mapping[str, MarketCalendar] = field(default_factory=dict)
    default: MarketCalendar | None = None

    def __post_init__(self) -> None:
        normalized: dict[str, MarketCalendar] = {}
        for exchange, calendar in self.by_exchange.items():
            try:
                key = normalize_key_field(exchange, "exchange")
            except InstrumentInputError as exc:
                raise RuntimeValidationError(f"A venue calendar's key is invalid: {exc}") from exc
            if key in normalized:
                raise RuntimeValidationError(
                    f"Two venue calendars are declared for {key}; one venue has one calendar."
                )
            if not isinstance(calendar, MarketCalendar):
                raise RuntimeValidationError(
                    f"The calendar declared for {key} is a {type(calendar).__name__}, "
                    "not a MarketCalendar."
                )
            normalized[key] = calendar
        if self.default is not None and not isinstance(self.default, MarketCalendar):
            raise RuntimeValidationError(
                f"The default calendar is a {type(self.default).__name__}, not a MarketCalendar."
            )
        object.__setattr__(self, "by_exchange", MappingProxyType(dict(sorted(normalized.items()))))

    @property
    def declared(self) -> bool:
        """Whether any calendar is declared at all."""

        return self.default is not None or bool(self.by_exchange)

    def calendar_for(self, exchange: str | None) -> MarketCalendar | None:
        """The calendar an instrument listed on ``exchange`` trades in.

        ``exchange`` is ``None`` when the listing venue is not known. The
        venue's own calendar when one is declared, else :attr:`default`.
        """

        if exchange is not None:
            calendar = self.by_exchange.get(exchange)
            if calendar is not None:
                return calendar
        return self.default

    def __serializable__(self) -> dict[str, Any]:
        return {
            "by_exchange": {
                exchange: _calendar_payload(calendar)
                for exchange, calendar in self.by_exchange.items()
            },
            "default": None if self.default is None else _calendar_payload(self.default),
        }


# --------------------------------------------------------------------------- #
# The projection, and its inverse
# --------------------------------------------------------------------------- #


def _windows_payload(windows: tuple[SessionWindow, ...]) -> list[list[str]]:
    return [[window.opens.isoformat(), window.closes.isoformat()] for window in windows]


def _calendar_payload(calendar: MarketCalendar) -> dict[str, Any]:
    """One calendar as JSON: weekdays and dates as text keys, times as ISO text, sorted."""

    return {
        "calendar_id": calendar.calendar_id,
        "timezone_name": calendar.timezone_name,
        "weekly_sessions": {
            str(weekday): _windows_payload(tuple(windows))
            for weekday, windows in sorted(calendar.weekly_sessions.items())
        },
        "holidays": [day.isoformat() for day in sorted(calendar.holidays)],
        "special_sessions": {
            day.isoformat(): _windows_payload(tuple(windows))
            for day, windows in sorted(calendar.special_sessions.items())
        },
    }


def _time(value: Any, where: str) -> time:
    text = as_str(value, where)
    try:
        return time.fromisoformat(text)
    except ValueError as exc:
        raise StateDecodeError(f"{where} is not an ISO time: {text!r}.") from exc


def _date(value: Any, where: str) -> date:
    text = as_str(value, where)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise StateDecodeError(f"{where} is not an ISO date: {text!r}.") from exc


def _windows(value: Any, where: str) -> tuple[SessionWindow, ...]:
    windows: list[SessionWindow] = []
    for index, entry in enumerate(as_sequence(value, where)):
        bounds = as_sequence(entry, f"{where}[{index}]")
        if len(bounds) != 2:
            raise StateDecodeError(f"{where}[{index}] must be [opens, closes].")
        windows.append(
            SessionWindow(
                _time(bounds[0], f"{where}[{index}][0]"), _time(bounds[1], f"{where}[{index}][1]")
            )
        )
    return tuple(windows)


def _weekday(text: str, where: str) -> int:
    if text not in {"0", "1", "2", "3", "4", "5", "6"}:
        raise StateDecodeError(f"{where} has weekday {text!r}; weekdays are 0 through 6.")
    return int(text)


def _calendar(value: Any, where: str) -> MarketCalendar:
    payload = as_mapping(value, where)
    weekly = as_mapping(require(payload, "weekly_sessions"), f"{where}.weekly_sessions")
    special = as_mapping(require(payload, "special_sessions"), f"{where}.special_sessions")
    try:
        return MarketCalendar(
            calendar_id=as_str(require(payload, "calendar_id"), f"{where}.calendar_id"),
            timezone_name=as_str(require(payload, "timezone_name"), f"{where}.timezone_name"),
            weekly_sessions={
                _weekday(key, f"{where}.weekly_sessions"): _windows(
                    windows, f"{where}.weekly_sessions.{key}"
                )
                for key, windows in weekly.items()
            },
            holidays=frozenset(
                _date(entry, f"{where}.holidays[{index}]")
                for index, entry in enumerate(
                    as_sequence(require(payload, "holidays"), f"{where}.holidays")
                )
            ),
            special_sessions={
                _date(key, f"{where}.special_sessions"): _windows(
                    windows, f"{where}.special_sessions.{key}"
                )
                for key, windows in special.items()
            },
        )
    except DataValidationError as exc:
        raise StateDecodeError(f"{where} is not a valid calendar: {exc}") from exc


def venue_calendars_from_primitives(value: Any, where: str) -> VenueCalendars:
    """Read :meth:`VenueCalendars.__serializable__`'s projection back.

    Raises:
        StateDecodeError: If a field is missing or malformed, or the calendars
            it describes could not be declared.
    """

    payload = as_mapping(value, where)
    by_exchange = as_mapping(require(payload, "by_exchange"), f"{where}.by_exchange")
    default = require(payload, "default")
    try:
        return VenueCalendars(
            by_exchange={
                exchange: _calendar(calendar, f"{where}.by_exchange.{exchange}")
                for exchange, calendar in by_exchange.items()
            },
            default=None if default is None else _calendar(default, f"{where}.default"),
        )
    except RuntimeValidationError as exc:
        raise StateDecodeError(f"{where} could not be declared: {exc}") from exc
