"""Capital allocation: how much capital each strategy, market, broker, account and currency gets.

:class:`~alphalab.allocation.budget.CapitalBudget` is the capital a *run* may
deploy, and :class:`~alphalab.allocation.state.AllocationState` holds what that
run has committed (ADR-0015). Neither says how capital is divided *between*
runs -- between strategies, the markets they trade, the brokers and accounts
that hold the money, and the currencies it is in. This module does, as a plan
decided before anything trades, and it composes with both rather than
replacing either:

* capital already committed on the execution path is read from the one
  reservation ledger (:func:`reserved_capital`) and never re-recorded here;
* an allocation becomes an execution-path budget through
  :func:`capital_budget`, one account at a time, in that account's currency.

What a plan states, and what it decides
---------------------------------------

=========================== =============================================================
available capital           :attr:`CapitalAccount.available`, per account, in its currency
reserved capital            :attr:`CapitalAccount.reserved`: already committed, not
                            allocatable
allocation target           the :class:`FixedAmounts`, :class:`PlacementWeights` or
                            :class:`EqualWeights` rule
allocation constraints      :class:`CapitalLimit` shares per strategy, market, broker,
                            account or currency; and each account's free capital
allocated capital           :attr:`PlacementAllocation.allocated`
remaining capital           :attr:`AccountAllocation.unallocated`
allocation provenance       :attr:`CapitalAllocationResult.plan_id`, every FX conversion
                            with its rate and source, and a rule's own ``source``
=========================== =============================================================

and every account reconciles **exactly**, in its own currency::

    available == reserved + allocated + unallocated

A **placement** is where one strategy's capital sits: the strategy, the market
it trades and the account that funds it. *Broker* and *account* are
identifiers -- labels an application maps to its own adapters and credentials,
which never reach AlphaLab. Nothing here connects to, authenticates with or
names a vendor.

No silent scaling
-----------------

A request an account cannot fund is refused, whole, unless the plan states
:attr:`OversubscriptionRule.PRO_RATA` -- in which case the scale applied to that
account is recorded on the result. A limit that the allocation would breach
refuses it; limits are never met by scaling. Allocations are whole multiples of
the plan's :attr:`~CapitalAllocationPlan.granularity`, always rounded *down*, so
no rounding can allocate capital that is not there; what rounding leaves is
reported as unallocated, not redistributed by a rule nobody stated.

Money in more than one currency
-------------------------------

Capital is allocated **in the currency of the account that holds it** -- that is
settlement truth. Anything expressed against the whole plan (a target weight, a
limit share, a total) is in the plan's base currency, reached through a
:class:`CurrencyConverter`: a structural protocol that
:class:`~alphalab.portfolio.fx.FxRates` satisfies as it stands, so every rate is
the canonical authority's, refused when missing, stale or dated after the
plan's instant, and recorded. ``alphalab.allocation`` does not import
``alphalab.portfolio`` -- the separation
:meth:`~alphalab.allocation.engine.AllocationEngine.allocate` documents -- and
does not need to. Base-currency figures are translations, each rounded once by
the converter; the exact identities are the per-account ones.

Deterministic
-------------

Every quantity is ``Decimal`` in an explicit 28-digit, half-even context, never
the caller's thread context; every collection is sorted; nothing reads a clock,
a random source or the environment. The same plan and rates give the same
result, and :attr:`CapitalAllocationResult.result_id` says so.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Final, Protocol

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.exceptions import AllocationValidationError
from alphalab.allocation.state import AllocationState

__all__ = [
    "CAPITAL_ALLOCATION_SCHEME",
    "CAPITAL_PLAN_SCHEME",
    "AccountAllocation",
    "AllocationRule",
    "CapitalAccount",
    "CapitalAllocationPlan",
    "CapitalAllocationResult",
    "CapitalAllocationStatus",
    "CapitalConversion",
    "CapitalDimension",
    "CapitalLimit",
    "CapitalPlacement",
    "ConversionRecord",
    "CurrencyConverter",
    "DimensionAllocation",
    "EqualWeights",
    "FixedAmounts",
    "LimitCheck",
    "OversubscriptionRule",
    "PlacementAllocation",
    "PlacementWeights",
    "RateRecord",
    "allocate_capital",
    "capital_budget",
    "reserved_capital",
]

CAPITAL_PLAN_SCHEME: Final = "alphalab.capital_plan.v1"
CAPITAL_ALLOCATION_SCHEME: Final = "alphalab.capital_allocation.v1"

#: Every allocation computation's arithmetic: never the caller's thread context.
_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)

_ZERO: Final = Decimal(0)


def _identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AllocationValidationError(
            f"{what} must be a non-blank, unpadded string, got {value!r}."
        )
    if not value.isprintable():
        raise AllocationValidationError(f"{what} {value!r} contains a control character.")
    return value


def _amount(value: object, what: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise AllocationValidationError(
            f"{what} is {value!r}; it must be a non-negative, finite Decimal."
        )
    return value


def _share(value: object, what: str) -> Decimal:
    share = _amount(value, what)
    if share > 1:
        raise AllocationValidationError(f"{what} is {share}; a share of capital is at most one.")
    return share


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# The currency seam
# --------------------------------------------------------------------------- #


class RateRecord(Protocol):
    """The facts of one rate: what :class:`~alphalab.portfolio.fx.FxRate` carries."""

    @property
    def base(self) -> str:
        """The currency converted from."""
        ...

    @property
    def quote(self) -> str:
        """The currency converted to."""
        ...

    @property
    def rate(self) -> Decimal:
        """Units of ``quote`` per unit of ``base``."""
        ...

    @property
    def as_of(self) -> float:
        """The instant the rate was true."""
        ...

    @property
    def source(self) -> str:
        """Who quoted it, in words."""
        ...

    @property
    def derived(self) -> bool:
        """Whether it was derived (an inverse or a cross) rather than quoted."""
        ...


class ConversionRecord(Protocol):
    """One conversion: what :class:`~alphalab.portfolio.fx.FxConversion` carries."""

    @property
    def amount(self) -> Decimal:
        """The figure converted, in the rate's base currency."""
        ...

    @property
    def converted(self) -> Decimal:
        """The result, in the rate's quote currency, rounded once by the converter."""
        ...

    @property
    def rate(self) -> RateRecord:
        """The rate the conversion used."""
        ...


