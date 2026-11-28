"""Lot relief: which acquisitions a disposal is matched against, by a stated method (v4.3, GAP-50).

The book relieves a position's basis proportionally -- average cost -- as it
always has, and its realized P&L is that. A jurisdiction may match a disposal
against particular acquisitions instead. The gains it realizes then differ,
though never the total of what was realized and what is still held. This
module reads them from the book's own records, by the method the caller states
(AC-12): none is assumed.

* ``AVERAGE_COST``: the proportional slice of the basis, exactly as the book
  relieves it, to the minor unit.
* ``FIFO``: the earliest acquisitions first.
* ``LIFO``: the latest acquisitions first.
* ``HIGHEST_COST`` and ``LOWEST_COST``: specific identification by a standing
  rule -- the dearest, or the cheapest, units first.
* ``SECTION_104``: the United Kingdom's share identification (TCGA 1992 ss.
  104-106A). A disposal is matched first with acquisitions on the same day,
  then with acquisitions in the 30 days after it, earliest first, and then
  with the section 104 pool, at the pool's average cost.

Section 104 matches a disposal against acquisitions made after it, so its gains
are a reading of the whole record, not a figure any instant could book: that is
why lot relief is read here rather than booked. A disposal within 30 days of
the record's last day may yet be matched with a later acquisition, and says so
(``provisional``).

What is read, and what is refused
---------------------------------
The asset's fills -- every ``BUY`` and ``SELL`` the ledger holds for it, an
exercise's, an assignment's and an expiry's included -- and its splits, from the
portfolio's event log, in the order they happened. A gain is proceeds less the
cost relieved, before commission, as the book's realized P&L is.

Refused, with the reason: a ledger or event log a retention policy trimmed,
since lots cannot be relieved from part of a history; an instrument whose gains
settle as cash at every mark (a future), which holds no lots; a holding
exchange of the asset or into it, whose basis is apportioned by declared shares
that lots do not follow here; and under ``SECTION_104``, a short, a split, and
a call that does not state how an instant maps to its trade day.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum, auto

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context
from alphalab.conventions.economics import InstrumentEconomics
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.events import (
    DeliveryAmended,
    PositionClosed,
    PositionExchanged,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
    PositionSplit,
)
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.money import ZERO_MONEY, to_money
from alphalab.portfolio.position import Position
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType

__all__ = ["Lot", "LotMatch", "LotRelief", "LotReport", "relieve_lots"]

_ZERO = Decimal("0")
_WINDOW_DAYS = 30


class LotRelief(Enum):
    """Which acquisitions a disposal is matched against (GAP-50)."""

    #: The proportional slice of the basis: what the book relieves.
    AVERAGE_COST = auto()
    #: The earliest acquisitions first.
    FIFO = auto()
    #: The latest acquisitions first.
    LIFO = auto()
    #: Specific identification by a standing rule: the dearest units first.
    HIGHEST_COST = auto()
    #: Specific identification by a standing rule: the cheapest units first.
    LOWEST_COST = auto()
    #: The United Kingdom's: same day, then the next 30 days, then the pool.
    SECTION_104 = auto()


@dataclass(frozen=True, slots=True)
class Lot:
    """Units still held, and what they cost.

    Attributes:
        acquisitions: The fills they were acquired by: one, for a lot; every
            fill that entered it, for a pool.
        acquired_at: When the lot was acquired; ``None`` for a pool.
        quantity: Units held, signed: a long's positive, a short's negative.
        cost: Their basis: paid for a long, received for a short.
    """

    acquisitions: tuple[str, ...]
    acquired_at: float | None
    quantity: Decimal
    cost: Decimal


@dataclass(frozen=True, slots=True)
class LotMatch:
    """Units of one disposal matched against one acquisition, a day's, or a pool.

    Attributes:
        disposal: The disposing fill's ledger transaction.
        disposed_at: When it was made.
        acquisitions: What it was matched against: one fill, a day's fills, or
            the pool's.
        rule: How: ``"average cost"``, ``"lot"``, ``"same day"``,
            ``"30 days"`` or ``"pool"``.
        quantity: Units matched, signed as the units held: positive for a long.
        proceeds: The money the disposal moved for them: received for a long,
            paid for a short.
        cost: The basis relieved for them.
        gain: What they realized: proceeds less cost for a long, cost less
            proceeds for a short.
        provisional: Whether a later acquisition could still be matched with
            it (``SECTION_104``, a disposal within 30 days of the last day read).
    """

    disposal: str
    disposed_at: float
    acquisitions: tuple[str, ...]
    rule: str
    quantity: Decimal
    proceeds: Decimal
    cost: Decimal
    gain: Decimal
    provisional: bool = False


@dataclass(frozen=True, slots=True)
class LotReport:
    """An asset's gains and what is still held, by one method of lot relief.

    Attributes:
        asset_id: The asset.
        currency: What every money figure is in.
        method: How each disposal was matched.
        matches: Every disposal's matches, in the order made.
        open_lots: What is still held, each lot with its cost.
    """

    asset_id: str
    currency: str
    method: LotRelief
    matches: tuple[LotMatch, ...]
    open_lots: tuple[Lot, ...]

    @property
    def realized(self) -> Decimal:
        """Every match's gain, summed."""

        return sum((match.gain for match in self.matches), ZERO_MONEY)

    @property
    def open_quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.open_lots), _ZERO)

    @property
    def open_cost(self) -> Decimal:
        return sum((lot.cost for lot in self.open_lots), ZERO_MONEY)


