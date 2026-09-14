"""``Dispatcher.dispatch_event`` no longer takes ``Any``, and still routes exactly.

ADR-0032 category C finding 4: "``Dispatcher.dispatch_event`` takes ``event: Any``.
Narrowing it to the union it routes would let a caller's mistake be a static
error. It cannot be done inside ``alphalab.strategy`` while ADR-0016 decision 3
stands, because naming the union requires the market types; doing it properly
means moving hook selection up to ``alphalab.runtime``, which changes a canonical
public API."

ADR-0034 closes it, and the third option the finding did not consider is the one
taken: **name the supertype, not the union.** Both families that reach a hook --
the three events in ``alphalab.strategy.events`` and the canonical market
vocabulary -- derive from :class:`~alphalab.common.events.BaseEvent`, and
``alphalab.common`` sits below both, so it is nameable from ``alphalab.strategy``
without acquiring the dependency ADR-0016 forbids.

What that buys and what it does not, stated so neither is overclaimed:

* a bare ``object()``, a dict, a string or an ``Intent`` at a call site is now a
  **static error** -- which is the whole of what the finding asked for;
* ``alphalab.live.events.TickReceived`` still type-checks, because what makes it
  the wrong class is the *module* it is defined in and no static type can say
  that. :func:`~alphalab.strategy.dispatcher.market_hook_for` is still the exact
  check, still at runtime, and still refuses it.

This file pins all four properties: the signature changed, hook selection did
**not** move, the dependency boundary did not move, and routing is unchanged.
"""

import ast
import inspect
import pathlib
import typing

import pytest

from alphalab.common.events import BaseEvent
from alphalab.market.events import (
    BarClosed,
    BookUpdated,
    MarketEvent,
    QuoteReceived,
    SnapshotCreated,
    TickReceived,
    TradeReceived,
)
from alphalab.strategy.dispatcher import MARKET_EVENT_HOOKS, Dispatcher, market_hook_for
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import (
    FillEvent,
    OrderEvent,
    StrategyInboundEvent,
    TimerEvent,
)

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"

#: Every canonical market event, and the hook it must reach (``None`` for the
#: two depth events, which are a stated non-route).
CANONICAL_EVENTS: tuple[tuple[type[MarketEvent], str | None], ...] = (
    (TickReceived, "on_tick"),
    (QuoteReceived, "on_quote"),
    (TradeReceived, "on_trade"),
    (BarClosed, "on_bar"),
    (BookUpdated, None),
    (SnapshotCreated, None),
)


# ---------------------------------------------------------------------------
# 1. The Any is gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "function",
    [Dispatcher.dispatch_event, StrategyEngine.process_event],
    ids=lambda f: f.__qualname__,
)
def test_the_event_parameter_is_not_any(function: object) -> None:
    declared = typing.get_type_hints(function)["event"]

    assert declared is not typing.Any
    assert declared is StrategyInboundEvent
    # ``type X = Y`` is lazy, so the alias is what is declared and the supertype
    # is what it resolves to. Both halves matter: the first is the vocabulary a
    # reader sees, the second is what a type checker enforces.
    assert declared.__value__ is BaseEvent


def test_the_dispatcher_return_type_is_not_any_either() -> None:
    """``tuple[..., tuple[Any, ...]]`` said nothing about the lifecycle events."""

    from alphalab.strategy.events import LifecycleTransitioned

    returned = typing.get_type_hints(Dispatcher.dispatch_event)["return"]
    lifecycle = typing.get_args(returned)[2]

    assert typing.get_args(lifecycle)[0] is LifecycleTransitioned


def test_no_any_survives_in_the_dispatcher_module() -> None:
    """The narrowing is the point; a leftover ``Any`` import would undercut it."""

    source = (PACKAGE / "strategy" / "dispatcher.py").read_text()
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
        for alias in node.names
    }

    assert "Any" not in imported


# ---------------------------------------------------------------------------
# 2. Every event a hook takes satisfies the declared type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", [cls for cls, _ in CANONICAL_EVENTS])
def test_every_canonical_market_event_satisfies_the_declared_parameter(
    event_type: type[MarketEvent],
) -> None:
    assert issubclass(event_type, BaseEvent)


@pytest.mark.parametrize("event_type", [FillEvent, OrderEvent, TimerEvent])
def test_every_strategy_event_satisfies_the_declared_parameter(event_type: type) -> None:
    assert issubclass(event_type, BaseEvent)


def test_the_declared_type_is_the_tightest_common_supertype() -> None:
    """Anything tighter would exclude one of the two families it must admit.

    Stated as a property rather than asserted by hand: there is no common base
    between the two families below ``BaseEvent``, so ``BaseEvent`` is not a lazy
    choice, it is the only one.
    """

    market = set(TickReceived.__mro__)
    strategy = set(FillEvent.__mro__)
    shared = market & strategy

    assert BaseEvent in shared
    below = shared - {BaseEvent, object}
    assert not below, f"a tighter shared supertype exists: {below}"


# ---------------------------------------------------------------------------
# 3. Hook selection did not move, and neither did the dependency boundary
# ---------------------------------------------------------------------------


def test_hook_selection_still_lives_in_the_strategy_package() -> None:
    """ADR-0032's ownership table assigns it here, and ADR-0034 did not move it.

    Moving selection up to ``alphalab.runtime`` -- the alternative the finding
    named -- would have put a second dispatch authority beside this table.
    """

    assert market_hook_for.__module__ == "alphalab.strategy.dispatcher"
    assert set(MARKET_EVENT_HOOKS) == {
        "TickReceived",
        "QuoteReceived",
        "TradeReceived",
        "BarClosed",
    }
    assert "market_hook_for" in inspect.getsource(Dispatcher.dispatch_event)


def test_exactly_one_module_holds_a_market_hook_table() -> None:
    """A second table anywhere would be the duplicate authority to avoid."""

    holders = [
        str(path.relative_to(PACKAGE.parent))
        for path in sorted(PACKAGE.rglob("*.py"))
        if "__pycache__" not in str(path) and "MARKET_EVENT_HOOKS" in path.read_text()
    ]

    assert holders == ["alphalab/strategy/dispatcher.py"], holders


def test_the_strategy_package_still_imports_no_market_type() -> None:
    """ADR-0016 decision 3, which the narrowing was not allowed to breach."""

    offenders: list[str] = []
    for path in sorted((PACKAGE / "strategy").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(("alphalab.market", "alphalab.instrument")):
                    offenders.append(f"{path.name}: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("alphalab.market", "alphalab.instrument")):
                        offenders.append(f"{path.name}: {alias.name}")

    assert not offenders, f"alphalab.strategy acquired a forbidden dependency: {offenders}"


# ---------------------------------------------------------------------------
# 4. Routing is byte-for-byte what it was
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("event_type", "hook"), CANONICAL_EVENTS, ids=lambda v: str(v))
def test_routing_is_unchanged_for_every_canonical_event(
    event_type: type[MarketEvent], hook: str | None
) -> None:
    assert market_hook_for(event_type.__new__(event_type)) == hook


def test_a_class_that_only_shares_a_name_still_reaches_no_hook() -> None:
    """The static type admits it; the runtime check is what refuses it.

    This is the property the narrowing could not provide and did not claim to.
    """

    from alphalab.live.events import TickReceived as LiveTick

    impostor = LiveTick.__new__(LiveTick)

    assert isinstance(impostor, BaseEvent), "it does satisfy the declared type"
    assert market_hook_for(impostor) is None, "and is still refused at runtime"
