"""The rebalancing strategy at a slice, alone: its book, its checks, its order and its state.

* **Its book is its own**, kept from its share of each fill.
* **What it sends.** A certified answer becomes lot-rounded target quantities
  for every asset of the problem's universe and zero for a held one it leaves.
  Sells first holds the purchases until every sale has filled; a later
  rebalance replaces purchases still waiting.
* **What it refuses.** A problem whose costs or turnover start from another
  book, a turnover limit that would sell a held asset unseen, a universe it was
  not given or cannot price, an asset the run does not declare; and nothing is
  assumed of a future that declares no economics.
* **Certified answers only**, a cardinality limit included.
* **Its state** travels through the persistence encoder and back, exactly.
* **Capital** is stated: a fixed amount, or the account's equity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.common.order_terms import MARKET
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio_optimizer import (
    CardinalityOutcome,
    ConstraintSet,
    ConstructionProblem,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    LinearCosts,
    MaximumReturn,
    MinimumVariance,
    SolverSettings,
    TurnoverLimit,
    WeightBounds,
)
from alphalab.rebalancing import (
    AccountEquity,
    CapitalRule,
    DriftTrigger,
    FixedCapital,
    RebalanceExecution,
    RebalanceView,
    RebalancingInputError,
    RebalancingStrategy,
    ThresholdTrigger,
)
from alphalab.strategy import DiscardingLogger, FixedClock, NoOrders, NoRiskView
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, IntentKind, SliceClosed

RECORDS = {
    record.asset_id: record
    for record in (
        InstrumentRecord("RSA", AssetType.EQUITY, "XNAS", "USD"),
        InstrumentRecord("RSB", AssetType.EQUITY, "XNAS", "USD"),
        InstrumentRecord("RSC", AssetType.EQUITY, "XNAS", "USD"),
    )
}
A, B, C = sorted(RECORDS)
PRICES = {A: Decimal("50"), B: Decimal("100"), C: Decimal("25")}
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
CAPITAL = Decimal("100000")


@dataclass
class Market:
    prices: Mapping[str, Decimal]

    def price(self, asset_id: str) -> Decimal | None:
        return self.prices.get(asset_id)


@dataclass
class Universe:
    records: Mapping[str, InstrumentRecord]

    def instrument(self, asset_id: str) -> Any | None:
        return self.records.get(asset_id)


@dataclass
class Account:
    cash_held: Decimal = Decimal("0")
    held: dict[str, Decimal] = field(default_factory=dict)

    def cash(self, currency: str) -> Decimal:
        return self.cash_held if currency == "USD" else Decimal("0")

    def quantity(self, asset_id: str) -> Decimal:
        return self.held.get(asset_id, Decimal("0"))


@dataclass
class Orders:
    """The strategy's working quantity per asset, as the run's order view reports it."""

    working: dict[str, Decimal] = field(default_factory=dict)

    def net_quantity(self, asset_id: str) -> Decimal:
        return self.working.get(asset_id, Decimal("0"))


def context(
    prices: Mapping[str, Decimal] = PRICES,
    records: Mapping[str, InstrumentRecord] = RECORDS,
    account: Account | None = None,
    orders: Orders | None = None,
) -> StrategyContext:
    return StrategyContext(
        portfolio=account or Account(),  # type: ignore[arg-type]
        market=Market(prices),  # type: ignore[arg-type]
        clock=FixedClock(0.0),
        logger=DiscardingLogger(),
        risk_view=NoRiskView(),
        config={},
        orders=orders if orders is not None else NoOrders(),  # type: ignore[arg-type]
        universe=Universe(records),  # type: ignore[arg-type]
    )


def covariance(assets: tuple[str, ...] = (A, B, C)) -> CovarianceMatrix:
    variances = {A: 0.04, B: 0.09, C: 0.16}
    rows = [[variances[a] if a == b else 0.0 for b in assets] for a in assets]
    return CovarianceMatrix.from_rows(
        assets, rows, currency="USD", period="1M", source="unit", observations=None
    )


@dataclass
class Fixed:
    """A model that states the same kind of problem at every rebalance, through ``build``."""

    build: Callable[[RebalanceView], ConstructionProblem | None]
    views: list[RebalanceView] = field(default_factory=list)

    def problem(self, view: RebalanceView) -> ConstructionProblem | None:
        self.views.append(view)
        return self.build(view)


def minimum_variance(view: RebalanceView, cap: float = 1.0) -> ConstructionProblem:
    return ConstructionProblem(
        covariance(),
        MinimumVariance(),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, cap)),
        SETTINGS,
    )


def strategy(
    build: Callable[[RebalanceView], ConstructionProblem | None] = minimum_variance,
    *,
    execution: RebalanceExecution = RebalanceExecution.SIMULTANEOUS,
    capital: CapitalRule | None = None,
    max_names: int | None = None,
) -> RebalancingStrategy:
    return RebalancingStrategy(
        "RB",
        assets=(A, B, C),
        model=Fixed(build),
        triggers=(DriftTrigger(0.02),),
        capital=capital or FixedCapital(CAPITAL),
        quantum=Decimal("1"),
        terms=MARKET,
        execution=execution,
        max_names=max_names,
    )


def slice_at(instant: float) -> SliceClosed:
    return SliceClosed(event_id=f"slice-{instant}", timestamp=instant, assets=(A, B, C))


def fill(asset: str, quantity: Decimal) -> FillEvent:
    side = "buy" if quantity > 0 else "sell"
    return FillEvent(
        event_id="F",
        timestamp=0.0,
        order_id="O",
        instrument=asset,
        fill_quantity=abs(quantity),
        fill_price=PRICES[asset],
        side=side,
        attributed_quantity=quantity,
    )


def targets(intents: Any) -> dict[str, Decimal]:
    out = {}
    for intent in intents:
        assert intent.kind is IntentKind.TARGET_QUANTITY
        out[intent.instrument] = intent.target
    return out


@dataclass
class Desk:
    """Allocation and the OMS in miniature, for one strategy.

    A target becomes working quantity for its difference from what the
    strategy holds, as allocation sizes a target against the strategy's
    position and its working orders; a fill moves quantity from working to held.
    """

    rebalancer: RebalancingStrategy
    orders: Orders = field(default_factory=Orders)

    def slice(self, instant: float, prices: Mapping[str, Decimal] = PRICES) -> dict[str, Decimal]:
        intents = list(
            self.rebalancer.on_slice(context(prices, orders=self.orders), slice_at(instant))
        )
        sent = targets(intents)
        for asset, target in sent.items():
            remaining = target - self.rebalancer.holdings.get(asset, Decimal("0"))
            if remaining:
                self.orders.working[asset] = remaining
            else:
                self.orders.working.pop(asset, None)
        return sent

    def hold(self, asset: str, quantity: Decimal) -> None:
        """A book filled before the test began: no order of the desk's."""

        self.rebalancer.on_fill(context(orders=self.orders), fill(asset, quantity))

    def fill(self, asset: str, quantity: Decimal) -> None:
        self.rebalancer.on_fill(context(orders=self.orders), fill(asset, quantity))
        remaining = self.orders.working.get(asset, Decimal("0")) - quantity
        if remaining:
            self.orders.working[asset] = remaining
        else:
            self.orders.working.pop(asset, None)


