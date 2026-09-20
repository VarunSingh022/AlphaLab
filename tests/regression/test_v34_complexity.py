"""The v3.4 paths stay near-linear, measured on every run.

Same shape as ``test_data_ingestion_complexity.py`` and
``test_research_complexity.py``: measure the **growth ratio** between two input
sizes rather than an absolute duration, so the assertion is about the algorithm
rather than about the machine it ran on. Bounds sit well above linear and well
below quadratic, because the point is to catch a change in complexity class and
a tight bound on shared hardware is a flaky test.

Which paths, and why these
---------------------------

Four of the new surfaces are the ones that would plausibly become super-linear
if somebody changed them without noticing:

* **Roll selection**, where the obvious implementation compares every contract
  against every other rather than adjacent pairs.
* **Continuous-segment construction**, where a rescan of the whole observation
  set per segment is the natural mistake.
* **Chain inversion into a surface**, which is one bounded inversion per
  contract and would go quadratic if each one rescanned the chain.
* **Contract exposure**, which is one pass and would go quadratic on a
  membership check against a list rather than a dict.

The two numerical solvers -- implied volatility and the yield inversion -- are
bounded by their iteration caps rather than by input size, so they are checked
for a **constant** cost per call instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from alphalab.conventions import (
    Compounding,
    DayCount,
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
)
from alphalab.data.calendar import MarketCalendar
from alphalab.futures import (
    ContractChain,
    FutureContract,
    RollPolicy,
    RollTrigger,
    continuous_segments,
    roll_schedule,
)
from alphalab.macro.bond import Bond, clean_price, yield_from_clean_price
from alphalab.market.bar import Bar, TimeFrame
from alphalab.options import (
    ExerciseStyle,
    OptionChain,
    OptionContract,
    OptionType,
    black_scholes_value,
    implied_volatility,
    occ_symbol,
    surface_from_chain,
)
from alphalab.portfolio.contracts import ContractHolding, contract_exposures
from alphalab.portfolio.position import Position

DAY = 86400.0

#: A 10x input increase may cost at most this much more time.
#:
#: Linear is 10x and quadratic is 100x, so this sits between them with room for
#: the noise of a shared machine on either side. The same number
#: ``test_research_complexity.py`` uses, for the same reason.
LINEAR_BOUND = 25.0

#: A 4x increase, for the paths whose fixtures are expensive to build.
SMALL_STEP_BOUND = 12.0


def _elapsed(work: Callable[[], object]) -> float:
    """Best of three, so one scheduling hiccup does not fail the suite."""

    return min(_once(work) for _ in range(3))


def _once(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def _growth(small: Callable[[], object], large: Callable[[], object]) -> float:
    return _elapsed(large) / max(_elapsed(small), 1e-4)


# --------------------------------------------------------------------------- #
# Futures: chains, rolls and segments
# --------------------------------------------------------------------------- #


def _contract(index: int) -> FutureContract:
    """One contract per calendar month, so ``futures_symbol`` is distinct for each.

    Stepping by 30 days would put two contracts in one month and give them the
    same symbol, which the chain rightly refuses.
    """

    stamp = datetime(2026 + index // 12, index % 12 + 1, 1, tzinfo=UTC).timestamp()
    return FutureContract("CL", stamp, stamp, 1000, Decimal("0.01"), "USD")


def _chain(months: int) -> ContractChain:
    return ContractChain("CL", tuple(_contract(index) for index in range(months)))


def test_building_a_chain_is_linear_in_contracts() -> None:
    """Adjacent-pair validation. Comparing every month against every other would
    be quadratic, and a chain of a decade's monthly contracts is 120 of them."""

    small = tuple(_contract(index) for index in range(100))
    large = tuple(_contract(index) for index in range(1_000))
    growth = _growth(lambda: ContractChain("CL", small), lambda: ContractChain("CL", large))
    assert growth < LINEAR_BOUND, f"chain validation grew {growth:.1f}x for 10x contracts"


