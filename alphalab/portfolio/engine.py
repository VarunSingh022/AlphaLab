"""Pure functional portfolio accounting engine.

Accounting model
----------------
The portfolio keeps four separated quantities, and never mixes them:

``cash``
    Actual money movements only: trade proceeds/cost and commissions. Realized
    P&L is *never* added to cash on top of those, because it is already implicit
    in the entry cost and the exit proceeds, each applied on its own fill.
``realized_pnl``
    Cumulative P&L crystallised by reducing or closing positions. It is an
    accounting result carried on the state (and on the position while it is
    open), not a cash movement. Keeping it on the state means it survives a
    position going flat and being dropped from ``positions``.
``commission_paid``
    Cumulative commissions, expensed to cash at fill time. Commissions do not
    enter a position's cost basis, so ``average_cost`` stays a clean price.
``unrealized_pnl``
    Derived, never stored: computed from each open position's ``average_cost``
    and its current ``market_price`` (see :mod:`alphalab.portfolio.valuation`).

Together these satisfy the portfolio accounting identity, which
``PortfolioValuation.snapshot`` exposes and the invariant tests assert:

    equity == deposits - withdrawals + realized_pnl + unrealized_pnl
              - commission_paid
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import new_id
from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.events import (
    CashDeposited,
    CashWithdrawn,
    MarketValueUpdated,
    PortfolioEvent,
    PositionClosed,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
)
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.money import ZERO_MONEY, notional, to_money, to_price, to_quantity
from alphalab.portfolio.position import Position
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Canonical immutable portfolio state.

    ``realized_pnl`` and ``commission_paid`` are cumulative account totals: they
    keep accruing after a position is closed and removed from ``positions``.
    """

    account: Account
    cash: CashLedger = field(default_factory=CashLedger)
    positions: Mapping[str, Position] = field(default_factory=dict)
    ledger: TransactionLedger = field(default_factory=TransactionLedger)
    events: AppendOnlyLog[PortfolioEvent] = field(default_factory=AppendOnlyLog)
    realized_pnl: Decimal = ZERO_MONEY
    commission_paid: Decimal = ZERO_MONEY


