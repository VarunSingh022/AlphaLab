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
