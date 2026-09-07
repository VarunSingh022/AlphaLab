"""Read-only views the pipeline hands a strategy through its context.

`StrategyContext` has existed since v0.10.0 with nine fields, six of whose
supporting protocols declare no methods at all, and every construction site in
the repository passed ``object()`` for them. A strategy could observe the event
handed to its hook and nothing else -- not its position, not its cash, not its
own orders, not the price it was about to be marked at. ADR-0026 settles what it
sees and who assembles it; this module is the "what".

Every view here is a **reference** to state the pipeline already holds. Nothing
is copied, nothing is recomputed, and no view owns a fact: if
:class:`~alphalab.portfolio.engine.PortfolioState` says a position is 15 units,
:class:`PortfolioView` cannot say otherwise, because it *is* that state. That is
ADR-0026 decision 1, and it is what stops a context becoming a second source of
truth.

Immutability, and why a frozen dataclass is not enough
------------------------------------------------------
The states these views wrap are immutable at the top level and are **not**
immutable all the way down: ``PortfolioState.positions``, ``CashLedger.balances``
and ``CashLedger.reserved`` are typed ``Mapping`` and are plain ``dict`` at
runtime, as is ``ExecutionPipelineState.market_prices``. Handing one out
directly would give a strategy a live, writable handle on pipeline state through
a field documented as read-only.

Every mapping this module returns is therefore wrapped in
:class:`types.MappingProxyType`, which is O(1) and copies nothing, and every
sequence is a ``tuple``. ``MarketState.latest_*`` and
``AllocationState.contributions`` are already :class:`PersistentMap` and need no
wrapping. See ADR-0026 decision 5.

Order attribution, and the index that looks right and is not
------------------------------------------------------------
:meth:`~alphalab.oms.book.OrderBook.orders_for_strategy` exists, is public, and
answers ``()`` for every order the execution path produces. ADR-0015 decision 4
settled that a netted order declares no owning strategy -- ``MOMENTUM`` wanting
``+60`` and ``MEANREV`` wanting ``+40`` produce one ``BUY 100``, and a
single-valued index cannot describe it. Attribution lives in
``AllocationState.contributions``, keyed by order id, and that is what
:func:`order_shares_by_strategy` reads.

Two consequences, both stated rather than worked around:

* A strategy sees its **share** of an order, never sole ownership. An order
  carrying two contributions appears in both strategies' views, with different
  quantities and no claim of exclusivity.
* The view covers **live** orders only. ADR-0021 retires a contribution when its
  order reaches a terminal state, so a filled or cancelled order leaves the view
  and its history is not available here.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

from alphalab.allocation.state import AllocationState
from alphalab.market.bar import Bar
from alphalab.market.quote import Quote
from alphalab.market.state import MarketState
from alphalab.market.tick import Tick
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.state import OMSState
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.position import Position
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.state import RiskState

__all__ = [
    "MarketView",
    "OrderShare",
    "OrderView",
    "PortfolioView",
    "RiskView",
    "order_shares_by_strategy",
]

_EMPTY_DECIMALS: Mapping[str, Decimal] = MappingProxyType({})
_NO_SHARES: Mapping[str, tuple[OrderShare, ...]] = MappingProxyType({})


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioView:
    """The marked portfolio, as of the event being dispatched.

    Wraps the ``PortfolioState`` produced by
    :meth:`~alphalab.portfolio.engine.PortfolioEngine.update_market_prices` for
    *this* event, so a position's ``market_price`` is the price the run is
    marking at now and not the previous event's. See ADR-0026 decision 3.

    Unrealized P&L and equity are deliberately absent: they are derived by
    :class:`~alphalab.portfolio.valuation.PortfolioValuation`, which refuses a
    book it cannot express in one currency (ADR-0020), and a view that computed
    them itself would be the second source of truth decision 1 forbids.
    """

    _state: PortfolioState

    @property
    def account(self) -> Account:
        return self._state.account

    @property
    def positions(self) -> Mapping[str, Position]:
        """Marked positions by ``asset_id``. Read-only; assignment raises."""

        return MappingProxyType(dict(self._state.positions))

    def position(self, asset_id: str) -> Position | None:
        """This strategy's book for one asset, or ``None`` when flat.

        ``None`` is a real answer -- the run holds nothing in this asset -- and
        not a substituted placeholder.
        """

        return self._state.positions.get(asset_id)

    def quantity(self, asset_id: str) -> Decimal:
        """Signed position size, zero when flat."""

        position = self._state.positions.get(asset_id)
        return Decimal("0") if position is None else position.quantity

    def cash(self, currency: str) -> Decimal:
        """Settled cash balance in ``currency``."""

        return self._state.cash.balance(currency)

    def available_cash(self, currency: str) -> Decimal:
        """Cash not held against an outstanding reservation."""

        return self._state.cash.available_cash(currency)

    @property
    def balances(self) -> Mapping[str, Decimal]:
        return MappingProxyType(dict(self._state.cash.balances))

    @property
    def reserved(self) -> Mapping[str, Decimal]:
        return MappingProxyType(dict(self._state.cash.reserved))

    @property
    def realized_pnl(self) -> Decimal:
        return self._state.realized_pnl

    @property
    def commission_paid(self) -> Decimal:
        return self._state.commission_paid


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderShare:
    """One live order, and this strategy's attributable share of it.

    ``quantity`` is the strategy's own signed contribution as the sizing model
    produced it, *before* netting -- it is not what executed, and it is not the
    order's quantity. ``order.quantity`` is the netted total, which may belong to
    several strategies at once.

    Attributes:
        order: The OMS order, frozen and canonical.
        quantity: This strategy's signed contribution, in the asset's units.
    """

    order: OMSOrder
    quantity: Decimal

    @property
    def asset_id(self) -> str:
        return self.order.asset_id

    @property
    def is_sole_contributor(self) -> bool:
        """Whether this strategy's share is the whole order.

        A strategy that wants to reason about "my order" rather than "my share
        of an order" has to ask, because the honest default is that it does not
        know.
        """

        return self.quantity == self.order.quantity


@dataclass(frozen=True, slots=True)
class OrderView:
    """This strategy's live working orders, and nothing else.

    A query surface only. There is no ``submit``, ``cancel``, ``replace`` or
    ``modify`` here and there must never be: the only way an effect leaves a
    strategy is the ``Iterable[Intent]`` its hook returns, which is what keeps
    one audited channel for "a strategy wants something to happen" (ADR-0026
    decision 5).

    Contains no reference reachable to another strategy's orders, so isolation
    is structural rather than a rule callers are trusted to follow.
    """

    shares: tuple[OrderShare, ...] = ()

    def __iter__(self) -> Iterator[OrderShare]:
        return iter(self.shares)

    def __len__(self) -> int:
        return len(self.shares)

    def __bool__(self) -> bool:
        return bool(self.shares)

    def for_asset(self, asset_id: str) -> tuple[OrderShare, ...]:
        """This strategy's live shares in one asset."""

        return tuple(share for share in self.shares if share.order.asset_id == asset_id)

    def net_quantity(self, asset_id: str) -> Decimal:
        """This strategy's signed working quantity in one asset.

        The number a strategy needs to answer "have I already asked for this?"
        without keeping its own copy -- which is the copy most likely to desync
        across a restore (ADR-0026 decision 9).
        """

        return sum(
            (share.quantity for share in self.shares if share.order.asset_id == asset_id),
            Decimal("0"),
        )


