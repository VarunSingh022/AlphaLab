"""Names this repository reuses on purpose, and the reasons they are not one thing.

The v2.16 refactor audit went looking for duplicate domain models and found
several pairs that *look* like duplication and are not. This file is the answer
to each, in the form ``test_venue_concepts_stay_distinct.py`` established in
v2.8: a merge would have to break these assertions first, and whoever proposes
one can read here what they would be giving up.

Nothing here is a fix. Every entry is a decision to keep two things apart.
"""

import inspect
from collections.abc import Sequence
from decimal import Decimal

from alphalab.core.enums import AssetType

# --------------------------------------------------------------------------- #
# 1. "OrderBook" -- my working orders, and the market's depth
# --------------------------------------------------------------------------- #


def test_the_two_order_books_are_different_concepts() -> None:
    """One holds *my* orders. The other holds *the market's* resting size.

    ``alphalab.oms.book.OrderBook`` is the OMS index of this run's live orders,
    keyed by :class:`~alphalab.oms.ids.OrderId` and indexed by asset and
    strategy. ``alphalab.data.feed.OrderBook`` is a venue depth snapshot: bid
    and ask price levels on the wire.

    Merging them is not a simplification, it is a category error -- the OMS book
    answers "what have I got working?", the depth book answers "what is
    available?". They share only the English word, and both spellings are the
    standard ones in their own domain, so renaming either would make one of the
    two surfaces read wrongly to the people who use it.
    """

    from alphalab.data.feed import OrderBook as DepthBook
    from alphalab.oms.book import OrderBook as OMSBook

    # Widened so the check is a runtime one; mypy already rejects the
    # comparison as non-overlapping, which is the static half of the same fact.
    oms_book: type = OMSBook
    assert oms_book is not DepthBook

    oms_members = {name for name in dir(OMSBook) if not name.startswith("_")}
    depth_members = {name for name in dir(DepthBook) if not name.startswith("_")}
    assert oms_members & depth_members == set(), "the two books share no operation"

    assert {"add", "remove", "replace"} <= oms_members
    assert {"bids", "asks", "symbol"} <= depth_members


def test_the_canonical_depth_snapshot_is_named_for_what_it_is() -> None:
    """``alphalab.market`` avoids the collision entirely: it says *Snapshot*."""

    from alphalab.market.snapshot import OrderBookSnapshot

    assert OrderBookSnapshot.__name__.endswith("Snapshot")
    fields = set(OrderBookSnapshot.__dataclass_fields__)
    assert {"asset_id", "bids", "asks", "sequence"} <= fields
    assert "orders" not in fields, "a depth snapshot holds levels, never orders"


# --------------------------------------------------------------------------- #
# 2. "PortfolioEngine" -- accounting, and construction
# --------------------------------------------------------------------------- #


def test_the_two_portfolio_engines_own_different_questions() -> None:
    """One answers "what do I own and what is it worth?". The other answers
    "what *should* I own?".

    ``alphalab.portfolio.engine.PortfolioEngine`` is the canonical accounting
    engine on the execution path: it applies fills, moves cash, and keeps the
    accounting identity that ties equity to deposits, realized P&L and
    commission. ``alphalab.portfolio_optimizer.engine.PortfolioEngine`` is a
    standalone construction library: it turns weights and constraints into a
    target portfolio and never sees a fill.

    Neither claims the other's authority, which is the test that matters -- a
    shared *name* across two packages is an import-site ambiguity, not an
    ownership conflict. Renaming either is a breaking change to a public symbol
    and buys nothing structural, so both keep the name their own domain uses.
    """

    from alphalab.portfolio.engine import PortfolioEngine as AccountingEngine
    from alphalab.portfolio_optimizer.engine import PortfolioEngine as ConstructionEngine

    accounting_engine: type = AccountingEngine
    assert accounting_engine is not ConstructionEngine

    accounting = {name for name in dir(AccountingEngine) if not name.startswith("_")}
    construction = {name for name in dir(ConstructionEngine) if not name.startswith("_")}
    assert accounting & construction == set(), "the two engines share no operation"

    assert "apply_fill" in accounting
    assert "apply_fill" not in construction, "construction must never settle a fill"


def test_only_the_accounting_engine_is_reachable_from_the_execution_path() -> None:
    """The pipeline composes one of them. There is no ambiguity at run time."""

    from alphalab.runtime import execution_pipeline

    source = inspect.getsource(execution_pipeline)
    assert "from alphalab.portfolio.engine import PortfolioEngine" in source
    assert "portfolio_optimizer" not in source


