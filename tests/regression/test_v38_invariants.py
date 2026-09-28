"""The v3.8 invariants, measured from the code rather than claimed in a release note.

Same shape as ``test_v37_invariants.py``. Each section is one property the
portfolio and risk work depends on. The property sections generate inputs and
check every answer against an evaluation written here, from the definitions --
constraints evaluated directly from the constraint set, contributions summed by
hand, capital reconciled account by account -- because an invariant tested
only against the implementation's own helpers would pass with the helpers
wrong. The determinism section spawns fresh interpreters with different hash
seeds *and different working directories*, because a salted ``hash()`` or a
relative path is invisible inside one process.
"""

from __future__ import annotations

import ast
import enum
import json
import math
import os
import pathlib
import random
import re
import subprocess
import sys
import types
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Protocol

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
ROOT = PACKAGE.parent

#: The modules v3.8 added. Every sweep is scoped to them.
V38_MODULES = (
    "alphalab/allocation/capital.py",
    "alphalab/analytics/cross_strategy.py",
    "alphalab/analytics/risk_budget.py",
    "alphalab/analytics/risk_model.py",
    "alphalab/factor_library/loadings.py",
    "alphalab/portfolio/multi_strategy.py",
    "alphalab/portfolio_optimizer/black_litterman.py",
    "alphalab/portfolio_optimizer/construction.py",
    "alphalab/portfolio_optimizer/quadratic.py",
    "alphalab/portfolio_optimizer/risk_parity.py",
)


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(ROOT)), path.read_text()))
    return found


def _v38_sources() -> list[tuple[str, str]]:
    return [(name, text) for name, text in _sources() if name in V38_MODULES]


def test_every_v38_module_exists() -> None:
    """So a rename cannot silently empty every sweep in this file."""

    assert len(_v38_sources()) == len(V38_MODULES)


# --------------------------------------------------------------------------- #
# 1. One authority per concept
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("marker", "home"),
    [
        ("class CovarianceMatrix", "analytics/risk_model.py"),
        ("class CorrelationMatrix", "analytics/risk_model.py"),
        ("class FactorLoadings", "analytics/risk_model.py"),
        ("class Classification", "analytics/risk_model.py"),
        ("class RiskContributions", "analytics/risk_model.py"),
        ("def euler_decomposition", "analytics/risk_model.py"),
        ("def herfindahl_index", "analytics/risk_model.py"),
        ("def _pairwise_sample_covariance", "analytics/risk_model.py"),
        ("def _euler", "analytics/risk_model.py"),
        ("def _accumulate_exposures", "analytics/risk_model.py"),
        ("def _correlation_rows", "analytics/risk_model.py"),
        ("class RiskBudget", "analytics/risk_budget.py"),
        ("class BudgetLimit", "analytics/risk_budget.py"),
        ("def evaluate_risk_budget", "analytics/risk_budget.py"),
        ("def strategy_return_correlation", "analytics/cross_strategy.py"),
        ("def strategy_overlap", "analytics/cross_strategy.py"),
        ("def factor_crowding", "analytics/cross_strategy.py"),
        ("def capital_concentration", "analytics/cross_strategy.py"),
        ("class ConstructionProblem", "portfolio_optimizer/construction.py"),
        ("class ConstraintSet", "portfolio_optimizer/construction.py"),
        ("def construct", "portfolio_optimizer/construction.py"),
        ("def solve_quadratic_program", "portfolio_optimizer/quadratic.py"),
        ("def _cholesky", "portfolio_optimizer/quadratic.py"),
        ("def solve_risk_budgets", "portfolio_optimizer/risk_parity.py"),
        ("def black_litterman", "portfolio_optimizer/black_litterman.py"),
        ("class MultiStrategyBook", "portfolio/multi_strategy.py"),
        ("class StrategySleeve", "portfolio/multi_strategy.py"),
        ("def value_book", "portfolio/multi_strategy.py"),
        ("class CapitalAllocationPlan", "allocation/capital.py"),
        ("def allocate_capital", "allocation/capital.py"),
        ("def reserved_capital", "allocation/capital.py"),
        ("def capital_budget", "allocation/capital.py"),
        ("def loadings_from_panels", "factor_library/loadings.py"),
        ("def research_configuration_with_portfolio", "lifecycle/fingerprint.py"),
    ],
)
def test_each_new_concept_has_exactly_one_home(marker: str, home: str) -> None:
    pattern = re.compile(rf"^{re.escape(marker)}\b", re.MULTILINE)
    found = [name for name, source in _sources() if pattern.search(source)]

    assert found == [f"alphalab/{home}"], f"{marker!r} is defined in {found}"


def test_the_sample_covariance_is_estimated_pairwise_in_one_place() -> None:
    """``common.statistics.sample_covariance`` is called by the risk model and nothing else."""

    direct = re.compile(r"\bsample_covariance\(")
    callers = sorted(
        name
        for name, source in _sources()
        if direct.search(source) and name != "alphalab/common/statistics.py"
    )

    assert callers == ["alphalab/analytics/risk_model.py"]


def test_the_v33_decomposition_reads_the_shared_kernels_and_spells_none_itself() -> None:
    source = (PACKAGE / "analytics" / "decomposition.py").read_text()

    for kernel in (
        "_pairwise_sample_covariance",
        "_euler",
        "_accumulate_exposures",
        "_correlation_rows",
        "herfindahl_index",
    ):
        assert kernel in source
    assert not re.search(r"\bsample_covariance\(", source)
    assert "* covariance[first][second] * weights[second]" not in source