# --------------------------------------------------------------------------- #
# Its book, and what it sends
# --------------------------------------------------------------------------- #


def test_its_book_is_its_share_of_each_fill() -> None:
    rebalancer = strategy()
    rebalancer.on_fill(context(), fill(A, Decimal("10")))
    rebalancer.on_fill(context(), fill(A, Decimal("-4")))
    unattributed = FillEvent(
        event_id="F",
        timestamp=0.0,
        order_id="O",
        instrument=B,
        fill_quantity=Decimal("3"),
        fill_price=PRICES[B],
        side="sell",
    )
    rebalancer.on_fill(context(), unattributed)
    assert rebalancer.holdings == {A: Decimal("6"), B: Decimal("-3")}
    rebalancer.on_fill(context(), fill(A, Decimal("-6")))
    assert rebalancer.holdings == {B: Decimal("-3")}


def test_a_certified_answer_is_sent_as_lot_rounded_targets() -> None:
    rebalancer = strategy()
    intents = list(rebalancer.on_slice(context(), slice_at(1.0)))
    # Minimum variance over independent assets: weights inverse to variance,
    # 25 : 11.1 : 6.25, so 0.5902, 0.2623 and 0.1475 of 100,000, at 50, 100 and
    # 25, rounded toward zero to whole units.
    sent = targets(intents)
    assert sent == {A: Decimal("1180"), B: Decimal("262"), C: Decimal("590")}
    record = rebalancer.records[0]
    assert record.status is ConstructionStatus.OPTIMAL and record.traded
    assert record.targets == sent and record.deferred == ()
    assert {intent.correlation_id for intent in intents} == {"RB:rebalance:1"}
    assert intents[0].metadata["problem_id"] == record.problem_id
    assert rebalancer.target == record.weights


