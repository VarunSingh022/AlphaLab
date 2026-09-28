"""Execution Simulator composing models to generate reports.

The simulator turns an instruction plus a decided fill quantity into an
:class:`~alphalab.execution.report.ExecutionReport`. Since v3.3 every fill it
produces is priced by one :class:`~alphalab.execution.costs.ExecutionCostModel`,
including a simulator configured the pre-v3.3 way -- there is one costing path,
not a legacy one beside a new one. A simulator given only ``slippage_model`` and
``commission_model`` is exactly a cost model whose other four roles are the
named absences, and it produces byte-identical reports to the ones it produced
before.

What the report carries, and what it cannot
--------------------------------------------

:class:`~alphalab.execution.report.ExecutionReport` is a persisted record: it is
captured into the run-state envelope by :mod:`alphalab.runtime.snapshot` under
ADR-0023, so its shape is not a thing v3.3 may extend casually. It keeps the two
cost figures it has always carried -- ``slippage``, the per-unit price
concession, and ``commission``, the one cash charge that reaches
:meth:`~alphalab.portfolio.engine.PortfolioEngine.apply_fill`.

The itemization behind those two totals is not stored. It does not need to be:
:meth:`ExecutionSimulator.simulate_costs` is a pure function of the instruction,
the quantity and the configuration, so the breakdown for any fill is
recomputable exactly and for ever from the run's own configuration. Storing it
would create a second copy of a derived fact, and a second copy is a second
thing that can disagree.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.common.ids import new_id
from alphalab.core.enums import Side
from alphalab.execution.commission import CommissionModel, FixedCommission
from alphalab.execution.costs import (
    CostContext,
    ExecutionCostModel,
    ExecutionCosts,
    NoFee,
    NoImpact,
    NoSlippage,
    NoSpread,
    NoTax,
)
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.latency import ConstantLatency, LatencyModel
from alphalab.execution.report import ExecutionReport
from alphalab.execution.slippage import FixedSlippage, SlippageModel
from alphalab.execution.validation import validate_execution_parameters

DEFAULT_COMMISSION = FixedCommission(Decimal("0.00"))
DEFAULT_SLIPPAGE = FixedSlippage(Decimal("0.00"))
DEFAULT_LATENCY = ConstantLatency(0.0)


@dataclass(frozen=True, slots=True)
class ExecutionSimulator:
    """Deterministic simulator combining latency, costs, and fill accounting.

    Attributes:
        commission_model: The commission role. Read only when ``cost_model`` is
            ``None``; naming a ``cost_model`` means this is expressed there.
        slippage_model: The explicit-slippage role, on the same terms.
        latency_model: Decides when the fill is stamped. Not part of the cost
            model, because latency moves a timestamp rather than a price.
        cost_model: The full six-role itemization. ``None`` means "the two
            models above and no other cost", which is what every pre-v3.3
            simulator meant.
    """

    commission_model: CommissionModel = DEFAULT_COMMISSION
    slippage_model: SlippageModel = DEFAULT_SLIPPAGE
    latency_model: LatencyModel = DEFAULT_LATENCY
    cost_model: ExecutionCostModel | None = field(default=None)

    @property
    def is_frictionless(self) -> bool:
        """Whether every cost role charges nothing by construction.

        True for :data:`~alphalab.execution.costs.FREE` and for the default
        simulator, whose two legacy roles are fixed charges of zero -- a run
        that states no costs pays none (ledger EXE-002). It says which
        configuration was chosen, not what a fill happened to be charged: a
        percentage model at a zero rate charges nothing too, and is reported as
        the model it is.
        """

        return all(_charges_nothing(role) for role in astuple_shallow(self.costs))

    @property
    def costs(self) -> ExecutionCostModel:
        """The cost model every fill is priced by.

        Either the one given, or the two legacy models lifted into the four
        absences. Building it here rather than at each call site is what keeps
        one costing path.
        """

        if self.cost_model is not None:
            return self.cost_model
        return ExecutionCostModel(
            spread_model=NoSpread(),
            slippage_model=self.slippage_model,
            impact_model=NoImpact(),
            commission_model=self.commission_model,
            fee_model=NoFee(),
            tax_model=NoTax(),
        )

    @property
    def passive_costs(self) -> ExecutionCostModel:
        """What a *resting* fill is priced by: the cash charges and no price concession.

        A limit order that rested and was filled at its own price did not cross
        the spread, walk the book or move the market; it was the liquidity. Its
        spread, slippage and impact are therefore nothing, and its commission,
        fees and tax are what the configuration charges. Since v3.11 (ledger
        EXE-003), for fills :func:`simulate_fill` is told are passive.
        """

        costs = self.costs
        return ExecutionCostModel(
            spread_model=NoSpread(),
            slippage_model=NoSlippage(),
            impact_model=NoImpact(),
            commission_model=costs.commission_model,
            fee_model=costs.fee_model,
            tax_model=costs.tax_model,
        )

    def context(
        self,
        instruction: OrderInstruction,
        fill_quantity: Decimal,
        market_price: Decimal,
        timestamp: float,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        available_liquidity: Decimal | None = None,
    ) -> CostContext:
        """Project one instruction and its decided quantity into a cost context.

        ``timestamp`` is the *event* time; the returned context carries the
        post-latency time, because that is when the fill happens and therefore
        what a cost model asking "when" should see.
        """

        return CostContext(
            asset_id=instruction.asset_id,
            side=instruction.side,
            quantity=fill_quantity,
            reference_price=market_price,
            currency=instruction.currency,
            venue=instruction.venue,
            timestamp=timestamp + self.latency_model.calculate(instruction.order_id, timestamp),
            bid=bid,
            ask=ask,
            available_liquidity=available_liquidity,
            minor_units=instruction.minor_units,
        )

    def simulate_costs(
        self,
        instruction: OrderInstruction,
        fill_quantity: Decimal,
        market_price: Decimal,
        timestamp: float,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        available_liquidity: Decimal | None = None,
        *,
        passive: bool = False,
    ) -> ExecutionCosts:
        """Itemize what this fill costs, without producing a report.

        ``passive`` itemizes a resting fill -- see :attr:`passive_costs`; a
        report says which a fill was by its ``liquidity_flag``.

        The breakdown behind :attr:`~alphalab.execution.report.ExecutionReport.slippage`
        and :attr:`~alphalab.execution.report.ExecutionReport.commission`. Pure,
        so calling it after the fact reproduces exactly the itemization the fill
        was priced with.
        """

        model = self.passive_costs if passive else self.costs
        return model.quote(
            self.context(
                instruction, fill_quantity, market_price, timestamp, bid, ask, available_liquidity
            )
        )

    def simulate_fill(
        self,
        instruction: OrderInstruction,
        fill_quantity: Decimal,
        market_price: Decimal,
        timestamp: float,
        status: FillStatus,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        available_liquidity: Decimal | None = None,
        *,
        passive: bool = False,
    ) -> ExecutionReport:
        """Simulates a fill deterministically.

        ``bid``, ``ask`` and ``available_liquidity`` are what the market event
        showed, and each is ``None`` when the event showed nothing of the kind.
        They reach the cost model and nothing else: a cost role that needs one
        of them refuses when it is absent rather than substituting a figure, so
        a feed without sizes cannot silently produce an impact charge.

        ``passive`` prices a resting order's fill at ``market_price`` itself --
        its limit, or better -- charging only commission, fees and tax (see
        :attr:`passive_costs`), and flags it ``MAKER``. Otherwise the fill takes
        liquidity and is flagged ``TAKER``, as every simulated fill was before
        v3.11.
        """

        context = self.context(
            instruction, fill_quantity, market_price, timestamp, bid, ask, available_liquidity
        )
        model = self.passive_costs if passive else self.costs
        costs = model.quote(context)
        fill_price = model.fill_price(context, costs)

        if status in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL):
            validate_execution_parameters(
                fill_quantity, fill_price, costs.cash_charged, context.timestamp
            )

        return ExecutionReport(
            execution_id=str(new_id()),
            order_id=instruction.order_id,
            asset_id=instruction.asset_id,
            strategy_id=instruction.strategy_id,
            timestamp=context.timestamp,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            # The single cash channel: commission, fees and tax reach the
            # portfolio as one debit, because apply_fill takes one. The split
            # between the three is recovered from simulate_costs, never by
            # adding a second charge here -- that would be the double count
            # alphalab.execution.costs exists to prevent.
            commission=costs.cash_charged,
            # The per-unit concession, which is already inside fill_price.
            slippage=costs.price_concession,
            liquidity_flag="MAKER" if passive else "TAKER",
            venue=instruction.venue,
            currency=instruction.currency,
            status=status,
        )


def astuple_shallow(costs: ExecutionCostModel) -> tuple[object, ...]:
    """The six roles of a cost model, in declaration order."""

    return (
        costs.spread_model,
        costs.slippage_model,
        costs.impact_model,
        costs.commission_model,
        costs.fee_model,
        costs.tax_model,
    )


def _charges_nothing(role: object) -> bool:
    """Whether a cost role is a no-cost member, or a fixed charge of zero."""

    if isinstance(role, NoSpread | NoSlippage | NoImpact | NoFee | NoTax):
        return True
    # A fixed charge is asked for what it charges: one unit at a price of one.
    if isinstance(role, FixedCommission):
        return role.calculate(_ONE, _ONE) == 0
    if isinstance(role, FixedSlippage):
        return role.calculate(_ONE, _ONE, Side.BUY) == 0
    return False


_ONE = Decimal(1)