class CurrencyConverter(Protocol):
    """Converts an amount between two currencies at an instant, or refuses.

    :class:`~alphalab.portfolio.fx.FxRates` satisfies this as it stands. It must
    refuse -- raise -- rather than invent a rate; ``FxRates`` refuses a missing,
    stale or future-dated one.
    """

    def convert(
        self, amount: Decimal, base: str, quote: str, as_of: float | None
    ) -> ConversionRecord:
        """``amount`` of ``base`` in ``quote`` at ``as_of``, with the rate used; or raise."""
        ...


@dataclass(frozen=True, slots=True)
class CapitalConversion:
    """One conversion a capital allocation performed, as plain, serializable facts.

    Attributes:
        amount: What was converted, in ``base``.
        base: From.
        quote: To.
        rate: The rate used.
        rate_as_of: When the rate was true.
        source: Where the rate came from.
        derived: Whether the rate was derived rather than quoted.
        converted: The result, in ``quote``.
    """

    amount: Decimal
    base: str
    quote: str
    rate: Decimal
    rate_as_of: float
    source: str
    derived: bool
    converted: Decimal


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


class CapitalDimension(Enum):
    """A dimension capital is grouped and limited along."""

    STRATEGY = auto()
    MARKET = auto()
    BROKER = auto()
    ACCOUNT = auto()
    CURRENCY = auto()


@dataclass(frozen=True, slots=True)
class CapitalAccount:
    """Capital held in one account, and how much of it is already committed.

    Attributes:
        account_id: The account -- an identifier, never a credential.
        broker_id: Who holds the account, as a vendor-neutral label an
            application maps to its own adapter. AlphaLab resolves it to
            nothing.
        currency: What the account's capital is denominated in. Required.
        available: The capital the account holds for allocation, in
            ``currency``.
        reserved: What is already committed and cannot be allocated again --
            for an account on the execution path, :func:`reserved_capital` reads
            it from the reservation ledger. At most ``available``.
    """

    account_id: str
    broker_id: str
    currency: str
    available: Decimal
    reserved: Decimal

    def __post_init__(self) -> None:
        _identifier(self.account_id, "CapitalAccount.account_id")
        _identifier(self.broker_id, "CapitalAccount.broker_id")
        _identifier(self.currency, "CapitalAccount.currency")
        _amount(self.available, f"available capital of {self.account_id!r}")
        _amount(self.reserved, f"reserved capital of {self.account_id!r}")
        if self.reserved > self.available:
            raise AllocationValidationError(
                f"Account {self.account_id!r} reserves {self.reserved} of {self.available} "
                f"{self.currency}: more is committed than it holds."
            )

    @property
    def free(self) -> Decimal:
        """``available - reserved``: what may be allocated."""

        return _CONTEXT.subtract(self.available, self.reserved)


