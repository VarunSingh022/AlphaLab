from alphalab.common.exceptions import AlphaLabError


class PortfolioError(AlphaLabError):
    """Base exception for portfolio engine errors."""


class InsufficientFundsError(PortfolioError):
    """Raised when an operation exceeds available cash."""


class InvalidTransactionError(PortfolioError):
    """Raised when a transaction is malformed or invalid."""


class PositionNotFoundError(PortfolioError):
    """Raised when operating on a non-existent position."""


class MixedCurrencyValuationError(PortfolioError):
    """Raised when a book cannot be valued as one figure in one currency.

    This is the absence of an FX rate source, not a rule that foreign-currency
    instruments are invalid. :class:`~alphalab.portfolio.cash.CashLedger` is
    already multi-currency and :class:`~alphalab.portfolio.position.Position`
    already declares its own currency, so holding and booking in a foreign
    currency is supported. Only *aggregating* two currencies into one number is
    not, and there is no honest answer to give without a rate.
    """
