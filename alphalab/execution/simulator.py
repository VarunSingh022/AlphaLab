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
from alphalab.execution.commission import CommissionModel, FixedCommission
from alphalab.execution.costs import (
    CostContext,
    ExecutionCostModel,
    ExecutionCosts,
    NoFee,
    NoImpact,
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
    ) -> ExecutionCosts:
        """Itemize what this fill costs, without producing a report.

        The breakdown behind :attr:`~alphalab.execution.report.ExecutionReport.slippage`
        and :attr:`~alphalab.execution.report.ExecutionReport.commission`. Pure,
        so calling it after the fact reproduces exactly the itemization the fill
        was priced with.
        """

        return self.costs.quote(
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
    ) -> ExecutionReport:
        """Simulates a fill deterministically.

        ``bid``, ``ask`` and ``available_liquidity`` are what the market event
        showed, and each is ``None`` when the event showed nothing of the kind.
        They reach the cost model and nothing else: a cost role that needs one
        of them refuses when it is absent rather than substituting a figure, so
        a feed without sizes cannot silently produce an impact charge.
        """

        context = self.context(
            instruction, fill_quantity, market_price, timestamp, bid, ask, available_liquidity
        )
        costs = self.costs.quote(context)
        fill_price = self.costs.fill_price(context, costs)

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
            liquidity_flag="TAKER",
            venue=instruction.venue,
            currency=instruction.currency,
            status=status,
        )