@dataclass(frozen=True, slots=True)
class CapitalPlacement:
    """Where one strategy's capital sits: the strategy, its market and the funding account.

    Attributes:
        strategy_id: The strategy.
        market: The market it trades there -- a vendor-neutral label.
        account_id: The account that funds it.
    """

    strategy_id: str
    market: str
    account_id: str

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "CapitalPlacement.strategy_id")
        _identifier(self.market, "CapitalPlacement.market")
        _identifier(self.account_id, "CapitalPlacement.account_id")

    @property
    def key(self) -> tuple[str, str, str]:
        """``(strategy, market, account)``: its sort key and identity."""

        return (self.strategy_id, self.market, self.account_id)

    def rendering(self) -> str:
        return f"{self.strategy_id!r}|{self.market!r}|{self.account_id!r}"


@dataclass(frozen=True, slots=True)
class FixedAmounts:
    """Allocate a stated amount to every placement, in its account's currency.

    Attributes:
        amounts: Placement -> amount. Every placement in the plan must be named;
            an unnamed one is refused rather than given zero.
    """

    amounts: Mapping[CapitalPlacement, Decimal]

    def __post_init__(self) -> None:
        ordered = {
            placement: _amount(amount, f"fixed amount for {placement.rendering()}")
            for placement, amount in sorted(self.amounts.items(), key=lambda item: item[0].key)
        }
        object.__setattr__(self, "amounts", MappingProxyType(ordered))

    def rendering(self) -> list[str]:
        return [
            "rule=fixed_amounts",
            *(
                f"amount[{placement.rendering()}]={amount}"
                for placement, amount in self.amounts.items()
            ),
        ]


@dataclass(frozen=True, slots=True)
class PlacementWeights:
    """Allocate fractions of the plan's total free capital, in the base currency.

    Attributes:
        weights: Placement -> fraction, each in ``[0, 1]``, summing to at most
            one exactly. Every placement in the plan must be named.
        source: Where the weights came from -- a construction result's
            identity, a committee decision. Required: a target weight with no
            provenance cannot be reproduced.
    """

    weights: Mapping[CapitalPlacement, Decimal]
    source: str

    def __post_init__(self) -> None:
        ordered = {
            placement: _share(weight, f"target weight for {placement.rendering()}")
            for placement, weight in sorted(self.weights.items(), key=lambda item: item[0].key)
        }
        total = sum(ordered.values(), _ZERO)
        if total > 1:
            raise AllocationValidationError(
                f"Placement weights sum to {total}; they are fractions of one plan's capital and "
                "cannot exceed it. Scaling them down would be a rule nobody stated."
            )
        object.__setattr__(self, "weights", MappingProxyType(ordered))
        _identifier(self.source, "PlacementWeights.source")

    def rendering(self) -> list[str]:
        return [
            "rule=placement_weights",
            f"source={self.source!r}",
            *(
                f"weight[{placement.rendering()}]={weight}"
                for placement, weight in self.weights.items()
            ),
        ]


@dataclass(frozen=True, slots=True)
class EqualWeights:
    """Allocate ``fraction`` of the plan's total free capital equally over every placement.

    Attributes:
        fraction: The share of total free capital distributed, in ``(0, 1]``.
            Stated, because "equal" does not say how much of the capital.
    """

    fraction: Decimal

    def __post_init__(self) -> None:
        if _share(self.fraction, "EqualWeights.fraction") == 0:
            raise AllocationValidationError(
                "EqualWeights.fraction is zero: it would allocate nothing."
            )

    def rendering(self) -> list[str]:
        return ["rule=equal_weights", f"fraction={self.fraction}"]


type AllocationRule = FixedAmounts | PlacementWeights | EqualWeights


class OversubscriptionRule(Enum):
    """What happens when placements ask an account for more than it can fund."""

    #: The whole allocation is refused, naming the account and the shortfall.
    REFUSE = auto()

    #: Every request on that account is scaled by ``free / requested``, and the
    #: scale is recorded on the account's allocation.
    PRO_RATA = auto()


