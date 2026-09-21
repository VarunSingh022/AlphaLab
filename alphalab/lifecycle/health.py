"""Is this deployment still doing what it was deployed to do?

:func:`~alphalab.runtime.live.live_health` has answered a version of that
question since v2.16, and its docstring is honest about what it is: "everything
wrong with this live run right now, **in plain sentences**". Plain sentences are
the right output for a human reading a console and the wrong one for everything
else -- nothing can count them by kind, act on the severe ones, compare today's
against yesterday's, or say *what number* was wrong and *what number* would have
been acceptable. It also reads a :class:`~alphalab.runtime.live.LiveRunState`
directly, so it can only speak about a run this process is driving, and it knows
no thresholds at all because a run does not carry any.

This module is the structured half, and the two are kept apart on purpose:

========================= =======================================================
``live_health``           the live *driver* inspecting its own aggregate. No
                          thresholds, no policy, no observations from outside.
                          Unchanged.
:func:`evaluate_health`   supplied **observations** judged against the
                          **requirements** a
                          :class:`~alphalab.lifecycle.specification.DeploymentSpecification`
                          declares. Structured, severity-bearing, and usable for
                          a deployment AlphaLab is not driving.
========================= =======================================================

:func:`observation_from_live_run` is the bridge, so the two never disagree about
a run this process *is* driving: it projects the live aggregate into the
canonical observation rather than duplicating the reading.

Detection, not remediation
--------------------------

Nothing here cancels an order, disconnects an adapter, pauses a progression or
edits any state. A finding is a fact for a caller to act on under its own policy
-- the position ``ROADMAP.md`` records as "restart policy, alerting and
scheduling are an operator's concern". :func:`evaluate_health` is a pure
function of its two arguments and returns a value.

Missing observations stay missing
----------------------------------

The rule the whole module is built around: **an observation nobody supplied is
not a healthy one.** Every category that could not be evaluated is reported in
:attr:`HealthReport.unevaluated` with the reason, and a report with nothing
wrong and something unevaluated is :attr:`~HealthStatus.UNKNOWN`, never
:attr:`~HealthStatus.HEALTHY`. A missing heartbeat time does not mean the
heartbeat is fine, and the only way to make that impossible to misread is to
make it impossible to spell.

No clock
--------

:attr:`RuntimeObservation.observed_at` is the instant the evaluation is for, and
it is supplied. Nothing in this module calls ``time.time()``, so the same
observation evaluated twice, in two processes, a year apart, produces the same
report -- which is what lets a health report be stored, replayed and compared.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto

from alphalab.broker.state import ConnectionStatus
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.specification import DeploymentSpecification, RuntimeRequirements
from alphalab.lifecycle.tolerance import ToleranceOutcome
from alphalab.risk.models import RiskViolation

__all__ = [
    "ExecutionObservation",
    "HealthCategory",
    "HealthFinding",
    "HealthReport",
    "HealthSeverity",
    "HealthStatus",
    "RuntimeObservation",
    "StateExpectation",
    "UnevaluatedCategory",
    "evaluate_health",
    "observation_from_live_run",
]


class HealthCategory(Enum):
    """The seven things that can be wrong with a running deployment.

    The roadmap's list, one member each, in the order it gives them. A category
    is *always* either evaluated or explicitly reported as unevaluated, so a
    report is total over this enum and a reader never has to wonder whether a
    check ran.
    """

    #: The newest market observation is older than the freshness budget.
    STALE_DATA = auto()

    #: An execution took longer than the latency budget, or was refused.
    ABNORMAL_EXECUTION = auto()

    #: A position differs from the one expected by more than the tolerance, or
    #: exists where none was expected.
    UNEXPECTED_POSITION = auto()

    #: A risk limit was breached. The violation is the risk engine's; this
    #: carries it rather than re-deciding it.
    RISK_BREACH = auto()

    #: Nothing has been heard from the strategy for longer than the budget.
    HEARTBEAT_LOSS = auto()

    #: The broker connection is not in a state that can carry an order.
    BROKER_DISCONNECT = auto()

    #: A named fact about the runtime is not what it was expected to be.
    EXPECTED_STATE_DIVERGENCE = auto()


class HealthSeverity(Enum):
    """How bad a finding is. Two levels, because there are two responses.

    ``WARNING`` is "this is not right and trading can continue" -- a reconnecting
    adapter, a position a hair outside tolerance. ``BREACH`` is "something a
    caller said must hold does not" -- a stale feed, a risk limit, a dead
    connection. Nothing here decides what to do about either.
    """

    WARNING = auto()
    BREACH = auto()


class HealthStatus(Enum):
    """The report's overall answer, and the reason there are four.

    ``UNKNOWN`` is the member that makes this honest. A report with no findings
    and an unevaluated category has not established that the deployment is
    healthy; it has established that nobody looked. Collapsing that into
    ``HEALTHY`` is precisely the missing-data-as-success reading this release
    exists to make unspellable.
    """

    HEALTHY = auto()
    UNKNOWN = auto()
    DEGRADED = auto()
    BREACHED = auto()


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """One execution as it was seen, with its latency derived and not asserted.

    Attributes:
        order_id: Which order. AlphaLab's own identifier, rendered -- the
            ``oms_order_id``, not a venue handle, because the venue handle means
            nothing to the rest of a health report.
        submitted_at: When AlphaLab sent it, or ``None`` when that was not
            recorded.
        executed_at: When the venue reported it done, or ``None`` when it has
            not been.
        rejected: Whether the venue refused it. A refusal is a decision the venue
            made, which is a fact and not a failure -- but an unexpected one is
            abnormal execution, which is what this records.
        detail: Whatever the observer wants to carry forward, verbatim.

    ``latency_seconds`` is a property, derived from the two timestamps, so an
    observation can never claim a latency its own timestamps do not support. An
    execution missing either timestamp has **no** latency, reported as ``None``
    rather than as zero.
    """

    order_id: str
    submitted_at: float | None
    executed_at: float | None
    rejected: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise LifecycleInputError("ExecutionObservation.order_id cannot be empty.")

    @property
    def latency_seconds(self) -> float | None:
        """Seconds between submission and execution, or ``None`` if unknowable.

        Negative when the venue stamped the execution before AlphaLab stamped
        the submission, which is a real clock disagreement and is reported as
        such rather than clamped to zero.
        """

        if self.submitted_at is None or self.executed_at is None:
            return None
        return self.executed_at - self.submitted_at


@dataclass(frozen=True, slots=True)
class StateExpectation:
    """One named fact about the runtime, as expected and as observed.

    Deliberately a pair of optional strings rather than a typed value. What a
    caller wants to assert about a running deployment -- which strategy version
    is serving, which environment, how many orders are working, which schema a
    payload is on -- has no single type, and the alternative to a rendered pair
    is a different field per question and a release every time somebody thinks
    of another one.

    Attributes:
        name: What this is a fact about.
        expected: What it should be, or ``None`` when nothing was expected.
        observed: What it is, or ``None`` when it was not observed.

    ``None`` on either side is a distinct finding from a mismatch: "nobody said
    what this should be" and "it is not what it should be" call for different
    responses.
    """

    name: str
    expected: str | None
    observed: str | None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise LifecycleInputError("StateExpectation.name cannot be empty.")


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """What was actually seen, at one instant, by whoever was watching.

    Every field except ``observed_at`` is optional and ``None`` means **not
    observed**, never "nothing happened". An empty tuple or mapping means the
    opposite -- somebody looked and there was nothing -- and the two produce
    different reports.

    Nothing in AlphaLab fabricates one of these. A live run this process drives
    produces one through :func:`observation_from_live_run`; anything else is
    supplied by whoever is watching the deployment.

    Attributes:
        observed_at: The instant this observation is for. Supplied, because the
            evaluation must be reproducible and a wall clock is not.
        last_market_data_at: When the newest market observation was stamped.
        last_heartbeat_at: When the strategy last reported in.
        broker_connection: The normalized
            :class:`~alphalab.broker.state.ConnectionStatus`. This is how a
            broker disconnect reaches a health report: through the canonical
            connection state an adapter already reports, never by this module
            reaching a broker.
        executions: Executions observed in the window being judged.
        observed_positions: ``asset_id -> signed quantity``, as observed.
        expected_positions: ``asset_id -> signed quantity``, as AlphaLab
            expected. Both are needed to judge a position; either one missing
            leaves the category unevaluated.
        risk_violations: Violations the risk engine produced. Carried, never
            re-derived: :mod:`alphalab.risk` decides what a breach is.
        state_expectations: Named facts to check, as expected and as observed.

    Raises:
        LifecycleInputError: If ``observed_at`` is negative.
    """

    observed_at: float
    last_market_data_at: float | None = None
    last_heartbeat_at: float | None = None
    broker_connection: ConnectionStatus | None = None
    executions: tuple[ExecutionObservation, ...] | None = None
    observed_positions: Mapping[str, Decimal] | None = None
    expected_positions: Mapping[str, Decimal] | None = None
    risk_violations: tuple[RiskViolation, ...] | None = None
    state_expectations: tuple[StateExpectation, ...] | None = None

    def __post_init__(self) -> None:
        if self.observed_at < 0:
            raise LifecycleInputError(
                f"RuntimeObservation.observed_at is {self.observed_at!r}; a negative "
                "instant is not a reading of any clock AlphaLab uses."
            )


@dataclass(frozen=True, slots=True)
class HealthFinding:
    """One thing that is wrong, with the numbers that made it wrong.

    Attributes:
        category: Which of the seven.
        severity: How bad.
        subject: What it is about -- an ``order_id``, an ``asset_id``, a state
            expectation's name. ``""`` for a finding about the deployment as a
            whole, which a connection loss and a stale feed are.
        summary: One sentence, for a human.
        detail: The machine-readable half. Keys are stable per category and
            include ``observed`` and ``threshold`` wherever the finding came
            from comparing two numbers, so a reader never has to parse the
            sentence to learn what the limit was.
        observed_at: The instant the observation this came from was for.
    """

    category: HealthCategory
    severity: HealthSeverity
    subject: str
    summary: str
    observed_at: float
    detail: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UnevaluatedCategory:
    """A check that did not run, and why it could not.

    The type that keeps a missing observation visible. A category here is
    neither passing nor failing; nobody looked.
    """

    category: HealthCategory
    reason: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Everything one evaluation established, and everything it could not.

    Attributes:
        specification_id: The specification whose requirements were applied, so
            a stored report says what it was judged against.
        evaluated_at: The observation's instant.
        findings: What is wrong, in :class:`HealthCategory` declaration order and
            then by subject. Deterministic, so two reports over the same
            observation compare equal.
        unevaluated: What could not be judged, in the same order.
    """

    specification_id: str
    evaluated_at: float
    findings: tuple[HealthFinding, ...] = ()
    unevaluated: tuple[UnevaluatedCategory, ...] = ()

    @property
    def fully_evaluated(self) -> bool:
        """Whether every category was actually judged."""

        return not self.unevaluated

    @property
    def status(self) -> HealthStatus:
        """The overall answer.

        ``BREACHED`` if anything breached, else ``DEGRADED`` if anything warned,
        else ``UNKNOWN`` if anything went unevaluated, else ``HEALTHY``. A clean
        but incomplete evaluation is therefore never ``HEALTHY``, which is the
        whole reason ``UNKNOWN`` exists.
        """

        severities = {finding.severity for finding in self.findings}
        if HealthSeverity.BREACH in severities:
            return HealthStatus.BREACHED
        if HealthSeverity.WARNING in severities:
            return HealthStatus.DEGRADED
        if self.unevaluated:
            return HealthStatus.UNKNOWN
        return HealthStatus.HEALTHY

    def findings_in(self, category: HealthCategory) -> tuple[HealthFinding, ...]:
        """Every finding in one category, in report order."""

        return tuple(finding for finding in self.findings if finding.category is category)

    def was_evaluated(self, category: HealthCategory) -> bool:
        """Whether ``category`` was actually judged by this evaluation."""

        return all(entry.category is not category for entry in self.unevaluated)


