import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.events import (
    CashDeposited,
    CashWithdrawn,
    PortfolioEvent,
    PositionClosed,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
)
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.position import Position
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType


@dataclass(frozen=True, slots=True)
class PortfolioState:
    account: Account
    cash: CashLedger = field(default_factory=CashLedger)
    positions: Mapping[str, Position] = field(default_factory=dict)
    ledger: TransactionLedger = field(default_factory=TransactionLedger)
    events: tuple[PortfolioEvent, ...] = field(default_factory=tuple)


class PortfolioEngine:
    @staticmethod
    def apply_deposit(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        new_cash = state.cash.deposit(amount, currency)
        evt = CashDeposited(
            timestamp=timestamp,
            account_id=state.account.account_id,
            amount=amount,
            currency=currency,
        )
        tx = Transaction(
            transaction_id=str(uuid.uuid4()),
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
            state, cash=new_cash, ledger=state.ledger.append(tx), events=(*state.events, evt)
        )

    @staticmethod
    def apply_withdrawal(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        new_cash = state.cash.withdraw(amount, currency)
        evt = CashWithdrawn(
            timestamp=timestamp,
            account_id=state.account.account_id,
            amount=amount,
            currency=currency,
        )
        tx = Transaction(
            transaction_id=str(uuid.uuid4()),
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
            state, cash=new_cash, ledger=state.ledger.append(tx), events=(*state.events, evt)
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
        positions = dict(state.positions)
        pos = positions.get(
            asset_id,
            Position(
                asset_id, Decimal("0"), Decimal("0"), price, Decimal("0"), currency, timestamp
            ),
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

        cash_impact = -(quantity * price) - commission + pnl
        new_cash = state.cash
        if cash_impact > 0:
            new_cash = new_cash.deposit(cash_impact, currency)
        elif cash_impact < 0:
            new_cash = new_cash.withdraw(-cash_impact, currency)

        tx_type = TransactionType.BUY if quantity > 0 else TransactionType.SELL
        tx = Transaction(
            str(uuid.uuid4()),
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
            events=(*state.events, evt),
        )

    @staticmethod
    def update_market_prices(
        state: PortfolioState, prices: Mapping[str, Decimal], timestamp: float
    ) -> PortfolioState:
        positions = dict(state.positions)
        for asset, price in prices.items():
            if asset in positions:
                positions[asset] = positions[asset].update_market_price(price, timestamp)
        return replace(state, positions=positions)
