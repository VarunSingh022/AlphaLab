"""Immutable interface protocol for Research data ingestion.

``parameters`` and the frozen dataclass
---------------------------------------

Until v2.17 :attr:`ResearchPayload.parameters` was annotated ``dict[str, float]``
-- the only mutable field *type* on any frozen dataclass in the package, which is
what ADR-0032 recorded as category C finding 2. ``frozen=True`` stops
``payload.parameters = {...}``; it does nothing about
``payload.parameters["ma"] = 50``, so a value that advertised itself as immutable
could be edited in place by anyone holding it, and the caller's own dictionary
stayed aliased into it besides.

ADR-0034 closes it, and the annotation alone is not the fix. A ``Mapping``
annotation states the intent and is erased at runtime; what makes the field
immutable is :meth:`ResearchPayload.__post_init__`, which copies what it is
given into a :class:`types.MappingProxyType`. The copy severs the caller's
reference and the proxy refuses every mutating operation, so both routes are
closed. This is the same idiom :mod:`alphalab.runtime.context_views` uses for
the read-only views it hands a strategy.

**Hashing is deliberately unchanged, and is not a regression.** A
``MappingProxyType`` is unhashable, exactly as the ``dict`` before it was, so
``hash(payload)`` raises ``TypeError`` today and raised it before. That is the
package-wide norm rather than an oversight:
:class:`~alphalab.portfolio.engine.PortfolioState`,
:class:`~alphalab.portfolio.cash.CashLedger` and
:class:`~alphalab.studio.strategy.StrategyDefinition` are all frozen dataclasses
holding a ``Mapping`` and none of them is hashable either. Equality is what these
values are compared by, and equality is unaffected -- a ``MappingProxyType``
compares equal to the dictionary it wraps, so a payload built from a literal
still equals one built from the same literal.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

__all__ = [
    "ResearchPayload",
    "ResearchProtocol",
    "TradePayload",
]


@dataclass(frozen=True, slots=True)
class TradePayload:
    trade_id: str
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ResearchPayload:
    """Standardized immutable data structure required for research evaluation.

    Attributes:
        strategy_id: Which strategy this evidence is about.
        returns: The return series, in order.
        trades: Every trade the run produced.
        parameters: The parameter set the run used. **Genuinely immutable**: the
            mapping given here is copied and wrapped, so neither the holder of
            this payload nor the caller that built it can change what it says
            afterwards. See the module docstring.
        market_regimes: The regime label observed for each period.
        aum: Assets under management the run was sized against.
    """

    strategy_id: str
    returns: tuple[float, ...]
    trades: tuple[TradePayload, ...]
    parameters: Mapping[str, float]
    market_regimes: tuple[str, ...]
    aum: float

    def __post_init__(self) -> None:
        # Copy first, then wrap. The proxy alone would still be a live view of
        # the caller's dictionary, so a caller that mutated its own copy would
        # change what this payload reports having been run with.
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


class ResearchProtocol(Protocol):
    """Pure functional interface for providing research data."""

    def get_research_payload(self) -> ResearchPayload: ...