class PortfolioEngine:
    @staticmethod
    def apply_deposit(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        amount = to_money(amount)
        new_cash = state.cash.deposit(amount, currency)
        evt = CashDeposited(
            timestamp=timestamp,
            account_id=state.account.account_id,
            amount=amount,
            currency=currency,
        )
        tx = Transaction(
            transaction_id=str(new_id()),
            timestamp=timestamp,
            account_id=state.account.account_id,
            type=TransactionType.DEPOSIT,
            asset_id="CASH",
            quantity=amount,
            price=Decimal("1.0"),
            commission=Decimal("0.0"),
            currency=currency,
        )
        return replace(
            state, cash=new_cash, ledger=state.ledger.append(tx), events=state.events.append(evt)
        )

    @staticmethod
    def apply_withdrawal(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        amount = to_money(amount)
        new_cash = state.cash.withdraw(amount, currency)
        evt = CashWithdrawn(
            timestamp=timestamp,
            account_id=state.account.account_id,
            amount=amount,
            currency=currency,
        )
        tx = Transaction(
            transaction_id=str(new_id()),
            timestamp=timestamp,
            account_id=state.account.account_id,
            type=TransactionType.WITHDRAWAL,
            asset_id="CASH",
            quantity=-amount,
            price=Decimal("1.0"),
            commission=Decimal("0.0"),
            currency=currency,
        )
        return replace(
            state, cash=new_cash, ledger=state.ledger.append(tx), events=state.events.append(evt)
        )

    @staticmethod
    def apply_fill(
        state: PortfolioState,
        asset_id: str,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        timestamp: float,
        currency: str = "USD",
    ) -> PortfolioState:
        """Apply one signed fill (BUY > 0, SELL < 0) to the portfolio.

        Exactly one position update, one cash movement, one ledger transaction
        and one portfolio event are produced per call, so a fill can never be
        counted twice.
        """

        if price <= 0:
            raise InvalidTransactionError("A fill must have a positive price.")
        if commission < 0:
            raise InvalidTransactionError("A fill commission cannot be negative.")

        # Round once, here, at the boundary. `quantity`, `price`, `trade_value`
        # and `commission` are from now on exact at their declared precision, and
        # both the cash movement and the position's cost basis are derived from
        # these same values -- see alphalab.portfolio.money.
        quantity = to_quantity(quantity)
        price = to_price(price)
        trade_value = notional(quantity, price)
        commission = to_money(commission)

        # Checked after rounding: a quantity below the supported share precision
        # is not a tiny fill, it is zero shares, and applying it would fabricate
        # a position event and a ledger entry for a trade that did not happen.
        if quantity == 0:
            raise InvalidTransactionError("A fill must have a non-zero quantity.")

        positions = dict(state.positions)
        pos = positions.get(
            asset_id,
            Position(asset_id, Decimal("0"), Decimal("0"), price, ZERO_MONEY, currency, timestamp),
        )

        is_opening = pos.quantity == 0

        new_pos, pnl = pos.apply_fill(quantity, price, timestamp)

        if new_pos.quantity == 0:
            positions.pop(asset_id, None)
            evt: PortfolioEvent = PositionClosed(
                timestamp, state.account.account_id, asset_id, price, pnl
            )
        else:
            positions[asset_id] = new_pos
            if is_opening:
                evt = PositionOpened(timestamp, state.account.account_id, asset_id, quantity, price)
            elif (pos.quantity > 0 and quantity > 0) or (pos.quantity < 0 and quantity < 0):
                evt = PositionIncreased(
                    timestamp, state.account.account_id, asset_id, quantity, price
                )
            else:
                evt = PositionReduced(
                    timestamp, state.account.account_id, asset_id, quantity, price, pnl
                )

        # Cash moves by actual trade proceeds/cost and commission only. Realized
        # P&L is an accounting result (accumulated on `realized_pnl` and carried
        # on the PositionReduced/PositionClosed events); it is already implicit in
        # the proceeds vs. the entry cost that were each applied on their own fill,
        # so it must not be added to cash a second time here.
        cash_impact = (trade_value if quantity < 0 else -trade_value) - commission
        new_cash = state.cash
        if cash_impact > 0:
            new_cash = new_cash.deposit(cash_impact, currency)
        elif cash_impact < 0:
            new_cash = new_cash.withdraw(-cash_impact, currency)

        tx_type = TransactionType.BUY if quantity > 0 else TransactionType.SELL
        tx = Transaction(
            str(new_id()),
            timestamp,
            state.account.account_id,
            tx_type,
            asset_id,
            quantity,
            price,
            commission,
            currency,
        )

        return replace(
            state,
            positions=positions,
            cash=new_cash,
            ledger=state.ledger.append(tx),
            events=state.events.append(evt),
            realized_pnl=state.realized_pnl + pnl,
            commission_paid=state.commission_paid + commission,
        )

    @staticmethod
    def update_market_prices(
        state: PortfolioState, prices: Mapping[str, Decimal], timestamp: float
    ) -> PortfolioState:
        """Mark open positions to market.

        This is the portfolio's mark-to-market step: it re-prices held positions
        from observed market prices and touches nothing else -- cash, realized
        P&L, commissions and the transaction ledger are all left untouched, so
        unrealized P&L is the only thing that moves. Prices for assets that are
        not held are ignored; positions with no price in ``prices`` keep their
        previous mark. A ``MarketValueUpdated`` event is emitted only when at
        least one held position was actually re-marked, which keeps replaying a
        quiet market free of empty events.
        """

        # Iterating held positions rather than `prices` keeps the cost bound to
        # the size of the book, not to how many assets have ever been quoted.
        marked: dict[str, Decimal] = {}
        positions = dict(state.positions)
        for asset, position in state.positions.items():
            price = prices.get(asset)
            if price is None or price <= 0:
                continue
            positions[asset] = position.update_market_price(price, timestamp)
            marked[asset] = price

        if not marked:
            return state

        evt = MarketValueUpdated(timestamp, state.account.account_id, marked)
        return replace(state, positions=positions, events=state.events.append(evt))
