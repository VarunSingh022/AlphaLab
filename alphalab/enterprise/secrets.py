"""Secret *references* and rotation metadata -- never secret values.

This module records where a secret lives (provider + external id) and when it
was last rotated. It never accepts, stores, returns, or logs secret material.
To read an actual secret, resolve the reference against the named provider
outside AlphaLab.
"""

from dataclasses import replace

from alphalab.common.registry import with_mapping_item
from alphalab.enterprise.exceptions import EnterpriseInputError
from alphalab.enterprise.models import EnterpriseState, SecretRef

_SECONDS_PER_DAY = 86_400.0


def register_secret_ref(
    state: EnterpriseState,
    name: str,
    provider: str,
    external_id: str,
    rotation_period_days: float,
    timestamp: float,
) -> tuple[EnterpriseState, SecretRef]:
    """Registers a reference to an externally held secret.

    Raises:
        EnterpriseInputError: If ``name``, ``provider``, or ``external_id`` is
            blank, ``rotation_period_days`` is not positive, or ``name`` is
            already registered.
    """
    if not name.strip():
        raise EnterpriseInputError("name cannot be empty.")
    if not provider.strip():
        raise EnterpriseInputError("provider cannot be empty.")
    if not external_id.strip():
        raise EnterpriseInputError("external_id cannot be empty.")
    if rotation_period_days <= 0:
        raise EnterpriseInputError(
            f"rotation_period_days must be positive, got {rotation_period_days}."
        )
    if name in state.secret_refs:
        raise EnterpriseInputError(f"Secret reference '{name}' is already registered.")

    ref = SecretRef(
        name=name,
        provider=provider,
        external_id=external_id,
        rotation_period_days=rotation_period_days,
        last_rotated_at=timestamp,
    )
    return replace(state, secret_refs=with_mapping_item(state.secret_refs, name, ref)), ref


def mark_rotated(state: EnterpriseState, name: str, timestamp: float) -> EnterpriseState:
    """Records that the secret behind reference ``name`` was rotated at ``timestamp``.

    Raises:
        EnterpriseInputError: If ``name`` is unknown.
    """
    ref = state.secret_refs.get(name)
    if ref is None:
        raise EnterpriseInputError(f"Secret reference '{name}' is not registered.")
    updated = replace(ref, last_rotated_at=timestamp)
    return replace(state, secret_refs=with_mapping_item(state.secret_refs, name, updated))


def list_secret_refs(state: EnterpriseState) -> tuple[SecretRef, ...]:
    """Returns every registered secret reference, name-sorted."""
    return tuple(sorted(state.secret_refs.values(), key=lambda ref: ref.name))


def secrets_due_for_rotation(state: EnterpriseState, now: float) -> tuple[SecretRef, ...]:
    """Returns references whose rotation period has elapsed by ``now``, name-sorted."""
    return tuple(
        ref
        for ref in list_secret_refs(state)
        if now - ref.last_rotated_at >= ref.rotation_period_days * _SECONDS_PER_DAY
    )