@dataclass(frozen=True, slots=True)
class CapitalLimit:
    """A limit on one bucket's share of the plan's total free capital.

    Attributes:
        dimension: Which grouping.
        bucket: The label -- a strategy, market, broker, account or currency.
        maximum_share: The largest share allowed, or ``None``.
        minimum_share: The smallest share required, or ``None``.
    """

    dimension: CapitalDimension
    bucket: str
    maximum_share: Decimal | None
    minimum_share: Decimal | None

    def __post_init__(self) -> None:
        _identifier(self.bucket, "CapitalLimit.bucket")
        if self.maximum_share is None and self.minimum_share is None:
            raise AllocationValidationError(
                f"The limit on {self.dimension.name} {self.bucket!r} states neither end."
            )
        if self.maximum_share is not None:
            _share(self.maximum_share, "CapitalLimit.maximum_share")
        if self.minimum_share is not None:
            _share(self.minimum_share, "CapitalLimit.minimum_share")
        if (
            self.maximum_share is not None
            and self.minimum_share is not None
            and self.minimum_share > self.maximum_share
        ):
            raise AllocationValidationError(
                f"The limit on {self.dimension.name} {self.bucket!r} has its minimum above its "
                "maximum."
            )

    @property
    def key(self) -> tuple[str, str]:
        """``(dimension, bucket)``: what makes two limits the same limit."""

        return (self.dimension.name, self.bucket)

    def rendering(self) -> str:
        return (
            f"limit={self.dimension.name}|{self.bucket!r}|max={self.maximum_share}|"
            f"min={self.minimum_share}"
        )


@dataclass(frozen=True, slots=True)
class CapitalAllocationPlan:
    """Everything a capital allocation is decided by.

    Attributes:
        name: What the plan is called.
        base_currency: What plan-wide figures -- totals, target weights, limit
            shares -- are expressed in.
        as_of: The instant every rate is read at. A rate dated after it, or
            older than the converter tolerates, is refused.
        accounts: The accounts, held sorted by id.
        placements: The placements, held sorted; each names a known account.
        rule: How capital is divided.
        limits: Share limits, held sorted.
        oversubscription: What happens when an account cannot fund its
            placements. Required: refusing and scaling are different policies.
        granularity: The unit every allocation is a whole multiple of, in each
            account's currency (``Decimal("0.01")`` for cents,
            ``Decimal("1000")`` for thousands). Positive. Allocations are
            rounded down to it.
    """

    name: str
    base_currency: str
    as_of: float
    accounts: tuple[CapitalAccount, ...]
    placements: tuple[CapitalPlacement, ...]
    rule: AllocationRule
    limits: tuple[CapitalLimit, ...]
    oversubscription: OversubscriptionRule
    granularity: Decimal

    def __post_init__(self) -> None:
        _identifier(self.name, "CapitalAllocationPlan.name")
        _identifier(self.base_currency, "CapitalAllocationPlan.base_currency")
        if isinstance(self.as_of, bool) or not isinstance(self.as_of, int | float):
            raise AllocationValidationError(f"as_of is {self.as_of!r}; it must be a timestamp.")
        if self.as_of != self.as_of or self.as_of in (float("inf"), float("-inf")):
            raise AllocationValidationError("as_of must be finite.")
        object.__setattr__(self, "as_of", float(self.as_of))
        accounts = tuple(sorted(self.accounts, key=lambda account: account.account_id))
        ids = [account.account_id for account in accounts]
        if len(set(ids)) != len(ids):
            raise AllocationValidationError(f"An account is listed twice in {ids}.")
        if not accounts:
            raise AllocationValidationError("A capital plan needs at least one account.")
        placements = tuple(sorted(self.placements, key=lambda placement: placement.key))
        keys = [placement.key for placement in placements]
        if len(set(keys)) != len(keys):
            raise AllocationValidationError("A placement is listed twice.")
        if not placements:
            raise AllocationValidationError("A capital plan needs at least one placement.")
        unknown = sorted({placement.account_id for placement in placements} - set(ids))
        if unknown:
            raise AllocationValidationError(
                f"Placements name accounts the plan does not hold: {unknown}."
            )
        if isinstance(self.rule, FixedAmounts | PlacementWeights):
            named = set(
                self.rule.amounts if isinstance(self.rule, FixedAmounts) else self.rule.weights
            )
            if named != set(placements):
                missing = sorted(placement.rendering() for placement in set(placements) - named)
                extra = sorted(placement.rendering() for placement in named - set(placements))
                raise AllocationValidationError(
                    f"The rule must name exactly the plan's placements: missing {missing}, "
                    f"unknown {extra}. An unnamed placement is not given zero."
                )
        limits = tuple(sorted(self.limits, key=lambda limit: limit.key))
        limit_keys = [limit.key for limit in limits]
        if len(set(limit_keys)) != len(limit_keys):
            raise AllocationValidationError("The same limit is stated twice.")
        if (
            not isinstance(self.granularity, Decimal)
            or not self.granularity.is_finite()
            or (self.granularity <= 0)
        ):
            raise AllocationValidationError(
                f"granularity is {self.granularity!r}; it must be a positive Decimal."
            )
        object.__setattr__(self, "accounts", accounts)
        object.__setattr__(self, "placements", placements)
        object.__setattr__(self, "limits", limits)

    def account(self, account_id: str) -> CapitalAccount:
        """One account by id."""

        for account in self.accounts:
            if account.account_id == account_id:
                return account
        raise AllocationValidationError(f"The plan holds no account {account_id!r}.")

    @property
    def plan_id(self) -> str:
        """The derived identity of this plan."""

        return _digest(
            [
                CAPITAL_PLAN_SCHEME,
                f"name={self.name!r}",
                f"base={self.base_currency!r}",
                f"as_of={self.as_of!r}",
                f"oversubscription={self.oversubscription.name}",
                f"granularity={self.granularity}",
                *(
                    f"account={account.account_id!r}|{account.broker_id!r}|{account.currency!r}|"
                    f"{account.available}|{account.reserved}"
                    for account in self.accounts
                ),
                *(f"placement={placement.rendering()}" for placement in self.placements),
                *self.rule.rendering(),
                *(limit.rendering() for limit in self.limits),
            ]
        )


