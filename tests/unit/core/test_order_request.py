"""Tests for the canonical alphalab.core.order_request.OrderRequest DTO."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.core import OrderRequest as OrderRequestFromPackage
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest


def _request(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "order_id": "ORD-1",
        "strategy_id": "STRAT-1",
        "asset_id": "AAPL",
        "side": Side.BUY,
        "quantity": Decimal("10"),
        "price": Decimal("150.00"),
    }
    kwargs.update(overrides)
    return OrderRequest(**kwargs)  # type: ignore[arg-type]


def test_core_package_reexports_the_same_class() -> None:
    assert OrderRequestFromPackage is OrderRequest
    assert "OrderRequest" in __import__("alphalab.core", fromlist=["__all__"]).__all__


def test_side_field_is_the_canonical_core_side() -> None:
    request = _request(side=Side.SELL)
    assert request.side is Side.SELL
    assert type(request.side) is Side


def test_timestamp_defaults_to_zero() -> None:
    assert _request().timestamp == 0.0
    assert _request(timestamp=12.5).timestamp == 12.5


def test_notional_value_is_quantity_times_price_quantized() -> None:
    request = _request(quantity=Decimal("3"), price=Decimal("100.005"))
    assert request.notional_value == Decimal("300.0150")


def test_positional_construction_matches_the_former_risk_signature() -> None:
    # risk.models.OrderRequest was constructed positionally as
    # (order_id, strategy_id, asset_id, side, quantity, price) -- still valid.
    request = OrderRequest("O1", "S1", "AAPL", Side.BUY, Decimal("-10"), Decimal("100"))
    assert request.side is Side.BUY
    assert request.timestamp == 0.0


def test_is_frozen() -> None:
    request = _request()
    with pytest.raises(FrozenInstanceError):
        request.quantity = Decimal("1")  # type: ignore[misc]