def test_the_v33_decomposition_is_unchanged_float_for_float() -> None:
    """The kernels were extracted without moving one published number.

    Recomputed here with the exact v3.3 expressions, on generated books.
    """

    from alphalab.analytics.decomposition import (
        PositionRisk,
        concentration,
        covariance_matrix,
        factor_exposure,
        gross_weights,
        portfolio_volatility,
        risk_contributions,
    )
    from alphalab.common.statistics import sample_covariance

    for seed in range(25):
        rng = random.Random(seed)
        book = [
            PositionRisk(
                f"A{index}",
                Decimal(str(round(rng.uniform(-5e5, 5e5), 2))) or Decimal("1"),
                "USD",
                tuple(rng.gauss(0.0005, 0.02) for _ in range(20)),
            )
            for index in range(rng.randint(1, 7))
        ]
        ordered = sorted(book, key=lambda item: item.asset_id)
        old_cov: dict[str, dict[str, float]] = {p.asset_id: {} for p in ordered}
        for outer, first in enumerate(ordered):
            for second in ordered[outer:]:
                value = sample_covariance(first.returns, second.returns)
                old_cov[first.asset_id][second.asset_id] = value
                old_cov[second.asset_id][first.asset_id] = value
        weights = gross_weights(book)
        variance = sum(weights[a] * old_cov[a][b] * weights[b] for a in weights for b in weights)
        volatility = math.sqrt(max(0.0, variance))
        loadings = {p.asset_id: {"m": rng.uniform(-1, 2)} for p in book}
        old_exposure: dict[str, float] = {}
        for asset, factors in sorted(loadings.items()):
            for factor, loading in sorted(factors.items()):
                old_exposure[factor] = old_exposure.get(factor, 0.0) + weights[asset] * loading

        assert covariance_matrix(book) == old_cov
        assert portfolio_volatility(book) == volatility
        assert risk_contributions(book) == {
            a: weights[a] * sum(old_cov[a][o] * weights[o] for o in weights) / volatility
            for a in weights
        }
        assert factor_exposure(book, loadings) == old_exposure
        assert concentration(book).herfindahl == sum(w * w for w in weights.values())


# --------------------------------------------------------------------------- #
# 2. Layering, measured from the import graph
# --------------------------------------------------------------------------- #


