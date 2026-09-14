"""One registry maps a strategy identity to the code that runs it.

ADR-0033 decision 5 named this gap and said where it must not be solved:

    Mapping that to a class is the caller's knowledge, and a registry of
    strategy classes **here** would be a plugin system this package has no
    business owning.

ADR-0035 keeps that -- :mod:`alphalab.lifecycle` still constructs nothing -- and
puts the registry beside :class:`~alphalab.strategy.protocol.StrategyProtocol`,
which is the thing being registered and which imports nothing above itself.

What the tests below establish, in order:

1. **One authority.** ``StrategyDefinition`` stays the only record of what a
   strategy *is*; the registry stores an identity and a factory and nothing else.
2. **Refusals.** A duplicate registration and an unknown identity are errors, not
   a silent replacement and not a ``None``.
3. **No name guessing.** Nothing here imports a module by name or derives a class
   from a string -- the failure mode ``alphalab.strategy.dispatcher`` already
   carries a scar from (ADR-0032 decision 1).
4. **The joins.** The lifecycle's ``RunPlan`` resolves through it without the
   lifecycle depending on the strategy runtime, and a restored run rebuilds its
   strategies from it deterministically.
"""

import ast
import inspect
import pathlib
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.exceptions import StrategyRuntimeError
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.registry import (
    DuplicateStrategyError,
    StrategyClassRegistry,
    StrategyDeclaration,
    StrategyRegistration,
    UnknownStrategyError,
    instances_for,
    runtime_for,
)
from alphalab.strategy.state import LifecycleState
from alphalab.studio.strategy import StrategyDefinition


class MomentumStrategy(BaseStrategy):
    """A real strategy: it emits an intent sized from its own parameters."""

    def __init__(self, strategy_id: str, parameters: Mapping[str, float]) -> None:
        self.strategy_id = strategy_id
        self.parameters = dict(parameters)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        size = Decimal(str(self.parameters.get("size", 0.0)))
        if size == 0:
            return ()
        return (Intent(self.strategy_id, event.quote.asset_id, size),)


class MeanReversionStrategy(MomentumStrategy):
    """A second registered strategy, so a registry holds more than one."""


def _definition(strategy_id: str = "momentum-1", **parameters: float) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=strategy_id,
        name="Momentum",
        version="1",
        author="quant",
        description="synthetic",
        parameters=parameters,
    )


def _registry() -> StrategyClassRegistry:
    return StrategyClassRegistry().register("momentum-1", MomentumStrategy)


def _imported_modules(module: Any) -> set[str]:
    """Every module ``module`` imports, read from its source rather than its text."""

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


# --------------------------------------------------------------------------- #
# 1. Registration, and the identity it is keyed by
# --------------------------------------------------------------------------- #


def test_a_registered_strategy_is_looked_up_by_the_identity_it_was_given() -> None:
    registry = _registry()

    assert "momentum-1" in registry
    assert len(registry) == 1
    assert registry.strategy_ids == ("momentum-1",)
    assert registry.require("momentum-1").factory is MomentumStrategy


def test_registration_records_where_the_code_came_from() -> None:
    """Provenance, so a run record can say which class ran."""

    registration = _registry().require("momentum-1")

    assert registration.qualified_name.endswith("MomentumStrategy")
    assert registration.qualified_name.startswith("tests.regression")


def test_the_registry_is_a_value() -> None:
    """Registering returns a new registry and mutates nothing."""

    before = _registry()
    after = before.register("mean-reversion-1", MeanReversionStrategy)

    assert len(before) == 1 and len(after) == 2
    assert "mean-reversion-1" not in before


def test_iteration_is_in_registration_order() -> None:
    registry = StrategyClassRegistry().register_all(
        [("c", MomentumStrategy), ("a", MomentumStrategy), ("b", MomentumStrategy)]
    )

    assert registry.strategy_ids == ("c", "a", "b")
    assert list(registry) == ["c", "a", "b"]


# --------------------------------------------------------------------------- #
# 2. Refusals
# --------------------------------------------------------------------------- #


def test_a_duplicate_registration_is_refused_rather_than_replacing() -> None:
    """Silently taking the second would make which code runs depend on import
    order -- the least debuggable non-determinism there is.
    """

    registry = _registry()

    with pytest.raises(DuplicateStrategyError) as caught:
        registry.register("momentum-1", MeanReversionStrategy)

    message = str(caught.value)
    assert "MomentumStrategy" in message, "names the incumbent"
    assert "MeanReversionStrategy" in message, "and the challenger"
    assert registry.require("momentum-1").factory is MomentumStrategy, "unchanged"


