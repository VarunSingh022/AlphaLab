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
from typing import Any, Final, cast

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import in_accounting_context, require_finite
from alphalab.common.evolve import evolve
from alphalab.common.ids import new_id
from alphalab.conventions.economics import CASH_EQUITY, InstrumentEconomics
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.book import PositionBook
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.corporate_actions import CashFlow, CashFlowKind, ExchangedHolding, Split
from alphalab.portfolio.events import (
    CashConverted,
    CashDeposited,
    CashFlowBooked,
    CashWithdrawn,
    DeliveryAmended,
    MarketValueUpdated,
    PaymentAmended,
    PortfolioEvent,
    PositionClosed,
    PositionExchanged,
    PositionIncreased,
    PositionOpened,
    PositionReduced,
    PositionSplit,
    VariationSettled,
)
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.financing import ACCRUAL_CLOSED, ACCRUES
from alphalab.portfolio.fx import FxConversion, FxRates
from alphalab.portfolio.ledger import TransactionLedger
from alphalab.portfolio.money import ZERO_MONEY, notional, to_money
from alphalab.portfolio.position import Position
from alphalab.portfolio.settlement import (
    AFFECTS,
    DELIVERS,
    DELIVERY_BASIS,
    DELIVERY_OF,
    DELIVERY_PARTS,
    DELIVERY_TERMS,
    ENTITLEMENT,
    PAYMENT_CHANGE,
    SETTLEMENT_CASH,
    SETTLES_AT,
    CashPosition,
    DeliveryConvention,
    PaymentChange,
    PendingSettlement,
    amend_pending,
    entitlement_key,
    payment_chain,
    settle_pending,
)
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
    #: Fills whose cash and units have not settled, in the order traded (v4.2.0,
    #: ledger FUT-015). Derived from the ledger's settlement metadata and rebuilt
    #: from it on restore, so it is never persisted on its own; empty for a book
    #: whose instruments declare no settlement rule. See
    #: :mod:`alphalab.portfolio.settlement`.
    unsettled: tuple[PendingSettlement, ...] = ()

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
        *,
        financed: bool = False,
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

        # ``financed``: the consideration of a delivery taken on margin, owed in
        # ``from_currency`` whether or not it is held there (FUT-017).
        cash = (
            state.cash.settle(-amount, from_currency)
            if financed
            else state.cash.withdraw(amount, from_currency)
        )
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
        final_settlement: bool = False,
        settles_at: float | None = None,
        financed: bool = False,
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
        be one the economics admit: positive, unless they allow otherwise --
        or, for a contract's ``final_settlement`` (v4.1.1, FUT-012), zero too:
        an option that expires worthless is closed at nothing, which no trade
        could have been.

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
        admitted = (declared or CASH_EQUITY).admits_price(price) or (
            final_settlement and price == 0
        )
        if not admitted:
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
        # Buying back a short is an obligation the account already carries --
        # the shares are owed -- so its cost is debited whether or not cash
        # covers it, as a margin account's debit balance is; a forced
        # liquidation of an account whose short has outrun its cash is exactly
        # this (FUT-017). Every other cost is spent from cash the account holds.
        covering = pos.quantity < 0 < quantity <= -pos.quantity
        if cash_impact > 0:
            new_cash = new_cash.deposit(cash_impact, currency)
        elif cash_impact < 0 and (covering or financed):
            # ``financed``: a delivery the account takes on margin, debited as
            # the obligation it is -- what the caller has checked its margin
            # terms allow (FUT-017).
            new_cash = new_cash.settle(cash_impact, currency)
        elif cash_impact < 0:
            new_cash = new_cash.withdraw(-cash_impact, currency)

        tx_type = TransactionType.BUY if quantity > 0 else TransactionType.SELL
        # Settlement-date accounting (FUT-015): booked on the transaction only
        # when its instrument declares a rule, so every other fill's transaction
        # is the one it always was.
        settled_cash = None
        if settles_at is not None:
            settled_cash = cash_impact + (pnl if not pos.pays_notional else Decimal("0"))
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
            {}
            if settles_at is None
            else {SETTLES_AT: settles_at, SETTLEMENT_CASH: str(settled_cash)},
        )
        unsettled = state.unsettled
        if settles_at is not None or unsettled:
            assert settles_at is None or settled_cash is not None
            unsettled = settle_pending(
                unsettled,
                timestamp,
                None
                if settles_at is None or settled_cash is None
                else PendingSettlement(
                    tx.transaction_id,
                    asset_id,
                    currency,
                    settled_cash,
                    quantity,
                    timestamp,
                    settles_at,
                ),
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
            unsettled,
        )

    @staticmethod
    def cash_position(state: PortfolioState, currency: str, at: float) -> CashPosition:
        """``currency``'s cash at ``at``: trade-date, and what of it has not settled (FUT-015).

        See :mod:`alphalab.portfolio.settlement`. For a book whose instruments
        declare no settlement rule nothing is unsettled, and every figure is the
        cash ledger's balance.
        """

        credits = Decimal("0")
        debits = Decimal("0")
        for entry in state.unsettled:
            if entry.currency != currency or not entry.pending_at(at):
                continue
            if entry.cash > 0:
                credits += entry.cash
            else:
                debits -= entry.cash
        return CashPosition(currency, state.cash.balance(currency), credits, debits)

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
    def record_accrual(
        state: PortfolioState, key: str, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        """Record one day's accrued interest or fee under ``key``, moving no cash (FUT-023).

        An ``ACCRUAL`` ledger transaction holding the exact amount, signed as the
        cash it will move -- negative owed, positive earned -- unrounded, so a
        period posting the sum rounds it once. Cash, P&L and every event are
        untouched until :meth:`close_accrual` closes it.
        """

        tx = Transaction(
            str(new_id()),
            timestamp,
            state.account.account_id,
            TransactionType.ACCRUAL,
            "CASH",
            amount,
            Decimal("1.0"),
            Decimal("0.0"),
            currency,
            {ACCRUES: key},
        )
        return evolve(state, ledger=state.ledger.append(tx))

    @staticmethod
    def accrued(state: PortfolioState, key: str) -> Decimal:
        """What is accrued and open under ``key``: its accruals, less any closing (FUT-023)."""

        total = Decimal("0")
        for tx in state.ledger.transactions:
            if tx.type is TransactionType.ACCRUAL and tx.metadata.get(ACCRUES) == key:
                total += tx.quantity
        return total

    @staticmethod
    @in_accounting_context
    def close_accrual(
        state: PortfolioState, key: str, amount: Decimal, currency: str, timestamp: float
    ) -> PortfolioState:
        """Close ``key``'s accrual: an ``ACCRUAL`` of the negated sum, marked closed (FUT-023).

        What it closed is then posted, if anything, as an ordinary cash flow;
        the closing entry is what tells a bounded ledger the period is done.
        """

        tx = Transaction(
            str(new_id()),
            timestamp,
            state.account.account_id,
            TransactionType.ACCRUAL,
            "CASH",
            -amount,
            Decimal("1.0"),
            Decimal("0.0"),
            currency,
            {ACCRUES: key, ACCRUAL_CLOSED: True},
        )
        return evolve(state, ledger=state.ledger.append(tx))

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
    def apply_exchange(
        state: PortfolioState,
        asset_id: str,
        received: tuple[ExchangedHolding, ...],
        retained_basis: Decimal | None,
        consideration: Mapping[str, Decimal],
        realized: Decimal,
        timestamp: float,
        reference: str = "",
        *,
        pays_at: float | None = None,
        kept: tuple[Decimal, Decimal] | None = None,
        delivers: Mapping[str, tuple[float, Mapping[str, Decimal]]] | None = None,
        convention: DeliveryConvention | None = None,
        effective_at: float | None = None,
    ) -> PortfolioState:
        """Book a holding exchanged for others and cash, exactly as computed (ledger FUT-021).

        The caller -- the execution pipeline -- has apportioned the basis and
        priced every fraction by the exchange's declared terms; this books it:
        each received holding opened at the basis it carries, or added to one on
        the same side; the exchanged holding kept at ``retained_basis`` or closed;
        each currency's ``consideration`` moved as an ``EXCHANGE`` ledger
        transaction; ``realized`` -- in the exchanged holding's currency -- added
        to realized P&L; and one :class:`~alphalab.portfolio.events.PositionExchanged`
        recording all of it.

        ``pays_at``, when later than ``timestamp``, is when the cash moves (FUT-025,
        phase 3): each currency's consideration is then an entitlement -- booked
        at the effective instant, unsettled until ``pays_at`` -- which buying
        power and settled cash leave out until it is paid.

        ``kept`` is the part of the holding the exchange does not take -- its
        signed units and their basis -- when it takes only part (FUT-030, phase
        4): one alternative of an elective action applied to its electors' part
        of one position. It stays held as it was, beside a retained part.

        ``delivers`` names each received leg delivered after ``timestamp``
        (FUT-037, phase 5): its delivery instant and each strategy's part. The
        leg is the holder's entitlement from ``timestamp`` -- booked, valued and
        attributed -- and an ``EXCHANGE`` ledger transaction of its own records
        its units owed (:data:`~alphalab.portfolio.settlement.DELIVERS`), pending
        until the instant under ``convention``: by default neither sold nor
        counted as collateral until delivered. ``effective_at`` is the action's
        effective instant the deliveries are keyed by, ``timestamp`` unless given.

        Raises:
            InvalidTransactionError: If the account holds no position in
                ``asset_id``, or holds the opposite side of an instrument it
                would receive -- booking it would net two holdings at a basis
                no fill set.
        """

        held = state.book.get(asset_id)
        if held is None or held.quantity == 0:
            raise InvalidTransactionError(
                f"No position in {asset_id} to exchange; the run holds none."
            )
        units = state.account.currency_units
        positions = state.book
        for leg in received:
            existing = positions.get(leg.asset_id)
            basis = to_money(leg.basis, leg.currency, units)
            if existing is not None and existing.quantity != 0:
                if (existing.quantity > 0) != (leg.quantity > 0):
                    raise InvalidTransactionError(
                        f"The exchange delivers {leg.quantity} {leg.asset_id}, and the account "
                        f"holds {existing.quantity} of it: an exchange does not net opposite "
                        "holdings. Close the opposite position first."
                    )
                quantity = existing.quantity + leg.quantity
                combined = existing._rebased(
                    quantity, existing.basis + basis, existing.realized_pnl, existing.market_price,
                    timestamp,
                )  # fmt: skip
                positions = positions.set(leg.asset_id, combined)
                continue
            opened = Position(
                leg.asset_id,
                Decimal("0"),
                Decimal("0"),
                leg.price,
                ZERO_MONEY,
                leg.currency,
                timestamp,
                minor_units=units.minor_units(leg.currency),
                economics=leg.economics,
            )._rebased(leg.quantity, basis, ZERO_MONEY, leg.price, timestamp, opened_at=timestamp)
            positions = positions.set(leg.asset_id, opened)
        kept_units, kept_basis = (Decimal("0"), Decimal("0")) if kept is None else kept
        exchanged = held.quantity - kept_units
        if retained_basis is None and kept_units == 0:
            positions = positions.delete(asset_id)
        else:
            retained = (
                Decimal("0")
                if retained_basis is None
                else to_money(retained_basis, held.currency, units)
            )
            quantity = held.quantity if retained_basis is not None else kept_units
            positions = positions.set(
                asset_id,
                held._rebased(
                    quantity, kept_basis + retained, held.realized_pnl, held.market_price, timestamp
                ),
            )
        cash = state.cash
        ledger = state.ledger
        unsettled = state.unsettled
        deferred = pays_at is not None and pays_at > timestamp
        moved: dict[str, Decimal] = {}
        for currency, amount in sorted(consideration.items()):
            money = to_money(amount, currency, units)
            if money == 0:
                continue
            moved[currency] = money
            cash = cash.settle(money, currency)
            metadata: dict[str, Any] = {} if not reference else {"exchange": reference}
            if deferred:
                metadata = {**metadata, SETTLES_AT: pays_at, SETTLEMENT_CASH: str(money)}
            tx = Transaction(
                str(new_id()),
                timestamp,
                state.account.account_id,
                TransactionType.EXCHANGE,
                asset_id,
                money,
                Decimal("1.0"),
                Decimal("0.0"),
                currency,
                metadata,
            )
            ledger = ledger.append(tx)
            if deferred:
                assert pays_at is not None
                unsettled = settle_pending(
                    unsettled,
                    timestamp,
                    PendingSettlement(
                        tx.transaction_id, asset_id, currency, money, Decimal("0"), timestamp,
                        pays_at,
                    ),
                )  # fmt: skip
        key = entitlement_key(asset_id, timestamp if effective_at is None else effective_at)
        for leg in received:
            planned = None if delivers is None else delivers.get(leg.asset_id)
            if planned is None or planned[0] <= timestamp:
                continue
            when, parts = planned
            metadata = {
                SETTLES_AT: when,
                SETTLEMENT_CASH: "0",
                DELIVERS: str(leg.quantity),
                DELIVERY_OF: key,
                DELIVERY_PARTS: ";".join(f"{sid}={units}" for sid, units in sorted(parts.items())),
                DELIVERY_BASIS: str(to_money(leg.basis, leg.currency, units)),
                DELIVERY_TERMS: (convention or DeliveryConvention()).terms,
                **({"exchange": reference} if reference else {}),
            }
            tx = Transaction(
                str(new_id()), timestamp, state.account.account_id, TransactionType.EXCHANGE,
                leg.asset_id, Decimal("0"), Decimal("0"), Decimal("0.0"), leg.currency, metadata,
            )  # fmt: skip
            ledger = ledger.append(tx)
            unsettled = settle_pending(unsettled, timestamp, _delivery_entry(tx, when))
        gain = to_money(realized, held.currency, units)
        evt = PositionExchanged(
            timestamp,
            state.account.account_id,
            asset_id,
            exchanged,
            {leg.asset_id: leg.quantity for leg in received},
            moved,
            gain,
            retained_basis is not None,
            reference,
        )
        return evolve(
            state,
            positions=positions,
            cash=cash,
            ledger=ledger,
            unsettled=unsettled,
            events=state.events.append(evt),
            realized_pnl=state.realized_pnl
            if gain == 0
            else state.realized_pnl.add(gain, held.currency),
        )

    @staticmethod
    @in_accounting_context
    def amend_payment(
        state: PortfolioState,
        holding: str,
        effective_at: float,
        currency: str,
        change: PaymentChange,
        timestamp: float,
        *,
        units: Decimal | None = None,
        per_unit: Decimal | None = None,
        replacement_currency: str | None = None,
        pays_at: float | None = None,
        reference: str = "",
    ) -> PortfolioState:
        """Book an issuer's later word on a corporate action's payment (FUT-034, phase 4).

        The payment is the one ``currency``'s consideration of the exchange of
        ``holding`` effective at ``effective_at`` booked, with every amendment
        since; each amendment is a ledger transaction of its own, naming it, so
        the original is never rewritten:

        * ``DELAYED`` -- not paid yet: every part of it awaited is pending until
          ``pays_at``, though the clock had paid it; no cash moves.
        * ``FAILED`` -- will not be paid: what is owed is written off, out of
          trade-date cash and realized P&L, and none of it ever settles.
        * ``CORRECTED`` -- owed at ``per_unit`` for each of the ``units`` the
          holding was: the difference moves cash and realized P&L, pending with
          the rest of it or, once that is paid, until ``pays_at`` if later.
        * ``REVERSED`` -- paid, and clawed back: as a failure, of a payment made.
        * ``REPLACED`` -- paid instead in ``replacement_currency``, ``per_unit``
          for each of ``units``, at ``pays_at``: what was owed in ``currency``
          is written off, the replacement owed in its place.

        A book holding no payment of the action -- the run held none of it --
        is returned as it is.

        Raises:
            InvalidTransactionError: If the action paid nothing in ``currency``
                though it paid in another, the payment failed or was reversed
                already, a reversal names one not yet paid, a delay names an
                instant not after ``timestamp`` or a payment owing nothing, or a
                correction or replacement lacks what it needs.
        """

        ledger_txs = state.ledger.transactions
        chain = payment_chain(ledger_txs, holding, effective_at, currency)
        if not chain:
            if any(
                payment_chain(ledger_txs, holding, effective_at, c) for c in state.cash.balances
            ):
                raise InvalidTransactionError(
                    f"The action on {holding} at {effective_at!r} paid nothing in {currency}."
                )
            return state
        done = [t for t in chain if t.metadata.get(PAYMENT_CHANGE) in _TERMINAL_CHANGES]
        if done:
            raise InvalidTransactionError(
                f"The payment of {holding}'s action in {currency} was "
                f"{str(done[0].metadata[PAYMENT_CHANGE]).lower()} at {done[0].timestamp!r}; "
                "nothing amends it after."
            )
        units_of = state.account.currency_units
        owed = sum((t.quantity for t in chain), Decimal("0"))
        ids = {t.transaction_id for t in chain}
        waiting = [
            e for e in state.unsettled if e.transaction_id in ids and e.pending_at(timestamp)
        ]
        key = entitlement_key(holding, effective_at)
        name = change.name

        def booked(money: Decimal, in_currency: str, extra: Mapping[str, Any]) -> Transaction:
            metadata: dict[str, Any] = {ENTITLEMENT: key, PAYMENT_CHANGE: name, **extra}
            if reference:
                metadata["exchange"] = reference
            return Transaction(
                str(new_id()), timestamp, state.account.account_id, TransactionType.EXCHANGE,
                holding, money, Decimal("1.0"), Decimal("0.0"), in_currency, metadata,
            )  # fmt: skip

        cash, ledger, unsettled = state.cash, state.ledger, state.unsettled
        realized = state.realized_pnl
        owed_after, settles, replacement, replaced_in = owed, None, Decimal("0"), ""
        by_id = {t.transaction_id: t for t in chain}
        if change is PaymentChange.DELAYED:
            if pays_at is None or pays_at <= timestamp:
                raise InvalidTransactionError(
                    f"A delayed payment is paid after the delay is known ({timestamp!r}), got "
                    f"{pays_at!r}."
                )
            if owed == 0:
                raise InvalidTransactionError(f"{holding}'s payment in {currency} owes nothing.")
            awaited = [t.transaction_id for t in chain if t.quantity != 0]
            tx = booked(Decimal("0"), currency, {AFFECTS: ",".join(awaited), SETTLES_AT: pays_at})
            ledger = ledger.append(tx)
            unsettled = amend_pending(settle_pending(unsettled, timestamp), tx, by_id)
            settles = pays_at
        elif change in (PaymentChange.FAILED, PaymentChange.REVERSED):
            if change is PaymentChange.REVERSED and waiting:
                raise InvalidTransactionError(
                    f"{holding}'s payment in {currency} is not paid until "
                    f"{max(e.settles_at for e in waiting)!r}: what is not paid fails, and is "
                    "not reversed."
                )
            tx = booked(-owed, currency, {AFFECTS: ",".join(t.transaction_id for t in chain)})
            ledger = ledger.append(tx)
            unsettled = amend_pending(unsettled, tx, by_id)
            cash = cash.settle(-owed, currency)
            realized = realized.add(-owed, currency)
            owed_after = Decimal("0")
        else:
            if units is None or per_unit is None:
                raise InvalidTransactionError(
                    f"A {name.lower()} payment states the units exchanged and what each is paid."
                )
            if change is PaymentChange.CORRECTED:
                owed_after = to_money(units * per_unit, currency, units_of)
                diff = owed_after - owed
                later = pays_at if pays_at is not None and pays_at > timestamp else None
                settles = max((e.settles_at for e in waiting), default=later)
                extra: dict[str, Any] = {"per_unit": str(per_unit)}
                if settles is not None:
                    extra = {**extra, SETTLES_AT: settles, SETTLEMENT_CASH: str(diff)}
                tx = booked(diff, currency, extra)
                ledger = ledger.append(tx)
                if settles is not None:
                    unsettled = settle_pending(
                        unsettled,
                        timestamp,
                        PendingSettlement(
                            tx.transaction_id, holding, currency, diff, Decimal("0"), timestamp,
                            settles,
                        ),
                    )  # fmt: skip
                cash = cash.settle(diff, currency)
                realized = realized.add(diff, currency)
            else:
                if replacement_currency is None or replacement_currency == currency:
                    raise InvalidTransactionError(
                        "A replaced payment names the other currency it is paid in."
                    )
                out = booked(-owed, currency, {AFFECTS: ",".join(t.transaction_id for t in chain)})
                ledger = ledger.append(out)
                unsettled = amend_pending(unsettled, out, by_id)
                cash = cash.settle(-owed, currency)
                realized = realized.add(-owed, currency)
                owed_after = Decimal("0")
                replacement = to_money(units * per_unit, replacement_currency, units_of)
                replaced_in = replacement_currency
                later = pays_at if pays_at is not None and pays_at > timestamp else None
                extra = {"per_unit": str(per_unit)}
                if later is not None:
                    extra = {**extra, SETTLES_AT: later, SETTLEMENT_CASH: str(replacement)}
                into = booked(replacement, replacement_currency, extra)
                ledger = ledger.append(into)
                if later is not None:
                    unsettled = settle_pending(
                        unsettled,
                        timestamp,
                        PendingSettlement(
                            into.transaction_id, holding, replacement_currency, replacement,
                            Decimal("0"), timestamp, later,
                        ),
                    )  # fmt: skip
                cash = cash.settle(replacement, replacement_currency)
                realized = realized.add(replacement, replacement_currency)
                settles = later
        evt = PaymentAmended(
            timestamp, state.account.account_id, holding, effective_at, currency, name, owed,
            owed_after, settles, replaced_in, replacement, reference,
        )  # fmt: skip
        return evolve(
            state,
            cash=cash,
            ledger=ledger,
            unsettled=unsettled,
            realized_pnl=realized,
            events=state.events.append(evt),
        )

    @staticmethod
    @in_accounting_context
    def amend_delivery(
        state: PortfolioState,
        holding: str,
        effective_at: float,
        asset_id: str,
        change: PaymentChange,
        timestamp: float,
        *,
        delivers_at: float | None = None,
        units: Decimal | None = None,
        reference: str = "",
    ) -> tuple[PortfolioState, Mapping[str, Decimal]]:
        """Book a later word on units an exchange delivers after its instant (FUT-037, phase 5).

        The delivery is ``asset_id``'s leg of the exchange of ``holding``
        effective at ``effective_at``; each amendment is a ledger transaction of
        its own, naming it, so the original stays as booked:

        * ``DELAYED`` -- delivered at ``delivers_at`` instead, after ``timestamp``;
          the units are owed until then, though the clock had delivered them.
        * ``CORRECTED`` -- ``units`` delivered, fewer than owed: the shortfall
          and its share of the basis are written off, out of the position and
          realized P&L, and the rest is owed as before.
        * ``FAILED`` -- not delivered: every unit owed and its basis written off,
          none of it ever held.

        Returns the book and each strategy's units written off, by its part of
        the delivery, for the allocation ledger to follow. A book owed no such
        delivery -- the run held none of the action -- is returned as it is.

        Raises:
            InvalidTransactionError: If the delivery failed already, a delay names
                no later instant, a correction names no fewer units than owed, or
                the change is not one a delivery takes.
        """

        key = entitlement_key(holding, effective_at)
        chain = [
            t
            for t in state.ledger.transactions
            if t.type is TransactionType.EXCHANGE
            and t.asset_id == asset_id
            and t.metadata.get(DELIVERY_OF) == key
        ]
        if not chain:
            return state, {}
        if any(t.metadata.get(PAYMENT_CHANGE) == PaymentChange.FAILED.name for t in chain):
            raise InvalidTransactionError(
                f"The delivery of {asset_id} for {holding}'s action failed already; nothing "
                "amends it after."
            )
        owed = sum(
            (Decimal(str(t.metadata[DELIVERS])) for t in chain if DELIVERS in t.metadata),
            Decimal("0"),
        )
        basis = sum(
            (
                Decimal(str(t.metadata[DELIVERY_BASIS]))
                for t in chain
                if DELIVERY_BASIS in t.metadata
            ),
            Decimal("0"),
        )
        original = next(t for t in chain if DELIVERY_PARTS in t.metadata)
        parts = _parts_of(original)
        by_id = {t.transaction_id: t for t in chain}
        unit_ids = [t.transaction_id for t in chain if DELIVERS in t.metadata]
        name = change.name

        def booked(extra: Mapping[str, Any]) -> Transaction:
            metadata: dict[str, Any] = {DELIVERY_OF: key, PAYMENT_CHANGE: name, **extra}
            if reference:
                metadata["exchange"] = reference
            return Transaction(
                str(new_id()), timestamp, state.account.account_id, TransactionType.EXCHANGE,
                asset_id, Decimal("0"), Decimal("0"), Decimal("0.0"), original.currency, metadata,
            )  # fmt: skip

        ledger, unsettled = state.ledger, state.unsettled
        written_off = Decimal("0")
        if change is PaymentChange.DELAYED:
            if delivers_at is None or delivers_at <= timestamp:
                raise InvalidTransactionError(
                    f"A delayed delivery arrives after the delay is known ({timestamp!r}), got "
                    f"{delivers_at!r}."
                )
            tx = booked({AFFECTS: ",".join(unit_ids), SETTLES_AT: delivers_at})
            ledger = ledger.append(tx)
            unsettled = amend_pending(settle_pending(unsettled, timestamp), tx, by_id)
            settles: float | None = delivers_at
        elif change is PaymentChange.CORRECTED:
            if (
                units is None
                or units.copy_abs() >= owed.copy_abs()
                or (units != 0 and (units > 0) != (owed > 0))
            ):
                raise InvalidTransactionError(
                    f"A corrected delivery delivers fewer than the {owed} owed, got {units!r}."
                )
            written_off = owed - units
            waiting = [e for e in state.unsettled if e.transaction_id in unit_ids]
            settles = max((e.settles_at for e in waiting), default=timestamp)
            tx = booked(
                {
                    DELIVERS: str(-written_off),
                    DELIVERY_TERMS: str(original.metadata.get(DELIVERY_TERMS, "")),
                    DELIVERY_BASIS: str(-_share_of(basis, written_off, owed)),
                    SETTLES_AT: settles,
                    SETTLEMENT_CASH: "0",
                }
            )
            ledger = ledger.append(tx)
            if settles > timestamp:
                unsettled = settle_pending(unsettled, timestamp, _delivery_entry(tx, settles))
        elif change is PaymentChange.FAILED:
            written_off = owed
            tx = booked({AFFECTS: ",".join(unit_ids)})
            ledger = ledger.append(tx)
            unsettled = amend_pending(unsettled, tx, by_id)
            settles = None
        else:
            raise InvalidTransactionError(
                f"A delivery is delayed, corrected or failed, not {name}."
            )
        positions, realized = state.book, state.realized_pnl
        if written_off:
            position = positions.get(asset_id)
            lost = _share_of(basis, written_off, owed)
            if position is not None:
                remaining = position.quantity - written_off
                if remaining == 0:
                    positions = positions.delete(asset_id)
                else:
                    positions = positions.set(
                        asset_id,
                        position._rebased(
                            remaining, position.basis - lost, position.realized_pnl,
                            position.market_price, timestamp,
                        ),
                    )  # fmt: skip
            realized = realized.add(-lost, original.currency)
        evt = DeliveryAmended(
            timestamp, state.account.account_id, holding, effective_at, asset_id, name, owed,
            owed - written_off, settles, reference,
        )  # fmt: skip
        lost_parts = {
            sid: (part * written_off / owed if owed else Decimal("0"))
            for sid, part in parts.items()
        }
        return (
            evolve(
                state,
                positions=positions,
                ledger=ledger,
                unsettled=unsettled,
                realized_pnl=realized,
                events=state.events.append(evt),
            ),
            {sid: part for sid, part in lost_parts.items() if part},
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


def _delivery_entry(transaction: Transaction, settles_at: float) -> PendingSettlement:
    """An exchange leg's units owed until ``settles_at`` (FUT-037)."""

    return PendingSettlement(
        transaction.transaction_id,
        transaction.asset_id,
        transaction.currency,
        Decimal("0"),
        Decimal(str(transaction.metadata[DELIVERS])),
        transaction.timestamp,
        settles_at,
        str(transaction.metadata.get(DELIVERY_TERMS, "")),
    )


def _parts_of(transaction: Transaction) -> dict[str, Decimal]:
    """Each strategy's part of a delivery, as its ledger transaction records it."""

    text = str(transaction.metadata.get(DELIVERY_PARTS, ""))
    parts = {}
    for item in text.split(";") if text else ():
        sid, _, units = item.rpartition("=")
        parts[sid] = Decimal(units)
    return parts


def _share_of(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    return Decimal("0") if whole == 0 else total * part / whole


#: The payment changes after which nothing more amends a payment (FUT-034).
_TERMINAL_CHANGES: Final = frozenset({PaymentChange.FAILED.name, PaymentChange.REVERSED.name})

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
