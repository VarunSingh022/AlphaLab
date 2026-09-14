"""Every StrategyContext surface declares what it supplies.

ADR-0031 named the defect and fixed two of six instances of it: a field whose
protocol "declares no methods, and every construction site in the repository
passes ``object()``" promises a capability nothing can be written against.
``HistoryAccessorProtocol`` and ``UniverseProtocol`` got the members their views
implement; ``PortfolioSnapshotProtocol``, ``MarketViewProtocol``,
``RiskViewProtocol`` and ``OrderFacadeProtocol`` -- the four the pipeline had
been populating since v2.10 -- were left empty.

That was not cosmetic. AlphaLab ships ``py.typed``, so the declared type is the
contract a downstream strategy is checked against. Under ``mypy --strict`` a
strategy could write ``context.history.bars(asset)`` and could **not** write
``context.portfolio.cash("USD")``: *"PortfolioSnapshotProtocol has no attribute
cash"*, for the single surface ADR-0026 exists to supply.

These tests pin the repaired contract from both ends. Structurally: each
protocol declares members, and the view the pipeline overlays satisfies it.
Behaviourally: the null objects answer "nothing" and say so, rather than raising
``AttributeError`` from a bare ``object()`` and having
:class:`~alphalab.strategy.dispatcher.Dispatcher` report it as a *strategy*
failure.
"""

import inspect
from decimal import Decimal
from typing import Any, get_type_hints

import pytest

from alphalab.runtime.context_views import (
    HistoryView,
    MarketView,
    OrderView,
    PortfolioView,
    RiskView,
    UniverseView,
)
from alphalab.strategy.context import (
    HistoryAccessorProtocol,
    MarketViewProtocol,
    NoHistory,
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    NoUniverse,
    OrderFacadeProtocol,
    PortfolioSnapshotProtocol,
    RiskViewProtocol,
    StrategyContext,
    UniverseProtocol,
)

#: Field -> (declared protocol, the view the pipeline overlays, the null object).
#:
#: ``history`` and ``universe`` carried ``None`` here until v2.17, which made
#: :func:`test_the_overlaid_view_satisfies_the_declared_protocol` skip for both
#: on the grounds that they were "pinned by
#: test_strategy_context_history_and_universe". That file pins their *behaviour*
#: -- the look-ahead bound, what an unconfigured universe means -- and never
#: asserted the structural property this table exists for, so two of the six
#: surfaces had no structural check at all and the suite reported two skips
#: rather than a gap. ADR-0034 fills the table; the behavioural file is
#: unchanged and still the place those guarantees live.
SURFACES: tuple[tuple[str, type, type, type], ...] = (
    ("portfolio", PortfolioSnapshotProtocol, PortfolioView, NoPortfolio),
    ("market", MarketViewProtocol, MarketView, NoMarket),
    ("risk_view", RiskViewProtocol, RiskView, NoRiskView),
    ("orders", OrderFacadeProtocol, OrderView, NoOrders),
    ("history", HistoryAccessorProtocol, HistoryView, NoHistory),
    ("universe", UniverseProtocol, UniverseView, NoUniverse),
)


def _declared_members(protocol: type) -> frozenset[str]:
    return frozenset(
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") or name in {"__iter__", "__len__", "__contains__"}
        if callable(value) or isinstance(value, property)
    )


@pytest.mark.parametrize(("field", "protocol", "view", "null"), SURFACES, ids=lambda v: str(v))
def test_no_context_protocol_is_decorative(
    field: str, protocol: type, view: type, null: type
) -> None:
    """A protocol with no members is a promise nothing can be written against."""

    assert _declared_members(protocol), (
        f"{protocol.__name__} declares no members, so StrategyContext.{field} "
        "promises a capability a typed caller cannot reach. This is the exact "
        "defect ADR-0031 named and ADR-0032 finished."
    )


@pytest.mark.parametrize(("field", "protocol", "view", "null"), SURFACES, ids=lambda v: str(v))
def test_the_null_object_satisfies_the_declared_protocol(
    field: str, protocol: type, view: type, null: type
) -> None:
    missing = sorted(_declared_members(protocol) - set(dir(null)))
    assert not missing, f"{null.__name__} does not supply {missing}"


@pytest.mark.parametrize(("field", "protocol", "view", "null"), SURFACES, ids=lambda v: str(v))
def test_the_overlaid_view_satisfies_the_declared_protocol(
    field: str, protocol: type, view: type, null: type
) -> None:
    """The type the pipeline actually supplies answers everything it declares."""

    missing = sorted(_declared_members(protocol) - set(dir(view)))
    assert not missing, f"{view.__name__} does not supply {missing}"


def test_the_declared_members_are_the_view_members_and_not_a_subset_chosen_by_hand() -> None:
    """A protocol that declares three of ten members is decorative by degree.

    The rule is the one ADR-0031 applied: the protocol declares what the view
    implements. Private helpers and dunder plumbing are the view's own.
    """

    for _field, protocol, view, _null in SURFACES:
        public_view_members = {
            name
            for name, value in vars(view).items()
            if not name.startswith("_") and (callable(value) or isinstance(value, property))
        }
        declared = {n for n in _declared_members(protocol) if not n.startswith("_")}
        assert public_view_members <= declared, (
            f"{view.__name__} supplies {sorted(public_view_members - declared)} "
            f"that {protocol.__name__} does not declare"
        )


