"""Regression guard: a snapshot must not silently omit a state's field.

``capture`` names each field it projects. That is deliberate -- explicit
projection is what lets a snapshot record ``model_type`` where the state holds an
object, and flatten a keyed index to an array. The cost is that adding a field to
a state and forgetting to add it to its snapshot loses data *silently*: the
round-trip still passes, because ``restore`` reconstructs the field's default and
two defaults compare equal.

These tests fail when that happens. Each names the fields its snapshot
deliberately does not carry, and why, so a new field is a decision rather than an
omission.
"""

from dataclasses import fields

from alphalab.lifecycle.snapshot import LifecycleSnapshot
from alphalab.lifecycle.state import LifecycleState
from alphalab.oms.snapshot import OMSSnapshot
from alphalab.oms.state import OMSState
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.snapshot import PortfolioSnapshot


def _names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


# --------------------------------------------------------------------------- #
# Portfolio
# --------------------------------------------------------------------------- #


def test_the_portfolio_snapshot_covers_every_state_field() -> None:
    """``cash`` and ``ledger`` are carried under the names of what they hold."""

    carried = _names(PortfolioSnapshot) | {
        "cash",  # -> balances + reserved
        "ledger",  # -> transactions
    }
    missing = _names(PortfolioState) - carried

    assert not missing, (
        f"PortfolioState fields absent from PortfolioSnapshot: {sorted(missing)}. "
        "Add them to capture/restore/from_primitives, or state here why they are "
        "deliberately not persisted."
    )


def test_the_portfolio_snapshot_carries_nothing_the_state_does_not_have() -> None:
    derived = {"balances", "reserved", "transactions", "schema_version"}
    unexpected = _names(PortfolioSnapshot) - _names(PortfolioState) - derived

    assert not unexpected, f"PortfolioSnapshot invents: {sorted(unexpected)}"


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_the_lifecycle_snapshot_covers_every_state_field() -> None:
    """Each registry flattens to the arrays and indexes that reconstruct it."""

    carried = _names(LifecycleSnapshot) | {
        "experiments",  # -> runs
        "models",  # -> model_versions + model_promotions + production + production_line
        "strategies",  # -> strategy_versions + strategy_promotions
        "deployments",  # -> release_packages + deployments (environments is rebuilt)
    }
    missing = _names(LifecycleState) - carried

    assert not missing, (
        f"LifecycleState fields absent from LifecycleSnapshot: {sorted(missing)}. "
        "Add them to capture/restore/from_primitives, or state here why they are "
        "deliberately not persisted."
    )


def test_the_lifecycle_snapshot_records_the_model_type_it_cannot_carry() -> None:
    """The one field a lifecycle snapshot deliberately drops, and its stand-in."""

    from alphalab.lifecycle.snapshot import ModelVersionRecord
    from alphalab.model_registry.registry import ModelVersion

    record = _names(ModelVersionRecord)
    assert "model" not in record, "an arbitrary object has no deterministic JSON form"
    assert "model_type" in record, "dropping the object without recording what it was"
    assert _names(ModelVersion) - record == {"model"}


# --------------------------------------------------------------------------- #
# OMS -- the precedent this pattern came from
# --------------------------------------------------------------------------- #


def test_the_oms_snapshot_covers_every_state_field() -> None:
    carried = _names(OMSSnapshot) | {
        "orders",  # the book -> orders array; its indices are rebuilt by restore
    }
    missing = _names(OMSState) - carried

    assert not missing, f"OMSState fields absent from OMSSnapshot: {sorted(missing)}"