def test_a_date_based_roll_schedule_is_linear_in_contracts() -> None:
    small, large = _chain(100), _chain(1_000)
    policy = RollPolicy(RollTrigger.CALENDAR_DAYS_BEFORE_EXPIRY, 5)
    growth = _growth(lambda: roll_schedule(small, policy), lambda: roll_schedule(large, policy))
    assert growth < LINEAR_BOUND, f"roll selection grew {growth:.1f}x for 10x contracts"


def _bars(symbol: str, count: int, volume: str) -> tuple[Bar, ...]:
    price = Decimal("70.00")
    return tuple(
        Bar(
            symbol, index * DAY, price, price, price, price, Decimal(volume), price, 1, TimeFrame.D1
        )
        for index in range(count)
    )


def _observations(months: int, bars_each: int) -> dict[str, tuple[Bar, ...]]:
    """Each month out-trades the one before it from its own index onward, so a
    crossover exists for every adjacent pair."""

    built: dict[str, tuple[Bar, ...]] = {}
    for index in range(months):
        symbol = f"CL_{_month_code(index)}"
        built[symbol] = tuple(
            Bar(
                symbol,
                step * DAY,
                Decimal("70.00"),
                Decimal("70.00"),
                Decimal("70.00"),
                Decimal("70.00"),
                Decimal(str(100 + index)) if step >= index else Decimal("1"),
                Decimal("70.00"),
                1,
                TimeFrame.D1,
            )
            for step in range(bars_each)
        )
    return built


def _month_code(index: int) -> str:
    from alphalab.futures import futures_symbol

    return futures_symbol(_contract(index)).split("_", 1)[1]


def test_segment_construction_is_near_linear_in_observations() -> None:
    """One pass per segment over its own window. A rescan of every bar per
    segment would be quadratic in the number of rolls."""

    def build(months: int, bars_each: int) -> Callable[[], object]:
        chain = _chain(months)
        observations = _observations(months, bars_each)
        rolls = roll_schedule(
            chain, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
        )
        return lambda: continuous_segments(chain, rolls, observations)

    growth = _growth(build(10, 200), build(20, 400))
    assert growth < SMALL_STEP_BOUND, f"segment construction grew {growth:.1f}x for 4x work"


# --------------------------------------------------------------------------- #
# Options: chain inversion
# --------------------------------------------------------------------------- #


def _option_chain(strikes: int) -> tuple[OptionChain, dict[str, Decimal]]:
    contracts = tuple(
        OptionContract(
            "UND",
            Decimal(str(100 + index)),
            365.25 * DAY,
            OptionType.CALL,
            ExerciseStyle.EUROPEAN,
            100,
        )
        for index in range(strikes)
    )
    chain = OptionChain("UND", 0.0, contracts)
    prices = {
        occ_symbol(contract): Decimal(str(black_scholes_value(contract, 150.0, 0.25, 0.04, 1.0)))
        for contract in contracts
    }
    return chain, prices


def test_inverting_a_chain_into_a_surface_is_near_linear_in_contracts() -> None:
    small_chain, small_prices = _option_chain(50)
    large_chain, large_prices = _option_chain(500)
    growth = _growth(
        lambda: surface_from_chain(small_chain, small_prices, Decimal("150"), 0.04, 0.0),
        lambda: surface_from_chain(large_chain, large_prices, Decimal("150"), 0.04, 0.0),
    )
    assert growth < LINEAR_BOUND, f"surface construction grew {growth:.1f}x for 10x strikes"


def test_one_implied_volatility_costs_a_bounded_amount_regardless_of_the_input() -> None:
    """A bisection over a fixed bracket: the cost is the iteration cap, not the
    strike. A solver whose cost varied with the input would be one that had
    stopped bracketing."""

    contract = OptionContract(
        "UND", Decimal("150"), 365.25 * DAY, OptionType.CALL, ExerciseStyle.EUROPEAN, 100
    )
    near = Decimal(str(black_scholes_value(contract, 150.0, 0.25, 0.04, 1.0)))
    far = Decimal(str(black_scholes_value(contract, 150.0, 4.0, 0.04, 1.0)))
    ratio = _elapsed(lambda: implied_volatility(contract, far, Decimal("150"), 0.04, 0.0)) / max(
        _elapsed(lambda: implied_volatility(contract, near, Decimal("150"), 0.04, 0.0)), 1e-6
    )
    assert ratio < 5.0, f"a high-volatility inversion cost {ratio:.1f}x a low one"


