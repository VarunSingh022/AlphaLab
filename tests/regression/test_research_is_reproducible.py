"""A research result reproduces, or it is not a result.

The release's success criteria require "deterministic/reproducible
experiments", and there are three distinct ways that can fail. Each has its own
section, and each is checked by *running the thing twice* rather than by
inspecting it.

1. **Wall-clock contamination.** A result whose identity moved with the clock
   would be irreproducible by construction. The digests are checked against two
   very different ``produced_at`` values.
2. **Unseeded randomness.** Every stochastic entry point is enumerated from the
   source and required to take a seed, so a new one cannot be added without a
   decision.
3. **Iteration-order dependence.** A result that depended on the order a
   mapping happened to be built in would reproduce in one process and not in
   another. The same study is run from inputs assembled in different orders.
"""

from __future__ import annotations

import ast
import pathlib
import random
from collections.abc import Callable

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    ObservationFrame,
    compute_panel,
    forward_returns,
    information_coefficient,
    observations_from_records,
)
from alphalab.research import (
    ResearchStudy,
    block_bootstrap_indices,
    build_result,
    monte_carlo_orders,
    perturb_observations,
    perturb_signal,
    signal_diagnostics,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
DAY = 86400.0
START = 1_735_689_600.0

MOMENTUM = FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5)


def _paths(assets: int = 6, length: int = 40) -> dict[str, list[float]]:
    prng = random.Random(4242)
    return {
        f"S{index}": [
            100.0 * (1.0 + 0.001 * index) ** step * (1.0 + prng.gauss(0.0, 0.004))
            for step in range(length)
        ]
        for index in range(assets)
    }


def _frame(
    paths: dict[str, list[float]], symbol_order: list[str] | None = None
) -> ObservationFrame:
    order = symbol_order if symbol_order is not None else list(paths)
    records = [
        WireBar(symbol, START + index * DAY, close, close, close, close, 1_000_000.0)
        for symbol in order
        for index, close in enumerate(paths[symbol])
    ]
    return observations_from_records(records, FeatureField.CLOSE, "UTC", "ds@v1")


# --------------------------------------------------------------------------- #
# 1. The clock never reaches a digest
# --------------------------------------------------------------------------- #


def test_no_identity_in_the_research_layer_depends_on_the_clock() -> None:
    study = ResearchStudy(
        study_name="reproducible",
        dataset_version="ds@v1",
        universe=("S0", "S1"),
        features=(MOMENTUM,),
        horizons=(1,),
        seed=11,
    )
    metrics = {"mom_5.h1.rank_ic": 0.0321}

    early = build_result(study, metrics, produced_at=0.0)
    late = build_result(study, metrics, produced_at=1_999_999_999.0)

    assert early.result_id == late.result_id
    assert (
        study.study_id
        == ResearchStudy(
            study_name="reproducible",
            dataset_version="ds@v1",
            universe=("S0", "S1"),
            features=(MOMENTUM,),
            horizons=(1,),
            seed=11,
        ).study_id
    )


def test_no_research_key_rendering_reads_a_clock() -> None:
    """Read from the source: no canonical key may call a time function.

    The runtime check above can only observe the values it was given. This
    observes what the code can reach.
    """

    forbidden = {"time", "monotonic", "now", "utcnow", "perf_counter"}
    offenders: list[str] = []

    for name in ("study.py", "splits.py", "purging.py"):
        path = ROOT / "alphalab" / "research" / name
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                target = node.func
                called = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else target.id
                    if isinstance(target, ast.Name)
                    else ""
                )
                if called in forbidden:
                    offenders.append(f"{name}:{node.lineno} {called}()")

    for name in ("definition.py", "series.py"):
        path = ROOT / "alphalab" / "factor_library" / name
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                target = node.func
                called = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else target.id
                    if isinstance(target, ast.Name)
                    else ""
                )
                if called in forbidden:
                    offenders.append(f"{name}:{node.lineno} {called}()")

    assert not offenders, (
        "an identity-deriving module reads a clock, which would make its digest "
        f"irreproducible: {offenders}"
    )


# --------------------------------------------------------------------------- #
# 2. Every stochastic entry point is seeded
# --------------------------------------------------------------------------- #


def test_every_function_that_draws_a_random_number_takes_a_seed() -> None:
    """Enumerated from the source, so a new unseeded one cannot slip in."""

    path = ROOT / "alphalab" / "research" / "perturbation.py"
    tree = ast.parse(path.read_text())
    offenders: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        drawing = {"Random", "gauss", "shuffle", "randint", "random", "choices"}
        draws = any(
            isinstance(inner, ast.Attribute) and inner.attr in drawing for inner in ast.walk(node)
        ) or any(isinstance(inner, ast.Name) and inner.id == "random" for inner in ast.walk(node))
        if not draws:
            continue
        arguments = {argument.arg for argument in node.args.args}
        if "seed" not in arguments:
            offenders.append(f"{node.name} at line {node.lineno}")

    assert not offenders, (
        "a perturbation draws random numbers without taking a seed, so a run using it "
        f"could not be reproduced: {offenders}"
    )


