from enum import Enum, auto


class TransactionType(Enum):
    BUY = auto()
    SELL = auto()
    DIVIDEND = auto()
    DEPOSIT = auto()
    WITHDRAWAL = auto()
    FEE = auto()
    INTEREST = auto()
    TRANSFER = auto()


class PositionSide(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()
