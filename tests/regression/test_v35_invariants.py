"""The v3.5 invariants, measured from the code rather than claimed in a release note.

Each section is one property the strategy-execution work depends on, written so
that a change which breaks it fails here rather than in a number somebody reads
a year later. The shape follows ``test_v34_invariants.py``, and one test spawns a
**fresh interpreter** for the reason ``test_v33_invariants.py`` gives: a
process-local hash is perfectly stable inside one run, so no in-process
assertion can tell a reproducible identity from a merely consistent one.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import subprocess
import sys
from decimal import Decimal

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"

#: The modules v3.5 added. Every sweep below is scoped to them, so the file
#: says what the release is responsible for and cannot quietly start policing
#: something it did not write.
V35_MODULES = (
    "alphalab/lifecycle/tolerance.py",
    "alphalab/lifecycle/progression.py",
    "alphalab/lifecycle/specification.py",
    "alphalab/lifecycle/health.py",
    "alphalab/lifecycle/comparison.py",
    "alphalab/lifecycle/reconciliation.py",
)


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(PACKAGE.parent)), path.read_text()))
    return found


def _v35_sources() -> list[tuple[str, str]]:
    return [(name, text) for name, text in _sources() if name in V35_MODULES]


def _code(source: str) -> str:
    """The module with its docstrings and comments removed.

    Sweeps for a forbidden construct have to read *code*: this file's own
    subjects say things like "nothing here calls ``time.time()``" and "holds no
    credential", and a grep over raw text would fail on the sentence that states
    the property it is checking.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_every_v35_module_exists() -> None:
    """So a rename cannot silently empty every sweep in this file."""

    assert len(_v35_sources()) == len(V35_MODULES)


# --------------------------------------------------------------------------- #
# 1. One authority per concept
# --------------------------------------------------------------------------- #


def test_there_is_one_strategy_progression_vocabulary() -> None:
    """And it is not a second copy of either state machine that existed before."""

    from alphalab.lifecycle.progression import StrategyLifecycleStage
    from alphalab.model_registry.registry import ModelStage
    from alphalab.strategy.state import LifecycleState as InstanceState

    offenders = [
        name
        for name, source in _sources()
        if "class StrategyLifecycleStage" in source
        and not name.endswith("lifecycle/progression.py")
    ]
    assert offenders == [], f"a second progression vocabulary: {offenders}"

    names = {stage.name for stage in StrategyLifecycleStage}
    assert names != {stage.name for stage in ModelStage}
    assert names != {stage.name for stage in InstanceState}
    # Each answers a different question, so each has members the others lack.
    assert "PRODUCTION_CANDIDATE" in names
    assert "PRODUCTION_CANDIDATE" not in {stage.name for stage in ModelStage}
    assert "DISPOSED" not in names


def test_the_progression_relates_to_model_stage_rather_than_replacing_it() -> None:
    """The relation is total and declared, so the two axes cannot silently drift."""

    from alphalab.lifecycle.progression import (
        PROGRESSION_MODEL_STAGES,
        StrategyLifecycleStage,
    )
    from alphalab.model_registry.registry import ModelStage

    assert set(PROGRESSION_MODEL_STAGES) == set(StrategyLifecycleStage)
    for stages in PROGRESSION_MODEL_STAGES.values():
        assert stages
        assert stages <= set(ModelStage)


def test_the_progression_is_not_keyed_by_environment() -> None:
    """``ROADMAP.md``'s deliberate boundary: one stage across all environments.

    A per-environment stage would be a second source of truth beside the
    deployment ledger, which is what that boundary exists to prevent.
    """

    from alphalab.lifecycle.progression import StrategyProgression

    fields = {field.name for field in dataclasses.fields(StrategyProgression)}
    assert fields == {"reference", "stage", "history"}
    assert "environment" not in fields


