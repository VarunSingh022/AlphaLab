"""The OMS snapshot declares its schema version, and reads exactly one legacy shape.

``OMSSnapshot`` was the last round-trip snapshot without a ``schema_version``.
Portfolio has carried one since v2.5 and bumped it to 2 in v2.6; lifecycle
carries one and de-aliased it in v2.8. The OMS payload carried none, so a stored
payload of any vintage decoded as current and the first schema change would have
been a silent misread rather than a decision -- the thing ADR-0014 added the
field to prevent.

v2.9 declares ``OMS_SNAPSHOT_SCHEMA = 1`` and keeps one bounded compatibility
path. ``alphalab.oms`` exports ``capture``, ``restore`` and ``from_primitives``
and its module docstring teaches the JSON round trip as a public recipe, so
payloads written between v2.2 and v2.8 exist by invitation. Unlike the
portfolio's refused version 1 they are missing no data: every field the decoder
reads is present. Refusing them would discard a payload that can be read
perfectly, for no informational reason.

So an unversioned payload is read **only** when its top-level keys are exactly
``LEGACY_UNVERSIONED_V0_KEYS``. That is a total structural match. These tests
pin the distinction that matters: a missing ``schema_version`` is never read as
version 1: it is sent to a shape test that a payload either passes whole or
fails. See ADR-0023.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.oms.engine import OMSEngine
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order
from alphalab.oms.snapshot import (
    LEGACY_UNVERSIONED_V0_KEYS,
    OMS_SNAPSHOT_SCHEMA,
    OMSSnapshot,
    SnapshotDecodeError,
    capture,
    from_primitives,
    restore,
)
from alphalab.oms.state import OMSState
from alphalab.persistence import deserialize, serialize

# ---------------------------------------------------------------------------
# Fixtures: one populated state, and the four payload shapes under test
# ---------------------------------------------------------------------------


def _order(order_id: OrderId, asset: str = "AAPL", strategy: str = "S1") -> Order:
    return Order(
        order_id=order_id,
        strategy_id=strategy,
        asset_id=asset,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.NEW,
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal("10"),
        limit_price=Decimal("100.0050"),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
        metadata={"reference_price": "100.005"},
    )


def _state() -> OMSState:
    """A state with one working and one completed order, and a full event log."""

    working = OrderId(UUID("11111111-1111-4111-8111-111111111111"))
    done = OrderId(UUID("22222222-2222-4222-8222-222222222222"))

    state = OMSEngine.submit(OMSState(), _order(working), 1.0)
    state = OMSEngine.accept(state, working, 2.0)
    state = OMSEngine.partial_fill(state, working, Decimal("4"), Decimal("100.0050"), 3.0)
    state = OMSEngine.submit(state, _order(done, asset="MSFT", strategy="OTHER"), 1.0)
    state = OMSEngine.accept(state, done, 2.0)
    return OMSEngine.fill(state, done, Decimal("10"), Decimal("99.9950"), 4.0)


def schema_v1(state: OMSState | None = None) -> dict[str, Any]:
    """What this build writes: the five projected fields plus a declared version.

    Takes the state rather than building one, because every ``_state()`` call
    mints fresh event ids -- so a test comparing a restored payload against a
    freshly built state would be comparing two different runs.
    """

    return dict(deserialize(serialize(capture(state if state is not None else _state()))))


def legacy_unversioned_v0(state: OMSState | None = None) -> dict[str, Any]:
    """What v2.2-v2.8 wrote: exactly the five projected fields, and nothing else."""

    payload = schema_v1(state)
    del payload["schema_version"]
    return payload


def schema_v2_or_future(version: int = 2) -> dict[str, Any]:
    """A payload from a build newer than this one."""

    payload = schema_v1()
    payload["schema_version"] = version
    return payload


def malformed_unversioned() -> dict[str, Any]:
    """Unversioned, and not the legacy shape: one key short."""

    payload = legacy_unversioned_v0()
    del payload["history"]
    return payload


# ---------------------------------------------------------------------------
# The declared version
# ---------------------------------------------------------------------------


def test_the_schema_constant_is_one() -> None:
    """The first *declared* shape, not the first shape: no field was added."""

    assert OMS_SNAPSHOT_SCHEMA == 1


def test_the_oms_constant_is_not_an_alias_of_the_shared_default() -> None:
    """It must be independently settable, or the next bump versions every event.

    The OMS version is 1 and so is ``DEFAULT_SCHEMA_VERSION``, so an inequality
    proves nothing -- the same problem the v2.8 lifecycle guard hit. What must
    not exist is the *dependency*, so that is what is asserted: no import, and
    no assignment from it.
    """

    import inspect

    from alphalab.oms import snapshot as oms_snapshot

    source = inspect.getsource(oms_snapshot)

    assert OMSSnapshot.__dataclass_fields__["schema_version"].default is OMS_SNAPSHOT_SCHEMA
    assert not hasattr(oms_snapshot, "DEFAULT_SCHEMA_VERSION")
    assert "OMS_SNAPSHOT_SCHEMA: Final = 1" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source
    assert "import DEFAULT_SCHEMA_VERSION" not in source


def test_capture_declares_the_version() -> None:
    assert capture(_state()).schema_version == OMS_SNAPSHOT_SCHEMA
    assert schema_v1()["schema_version"] == 1


def test_a_version_one_payload_round_trips() -> None:
    state = _state()

    assert restore(from_primitives(schema_v1(state))) == state


def test_the_documented_json_recipe_still_holds() -> None:
    """The module docstring's round trip, unchanged by versioning."""

    state = _state()

    assert restore(from_primitives(deserialize(serialize(state)))) == state