# --------------------------------------------------------------------------- #
# The evaluation
# --------------------------------------------------------------------------- #

#: Connection states that warn rather than breach. A reconnecting adapter has not
#: given up, and the broker layer already draws this exact distinction: "a
#: reconnecting adapter should hold orders, a failed one should refuse them".
_DEGRADED_CONNECTIONS = (ConnectionStatus.CONNECTING, ConnectionStatus.RECONNECTING)


def _elapsed_finding(
    category: HealthCategory,
    label: str,
    last_at: float,
    budget: float,
    observation: RuntimeObservation,
) -> HealthFinding | None:
    """One age-against-a-budget check, shared by the data and heartbeat cases.

    Returns ``None`` when the age is within budget. *Exactly* at the budget is
    within it: a budget of sixty seconds that refuses a sixty-second-old reading
    is a fifty-nine-second budget, and the difference is the kind that is
    discovered in production.
    """

    elapsed = observation.observed_at - last_at
    if elapsed < 0:
        return HealthFinding(
            category=category,
            severity=HealthSeverity.BREACH,
            subject="",
            summary=(
                f"the newest {label} is stamped {-elapsed!r}s after the observation "
                "instant; the two clocks disagree, and a future-dated reading is not a "
                "fresh one."
            ),
            observed_at=observation.observed_at,
            detail={
                "last_at": repr(last_at),
                "observed_at": repr(observation.observed_at),
                "elapsed_seconds": repr(elapsed),
                "threshold_seconds": repr(budget),
            },
        )
    if elapsed <= budget:
        return None
    return HealthFinding(
        category=category,
        severity=HealthSeverity.BREACH,
        subject="",
        summary=(f"the newest {label} is {elapsed!r}s old and the budget is {budget!r}s."),
        observed_at=observation.observed_at,
        detail={
            "last_at": repr(last_at),
            "observed_at": repr(observation.observed_at),
            "elapsed_seconds": repr(elapsed),
            "threshold_seconds": repr(budget),
        },
    )