def _package_edges(package: str) -> set[str]:
    edges: set[str] = set()
    for path in sorted((PACKAGE / package).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                parts = node.module.split(".")
                if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != package:
                    edges.add(parts[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "alphalab" and len(parts) > 1 and parts[1] != package:
                        edges.add(parts[1])
    return edges


def test_the_construction_authority_reads_only_common_and_the_risk_model() -> None:
    """ADR-0043: ``portfolio_optimizer``'s one new edge is ``analytics``.

    v3.11 (ledger OFE-002, ADR-0046) adds ``conventions``: rounding a
    constructed portfolio to lots reads the instrument's declared economics
    and lot grid -- a package that itself reads only ``common``.
    """

    assert _package_edges("portfolio_optimizer") == {"common", "analytics", "conventions"}


def test_analytics_still_imports_neither_portfolio_nor_allocation_nor_instrument() -> None:
    assert _package_edges("analytics") == {"common", "core"}


def test_allocation_still_does_not_import_portfolio() -> None:
    """FX reaches capital allocation through a structural protocol, not an import."""

    assert "portfolio" not in _package_edges("allocation")
    # v3.11 (FEA-001): a target is rounded to the instrument's lot grid, which
    # the conventions package states; conventions imports only common.
    assert _package_edges("allocation") == {
        "common",
        "conventions",
        "core",
        "persistence",
        "strategy",
    }


def test_portfolio_gains_only_core_for_the_contribution_record() -> None:
    assert _package_edges("portfolio") == {"common", "conventions", "core", "persistence"}


def test_the_lifecycle_path_reaches_no_standalone_construction_engine() -> None:
    """A fingerprint names constructions and plans through protocols, importing neither."""

    edges = _package_edges("lifecycle")

    assert "portfolio_optimizer" not in edges
    assert "allocation" not in edges
    assert "analytics" not in edges


def test_common_is_still_the_bottom_layer() -> None:
    assert _package_edges("common") == set()


# --------------------------------------------------------------------------- #
# 3. Determinism: no clock, no salted hash, no environment
# --------------------------------------------------------------------------- #

_NONDETERMINISTIC_MODULES = frozenset(
    {
        "datetime",
        "getpass",
        "importlib",
        "os",
        "platform",
        "random",
        "secrets",
        "socket",
        "tempfile",
        "time",
        "uuid",
    }
)
_NONDETERMINISTIC_CALLS = frozenset({"hash", "id", "new_id", "uuid4"})


def test_no_v38_module_reads_a_clock_entropy_or_the_environment() -> None:
    offenders: list[str] = []
    for name, source in _v38_sources():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                roots = set()
            offenders.extend(
                f"{name}: imports {root}" for root in roots & _NONDETERMINISTIC_MODULES
            )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _NONDETERMINISTIC_CALLS
            ):
                offenders.append(f"{name}: calls {node.func.id}()")
    assert offenders == [], f"a v3.8 path is non-deterministic: {offenders}"


def test_no_v38_solver_uses_a_transcendental_whose_last_bit_is_platform_dependent() -> None:
    """Only ``+ - * /`` and ``sqrt`` -- all correctly rounded under IEEE-754.

    ``exp``, ``log``, ``pow`` and ``hypot`` come from each platform's ``libm``
    and may differ in the last bit, which would reach a result identity.
    """

    offenders = [
        f"{name}: math.{function}"
        for name, source in _v38_sources()
        for function in ("exp", "log", "pow", "hypot", "sin", "cos", "tan")
        if f"math.{function}(" in source
    ]
    assert offenders == []


def identities() -> list[str]:
    """Every v3.8 identity, built from fixed inputs -- run in fresh interpreters."""

    from alphalab.allocation.capital import (
        CapitalAccount,
        CapitalAllocationPlan,
        CapitalPlacement,
        OversubscriptionRule,
        PlacementWeights,
        allocate_capital,
    )
    from alphalab.analytics.risk_budget import (
        BudgetBasis,
        BudgetLimit,
        RiskBudget,
        RiskDimension,
        evaluate_risk_budget,
    )
    from alphalab.analytics.risk_model import Classification, CovarianceMatrix, FactorLoadings
    from alphalab.portfolio.amounts import CurrencyAmounts
    from alphalab.portfolio.fx import FxRate, FxRates
    from alphalab.portfolio.multi_strategy import MultiStrategyBook, StrategySleeve, value_book
    from alphalab.portfolio.position import Position
    from alphalab.portfolio_optimizer import (
        BlackLittermanModel,
        ConstraintSet,
        ConstructionProblem,
        EllipsoidalUncertainty,
        EquilibriumPrior,
        ExpectedReturns,
        ExposureRange,
        FactorBound,
        GroupBound,
        InvestorView,
        RiskParity,
        RobustMeanVariance,
        SolverSettings,
        WeightBounds,
        black_litterman,
        construct,
    )

    rng = random.Random(38)
    covariance = CovarianceMatrix.sample(
        {
            name: [rng.gauss(0.0, 0.01 * (i + 1)) for _ in range(40)]
            for i, name in enumerate(("A", "B", "C"))
        },
        currency="USD",
        period="1D",
        source="identity fixture",
    )
    loadings = FactorLoadings.of(
        {"A": {"f": 0.5}, "B": {"f": 1.2}, "C": {"f": -0.3}},
        source="m",
        lineage={"f": "x"},
        as_of=1.0,
    )
    sectors = Classification("sector", {"A": "T", "B": "T", "C": "E"}, "registry", None)
    settings = SolverSettings(1e-10, 1e-10, 500)
    returns = ExpectedReturns({"A": 0.001, "B": 0.002, "C": 0.0015}, "USD", "1D", "fixture")
    robust = ConstructionProblem(
        covariance,
        RobustMeanVariance(
            returns, 5.0, EllipsoidalUncertainty.of_sample_mean(covariance, 40, 1.0)
        ),
        ConstraintSet(
            ExposureRange.exactly(1.0),
            WeightBounds.long_only(0.7),
            groups=(GroupBound(sectors, "T", None, 0.8),),
            factors=(FactorBound.target(loadings, "f", 0.6, 0.2),),
        ),
        settings,
    )
    parity = ConstructionProblem(
        covariance,
        RiskParity.equal(),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None)),
        settings,
    )
    model = BlackLittermanModel(
        covariance,
        EquilibriumPrior({"A": Decimal("5"), "B": Decimal("3"), "C": Decimal("2")}, "USD", 2.5),
        0.05,
        (InvestorView("v", {"A": 1.0, "C": -1.0}, 0.001, 1e-6),),
    )
    position = Position(
        "A", Decimal("10"), Decimal("100"), Decimal("101"), Decimal("0"), "EUR", 1.0
    )
    book = MultiStrategyBook(
        "P",
        (
            StrategySleeve(
                "S", {"A": position}, CurrencyAmounts(), CurrencyAmounts(), CurrencyAmounts()
            ),
        ),
        CurrencyAmounts(),
    )
    rates = FxRates.of([FxRate("EUR", "USD", Decimal("1.1"), 0.0, "fixture")])
    valuation = value_book(book, reporting_currency="USD", rates=rates, as_of=1.0)
    budget = RiskBudget(
        "b", (BudgetLimit(RiskDimension.ASSET, "A", BudgetBasis.RELATIVE, 1.0, None, None),), 0.0
    )
    report = evaluate_risk_budget(
        valuation.lines,
        capital=valuation.nav,
        reporting_currency="USD",
        covariance=covariance,
        budget=budget,
        classifications=(),
    )
    placement = CapitalPlacement("S", "M", "ACC")
    plan = CapitalAllocationPlan(
        "p",
        "USD",
        1.0,
        (CapitalAccount("ACC", "B", "EUR", Decimal("1000"), Decimal("0")),),
        (placement,),
        PlacementWeights({placement: Decimal("0.5")}, "fixture"),
        (),
        OversubscriptionRule.REFUSE,
        Decimal("1"),
    )
    allocation = allocate_capital(
        plan,
        FxRates.of(
            [
                FxRate("EUR", "USD", Decimal("1.1"), 0.0, "f"),
                FxRate("USD", "EUR", Decimal("0.9"), 0.0, "f"),
            ]
        ),
    )
    return [
        covariance.covariance_id,
        covariance.with_ridge(1e-6).covariance_id,
        loadings.loadings_id,
        sectors.classification_id,
        robust.problem_id,
        construct(robust).result_id,
        parity.problem_id,
        construct(parity).result_id,
        model.model_id,
        black_litterman(model).posterior_returns.returns_id,
        book.book_id,
        valuation.valuation_id,
        budget.budget_id,
        report.report_id,
        plan.plan_id,
        allocation.result_id,
    ]