def test_there_is_one_of_each_new_authority() -> None:
    markers = {
        "class HealthCategory": "lifecycle/health.py",
        "class ComparisonOutcome": "lifecycle/comparison.py",
        "class MismatchCategory": "lifecycle/reconciliation.py",
        "class DeploymentSpecification": "lifecycle/specification.py",
        "class Tolerance": "lifecycle/tolerance.py",
    }
    for marker, home in markers.items():
        offenders = [
            name for name, source in _sources() if marker in source and not name.endswith(home)
        ]
        assert offenders == [], f"a second {marker!r}: {offenders}"


def test_v35_defines_no_second_order_fill_position_or_account_type() -> None:
    """Every compared value is read from an existing authority."""

    offenders: list[str] = []
    for name, source in _v35_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in {
                "Order",
                "BrokerOrder",
                "Fill",
                "ExecutionReport",
                "Position",
                "BrokerPosition",
                "Account",
                "BrokerAccount",
                "Trade",
            }:
                offenders.append(f"{name}: {node.name}")
    assert offenders == [], f"v3.5 redefined a canonical execution type: {offenders}"


def test_the_two_reconciliation_functions_compare_different_pairs() -> None:
    """``broker.reconcile`` is unchanged and still owns its own pair."""

    from alphalab.broker.reconciliation import reconcile
    from alphalab.lifecycle.reconciliation import reconcile_execution_state

    mirror = inspect.signature(reconcile).parameters
    book = inspect.signature(reconcile_execution_state).parameters

    assert set(mirror) == {"state", "remote_orders", "remote_positions", "remote_account"}
    assert set(book) == {"pipeline", "broker", "mapping", "symbols", "tolerances"}
    # The v3.5 function reads a BrokerState as one *side*, not as the thing
    # being corrected, and it takes an ExecutionPipelineState the older one
    # has never seen.
    assert book["pipeline"].annotation == "ExecutionPipelineState"


def test_live_health_is_untouched_and_still_returns_sentences() -> None:
    """The v2.16 surface is not replaced, redefined or re-typed."""

    from alphalab.runtime.live import live_health

    signature = inspect.signature(live_health)
    assert list(signature.parameters) == ["state"]
    assert signature.return_annotation == "tuple[str, ...]"
    assert "alphalab.lifecycle" not in inspect.getsource(sys.modules["alphalab.runtime.live"])


# --------------------------------------------------------------------------- #
# 2. Determinism
# --------------------------------------------------------------------------- #


def test_no_v35_module_reads_a_clock_or_a_salted_hash() -> None:
    offenders: list[str] = []
    for name, source in _v35_sources():
        code = _code(source)
        for marker in (
            "time.time(",
            "datetime.now(",
            "utc_now(",
            "random.",
            "hash(",
            "uuid4",
            "new_id(",
        ):
            if marker in code:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], f"a v3.5 path is non-deterministic: {offenders}"


_CROSS_PROCESS = """
from decimal import Decimal

from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.lifecycle import (
    BrokerRequirements,
    CapitalPolicy,
    DatasetAssumption,
    MarketRequirements,
    RuntimeRequirements,
    StrategyVersionRef,
    Tolerance,
    build_specification,
)
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)

specification = build_specification(
    strategy=StrategyVersionRef("momentum", 7),
    parameters={"fast": 10.0, "slow": 30.5},
    datasets=(DatasetAssumption("prices", "dsv-abc"),),
    risk=RiskLimits(
        order_size=OrderSizeLimit(Decimal("100"), Decimal("10000")),
        position=PositionLimit(Decimal("1000"), Decimal("100000")),
        exposure=ExposureLimit(Decimal("200000"), Decimal("150000")),
        leverage=LeverageLimit(Decimal("2")),
        margin=MarginLimit(Decimal("0.5")),
        daily_loss=DailyLossLimit(Decimal("5000")),
        drawdown=DrawdownLimit(Decimal("0.2")),
    ),
    capital=CapitalPolicy("acct", "USD", Decimal("1000000"), ("USD", "EUR")),
    broker=BrokerRequirements(
        order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
        time_in_force=frozenset({TimeInForce.DAY}),
        asset_classes=frozenset({AssetType.EQUITY}),
        short_selling=True,
        fractional_quantities=False,
    ),
    market=MarketRequirements(("a", "b"), ("XNYS",), ("XNYS",), ("USD",)),
    runtime=RuntimeRequirements(60.0, 30.0, 2.0, Tolerance(absolute=Decimal("0.5"))),
)
print(specification.specification_id)
"""


