from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    timestamp: float
    account_id: str


@dataclass(frozen=True, slots=True)
class CashDeposited(PortfolioEvent):
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CashWithdrawn(PortfolioEvent):
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class PositionOpened(PortfolioEvent):
    asset_id: str
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PositionIncreased(PortfolioEvent):
    asset_id: str
    added_quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PositionReduced(PortfolioEvent):
    asset_id: str
    reduced_quantity: Decimal
    price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PositionClosed(PortfolioEvent):
    asset_id: str
    price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class MarketValueUpdated(PortfolioEvent):
    prices: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class PortfolioValuationUpdated(PortfolioEvent):
    nav: Decimal


@dataclass(frozen=True, slots=True)
class CashConverted(PortfolioEvent):
    """Cash moved from one settlement currency to another at a stated rate.

    New in v2.17. A multi-currency run must be able to fund a currency it settles
    in out of one it holds, and doing that as a bare withdrawal plus a bare
    deposit would record two unrelated movements of unexplained size -- the
    reader could see that 1,000 USD left and 909.09 EUR arrived, and nothing
    would say those were the same act or what rate joined them.

    Every field a conversion needs to stay attributable is therefore on the
    event: both currencies, both amounts, the rate, when that rate was true, and
    where it came from. That is ADR-0020's requirement applied to settlement
    rather than to valuation.

    Attributes:
        amount: What left ``from_currency``.
        from_currency: The currency debited.
        converted: What arrived in ``to_currency``, rounded to its minor unit.
        to_currency: The currency credited.
        rate: How much ``to_currency`` one unit of ``from_currency`` bought.
        rate_as_of: When that rate was true, in Unix seconds.
        rate_source: Where the rate came from. Never empty -- an
            :class:`~alphalab.portfolio.fx.FxRate` refuses to exist without one.
        rate_derived: Whether the rate was computed rather than quoted.
    """

    amount: Decimal
    from_currency: str
    converted: Decimal
    to_currency: str
    rate: Decimal
    rate_as_of: float
    rate_source: str
    rate_derived: bool = False


@dataclass(frozen=True, slots=True)
class VariationSettled(PortfolioEvent):
    """A position whose gains settle as cash was settled at a price (ledger ACC-005).

    Attributes:
        asset_id: The position.
        price: The price it was settled at, and is carried at from now on.
        amount: The cash the settlement moved, signed: a gain received is
            positive, a loss paid negative. Realized P&L.
        currency: What ``amount`` is in -- the position's currency.
    """

    asset_id: str
    price: Decimal
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CashFlowBooked(PortfolioEvent):
    """Cash a holding earned or cost without a trade (ledger ACC-006).

    Attributes:
        kind: A :class:`~alphalab.portfolio.corporate_actions.CashFlowKind` value.
        asset_id: The holding it arose from, or ``""`` for the account itself.
        amount: Signed: received is positive, paid negative.
        currency: What ``amount`` is in.
        reference: The caller's reference for it -- a dividend's declaration,
            a funding interval. Free text, not interpreted.
    """

    kind: str
    asset_id: str
    amount: Decimal
    currency: str
    reference: str


@dataclass(frozen=True, slots=True)
class PositionExchanged(PortfolioEvent):
    """A holding exchanged by its issuer for other instruments and cash (ledger FUT-021).

    What a reader needs to see why a holding became another: what was held, what
    it became, what cash moved and what of it was realized.

    Attributes:
        asset_id: The holding exchanged.
        quantity: Its signed quantity before.
        received: ``asset_id`` -> the signed quantity received of each leg.
        consideration: Currency -> the signed cash it moved: the cash part and
            any cash in lieu; received positive, paid negative.
        realized_pnl: The P&L the cash realized, in the holding's currency.
        retained: Whether the holding stays: a spin-off or a rights issue.
        reference: The issuer's reference.
    """

    asset_id: str
    quantity: Decimal
    received: Mapping[str, Decimal]
    consideration: Mapping[str, Decimal]
    realized_pnl: Decimal
    retained: bool
    reference: str


@dataclass(frozen=True, slots=True)
class PaymentAmended(PortfolioEvent):
    """A corporate action's payment amended by the issuer's later word (FUT-034, phase 4).

    Attributes:
        asset_id: The holding the action exchanged.
        effective_at: The action's effective instant.
        currency: The payment's currency.
        change: What changed, by :class:`~alphalab.portfolio.settlement.PaymentChange` name.
        owed_before: What was owed in ``currency`` before, signed.
        owed_after: What is owed in it after.
        settles_at: When what is owed now is paid, or ``None`` if not pending.
        replacement_currency: The currency a replacement pays in, or ``""``.
        replacement: What the replacement pays in it.
        reference: The issuer's reference.
    """

    asset_id: str
    effective_at: float
    currency: str
    change: str
    owed_before: Decimal
    owed_after: Decimal
    settles_at: float | None
    replacement_currency: str
    replacement: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class PositionSplit(PortfolioEvent):
    """A split or stock dividend changed a position's quantity, not its value (ACC-006).

    Attributes:
        asset_id: The position.
        ratio: Units after for each unit before: ``2`` for a two-for-one split,
            ``0.1`` for a one-for-ten reverse split, ``1.05`` for a 5% stock
            dividend.
        quantity: The position's quantity after.
    """

    asset_id: str
    ratio: Decimal
    quantity: Decimal