_CROSS_PROCESS = """
from tests.regression.test_v38_invariants import identities
for value in identities():
    print(value)
"""


def _fresh(hash_seed: str, cwd: pathlib.Path) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(cwd),
        env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(ROOT)},
    )
    return completed.stdout.split()


def test_every_v38_identity_is_the_same_across_hash_seeds_and_working_directories(
    tmp_path: pathlib.Path,
) -> None:
    first = _fresh("1", ROOT)
    second = _fresh("4242", ROOT)
    elsewhere = _fresh("99", tmp_path)

    assert first == second == elsewhere
    assert len(first) == 16
    assert first == identities()


def test_nothing_machine_local_reaches_a_v38_identity_or_artifact() -> None:
    rendered = "\n".join(identities())
    local = {str(ROOT), str(pathlib.Path.home()), os.uname().nodename}
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        local.add(f"/{user}/")
    for marker in local:
        assert marker not in rendered, f"{marker!r} leaked into an identity"


# --------------------------------------------------------------------------- #
# 4. Value semantics and serialization
# --------------------------------------------------------------------------- #


def _public_classes() -> list[tuple[str, type]]:
    import importlib

    found: list[tuple[str, type]] = []
    for name in V38_MODULES:
        module = importlib.import_module(name.removesuffix(".py").replace("/", "."))
        for attribute in getattr(module, "__all__", ()):
            value = getattr(module, attribute)
            if isinstance(value, type):
                found.append((f"{module.__name__}.{attribute}", value))
    return found


def test_every_v38_type_is_a_frozen_dataclass_an_enum_or_a_protocol() -> None:
    offenders = []
    for qualified, value in _public_classes():
        if issubclass(value, enum.Enum) or getattr(value, "_is_protocol", False):
            continue
        if Protocol in getattr(value, "__mro__", ()):
            continue
        params = getattr(value, "__dataclass_params__", None)
        if params is None or not params.frozen:
            offenders.append(qualified)
    assert offenders == [], f"not a frozen value: {offenders}"


def test_object_setattr_is_used_only_while_constructing() -> None:
    offenders: list[str] = []
    for name, source in _v38_sources():
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "__setattr__"
                    and function.name != "__post_init__"
                ):
                    offenders.append(f"{name}: {function.name}")
    assert offenders == []


def test_every_v38_result_is_machine_readable_as_deterministic_json() -> None:
    """Every v3.8 result type, built from the integration workflow's real runs."""

    from alphalab.analytics import (
        CommonDimension,
        FactorLoadings,
        StrategyReturns,
        capital_concentration,
        capital_overlap,
        common_exposures,
        euler_decomposition,
        factor_crowding,
        strategy_overlap,
        strategy_return_correlation,
    )
    from alphalab.persistence.serializer import serialize
    from alphalab.portfolio_optimizer import (
        BlackLittermanModel,
        EquilibriumPrior,
        InvestorView,
        black_litterman,
    )
    from tests.integration.test_v38_capabilities import _build

    workflow = _build()
    covariance = workflow.covariance
    assets = covariance.assets
    lines = workflow.valuation.lines
    loadings = FactorLoadings.of(
        {asset: {"f": float(index) - 1.5} for index, asset in enumerate(assets)},
        source="fixture",
        lineage={"f": "fixture"},
        as_of=None,
    )
    posterior = black_litterman(
        BlackLittermanModel(
            covariance,
            EquilibriumPrior(dict.fromkeys(assets, Decimal("1")), "USD", 2.5),
            0.05,
            (InvestorView("v", {assets[0]: 1.0}, 0.001, 1e-6),),
        )
    )
    held = sorted({line.asset_id for line in lines})
    # MOM and MR ran in dollars; CARRY's returns are in euros and are left out
    # rather than relabelled.
    strategies = strategy_return_correlation(
        [
            StrategyReturns(name, workflow.research_returns[name], "USD", "1step")
            for name in ("MOM", "MR")
        ],
        source="fixture",
    )
    for value in (
        workflow.parity,
        workflow.allocation,
        workflow.book,
        workflow.valuation,
        covariance,
        covariance.correlation(),
        workflow.budget_report,
        posterior,
        euler_decomposition(dict.fromkeys(held, 1.0 / len(held)), covariance),
        strategy_overlap(lines),
        factor_crowding(lines, loadings, capital=workflow.valuation.nav),
        common_exposures(lines, by=CommonDimension.CURRENCY, classification=None),
        strategies,
        capital_concentration(
            {"A": Decimal("3"), "B": Decimal("1")}, currency="USD", dimension="strategy"
        ),
        capital_overlap({"A": {"pool": Decimal("1")}, "B": {"pool": Decimal("2")}}, currency="USD"),
    ):
        payload = serialize(value)
        assert json.loads(payload) is not None
        assert serialize(value) == payload


