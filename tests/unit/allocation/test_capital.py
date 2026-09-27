"""Capital allocation across strategies, markets, brokers, accounts and currencies.

Every account must reconcile exactly in its own currency --
``available == reserved + allocated + unallocated`` -- and a plan that cannot be
funded is refused whole, never quietly shrunk.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.capital import (
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalAllocationResult,
    CapitalAllocationStatus,
    CapitalDimension,
    CapitalLimit,
    CapitalPlacement,
    EqualWeights,
    FixedAmounts,
    OversubscriptionRule,
    PlacementWeights,
    allocate_capital,
    capital_budget,
    reserved_capital,
)
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.exceptions import AllocationValidationError
from alphalab.allocation.sizing import FixedDollarSizing
from alphalab.allocation.state import AllocationState
from alphalab.common.persistent_map import PersistentMap
from alphalab.portfolio.fx import NO_RATES, FxRate, FxRates, MissingRateError
from alphalab.strategy.events import Intent

RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.10"), 0.0, "unit desk"),
        FxRate("USD", "EUR", Decimal("0.90"), 0.0, "unit desk"),
    ]
)
US = CapitalAccount("ACC-US", "BROKER-A", "USD", Decimal("1000000"), Decimal("100000"))
EU = CapitalAccount("ACC-EU", "BROKER-B", "EUR", Decimal("500000"), Decimal("0"))
MOM = CapitalPlacement("MOM", "US_EQUITIES", "ACC-US")
MR = CapitalPlacement("MR", "US_EQUITIES", "ACC-US")
CARRY = CapitalPlacement("CARRY", "EU_FUTURES", "ACC-EU")


def plan(
    rule: FixedAmounts | PlacementWeights | EqualWeights,
    *,
    accounts: tuple[CapitalAccount, ...] = (US, EU),
    placements: tuple[CapitalPlacement, ...] = (MOM, MR, CARRY),
    limits: tuple[CapitalLimit, ...] = (),
    oversubscription: OversubscriptionRule = OversubscriptionRule.REFUSE,
    granularity: str = "1000",
) -> CapitalAllocationPlan:
    return CapitalAllocationPlan(
        "q4",
        "USD",
        10.0,
        accounts,
        placements,
        rule,
        limits,
        oversubscription,
        Decimal(granularity),
    )


WEIGHTS = PlacementWeights(
    {MOM: Decimal("0.4"), MR: Decimal("0.2"), CARRY: Decimal("0.3")}, "committee"
)


def allocated(result: CapitalAllocationResult) -> dict[str, Decimal]:
    assert result.placements is not None, result.reasons
    return {entry.placement.strategy_id: entry.allocated for entry in result.placements}


def assert_reconciles(result: CapitalAllocationResult) -> None:
    for entry in result.accounts:
        assert (
            entry.account.available == entry.account.reserved + entry.allocated + entry.unallocated
        )
        assert entry.unallocated >= 0 and entry.allocated >= 0


# --------------------------------------------------------------------------- #
# Allocation rules
# --------------------------------------------------------------------------- #


def test_placement_weights_divide_the_free_capital_in_the_base_currency() -> None:
    result = allocate_capital(plan(WEIGHTS), RATES)

    # Free capital: 900,000 USD + 500,000 EUR at 1.10 = 1,450,000 USD.
    assert result.status is CapitalAllocationStatus.ALLOCATED
    assert result.total_free == Decimal("1450000.00")
    # 0.4 and 0.2 of it land in the dollar account; 0.3 is 435,000 USD, which
    # the euro account receives as 391,500 EUR, rounded down to 391,000.
    assert allocated(result) == {
        "CARRY": Decimal("391000"),
        "MOM": Decimal("580000"),
        "MR": Decimal("290000"),
    }
    assert_reconciles(result)


def test_a_foreign_account_is_allocated_in_its_own_currency_with_the_rate_recorded() -> None:
    result = allocate_capital(plan(WEIGHTS), RATES)
    assert result.placements is not None
    carry = next(entry for entry in result.placements if entry.placement.strategy_id == "CARRY")

    # 0.3 of 1,450,000 USD is 435,000 USD, which is 391,500.00 EUR at 0.90,
    # rounded down to the 1,000 granularity.
    assert carry.currency == "EUR"
    assert carry.requested == Decimal("391500.00")
    assert carry.allocated == Decimal("391000")
    assert carry.conversion is not None and carry.conversion.source == "unit desk"
    assert carry.base_amount == Decimal("430100.00")
    assert any(c.base == "USD" and c.quote == "EUR" for c in result.conversions)


def test_fixed_amounts_are_allocated_as_stated_in_each_account_s_currency() -> None:
    rule = FixedAmounts({MOM: Decimal("500000"), MR: Decimal("400000"), CARRY: Decimal("250000")})
    result = allocate_capital(plan(rule), RATES)

    assert allocated(result) == {
        "CARRY": Decimal("250000"),
        "MOM": Decimal("500000"),
        "MR": Decimal("400000"),
    }
    us = next(entry for entry in result.accounts if entry.account.account_id == "ACC-US")
    assert us.unallocated == Decimal("0")  # exactly exhausted: 900,000 free
    assert_reconciles(result)


def test_equal_weights_split_the_stated_fraction_equally() -> None:
    result = allocate_capital(plan(EqualWeights(Decimal("0.6")), granularity="0.01"), RATES)

    # 0.6 of 1,450,000 is 870,000; a third each is 290,000 USD.
    assert allocated(result)["MOM"] == Decimal("290000.00")
    assert allocated(result)["MR"] == Decimal("290000.00")
    assert_reconciles(result)


def test_a_partial_allocation_leaves_the_rest_unallocated_not_redistributed() -> None:
    rule = PlacementWeights(
        {MOM: Decimal("0.1"), MR: Decimal("0.1"), CARRY: Decimal("0.1")}, "partial"
    )
    result = allocate_capital(plan(rule), RATES)
    us = next(entry for entry in result.accounts if entry.account.account_id == "ACC-US")

    assert us.allocated == Decimal("290000")
    assert us.unallocated == Decimal("610000")
    assert_reconciles(result)


def test_the_granularity_rounds_down_and_the_residue_stays_unallocated() -> None:
    rule = FixedAmounts({MOM: Decimal("123456.78"), MR: Decimal("0"), CARRY: Decimal("0")})
    result = allocate_capital(plan(rule, granularity="1000"), RATES)

    assert allocated(result)["MOM"] == Decimal("123000")
    assert allocated(result)["MR"] == Decimal("0")
    assert_reconciles(result)


# --------------------------------------------------------------------------- #
# Oversubscription
# --------------------------------------------------------------------------- #

GREEDY = PlacementWeights(
    {MOM: Decimal("0.6"), MR: Decimal("0.3"), CARRY: Decimal("0.1")}, "greedy"
)


def test_an_account_asked_for_more_than_it_holds_refuses_the_whole_plan() -> None:
    result = allocate_capital(plan(GREEDY), RATES)

    assert result.status is CapitalAllocationStatus.REFUSED
    assert result.placements is None
    assert result.dimensions == ()
    assert "'ACC-US' is asked for 1305000" in result.reasons[0]
    us = next(entry for entry in result.accounts if entry.account.account_id == "ACC-US")
    assert (us.requested, us.allocated, us.unallocated) == (
        Decimal("1305000.000"),
        Decimal("0"),
        Decimal("900000"),
    )
    with pytest.raises(AllocationValidationError, match="refused"):
        result.dimension(CapitalDimension.STRATEGY)


def test_pro_rata_scales_only_the_oversubscribed_account_and_says_by_how_much() -> None:
    result = allocate_capital(plan(GREEDY, oversubscription=OversubscriptionRule.PRO_RATA), RATES)
    us = next(entry for entry in result.accounts if entry.account.account_id == "ACC-US")
    eu = next(entry for entry in result.accounts if entry.account.account_id == "ACC-EU")

    assert result.status is CapitalAllocationStatus.ALLOCATED
    assert us.scale is not None and eu.scale is None
    assert allocated(result)["MOM"] + allocated(result)["MR"] <= Decimal("900000")
    assert allocated(result)["MOM"] == Decimal("600000")
    assert "scaled by" in result.reasons[0]
    assert_reconciles(result)


def test_an_account_with_no_capital_allocates_nothing_and_says_so() -> None:
    empty = CapitalAccount("ACC-EU", "BROKER-B", "EUR", Decimal("0"), Decimal("0"))
    fixed = FixedAmounts({MOM: Decimal("0"), MR: Decimal("0"), CARRY: Decimal("1")})

    refused = allocate_capital(plan(fixed, accounts=(US, empty)), RATES)
    assert refused.status is CapitalAllocationStatus.REFUSED
    zero = allocate_capital(
        plan(
            PlacementWeights({MOM: Decimal("0"), MR: Decimal("0"), CARRY: Decimal("0")}, "none"),
            accounts=(US, empty),
        ),
        RATES,
    )
    assert allocated(zero) == {"CARRY": Decimal("0"), "MOM": Decimal("0"), "MR": Decimal("0")}
    assert_reconciles(zero)


# --------------------------------------------------------------------------- #
# Dimensions and limits
# --------------------------------------------------------------------------- #


def test_capital_is_grouped_along_all_five_dimensions_and_every_grouping_reconciles() -> None:
    result = allocate_capital(plan(WEIGHTS), RATES)
    assert result.placements is not None
    total = sum((entry.base_amount for entry in result.placements), Decimal(0))

    for dimension in CapitalDimension:
        grouping = result.dimension(dimension)
        assert sum(grouping.amounts.values(), Decimal(0)) == total
    assert set(result.dimension(CapitalDimension.BROKER).amounts) == {"BROKER-A", "BROKER-B"}
    assert set(result.dimension(CapitalDimension.MARKET).amounts) == {"EU_FUTURES", "US_EQUITIES"}
    currency = result.dimension(CapitalDimension.CURRENCY)
    assert currency.native == {"EUR": Decimal("391000"), "USD": Decimal("870000")}
    assert result.dimension(CapitalDimension.ACCOUNT).native is None


def test_a_limit_the_allocation_would_breach_refuses_it_with_every_reason() -> None:
    limits = (
        CapitalLimit(CapitalDimension.BROKER, "BROKER-A", Decimal("0.5"), None),
        CapitalLimit(CapitalDimension.MARKET, "EU_FUTURES", None, Decimal("0.35")),
    )
    result = allocate_capital(plan(WEIGHTS, limits=limits), RATES)

    assert result.status is CapitalAllocationStatus.REFUSED
    assert len(result.reasons) == 2
    assert [check.satisfied for check in result.checks] == [False, False]
    assert result.placements is None


def test_limits_that_hold_are_reported_as_checked() -> None:
    limits = (CapitalLimit(CapitalDimension.STRATEGY, "MOM", Decimal("0.4"), Decimal("0.1")),)
    result = allocate_capital(plan(WEIGHTS, limits=limits), RATES)

    assert result.succeeded
    assert result.checks[0].satisfied
    assert result.checks[0].share == result.dimension(CapitalDimension.STRATEGY).shares["MOM"]


# --------------------------------------------------------------------------- #
# Currency and determinism
# --------------------------------------------------------------------------- #


def test_a_plan_across_currencies_without_a_rate_is_refused_by_the_rate_authority() -> None:
    with pytest.raises(MissingRateError):
        allocate_capital(
            plan(WEIGHTS), FxRates.of([FxRate("GBP", "USD", Decimal("1.3"), 0.0, "x")])
        )


def test_a_single_currency_plan_needs_no_rates() -> None:
    single = plan(
        PlacementWeights({MOM: Decimal("0.5"), MR: Decimal("0.5")}, "halves"),
        accounts=(US,),
        placements=(MOM, MR),
    )
    result = allocate_capital(single, NO_RATES)

    assert result.conversions == ()
    assert allocated(result) == {"MOM": Decimal("450000"), "MR": Decimal("450000")}


def test_the_same_plan_and_rates_give_the_same_result_and_identity() -> None:
    first = allocate_capital(plan(WEIGHTS), RATES)

    assert first == allocate_capital(plan(WEIGHTS), RATES)
    assert first.result_id == allocate_capital(plan(WEIGHTS), RATES).result_id
    assert first.plan_id != plan(WEIGHTS, granularity="100").plan_id


def test_the_plan_identity_ignores_listing_order() -> None:
    assert (
        plan(WEIGHTS).plan_id
        == plan(WEIGHTS, accounts=(EU, US), placements=(CARRY, MR, MOM)).plan_id
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_accounts_refuse_reservations_beyond_their_capital() -> None:
    with pytest.raises(AllocationValidationError, match="more is committed than it holds"):
        CapitalAccount("A", "B", "USD", Decimal("10"), Decimal("11"))
    with pytest.raises(AllocationValidationError, match="non-negative"):
        CapitalAccount("A", "B", "USD", Decimal("-1"), Decimal("0"))
    with pytest.raises(AllocationValidationError, match="non-blank"):
        CapitalAccount("A", "", "USD", Decimal("1"), Decimal("0"))


def test_a_plan_names_every_placement_in_its_rule_and_nothing_else() -> None:
    with pytest.raises(AllocationValidationError, match="not given zero"):
        plan(PlacementWeights({MOM: Decimal("0.5")}, "partial"))
    elsewhere = CapitalPlacement("CARRY", "EU_FUTURES", "ACC-JP")
    with pytest.raises(AllocationValidationError, match="does not hold"):
        plan(
            FixedAmounts({MOM: Decimal("1"), MR: Decimal("1"), elsewhere: Decimal("1")}),
            placements=(MOM, MR, elsewhere),
        )


def test_placement_weights_cannot_exceed_the_whole_or_go_unsourced() -> None:
    with pytest.raises(AllocationValidationError, match="cannot exceed"):
        PlacementWeights({MOM: Decimal("0.7"), MR: Decimal("0.4")}, "x")
    with pytest.raises(AllocationValidationError, match="non-blank"):
        PlacementWeights({MOM: Decimal("0.5")}, "")
    with pytest.raises(AllocationValidationError, match="at most one"):
        PlacementWeights({MOM: Decimal("1.5")}, "x")


@pytest.mark.parametrize("granularity", ["0", "-1", "NaN"])
def test_the_granularity_is_positive(granularity: str) -> None:
    with pytest.raises(AllocationValidationError, match="granularity"):
        plan(WEIGHTS, granularity=granularity)


def test_duplicated_accounts_placements_and_limits_are_refused() -> None:
    with pytest.raises(AllocationValidationError, match="listed twice"):
        plan(WEIGHTS, accounts=(US, US, EU))
    with pytest.raises(AllocationValidationError, match="listed twice"):
        plan(FixedAmounts({MOM: Decimal("1")}), placements=(MOM, MOM))
    limit = CapitalLimit(CapitalDimension.STRATEGY, "MOM", Decimal("0.5"), None)
    with pytest.raises(AllocationValidationError, match="stated twice"):
        plan(WEIGHTS, limits=(limit, limit))
    with pytest.raises(AllocationValidationError, match="neither end"):
        CapitalLimit(CapitalDimension.STRATEGY, "MOM", None, None)


# --------------------------------------------------------------------------- #
# Composition with the execution path
# --------------------------------------------------------------------------- #


def test_reserved_capital_is_read_from_the_one_reservation_ledger() -> None:
    state = AllocationState(
        budget=CapitalBudget(Decimal("1000000"), Decimal("2000000"), currency="USD"),
        notional_allocated=Decimal("100000"),
        reservations=PersistentMap({"order-1": Decimal("60000"), "order-2": Decimal("40000")}),
    )

    assert reserved_capital(state, currency="USD") == Decimal("100000")
    with pytest.raises(AllocationValidationError, match="relabel"):
        reserved_capital(state, currency="EUR")


def test_an_unstated_budget_currency_and_an_inconsistent_ledger_are_refused() -> None:
    unstated = AllocationState(budget=CapitalBudget(Decimal("1"), Decimal("1")))
    inconsistent = AllocationState(
        budget=CapitalBudget(Decimal("1"), Decimal("1"), currency="USD"),
        notional_allocated=Decimal("5"),
        reservations=PersistentMap({"order-1": Decimal("4")}),
    )

    with pytest.raises(AllocationValidationError, match="no currency"):
        reserved_capital(unstated, currency="USD")
    with pytest.raises(AllocationValidationError, match="invariant I1"):
        reserved_capital(inconsistent, currency="USD")


def test_an_allocation_becomes_an_execution_budget_that_admits_exactly_the_allocation() -> None:
    result = allocate_capital(plan(WEIGHTS), RATES)
    budget = capital_budget(
        result, account_id="ACC-US", maximum_exposure=Decimal("10000000"), cash_buffer=Decimal("0")
    )

    assert budget == CapitalBudget(
        global_capital=Decimal("970000"),
        maximum_exposure=Decimal("10000000"),
        cash_buffer=Decimal("0"),
        strategy_budgets={"MOM": Decimal("580000"), "MR": Decimal("290000")},
        currency="USD",
    )

    # The account already holds 100,000 of reservations; the budget admits
    # exactly the 870,000 allocated on top of them, and refuses one unit more.
    committed = AllocationState(
        budget=budget,
        notional_allocated=Decimal("100000"),
        reservations=PersistentMap({"working-order": Decimal("100000")}),
    )

    def ask(dollars: str) -> int:
        _, orders = AllocationEngine.allocate(
            committed,
            (
                Intent(
                    strategy_id="MOM", instrument="ASSET", target=Decimal(dollars), timestamp=1.0
                ),
            ),
            {"ASSET": Decimal("1")},
            FixedDollarSizing(),
            AllocationConstraints(),
            1.0,
        )
        return len(orders)

    assert ask("870000") == 1
    assert ask("870001") == 0


def test_a_refused_plan_has_no_execution_budget() -> None:
    refused = allocate_capital(plan(GREEDY), RATES)
    with pytest.raises(AllocationValidationError, match="refused"):
        capital_budget(
            refused, account_id="ACC-US", maximum_exposure=Decimal("1"), cash_buffer=Decimal("0")
        )
    ok = allocate_capital(plan(WEIGHTS), RATES)
    with pytest.raises(AllocationValidationError, match="no account"):
        capital_budget(
            ok, account_id="ACC-JP", maximum_exposure=Decimal("1"), cash_buffer=Decimal("0")
        )