def order_shares_by_strategy(
    oms: OMSState, allocation: AllocationState
) -> Mapping[str, tuple[OrderShare, ...]]:
    """Index this event's live orders by the strategies that contributed to them.

    Built **once per event** and sliced per strategy, so each context costs a
    reference assignment rather than a scan (ADR-0026 decision 7).

    **The iteration runs over the contribution ledger, not over the order
    book**, and that is a performance requirement rather than a stylistic
    choice. ADR-0021 retires a contribution when its order reaches a terminal
    state, so the ledger is bounded by *live* orders; ``OrderBook.open_orders``
    is bounded by every order the run has ever placed, because it filters the
    whole book on ``is_open``. Driving this loop from the book made a 4,000-event
    benchmark quadratic -- 3,770 events/sec fell to 721, and 4x the work cost
    10.63x the time instead of 4.31x. Each order here is fetched by key from the
    book's :class:`PersistentMap`, which is O(1).

    Orders the OMS no longer holds open are skipped even when a contribution
    survives for them, so the view cannot outlive the order it describes.
    """

    if not allocation.contributions:
        # An empty ledger is the common case and must cost nothing to notice.
        # ``PersistentMap.__iter__`` walks every key the map has *ever* held and
        # probes each one, so iterating a ledger emptied by retirement costs
        # O(orders the run has placed) rather than O(0) -- 19us after 200
        # events, growing without bound. ``__len__`` reads a stored size and is
        # O(1), so the check in front of the loop is what keeps a backtest,
        # where every order fills and retires at once, from paying per event.
        return _NO_SHARES

    book = oms.orders
    collected: dict[str, list[OrderShare]] = {}

    for order_id, contributions in allocation.contributions.items():
        key = _order_key(order_id)
        if key is None or not book.contains(key):
            continue
        order = book.find(key)
        if not order.is_open:
            continue
        for contribution in contributions:
            collected.setdefault(contribution.strategy_id, []).append(
                OrderShare(order=order, quantity=contribution.quantity)
            )

    return MappingProxyType(
        {strategy_id: tuple(shares) for strategy_id, shares in collected.items()}
    )


