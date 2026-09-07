"""``Position.opened_at``, the holding period derived from it, and absent sectors.

``holding_period_seconds`` was hard-coded ``0.0`` and ``sector_id``
``"UNCLASSIFIED"``. They failed differently and were fixed differently: the
holding period is *derivable* from state the portfolio engine already had, while
in v2.6 no security master existed anywhere in this repository, so a sector was
not knowable at all. One became a measurement; the other became an explicit
absence.

v2.11 supplies the missing authority -- an operator classifies an instrument
through :func:`alphalab.instrument.registry.classify_instrument`, and the
pipeline freezes that classification onto each fill (ADR-0027). The absence rule
is unchanged and is what the tests below still pin: a run with no registry, or
one whose asset is unregistered or unclassified, reports ``None`` and an empty
breakdown rather than a fictional bucket.

``opened_at`` is state rather than something read back from the event log,
because a reversal emits ``PositionReduced`` -- not ``PositionClosed`` followed
by ``PositionOpened`` -- so the log cannot distinguish a reversal from a partial
reduction without replaying running quantity and watching for a sign change.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from alphalab.analytics.attribution import TradeRecord, calculate_attribution
from alphalab.analytics.summary import calculate_trade_metrics
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    equity,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

PRICE = Decimal("100")


def _fresh() -> PortfolioState:
    return PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("ACC", "USD", "Holding", 1.0)),
        Decimal("1000000"),
        "USD",
        1.0,
    )


def _fill(
    state: PortfolioState, quantity: Decimal, timestamp: float, price: Decimal = PRICE
) -> PortfolioState:
    return PortfolioEngine.apply_fill(
        state, "AAPL", quantity, price, Decimal("0"), timestamp, "USD"
    )


def _opened_at(state: PortfolioState) -> float | None:
    return state.positions["AAPL"].opened_at


# ---------------------------------------------------------------------------
# The six transitions
# ---------------------------------------------------------------------------


def test_opening_from_flat_records_the_fill_timestamp() -> None:
    assert _opened_at(_fill(_fresh(), Decimal("10"), 2.0)) == 2.0


def test_increasing_a_position_leaves_it_unchanged() -> None:
    state = _fill(_fill(_fresh(), Decimal("10"), 2.0), Decimal("5"), 7.0)

    assert _opened_at(state) == 2.0
    assert state.positions["AAPL"].quantity == Decimal("15")


def test_repeatedly_increasing_a_position_leaves_it_unchanged() -> None:
    state = _fresh()
    state = _fill(state, Decimal("10"), 2.0)
    for timestamp in (3.0, 4.0, 5.0):
        state = _fill(state, Decimal("1"), timestamp)

    assert _opened_at(state) == 2.0


def test_partially_reducing_a_position_leaves_it_unchanged() -> None:
    """The remaining quantity has been held since the position opened."""

    state = _fill(_fill(_fresh(), Decimal("10"), 2.0), Decimal("-4"), 9.0)

    assert _opened_at(state) == 2.0
    assert state.positions["AAPL"].quantity == Decimal("6")


def test_closing_a_position_removes_it_entirely() -> None:
    state = _fill(_fill(_fresh(), Decimal("10"), 2.0), Decimal("-10"), 9.0)

    assert "AAPL" not in state.positions


def test_a_reversal_replaces_it_with_the_reversing_fill() -> None:
    """The long closed and a short opened in one fill, and says so."""

    state = _fill(_fill(_fresh(), Decimal("10"), 2.0), Decimal("-25"), 9.0)

    assert state.positions["AAPL"].quantity == Decimal("-15")
    assert _opened_at(state) == 9.0, "carrying the long's open time would be a false number"


def test_a_short_to_long_reversal_replaces_it_too() -> None:
    state = _fill(_fill(_fresh(), Decimal("-10"), 2.0), Decimal("25"), 9.0)

    assert state.positions["AAPL"].quantity == Decimal("15")
    assert _opened_at(state) == 9.0


def test_a_reopened_position_gets_the_new_open_time() -> None:
    state = _fill(_fill(_fresh(), Decimal("10"), 2.0), Decimal("-10"), 9.0)
    state = _fill(state, Decimal("4"), 20.0)

    assert _opened_at(state) == 20.0


def test_a_hand_built_position_reports_an_unrecorded_open_time() -> None:
    from alphalab.portfolio.position import Position

    position = Position("AAPL", Decimal("1"), PRICE, PRICE, Decimal("0"), "USD", 1.0)

    assert position.opened_at is None


# ---------------------------------------------------------------------------
# The holding period the pipeline derives from it
# ---------------------------------------------------------------------------


def _records(plan: dict[float, Decimal]) -> tuple[TradeRecord, ...]:
    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )
    for timestamp in sorted(plan):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, PRICE), context_factory
        ).state
    return tuple(state.trade_records)


def test_an_opening_fill_has_no_holding_period() -> None:
    """It held nothing; ``0.0`` would be a measurement never made."""

    (record,) = _records({2.0: Decimal("10")})

    assert record.holding_period_seconds is None


def test_an_increasing_fill_has_no_holding_period() -> None:
    records = _records({2.0: Decimal("10"), 5.0: Decimal("5")})

    assert [r.holding_period_seconds for r in records] == [None, None]


def test_a_closing_fill_measures_from_the_open() -> None:
    opening, closing = _records({2.0: Decimal("10"), 11.0: Decimal("-10")})

    assert opening.holding_period_seconds is None
    assert closing.holding_period_seconds == 9.0


def test_a_reducing_fill_measures_from_the_open() -> None:
    _opening, reducing = _records({2.0: Decimal("10"), 8.5: Decimal("-4")})

    assert reducing.holding_period_seconds == 6.5


def test_the_average_holding_period_ignores_fills_that_held_nothing() -> None:
    """Previously a mean of zeros; now a mean over the fills that measured one."""

    records = _records({2.0: Decimal("10"), 11.0: Decimal("-10")})
    metrics = calculate_trade_metrics(
        profits=tuple(r.realized_pnl for r in records),
        holding_periods=tuple(r.holding_period_seconds for r in records),
        total_traded_notional=Decimal("2000"),
        average_equity=Decimal("1000000"),
    )

    assert metrics.avg_holding_period == 9.0


def test_the_average_is_zero_when_nothing_measured_one() -> None:
    metrics = calculate_trade_metrics(
        profits=(Decimal("1"),),
        holding_periods=(None,),
        total_traded_notional=Decimal("1"),
        average_equity=Decimal("1"),
    )

    assert metrics.avg_holding_period == 0.0


# ---------------------------------------------------------------------------
# Sector
# ---------------------------------------------------------------------------


def test_the_pipeline_reports_no_sector_at_all() -> None:
    """Unchanged by v2.11, and still exactly right: this run configures no registry.

    v2.11 lets a registry classify an instrument and reads that classification
    onto each fill, but ``pipeline_config`` leaves ``instruments`` at ``None``,
    which is the configuration this test has always described. ADR-0016's
    invariant 9 is superseded only for the case where a registry *is* configured
    and *does* classify the asset -- see
    ``tests/regression/test_sector_classification_reaches_attribution.py`` and
    :func:`test_the_pipeline_reports_a_sector_when_the_registry_declares_one`
    below.
    """

    records = _records({2.0: Decimal("10"), 11.0: Decimal("-10")})

    assert all(record.sector_id is None for record in records)


def test_the_pipeline_reports_a_sector_when_the_registry_declares_one() -> None:
    """The other half of the same rule, added in v2.11. See ADR-0027."""

    apple = equity("AAPL", "Technology")
    strategy_id = "MOMENTUM"
    plan = {2.0: Decimal("10"), 11.0: Decimal("-10")}
    config = replace(
        pipeline_config(strategy_id, Decimal("1000000")), instruments=registry_of(apple)
    )
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, apple.asset_id, plan)),
        1.0,
    )
    for timestamp in sorted(plan):
        state = ExecutionPipeline.process_quote(
            state, quote(apple.asset_id, timestamp, PRICE), context_factory
        ).state

    assert [record.sector_id for record in state.trade_records] == ["Technology", "Technology"]


def test_an_absent_sector_produces_an_empty_breakdown_not_a_bucket() -> None:
    records = _records({2.0: Decimal("10"), 11.0: Decimal("-10")})
    metrics = calculate_attribution(records)

    assert metrics.pnl_by_sector == {}
    assert "UNCLASSIFIED" not in metrics.pnl_by_sector
    assert metrics.pnl_by_asset, "the asset breakdown is still real"


def test_a_caller_supplied_sector_still_produces_a_breakdown() -> None:
    """No security master is added; a caller who has one is not prevented."""

    trades = (
        TradeRecord("T1", "AAPL", "TECH", Decimal("100"), Decimal("1000"), 60.0),
        TradeRecord("T2", "JPM", "FIN", Decimal("-25"), Decimal("500"), 60.0),
        TradeRecord("T3", "MSFT", None, Decimal("7"), Decimal("100"), 60.0),
    )
    metrics = calculate_attribution(trades)

    assert metrics.pnl_by_sector == {"TECH": Decimal("100"), "FIN": Decimal("-25")}
    assert "MSFT" in metrics.pnl_by_asset, "the unsectored trade is still counted by asset"
