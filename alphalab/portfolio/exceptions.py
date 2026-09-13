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

    This is the absence of a **rate**, not a rule that foreign-currency
    instruments are invalid. :class:`~alphalab.portfolio.cash.CashLedger` is
    already multi-currency and :class:`~alphalab.portfolio.position.Position`
    already declares its own currency, so holding and booking in a foreign
    currency has always been supported. Only *aggregating* two currencies into
    one number needs a rate.

    From v2.16 there is somewhere to supply one, so this error distinguishes two
    situations and the message says which it is:

    * **No rates were supplied at all.** Pass an
      :class:`~alphalab.portfolio.fx.FxRates` table covering the currencies the
      book holds, value each currency separately, or ask for a base currency the
      whole book is denominated in.
    * **Rates were supplied and this pair is not among them.** AlphaLab does not
      triangulate or invert a rate it was not given -- supply the pair, or call
      :meth:`~alphalab.portfolio.fx.FxRates.with_inverses` if the opposite
      direction is an acceptable derivation.

    A rate that exists but is *too old* raises
    :class:`~alphalab.portfolio.fx.StaleRateError` instead, because that calls
    for refreshing a feed rather than supplying a pair.
    """
