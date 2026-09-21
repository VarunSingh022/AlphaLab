"""What a deployment needs in order to be the thing that was researched.

:mod:`alphalab.lifecycle.deployment` records *that* an environment should be
running a strategy version. It has never recorded what that version needs to be
running *correctly* -- which data it assumes, how much capital, under which
risk limits, on a broker that can do what, in markets with which characteristics,
against which freshness and latency budgets. Every one of those was carried in
somebody's head, or in a release manifest's flat ``config`` mapping of strings,
which is the shape this module replaces with typed contracts.

Four things that are not the same thing
----------------------------------------

The whole design rests on keeping these apart, because collapsing any two is how
a deployment comes to claim a property it never had:

=========================== ==================================================
**Research assumption**     What the measurement was taken over. A
                            :class:`DatasetAssumption` names a *derived dataset
                            version*, so it identifies exact bytes under an
                            exact configuration.
**Deployment requirement**  What the environment must supply for the strategy
                            to run as researched. :class:`BrokerRequirements`,
                            :class:`MarketRequirements`,
                            :class:`RuntimeRequirements`, the risk limits and
                            the capital policy.
**Runtime observation**     What was actually seen. Owned by
                            :mod:`alphalab.lifecycle.health`, never by this
                            module.
**Current runtime state**   What is running now. Owned by the execution path
                            and the deployment ledger, never by this module.
=========================== ==================================================

A specification is entirely the first two. It observes nothing, reaches nothing
and starts nothing.

Identity
--------

:func:`specification_id_for` is a SHA-256 digest over a canonical rendering, the
fourth use in this repository of the construction
:func:`~alphalab.lifecycle.evidence.evidence_id_for`,
:func:`~alphalab.deployment_manager.packaging.compute_checksum` and
:func:`~alphalab.data.provenance.derive_dataset_version` already share --
``label=value`` lines joined with newlines, open key sets sorted, closed field
sets in a fixed order, and every number rendered with ``repr`` so it round-trips
exactly. Two identically-specified deployments identify themselves identically
in any process on any machine, and editing a specification changes its id.

:data:`DEPLOYMENT_SPECIFICATION_SCHEME` tags the rendering, exactly as
``DATASET_KEY_SCHEME`` and ``INSTRUMENT_KEY_SCHEME`` tag theirs: a release that
changes how specifications are identified changes the tag deliberately, and
every existing id stays recognisable under the old one. ``alphalab.__version__``
is deliberately not in the digest, for the reason
:mod:`alphalab.data.provenance` gives at length.

Reuse, and what is deliberately not re-modelled
------------------------------------------------

:class:`~alphalab.risk.limits.RiskLimits` is *the* risk policy and is carried
whole rather than restated -- the pre-trade gate reads that type, so a
specification that described limits in its own shape would be describing
something the gate does not enforce. Parameters are
:attr:`~alphalab.studio.strategy.StrategyDefinition.parameters`, read from the
registered version by :func:`specification_for_version` rather than typed again.
Dataset assumptions are the derived version string that already flows through
``RunState.source_id`` and ``ValidationEvidence.dataset_id``.

What broker requirements are
-----------------------------

Capabilities, never a vendor. :class:`BrokerRequirements` says the strategy needs
stop orders, immediate-or-cancel, short selling and equities; it does not say
which broker, and :class:`BrokerCapabilities` is a declaration an application
fills in for whichever broker it has. AlphaLab holds no adapter, no credential
and no client, and this module does not change that.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from alphalab.core.enums import AssetType, OrderType, TimeInForce
from alphalab.data.dataset import Dataset
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.lifecycle.identity import StrategyVersionRef
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.lifecycle.tolerance import Tolerance
from alphalab.risk.limits import RiskLimits

__all__ = [
    "DEPLOYMENT_SPECIFICATION_SCHEME",
    "BrokerCapabilities",
    "BrokerRequirements",
    "CapitalPolicy",
    "DatasetAssumption",
    "DeploymentSpecification",
    "MarketAvailability",
    "MarketRequirements",
    "RuntimeRequirements",
    "build_specification",
    "dataset_assumption_from",
    "specification_for_version",
    "specification_id_for",
    "unmet_broker_requirements",
    "unmet_market_requirements",
    "validate_specification",
    "verify_specification_id",
]

#: Scheme tag, and the first line of every canonical specification rendering.
#:
#: Frozen for the life of the scheme. Changing it changes every specification id
#: in existence and requires an ADR, exactly as ``DATASET_KEY_SCHEME`` does.
DEPLOYMENT_SPECIFICATION_SCHEME: Final = "alphalab.deployment_specification.v1"


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise LifecycleInputError(f"{field} cannot be empty.")


def _require_distinct(values: Sequence[str], field: str) -> None:
    if len(set(values)) != len(values):
        raise LifecycleInputError(
            f"{field} lists a value twice: {list(values)}. A requirement declared "
            "twice is either a typo or two different requirements sharing a name, and "
            "neither is resolved by silently collapsing them."
        )


def _require_non_empty(values: Sequence[str], field: str) -> None:
    if not values:
        raise LifecycleInputError(
            f"{field} is empty. A deployment that cannot say what it needs cannot be "
            "checked against an environment, and an empty requirement reads as "
            "'anything will do' rather than as 'nobody stated it'."
        )
    for value in values:
        _require_text(value, f"an entry of {field}")
    _require_distinct(values, field)


# --------------------------------------------------------------------------- #
# Research assumptions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DatasetAssumption:
    """One dataset a deployment assumes, named by its derived identity.

    Attributes:
        role: What this dataset is *for* in the strategy -- ``"prices"``,
            ``"signals"``, ``"benchmark"``. Two datasets in one specification
            must play different roles, because "which of these two is the price
            series?" is not a question a deployment should have to guess at.
        dataset_version: The derived, content-addressed identity from
            :func:`~alphalab.data.provenance.derive_dataset_version`. Spelled
            exactly as :attr:`~alphalab.lifecycle.evidence.ValidationEvidence.dataset_id`
            and :attr:`~alphalab.backtesting.state.BacktestResult.dataset_id`
            spell it, so a specification, the evidence behind it and the run that
            produced that evidence all name the same bytes with the same string.

    There is deliberately no free-text dataset *name* field. A name is not an
    identity, and carrying both invites a specification whose two fields point
    at different data.

    Raises:
        LifecycleInputError: If either field is blank.
    """

    role: str
    dataset_version: str

    def __post_init__(self) -> None:
        _require_text(self.role, "DatasetAssumption.role")
        _require_text(self.dataset_version, "DatasetAssumption.dataset_version")


def dataset_assumption_from(dataset: Dataset, role: str) -> DatasetAssumption:
    """Read an assumption off a canonical dataset, refusing one with no lineage.

    Goes through :meth:`~alphalab.data.dataset.Dataset.require_provenance`, so a
    dataset assembled from rows already in memory is refused rather than recorded
    with an invented identity. That is the same refusal
    :func:`~alphalab.lifecycle.evidence.evidence_from_study` performs, and for
    the same reason: an unverifiable dataset must never be able to look like a
    verified one.

    Raises:
        DataValidationError: If ``dataset`` carries no provenance.
        LifecycleInputError: If ``role`` is blank.
    """

    provenance = dataset.require_provenance()
    return DatasetAssumption(role=role, dataset_version=provenance.dataset_version)


# --------------------------------------------------------------------------- #
# Deployment requirements
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CapitalPolicy:
    """How much money a deployment is given, in what, and where it is held.

    Capital, deliberately not limits. What the strategy is *allowed to do* with
    the money is :class:`~alphalab.risk.limits.RiskLimits`, which the pre-trade
    gate enforces; this is what money there is. Keeping them apart is why a
    leverage cap appears once in this specification rather than twice.

    Attributes:
        account_id: The :class:`~alphalab.portfolio.account.Account` this
            deployment draws on.
        base_currency: The currency the deployment is *reported* in. ADR-0019's
            reporting role, and no default: a wrong one here produces a number,
            not a refusal, which is exactly the shape
            ``test_no_silent_financial_defaults.py`` sweeps for.
        allocated_capital: How much, in ``base_currency``. Must be positive -- a
            deployment funded with nothing cannot trade, and recording it as a
            deployment says it can.
        settlement_currencies: Every currency this deployment is permitted to
            settle in. ADR-0019's settlement role, which is a different question
            from what it reports in; see :mod:`alphalab.portfolio.amounts`.

    Raises:
        LifecycleInputError: If a name is blank, the capital is not positive, or
            the settlement currencies are empty or repeat one.
    """

    account_id: str
    base_currency: str
    allocated_capital: Decimal
    settlement_currencies: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.account_id, "CapitalPolicy.account_id")
        _require_text(self.base_currency, "CapitalPolicy.base_currency")
        if self.allocated_capital <= Decimal("0"):
            raise LifecycleInputError(
                f"CapitalPolicy.allocated_capital is {self.allocated_capital}; a "
                "deployment funded with nothing cannot trade, and recording one says "
                "it can."
            )
        _require_non_empty(self.settlement_currencies, "CapitalPolicy.settlement_currencies")


@dataclass(frozen=True, slots=True)
class BrokerRequirements:
    """What a broker must be able to do for this strategy to run as researched.

    A capability contract. Nothing here names a vendor, a protocol, a credential
    or an endpoint, and nothing here reaches one: AlphaLab owns the contract and
    an application owns whatever speaks it.

    Attributes:
        order_types: Every :class:`~alphalab.core.enums.OrderType` the strategy
            submits. A broker that cannot accept one of them cannot run it.
        time_in_force: Every :class:`~alphalab.core.enums.TimeInForce` it uses.
        asset_classes: Every :class:`~alphalab.core.enums.AssetType` it trades.
        short_selling: Whether it takes short positions.
        fractional_quantities: Whether it submits quantities that are not whole
            units. A broker that rounds them changes the strategy's sizing, so
            this is a requirement rather than a preference.

    Raises:
        LifecycleInputError: If any of the three sets is empty. A strategy that
            submits no order type does not trade.
    """

    order_types: frozenset[OrderType]
    time_in_force: frozenset[TimeInForce]
    asset_classes: frozenset[AssetType]
    short_selling: bool
    fractional_quantities: bool

    def __post_init__(self) -> None:
        for field, values in (
            ("order_types", self.order_types),
            ("time_in_force", self.time_in_force),
            ("asset_classes", self.asset_classes),
        ):
            if not values:
                raise LifecycleInputError(
                    f"BrokerRequirements.{field} is empty; a strategy that requires none "
                    "of them does not trade."
                )


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    """What a broker declares it can do. Supplied, never discovered.

    The counterpart to :class:`BrokerRequirements`, and the whole of AlphaLab's
    knowledge about any broker's abilities. AlphaLab queries nothing to build
    one: an application that has a broker adapter knows what its venue supports
    and declares it here, which is the same division
    :class:`~alphalab.crypto.venue.VenueSpecification` draws for exchange
    specifications and :class:`~alphalab.conventions.market.MarketConvention`
    draws for tick and lot tables.
    """

    order_types: frozenset[OrderType]
    time_in_force: frozenset[TimeInForce]
    asset_classes: frozenset[AssetType]
    short_selling: bool
    fractional_quantities: bool


def unmet_broker_requirements(
    requirements: BrokerRequirements, capabilities: BrokerCapabilities
) -> tuple[str, ...]:
    """Everything ``requirements`` asks for that ``capabilities`` does not offer.

    Empty means the broker can run the deployment. Every gap is reported, not
    just the first, so one call says what would have to change.
    """

    gaps: list[str] = []
    for field, wanted, offered in (
        ("order types", requirements.order_types, capabilities.order_types),
        ("time-in-force policies", requirements.time_in_force, capabilities.time_in_force),
        ("asset classes", requirements.asset_classes, capabilities.asset_classes),
    ):
        missing = sorted(str(value) for value in wanted - offered)
        if missing:
            gaps.append(f"the broker does not support the required {field}: {missing}.")

    if requirements.short_selling and not capabilities.short_selling:
        gaps.append("the strategy takes short positions and the broker does not permit them.")
    if requirements.fractional_quantities and not capabilities.fractional_quantities:
        gaps.append(
            "the strategy submits fractional quantities and the broker does not accept "
            "them; rounding them would change its sizing."
        )
    return tuple(gaps)


@dataclass(frozen=True, slots=True)
class MarketRequirements:
    """The market characteristics a deployment needs to exist.

    Attributes:
        instruments: The ``asset_id`` of every instrument the strategy trades.
            Derived identities from
            :func:`~alphalab.instrument.identity.derive_asset_id`, not venue
            symbols -- a venue symbol is what a broker calls something, and two
            venues call one instrument two things.
        venues: Where those instruments are listed. The *listing* venue sense
            ``test_venue_concepts_stay_distinct.py`` pins and
            :attr:`~alphalab.conventions.market.MarketConvention.venue` carries.
        calendar_ids: The :class:`~alphalab.data.calendar.MarketCalendar` ids
            whose sessions those instruments trade in. Named, never carried:
            AlphaLab ships no holiday data.
        quote_currencies: What prices are quoted in. ADR-0019's quote role, and
            not necessarily what anything settles in -- that is
            :attr:`CapitalPolicy.settlement_currencies`.

    Raises:
        LifecycleInputError: If any field is empty or repeats a value. An empty
            requirement reads as "anything will do"; nobody means that.
    """

    instruments: tuple[str, ...]
    venues: tuple[str, ...]
    calendar_ids: tuple[str, ...]
    quote_currencies: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.instruments, "MarketRequirements.instruments")
        _require_non_empty(self.venues, "MarketRequirements.venues")
        _require_non_empty(self.calendar_ids, "MarketRequirements.calendar_ids")
        _require_non_empty(self.quote_currencies, "MarketRequirements.quote_currencies")


@dataclass(frozen=True, slots=True)
class MarketAvailability:
    """What an environment declares it actually has. Supplied, never discovered."""

    instruments: frozenset[str]
    venues: frozenset[str]
    calendar_ids: frozenset[str]
    quote_currencies: frozenset[str]


def unmet_market_requirements(
    requirements: MarketRequirements, availability: MarketAvailability
) -> tuple[str, ...]:
    """Everything ``requirements`` needs that ``availability`` does not have."""

    gaps: list[str] = []
    for field, wanted, offered in (
        ("instruments", requirements.instruments, availability.instruments),
        ("venues", requirements.venues, availability.venues),
        ("market calendars", requirements.calendar_ids, availability.calendar_ids),
        ("quote currencies", requirements.quote_currencies, availability.quote_currencies),
    ):
        missing = sorted(set(wanted) - offered)
        if missing:
            gaps.append(f"the environment does not provide the required {field}: {missing}.")
    return tuple(gaps)


@dataclass(frozen=True, slots=True)
class RuntimeRequirements:
    """The operational budgets a running deployment is held to.

    Every threshold is **seconds**, stated in the field name, and every one is
    required. There is no default freshness budget for the same reason there is
    no default FX rate: a market-data feed that may be a minute stale is normal
    for a daily strategy and a failure for an intraday one, so a number chosen
    here would be an invented policy presented as an architectural one.

    Attributes:
        max_data_staleness_seconds: How long the newest market observation may
            be older than the moment of evaluation. At exactly this age the data
            is still acceptable; beyond it, it is stale.
        max_heartbeat_silence_seconds: How long since the last heartbeat before
            the strategy is considered to have stopped reporting.
        max_execution_latency_seconds: How long an execution may take before it
            is abnormal.
        position_tolerance: How far an observed position may sit from the
            expected one before it is an unexpected position. A
            :class:`~alphalab.lifecycle.tolerance.Tolerance` over quantities,
            because a quantity is a ``Decimal`` and a duration is not.

    Raises:
        LifecycleInputError: If any budget is negative. Zero is permitted and
            means "no staleness at all is acceptable", which is a real, if
            demanding, policy.
    """

    max_data_staleness_seconds: float
    max_heartbeat_silence_seconds: float
    max_execution_latency_seconds: float
    position_tolerance: Tolerance

    def __post_init__(self) -> None:
        for field, value in (
            ("max_data_staleness_seconds", self.max_data_staleness_seconds),
            ("max_heartbeat_silence_seconds", self.max_heartbeat_silence_seconds),
            ("max_execution_latency_seconds", self.max_execution_latency_seconds),
        ):
            if value < 0:
                raise LifecycleInputError(
                    f"RuntimeRequirements.{field} is {value!r}; a negative budget would "
                    "make every observation late."
                )


# --------------------------------------------------------------------------- #
# The specification
# --------------------------------------------------------------------------- #


def _rendered_risk(risk: RiskLimits) -> list[str]:
    """The risk limits in a fixed field order, every number rendered exactly."""

    return [
        f"order_size.max_quantity={risk.order_size.max_quantity!r}",
        f"order_size.max_notional={risk.order_size.max_notional!r}",
        f"position.max_quantity={risk.position.max_quantity!r}",
        f"position.max_notional={risk.position.max_notional!r}",
        f"exposure.max_gross_exposure={risk.exposure.max_gross_exposure!r}",
        f"exposure.max_net_exposure={risk.exposure.max_net_exposure!r}",
        f"leverage.max_leverage={risk.leverage.max_leverage!r}",
        f"margin.max_margin_utilization={risk.margin.max_margin_utilization!r}",
        f"daily_loss.max_daily_loss={risk.daily_loss.max_daily_loss!r}",
        f"drawdown.max_drawdown_pct={risk.drawdown.max_drawdown_pct!r}",
    ]


def specification_id_for(
    strategy: StrategyVersionRef,
    parameters: Mapping[str, float],
    datasets: Sequence[DatasetAssumption],
    risk: RiskLimits,
    capital: CapitalPolicy,
    broker: BrokerRequirements,
    market: MarketRequirements,
    runtime: RuntimeRequirements,
) -> str:
    """The deterministic id of a specification with this content.

    A pure function of the arguments: the same specification identifies itself
    identically in every process, on every machine, for ever. Parameter names
    and dataset roles are sorted, because a mapping's order is not part of what
    it means; closed field sets keep their declared order, because their meaning
    is positional and sorting them would hide a field being renamed.

    ``repr`` renders a :class:`~decimal.Decimal` with its exponent, so
    ``Decimal("100")`` and ``Decimal("100.00")`` produce different ids. That is
    the same property ``evidence_id_for`` has for a float and is deliberate:
    normalizing here would be this function deciding that two values a caller
    wrote differently are the same, which is a judgement about significant
    figures that only the caller can make.
    """

    canonical = "\n".join(
        [
            f"scheme={DEPLOYMENT_SPECIFICATION_SCHEME}",
            f"strategy={strategy}",
            "parameters",
            *(f"{key}={parameters[key]!r}" for key in sorted(parameters)),
            "datasets",
            *(
                f"{assumption.role}={assumption.dataset_version}"
                for assumption in sorted(datasets, key=lambda item: item.role)
            ),
            "risk",
            *_rendered_risk(risk),
            "capital",
            f"account_id={capital.account_id}",
            f"base_currency={capital.base_currency}",
            f"allocated_capital={capital.allocated_capital!r}",
            f"settlement_currencies={sorted(capital.settlement_currencies)!r}",
            "broker",
            f"order_types={sorted(value.name for value in broker.order_types)!r}",
            f"time_in_force={sorted(value.name for value in broker.time_in_force)!r}",
            f"asset_classes={sorted(value.name for value in broker.asset_classes)!r}",
            f"short_selling={broker.short_selling!r}",
            f"fractional_quantities={broker.fractional_quantities!r}",
            "market",
            f"instruments={sorted(market.instruments)!r}",
            f"venues={sorted(market.venues)!r}",
            f"calendar_ids={sorted(market.calendar_ids)!r}",
            f"quote_currencies={sorted(market.quote_currencies)!r}",
            "runtime",
            f"max_data_staleness_seconds={runtime.max_data_staleness_seconds!r}",
            f"max_heartbeat_silence_seconds={runtime.max_heartbeat_silence_seconds!r}",
            f"max_execution_latency_seconds={runtime.max_execution_latency_seconds!r}",
            f"position_tolerance.absolute={runtime.position_tolerance.absolute!r}",
            f"position_tolerance.relative={runtime.position_tolerance.relative!r}",
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeploymentSpecification:
    """Everything a strategy version needs in order to be deployed as researched.

    Immutable and self-identifying. :func:`build_specification` is how one is
    made with its id computed; constructing one by hand with an id of your own
    choosing is possible and :func:`verify_specification_id` will say it does not
    match, which is the same contract
    :class:`~alphalab.lifecycle.evidence.ValidationEvidence` has.

    Attributes:
        specification_id: The digest of this specification's own content.
        strategy: Which strategy version this is a deployment specification for.
        parameters: The strategy's parameters, spelled as
            :attr:`~alphalab.studio.strategy.StrategyDefinition.parameters`
            spells them. Read from the registered version by
            :func:`specification_for_version` rather than restated, so a
            specification cannot configure something the version does not
            declare.
        datasets: What data it assumes, by derived identity.
        risk: The pre-trade limits, carried whole.
        capital: What money it has.
        broker: What a broker must be able to do.
        market: What markets must exist.
        runtime: The operational budgets it is held to.

    Raises:
        LifecycleInputError: If the id is blank, there are no dataset
            assumptions, or two assumptions claim the same role.
    """

    specification_id: str
    strategy: StrategyVersionRef
    parameters: Mapping[str, float]
    datasets: tuple[DatasetAssumption, ...]
    risk: RiskLimits
    capital: CapitalPolicy
    broker: BrokerRequirements
    market: MarketRequirements
    runtime: RuntimeRequirements

    def __post_init__(self) -> None:
        _require_text(self.specification_id, "DeploymentSpecification.specification_id")
        if not self.datasets:
            raise LifecycleInputError(
                f"Deployment specification for {self.strategy} names no dataset. A "
                "deployment whose data assumptions nobody stated cannot be checked "
                "against the research that justified it."
            )
        _require_distinct(
            [assumption.role for assumption in self.datasets],
            "DeploymentSpecification.datasets roles",
        )

    @property
    def dataset_versions(self) -> tuple[str, ...]:
        """Every derived dataset identity this deployment assumes, in role order."""

        return tuple(
            assumption.dataset_version
            for assumption in sorted(self.datasets, key=lambda item: item.role)
        )

    def dataset_for(self, role: str) -> DatasetAssumption | None:
        """The assumption playing ``role``, or ``None`` when none does."""

        for assumption in self.datasets:
            if assumption.role == role:
                return assumption
        return None


def build_specification(
    strategy: StrategyVersionRef,
    parameters: Mapping[str, float],
    datasets: Sequence[DatasetAssumption],
    risk: RiskLimits,
    capital: CapitalPolicy,
    broker: BrokerRequirements,
    market: MarketRequirements,
    runtime: RuntimeRequirements,
) -> DeploymentSpecification:
    """Build a :class:`DeploymentSpecification` with its id computed."""

    frozen_parameters = dict(parameters)
    frozen_datasets = tuple(datasets)
    return DeploymentSpecification(
        specification_id=specification_id_for(
            strategy, frozen_parameters, frozen_datasets, risk, capital, broker, market, runtime
        ),
        strategy=strategy,
        parameters=frozen_parameters,
        datasets=frozen_datasets,
        risk=risk,
        capital=capital,
        broker=broker,
        market=market,
        runtime=runtime,
    )


def specification_for_version(
    version: StrategyVersion,
    datasets: Sequence[DatasetAssumption],
    risk: RiskLimits,
    capital: CapitalPolicy,
    broker: BrokerRequirements,
    market: MarketRequirements,
    runtime: RuntimeRequirements,
) -> DeploymentSpecification:
    """Build a specification for a registered strategy version.

    The reference and the parameters are **derived** from ``version`` rather than
    supplied, which is the lesson ADR-0017 records about
    :func:`~alphalab.lifecycle.evidence.evidence_from_backtest`: a value a caller
    types is hashed into the digest and is only as trustworthy as the typing. A
    specification built this way configures exactly what the registered version
    declares, and cannot configure a parameter that version does not have.
    """

    return build_specification(
        strategy=version.ref,
        parameters=version.definition.parameters,
        datasets=datasets,
        risk=risk,
        capital=capital,
        broker=broker,
        market=market,
        runtime=runtime,
    )


def verify_specification_id(specification: DeploymentSpecification) -> bool:
    """Whether the stored id still matches the specification's content."""

    return specification.specification_id == specification_id_for(
        specification.strategy,
        specification.parameters,
        specification.datasets,
        specification.risk,
        specification.capital,
        specification.broker,
        specification.market,
        specification.runtime,
    )


