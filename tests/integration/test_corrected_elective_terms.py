"""Corrections to an elective action's terms after their announcement (phase 5, ledger FUT-038).

An :class:`ElectiveExchange` declares the issuer's :class:`TermsCorrection`\\ s,
each a version of its own, in effect from its announcement and recorded then as
a :class:`TermsAnnounced`. Each election is read against the version in effect
when it was made, within that version's window; a correction declares whether
the elections before it stand or are void, and may reopen a window. The holding
is exchanged by the version in effect when the action takes effect. A run
declaring no correction is the run it was.

OLDCO is bought at 100 on 2.0; the action -- half a NEWCO share (the default)
or 120 in cash for each OLDCO -- is announced at 2.2, closes its elections at
2.8 and takes effect at 3.0 (the helpers are tests/integration/test_split_elections.py's).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    EarlierElections,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.backtesting.schedule import TermsCorrection
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import HoldingExchange
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.events import ElectionMade, TermsAnnounced
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_split_elections as split
from tests.integration.harness import context_factory

D = Decimal
OLD, NEW = exchanges.OLD, exchanges.NEW
RAISED = HoldingExchange(OLD.asset_id, cash_per_unit=D("130"))
#: The cash offer raised to 130 at 2.4.
RAISE = TermsCorrection(2.4, alternatives=(elective.STOCK, RAISED))


def cash_taken(result: Any) -> Decimal:
    return exchanges.usd(result) - (exchanges.START - D("10000"))


def one(choices: dict[float, tuple[int, str | None]], **kw: Any) -> Any:
    return split.run(("S", split.Holder(split.buys("100"), choices)), **kw)


# --------------------------------------------------------------------------- #
# An election against the version in effect when it was made
# --------------------------------------------------------------------------- #


def test_an_election_after_a_correction_takes_the_corrected_terms() -> None:
    result = one({2.5: (1, None)}, corrections=(RAISE,))
    assert cash_taken(result) == D("13000")


def test_an_election_kept_through_a_correction_takes_the_corrected_alternative() -> None:
    result = one({2.3: (1, None)}, corrections=(RAISE,))
    # Elected at 2.3, under the original; the correction at 2.4 keeps it: the
    # election of the cash alternative is of the corrected one, 130.
    assert cash_taken(result) == D("13000")


def test_a_correction_voiding_earlier_elections_returns_a_holder_to_the_default() -> None:
    voiding = TermsCorrection(2.4, alternatives=(elective.STOCK, RAISED),
                              elections=EarlierElections.VOIDED)  # fmt: skip
    void = one({2.3: (1, None)}, corrections=(voiding,))
    assert cash_taken(void) == D("0") and split.new_held(void) == D("50")
    again = one({2.3: (1, None), 2.6: (1, None)}, corrections=(voiding,))
    assert cash_taken(again) == D("13000")


def test_a_reopened_window_counts_only_elections_made_while_it_is_open() -> None:
    reopen = TermsCorrection(2.7, deadline=2.9)
    # Closed at 2.5; the election at 2.6 is after it and before the reopening
    # at 2.7 is known: not counted, then or later.
    late = one({2.6: (1, None)}, deadline=2.5, corrections=(reopen,))
    assert cash_taken(late) == D("0")
    reopened = one({2.8: (1, None)}, deadline=2.5, corrections=(reopen,))
    assert cash_taken(reopened) == D("12000")


def test_a_corrected_proration_prorates_the_elections_it_finds() -> None:
    result = one({2.3: (1, None)}, corrections=(TermsCorrection(2.5, proration=((1, D("0.5")),)),))
    assert cash_taken(result) == D("6000") and split.new_held(result) == D("25")


def test_holders_on_either_side_of_a_voiding_correction() -> None:
    voiding = TermsCorrection(2.4, elections=EarlierElections.VOIDED)
    result = split.run(
        ("A", split.Holder(split.buys("50"), {2.3: (1, None)})),
        ("B", split.Holder(split.buys("50"), {2.5: (1, None)})),
        corrections=(voiding,),
    )
    # A elected before the voiding correction, B after: B's cash, A's stock.
    assert cash_taken(result) == D("6000") and split.new_held(result) == D("25")


# --------------------------------------------------------------------------- #
# Payments and deliveries follow the corrected terms
# --------------------------------------------------------------------------- #


def test_a_corrected_cash_offer_is_owed_until_its_payment() -> None:
    result = one({2.5: (1, None)}, corrections=(RAISE,), payment_at=3.05)
    owed = PortfolioEngine.cash_position(result.state.portfolio, "USD", 3.02)
    assert owed.unsettled_credits == D("13000.00")


def test_a_corrected_action_delivers_its_stock_later() -> None:
    result = one({}, corrections=(RAISE,), deliveries=((NEW.asset_id, 3.08),))
    owed = [e for e in result.state.portfolio.unsettled if e.delivery is not None]
    assert [(e.quantity, e.settles_at) for e in owed] == [(D("50"), 3.08)]


# --------------------------------------------------------------------------- #
# The record: each version apart, nothing rewritten
# --------------------------------------------------------------------------- #


def test_each_correction_is_recorded_at_its_announcement_and_the_original_kept() -> None:
    later = TermsCorrection(2.6, deadline=2.9)
    result = one({2.3: (1, None)}, corrections=(RAISE, later))
    announced = [e for e in result.state.strategy.events if isinstance(e, TermsAnnounced)]
    assert [(e.timestamp, e.version) for e in announced] == [(2.4, 1), (2.6, 2)]
    (made,) = [e for e in result.state.strategy.events if isinstance(e, ElectionMade)]
    assert made.timestamp == 2.3  # the election as made, never restated


def test_a_run_without_the_correction_reproduces_the_original() -> None:
    corrected = one({2.3: (1, None)}, corrections=(RAISE,))
    original = one({2.3: (1, None)})
    assert cash_taken(original) == D("12000") and cash_taken(corrected) == D("13000")
    assert digest_run(original).result_id == digest_run(one({2.3: (1, None)})).result_id
    assert not [e for e in original.state.strategy.events if isinstance(e, TermsAnnounced)]


@pytest.mark.parametrize(
    "build",
    [
        lambda: elective.action(corrections=(TermsCorrection(3.5),)),
        lambda: elective.action(corrections=(TermsCorrection(2.1),)),
        lambda: elective.action(corrections=(TermsCorrection(2.5), TermsCorrection(2.4))),
        lambda: elective.action(corrections=(TermsCorrection(2.5, default=5),)),
        lambda: elective.action(corrections=(TermsCorrection(2.5, deadline=3.5),)),
        lambda: elective.action(
            corrections=(TermsCorrection(2.5, alternatives=(elective.STOCK,)),)
        ),
        lambda: elective.action(corrections=(TermsCorrection(2.5, proration=((0, D("0.5")),)),)),
        lambda: TermsCorrection(2.5, elections="VOIDED"),  # type: ignore[arg-type]
    ],
)
def test_a_correction_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


def test_a_corrected_action_resumes_to_one_result_from_every_pause() -> None:
    voiding = TermsCorrection(2.4, alternatives=(elective.STOCK, RAISED),
                              elections=EarlierElections.VOIDED, deadline=2.9)  # fmt: skip
    schedule = RunSchedule.of(exchanges=[elective.action(corrections=(voiding,))])

    def holders() -> dict[str, split.Holder]:
        made = {
            "A": split.Holder(split.buys("60"), {2.3: (1, None), 2.8: (1, "20")}),
            "B": split.Holder(split.buys("40"), {2.5: (1, None)}),
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
                cfg.pipeline.sizing_model, cfg.pipeline.simulator, dict(holders()),
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )  # fmt: skip
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    # A's first election voided; its 20 at 2.8 count, as do B's 40: 60 x 130.
    assert cash_taken(whole.backtest) == D("7800")
