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
    #: v4.2.0 (FUT-021): cash a holding exchange paid or cost -- its cash part
    #: and any cash in lieu of a fraction.
    EXCHANGE = auto()
    #: v4.2.0, phase 3 (FUT-023): interest or a fee accrued for one day and
    #: not yet posted -- moving no cash; its period posts the sum once.
    ACCRUAL = auto()


class PositionSide(Enum):
    LONG = auto()
    SHORT = auto()
    FLAT = auto()
