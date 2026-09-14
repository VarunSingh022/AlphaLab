"""The one registry that maps a strategy identity to the code that runs it.

ADR-0033 decision 5 left this gap open and said exactly where it must *not* go:

    It does not construct the strategy, deliberately: the lifecycle records a
    ``StrategyDefinition`` -- author metadata and parameter bounds -- and not
    code. Mapping that to a class is the caller's knowledge, and a registry of
    strategy classes **here** would be a plugin system this package has no
    business owning.

That is still true of :mod:`alphalab.lifecycle`, and this registry is not there.
It is here, beside :class:`~alphalab.strategy.protocol.StrategyProtocol` -- the
thing being registered -- in the package that owns what a strategy *is* and
imports nothing above itself. Every layer that needs the mapping (a backtest, a
live run, the lifecycle join, a restore) can reach it, and none of them has to
own it.

What it fixes
-------------

Without it, "the caller maps a definition to a class" is a sentence, not a
mechanism, and the mapping was made three ways in practice: a hand-written
``if`` ladder, a dictionary built at the call site, and -- the dangerous one --
guessing at a class from a name. A name is not a type; ``alphalab.strategy.dispatcher``
already carries the scar of that assumption (ADR-0032 decision 1). A registry
makes the mapping **declared**, **deterministic** and **refusable**: a duplicate
registration is an error rather than a silent replacement, and an unknown
identity is an error rather than ``None`` reaching a runtime that then fails
somewhere less informative.

What it deliberately is not
---------------------------

**Not a second strategy-definition authority.**
:class:`~alphalab.studio.strategy.StrategyDefinition` remains the one record of
what a strategy is, and this registry stores nothing from it: it maps an
identity to a factory and holds no parameters, no author, no description. The
identity it keys on is ``StrategyDefinition.strategy_id``, which is the identity
that already exists.

**Not an importer.** It never resolves a string to a module, never calls
``importlib``, and never derives a class name from a strategy name. A caller
registers the class it already has. That is what keeps "which code is this?"
answerable by reading the registration site rather than by reproducing an import
rule.

**Not serializable.** A factory is a live object, exactly as a strategy instance,
a sizing model and an instrument registry are, and ADR-0023's rule for all of
them applies unchanged: a snapshot records *what the object was* and a restore
requires the caller to supply it back. What *is* durable is the identity, which
is a string -- so a restored run can rebuild its strategies from a registry
deterministically, which is what :func:`instances_for` exists for.

The declaration it takes
------------------------

:class:`StrategyDeclaration` is a structural protocol, not an import.
``alphalab.strategy`` acquires no dependency on ``alphalab.studio``, and a
``StrategyDefinition`` satisfies the protocol as it stands -- it has a
``strategy_id`` and ``parameters`` and always has. Declaring the shape this
registry needs, rather than importing a class it does not own, is the same
reasoning ADR-0016 decision 3 applies to the market vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from alphalab.common.persistent_map import PersistentMap
from alphalab.strategy.exceptions import StrategyRuntimeError
from alphalab.strategy.protocol import StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState

__all__ = [
    "DuplicateStrategyError",
    "StrategyClassRegistry",
    "StrategyDeclaration",
    "StrategyFactory",
    "StrategyRegistration",
    "UnknownStrategyError",
    "instances_for",
    "runtime_for",
]


class DuplicateStrategyError(StrategyRuntimeError):
    """Raised when one identity is registered twice.

    Refused rather than replaced. A registry that silently took the second
    registration would make which code runs depend on import order, which is the
    least debuggable kind of non-determinism: the same configuration produces a
    different run and nothing records that it did.
    """


class UnknownStrategyError(StrategyRuntimeError):
    """Raised when an identity nothing registered is asked for.

    Refused rather than returning ``None``. A ``None`` a caller forgets to check
    reaches the runtime as a missing strategy and fails later, somewhere that
    cannot say the registration was the problem.
    """


@runtime_checkable
class StrategyDeclaration(Protocol):
    """What this registry needs in order to construct a strategy.

    A structural protocol, so :class:`~alphalab.studio.strategy.StrategyDefinition`
    satisfies it without this package importing it. See the module docstring.
    """

    @property
    def strategy_id(self) -> str:
        """The identity the registry is keyed by."""
        ...

    @property
    def parameters(self) -> Mapping[str, float]:
        """The parameter set this strategy was declared with."""
        ...


class StrategyFactory(Protocol):
    """How a registered strategy is built.

    Takes the runtime ``strategy_id`` the instance will be registered under and
    the parameters it was declared with, and returns something satisfying
    :class:`~alphalab.strategy.protocol.StrategyProtocol`.

    Both arguments are passed positionally and both are always passed: a factory
    that ignores its parameters is a decision its author can make, and one that
    never receives them cannot.
    """

    def __call__(
        self, strategy_id: str, parameters: Mapping[str, float], /
    ) -> StrategyProtocol: ...


@dataclass(frozen=True, slots=True)
class StrategyRegistration:
    """One registered strategy, and where its code came from.

    Attributes:
        strategy_id: The identity this registration answers to.
        factory: What builds an instance.
        qualified_name: ``module.QualName`` of the factory, recorded for
            provenance. **Never used to look anything up** -- resolving a string
            back to code is the import rule this registry exists to replace. It
            is what a run record, a log line or a reconciliation reads to say
            which code ran.
    """

    strategy_id: str
    factory: StrategyFactory
    qualified_name: str


#: The members ``StrategyProtocol`` declares, read from the protocol itself so
#: the two cannot drift. Used only to say *which* are missing in a refusal --
#: the refusal itself is ``isinstance`` against the protocol.
_STRATEGY_HOOKS: tuple[str, ...] = tuple(
    sorted(name for name in vars(StrategyProtocol) if name.startswith("on_"))
)


def _qualified_name(factory: StrategyFactory) -> str:
    module = getattr(factory, "__module__", "")
    name = getattr(factory, "__qualname__", None) or type(factory).__qualname__
    return f"{module}.{name}" if module else name


@dataclass(frozen=True, slots=True)
class StrategyClassRegistry:
    """An immutable registry of strategy identities and the code behind them.

    A value like every other state here: :meth:`register` returns a new registry
    and mutates nothing. Iteration is in registration order, so two runs that
    register the same strategies in the same order produce identical registries
    and a deterministic listing.
    """

    registrations: PersistentMap[str, StrategyRegistration] = field(default_factory=PersistentMap)

    # -- Registration ------------------------------------------------------- #

    def register(self, strategy_id: str, factory: StrategyFactory) -> StrategyClassRegistry:
        """A registry that also maps ``strategy_id`` to ``factory``.

        Raises:
            StrategyRuntimeError: If ``strategy_id`` is blank. An unnamed
                strategy cannot be looked up, and registering one would put an
                entry in the registry that nothing could ever reach.
            DuplicateStrategyError: If ``strategy_id`` is already registered --
                naming both the code that holds it and the code that tried to
                take it, because "duplicate registration" alone does not say
                which two.
        """

        if not strategy_id.strip():
            raise StrategyRuntimeError(
                "A strategy registration names the identity it answers to. An "
                "unnamed one could never be looked up."
            )

        held = self.registrations.get(strategy_id)
        if held is not None:
            raise DuplicateStrategyError(
                f"Strategy {strategy_id!r} is already registered to "
                f"{held.qualified_name}, and {_qualified_name(factory)} tried to take "
                "it. Which one runs is not a question this registry will answer by "
                "import order -- give them distinct identities, or build a registry "
                "that holds one of them."
            )

        registration = StrategyRegistration(
            strategy_id=strategy_id,
            factory=factory,
            qualified_name=_qualified_name(factory),
        )
        return replace(self, registrations=self.registrations.set(strategy_id, registration))

    def register_all(self, entries: Iterable[tuple[str, StrategyFactory]]) -> StrategyClassRegistry:
        """A registry with every ``(identity, factory)`` pair added, in order."""

        registry = self
        for strategy_id, factory in entries:
            registry = registry.register(strategy_id, factory)
        return registry

    # -- Lookup ------------------------------------------------------------- #

    def registration_for(self, strategy_id: str) -> StrategyRegistration | None:
        """What is registered for ``strategy_id``, or ``None``.

        The non-refusing read, for a caller that genuinely wants to ask whether
        something is registered. :meth:`construct` is the one that refuses,
        because it is about to need an answer.
        """

        return self.registrations.get(strategy_id)

    def require(self, strategy_id: str) -> StrategyRegistration:
        """What is registered for ``strategy_id``.

        Raises:
            UnknownStrategyError: If nothing is. The message lists what *is*
                registered, because a typo and a missing registration look
                identical from the failure alone.
        """

        registration = self.registrations.get(strategy_id)
        if registration is None:
            known = ", ".join(repr(each) for each in self.strategy_ids) or "nothing"
            raise UnknownStrategyError(
                f"No strategy is registered as {strategy_id!r}. This registry holds "
                f"{known}. AlphaLab does not resolve a strategy identity to a class "
                "by importing it or by matching a name -- a name is not a type -- so "
                "register the class before the run that needs it."
            )
        return registration

    def __contains__(self, strategy_id: object) -> bool:
        return strategy_id in self.registrations

    def __len__(self) -> int:
        return len(self.registrations)

    def __iter__(self) -> Iterator[str]:
        return iter(self.registrations)

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        """Every registered identity, in registration order."""

        return tuple(self.registrations)

    # -- Construction ------------------------------------------------------- #

    def construct(
        self,
        strategy_id: str,
        parameters: Mapping[str, float] = MappingProxyType({}),
        *,
        runtime_id: str | None = None,
    ) -> StrategyProtocol:
        """Build the strategy registered as ``strategy_id``.

        ``runtime_id`` is the identity the *instance* will run under, which
        defaults to ``strategy_id`` and differs only when one registered
        strategy is run several times in one runtime -- two parameter sets of
        one model, say. The registry is keyed by the first and the runtime by
        the second, and conflating them would make the second impossible.

        Raises:
            UnknownStrategyError: If nothing is registered for ``strategy_id``.
            StrategyRuntimeError: If the factory returns something that is not a
                :class:`~alphalab.strategy.protocol.StrategyProtocol`. Checked
                rather than trusted: a factory that returns a stub, a ``None`` or
                a half-built object would otherwise fail inside
                :class:`~alphalab.strategy.dispatcher.Dispatcher`, which reports
                a failure as the *strategy's* -- blaming the strategy for a
                registration mistake.
        """

        registration = self.require(strategy_id)
        instance = registration.factory(
            runtime_id if runtime_id is not None else strategy_id, parameters
        )

        if not isinstance(instance, StrategyProtocol):
            missing = sorted(hook for hook in _STRATEGY_HOOKS if not hasattr(instance, hook))
            raise StrategyRuntimeError(
                f"{registration.qualified_name} was registered as {strategy_id!r} and "
                f"returned {type(instance).__name__}, which does not satisfy "
                f"StrategyProtocol: it supplies no {missing}. A strategy that cannot "
                "be dispatched is refused here rather than at its first market event, "
                "where the failure would be recorded against the strategy."
            )
        return instance

    def construct_from(
        self, declaration: StrategyDeclaration, *, runtime_id: str | None = None
    ) -> StrategyProtocol:
        """Build the strategy a declaration names, with the parameters it names.

        The join a lifecycle caller makes: a
        :class:`~alphalab.studio.strategy.StrategyDefinition` -- which
        :attr:`~alphalab.lifecycle.execution.RunPlan.definition` hands back --
        satisfies :class:`StrategyDeclaration` as it stands.
        """

        return self.construct(
            declaration.strategy_id, declaration.parameters, runtime_id=runtime_id
        )


def instances_for(
    registry: StrategyClassRegistry, declarations: Iterable[StrategyDeclaration]
) -> dict[str, StrategyProtocol]:
    """Construct every declared strategy, keyed by its identity.

    What a restore needs: :func:`alphalab.runtime.snapshot.restore` requires the
    caller to supply a strategy instance per captured identity and checks the
    type it is given against what the payload recorded. Building them from a
    registry makes that supply deterministic -- the same registry and the same
    declarations produce the same instances -- rather than hand-assembled at
    every restore site.

    Raises:
        UnknownStrategyError: If any declaration names an unregistered strategy.
            Nothing is constructed when one fails: a half-built set of
            strategies is a run that would start with some of its book missing.
    """

    built: dict[str, StrategyProtocol] = {}
    for declaration in declarations:
        built[declaration.strategy_id] = registry.construct_from(declaration)
    return built


def runtime_for(
    registry: StrategyClassRegistry, declarations: Iterable[StrategyDeclaration]
) -> RuntimeState:
    """A :class:`~alphalab.strategy.state.RuntimeState` holding every declared strategy.

    Delegates to :func:`~alphalab.strategy.runtime.create_runtime` and
    :func:`~alphalab.strategy.runtime.register_strategy`, which remain the
    runtime's own entry points -- this creates no second way to build one, it
    only removes the hand-written loop from every caller.

    Every strategy is therefore left in
    :attr:`~alphalab.strategy.state.LifecycleState.CREATED`, exactly as
    ``register_strategy`` leaves one, and a caller drives it to ``RUNNING``
    through :class:`~alphalab.strategy.supervisor.RuntimeSupervisor`.
    Transitioning here would put a second lifecycle authority in a registry --
    and it would skip ``configure`` and ``subscribe``, which are decisions the
    caller makes and this function has no input for.
    """

    state = create_runtime()
    for strategy_id, instance in instances_for(registry, declarations).items():
        state = register_strategy(state, strategy_id, instance)
    return state
