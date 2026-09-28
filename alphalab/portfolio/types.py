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
    #: v3.11 (ACC-005, ACC-006): a settlement of a position whose gains settle
    #: as cash, and a perpetual's funding payment.
    VARIATION_MARGIN = auto()
    FUNDING = auto()


class PositionSide(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()
