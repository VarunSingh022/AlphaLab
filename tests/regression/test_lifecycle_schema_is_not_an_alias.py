"""The lifecycle snapshot version is its own, not a shared constant's.

``LIFECYCLE_SNAPSHOT_SCHEMA`` aliased ``DEFAULT_SCHEMA_VERSION`` until v2.8. That
constant is also the schema version every event in the system carries, so bumping it
would have versioned every event in the system as a side effect of a lifecycle
change. v2.6 removed exactly this trap from ``PortfolioSnapshot`` -- see
``test_portfolio_snapshot_schema_2`` -- and left it standing here.

v2.8 de-aliased it without moving the value, and said why: "what changes is that
it is now independently settable, which is what the next bump needs".

**v2.16 is that bump, and this file is what shows the de-alias was worth doing.**
``LIFECYCLE_SNAPSHOT_SCHEMA`` is now 2 -- carrying ``actor_id`` on two persisted
records and the approval log (ADR-0018) -- and ``DEFAULT_SCHEMA_VERSION`` is
still 1, so ``BaseEvent`` and every event in the system are untouched by a
lifecycle change. That is the whole point, now demonstrated
rather than asserted in advance.

The portfolio's own guard asserts ``PORTFOLIO_SNAPSHOT_SCHEMA !=
DEFAULT_SCHEMA_VERSION``. Until v2.16 that inequality was unavailable here
because the two values agreed, so the assertions were structural instead: the
module does not import the shared constant at all. Both kinds are now kept --
the structural ones are still the ones that would catch a re-aliasing.
"""

import inspect

import pytest

from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
from alphalab.lifecycle import snapshot as lifecycle_snapshot
from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA, capture, from_primitives
from alphalab.lifecycle.state import LifecycleState
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.serializer import deserialize, serialize


def _payload() -> dict[str, object]:
    payload = deserialize(serialize(capture(LifecycleState())))
    assert isinstance(payload, dict)
    return payload


def test_the_lifecycle_version_is_two_and_the_shared_one_did_not_follow() -> None:
    """The bump the de-alias was performed to make possible."""

    assert LIFECYCLE_SNAPSHOT_SCHEMA == 2
    assert capture(LifecycleState()).schema_version == 2
    assert _payload()["schema_version"] == 2
    assert DEFAULT_SCHEMA_VERSION == 1, (
        "the lifecycle bump moved the shared constant, which is exactly what "
        "de-aliasing it in v2.8 existed to prevent"
    )


def test_the_module_no_longer_depends_on_the_shared_constant() -> None:
    """The property that matters, and the one an equality check cannot show."""

    assert not hasattr(lifecycle_snapshot, "DEFAULT_SCHEMA_VERSION")


def test_the_constant_is_written_as_a_literal() -> None:
    """Neither assigned from the shared constant nor importing it.

    The word itself still appears, in the comment explaining why it is gone.
    What must not exist is the dependency, so that is what is asserted: no
    import, and no assignment from it.
    """

    source = inspect.getsource(lifecycle_snapshot)

    assert f"LIFECYCLE_SNAPSHOT_SCHEMA = {LIFECYCLE_SNAPSHOT_SCHEMA}" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source
    assert "import DEFAULT_SCHEMA_VERSION" not in source
    assert "from alphalab.common.constants import" not in source


def test_the_shared_constant_itself_did_not_move() -> None:
    """De-aliasing is not a bump: nothing else changes version."""

    from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA

    assert DEFAULT_SCHEMA_VERSION == 1
    assert PORTFOLIO_SNAPSHOT_SCHEMA == 3


def test_a_payload_this_build_writes_is_a_payload_this_build_reads() -> None:
    payload = _payload()

    assert from_primitives(payload).schema_version == LIFECYCLE_SNAPSHOT_SCHEMA


def test_a_version_one_payload_is_refused_rather_than_read() -> None:
    """ADR-0018 rejected decoding the new fields as optional at schema 1.

    That would give one version two payload shapes, which is the silent misread
    ``schema_version`` was introduced to prevent. A v2.15 lifecycle payload
    needs a v2.15 interpreter, exactly as the v2.6 ``PortfolioSnapshot``
    precedent set.
    """

    payload = _payload()
    payload["schema_version"] = 1
    for added in ("approvals",):
        payload.pop(added, None)

    with pytest.raises(StateDecodeError, match="declares schema version 1"):
        from_primitives(payload)


def test_an_unknown_version_is_still_refused_by_version() -> None:
    payload = _payload()
    payload["schema_version"] = LIFECYCLE_SNAPSHOT_SCHEMA + 7

    with pytest.raises(StateDecodeError, match="declares schema version"):
        from_primitives(payload)