def _stale_data(
    observation: RuntimeObservation, requirements: RuntimeRequirements
) -> tuple[list[HealthFinding], str | None]:
    if observation.last_market_data_at is None:
        return [], (
            "no market-data timestamp was observed, so how old the feed is cannot be "
            "known. An absent timestamp is not a fresh one."
        )
    finding = _elapsed_finding(
        HealthCategory.STALE_DATA,
        "market observation",
        observation.last_market_data_at,
        requirements.max_data_staleness_seconds,
        observation,
    )
    return ([finding] if finding is not None else []), None


def _heartbeat(
    observation: RuntimeObservation, requirements: RuntimeRequirements
) -> tuple[list[HealthFinding], str | None]:
    if observation.last_heartbeat_at is None:
        return [], (
            "no heartbeat timestamp was observed, so whether the strategy is still "
            "reporting cannot be known. Silence and a missing reading are different "
            "facts and this is the second one."
        )
    finding = _elapsed_finding(
        HealthCategory.HEARTBEAT_LOSS,
        "strategy heartbeat",
        observation.last_heartbeat_at,
        requirements.max_heartbeat_silence_seconds,
        observation,
    )
    return ([finding] if finding is not None else []), None


def _broker_disconnect(
    observation: RuntimeObservation,
) -> tuple[list[HealthFinding], str | None]:
    status = observation.broker_connection
    if status is None:
        return [], (
            "no broker connection status was observed. A connection nobody looked at "
            "is not a connected one."
        )
    if status.can_trade:
        return [], None
    severity = HealthSeverity.WARNING if status in _DEGRADED_CONNECTIONS else HealthSeverity.BREACH
    return (
        [
            HealthFinding(
                category=HealthCategory.BROKER_DISCONNECT,
                severity=severity,
                subject="",
                summary=(f"the broker connection is {status.name}, so no order can be routed."),
                observed_at=observation.observed_at,
                detail={"observed": status.name, "threshold": ConnectionStatus.CONNECTED.name},
            )
        ],
        None,
    )


