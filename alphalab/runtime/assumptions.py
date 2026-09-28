"""What a run assumed about execution, stated beside its results (ledger EXE-002).

A simulated result is only as honest as its execution model, and until v3.10 a
default backtest was the most optimistic one there is and said nothing about it:
every order filled at the price that decided it, in full, with no cost of any
kind. :class:`ExecutionAssumptions` is the statement a result carries instead --
derived from the run's configuration, so it cannot disagree with what ran -- and
:attr:`ExecutionAssumptions.optimistic` names each optimistic assumption in force.

Nothing here changes how a run executes. The defaults are what they were; what
changed is that a result produced under them says so, and a strategy fingerprint
can carry the assumptions as research settings (:meth:`ExecutionAssumptions.settings`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from alphalab.execution.policy import FillPolicy, FillTiming, ImmediateFill

if TYPE_CHECKING:
    from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig, ExecutionRouting

__all__ = ["ExecutionAssumptions", "describe", "execution_assumptions"]


def describe(value: Any) -> str:
    """A deterministic description of a configuration object.

    A dataclass is written as its name and fields, recursively; a ``Decimal``
    as its exact text, an enum member by name, a primitive by ``repr``, and
    anything else by its qualified type name -- never by the default object
    ``repr``, which carries a memory address and so differs in every process.
    """

    if is_dataclass(value) and not isinstance(value, type):
        body = ", ".join(f"{f.name}={describe(getattr(value, f.name))}" for f in fields(value))
        return f"{type(value).__qualname__}({body})"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if value is None or isinstance(value, bool | int | float | str):
        return repr(value)
    if isinstance(value, tuple | list):
        return "(" + ", ".join(describe(item) for item in value) + ")"
    # A plain configuration class -- the commission and slippage models are
    # slotted classes, not dataclasses -- is written by its stored parameters,
    # so ``PercentageCommission(rate=0.001)`` and ``(rate=0.002)`` differ.
    stored = _stored(value)
    if stored:
        body = ", ".join(f"{name.lstrip('_')}={describe(item)}" for name, item in stored)
        return f"{type(value).__qualname__}({body})"
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _stored(value: Any) -> list[tuple[str, Any]]:
    """The attributes an object stores, by slot or in its ``__dict__``, in a fixed order."""

    names: list[str] = []
    for cls in reversed(type(value).__mro__):
        slots = cls.__dict__.get("__slots__", ())
        names.extend([slots] if isinstance(slots, str) else list(slots))
    stored = [(name, getattr(value, name)) for name in names if hasattr(value, name)]
    if hasattr(value, "__dict__"):
        stored.extend(sorted(vars(value).items()))
    return stored


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    """How a run modelled execution.

    Attributes:
        routing: Where accepted orders executed. Under ``EXTERNAL`` routing
            nothing was simulated, and the fields below describe a model no
            order went through.
        fill_timing: When a simulated order filled.
        fill_policy: The fill policy, described.
        costs: The cost model every simulated fill was priced by, described.
        latency: The latency model, described.
        frictionless: Whether every cost role charged nothing by construction
            (:attr:`~alphalab.execution.simulator.ExecutionSimulator.is_frictionless`).
        unlimited_liquidity: Whether every order filled in full
            (:class:`~alphalab.execution.policy.ImmediateFill`).
    """

    routing: ExecutionRouting
    fill_timing: FillTiming
    fill_policy: str
    costs: str
    latency: str
    frictionless: bool
    unlimited_liquidity: bool

    @property
    def simulated(self) -> bool:
        """Whether a simulator executed the run's orders."""

        return self.routing.name == "SIMULATED"

    @property
    def optimistic(self) -> tuple[str, ...]:
        """Each optimistic assumption in force, in a sentence; empty if none."""

        if not self.simulated:
            return ()
        found: list[str] = []
        if self.fill_timing is FillTiming.SAME_EVENT:
            found.append(
                "orders fill at the price of the event that decided them (FillTiming.SAME_EVENT)"
            )
        if self.frictionless:
            found.append("execution costs nothing: no spread, slippage, impact, fees or tax")
        if self.unlimited_liquidity:
            found.append("liquidity is unlimited: every order fills in full (ImmediateFill)")
        return tuple(found)

    def settings(self) -> Mapping[str, str]:
        """The assumptions as research settings, keyed ``execution.*``.

        For :func:`~alphalab.lifecycle.fingerprint.research_configuration`: a
        strategy fingerprint that carries them identifies the execution model
        its evidence was measured under, not only the code.
        """

        return MappingProxyType(
            {
                "execution.routing": self.routing.name,
                "execution.fill_timing": self.fill_timing.value,
                "execution.fill_policy": self.fill_policy,
                "execution.costs": self.costs,
                "execution.latency": self.latency,
            }
        )


def execution_assumptions(
    pipeline: ExecutionPipelineConfig, fill_policy: FillPolicy
) -> ExecutionAssumptions:
    """What a pipeline configured this way, with this fill policy, assumes."""

    simulator = pipeline.simulator
    return ExecutionAssumptions(
        routing=pipeline.routing,
        fill_timing=pipeline.fill_timing,
        fill_policy=describe(fill_policy),
        costs=describe(simulator.costs),
        latency=describe(simulator.latency_model),
        frictionless=simulator.is_frictionless,
        unlimited_liquidity=isinstance(fill_policy, ImmediateFill),
    )