def test_a_held_asset_the_problem_leaves_is_sold_to_nothing() -> None:
    def two(view: RebalanceView) -> ConstructionProblem:
        return ConstructionProblem(
            covariance((A, B)),
            MinimumVariance(),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, 1.0)),
            SETTINGS,
        )

    rebalancer = strategy(two)
    rebalancer.on_fill(context(), fill(C, Decimal("100")))
    sent = targets(rebalancer.on_slice(context(), slice_at(1.0)))
    assert sent[C] == Decimal("0") and set(sent) == {A, B, C}


def test_sells_first_holds_the_purchases_until_every_sale_has_filled() -> None:
    desk = Desk(strategy(execution=RebalanceExecution.SELLS_FIRST))
    # It holds 2,000 of C and nothing else: C is sold, A and B bought.
    desk.hold(C, Decimal("2000"))
    assert desk.slice(1.0) == {C: Decimal("590")}
    assert desk.rebalancer.records[0].deferred == (A, B)
    # Nothing goes while the sale works, and the drift trigger does not take the
    # rebalance in progress for a distance from its own target ...
    assert desk.slice(2.0) == {}
    desk.fill(C, Decimal("-1000"))
    assert desk.slice(3.0) == {}
    # ... and the purchases go at the slice after the last of the sale.
    desk.fill(C, Decimal("-410"))
    assert desk.slice(4.0) == {A: Decimal("1180"), B: Decimal("262")}
    assert desk.slice(5.0) == {}
    assert len(desk.rebalancer.records) == 1


def test_a_later_rebalance_replaces_purchases_still_waiting() -> None:
    caps = iter([1.0, 0.5])
    desk = Desk(
        strategy(
            lambda view: minimum_variance(view, next(caps)),
            execution=RebalanceExecution.SELLS_FIRST,
        )
    )
    desk.hold(C, Decimal("2000"))
    desk.slice(1.0)
    # C rises far enough before its sale fills for the drift trigger to fire:
    # a second rebalance, whose own sale replaces the first's purchases.
    moved = {A: Decimal("50"), B: Decimal("100"), C: Decimal("40")}
    second = desk.slice(2.0, moved)
    record = desk.rebalancer.records[1]
    assert record.deferred == (A, B) and second == {C: record.targets[C]}
    desk.fill(C, record.targets[C] - Decimal("2000"))  # the second sale fills
    released = list(desk.rebalancer.on_slice(context(moved, orders=desk.orders), slice_at(3.0)))
    assert targets(released) == {A: record.targets[A], B: record.targets[B]}
    assert {intent.correlation_id for intent in released} == {"RB:rebalance:2"}


def test_simultaneous_sends_every_target_at_once() -> None:
    rebalancer = strategy()
    rebalancer.on_fill(context(), fill(C, Decimal("2000")))
    assert set(targets(rebalancer.on_slice(context(), slice_at(1.0)))) == {A, B, C}


def test_a_book_holding_an_unpriced_asset_is_not_evaluated() -> None:
    rebalancer = strategy()
    rebalancer.on_fill(context(), fill(C, Decimal("10")))
    unpriced = {A: PRICES[A], B: PRICES[B]}
    assert list(rebalancer.on_slice(context(prices=unpriced), slice_at(1.0))) == []
    assert rebalancer.records == ()


def test_a_model_that_declines_trades_and_records_nothing() -> None:
    rebalancer = strategy(lambda view: None)
    assert list(rebalancer.on_slice(context(), slice_at(1.0))) == []
    assert rebalancer.records == () and rebalancer.target is None


# --------------------------------------------------------------------------- #
# Certified answers only
# --------------------------------------------------------------------------- #


def test_a_construction_that_is_not_optimal_records_why_and_sends_nothing() -> None:
    rebalancer = strategy(lambda view: minimum_variance(view, cap=0.2))
    assert list(rebalancer.on_slice(context(), slice_at(1.0))) == []
    record = rebalancer.records[0]
    assert record.status is ConstructionStatus.INFEASIBLE and not record.traded
    assert rebalancer.target is None


def test_a_cardinality_limit_is_kept_or_nothing_is_sent() -> None:
    limited = strategy(max_names=2)
    sent = targets(limited.on_slice(context(), slice_at(1.0)))
    assert sum(1 for quantity in sent.values() if quantity) == 2
    assert limited.records[0].cardinality is CardinalityOutcome.RESTRICTED
    impossible = strategy(lambda view: minimum_variance(view, cap=0.4), max_names=2)
    assert list(impossible.on_slice(context(), slice_at(1.0))) == []
    assert impossible.records[0].cardinality is CardinalityOutcome.UNRESOLVED


