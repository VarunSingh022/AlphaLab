"""The lifecycle snapshot version is its own, not a shared constant's.

``LIFECYCLE_SNAPSHOT_SCHEMA`` aliased ``DEFAULT_SCHEMA_VERSION`` until v2.8. That
constant is also the version of ``CommonEvent`` and ``BaseEvent``, so bumping it
would have versioned every event in the system as a side effect of a lifecycle
change. v2.6 removed exactly this trap from ``PortfolioSnapshot`` -- see
``test_portfolio_snapshot_schema_2`` -- and left it standing here.

The value does not move: it is 1 before and after, so no payload reads or writes
differently. What changes is that it is now independently settable.

The portfolio's own guard asserts ``PORTFOLIO_SNAPSHOT_SCHEMA !=
DEFAULT_SCHEMA_VERSION``, which works only because the values differ. Here they
do not, so an inequality proves nothing and the assertions have to be
structural: the module no longer imports the shared constant at all.
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


def test_the_lifecycle_version_is_still_one() -> None:
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 1
    assert capture(LifecycleState()).schema_version == 1
    assert _payload()["schema_version"] == 1


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

    assert "LIFECYCLE_SNAPSHOT_SCHEMA = 1" in source
    assert "= DEFAULT_SCHEMA_VERSION" not in source
    assert "import DEFAULT_SCHEMA_VERSION" not in source
    assert "from alphalab.common.constants import" not in source


def test_the_shared_constant_itself_did_not_move() -> None:
    """De-aliasing is not a bump: nothing else changes version."""

    from alphalab.common.events import CommonEvent
    from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA

    assert DEFAULT_SCHEMA_VERSION == 1
    assert CommonEvent("e").schema_version == 1
    assert PORTFOLIO_SNAPSHOT_SCHEMA == 2


def test_a_payload_written_before_the_de_alias_still_restores() -> None:
    """Byte-identical on both sides, so a v2.7 payload is a v2.8 payload."""

    payload = _payload()

    assert from_primitives(payload).schema_version == 1


def test_an_unknown_version_is_still_refused_by_version() -> None:
    payload = _payload()
    payload["schema_version"] = LIFECYCLE_SNAPSHOT_SCHEMA + 7

    with pytest.raises(StateDecodeError, match="declares schema version"):
        from_primitives(payload)
