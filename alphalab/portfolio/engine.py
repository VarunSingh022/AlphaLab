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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import cast

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import in_accounting_context, require_finite
from alphalab.common.evolve import evolve
from alphalab.common.ids import new_id
from alphalab.conventions.economics import CASH_EQUITY, InstrumentEconomics
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.book import PositionBook
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.corporate_actions import CashFlow, CashFlowKind, Split
from alphalab.portfolio.events import (
    CashConverted,
    CashDeposited,
    CashFlowBooked,
    CashWithdrawn,
    MarketValueUpdated,
    PortfolioEvent,
    PositionClosed,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
    PositionSplit,
    VariationSettled,
)
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.fx import FxConversion, FxRates
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.money import ZERO_MONEY, notional, to_money
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
    positions: Mapping[str, Position] = field(default_factory=PositionBook)
    ledger: TransactionLedger = field(default_factory=TransactionLedger)
    events: AppendOnlyLog[PortfolioEvent] = field(default_factory=AppendOnlyLog)
    realized_pnl: CurrencyAmounts = field(default_factory=CurrencyAmounts)
    commission_paid: CurrencyAmounts = field(default_factory=CurrencyAmounts)
    #: Assets whose position was last priced by a fill rather than by the market,
    #: sorted. A fill marks the position at its execution price; the next
    #: mark-to-market re-marks it at the market's.
    #: :meth:`PortfolioEngine.mark_changed` re-marks these and the asset whose
    #: price changed, and nothing else (PRF-001).
    pending_marks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Any mapping of positions is accepted, and kept as a PositionBook, so
        # the totals every valuation reads are always the book's own (PRF-001).
        if not isinstance(self.positions, PositionBook):
            object.__setattr__(self, "positions", PositionBook(self.positions))
        pending = self.pending_marks
        # Checked before it is rebuilt: every fill builds a state, and its
        # pending marks are almost always already sorted and unique (PRF-006).
        if type(pending) is not tuple or any(b <= a for a, b in pairwise(pending)):
            object.__setattr__(self, "pending_marks", _pending(pending))

    @property
    def book(self) -> PositionBook:
        """``positions``, as the :class:`~alphalab.portfolio.book.PositionBook` it is kept as."""

        return cast(PositionBook, self.positions)

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
        currencies.update(self.book.currencies)
        currencies.update(self.realized_pnl.currencies)
        currencies.update(self.commission_paid.currencies)
        return tuple(sorted(currencies))