# --------------------------------------------------------------------------- #
# What is read
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Fill:
    transaction: Transaction
    #: What the fill moved, money: ``|quantity| x price x multiplier``, rounded once.
    total: Decimal


@dataclass(frozen=True, slots=True)
class _Split:
    ratio: Decimal
    at: float


def _history(
    state: PortfolioState, asset_id: str, economics: InstrumentEconomics | None
) -> tuple[list[_Fill | _Split], str, int]:
    """The asset's fills and splits in the order they happened, its currency and minor unit."""

    ledger, events = state.ledger.transactions, state.events
    if ledger.dropped or events.dropped:
        raise InvalidTransactionError(
            f"The ledger or event log of {state.account.account_id} was trimmed by a retention "
            "policy: lots cannot be relieved from part of a history."
        )
    if economics is not None and not economics.settlement.pays_notional:
        raise InvalidTransactionError(
            f"{asset_id} settles its gains as cash at every mark: it holds no lots to relieve."
        )
    fills = iter(
        t
        for t in ledger
        if t.asset_id == asset_id and t.type in (TransactionType.BUY, TransactionType.SELL)
    )
    multiplier = Decimal("1") if economics is None else economics.multiplier
    out: list[_Fill | _Split] = []
    currency: str | None = None
    for event in events:
        if isinstance(event, PositionExchanged | DeliveryAmended) and (
            event.asset_id == asset_id
            or (isinstance(event, PositionExchanged) and asset_id in event.received)
        ):
            raise InvalidTransactionError(
                f"{asset_id} took part in a holding exchange, whose basis is apportioned by "
                "declared shares that lots do not follow here."
            )
        if isinstance(event, PositionSplit) and event.asset_id == asset_id:
            out.append(_Split(event.ratio, event.timestamp))
            continue
        if not (
            isinstance(event, PositionOpened | PositionIncreased | PositionReduced | PositionClosed)
            and event.asset_id == asset_id
        ):
            continue
        transaction = next(fills)
        currency = transaction.currency
        units = state.account.currency_units
        notional = ACCOUNTING_CONTEXT.multiply(abs(transaction.quantity), transaction.price)
        scaled = notional if multiplier == 1 else ACCOUNTING_CONTEXT.multiply(notional, multiplier)
        out.append(_Fill(transaction, to_money(scaled, currency, units)))
    if currency is None:
        raise InvalidTransactionError(f"The book holds no fill of {asset_id} to relieve.")
    return out, currency, state.account.currency_units.minor_units(currency)


def _apportioned(
    total: Decimal, parts: list[Decimal], currency: str, state: PortfolioState
) -> Iterator[Decimal]:
    """``total`` split in proportion to ``parts``, each rounded once and the last by subtraction."""

    whole = sum(parts, _ZERO)
    given = ZERO_MONEY
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            yield total - given
            return
        share = to_money(
            ACCOUNTING_CONTEXT.divide(ACCOUNTING_CONTEXT.multiply(total, part), whole),
            currency,
            state.account.currency_units,
        )
        given += share
        yield share


