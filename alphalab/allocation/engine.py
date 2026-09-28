"""Pure functional Allocation Engine."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from types import MappingProxyType
from typing import Final

from alphalab.allocation.allocator import IntentAllocator
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import (
    AllocationCompleted,
    AllocationExecutionApplied,
    AllocationRejected,
    AllocationReservationReleased,
    AllocationStarted,
    BudgetExceeded,
    NettingCompleted,
)
from alphalab.allocation.exceptions import (
    AllocationValidationError,
    SizingRefusedError,
    UnknownReservationError,
)
from alphalab.allocation.netting import NettingEngine
from alphalab.allocation.sizing import QUANTITY_QUANTUM, SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.allocation.validation import (
    validate_intent,
    validate_long_only,
    validate_net_quantity,
)
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context
from alphalab.common.ids import new_id
from alphalab.common.order_terms import OrderTerms
from alphalab.common.persistent_map import PersistentMap
from alphalab.conventions.lot import LotSpecification, round_down_to_lot
from alphalab.core.contribution import StrategyContribution, split_by_contribution
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.strategy.events import Intent, IntentKind


class AllocationEngine:
    """Stateless engine responsible for sizing, netting, and budgeting."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(budget: CapitalBudget) -> AllocationState:
        """Returns a fresh allocation state initialized with provided budget."""
        return AllocationState(budget=budget)

    @staticmethod
    @in_accounting_context
    def allocate(
        state: AllocationState,
        intents: Sequence[Intent],
        market_prices: Mapping[str, Decimal],
        sizing_model: SizingModel,
        constraints: AllocationConstraints,
        timestamp: float,
        budget_prices: Mapping[str, Decimal] = MappingProxyType({}),
        *,
        positions: Mapping[str, Decimal] | None = None,
        working: Mapping[tuple[str, str], Decimal] = MappingProxyType({}),
        lots: Mapping[str, LotSpecification] = MappingProxyType({}),
        minimum_notionals: Mapping[str, Decimal] = MappingProxyType({}),
        multipliers: Mapping[str, Decimal] = MappingProxyType({}),
    ) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
        """
                Processes a batch of intents, sizes them, applies cross-strategy netting,
                checks capital budgets, and emits netted OrderRequests.

        ``positions`` is each asset's **committed** signed position -- filled plus
                working orders, in units -- as plain numbers, so this package goes on
                knowing nothing of the portfolio. Long-only
                (``AllocationConstraints.allow_shorting=False``) is checked against it:
                a sale that closes a long passes and one that would leave a short is
                refused. ``None`` says the positions are unknown, and long-only then
                refuses every sale, because a delta alone cannot tell a close from a
                short; an empty mapping says the account is flat. Until v3.10
                long-only refused every sale whatever the caller held (ledger ALC-001).

        An intent a sizing model cannot size -- no positive price, no volatility --
                is recorded as an ``AllocationRejected`` naming it, and the others are
                sized (ledger ALC-003). With ``enforce_integer_quantities`` each netted
                delta is rounded to the nearest unit *before* long-only is checked,
                and one that rounds to zero emits no order: until v3.10 it emitted a
                zero-quantity SELL, which the risk gate refuses by raising.

        ``budget_prices`` is the same assets priced in the **budget's** currency, and
                it is empty for the single-currency run that is every run before v2.17. An
                asset absent from it is priced by ``market_prices``, which is what this
                always did.

                The two maps exist because two different questions are being asked, and
                the asymmetry is deliberate:

                * ``market_prices`` prices an **order**, so it is in the instrument's own
                  currency -- that is what the venue executes at and what the fill will be
                  denominated in. ``OrderRequest.price`` comes from here and is unchanged.
                * ``budget_prices`` prices a **comparison against one number**.
                  ``notional_allocated``, ``reservations`` and every budget ceiling are
                  figures in the budget's currency, and adding a JPY notional to them
                  unconverted would be the "figure in no currency at all" ADR-0020 removed
                  from valuation -- silently mis-sizing every later order.

                **This engine performs no conversion and knows nothing about exchange
                rates.** It takes a second price map and does arithmetic. Whoever supplies
                it converted, and is where the rate and its provenance live -- see
                :func:`alphalab.runtime.execution_pipeline._budget_prices`. That keeps
                ``alphalab.allocation`` independent of ``alphalab.portfolio``, which it
                has always been and which importing an ``FxRates`` here would have ended:
                measured, it pulled all eighteen portfolio modules into a package that
                previously imported none of them.

        **Target intents** (ledger FEA-001) are not sized by ``sizing_model``: the
                order is the difference between the target and what the strategy
                already has -- its own position from :attr:`AllocationState.strategy_positions`
                plus ``working``, its signed share of each order still working, keyed
                ``(strategy_id, asset_id)`` -- rounded toward zero onto the asset's
                ``lots`` grid. A difference that rounds to nothing asks for nothing;
                one whose value is below the asset's ``minimum_notionals`` is refused
                and recorded, never scaled up. Every netted order for an asset with a
                declared lot must itself be a whole number of lots, or it is refused
                with the reason: rounding a strategy's stated delta would place an
                order nobody asked for.

        ``multipliers`` is each asset's contract multiplier, where it is not one
                (ledger ACC-005): a unit of a contract on fifty units of an index is
                worth fifty times its price, and every figure that values a quantity
                -- a sizing model's, a target weight's, a minimum notional's and the
                budget's -- values it so. ``OrderRequest.price`` stays the price.
        """
        events = state.events.append(
            AllocationStarted(AllocationEngine._create_id(), timestamp, len(intents))
        )

        # 1. Validation
        valid_intents = []
        for intent in intents:
            try:
                validate_intent(intent)
                # Reject duplicates dynamically in batch
                if intent in valid_intents:
                    continue
                valid_intents.append(intent)
            except Exception as e:
                events = events.append(
                    AllocationRejected(AllocationEngine._create_id(), timestamp, str(e))
                )

        if not valid_intents:
            return replace(state, events=events), ()

        # 2. Sizing, one intent at a time: a refusal names its intent and the
        # rest of the batch is sized. A model that sizes by value reads what one
        # unit is worth, which the multiplier scales.
        unit_prices = _per_unit(market_prices, multipliers)
        unit_budget_prices = _per_unit(budget_prices, multipliers)
        sized_deltas: list[tuple[str, str, OrderTerms, Decimal]] = []
        for intent in valid_intents:
            try:
                if intent.kind is IntentKind.DELTA:
                    quantity = IntentAllocator.size_intent(
                        intent, state.budget, unit_prices, sizing_model
                    )
                else:
                    quantity = _target_delta(
                        state,
                        intent,
                        unit_prices,
                        unit_budget_prices,
                        working,
                        lots.get(intent.instrument),
                        minimum_notionals.get(intent.instrument),
                    )
            except SizingRefusedError as refusal:
                events = events.append(
                    AllocationRejected(AllocationEngine._create_id(), timestamp, str(refusal))
                )
                continue
            if intent.kind is not IntentKind.DELTA and quantity == 0:
                # The strategy already holds its target, to the nearest lot.
                continue
            sized_deltas.append((intent.strategy_id, intent.instrument, intent.terms, quantity))

        # 3. Netting, per asset and terms: only equal terms net (EXE-003).
        net_quantities = NettingEngine.net_by_terms(sized_deltas)
        contributions_by_key = NettingEngine.contributions_by_terms(sized_deltas)

        # 4. Enforce constraints & Budget Pre-check
        total_notional = Decimal("0.00")
        orders: list[OrderRequest] = []
        # Long-only judges each order against the position the batch's earlier
        # orders for the same asset leave, since one asset can now take two.
        projected = dict(positions) if positions is not None else None

        for (asset_id, terms), net_qty in net_quantities.items():
            if constraints.enforce_integer_quantities:
                # Nearest unit, ties to even, before anything reads the quantity:
                # long-only judges the order that will be sent.
                net_qty = net_qty.to_integral_value(rounding=ROUND_HALF_EVEN)
            if net_qty == 0:
                continue

            try:
                validate_net_quantity(net_qty)
                lot = lots.get(asset_id)
                if lot is not None and not lot.admits(net_qty):
                    raise AllocationValidationError(
                        f"An order for {net_qty} of {asset_id} is not a whole number of its "
                        f"{lot.lot_size} lots at or above {lot.minimum_quantity}; it is "
                        "refused rather than rounded to an order nobody asked for."
                    )
                if not constraints.allow_shorting:
                    if projected is None:
                        validate_net_quantity(net_qty, enforce_long_only=True)
                    else:
                        held = projected.get(asset_id, Decimal("0"))
                        validate_long_only(asset_id, net_qty, held)
                        projected[asset_id] = held + net_qty
            except AllocationValidationError as e:
                events = events.append(
                    AllocationRejected(AllocationEngine._create_id(), timestamp, str(e))
                )
                continue

            side = Side.BUY if net_qty > Decimal("0") else Side.SELL
            abs_qty = abs(net_qty)
            price = market_prices.get(asset_id, Decimal("0.00"))

            # Two prices, two questions. ``price`` denominates the order;
            # ``budget_price`` denominates the comparison against the budget --
            # one unit's value, in the budget's currency. They are the same
            # number unless the budget is in another currency or the instrument
            # has a multiplier.
            budget_price = unit_budget_prices.get(asset_id, unit_prices.get(asset_id, price))
            # Committed capital is a magnitude: a contract priced below zero
            # commits as much as one priced as far above it (ACC-007).
            total_notional += abs_qty * budget_price.copy_abs()

            events = events.append(
                NettingCompleted(
                    AllocationEngine._create_id(), timestamp, asset_id, abs_qty, side.name
                )
            )

            orders.append(
                OrderRequest(
                    order_id=AllocationEngine._create_id(),
                    # A netted order can represent several strategies, so it has
                    # no single owner and says so. Until v2.6 this carried the
                    # fabricated "ALLOC-NETTED", which the OMS then indexed as
                    # though it were a strategy. Attribution reads
                    # ``contributions``. See ADR-0015 decision 4.
                    strategy_id="",
                    asset_id=asset_id,
                    side=side,
                    quantity=abs_qty,
                    price=price,
                    timestamp=timestamp,
                    contributions=contributions_by_key.get((asset_id, terms), ()),
                    terms=terms,
                )
            )

        # 5. Budget Application
        #
        # Capital already committed to orders that have not settled counts
        # against the budget. Before v2.6 only ``total_notional`` was compared,
        # so the whole budget was re-offered on every market event no matter how
        # much was already reserved. Under SIMULATED routing a reservation is
        # consumed or released inside the event that created it, so this was
        # invisible; under EXTERNAL routing the order stays working and its
        # reservation stays held, and six events committed 5.4x the budget with
        # no position held and no rejection recorded.
        #
        # ``CapitalBudget`` itself is untouched: it remains an immutable sizing
        # parameter, and the commitment lives in ``notional_allocated``. See
        # ADR-0015 decision 1 for why the budget is not made consumable.
        outstanding = state.notional_allocated
        committed = outstanding + total_notional
        remaining = state.budget.available_global_capital - outstanding
        if committed > state.budget.available_global_capital or (
            committed > state.budget.maximum_exposure
        ):
            reason = (
                "Requested notional plus outstanding commitment exceeds global "
                "capital or exposure limits."
            )
            events = events.append(
                BudgetExceeded(
                    AllocationEngine._create_id(), timestamp, reason, total_notional, remaining
                )
            )
            # Strict rejection mode: if batch breaches budget, drop batch. The
            # returned state differs from the input only by these events --
            # history, reservations and notional_allocated are untouched, so a
            # rejected allocation changes no prior reservation.
            return replace(state, events=events), ()

        # 6. Finalization
        events = events.append(
            AllocationCompleted(
                AllocationEngine._create_id(), timestamp, len(orders), total_notional
            )
        )

        # Every emitted request reserves its own notional, so the capital held
        # against it can later be consumed or released by order id.
        reservations = state.reservations
        contributions = state.contributions
        for order in orders:
            # In the budget's currency, like the total it is a part of.
            unit = unit_budget_prices.get(
                order.asset_id, unit_prices.get(order.asset_id, order.price)
            )
            reservations = reservations.set(order.order_id, order.quantity * unit.copy_abs())
            contributions = contributions.set(order.order_id, order.contributions)

        new_state = replace(
            state,
            history=state.history.extend(orders),
            events=events,
            notional_allocated=state.notional_allocated + total_notional,
            reservations=reservations,
            contributions=contributions,
        )

        return new_state, tuple(orders)

    @staticmethod
    def reserved_notional(state: AllocationState, order_id: str) -> Decimal:
        """Capital still held against ``order_id``; zero if it holds none."""

        return state.reservations.get(order_id, Decimal("0.00"))

    @staticmethod
    def contributions_for(
        state: AllocationState, order_id: str
    ) -> tuple[StrategyContribution, ...]:
        """Who asked for ``order_id``; empty once the order has been retired.

        Read this *before* the order reaches a terminal state: post-trade
        attribution needs it at fill time, and
        :meth:`retire_contributions` drops it when the order's life ends.
        """

        return state.contributions.get(order_id, ())

    @staticmethod
    def retire_contributions(state: AllocationState, order_id: str) -> AllocationState:
        """Drop the contribution ledger entry for an order whose life has ended.

        Retiring on the *terminal transition* rather than on reservation
        exhaustion is deliberate: a reservation can be exhausted by a fill while
        the order is still working, and a contribution describes the order, not
        the capital. Sharing the reservation's retirement point would also have
        inherited its defect -- see ADR-0015 decision 5.
        """

        if order_id not in state.contributions:
            return state
        return replace(state, contributions=state.contributions.delete(order_id))

    @staticmethod
    @in_accounting_context
    def record_fill(
        state: AllocationState, order_id: str, asset_id: str, signed_quantity: Decimal
    ) -> AllocationState:
        """Add a fill to the positions of the strategies that asked for its order.

        The fill is divided by contribution --
        :func:`~alphalab.core.contribution.split_by_contribution`, the rule
        realized P&L is divided by -- so a strategy's position is its own share
        of every order it took part in. Read the contributions before the order
        is retired: an order with none (placed outside allocation) is nobody's,
        and a run whose positions were never recorded (``None``) is left so.
        """

        positions = state.strategy_positions
        contributions = state.contributions.get(order_id)
        if positions is None or not contributions or signed_quantity == 0:
            return state
        for strategy_id, share in split_by_contribution(
            signed_quantity, contributions, _POSITION_QUANTUM
        ):
            held = positions.get(strategy_id, PersistentMap())
            total = held.get(asset_id, Decimal("0")) + share
            positions = positions.set(strategy_id, held.set(asset_id, total))
        return replace(state, strategy_positions=positions)

    @staticmethod
    @in_accounting_context
    def apply_split(state: AllocationState, asset_id: str, ratio: Decimal) -> AllocationState:
        """Each strategy's position in ``asset_id`` restated in post-split units (ACC-006).

        A split multiplies what everyone holds by the ratio, a strategy's share
        included; a target measured against the old count would ask for the
        split back as a trade. Nothing is recorded where nothing was.
        """

        positions = state.strategy_positions
        if positions is None:
            return state
        restated = positions
        for strategy_id, held in positions.items():
            quantity = held.get(asset_id)
            if quantity is not None:
                restated = restated.set(strategy_id, held.set(asset_id, quantity * ratio))
        return state if restated is positions else replace(state, strategy_positions=restated)

    @staticmethod
    def strategy_position(state: AllocationState, strategy_id: str, asset_id: str) -> Decimal:
        """A strategy's own signed position in an asset; zero when it holds none.

        Raises:
            SizingRefusedError: If the run's positions were never recorded.
        """

        positions = state.strategy_positions
        if positions is None:
            raise SizingRefusedError(_UNRECORDED)
        return positions.get(strategy_id, PersistentMap()).get(asset_id, Decimal("0"))

    @staticmethod
    @in_accounting_context
    def apply_execution(
        state: AllocationState,
        order_id: str,
        executed_notional: Decimal,
        timestamp: float,
    ) -> AllocationState:
        """Consume executed notional from the capital reserved for an order.

        A fill consumes the reservation up to what it executed. A fully
        executed order's entry is dropped from the ledger; a partial fill
        leaves the residual reserved, because the order is still working and
        that capital is still committed.

        ``executed_notional`` is in the **budget's** currency, like the
        reservation it consumes. A caller settling in another converts before
        calling: consuming a JPY notional from a USD reservation would free the
        wrong amount of capital, and would do it silently. The pipeline does that
        conversion in :func:`alphalab.runtime.execution_pipeline._apply_reports`,
        which is where the rate lives.
        """

        reserved = state.reservations.get(order_id)
        if reserved is None:
            # The order holds no reservation (it was released, or fully
            # consumed by earlier fills). Record the execution; nothing to free.
            consumed = Decimal("0.00")
            reservations = state.reservations
        else:
            consumed = min(reserved, executed_notional)
            remaining = reserved - consumed
            reservations = (
                state.reservations.delete(order_id)
                if remaining <= Decimal("0.00")
                else state.reservations.set(order_id, remaining)
            )

        evt = AllocationExecutionApplied(
            AllocationEngine._create_id(), timestamp, order_id, executed_notional
        )
        return replace(
            state,
            notional_allocated=state.notional_allocated - consumed,
            reservations=reservations,
            events=state.events.append(evt),
        )

    @staticmethod
    @in_accounting_context
    def release_reservation(
        state: AllocationState, order_id: str, timestamp: float
    ) -> AllocationState:
        """Release whatever capital ``order_id`` still holds.

        Called once, at the point a request's lifecycle ends without further
        execution: risk rejected it, it was dropped before reaching the OMS, or
        the venue returned a non-trading outcome. The amount comes from the
        ledger rather than from the caller, so a release can neither free more
        than was reserved nor free the same reservation twice.

        Raises:
            UnknownReservationError: if the order holds no live reservation.
        """

        released = state.reservations.get(order_id)
        if released is None:
            raise UnknownReservationError(
                f"Order {order_id} holds no allocation reservation to release."
            )

        evt = AllocationReservationReleased(
            AllocationEngine._create_id(), timestamp, order_id, released
        )
        return replace(
            state,
            notional_allocated=state.notional_allocated - released,
            reservations=state.reservations.delete(order_id),
            events=state.events.append(evt),
        )