def _order_key(order_id: str) -> OrderId | None:
    """The book's typed key for a contribution's string order id, or ``None``.

    ``AllocationState.contributions`` is keyed by ``OrderRequest.order_id``, a
    string, while :class:`~alphalab.oms.book.OrderBook` keys by
    :class:`~alphalab.oms.ids.OrderId`. ``None`` for an id that is not
    UUID-shaped: a ledger entry the book could not possibly hold is skipped
    rather than raised on, because this is a read of state the run already
    accepted and a view is not the place to discover it.
    """

    try:
        return OrderId(UUID(order_id))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskView:
    """Risk headroom, from the state resynced immediately before dispatch.

    Visibility, never authority: enforcement stays with
    :class:`~alphalab.risk.engine.RiskEngine`, and nothing here can alter a
    limit. A strategy reads this to self-limit rather than to discover its
    limits through rejections.
    """

    _state: RiskState

    @property
    def active_limits(self) -> RiskLimits:
        return self._state.active_limits

    @property
    def cash(self) -> Decimal:
        return self._state.cash

    @property
    def buying_power(self) -> Decimal:
        return self._state.buying_power

    @property
    def current_nav(self) -> Decimal:
        return self._state.current_nav

    @property
    def peak_nav(self) -> Decimal:
        return self._state.peak_nav

    @property
    def daily_loss(self) -> Decimal:
        return self._state.daily_loss

    @property
    def margin(self) -> MarginStatus:
        return self._state.margin

    @property
    def exposure(self) -> ExposureStatus:
        return self._state.exposure


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketView:
    """The market as of this event: latest indexes and the marking prices.

    Deliberately *not* a historical accessor. ``MarketState.history`` is in
    scope of the object this wraps and is not exposed, because a clock-bounded
    lookback needs a look-ahead guard enforced at construction and that is a
    release of its own (ADR-0026, non-goals).
    """

    _market: MarketState
    _prices: Mapping[str, Decimal] = field(default_factory=lambda: _EMPTY_DECIMALS)

    def quote(self, asset_id: str) -> Quote | None:
        return self._market.latest_quotes.get(asset_id)

    def tick(self, asset_id: str) -> Tick | None:
        return self._market.latest_ticks.get(asset_id)

    def bar(self, asset_id: str) -> Bar | None:
        return self._market.latest_bars.get(asset_id)

    def price(self, asset_id: str) -> Decimal | None:
        """The price the run is marking this asset at, or ``None`` if unpriced.

        ``None`` is the same absence
        :class:`~alphalab.runtime.execution_pipeline.UnpricedAsset` records: the
        run has never seen a price for this asset, which is a fact rather than a
        missing value.
        """

        return self._prices.get(asset_id)

    @property
    def prices(self) -> Mapping[str, Decimal]:
        return MappingProxyType(dict(self._prices))

    @property
    def assets(self) -> Sequence[str]:
        """Assets the run has priced, in sorted order for determinism."""

        return tuple(sorted(self._prices))
