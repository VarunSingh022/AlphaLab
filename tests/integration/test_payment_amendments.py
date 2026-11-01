"""A corporate action's payment amended by the issuer's later word (phase 4, ledger FUT-034).

A :class:`PaymentAmendment` names one currency's payment of an exchange the
schedule declares and what became of it: delayed, failed, corrected, reversed
or replaced. Each is a ledger transaction of its own naming the entitlement, so
the original stays as booked; a failed payment is never settled cash, a delayed
one is out of settled cash and buying power until it is paid, and
:func:`~alphalab.portfolio.settlement.entitlements` reconciles each payment from
the ledger alone.

OLDCO is bought at 100 on 2.0 with the whole 10,000 of cash; a cash merger at
120 a share takes effect at 3.0 and pays its 12,000 at 6.0 (the helpers are
tests/integration/test_paid_exchanges.py's). OTHR quotes 100 throughout.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    PaidExchange,
    PausedReplay,
    PaymentAmendment,
    ReplayBacktest,
    RunSchedule,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.events import PaymentAmended
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.settlement import (
    ENTITLEMENT,
    EntitlementStatus,
    PaymentChange,
    entitlements,
    pending_from,
)
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.runtime.snapshot import capture as capture_pipeline
from alphalab.runtime.snapshot import from_primitives as pipeline_from_primitives
from alphalab.runtime.snapshot import restore as restore_pipeline
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_paid_exchanges as paid
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
OLD, OTHER = paid.OLD, paid.OTHER
MERGED = PaidExchange(paid.MERGER, 3.0, 6.0)


def word(at: float, change: PaymentChange, **kw: Any) -> PaymentAmendment:
    return PaymentAmendment(OLD.asset_id, 3.0, kw.pop("currency", "USD"), at, change, **kw)


#: A EUR book is valued at a declared rate; a USD-only run declares none.
EUR_USD = FxRate("EUR", "USD", D("1.10"), 1.0, "synthetic")


def amended(*words: PaymentAmendment, rates: tuple[FxRate, ...] = ()) -> RunSchedule:
    return RunSchedule.of(exchanges=[MERGED], payments=words, rates=rates)


def run(
    *words: PaymentAmendment,
    plan: Any = None,
    trader: Any = None,
    settles: str = "",
    until: float | None = None,
) -> Any:
    """The run -- with its data cut after ``until``, the book exactly as it stood then."""

    from alphalab.backtesting import BacktestEngine
    from alphalab.backtesting.dataset import MarketDataset

    plan = paid.BUY_ALL if plan is None else plan
    cfg = paid.config()
    if settles:
        pipeline = replace(
            cfg.pipeline,
            also_settles=frozenset({settles}),
            budget=cfg.pipeline.budget.in_currency("USD"),
        )
        cfg = replace(cfg, pipeline=pipeline)
    dataset = paid.data()
    if until is not None:
        kept = [r.payload for r in dataset.records if r.payload.timestamp <= until]
        dataset = MarketDataset.of("CUT", kept)
    return BacktestEngine.run(
        cfg,
        dataset,
        running_strategy_state("S", trader or margin.Trader(plan)),
        context_factory,
        schedule=amended(*words, rates=(EUR_USD,) if settles else ()),
    )


def cash(at: float, *words: PaymentAmendment, currency: str = "USD", settles: str = "") -> Any:
    """``currency``'s cash at ``at``, as the book stood at the last record by then."""

    result = run(*words, settles=settles, until=at)
    return PortfolioEngine.cash_position(result.state.portfolio, currency, at)


def entitlement(result: Any, at: float, currency: str = "USD") -> Any:
    (found,) = [
        e
        for e in entitlements(result.state.portfolio.ledger.transactions, at)
        if e.currency == currency
    ]
    return found


def exchange_entries(result: Any) -> list[Any]:
    return [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.EXCHANGE
    ]


# --------------------------------------------------------------------------- #
# Delayed
# --------------------------------------------------------------------------- #

DELAY = word(5.0, PaymentChange.DELAYED, payment_at=7.5)


def test_a_delayed_payment_is_out_of_settled_cash_and_buying_power_until_it_is_paid() -> None:
    plan = {**paid.BUY_ALL, (7.0, OTHER.asset_id): D("50"), (8.0, OTHER.asset_id): D("50")}
    result = run(DELAY, plan=plan, trader=margin.Trader(plan))
    # Not paid at 6.0: at 7.0 the 12,000 is still owed, and funds nothing; at
    # 8.0 it has been paid, at 7.5, and the order trades.
    assert paid.other_fills(result) == [(8.0, D("50"))]
    assert cash(7.0, DELAY).settled == D("0.00")
    assert cash(7.5, DELAY).settled == D("12000.00")
    found = entitlement(result, 7.0)
    assert (found.status, found.settles_at, found.pending) == (
        EntitlementStatus.PENDING,
        7.5,
        D("12000.00"),
    )
    assert entitlement(result, 8.0).status is EntitlementStatus.PAID


