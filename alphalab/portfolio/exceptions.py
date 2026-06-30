class PortfolioError(Exception):
    """Base exception for portfolio engine errors."""


class InsufficientFundsError(PortfolioError):
    """Raised when an operation exceeds available cash."""


class InvalidTransactionError(PortfolioError):
    """Raised when a transaction is malformed or invalid."""


class PositionNotFoundError(PortfolioError):
    """Raised when operating on a non-existent position."""
