"""Orchestration of pure optimization, constraints, and rebalancing tasks."""

from dataclasses import replace
from typing import Any

from alphalab.common.ids import new_id
from alphalab.portfolio_optimizer.constraints import WeightConstraints, apply_weight_constraints
from alphalab.portfolio_optimizer.costs import CostModel, TransactionCostEstimate
from alphalab.portfolio_optimizer.events import ConstraintViolated, Rebalanced, WeightsCalculated
from alphalab.portfolio_optimizer.exceptions import OptimizationError
from alphalab.portfolio_optimizer.optimizer import (
    optimize_equal_weight,
    optimize_inverse_volatility,
    optimize_maximum_sharpe,
    optimize_minimum_variance,
)
from alphalab.portfolio_optimizer.rebalance import (
    RebalanceTrigger,
    check_schedule_rebalance,
    check_threshold_rebalance,
)
from alphalab.portfolio_optimizer.state import PortfolioEngineState
from alphalab.portfolio_optimizer.validation import validate_portfolio_exists
from alphalab.portfolio_optimizer.weights import TargetWeights


class PortfolioManager:
    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def optimize(
        state: PortfolioEngineState,
        port_id: str,
        method: str,
        symbols: tuple[str, ...],
        params: dict[str, Any],
        ts: float,
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, port_id)

        if method == "EQUAL_WEIGHT":
            raw_w = optimize_equal_weight(symbols)
        elif method == "INVERSE_VOLATILITY":
            raw_w = optimize_inverse_volatility(symbols, params.get("volatilities", {}))
        elif method == "MINIMUM_VARIANCE":
            raw_w = optimize_minimum_variance(symbols, params.get("covariance", []))
        elif method == "MAXIMUM_SHARPE":
            raw_w = optimize_maximum_sharpe(
                symbols, params.get("returns", []), params.get("covariance", [])
            )
        else:
            raise OptimizationError(f"Unsupported optimization method: {method}")

        # Apply constraints if configured
        constraints = state.constraints.get(port_id, WeightConstraints())
        final_w = apply_weight_constraints(raw_w, constraints)

        new_weights = dict(state.weights)
        new_weights[port_id] = TargetWeights(port_id, ts, final_w)

        evt = WeightsCalculated(PortfolioManager._create_id(), ts, port_id, method)
        return replace(state, weights=new_weights, events=(*state.events, evt))

    @staticmethod
    def apply_constraints(
        state: PortfolioEngineState, port_id: str, constraints: WeightConstraints, ts: float
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, port_id)
        new_constraints = dict(state.constraints)
        new_constraints[port_id] = constraints

        if port_id in state.weights:
            old_w = state.weights[port_id]
            new_w = apply_weight_constraints(old_w.weights, constraints)
            if new_w != old_w.weights:
                # Log constraint violation if clipping occurred
                evt = ConstraintViolated(
                    PortfolioManager._create_id(),
                    ts,
                    port_id,
                    "WeightLimits",
                    1.0,
                )
                new_weights = dict(state.weights)
                new_weights[port_id] = TargetWeights(port_id, ts, new_w)
                return replace(
                    state,
                    constraints=new_constraints,
                    weights=new_weights,
                    events=(*state.events, evt),
                )

        return replace(state, constraints=new_constraints)

    @staticmethod
    def rebalance(
        state: PortfolioEngineState,
        port_id: str,
        current_w: dict[str, float],
        trigger: RebalanceTrigger,
        last_ts: float,
        current_ts: float,
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, port_id)
        target_w = state.weights.get(port_id, TargetWeights(port_id, current_ts, {})).weights

        do_rebalance = False
        if trigger == RebalanceTrigger.THRESHOLD:
            do_rebalance = check_threshold_rebalance(current_w, target_w)
        else:
            do_rebalance = check_schedule_rebalance(last_ts, current_ts, trigger)

        if do_rebalance:
            evt = Rebalanced(PortfolioManager._create_id(), current_ts, port_id, trigger.name)
            return replace(state, events=(*state.events, evt))

        return state

    @staticmethod
    def estimate_costs(
        state: PortfolioEngineState,
        port_id: str,
        current_w: dict[str, float],
        model: CostModel,
        capital: float,
        ts: float,
    ) -> PortfolioEngineState:
        validate_portfolio_exists(state, port_id)
        target_w = state.weights.get(port_id, TargetWeights(port_id, ts, {})).weights

        total_trade_fraction = 0.0
        all_syms = set(current_w.keys()) | set(target_w.keys())
        for s in all_syms:
            total_trade_fraction += abs(target_w.get(s, 0.0) - current_w.get(s, 0.0))

        trade_value = total_trade_fraction * capital
        comm = trade_value * model.commission_rate
        slip = trade_value * model.slippage_rate
        impact = trade_value * model.market_impact_rate
        total_cost = comm + slip + impact + model.fixed_exchange_fee

        est = TransactionCostEstimate(port_id, trade_value, comm, slip, impact, total_cost)

        new_costs = dict(state.cost_estimates)
        new_costs[port_id] = est
        return replace(state, cost_estimates=new_costs)
