"""``ResearchPayload.parameters`` cannot be changed by anyone holding it.

ADR-0032 category C finding 2: ``parameters`` was annotated ``dict[str, float]``
on a ``frozen=True`` dataclass -- the only mutable field *type* on any frozen
dataclass in the package. ``frozen=True`` refuses ``payload.parameters = {...}``
and says nothing about ``payload.parameters["ma"] = 50``, so the value advertised
an immutability it did not have, and the caller's own dictionary stayed aliased
into it besides.

ADR-0034 closes it. The annotation is now ``Mapping[str, float]`` and
``__post_init__`` copies what it is given into a ``MappingProxyType``, which is
what actually enforces it: the annotation is erased at runtime, so a test that
only read ``__annotations__`` would pass against the original defect.

Two routes, two tests, because the fix needed both halves:

* **writing through the payload** -- closed by the proxy;
* **writing through the dictionary you built it from** -- closed by the copy.
  A proxy over the caller's live dictionary would still change what the payload
  reports having been run with.
"""

from dataclasses import FrozenInstanceError, fields, is_dataclass
from types import MappingProxyType

import pytest

from alphalab.persistence import deserialize, serialize
from alphalab.research.adapter import ResearchAdapter
from alphalab.research.protocol import ResearchPayload, TradePayload


def _payload(parameters: dict[str, float] | None = None) -> ResearchPayload:
    return ResearchPayload(
        strategy_id="STRAT-1",
        returns=(0.01, -0.005, 0.02),
        trades=(TradePayload("T1", "AAPL", 100.0, 110.0, 10.0, 100.0, 3600.0),),
        parameters={"ma": 20.0} if parameters is None else parameters,
        market_regimes=("BULL",),
        aum=1_000_000.0,
    )


# ---------------------------------------------------------------------------
# 1. Neither route can change it
# ---------------------------------------------------------------------------


def test_a_holder_cannot_write_through_the_payload() -> None:
    payload = _payload()

    with pytest.raises(TypeError):
        payload.parameters["ma"] = 50.0  # type: ignore[index]

    assert payload.parameters["ma"] == 20.0


@pytest.mark.parametrize("operation", ["__setitem__", "__delitem__", "clear", "update", "pop"])
def test_every_mutating_operation_is_refused(operation: str) -> None:
    payload = _payload()

    assert not hasattr(payload.parameters, operation), (
        f"parameters exposes {operation}, so it is still editable in place"
    )


def test_the_builder_cannot_write_through_its_own_dictionary() -> None:
    """The copy, not the proxy. A proxy over a live dict is still a live view."""

    source = {"ma": 20.0}
    payload = _payload(source)

    source["ma"] = 50.0
    source["lookback"] = 5.0

    assert dict(payload.parameters) == {"ma": 20.0}


def test_the_field_itself_is_still_frozen() -> None:
    payload = _payload()

    with pytest.raises(FrozenInstanceError):
        payload.parameters = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. It is enforced, not merely annotated
# ---------------------------------------------------------------------------


def test_the_stored_value_is_a_read_only_view_whatever_was_passed() -> None:
    """The annotation is erased at runtime; this is what does the work."""

    assert isinstance(_payload().parameters, MappingProxyType)
    assert isinstance(_payload({"a": 1.0}).parameters, MappingProxyType)


def test_no_frozen_dataclass_in_the_package_declares_a_mutable_container() -> None:
    """The finding was about the *class* of defect, so the sweep is too."""

    import importlib
    import pkgutil

    import alphalab

    offenders: list[str] = []
    for info in pkgutil.walk_packages(alphalab.__path__, "alphalab."):
        module = importlib.import_module(info.name)
        for name in getattr(module, "__all__", ()):
            value = getattr(module, name, None)
            if not isinstance(value, type) or not is_dataclass(value):
                continue
            if not value.__dataclass_params__.frozen:  # type: ignore[attr-defined]
                continue
            for declared in fields(value):
                text = str(declared.type)
                if text.startswith(("dict[", "list[", "set[")):
                    offenders.append(f"{value.__name__}.{declared.name}: {text}")

    assert not offenders, f"mutable container types on frozen dataclasses: {sorted(set(offenders))}"


# ---------------------------------------------------------------------------
# 3. Everything around it still works
# ---------------------------------------------------------------------------


def test_reading_is_unchanged() -> None:
    payload = _payload({"ma": 20.0, "lookback": 5.0})

    assert payload.parameters["ma"] == 20.0
    assert len(payload.parameters) == 2
    assert sorted(payload.parameters) == ["lookback", "ma"]
    assert dict(payload.parameters) == {"ma": 20.0, "lookback": 5.0}


def test_equality_survives_the_wrapping() -> None:
    """A proxy compares equal to the mapping it wraps, so payloads still compare."""

    assert _payload() == _payload()
    assert _payload({"ma": 20.0}) != _payload({"ma": 21.0})


def test_hashing_is_unchanged_which_is_the_deliberate_part() -> None:
    """Still unhashable, exactly as with the ``dict``, and not a regression.

    Every frozen dataclass in the package that holds a ``Mapping`` is unhashable
    -- ``PortfolioState``, ``CashLedger``, ``StrategyDefinition``. These values
    are compared by equality, never used as keys, and making this one hashable
    would make it the odd one out rather than fixing anything.
    """

    with pytest.raises(TypeError):
        hash(_payload())


def test_the_payload_still_serializes_deterministically() -> None:
    """A ``MappingProxyType`` is not a ``dict``, so the codec needed a branch."""

    payload = _payload({"ma": 20.0, "lookback": 5.0})
    encoded = serialize(payload)

    assert encoded == serialize(_payload({"lookback": 5.0, "ma": 20.0}))
    assert deserialize(encoded)["parameters"] == {"ma": 20.0, "lookback": 5.0}


def test_the_adapter_builds_an_immutable_payload_too() -> None:
    """The other construction site, and the one a caller is steered toward."""

    source = {"ma": 20.0}
    payload = ResearchAdapter.to_research_payload(
        strategy_id="S",
        returns=(0.01,),
        trades=({"trade_id": "T1", "symbol": "AAPL"},),
        parameters=source,
        market_regimes=("BULL",),
        aum=1_000.0,
    )

    source["ma"] = 99.0

    assert dict(payload.parameters) == {"ma": 20.0}
    with pytest.raises(TypeError):
        payload.parameters["ma"] = 99.0  # type: ignore[index]
