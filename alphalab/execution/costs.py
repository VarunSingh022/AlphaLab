"""What one fill costs, itemized, and the order the items are applied in.

Until v3.3 a simulated fill carried two cost numbers: ``slippage`` -- a per-unit
price concession -- and ``commission``. Everything else an institution pays was
either folded into one of those two or absent entirely. A backtest could not say
whether a concession came from crossing a quoted spread, from an explicit
slippage assumption, or from the size of the order itself, and it had nowhere at
all to put an exchange fee or a transaction tax.

This module is the answer, and it is deliberately *not* a second cost engine.
:class:`ExecutionCostModel` composes the models that already exist --
:class:`~alphalab.execution.slippage.SlippageModel` and
:class:`~alphalab.execution.commission.CommissionModel` -- alongside the three
roles nothing owned before (spread, fee, tax) and the one that could not be
expressed (liquidity-aware impact). :class:`~alphalab.execution.simulator.ExecutionSimulator`
routes every fill through it, including a fill configured the pre-v3.3 way, so
there is one code path rather than a legacy one and a new one.

Two settlements, and why the distinction is the whole design
-------------------------------------------------------------

Every cost here is exactly one of two kinds, named by :class:`CostSettlement`:

``PRICE_EMBEDDED``
    Spread, slippage and impact. These move the price the fill happens at. They
    are **never** charged to cash, because they are already in the cash the fill
    moved: a buy that pays a concession pays it by paying more per share.

``CASH_CHARGED``
    Commission, fees and tax. These are debited separately from the traded
    notional.

Collapsing the two would double-count, and AlphaLab's accounting identity would
be the thing that broke. :meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_fill`
takes a price and *one* cash cost, which is the single cash channel invariant 3
of ``nowandfuture.md`` section 14 protects. So this module does not add a second
channel: :attr:`ExecutionCosts.price_concession` is what moves the fill price and
:attr:`ExecutionCosts.cash_charged` is what goes down that one channel, and
:func:`reconciles` is the assertion that the itemization still sums to exactly
the two figures the report carries.

Ordering is a decision, not an implementation detail
-----------------------------------------------------

Several of these costs are computed from a price, and which price changes the
answer. The order is fixed here and stated once::

    order intent
      -> latency model            when the fill is stamped
      -> reference price          what the event showed
      -> liquidity / fill policy  how much fills            (FillPolicy)
      -> spread                   per-unit concession
      -> slippage                 per-unit concession
      -> impact                   per-unit concession
      -> fill price = reference +/- (spread + slippage + impact)
      -> cash base  = fill price * filled quantity
      -> commission, fee, tax     charged on that cash base
      -> execution result

The three price concessions are computed from the **reference** price and are
additive, so they do not compound and their order among themselves does not
matter. The three cash costs are computed from the **post-concession** cash base,
because that is the notional that actually changed hands -- a fee quoted as a
fraction of consideration is a fraction of what was paid, not of what was quoted.

Nothing here assumes a cost
---------------------------

Every role has a ``No*`` member meaning "this participant does not pay this",
and :data:`FREE` composes the six of them. That is an *absence* of cost, which is
honest, and it is the only thing this module will supply without being asked --
in the same way ``NO_RATES`` is the empty FX table rather than a default rate.
No spread, impact, fee or tax model here carries a defaulted coefficient: a rate
that decides money is named by the caller or it does not exist. See
``tests/regression/test_no_silent_financial_defaults.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Protocol

from alphalab.core.enums import Side
from alphalab.execution.commission import CommissionModel, FixedCommission
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.slippage import SlippageModel

__all__ = [
    "FREE",
    "CostContext",
    "CostSettlement",
    "ExecutionCostModel",
    "ExecutionCosts",
    "FeeModel",
    "FixedHalfSpread",
    "ImpactModel",
    "LinearImpact",
    "NoFee",
    "NoImpact",
    "NoSlippage",
    "NoSpread",
    "NoTax",
    "PerTradeFee",
    "ProportionalFee",
    "ProportionalTax",
    "QuotedHalfSpread",
    "SpreadModel",
    "SquareRootImpact",
    "TaxModel",
    "itemized",
    "reconciles",
]

#: Per-unit price concessions are carried at this exponent, matching
#: ``PRICE_QUANT`` in :mod:`alphalab.portfolio.money`: a concession is a price
#: delta, so it is quantized like a price rather than like money.
_PRICE_QUANT = Decimal("0.0001")

#: Cash costs are carried at money precision, matching ``CURRENCY_QUANT``.
_CASH_QUANT = Decimal("0.01")

_ZERO = Decimal("0")


class CostSettlement(Enum):
    """How a cost reaches the portfolio, and therefore where it may be counted.

    The two members are exhaustive by construction: a cost that moved the price
    cannot also be charged to cash without being paid twice.
    """

    #: Moves the fill price. Already inside the traded notional.
    PRICE_EMBEDDED = auto()

    #: Debited from cash separately, alongside the traded notional.
    CASH_CHARGED = auto()


@dataclass(frozen=True, slots=True)
class CostContext:
    """Everything a cost model is allowed to read about one fill.

    A cost model reads this and nothing else -- no pipeline state, no portfolio,
    no registry -- which is what makes a configured cost model reproduce a run
    from its configuration alone.

    Attributes:
        asset_id: Asset being filled.
        side: Direction of the fill.
        quantity: Quantity actually filling, after the fill policy has capped
            it. A cost is charged on what executed, never on what was asked for.
        reference_price: The price the market event showed, before any
            concession. All three price concessions are computed from this.
        currency: Currency the fill settles in. Costs are expressed in it, and
            this module converts nothing -- a model handed a context in one
            currency returns a figure in that currency.
        venue: Execution venue the fill happened at.
        timestamp: Fill timestamp, after the latency model has moved it.
        bid: Best bid at the event, or ``None`` when the event carried no quote.
        ask: Best ask at the event, or ``None`` when the event carried no quote.
        available_liquidity: Quantity the venue was showing, or ``None`` when the
            event carried no size. An impact model that needs a participation
            rate refuses without it rather than inventing a denominator.
    """

    asset_id: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    currency: str
    venue: str
    timestamp: float
    bid: Decimal | None = None
    ask: Decimal | None = None
    available_liquidity: Decimal | None = None

    @property
    def participation(self) -> Decimal | None:
        """``quantity / available_liquidity``, or ``None`` when size is unknown.

        The share of the shown liquidity this fill is taking. ``None`` rather
        than a guess: an impact model that reads this refuses when it is absent.
        """

        available = self.available_liquidity
        if available is None or available <= _ZERO:
            return None
        return self.quantity / available


@dataclass(frozen=True, slots=True)
class ExecutionCosts:
    """One fill's costs, itemized by role and separated by settlement.

    The first three are **per unit** price concessions; the last three are cash
    amounts for the whole fill. Mixing those two scales is the mistake this
    class exists to make impossible to write by accident, which is why
    :meth:`total` requires the quantity to combine them.

    Every field is non-negative: a cost is a cost. Direction is applied by
    :meth:`ExecutionCostModel.fill_price`, which adds the concession for a buy
    and subtracts it for a sell -- a negative concession here would mean a fill
    that improved on the reference price, which is price improvement rather than
    a cost and is not what this records.
    """

    spread: Decimal
    slippage: Decimal
    impact: Decimal
    commission: Decimal
    fees: Decimal
    tax: Decimal

    def __post_init__(self) -> None:
        for name in ("spread", "slippage", "impact", "commission", "fees", "tax"):
            value: Decimal = getattr(self, name)
            if value < _ZERO:
                raise ExecutionValidationError(
                    f"{name} is {value}, which is negative. ExecutionCosts records what a "
                    "fill cost; a concession that improved on the reference price is price "
                    "improvement, not a negative cost, and has no field here."
                )

    @property
    def price_concession(self) -> Decimal:
        """Per-unit total of the three ``PRICE_EMBEDDED`` costs."""

        return self.spread + self.slippage + self.impact

    @property
    def cash_charged(self) -> Decimal:
        """Total of the three ``CASH_CHARGED`` costs, for the whole fill."""

        return self.commission + self.fees + self.tax

    def total(self, quantity: Decimal) -> Decimal:
        """All-in cost of a fill of ``quantity``, in the fill's currency.

        ``price_concession * quantity + cash_charged``. The quantity is required
        rather than remembered because the concessions are per-unit: an
        ``ExecutionCosts`` describes a *rate* of concession and an *amount* of
        cash, and only the caller knows which fill it is being applied to.
        """

        return self.price_concession * quantity + self.cash_charged

    def by_settlement(self, settlement: CostSettlement) -> Mapping[str, Decimal]:
        """The components of one settlement kind, keyed by role name."""

        if settlement is CostSettlement.PRICE_EMBEDDED:
            return {"spread": self.spread, "slippage": self.slippage, "impact": self.impact}
        return {"commission": self.commission, "fees": self.fees, "tax": self.tax}


# --------------------------------------------------------------------------- #
# The four roles nothing owned before v3.3
# --------------------------------------------------------------------------- #


class SpreadModel(Protocol):
    """The per-unit concession from crossing the quoted spread.

    Separate from slippage because it is *observable*: when the event carried a
    quote, half the quoted spread is a measured fact about the venue, not an
    assumption about the participant. :class:`QuotedHalfSpread` uses that fact
    and refuses when the quote is absent; the other members are assumptions and
    say so.
    """

    def half_spread(self, context: CostContext) -> Decimal: ...


class ImpactModel(Protocol):
    """The per-unit concession caused by the order's own size.

    Distinct from :class:`~alphalab.execution.slippage.SlippageModel` by its
    inputs, not by its units: an impact model reads
    :attr:`CostContext.participation` and is therefore a function of how much of
    the available liquidity the order takes. ``SlippageModel`` sees only
    ``(quantity, price, side)`` and cannot express that -- which is also why
    :class:`~alphalab.execution.slippage.MarketImpactSlippage`, despite its name,
    stays a slippage-role model. The two are pinned apart in
    ``tests/regression/test_shared_names_stay_distinct.py``.
    """

    def impact(self, context: CostContext) -> Decimal: ...


class FeeModel(Protocol):
    """A venue or broker fee, charged in cash on the consideration."""

    def fee(self, context: CostContext, cash_base: Decimal) -> Decimal: ...


class TaxModel(Protocol):
    """A transaction tax, charged in cash on the consideration.

    Taxes are side-dependent in most jurisdictions that levy them -- UK stamp
    duty falls on purchases and not on sales -- so this reads the context rather
    than only the base, which is what distinguishes it from :class:`FeeModel`.
    """

    def tax(self, context: CostContext, cash_base: Decimal) -> Decimal: ...


# --------------------------------------------------------------------------- #
# Absence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NoSpread:
    """This participant crosses no spread. Zero because it was asked for."""

    def half_spread(self, context: CostContext) -> Decimal:
        return _ZERO


@dataclass(frozen=True, slots=True)
class NoSlippage:
    """No explicit slippage assumption.

    Provided so a cost model can be written with all six roles named, rather
    than leaving the reader to infer that an omitted model meant zero.
    """

    def calculate(self, fill_quantity: Decimal, fill_price: Decimal, side: Side) -> Decimal:
        return _ZERO


@dataclass(frozen=True, slots=True)
class NoImpact:
    """This order is assumed too small to move the price."""

    def impact(self, context: CostContext) -> Decimal:
        return _ZERO


@dataclass(frozen=True, slots=True)
class NoFee:
    """No venue or broker fee."""

    def fee(self, context: CostContext, cash_base: Decimal) -> Decimal:
        return _ZERO


@dataclass(frozen=True, slots=True)
class NoTax:
    """No transaction tax."""

    def tax(self, context: CostContext, cash_base: Decimal) -> Decimal:
        return _ZERO


# --------------------------------------------------------------------------- #
# Spread
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuotedHalfSpread:
    """Half the spread the event actually quoted.

    The only cost model in this module that reports a *measurement* rather than
    an assumption. It needs both sides of the quote and refuses without them:
    a run whose feed carries no sizes or no quotes has not observed a spread,
    and substituting one would put an invented number where the absence of a
    measurement belongs.

    Raises:
        ExecutionValidationError: If either side of the quote is missing, or if
            the quote is crossed (``bid > ask``), which is not a spread.
    """

    def half_spread(self, context: CostContext) -> Decimal:
        bid, ask = context.bid, context.ask
        if bid is None or ask is None:
            missing = "bid" if bid is None else "ask"
            raise ExecutionValidationError(
                f"QuotedHalfSpread needs both sides of the quote and {missing} is absent "
                f"for {context.asset_id} at {context.timestamp}. The event quoted no "
                "spread, so none was observed; use FixedHalfSpread to state an assumed "
                "one, or NoSpread to charge none."
            )
        if bid > ask:
            raise ExecutionValidationError(
                f"Crossed quote for {context.asset_id}: bid {bid} is above ask {ask}. "
                "Half of a negative spread is not a cost."
            )
        return ((ask - bid) / Decimal("2")).quantize(_PRICE_QUANT)


@dataclass(frozen=True, slots=True)
class FixedHalfSpread:
    """An assumed half-spread, in price units per share.

    Stated by the caller, which is the point: it is an assumption, and naming it
    in the configuration is what makes it visible in the run that used it.
    """

    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < _ZERO:
            raise ExecutionValidationError(
                f"FixedHalfSpread amount is {self.amount}; a half-spread is not negative."
            )

    def half_spread(self, context: CostContext) -> Decimal:
        return self.amount


# --------------------------------------------------------------------------- #
# Impact
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LinearImpact:
    """Concession proportional to participation: ``coefficient * p * price``.

    ``p`` is :attr:`CostContext.participation` -- the share of shown liquidity
    the fill takes. Linear impact is the simplest form that responds to
    liquidity at all, and it is offered alongside :class:`SquareRootImpact`
    rather than instead of it because the two disagree materially at small
    participation and the literature does not settle which is right for a given
    venue. Choosing is the caller's.

    Raises:
        ExecutionValidationError: At construction if ``coefficient`` is
            negative; at call time if the event showed no liquidity, because a
            participation rate has no denominator then.
    """

    coefficient: Decimal

    def __post_init__(self) -> None:
        if self.coefficient < _ZERO:
            raise ExecutionValidationError(
                f"LinearImpact coefficient is {self.coefficient}; impact is not negative."
            )

    def impact(self, context: CostContext) -> Decimal:
        participation = _require_participation(context, "LinearImpact")
        return (self.coefficient * participation * context.reference_price).quantize(_PRICE_QUANT)


@dataclass(frozen=True, slots=True)
class SquareRootImpact:
    """Concession proportional to ``sqrt(participation)``: the square-root law.

    ``coefficient * sqrt(p) * price``. The square root is taken on
    :class:`~decimal.Decimal` rather than through :mod:`math`, so the result is
    exact to the working precision and identical on every platform -- a float
    ``sqrt`` would make a backtest's costs depend on the machine it ran on.

    Raises:
        ExecutionValidationError: At construction if ``coefficient`` is
            negative; at call time if the event showed no liquidity.
    """

    coefficient: Decimal

    def __post_init__(self) -> None:
        if self.coefficient < _ZERO:
            raise ExecutionValidationError(
                f"SquareRootImpact coefficient is {self.coefficient}; impact is not negative."
            )

    def impact(self, context: CostContext) -> Decimal:
        participation = _require_participation(context, "SquareRootImpact")
        return (self.coefficient * participation.sqrt() * context.reference_price).quantize(
            _PRICE_QUANT
        )


def _require_participation(context: CostContext, model: str) -> Decimal:
    participation = context.participation
    if participation is None:
        raise ExecutionValidationError(
            f"{model} needs the liquidity the event showed to form a participation rate, "
            f"and {context.asset_id} at {context.timestamp} showed none. An impact model "
            "without a denominator would be charging a rate against a quantity it invented; "
            "use NoImpact, or a slippage-role model that reads notional alone."
        )
    return participation


# --------------------------------------------------------------------------- #
# Fees and taxes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PerTradeFee:
    """A flat fee per fill, in the fill's settlement currency."""

    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < _ZERO:
            raise ExecutionValidationError(
                f"PerTradeFee amount is {self.amount}; a fee is not negative."
            )

    def fee(self, context: CostContext, cash_base: Decimal) -> Decimal:
        return self.amount if context.quantity > _ZERO else _ZERO