# --------------------------------------------------------------------------- #
# 3. `optimizer` and `portfolio_optimizer` -- two searches, two subjects
# --------------------------------------------------------------------------- #


def test_the_two_optimizer_packages_do_not_overlap() -> None:
    """``optimizer`` searches *parameters*; ``portfolio_optimizer`` sets *weights*.

    The similar names invite a merge. The subjects are unrelated:
    ``optimizer`` runs trials over a search space and scores each with an
    objective (Sharpe, Calmar, drawdown); ``portfolio_optimizer`` solves for
    asset weights under constraints. Neither imports the other, and neither has
    a function the other could use.
    """

    import alphalab.optimizer as parameter_search
    import alphalab.portfolio_optimizer as portfolio_construction

    shared = set(parameter_search.__all__) & set(portfolio_construction.__all__)
    assert shared == set(), f"the two optimizer packages export {shared} in common"

    assert {"generate_grid_search", "Parameter", "TrialResult"} <= set(parameter_search.__all__)
    assert {"optimize_minimum_variance", "WeightConstraints"} <= set(portfolio_construction.__all__)


# --------------------------------------------------------------------------- #
# 4. `broker` and `brokers` -- converged in v2.3, and the residue is not a copy
# --------------------------------------------------------------------------- #


def test_the_connector_package_routes_the_canonical_types() -> None:
    """v2.3 collapsed the duplicate models; the historical names are aliases.

    ``ARCHITECTURE.md`` listed "broker / brokers overlap" as an open gap
    deferred to v2.3. v2.3 closed it and the entry was never removed, which the
    v2.16 audit corrected. These identities are what "closed" means.
    """

    import alphalab.brokers as connectors
    from alphalab.broker.account import BrokerAccount
    from alphalab.broker.execution import BrokerExecution
    from alphalab.broker.order import BrokerOrderStatus
    from alphalab.broker.position import BrokerPosition

    assert connectors.AccountSnapshot is BrokerAccount
    assert connectors.ExecutionReport is BrokerExecution
    assert connectors.PositionSnapshot is BrokerPosition
    assert connectors.OrderStatus is BrokerOrderStatus
    assert connectors.AssetClass is AssetType


def test_exactly_one_public_broker_protocol_exists() -> None:
    """ADR-0032 finding C3, closed by ADR-0034: one name, one contract.

    Until v2.17 ``alphalab.broker.protocol.BrokerProtocol`` and
    ``alphalab.brokers.protocol.BrokerProtocol`` were two public symbols of the
    same name standing for two different contracts. The contracts are still
    genuinely different -- the venue boundary takes a ``BrokerState``, which is
    *one* broker; the connector takes a ``BrokerConnectorState`` holding many
    brokers and many accounts, which is why its queries take an ``account_id``
    the boundary has no need of -- so what changed is the *name*, not the shape.

    The connector is now ``BrokerConnectorProtocol``, which is the word this
    package already uses for its state, its engine and its error. There is no
    alias: an alias would leave one name meaning two things at an import site,
    which is the whole defect.
    """

    import importlib
    import pkgutil
    import typing

    import alphalab
    from alphalab.broker.protocol import BrokerProtocol as VenueBoundary
    from alphalab.broker.state import BrokerState
    from alphalab.brokers.protocol import BrokerConnectorProtocol as ConnectorBoundary
    from alphalab.brokers.state import BrokerConnectorState

    venue_boundary: type = VenueBoundary
    assert venue_boundary is not ConnectorBoundary

    def state_types(protocol: type) -> set[object]:
        return {
            typing.get_type_hints(getattr(protocol, name))["state"]
            for name in dir(protocol)
            if not name.startswith("_") and callable(getattr(protocol, name))
        }

    assert state_types(VenueBoundary) == {BrokerState}
    assert state_types(ConnectorBoundary) == {BrokerConnectorState}

    # The connector routes by account; the boundary is already one account.
    assert "query_account" in dir(ConnectorBoundary)
    assert "query_account" not in dir(VenueBoundary)
    assert "apply_execution" in dir(VenueBoundary)
    assert "apply_execution" not in dir(ConnectorBoundary)

    # And only one module in the repository exports the bare name.
    exporters = [
        info.name
        for info in pkgutil.walk_packages(alphalab.__path__, "alphalab.")
        if "BrokerProtocol" in getattr(importlib.import_module(info.name), "__all__", ())
    ]
    assert exporters == ["alphalab.broker", "alphalab.broker.protocol"], exporters


