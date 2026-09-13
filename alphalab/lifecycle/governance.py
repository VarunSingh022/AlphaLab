"""Who may change what is live, who did, and who approved it.

ADR-0018 was written in v2.7, marked *Proposed — deferred*, and recorded the
seam precisely "so that the next release starts from a decision rather than from
a rediscovery". v2.16 is that release, and this module is that decision
implemented rather than redesigned.

The gap it closes
-----------------

Before v2.16 ``promote_strategy_version`` took no actor, ``StrategyPromotionRecord``
had no actor field, and ``DeploymentRecord`` had none either. The lifecycle's
audit trail answered *what* changed and *when*, and was silent on *who*.
Meanwhile ``alphalab.enterprise`` held ``Principal``, ``Session``, ``AuditEvent``
and a complete RBAC implementation -- thirty-two tests and, as
``git grep "from alphalab.enterprise"`` outside the package showed, **zero
production consumers**.

The shape, and why each part of it
----------------------------------

**Option (b), not (a) or (c).** ADR-0018 considered three ways to reach an
``EnterpriseState`` from a governance entry point:

* *(a) a field on ``LifecycleState``* -- rejected: it puts enterprise data inside
  the lifecycle snapshot and couples two packages that are deliberately separate.
* *(c) leave the check to callers* -- rejected as a permanent answer, in the
  ADR's own words: "a gate anyone can bypass by calling the function directly is
  not a governance control".
* *(b) an explicit parameter* -- taken. The dependency is visible in the
  signature rather than hidden inside a state container.

:class:`Governance` is that parameter. It is **required**, not optional with a
default of "unchecked" -- an optional gate is option (c) wearing option (b)'s
clothes.

**The actor is a reference, not an object.** ``Principal.principal_id`` carried
as a bare ``str`` on the lifecycle's records, matching how ``run_id``,
``evidence_id`` and ``policy_id`` are already carried.

**Two logs, one authority each.** The lifecycle's own append-only records are
authoritative for what, when and who; ``enterprise.AuditEvent`` remains the
standalone capability its docstring describes. Nothing here writes into the
enterprise audit log, so no governance entry point returns an
``EnterpriseState`` and ``alphalab.enterprise`` keeps its stated rule that RBAC,
workspace and secret operations do not auto-emit audit events.

**Nothing is written before a refusal.** Every gated function authorizes first,
which is the contract ``deploy_strategy_version`` already kept for its other
refusals.

Approval, and the one thing AlphaLab will not decide
----------------------------------------------------

A permission says *this principal may deploy*. An approval says *this particular
version was approved for this particular environment, by someone else*.
Separation of duties is enforced here -- :func:`approve_deployment` refuses an
approver who lacks ``lifecycle.approve``, and
:func:`~alphalab.lifecycle.deployment.deploy_strategy_version` refuses an
approval granted by the deployer themselves.

**Which environments require an approval is the operator's policy, and AlphaLab
does not guess it.** A firm that gates production and not paper is as
legitimate as one that gates both, and a default either way would be an invented
rule presented as an architectural one. :attr:`Governance.approval_required_in`
therefore states it per call, explicitly, next to the actor it applies to --
there is no configuration object and no hidden default. This is the same
reasoning ADR-0020 used to refuse a configured FX rate: an invented policy that
looks authoritative is worse than an absent one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from alphalab.deployment_manager.releases import DeploymentRecord
from alphalab.enterprise.models import EnterpriseState
from alphalab.enterprise.rbac import require_permission
from alphalab.lifecycle.exceptions import LifecycleInputError, LifecycleTransitionError
from alphalab.lifecycle.strategy_version import StrategyPromotionRecord

__all__ = [
    "LIFECYCLE_PERMISSIONS",
    "PERMISSION_APPROVE",
    "PERMISSION_DEPLOY",
    "PERMISSION_PROMOTE",
    "PERMISSION_RETIRE",
    "PERMISSION_ROLLBACK",
    "ApprovalRecord",
    "Governance",
    "GovernedAct",
    "approval_for",
    "approvals_for",
    "governance_trail",
]

#: Move a strategy version to ``STAGING`` on passing evidence.
PERMISSION_PROMOTE: Final = "lifecycle.promote"

#: Make a strategy version the one active in an environment.
PERMISSION_DEPLOY: Final = "lifecycle.deploy"

#: Return an environment to the version that ran before the current one.
PERMISSION_ROLLBACK: Final = "lifecycle.rollback"

#: Archive a strategy version that is not deployed anywhere.
PERMISSION_RETIRE: Final = "lifecycle.retire"

#: Approve a strategy version for deployment to an environment.
PERMISSION_APPROVE: Final = "lifecycle.approve"

#: Every permission this package checks. A deployment role is built from these.
LIFECYCLE_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        PERMISSION_PROMOTE,
        PERMISSION_DEPLOY,
        PERMISSION_ROLLBACK,
        PERMISSION_RETIRE,
        PERMISSION_APPROVE,
    }
)


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """One recorded approval of a strategy version for an environment.

    Immutable and append-only, like every other lifecycle record. An approval is
    for an exact ``(name, version, environment)``: approving version 3 for
    ``paper`` says nothing about version 4, and nothing about ``live``.

    Attributes:
        name: The strategy line.
        version: The version approved.
        environment: The environment it is approved for.
        approver_id: Principal that approved it.
        timestamp: Unix timestamp of the approval.
        note: Free-form context. Not interpreted.
    """

    name: str
    version: int
    environment: str
    approver_id: str
    timestamp: float
    note: str = ""

    @property
    def subject(self) -> str:
        """What was approved, rendered: ``name@version -> environment``."""

        return f"{self.name}@{self.version} -> {self.environment}"


@dataclass(frozen=True, slots=True)
class Governance:
    """Who is acting, against which enterprise state, and where approval is needed.

    Passed explicitly to every entry point that changes what is live or
    archived. It is a *parameter*, deliberately: ADR-0018 rejected putting an
    ``EnterpriseState`` on ``LifecycleState``, which would have put enterprise
    data inside the lifecycle snapshot.

    Attributes:
        enterprise: The state permissions are checked against.
        actor_id: The principal performing the act. Recorded on whatever record
            the act produces.
        approval_required_in: Environments in which a deployment must have a
            recorded approval from a *different* principal. Empty by default,
            because AlphaLab does not know which environments a given firm
            gates -- see the module docstring.
    """

    enterprise: EnterpriseState
    actor_id: str
    approval_required_in: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise LifecycleInputError(
                "Governance.actor_id cannot be empty. A governed act is performed by "
                "someone; an anonymous one is what this parameter exists to prevent."
            )
        if not isinstance(self.approval_required_in, frozenset):
            object.__setattr__(self, "approval_required_in", frozenset(self.approval_required_in))

    def authorize(self, permission: str) -> str:
        """Check the actor holds ``permission``, and return their id.

        Returns the id so a caller can write ``actor_id=governance.authorize(...)``
        and cannot record an actor it did not authorize.

        Raises:
            EnterpriseInputError: If the principal is unknown.
            EnterprisePermissionError: If the principal lacks ``permission``.
        """

        require_permission(self.enterprise, self.actor_id, permission)
        return self.actor_id

    def requires_approval(self, environment: str) -> bool:
        """Whether a deployment to ``environment`` must be approved first."""

        return environment in self.approval_required_in


def approvals_for(
    approvals: Iterable[ApprovalRecord], name: str, version: int, environment: str
) -> tuple[ApprovalRecord, ...]:
    """Every approval recorded for exactly this version and environment."""

    return tuple(
        record
        for record in approvals
        if record.name == name and record.version == version and record.environment == environment
    )


def approval_for(
    approvals: Iterable[ApprovalRecord],
    name: str,
    version: int,
    environment: str,
    deployer_id: str,
) -> ApprovalRecord:
    """The approval that lets ``deployer_id`` deploy this version here.

    Separation of duties: an approval granted by the deployer does not count.
    Someone who may both approve and deploy can still do both -- to two
    different versions -- which is the property this enforces and the one an
    auditor asks about.

    Raises:
        LifecycleTransitionError: If no approval exists, or every approval was
            granted by the deployer. The message distinguishes the two, because
            they call for different responses.
    """

    recorded = approvals_for(approvals, name, version, environment)
    if not recorded:
        raise LifecycleTransitionError(
            f"Deploying '{name}' version {version} to '{environment}' requires a "
            f"recorded approval and there is none. Record one with "
            f"approve_deployment before deploying."
        )

    independent = tuple(record for record in recorded if record.approver_id != deployer_id)
    if not independent:
        raise LifecycleTransitionError(
            f"Deploying '{name}' version {version} to '{environment}' requires an "
            f"approval from a principal other than the deployer, and all "
            f"{len(recorded)} recorded approval(s) were granted by "
            f"'{deployer_id}'. Separation of duties is not satisfied by approving "
            "one's own deployment."
        )
    return independent[-1]


@dataclass(frozen=True, slots=True)
class GovernedAct:
    """One governed change, flattened for reading: what, when, and who.

    A projection over records that already exist, not a fourth log. Promotions
    and deployments are kept by their own registries and keep their own shapes;
    this is the view an auditor reads them through.
    """

    action: str
    subject: str
    actor_id: str
    timestamp: float
    detail: str = ""

    @property
    def attributed(self) -> bool:
        """Whether this act records who performed it.

        ``False`` for every record written before v2.16, where ``actor_id`` is
        ``""`` -- which is the truthful reading of them, not a gap to fill in.
        """

        return bool(self.actor_id)


def governance_trail(
    promotions: Iterable[StrategyPromotionRecord],
    deployments: Iterable[DeploymentRecord],
    approvals: Iterable[ApprovalRecord],
) -> tuple[GovernedAct, ...]:
    """Every governed act, in timestamp order, as one readable sequence.

    Reads the three append-only records the lifecycle already keeps rather than
    maintaining a fourth. Ties are broken by the order the records were made, so
    two acts at the same instant read in the order they happened.
    """

    acts: list[GovernedAct] = []

    for promotion in promotions:
        acts.append(
            GovernedAct(
                action="promotion",
                subject=f"{promotion.name}@{promotion.version}",
                actor_id=promotion.actor_id,
                timestamp=promotion.timestamp,
                detail=(
                    f"{promotion.from_stage.name} -> {promotion.to_stage.name}: {promotion.reason}"
                ),
            )
        )

    for deployment in deployments:
        acts.append(
            GovernedAct(
                action="rollback" if deployment.is_rollback else "deployment",
                subject=f"{deployment.release_name}@{deployment.version}",
                actor_id=deployment.actor_id,
                timestamp=deployment.timestamp,
                detail=f"environment '{deployment.environment}'",
            )
        )

    for approval in approvals:
        acts.append(
            GovernedAct(
                action="approval",
                subject=approval.subject,
                actor_id=approval.approver_id,
                timestamp=approval.timestamp,
                detail=approval.note,
            )
        )

    return tuple(sorted(acts, key=lambda act: act.timestamp))
