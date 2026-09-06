"""Domain exceptions for the Market Data Engine."""

from alphalab.common.exceptions import AlphaLabError


class MarketDataError(AlphaLabError):
    """Base exception for all Market Data Engine errors."""

    pass


class MarketValidationError(MarketDataError):
    """Raised when market data fails business invariant validation."""

    pass


class UnsupportedRecordError(MarketDataError):
    """Raised when a record carries a market input nothing can publish.

    Defined here rather than in :mod:`alphalab.backtesting`, where it started:
    four environments now publish records through one canonical step, so an
    unpublishable input is a market-layer fact, not a backtest-specific one.
    ``alphalab.backtesting.exceptions.UnsupportedRecordError`` is this class.
    """

    pass


class InstrumentResolutionError(MarketValidationError):
    """Raised when a provider symbol does not name a registered instrument.

    This is the refusal ADR-0016 moves to the front of the path. Until v2.7 an
    unregistered symbol became an ``asset_id`` verbatim and was refused by
    :class:`alphalab.core.Fill` at the *last* stage, after market data,
    strategy, allocation and risk had all succeeded -- so an identity problem
    surfaced as a domain-validation failure deep in execution, naming neither
    the provider nor the symbol that caused it.

    It lives here, with the rest of the wire boundary's vocabulary, rather than
    in :mod:`alphalab.instrument`: that package answers ``None`` for an
    unregistered pair and never imports the market layer. Only the boundary
    knows which record was being normalized, so only the boundary can say so.
    """