def test_a_delay_known_after_the_payment_was_due_reopens_it() -> None:
    late = word(6.5, PaymentChange.DELAYED, payment_at=7.5)
    # The clock paid it at 6.0; at 6.5 the issuer says it was not paid. It is
    # owed again until 7.5 -- never settled cash before it arrives.
    assert cash(6.0, late).settled == D("12000.00")
    assert cash(7.0, late).settled == D("0.00")
    assert cash(7.0, late).trade_date == D("12000.00")
    assert cash(7.5, late).settled == D("12000.00")


# --------------------------------------------------------------------------- #
# Failed and reversed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("at", [5.0, 6.5])
def test_a_failed_payment_is_never_settled_cash_and_is_written_off(at: float) -> None:
    failed = word(at, PaymentChange.FAILED)
    result = run(failed)
    # The 12,000 owed is out of trade-date cash, never settled, and the merger's
    # realized 2,000 becomes the whole 10,000 lost.
    end = cash(8.0, failed)
    assert (end.trade_date, end.settled) == (D("0.00"), D("0.00"))
    if at < 6.0:
        # Owed no longer: no credit left pending to take settled cash below
        # the trade-date balance before the payment date either.
        now = cash(at, failed)
        assert (now.settled, now.available, now.unsettled_credits) == (D("0.00"),) * 3
        assert cash(6.0, failed).settled == D("0.00")
    assert result.state.portfolio.realized_pnl["USD"] == D("-10000.00")
    assert entitlement(result, 8.0).status is EntitlementStatus.FAILED
    # The original is as booked: a write-off of its own beside it.
    original, failure = exchange_entries(result)
    assert (original.quantity, ENTITLEMENT in original.metadata) == (D("12000.00"), False)
    assert (failure.quantity, failure.timestamp) == (D("-12000.00"), at)


def test_a_reversal_claws_back_a_payment_made() -> None:
    reversed_ = word(7.0, PaymentChange.REVERSED)
    assert cash(6.0, reversed_).settled == D("12000.00")
    assert cash(7.0, reversed_).settled == D("0.00")
    assert entitlement(run(reversed_), 8.0).status is EntitlementStatus.REVERSED


def test_nothing_amends_a_failed_payment_though_it_is_asked_directly() -> None:
    from alphalab.runtime.execution_pipeline import ExecutionPipeline

    failed = run(word(5.0, PaymentChange.FAILED))
    with pytest.raises(RuntimeValidationError, match="nothing amends it after"):
        ExecutionPipeline.amend_payment(
            failed.state, OLD.asset_id, 3.0, "USD", PaymentChange.DELAYED, 8.0, payment_at=9.0
        )


def test_a_payment_not_yet_made_cannot_be_reversed() -> None:
    with pytest.raises(RuntimeValidationError, match="not reversed"):
        run(word(4.0, PaymentChange.REVERSED))


# --------------------------------------------------------------------------- #
# Corrected and replaced
# --------------------------------------------------------------------------- #

CORRECTED = word(4.0, PaymentChange.CORRECTED, per_unit=D("110"))


def test_a_correction_books_the_difference_with_the_rest_and_keeps_the_original() -> None:
    result = run(CORRECTED)
    # 100 shares at 110: 11,000 owed. The 1,000 less is booked beside the
    # original 12,000, unsettled with it until 6.0; 1,000 of the gain undone.
    assert cash(5.0, CORRECTED).settled == D("0.00")
    assert cash(6.0, CORRECTED).settled == D("11000.00")
    assert result.state.portfolio.realized_pnl["USD"] == D("1000.00")
    original, correction = exchange_entries(result)
    assert (original.quantity, correction.quantity) == (D("12000.00"), D("-1000.00"))
    found = entitlement(result, 8.0)
    assert (found.declared, found.owed, found.status) == (
        D("12000.00"),
        D("11000.00"),
        EntitlementStatus.PAID,
    )
    assert found.changes == (PaymentChange.CORRECTED,)


def test_a_correction_after_the_payment_is_paid_when_it_states() -> None:
    later = word(7.0, PaymentChange.CORRECTED, per_unit=D("125"), payment_at=7.5)
    # 500 more, owed from 7.0 and paid at 7.5.
    assert cash(7.0, later).settled == D("12000.00")
    assert cash(7.0, later).trade_date == D("12500.00")
    assert cash(7.5, later).settled == D("12500.00")


REPLACED = word(
    4.0,
    PaymentChange.REPLACED,
    per_unit=D("110"),
    replacement_currency="EUR",
    payment_at=6.5,
)


def test_a_replacement_in_another_currency_closes_the_first_and_owes_the_second() -> None:
    result = run(REPLACED, settles="EUR")
    assert cash(8.0, REPLACED, settles="EUR").trade_date == D("0.00")
    eur = cash(6.0, REPLACED, currency="EUR", settles="EUR")
    assert (eur.trade_date, eur.settled) == (D("11000.00"), D("0.00"))
    assert cash(7.0, REPLACED, currency="EUR", settles="EUR").settled == D("11000.00")
    assert entitlement(result, 8.0).status is EntitlementStatus.REPLACED
    assert entitlement(result, 6.0, "EUR").status is EntitlementStatus.PENDING
    assert entitlement(result, 7.0, "EUR").status is EntitlementStatus.PAID
    realized = result.state.portfolio.realized_pnl
    assert (realized["USD"], realized["EUR"]) == (D("-10000.00"), D("11000.00"))