def test_an_unknown_strategy_is_refused_rather_than_returning_none() -> None:
    with pytest.raises(UnknownStrategyError, match="No strategy is registered as 'ghost'"):
        _registry().require("ghost")

    with pytest.raises(UnknownStrategyError):
        _registry().construct("ghost")


def test_the_refusal_lists_what_is_registered() -> None:
    """A typo and a missing registration look identical from the failure alone."""

    with pytest.raises(UnknownStrategyError, match="momentum-1"):
        _registry().construct("momentum-2")


def test_the_non_refusing_read_exists_for_a_caller_that_is_only_asking() -> None:
    assert _registry().registration_for("ghost") is None
    assert isinstance(_registry().registration_for("momentum-1"), StrategyRegistration)


def test_an_unnamed_strategy_cannot_be_registered() -> None:
    with pytest.raises(StrategyRuntimeError, match="names the identity"):
        StrategyClassRegistry().register("   ", MomentumStrategy)


def test_a_factory_that_returns_something_undispatchable_is_refused() -> None:
    """Refused at construction, not at its first market event.

    ``Dispatcher`` reports a hook failure as the *strategy's* failure, so a
    registration mistake that reached it would be recorded against the strategy.
    """

    class NotAStrategy:
        pass

    def build_broken(strategy_id: str, parameters: Mapping[str, float], /) -> Any:
        return NotAStrategy()

    registry = StrategyClassRegistry().register("broken", build_broken)

    with pytest.raises(StrategyRuntimeError) as caught:
        registry.construct("broken")

    message = str(caught.value)
    assert "does not satisfy StrategyProtocol" in message
    assert "on_quote" in message, "and says what is missing"


def test_a_factory_returning_none_is_refused_too() -> None:
    def build_nothing(strategy_id: str, parameters: Mapping[str, float], /) -> Any:
        return None

    registry = StrategyClassRegistry().register("null", build_nothing)

    with pytest.raises(StrategyRuntimeError, match="does not satisfy StrategyProtocol"):
        registry.construct("null")


# --------------------------------------------------------------------------- #
# 3. Construction, and the parameters it passes through
# --------------------------------------------------------------------------- #


def test_construction_passes_the_identity_and_the_parameters() -> None:
    strategy = _registry().construct("momentum-1", {"size": 5.0})

    assert isinstance(strategy, MomentumStrategy)
    assert strategy.strategy_id == "momentum-1"
    assert strategy.parameters == {"size": 5.0}


def test_a_runtime_identity_may_differ_from_the_registered_one() -> None:
    """One registered strategy, run twice under two parameter sets."""

    registry = _registry()
    fast = registry.construct("momentum-1", {"size": 1.0}, runtime_id="momentum-fast")
    slow = registry.construct("momentum-1", {"size": 9.0}, runtime_id="momentum-slow")

    assert isinstance(fast, MomentumStrategy) and isinstance(slow, MomentumStrategy)
    assert (fast.strategy_id, slow.strategy_id) == ("momentum-fast", "momentum-slow")
    assert fast.parameters != slow.parameters


def test_construction_is_deterministic() -> None:
    """The same registry and the same declaration produce the same instance."""

    declaration = _definition(size=3.0)
    first = _registry().construct_from(declaration)
    second = _registry().construct_from(declaration)

    assert type(first) is type(second)
    assert isinstance(first, MomentumStrategy) and isinstance(second, MomentumStrategy)
    assert first.parameters == second.parameters == {"size": 3.0}


def test_the_constructed_strategy_actually_runs() -> None:
    """Not a stub: the registry produces something the dispatcher can drive."""

    from alphalab.market.events import QuoteReceived
    from alphalab.market.quote import Quote
    from alphalab.strategy.context import NoMarket, NoOrders, NoPortfolio, NoRiskView
    from alphalab.strategy.dispatcher import Dispatcher
    from alphalab.strategy.state import StrategyState

    strategy = _registry().construct("momentum-1", {"size": 4.0})
    state = StrategyState("momentum-1", LifecycleState.RUNNING, strategy)
    context = StrategyContext(
        portfolio=NoPortfolio(),
        market=NoMarket(),
        clock=type("C", (), {"now": lambda self: 0.0})(),
        logger=type("L", (), {"info": lambda self, m: None, "error": lambda self, m: None})(),
        risk_view=NoRiskView(),
        config={},
        orders=NoOrders(),
    )
    event = QuoteReceived(
        "evt-1",
        1.0,
        Quote(
            asset_id="AAPL",
            timestamp=1.0,
            bid=Decimal("99"),
            ask=Decimal("101"),
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
            venue="SIM",
            currency="USD",
        ),
    )

    _, intents, _ = Dispatcher.dispatch_event(state, event, context, 1.0)

    assert [intent.target for intent in intents] == [Decimal("4.0")]