# --------------------------------------------------------------------------- #
# Fixed income
# --------------------------------------------------------------------------- #


def test_pricing_a_bond_is_linear_in_its_cash_flows() -> None:
    def bond(years: int) -> Bond:
        return Bond(
            Decimal("100"),
            0.05,
            Compounding.MONTHLY,
            date(2020, 1, 15),
            date(2020 + years, 1, 15),
            DayCount.ACT_365_FIXED,
            "USD",
        )

    settlement = date(2021, 1, 15)
    growth = _growth(
        lambda: clean_price(bond(3), 0.05, settlement),
        lambda: clean_price(bond(30), 0.05, settlement),
    )
    assert growth < LINEAR_BOUND, f"bond pricing grew {growth:.1f}x for 10x flows"


def test_the_yield_inversion_is_bounded_by_its_iteration_cap() -> None:
    bond = Bond(
        Decimal("100"),
        0.05,
        Compounding.SEMI_ANNUAL,
        date(2020, 1, 15),
        date(2030, 1, 15),
        DayCount.ACT_365_FIXED,
        "USD",
    )
    settlement = date(2025, 1, 15)
    near = clean_price(bond, 0.05, settlement)
    far = clean_price(bond, 0.40, settlement)
    ratio = _elapsed(lambda: yield_from_clean_price(bond, far, settlement)) / max(
        _elapsed(lambda: yield_from_clean_price(bond, near, settlement)), 1e-6
    )
    assert ratio < 5.0, f"a far-from-par inversion cost {ratio:.1f}x a near-par one"


# --------------------------------------------------------------------------- #
# Multi-asset exposure
# --------------------------------------------------------------------------- #


def _holdings(count: int) -> list[ContractHolding]:
    convention = MarketConvention(
        "XCME",
        "XCME",
        "USD",
        "USD",
        Decimal("1000"),
        TickSchedule.flat(Decimal("0.01")),
        LotSpecification.single_units(),
        SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )
    return [
        ContractHolding(
            Position(
                f"A{index}",
                Decimal("10"),
                Decimal("75"),
                Decimal("75"),
                Decimal("0"),
                "USD",
                0.0,
            ),
            convention,
        )
        for index in range(count)
    ]


def test_contract_exposure_is_linear_in_holdings() -> None:
    """A dict for the duplicate check. A membership test against a list would be
    quadratic, and a real cross-asset book is thousands of instruments."""

    small = _holdings(1_000)
    large = _holdings(10_000)
    growth = _growth(lambda: contract_exposures(small), lambda: contract_exposures(large))
    assert growth < LINEAR_BOUND, f"contract exposure grew {growth:.1f}x for 10x holdings"


# --------------------------------------------------------------------------- #
# Calendars
# --------------------------------------------------------------------------- #


def test_a_session_lookup_does_not_scale_with_the_holiday_set() -> None:
    """``holidays`` is a frozenset, so membership is O(1). A list would make
    every bar's session check linear in the calendar's history."""

    from datetime import time as clock

    from alphalab.data.calendar import SessionWindow

    def calendar(holidays: int) -> MarketCalendar:
        return MarketCalendar(
            calendar_id="X",
            timezone_name="UTC",
            weekly_sessions=dict.fromkeys(range(5), (SessionWindow(clock(9, 30), clock(16, 0)),)),
            holidays=frozenset(
                date(2000, 1, 1) + __import__("datetime").timedelta(days=index * 7)
                for index in range(holidays)
            ),
        )

    few, many = calendar(10), calendar(1_000)
    instant = datetime(2026, 3, 16, 14, tzinfo=UTC).timestamp()
    ratio = _elapsed(lambda: [many.is_open(instant) for _ in range(2_000)]) / max(
        _elapsed(lambda: [few.is_open(instant) for _ in range(2_000)]), 1e-4
    )
    assert ratio < 3.0, f"a 100x holiday set cost {ratio:.1f}x per session lookup"
