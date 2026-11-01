"""Legs an exchange delivers after its effective instant (phase 5, ledger FUT-037).

A :class:`DeliveredExchange` keeps the issuer's instants apart: at ``at`` the
holding is exchanged and each holder's entitlement to the legs recorded -- its
units, its basis, its strategies' parts -- and at each leg's declared delivery
instant the units arrive. Until then they are owed: valued and attributed, but
under the declared :class:`DeliveryConvention` -- by default -- neither sold
nor counted as margin collateral. A :class:`DeliveryAmendment` delays, corrects
or fails a delivery, each a ledger entry beside the original.

OLDCO is bought at 100 on 2.0 -- 100 shares, a basis of 10,000 -- and merged at
3.0 into half a NEWCO each, delivered at 5.0 (the helpers are
tests/integration/test_holding_exchanges.py's). Every asset quotes 100 from 2.0
to 7.0, one quote a second.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    DeliveredExchange,
    DeliveryAmendment,
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
from alphalab.portfolio import DeliveryConvention
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.portfolio.events import DeliveryAmended
from alphalab.portfolio.settlement import DELIVERS, PaymentChange, entitlements, pending_from
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
OLD, NEW, SPIN = exchanges.OLD, exchanges.NEW, exchanges.SPIN
MERGER = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
STEPS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0)


def delivered(
    exchange: HoldingExchange = MERGER,
    deliveries: tuple[tuple[str, float], ...] = ((NEW.asset_id, 5.0),),
    **kw: Any,
) -> DeliveredExchange:
    return DeliveredExchange(exchange, 3.0, deliveries, **kw)


def mids(*records: Any) -> dict[str, dict[float, str]]:
    return {r.asset_id: dict.fromkeys(STEPS, "100") for r in records}


def run(
    action: DeliveredExchange | None = None,
    *words: DeliveryAmendment,
    plan: dict[tuple[float, str], Decimal] | None = None,
    trader: Any = None,
    records: tuple[Any, ...] = (OLD, NEW),
    **kw: Any,
) -> Any:
    plan = {**exchanges.BUY, **(plan or {})}
    return exchanges.run(
        (action or delivered()).exchange,
        *records,
        plan=plan,
        mids=mids(*records),
        strategies=None if trader is None else trader,
        schedule=RunSchedule.of(exchanges=[action or delivered()], payments=words),
        **kw,
    )


def sells(*fills: Any) -> list[tuple[float, Decimal]]:
    return [(at, q) for at, q in fills if q < 0]


def owed(result: Any, at: float) -> Decimal:
    return sum(
        (e.quantity for e in result.state.portfolio.unsettled
         if e.delivery is not None and e.pending_at(at)),
        D("0"),
    )  # fmt: skip


# --------------------------------------------------------------------------- #
# The entitlement and its delivery
# --------------------------------------------------------------------------- #


def test_the_entitlement_is_booked_at_the_effective_instant_and_owed_until_delivered() -> None:
    result = run(plan={(7.0, NEW.asset_id): D("0")})
    portfolio = result.state.portfolio
    # The holder's from 3.0 -- 50 NEWCO at the 10,000 basis, valued -- and owed
    # until 5.0: an EXCHANGE entry of its own records the units.
    position = exchanges.position(result, NEW)
    assert (position.quantity, position.basis) == (D("50"), D("10000.00"))
    (entry,) = [t for t in portfolio.ledger.transactions if DELIVERS in t.metadata]
    assert (entry.timestamp, D(entry.metadata[DELIVERS]), entry.asset_id) == (
        3.0,
        D("50"),
        NEW.asset_id,
    )
    # Not a payment: reconciliation of payments does not count it.
    assert entitlements(portfolio.ledger.transactions, 4.0) == ()


def test_units_owed_are_not_sold_before_they_arrive() -> None:
    plan = {(4.0, NEW.asset_id): D("-20"), (6.0, NEW.asset_id): D("-20")}
    trader = margin.Trader({**exchanges.BUY, **plan})
    result = run(plan=plan, trader=elective_strategy(trader))
    # At 4.0 the 50 are owed and not delivered: the sale is refused; at 6.0,
    # delivered at 5.0, it trades.
    assert sells(*margin.fills(result, NEW)) == [(6.0, D("-20"))]
    assert any("pending delivery" in reason for reason in trader.rejections)


def test_a_convention_selling_when_issued_lets_them_be_sold() -> None:
    plan = {(4.0, NEW.asset_id): D("-20")}
    trader = margin.Trader({**exchanges.BUY, **plan})
    result = run(
        delivered(convention=DeliveryConvention(sellable=True)),
        plan=plan,
        trader=elective_strategy(trader),
    )
    assert sells(*margin.fills(result, NEW)) == [(4.0, D("-20"))]


def test_units_owed_are_not_collateral_unless_the_convention_says() -> None:
    from dataclasses import replace

    from alphalab.instrument.margin import MarginBasis, MarginTerms
    from alphalab.portfolio.fx import NO_RATES
    from alphalab.runtime.execution_pipeline import margin_position

    margined = replace(NEW, margin=MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3")))
    at_four = {(4.0, OLD.asset_id): D("0")}

    def equity(convention: DeliveryConvention) -> Decimal:
        result = run(
            delivered(convention=convention),
            plan=at_four,
            records=(OLD, margined),
        )
        figures = margin_position(result.state, NO_RATES, 4.0)
        assert figures is not None
        return figures.equity

    # 50 owed at 100: their 5,000 is out of margin equity until they arrive.
    held, counted = equity(DeliveryConvention()), equity(DeliveryConvention(collateral=True))
    assert counted - held == D("5000")


# --------------------------------------------------------------------------- #
# Delayed, corrected, failed
# --------------------------------------------------------------------------- #


def test_a_delayed_delivery_is_owed_until_its_new_instant() -> None:
    word = DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.5, PaymentChange.DELAYED, 6.5)
    plan = {(6.0, NEW.asset_id): D("-20"), (7.0, NEW.asset_id): D("-20")}
    trader = margin.Trader({**exchanges.BUY, **plan})
    result = run(delivered(), word, plan=plan, trader=elective_strategy(trader))
    assert sells(*margin.fills(result, NEW)) == [(7.0, D("-20"))]
    (amended,) = [e for e in result.state.portfolio.events if isinstance(e, DeliveryAmended)]
    assert (amended.change, amended.settles_at) == ("DELAYED", 6.5)


def test_a_partial_delivery_writes_off_the_shortfall_and_its_basis() -> None:
    word = DeliveryAmendment(
        OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.CORRECTED, units=D("30")
    )
    result = run(delivered(), word)
    position = exchanges.position(result, NEW)
    # 30 of the 50 delivered: 20 and their 4,000 of the basis written off.
    assert (position.quantity, position.basis) == (D("30"), D("6000.00"))
    assert result.state.portfolio.realized_pnl["USD"] == D("-4000.00")
    assert owed(result, 4.5) == D("30")


def test_a_failed_delivery_is_never_held_and_its_basis_is_lost() -> None:
    word = DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.FAILED)
    result = run(delivered(), word)
    assert exchanges.position(result, NEW) is None
    assert result.state.portfolio.realized_pnl["USD"] == D("-10000.00")
    assert owed(result, 4.5) == D("0")
    # The original entitlement stays as booked, the failure beside it.
    entries = [t for t in result.state.portfolio.ledger.transactions if "delivery_of" in t.metadata]
    assert [t.metadata.get("payment_change") for t in entries] == [None, "FAILED"]


def test_a_shortfall_is_taken_from_each_strategy_by_its_part() -> None:
    from alphalab.allocation import AllocationEngine

    word = DeliveryAmendment(
        OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.CORRECTED, units=D("25")
    )
    holders = elective.strategies(
        ("A", elective.Elector({(2.0, OLD.asset_id): D("60")}, {})),
        ("B", elective.Elector({(2.0, OLD.asset_id): D("40")}, {})),
    )
    result = run(delivered(), word, trader=holders)
    shares = dict(AllocationEngine.shares_of(result.state.allocation, NEW.asset_id))
    # 30 and 20 owed; half delivered: 15 and 10.
    assert shares == {"A": D("15"), "B": D("10")}


# --------------------------------------------------------------------------- #
# Baskets, fractions in lieu
# --------------------------------------------------------------------------- #


def test_each_leg_of_a_basket_is_delivered_at_its_own_instant() -> None:
    basket = HoldingExchange(
        OLD.asset_id,
        (
            ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),
            ExchangeLeg(SPIN.asset_id, D("1"), D("0.4")),
        ),
    )
    action = delivered(basket, ((NEW.asset_id, 4.5), (SPIN.asset_id, 6.5)))
    result = run(action, records=(OLD, NEW, SPIN))
    assert owed(result, 5.0) == D("100")  # SPINCO's, NEWCO's delivered at 4.5
    assert owed(result, 4.0) == D("150")
    assert exchanges.position(result, SPIN).basis == D("4000.00")


def test_a_fraction_is_paid_in_lieu_at_the_instant_and_the_whole_units_delivered_later() -> None:
    merger = HoldingExchange(
        OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1"), in_lieu_price=D("210")),)
    )
    result = run(delivered(merger), plan={(2.0, OLD.asset_id): D("101")}, whole=True)
    # 50.5 NEWCO: half a share paid at 210 = 105 now; the 50 owed until 5.0.
    assert owed(result, 4.0) == D("50")
    assert exchanges.usd(result) == exchanges.START - D("10100") + D("105")


# --------------------------------------------------------------------------- #
# Declarations, restoration and replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: delivered(deliveries=()),
        lambda: delivered(deliveries=((SPIN.asset_id, 5.0),)),
        lambda: delivered(deliveries=((NEW.asset_id, 3.0),)),
        lambda: delivered(deliveries=((NEW.asset_id, 5.0), (NEW.asset_id, 6.0))),
        lambda: DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.REVERSED),
        lambda: DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.DELAYED),
        lambda: DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.CORRECTED),
        lambda: RunSchedule.of(
            payments=[DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.FAILED)]
        ),
    ],
)
def test_a_delivery_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


def test_a_restored_book_rebuilds_the_owed_units_exactly() -> None:
    word = DeliveryAmendment(
        OLD.asset_id, 3.0, NEW.asset_id, 4.0, PaymentChange.CORRECTED, units=D("30")
    )
    result = run(delivered(), word, plan={(4.0, OLD.asset_id): D("0")})
    assert (
        pending_from(result.state.portfolio.ledger.transactions) == result.state.portfolio.unsettled
    )


def test_a_delayed_delivery_resumes_to_one_result_from_every_pause() -> None:
    from alphalab.runtime.retention import RetentionPolicy

    schedule = RunSchedule.of(
        exchanges=[delivered()],
        payments=[
            DeliveryAmendment(OLD.asset_id, 3.0, NEW.asset_id, 4.5, PaymentChange.DELAYED, 6.5)
        ],
    )
    plan = {**exchanges.BUY, (6.0, NEW.asset_id): D("-20"), (7.0, NEW.asset_id): D("-20")}
    cfg = exchanges.config(OLD, NEW, retention=RetentionPolicy(results=2))
    dataset = margin.prices(mids(OLD, NEW))

    def fresh() -> Any:
        return elective_strategy(margin.Trader(plan))

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
                cfg.pipeline.sizing_model, cfg.pipeline.simulator,
                {"S": margin.Trader(plan)}, cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    # Held to a results bound of two, the entitlement is kept while owed.
    assert sells(*margin.fills(whole.backtest, NEW)) == [(7.0, D("-20"))]


def elective_strategy(trader: margin.Trader) -> Any:
    """``trader`` as the run's one strategy, S."""

    return running_strategy_state("S", trader)