# --------------------------------------------------------------------------- #
# 4. One authority, and no name guessing
# --------------------------------------------------------------------------- #


def test_the_registry_is_not_a_second_strategy_definition() -> None:
    """It stores an identity and a factory. Nothing about what a strategy *is*."""

    fields = set(StrategyRegistration.__dataclass_fields__)

    assert fields == {"strategy_id", "factory", "qualified_name"}
    assert not fields & {"name", "version", "author", "description", "parameters", "metadata"}


def test_a_strategy_definition_satisfies_the_declaration_without_being_imported() -> None:
    """Structural, so ``alphalab.strategy`` acquires no dependency on studio.

    Read from the *imports* rather than from the text: the registry's docstring
    explains the relationship it deliberately does not have, and a substring
    search would flag that explanation as the offence.
    """

    assert isinstance(_definition(size=1.0), StrategyDeclaration)

    from alphalab.strategy import registry as registry_module

    assert "alphalab.studio" not in _imported_modules(registry_module)


def test_the_strategy_package_still_imports_nothing_above_itself() -> None:
    package = pathlib.Path(inspect.getfile(StrategyClassRegistry)).parent
    permitted = ("alphalab.common", "alphalab.strategy")

    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("alphalab.")
                and not node.module.startswith(permitted)
            ):
                offenders.append(f"{path.name}: {node.module}")

    assert not offenders, f"alphalab.strategy reached upward: {offenders}"


def test_nothing_resolves_a_strategy_by_importing_or_guessing_a_name() -> None:
    """A name is not a type. The registry never turns one into code.

    Checked against the *calls* it makes, for the reason above: the module says
    in words that it never calls ``importlib``, and that sentence is not a call.
    """

    from alphalab.strategy import registry as registry_module

    tree = ast.parse(inspect.getsource(registry_module))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    # ``vars`` is deliberately absent: the registry calls it on
    # ``StrategyProtocol``, a class it already holds, to list the hooks a
    # refusal names. That is introspecting a type, not resolving a string.
    forbidden = {"eval", "exec", "__import__", "import_module", "globals"}
    assert not (called & forbidden), f"the registry resolves a name: {called & forbidden}"
    assert "importlib" not in _imported_modules(registry_module)


def test_only_one_module_declares_a_strategy_class_registry() -> None:
    """A second would be the duplicate authority this release exists to avoid."""

    import importlib
    import pkgutil

    import alphalab

    holders = [
        info.name
        for info in pkgutil.walk_packages(alphalab.__path__, "alphalab.")
        if "StrategyClassRegistry" in getattr(importlib.import_module(info.name), "__all__", ())
    ]

    assert holders == ["alphalab.strategy", "alphalab.strategy.registry"], holders


# --------------------------------------------------------------------------- #
# 5. The runtime join
# --------------------------------------------------------------------------- #


def test_a_runtime_is_built_from_declarations_without_a_hand_written_loop() -> None:
    registry = _registry().register("mean-reversion-1", MeanReversionStrategy)
    declarations = (
        _definition("momentum-1", size=2.0),
        _definition("mean-reversion-1", size=-3.0),
    )

    state = runtime_for(registry, declarations)

    assert set(state.strategies) == {"momentum-1", "mean-reversion-1"}
    assert all(s.status is LifecycleState.CREATED for s in state.strategies.values())

    instance = state.strategies["momentum-1"].instance
    assert isinstance(instance, MomentumStrategy)
    assert instance.parameters == {"size": 2.0}


def test_runtime_construction_delegates_rather_than_reimplementing() -> None:
    """No second way to build a ``RuntimeState``."""

    source = inspect.getsource(runtime_for)

    assert "create_runtime" in source and "register_strategy" in source
    assert "RuntimeState(" not in source