def _in_a_fresh_interpreter() -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(PACKAGE.parent),
    )
    return completed.stdout.strip()


def test_a_deployment_identity_is_the_same_in_two_separate_processes() -> None:
    """A salted hash is stable inside one run; only a second one proves this."""

    first = _in_a_fresh_interpreter()
    second = _in_a_fresh_interpreter()

    assert first == second
    assert len(first) == 64, "a SHA-256 hex digest, not something process-local"


def test_a_specification_identity_is_a_pure_function_of_its_content() -> None:
    from alphalab.lifecycle import specification_id_for

    source = inspect.getsource(specification_id_for)
    assert "hashlib.sha256" in source
    assert "sorted(" in source
    assert "!r" in source


# --------------------------------------------------------------------------- #
# 3. Missing data stays missing
# --------------------------------------------------------------------------- #


def test_a_health_report_is_never_healthy_while_something_is_unevaluated() -> None:
    """Swept over every subset of the seven categories, not argued about."""

    import itertools

    from alphalab.lifecycle.health import (
        HealthCategory,
        HealthReport,
        HealthStatus,
        UnevaluatedCategory,
    )

    for size in range(1, len(HealthCategory) + 1):
        for missing in itertools.combinations(HealthCategory, size):
            report = HealthReport(
                specification_id="s",
                evaluated_at=0.0,
                findings=(),
                unevaluated=tuple(UnevaluatedCategory(c, "not observed") for c in missing),
            )
            assert report.status is HealthStatus.UNKNOWN
            assert not report.fully_evaluated

    assert HealthReport(specification_id="s", evaluated_at=0.0).status is HealthStatus.HEALTHY


def test_a_comparison_never_turns_a_missing_value_into_a_zero() -> None:
    from alphalab.core.enums import Side
    from alphalab.lifecycle import (
        ComparisonMetric,
        ComparisonOutcome,
        ComparisonSource,
        FillObservation,
        RunObservations,
        Tolerance,
        TradeObservation,
        compare_runs,
    )

    tolerances = dict.fromkeys(ComparisonMetric, Tolerance(absolute=Decimal("0")))
    left = RunObservations(
        source=ComparisonSource.EXPECTED,
        trades=(TradeObservation("k", "a", Side.BUY, Decimal("1"), Decimal("1"), 1.0),),
        fills=(FillObservation("k", "a", Side.BUY, Decimal("1"), Decimal("1"), Decimal("0"), 1.0),),
    )
    right = RunObservations(source=ComparisonSource.LIVE, trades=(), fills=())

    result = compare_runs(left, right, tolerances)

    assert result.entries
    for entry in result.entries:
        if entry.outcome in (
            ComparisonOutcome.MISSING_EXPECTED,
            ComparisonOutcome.MISSING_OBSERVED,
        ):
            assert entry.expected is None or entry.observed is None
        assert not (
            entry.expected == Decimal("0") and entry.outcome is ComparisonOutcome.EXACT_MATCH
        ), "a missing value was compared as a zero"


def test_a_metric_with_no_tolerance_is_not_comparable_rather_than_equal() -> None:
    from alphalab.core.enums import Side
    from alphalab.lifecycle import (
        ComparisonMetric,
        ComparisonOutcome,
        ComparisonSource,
        RunObservations,
        TradeObservation,
        compare_runs,
    )

    trade = TradeObservation("k", "a", Side.BUY, Decimal("1"), Decimal("1"), 1.0)
    result = compare_runs(
        RunObservations(ComparisonSource.EXPECTED, trades=(trade,), fills=()),
        RunObservations(ComparisonSource.LIVE, trades=(trade,), fills=()),
        {},
    )

    quantities = result.entries_for(ComparisonMetric.TRADE_QUANTITY)
    assert quantities
    assert all(entry.outcome is ComparisonOutcome.NOT_COMPARABLE for entry in quantities)