# --------------------------------------------------------------------------- #
# Average cost: the book's own relief
# --------------------------------------------------------------------------- #


def _average_cost(
    history: list[_Fill | _Split],
    asset_id: str,
    currency: str,
    minor_units: int,
    economics: InstrumentEconomics | None,
) -> tuple[list[LotMatch], tuple[Lot, ...]]:
    """Replayed through :class:`Position` itself: its figures are the book's, to the minor unit."""

    declared = None if economics is None or economics.is_cash_equity else economics
    position = Position(
        asset_id, _ZERO, _ZERO, _ZERO, ZERO_MONEY, currency, 0.0, minor_units=minor_units,
        economics=declared,
    )  # fmt: skip
    entered: list[str] = []
    matches: list[LotMatch] = []
    for item in history:
        if isinstance(item, _Split):
            position = position.split(item.ratio, item.at)
            continue
        t = item.transaction
        before = position
        position, gain = position.apply_fill(t.quantity, t.price, t.timestamp)
        held = before.quantity
        if held == 0 or (held > 0) == (t.quantity > 0):
            entered = [*([] if held == 0 else entered), t.transaction_id]
            continue
        matched = min(abs(t.quantity), abs(held))
        cost = (
            before.basis
            if (position.quantity == 0 or (position.quantity > 0) != (held > 0))
            else before.basis - position.basis
        )
        proceeds = cost + gain if held > 0 else cost - gain
        sign = Decimal("1") if held > 0 else Decimal("-1")
        matches.append(
            LotMatch(
                t.transaction_id,
                t.timestamp,
                tuple(entered),
                "average cost",
                sign * matched,
                proceeds,
                cost,
                gain,
            )
        )
        if position.quantity != 0 and (position.quantity > 0) != (held > 0):
            entered = [t.transaction_id]  # reversed: the new side entered by this fill
        elif position.quantity == 0:
            entered = []
    lots = (
        ()
        if position.quantity == 0
        else (Lot(tuple(entered), None, position.quantity, position.basis),)
    )
    return matches, lots


# --------------------------------------------------------------------------- #
# Lots relieved in an order: FIFO, LIFO, highest and lowest cost
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Open:
    acquisition: str
    acquired_at: float
    quantity: Decimal  # magnitude
    cost: Decimal
    order: int


def _ordered(lots: list[_Open], method: LotRelief) -> list[_Open]:
    if method is LotRelief.FIFO:
        return sorted(lots, key=lambda lot: lot.order)
    if method is LotRelief.LIFO:
        return sorted(lots, key=lambda lot: -lot.order)
    per_unit = [(ACCOUNTING_CONTEXT.divide(lot.cost, lot.quantity), lot.order, lot) for lot in lots]
    dearest = method is LotRelief.HIGHEST_COST
    per_unit.sort(key=lambda item: ((-item[0] if dearest else item[0]), item[1]))
    return [lot for _, _, lot in per_unit]