@dataclass(frozen=True, slots=True)
class ProportionalFee:
    """A fee quoted as a fraction of consideration.

    ``fraction`` is a fraction, not basis points and not a percentage: ``0.0001``
    is one basis point. Spelled out because a factor-of-ten error here is
    invisible in a result and expensive in a live account.
    """

    fraction: Decimal

    def __post_init__(self) -> None:
        if self.fraction < _ZERO:
            raise ExecutionValidationError(
                f"ProportionalFee fraction is {self.fraction}; a fee is not negative."
            )

    def fee(self, context: CostContext, cash_base: Decimal) -> Decimal:
        return (cash_base * self.fraction).quantize(_CASH_QUANT)


@dataclass(frozen=True, slots=True)
class ProportionalTax:
    """A transaction tax on consideration, levied on the named sides only.

    ``sides`` is required and has no default. Which side a tax falls on is the
    substance of the tax -- UK stamp duty is charged on purchases of UK shares
    and not on sales, and a model that assumed "both" would overstate the cost
    of every sell by the full rate. There is no universal answer to default to,
    so there is no default.
    """

    fraction: Decimal
    sides: frozenset[Side]

    def __post_init__(self) -> None:
        if self.fraction < _ZERO:
            raise ExecutionValidationError(
                f"ProportionalTax fraction is {self.fraction}; a tax is not negative."
            )
        if not self.sides:
            raise ExecutionValidationError(
                "ProportionalTax names no side, so it would never be levied. Name the "
                "side or sides the tax falls on, or use NoTax."
            )

    def tax(self, context: CostContext, cash_base: Decimal) -> Decimal:
        if context.side not in self.sides:
            return _ZERO
        return (cash_base * self.fraction).quantize(_CASH_QUANT)


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExecutionCostModel:
    """The six cost roles of one execution configuration, applied in one order.

    Every field is required. A cost model with an unstated role would be a cost
    model with a hidden assumption in it, which is the thing this release exists
    to remove -- :data:`FREE` is how a caller says "none of these", once and
    visibly, rather than by omission.

    The model owns no state and reads nothing but its :class:`CostContext`, so
    two runs with the same configuration produce the same costs, in this process
    or another.
    """

    spread_model: SpreadModel
    slippage_model: SlippageModel
    impact_model: ImpactModel
    commission_model: CommissionModel
    fee_model: FeeModel
    tax_model: TaxModel

    def quote(self, context: CostContext) -> ExecutionCosts:
        """Itemize what a fill described by ``context`` costs.

        Applies the ordering stated in this module's docstring: the three price
        concessions from the reference price, then the three cash costs from the
        resulting consideration.

        A fill of zero quantity costs nothing and is not passed to any model --
        there is no fill to charge for, and a per-trade fee on a non-event would
        be a charge for nothing.
        """

        if context.quantity <= _ZERO:
            return ExecutionCosts(_ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO)

        spread = self.spread_model.half_spread(context)
        slippage = self.slippage_model.calculate(
            context.quantity, context.reference_price, context.side
        )
        impact = self.impact_model.impact(context)

        fill_price = self.fill_price_from(context, spread + slippage + impact)
        cash_base = fill_price * context.quantity

        commission = self.commission_model.calculate(context.quantity, fill_price)
        fees = self.fee_model.fee(context, cash_base)
        tax = self.tax_model.tax(context, cash_base)

        return ExecutionCosts(
            spread=spread,
            slippage=slippage,
            impact=impact,
            commission=commission,
            fees=fees,
            tax=tax,
        )

    @staticmethod
    def fill_price_from(context: CostContext, concession: Decimal) -> Decimal:
        """Apply a per-unit concession to the reference price, directionally.

        A buy pays more, a sell receives less. The floor at one tick is the rule
        :class:`~alphalab.execution.simulator.ExecutionSimulator` has applied
        since it was written: a concession large enough to drive a sale to zero
        or below describes an arithmetic accident rather than a market, and a
        non-positive fill price is refused downstream by
        :meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_fill` anyway.
        """

        if context.side is Side.BUY:
            return context.reference_price + concession
        return max(Decimal("0.01"), context.reference_price - concession)

    def fill_price(self, context: CostContext, costs: ExecutionCosts) -> Decimal:
        """The price a fill happens at once ``costs`` are embedded in it."""

        return self.fill_price_from(context, costs.price_concession)