def _per_unit(
    prices: Mapping[str, Decimal], multipliers: Mapping[str, Decimal]
) -> Mapping[str, Decimal]:
    """``prices`` with each multiplied asset's price scaled to one unit's value.

    The same mapping, untouched, when nothing has a multiplier -- which is every
    run that declares none, so its arithmetic is exactly what it was.
    """

    if not multipliers or not prices:
        return prices
    scaled = dict(prices)
    for asset_id, multiplier in multipliers.items():
        price = scaled.get(asset_id)
        if price is not None:
            scaled[asset_id] = price * multiplier
    return scaled


#: The unit a fill is divided among strategies in for their positions; every
#: part but the last is rounded to it and the last takes the remainder, so the
#: parts sum to the fill exactly.
_POSITION_QUANTUM: Final = Decimal("1E-12")

_UNRECORDED: Final = (
    "This run's per-strategy positions were not recorded -- it began before v3.11 -- so a "
    "target cannot be measured against them; state a delta instead."
)


def _target_delta(
    state: AllocationState,
    intent: Intent,
    market_prices: Mapping[str, Decimal],
    budget_prices: Mapping[str, Decimal],
    working: Mapping[tuple[str, str], Decimal],
    lot: LotSpecification | None,
    minimum_notional: Decimal | None,
) -> Decimal:
    """The signed quantity that takes a strategy from what it has to its target.

    Raises:
        SizingRefusedError: If the positions were never recorded, the asset has
            no positive price, or the order is below the minimum notional.
    """

    held = AllocationEngine.strategy_position(state, intent.strategy_id, intent.instrument)
    price = market_prices.get(intent.instrument)
    if price is None or not price.is_finite():
        raise SizingRefusedError(
            f"Cannot rebalance {intent.strategy_id} in {intent.instrument}: it has no price "
            f"(got {price}), and a target is reached by an order that must be priced."
        )
    if intent.kind is IntentKind.TARGET_WEIGHT and price <= 0:
        # A weight is a value, and a value cannot be sized at a price that is
        # not positive. A quantity can: an instrument whose economics allow a
        # negative price trades at one (ACC-007).
        raise SizingRefusedError(
            f"Cannot rebalance {intent.strategy_id} in {intent.instrument} to a weight: it has "
            f"no positive price (got {price}); state a target quantity instead."
        )
    ctx = ACCOUNTING_CONTEXT
    scale = ctx.multiply(intent.target, intent.strength)
    if intent.kind is IntentKind.TARGET_QUANTITY:
        target = scale
    else:
        capital = state.budget.available_strategy_capital(intent.strategy_id)
        target = ctx.divide(
            ctx.multiply(capital, scale), budget_prices.get(intent.instrument, price)
        )
    committed = ctx.add(held, working.get((intent.strategy_id, intent.instrument), Decimal("0")))
    # Toward zero: a target is approached, never overshot by rounding.
    delta = ctx.subtract(target, committed).quantize(
        QUANTITY_QUANTUM, rounding=ROUND_DOWN, context=ctx
    )
    if lot is not None:
        delta = round_down_to_lot(delta, lot)
    if delta == 0:
        return delta
    notional = ctx.multiply(abs(delta), price.copy_abs())
    if minimum_notional is not None and notional < minimum_notional:
        raise SizingRefusedError(
            f"Rebalancing {intent.strategy_id} to its target in {intent.instrument} needs "
            f"{delta}, worth {notional}, below the {minimum_notional} minimum; it is refused "
            "rather than scaled up."
        )
    return delta