class PortfolioEngine:
    @staticmethod
    @in_accounting_context
    def apply_deposit(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        """Credit ``amount`` of ``currency``, rounded once to its minor unit.

        Raises:
            InvalidTransactionError: If ``amount`` is not a finite positive
                number, or rounds to nothing at the currency's minor unit. A
                negative deposit is a withdrawal that skipped the funds check.
            UnknownCurrencyUnitsError: If the account cannot say what the
                currency's minor unit is.
        """

        amount = _positive_money(state, amount, currency, "A deposit")
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
        return evolve(
            state, cash=new_cash, ledger=state.ledger.append(tx), events=state.events.append(evt)
        )

    @staticmethod
    @in_accounting_context
    def apply_withdrawal(
        state: PortfolioState, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        """Debit ``amount`` of ``currency``, rounded once to its minor unit.

        Raises:
            InvalidTransactionError: If ``amount`` is not a finite positive
                number, or rounds to nothing at the currency's minor unit.
            InsufficientFundsError: If the balance cannot cover it.
        """

        amount = _positive_money(state, amount, currency, "A withdrawal")
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
        return evolve(
            state, cash=new_cash, ledger=state.ledger.append(tx), events=state.events.append(evt)
        )

    @staticmethod
    @in_accounting_context
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

        if not amount.is_finite() or amount <= 0:
            raise InvalidTransactionError(
                f"A cash conversion moves a positive amount, got {amount}."
            )
        if from_currency == to_currency:
            raise InvalidTransactionError(
                f"{from_currency} to {to_currency} is not a conversion. A currency is "
                "worth one of itself, and recording it as a conversion would claim a "
                "rate for a pair no FxRates table may hold."
            )

        units = state.account.currency_units
        amount = to_money(amount, from_currency, units)
        # Convert first: a refused rate must leave the ledger untouched rather
        # than debit one side of a movement that never completes. The credit is
        # rounded to the *account's* unit for the currency it lands in, which is
        # the unit that currency's cash balance is kept at.
        conversion = rates.convert(amount, from_currency, to_currency, timestamp, units=units)

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
            evolve(
                state,
                cash=cash,
                ledger=state.ledger.append(debit).append(credit),
                events=state.events.append(evt),
            ),
            conversion,
        )

    @staticmethod
    @in_accounting_context
    def apply_fill(
        state: PortfolioState,
        asset_id: str,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        timestamp: float,
        currency: str,
        *,
        economics: InstrumentEconomics | None = None,
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

        Prices and quantities are booked exactly as given; only money is
        rounded, once, to the settlement currency's minor unit (see
        :mod:`alphalab.portfolio.money`).

        ``economics`` is what one unit of the instrument is worth and how it
        settles (ledger ACC-005); ``None`` is a fully paid unit with a
        multiplier of one, as every fill was before v3.11. The multiplier scales
        the money the fill moves. A position whose gains settle as cash -- a
        future, a perpetual -- moves no notional: the fill settles the open
        position at the fill price, and only that settlement and the commission
        move cash. A position is booked by one set of economics for its life.

        ``commission`` is signed since v3.11 (ledger ACC-007): a negative one is
        a rebate, which a venue pays a liquidity provider. And a price need only
        be one the economics admit: positive, unless they allow otherwise.

        Raises:
            InvalidTransactionError: If the price is not one the economics
                admit, the quantity is zero, any figure is not finite, or the
                economics differ from those the open position was booked by.
            UnknownCurrencyUnitsError: If the account cannot say what the
                settlement currency's minor unit is.
        """

        if not currency.strip():
            raise InvalidTransactionError(
                "A fill must name the currency it settles in. It decides which "
                "cash balance moves and which realized-P&L and commission bucket "
                "accrues, and no currency can be assumed for it."
            )

        for label, value in (("quantity", quantity), ("price", price), ("commission", commission)):
            require_finite(value, f"A fill's {label}", exception_type=InvalidTransactionError)
        # Booked by the economics the position already has, or those declared --
        # a fully paid unit of one is recorded as no declaration at all, which
        # is what it was before v3.11.
        declared = None if economics is None or economics.is_cash_equity else economics
        if not (declared or CASH_EQUITY).admits_price(price):
            raise InvalidTransactionError(
                f"A fill of {asset_id} at {price} is refused: its price must be positive, "
                "unless its economics declare that it may not be (ACC-007)."
            )
        # A zero quantity is not a tiny fill; it is no fill, and applying it
        # would fabricate a position event and a ledger entry for a trade that
        # did not happen. Any non-zero quantity is booked exactly as reported.
        if quantity == 0:
            raise InvalidTransactionError("A fill must have a non-zero quantity.")

        # Round money once, here, at the boundary. `trade_value` and
        # `commission` are from now on exact at the settlement currency's minor
        # unit, and both the cash movement and the position's cost basis are
        # derived from these same values -- see alphalab.portfolio.money.
        units = state.account.currency_units
        minor_units = units.minor_units(currency)
        commission = to_money(commission, currency, units)

        positions = state.book
        held = positions.get(asset_id)
        pos = (
            Position(
                asset_id,
                Decimal("0"),
                Decimal("0"),
                price,
                ZERO_MONEY,
                currency,
                timestamp,
                minor_units=minor_units,
                economics=declared,
            )
            if held is None
            else held
        )
        if pos.currency != currency:
            raise InvalidTransactionError(
                f"{asset_id} is held in {pos.currency} and this fill settles in {currency}. "
                "One position has one currency; a fill in another is a different instrument "
                "or a mislabelled report, and booking it would mix two currencies in one basis."
            )
        if pos.economics != declared:
            raise InvalidTransactionError(
                f"{asset_id} is held by the economics {pos.economics!r} and this fill is "
                f"booked by {declared!r}. A position is booked one way for its life; a "
                "change of multiplier or settlement is a different instrument."
            )
        # The money the trade itself moves: its notional, for a fully paid
        # position; nothing, for one whose gains settle as cash.
        if not pos.pays_notional:
            trade_value = ZERO_MONEY
        elif pos.multiplier == 1:
            trade_value = notional(quantity, price, currency, units)
        else:
            trade_value = to_money(abs(quantity) * price * pos.multiplier, currency, units)

        is_opening = pos.quantity == 0

        new_pos, pnl = pos.apply_fill(quantity, price, timestamp)

        if new_pos.quantity == 0:
            positions = positions.delete(asset_id)
            evt: PortfolioEvent = PositionClosed(
                timestamp, state.account.account_id, asset_id, price, pnl
            )
        else:
            positions = positions.set(asset_id, new_pos)
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
        # so it must not be added to cash a second time here. A position whose
        # gains settle as cash is the exception that proves it: no proceeds move,
        # so its P&L -- the settlement at the fill price -- is the cash, owed
        # whether or not cash covers it.
        new_cash = state.cash
        if not pos.pays_notional and pnl != 0:
            new_cash = new_cash.settle(pnl, currency)
        cash_impact = (trade_value if quantity < 0 else -trade_value) - commission
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

        # A position a fill leaves open is priced at the fill until the market
        # prices it again; a closed one needs no mark at all.
        pending = state.pending_marks
        if asset_id in positions:
            if asset_id not in pending:
                pending = _pending(pending, add=(asset_id,))
        elif asset_id in pending:
            pending = _pending(pending, remove=(asset_id,))
        # Built field by field rather than through ``dataclasses.replace``, which
        # introspects the class on every call: this runs on every fill (PRF-006).
        return PortfolioState(
            state.account,
            new_cash,
            positions,
            state.ledger.append(tx),
            state.events.append(evt),
            # Accrued against the currency the fill actually settled in, never
            # summed across two. See the module docstring and ADR-0035.
            state.realized_pnl.add(pnl, currency),
            state.commission_paid.add(commission, currency),
            pending,
        )

    @staticmethod
    @in_accounting_context
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
        return _marked(state, state.positions.items(), prices, timestamp)

    @staticmethod
    @in_accounting_context
    def mark_changed(
        state: PortfolioState,
        prices: Mapping[str, Decimal],
        timestamp: float,
        changed: str | None,
    ) -> PortfolioState:
        """Mark to market after at most one price changed: ``changed``'s.

        ``prices`` is every current price, ``changed`` the one asset whose
        price is new since the book was last marked -- what one market event
        does -- or ``None`` when no price changed. The result is the book
        :meth:`update_market_prices` would produce from the same ``prices``,
        at a cost that does not grow with the book: it re-marks ``changed`` and
        every position a fill priced since (:attr:`PortfolioState.pending_marks`),
        and no other. Every other held position already carries its price in
        ``prices``, and re-marking it would change nothing but its
        ``last_updated`` -- which therefore stays the instant its own price was
        last observed rather than the instant any asset last ticked, and the
        ``MarketValueUpdated`` event lists the marks that changed rather than
        every held position (ledger PRF-001).

        The equivalence rests on the caller's word that no other price changed.
        :class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline`, which
        moves one price per market event, is the caller it was built for; a
        caller that moves several prices at once uses
        :meth:`update_market_prices`.
        """

        assets = (
            state.pending_marks
            if changed is None
            else _pending(state.pending_marks, add=(changed,))
        )
        if not assets:
            return state

        book = state.book
        held = ((asset, book[asset]) for asset in assets if asset in book)
        return _marked(state, held, prices, timestamp)

    @staticmethod
    @in_accounting_context
    def apply_cash_flow(state: PortfolioState, flow: CashFlow, timestamp: float) -> PortfolioState:
        """Book cash a holding earned or cost without a trade (ledger ACC-006).

        A dividend, interest, a fee or a funding payment: the cash moves by the
        signed amount, rounded once to the currency's minor unit, and the same
        amount is realized P&L -- so ``equity == deposits - withdrawals +
        realized_pnl + unrealized_pnl - commission_paid`` holds with it in. A
        payment is an obligation and is booked whether or not cash covers it,
        as :meth:`~alphalab.portfolio.cash.CashLedger.settle` records.

        Raises:
            InvalidTransactionError: If the amount rounds to nothing at the
                currency's minor unit.
            UnknownCurrencyUnitsError: If the account cannot say what the
                currency's minor unit is.
        """

        amount = to_money(flow.amount, flow.currency, state.account.currency_units)
        if amount == 0:
            raise InvalidTransactionError(
                f"A {flow.kind.value} of {flow.amount} {flow.currency} rounds to nothing at the "
                "currency's minor unit; there is nothing to book."
            )
        tx = Transaction(
            str(new_id()),
            timestamp,
            state.account.account_id,
            _CASH_FLOW_TRANSACTIONS[flow.kind],
            flow.asset_id or "CASH",
            amount,
            Decimal("1.0"),
            Decimal("0.0"),
            flow.currency,
        )
        evt = CashFlowBooked(
            timestamp,
            state.account.account_id,
            flow.kind.value,
            flow.asset_id,
            amount,
            flow.currency,
            flow.reference,
        )
        return evolve(
            state,
            cash=state.cash.settle(amount, flow.currency),
            ledger=state.ledger.append(tx),
            events=state.events.append(evt),
            realized_pnl=state.realized_pnl.add(amount, flow.currency),
        )

    @staticmethod
    @in_accounting_context
    def apply_split(state: PortfolioState, split: Split, timestamp: float) -> PortfolioState:
        """Apply a split, reverse split or stock dividend to a holding (ledger ACC-006).

        The position's quantity is multiplied by the ratio and its value does
        not move -- see :meth:`~alphalab.portfolio.position.Position.split`. No
        cash moves, so no transaction is written; the event records it.

        Raises:
            InvalidTransactionError: If the account holds no position in the asset.
        """

        position = state.book.get(split.asset_id)
        if position is None:
            raise InvalidTransactionError(
                f"No position in {split.asset_id} to split; a split of a holding the account "
                "does not have changes nothing and is more likely a mistake than an action."
            )
        after = position.split(split.ratio, timestamp)
        evt = PositionSplit(
            timestamp, state.account.account_id, split.asset_id, split.ratio, after.quantity
        )
        return evolve(
            state,
            positions=state.book.set(split.asset_id, after),
            events=state.events.append(evt),
        )


#: Which ledger entry each kind of cash flow writes.
_CASH_FLOW_TRANSACTIONS: Mapping[CashFlowKind, TransactionType] = {
    CashFlowKind.DIVIDEND: TransactionType.DIVIDEND,
    CashFlowKind.INTEREST: TransactionType.INTEREST,
    CashFlowKind.FEE: TransactionType.FEE,
    CashFlowKind.FUNDING: TransactionType.FUNDING,
}


def _marked(
    state: PortfolioState,
    held: Iterable[tuple[str, Position]],
    prices: Mapping[str, Decimal],
    timestamp: float,
) -> PortfolioState:
    """``state`` with each of ``held`` marked at its price in ``prices``, and settled.

    A position with no price in ``prices`` keeps its mark. One whose price its
    economics do not admit is refused rather than skipped: until v3.11 a
    non-positive mark was silently ignored, and the position went on carrying
    a stale price with nothing to say so (ledger ACC-007). A position whose
    gains settle as cash is settled at its mark -- variation margin -- and the
    cash and the realized P&L move with it.
    """

    marked: dict[str, Decimal] = {}
    positions = state.book
    cash = state.cash
    realized = state.realized_pnl
    ledger = state.ledger
    events = state.events
    settled: list[PortfolioEvent] = []
    for asset, position in held:
        price = prices.get(asset)
        if price is None:
            continue
        if not (position.economics or CASH_EQUITY).admits_price(price):
            raise InvalidTransactionError(
                f"{asset} cannot be marked at {price}: its price must be positive, unless its "
                "economics declare that it may not be (ACC-007). A mark is refused, not "
                "skipped, so no position goes on carrying a price nothing says is stale."
            )
        after, amount = position.settle(price, timestamp)
        positions = positions.set(asset, after)
        marked[asset] = price
        if amount != 0:
            currency = position.currency
            cash = cash.settle(amount, currency)
            realized = realized.add(amount, currency)
            ledger = ledger.append(
                Transaction(
                    str(new_id()),
                    timestamp,
                    state.account.account_id,
                    TransactionType.VARIATION_MARGIN,
                    asset,
                    position.quantity,
                    price,
                    Decimal("0.0"),
                    currency,
                )
            )
            settled.append(
                VariationSettled(
                    timestamp, state.account.account_id, asset, price, amount, currency
                )
            )

    if not marked:
        return state

    events = events.append(MarketValueUpdated(timestamp, state.account.account_id, marked))
    for event in settled:
        events = events.append(event)
    return evolve(
        state,
        positions=positions,
        pending_marks=_pending(state.pending_marks, remove=marked),
        cash=cash,
        realized_pnl=realized,
        ledger=ledger,
        events=events,
    )


def _pending(
    pending: tuple[str, ...], add: Iterable[str] = (), remove: Iterable[str] = ()
) -> tuple[str, ...]:
    """``pending`` with ``add`` included and ``remove`` left out, sorted and unique."""

    removed = set(remove)
    return tuple(sorted({*pending, *add} - removed))


def _positive_money(state: PortfolioState, amount: Decimal, currency: str, what: str) -> Decimal:
    """``amount`` as money in ``currency``, refusing what is not a real movement."""

    if not currency.strip():
        raise InvalidTransactionError(f"{what} must name the currency it is in.")
    require_finite(amount, f"{what}'s amount", exception_type=InvalidTransactionError)
    money = to_money(amount, currency, state.account.currency_units)
    if money <= 0:
        raise InvalidTransactionError(
            f"{what} moves a positive amount of money, got {amount} {currency}"
            + (f", which is {money} at the currency's minor unit." if money != amount else ".")
        )
    return money