#: The cost model of a participant who pays nothing: six absences, named.
#:
#: This is the empty table rather than a default, the distinction
#: ``tests/regression/test_no_silent_financial_defaults.py`` draws for
#: ``NO_RATES``. A frictionless backtest is a legitimate and common thing to
#: want; what is not legitimate is getting one without having said so.
FREE = ExecutionCostModel(
    spread_model=NoSpread(),
    slippage_model=NoSlippage(),
    impact_model=NoImpact(),
    commission_model=FixedCommission(Decimal("0.00")),
    fee_model=NoFee(),
    tax_model=NoTax(),
)


def reconciles(costs: ExecutionCosts, concession: Decimal, cash: Decimal) -> bool:
    """Whether an itemization still sums to the two figures a report carries.

    The invariant that keeps the itemization honest: an
    :class:`~alphalab.execution.report.ExecutionReport` records one per-unit
    concession and one cash cost, and the six items here must reproduce both
    exactly. Used by ``tests/regression/`` to assert that no component is
    dropped, counted twice, or quietly rounded away between the cost model and
    the report.
    """

    return costs.price_concession == concession and costs.cash_charged == cash


def itemized(costs: ExecutionCosts, quantity: Decimal) -> Mapping[str, Decimal]:
    """The **money** one fill paid in each of the six roles, keyed by role name.

    ``quantity`` is required, and required for the reason :meth:`ExecutionCosts.total`
    requires it: the three price concessions are stored per unit and the three
    cash costs are stored as amounts, so a mapping of the raw fields would put
    two different units in one table. Anything consuming this -- an execution
    attribution, a cost report, a comparison between venues -- sums across the
    roles, and summing a per-share concession into a commission is a
    dimensionally incoherent number that looks entirely plausible.

    So the concessions are multiplied out here, once, and every value returned
    is an amount in the fill's settlement currency. The values therefore sum to
    :meth:`ExecutionCosts.total`.

    This is the projection :mod:`alphalab.analytics.attribution` consumes for
    execution attribution. It is a function here rather than a second dataclass
    there: analytics must not import :mod:`alphalab.execution` -- the two are
    siblings over :mod:`alphalab.core` and joining them would be a new package
    edge for no gain -- and a parallel six-field record in analytics would be a
    second definition of this itemization. A mapping is the shape that crosses
    the boundary without either.
    """

    return {
        "spread": costs.spread * quantity,
        "slippage": costs.slippage * quantity,
        "impact": costs.impact * quantity,
        "commission": costs.commission,
        "fees": costs.fees,
        "tax": costs.tax,
    }