def _by_lot(
    history: list[_Fill | _Split],
    method: LotRelief,
    currency: str,
    state: PortfolioState,
    economics: InstrumentEconomics | None,
) -> tuple[list[LotMatch], tuple[Lot, ...]]:
    units = state.account.currency_units
    multiplier = Decimal("1") if economics is None else economics.multiplier
    lots: list[_Open] = []
    side = 0
    order = 0
    matches: list[LotMatch] = []
    for item in history:
        if isinstance(item, _Split):
            for lot in lots:
                lot.quantity *= item.ratio
            continue
        t, total = item.transaction, item.total
        direction = 1 if t.quantity > 0 else -1
        size = abs(t.quantity)
        if side in (0, direction):
            lots.append(_Open(t.transaction_id, t.timestamp, size, total, order))
            order += 1
            side = direction
            continue
        held = sum((lot.quantity for lot in lots), _ZERO)
        matched = min(size, held)
        if size <= held:
            closing = total
        else:
            notional = ACCOUNTING_CONTEXT.multiply(held, t.price)
            closing = to_money(
                notional if multiplier == 1 else ACCOUNTING_CONTEXT.multiply(notional, multiplier),
                currency,
                units,
            )
        taken: list[tuple[_Open, Decimal, Decimal]] = []
        remaining = matched
        for lot in _ordered(lots, method):
            if remaining == 0:
                break
            take = min(remaining, lot.quantity)
            part = (
                lot.cost
                if take == lot.quantity
                else to_money(
                    ACCOUNTING_CONTEXT.divide(
                        ACCOUNTING_CONTEXT.multiply(lot.cost, take), lot.quantity
                    ),
                    currency,
                    units,
                )
            )
            taken.append((lot, take, part))
            remaining -= take
        shares = list(_apportioned(closing, [take for _, take, _ in taken], currency, state))
        for (lot, take, part), proceeds in zip(taken, shares, strict=True):
            gain = proceeds - part if side > 0 else part - proceeds
            matches.append(
                LotMatch(
                    t.transaction_id,
                    t.timestamp,
                    (lot.acquisition,),
                    "lot",
                    side * take,
                    proceeds,
                    part,
                    gain,
                )
            )
            lot.quantity -= take
            lot.cost -= part
        lots = [lot for lot in lots if lot.quantity != 0]
        if size > held:
            lots = [_Open(t.transaction_id, t.timestamp, size - held, total - closing, order)]
            order += 1
            side = direction
        elif not lots:
            side = 0
    open_lots = tuple(
        Lot((lot.acquisition,), lot.acquired_at, side * lot.quantity, lot.cost)
        for lot in sorted(lots, key=lambda lot: lot.order)
    )
    return matches, open_lots


# --------------------------------------------------------------------------- #
# Section 104
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Day:
    """One trade day's acquisitions, as one (TCGA 1992 s. 105), and what is left of them."""

    acquisitions: list[str] = field(default_factory=list)
    quantity: Decimal = _ZERO
    cost: Decimal = ZERO_MONEY


@dataclass(slots=True)
class _Disposal:
    fill: _Fill
    day: date
    remaining: Decimal
    parts: list[tuple[tuple[str, ...], str, Decimal, Decimal]] = field(default_factory=list)


def _take(day: _Day, quantity: Decimal, currency: str, state: PortfolioState) -> Decimal:
    """``quantity`` of a day's acquisitions, and the cost it relieves, pro rata."""

    if quantity == day.quantity:
        cost = day.cost
    else:
        cost = to_money(
            ACCOUNTING_CONTEXT.divide(
                ACCOUNTING_CONTEXT.multiply(day.cost, quantity), day.quantity
            ),
            currency,
            state.account.currency_units,
        )
    day.quantity -= quantity
    day.cost -= cost
    return cost


