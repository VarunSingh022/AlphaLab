from alphalab.core import AssetType, EventType, OrderStatus, OrderType, Side, TimeInForce


def test_str_enum_values_are_stable_strings() -> None:
    assert Side.BUY.value == "buy"
    assert OrderType.STOP_LIMIT.value == "stop_limit"
    assert AssetType.EQUITY.value == "equity"
    assert EventType.PORTFOLIO.value == "portfolio"
    assert TimeInForce.GTC.value == "good_til_cancelled"
    assert OrderStatus.PENDING.value == "pending"


def test_str_enums_compare_to_their_values() -> None:
    assert isinstance(Side.SELL, str)
    assert str(Side.SELL) == "sell"
    assert str(OrderType.MARKET) == "market"
    assert str(AssetType.CRYPTO) == "crypto"
    assert str(OrderStatus.FILLED) == "filled"