def test_instances_for_produces_one_instance_per_declaration() -> None:
    registry = _registry().register("mean-reversion-1", MeanReversionStrategy)
    built = instances_for(
        registry, (_definition("momentum-1", size=1.0), _definition("mean-reversion-1"))
    )

    assert set(built) == {"momentum-1", "mean-reversion-1"}
    assert isinstance(built["mean-reversion-1"], MeanReversionStrategy)


def test_an_unregistered_declaration_stops_the_whole_set() -> None:
    """A half-built set of strategies is a run starting with part of its book."""

    with pytest.raises(UnknownStrategyError):
        instances_for(_registry(), (_definition("momentum-1"), _definition("ghost")))


# --------------------------------------------------------------------------- #
# 6. The lifecycle join
# --------------------------------------------------------------------------- #


def test_a_run_plan_resolves_through_the_registry() -> None:
    """The join ADR-0033 left to "the caller's knowledge", now a lookup."""

    from alphalab.lifecycle.execution import RunPlan

    plan = RunPlan(
        environment="live",
        version=replace(_strategy_version(), definition=_definition("momentum-1", size=7.0)),
        reference=_strategy_version().ref,
        evidence=None,
        deployed_by="releaser",
        deployed_at=1.0,
    )

    strategy = _registry().construct_from(plan.definition)

    assert isinstance(strategy, MomentumStrategy)
    assert strategy.parameters == {"size": 7.0}
    assert plan.strategy_id == "momentum-1"


def test_the_lifecycle_still_constructs_nothing() -> None:
    """ADR-0033 decision 5 is unchanged: the registry is not in the lifecycle."""

    from alphalab.lifecycle import execution

    source = inspect.getsource(execution)

    assert "StrategyClassRegistry" not in source.replace(
        ":class:`~alphalab.strategy.registry.StrategyDeclaration`", ""
    ).replace("``alphalab.strategy.registry``", "").replace(
        ":meth:`~alphalab.strategy.registry.StrategyClassRegistry.construct_from`", ""
    )
    for forbidden in ("RunEngine", "LiveSession", "ExecutionPipeline", "RunConfig("):
        assert forbidden not in source


def test_a_run_plans_definition_is_typed_as_itself() -> None:
    """It was ``object``, which cost the caller the only thing it is for."""

    from typing import get_type_hints

    from alphalab.lifecycle.execution import RunPlan

    descriptor = inspect.getattr_static(RunPlan, "definition")
    assert isinstance(descriptor, property)
    assert descriptor.fget is not None
    assert get_type_hints(descriptor.fget)["return"] is StrategyDefinition


def _strategy_version() -> Any:
    from alphalab.lifecycle.strategy_version import StrategyVersion
    from alphalab.model_registry.registry import ModelStage

    return StrategyVersion(
        name="momentum",
        version=1,
        definition=_definition(),
        stage=ModelStage.PRODUCTION,
        created_at=1.0,
    )


# --------------------------------------------------------------------------- #
# 7. Durable identity and deterministic restoration
# --------------------------------------------------------------------------- #


def test_the_registry_holds_live_objects_and_is_not_serializable() -> None:
    """ADR-0023's rule for every live object, applied unchanged."""

    from alphalab.persistence import serialize
    from alphalab.persistence.exceptions import SerializationError

    with pytest.raises(SerializationError):
        serialize(_registry())


def test_the_identity_is_what_is_durable() -> None:
    """A string survives a process; a factory does not. That is the whole design."""

    assert isinstance(_registry().require("momentum-1").strategy_id, str)
    assert isinstance(_registry().require("momentum-1").qualified_name, str)


def test_a_restore_rebuilds_the_same_strategy_from_the_same_registry() -> None:
    """What ``instances_for`` exists for: a deterministic supply at restore."""

    declarations = (_definition("momentum-1", size=6.0),)

    before = instances_for(_registry(), declarations)["momentum-1"]
    after = instances_for(_registry(), declarations)["momentum-1"]

    assert type(before) is type(after)
    assert isinstance(before, MomentumStrategy) and isinstance(after, MomentumStrategy)
    assert before.parameters == after.parameters
    # And it is the type a snapshot would have recorded for it.
    assert type(after).__name__ == "MomentumStrategy"


def test_the_protocol_is_runtime_checkable_so_the_refusal_can_exist() -> None:
    assert isinstance(_registry().construct("momentum-1"), StrategyProtocol)
