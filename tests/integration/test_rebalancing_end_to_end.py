"""Plan scenario S-01's construction: WS-12's end to end and M9's exit gate (ADR-0063).

``examples/83_rebalance_a_factor_neutral_book.py`` rebalances eight equities
monthly for ten years to the most expected return a value-neutral,
sector-capped book holds within a CVaR limit, run as an experiment of two
limits; its experiment is ``examples/_rebalanced_book.py``'s. Held here:

* **As an experiment.** Both trials are completed and counted, and the
  experiment's record is pinned: the same on every host and at every rerun.
* **Monthly.** A rebalance at the first slice of every month in New York, from
  July 2015 -- once the model holds 126 returns -- to December 2024: 114 in
  each trial.
* **Certified answers only.** Every rebalance is ``OPTIMAL`` and traded but
  one: the tight trial's program of 2024-05-01 cannot hold its limit, is
  proven infeasible, and trades nothing.
* **What was certified holds.** At the rebalances sampled, the construction is
  solved again and its weights are held to every constraint without the
  solver: the invested fraction, the caps, the sector caps, zero value
  exposure, and the CVaR by sorting the scenarios' losses. Each target is its
  weight in whole shares.
* **The limit is what separates the trials.** They trade alike until a
  rebalance where the tight trial's CVaR limit binds, at the limit.
* **Reconciled.** At every rebalance the strategy's book is its last certified
  targets, and the account holds exactly that book.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from alphalab.lifecycle.experiments import ExperimentRecord, LedgerCount, TrialLedger
from alphalab.portfolio_optimizer import (
    ConstructionProblem,
    ConstructionStatus,
    CVaRLimit,
    construct,
)
from alphalab.rebalancing import RebalanceRecord

ROOT = Path(__file__).resolve().parents[2]
#: Example 83's experiment, defined where its identity is stable.
DEFINITION = ROOT / "examples" / "_rebalanced_book.py"
NEW_YORK = ZoneInfo("America/New_York")
TOLERANCE = 1e-9

#: The experiment's record: both trials' records, outcomes and series.
PINNED_RECORD = "e5d2c17d2600bf5aa008db0e0b2cf09adfd6576d26b16849383014f7016bf952"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    """The experiment's module, loaded under the name the example imports it by.

    Its identity names its configuration type by module, so it is loaded as
    ``_rebalanced_book``, as ``python examples/83_...`` loads it.
    """

    name = "_rebalanced_book"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, DEFINITION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ran(example: ModuleType) -> tuple[ExperimentRecord, TrialLedger]:
    where = example.ledger()
    return example.run(where), where


def _trials(example: ModuleType) -> dict[float, Any]:
    """Each trial by its CVaR limit."""

    return {trial.configuration.cvar_limit: trial for trial in example.SPEC.trials()}


def _built(example: ModuleType, limit: float) -> tuple[Any, tuple[RebalanceRecord, ...]]:
    model, strategy = example.BUILT[_trials(example)[limit].trial_id]
    return model, strategy.records


def _day(record: RebalanceRecord) -> date:
    from datetime import datetime

    return datetime.fromtimestamp(record.instant, NEW_YORK).date()


def _sorted_cvar(
    scenarios: tuple[tuple[float, ...], ...], weights: list[float], level: float
) -> float:
    """The CVaR of the loss at ``level``: the worst ``(1 - level) S`` losses, averaged."""

    losses = sorted(
        (-math.fsum(r * w for r, w in zip(row, weights, strict=True)) for row in scenarios),
        reverse=True,
    )
    tail = (1.0 - level) * len(losses)
    whole = math.floor(tail)
    total = math.fsum(losses[:whole])
    if tail > whole:
        total += (tail - whole) * losses[whole]
    return total / tail


def _holds_every_constraint(
    example: ModuleType, problem: ConstructionProblem, weights: Mapping[str, float]
) -> float:
    """Every constraint of the model's problem, evaluated without the solver; the CVaR."""

    model = example.ValueNeutralMomentum(cvar_limit=0.0)
    assets = problem.universe
    held = [weights[asset] for asset in assets]
    assert math.fsum(held) == pytest.approx(model.invested, abs=TOLERANCE)
    assert all(-TOLERANCE <= w <= model.weight_cap + TOLERANCE for w in held)
    sectors: dict[str, float] = {}
    for asset, weight in zip(assets, held, strict=True):
        sector = example.DECLARED[example.SYMBOL[asset]][0]
        sectors[sector] = sectors.get(sector, 0.0) + weight
    assert all(total <= model.sector_cap + TOLERANCE for total in sectors.values())
    exposure = math.fsum(example.VALUE[a] * w for a, w in zip(assets, held, strict=True))
    assert abs(exposure) <= TOLERANCE
    limit = problem.constraints.limits[0]
    assert isinstance(limit, CVaRLimit)
    cvar = _sorted_cvar(limit.scenarios.returns, held, limit.level)
    assert cvar <= limit.limit + TOLERANCE
    return cvar