def test_the_renamed_connector_protocol_is_not_aliased_back() -> None:
    """A rename that leaves the old spelling reachable has renamed nothing."""

    import alphalab.brokers
    import alphalab.brokers.protocol

    for module in (alphalab.brokers, alphalab.brokers.protocol):
        assert not hasattr(module, "BrokerProtocol")
        assert "BrokerProtocol" not in module.__all__


# --------------------------------------------------------------------------- #
# 5. AlphaLab is a library: there is no composition root to own
# --------------------------------------------------------------------------- #


def test_the_package_declares_no_entry_point() -> None:
    """The audit asked after a "composition root / CLI". There is none, by design.

    ``ARCHITECTURE.md`` opens with "AlphaLab is a library". Every engine is a
    pure function over immutable state and the caller owns the wiring -- which
    is what ``examples/`` demonstrates thirteen times over. A CLI would have to
    invent a configuration format, a run directory and a process lifecycle, none
    of which the library has an opinion about. A half-built one would be worse
    than none, so there is none: no ``__main__``, no console script.
    """

    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    assert "scripts" not in pyproject["project"]
    assert "gui-scripts" not in pyproject["project"]

    mains = [
        path for path in (root / "alphalab").rglob("__main__.py") if "__pycache__" not in path.parts
    ]
    assert mains == [], f"an entry point appeared at {mains}"


def test_the_library_still_has_no_runtime_dependency() -> None:
    """The constraint that makes the pure-Python numerical code necessary."""

    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == []


# --------------------------------------------------------------------------- #
# 6. Two small linear-algebra routines, deliberately not shared
# --------------------------------------------------------------------------- #


def test_the_two_matrix_inversions_are_not_a_shared_numerical_layer() -> None:
    """``ml.linalg`` and ``portfolio_optimizer`` each invert a matrix, apart.

    Both are Gauss-Jordan elimination and they are not the same routine:
    ``ml.linalg.matrix_inverse`` does partial pivoting because
    ``ml.linear_regression`` hands it a design matrix ``X^T X``, which a caller
    can make arbitrarily ill-conditioned; ``portfolio_optimizer`` inverts a
    covariance matrix, which is symmetric positive semi-definite, where
    elimination without pivoting is the standard backward-stable choice.

    Sharing one would join two packages the architecture keeps independent --
    ``portfolio_optimizer`` imports nothing but ``common`` and its own siblings
    -- to save about thirty lines, and would push the stricter routine's cost
    onto the caller that does not need it. ``ml.linalg``'s own docstring already
    says it is "not a general-purpose linear algebra library".
    """

    import alphalab.portfolio_optimizer.optimizer as construction
    from alphalab.ml import linalg

    assert linalg.matrix_inverse is not construction._invert_matrix
    assert "pivot" in inspect.getsource(linalg.matrix_inverse).lower()

    imports = {
        line.split()[1].split(".")[1]
        for line in inspect.getsource(construction).splitlines()
        if line.startswith("from alphalab.")
    }
    assert "ml" not in imports, "portfolio_optimizer must not depend on ml"


def test_each_inversion_is_correct_on_the_input_it_documents() -> None:
    """The division of labour, demonstrated rather than asserted."""

    import alphalab.portfolio_optimizer.optimizer as construction
    from alphalab.ml import linalg

    def residual(inverse: Sequence[Sequence[float]], matrix: Sequence[Sequence[float]]) -> float:
        n = len(matrix)
        return max(
            abs(sum(inverse[i][k] * matrix[k][j] for k in range(n)) - (1.0 if i == j else 0.0))
            for i in range(n)
            for j in range(n)
        )

    # A covariance matrix: both are accurate.
    covariance = ((0.040, 0.010), (0.010, 0.090))
    assert residual(construction._invert_matrix(covariance), covariance) < 1e-12
    assert residual(linalg.matrix_inverse(covariance), covariance) < 1e-12

    # A tiny leading pivot, which a covariance matrix does not produce and a
    # design matrix can: only the pivoting routine stays accurate.
    ill_conditioned = ((1e-18, 1.0), (1.0, 1.0))
    assert residual(linalg.matrix_inverse(ill_conditioned), ill_conditioned) < 1e-9
    assert residual(construction._invert_matrix(ill_conditioned), ill_conditioned) > 0.5


