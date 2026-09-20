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

import pytest

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


# --------------------------------------------------------------------------- #
# 9. "source" -- a stream to pull from, and a record of bytes already read
# --------------------------------------------------------------------------- #


def test_the_two_sources_are_a_protocol_and_a_receipt() -> None:
    """One is a live relationship. The other is evidence about a file.

    ``alphalab.market.source.MarketDataSource`` is a *protocol*: something the
    execution path pulls canonical records from, one at a time, for as long as
    it runs. ``alphalab.data.source.RawSource`` is a *record*: an immutable
    statement about bytes that were already read, made after the fact, carrying
    a content hash and a retrieval time.

    A socket can satisfy the first and can never be the second; a hash of a
    downloaded file is the second and can never yield a record. Merging them
    would give the execution path a content hash it has no use for, and give
    provenance an iterator it cannot store.
    """

    from alphalab.data.source import RawSource
    from alphalab.market.source import MarketDataSource

    raw: type = RawSource
    assert raw is not MarketDataSource

    assert hasattr(MarketDataSource, "records"), "the protocol yields a stream"
    assert not hasattr(RawSource, "records"), "the receipt yields nothing"

    fields = set(RawSource.__dataclass_fields__)
    assert {"content_hash", "retrieved_at", "byte_count"} <= fields
    assert "source_id" not in fields, "a receipt is not a stream identity"


# --------------------------------------------------------------------------- #
# 10. "calendar" -- when a job fires, and when a venue is open
# --------------------------------------------------------------------------- #


def test_the_scheduler_calendar_and_the_market_calendar_answer_different_questions() -> None:
    """``TradingCalendar`` asks "should this job fire today?" over UTC weekends
    and an optional holiday hook. It knows nothing about venues, sessions or
    local time, and deliberately so -- a scheduler that had to resolve an
    exchange's session to decide whether to run would need reference data it
    has no business holding.

    ``MarketCalendar`` asks "was this venue open at this instant?", which needs
    the exchange's timezone, its session windows, its lunch break and its half
    days. Answering the scheduler's question with it would require every caller
    to declare a venue; answering the market's question with the scheduler's
    would put every bar in UTC and misplace every session outside it.
    """

    from alphalab.data.calendar import MarketCalendar
    from alphalab.scheduler.calendar import TradingCalendar

    market: type = MarketCalendar
    assert market is not TradingCalendar

    assert "timezone_name" in MarketCalendar.__dataclass_fields__
    assert not hasattr(TradingCalendar, "__dataclass_fields__"), (
        "the scheduler's is stateless utilities, not a declared calendar"
    )

    scheduler_members = {name for name in dir(TradingCalendar) if not name.startswith("_")}
    market_members = {name for name in dir(MarketCalendar) if not name.startswith("_")}

    # They share exactly one name, and it takes different things and means
    # different things in each. The scheduler's reads an instant and answers
    # about the UTC week; the market's reads a *local date* and answers about a
    # venue. A caller passing a timestamp to the market one gets a type error
    # rather than a plausible wrong answer, which is what keeps the collision
    # harmless.
    assert scheduler_members & market_members == {"is_trading_day"}

    scheduler_signature = inspect.signature(TradingCalendar.is_trading_day)
    market_signature = inspect.signature(MarketCalendar.is_trading_day)
    assert list(scheduler_signature.parameters) == ["timestamp", "holiday_calendar"]
    assert list(market_signature.parameters) == ["self", "day"]
    assert market_signature.parameters["day"].annotation == "date"

    # And the market one can express what the scheduler's cannot.
    assert MarketCalendar.continuous("X", "UTC").is_continuous
    assert not hasattr(TradingCalendar, "continuous")


# --------------------------------------------------------------------------- #
# 11. Two adjustments: one company's shares, and two contracts spliced
# --------------------------------------------------------------------------- #