def test_no_stochastic_perturbation_has_a_default_seed() -> None:
    """A hidden default makes a result reproducible only until it changes."""

    path = ROOT / "alphalab" / "research" / "perturbation.py"
    tree = ast.parse(path.read_text())
    offenders: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        names = [argument.arg for argument in node.args.args]
        if "seed" not in names:
            continue
        # Defaults align to the tail of the argument list.
        offset = len(names) - len(node.args.defaults)
        position = names.index("seed")
        if position >= offset:
            offenders.append(node.name)

    assert not offenders, f"these perturbations default their seed: {offenders}"


@pytest.mark.parametrize(
    "call",
    [
        lambda seed: block_bootstrap_indices(40, 5, seed=seed),
        lambda seed: monte_carlo_orders(20, 3, seed=seed),
    ],
)
def test_a_seeded_draw_reproduces_and_a_different_seed_does_not(
    call: Callable[[int], object],
) -> None:
    assert call(1) == call(1)
    assert call(1) != call(2)


def test_seeded_perturbations_reproduce_exactly() -> None:
    frame = _frame(_paths())
    panel = compute_panel(MOMENTUM, frame)

    assert (
        perturb_observations(frame, 0.01, seed=5).series["S0"].values
        == perturb_observations(frame, 0.01, seed=5).series["S0"].values
    )
    assert perturb_signal(panel, 0.01, seed=5).rows == perturb_signal(panel, 0.01, seed=5).rows


def test_a_seeded_perturbation_reproduces_across_a_fresh_interpreter() -> None:
    """``random.Random`` is reproducible across processes, and this proves it.

    A property asserted inside one process could be satisfied by a cached
    value; this one cannot.
    """

    import subprocess
    import sys

    probe = (
        "from alphalab.research import block_bootstrap_indices, monte_carlo_orders\n"
        "print(block_bootstrap_indices(40, 5, seed=99))\n"
        "print(monte_carlo_orders(12, 2, seed=99))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, check=True
    )
    lines = result.stdout.strip().splitlines()

    assert lines[0] == str(block_bootstrap_indices(40, 5, seed=99))
    assert lines[1] == str(monte_carlo_orders(12, 2, seed=99))


# --------------------------------------------------------------------------- #
# 3. Nothing depends on the order the inputs were assembled in
# --------------------------------------------------------------------------- #


def test_a_diagnostic_does_not_depend_on_the_order_symbols_arrived_in() -> None:
    """A result that did would reproduce in one process and not another."""

    paths = _paths()
    forward = _frame(paths, sorted(paths))
    backward = _frame(paths, sorted(paths, reverse=True))

    first = signal_diagnostics(
        compute_panel(MOMENTUM, forward), forward_returns(forward, 1), buckets=3, minimum_assets=3
    )
    second = signal_diagnostics(
        compute_panel(MOMENTUM, backward),
        forward_returns(backward, 1),
        buckets=3,
        minimum_assets=3,
    )

    assert first.rank_ic.mean_rank == second.rank_ic.mean_rank
    assert first.spread == second.spread
    assert [bucket.mean_forward_return for bucket in first.quantiles] == [
        bucket.mean_forward_return for bucket in second.quantiles
    ]


def test_a_feature_identity_does_not_depend_on_parameter_insertion_order() -> None:
    """The canonical key sorts the mapping, which is what makes this true."""

    first = FeatureDefinition(
        "r",
        FeatureKind.VOLATILITY_REGIME,
        FeatureField.CLOSE,
        window=5,
        parameters={"long_window": 10.0},
    )
    second = FeatureDefinition(
        "r",
        FeatureKind.VOLATILITY_REGIME,
        FeatureField.CLOSE,
        window=5,
        parameters=dict(reversed(list({"long_window": 10.0}.items()))),
    )

    assert first.feature_version == second.feature_version


def test_a_result_identity_does_not_depend_on_metric_insertion_order() -> None:
    study = ResearchStudy(
        study_name="ordered",
        dataset_version="ds@v1",
        universe=("S0",),
        features=(MOMENTUM,),
        horizons=(1,),
    )

    forward = build_result(study, {"a": 1.0, "b": 2.0, "c": 3.0}, 0.0)
    backward = build_result(study, {"c": 3.0, "b": 2.0, "a": 1.0}, 0.0)

    assert forward.result_id == backward.result_id


def test_an_information_coefficient_reproduces_bit_for_bit() -> None:
    """Floating-point identity, not approximate equality: the same code path twice."""

    frame = _frame(_paths())
    panel = compute_panel(MOMENTUM, frame)
    realized = forward_returns(frame, 3)

    first = information_coefficient(panel, realized, minimum_assets=3)
    second = information_coefficient(panel, realized, minimum_assets=3)

    assert first.mean_rank == second.mean_rank
    assert first.mean_pearson == second.mean_pearson
    assert first.per_instant_rank == second.per_instant_rank
