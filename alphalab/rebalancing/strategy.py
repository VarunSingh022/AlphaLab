"""A construction rule run as a strategy (v4.3, ADR-0063 section 6, N19).

:class:`RebalancingStrategy` decides at a slice's close, when every price of
the instant is known. It values its book, asks its triggers, and when one fires
asks a researcher's :class:`ConstructionModel` for a construction problem. It
constructs the problem, and states a certified answer as lot-rounded
``TARGET_QUANTITY`` intents. The run turns those into orders through
allocation, risk and the OMS, as it does any strategy's: the optimizer never
generates an order (ADR-0063 reopens ADR-0043's non-goal only this far).

* **Its book is its own.** It is the strategy's share of each of its fills
  (:attr:`~alphalab.strategy.events.FillEvent.attributed_quantity`). With its
  share of the orders still working
  (:meth:`~alphalab.strategy.context.OrderFacadeProtocol.net_quantity`), that is
  the position allocation measures its targets against, and the one a model and
  a problem's costs and turnover start from. The context's portfolio is the
  account's, which every strategy of a run shares; in a run of one strategy the
  two agree. Its triggers read the position it has planned -- that, and the
  purchases it holds back -- so a rebalance still being executed is not taken
  for a distance from its own target.
* **Certified answers only.** A construction that is not ``OPTIMAL`` -- or,
  under a cardinality limit, one no certified portfolio keeps -- trades
  nothing. The strategy keeps its book, and its record says why.
* **Every rebalance is recorded** (:attr:`RebalancingStrategy.records`). A
  record carries its problem's and result's identities, the targets it set and
  the correlation id its intents carry, so a run's rebalances reconcile to its
  fills.
* **Its memory is its declared state.** Its book, its last target, its
  triggers' memories and its records are what
  :class:`~alphalab.strategy.protocol.StrategyStateProtocol` captures, so a
  paused run resumes where it was.

Nothing is estimated on the researcher's behalf. A model reads the history,
membership and sectors the slice's context exposes, and states its own risk
model, expected returns and scenarios (ADR-0063 section 6).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.order_terms import OrderTerms
from alphalab.conventions.economics import InstrumentEconomics
from alphalab.instrument.economics import economics_for
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.persistence.decode import as_named_enum
from alphalab.portfolio_optimizer import (
    CardinalityOutcome,
    ConstructionProblem,
    ConstructionStatus,
    LinearCosts,
    construct,
    construct_with_cardinality,
    round_to_lots,
)
from alphalab.rebalancing.capital import CapitalRule
from alphalab.rebalancing.exceptions import RebalancingInputError
from alphalab.rebalancing.triggers import RebalanceMoment, Trigger
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import FillEvent, Intent, IntentKind, SliceClosed
from alphalab.strategy.protocol import BaseStrategy

__all__ = [
    "REBALANCING_STATE_VERSION",
    "ConstructionModel",
    "RebalanceExecution",
    "RebalanceRecord",
    "RebalanceView",
    "RebalancingStrategy",
]

#: The version of :meth:`RebalancingStrategy.capture_state`'s payload.
REBALANCING_STATE_VERSION: int = 1

_ZERO = Decimal(0)


class RebalanceExecution(Enum):
    """How a rebalance's targets are sent: stated, since each is a model of execution (v4.3).

    An account that cannot spend what it does not hold refuses a fill it
    cannot pay for, and a run stops there. Fills arrive in the order the
    assets' next events do, so:

    * ``SIMULTANEOUS`` sends every target at the rebalance's slice. A buy may
      fill before the sale that pays for it, which a margin account can carry
      and a cash account cannot.
    * ``SELLS_FIRST`` sends the targets that sell, and holds the ones that buy
      until every sale has filled. They go at the first slice after the last
      sale, so a cash account's purchases are paid from its sales. A later
      certified rebalance replaces purchases still waiting.

    Either way the targets are sized at the rebalance's prices and fill at
    later ones. A book fully invested at those prices can cost more than it
    holds when it fills, so leave room: a net exposure below one, say.
    """

    SIMULTANEOUS = "simultaneous"
    SELLS_FIRST = "sells_first"


@dataclass(frozen=True, slots=True)
class RebalanceView:
    """What a construction model reads at a rebalance (v4.3, ADR-0063 section 6).

    Attributes:
        strategy_id: The strategy rebalancing.
        instant: The slice's timestamp, in Unix seconds.
        assets: Every asset the strategy may hold, sorted.
        prices: Asset -> its price at the slice, for every asset priced there.
        quantities: Asset -> the strategy's position: its share of every fill
            and of every order still working, as allocation measures its
            targets. For every asset where it is not zero.
        weights: Asset -> the position's value over :attr:`capital`, for every
            asset priced (zero where there is none). A problem's costs and
            turnover start from exactly these.
        capital: What the weights are fractions of, in the prices' currency.
        fired: The renderings of the triggers that fired, each joined.
        context: The slice's context. Its history is bounded at the instant,
            and its universe gives membership, sectors and instruments.
    """

    strategy_id: str
    instant: float
    assets: tuple[str, ...]
    prices: Mapping[str, Decimal]
    quantities: Mapping[str, Decimal]
    weights: Mapping[str, float]
    capital: Decimal
    fired: tuple[str, ...]
    context: StrategyContext


@runtime_checkable
class ConstructionModel(Protocol):
    """A researcher's rule: what the strategy has observed, as a construction problem (v4.3).

    It reads what it needs from the view -- the history to estimate a risk
    model, expected returns or scenarios from, the universe's membership at the
    instant, the book -- and states the problem; the engine estimates nothing
    on its behalf. The problem's universe is among the strategy's assets, each
    priced at the slice. Where it charges costs or limits turnover, it does so
    from the strategy's book, :attr:`RebalanceView.weights`, exactly, and a
    turnover limit keeps every held asset in the universe.

    A model that holds too little to state a problem -- a history shorter than
    its estimate needs -- declines by returning ``None``: nothing is constructed
    or traded, nothing is recorded, and the strategy waits for its triggers to
    fire again.
    """

    def problem(self, view: RebalanceView) -> ConstructionProblem | None:
        """The construction problem for this rebalance, or ``None`` to decline it."""
        ...


@dataclass(frozen=True, slots=True)
class RebalanceRecord:
    """One rebalance: what fired, what was solved, and what was targeted (v4.3).

    Attributes:
        sequence: The rebalance's number in the run, from one.
        instant: The slice it was decided at.
        fired: The renderings, each joined, of the triggers that fired.
        correlation_id: What every intent of this rebalance carries.
        problem_id: The construction problem's identity -- the limited
            problem's, under a cardinality limit.
        result_id: The construction result's identity.
        status: The status of the construction that answered, or that failed.
        cardinality: Under a cardinality limit, how the relaxation ended;
            ``None`` without one.
        detail: Why the status is what it is.
        targets: Asset -> the target quantity set, for every asset of the
            problem's universe and every held asset it leaves (zero). Empty when
            nothing was traded.
        weights: Asset -> the weight each target is, at the slice's prices: the
            target the triggers measure from next.
        residual_weight: What rounding to lots left out:
            ``sum |weight - rounded weight|``.
        below_one_lot: Assets whose weight was not zero and whose target is.
        deferred: Under ``SELLS_FIRST``, the assets whose purchase waited for
            the rebalance's sales; empty otherwise.
    """

    sequence: int
    instant: float
    fired: tuple[str, ...]
    correlation_id: str
    problem_id: str
    result_id: str
    status: ConstructionStatus
    cardinality: CardinalityOutcome | None
    detail: str
    targets: Mapping[str, Decimal]
    weights: Mapping[str, float]
    residual_weight: float
    below_one_lot: tuple[str, ...]
    deferred: tuple[str, ...] = ()

    @property
    def traded(self) -> bool:
        """Whether this rebalance set targets: its construction was certified."""

        return bool(self.targets)


def _record_to_primitives(record: RebalanceRecord) -> dict[str, Any]:
    return {
        "sequence": record.sequence,
        "instant": record.instant,
        "fired": list(record.fired),
        "correlation_id": record.correlation_id,
        "problem_id": record.problem_id,
        "result_id": record.result_id,
        # A plain enum is written as ``Cls.NAME``, the persistence format's one form.
        "status": str(record.status),
        "cardinality": None if record.cardinality is None else str(record.cardinality),
        "detail": record.detail,
        "targets": {asset: str(quantity) for asset, quantity in record.targets.items()},
        "weights": dict(record.weights),
        "residual_weight": record.residual_weight,
        "below_one_lot": list(record.below_one_lot),
        "deferred": list(record.deferred),
    }


def _record_from_primitives(payload: Mapping[str, Any]) -> RebalanceRecord:
    cardinality = payload["cardinality"]
    return RebalanceRecord(
        sequence=int(payload["sequence"]),
        instant=float(payload["instant"]),
        fired=tuple(str(item) for item in payload["fired"]),
        correlation_id=str(payload["correlation_id"]),
        problem_id=str(payload["problem_id"]),
        result_id=str(payload["result_id"]),
        status=as_named_enum(ConstructionStatus, payload["status"], "RebalanceRecord.status"),
        cardinality=(
            None
            if cardinality is None
            else as_named_enum(CardinalityOutcome, cardinality, "RebalanceRecord.cardinality")
        ),
        detail=str(payload["detail"]),
        targets=MappingProxyType(
            {str(asset): Decimal(str(value)) for asset, value in payload["targets"].items()}
        ),
        weights=MappingProxyType(
            {str(asset): float(value) for asset, value in payload["weights"].items()}
        ),
        residual_weight=float(payload["residual_weight"]),
        below_one_lot=tuple(str(item) for item in payload["below_one_lot"]),
        deferred=tuple(str(item) for item in payload["deferred"]),
    )


class RebalancingStrategy(BaseStrategy):
    """A construction rule run as a strategy. See the module docstring.

    Subscribe it to ``slices`` and ``fills``. Every argument is stated; none has
    a default but the cardinality limit, which is absent unless given (AC-12).

    Args:
        strategy_id: The strategy's identity in the run.
        assets: Every asset it may hold: what it prices and values, and the
            most a problem's universe may be.
        model: The researcher's :class:`ConstructionModel`.
        triggers: At least one :class:`~alphalab.rebalancing.triggers.Trigger`;
            it rebalances when any fires.
        capital: What its weights are fractions of
            (:class:`~alphalab.rebalancing.capital.CapitalRule`).
        quantum: The finest quantity step, as
            :func:`~alphalab.portfolio_optimizer.lots.round_to_lots` takes it.
        terms: The order terms its targets are sent with.
        execution: Whether its targets go together or its sales first
            (:class:`RebalanceExecution`).
        max_names: At most this many names, by
            :func:`~alphalab.portfolio_optimizer.construction.construct_with_cardinality`;
            ``None`` for no limit.

    Raises:
        RebalancingInputError: If an argument is malformed.
    """

    def __init__(
        self,
        strategy_id: str,
        *,
        assets: Sequence[str],
        model: ConstructionModel,
        triggers: Sequence[Trigger],
        capital: CapitalRule,
        quantum: Decimal,
        terms: OrderTerms,
        execution: RebalanceExecution,
        max_names: int | None = None,
    ) -> None:
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise RebalancingInputError(f"strategy_id must be named, got {strategy_id!r}.")
        names = tuple(assets)
        if not names or any(not isinstance(a, str) or not a.strip() for a in names):
            raise RebalancingInputError("assets must name at least one asset, each non-blank.")
        if len(set(names)) != len(names):
            raise RebalancingInputError("assets names an asset twice.")
        if not isinstance(model, ConstructionModel):
            raise RebalancingInputError(
                f"{model!r} is not a ConstructionModel: it states no problem."
            )
        rules = tuple(triggers)
        if not rules:
            raise RebalancingInputError("A rebalancing strategy needs at least one trigger.")
        for rule in rules:
            if not isinstance(rule, Trigger):
                raise RebalancingInputError(f"{rule!r} is not a Trigger.")
        if not isinstance(capital, CapitalRule):
            raise RebalancingInputError(f"{capital!r} is not a CapitalRule.")
        if not isinstance(quantum, Decimal) or not quantum.is_finite() or quantum <= 0:
            raise RebalancingInputError(f"quantum is {quantum!r}; it must be a positive Decimal.")
        if not isinstance(terms, OrderTerms):
            raise RebalancingInputError(f"terms must be OrderTerms, got {terms!r}.")
        if not isinstance(execution, RebalanceExecution):
            raise RebalancingInputError(
                f"execution must be a RebalanceExecution, got {execution!r}."
            )
        if max_names is not None and (
            isinstance(max_names, bool) or not isinstance(max_names, int) or max_names < 1
        ):
            raise RebalancingInputError(f"max_names is {max_names!r}; a positive integer or None.")
        self._strategy_id = strategy_id
        self._assets = tuple(sorted(names))
        self._model = model
        self._triggers = rules
        self._capital = capital
        self._quantum = quantum
        self._terms = terms
        self._execution = execution
        self._max_names = max_names
        # Declared state: everything below is captured and restored.
        self._holdings: dict[str, Decimal] = {}
        self._target: dict[str, float] | None = None
        self._memories: list[str | None] = [None] * len(rules)
        self._records: list[RebalanceRecord] = []
        # Under SELLS_FIRST: the purchases waiting, the sales they wait for, and
        # the rebalance they belong to.
        self._pending: dict[str, Decimal] = {}
        self._awaiting: dict[str, Decimal] = {}
        self._pending_sequence: int | None = None

    # -- what it holds and has done --------------------------------------------

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def assets(self) -> tuple[str, ...]:
        return self._assets

    @property
    def holdings(self) -> Mapping[str, Decimal]:
        """Its own book: asset -> its share of every fill, for each asset it holds."""

        return MappingProxyType(dict(sorted(self._holdings.items())))

    @property
    def target(self) -> Mapping[str, float] | None:
        """The weights of its last target, or ``None`` before its first trade."""

        return None if self._target is None else MappingProxyType(dict(self._target))

    @property
    def records(self) -> tuple[RebalanceRecord, ...]:
        """Every rebalance, in order: the traded and the refused alike."""

        return tuple(self._records)

    # -- what it is told -------------------------------------------------------

    def on_fill(self, context: StrategyContext, event: FillEvent) -> Iterable[Intent]:
        share = event.attributed_quantity
        if share is None:
            # An order this strategy asked for alone: the signed fill itself.
            share = event.fill_quantity if event.side == "buy" else -event.fill_quantity
        held = self._holdings.get(event.instrument, _ZERO) + share
        if held:
            self._holdings[event.instrument] = held
        else:
            self._holdings.pop(event.instrument, None)
        return ()

    # -- what it decides -------------------------------------------------------

    def on_slice(self, context: StrategyContext, event: SliceClosed) -> Iterable[Intent]:
        instant = float(event.timestamp)
        decided = self._decide(context, instant)
        if decided is not None:
            return decided
        return self._release(instant)

    def _decide(self, context: StrategyContext, instant: float) -> list[Intent] | None:
        """A new certified target's intents, or ``None`` when this slice set none."""

        prices: dict[str, Decimal] = {}
        economics: dict[str, InstrumentEconomics] = {}
        for asset in self._assets:
            price = context.market.price(asset)
            if price is None or not price.is_finite() or price <= 0:
                continue
            prices[asset] = price
            economics[asset] = _economics(context, asset)
        # Its position, as allocation measures its targets: its share of every fill
        # and of every order still working.
        position: dict[str, Decimal] = {}
        for asset in self._assets:
            held = self._holdings.get(asset, _ZERO) + context.orders.net_quantity(asset)
            if held:
                position[asset] = held
        if any(asset not in prices for asset in position):
            return None  # nothing can value a position in an unpriced asset
        multipliers = {asset: terms.multiplier for asset, terms in economics.items()}
        capital = self._capital.amount(
            context, MappingProxyType(dict(prices)), MappingProxyType(multipliers)
        )
        if capital is None:
            return None
        if not isinstance(capital, Decimal) or not capital.is_finite():
            raise RebalancingInputError(f"The capital rule gave {capital!r}, not a finite Decimal.")
        if capital <= 0:
            return None

        def weigh(quantities: Mapping[str, Decimal]) -> dict[str, float]:
            return {
                asset: float(
                    ACCOUNTING_CONTEXT.divide(
                        ACCOUNTING_CONTEXT.multiply(
                            ACCOUNTING_CONTEXT.multiply(
                                quantities.get(asset, _ZERO), prices[asset]
                            ),
                            multipliers[asset],
                        ),
                        capital,
                    )
                )
                for asset in sorted(prices)
            }

        weights = weigh(position)
        # The triggers read the position it has planned: what is working, and the
        # purchases it holds back, as if they had filled. A rebalance still being
        # executed is not a distance from its own target.
        planned = weigh({**position, **self._pending})
        moment = RebalanceMoment(
            instant,
            MappingProxyType(planned),
            None if self._target is None else MappingProxyType(dict(self._target)),
        )
        fired: list[int] = []
        for index, trigger in enumerate(self._triggers):
            fires, memory = trigger.fires(moment, self._memories[index])
            if memory is not None and not isinstance(memory, str):
                raise RebalancingInputError(
                    f"Trigger {index} kept {memory!r} as its memory; a memory is a string or None."
                )
            self._memories[index] = memory
            if fires:
                fired.append(index)
        if not fired:
            return None
        return self._rebalance(
            context,
            instant,
            prices,
            economics,
            capital,
            position,
            weights,
            tuple(" ".join(self._triggers[index].rendering()) for index in fired),
        )

    def _rebalance(
        self,
        context: StrategyContext,
        instant: float,
        prices: Mapping[str, Decimal],
        economics: Mapping[str, InstrumentEconomics],
        capital: Decimal,
        position: Mapping[str, Decimal],
        weights: Mapping[str, float],
        fired: tuple[str, ...],
    ) -> list[Intent] | None:
        sequence = len(self._records) + 1
        correlation = f"{self._strategy_id}:rebalance:{sequence}"
        view = RebalanceView(
            strategy_id=self._strategy_id,
            instant=instant,
            assets=self._assets,
            prices=MappingProxyType(dict(prices)),
            quantities=MappingProxyType(dict(sorted(position.items()))),
            weights=MappingProxyType(dict(weights)),
            capital=capital,
            fired=fired,
            context=context,
        )
        problem = self._model.problem(view)
        if problem is None:
            return None  # the model declines: it holds too little to state a problem
        if not isinstance(problem, ConstructionProblem):
            raise RebalancingInputError(f"The model gave {problem!r}, not a ConstructionProblem.")
        universe = problem.universe
        outside = sorted(set(universe) - set(self._assets))
        if outside:
            raise RebalancingInputError(
                f"The model's problem weights {outside}, which the strategy was not given."
            )
        unpriced = sorted(asset for asset in universe if asset not in prices)
        if unpriced:
            raise RebalancingInputError(
                f"The model's problem weights {unpriced}, which have no price at this slice: "
                "no target can be sized for them."
            )
        self._check_book(problem, view, position)

        cardinality: CardinalityOutcome | None = None
        if self._max_names is None:
            result = construct(problem)
            answer = result.weights if result.succeeded else None
            status, detail = result.status, result.diagnostics.detail
            problem_id, result_id = result.problem_id, result.result_id
        else:
            limited = construct_with_cardinality(problem, self._max_names)
            answer = limited.weights
            step = limited.restricted if limited.restricted is not None else limited.relaxed
            status, detail = step.status, limited.detail
            problem_id, result_id = limited.problem_id, limited.result_id
            cardinality = limited.outcome

        if answer is None:
            self._records.append(
                RebalanceRecord(
                    sequence=sequence,
                    instant=instant,
                    fired=fired,
                    correlation_id=correlation,
                    problem_id=problem_id,
                    result_id=result_id,
                    status=status,
                    cardinality=cardinality,
                    detail=detail,
                    targets=MappingProxyType({}),
                    weights=MappingProxyType({}),
                    residual_weight=0.0,
                    below_one_lot=(),
                )
            )
            return None  # not certified: nothing new is traded

        rounding = round_to_lots(
            dict(answer),
            capital=capital,
            prices={asset: prices[asset] for asset in universe},
            economics={asset: economics[asset] for asset in universe},
            quantum=self._quantum,
        )
        targets = dict(rounding.quantities)
        target_weights = dict(rounding.weights)
        for asset in position:
            if asset not in targets:
                # An asset held or working that the problem leaves: sold to nothing.
                targets[asset] = _ZERO
                target_weights[asset] = 0.0
        targets = dict(sorted(targets.items()))
        target_weights = dict(sorted(target_weights.items()))
        sent = targets
        self._pending, self._awaiting, self._pending_sequence = {}, {}, None
        if self._execution is RebalanceExecution.SELLS_FIRST:
            sales = {a: q for a, q in targets.items() if q < position.get(a, _ZERO)}
            purchases = {a: q for a, q in targets.items() if q > position.get(a, _ZERO)}
            if sales and purchases:
                sent = sales
                self._pending, self._awaiting = purchases, sales
                self._pending_sequence = sequence
        self._records.append(
            RebalanceRecord(
                sequence=sequence,
                instant=instant,
                fired=fired,
                correlation_id=correlation,
                problem_id=problem_id,
                result_id=result_id,
                status=status,
                cardinality=cardinality,
                detail=detail,
                targets=MappingProxyType(targets),
                weights=MappingProxyType(target_weights),
                residual_weight=rounding.residual_weight,
                below_one_lot=rounding.below_one_lot,
                deferred=tuple(sorted(self._pending)),
            )
        )
        self._target = target_weights
        return self._intents(self._records[-1], sent, instant)

    def _intents(
        self, record: RebalanceRecord, targets: Mapping[str, Decimal], instant: float
    ) -> list[Intent]:
        metadata = {
            "rebalance": str(record.sequence),
            "problem_id": record.problem_id,
            "result_id": record.result_id,
        }
        return [
            Intent(
                strategy_id=self._strategy_id,
                instrument=asset,
                target=quantity,
                timestamp=instant,
                kind=IntentKind.TARGET_QUANTITY,
                terms=self._terms,
                correlation_id=record.correlation_id,
                metadata=metadata,
            )
            for asset, quantity in sorted(targets.items())
        ]

    def _release(self, instant: float) -> list[Intent]:
        """The purchases a rebalance held back, once every one of its sales has filled."""

        if not self._pending or self._pending_sequence is None:
            return []
        if any(self._holdings.get(a, _ZERO) != q for a, q in self._awaiting.items()):
            return []
        record = self._records[self._pending_sequence - 1]
        intents = self._intents(record, self._pending, instant)
        self._pending, self._awaiting, self._pending_sequence = {}, {}, None
        return intents

    def _check_book(
        self, problem: ConstructionProblem, view: RebalanceView, position: Mapping[str, Decimal]
    ) -> None:
        """A problem's costs and turnover start from the strategy's position, exactly."""

        sources: list[tuple[str, Mapping[str, float]]] = []
        turnover = problem.constraints.turnover
        if turnover is not None:
            left = sorted(asset for asset in position if asset not in problem.universe)
            if left:
                raise RebalancingInputError(
                    f"The problem limits turnover and leaves {left}, which the strategy holds: "
                    "their sale is turnover the limit cannot see. Keep them in its universe."
                )
            sources.append(("turnover", turnover.current))
        costs = getattr(problem.objective, "costs", None)
        if isinstance(costs, LinearCosts):
            sources.append(("costs", costs.current))
        for name, current in sources:
            differ = sorted(
                asset
                for asset in problem.universe
                if current.get(asset) != view.weights.get(asset, 0.0)
            )
            if differ:
                raise RebalancingInputError(
                    f"The problem's {name} start from a book other than the strategy's: {differ} "
                    "differ from RebalanceView.weights."
                )

    # -- what it remembers -----------------------------------------------------

    def strategy_state_version(self) -> int:
        return REBALANCING_STATE_VERSION

    def capture_state(self) -> dict[str, Any]:
        return {
            "holdings": {
                asset: str(quantity) for asset, quantity in sorted(self._holdings.items())
            },
            "target": None if self._target is None else dict(self._target),
            "memories": list(self._memories),
            "records": [_record_to_primitives(record) for record in self._records],
            "pending": {asset: str(quantity) for asset, quantity in sorted(self._pending.items())},
            "awaiting": {
                asset: str(quantity) for asset, quantity in sorted(self._awaiting.items())
            },
            "pending_sequence": self._pending_sequence,
        }

    def restore_state(self, payload: Any, version: int) -> None:
        if version != REBALANCING_STATE_VERSION:
            raise RebalancingInputError(
                f"RebalancingStrategy reads state version {REBALANCING_STATE_VERSION}, "
                f"not {version}."
            )
        memories = [None if item is None else str(item) for item in payload["memories"]]
        if len(memories) != len(self._triggers):
            raise RebalancingInputError(
                f"The state holds {len(memories)} trigger memories for {len(self._triggers)} "
                "triggers: it was captured from another strategy."
            )
        target = payload["target"]
        self._holdings = {
            str(asset): Decimal(str(quantity)) for asset, quantity in payload["holdings"].items()
        }
        self._target = (
            None
            if target is None
            else {str(asset): float(value) for asset, value in target.items()}
        )
        self._memories = memories
        self._records = [_record_from_primitives(item) for item in payload["records"]]
        self._pending = {
            str(asset): Decimal(str(quantity)) for asset, quantity in payload["pending"].items()
        }
        self._awaiting = {
            str(asset): Decimal(str(quantity)) for asset, quantity in payload["awaiting"].items()
        }
        sequence = payload["pending_sequence"]
        self._pending_sequence = None if sequence is None else int(sequence)


def _economics(context: StrategyContext, asset: str) -> InstrumentEconomics:
    """What the run books ``asset`` by: its registry record's economics, read by the one rule.

    :func:`~alphalab.instrument.economics.economics_for` -- what the record
    declares, or a cash equity for the types that are one -- is the authority
    allocation and the accounting read, so a target is sized as it will be
    booked (AC-15). A future or an option that declares nothing is refused
    there, and so here: its multiplier cannot be assumed.
    """

    record = context.universe.instrument(asset)
    if not isinstance(record, InstrumentRecord):
        raise RebalancingInputError(
            f"{asset!r} is not in the run's instrument registry, so its multiplier and lot "
            "cannot be known: a rebalancing strategy sizes only what the run declares."
        )
    try:
        return economics_for(record.asset_type, record.economics, record.symbol)
    except InstrumentInputError as error:
        raise RebalancingInputError(str(error)) from error
