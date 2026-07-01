"""Execution Simulator composing models to generate reports."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from alphalab.execution.commission import CommissionModel, FixedCommission
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
    """Deterministic simulator combining latency, slippage, and commission."""

    commission_model: CommissionModel = DEFAULT_COMMISSION
    slippage_model: SlippageModel = DEFAULT_SLIPPAGE
    latency_model: LatencyModel = DEFAULT_LATENCY

    def simulate_fill(
        self,
        instruction: OrderInstruction,
        fill_quantity: Decimal,
        market_price: Decimal,
        timestamp: float,
        status: FillStatus,
    ) -> ExecutionReport:
        """Simulates a fill deterministically."""

        latency = self.latency_model.calculate(instruction.order_id, timestamp)
        execution_time = timestamp + latency

        slippage = self.slippage_model.calculate(fill_quantity, market_price, instruction.side)

        # Apply slippage directionally
        if instruction.side.upper() == "BUY":
            fill_price = market_price + slippage
        else:
            fill_price = max(Decimal("0.01"), market_price - slippage)

        commission = self.commission_model.calculate(fill_quantity, fill_price)

        if status in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL):
            validate_execution_parameters(fill_quantity, fill_price, commission, execution_time)

        return ExecutionReport(
            execution_id=str(uuid.uuid4()),
            order_id=instruction.order_id,
            asset_id=instruction.asset_id,
            strategy_id=instruction.strategy_id,
            timestamp=execution_time,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            commission=commission,
            slippage=slippage,
            liquidity_flag="TAKER",
            venue=instruction.venue,
            currency=instruction.currency,
            status=status,
        )