def test_a_tolerance_that_bounds_nothing_cannot_be_constructed() -> None:
    from alphalab.lifecycle import LifecycleInputError, Tolerance

    with pytest.raises(LifecycleInputError):
        Tolerance()


# --------------------------------------------------------------------------- #
# 4. Detection is not remediation
# --------------------------------------------------------------------------- #


def test_no_v35_module_mutates_anything_in_place() -> None:
    """No ``object.__setattr__``, and no assignment to an argument's attribute."""

    offenders: list[str] = []
    for name, source in _v35_sources():
        if "object.__setattr__" in _code(source):
            offenders.append(f"{name}: object.__setattr__")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        offenders.append(f"{name}: assignment to {ast.unparse(target)}")
    assert offenders == [], f"a v3.5 module mutates state: {offenders}"


def test_reconciliation_and_health_return_reports_rather_than_states() -> None:
    from alphalab.lifecycle import evaluate_health, reconcile_execution_state

    assert inspect.signature(evaluate_health).return_annotation == "HealthReport"
    assert inspect.signature(reconcile_execution_state).return_annotation == "StateReconciliation"


def test_v35_added_no_snapshot_owner_and_no_schema_constant() -> None:
    """Every durable state has one snapshot owner and one module-local schema
    literal, and AlphaLab has no migration framework. A progression, a
    specification, a health report and a reconciliation are values a caller
    holds, so there is nothing to persist and nothing to version."""

    offenders: list[str] = []
    for name, source in _v35_sources():
        for marker in ("SNAPSHOT_SCHEMA", "def capture(", "def restore(", "__serializable__"):
            if marker in source:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], f"a v3.5 module grew durable state: {offenders}"


def test_the_lifecycle_state_and_its_snapshot_are_unchanged_by_v35() -> None:
    """A new field there would make every payload written before v3.5
    unreadable, to serve a value the caller can simply hold."""

    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.lifecycle.state import LifecycleState

    assert {field.name for field in dataclasses.fields(LifecycleState)} == {
        "experiments",
        "models",
        "strategies",
        "deployments",
        "evidence",
        "releases",
        "approvals",
    }
    # Unmoved from v3.4: no payload written before this release stopped reading.
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 2


# --------------------------------------------------------------------------- #
# 5. Dimensions and units
# --------------------------------------------------------------------------- #


def test_every_v35_duration_field_names_its_unit() -> None:
    offenders: list[str] = []
    for name, source in _v35_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                if not isinstance(statement.target, ast.Name):
                    continue
                field = statement.target.id
                if any(
                    token in field for token in ("latency", "staleness", "silence")
                ) and not field.endswith("_seconds"):
                    offenders.append(f"{name}: {node.name}.{field}")
    assert offenders == [], f"a duration field does not name its unit: {offenders}"


def test_money_is_compared_per_currency_and_never_summed_across_two() -> None:
    from alphalab.lifecycle import (
        ComparisonMetric,
        ComparisonSource,
        RunObservations,
        Tolerance,
        compare_runs,
    )
    from alphalab.portfolio.amounts import CurrencyAmounts

    left = RunObservations(
        source=ComparisonSource.EXPECTED,
        realized_pnl=CurrencyAmounts({"USD": Decimal("100.00"), "JPY": Decimal("-100.00")}),
    )
    right = RunObservations(
        source=ComparisonSource.LIVE,
        realized_pnl=CurrencyAmounts({"USD": Decimal("200.00"), "JPY": Decimal("-200.00")}),
    )

    result = compare_runs(
        left, right, {ComparisonMetric.REALIZED_PNL: Tolerance(absolute=Decimal("0"))}
    )

    entries = result.entries_for(ComparisonMetric.REALIZED_PNL)
    assert [entry.key for entry in entries] == ["JPY", "USD"]
    # Two material differences, not one net of zero.
    assert len(result.material) == 2


