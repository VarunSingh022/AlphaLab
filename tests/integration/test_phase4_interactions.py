"""The phase 4 capabilities against each other (FUT-030 to FUT-036).

Each test drives several of them through one run on the canonical path: holders
electing apart, one prorated, their cash delayed by the issuer; an amend's
reservation under a ceiling and a queue; a computed reopening under a reference
band; recalls competing across strategies, each bought in for its own and
charged its lender's premium; a failed payment leaving buying power as if it
had never been owed. Each that persists is resumed from every pause to one
result, every input applied once.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from alphalab.backtesting import (
    BacktestEngine,
    PausedReplay,
    PaymentAmendment,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.execution.queue import PriceTimeQueue
from alphalab.instrument.borrow import BorrowAvailability
from alphalab.instrument.controls import (
    AuctionTieBreak,
    AuctionUncross,
    BandAction,
    BandReference,
    ReferenceBand,
    TradingControls,
    TradingHalt,
)
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.events import CashFlowBooked, PaymentAmended
from alphalab.portfolio.settlement import EntitlementStatus, PaymentChange, entitlements
from alphalab.portfolio.types import TransactionType
from alphalab.runtime import borrow_claims
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_borrow_recall as recall
from tests.integration import test_buy_in_premiums_and_located_borrow as located
from tests.integration import test_computed_auctions as computed
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_paid_exchanges as paid
from tests.integration import test_payment_amendments as amendments
from tests.integration import test_reopening_auctions as reopening
from tests.integration import test_split_elections as split
from tests.integration import test_strategy_amends as amends
from tests.integration import test_trading_controls as controls
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
OLD, NEW = exchanges.OLD, exchanges.NEW


# --------------------------------------------------------------------------- #
# Holders electing apart, a proration, and the issuer's delay
# --------------------------------------------------------------------------- #


def test_holders_electing_apart_one_prorated_paid_late_by_the_issuer() -> None:
    action = elective.action(proration=((1, D("0.5")),), payment_at=3.05)
    delayed = PaymentAmendment(
        OLD.asset_id, 3.0, "USD", 3.04, PaymentChange.DELAYED, payment_at=3.08
    )
    holders = (
        ("A", split.Holder(split.buys("60"), {2.5: (1, None)})),
        ("B", split.Holder(split.buys("40"), {})),
    )
    result = exchanges.run(
        elective.STOCK,
        OLD,
        NEW,
        mids=split.MIDS,
        strategies=elective.strategies(*holders),
        schedule=RunSchedule.of(exchanges=[action], payments=[delayed]),
    )
    portfolio = result.state.portfolio
    # A's 60 elect cash, half accepted: 30 for 3,600; A's other 30 and B's 40
    # take the default, 35 NEWCO. The 3,600 is owed from 3.0, due at 3.05, and
    # delayed at 3.04 to 3.08: never settled cash before then.
    assert split.new_held(result) == D("35")
    (owed,) = [e for e in entitlements(portfolio.ledger.transactions, 3.06) if e.currency == "USD"]
    assert (owed.owed, owed.status, owed.settles_at) == (
        D("3600.00"),
        EntitlementStatus.PENDING,
        3.08,
    )
    assert PortfolioEngine.cash_position(portfolio, "USD", 3.06).unsettled_credits == D("3600.00")
    (paid_late,) = entitlements(portfolio.ledger.transactions, 3.1)
    assert paid_late.status is EntitlementStatus.PAID
    assert [e.change for e in portfolio.events if isinstance(e, PaymentAmended)] == ["DELAYED"]


# --------------------------------------------------------------------------- #
# An amend under a ceiling, and in a queue
# --------------------------------------------------------------------------- #


def test_an_amend_resting_in_a_queue_frees_its_ceiling_and_keeps_its_place() -> None:
    budget_cfg = amends.ceilinged("1500")
    script = {
        **amends.REST,
        (3.0, controls.ACME.asset_id): (amends.amend(3.0, "4", amends.bid()),),
        (4.0, controls.ACME.asset_id): (amends.amend(4.0, "30", amends.bid()),),
    }
    mids = {controls.ACME.asset_id: {2.0: "100", 3.0: "99", 4.0: "99", 5.0: "99"}}
    result = BacktestEngine.run(
        budget_cfg,
        controls.data(mids, "1000"),
        running_strategy_state("S", controls.Script(script)),
        context_factory,
        schedule=RunSchedule.of(),
        queue=PriceTimeQueue(),
    )
    # Cut to 4 at 3.0: in place, its queue place kept and the ceiling holding
    # 400. Grown to 30 at 4.0: 3,000 against the 1,500 ceiling is refused, and
    # the 4 work on -- still queued, still holding only its 400.
    (order,) = amends.orders(result)
    assert (order.quantity, order.is_open) == (D("4"), True)
    assert result.queue.position_of(str(order.order_id.value)) is not None
    assert amends.capital(result).reserved_total == D("400")
    assert "ceiling" in amends.refused(result)[0].reason


# --------------------------------------------------------------------------- #
# A computed reopening under a reference band
# --------------------------------------------------------------------------- #


def test_a_computed_reopening_is_held_to_the_reference_band_and_extended() -> None:
    acme = controls.ACME.asset_id
    first = computed.book(bids=(("112", "50"),), offers=(("100", "40"),))
    later = computed.book(at=6.2, bids=(("104", "50"),), offers=(("100", "40"),))
    declared = TradingControls(
        halts=(
            TradingHalt(
                2.5, 4.5, asset_id=acme, auction=True,
                uncross=AuctionUncross(AuctionTieBreak.HIGHEST, extension=1.5),
            ),
        ),
        references=(BandReference(acme, 1.0, D("100"), "close"),),
        reference_bands=(
            ReferenceBand(acme, 1.0, None, BandAction.REFUSE, D("0.10"), D("0.10")),
        ),
        auction_books=(first, later),
    )  # fmt: skip
    result, _ = reopening.run(
        {(3.0, acme): (controls.buy(3.0, "10"),)}, declared, reopening.BARS_AFTER
    )
    # The band is 90 to 110 around the reference 100. At 5.0 the book and the
    # run's 10 tie at 100 and at 112, 40 each with 20 left over; the highest,
    # 112, is beyond the band: extended to 6.5. Held again at 7.0 from the book
    # published at 6.2: 104, within it, where the 10 cross.
    assert controls.trades(result) == [(7.0, D("10.000000"), D("104"))]
    held = computed.reopened(result)
    assert (held.at, held.price, held.matched) == (7.0, D("104"), D("40"))


# --------------------------------------------------------------------------- #
# Recalls competing across strategies, each charged its lender's premium
# --------------------------------------------------------------------------- #


def test_competing_recalls_buy_each_strategy_in_for_its_own_and_charge_it_alone() -> None:
    record = recall.lent(
        BorrowAvailability(1.0, D("100"), recall.RATE),
        BorrowAvailability(
            3.0, D("40"), recall.RATE, buy_in_at=4.0, buy_in_premium=D("0.01"),
            allocations=(("A", D("20")), ("B", D("20"))),
        ),
    )  # fmt: skip
    result, _ = located.two(record, {2.0: "-60"}, {2.0: "-20"})
    # A, 40 over its own 20, is bought in at 5.0 at 100; B, within its own, is
    # not. The premium, 1% of 4,000, is A's alone.
    booked = [e for e in result.state.portfolio.events if isinstance(e, CashFlowBooked)]
    assert [(e.reference, e.amount) for e in booked] == [("buy-in:A", D("-40.00"))]
    claims = {c.strategy_id: c for c in borrow_claims(result.state, located.ACME, 6.0)}
    assert (claims["A"].short, claims["A"].drawn) == (D("20"), D("0"))
    assert (claims["B"].short, claims["B"].drawn) == (D("20"), D("0"))


# --------------------------------------------------------------------------- #
# A failed payment and buying power
# --------------------------------------------------------------------------- #


def test_a_failed_payment_funds_nothing_and_leaves_no_collateral_behind() -> None:
    plan = {**paid.BUY_ALL, (5.0, paid.OTHER.asset_id): D("50")}
    trader = margin.Trader(plan)
    failed = amendments.word(4.0, PaymentChange.FAILED)
    result = amendments.run(failed, plan=plan, trader=trader)
    # The 12,000 owed fails at 4.0: the account's cash is the 0 the purchase
    # left, so the order at 5.0 is refused -- not funded by cash never paid.
    assert paid.other_fills(result) == []
    assert trader.rejections
    last = result.state.portfolio_snapshots.to_tuple()[-1]
    assert (last.cash, last.total_equity) == (D("0.00"), D("0.00"))


# --------------------------------------------------------------------------- #
# One result from every pause
# --------------------------------------------------------------------------- #


def test_holders_electing_apart_and_paid_late_resume_to_one_result() -> None:
    action = elective.action(proration=((1, D("0.5")),), payment_at=3.05, record_at=2.4)
    schedule = RunSchedule.of(
        exchanges=[action],
        payments=[
            PaymentAmendment(OLD.asset_id, 3.0, "USD", 3.04, PaymentChange.DELAYED, payment_at=3.08)
        ],
    )

    def holders() -> dict[str, split.Holder]:
        made = {
            "A": split.Holder(split.buys("60"), {2.5: (1, "40")}),
            "B": split.Holder(split.buys("40"), {2.6: (1, None)}),
        }
        for sid, holder in made.items():
            holder.sid = sid
        return made

    cfg = exchanges.config(OLD, NEW)
    dataset = margin.prices(split.MIDS)
    whole = ReplayBacktest.run(
        cfg, dataset, elective.strategies(*holders().items()), context_factory, schedule=schedule
    )
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, elective.strategies(*holders().items()), context_factory,
            schedule=schedule, pause_when=margin._at(boundary),
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
    events = whole.backtest.state.portfolio.events
    assert len([e for e in events if isinstance(e, PaymentAmended)]) == 1
    booked = whole.backtest.state.portfolio.ledger.transactions
    originals = [
        t
        for t in booked
        if t.type is TransactionType.EXCHANGE and "payment_change" not in t.metadata
    ]
    assert len(originals) == 1  # the one cash part, booked once


# --------------------------------------------------------------------------- #
# A surface's shock calling the margin and liquidating
# --------------------------------------------------------------------------- #


def test_a_volatility_spike_on_a_later_surface_calls_the_margin_and_liquidates() -> None:
    from dataclasses import replace

    from alphalab.backtesting.dataset import MarketDataset
    from alphalab.instrument.registry import declare_margin_model
    from alphalab.risk.events import MarginCall
    from tests.integration import test_margin_option_repricing as reprice
    from tests.integration import test_volatility_surfaces as surfaces
    from tests.integration.harness import backtest_config, quote, registry_of

    calm = surfaces.surface(("100", reprice.EXPIRES, "0.30"), at=reprice.T0 - 10)
    stormy = surfaces.surface(("100", reprice.EXPIRES, "0.90"), at=reprice.T0 + 30)

    def run(*declared: Any) -> Any:
        registry = declare_margin_model(
            registry_of(reprice.SHARE, reprice.PUT), surfaces.model(*declared)
        )
        base = backtest_config("S", starting_cash=D("15000"), risk_limits=margin.LEVERED)
        cfg = replace(
            base,
            pipeline=replace(
                base.pipeline,
                instruments=registry,
                budget=replace(
                    base.pipeline.budget,
                    global_capital=margin.WIDE,
                    maximum_exposure=margin.WIDE,
                    strategy_budgets={"S": margin.WIDE},
                ),
            ),
        )
        # The put's market price spikes with its volatility at T0 + 60.
        marks = {
            reprice.SHARE.asset_id: ("100", "100", "100"),
            reprice.PUT.asset_id: ("5", "18", "18"),
        }
        instants = (reprice.T0, reprice.T0 + 60, reprice.T0 + 120)
        quotes = [
            quote(a, at, D(m))
            for a, ms in marks.items()
            for at, m in zip(instants, ms, strict=True)
        ]
        dataset = MarketDataset.of("STORM", sorted(quotes, key=lambda q: (q.timestamp, q.asset_id)))
        trader = margin.Trader({(reprice.T0, reprice.PUT.asset_id): D("-10")})
        return BacktestEngine.run(
            cfg, dataset, running_strategy_state("S", trader), context_factory,
            schedule=RunSchedule.of(),
        )  # fmt: skip

    stormed, calmed = run(calm, stormy), run(calm)
    # Written at 30%; at T0 + 60 the put marks 18, the short's equity falls to
    # 2,000, below the requirement the surface as of then gives -- the call is
    # made there, and at the next quote, T0 + 120, as many puts are bought back
    # as restore the requirement: 8 of the 10.
    (call,) = [e for e in stormed.state.risk.events if isinstance(e, MarginCall)]
    assert call.timestamp == reprice.T0 + 60
    assert margin.fills(stormed, reprice.PUT) == [
        (reprice.T0, D("-10")),
        (reprice.T0 + 120, D("8")),
    ]
    # The requirement called is the one the later surface gives, not the calm one's.
    (calm_call,) = [e for e in calmed.state.risk.events if isinstance(e, MarginCall)]
    assert call.maintenance_requirement != calm_call.maintenance_requirement