def _abnormal_execution(
    observation: RuntimeObservation, requirements: RuntimeRequirements
) -> tuple[list[HealthFinding], str | None]:
    if observation.executions is None:
        return [], (
            "no executions were observed. That is different from observing that none "
            "happened, which an empty sequence records."
        )

    budget = requirements.max_execution_latency_seconds
    findings: list[HealthFinding] = []
    for execution in sorted(observation.executions, key=lambda item: item.order_id):
        if execution.rejected:
            findings.append(
                HealthFinding(
                    category=HealthCategory.ABNORMAL_EXECUTION,
                    severity=HealthSeverity.BREACH,
                    subject=execution.order_id,
                    summary=f"order {execution.order_id} was rejected by the venue.",
                    observed_at=observation.observed_at,
                    detail={"rejected": "True", "reason": execution.detail},
                )
            )
            continue

        latency = execution.latency_seconds
        if latency is None:
            findings.append(
                HealthFinding(
                    category=HealthCategory.ABNORMAL_EXECUTION,
                    severity=HealthSeverity.WARNING,
                    subject=execution.order_id,
                    summary=(
                        f"order {execution.order_id} has no measurable latency: it "
                        "records no submission time, no execution time, or neither."
                    ),
                    observed_at=observation.observed_at,
                    detail={
                        "submitted_at": repr(execution.submitted_at),
                        "executed_at": repr(execution.executed_at),
                        "threshold_seconds": repr(budget),
                    },
                )
            )
            continue

        if latency < 0:
            findings.append(
                HealthFinding(
                    category=HealthCategory.ABNORMAL_EXECUTION,
                    severity=HealthSeverity.BREACH,
                    subject=execution.order_id,
                    summary=(
                        f"order {execution.order_id} was executed {-latency!r}s before "
                        "it was submitted; the two clocks disagree."
                    ),
                    observed_at=observation.observed_at,
                    detail={
                        "observed_seconds": repr(latency),
                        "threshold_seconds": repr(budget),
                    },
                )
            )
            continue

        if latency > budget:
            findings.append(
                HealthFinding(
                    category=HealthCategory.ABNORMAL_EXECUTION,
                    severity=HealthSeverity.BREACH,
                    subject=execution.order_id,
                    summary=(
                        f"order {execution.order_id} took {latency!r}s to execute and "
                        f"the budget is {budget!r}s."
                    ),
                    observed_at=observation.observed_at,
                    detail={
                        "observed_seconds": repr(latency),
                        "threshold_seconds": repr(budget),
                    },
                )
            )
    return findings, None