# --------------------------------------------------------------------------- #
# The result
# --------------------------------------------------------------------------- #


class CapitalAllocationStatus(Enum):
    """``ALLOCATED``, or ``REFUSED`` with the reasons -- never a quietly smaller allocation."""

    ALLOCATED = auto()
    REFUSED = auto()


@dataclass(frozen=True, slots=True)
class PlacementAllocation:
    """What one placement asked for and received.

    Attributes:
        placement: Where.
        currency: The funding account's currency, which every amount here but
            :attr:`base_amount` is in.
        requested: What the rule asked for.
        allocated: What it received: the request, scaled if its account was
            oversubscribed under ``PRO_RATA``, rounded down to the granularity.
        base_amount: :attr:`allocated`, in the base currency.
        conversion: The conversion behind :attr:`base_amount`, or ``None`` when
            the account is in the base currency or nothing was allocated.
    """

    placement: CapitalPlacement
    currency: str
    requested: Decimal
    allocated: Decimal
    base_amount: Decimal
    conversion: CapitalConversion | None


@dataclass(frozen=True, slots=True)
class AccountAllocation:
    """One account, reconciled in its own currency.

    ``account.available == account.reserved + allocated + unallocated``, exactly.

    Attributes:
        account: The account as the plan stated it.
        requested: The sum of its placements' requests.
        allocated: The sum of its placements' allocations.
        unallocated: ``free - allocated``: capital the plan left in the account.
        scale: The ``PRO_RATA`` factor applied to its requests, or ``None``
            when none was.
        base_free: The account's free capital in the base currency.
    """

    account: CapitalAccount
    requested: Decimal
    allocated: Decimal
    unallocated: Decimal
    scale: Decimal | None
    base_free: Decimal


@dataclass(frozen=True, slots=True)
class DimensionAllocation:
    """Allocated capital grouped along one dimension, in the base currency.

    Attributes:
        dimension: The dimension.
        amounts: Bucket -> allocated capital in the base currency, sorted.
        shares: Bucket -> ``amount / total free capital``.
        native: For ``CURRENCY`` only, bucket -> allocated capital in that
            currency itself -- settlement truth, never converted. ``None`` for
            every other dimension.
    """

    dimension: CapitalDimension
    amounts: Mapping[str, Decimal]
    shares: Mapping[str, Decimal]
    native: Mapping[str, Decimal] | None


@dataclass(frozen=True, slots=True)
class LimitCheck:
    """One limit, judged against the allocation.

    Attributes:
        limit: The limit.
        share: The bucket's share of total free capital.
        satisfied: Whether the share lies within the limit, exactly.
    """

    limit: CapitalLimit
    share: Decimal
    satisfied: bool