# ---------------------------------------------------------------------------
# The bounded legacy path
# ---------------------------------------------------------------------------


def test_the_legacy_key_set_is_exactly_the_five_projected_fields() -> None:
    assert {
        "orders",
        "active_orders",
        "completed_orders",
        "history",
        "events",
    } == LEGACY_UNVERSIONED_V0_KEYS


def test_the_legacy_fixture_carries_those_keys_and_nothing_else() -> None:
    assert set(legacy_unversioned_v0()) == LEGACY_UNVERSIONED_V0_KEYS
    assert "schema_version" not in legacy_unversioned_v0()


def test_an_exact_legacy_payload_is_accepted_without_a_version() -> None:
    assert from_primitives(legacy_unversioned_v0()).orders


def test_an_exact_legacy_payload_reconstructs_the_same_state() -> None:
    state = _state()

    assert restore(from_primitives(legacy_unversioned_v0(state))) == state


def test_reading_a_legacy_payload_does_not_make_the_payload_version_one() -> None:
    """The decoded object is the current shape; the payload it came from was not.

    Re-capturing and serializing writes the version explicitly, which is the
    whole of the migration -- there is no in-place rewrite of a stored payload.
    """

    restored = restore(from_primitives(legacy_unversioned_v0()))
    rewritten = deserialize(serialize(capture(restored)))

    assert "schema_version" not in legacy_unversioned_v0()
    assert rewritten["schema_version"] == OMS_SNAPSHOT_SCHEMA
    assert set(rewritten) == LEGACY_UNVERSIONED_V0_KEYS | {"schema_version"}


# ---------------------------------------------------------------------------
# Refusal: unversioned payloads that are not the exact shape
# ---------------------------------------------------------------------------


def test_a_legacy_payload_missing_a_key_is_refused() -> None:
    with pytest.raises(SnapshotDecodeError, match=r"not the pre-v2\.9 payload shape"):
        from_primitives(malformed_unversioned())


def test_a_legacy_payload_with_an_extra_key_is_refused() -> None:
    payload = legacy_unversioned_v0()
    payload["surprise"] = 1

    with pytest.raises(SnapshotDecodeError, match=r"not the pre-v2\.9 payload shape"):
        from_primitives(payload)


def test_an_arbitrary_unversioned_mapping_is_refused() -> None:
    with pytest.raises(SnapshotDecodeError, match=r"not the pre-v2\.9 payload shape"):
        from_primitives({"hello": "world"})


def test_an_empty_mapping_is_refused() -> None:
    with pytest.raises(SnapshotDecodeError, match=r"not the pre-v2\.9 payload shape"):
        from_primitives({})


def test_the_refusal_says_a_missing_version_is_not_version_one() -> None:
    """The message must not leave 'unversioned' looking like an accepted default."""

    with pytest.raises(SnapshotDecodeError) as excinfo:
        from_primitives({"hello": "world"})

    assert "is not read as version 1" in str(excinfo.value)


