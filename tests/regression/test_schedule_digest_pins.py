"""A schedule's digest is pinned: one golden value per kind of entry (plan WS-01, R53-6).

A paused replay holds its continuation to ``RunSchedule.digest`` (FUT-003), and
the v4.3 provenance work (ADR-0053, M1) records it beside the run. The digest is
SHA-256 over the schedule's maximum rate age and then each entry's kind -- by
its persisted value -- its instant and its canonical JSON, in order. Until
these pins nothing held it but the payloads that carry one: a change to an
entry's encoder, to a kind's value or to the formula would have moved every
recorded digest, and been found only when a stored replay failed to resume.

The golden values are what v4.2.1 computed, and what v4.2.0 computes. Each is
the digest of a schedule holding the entries of one kind: eight built here from
the public API alone, and twelve taken from the schedule of the frozen workload
that has them (``tests/regression/v4_2_1_workloads.py``), whose paused replays
recorded the whole schedule's digest -- which these tests hold too.

``RUN_SCHEDULE_DIGEST_SCHEME``, the constant that will name the formula without
entering it, waits for ADR-0053's acceptance (G0 item 6); the formula itself is
pinned here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from alphalab.backtesting.schedule import (
    Funding,
    RunSchedule,
    ScheduledAssignment,
    ScheduledCashFlow,
    ScheduledFundingRate,
    ScheduledSettlement,
    ScheduledSplit,
    ScheduleKind,
)
from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.persistence import serialize
from alphalab.portfolio.corporate_actions import (
    Assignment,
    CashFlow,
    CashFlowKind,
    ContractSettlement,
    Split,
)
from alphalab.portfolio.fx import FxRate
from alphalab.strategy.events import TimerEvent
from tests.regression import v4_2_1_workloads as workloads

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v4.2.1"
ASSET = str(uuid.UUID(int=0x4210_0001))

#: Every kind's persisted value: the text the digest is computed over.
PERSISTED_KINDS = {
    "RATE": "rate",
    "FUNDING": "funding",
    "CASH_FLOW": "cash_flow",
    "DISTRIBUTION": "distribution",
    "FUNDING_RATE": "funding_rate",
    "SPLIT": "split",
    "OBSERVATION": "observation",
    "TIMER": "timer",
    "EXERCISE": "exercise",
    "ASSIGNMENT": "assignment",
    "EXPIRY": "expiry",
    "SETTLEMENT": "settlement",
    "ACCRUAL": "accrual",
    "INTEREST": "interest",
    "BORROW_FEE": "borrow_fee",
    "EXCHANGE": "exchange",
    "PAYMENT": "payment",
    "RECORD": "record",
    "SURFACE": "surface",
    "AUCTION": "auction",
}

#: The frozen workload each kind no hand-built entry covers is taken from.
FROM_WORKLOADS = {
    ScheduleKind.DISTRIBUTION: "replay_option_life.json",
    ScheduleKind.EXERCISE: "replay_option_life.json",
    ScheduleKind.EXPIRY: "replay_option_life.json",
    ScheduleKind.INTEREST: "replay_financed_book.json",
    ScheduleKind.BORROW_FEE: "replay_financed_book.json",
    ScheduleKind.EXCHANGE: "replay_financed_book.json",
    ScheduleKind.ACCRUAL: "replay_recalled_short.json",
    ScheduleKind.PAYMENT: "replay_prorated_tender.json",
    ScheduleKind.RECORD: "replay_prorated_tender.json",
    ScheduleKind.SURFACE: "replay_delivered_surfaces.json",
    ScheduleKind.AUCTION: "replay_delivered_auctions.json",
    ScheduleKind.OBSERVATION: "replay_observations.json",
}

#: What v4.2.1 computed for each kind's schedule.
GOLDEN: dict[str, str] = {
    "rate": "6b0594fd83abf77b610a4f6bd769e5fe1cda5b45adc24fa71622c8c7d4d94034",
    "funding": "9d2bab57a5eb9c934aa79348655219c09e19ab7786fe5601c3a1ada57fb8ce76",
    "cash_flow": "7fa01a0277adb006fe4b1a9c3dbc2bb1accbc636ff61dc7d56140c0c0caab9b7",
    "distribution": "ddf08179aa93296e712ee3c790b73f9c577ddee6107104bcfc7bae5f5981a267",
    "funding_rate": "2cc35e54d8bc0d562b6578fba026d1b4d45fd99cc18113d980451bc290b63589",
    "split": "64120976b25d433c0dd83fa1c4eab3abec9582f9a8f8e1dc3baf7595933646e3",
    "observation": "59a05797dc95db750664229979b11c75828cab3f016678bb3b33bf93aafa82af",
    "timer": "ffb10394e93b01fa10fba022319e179e34dff8bed4988396aa21622a1bf5d237",
    "exercise": "1e5287df5a9bbeb072aa3a2cdf4d37c6fb660a456970a5b5267a7a196dbbc9b8",
    "assignment": "d62e2fd2ad3d676cd13edce4049c69dd98e709437ee9fc08a50869ad8e550503",
    "expiry": "3184ce39058e569367eef7df9ba66a5dd24fdaff09d56e5a958f06f077a519bf",
    "settlement": "e8fae8feb07c0fd0261f737788482a75a7f79a8bccaada36032b50e7f7eb6241",
    "accrual": "e1930ca5590665579ba02e6aa0500ed41eabff6e1cfb7ea5e50fee28e3db3557",
    "interest": "2a6ca04acb5d3b83c8975765e62d401799759fb65044a73656fe1ea539495847",
    "borrow_fee": "99cabc9e52a20cd132d76936146bdd7982d2cc4de023198e6c559ce369106a41",
    "exchange": "8a65fd77fe8356c8f16b35f66a7054f65ac1439087f9b88551ebbca2e91c61c3",
    "payment": "9552e22c4df6971f33f8dcdfe3b2f6e05e5abb25e584fe657a4f17028fe87082",
    "record": "75b92e1e7c8a4ff25885e69c691e96f48a4ea576df68a6436db7cce71cddb3de",
    "surface": "fb03fcad629c9267ce1fdeffef8a11d91d47d5803adf50f7d602ff4d625a4e32",
    "auction": "240dff21f4b268e2b6b0d651b7aaba4aa20f15b6dad730c73382d3bbb85316af",
}

#: An empty schedule, and one stating only a maximum rate age: the age enters the digest.
EMPTY = "dc937b59892604f5a86ac96936cd7ff09e25f18ae6b758e8014a24c7fa039e91"
AGED = "1ba0363418c0b5c15b34817fcbd25ff7b1d43036f0635f81bf4f21a01b2ada60"
#: Every kind's entries in one schedule, ordered by their keys.
EVERY_KIND = "bf07e14067f406fb5f05845a6f6af98270312699a4915657d64e5b2c2bcb92b7"


def built() -> dict[ScheduleKind, RunSchedule]:
    """A schedule of one entry for each kind no frozen workload's schedule holds."""

    return {
        ScheduleKind.RATE: RunSchedule.of(
            rates=[FxRate("EUR", "USD", Decimal("1.08"), 1.0, "ECB")]
        ),
        ScheduleKind.FUNDING: RunSchedule.of(fundings=[Funding(Decimal("3600"), "EUR", 1.5)]),
        ScheduleKind.CASH_FLOW: RunSchedule.of(
            cash_flows=[
                ScheduledCashFlow(
                    CashFlow(CashFlowKind.DIVIDEND, Decimal("2.50"), "USD", ASSET), 5.0
                )
            ]
        ),
        ScheduleKind.FUNDING_RATE: RunSchedule.of(
            funding_rates=[ScheduledFundingRate(ASSET, Decimal("0.001"), 3.5)]
        ),
        ScheduleKind.SPLIT: RunSchedule.of(
            splits=[ScheduledSplit(Split(ASSET, Decimal("2")), 6.0)]
        ),
        ScheduleKind.TIMER: RunSchedule.of(
            timers=[TimerEvent("T-1", 3.5, timer_id="t1", schedule_type="once")]
        ),
        ScheduleKind.ASSIGNMENT: RunSchedule.of(
            assignments=[ScheduledAssignment(Assignment(ASSET, Decimal("1")), 3.0)]
        ),
        ScheduleKind.SETTLEMENT: RunSchedule.of(
            settlements=[ScheduledSettlement(ContractSettlement(ASSET, Decimal("104")), 4.0)]
        ),
    }


