"""
AlphaLab Examples
=================

Example 60 : Capital Allocation Across Strategies, Markets, Brokers and Currencies

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 11 (the execution path)
✓ Example 46 (strategy fingerprints)
✓ Example 56 (portfolio construction)

Topics
------

• Accounts at two brokers in three currencies, with what is already committed
  read from the execution path's one reservation ledger
• Strategy weights from a risk-parity construction across the strategies'
  returns, carried into the plan with that construction's identity
• One plan: every placement's capital in its own account's currency, every
  account reconciling exactly, every conversion recorded
• Capital along five dimensions -- strategy, market, broker, account and
  currency -- and limits on each, judged exactly
• Refusals: an oversubscribed account, a breached limit, weights that exceed
  the whole, a missing rate -- and PRO_RATA, which scales and says by how much
• One account's allocation becoming the budget its run is given, which admits
  exactly the allocation and not a unit more
• A strategy fingerprint that names the construction and the capital behind it

What this shows
---------------

Capital is allocated before anything trades, and the allocation has to add up
in the currency each account actually holds. A plan states its accounts, the
placements that draw on them and the rule that divides the capital; AlphaLab
allocates in each account's currency, reconciles every account to the unit,
converts only what is expressed against the whole plan -- at stated rates, each
recorded -- and refuses a plan that cannot be met rather than scaling it
quietly. A broker and an account are identifiers here: labels an application
maps to its own adapters and credentials, none of which reach AlphaLab.

Run

    python examples/60_capital_allocation.py
"""

import textwrap
from decimal import ROUND_FLOOR, Decimal

from _portfolio_world import (
    AS_OF,
    FX_SOURCE,
    RATES,
    STRATEGY_RETURNS_SOURCE,
    banner,
    refusal,
    section,
    strategy_returns,
)
from _strategy_evidence import CLASSES, DEFINITION, SOURCES, STRATEGY_ID

from alphalab.allocation import (
    AllocationConstraints,
    AllocationEngine,
    AllocationState,
    AllocationValidationError,
    CapitalAccount,
    CapitalAllocationPlan,
    CapitalAllocationResult,
    CapitalBudget,
    CapitalDimension,
    CapitalLimit,
    CapitalPlacement,
    FixedAmounts,
    FixedDollarSizing,
    OversubscriptionRule,
    PlacementWeights,
    allocate_capital,
    capital_budget,
    reserved_capital,
)
from alphalab.analytics import StrategyReturns, strategy_return_correlation
from alphalab.common.persistent_map import PersistentMap
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    EngineIdentity,
    LifecycleState,
    code_identity_for,
    fingerprint_for_version,
    get_strategy_version,
    register_strategy,
    research_configuration_with_portfolio,
)
from alphalab.portfolio import FxRate, FxRates, MissingRateError
from alphalab.portfolio_optimizer import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ExposureRange,
    RiskParity,
    SolverSettings,
    WeightBounds,
    construct,
)
from alphalab.strategy.events import Intent

#: The New York account's commitments on the execution path, as the reservation
#: ledger holds them: two working orders.
NEW_YORK_LEDGER = AllocationState(
    budget=CapitalBudget(Decimal("6000000"), Decimal("12000000"), currency="USD"),
    notional_allocated=Decimal("250000"),
    reservations=PersistentMap(
        {"working-order-1": Decimal("150000"), "working-order-2": Decimal("100000")}
    ),
)

#: Where each strategy's capital sits, and how a strategy's share is split
#: between its placements -- the desk's decision, stated.
SPLITS = {
    "MOMENTUM": (
        ("US_EQUITIES", "ACC-NY", Decimal("0.7")),
        ("JP_EQUITIES", "ACC-TKY", Decimal("0.3")),
    ),
    "VALUE": (
        ("US_EQUITIES", "ACC-NY", Decimal("0.8")),
        ("EU_EQUITIES", "ACC-FRA", Decimal("0.2")),
    ),
    "CARRY": (
        ("EU_EQUITIES", "ACC-FRA", Decimal("0.6")),
        ("JP_EQUITIES", "ACC-TKY", Decimal("0.4")),
    ),
}
DEPLOYED = Decimal("0.90")
LIMITS = (
    CapitalLimit(CapitalDimension.BROKER, "BROKER-B", Decimal("0.65"), None),
    CapitalLimit(CapitalDimension.CURRENCY, "JPY", None, Decimal("0.10")),
    CapitalLimit(CapitalDimension.STRATEGY, "MOMENTUM", Decimal("0.40"), None),
)