# --------------------------------------------------------------------------- #
# What it refuses
# --------------------------------------------------------------------------- #


def turnover_from(book: Mapping[str, float]) -> Callable[[RebalanceView], ConstructionProblem]:
    def build(view: RebalanceView) -> ConstructionProblem:
        return ConstructionProblem(
            covariance(),
            MinimumVariance(),
            ConstraintSet(
                ExposureRange.exactly(1.0),
                WeightBounds(0.0, 1.0),
                turnover=TurnoverLimit(dict(book) or dict(view.weights), 2.0),
            ),
            SETTINGS,
        )

    return build


def test_a_turnover_limit_from_the_book_is_taken_and_from_another_refused() -> None:
    assert strategy(turnover_from({})).on_slice(context(), slice_at(1.0))
    other = strategy(turnover_from({A: 0.5, B: 0.5, C: 0.0}))
    with pytest.raises(RebalancingInputError, match="turnover start from a book other"):
        list(other.on_slice(context(), slice_at(1.0)))


def test_costs_from_another_book_are_refused() -> None:
    mu = ExpectedReturns({A: 0.01, B: 0.02, C: 0.015}, "USD", "1M", "unit")

    def build(view: RebalanceView) -> ConstructionProblem:
        costs = LinearCosts({A: 0.2, B: 0.2, C: 0.6}, dict.fromkeys((A, B, C), 0.001))
        return ConstructionProblem(
            covariance(),
            MaximumReturn(mu, costs),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, 0.5)),
            SETTINGS,
        )

    with pytest.raises(RebalancingInputError, match="costs start from a book other"):
        list(strategy(build).on_slice(context(), slice_at(1.0)))


def test_a_turnover_limit_that_would_sell_a_held_asset_unseen_is_refused() -> None:
    def build(view: RebalanceView) -> ConstructionProblem:
        return ConstructionProblem(
            covariance((A, B)),
            MinimumVariance(),
            ConstraintSet(
                ExposureRange.exactly(1.0),
                WeightBounds(0.0, 1.0),
                turnover=TurnoverLimit({A: view.weights[A], B: view.weights[B]}, 2.0),
            ),
            SETTINGS,
        )

    rebalancer = strategy(build)
    rebalancer.on_fill(context(), fill(C, Decimal("10")))
    with pytest.raises(RebalancingInputError, match="turnover the limit cannot see"):
        list(rebalancer.on_slice(context(), slice_at(1.0)))


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda view: "a problem", "not a ConstructionProblem"),
        (
            lambda view: ConstructionProblem(
                CovarianceMatrix.from_rows(
                    (A, B, "ZZZ"),
                    [[0.04, 0, 0], [0, 0.09, 0], [0, 0, 0.16]],
                    currency="USD",
                    period="1M",
                    source="unit",
                    observations=None,
                ),
                MinimumVariance(),
                ConstraintSet(ExposureRange.exactly(1.0), WeightBounds(0.0, 1.0)),
                SETTINGS,
            ),
            r"weights \['ZZZ'\], which the strategy was not given",
        ),
    ],
    ids=["not-a-problem", "outside"],
)
def test_a_model_whose_problem_is_not_one_it_can_trade_is_refused(
    build: Callable[[RebalanceView], Any], reason: str
) -> None:
    with pytest.raises(RebalancingInputError, match=reason):
        list(strategy(build).on_slice(context(), slice_at(1.0)))


def test_a_universe_asset_without_a_price_is_refused() -> None:
    unpriced = {A: PRICES[A], B: PRICES[B]}
    with pytest.raises(RebalancingInputError, match="no price at this slice"):
        list(strategy().on_slice(context(prices=unpriced), slice_at(1.0)))


