"""ACC-004: a run's books do not depend on the caller's decimal context.

``Decimal`` arithmetic rounds to the thread's current context, and so does
``quantize`` unless told otherwise. Measured at v3.9.0, one round trip booked
cash ``999603.30`` under the default context and ``999603.31`` after
``getcontext().rounding = ROUND_DOWN``; a caller that lowered the precision got
an ``InvalidOperation`` from deep inside a fill. v3.9 pinned four modules; v3.10
pins the whole accounting and execution path to
:data:`~alphalab.common.arithmetic.ACCOUNTING_CONTEXT`.

The oracle is the strongest surface there is: a whole backtest -- quoted spread,
square-root impact, percentage commission, a proportional fee and a buy-side
tax, partial reversals -- serialized with ``serialize(capture(...))``, which
must be **byte-identical** under every hostile ambient context to the run under
the default one. A field added to a captured state joins the oracle
automatically.
"""

from __future__ import annotations

import decimal
import uuid
from decimal import Decimal

import pytest

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.common.ids import id_scope
from alphalab.core.enums import Side
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    NoSlippage,
    ProportionalFee,
    ProportionalTax,
    QuotedHalfSpread,
    SquareRootImpact,
)
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.persistence import serialize
from alphalab.runtime.run_snapshot import capture
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)

ASSET = str(uuid.UUID(int=0xACC004))
STRATEGY = "AMBIENT"

#: Built once, in the default context: the inputs are data, not arithmetic the
#: library performs, and a hostile context must not reach them.
QUOTES = tuple(
    sized_quote(
        ASSET,
        2.0 + index,
        Decimal("100.005") + Decimal(index) / Decimal(7),
        Decimal("333"),
        Decimal("0.013"),
    )
    for index in range(12)
)
PLAN = {
    2.0: Decimal("7.3333"),
    4.0: Decimal("-2.1"),
    6.0: Decimal("-9"),
    8.0: Decimal("5.55"),
    10.0: Decimal("-1.7833"),
}
SIMULATOR = ExecutionSimulator(
    cost_model=ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=SquareRootImpact(Decimal("0.1")),
        commission_model=PercentageCommission(Decimal("0.00123")),
        fee_model=ProportionalFee(Decimal("0.0000345")),
        tax_model=ProportionalTax(Decimal("0.005"), frozenset({Side.BUY})),
    )
)


def _run() -> str:
    config = backtest_config(STRATEGY, simulator=SIMULATOR)
    dataset = MarketDataset.of("DS", QUOTES)
    strategies = running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, PLAN))
    with id_scope(5):
        result = BacktestEngine.run(config, dataset, strategies, context_factory)
    return serialize(capture(result.run))


REFERENCE = _run()


@pytest.mark.parametrize(
    ("rounding", "precision"),
    [
        (decimal.ROUND_DOWN, 28),
        (decimal.ROUND_HALF_UP, 28),
        (decimal.ROUND_UP, 50),
        (decimal.ROUND_CEILING, 12),
        (decimal.ROUND_FLOOR, 9),
    ],
)
def test_a_backtest_is_byte_identical_under_any_ambient_context(
    rounding: str, precision: int
) -> None:
    with decimal.localcontext() as context:
        context.rounding = rounding
        context.prec = precision
        assert _run() == REFERENCE


def test_the_run_leaves_the_callers_context_as_it_found_it() -> None:
    """Pinning is done on copies: the caller's own context is never modified."""

    with decimal.localcontext() as context:
        context.rounding = decimal.ROUND_DOWN
        context.prec = 17
        _run()
        assert decimal.getcontext().rounding == decimal.ROUND_DOWN
        assert decimal.getcontext().prec == 17