def test_a_null_object_answers_rather_than_raising() -> None:
    """A bare ``object()`` raised AttributeError and was blamed on the strategy."""

    portfolio = NoPortfolio()
    assert portfolio.cash("USD") == Decimal("0")
    assert portfolio.quantity("ASSET") == Decimal("0")
    assert portfolio.position("ASSET") is None
    assert dict(portfolio.positions) == {}

    market = NoMarket()
    assert market.price("ASSET") is None
    assert market.quote("ASSET") is None
    assert tuple(market.assets) == ()

    risk = NoRiskView()
    assert risk.buying_power == Decimal("0")
    assert risk.active_limits is None

    orders = NoOrders()
    assert len(orders) == 0
    assert orders.net_quantity("ASSET") == Decimal("0")
    assert tuple(orders.for_asset("ASSET")) == ()


@pytest.mark.parametrize(("field", "protocol", "view", "null"), SURFACES, ids=lambda v: str(v))
def test_a_null_object_says_it_supplies_nothing(
    field: str, protocol: type, view: type | None, null: type
) -> None:
    """ "Nothing supplied" must be distinguishable from "nothing happened"."""

    instance = null()
    flag = "available" if hasattr(instance, "available") else "configured"
    assert getattr(instance, flag) is False
    assert bool(instance) is False


def test_no_construction_site_in_the_repository_passes_a_bare_object() -> None:
    """The signature ADR-0031 used to identify a decorative surface."""

    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        if path.name == pathlib.Path(__file__).name:
            continue
        text = path.read_text()
        for field, _protocol, _view, _null in SURFACES:
            if f"{field}=object()" in text:
                offenders.append(f"{path.relative_to(root)}: {field}=object()")
    assert offenders == [], (
        "a bare object() satisfies an empty protocol, raises AttributeError on "
        "every access, and is then reported as a strategy failure. Use the "
        "matching null object: " + "; ".join(offenders)
    )


def test_the_context_still_refuses_to_grow_an_allocation_surface() -> None:
    """ADR-0026 decision 8 refuses it permanently. v2.16 does not add it."""

    hints = get_type_hints(StrategyContext)
    assert "allocation" not in hints
    assert set(hints) == {
        "portfolio",
        "market",
        "clock",
        "logger",
        "risk_view",
        "config",
        "orders",
        "history",
        "universe",
    }


def test_the_protocols_are_protocols_and_not_base_classes() -> None:
    """Structural, so a caller's own view satisfies them without inheriting."""

    for _field, protocol, _view, _null in SURFACES:
        assert getattr(protocol, "_is_protocol", False), f"{protocol.__name__} is not a Protocol"
        assert not inspect.isabstract(protocol)


def test_every_declared_member_is_documented() -> None:
    """A member with no docstring is the next decorative surface."""

    undocumented: list[str] = []
    for _field, protocol, _view, _null in SURFACES:
        for name in _declared_members(protocol):
            member: Any = getattr(protocol, name)
            target = member.fget if isinstance(member, property) else member
            if not (target.__doc__ or "").strip() and not name.startswith("__"):
                undocumented.append(f"{protocol.__name__}.{name}")
    assert undocumented == [], f"undocumented context members: {undocumented}"


def test_the_table_names_the_views_the_pipeline_actually_overlays() -> None:
    """The table is only worth what its agreement with the pipeline is worth.

    Two of these entries were ``None`` until v2.17 and the corresponding case
    skipped. Filling them in by hand is worth nothing if the pipeline overlays a
    different type, so this reads the construction site: every view listed above
    must be the class ``_populate_context`` instantiates for that field, and
    every field it populates must be listed here.
    """

    import ast
    import inspect
    import textwrap

    from alphalab.runtime import execution_pipeline

    tree = ast.parse(textwrap.dedent(inspect.getsource(execution_pipeline._populate_context)))

    # ``name -> the class it is constructed from``, for the views built once
    # outside the per-strategy closure.
    locals_to_class = {
        target.id: node.value.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value.func, ast.Name)
    }

    fields = {field for field, _, _, _ in SURFACES}
    overlaid = next(
        {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg in fields}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "replace"
    )

    assert set(overlaid) == fields, (
        "the pipeline populates a different set of context fields than this table lists: "
        f"{sorted(set(overlaid) ^ fields)}"
    )

    for field, _protocol, view, _null in SURFACES:
        value = overlaid[field]
        if isinstance(value, ast.Name):
            # Built once, above the closure, and referenced by name.
            built = locals_to_class.get(value.id)
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            # Built per strategy, inline -- ``orders`` is the one such field.
            built = value.func.id
        else:  # pragma: no cover - a shape this assertion does not understand
            raise AssertionError(f"{field} is overlaid from an unrecognised expression")

        assert built == view.__name__, (
            f"the pipeline overlays {built} onto {field}, but this table names {view.__name__}"
        )
