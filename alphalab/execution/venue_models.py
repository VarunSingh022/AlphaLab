"""One execution model per declared venue, and how a simulated order is routed among them.

Until v4.3 a run held one :class:`~alphalab.execution.simulator.ExecutionSimulator`:
one cost model and one latency model priced every simulated order, so a
strategy trading an instrument listed on two venues could be measured on
neither, nor on a routing between them (FUT-045). ADR-0061 declares a model per
venue: :class:`VenueExecution` holds one venue's simulator -- its spread,
slippage, impact, commission, fees, tax and latency -- and
``ExecutionPipelineConfig.venues`` maps each venue's code to one.

Each simulated order is routed to one declared venue by the first of: the venue
its :class:`~alphalab.common.order_terms.OrderTerms` names; the run's
:class:`VenueRouter`; the instrument's listing venue, when it is declared. An
order none of them routes is refused, never filled by a model nobody chose.
The routed venue's simulator then prices and stamps its fills.

A router is a deterministic function of the venues it may choose among and what
the order would cost at each, priced by that venue's own cost model at the
deciding event: :class:`CheapestVenue` is the first. Live best execution is out
of scope (BDY-026): a router here chooses among declared models, and nothing
more.

:func:`alphalab.crypto.venue.crypto_venue_execution` builds a venue's model
from a crypto venue's specification: the execution package knows nothing of
any market's conventions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.simulator import ExecutionSimulator

__all__ = [
    "CheapestVenue",
    "VenueExecution",
    "VenueRouter",
]


@dataclass(frozen=True, slots=True)
class VenueExecution:
    """One venue's execution model: the simulator its fills are priced and stamped by (ADR-0061).

    Attributes:
        simulator: The venue's costs and latency.

    Raises:
        ExecutionValidationError: If ``simulator`` is not an
            :class:`~alphalab.execution.simulator.ExecutionSimulator`.
    """

    simulator: ExecutionSimulator

    def __post_init__(self) -> None:
        if not isinstance(self.simulator, ExecutionSimulator):
            raise ExecutionValidationError(
                f"A venue's model is an ExecutionSimulator, got {type(self.simulator).__name__}."
            )


class VenueRouter(Protocol):
    """Chooses the venue an order is routed to, among the declared ones (ADR-0061).

    Deterministic: the same venues and costs choose the same venue, in every
    process.
    """

    def choose(self, venues: Sequence[str], cost_at: Callable[[str], Decimal]) -> str:
        """The code among ``venues`` to route to; ``cost_at`` prices the order at one."""
        ...


@dataclass(frozen=True, slots=True)
class CheapestVenue:
    """Routes each order to the venue whose cost model prices it lowest (ADR-0061).

    Each venue's own cost model prices the order -- spread, slippage, impact,
    commission, fees and tax, for its whole quantity -- at the deciding event's
    price. A tie goes to the first venue by code.
    """

    def choose(self, venues: Sequence[str], cost_at: Callable[[str], Decimal]) -> str:
        if not venues:
            raise ExecutionValidationError("A router chooses among declared venues, and none is.")
        best: tuple[Decimal, str] | None = None
        for code in sorted(venues):
            cost = cost_at(code)
            if best is None or cost < best[0]:
                best = (cost, code)
        assert best is not None
        return best[1]