@dataclass(frozen=True, slots=True)
class CapitalAllocationResult:
    """The outcome of :func:`allocate_capital`.

    Attributes:
        plan_id: The plan it answers.
        status: ``ALLOCATED`` or ``REFUSED``.
        base_currency: What plan-wide figures are in.
        total_free: Every account's free capital, translated and summed.
        placements: Every placement's allocation when ``ALLOCATED``; ``None``
            when ``REFUSED`` -- a refused plan allocates nothing.
        accounts: Every account: its requests, and when ``ALLOCATED`` its
            reconciled allocation.
        dimensions: Allocated capital by strategy, market, broker, account and
            currency, when ``ALLOCATED``.
        checks: Every limit, judged.
        reasons: Why a refused plan was refused, and any scaling applied to an
            allocated one, in words.
        conversions: Every conversion performed, in order.
    """

    plan_id: str
    status: CapitalAllocationStatus
    base_currency: str
    total_free: Decimal
    placements: tuple[PlacementAllocation, ...] | None
    accounts: tuple[AccountAllocation, ...]
    dimensions: tuple[DimensionAllocation, ...]
    checks: tuple[LimitCheck, ...]
    reasons: tuple[str, ...]
    conversions: tuple[CapitalConversion, ...]

    @property
    def succeeded(self) -> bool:
        """Whether capital was allocated."""

        return self.status is CapitalAllocationStatus.ALLOCATED

    def dimension(self, which: CapitalDimension) -> DimensionAllocation:
        """Allocated capital along one dimension.

        Raises:
            AllocationValidationError: If the plan was refused.
        """

        for entry in self.dimensions:
            if entry.dimension is which:
                return entry
        raise AllocationValidationError(
            f"A refused capital plan has no {which.name} allocation: {list(self.reasons)}."
        )

    @property
    def result_id(self) -> str:
        """The derived identity of this outcome."""

        placements = (
            ["placements=None"]
            if self.placements is None
            else [
                f"placement={entry.placement.rendering()}={entry.allocated}|{entry.base_amount}"
                for entry in self.placements
            ]
        )
        return _digest(
            [
                CAPITAL_ALLOCATION_SCHEME,
                f"plan={self.plan_id}",
                f"status={self.status.name}",
                f"total_free={self.total_free}",
                *placements,
                *(f"reason={reason!r}" for reason in self.reasons),
                *(
                    f"conversion={c.amount}|{c.base}|{c.quote}|{c.rate}|{c.rate_as_of!r}|"
                    f"{c.source!r}|{c.derived}|{c.converted}"
                    for c in self.conversions
                ),
            ]
        )


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #


def _floor(amount: Decimal, granularity: Decimal) -> Decimal:
    """The largest whole multiple of ``granularity`` not above ``amount``."""

    units = _CONTEXT.divide(amount, granularity).to_integral_value(rounding=ROUND_FLOOR)
    return _CONTEXT.multiply(units, granularity)


def _labels(placement: CapitalPlacement, account: CapitalAccount) -> dict[CapitalDimension, str]:
    return {
        CapitalDimension.STRATEGY: placement.strategy_id,
        CapitalDimension.MARKET: placement.market,
        CapitalDimension.BROKER: account.broker_id,
        CapitalDimension.ACCOUNT: account.account_id,
        CapitalDimension.CURRENCY: account.currency,
    }