def accounts() -> tuple[CapitalAccount, ...]:
    return (
        CapitalAccount(
            "ACC-NY",
            "BROKER-A",
            "USD",
            Decimal("6000000"),
            reserved_capital(NEW_YORK_LEDGER, currency="USD"),
        ),
        CapitalAccount("ACC-FRA", "BROKER-B", "EUR", Decimal("5000000"), Decimal("0")),
        CapitalAccount("ACC-TKY", "BROKER-B", "JPY", Decimal("600000000"), Decimal("20000000")),
    )


def strategy_mix() -> ConstructionResult:
    """Risk parity across the three strategies' return series."""

    series = [
        StrategyReturns(name, values, "USD", "1D") for name, values in strategy_returns().items()
    ]
    covariance = strategy_return_correlation(series, source=STRATEGY_RETURNS_SOURCE).covariance
    return construct(
        ConstructionProblem(
            covariance,
            RiskParity.equal(),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None)),
            SolverSettings(1e-12, 1e-12, 1_000),
        )
    )


def plan_from(
    mix: ConstructionResult,
    *,
    limits: tuple[CapitalLimit, ...] = LIMITS,
    oversubscription: OversubscriptionRule = OversubscriptionRule.REFUSE,
) -> CapitalAllocationPlan:
    """The construction's weights, 90% deployed, split across each strategy's placements.

    Each placement weight is floored to six places, so the weights never add up
    to more than was decided.
    """

    weights = {}
    for strategy, legs in SPLITS.items():
        share = Decimal(repr(mix.require_weights()[strategy])) * DEPLOYED
        for market, account, split in legs:
            weights[CapitalPlacement(strategy, market, account)] = (share * split).quantize(
                Decimal("0.000001"), rounding=ROUND_FLOOR
            )
    return CapitalAllocationPlan(
        name="Q3 2024 capital plan",
        base_currency="USD",
        as_of=AS_OF,
        accounts=accounts(),
        placements=tuple(weights),
        rule=PlacementWeights(weights, f"risk parity {mix.result_id[:16]}, 90% deployed"),
        limits=limits,
        oversubscription=oversubscription,
        granularity=Decimal("1000"),
    )


def print_allocation(result: CapitalAllocationResult) -> None:
    assert result.placements is not None
    print(f"  {'strategy':<9}{'market':<12}{'account':<8}{'allocated':>18}{'in USD':>16}  rate")
    for entry in result.placements:
        conversion = entry.conversion
        rate = (
            "-"
            if conversion is None
            else f"{conversion.base}/{conversion.quote} {conversion.rate:.6g}"
            + (" (derived)" if conversion.derived else "")
        )
        print(
            f"  {entry.placement.strategy_id:<9}{entry.placement.market:<12}"
            f"{entry.placement.account_id:<8}{entry.allocated:>14,} {entry.currency}"
            f"{entry.base_amount:>16,.2f}  {rate}"
        )