# --------------------------------------------------------------------------- #
# 5. Properties on generated inputs
# --------------------------------------------------------------------------- #


def _covariance(rng: random.Random, names: tuple[str, ...]) -> CovarianceMatrix:
    return CovarianceMatrix.sample(
        {
            name: [rng.gauss(0.0, rng.uniform(0.005, 0.03)) for _ in range(len(names) + 12)]
            for name in names
        },
        currency="USD",
        period="1D",
        source="generated",
    )


@pytest.mark.parametrize("seed", range(40))
def test_an_optimal_construction_satisfies_every_constraint_evaluated_directly(seed: int) -> None:
    """Constraints are evaluated here from the ConstraintSet, not by the solver's own residuals."""

    from alphalab.analytics.risk_model import Classification, FactorLoadings
    from alphalab.portfolio_optimizer import (
        ConstraintSet,
        ConstructionProblem,
        ConstructionStatus,
        ExpectedReturns,
        ExposureRange,
        FactorBound,
        GroupBound,
        MeanVariance,
        MinimumVariance,
        SolverSettings,
        TurnoverLimit,
        WeightBounds,
        construct,
    )

    rng = random.Random(seed)
    names = tuple(f"S{index}" for index in range(rng.randint(2, 6)))
    covariance = _covariance(rng, names)
    sectors = Classification("sector", {name: rng.choice("XY") for name in names}, "gen", None)
    loadings = FactorLoadings.of(
        {name: {"f": rng.uniform(-1, 1)} for name in names},
        source="g",
        lineage={"f": "g"},
        as_of=None,
    )
    upper = rng.choice([None, rng.uniform(0.3, 0.9)])
    lower = rng.choice([0.0, None, -0.2])
    net = rng.uniform(0.5, 1.0)
    cap = rng.uniform(0.2, 0.9)
    low, high = rng.uniform(-0.5, 0.0), rng.uniform(0.0, 0.5)
    current = {name: 1.0 / len(names) for name in names}
    constraints = ConstraintSet(
        ExposureRange.exactly(net),
        WeightBounds.uniform(lower, upper),
        max_gross_exposure=rng.choice([None, 1.6]),
        groups=(GroupBound(sectors, "X", None, cap),),
        factors=(FactorBound(loadings, "f", low, high),),
        turnover=rng.choice([None, TurnoverLimit(current, rng.uniform(0.2, 1.5))]),
    )
    objective = (
        MinimumVariance()
        if rng.random() < 0.5
        else MeanVariance(
            ExpectedReturns({n: rng.uniform(-0.002, 0.003) for n in names}, "USD", "1D", "g"),
            rng.uniform(1, 10),
        )
    )
    result = construct(
        ConstructionProblem(covariance, objective, constraints, SolverSettings(1e-9, 1e-9, 2000))
    )
    if result.status is not ConstructionStatus.OPTIMAL:
        assert result.status is ConstructionStatus.INFEASIBLE, result.diagnostics.detail
        assert result.weights is None and result.diagnostics.conflict
        return
    w = result.require_weights()
    tolerance = 1e-8
    assert abs(sum(w.values()) - net) <= tolerance
    for name in names:
        if lower is not None:
            assert w[name] >= lower - tolerance
        if upper is not None:
            assert w[name] <= upper + tolerance
    if constraints.max_gross_exposure is not None:
        assert sum(abs(v) for v in w.values()) <= constraints.max_gross_exposure + tolerance
    assert sum(w[n] for n in names if sectors.labels[n] == "X") <= cap + tolerance
    exposure = sum(w[n] * loadings.loading(n, "f") for n in names)
    assert low - tolerance <= exposure <= high + tolerance
    if constraints.turnover is not None:
        assert (
            sum(abs(w[n] - current[n]) for n in names) <= constraints.turnover.maximum + tolerance
        )


@pytest.mark.parametrize("seed", range(40))
def test_feasibility_and_the_optimum_agree_with_exact_rational_enumeration(seed: int) -> None:
    """Every minimum-variance construction against the exhaustive exact reference.

    The rows are written here from the constraint definitions, and the reference
    solves every candidate KKT system in rational arithmetic -- so an
    ``INFEASIBLE`` answer is checked against a proof that no feasible point
    exists, and an ``OPTIMAL`` one against the exact optimum.
    """

    from alphalab.analytics.risk_model import Classification, FactorLoadings
    from alphalab.portfolio_optimizer import (
        ConstraintSet,
        ConstructionProblem,
        ConstructionStatus,
        ExposureRange,
        FactorBound,
        GroupBound,
        MinimumVariance,
        SolverSettings,
        WeightBounds,
        construct,
    )
    from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

    rng = random.Random(5000 + seed)
    size = rng.randint(2, 4)
    names = tuple(f"S{index}" for index in range(size))
    covariance = _covariance(rng, names)
    sectors = Classification("sector", {name: rng.choice("XY") for name in names}, "gen", None)
    loadings = FactorLoadings.of(
        {name: {"f": rng.uniform(-1, 1)} for name in names},
        source="g",
        lineage={"f": "g"},
        as_of=None,
    )
    lower = rng.choice([0.0, -0.3, None])
    upper = rng.choice([None, rng.uniform(0.2, 0.9)])
    net, cap = rng.uniform(0.5, 1.0), rng.uniform(0.1, 0.9)
    low, high = sorted((rng.uniform(-0.5, 0.1), rng.uniform(-0.1, 0.5)))
    constraints = ConstraintSet(
        ExposureRange.exactly(net),
        WeightBounds.uniform(lower, upper),
        groups=(GroupBound(sectors, "X", None, cap),),
        factors=(FactorBound(loadings, "f", low, high),),
    )
    result = construct(
        ConstructionProblem(
            covariance, MinimumVariance(), constraints, SolverSettings(1e-10, 1e-10, 2000)
        )
    )

    rows: list[Row] = [([1.0] * size, net, True)]
    for index in range(size):
        unit = [1.0 if other == index else 0.0 for other in range(size)]
        if lower is not None:
            rows.append((unit, lower, False))
        if upper is not None:
            rows.append(([-value for value in unit], -upper, False))
    rows.append(([-1.0 if sectors.labels[name] == "X" else 0.0 for name in names], -cap, False))
    loading = [loadings.loading(name, "f") for name in names]
    rows.append((loading, low, False))
    rows.append(([-value for value in loading], -high, False))
    reference = exact_optimum([list(row) for row in covariance.values], [0.0] * size, rows)

    if reference is None:
        assert result.status is ConstructionStatus.INFEASIBLE
        assert result.diagnostics.conflict
    else:
        assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
        weights = result.require_weights()
        assert max(abs(weights[name] - reference[i]) for i, name in enumerate(names)) < 1e-12