def allocate_capital(
    plan: CapitalAllocationPlan, converter: CurrencyConverter
) -> CapitalAllocationResult:
    """Divide the plan's free capital by its rule, within its accounts and limits.

    Args:
        plan: The plan.
        converter: The rates plan-wide figures are converted with --
            :class:`~alphalab.portfolio.fx.FxRates`. A plan entirely in its
            base currency converts nothing; ``NO_RATES`` is then the honest
            table.

    Returns:
        ``ALLOCATED`` with every placement's capital, or ``REFUSED`` with the
        reasons, having allocated nothing.

    Raises:
        Whatever the converter raises for a missing, stale or future-dated
        rate -- a plan that cannot be expressed in its base currency is not
        allocated at all.
    """

    base = plan.base_currency
    conversions: list[CapitalConversion] = []
    by_id = {account.account_id: account for account in plan.accounts}

    def convert(
        amount: Decimal, source: str, target: str
    ) -> tuple[Decimal, CapitalConversion | None]:
        if source == target or amount == 0:
            return amount, None
        record = converter.convert(amount, source, target, plan.as_of)
        conversion = CapitalConversion(
            amount=record.amount,
            base=record.rate.base,
            quote=record.rate.quote,
            rate=record.rate.rate,
            rate_as_of=record.rate.as_of,
            source=record.rate.source,
            derived=record.rate.derived,
            converted=record.converted,
        )
        if conversion.base != source or conversion.quote != target:
            raise AllocationValidationError(
                f"The converter answered a {source}/{target} request with a "
                f"{conversion.base}/{conversion.quote} rate."
            )
        conversions.append(conversion)
        return record.converted, conversion

    base_free = {
        account.account_id: convert(account.free, account.currency, base)[0]
        for account in plan.accounts
    }
    total_free = sum(base_free.values(), _ZERO)

    # 1. What each placement asks for, in its account's currency.
    requested: dict[CapitalPlacement, Decimal] = {}
    rule = plan.rule
    for placement in plan.placements:
        account = by_id[placement.account_id]
        if isinstance(rule, FixedAmounts):
            requested[placement] = rule.amounts[placement]
            continue
        if isinstance(rule, PlacementWeights):
            fraction = rule.weights[placement]
        else:
            fraction = _CONTEXT.divide(rule.fraction, Decimal(len(plan.placements)))
        target = _CONTEXT.multiply(fraction, total_free)
        requested[placement] = convert(target, base, account.currency)[0]

    # 2. Each account against its free capital.
    reasons: list[str] = []
    scales: dict[str, Decimal] = {}
    account_requests = dict.fromkeys(by_id, _ZERO)
    for placement, amount in requested.items():
        account_requests[placement.account_id] += amount
    for account in plan.accounts:
        asked = account_requests[account.account_id]
        if asked <= account.free:
            continue
        if plan.oversubscription is OversubscriptionRule.REFUSE:
            reasons.append(
                f"Account {account.account_id!r} is asked for {asked} {account.currency} and has "
                f"{account.free} free ({account.available} available, {account.reserved} reserved)."
            )
        else:
            scales[account.account_id] = _CONTEXT.divide(account.free, asked)
            reasons.append(
                f"Account {account.account_id!r} was oversubscribed ({asked} asked, "
                f"{account.free} free {account.currency}); its requests were scaled by "
                f"{scales[account.account_id]} under PRO_RATA."
            )

    def refused(checks: tuple[LimitCheck, ...]) -> CapitalAllocationResult:
        return CapitalAllocationResult(
            plan_id=plan.plan_id,
            status=CapitalAllocationStatus.REFUSED,
            base_currency=base,
            total_free=total_free,
            placements=None,
            accounts=tuple(
                AccountAllocation(
                    account=account,
                    requested=account_requests[account.account_id],
                    allocated=_ZERO,
                    unallocated=account.free,
                    scale=None,
                    base_free=base_free[account.account_id],
                )
                for account in plan.accounts
            ),
            dimensions=(),
            checks=checks,
            reasons=tuple(reasons),
            conversions=tuple(conversions),
        )

    if plan.oversubscription is OversubscriptionRule.REFUSE and reasons:
        return refused(())

    # 3. Scale where stated, round down to the granularity.
    allocations: list[PlacementAllocation] = []
    for placement in plan.placements:
        account = by_id[placement.account_id]
        amount = requested[placement]
        scale = scales.get(account.account_id)
        if scale is not None:
            amount = _CONTEXT.multiply(amount, scale)
        allocated = _floor(amount, plan.granularity)
        base_amount, conversion = convert(allocated, account.currency, base)
        allocations.append(
            PlacementAllocation(
                placement=placement,
                currency=account.currency,
                requested=requested[placement],
                allocated=allocated,
                base_amount=base_amount,
                conversion=conversion,
            )
        )

    allocated_by_account = dict.fromkeys(by_id, _ZERO)
    for entry in allocations:
        allocated_by_account[entry.placement.account_id] += entry.allocated
    accounts = []
    for account in plan.accounts:
        allocated = allocated_by_account[account.account_id]
        unallocated = _CONTEXT.subtract(account.free, allocated)
        if unallocated < 0:
            # Unreachable by construction -- a floor never adds -- and checked
            # anyway, because an account allocated beyond its free capital is
            # the one outcome this module exists to prevent.
            raise AllocationValidationError(
                f"Account {account.account_id!r} would be allocated {allocated}, above its free "
                f"{account.free}."
            )
        accounts.append(
            AccountAllocation(
                account=account,
                requested=account_requests[account.account_id],
                allocated=allocated,
                unallocated=unallocated,
                scale=scales.get(account.account_id),
                base_free=base_free[account.account_id],
            )
        )

    # 4. Group along every dimension, and judge the limits.
    dimensions = []
    for dimension in CapitalDimension:
        amounts: dict[str, Decimal] = {}
        native: dict[str, Decimal] = {}
        for entry in allocations:
            label = _labels(entry.placement, by_id[entry.placement.account_id])[dimension]
            amounts[label] = amounts.get(label, _ZERO) + entry.base_amount
            if dimension is CapitalDimension.CURRENCY:
                native[label] = native.get(label, _ZERO) + entry.allocated
        dimensions.append(
            DimensionAllocation(
                dimension=dimension,
                amounts=MappingProxyType(dict(sorted(amounts.items()))),
                shares=MappingProxyType(
                    {
                        label: (_CONTEXT.divide(amount, total_free) if total_free else _ZERO)
                        for label, amount in sorted(amounts.items())
                    }
                ),
                native=(
                    MappingProxyType(dict(sorted(native.items())))
                    if dimension is CapitalDimension.CURRENCY
                    else None
                ),
            )
        )

    checks = []
    for limit in plan.limits:
        grouping = next(entry for entry in dimensions if entry.dimension is limit.dimension)
        share = grouping.shares.get(limit.bucket, _ZERO)
        satisfied = (limit.maximum_share is None or share <= limit.maximum_share) and (
            limit.minimum_share is None or share >= limit.minimum_share
        )
        checks.append(LimitCheck(limit, share, satisfied))
        if not satisfied:
            reasons.append(
                f"{limit.dimension.name} {limit.bucket!r} would hold {share} of free capital, "
                f"outside its limit [{limit.minimum_share}, {limit.maximum_share}]."
            )
    if not all(check.satisfied for check in checks):
        return refused(tuple(checks))

    return CapitalAllocationResult(
        plan_id=plan.plan_id,
        status=CapitalAllocationStatus.ALLOCATED,
        base_currency=base,
        total_free=total_free,
        placements=tuple(allocations),
        accounts=tuple(accounts),
        dimensions=tuple(dimensions),
        checks=tuple(checks),
        reasons=tuple(reasons),
        conversions=tuple(conversions),
    )


