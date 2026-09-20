"""The itemized execution cost contract."""

from decimal import Decimal

import pytest

from alphalab.core.enums import Side
from alphalab.execution.commission import FixedCommission, PercentageCommission
from alphalab.execution.costs import (
    FREE,
    CostContext,
    CostSettlement,
    ExecutionCostModel,
    ExecutionCosts,
    FixedHalfSpread,
    LinearImpact,
    NoFee,
    NoImpact,
    NoSlippage,
    NoSpread,
    NoTax,
    PerTradeFee,
    ProportionalFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
    itemized,
    reconciles,
)
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.slippage import PercentageSlippage


def context(**overrides: object) -> CostContext:
    base: dict[str, object] = {
        "asset_id": "AAPL",
        "side": Side.BUY,
        "quantity": Decimal("100"),
        "reference_price": Decimal("50"),
        "currency": "USD",
        "venue": "SIM",
        "timestamp": 1.0,
    }
    base.update(overrides)
    return CostContext(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The two settlements
# --------------------------------------------------------------------------- #


def test_price_embedded_and_cash_charged_partition_the_six_roles() -> None:
    """Every component belongs to exactly one settlement, and none is lost."""

    costs = ExecutionCosts(
        spread=Decimal("0.01"),
        slippage=Decimal("0.02"),
        impact=Decimal("0.03"),
        commission=Decimal("1.00"),
        fees=Decimal("2.00"),
        tax=Decimal("3.00"),
    )

    embedded = costs.by_settlement(CostSettlement.PRICE_EMBEDDED)
    charged = costs.by_settlement(CostSettlement.CASH_CHARGED)

    assert set(embedded) | set(charged) == set(itemized(costs, Decimal("1")))
    assert set(embedded) & set(charged) == set()
    assert sum(embedded.values()) == costs.price_concession == Decimal("0.06")
    assert sum(charged.values()) == costs.cash_charged == Decimal("6.00")


def test_total_needs_the_quantity_because_concessions_are_per_unit() -> None:
    costs = ExecutionCosts(
        Decimal("0.01"), Decimal("0"), Decimal("0"), Decimal("5.00"), Decimal("0"), Decimal("0")
    )

    assert costs.total(Decimal("100")) == Decimal("6.00")
    assert costs.total(Decimal("200")) == Decimal("7.00")


def test_a_negative_component_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="price improvement"):
        ExecutionCosts(
            Decimal("-0.01"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )


# --------------------------------------------------------------------------- #
# Ordering
# --------------------------------------------------------------------------- #


def test_cash_costs_are_charged_on_the_post_concession_consideration() -> None:
    """The ordering the module documents, asserted on a number.

    A 1% fee on a buy that paid a 1.00 concession on a 50.00 reference is 1% of
    51.00, not of 50.00.
    """

    model = ExecutionCostModel(
        spread_model=FixedHalfSpread(Decimal("1.00")),
        slippage_model=NoSlippage(),
        impact_model=NoImpact(),
        commission_model=FixedCommission(Decimal("0")),
        fee_model=ProportionalFee(Decimal("0.01")),
        tax_model=NoTax(),
    )
    costs = model.quote(context())

    assert costs.spread == Decimal("1.00")
    assert costs.fees == (Decimal("51") * Decimal("100") * Decimal("0.01")).quantize(
        Decimal("0.01")
    )


def test_the_three_concessions_are_additive_and_do_not_compound() -> None:
    model = ExecutionCostModel(
        spread_model=FixedHalfSpread(Decimal("0.10")),
        slippage_model=PercentageSlippage(Decimal("0.001")),
        impact_model=LinearImpact(Decimal("0.02")),
        commission_model=FixedCommission(Decimal("0")),
        fee_model=NoFee(),
        tax_model=NoTax(),
    )
    ctx = context(available_liquidity=Decimal("1000"))
    costs = model.quote(ctx)

    # Each computed from the reference price, then summed.
    assert costs.spread == Decimal("0.10")
    assert costs.slippage == Decimal("0.0500")
    assert costs.impact == Decimal("0.1000")
    assert costs.price_concession == Decimal("0.2500")
    assert model.fill_price(ctx, costs) == Decimal("50.2500")


def test_a_sell_receives_less_and_a_buy_pays_more() -> None:
    model = ExecutionCostModel(
        FixedHalfSpread(Decimal("0.25")),
        NoSlippage(),
        NoImpact(),
        FixedCommission(Decimal("0")),
        NoFee(),
        NoTax(),
    )
    buy = context(side=Side.BUY)
    sell = context(side=Side.SELL)

    assert model.fill_price(buy, model.quote(buy)) == Decimal("50.25")
    assert model.fill_price(sell, model.quote(sell)) == Decimal("49.75")


# --------------------------------------------------------------------------- #
# Spread
# --------------------------------------------------------------------------- #


def test_quoted_half_spread_measures_the_quote() -> None:
    ctx = context(bid=Decimal("49.90"), ask=Decimal("50.10"))

    assert QuotedHalfSpread().half_spread(ctx) == Decimal("0.1000")


def test_quoted_half_spread_refuses_a_missing_side_rather_than_assuming_one() -> None:
    with pytest.raises(ExecutionValidationError, match="ask is absent"):
        QuotedHalfSpread().half_spread(context(bid=Decimal("49.90")))


def test_quoted_half_spread_refuses_a_crossed_quote() -> None:
    with pytest.raises(ExecutionValidationError, match="Crossed quote"):
        QuotedHalfSpread().half_spread(context(bid=Decimal("50.10"), ask=Decimal("49.90")))


# --------------------------------------------------------------------------- #
# Impact
# --------------------------------------------------------------------------- #


def test_impact_refuses_without_the_liquidity_that_forms_a_participation_rate() -> None:
    for model in (LinearImpact(Decimal("0.1")), SquareRootImpact(Decimal("0.1"))):
        with pytest.raises(ExecutionValidationError, match="participation rate"):
            model.impact(context())


def test_impact_grows_with_participation() -> None:
    small = context(available_liquidity=Decimal("100000"))
    large = context(available_liquidity=Decimal("200"))
    model = SquareRootImpact(Decimal("0.1"))

    assert model.impact(large) > model.impact(small) > Decimal("0")


def test_square_root_impact_follows_the_square_root_law() -> None:
    """Quadrupling participation doubles the concession."""

    model = SquareRootImpact(Decimal("0.2"))
    one = model.impact(context(quantity=Decimal("100"), available_liquidity=Decimal("10000")))
    four = model.impact(context(quantity=Decimal("400"), available_liquidity=Decimal("10000")))

    assert four == (one * Decimal("2")).quantize(Decimal("0.0001"))


def test_negative_coefficients_are_refused() -> None:
    for factory in (LinearImpact, SquareRootImpact):
        with pytest.raises(ExecutionValidationError, match="not negative"):
            factory(Decimal("-0.1"))


# --------------------------------------------------------------------------- #
# Fees and tax
# --------------------------------------------------------------------------- #


def test_a_tax_falls_only_on_the_sides_it_names() -> None:
    tax = ProportionalTax(Decimal("0.005"), frozenset({Side.BUY}))
    base = Decimal("5000")

    assert tax.tax(context(side=Side.BUY), base) == Decimal("25.00")
    assert tax.tax(context(side=Side.SELL), base) == Decimal("0")


def test_a_tax_naming_no_side_is_refused_rather_than_defaulted_to_both() -> None:
    with pytest.raises(ExecutionValidationError, match="names no side"):
        ProportionalTax(Decimal("0.005"), frozenset())


def test_a_per_trade_fee_is_not_charged_on_a_zero_quantity() -> None:
    assert PerTradeFee(Decimal("1.00")).fee(context(quantity=Decimal("0")), Decimal("0")) == (
        Decimal("0")
    )


# --------------------------------------------------------------------------- #
# Absence, and determinism
# --------------------------------------------------------------------------- #


def test_free_costs_nothing_and_says_so_in_six_places() -> None:
    costs = FREE.quote(context())

    assert costs.price_concession == Decimal("0")
    assert costs.cash_charged == Decimal("0")
    assert all(value == Decimal("0") for value in itemized(costs, Decimal("100")).values())


def test_the_itemization_is_money_and_sums_to_the_all_in_cost() -> None:
    """Dimensional coherence: six amounts in one currency, not four and two."""

    costs = ExecutionCosts(
        Decimal("0.01"),
        Decimal("0.02"),
        Decimal("0.03"),
        Decimal("1.00"),
        Decimal("2.00"),
        Decimal("3.00"),
    )
    quantity = Decimal("100")
    parts = itemized(costs, quantity)

    assert parts["spread"] == Decimal("1.00")
    assert parts["impact"] == Decimal("3.00")
    assert parts["commission"] == Decimal("1.00")
    assert sum(parts.values()) == costs.total(quantity)


def test_a_zero_quantity_fill_reaches_no_model_at_all() -> None:
    """A per-trade fee on a non-event would be a charge for nothing."""

    model = ExecutionCostModel(
        NoSpread(),
        NoSlippage(),
        NoImpact(),
        FixedCommission(Decimal("9.99")),
        PerTradeFee(Decimal("5.00")),
        NoTax(),
    )

    assert model.quote(context(quantity=Decimal("0"))).cash_charged == Decimal("0")


def test_the_same_configuration_and_context_give_the_same_costs() -> None:
    model = ExecutionCostModel(
        QuotedHalfSpread(),
        PercentageSlippage(Decimal("0.0005")),
        SquareRootImpact(Decimal("0.08")),
        PercentageCommission(Decimal("0.0002")),
        ProportionalFee(Decimal("0.0001")),
        ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
    )
    ctx = context(bid=Decimal("49.95"), ask=Decimal("50.05"), available_liquidity=Decimal("4000"))

    assert model.quote(ctx) == model.quote(ctx)


def test_reconciles_ties_the_itemization_to_the_two_reported_totals() -> None:
    costs = ExecutionCosts(
        Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), Decimal("1"), Decimal("2"), Decimal("3")
    )

    assert reconciles(costs, Decimal("0.06"), Decimal("6"))
    assert not reconciles(costs, Decimal("0.05"), Decimal("6"))
    assert not reconciles(costs, Decimal("0.06"), Decimal("5"))
