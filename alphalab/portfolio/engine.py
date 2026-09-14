"""Pure functional portfolio accounting engine.

Accounting model
----------------
The portfolio keeps four separated quantities, and never mixes them:

``cash``
    Actual money movements only: trade proceeds/cost and commissions. Realized
    P&L is *never* added to cash on top of those, because it is already implicit
    in the entry cost and the exit proceeds, each applied on its own fill.
``realized_pnl``
    Cumulative P&L crystallised by reducing or closing positions, **per
    currency**. It is an accounting result carried on the state (and on the
    position while it is open), not a cash movement. Keeping it on the state
    means it survives a position going flat and being dropped from ``positions``.
``commission_paid``
    Cumulative commissions, expensed to cash at fill time, **per currency**.
    Commissions do not enter a position's cost basis, so ``average_cost`` stays
    a clean price.
``unrealized_pnl``
    Derived, never stored: computed from each open position's ``average_cost``
    and its current ``market_price`` (see :mod:`alphalab.portfolio.valuation`).

Together these satisfy the portfolio accounting identity, which
``PortfolioValuation.snapshot`` exposes and the invariant tests assert:

    equity == deposits - withdrawals + realized_pnl + unrealized_pnl
              - commission_paid

Settlement currency, and what v2.17 changed
-------------------------------------------

``realized_pnl`` and ``commission_paid`` were single ``Decimal`` scalars naming
no currency until v2.17. ADR-0033 decision 13 named them as the first two of the
four blockers to settlement-level multi-currency: "a run trading in two would sum
them across both". They are now
:class:`~alphalab.portfolio.amounts.CurrencyAmounts`, so a JPY fill accrues
against JPY and a USD fill against USD, and the identity above holds **per
currency** -- which is the only sense in which it can hold for a book that
settles in more than one.

``cash`` has been keyed by currency since v2.1 and ``Position`` has declared its
own since v2.7, so those two halves were already right; these two were the ones
that were not. See ADR-0035.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.ids import new_id
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.events import (
    CashConverted,
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
from alphalab.portfolio.fx import FxConversion, FxRates
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.money import ZERO_MONEY, notional, to_money, to_price, to_quantity
from alphalab.portfolio.position import Position
from alphalab.portfolio.transaction import Transaction
from alphalab.portfolio.types import TransactionType


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Canonical immutable portfolio state.

    ``realized_pnl`` and ``commission_paid`` are cumulative account totals **per
    settlement currency**: they keep accruing after a position is closed and
    removed from ``positions``, and they are never summed across two currencies
    without a stated rate. Read one currency with
    :meth:`~alphalab.portfolio.amounts.CurrencyAmounts.of` and the whole
    accumulation with
    :meth:`~alphalab.portfolio.amounts.CurrencyAmounts.total_in`.
    """

    account: Account
    cash: CashLedger = field(default_factory=CashLedger)
    positions: Mapping[str, Position] = field(default_factory=dict)
    ledger: TransactionLedger = field(default_factory=TransactionLedger)
    events: AppendOnlyLog[PortfolioEvent] = field(default_factory=AppendOnlyLog)
    realized_pnl: CurrencyAmounts = field(default_factory=CurrencyAmounts)
    commission_paid: CurrencyAmounts = field(default_factory=CurrencyAmounts)

    @property
    def settlement_currencies(self) -> tuple[str, ...]:
        """Every currency this book has settled anything in, sorted.

        The union of what cash holds, what positions declare, and what has been
        realized or expensed. This is what makes "is this book single-currency?"
        answerable from the state rather than from a configuration flag.
        """

        currencies = {
            currency for currency, amount in self.cash.balances.items() if amount != ZERO_MONEY
        }
        currencies.update(position.currency for position in self.positions.values())
        currencies.update(self.realized_pnl.currencies)
        currencies.update(self.commission_paid.currencies)
        return tuple(sorted(currencies))


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
    def convert_cash(
        state: PortfolioState,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        rates: FxRates,
        timestamp: float,
    ) -> tuple[PortfolioState, FxConversion]:
        """Move cash between two settlement currencies at a supplied rate.

        What funds a currency a run settles in out of one it already holds. A
        multi-currency pipeline needs it: ``starting_cash`` funds
        ``ExecutionPipelineConfig.currency`` and nothing else, and a fill in
        another currency debits *that* currency's balance -- so a run that
        settles EUR without EUR cash is refused by
        :class:`~alphalab.portfolio.exceptions.InsufficientFundsError`, loudly
        and correctly.

        **Nothing is converted implicitly.** This is a deliberate act with a
        rate the caller supplied, and it records that rate on a
        :class:`~alphalab.portfolio.events.CashConverted` event: both
        currencies, both amounts, the rate, its ``as_of`` and its source. A
        settlement conversion nobody can trace back to a quote is the "invented
        figure that looks authoritative" ADR-0020 refuses, and there is no
        auto-conversion path anywhere in the pipeline for the same reason.

        Rounding happens once, in :meth:`~alphalab.portfolio.fx.FxRates.convert`,
        which is :mod:`alphalab.portfolio.money`'s rule applied to this path.
        The debit is the exact ``amount`` and the credit is the rounded
        conversion, so no fraction of a minor unit is created or destroyed on
        either side.

        Returns:
            The new state, and the :class:`~alphalab.portfolio.fx.FxConversion`
            performed -- so a caller can report what it cost without re-deriving
            it from the event log.

        Raises:
            InvalidTransactionError: If ``amount`` is not positive, or the two
                currencies are the same. Converting a currency into itself is
                not a conversion, and doing it through this path would write an
                event claiming a rate for a pair no table may hold.
            InsufficientFundsError: If ``from_currency`` cannot cover ``amount``.
            MissingRateError: If ``rates`` holds no rate for the pair.
            StaleRateError: If the only rate for the pair is too old at
                ``timestamp``.
        """

        if amount <= 0:
            raise InvalidTransactionError(
                f"A cash conversion moves a positive amount, got {amount}."
            )
        if from_currency == to_currency:
            raise InvalidTransactionError(
                f"{from_currency} to {to_currency} is not a conversion. A currency is "
                "worth one of itself, and recording it as a conversion would claim a "
                "rate for a pair no FxRates table may hold."
            )

        amount = to_money(amount)
        # Convert first: a refused rate must leave the ledger untouched rather
        # than debit one side of a movement that never completes.
        conversion = rates.convert(amount, from_currency, to_currency, timestamp)

        cash = state.cash.withdraw(amount, from_currency)
        cash = cash.deposit(conversion.converted, to_currency)

        evt = CashConverted(
            timestamp=timestamp,
            account_id=state.account.account_id,
            amount=amount,
            from_currency=from_currency,
            converted=conversion.converted,
            to_currency=to_currency,
            rate=conversion.rate.rate,
            rate_as_of=conversion.rate.as_of,
            rate_source=conversion.rate.source,
            rate_derived=conversion.rate.derived,
        )
        debit = Transaction(
            transaction_id=str(new_id()),
            timestamp=timestamp,
            account_id=state.account.account_id,
            type=TransactionType.WITHDRAWAL,
            asset_id="CASH",
            quantity=-amount,
            price=Decimal("1.0"),
            commission=Decimal("0.0"),
            currency=from_currency,
        )
        credit = Transaction(
            transaction_id=str(new_id()),
            timestamp=timestamp,
            account_id=state.account.account_id,
            type=TransactionType.DEPOSIT,
            asset_id="CASH",
            quantity=conversion.converted,
            price=Decimal("1.0"),
            commission=Decimal("0.0"),
            currency=to_currency,
        )
        return (
            replace(
                state,
                cash=cash,
                ledger=state.ledger.append(debit).append(credit),
                events=state.events.append(evt),
            ),
            conversion,
        )

    @staticmethod
    def apply_fill(
        state: PortfolioState,
        asset_id: str,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        timestamp: float,
        currency: str,
    ) -> PortfolioState:
        """Apply one signed fill (BUY > 0, SELL < 0) to the portfolio.

        Exactly one position update, one cash movement, one ledger transaction
        and one portfolio event are produced per call, so a fill can never be
        counted twice.

        ``currency`` is **required** as of v2.17, and until then defaulted to
        ``"USD"``. A default was survivable while ``realized_pnl`` and
        ``commission_paid`` were currency-less scalars, because the string only
        reached the cash ledger and the position. It is not survivable now: the
        currency decides which bucket a fill's P&L and commission accrue into,
        so a caller that forgets it would book EUR results against USD and every
        figure derived from them would be wrong in a way no later check could
        detect. This is ADR-0019's rule -- a currency is named, never assumed --
        applied to the one entry point that had escaped it.

        Raises:
            InvalidTransactionError: If the price is not positive, the
                commission is negative, or the quantity rounds to zero.
        """

        if not currency.strip():
            raise InvalidTransactionError(
                "A fill must name the currency it settles in. It decides which "
                "cash balance moves and which realized-P&L and commission bucket "
                "accrues, and no currency can be assumed for it."
            )

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
            # Accrued against the currency the fill actually settled in, never
            # summed across two. See the module docstring and ADR-0035.
            realized_pnl=state.realized_pnl.add(pnl, currency),
            commission_paid=state.commission_paid.add(commission, currency),
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
