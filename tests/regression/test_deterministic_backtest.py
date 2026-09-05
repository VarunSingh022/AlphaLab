"""Regression guard for run-to-run determinism, added in v2.2.

Every identifier on the execution path -- event ids, execution ids, order ids,
transaction ids -- came from ``uuid4``. Quantities reproduced across runs;
identities never did, so "the same backtest twice" could only ever be compared
on P&L, not on the orders and fills that produced it.

v2.2 makes the identifier source explicit and scoped (:mod:`alphalab.common.ids`)
and records the seed on the run's configuration. A seeded run reproduces field
for field; an unseeded one keeps ``uuid4``, and these tests pin that too, so the
default is not silently made deterministic.
"""

from decimal import Decimal
from uuid import uuid4

from alphalab.backtesting import BacktestEngine, BacktestResult, ReplayBacktest
from alphalab.common.ids import DeterministicIdSource, new_id, use_id_source
from alphalab.oms.snapshot import capture
from alphalab.persistence.serializer import serialize
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

MIDS = [
    Decimal("100.005"),
    Decimal("101.007"),
    Decimal("99.003"),
    Decimal("102.001"),
    Decimal("98.009"),
]
PLAN = {2.0: Decimal("10.5"), 4.0: Decimal("-4.25"), 6.0: Decimal("2.75")}


def _run(strategy_id: str, asset_id: str, seed: int | None) -> BacktestResult:
    return BacktestEngine.run(
        backtest_config(strategy_id, seed=seed),
        dataset_of_quotes(asset_id, MIDS),
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN)),
        context_factory,
    )


def _fingerprint(result: BacktestResult) -> str:
    """Everything a run produced, as one comparable payload."""

    return serialize(
        {
            "oms": capture(result.state.oms),
            "portfolio": result.state.portfolio,
            "fills": result.fills,
            "trades": result.trades,
            "equity_curve": result.equity_curve,
            "report": result.report,
        }
    )


# ---------------------------------------------------------------------------
# The identifier source itself
# ---------------------------------------------------------------------------


def test_a_seeded_source_reproduces_its_stream() -> None:
    with use_id_source(DeterministicIdSource(11)):
        first = [str(new_id()) for _ in range(5)]
    with use_id_source(DeterministicIdSource(11)):
        second = [str(new_id()) for _ in range(5)]

    assert first == second
    assert len(set(first)) == 5


def test_different_seeds_produce_different_streams() -> None:
    with use_id_source(DeterministicIdSource(1)):
        first = [str(new_id()) for _ in range(5)]
    with use_id_source(DeterministicIdSource(2)):
        second = [str(new_id()) for _ in range(5)]

    assert first != second


def test_the_source_is_restored_when_the_scope_ends() -> None:
    with use_id_source(DeterministicIdSource(3)):
        seeded = str(new_id())

    assert str(new_id()) != seeded


def test_the_scope_nests() -> None:
    with use_id_source(DeterministicIdSource(3)):
        outer_first = str(new_id())
        with use_id_source(DeterministicIdSource(99)):
            inner = str(new_id())
        outer_second = str(new_id())

    with use_id_source(DeterministicIdSource(3)):
        expected = [str(new_id()) for _ in range(2)]

    assert [outer_first, outer_second] == expected
    assert inner not in expected


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def test_the_same_seeded_backtest_reproduces_exactly() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())

    first = _run(strategy_id, asset_id, seed=4242)
    second = _run(strategy_id, asset_id, seed=4242)

    assert _fingerprint(first) == _fingerprint(second)


def test_repeated_runs_agree_on_order_and_fill_identities() -> None:
    """Not just the numbers: the actual orders and fills."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())

    runs = [_run(strategy_id, asset_id, seed=7) for _ in range(4)]
    identities = {tuple(str(order.order_id.value) for order in run.orders) for run in runs}
    fill_ids = {tuple(str(fill.fill_id) for fill in run.fills) for run in runs}

    assert len(identities) == 1
    assert len(fill_ids) == 1
    assert len(next(iter(identities))) == 3


def test_repeated_runs_agree_on_the_portfolio_and_analytics() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())

    runs = [_run(strategy_id, asset_id, seed=7) for _ in range(3)]

    assert len({run.valuation for run in runs}) == 1
    assert len({run.valuation.realized_pnl for run in runs}) == 1
    assert len({run.valuation.unrealized_pnl for run in runs}) == 1
    assert len({serialize(run.report) for run in runs}) == 1


def test_a_different_seed_changes_identities_but_not_the_economics() -> None:
    """The seed names identifiers; it must not move a single number."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())

    first = _run(strategy_id, asset_id, seed=1)
    second = _run(strategy_id, asset_id, seed=2)

    assert [str(o.order_id.value) for o in first.orders] != [
        str(o.order_id.value) for o in second.orders
    ]
    assert first.valuation.equity == second.valuation.equity
    assert first.valuation.realized_pnl == second.valuation.realized_pnl
    assert first.valuation.cash == second.valuation.cash
    assert [f.quantity for f in first.fills] == [f.quantity for f in second.fills]
    assert [f.price for f in first.fills] == [f.price for f in second.fills]


def test_an_unseeded_run_still_reproduces_its_economics() -> None:
    """Determinism of quantities never depended on the seed, and still does not."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())

    first = _run(strategy_id, asset_id, seed=None)
    second = _run(strategy_id, asset_id, seed=None)

    assert first.valuation.equity == second.valuation.equity
    assert [f.quantity for f in first.fills] == [f.quantity for f in second.fills]


def test_an_unseeded_run_does_not_claim_reproducible_identities() -> None:
    """The source of nondeterminism stays visible rather than being hidden."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())

    first = _run(strategy_id, asset_id, seed=None)
    second = _run(strategy_id, asset_id, seed=None)

    assert first.seed is None
    assert [str(o.order_id.value) for o in first.orders] != [
        str(o.order_id.value) for o in second.orders
    ]


def test_a_seeded_replay_reproduces_exactly() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    config = backtest_config(strategy_id, seed=555)
    dataset = dataset_of_quotes(asset_id, MIDS)

    runs = [
        ReplayBacktest.run(
            config,
            dataset,
            running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, PLAN)),
            context_factory,
        )
        for _ in range(3)
    ]

    assert len({_fingerprint(run.backtest) for run in runs}) == 1
    assert {run.replay_status for run in runs} == {"COMPLETED"}
    assert {run.records_replayed for run in runs} == {len(MIDS)}