def main() -> None:
    banner(60, "Capital Allocation")

    # ----------------------------------------------------------------- #
    # 1. Accounts, and what is already committed
    # ----------------------------------------------------------------- #

    section("1. Three accounts at two brokers, in three currencies")
    for account in accounts():
        print(
            f"  {account.account_id:<8}{account.broker_id:<9}{account.available:>16,} "
            f"{account.currency}  reserved {account.reserved:>12,}  free {account.free:>16,}"
        )
    print(
        f"  ACC-NY's reservation is read from the execution path's ledger\n"
        f"  ({len(NEW_YORK_LEDGER.reservations)} working orders), never re-entered by hand."
    )

    # ----------------------------------------------------------------- #
    # 2. Strategy weights from a construction
    # ----------------------------------------------------------------- #

    section("2. Strategy weights from a risk-parity construction")
    mix = strategy_mix()
    risk = mix.diagnostics.risk
    assert risk is not None
    for name, weight in mix.require_weights().items():
        share = 100 * risk.relative[name]
        print(f"  {name:<9} weight {100 * weight:6.2f}%   risk share {share:6.2f}%")
    print(f"  construction {mix.result_id[:16]}, over returns from:")
    print(f"    {STRATEGY_RETURNS_SOURCE}")

    # ----------------------------------------------------------------- #
    # 3. The plan, allocated
    # ----------------------------------------------------------------- #

    section("3. The plan: capital in each account's own currency")
    plan = plan_from(mix)
    result = allocate_capital(plan, RATES)
    print(f"  plan {plan.plan_id[:16]} -> {result.status.name}, result {result.result_id[:16]}")
    print(f"  total free capital: {result.total_free:,} USD (every account translated)")
    print_allocation(result)
    print()
    print("  Every account reconciles exactly, in its own currency:")
    for entry in result.accounts:
        account = entry.account
        exact = account.available == account.reserved + entry.allocated + entry.unallocated
        print(
            f"    {account.account_id:<8}{account.available:>14,} = {account.reserved:,} reserved"
            f" + {entry.allocated:,} allocated + {entry.unallocated:,} left  [{exact}]"
        )
    print(f"  {len(result.conversions)} conversions, from '{FX_SOURCE}' and its derived inverses")

    # ----------------------------------------------------------------- #
    # 4. Five dimensions, and the limits on them
    # ----------------------------------------------------------------- #

    section("4. Capital along five dimensions, and the limits")
    for grouping in result.dimensions:
        parts = ", ".join(
            f"{label} {100 * grouping.shares[label]:.1f}%" for label in grouping.amounts
        )
        print(f"  {grouping.dimension.name:<9}{parts}")
    currency = result.dimension(CapitalDimension.CURRENCY)
    assert currency.native is not None
    print(
        "  currency, as settled: "
        + ", ".join(f"{amount:,} {code}" for code, amount in currency.native.items())
    )
    for check in result.checks:
        limit = check.limit
        print(
            f"  limit {limit.dimension.name}/{limit.bucket}: share {100 * check.share:.2f}% "
            f"within [{limit.minimum_share}, {limit.maximum_share}] -> {check.satisfied}"
        )

    # ----------------------------------------------------------------- #
    # 5. Refusals, and scaling that is stated
    # ----------------------------------------------------------------- #

    section("5. Plans that cannot be met are refused; scaling is stated")
    greedy_amounts = {placement: Decimal("3000000") for placement in plan.placements}
    greedy = CapitalAllocationPlan(
        "greedy", "USD", AS_OF, accounts(), plan.placements, FixedAmounts(greedy_amounts), (),
        OversubscriptionRule.REFUSE, Decimal("1000"),
    )  # fmt: skip
    refused = allocate_capital(greedy, RATES)
    print(f"  3,000,000 per placement, REFUSE -> {refused.status.name}")
    print(f"    placements: {refused.placements} -- a refused plan allocates nothing")
    for reason in refused.reasons:
        print(f"    {reason}")
    scaled = allocate_capital(
        CapitalAllocationPlan(
            "greedy, pro rata", "USD", AS_OF, accounts(), plan.placements,
            FixedAmounts(greedy_amounts), (), OversubscriptionRule.PRO_RATA, Decimal("1000"),
        ),
        RATES,
    )  # fmt: skip
    print(f"  the same under PRO_RATA -> {scaled.status.name}")
    for entry in scaled.accounts:
        scale = "not scaled" if entry.scale is None else f"scaled by {entry.scale:.6f}"
        print(f"    {entry.account.account_id:<8}{scale}")
    tight = allocate_capital(
        plan_from(
            mix, limits=(CapitalLimit(CapitalDimension.BROKER, "BROKER-B", Decimal("0.20"), None),)
        ),
        RATES,
    )
    print(f"  BROKER-B capped at 20% -> {tight.status.name}:")
    print(
        textwrap.fill(tight.reasons[0], width=76, initial_indent="    ", subsequent_indent="    ")
    )
    try:
        PlacementWeights({placement: Decimal("0.2") for placement in plan.placements}, "too much")
    except AllocationValidationError as error:
        refusal("six placements at 20% each", error)
    dollars_only = FxRates.of([FxRate("EUR", "USD", Decimal("1.0850"), AS_OF - 3600.0, FX_SOURCE)])
    try:
        allocate_capital(plan, dollars_only.with_inverses())
    except MissingRateError as error:
        refusal("a plan with a yen account and no yen rate", error)

    # ----------------------------------------------------------------- #
    # 6. Onto the execution path
    # ----------------------------------------------------------------- #

    section("6. ACC-NY's allocation becomes the budget its run is given")
    budget = capital_budget(
        result, account_id="ACC-NY", maximum_exposure=Decimal("12000000"), cash_buffer=Decimal("0")
    )
    allocated = next(
        entry.allocated for entry in result.accounts if entry.account.account_id == "ACC-NY"
    )
    print(f"  budget: {budget.global_capital:,} {budget.currency}")
    print(f"          = 250,000 already reserved + {allocated:,} allocated by the plan")
    per_strategy = ", ".join(
        f"{name} {amount:,}" for name, amount in budget.strategy_budgets.items()
    )
    print(f"  per strategy: {per_strategy} USD")
    state = AllocationState(
        budget=budget,
        notional_allocated=NEW_YORK_LEDGER.notional_allocated,
        reservations=NEW_YORK_LEDGER.reservations,
    )

    def admitted(dollars: Decimal) -> bool:
        _, orders = AllocationEngine.allocate(
            state,
            (Intent(strategy_id="MOMENTUM", instrument="ATLS", target=dollars, timestamp=AS_OF),),
            {"ATLS": Decimal("1")},
            FixedDollarSizing(),
            AllocationConstraints(),
            AS_OF,
        )
        return len(orders) == 1

    print(f"  an order for exactly {allocated:,} USD : admitted {admitted(allocated)}")
    print(f"  an order for one dollar more    : admitted {admitted(allocated + 1)}")
    print(
        "  The execution path enforces the account's ceiling. The per-strategy amounts\n"
        "  are what weight-based sizing reads; they are not separate ceilings there."
    )

    # ----------------------------------------------------------------- #
    # 7. A fingerprint that names the construction and the capital
    # ----------------------------------------------------------------- #

    section("7. A strategy fingerprint that names its construction and capital")
    research = research_configuration_with_portfolio(
        {"validation": "walk-forward, 2023-2024"},
        constructions={"strategy_mix": mix},
        capital={"q3_2024": result},
        study=None,
    )
    for key, value in research.settings.items():
        shown = value if key == "validation" else f"{value[:16]}..."
        print(f"  {key:<32}= {shown}")
    lifecycle, reference = register_strategy(LifecycleState(), "ex-momentum", DEFINITION, AS_OF)
    version = get_strategy_version(lifecycle.strategies, reference.name, reference.version)
    code = code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.8.0", SOURCES)
    engine = EngineIdentity("alphalab", "3.8.0")
    fingerprint = fingerprint_for_version(version, code, NO_DEPENDENCIES, research, engine)
    other_plan = research_configuration_with_portfolio(
        {"validation": "walk-forward, 2023-2024"},
        constructions={"strategy_mix": mix},
        capital={"q3_2024": allocate_capital(plan_from(mix, limits=()), RATES)},
        study=None,
    )
    other = fingerprint_for_version(version, code, NO_DEPENDENCIES, other_plan, engine)
    print(f"  fingerprint                   : {fingerprint.fingerprint[:28]}...")
    print(f"  the same plan with no limits  : {other.fingerprint[:28]}...  (a different strategy)")


if __name__ == "__main__":
    main()
