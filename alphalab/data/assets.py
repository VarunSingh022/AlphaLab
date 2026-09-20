"""What kind of instrument a dataset describes, without flattening the differences.

An equity price series and a futures price series are not the same object with
a different label on it. One is a price per share; the other is a price per
contract that must be multiplied by a contract size to mean anything in money,
that stops existing on an expiry date, and that a research pipeline has to
stitch across rolls before it is a continuous series at all.

Forcing both into one shape is how a backtest silently computes P&L that is
1,000x wrong. So this module declares one spec per asset class, each carrying
exactly the fields that class needs and none it does not, and
:data:`InstrumentSpec` is the union of them.

The wire/domain split, applied to instruments
---------------------------------------------

These specs are **wire-layer descriptions**: ``float``, keyed by provider
``symbol``, cheap for a provider to fill in. That is the same split
:mod:`alphalab.data.feed` already draws between a wire ``Bar`` and
:class:`alphalab.market.bar.Bar`, and it is drawn here for the same reason.

:class:`alphalab.futures.contract.FutureContract` and
:class:`alphalab.options.contract.OptionContract` are the **domain**
counterparts: ``Decimal``, keyed by ``asset_id``, and bridged to
:class:`~alphalab.portfolio.position.Position` so a contract can be *held*.
A :class:`FutureSpec` says what a price series is about; a ``FutureContract``
opens a position in it. **v3.4 joined them**, and put the join in
:mod:`alphalab.api` rather than here: ``future_contract_from_spec`` and
``option_contract_from_spec`` lift a spec into the domain, and
``convention_from_spec`` builds the
:class:`~alphalab.conventions.market.MarketConvention` that says what the
numbers mean. A joining layer belongs *above* the things it joins, which is why
this module still imports neither ``alphalab.futures`` nor
``alphalab.portfolio``; ``tests/regression/test_shared_names_stay_distinct.py``
records why they remain two types.

``OptionType`` is *not* redefined
---------------------------------

Call versus put is the same fact in both layers -- there is no ``float`` /
``Decimal`` distinction in an enum -- so :class:`OptionSpec` imports
:class:`alphalab.options.enums.OptionType` rather than spelling it a third
time. ``alphalab/common/types.py`` records the lesson this follows: the
Order/Side/Status fragmentation across ``broker``/``brokers``/``oms``/
``execution`` was learned the hard way, and one more copy of CALL/PUT is how
that starts again. The import is a leaf -- ``options.enums`` imports nothing
but ``enum`` -- so it carries no pricing engine behind it.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.data.exceptions import DataValidationError
from alphalab.data.symbols import DataAssetClass
from alphalab.options.enums import ExerciseStyle, OptionType

# Re-exported, not redefined. Listed in ``__all__`` so that a caller building an
# OptionSpec reaches for the one spelling of call/put AlphaLab has, rather than
# importing the options engine or -- worse -- declaring a third enum.

__all__ = [
    "CommoditySpec",
    "CryptoSpec",
    "EquitySpec",
    "ExerciseStyle",
    "FutureSpec",
    "FxSpec",
    "IndexSpec",
    "InstrumentSpec",
    "OptionSpec",
    "OptionType",
    "RateSpec",
    "asset_class_of",
]


def _require(value: str, field_name: str, symbol: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise DataValidationError(f"{symbol or '<unnamed>'}: {field_name} must be named.")
    return stripped


@dataclass(frozen=True, slots=True)
class EquitySpec:
    """A share in a company, or a fund that trades like one.

    The simplest case, and the one every other spec is a departure from: one
    unit is one share, priced in one currency, on one listing venue. No
    multiplier, because it is always one; no expiry, because it has none.

    ``is_fund`` distinguishes an ETF from an ordinary listing. It is one flag
    rather than a separate spec because the two are identical in every respect
    this layer cares about -- the difference is what the issuer does, not how a
    price series is read.
    """

    symbol: str
    currency: str
    exchange: str
    is_fund: bool = False

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.exchange, "exchange", self.symbol)


@dataclass(frozen=True, slots=True)
class IndexSpec:
    """A published level, which nobody trades and everybody references.

    Carries no exchange and no multiplier because neither applies: an index is
    calculated, not listed. ``currency`` is the currency its constituents are
    valued in, which a level does not have units of but a return attribution
    needs to know.
    """

    symbol: str
    currency: str
    publisher: str

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.publisher, "publisher", self.symbol)


@dataclass(frozen=True, slots=True)
class FutureSpec:
    """One delivery month of a futures contract.

    Attributes:
        symbol: The provider's name for this contract month.
        currency: Settlement currency.
        exchange: Listing venue.
        root: The contract root, e.g. ``"CL"``. What a continuous series is
            built *of*, and what distinguishes two months of the same contract
            from two different contracts.
        expiry: Unix seconds of the last trading day. The fact that makes a
            futures series finite, and the one a roll schedule is built from.
        multiplier: Units of the underlying per contract. A price series is
            not money until it is multiplied by this.
        tick_size: Minimum price increment.
        contract_month: Unix seconds within the delivery month. Distinct from
            ``expiry`` because they are different dates and a roll calendar
            keys on the month.
    """

    symbol: str
    currency: str
    exchange: str
    root: str
    expiry: float
    multiplier: float
    tick_size: float
    contract_month: float | None = None

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.exchange, "exchange", self.symbol)
        _require(self.root, "root", self.symbol)
        if self.multiplier <= 0.0:
            raise DataValidationError(
                f"{self.symbol}: multiplier must be positive, got {self.multiplier!r}; a "
                "contract controlling nothing has no price series worth reading."
            )
        if self.tick_size <= 0.0:
            raise DataValidationError(
                f"{self.symbol}: tick_size must be positive, got {self.tick_size!r}."
            )
        if self.contract_month is not None and self.expiry < self.contract_month:
            raise DataValidationError(
                f"{self.symbol}: expiry {self.expiry!r} precedes contract month "
                f"{self.contract_month!r}."
            )


@dataclass(frozen=True, slots=True)
class OptionSpec:
    """One option contract: a strike, an expiry, and a right.

    Attributes:
        symbol: The provider's name for this contract.
        currency: Premium currency.
        exchange: Listing venue.
        underlying_symbol: What the option is on.
        strike: Exercise price per unit of the underlying.
        expiry: Unix seconds of expiration.
        option_type: Call or put, from :mod:`alphalab.options.enums`.
        multiplier: Units of the underlying per contract. 100 for a standard
            US equity option, 50 for a Nifty option, 10 for a Eurostoxx one.
        style: When the contract may be exercised. **Required** as of v3.4,
            having defaulted to
            :attr:`~alphalab.options.enums.ExerciseStyle.AMERICAN` -- the US
            single-stock convention, which index options on the same exchange
            do not follow. The domain counterpart
            :class:`alphalab.options.contract.OptionContract` made the same
            field required in the same release.
    """

    symbol: str
    currency: str
    exchange: str
    underlying_symbol: str
    strike: float
    expiry: float
    option_type: OptionType
    multiplier: float
    style: ExerciseStyle

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.exchange, "exchange", self.symbol)
        _require(self.underlying_symbol, "underlying_symbol", self.symbol)
        if self.strike <= 0.0:
            raise DataValidationError(
                f"{self.symbol}: strike must be positive, got {self.strike!r}."
            )
        if self.multiplier <= 0.0:
            raise DataValidationError(
                f"{self.symbol}: multiplier must be positive, got {self.multiplier!r}."
            )


@dataclass(frozen=True, slots=True)
class FxSpec:
    """A currency pair, which has no single currency of its own.

    This is the spec that most obviously refuses the equity shape: the price of
    ``EURUSD`` is not "in" a currency, it is a ratio *between* two, and a spec
    with one ``currency`` field would have to pick one and be wrong about the
    other. ``base``/``quote`` is the only honest rendering.
    """

    symbol: str
    base_currency: str
    quote_currency: str

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        base = _require(self.base_currency, "base_currency", self.symbol)
        quote = _require(self.quote_currency, "quote_currency", self.symbol)
        if base.upper() == quote.upper():
            raise DataValidationError(
                f"{self.symbol}: base and quote are both {base!r}; a pair of one currency "
                "with itself has no rate."
            )


@dataclass(frozen=True, slots=True)
class CryptoSpec:
    """A pair on one venue, where the venue is part of the identity.

    Unlike a listed equity, the *same* pair on two venues is genuinely two
    price series with two order books and two prices at the same instant, and
    neither is the reference. ``venue`` is therefore required rather than
    descriptive: a crypto series that does not say where it traded cannot be
    reconciled against anything.
    """

    symbol: str
    venue: str
    base_asset: str
    quote_asset: str

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.venue, "venue", self.symbol)
        _require(self.base_asset, "base_asset", self.symbol)
        _require(self.quote_asset, "quote_asset", self.symbol)


@dataclass(frozen=True, slots=True)
class RateSpec:
    """An interest rate, quoted in percent rather than money.

    ``quoted_in_percent`` is explicit because the alternative convention --
    decimal fractions -- is equally common, the two differ by 100x, and no
    amount of looking at the numbers reliably distinguishes 5.0% from 500%.

    ``tenor_days`` and ``day_count`` are the rate-specific semantics: a
    3-month rate and an overnight rate are different series, and a rate means
    nothing without the convention used to accrue it.
    """

    symbol: str
    currency: str
    tenor_days: int
    day_count: str
    quoted_in_percent: bool

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.day_count, "day_count", self.symbol)
        if self.tenor_days <= 0:
            raise DataValidationError(
                f"{self.symbol}: tenor_days must be positive, got {self.tenor_days!r}."
            )


@dataclass(frozen=True, slots=True)
class CommoditySpec:
    """A physical commodity series, which may have no contract behind it.

    A spot assessment is published by a price reporting agency against a stated
    delivery location and unit, and is not a traded instrument. ``unit`` and
    ``delivery_location`` are what make two "crude oil" series comparable or
    not.
    """

    symbol: str
    currency: str
    unit: str
    delivery_location: str

    def __post_init__(self) -> None:
        _require(self.symbol, "symbol", self.symbol)
        _require(self.currency, "currency", self.symbol)
        _require(self.unit, "unit", self.symbol)
        _require(self.delivery_location, "delivery_location", self.symbol)


#: Every shape a dataset's instrument description can take.
type InstrumentSpec = (
    EquitySpec
    | IndexSpec
    | FutureSpec
    | OptionSpec
    | FxSpec
    | CryptoSpec
    | RateSpec
    | CommoditySpec
)


def asset_class_of(spec: InstrumentSpec) -> DataAssetClass:
    """The asset class a spec describes.

    Exhaustive by construction: a new spec added to the union without an entry
    here fails type checking at the ``match``, which is the point of using one.
    """

    match spec:
        case EquitySpec():
            return DataAssetClass.ETF if spec.is_fund else DataAssetClass.EQUITY
        case IndexSpec():
            return DataAssetClass.INDEX
        case FutureSpec():
            return DataAssetClass.FUTURE
        case OptionSpec():
            return DataAssetClass.OPTION
        case FxSpec():
            return DataAssetClass.FOREX
        case CryptoSpec():
            return DataAssetClass.CRYPTO
        case RateSpec():
            return DataAssetClass.RATE
        case CommoditySpec():
            return DataAssetClass.COMMODITY