# --------------------------------------------------------------------------- #
# Composition with the execution path
# --------------------------------------------------------------------------- #


def reserved_capital(state: AllocationState, *, currency: str) -> Decimal:
    """Capital committed on the execution path, read from the one reservation ledger.

    Composes with ADR-0015's ledger rather than keeping a second one: the figure
    is ``notional_allocated``, checked against the ledger it totals (invariant
    I1) and against the budget's stated currency.

    Raises:
        AllocationValidationError: If the budget states no currency or another
            one (relabel it with :meth:`CapitalBudget.in_currency` if it is in
            fact in ``currency``), or the running total disagrees with the
            ledger.
    """

    _identifier(currency, "currency")
    if state.budget.currency != currency:
        stated = state.budget.currency or "no currency"
        raise AllocationValidationError(
            f"The allocation state's budget states {stated!r}, and its reservations are being read "
            f"as {currency!r}. A reservation's currency is its budget's; relabel the budget with "
            "in_currency() if that is what it is in."
        )
    ledger = sum(state.reservations.values(), _ZERO)
    if ledger != state.notional_allocated:
        raise AllocationValidationError(
            f"notional_allocated is {state.notional_allocated} and the reservation ledger totals "
            f"{ledger}: the state is inconsistent (ADR-0015 invariant I1)."
        )
    return state.notional_allocated


def capital_budget(
    result: CapitalAllocationResult,
    *,
    account_id: str,
    maximum_exposure: Decimal,
    cash_buffer: Decimal,
) -> CapitalBudget:
    """The execution-path budget one account's allocation becomes.

    ``global_capital`` is the account's reserved capital plus what the plan
    allocated in it, so that an :class:`~alphalab.allocation.state.AllocationState`
    already holding those reservations can commit exactly the allocation more
    (less the stated buffer); ``strategy_budgets`` holds each strategy's
    allocation in the account; ``currency`` is the account's.

    Args:
        result: An ``ALLOCATED`` result.
        account_id: The account.
        maximum_exposure: The ceiling on committed notional -- a leverage
            decision the plan does not make, so it is required.
        cash_buffer: Capital held back from the budget. Required; zero is a
            choice.

    Raises:
        AllocationValidationError: If the result was refused or holds no such
            account, or an amount is negative.
    """

    if result.placements is None:
        raise AllocationValidationError(
            f"Capital plan {result.plan_id[:12]} was refused and allocated nothing: "
            f"{list(result.reasons)}."
        )
    _amount(maximum_exposure, "maximum_exposure")
    _amount(cash_buffer, "cash_buffer")
    matches = [entry for entry in result.accounts if entry.account.account_id == account_id]
    if not matches:
        raise AllocationValidationError(f"The allocation holds no account {account_id!r}.")
    entry = matches[0]
    strategies: dict[str, Decimal] = {}
    for placement in result.placements:
        if placement.placement.account_id == account_id:
            name = placement.placement.strategy_id
            strategies[name] = strategies.get(name, _ZERO) + placement.allocated
    return CapitalBudget(
        global_capital=entry.account.reserved + entry.allocated,
        maximum_exposure=maximum_exposure,
        cash_buffer=cash_buffer,
        strategy_budgets=dict(sorted(strategies.items())),
        currency=entry.account.currency,
    )