def _unexpected_position(
    observation: RuntimeObservation, requirements: RuntimeRequirements
) -> tuple[list[HealthFinding], str | None]:
    expected = observation.expected_positions
    observed = observation.observed_positions
    if expected is None or observed is None:
        which = []
        if expected is None:
            which.append("expected")
        if observed is None:
            which.append("observed")
        return [], (
            f"no {' and no '.join(which)} positions were supplied, so a position cannot "
            "be judged unexpected. One side alone says nothing about the other."
        )

    tolerance = requirements.position_tolerance
    findings: list[HealthFinding] = []
    for asset_id in sorted({*expected, *observed}):
        # A position absent from one side is a real zero on that side: a book
        # that does not hold an instrument holds none of it. That is different
        # from the whole mapping being absent, which is handled above.
        want = expected.get(asset_id, Decimal("0"))
        have = observed.get(asset_id, Decimal("0"))
        outcome = tolerance.outcome(want, have)
        if outcome is not ToleranceOutcome.MATERIAL:
            continue
        findings.append(
            HealthFinding(
                category=HealthCategory.UNEXPECTED_POSITION,
                severity=HealthSeverity.BREACH,
                subject=asset_id,
                summary=(
                    f"{asset_id} is held at {have} and was expected at {want}, which is "
                    f"outside the permitted {tolerance.allowance(want)}."
                ),
                observed_at=observation.observed_at,
                detail={
                    "expected": str(want),
                    "observed": str(have),
                    "allowance": str(tolerance.allowance(want)),
                },
            )
        )
    return findings, None


def _risk_breach(observation: RuntimeObservation) -> tuple[list[HealthFinding], str | None]:
    if observation.risk_violations is None:
        return [], (
            "no risk evaluation was observed. An unevaluated limit is not an unbreached one."
        )
    findings = [
        HealthFinding(
            category=HealthCategory.RISK_BREACH,
            severity=HealthSeverity.BREACH,
            subject=violation.rule,
            summary=(f"risk limit '{violation.rule}' is breached: {violation.description}"),
            observed_at=observation.observed_at,
            detail={
                "observed": str(violation.current_value),
                "threshold": str(violation.allowed_value),
                # The risk engine's own severity string, carried verbatim rather
                # than mapped onto HealthSeverity: it is a different vocabulary
                # owned by a different package, and translating it here would
                # invent a correspondence nobody declared.
                "risk_severity": violation.severity,
            },
        )
        for violation in sorted(observation.risk_violations, key=lambda item: item.rule)
    ]
    return findings, None


