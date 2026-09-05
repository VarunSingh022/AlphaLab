"""Unit tests for the execution fill policies."""

from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import (
    ImmediateFill,
    LiquidityCappedFill,
    LiquidityContext,
    StaticFill,
)


def _context(
    requested: str = "10", available: str | None = "100", side: Side = Side.BUY
) -> LiquidityContext:
    return LiquidityContext(
        asset_id="AAPL",
        side=side,
        requested_quantity=Decimal(requested),
        price=Decimal("100.005"),
        available_quantity=None if available is None else Decimal(available),
        timestamp=1.0,
    )


def test_immediate_fill_takes_the_whole_request() -> None:
    decision = ImmediateFill().decide(_context(requested="10", available="1"))

    assert decision.status is FillStatus.FULL_FILL
    assert decision.quantity == Decimal("10")


def test_static_fill_returns_its_configured_status() -> None:
    decision = StaticFill(FillStatus.REJECTED).decide(_context())

    assert decision.status is FillStatus.REJECTED
    assert decision.quantity == Decimal("10")


def test_static_fill_honours_an_explicit_quantity() -> None:
    decision = StaticFill(FillStatus.PARTIAL_FILL, Decimal("3")).decide(_context())

    assert decision.status is FillStatus.PARTIAL_FILL
    assert decision.quantity == Decimal("3")


def test_liquidity_capped_fill_fills_in_full_when_liquidity_allows() -> None:
    decision = LiquidityCappedFill().decide(_context(requested="10", available="100"))

    assert decision.status is FillStatus.FULL_FILL
    assert decision.quantity == Decimal("10")


def test_liquidity_capped_fill_partially_fills_an_oversized_order() -> None:
    decision = LiquidityCappedFill().decide(_context(requested="200", available="100"))

    assert decision.status is FillStatus.PARTIAL_FILL
    assert decision.quantity == Decimal("100")


def test_participation_rate_limits_the_share_of_shown_liquidity() -> None:
    policy = LiquidityCappedFill(participation_rate=Decimal("0.25"))

    decision = policy.decide(_context(requested="100", available="100"))

    assert decision.status is FillStatus.PARTIAL_FILL
    assert decision.quantity == Decimal("25.00")


def test_no_liquidity_produces_no_fill() -> None:
    decision = LiquidityCappedFill().decide(_context(requested="10", available="0"))

    assert decision.status is FillStatus.NO_FILL
    assert decision.quantity is None


def test_an_event_without_size_information_cannot_be_capped() -> None:
    """A quote feed with no sizes still trades, rather than silently stalling."""

    decision = LiquidityCappedFill().decide(_context(requested="10", available=None))

    assert decision.status is FillStatus.FULL_FILL
    assert decision.quantity == Decimal("10")


def test_policies_are_pure_and_reusable() -> None:
    policy = LiquidityCappedFill(participation_rate=Decimal("0.5"))
    context = _context(requested="100", available="100")

    assert policy.decide(context) == policy.decide(context)