# --------------------------------------------------------------------------- #
# 7. Every package with no importer is a standalone engine, not an orphan
# --------------------------------------------------------------------------- #


def test_every_zero_consumer_production_package_is_a_standalone_engine() -> None:
    """ "No importer" is a finding only when nothing explains it.

    ADR-0032 answered this finding with two categories: the standalone engine
    libraries ADR-0009 describes -- independently useful, deliberately not wired
    into the execution path -- and three packages (``kernel``, ``production``,
    ``integrations``) that had no importer *because* they were deprecated, with
    notices ``test_deprecation_notices.py`` enforced.

    v2.17 removed the second category (ADR-0034), so only the first remains and
    the explanation is now uniform: a package with no importer here is a
    standalone engine by design. What this asserts is the part that would
    otherwise go unnoticed -- that the removed three are gone rather than
    quietly re-added with the notice dropped, which would turn a managed
    deprecation into a genuine orphan.
    """

    import importlib

    for module_name in ("alphalab.kernel", "alphalab.production", "alphalab.integrations"):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(
            f"{module_name} is back. It was removed in v2.17 because it had no "
            "importer and no future; re-adding it without a consumer makes it the "
            "orphan the deprecation existed to avoid."
        )


def test_no_second_portfolio_model_came_back_with_the_removed_kernel() -> None:
    """What the audit was actually looking for inside ``alphalab.kernel``.

    The finding was "``kernel`` exports ``PortfolioState`` and ``PositionState``,
    which reads like a second portfolio model". It was not one -- both names
    *were* the canonical types -- and the package is now removed, so the
    surviving question is the one that mattered: is there exactly one source of
    portfolio truth in the repository?
    """

    import importlib
    import pkgutil

    import alphalab
    from alphalab.portfolio.amounts import CurrencyAmounts
    from alphalab.portfolio.engine import PortfolioState as CanonicalPortfolioState
    from alphalab.portfolio.position import Position as CanonicalPosition

    impostors: list[str] = []
    for info in pkgutil.walk_packages(alphalab.__path__, "alphalab."):
        module = importlib.import_module(info.name)
        for name in ("PortfolioState", "PositionState"):
            value = getattr(module, name, None)
            if value is None:
                continue
            if value not in (CanonicalPortfolioState, CanonicalPosition):
                impostors.append(f"{info.name}.{name}")

    assert not impostors, f"a second portfolio model exists at {impostors}"
    # And the canonical one accounts in money, per settlement currency.
    assert (
        CanonicalPortfolioState.__dataclass_fields__["realized_pnl"].default_factory
        is CurrencyAmounts
    )
    assert isinstance(CurrencyAmounts().of("USD"), Decimal)


# --------------------------------------------------------------------------- #
# 8. `lifecycle` imports `studio`, and that is one model rather than two
# --------------------------------------------------------------------------- #


def test_the_strategy_declaration_has_one_definition_and_lifecycle_takes_it() -> None:
    """It reads like an upward dependency. It is the single-model rule.

    ``alphalab.lifecycle`` and ``alphalab.experiment_tracking`` import from
    ``alphalab.studio``, which the layer sketch in ``ARCHITECTURE.md`` places
    above them. The alternative is worse and is the defect this repository keeps
    removing: a second strategy-declaration type, so a candidate produced by
    ``research_assistant`` would need translating before it could reach a
    strategy version. ``register_strategy``'s own docstring says so.

    What makes it safe is that ``StrategyDefinition`` is not orchestration. It
    is a frozen dataclass of author metadata and parameter bounds that imports
    nothing but the standard library, so taking it drags no Studio machinery
    along. The two ``studio_bridge`` modules are named for exactly this seam.
    """

    import alphalab.studio.strategy as declaration
    from alphalab.lifecycle.registration import register_strategy
    from alphalab.studio.strategy import StrategyDefinition

    source = inspect.getsource(declaration)
    assert "from alphalab." not in source, (
        "the strategy declaration must stay a leaf, or importing it would drag "
        "the Studio engine into the lifecycle"
    )

    import typing

    hints = typing.get_type_hints(register_strategy)
    assert hints["definition"] is StrategyDefinition

    # And there is no second one waiting to be introduced.
    import alphalab.lifecycle as lifecycle

    assert not [name for name in dir(lifecycle) if "StrategyDefinition" in name]