def one_kind_schedules() -> dict[ScheduleKind, RunSchedule]:
    """For every kind, a schedule holding entries of that kind alone."""

    schedules = built()
    frozen = {replay.fixture: replay for replay in workloads.FROZEN_REPLAYS}
    for kind, fixture in FROM_WORKLOADS.items():
        entries = frozen[fixture].inputs().schedule.entries
        schedules[kind] = RunSchedule(tuple(entry for entry in entries if entry.kind is kind))
    return schedules


def every_kind(schedules: dict[ScheduleKind, RunSchedule]) -> RunSchedule:
    entries = [entry for schedule in schedules.values() for entry in schedule.entries]
    return RunSchedule(tuple(sorted(entries, key=lambda entry: entry.key)))


@pytest.fixture(scope="module")
def schedules() -> dict[ScheduleKind, RunSchedule]:
    return one_kind_schedules()


def test_every_kind_is_pinned_by_its_persisted_value() -> None:
    """A kind added later is pinned by the release that adds it, from that release."""

    assert {kind.name: kind.value for kind in ScheduleKind} == PERSISTED_KINDS
    assert set(GOLDEN) == set(PERSISTED_KINDS.values())
    assert set(built()) | set(FROM_WORKLOADS) == set(ScheduleKind)
    assert not set(built()) & set(FROM_WORKLOADS)