def test_a_legacy_shaped_payload_with_a_malformed_value_is_still_refused() -> None:
    """Passing the shape test buys nothing: every field is still decoded."""

    payload = legacy_unversioned_v0()
    payload["orders"][0]["order_id"]["value"] = "not-a-uuid"

    with pytest.raises(SnapshotDecodeError, match="Not a valid OrderId"):
        from_primitives(payload)


# ---------------------------------------------------------------------------
# Refusal: declared versions this build does not read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [2, 3, 99])
def test_a_future_version_is_refused_naming_the_version(version: int) -> None:
    with pytest.raises(SnapshotDecodeError, match=f"declares schema version {version}"):
        from_primitives(schema_v2_or_future(version))


@pytest.mark.parametrize("version", [0, -1])
def test_a_version_below_one_is_refused(version: int) -> None:
    with pytest.raises(SnapshotDecodeError, match=f"declares schema version {version}"):
        from_primitives(schema_v2_or_future(version))


@pytest.mark.parametrize("version", ["1", 1.0, True, None, []])
def test_a_non_integer_version_is_refused(version: object) -> None:
    payload = schema_v1()
    payload["schema_version"] = version

    with pytest.raises(SnapshotDecodeError, match="schema_version is not an integer"):
        from_primitives(payload)


def test_the_version_refusal_names_the_oms_subsystem() -> None:
    with pytest.raises(SnapshotDecodeError) as excinfo:
        from_primitives(schema_v2_or_future())

    assert str(excinfo.value).startswith("oms snapshot")


def test_version_errors_are_the_oms_decode_error_callers_already_catch() -> None:
    """The rule is shared with portfolio and lifecycle; the error type is ours.

    ``SnapshotDecodeError`` is what every other OMS decode failure raises and
    what existing callers catch, so a version failure must not arrive as an
    unrelated persistence exception.
    """

    from alphalab.oms.exceptions import OMSError
    from alphalab.persistence.exceptions import StateDecodeError

    with pytest.raises(SnapshotDecodeError) as excinfo:
        from_primitives(schema_v2_or_future())

    assert isinstance(excinfo.value, OMSError)
    assert not isinstance(excinfo.value, StateDecodeError)


# ---------------------------------------------------------------------------
# Nothing is substituted, and the field decoders still run
# ---------------------------------------------------------------------------


def test_a_versioned_payload_missing_a_field_still_names_the_field() -> None:
    payload = schema_v1()
    del payload["active_orders"]

    with pytest.raises(SnapshotDecodeError, match="missing 'active_orders'"):
        from_primitives(payload)


def test_a_non_object_payload_is_refused_before_the_version_is_read() -> None:
    with pytest.raises(SnapshotDecodeError, match="not an object"):
        from_primitives([])  # type: ignore[arg-type]


def test_an_empty_state_round_trips_under_the_new_version() -> None:
    assert restore(from_primitives(deserialize(serialize(OMSState())))) == OMSState()


def test_the_legacy_path_is_confined_to_standalone_oms_decoding() -> None:
    """Nothing this build writes relies on shape inference.

    Every payload ``capture`` produces declares its version, so a snapshot
    embedded in any future envelope is read by the versioned branch. The legacy
    branch exists only for payloads written before v2.9.
    """

    assert "schema_version" in deserialize(serialize(capture(_state())))
    assert "schema_version" in deserialize(serialize(capture(OMSState())))
    assert "schema_version" in deserialize(serialize(_state()))


def test_other_subsystems_keep_their_own_schema_rules() -> None:
    """D2 versions the OMS payload and moves nothing else."""

    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA

    assert PORTFOLIO_SNAPSHOT_SCHEMA == 2
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 1


def test_an_unversioned_portfolio_payload_gains_no_legacy_path() -> None:
    """The exact-shape rule is the OMS decoder's, not a generic decoding rule."""

    from alphalab.persistence.exceptions import StateDecodeError
    from alphalab.portfolio.snapshot import from_primitives as portfolio_from_primitives

    with pytest.raises(StateDecodeError, match="missing 'schema_version'"):
        portfolio_from_primitives({"account": {}, "balances": {}})


def test_an_order_id_that_is_not_a_uuid_is_still_rejected() -> None:
    """Guards against a legacy branch that skips validation to be permissive."""

    payload = schema_v1()
    payload["active_orders"][0] = {"value": str(uuid4())[:8]}

    with pytest.raises(SnapshotDecodeError, match="Not a valid OrderId"):
        from_primitives(payload)