def _section_104(
    history: list[_Fill | _Split],
    currency: str,
    state: PortfolioState,
    day_of: Callable[[float], date],
) -> tuple[list[LotMatch], tuple[Lot, ...]]:
    days: dict[date, _Day] = {}
    disposals: list[_Disposal] = []
    held = _ZERO
    for item in history:
        if isinstance(item, _Split):
            raise InvalidTransactionError(
                "Section 104 is read here for a holding no split changed: a split of it is "
                "refused rather than restated."
            )
        t = item.transaction
        held += t.quantity
        if held < 0:
            raise InvalidTransactionError(
                f"Section 104 identifies disposals of shares held; fill {t.transaction_id} sells "
                "short, which it does not identify."
            )
        day = day_of(t.timestamp)
        if t.quantity > 0:
            entry = days.setdefault(day, _Day())
            entry.acquisitions.append(t.transaction_id)
            entry.quantity += t.quantity
            entry.cost += item.total
        else:
            disposals.append(_Disposal(item, day, -t.quantity))
    # The same day: a day's disposals with its acquisitions, in the order made.
    for disposal in disposals:
        same = days.get(disposal.day)
        if same is None or same.quantity == 0 or disposal.remaining == 0:
            continue
        take = min(disposal.remaining, same.quantity)
        cost = _take(same, take, currency, state)
        disposal.parts.append((tuple(same.acquisitions), "same day", take, cost))
        disposal.remaining -= take
    # The next 30 days, earliest first, in the order the disposals were made.
    for disposal in disposals:
        for offset in range(1, _WINDOW_DAYS + 1):
            if disposal.remaining == 0:
                break
            later = days.get(disposal.day + timedelta(days=offset))
            if later is None or later.quantity == 0:
                continue
            take = min(disposal.remaining, later.quantity)
            cost = _take(later, take, currency, state)
            disposal.parts.append((tuple(later.acquisitions), "30 days", take, cost))
            disposal.remaining -= take
    # The pool, day by day: a day's disposals from the pool as it stood before
    # the day, then what is left of the day's acquisitions joins it.
    pool = _Day()
    by_day: dict[date, list[_Disposal]] = {}
    for disposal in disposals:
        by_day.setdefault(disposal.day, []).append(disposal)
    for day in sorted({*days, *by_day}):
        for disposal in by_day.get(day, ()):
            if disposal.remaining == 0:
                continue
            if disposal.remaining > pool.quantity:
                raise InvalidTransactionError(
                    f"Fill {disposal.fill.transaction.transaction_id} disposes of more than the "
                    "pool holds after its own day's and the next 30 days' acquisitions."
                )
            cost = _take(pool, disposal.remaining, currency, state)
            disposal.parts.append((tuple(pool.acquisitions), "pool", disposal.remaining, cost))
            disposal.remaining = _ZERO
        left = days.get(day)
        if left is not None and left.quantity:
            pool.acquisitions.extend(left.acquisitions)
            pool.quantity += left.quantity
            pool.cost += left.cost
    last = max(day_of(item.transaction.timestamp) for item in history if isinstance(item, _Fill))
    matches: list[LotMatch] = []
    for disposal in disposals:
        t, total = disposal.fill.transaction, disposal.fill.total
        provisional = (last - disposal.day).days < _WINDOW_DAYS
        shares = _apportioned(total, [part[2] for part in disposal.parts], currency, state)
        for (acquisitions, rule, quantity, cost), proceeds in zip(
            disposal.parts, shares, strict=True
        ):
            matches.append(
                LotMatch(
                    t.transaction_id,
                    t.timestamp,
                    acquisitions,
                    rule,
                    quantity,
                    proceeds,
                    cost,
                    proceeds - cost,
                    provisional,
                )
            )
    open_lots = (
        ()
        if pool.quantity == 0
        else (Lot(tuple(pool.acquisitions), None, pool.quantity, pool.cost),)
    )
    return matches, open_lots


# --------------------------------------------------------------------------- #
# The reading
# --------------------------------------------------------------------------- #


@in_accounting_context
def relieve_lots(
    state: PortfolioState,
    asset_id: str,
    method: LotRelief,
    *,
    economics: InstrumentEconomics | None = None,
    day_of: Callable[[float], date] | None = None,
) -> LotReport:
    """``asset_id``'s gains and open lots in ``state``, every disposal matched by ``method``.

    Args:
        state: The book, its ledger and event log whole.
        asset_id: The asset.
        method: How each disposal is matched; nothing is assumed.
        economics: What one unit is worth, as its record declares: its multiplier
            scales every money figure. ``None`` is a fully paid unit of one.
        day_of: The trade day an instant falls on -- the listing venue's local
            date, for a venue that states one. Required by ``SECTION_104``,
            whose rules are by day, and read by nothing else.

    Raises:
        InvalidTransactionError: If the record is trimmed, the asset settles as
            cash at every mark, it took part in a holding exchange, or the book
            holds no fill of it; or, under ``SECTION_104``, ``day_of`` is not
            given, a fill sells short, or a split changed the holding.
    """

    if not isinstance(method, LotRelief):
        raise InvalidTransactionError(f"{method!r} is not a LotRelief.")
    history, currency, minor_units = _history(state, asset_id, economics)
    if method is LotRelief.AVERAGE_COST:
        matches, lots = _average_cost(history, asset_id, currency, minor_units, economics)
    elif method is LotRelief.SECTION_104:
        if day_of is None:
            raise InvalidTransactionError(
                "Section 104 matches by day: state the trade day an instant falls on (day_of)."
            )
        matches, lots = _section_104(history, currency, state, day_of)
    else:
        matches, lots = _by_lot(history, method, currency, state, economics)
    return LotReport(asset_id, currency, method, tuple(matches), lots)