@pytest.mark.parametrize("seed", range(20))
def test_construction_is_deterministic_and_blind_to_input_order(seed: int) -> None:
    from alphalab.portfolio_optimizer import (
        ConstraintSet,
        ConstructionProblem,
        ExposureRange,
        RiskParity,
        SolverSettings,
        WeightBounds,
        construct,
    )

    rng = random.Random(1000 + seed)
    names = tuple(f"S{index}" for index in range(rng.randint(2, 6)))
    covariance = _covariance(rng, names)
    reversed_rows = [
        [covariance.values[i][j] for j in reversed(range(len(names)))]
        for i in reversed(range(len(names)))
    ]
    shuffled = CovarianceMatrix.from_rows(
        tuple(reversed(names)),
        reversed_rows,
        currency="USD",
        period="1D",
        source="generated",
        observations=covariance.observations,
    )
    settings = SolverSettings(1e-10, 1e-10, 500)
    constraints = ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(None))

    first = construct(ConstructionProblem(covariance, RiskParity.equal(), constraints, settings))
    assert first == construct(
        ConstructionProblem(covariance, RiskParity.equal(), constraints, settings)
    )
    assert first == construct(
        ConstructionProblem(shuffled, RiskParity.equal(), constraints, settings)
    )


@dataclass(frozen=True, slots=True)
class _Line:
    strategy_id: str
    asset_id: str
    currency: str
    market_value: Decimal
    reporting_value: Decimal


@pytest.mark.parametrize("seed", range(30))
def test_risk_budgets_conserve_volatility_along_every_dimension(seed: int) -> None:
    from alphalab.analytics.exceptions import AnalyticsValidationError
    from alphalab.analytics.risk_budget import RiskBudget, RiskDimension, evaluate_risk_budget
    from alphalab.analytics.risk_model import Classification

    rng = random.Random(2000 + seed)
    names = tuple(f"S{index}" for index in range(rng.randint(1, 6)))
    covariance = _covariance(rng, names)
    lines = []
    for strategy in ("A", "B", "C")[: rng.randint(1, 3)]:
        for name in rng.sample(names, rng.randint(1, len(names))):
            value = Decimal(str(round(rng.uniform(-1000, 1000), 2)))
            lines.append(_Line(strategy, name, rng.choice(("USD", "EUR")), value, value))
    capital = Decimal("5000")
    sectors = Classification("sector", {name: rng.choice("XYZ") for name in names}, "gen", None)
    countries = Classification(
        "country", {name: rng.choice(("US", "DE")) for name in names}, "gen", None
    )
    try:
        report = evaluate_risk_budget(
            lines, capital=capital, reporting_currency="USD", covariance=covariance,
            budget=RiskBudget("b", (), 0.0), classifications=(sectors, countries),
        )  # fmt: skip
    except AnalyticsValidationError as error:  # a flat book has no risk to apportion
        assert "no risk to decompose" in str(error)
        return

    # Independent: sigma and every contribution from the definition.
    weights = dict.fromkeys(names, 0.0)
    for line in lines:
        weights[line.asset_id] += float(line.reporting_value / capital)
    exposure = {a: sum(covariance.covariance(a, b) * weights[b] for b in names) for a in names}
    sigma = math.sqrt(sum(weights[a] * exposure[a] for a in names))
    assert report.volatility == pytest.approx(sigma, rel=1e-12)
    bound = (
        64
        * len(lines)
        * sys.float_info.epsilon
        * sum(abs(line.contribution) for line in report.lines)
    )
    for dimension in report.dimensions:
        assert abs(math.fsum(b.contribution for b in dimension.buckets) - sigma) <= bound + 1e-15
    assert [d.dimension for d in report.dimensions][:1] == [RiskDimension.ASSET]


