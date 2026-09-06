"""INV-3: nothing reports a strategy that no strategy declared.

    No value that no strategy declared is ever reported through a field named
    ``strategy_id``, or used as a key in the OMS strategy index.

Until v2.6 allocation stamped every request it produced -- including one from a
single intent, with no netting at all -- with ``"ALLOC-NETTED"``. That value was
reported by four public surfaces: ``OrderRequest.strategy_id``,
``oms.Order.strategy_id`` through ``oms.views.orders()``,
``ExecutionReport.strategy_id``, and ``OrderBook._by_strategy``, whose
``orders_for_strategy`` answered for the fiction and returned nothing for the
strategy that actually ran.

The OMS architecture is unchanged: ``_by_strategy`` is still a single-valued
index and ``orders_for_strategy`` is neither renamed nor deprecated. What
changed is that the pipeline no longer writes a strategy identity it does not
have. See ADR-0015 decision 4.
"""

import ast
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.book import OrderBook
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.state import OMSState
from alphalab.oms.views import orders, orders_for_strategy
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    running_strategy_state,
)

PRICE = Decimal("100")
_SOURCE = Path(__file__).resolve().parents[2] / "alphalab"


def _order(strategy_id: str, asset_id: str = "AAPL") -> Order:
    return Order(
        OrderId(uuid4()),
        strategy_id,
        asset_id,
        Side.BUY,
        OrderType.MARKET,
        OrderStatus.NEW,
        Decimal("10"),
        Decimal("0"),
        Decimal("10"),
        None,
        None,
        Decimal("0"),
        1.0,
        1.0,
    )


def _pipeline_run(strategy_id: str = "MOMENTUM") -> object:
    asset_id = str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
        1.0,
    )
    return ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, PRICE), context_factory)


# ---------------------------------------------------------------------------
# The fabricated value is gone
# ---------------------------------------------------------------------------


def _docstrings(tree: ast.Module) -> set[int]:
    """Node ids of every string that is a docstring rather than a value."""

    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def test_the_sentinel_can_no_longer_be_produced() -> None:
    """Deleted as a *value*, not merely stopped being written.

    Docstrings and comments still name it, because explaining what a field used
    to carry is how the next reader learns why it does not any more. What must
    not exist is an evaluable string: the AST is walked so that comments are
    excluded structurally and docstrings by identity, and anything left is a
    literal something could assign.
    """

    offenders: list[str] = []
    for path in _SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        offenders.extend(
            f"{path.relative_to(_SOURCE.parent)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "ALLOC-NETTED" in node.value
            and id(node) not in skip
        )

    assert not offenders, f"the fabricated strategy id is still evaluable at: {offenders}"


def test_no_public_surface_reports_a_strategy_the_pipeline_invented() -> None:
    """A single strategy, no netting at all -- every surface used to say otherwise."""

    result = _pipeline_run()

    assert result.order_requests[0].strategy_id == ""  # type: ignore[attr-defined]
    assert orders(result.state.oms)[0].strategy_id == ""  # type: ignore[attr-defined]
    assert result.execution_reports[0].strategy_id == ""  # type: ignore[attr-defined]


def test_the_real_strategy_is_still_recoverable_from_contributions() -> None:
    result = _pipeline_run("MOMENTUM")
    (contribution,) = result.order_requests[0].contributions  # type: ignore[attr-defined]

    assert contribution.strategy_id == "MOMENTUM"


# ---------------------------------------------------------------------------
# The OMS strategy index
# ---------------------------------------------------------------------------


def test_an_order_declaring_no_strategy_is_not_indexed() -> None:
    result = _pipeline_run()

    assert orders_for_strategy(result.state.oms, "") == ()  # type: ignore[attr-defined]
    assert orders_for_strategy(result.state.oms, "ALLOC-NETTED") == ()  # type: ignore[attr-defined]
    assert orders_for_strategy(result.state.oms, "MOMENTUM") == ()  # type: ignore[attr-defined]


def test_an_unindexed_order_can_still_be_found_and_removed() -> None:
    """The remove-side guard: ``_by_strategy[""]`` would raise ``KeyError``."""

    order = _order("")
    book = OrderBook().add(order)

    assert book.find(order.order_id) is order
    assert book.remove(order.order_id).contains(order.order_id) is False


def test_removing_an_unindexed_order_through_the_engine_does_not_raise() -> None:
    order = _order("")
    state = OMSEngine.submit(OMSState(), order, 1.0)
    accepted = OMSEngine.accept(state, order.order_id, 1.0)

    cancelled = OMSEngine.cancel(accepted, order.order_id, 2.0)

    assert cancelled.orders.find(order.order_id).status is OrderStatus.CANCELLED


def test_a_standalone_caller_with_a_real_strategy_is_still_indexed() -> None:
    """The OMS remains correct for callers who genuinely have one owner."""

    owned, unowned = _order("MOMENTUM"), _order("")
    book = OrderBook().add(owned).add(unowned)

    assert [o.order_id for o in book.orders_for_strategy("MOMENTUM")] == [owned.order_id]
    assert book.orders_for_strategy("") == ()
    assert len(book.orders()) == 2


def test_mixing_indexed_and_unindexed_orders_removes_cleanly_in_any_order() -> None:
    owned, unowned = _order("MOMENTUM"), _order("")
    book = OrderBook().add(owned).add(unowned)

    book = book.remove(unowned.order_id).remove(owned.order_id)

    assert book.orders() == ()
    assert book.orders_for_strategy("MOMENTUM") == ()


def test_an_unindexed_order_survives_a_snapshot_round_trip() -> None:
    from alphalab.oms.snapshot import capture, from_primitives, restore
    from alphalab.persistence.serializer import deserialize, serialize

    order = _order("")
    state = OMSEngine.submit(OMSState(), order, 1.0)

    restored = restore(from_primitives(deserialize(serialize(capture(state)))))

    assert restored == state
    assert restored.orders.find(OrderId(UUID(str(order.order_id.value)))).strategy_id == ""
    assert restored.orders.orders_for_strategy("") == ()
