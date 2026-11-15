"""The one registry a name selects code from (v4.3, ADR-0072 section 6, plan D-54-11).

The trial ledger's executors and a research cluster's cycle look names up in
the same :class:`~alphalab.common.registry.RunnerRegistry`. A name selects one
of its entries and nothing else: it is never resolved to code, so naming a
module imports nothing (ledger BDY-028, GAP-05's acceptance: an unregistered
name is refused before any import).
"""

from __future__ import annotations

import sys

import pytest

import alphalab.common
import alphalab.lifecycle
from alphalab.common.exceptions import AlphaLabRegistryError
from alphalab.common.registry import RunnerRegistry

NEVER_IMPORTED = "tests.unit.common._never_imported"


def _double(x: int) -> int:
    return 2 * x


def _square(x: int) -> int:
    return x * x


def test_a_name_is_looked_up_never_resolved_and_naming_a_module_imports_nothing() -> None:
    registry = RunnerRegistry().register("double", _double)

    with pytest.raises(AlphaLabRegistryError, match="never resolved to code") as refused:
        registry.runner(f"{NEVER_IMPORTED}.anything")

    assert NEVER_IMPORTED not in sys.modules
    assert "the registry holds double" in str(refused.value)


def test_registering_returns_a_new_registry_and_keeps_registration_order() -> None:
    empty = RunnerRegistry()
    one = empty.register("square", _square)
    two = one.register("double", _double)

    assert dict(empty.runners) == {}
    assert dict(one.runners) == {"square": _square}
    assert list(two.runners) == ["square", "double"]
    assert two.runner("double") is _double
    assert two.runner("double")(21) == 42
    with pytest.raises(TypeError):
        two.runners["triple"] = _double  # type: ignore[index]


@pytest.mark.parametrize(
    ("name", "runner", "match"),
    [
        ("", _double, "must be stated"),
        ("   ", _double, "must be stated"),
        (7, _double, "must be stated"),
        ("double", _double, "already holds a runner named 'double'"),
        ("triple", 3, "must be callable"),
    ],
)
def test_a_registration_names_callable_code_once(name: object, runner: object, match: str) -> None:
    registry = RunnerRegistry().register("double", _double)

    with pytest.raises(AlphaLabRegistryError, match=match):
        registry.register(name, runner)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", [None, 7, "", "double "])
def test_only_a_registered_name_finds_anything(name: object) -> None:
    registry = RunnerRegistry().register("double", _double)

    with pytest.raises(AlphaLabRegistryError, match="No runner is registered"):
        registry.runner(name)  # type: ignore[arg-type]


def test_the_ledger_and_the_cluster_take_the_same_registry() -> None:
    """D-54-11: one registry, exported where each of its takers is reached from."""

    import inspect

    from alphalab.cloud_research import run_cluster_cycle
    from alphalab.lifecycle.experiments import run_experiment

    assert alphalab.lifecycle.RunnerRegistry is RunnerRegistry
    assert alphalab.common.RunnerRegistry is RunnerRegistry
    for taker in (run_experiment, run_cluster_cycle):
        annotation = inspect.signature(taker, eval_str=True).parameters["registry"].annotation
        assert RunnerRegistry in (annotation, *getattr(annotation, "__args__", ())), taker
