"""One book against every broker account it is spread across (ledger BRK-004).

The fixtures are the single-account suite's: a real ``ExecutionPipelineState``,
real ``BrokerState`` values, real bindings. What changes is that the book is one
and the mirrors are several, and the questions are the ones only a desk with
several brokers can ask: was an order sent to the account it was meant for, is
a fill reported where its order lives, and do the accounts together hold what
the book holds.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus
from alphalab.lifecycle import (
    AccountMirror,
    LifecycleInputError,
    MismatchCategory,
    StateReconciliation,
    SymbolMapping,
    Tolerance,
    reconcile_accounts,
    reconcile_execution_state,
)
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.position import Position
from alphalab.runtime.broker_routing import ChildOrderBindings
from alphalab.runtime.execution_pipeline import ExecutionPipelineState
from tests.regression._timing import growth
from tests.unit.lifecycle.test_reconciliation import (
    ASSET,
    IDENTITY,
    TOLERANCES,
    base_pipeline,
    broker_order,
    broker_with,
    execution,
    oms_order,
    pipeline_with,
    report,
)

FIRST = "11111111-1111-1111-1111-111111111111"
SECOND = "22222222-2222-2222-2222-222222222222"
ASSIGNED = {FIRST: "alpha", SECOND: "beta"}


def _position(quantity: str, asset_id: str = ASSET) -> Position:
    return Position(
        asset_id, Decimal(quantity), Decimal("100"), Decimal("100"), Decimal("0"), "USD", 1.0
    )


def _broker_position(quantity: str, symbol: str = ASSET) -> BrokerPosition:
    return BrokerPosition(
        symbol, Decimal(quantity), Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0")
    )


def _book(
    *,
    positions: dict[str, Position] | None = None,
    cash: Decimal = Decimal("100000"),
    first_fill: bool = True,
    second_fill: bool = True,
) -> ExecutionPipelineState:
    reports = []
    if first_fill:
        reports.append(report("x-1", order_id=FIRST))
    if second_fill:
        reports.append(report("x-2", order_id=SECOND))
    return pipeline_with(
        orders=(oms_order(order_id=FIRST), oms_order(order_id=SECOND)),
        reports=tuple(reports),
        positions={ASSET: _position("20")} if positions is None else positions,
        cash=cash,
    )


def _mirror(
    oms_order_id: str,
    handle: str,
    *,
    execution_id: str | None,
    position: str = "10",
    cash: str = "50000",
    currency: str = "USD",
    symbols: SymbolMapping = IDENTITY,
    symbol: str = ASSET,
    children: ChildOrderBindings | None = None,
) -> AccountMirror:
    executions = () if execution_id is None else (execution(execution_id, broker_order_id=handle),)
    return AccountMirror(
        broker=broker_with(
            orders=(
                broker_order(broker_order_id=handle, oms_order_id=oms_order_id, symbol=symbol),
            ),
            executions=executions,
            positions=(_broker_position(position, symbol),),
            cash=Decimal(cash),
            currency=currency,
        ),
        mapping=ExternalOrderMap().bind(oms_order_id, handle),
        symbols=symbols,
        children=children,
    )


def _desk() -> dict[str, AccountMirror]:
    """Two accounts, one order and one fill each, half the position and cash each."""

    return {
        "alpha": _mirror(FIRST, "A-1", execution_id="x-1"),
        "beta": _mirror(SECOND, "B-1", execution_id="x-2"),
    }


def _reconcile(
    book: ExecutionPipelineState,
    accounts: Mapping[str, AccountMirror],
    assignment: Mapping[str, str] = ASSIGNED,
    **keywords: Mapping[str, Tolerance],
) -> StateReconciliation:
    return reconcile_accounts(book, accounts, assignment, TOLERANCES, **keywords)


class TestAgreement:
    def test_a_book_spread_across_two_accounts_reconciles_in_one_pass(self) -> None:
        result = _reconcile(_book(), _desk())

        assert result.mismatches == ()
        assert result.unreconciled == ()
        assert result.unassigned == ()
        assert result.fully_reconciled
        assert (result.compared_orders, result.compared_fills, result.compared_positions) == (
            2,
            2,
            1,
        )

    def test_each_account_has_its_own_handles_even_when_they_collide(self) -> None:
        """Two venues may number their orders alike; a binding is per account."""

        accounts = {
            "alpha": _mirror(FIRST, "1", execution_id="x-1"),
            "beta": _mirror(SECOND, "1", execution_id="x-2"),
        }

        assert _reconcile(_book(), accounts).fully_reconciled

    def test_each_account_names_instruments_its_own_way(self) -> None:
        beta = _mirror(
            SECOND,
            "B-1",
            execution_id="x-2",
            symbols=SymbolMapping({"AAA.B": ASSET}),
            symbol="AAA.B",
        )

        assert _reconcile(_book(), {"alpha": _desk()["alpha"], "beta": beta}).fully_reconciled

    def test_one_account_reconciles_as_reconcile_execution_state_does(self) -> None:
        """The same breaks, found the same way; only the account is added."""

        book = pipeline_with(
            orders=(oms_order(order_id=FIRST, quantity="10", status=OrderStatus.CANCELLED),),
            reports=(report("x-1", order_id=FIRST),),
            positions={ASSET: _position("10")},
            cash=Decimal("100000"),
        )
        broker = broker_with(
            orders=(broker_order(quantity="12", broker_order_id="A-1", oms_order_id=FIRST),),
            executions=(execution("x-9", broker_order_id="A-1"),),
            positions=(_broker_position("7"),),
            cash=Decimal("50000"),
        )
        mapping = ExternalOrderMap().bind(FIRST, "A-1")
        single = reconcile_execution_state(book, broker, mapping, IDENTITY, TOLERANCES)
        several = _reconcile(
            book, {"alpha": AccountMirror(broker, mapping, IDENTITY)}, {FIRST: "alpha"}
        )

        def shape(result: StateReconciliation) -> list[tuple[object, ...]]:
            return [
                (entry.category, entry.key, entry.expected, entry.observed)
                for entry in result.mismatches
            ]

        assert len(single.mismatches) > 4
        assert shape(several) == shape(single)
        assert (several.compared_orders, several.compared_fills) == (
            single.compared_orders,
            single.compared_fills,
        )


class TestAssignment:
    def test_an_order_bound_at_an_account_it_was_not_declared_for(self) -> None:
        """The multi-broker way to send an order twice."""

        accounts = {"alpha": _desk()["alpha"], "beta": _mirror(FIRST, "B-9", execution_id=None)}
        book = _book(second_fill=False)
        result = _reconcile(book, accounts, {FIRST: "alpha"})

        found = result.mismatches_in(MismatchCategory.ACCOUNT_ASSIGNMENT_MISMATCH)
        assert [(entry.key, entry.expected, entry.observed, entry.account) for entry in found] == [
            (FIRST, "alpha", "beta", "beta")
        ]
        assert "B-9" in found[0].reason
        # The venue order is accounted for by the binding, not reported unexpected.
        assert result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_ORDER) == ()

    def test_a_fill_reported_at_another_account_than_its_order(self) -> None:
        alpha = _mirror(FIRST, "A-1", execution_id=None)
        beta = replace(
            _desk()["beta"],
            broker=replace(
                _desk()["beta"].broker,
                executions=PersistentMap(
                    {
                        "x-1": execution("x-1", broker_order_id="B-1"),
                        "x-2": execution("x-2", broker_order_id="B-1"),
                    }
                ),
            ),
        )
        result = _reconcile(_book(), {"alpha": alpha, "beta": beta})

        missing = result.mismatches_in(MismatchCategory.MISSING_EXPECTED_FILL)
        misplaced = result.mismatches_in(MismatchCategory.ACCOUNT_ASSIGNMENT_MISMATCH)
        assert [(entry.key, entry.account) for entry in missing] == [("x-1", "alpha")]
        assert [(entry.key, entry.expected, entry.account) for entry in misplaced] == [
            ("x-1", "alpha", "beta")
        ]
        assert result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_FILL) == ()

    def test_an_order_the_assignment_does_not_name_is_reported_and_not_guessed(self) -> None:
        result = _reconcile(_book(), _desk(), {FIRST: "alpha"})

        assert result.unassigned == (SECOND,)
        assert result.reconciled
        assert not result.fully_reconciled
        # Nothing about it was compared, and nothing at beta reads as unexpected.
        assert result.compared_orders == 1
        assert result.compared_fills == 1
        assert result.mismatches_at("beta") == ()

    def test_fills_of_an_undeclared_unbound_order_are_unassigned(self) -> None:
        third = "33333333-3333-3333-3333-333333333333"
        book = replace(
            _book(),
            execution=replace(
                _book().execution,
                reports=_book().execution.reports.set("x-3", report("x-3", order_id=third)),
            ),
        )

        result = _reconcile(book, _desk())

        assert result.unassigned == (third,)

    def test_an_order_declared_and_filled_but_reported_nowhere(self) -> None:
        accounts = {
            "alpha": _mirror(FIRST, "A-1", execution_id=None),
            "beta": _desk()["beta"],
        }

        result = _reconcile(_book(), accounts)

        found = result.mismatches_in(MismatchCategory.MISSING_EXPECTED_FILL)
        assert [(entry.key, entry.account) for entry in found] == [("x-1", "alpha")]

    def test_children_working_at_another_account_than_their_parent(self) -> None:
        children = ChildOrderBindings().bind("B-child-1", FIRST)
        beta = replace(
            _desk()["beta"],
            broker=replace(
                _desk()["beta"].broker,
                orders=_desk()["beta"].broker.orders.set(
                    "B-child-1",
                    broker_order(broker_order_id="B-child-1", oms_order_id=FIRST, quantity="5"),
                ),
            ),
            children=children,
        )

        result = _reconcile(_book(), {"alpha": _desk()["alpha"], "beta": beta})

        found = result.mismatches_in(MismatchCategory.ACCOUNT_ASSIGNMENT_MISMATCH)
        assert [(entry.key, entry.account) for entry in found] == [(FIRST, "beta")]
        assert "B-child-1" in found[0].reason
        assert result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_ORDER) == ()


class TestTotals:
    def test_positions_are_compared_against_the_sum_over_accounts(self) -> None:
        accounts = _desk()
        accounts["beta"] = _mirror(SECOND, "B-1", execution_id="x-2", position="7")

        result = _reconcile(_book(), accounts)

        found = result.mismatches_in(MismatchCategory.POSITION_QUANTITY_MISMATCH)
        assert [(entry.key, entry.expected, entry.observed, entry.account) for entry in found] == [
            (ASSET, "20", "17", None)
        ]
        assert "alpha: 10, beta: 7" in found[0].reason

    def test_a_position_no_account_reports(self) -> None:
        other = "asset-b"
        result = _reconcile(
            _book(positions={ASSET: _position("20"), other: _position("3", other)}), _desk()
        )

        found = result.mismatches_in(MismatchCategory.POSITION_QUANTITY_MISMATCH)
        assert [(entry.key, entry.observed) for entry in found] == [(other, "0")]
        assert "does not report" in found[0].reason

    def test_a_position_held_at_an_account_the_book_does_not_carry(self) -> None:
        accounts = _desk()
        broker = accounts["beta"].broker
        accounts["beta"] = replace(
            accounts["beta"],
            broker=replace(
                broker, positions=broker.positions.set("ghost", _broker_position("4", "ghost"))
            ),
        )

        found = _reconcile(_book(), accounts).mismatches_in(MismatchCategory.UNEXPECTED_POSITION)

        assert [(entry.key, entry.observed) for entry in found] == [("ghost", "4")]
        assert "beta: 4" in found[0].reason

    def test_a_symbol_one_account_cannot_resolve_is_reported_at_that_account(self) -> None:
        accounts = _desk()
        accounts["beta"] = _mirror(
            SECOND, "B-1", execution_id="x-2", symbols=SymbolMapping({}), symbol="ZZZ"
        )

        found = _reconcile(_book(), accounts).mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH)

        assert {entry.account for entry in found} == {"beta"}

    def test_cash_is_compared_per_currency_against_the_sum_of_its_accounts(self) -> None:
        accounts = _desk()
        accounts["beta"] = _mirror(SECOND, "B-1", execution_id="x-2", cash="49000")

        found = _reconcile(_book(), accounts).mismatches_in(MismatchCategory.ACCOUNT_CASH_MISMATCH)

        assert [(entry.key, entry.expected, entry.observed, entry.account) for entry in found] == [
            ("USD", "100000.00", "99000", None)
        ]
        assert "alpha: 50000, beta: 49000" in found[0].reason

    def test_a_cash_tolerance_per_currency_overrides_the_book_tolerance(self) -> None:
        state = base_pipeline(Decimal("100000"))
        funded = PortfolioEngine.apply_deposit(state.portfolio, Decimal("1000000"), "JPY", 2.0)
        book = replace(
            _book(),
            portfolio=replace(funded, positions={ASSET: _position("20")}),
        )
        accounts = _desk()
        accounts["alpha"] = _mirror(FIRST, "A-1", execution_id="x-1", cash="100000")
        accounts["beta"] = _mirror(
            SECOND, "B-1", execution_id="x-2", cash="1000000.5", currency="JPY"
        )

        strict = _reconcile(book, accounts)
        per_currency = _reconcile(
            book, accounts, cash_by_currency={"JPY": Tolerance(absolute=Decimal("1"))}
        )

        assert [entry.key for entry in strict.mismatches] == ["JPY"]
        assert per_currency.fully_reconciled

    def test_an_account_with_no_currency_and_a_currency_no_account_holds(self) -> None:
        state = base_pipeline(Decimal("100000"))
        funded = PortfolioEngine.apply_deposit(state.portfolio, Decimal("500"), "CHF", 2.0)
        book = replace(_book(), portfolio=replace(funded, positions={ASSET: _position("20")}))
        accounts = _desk()
        accounts["alpha"] = _mirror(FIRST, "A-1", execution_id="x-1", cash="100000")
        accounts["beta"] = _mirror(SECOND, "B-1", execution_id="x-2", currency=" ")

        result = _reconcile(book, accounts)

        assert result.reconciled
        areas = {area.area: area.reason for area in result.unreconciled}
        assert set(areas) == {"account beta cash", "account cash"}
        assert "CHF" in areas["account cash"]


class TestRefusalsAndDeterminism:
    def test_no_account_at_all_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="at least one account"):
            _reconcile(_book(), {})

    def test_a_blank_account_id_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match="blank"):
            _reconcile(_book(), {" ": _desk()["alpha"]}, {})

    def test_an_assignment_to_an_account_not_supplied_is_refused(self) -> None:
        with pytest.raises(LifecycleInputError, match=r"\['beta'\]"):
            _reconcile(_book(), {"alpha": _desk()["alpha"]})

    def test_a_binding_that_disagrees_with_itself_names_its_account(self) -> None:
        broken = ExternalOrderMap(to_broker=PersistentMap({FIRST: "A-1"}), to_oms=PersistentMap())
        accounts = _desk()
        accounts["alpha"] = replace(accounts["alpha"], mapping=broken)

        with pytest.raises(LifecycleInputError, match="Account 'alpha'"):
            _reconcile(_book(), accounts)

    def test_reconciling_twice_returns_an_equal_result_and_changes_nothing(self) -> None:
        book = _book()
        accounts = _desk()
        accounts["beta"] = _mirror(SECOND, "B-1", execution_id=None, position="7", cash="1")
        before = (book, dict(accounts))

        first = _reconcile(book, accounts)

        assert first == _reconcile(book, accounts)
        assert (book, accounts) == before
        order = list(MismatchCategory)
        indexes = [order.index(entry.category) for entry in first.mismatches]
        assert indexes == sorted(indexes)

    def test_mismatches_at_partitions_the_result_by_account(self) -> None:
        accounts = _desk()
        accounts["beta"] = _mirror(SECOND, "B-1", execution_id=None, position="7")

        result = _reconcile(_book(), accounts)

        by_account = {account: result.mismatches_at(account) for account in ("alpha", "beta", None)}
        assert by_account["alpha"] == ()
        assert {entry.category for entry in by_account["beta"]} == {
            MismatchCategory.MISSING_EXPECTED_FILL
        }
        assert {entry.category for entry in by_account[None]} == {
            MismatchCategory.POSITION_QUANTITY_MISMATCH
        }
        assert sum(map(len, by_account.values())) == len(result.mismatches)


def test_reconciling_many_accounts_is_near_linear_in_the_book() -> None:
    """A hundred venues, then four hundred: the work is per account and per order."""

    def work(count: int) -> Callable[[], object]:
        ids = [f"{index:08d}-0000-0000-0000-000000000000" for index in range(count)]
        book = pipeline_with(
            orders=tuple(oms_order(order_id=oms_id) for oms_id in ids),
            reports=tuple(
                report(f"x-{index}", order_id=oms_id) for index, oms_id in enumerate(ids)
            ),
            positions={ASSET: _position(str(10 * count))},
            cash=Decimal(50000 * count),
        )
        accounts = {
            f"venue-{index:04d}": _mirror(oms_id, f"H-{index}", execution_id=f"x-{index}")
            for index, oms_id in enumerate(ids)
        }
        assignment = {oms_id: f"venue-{index:04d}" for index, oms_id in enumerate(ids)}
        assert reconcile_accounts(book, accounts, assignment, TOLERANCES).fully_reconciled
        return lambda: reconcile_accounts(book, accounts, assignment, TOLERANCES)

    ratio = growth(work(100), work(400))

    assert ratio < 8.0, f"reconciling across accounts grew {ratio:.1f}x for 4x the accounts"