def test_the_comparison_layer_computes_no_exposure_of_its_own() -> None:
    """A third exposure authority would be the one that forgot the multiplier."""

    code = _code(dict(_v35_sources())["alphalab/lifecycle/comparison.py"])
    for marker in ("market_value", "gross_exposure", "multiplier"):
        assert marker not in code, f"the comparison layer reads {marker}"
    assert "def _exposure" not in code


# --------------------------------------------------------------------------- #
# 6. The vendor and dependency boundary
# --------------------------------------------------------------------------- #


def test_no_v35_module_names_or_reaches_a_venue() -> None:
    offenders: list[str] = []
    for name, source in _v35_sources():
        lowered = _code(source).lower()
        for marker in (
            "http",
            "requests",
            "websocket",
            "urllib",
            "socket",
            "api_key",
            "api_secret",
            "credential",
        ):
            if marker in lowered:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], f"a v3.5 module reaches connectivity or a secret: {offenders}"


def test_v35_added_no_prohibited_integration() -> None:
    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    textual = tuple(
        term
        for term in FORBIDDEN_VENDORS
        if term not in ("torch", "transformers", "marketplace", "knight")
    )
    offenders = [
        f"{name}: {term}"
        for name, source in _v35_sources()
        for term in textual
        if term in source.lower()
    ]  # raw text on purpose: a vendor named in prose is a claim too
    assert offenders == [], f"a prohibited integration is named in {offenders}"


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    pyproject = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == []


def test_the_lifecycle_package_sits_above_the_execution_path_and_nothing_imports_it() -> None:
    """Which is what makes widening its footprint safe."""

    edges: set[str] = set()
    importers: set[str] = set()
    for name, source in _sources():
        package = ".".join(name[: -len(".py")].replace("/", ".").split(".")[:2])
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "alphalab"
            ):
                continue
            assert node.module is not None
            target = ".".join(node.module.split(".")[:2])
            if package == "alphalab.lifecycle" and target != package:
                edges.add(target)
            if target == "alphalab.lifecycle" and package != target:
                importers.add(package)

    assert importers == set(), f"alphalab.lifecycle is imported by {sorted(importers)}"
    assert "alphalab.runtime" in edges, "the v3.5 bridge to the live driver is missing"
    assert "alphalab.brokers" not in edges, "the multi-broker router is not a lifecycle concern"
    assert "alphalab.marketdata" not in edges, "no vendor market-data package"


# --------------------------------------------------------------------------- #
# 7. Value semantics
# --------------------------------------------------------------------------- #


def test_every_v35_type_is_a_frozen_dataclass_an_enum_or_a_protocol() -> None:
    """Value semantics, like every other state in AlphaLab.

    Read from the decorator rather than from the class object, so the test pins
    the declaration a reader sees -- including ``slots=True``, which is what
    stops a field being added to an instance at runtime.
    """

    offenders: list[str] = []
    counted = 0
    for name, source in _v35_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            bases = {ast.unparse(base) for base in node.bases}
            if bases & {"Enum", "Protocol"}:
                counted += 1
                continue
            decorators = {ast.unparse(decorator) for decorator in node.decorator_list}
            counted += 1
            if "dataclass(frozen=True, slots=True)" not in decorators:
                offenders.append(f"{name}: {node.name} is {sorted(decorators)}")

    assert counted > 20, "the sweep found almost nothing, so it is not measuring"
    assert offenders == [], f"a v3.5 type is not an immutable value: {offenders}"


def test_the_public_surface_advertises_every_new_name() -> None:
    from alphalab import lifecycle

    for module in V35_MODULES:
        imported = sys.modules["alphalab.lifecycle." + module.rsplit("/", 1)[-1][: -len(".py")]]
        for name in imported.__all__:
            assert hasattr(lifecycle, name), f"{name} is not reachable from alphalab.lifecycle"
            assert name in lifecycle.__all__, f"{name} is not advertised by __all__"