def validate_specification(specification: DeploymentSpecification) -> tuple[str, ...]:
    """Everything internally incoherent about a specification, in plain sentences.

    Empty means coherent. Each constructor already refuses a value that is wrong
    on its own; this is for the pairs of values that are each individually fine
    and cannot both be true -- an order-size cap above the position cap, a gross
    exposure limit the leverage limit can never permit, a market quoted in a
    currency the book may not settle.

    It **reports** rather than refuses, deliberately. Several of these are
    legitimate in a specification somebody is still drafting, and a constructor
    that raised would make a half-written specification unrepresentable. Nothing
    here is repaired, adjusted or defaulted.
    """

    findings: list[str] = []

    if not verify_specification_id(specification):
        findings.append(
            f"specification '{specification.specification_id}' does not match its own "
            "content; it was altered after it was built."
        )

    risk = specification.risk
    capital = specification.capital

    if risk.order_size.max_quantity > risk.position.max_quantity:
        findings.append(
            f"a single order may be {risk.order_size.max_quantity} units while a whole "
            f"position may be {risk.position.max_quantity}; the order limit can never "
            "bind before the position limit refuses the order."
        )
    if risk.order_size.max_notional > risk.position.max_notional:
        findings.append(
            f"a single order may be {risk.order_size.max_notional} notional while a "
            f"whole position may be {risk.position.max_notional}; the order limit can "
            "never bind before the position limit refuses the order."
        )
    if risk.exposure.max_net_exposure > risk.exposure.max_gross_exposure:
        findings.append(
            f"net exposure is capped at {risk.exposure.max_net_exposure} and gross at "
            f"{risk.exposure.max_gross_exposure}; net can never exceed gross, so the net "
            "cap is unreachable."
        )

    leveraged = capital.allocated_capital * risk.leverage.max_leverage
    if risk.exposure.max_gross_exposure > leveraged:
        findings.append(
            f"gross exposure is capped at {risk.exposure.max_gross_exposure}, which "
            f"{capital.allocated_capital} of capital at {risk.leverage.max_leverage}x "
            f"leverage cannot reach ({leveraged}); the leverage limit binds first and "
            "the exposure cap states a level this deployment cannot fund."
        )

    unsettleable = sorted(
        set(specification.market.quote_currencies) - set(capital.settlement_currencies)
    )
    if unsettleable:
        findings.append(
            f"prices are quoted in {unsettleable} and the deployment settles only in "
            f"{sorted(capital.settlement_currencies)}; every fill in those currencies "
            "needs an FX conversion with a supplied rate, which this specification does "
            "not provide and AlphaLab never invents."
        )

    return tuple(findings)