@pytest.mark.parametrize("seed", range(30))
def test_a_book_keeps_every_strategy_and_reconciles_in_every_currency(seed: int) -> None:
    from alphalab.portfolio.amounts import CurrencyAmounts
    from alphalab.portfolio.fx import FxRate, FxRates
    from alphalab.portfolio.multi_strategy import MultiStrategyBook, StrategySleeve, value_book
    from alphalab.portfolio.position import Position

    rng = random.Random(3000 + seed)
    currency = {name: rng.choice(("USD", "EUR", "JPY")) for name in ("A", "B", "C", "D")}
    marks = {name: Decimal(str(round(rng.uniform(1, 500), 2))) for name in currency}
    sleeves = []
    for strategy in ("S1", "S2", "S3")[: rng.randint(1, 3)]:
        held = rng.sample(sorted(currency), rng.randint(1, 4))
        positions = {
            name: Position(
                name,
                Decimal(rng.randint(-50, 50) or 1),
                marks[name],
                marks[name],
                Decimal("0"),
                currency[name],
                1.0,
            )
            for name in held
        }
        sleeves.append(
            StrategySleeve(
                strategy,
                positions,
                CurrencyAmounts.single(Decimal("100"), "USD"),
                CurrencyAmounts(),
                CurrencyAmounts(),
            )
        )
    book = MultiStrategyBook("P", tuple(sleeves), CurrencyAmounts())
    rates = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.1"), 0.0, "g"),
            FxRate("JPY", "USD", Decimal("0.0067"), 0.0, "g"),
        ]
    )
    valuation = value_book(book, reporting_currency="USD", rates=rates, as_of=1.0)

    for holding in book.holdings():
        by_hand = sum(
            (
                s.positions[holding.asset_id].quantity
                for s in sleeves
                if holding.asset_id in s.positions
            ),
            Decimal(0),
        )
        assert (
            holding.net_quantity
            == by_hand
            == sum((c.quantity for c in holding.contributions), Decimal(0))
        )
    assert valuation.net_exposure == sum(
        (line.reporting_value for line in valuation.lines), Decimal(0)
    )
    assert valuation.net_exposure == sum((s.net_exposure for s in valuation.strategies), Decimal(0))
    assert valuation.net_exposure == sum(
        valuation.reporting_exposure_by_currency.values(), Decimal(0)
    )
    for code in valuation.exposure_by_currency.currencies:
        native = sum(
            (p.market_value for s in sleeves for p in s.positions.values() if p.currency == code),
            Decimal(0),
        )
        assert valuation.exposure_by_currency.of(code) == native


@pytest.mark.parametrize("seed", range(40))
def test_capital_reconciles_exactly_and_is_never_negative_or_quietly_scaled(seed: int) -> None:
    from alphalab.allocation.capital import (
        CapitalAccount,
        CapitalAllocationPlan,
        CapitalAllocationStatus,
        CapitalPlacement,
        FixedAmounts,
        OversubscriptionRule,
        PlacementWeights,
        allocate_capital,
    )
    from alphalab.portfolio.fx import FxRate, FxRates

    rng = random.Random(4000 + seed)
    accounts = tuple(
        CapitalAccount(
            f"ACC{index}",
            f"BROKER{index % 2}",
            rng.choice(("USD", "EUR")),
            Decimal(rng.randint(0, 2_000_000)),
            Decimal(0),
        )
        for index in range(rng.randint(1, 3))
    )
    accounts = tuple(
        CapitalAccount(
            a.account_id,
            a.broker_id,
            a.currency,
            a.available,
            (a.available * Decimal(rng.randint(0, 3)) / 10).quantize(Decimal("1")),
        )
        for a in accounts
    )
    placements = tuple(
        CapitalPlacement(f"STRAT{index}", rng.choice(("M1", "M2")), rng.choice(accounts).account_id)
        for index in range(rng.randint(1, 5))
    )
    rule: FixedAmounts | PlacementWeights
    if rng.random() < 0.5:
        # Fractions of total free capital summing to at most one, exactly.
        cuts = [Decimal(0), *sorted(Decimal(rng.randint(0, 10_000)) / 10_000 for _ in placements)]
        rule = PlacementWeights(
            {p: high - low for p, (low, high) in zip(placements, pairwise(cuts), strict=True)},
            "gen",
        )
    else:
        rule = FixedAmounts({p: Decimal(rng.randint(0, 1_500_000)) for p in placements})
    oversubscription = rng.choice(list(OversubscriptionRule))
    granularity = Decimal(rng.choice(("1", "100", "0.01")))
    plan = CapitalAllocationPlan(
        "gen", "USD", 1.0, accounts, placements, rule, (), oversubscription, granularity
    )
    rates = FxRates.of(
        [
            FxRate("EUR", "USD", Decimal("1.1"), 0.0, "g"),
            FxRate("USD", "EUR", Decimal("0.9"), 0.0, "g"),
        ]
    )
    result = allocate_capital(plan, rates)

    for entry in result.accounts:
        # Reconciled exactly in the account's own currency, never negative.
        assert (
            entry.account.available == entry.account.reserved + entry.allocated + entry.unallocated
        )
        assert entry.allocated >= 0 and entry.unallocated >= 0
    if result.status is CapitalAllocationStatus.REFUSED:
        assert oversubscription is OversubscriptionRule.REFUSE
        assert result.placements is None and result.dimensions == ()
        assert all(entry.allocated == 0 and entry.scale is None for entry in result.accounts)
        assert result.reasons
        return
    assert result.placements is not None
    by_account = {entry.account.account_id: entry for entry in result.accounts}
    for placed in result.placements:
        assert placed.allocated >= 0
        assert placed.allocated % granularity == 0
        account = by_account[placed.placement.account_id]
        if account.scale is None:
            # No scale was stated for this account, so nothing was scaled: the
            # allocation is the request rounded down, and never less.
            assert placed.allocated <= placed.requested < placed.allocated + granularity
        else:
            # A scale was applied only under PRO_RATA, only to an oversubscribed
            # account, and it is said in words.
            assert oversubscription is OversubscriptionRule.PRO_RATA
            assert account.requested > account.account.free
            assert any(repr(account.account.account_id) in reason for reason in result.reasons)
            scaled = placed.requested * account.scale
            assert placed.allocated <= scaled < placed.allocated + granularity
    for account in result.accounts:
        assert account.allocated == sum(
            (
                e.allocated
                for e in result.placements
                if e.placement.account_id == account.account.account_id
            ),
            Decimal(0),
        )
    assert result == allocate_capital(plan, rates)