def _state_divergence(
    observation: RuntimeObservation,
) -> tuple[list[HealthFinding], str | None]:
    expectations = observation.state_expectations
    if expectations is None:
        return [], (
            "no state expectations were supplied, so nothing could be checked against "
            "what the deployment should look like."
        )

    findings: list[HealthFinding] = []
    for expectation in sorted(expectations, key=lambda item: item.name):
        if expectation.expected is None and expectation.observed is None:
            findings.append(
                HealthFinding(
                    category=HealthCategory.EXPECTED_STATE_DIVERGENCE,
                    severity=HealthSeverity.WARNING,
                    subject=expectation.name,
                    summary=(
                        f"'{expectation.name}' was neither expected nor observed, so "
                        "listing it established nothing."
                    ),
                    observed_at=observation.observed_at,
                    detail={"expected": "", "observed": ""},
                )
            )
            continue
        if expectation.expected is None:
            findings.append(
                HealthFinding(
                    category=HealthCategory.EXPECTED_STATE_DIVERGENCE,
                    severity=HealthSeverity.WARNING,
                    subject=expectation.name,
                    summary=(
                        f"'{expectation.name}' is {expectation.observed!r} and nothing "
                        "said what it should be."
                    ),
                    observed_at=observation.observed_at,
                    detail={"observed": str(expectation.observed)},
                )
            )
            continue
        if expectation.observed is None:
            findings.append(
                HealthFinding(
                    category=HealthCategory.EXPECTED_STATE_DIVERGENCE,
                    severity=HealthSeverity.BREACH,
                    subject=expectation.name,
                    summary=(
                        f"'{expectation.name}' should be {expectation.expected!r} and "
                        "was not observed at all."
                    ),
                    observed_at=observation.observed_at,
                    detail={"expected": str(expectation.expected)},
                )
            )
            continue
        if expectation.expected != expectation.observed:
            findings.append(
                HealthFinding(
                    category=HealthCategory.EXPECTED_STATE_DIVERGENCE,
                    severity=HealthSeverity.BREACH,
                    subject=expectation.name,
                    summary=(
                        f"'{expectation.name}' is {expectation.observed!r} and should be "
                        f"{expectation.expected!r}."
                    ),
                    observed_at=observation.observed_at,
                    detail={
                        "expected": expectation.expected,
                        "observed": expectation.observed,
                    },
                )
            )
    return findings, None


def evaluate_health(
    specification: DeploymentSpecification, observation: RuntimeObservation
) -> HealthReport:
    """Judge one observation against one specification's runtime requirements.

    Pure, total and deterministic: every :class:`HealthCategory` is either
    evaluated or reported as unevaluated, findings come out in a fixed order,
    and nothing is read that was not passed in. Two calls with equal arguments
    return equal reports, in any process.

    Nothing is changed, anywhere. See the module docstring on detection and
    remediation.
    """

    requirements = specification.runtime
    findings: list[HealthFinding] = []
    unevaluated: list[UnevaluatedCategory] = []

    evaluations: Sequence[tuple[HealthCategory, tuple[list[HealthFinding], str | None]]] = (
        (HealthCategory.STALE_DATA, _stale_data(observation, requirements)),
        (HealthCategory.ABNORMAL_EXECUTION, _abnormal_execution(observation, requirements)),
        (HealthCategory.UNEXPECTED_POSITION, _unexpected_position(observation, requirements)),
        (HealthCategory.RISK_BREACH, _risk_breach(observation)),
        (HealthCategory.HEARTBEAT_LOSS, _heartbeat(observation, requirements)),
        (HealthCategory.BROKER_DISCONNECT, _broker_disconnect(observation)),
        (HealthCategory.EXPECTED_STATE_DIVERGENCE, _state_divergence(observation)),
    )

    for category, (produced, skipped) in evaluations:
        if skipped is not None:
            unevaluated.append(UnevaluatedCategory(category, skipped))
            continue
        findings.extend(produced)

    return HealthReport(
        specification_id=specification.specification_id,
        evaluated_at=observation.observed_at,
        findings=tuple(findings),
        unevaluated=tuple(unevaluated),
    )