SEVERAL = (CORRECTED, DELAY, word(7.6, PaymentChange.REVERSED))


def test_several_words_on_one_payment_apply_in_order() -> None:
    result = run(*SEVERAL)
    assert cash(7.0, *SEVERAL).settled == D("0.00")
    assert cash(7.5, *SEVERAL).settled == D("11000.00")
    assert cash(8.0, *SEVERAL).trade_date == D("0.00")
    events = [e for e in result.state.portfolio.events if isinstance(e, PaymentAmended)]
    assert [(e.change, e.owed_before, e.owed_after) for e in events] == [
        ("CORRECTED", D("12000.00"), D("11000.00")),
        ("DELAYED", D("11000.00"), D("11000.00")),
        ("REVERSED", D("11000.00"), D("0")),
    ]


def test_a_run_holding_none_of_the_action_is_changed_by_nothing() -> None:
    held_none = run(word(5.0, PaymentChange.FAILED), plan={})
    plain = paid.run({}, RunSchedule.of(exchanges=[MERGED]))
    assert (
        held_none.state.portfolio.ledger.transactions == plain.state.portfolio.ledger.transactions
    )


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        # Two words on one payment at one instant, alike or not.
        lambda: amended(word(5.0, PaymentChange.FAILED), word(5.0, PaymentChange.FAILED)),
        lambda: amended(
            word(5.0, PaymentChange.FAILED), word(5.0, PaymentChange.DELAYED, payment_at=7.0)
        ),
        # Anything after a failure or a reversal.
        lambda: amended(
            word(4.0, PaymentChange.FAILED), word(5.0, PaymentChange.DELAYED, payment_at=7.0)
        ),
        # An exchange the schedule does not declare.
        lambda: RunSchedule.of(payments=[word(5.0, PaymentChange.FAILED)]),
        lambda: word(5.0, PaymentChange.DELAYED, payment_at=5.0),
        lambda: word(5.0, PaymentChange.CORRECTED),
        lambda: word(5.0, PaymentChange.FAILED, per_unit=D("1")),
        lambda: word(5.0, PaymentChange.REPLACED, per_unit=D("1")),
        lambda: word(5.0, PaymentChange.REPLACED, per_unit=D("1"), replacement_currency="USD"),
        lambda: word(2.0, PaymentChange.FAILED),
        lambda: word(5.0, PaymentChange.CORRECTED, per_unit=D("-1")),
    ],
)
def test_a_word_on_a_payment_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


def test_a_word_in_a_currency_the_action_did_not_pay_is_refused() -> None:
    with pytest.raises(RuntimeValidationError, match="paid nothing in EUR"):
        run(word(5.0, PaymentChange.FAILED, currency="EUR"), settles="EUR")


# --------------------------------------------------------------------------- #
# Persistence and replay
# --------------------------------------------------------------------------- #


def test_a_restored_book_rebuilds_the_amended_index_exactly() -> None:
    result = run(
        word(4.0, PaymentChange.CORRECTED, per_unit=D("110")),
        word(6.5, PaymentChange.DELAYED, payment_at=7.5),
    )
    state = result.state
    assert pending_from(state.portfolio.ledger.transactions) == state.portfolio.unsettled
    payload = deserialize(serialize(capture_pipeline(state)))
    cfg = paid.config()
    objects = RuntimeObjects(
        cfg.pipeline.sizing_model,
        cfg.pipeline.simulator,
        {"S": margin.Trader(paid.BUY_ALL)},
        cfg.pipeline.instruments,
    )
    restored = restore_pipeline(pipeline_from_primitives(payload), objects)
    assert restored.portfolio.unsettled == state.portfolio.unsettled
    assert [type(e) for e in restored.portfolio.events][-2:] == [PaymentAmended, PaymentAmended]


def test_amendments_resume_to_one_result_from_every_pause() -> None:
    schedule = amended(
        word(4.0, PaymentChange.CORRECTED, per_unit=D("110")),
        word(5.0, PaymentChange.DELAYED, payment_at=7.5),
        word(7.6, PaymentChange.REVERSED),
    )
    cfg, dataset = paid.config(), paid.data()

    def fresh() -> Any:
        return running_strategy_state("S", margin.Trader(paid.BUY_ALL))

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
                {"S": margin.Trader(paid.BUY_ALL)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    # Each word applied once: three amendments, three events.
    events = [e for e in whole.backtest.state.portfolio.events if isinstance(e, PaymentAmended)]
    assert len(events) == 3


def test_a_schedule_without_amendments_keeps_its_digest() -> None:
    assert amended().digest == RunSchedule.of(exchanges=[MERGED]).digest