def test_an_asset_the_run_does_not_declare_is_refused_and_a_future_needs_its_economics() -> None:
    undeclared = {asset: record for asset, record in RECORDS.items() if asset != C}
    with pytest.raises(RebalancingInputError, match="not in the run's instrument registry"):
        list(strategy().on_slice(context(records=undeclared), slice_at(1.0)))
    future = InstrumentRecord("RSF", AssetType.FUTURE, "XCME", "USD")
    records = {**undeclared, C: future}
    with pytest.raises(RebalancingInputError, match="declares no economics"):
        list(strategy().on_slice(context(records=records), slice_at(1.0)))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"assets": ()}, "at least one asset"),
        ({"assets": (A, A)}, "names an asset twice"),
        ({"model": object()}, "not a ConstructionModel"),
        ({"triggers": ()}, "at least one trigger"),
        ({"triggers": (object(),)}, "is not a Trigger"),
        ({"capital": object()}, "not a CapitalRule"),
        ({"quantum": Decimal("0")}, "positive Decimal"),
        ({"terms": "market"}, "must be OrderTerms"),
        ({"execution": "simultaneous"}, "must be a RebalanceExecution"),
        ({"max_names": 0}, "positive integer or None"),
    ],
    ids=[
        "no-assets",
        "twice",
        "model",
        "no-trigger",
        "trigger",
        "capital",
        "quantum",
        "terms",
        "execution",
        "names",
    ],
)
def test_a_malformed_strategy_is_refused(change: dict[str, Any], reason: str) -> None:
    arguments: dict[str, Any] = {
        "assets": (A, B, C),
        "model": Fixed(minimum_variance),
        "triggers": (ThresholdTrigger(0.05),),
        "capital": FixedCapital(CAPITAL),
        "quantum": Decimal("1"),
        "terms": MARKET,
        "execution": RebalanceExecution.SIMULTANEOUS,
        **change,
    }
    with pytest.raises(RebalancingInputError, match=reason):
        RebalancingStrategy("RB", **arguments)


# --------------------------------------------------------------------------- #
# Its state
# --------------------------------------------------------------------------- #


def test_its_state_travels_through_the_encoder_and_back_exactly() -> None:
    desk = Desk(strategy(execution=RebalanceExecution.SELLS_FIRST))
    desk.hold(C, Decimal("2000"))
    desk.slice(1.0)
    desk.slice(2.0)
    payload = deserialize(serialize(desk.rebalancer.capture_state()))
    fresh = strategy(execution=RebalanceExecution.SELLS_FIRST)
    fresh.restore_state(payload, desk.rebalancer.strategy_state_version())
    assert fresh.records == desk.rebalancer.records
    assert fresh.holdings == desk.rebalancer.holdings and fresh.target == desk.rebalancer.target
    assert fresh.capture_state() == desk.rebalancer.capture_state()
    # And it goes on as the original does: the waiting purchases are released.
    other = Desk(fresh, Orders(dict(desk.orders.working)))
    desk.fill(C, Decimal("-1410"))
    other.fill(C, Decimal("-1410"))
    assert other.slice(3.0) == desk.slice(3.0) == {A: Decimal("1180"), B: Decimal("262")}


def test_state_from_another_version_or_strategy_is_refused() -> None:
    rebalancer = strategy()
    payload = deserialize(serialize(rebalancer.capture_state()))
    with pytest.raises(RebalancingInputError, match="reads state version 1"):
        strategy().restore_state(payload, 2)
    two = RebalancingStrategy(
        "RB",
        assets=(A, B, C),
        model=Fixed(minimum_variance),
        triggers=(ThresholdTrigger(0.05), DriftTrigger(0.1)),
        capital=FixedCapital(CAPITAL),
        quantum=Decimal("1"),
        terms=MARKET,
        execution=RebalanceExecution.SIMULTANEOUS,
    )
    with pytest.raises(RebalancingInputError, match="captured from another strategy"):
        two.restore_state(payload, 1)


# --------------------------------------------------------------------------- #
# Capital
# --------------------------------------------------------------------------- #


def test_fixed_capital_is_what_it_states() -> None:
    assert (
        FixedCapital(CAPITAL).amount(context(), PRICES, dict.fromkeys(PRICES, Decimal("1")))
        == CAPITAL
    )
    for bad in (Decimal("0"), Decimal("-1"), Decimal("NaN"), 100):
        with pytest.raises(RebalancingInputError):
            FixedCapital(bad)  # type: ignore[arg-type]


def test_account_equity_marks_the_accounts_holdings_of_the_strategys_assets() -> None:
    account = Account(Decimal("1000"), {A: Decimal("10"), B: Decimal("-2"), "OTHER": Decimal("99")})
    rule = AccountEquity("USD")
    multipliers = dict.fromkeys(PRICES, Decimal("1"))
    # 1,000 + 10 * 50 - 2 * 100: the other asset is not the strategy's and is not counted.
    assert rule.amount(context(account=account), PRICES, multipliers) == Decimal("1300")
    empty = Account(Decimal("-5"))
    assert rule.amount(context(account=empty), PRICES, multipliers) is None
    with pytest.raises(RebalancingInputError):
        AccountEquity(" ")
