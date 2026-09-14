"""The join: what an environment says should run, resolved into a run.

:mod:`alphalab.lifecycle`'s own docstring has said for four releases that "a
deployment names what should run; running it is the execution path's job, **and
the two are joined by the caller**". That sentence is the gap. Nothing checked
that a run was serving the version an environment actually had live, so:

* a run could execute a strategy version that was never promoted, or one that
  was rolled back an hour ago, and nothing anywhere would notice;
* a run could not say which deployment it was serving, so a fill could not be
  traced back to the decision that authorized it.

What this module adds, and what it refuses to add
-------------------------------------------------

It resolves a deployment into a :class:`RunPlan` -- the identity of what should
run, the evidence that justified it, and who deployed it -- and it refuses when
an environment has nothing live. That is a *query with a refusal*, not a second
runtime: it builds no state, holds none, and starts nothing.

**It does not construct the strategy.** The lifecycle records a
:class:`~alphalab.studio.strategy.StrategyDefinition` -- author metadata and
parameter bounds -- and not code. Turning that into a
:class:`~alphalab.strategy.protocol.StrategyProtocol` requires knowing which
class implements it, and inventing a registry of strategy classes here would be
a plugin system this package has no business owning.

That is unchanged in v2.17, and the registry it declined to own now exists
somewhere it belongs: :mod:`alphalab.strategy.registry`, beside the protocol
being registered. The join is therefore a **lookup with a refusal** rather than
the caller's ad-hoc knowledge::

    plan = run_plan(state, "live")
    strategy = registry.construct_from(plan.definition)

:attr:`RunPlan.definition` satisfies that registry's structural
``StrategyDeclaration`` as it stands, so nothing translates between two shapes
and this module acquires no dependency on the strategy runtime -- which
``test_the_join_builds_no_state_and_starts_nothing`` still enforces.

**It does not build a ``RunConfig``.** An account, a capital budget, risk limits
and a settlement currency are operational configuration; a deployment is a
statement about *which version* is live, and it has never carried them.
:func:`authorize_run` therefore checks a config the caller built, rather than
producing one -- which is also what lets the same check run against a config
that came from anywhere.

Why the check is a function and not a field
-------------------------------------------

``RunState`` is eight fields and ADR-0030 decision 2 fixes it there; adding a
deployment reference would move ``RUN_SNAPSHOT_SCHEMA`` and reshape the envelope
every driver shares, to serve a link only a live run has. The authorization is
therefore performed *before* a run starts and its answer -- a
:class:`RunAuthorization` -- is the caller's to record.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.lifecycle.evidence import ValidationEvidence
from alphalab.lifecycle.exceptions import LifecycleInputError, LifecycleTransitionError
from alphalab.lifecycle.identity import StrategyVersionRef
from alphalab.lifecycle.state import LifecycleState
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.lifecycle.views import active_strategy_version, evidence_for
from alphalab.model_registry.registry import ModelStage
from alphalab.studio.strategy import StrategyDefinition

__all__ = [
    "RunAuthorization",
    "RunPlan",
    "authorize_run",
    "run_plan",
]


@dataclass(frozen=True, slots=True)
class RunPlan:
    """What an environment says should be running, and what stands behind it.

    Everything here is read from the lifecycle's own records. Nothing is
    fabricated and nothing is defaulted: a field that would have to be invented
    is ``None`` instead.

    Attributes:
        environment: The environment this plan is for.
        version: The strategy version the deployment ledger names as active.
        reference: That version's canonical reference, ``name@version``.
        evidence: The validation evidence that supported its promotion, or
            ``None`` when it was promoted without any -- which the promotion
            gate allows only for a version with no model.
        deployed_by: The principal that deployed it, or ``""`` when the
            deployment predates v2.16 governance or was made through
            ``alphalab.deployment_manager`` directly.
        deployed_at: When the active deployment was recorded.
    """

    environment: str
    version: StrategyVersion
    reference: StrategyVersionRef
    evidence: ValidationEvidence | None
    deployed_by: str
    deployed_at: float

    @property
    def definition(self) -> StrategyDefinition:
        """The strategy definition to construct an instance from.

        Typed as itself since v2.17, having been ``object``. The vagueness was
        never a boundary -- this package already imports
        :class:`~alphalab.studio.strategy.StrategyDefinition` and already stores
        one on every :class:`~alphalab.lifecycle.strategy_version.StrategyVersion`
        -- and it cost the caller the only thing this property is for: a
        ``StrategyDefinition`` satisfies
        :class:`~alphalab.strategy.registry.StrategyDeclaration` structurally, so
        a typed one can be handed straight to
        :meth:`~alphalab.strategy.registry.StrategyClassRegistry.construct_from`.

        The mapping from this to a class is still **not here** -- ADR-0033
        decision 5, unchanged. What v2.17 adds is a registry that owns the
        mapping (``alphalab.strategy.registry``), so a caller performs a lookup
        that refuses rather than writing an ``if`` ladder or guessing a class
        from a name.
        """

        return self.version.definition

    @property
    def strategy_id(self) -> str:
        """The identity a strategy-class registry is keyed by.

        ``definition.strategy_id``, named here because that is the whole of what
        a caller needs to resolve this plan to executable code.
        """

        return self.version.definition.strategy_id

    @property
    def attributed(self) -> bool:
        """Whether the active deployment records who made it."""

        return bool(self.deployed_by)


@dataclass(frozen=True, slots=True)
class RunAuthorization:
    """A run checked against what its environment has live.

    Produced by :func:`authorize_run` and never by a run itself. A caller
    records it beside the run so a fill can be traced to the deployment that
    authorized it.
    """

    plan: RunPlan
    strategy_id: str

    @property
    def reference(self) -> StrategyVersionRef:
        """The strategy version this run is authorized to serve."""

        return self.plan.reference


def run_plan(state: LifecycleState, environment: str) -> RunPlan:
    """What ``environment`` says should be running.

    Raises:
        LifecycleInputError: If ``environment`` is blank, or has nothing
            deployed. "Nothing is live here" is a refusal rather than ``None``
            because every caller of this function is about to start a run, and
            a ``None`` that a caller forgets to check starts one anyway.
        LifecycleTransitionError: If what is live is not in a stage that may
            run. A version rolled back to ``ARCHIVED`` while still named by a
            stale release is the case this catches.
    """

    if not environment.strip():
        raise LifecycleInputError("environment cannot be empty.")

    version = active_strategy_version(state, environment)
    if version is None:
        raise LifecycleInputError(
            f"Environment '{environment}' has no active strategy version. Deploy one "
            "before starting a run against it."
        )

    if version.stage is not ModelStage.PRODUCTION:
        raise LifecycleTransitionError(
            f"Environment '{environment}' names {version.ref}, which is in stage "
            f"{version.stage.name}. Only a PRODUCTION version may run; the ledger and "
            "the version's stage disagree, which is a reconciliation question and not "
            "something this function will resolve by guessing."
        )

    record = state.deployments.environments.get(environment)
    latest = record[-1] if record else None

    return RunPlan(
        environment=environment,
        version=version,
        reference=version.ref,
        evidence=evidence_for(state, version.name, version.version),
        deployed_by=latest.actor_id if latest is not None else "",
        deployed_at=latest.timestamp if latest is not None else 0.0,
    )


def authorize_run(
    state: LifecycleState,
    environment: str,
    reference: StrategyVersionRef,
    strategy_id: str,
) -> RunAuthorization:
    """Check that ``reference`` is what ``environment`` has live, and say so.

    The check a live run should make before it starts. It is deliberately about
    *identity* and nothing else: whether the caller built the right account,
    budget or risk limits is not something a deployment ledger can know, and
    pretending otherwise would make this look like a validation it is not.

    Raises:
        LifecycleInputError: If the environment is blank or has nothing live.
        LifecycleTransitionError: If what is live is not ``reference``, or is
            not in a runnable stage. The message names both versions, because
            "the wrong version is running" and "nothing is running" call for
            different responses.
    """

    plan = run_plan(state, environment)
    if plan.reference != reference:
        raise LifecycleTransitionError(
            f"Environment '{environment}' has {plan.reference} live, and this run "
            f"would serve {reference}. A run that serves a version the ledger does "
            "not name is either stale or a mistake; redeploy, or start the run "
            "against what is actually deployed."
        )
    return RunAuthorization(plan=plan, strategy_id=strategy_id)