def test_a_leg_in_another_currency_is_owed_in_its_own_currency_at_the_rate() -> None:
    from alphalab.backtesting import Funding
    from alphalab.portfolio.fx import FxRate

    euro = exchanges.EURO
    basket = HoldingExchange(
        OLD.asset_id,
        (
            ExchangeLeg(NEW.asset_id, D("0.5"), D("0.6")),
            ExchangeLeg(euro.asset_id, D("1"), D("0.4")),
        ),
    )
    action = delivered(basket, ((NEW.asset_id, 4.5), (euro.asset_id, 6.5)))
    rates = [FxRate("USD", "EUR", D("0.8"), 1.0, "t"), FxRate("EUR", "USD", D("1.25"), 1.0, "t")]
    result = exchanges.run(
        basket,
        OLD,
        NEW,
        euro,
        plan=exchanges.BUY,
        mids=mids(OLD, NEW, euro),
        schedule=RunSchedule.of(
            exchanges=[action], rates=rates, fundings=[Funding(D("1"), "EUR", 1.5)]
        ),
        also_settles=frozenset({"EUR"}),
    )
    pending = {
        e.asset_id: (e.currency, e.quantity, e.settles_at)
        for e in result.state.portfolio.unsettled
        if e.delivery is not None
    }
    # EUROCO's 100 owed until 6.5 in EUR, carrying 4,000 of the basis at 0.8.
    assert pending[euro.asset_id] == ("EUR", D("100"), 6.5)
    assert exchanges.position(result, euro).basis == D("3200.00")