# --------------------------------------------------------------------------- #
# 6. Nothing durable, nothing vendor, the surfaces say so
# --------------------------------------------------------------------------- #


def test_v38_added_no_snapshot_owner_and_no_schema_constant() -> None:
    constants = sorted(
        {
            match
            for _, source in _sources()
            for match in re.findall(r"^([A-Z_]*_SCHEMA)\s*(?::[^=]*)?=\s*\d", source, re.MULTILINE)
        }
    )

    assert constants == [
        "ALLOCATION_SNAPSHOT_SCHEMA",
        "BROKER_SNAPSHOT_SCHEMA",
        "FX_FEED_SNAPSHOT_SCHEMA",
        "INSTRUMENT_SNAPSHOT_SCHEMA",
        "LIFECYCLE_SNAPSHOT_SCHEMA",
        "LIVE_SNAPSHOT_SCHEMA",
        "OMS_SNAPSHOT_SCHEMA",
        "PIPELINE_SNAPSHOT_SCHEMA",
        "PORTFOLIO_SNAPSHOT_SCHEMA",
        "RUN_SNAPSHOT_SCHEMA",
        "RUN_STATE_ENVELOPE_SCHEMA",
    ]
    for name, source in _v38_sources():
        assert "SNAPSHOT_SCHEMA" not in source, name


def test_no_v38_module_names_a_vendor_or_reaches_a_network() -> None:
    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    network = {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "urllib",
    }
    for name, source in _v38_sources():
        lowered = source.lower()
        for term in FORBIDDEN_VENDORS:
            assert term not in lowered, f"{term} in {name}"
        assert "http://" not in lowered and "https://" not in lowered, name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                continue
            assert not roots & network, f"{name} imports {roots & network}"


def test_no_v38_value_has_a_field_that_could_hold_a_secret() -> None:
    """An account is an identifier. No field anywhere could carry a key or a login."""

    secret = re.compile(r"password|passphrase|secret|token|api_key|credential|login")
    offenders = [
        f"{qualified}.{field_name}"
        for qualified, value in _public_classes()
        for field_name in getattr(value, "__dataclass_fields__", {})
        if secret.search(field_name)
    ]
    assert offenders == []


def test_a_broker_is_an_identifier_and_never_an_adapter() -> None:
    source = (PACKAGE / "allocation" / "capital.py").read_text()
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not any(
        module.startswith(("alphalab.broker", "alphalab.brokers")) for module in imported
    )
    for vendor in (
        "alpaca",
        "ibkr",
        "interactive brokers",
        "zerodha",
        "binance",
        "oanda",
        "schwab",
    ):
        assert vendor not in source.lower()


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"] == []


def test_the_public_surfaces_advertise_every_new_name() -> None:
    import alphalab.allocation as allocation
    import alphalab.analytics as analytics
    import alphalab.api as api
    import alphalab.factor_library as factor_library
    import alphalab.lifecycle as lifecycle
    import alphalab.portfolio as portfolio
    import alphalab.portfolio_optimizer as portfolio_optimizer

    expected: dict[types.ModuleType, tuple[str, ...]] = {
        analytics: (
            "CovarianceMatrix",
            "CorrelationMatrix",
            "FactorLoadings",
            "Classification",
            "euler_decomposition",
            "RiskBudget",
            "evaluate_risk_budget",
            "strategy_overlap",
            "factor_crowding",
            "capital_concentration",
        ),
        portfolio_optimizer: (
            "construct",
            "ConstructionProblem",
            "ConstraintSet",
            "RiskParity",
            "black_litterman",
        ),
        portfolio: ("MultiStrategyBook", "StrategySleeve", "value_book"),
        allocation: (
            "CapitalAllocationPlan",
            "allocate_capital",
            "capital_budget",
            "reserved_capital",
        ),
        factor_library: ("loadings_from_panels",),
        lifecycle: ("research_configuration_with_portfolio",),
        api: ("sector_classification", "currency_classification"),
    }
    for module, names in expected.items():
        for name in names:
            assert name in module.__all__, f"{module.__name__} does not advertise {name}"


def test_no_underscore_name_is_advertised_by_a_v38_module() -> None:
    import importlib

    for name in V38_MODULES:
        module = importlib.import_module(name.removesuffix(".py").replace("/", "."))
        assert not [entry for entry in getattr(module, "__all__", ()) if entry.startswith("_")], (
            name
        )