@pytest.mark.parametrize("kind", list(ScheduleKind), ids=[kind.value for kind in ScheduleKind])
def test_each_kind_digests_as_v4_2_1_did(
    schedules: dict[ScheduleKind, RunSchedule], kind: ScheduleKind
) -> None:
    schedule = schedules[kind]

    assert schedule.entries and {entry.kind for entry in schedule.entries} == {kind}
    assert schedule.digest == GOLDEN[kind.value]


def test_the_digest_is_the_formula_it_documents(
    schedules: dict[ScheduleKind, RunSchedule],
) -> None:
    """SHA-256 over the rate age's ``repr``, then ``kind|instant|JSON`` per entry, in order."""

    combined = every_kind(schedules)
    digest = hashlib.sha256(repr(combined.rate_max_age_seconds).encode(DEFAULT_ENCODING))
    for entry in combined.entries:
        line = f"{entry.kind.value}|{entry.at!r}|{serialize(entry.item)}\n"
        digest.update(line.encode(DEFAULT_ENCODING))

    assert combined.digest == digest.hexdigest() == EVERY_KIND


def test_the_maximum_rate_age_enters_the_digest() -> None:
    assert RunSchedule().digest == EMPTY
    assert RunSchedule(rate_max_age_seconds=3600.0).digest == AGED != EMPTY


@pytest.mark.parametrize("replay", workloads.FROZEN_REPLAYS, ids=lambda replay: replay.fixture)
def test_each_frozen_replay_recorded_its_workloads_schedule(
    replay: workloads.FrozenReplay,
) -> None:
    """A replay given a schedule recorded its digest; one given none recorded ``None``."""

    schedule = replay.inputs().schedule
    recorded = json.loads((FIXTURES / replay.fixture).read_text(encoding="utf-8"))

    assert recorded["schedule_digest"] == (schedule.digest if schedule else None)