# --------------------------------------------------------------------------- #
# As an experiment
# --------------------------------------------------------------------------- #


def test_both_trials_are_completed_and_counted_and_the_record_is_pinned(
    ran: tuple[ExperimentRecord, TrialLedger],
) -> None:
    record, _ = ran
    assert record.counts == LedgerCount(
        completed=2, insolvent=0, strategy_failed=0, refused=0, failed=0
    )
    assert record.experiment_record_id == PINNED_RECORD


# --------------------------------------------------------------------------- #
# Monthly, certified answers only
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("limit", [0.012, 0.02])
def test_it_rebalances_at_the_first_slice_of_every_month(
    example: ModuleType, ran: tuple[ExperimentRecord, TrialLedger], limit: float
) -> None:
    _, records = _built(example, limit)
    days = [_day(record) for record in records]
    months = [(day.year, day.month) for day in days]
    expected = [(2015 + (6 + index) // 12, (6 + index) % 12 + 1) for index in range(114)]
    assert months == expected
    for day in days:
        # The first weekday of its month: the month's first slice.
        assert day.weekday() < 5
        assert all(date(day.year, day.month, d).weekday() >= 5 for d in range(1, day.day))


def test_every_rebalance_is_certified_but_the_one_that_cannot_hold_its_limit(
    example: ModuleType, ran: tuple[ExperimentRecord, TrialLedger]
) -> None:
    _, loose = _built(example, 0.02)
    assert all(r.status is ConstructionStatus.OPTIMAL and r.traded for r in loose)
    _, tight = _built(example, 0.012)
    refused = [r for r in tight if not r.traded]
    assert [(_day(r), r.status) for r in refused] == [
        (date(2024, 5, 1), ConstructionStatus.INFEASIBLE)
    ]
    assert refused[0].targets == {} and "cannot hold together" in refused[0].detail
    assert all(r.status is ConstructionStatus.OPTIMAL for r in tight if r.traded)


# --------------------------------------------------------------------------- #
# What was certified holds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("limit", "number"),
    [(0.012, 1), (0.012, 57), (0.012, 105), (0.012, 114), (0.02, 1), (0.02, 114)],
    ids=["tight-first", "tight-57", "tight-105", "tight-last", "loose-first", "loose-last"],
)
def test_what_was_certified_holds_every_constraint_evaluated_directly(
    example: ModuleType, ran: tuple[ExperimentRecord, TrialLedger], limit: float, number: int
) -> None:
    # The tight trial's 105th is the program whose interior point once jammed
    # (TD-76, tests/unit/portfolio_optimizer/test_captured_programs.py).
    model, records = _built(example, limit)
    view, problem, _ = model.seen[number - 1]
    record = records[number - 1]
    assert record.problem_id == problem.problem_id
    result = construct(problem)
    assert result.status is ConstructionStatus.OPTIMAL and result.weights is not None
    assert result.result_id == record.result_id
    _holds_every_constraint(example, problem, result.weights)
    # Each target is its weight in whole shares: within one share of it.
    for asset in problem.universe:
        share = view.prices[asset] / view.capital
        target = record.targets[asset] * view.prices[asset] / view.capital
        assert abs(float(target) - result.weights[asset]) <= float(share) + TOLERANCE


def test_the_limit_is_what_separates_the_trials(
    example: ModuleType, ran: tuple[ExperimentRecord, TrialLedger]
) -> None:
    tight_model, tight = _built(example, 0.012)
    _, loose = _built(example, 0.02)
    first = next(
        index
        for index, (a, b) in enumerate(zip(tight, loose, strict=True))
        if a.targets != b.targets
    )
    # Until then the tight trial's limit never bound, so the trials traded alike...
    assert all(a.targets == b.targets for a, b in zip(tight[:first], loose[:first], strict=True))
    # ...and where they part, it binds, at the limit.
    _, problem, _ = tight_model.seen[first]
    result = construct(problem)
    assert result.status is ConstructionStatus.OPTIMAL and result.weights is not None
    cvar = _holds_every_constraint(example, problem, result.weights)
    assert cvar == pytest.approx(0.012, abs=1e-8)
    assert any(name.startswith("CVaR at 0.9") for name in result.diagnostics.binding)


# --------------------------------------------------------------------------- #
# Reconciled
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("limit", [0.012, 0.02])
def test_at_every_rebalance_the_book_is_the_last_certified_targets_and_the_accounts(
    example: ModuleType, ran: tuple[ExperimentRecord, TrialLedger], limit: float
) -> None:
    model, records = _built(example, limit)
    assert len(model.seen) == len(records) == 114
    book: dict[str, Decimal] = {}
    for record, (view, _, account) in zip(records, model.seen, strict=True):
        assert dict(view.quantities) == book == account
        if record.traded:
            book = {asset: quantity for asset, quantity in record.targets.items() if quantity}
        for asset in view.assets:
            value = record.targets.get(asset, Decimal(0)) * view.prices[asset] / view.capital
            if record.traded:
                assert record.weights[asset] == pytest.approx(float(value), abs=1e-12)
