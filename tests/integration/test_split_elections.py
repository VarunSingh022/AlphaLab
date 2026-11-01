"""Elections split between holders, part elections, proration and record instants (FUT-030).

An :class:`ElectiveExchange` exchanges each holder's share of the one holding
by its own election -- the whole of it, or the units each election names --
and what a holder does not elect, or elects beyond what it may, by the default.
An alternative the issuer prorates accepts its declared factor of each
election, the default the rest; a record instant bounds each election by what
its holder held then. Each alternative is applied to its electors' part of the
holding, its basis apportioned by units; one holder, or holders agreeing,
exchange the holding whole, as before.

OLDCO is bought at 100 on 2.0; the action -- half a NEWCO share (the default)
or 120 in cash for each OLDCO -- is announced at 2.2, closes its elections at
2.8 and takes effect at 3.0 (the helpers are
tests/integration/test_elective_exchanges.py's).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.backtesting import (
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.portfolio.events import PositionExchanged
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import HoldersRecorded, Intent, IntentKind, UnitsElected
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.validation import validate_intent
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory

D = Decimal
OLD, NEW = exchanges.OLD, exchanges.NEW
STEPS = elective.STEPS
MIDS = {OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}}


class Holder(elective.Elector):
    """Buys by its plan; elects ``choices`` -- instant to (alternative, units or None)."""

    def __init__(
        self,
        plan: dict[tuple[float, str], Decimal],
        choices: dict[float, tuple[int, str | None]],
    ) -> None:
        super().__init__(plan, {})
        self._parts = choices

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        out = list(super().on_quote(context, event))
        at = event.quote.timestamp
        choice = self._parts.get(at) if event.quote.asset_id == OLD.asset_id else None
        if choice is not None:
            alternative, units = choice
            metadata = {"action": elective.REF, **({} if units is None else {"units": units})}
            out.append(
                Intent(
                    self.sid,
                    OLD.asset_id,
                    D(alternative),
                    timestamp=at,
                    kind=IntentKind.ELECT,
                    metadata=metadata,
                )
            )
        return tuple(out)


def buys(units: str, at: float = 2.0) -> dict[tuple[float, str], Decimal]:
    return {(at, OLD.asset_id): D(units)}


def run(*holders: tuple[str, Holder], **kw: Any) -> Any:
    return exchanges.run(
        elective.STOCK,
        OLD,
        NEW,
        mids=MIDS,
        strategies=elective.strategies(*holders),
        schedule=RunSchedule.of(exchanges=[elective.action(**kw)]),
    )


def share(result: Any, sid: str, record: Any) -> Decimal:
    return dict(AllocationEngine.shares_of(result.state.allocation, record.asset_id)).get(
        sid, D("0")
    )


def new_held(result: Any) -> Decimal:
    position = exchanges.position(result, NEW)
    return D("0") if position is None else position.quantity


# --------------------------------------------------------------------------- #
# Each holder by its own election
# --------------------------------------------------------------------------- #


def test_holders_electing_differently_each_receive_their_own_election() -> None:
    result = run(("A", Holder(buys("50"), {2.5: (1, None)})), ("B", Holder(buys("50"), {})))
    # A's 50 take cash, 6,000; B, electing nothing, takes the default: 25 NEWCO.
    assert exchanges.position(result, OLD) is None
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("6000")
    assert (new_held(result), share(result, "B", NEW), share(result, "A", NEW)) == (
        D("25"),
        D("25"),
        D("0"),
    )
    exchanged = [e for e in result.state.portfolio.events if isinstance(e, PositionExchanged)]
    assert sorted((e.quantity, tuple(e.consideration.items())) for e in exchanged) == [
        (D("50"), ()),
        (D("50"), (("USD", D("6000.00")),)),
    ]


def test_part_of_a_holding_elects_one_alternative_and_the_rest_the_default() -> None:
    result = run(("S", Holder(buys("100"), {2.5: (1, "40")})))
    # 40 for cash -- 4,800, realizing 800 on their 4,000 of the basis -- and 60
    # by the default, 30 NEWCO carrying the other 6,000.
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("4800")
    assert result.state.portfolio.realized_pnl["USD"] == D("800.00")
    position = exchanges.position(result, NEW)
    assert (position.quantity, position.basis) == (D("30"), D("6000.00"))
    (made,) = [e for e in result.state.strategy.events if isinstance(e, UnitsElected)]
    assert (made.alternative, made.units) == (1, D("40"))


def test_a_later_election_replaces_an_earlier_one() -> None:
    whole_then_part = run(("S", Holder(buys("100"), {2.4: (1, None), 2.6: (1, "40")})))
    part_then_whole = run(("S", Holder(buys("100"), {2.4: (1, "40"), 2.6: (1, None)})))
    assert new_held(whole_then_part) == D("30")
    assert new_held(part_then_whole) == D("0")
    # A part election replaces a whole one for another alternative too: the
    # stock for all, then 40 for cash, is 40 for cash and the rest the default.
    stock_then_cash = run(("S", Holder(buys("100"), {2.4: (0, None), 2.6: (1, "40")})))
    assert exchanges.usd(stock_then_cash) == exchanges.START - D("10000") + D("4800")


def test_an_election_for_more_than_is_held_is_not_counted() -> None:
    result = run(("S", Holder(buys("100"), {2.5: (1, "140")})))
    # Electing 140 of 100: not counted; the default takes the whole holding.
    assert new_held(result) == D("50")
    assert exchanges.usd(result) == exchanges.START - D("10000")


# --------------------------------------------------------------------------- #
# Proration and the record instant
# --------------------------------------------------------------------------- #


def test_an_oversubscribed_alternative_accepts_its_factor_and_the_default_the_rest() -> None:
    result = run(("S", Holder(buys("60"), {2.5: (1, None)})), proration=((1, D("0.5")),))
    # Half the 60 elected for cash is accepted: 3,600; the other 30 take the
    # default, 15 NEWCO.
    assert exchanges.usd(result) == exchanges.START - D("6000") + D("3600")
    assert new_held(result) == D("15")


def test_a_prorated_part_is_whole_units_where_the_holding_is() -> None:
    result = run(("S", Holder(buys("61"), {2.5: (1, None)})), proration=((1, D("0.5")),))
    # Half of 61 is 30.5: 30 accepted, in whole units; 31 by the default.
    assert exchanges.usd(result) == exchanges.START - D("6100") + D("3600")
    assert new_held(result) == D("15.5")


def test_an_election_is_bounded_by_the_holding_recorded_at_the_record_instant() -> None:
    plan = {**buys("50"), (2.6, OLD.asset_id): D("50")}
    result = run(("S", Holder(plan, {2.7: (1, None)})), record_at=2.4)
    # Held 50 at the record instant; 50 more bought after. The election takes
    # cash for the 50 recorded, and the default the 50 bought since.
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("6000")
    assert new_held(result) == D("25")
    (recorded,) = [e for e in result.state.strategy.events if isinstance(e, HoldersRecorded)]
    assert (recorded.timestamp, dict(recorded.holders)) == (2.4, {"S": D("50")})


def test_a_holder_holding_none_at_the_record_instant_elects_for_none() -> None:
    late = {(2.6, OLD.asset_id): D("100")}
    result = run(("S", Holder(late, {2.7: (1, None)})), record_at=2.4)
    assert new_held(result) == D("50")


# --------------------------------------------------------------------------- #
# Fractions and cash in lieu, per part
# --------------------------------------------------------------------------- #


def test_each_parts_fraction_is_paid_in_lieu() -> None:
    stock = HoldingExchange(
        OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1"), in_lieu_price=D("210")),)
    )
    result = exchanges.run(
        stock,
        OLD,
        NEW,
        mids=MIDS,
        whole=True,
        strategies=elective.strategies(
            ("A", Holder(buys("51"), {2.5: (1, None)})), ("B", Holder(buys("49"), {}))
        ),
        schedule=RunSchedule.of(exchanges=[elective.action(alternatives=(stock, elective.CASH))]),
    )
    # A's 51 take 6,120 in cash; B's 49 take 24 NEWCO and half a share in
    # lieu, 105.
    assert new_held(result) == D("24")
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("6120") + D("105")


# --------------------------------------------------------------------------- #
# Declarations, persistence and replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: elective.action(proration=((0, D("0.5")),)),
        lambda: elective.action(proration=((1, D("1.5")),)),
        lambda: elective.action(proration=((1, D("0")),)),
        lambda: elective.action(proration=((5, D("0.5")),)),
        lambda: elective.action(proration=((1, D("0.5")), (1, D("0.4")))),
        lambda: elective.action(record_at=3.5),
    ],
)
def test_an_elective_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


@pytest.mark.parametrize("units", ["0", "-5", "lots"])
def test_a_part_election_names_a_positive_number_of_units(units: str) -> None:
    intent = Intent(
        "S", OLD.asset_id, D("1"), kind=IntentKind.ELECT,
        metadata={"action": elective.REF, "units": units},
    )  # fmt: skip
    with pytest.raises(InvalidIntentError):
        validate_intent(intent)


def test_a_split_election_resumes_to_one_result_from_every_pause() -> None:
    def holders() -> dict[str, Holder]:
        made = {
            "A": Holder(buys("50"), {2.5: (1, "20")}),
            "B": Holder({**buys("50"), (2.6, OLD.asset_id): D("10")}, {2.7: (1, None)}),
        }
        for sid, holder in made.items():
            holder.sid = sid
        return made

    def fresh() -> Any:
        return elective.strategies(*holders().items())

    schedule = RunSchedule.of(
        exchanges=[elective.action(record_at=2.4, proration=((1, D("0.5")),))]
    )
    cfg = exchanges.config(OLD, NEW)
    dataset = margin.prices(MIDS)
    whole = ReplayBacktest.run(cfg, dataset, fresh(), context_factory, schedule=schedule)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, fresh(), context_factory, schedule=schedule,
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                dict(holders()),
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    # A: 20 elected, 10 accepted; B: 50 recorded of 60, 25 accepted.
    assert exchanges.usd(whole.backtest) == exchanges.START - D("11000") + D("4200")


def test_one_holder_agreeing_with_itself_exchanges_the_holding_whole() -> None:
    result = run(("S", Holder(buys("100"), {2.5: (1, None)})))
    exchanged = [e for e in result.state.portfolio.events if isinstance(e, PositionExchanged)]
    assert [e.quantity for e in exchanged] == [D("100")]