def test_price_basis_and_the_futures_roll_method_are_not_one_idea() -> None:
    """``PriceBasis`` is about *one instrument* whose share count or cash value
    genuinely changed -- a split, a dividend. ``AdjustmentMethod`` is about
    splicing *two contracts* into one continuous series, where the gap at the
    roll is an artefact of switching instruments and nothing happened to the
    company.

    A merge would have to claim that a 7-for-1 split and a December-to-March
    roll are the same event, which would let a back-adjusted futures series be
    labelled total-return.
    """

    from alphalab.data.corporate_actions import PriceBasis
    from alphalab.futures.roll import AdjustmentMethod

    basis: type = PriceBasis
    assert basis is not AdjustmentMethod
    assert {member.name for member in PriceBasis} == {"RAW", "SPLIT_ADJUSTED", "TOTAL_RETURN"}
    assert {member.name for member in AdjustmentMethod} == {
        "UNADJUSTED",
        "BACK_ADJUSTED",
        "RATIO_ADJUSTED",
    }
    assert not {m.name for m in PriceBasis} & {m.name for m in AdjustmentMethod}


def test_the_data_future_spec_and_the_futures_contract_are_wire_and_domain() -> None:
    """The same split ``alphalab.data.feed`` already draws for bars.

    ``FutureSpec`` is a wire-layer *description*: ``float``, keyed by provider
    ``symbol``, cheap for a data provider to fill in, and it says what a price
    series is about. ``FutureContract`` is the domain counterpart: ``Decimal``,
    bridged to ``Position`` so a contract can be *held*.

    Making ``data`` use ``FutureContract`` would put ``alphalab.portfolio`` --
    and a standalone engine with no in-repo consumers by design -- on the
    ingestion path, for a description that never opens a position.

    **v3.4 joined them, and the join is in ``alphalab.api``**, above both:
    ``future_contract_from_spec`` lifts one into the other. The two types stay
    distinct and ``alphalab.data`` still imports neither engine, which is what
    the assertions below check.
    """

    from decimal import Decimal

    from alphalab.data.assets import FutureSpec
    from alphalab.futures.contract import FutureContract

    spec: type = FutureSpec
    assert spec is not FutureContract

    assert FutureSpec.__annotations__["tick_size"] == "float"
    assert FutureContract.__annotations__["tick_size"] is Decimal or (
        FutureContract.__annotations__["tick_size"] == "Decimal"
    )
    assert "symbol" in FutureSpec.__dataclass_fields__
    assert "underlying_asset_id" in FutureContract.__dataclass_fields__

    # And the data layer drags no portfolio machinery along. Checked over the
    # import statements rather than the source text, because the module's
    # docstring names ``alphalab.portfolio`` while explaining this very split.
    import ast
    import pathlib

    module = pathlib.Path(inspect.getfile(FutureSpec))
    imported = {
        node.module
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [name for name in imported if name.startswith("alphalab.portfolio")]
    assert not [name for name in imported if name.startswith("alphalab.futures")]


# --------------------------------------------------------------------------- #
# 12. Two quality reports, and why that is one authority rather than two
# --------------------------------------------------------------------------- #


def test_the_scored_summary_is_a_projection_of_the_findings_not_a_second_measurement() -> None:
    """``DataQualityReport`` carries the counts *and* every finding.
    ``QualityReport`` is the scored summary stored in state and shown in a
    catalogue, and it is produced only by ``DataQualityReport.summarize``.

    Two independently-computed reports could come to disagree about one
    dataset, and the one in state would win while the detailed one was the one
    people read. This is the same shape as ``BacktestResult.dataset_id`` being
    a property over ``RunState``: one fact, one home, two views.
    """

    from alphalab.data.quality import DataQualityReport, QualityReport

    detail = DataQualityReport(
        dataset_id="DS", row_count=10, valid_rows=8, duplicate_count=1, invalid_count=1
    )
    summary = detail.summarize()

    assert isinstance(summary, QualityReport)
    assert summary.completeness == detail.completeness
    assert summary.quality_score == detail.quality_score
    assert summary.duplicate_count == detail.duplicate_count == 1

    # The detail is what a reader goes to; the summary cannot answer it.
    assert hasattr(detail, "rejected_rows") and hasattr(detail, "errors")
    assert not hasattr(summary, "rejected_rows")


# --------------------------------------------------------------------------- #
# 13. "Dataset" -- a canonical price series, and a design matrix
# --------------------------------------------------------------------------- #


def test_the_two_datasets_are_a_price_series_and_a_design_matrix() -> None:
    """One is what was observed; the other is what a model is fitted on.

    ``alphalab.data.dataset.Dataset`` is a canonical, versioned series of market
    records with the provenance saying where it came from.
    ``alphalab.ml.dataset.Dataset`` is an ML-ready ``(x, y)`` design matrix
    built out of the Feature Store, one row per *asset* rather than per instant,
    carrying no time axis at all.

    They share the English word and nothing else -- no field, and no operation.
    Merging them would either give a price series a target vector it has no
    notion of, or give a design matrix a schema, a calendar and a price basis
    that mean nothing for it. v3.1 made the first one considerably larger, which
    is why the pair is recorded now rather than left to look like an oversight.
    """

    from alphalab.data.dataset import Dataset as CanonicalDataset
    from alphalab.ml.dataset import Dataset as DesignMatrix

    canonical: type = CanonicalDataset
    assert canonical is not DesignMatrix

    canonical_fields = set(CanonicalDataset.__dataclass_fields__)
    matrix_fields = set(DesignMatrix.__dataclass_fields__)
    assert canonical_fields & matrix_fields == set(), "the two share no field"

    assert {"records", "provenance", "schema"} <= canonical_fields
    assert {"x", "y", "feature_names"} <= matrix_fields

    # And only one of them is on the path a run reads.
    assert hasattr(CanonicalDataset, "require_provenance")
    assert not hasattr(DesignMatrix, "require_provenance")


# --------------------------------------------------------------------------- #
# 14. "walk-forward" -- scoring a finished run, and partitioning a time index
# --------------------------------------------------------------------------- #


def test_the_two_walk_forwards_run_at_different_ends_of_the_pipeline() -> None:
    """One reads returns that already exist. The other decides what may be read.

    ``alphalab.research.cross_validation.walk_forward_analysis`` (v2) takes a
    ``ResearchPayload`` -- a *completed* run's return series -- cuts it into
    equal chunks and reports how consistent the Sharpe ratio was across them.
    It knows nothing about a dataset, a model or a training set, and there is
    nothing it could leak, because everything it touches has already happened.

    ``alphalab.research.walk_forward.walk_forward_splits`` (v3.2) takes a time
    index and returns folds, each naming the instants that may be trained on,
    selected on and reported on. Nothing has been run when it is called; its
    whole job is to decide what the run is allowed to see.

    Merging them is a category error in the direction that matters: the second
    would inherit the first's assumption that the data is already in hand, and
    that assumption is exactly what purging exists to break.
    """

    import inspect

    from alphalab.research.cross_validation import walk_forward_analysis
    from alphalab.research.walk_forward import walk_forward_splits

    scoring = inspect.signature(walk_forward_analysis)
    partitioning = inspect.signature(walk_forward_splits)

    assert "payload" in scoring.parameters
    assert "timestamps" in partitioning.parameters
    assert set(scoring.parameters) & set(partitioning.parameters) == set()

    # The scoring one returns a report of numbers; the partitioning one returns
    # folds whose membership can be inspected instant by instant. Rendered by
    # name because one module uses postponed annotations and the other does not,
    # so one signature holds a class and the other a string.
    def _named(annotation: object) -> str:
        return annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")

    assert _named(scoring.return_annotation) == "WalkForwardReport"
    assert _named(partitioning.return_annotation) == "SplitReport"


def test_only_the_v32_walk_forward_can_express_a_purge() -> None:
    """The concrete consequence of keeping them apart."""

    import inspect

    from alphalab.research.cross_validation import walk_forward_analysis
    from alphalab.research.walk_forward import walk_forward_splits

    assert "policy" in inspect.signature(walk_forward_splits).parameters
    assert "policy" not in inspect.signature(walk_forward_analysis).parameters


# --------------------------------------------------------------------------- #
# 15. Two resampling pairs: a finished return series, and an index
# --------------------------------------------------------------------------- #


def test_the_two_bootstraps_resample_different_things() -> None:
    """One draws returns independently; the other draws contiguous blocks.

    ``bootstrap_statistics`` (v2) resamples a completed run's returns *with
    replacement, one at a time*, to put a confidence interval on its Sharpe
    ratio. That is the right thing there: the question is about the
    distribution of a statistic of those returns.

    ``block_bootstrap_indices`` (v3.2) draws contiguous **blocks** of
    positions, because a research result depends on serial dependence -- trends,
    volatility clustering -- that an IID draw destroys. A confidence interval
    built from an IID resample of a time series is an interval for a different
    series.

    Merging them would force one of the two questions to be answered with the
    other's method.
    """

    import inspect

    from alphalab.research.bootstrap import bootstrap_statistics
    from alphalab.research.perturbation import block_bootstrap_indices

    assert "payload" in inspect.signature(bootstrap_statistics).parameters
    assert "block_size" in inspect.signature(block_bootstrap_indices).parameters
    assert "payload" not in inspect.signature(block_bootstrap_indices).parameters


def test_the_two_monte_carlos_differ_in_whether_paths_are_independent() -> None:
    """``monte_carlo_simulation`` shuffles one list repeatedly in place, so each
    path depends on every path before it -- fine for a summary statistic over a
    thousand of them, and unusable if a caller wants to reproduce path 500 on
    its own.

    ``monte_carlo_orders`` draws each permutation from ``range(count)`` afresh,
    so a path is a function of the seed and its index alone. The second is what
    a reproducible research experiment needs; the first is what the v2 report
    already promises and must keep promising.
    """

    import inspect

    from alphalab.research.montecarlo import monte_carlo_simulation
    from alphalab.research.perturbation import monte_carlo_orders

    assert "payload" in inspect.signature(monte_carlo_simulation).parameters
    assert "paths" in inspect.signature(monte_carlo_orders).parameters

    # Independence, asserted rather than described.
    paths = monte_carlo_orders(8, 3, seed=5)
    assert monte_carlo_orders(8, 3, seed=5) == paths
    assert all(sorted(path) == list(range(8)) for path in paths), "each path is a permutation"
    assert len(set(paths)) == len(paths), "and the paths are not the same one repeated"


# --------------------------------------------------------------------------- #
# 16. "parameter robustness" -- counting parameters, and evaluating them
# --------------------------------------------------------------------------- #


def test_the_two_parameter_diagnostics_measure_different_things() -> None:
    """One infers risk from how many parameters there are. The other runs them.

    ``parameter_robustness`` (v2) reads ``len(payload.parameters)`` and derives
    an instability index from the count -- a structural proxy, and its docstring
    says so. It perturbs nothing and evaluates nothing.

    ``parameter_sweep`` (v3.2) evaluates every configuration it is given and
    reports the whole surface, the sensitivity across it and the drop from the
    best configuration to its neighbour. It is the measurement the proxy stands
    in for when nobody has run the sweep.

    They are kept apart because the proxy is still the honest answer when there
    is no sweep to read, and replacing it with a function that *looks* like a
    measurement would overstate what a payload alone can support.
    """

    import inspect

    from alphalab.research.overfitting import parameter_sweep
    from alphalab.research.sensitivity import parameter_robustness

    proxy = inspect.signature(parameter_robustness)
    measured = inspect.signature(parameter_sweep)

    assert list(proxy.parameters) == ["payload"]
    assert "evaluate" in measured.parameters, "the sweep actually runs each configuration"
    assert "configurations" in measured.parameters


# --------------------------------------------------------------------------- #
# 17. "a series of prices" -- domain bars for one asset, and a scalar panel
# --------------------------------------------------------------------------- #


def test_the_price_series_and_the_observation_frame_hold_different_shapes() -> None:
    """``PriceSeries`` is one asset's domain bars; ``ObservationFrame`` is many
    assets' single extracted field.

    ``alphalab.factor_library.inputs.PriceSeries`` holds ``Decimal`` OHLCV bars
    keyed by ``asset_id`` for exactly one asset, and is what the six v2 style
    factors consume. An ``ObservationFrame`` holds one ``float`` per observation
    for every symbol at once, which is the shape a panel and a cross-section
    need and which a ``PriceSeries`` has no room for.

    v3.2 connects them rather than replacing either:
    ``observations_from_price_series`` reads a ``PriceSeries`` into a frame, so
    a caller holding the v2 shape never has to rebuild their data.
    """

    from alphalab.factor_library.inputs import PriceSeries
    from alphalab.factor_library.observations import (
        ObservationFrame,
        observations_from_price_series,
    )

    series_fields = set(PriceSeries.__dataclass_fields__)
    frame_fields = set(ObservationFrame.__dataclass_fields__)

    assert series_fields == {"asset_id", "bars"}
    assert "bars" not in frame_fields
    assert {"source_field", "series", "dataset_version"} <= frame_fields
    assert callable(observations_from_price_series), "the bridge exists rather than a merge"


def test_the_feature_metadata_and_the_feature_definition_answer_different_questions() -> None:
    """One is the catalogue record; the other is the computational contract.

    ``FeatureMetadata`` says who owns a feature, what it is called, what it
    depends on and which version of the *registration* this is. It carries no
    window, no input field and no parameters, because Feature Store does not
    compute.

    ``FeatureDefinition`` says exactly how to compute one, and derives its own
    identity from that. It carries no owner, no description and no tags,
    because it is not a registry entry.

    A single type would have to be both, and every field one of them does not
    need would become optional -- which is how a "definition" ends up
    computable only sometimes.
    """

    from alphalab.factor_library.definition import FeatureDefinition
    from alphalab.feature_store.metadata import FeatureMetadata

    catalogue = set(FeatureMetadata.__dataclass_fields__)
    contract = set(FeatureDefinition.__dataclass_fields__)

    assert {"owner", "description", "tags", "depends_on"} <= catalogue
    assert {"kind", "source_field", "window", "parameters"} <= contract
    assert catalogue & contract == {"feature_id"}, "they share the identifier and nothing else"


# --------------------------------------------------------------------------- #
# 16. "capacity" -- a research heuristic, and an execution constraint (v3.3)
# --------------------------------------------------------------------------- #


def test_the_two_capacity_models_answer_different_questions() -> None:
    """One degrades a CAGR. The other finds where the market pushes back.

    ``alphalab.research.capacity.estimate_capacity`` reads a
    :class:`~alphalab.research.protocol.ResearchPayload` -- a return series, a
    trade count and an AUM -- and reports what the CAGR would be at three fixed
    capital levels. It sees no prices, no volumes and no positions, so it cannot
    know which *name* would bind first or why; the degradation is a stated
    heuristic on trade frequency.

    ``alphalab.execution.capacity.CapacityModel`` reads liquidity: a price, an
    average daily volume and a weight per name, a turnover, a participation
    limit and an impact model. It reports the capital at which a named
    constraint binds, and which asset bound it.

    Merging them is not possible in either direction. The research report has no
    liquidity to give the execution model, and the execution model has no return
    series to degrade. They share the English word and nothing else -- neither
    reads an input of the other's, and neither produces an output the other
    could consume.
    """

    from alphalab.execution.capacity import CapacityModel, CapacityResult
    from alphalab.research.capacity import CapacityReport, estimate_capacity

    research_inputs = set(inspect.signature(estimate_capacity).parameters)
    execution_inputs = set(CapacityModel.__dataclass_fields__)

    assert research_inputs == {"payload"}
    assert {"participation_limit", "turnover", "impact_model"} <= execution_inputs
    assert research_inputs & execution_inputs == set()

    heuristic = set(CapacityReport.__dataclass_fields__)
    measured = set(CapacityResult.__dataclass_fields__)

    assert {"cagr_at_10m", "cagr_at_100m", "capacity_score"} <= heuristic
    assert {"capacity", "constraint", "binding_asset_id", "assumptions"} <= measured
    assert heuristic & measured == set(), "the two reports share no field"


def test_only_the_execution_capacity_model_reads_liquidity() -> None:
    """Which is the reason there are two, stated as an assertion."""

    from alphalab.execution.capacity import AssetLiquidity
    from alphalab.research.protocol import ResearchPayload

    liquidity = set(AssetLiquidity.__dataclass_fields__)
    payload = set(ResearchPayload.__dataclass_fields__)

    assert {"average_daily_volume", "price", "weight"} <= liquidity
    assert "average_daily_volume" not in payload


# --------------------------------------------------------------------------- #
# 17. "stress" -- a perturbed return series, and a shocked book (v3.3)
# --------------------------------------------------------------------------- #


def test_the_two_stress_surfaces_shock_different_objects() -> None:
    """One perturbs a curve of numbers. The other shocks positions.

    ``alphalab.research.stress.apply_stress_tests`` edits a **return series**:
    it subtracts a tenth from one observation and rescales the rest, then
    measures the drawdown of the result. It never sees a position, a price or a
    currency, so it cannot express "energy fell twenty percent" or "the euro
    fell against the dollar" at all.

    ``alphalab.scenario`` shocks a **book**: prices, volatilities, liquidity and
    exchange rates, each scoped to assets, sectors or currencies, and reports
    the change in the book's value per asset.

    A merge would have to pick one object to operate on, and would lose the
    other's entire vocabulary. The research function also has no place to put a
    scope, an FX rate or a per-asset result.
    """

    from alphalab.research.stress import StressReport, apply_stress_tests
    from alphalab.scenario import Scenario, ScenarioResult

    research_inputs = set(inspect.signature(apply_stress_tests).parameters)
    scenario_inputs = set(inspect.signature(Scenario.apply).parameters)

    assert research_inputs == {"payload"}
    assert scenario_inputs == {"self", "state"}

    curve_report = set(StressReport.__dataclass_fields__)
    book_report = set(ScenarioResult.__dataclass_fields__)

    assert {"flash_crash_drawdown", "stress_survival_score"} <= curve_report
    assert {"base_state", "shocked_state", "change_by_asset"} <= book_report
    assert curve_report & book_report == set()


def test_only_the_scenario_engine_can_express_a_scope_or_an_fx_shock() -> None:
    from alphalab.scenario import ShockKind

    assert {"PRICE", "VOLATILITY", "FX", "LIQUIDITY"} == {kind.name for kind in ShockKind}


def test_the_scenario_package_is_reusable_because_it_names_no_portfolio_class() -> None:
    """The property that makes one contract serve every portfolio class.

    ``alphalab.scenario`` imports ``alphalab.common`` and nothing else in the
    package. A scenario that named ``alphalab.portfolio.PortfolioState`` would
    be usable by exactly one portfolio class, which is the opposite of what the
    contract is for.
    """

    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
    imported: set[str] = set()
    for path in sorted((root / "scenario").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("alphalab.")
            ):
                imported.add(node.module.split(".")[1])

    assert imported <= {"common", "scenario"}, (
        f"alphalab.scenario reached into {sorted(imported - {'common', 'scenario'})}. "
        "The contract is generic precisely because it does not."
    )


# --------------------------------------------------------------------------- #
# 18. "impact" -- a slippage-role model, and a liquidity-aware one (v3.3)
# --------------------------------------------------------------------------- #


def test_the_two_impact_models_differ_in_what_they_are_allowed_to_read() -> None:
    """``MarketImpactSlippage`` cannot express participation. That is the split.

    ``MarketImpactSlippage`` is a ``SlippageModel``: it sees
    ``(quantity, price, side)`` and scales a notional. It has no liquidity
    input, so it cannot say what share of a name's volume an order is taking --
    and participation is exactly what an impact model and a capacity model both
    need.

    ``ImpactModel`` reads a ``CostContext``, which carries the liquidity the
    event showed, and refuses when it is absent rather than inventing a
    denominator. The two are separate roles in one ``ExecutionCostModel``, and
    ``MarketImpactSlippage`` stays available in the slippage slot it has always
    occupied.
    """

    from alphalab.execution.costs import CostContext, LinearImpact
    from alphalab.execution.slippage import MarketImpactSlippage

    notional_only = set(inspect.signature(MarketImpactSlippage.calculate).parameters)
    liquidity_aware = set(inspect.signature(LinearImpact.impact).parameters)

    assert notional_only == {"self", "fill_quantity", "fill_price", "side"}
    assert liquidity_aware == {"self", "context"}
    assert "available_liquidity" in CostContext.__dataclass_fields__
    assert "participation" not in notional_only


def test_an_impact_model_refuses_the_liquidity_it_was_not_given() -> None:
    from alphalab.core.enums import Side
    from alphalab.execution.costs import CostContext, SquareRootImpact
    from alphalab.execution.exceptions import ExecutionValidationError

    context = CostContext("AAPL", Side.BUY, Decimal("100"), Decimal("50"), "USD", "SIM", 1.0)

    with pytest.raises(ExecutionValidationError, match="participation rate"):
        SquareRootImpact(Decimal("0.1")).impact(context)


# --------------------------------------------------------------------------- #
# 19. "calendar" -- a job schedule, a venue's sessions, and a trading-day count
# --------------------------------------------------------------------------- #


def test_the_third_calendar_shaped_thing_is_a_protocol_not_a_calendar() -> None:
    """v3.4 needed trading-day arithmetic in two packages that must not import
    ``alphalab.data``, and did not add a calendar to either.

    ``alphalab.conventions`` rests on ``alphalab.common`` alone -- an edge into
    ``alphalab.data`` would close a package cycle through
    ``data -> options -> portfolio``. ``alphalab.futures`` could import it, and
    a roll rule needing one method does not justify dragging the ingestion layer
    onto the futures engine.

    So both name a one-method structural protocol that ``MarketCalendar``
    already satisfies. There is still exactly one calendar with holidays,
    sessions and a timezone, and it is ``alphalab.data.calendar.MarketCalendar``.
    """

    from alphalab.conventions.settlement import TradingDayCalendar
    from alphalab.data.calendar import MarketCalendar
    from alphalab.futures.chain import SessionCalendar

    for protocol in (TradingDayCalendar, SessionCalendar):
        declared = {name for name in vars(protocol) if not name.startswith("_")}
        assert declared == {"is_trading_day"}, (
            f"{protocol.__name__} grew beyond one method; a protocol with sessions and "
            "holidays on it is a second calendar in all but name."
        )

    owns_the_data = {"holidays", "special_sessions", "weekly_sessions", "timezone_name"}
    assert owns_the_data <= set(MarketCalendar.__dataclass_fields__)
    for protocol in (TradingDayCalendar, SessionCalendar):
        assert not hasattr(protocol, "holidays")


# --------------------------------------------------------------------------- #
# 20. "margin" -- an account calculation, a liquidation price, a published figure
# --------------------------------------------------------------------------- #


def test_the_three_margins_answer_three_questions_from_three_inputs() -> None:
    """None of the three can produce either of the others.

    ``MarginEngine`` reads a *book* and a rate the caller states, and answers
    buying power. ``compute_liquidation_price`` reads an entry price and a
    leverage, and answers a *price*. ``position_margin`` reads a clearing
    house's *published figures* and answers money per contract -- a number
    neither of the others holds and neither can derive, because a futures
    requirement is set per contract and revised without notice.
    """

    from alphalab.crypto.perpetual import compute_liquidation_price
    from alphalab.futures.margin import ContractMarginSpec, position_margin
    from alphalab.portfolio.margin import MarginEngine

    account = set(inspect.signature(MarginEngine.buying_power).parameters)
    liquidation = set(inspect.signature(compute_liquidation_price).parameters)
    published = set(inspect.signature(position_margin).parameters)

    assert "cash_ledger" in account and "specifications" not in account
    assert liquidation == {"entry_price", "side", "leverage", "maintenance_margin_rate"}
    assert "specifications" in published and "leverage" not in published

    # The published one carries an amount and a currency; the others carry rates.
    assert {"initial", "maintenance", "currency", "as_of"} <= set(
        ContractMarginSpec.__dataclass_fields__
    )


# --------------------------------------------------------------------------- #
# 21. "exposure" -- shares, and contracts (v3.4)
# --------------------------------------------------------------------------- #


def test_the_two_exposures_differ_in_whether_a_multiplier_exists() -> None:
    """``ExposureEngine`` reads ``Position.market_value``, which is
    ``quantity * market_price`` and means *shares*. ``Position`` carries no
    multiplier and never will -- every contract bridge in the repository says
    so and tells the caller to apply one.

    ``contract_exposures`` takes the convention that supplies it. It is not a
    better version of the first: it answers a question the first cannot express,
    and it needs an input the first does not have.
    """

    from alphalab.portfolio.contracts import ContractHolding, contract_exposures
    from alphalab.portfolio.exposure import ExposureEngine
    from alphalab.portfolio.position import Position

    assert "multiplier" not in Position.__dataclass_fields__
    assert set(inspect.signature(ExposureEngine.gross_exposure).parameters) == {"positions"}
    assert "convention" in ContractHolding.__dataclass_fields__
    assert set(inspect.signature(contract_exposures).parameters) == {"holdings"}


def test_the_unmultiplied_exposure_is_a_thousandth_of_the_contract_one() -> None:
    """The 1,000x error ``alphalab.data.assets`` opens by naming, demonstrated."""

    from alphalab.conventions import (
        LotSpecification,
        MarketConvention,
        SettlementBasis,
        SettlementRule,
        TickSchedule,
    )
    from alphalab.portfolio.contracts import ContractHolding, contract_exposures
    from alphalab.portfolio.exposure import ExposureEngine
    from alphalab.portfolio.position import Position

    position = Position(
        "CL_202606", Decimal("5"), Decimal("75"), Decimal("75"), Decimal("0"), "USD", 0.0
    )
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
    shares = ExposureEngine.gross_exposure({"CL_202606": position})
    contracts = contract_exposures([ContractHolding(position, convention)]).gross.of("USD")
    assert shares == Decimal("375.00")
    assert contracts == Decimal("375000.00")
    assert contracts == shares * 1000


# --------------------------------------------------------------------------- #
# 22. "currency attribution" -- a P&L breakdown, and a return decomposition (v3.4)
# --------------------------------------------------------------------------- #


def test_the_two_currency_breakdowns_measure_different_things() -> None:
    """``AttributionDimension.CURRENCY`` buckets *realized P&L by settlement
    currency*, from trades, and deliberately has no total -- summing it would
    need rates ADR-0020 forbids inventing.

    ``currency_attribution`` decomposes a *reporting-currency return* into a
    local component and a currency component, from values and two rate tables.
    It has a total because it was given the rates the other was not.

    Neither derives the other, and the return decomposition imports nothing
    from ``alphalab.analytics``.
    """

    import ast
    import pathlib

    from alphalab.analytics.attribution import AttributionDimension, attribute
    from alphalab.portfolio import fx_research
    from alphalab.portfolio.fx_research import currency_attribution

    assert "trades" in inspect.signature(attribute).parameters
    assert "trades" not in inspect.signature(currency_attribution).parameters
    assert {"opening_rates", "closing_rates"} <= set(
        inspect.signature(currency_attribution).parameters
    )
    assert AttributionDimension.CURRENCY.name == "CURRENCY"

    module = pathlib.Path(inspect.getfile(fx_research))
    imported = {
        node.module
        for node in ast.walk(ast.parse(module.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not [name for name in imported if name.startswith("alphalab.analytics")]
