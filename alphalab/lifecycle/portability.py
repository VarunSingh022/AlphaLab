"""Can this strategy version move to that environment without changing what it is?

AlphaLab's four environments already share one strategy code path. The parity
matrix :mod:`alphalab.runtime.session` states -- and
``tests/regression/test_environment_parity.py`` measures -- puts market events,
strategy dispatch, allocation, risk, the OMS lifecycle, portfolio accounting and
analytics on **one** path for a backtest, a replay, a paper run and a live run.
What differs is the record source, the clock, the staleness gate and the
execution venue, and a :class:`~alphalab.runtime.run.ExecutionMode` names which.

So a strategy's *quantitative logic* never has to change between environments.
What can stop it moving is everything around that logic: a broker that cannot
take its order types, a market that does not list its instruments, data that is
not there, a contract with a different multiplier, a feed too slow for its
freshness budget, an account that permits less leverage than it was researched
under. :func:`evaluate_portability` checks each of those against an environment
that *declares* what it offers, and reports what blocks the move.

Capabilities, never a vendor
----------------------------

A :class:`TargetEnvironment` is a declaration, and every capability in it is
the type that already owns the concept:
:class:`~alphalab.lifecycle.specification.BrokerCapabilities` for execution,
:class:`~alphalab.lifecycle.specification.MarketAvailability` for markets,
:class:`~alphalab.conventions.market.MarketConvention` for contract terms. The
requirements are the ones the strategy's
:class:`~alphalab.lifecycle.specification.DeploymentSpecification` already
states, checked by the functions that already check them --
:func:`~alphalab.lifecycle.specification.unmet_broker_requirements` and
:func:`~alphalab.lifecycle.specification.unmet_market_requirements`. "Broker A"
and "broker B" are two environments with two capability declarations; nothing
here names, reaches or knows a broker.

Nothing is adapted
------------------

:func:`evaluate_portability` is pure. It takes one fingerprint and one
specification and returns a report; it never returns a different fingerprint,
never substitutes a parameter, never swaps an order type for one the broker
does have. A strategy that needs stop orders and a broker that has none is
``NOT_PORTABLE`` with that stated as the blocker -- converting the stops to
limits would be a different strategy running under this one's identity, which
is the failure the fingerprint exists to make visible.

A requirement nobody can check is not a satisfied one
------------------------------------------------------

Each requirement is ``SATISFIED``, ``BLOCKED``, ``NOT_VERIFIED`` -- the
environment did not declare what the check needs -- or ``NOT_APPLICABLE`` -- the
requirement does not arise in that kind of environment, such as a freshness
budget in a backtest whose clock is its own records. An environment is
``PORTABLE`` only when nothing is blocked and nothing is unverified; anything
unverified and nothing blocked is ``INSUFFICIENT_EVIDENCE``. The v3.5 rule --
a missing observation is not a healthy one -- applied to capabilities.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Final

from alphalab.conventions.market import MarketConvention
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.fingerprint import (
    StrategyFingerprint,
    differing_parameters,
    verify_fingerprint,
)
from alphalab.lifecycle.specification import (
    BrokerCapabilities,
    DeploymentSpecification,
    MarketAvailability,
    unmet_broker_requirements,
    unmet_market_requirements,
    verify_specification_id,
)
from alphalab.runtime.run import ExecutionMode

__all__ = [
    "PORTABILITY_REPORT_SCHEME",
    "EnvironmentPortability",
    "PortabilityReport",
    "PortabilityRequirement",
    "PortabilityStatus",
    "RequirementCheck",
    "RequirementOutcome",
    "RuntimeProfile",
    "TargetEnvironment",
    "evaluate_portability",
    "verify_portability_report",
]

#: Scheme tag, and the first line of every canonical portability report key.
PORTABILITY_REPORT_SCHEME: Final = "alphalab.portability_report.v1"

#: Environments whose clock is their own records. Nothing in them is stale,
#: silent or slow, and they replay datasets rather than read a live source.
_HISTORICAL: Final = frozenset({ExecutionMode.BACKTEST, ExecutionMode.REPLAY})


class PortabilityStatus(Enum):
    """Whether a strategy version can move to one environment unchanged."""

    #: Every applicable requirement was checked and every one is met.
    PORTABLE = auto()

    #: At least one requirement is not met. The report names each.
    NOT_PORTABLE = auto()

    #: Nothing is blocked, and something could not be checked because the
    #: environment did not declare what the check needs.
    INSUFFICIENT_EVIDENCE = auto()


class PortabilityRequirement(Enum):
    """What an environment has to offer for a strategy to run there as researched."""

    #: The quantitative logic arrives unchanged: the specification configures
    #: exactly the fingerprinted version's parameters.
    STRATEGY_LOGIC = auto()

    #: Order types, time-in-force, asset classes, shorting and fractional
    #: quantities -- :class:`~alphalab.lifecycle.specification.BrokerRequirements`.
    EXECUTION = auto()

    #: Instruments, listing venues, calendars and quote currencies --
    #: :class:`~alphalab.lifecycle.specification.MarketRequirements`.
    MARKET = auto()

    #: The researched datasets, for an environment that replays history.
    DATA = auto()

    #: Contract terms -- multiplier, tick, lot, settlement and currencies -- as
    #: the strategy was researched under them.
    CONTRACTS = auto()

    #: Freshness, heartbeat and latency budgets, for an environment that runs
    #: against a moving clock.
    RUNTIME = auto()

    #: The leverage the environment permits, against the cap the strategy was
    #: researched under.
    RISK = auto()

    #: The currencies the environment's account holds and settles.
    CAPITAL = auto()


class RequirementOutcome(Enum):
    """How one requirement stands in one environment."""

    #: Checked, and met.
    SATISFIED = auto()

    #: Checked, and not met. The blockers say why.
    BLOCKED = auto()

    #: The environment did not declare what the check needs.
    NOT_VERIFIED = auto()

    #: The requirement does not arise in this kind of environment.
    NOT_APPLICABLE = auto()


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    """One requirement, in one environment.

    Attributes:
        requirement: Which requirement.
        outcome: How it stands.
        detail: What was compared, or why nothing could be.
        blockers: What prevents the move, one sentence each. Non-empty exactly
            when the outcome is ``BLOCKED``.
    """

    requirement: PortabilityRequirement
    outcome: RequirementOutcome
    detail: str
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """What an environment declares about its own runtime. Supplied, never measured here.

    Every figure is **seconds**, named in the field, and each is the environment's
    worst case -- what the strategy has to be prepared for.

    Attributes:
        data_delay_seconds: How old the freshest market observation the
            environment delivers can be.
        heartbeat_interval_seconds: The longest the environment goes between
            heartbeats.
        execution_latency_seconds: The longest an execution takes there.

    Raises:
        LifecycleInputError: If any figure is negative.
    """

    data_delay_seconds: float
    heartbeat_interval_seconds: float
    execution_latency_seconds: float

    def __post_init__(self) -> None:
        for field, value in (
            ("data_delay_seconds", self.data_delay_seconds),
            ("heartbeat_interval_seconds", self.heartbeat_interval_seconds),
            ("execution_latency_seconds", self.execution_latency_seconds),
        ):
            if value < 0:
                raise LifecycleInputError(
                    f"RuntimeProfile.{field} is {value!r}; no environment answers before it "
                    "is asked."
                )


@dataclass(frozen=True, slots=True)
class TargetEnvironment:
    """One environment a strategy could move to, as it declares itself.

    Attributes:
        name: What the environment is called -- ``"research"``, ``"paper"``,
            ``"live-broker-a"``. Distinct within one evaluation.
        mode: Which of AlphaLab's four environments it is.
        broker: What its execution can do. For a simulated environment this is
            what the simulation accepts; AlphaLab declares nothing on anyone's
            behalf, including its own simulator.
        market: What markets it provides.
        datasets: The dataset versions it can replay, or ``None`` when not
            declared. Read only by a historical environment.
        conventions: ``asset_id -> the contract terms in force there``, or
            ``None`` when not declared.
        runtime: Its runtime characteristics, or ``None`` when not declared.
            Read only by an environment with a moving clock.
        max_leverage: The most leverage its account permits, or ``None``.
        account_currencies: The currencies its account holds and settles, or
            ``None``.

    Every optional field is optional because an application may genuinely not
    know it. ``None`` makes the requirement that reads it ``NOT_VERIFIED`` --
    never satisfied.

    Raises:
        LifecycleInputError: If the name is blank or the leverage is not
            positive.
    """

    name: str
    mode: ExecutionMode
    broker: BrokerCapabilities
    market: MarketAvailability
    datasets: frozenset[str] | None = None
    conventions: Mapping[str, MarketConvention] | None = None
    runtime: RuntimeProfile | None = None
    max_leverage: Decimal | None = None
    account_currencies: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise LifecycleInputError("TargetEnvironment.name cannot be empty.")
        if self.max_leverage is not None and self.max_leverage <= Decimal("0"):
            raise LifecycleInputError(
                f"Environment {self.name!r} permits {self.max_leverage}x leverage; an account "
                "that permits no exposure at all cannot hold a position."
            )


@dataclass(frozen=True, slots=True)
class EnvironmentPortability:
    """Whether one strategy version can move to one environment, and why.

    Attributes:
        environment: The environment's name.
        mode: Its kind.
        status: The answer.
        checks: One per :class:`PortabilityRequirement`, in that order.
    """

    environment: str
    mode: ExecutionMode
    status: PortabilityStatus
    checks: tuple[RequirementCheck, ...]

    @property
    def blockers(self) -> tuple[str, ...]:
        """Everything preventing the move, requirement by requirement."""

        return tuple(
            f"{check.requirement.name}: {blocker}"
            for check in self.checks
            for blocker in check.blockers
        )

    def check(self, requirement: PortabilityRequirement) -> RequirementCheck:
        """How one requirement stands here."""

        for entry in self.checks:
            if entry.requirement is requirement:
                return entry
        raise LifecycleInputError(f"No check of {requirement.name} in {self.environment!r}.")


@dataclass(frozen=True, slots=True)
class PortabilityReport:
    """One strategy version against every environment it was evaluated for.

    Attributes:
        report_id: SHA-256 over the report's canonical rendering.
        fingerprint: The strategy version -- one, the same, for every
            environment. There is no per-environment identity to report.
        specification_id: The specification whose requirements were checked.
        environments: One result per environment, in the order given.
    """

    report_id: str
    fingerprint: str
    specification_id: str
    environments: tuple[EnvironmentPortability, ...]

    @property
    def portable_to(self) -> tuple[str, ...]:
        """The environments it can move to unchanged."""

        return tuple(
            entry.environment
            for entry in self.environments
            if entry.status is PortabilityStatus.PORTABLE
        )

    def for_environment(self, name: str) -> EnvironmentPortability:
        """The result for one environment."""

        for entry in self.environments:
            if entry.environment == name:
                return entry
        raise LifecycleInputError(f"The report evaluated no environment named {name!r}.")


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #


def _check(
    requirement: PortabilityRequirement,
    outcome: RequirementOutcome,
    detail: str,
    blockers: Sequence[str] = (),
) -> RequirementCheck:
    return RequirementCheck(requirement, outcome, detail, tuple(blockers))


def _strategy_logic(
    fingerprint: StrategyFingerprint, specification: DeploymentSpecification
) -> RequirementCheck:
    blockers: list[str] = []
    if specification.strategy.name != fingerprint.name:
        blockers.append(
            f"the specification is for {specification.strategy} and the fingerprint is of "
            f"strategy line {fingerprint.name!r}."
        )
    for key in differing_parameters(fingerprint.parameters, specification.parameters):
        declared = fingerprint.parameters.get(key)
        configured = specification.parameters.get(key)
        blockers.append(
            f"parameter {key!r} is {declared!r} in the fingerprinted version and "
            f"{configured!r} in the specification; deploying it would change the "
            "quantitative logic."
        )
    if blockers:
        return _check(
            PortabilityRequirement.STRATEGY_LOGIC,
            RequirementOutcome.BLOCKED,
            "the specification does not configure the fingerprinted version as it is.",
            blockers,
        )
    return _check(
        PortabilityRequirement.STRATEGY_LOGIC,
        RequirementOutcome.SATISFIED,
        "the specification configures exactly the fingerprinted parameters, and strategy "
        "dispatch is one code path in every execution mode, so the logic arrives unchanged.",
    )


def _execution(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    gaps = unmet_broker_requirements(specification.broker, environment.broker)
    if gaps:
        return _check(
            PortabilityRequirement.EXECUTION,
            RequirementOutcome.BLOCKED,
            "the environment's declared execution capabilities do not cover the "
            "specification's broker requirements.",
            gaps,
        )
    return _check(
        PortabilityRequirement.EXECUTION,
        RequirementOutcome.SATISFIED,
        "every order type, time-in-force, asset class, and the shorting and fractional "
        "requirements, are declared as supported.",
    )


def _market(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    gaps = unmet_market_requirements(specification.market, environment.market)
    if gaps:
        return _check(
            PortabilityRequirement.MARKET,
            RequirementOutcome.BLOCKED,
            "the environment does not provide every market the specification requires.",
            gaps,
        )
    return _check(
        PortabilityRequirement.MARKET,
        RequirementOutcome.SATISFIED,
        "every required instrument, venue, calendar and quote currency is declared available.",
    )


def _data(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    requirement = PortabilityRequirement.DATA
    if environment.mode not in _HISTORICAL:
        return _check(
            requirement,
            RequirementOutcome.NOT_APPLICABLE,
            f"a {environment.mode.name} environment reads a live source rather than replaying "
            "a dataset; the researched datasets are research assumptions, not deployment "
            "requirements (ADR-0040 decision 4), and instrument coverage is checked under "
            "MARKET.",
        )
    if environment.datasets is None:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "the environment did not declare which dataset versions it can replay.",
        )
    missing = sorted(set(specification.dataset_versions) - environment.datasets)
    if missing:
        return _check(
            requirement,
            RequirementOutcome.BLOCKED,
            "the environment cannot replay every dataset version the strategy was researched on.",
            [f"the environment cannot supply dataset {version}." for version in missing],
        )
    return _check(
        requirement,
        RequirementOutcome.SATISFIED,
        "every researched dataset version is declared replayable.",
    )


#: The convention fields compared, in the order they are reported.
_CONVENTION_FIELDS: Final = (
    "venue",
    "calendar_id",
    "quote_currency",
    "settlement_currency",
    "multiplier",
    "tick",
    "lot",
    "settlement",
)


def _contracts(
    specification: DeploymentSpecification,
    required: Mapping[str, MarketConvention] | None,
    environment: TargetEnvironment,
) -> RequirementCheck:
    requirement = PortabilityRequirement.CONTRACTS
    if not required:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "no contract terms were declared for the strategy as it was researched, so a "
            "different multiplier, tick or lot here could not be detected.",
        )
    if environment.conventions is None:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "the environment did not declare the contract terms in force there.",
        )
    blockers: list[str] = []
    for asset_id in sorted(required):
        researched = required[asset_id]
        offered = environment.conventions.get(asset_id)
        if offered is None:
            blockers.append(f"the environment declares no contract terms for {asset_id}.")
            continue
        for field in _CONVENTION_FIELDS:
            before = getattr(researched, field)
            after = getattr(offered, field)
            if before != after:
                blockers.append(
                    f"{asset_id} {field} was {before!r} as researched and is {after!r} here."
                )
    if blockers:
        return _check(
            requirement,
            RequirementOutcome.BLOCKED,
            "the environment's contract terms differ from those the strategy was researched "
            "under; the same order would carry a different notional or land on a different grid.",
            blockers,
        )
    undeclared = sorted(set(specification.market.instruments) - set(required))
    if undeclared:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            f"no contract terms were declared for {undeclared} as researched, so a different "
            "multiplier, tick or lot for them here could not be detected.",
        )
    return _check(
        requirement,
        RequirementOutcome.SATISFIED,
        f"the contract terms of all {len(required)} researched instrument(s) are identical here.",
    )


def _runtime(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    requirement = PortabilityRequirement.RUNTIME
    if environment.mode in _HISTORICAL:
        return _check(
            requirement,
            RequirementOutcome.NOT_APPLICABLE,
            f"a {environment.mode.name} environment's clock is its own records, so nothing in "
            "it is stale, silent or slow.",
        )
    profile = environment.runtime
    if profile is None:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "the environment did not declare its data delay, heartbeat interval or execution "
            "latency.",
        )
    budgets = specification.runtime
    blockers: list[str] = []
    # Exactly at a budget is within it, the rule lifecycle.health applies.
    for label, offered, budget in (
        ("data delay", profile.data_delay_seconds, budgets.max_data_staleness_seconds),
        (
            "heartbeat interval",
            profile.heartbeat_interval_seconds,
            budgets.max_heartbeat_silence_seconds,
        ),
        (
            "execution latency",
            profile.execution_latency_seconds,
            budgets.max_execution_latency_seconds,
        ),
    ):
        if offered > budget:
            blockers.append(
                f"the environment's {label} is {offered!r}s and the strategy's budget is "
                f"{budget!r}s."
            )
    if blockers:
        return _check(
            requirement,
            RequirementOutcome.BLOCKED,
            "the environment cannot meet the strategy's runtime budgets.",
            blockers,
        )
    return _check(
        requirement,
        RequirementOutcome.SATISFIED,
        "the declared data delay, heartbeat interval and execution latency are within budget.",
    )


def _risk(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    requirement = PortabilityRequirement.RISK
    if environment.max_leverage is None:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "the environment did not declare the leverage its account permits.",
        )
    cap = specification.risk.leverage.max_leverage
    if cap > environment.max_leverage:
        return _check(
            requirement,
            RequirementOutcome.BLOCKED,
            "the environment permits less leverage than the strategy was researched under.",
            (
                f"the strategy's limits allow {cap}x and the environment permits "
                f"{environment.max_leverage}x; orders the research allowed would be refused "
                "here.",
            ),
        )
    return _check(
        requirement,
        RequirementOutcome.SATISFIED,
        f"the environment permits {environment.max_leverage}x and the strategy is capped at "
        f"{cap}x.",
    )


def _capital(
    specification: DeploymentSpecification, environment: TargetEnvironment
) -> RequirementCheck:
    requirement = PortabilityRequirement.CAPITAL
    if environment.account_currencies is None:
        return _check(
            requirement,
            RequirementOutcome.NOT_VERIFIED,
            "the environment did not declare which currencies its account holds and settles.",
        )
    capital = specification.capital
    needed = {capital.base_currency, *capital.settlement_currencies}
    missing = sorted(needed - environment.account_currencies)
    if missing:
        return _check(
            requirement,
            RequirementOutcome.BLOCKED,
            "the environment's account cannot hold every currency the strategy is funded and "
            "settles in.",
            [f"the account cannot hold {currency}." for currency in missing],
        )
    return _check(
        requirement,
        RequirementOutcome.SATISFIED,
        "the account holds the base currency and every settlement currency.",
    )


def _status(checks: Sequence[RequirementCheck]) -> PortabilityStatus:
    outcomes = {check.outcome for check in checks}
    if RequirementOutcome.BLOCKED in outcomes:
        return PortabilityStatus.NOT_PORTABLE
    if RequirementOutcome.NOT_VERIFIED in outcomes:
        return PortabilityStatus.INSUFFICIENT_EVIDENCE
    return PortabilityStatus.PORTABLE


def _canonical_report_key(
    fingerprint: str, specification_id: str, environments: Sequence[EnvironmentPortability]
) -> str:
    lines = [
        PORTABILITY_REPORT_SCHEME,
        f"fingerprint={fingerprint!r}",
        f"specification={specification_id!r}",
    ]
    for entry in environments:
        lines.extend(
            [
                "environment",
                f"name={entry.environment!r}",
                f"mode={entry.mode.name}",
                f"status={entry.status.name}",
            ]
        )
        for check in entry.checks:
            lines.extend(
                [
                    f"check={check.requirement.name}",
                    f"outcome={check.outcome.name}",
                    f"detail={check.detail!r}",
                    *(f"blocker={blocker!r}" for blocker in check.blockers),
                ]
            )
    return "\n".join(lines)


def _report_id(
    fingerprint: str, specification_id: str, environments: Sequence[EnvironmentPortability]
) -> str:
    key = _canonical_report_key(fingerprint, specification_id, environments)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def evaluate_portability(
    fingerprint: StrategyFingerprint,
    specification: DeploymentSpecification,
    environments: Sequence[TargetEnvironment],
    conventions: Mapping[str, MarketConvention] | None = None,
) -> PortabilityReport:
    """Whether one strategy version can move, unchanged, to each environment.

    Pure and deterministic. The fingerprint and the specification are read and
    never replaced; the report carries the one fingerprint every environment was
    evaluated for.

    Args:
        fingerprint: The strategy version. Must verify.
        specification: Its requirements. Must verify.
        environments: Where it might move, each as it declares itself.
        conventions: ``asset_id -> the contract terms the strategy was researched
            under``, or ``None`` when nobody declared them -- in which case
            ``CONTRACTS`` is unverified everywhere, because a changed multiplier
            could not be detected. Terms declared for only some of the
            specification's instruments leave ``CONTRACTS`` unverified for the
            same reason, unless a declared one already blocks.

    Raises:
        LifecycleInputError: If the fingerprint or specification does not
            verify, no environment is given, two share a name, or a convention
            is declared for an instrument the specification does not trade.
    """

    if not verify_fingerprint(fingerprint):
        raise LifecycleInputError(
            f"Fingerprint {fingerprint.fingerprint} does not match its own content; it was "
            "altered after it was derived, so there is no identity to carry anywhere."
        )
    if not verify_specification_id(specification):
        raise LifecycleInputError(
            f"Specification {specification.specification_id} does not match its own content."
        )
    if not environments:
        raise LifecycleInputError("A portability evaluation needs at least one environment.")
    names = [environment.name for environment in environments]
    if len(set(names)) != len(names):
        raise LifecycleInputError(
            f"Two environments share a name among {names}; a report could not say which "
            "result is whose."
        )
    if conventions:
        stray = sorted(set(conventions) - set(specification.market.instruments))
        if stray:
            raise LifecycleInputError(
                f"Contract terms are declared for {stray}, which the specification does not trade."
            )

    logic = _strategy_logic(fingerprint, specification)
    results = []
    for environment in environments:
        checks = (
            logic,
            _execution(specification, environment),
            _market(specification, environment),
            _data(specification, environment),
            _contracts(specification, conventions, environment),
            _runtime(specification, environment),
            _risk(specification, environment),
            _capital(specification, environment),
        )
        results.append(
            EnvironmentPortability(
                environment=environment.name,
                mode=environment.mode,
                status=_status(checks),
                checks=checks,
            )
        )
    frozen = tuple(results)
    return PortabilityReport(
        report_id=_report_id(fingerprint.fingerprint, specification.specification_id, frozen),
        fingerprint=fingerprint.fingerprint,
        specification_id=specification.specification_id,
        environments=frozen,
    )


def verify_portability_report(report: PortabilityReport) -> bool:
    """Whether the report's id still matches its content."""

    return report.report_id == _report_id(
        report.fingerprint, report.specification_id, report.environments
    )