def observation_from_live_run(
    state: object,
    observed_at: float,
    last_market_data_at: float | None = None,
    expected_positions: Mapping[str, Decimal] | None = None,
    risk_violations: tuple[RiskViolation, ...] | None = None,
    state_expectations: tuple[StateExpectation, ...] | None = None,
    submitted_at: Mapping[str, float] | None = None,
) -> RuntimeObservation:
    """Project a :class:`~alphalab.runtime.live.LiveRunState` into an observation.

    The bridge that stops the structured evaluator and
    :func:`~alphalab.runtime.live.live_health` from disagreeing about a run this
    process *is* driving: the connection status, the heartbeat and the positions
    are **read** from the live aggregate rather than restated, so a caller cannot
    hand the evaluator a picture the run does not support.

    Four things it deliberately does **not** derive:

    * ``last_market_data_at`` -- a live run's clock and its feed's newest stamp
      are not the same reading, and the run does not carry the second one. It is
      supplied or the staleness check does not run.
    * ``expected_positions`` -- what the book *should* hold is a claim about the
      strategy's intent, and the run holds only what it does hold. Supplying only
      the observed side would make every comparison pass.
    * ``risk_violations`` -- the run carries risk *decisions* about orders it was
      asked to place, which is a different question from whether the book as it
      stands breaches a limit. Carrying the first under the name of the second
      would be a wrong answer rather than a missing one.
    * ``state_expectations`` -- an expectation is by definition something a
      caller asserts.

    ``executions`` is derived and is the empty tuple for a run that has settled
    nothing, because there the run genuinely does know that none happened. Each
    one's latency needs the instant AlphaLab *sent* the order, which a settled
    execution does not carry -- the venue stamps when it filled, not when it was
    asked -- so ``submitted_at`` supplies it and without it every execution
    reports its latency as unmeasurable rather than as zero.

    Args:
        state: A :class:`~alphalab.runtime.live.LiveRunState`. Typed as ``object``
            so that this module -- and therefore
            :class:`~alphalab.lifecycle.specification.DeploymentSpecification`,
            which every caller of :func:`evaluate_health` already holds -- does
            not import the live driver at module scope. The import happens here.
        observed_at: The instant this observation is for.
        last_market_data_at: The newest market stamp, if known.
        expected_positions: What the book should hold, if a caller can say.
        risk_violations: Violations the risk engine produced, if any were sought.
        state_expectations: Named facts to check, if any were stated.
        submitted_at: ``oms_order_id -> the instant AlphaLab sent that order``.
            Keyed by AlphaLab's identifier and not the venue handle, because
            that is the identifier the rest of a health report speaks.

    Raises:
        LifecycleInputError: If ``state`` is not a ``LiveRunState``.
    """

    from alphalab.runtime.live import LiveRunState

    if not isinstance(state, LiveRunState):
        raise LifecycleInputError(
            f"observation_from_live_run needs a LiveRunState, got "
            f"{type(state).__name__}. It reads the venue binding and the marked book "
            "off the live aggregate; anything else would have to be guessed at."
        )

    sent = dict(submitted_at) if submitted_at is not None else {}
    executions = tuple(
        ExecutionObservation(
            # AlphaLab's own order identifier where the venue binding knows it,
            # falling back to the venue handle for a fill against an order this
            # run never bound -- which is itself a reconciliation break, and is
            # reported under the identifier that exists rather than under none.
            order_id=(
                state.mapping.oms_id_for(settled.execution.broker_order_id)
                or settled.execution.broker_order_id
            ),
            submitted_at=sent.get(
                state.mapping.oms_id_for(settled.execution.broker_order_id) or ""
            ),
            executed_at=settled.execution.timestamp,
            rejected=settled.decision.is_break,
            detail=settled.decision.reason,
        )
        for settled in state.settled.to_tuple()
    )

    positions = {
        asset_id: position.quantity
        for asset_id, position in state.run.pipeline.portfolio.positions.items()
    }

    return RuntimeObservation(
        observed_at=observed_at,
        last_market_data_at=last_market_data_at,
        # 0.0 is what BrokerState carries when no heartbeat has ever arrived,
        # and "never" is not a timestamp. It becomes None so the category is
        # reported unevaluated rather than judged infinitely late.
        last_heartbeat_at=(
            state.broker.last_heartbeat if state.broker.last_heartbeat > 0.0 else None
        ),
        broker_connection=state.broker.connection_status,
        executions=executions,
        observed_positions=positions,
        expected_positions=expected_positions,
        risk_violations=risk_violations,
        state_expectations=state_expectations,
    )
