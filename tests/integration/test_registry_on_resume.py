"""A registry supplied back to a resumed run is held to its terms, not its type (phase 6, FUT-041).

A paused replay's and a live run's payload record the digest of the instrument
registry the run held (:func:`registry_digest`); a restore holds the registry
supplied back to it and refuses one of the right type with other terms -- a
borrow, margin, settlement, tick or contract term. A payload written before
phase 6 recorded none: it restores as before, checked by type alone, and says
so (:attr:`RegistryVerification.UNRECORDED`) rather than claiming verification.
A bare run snapshot is the run's identity and records none of its own; a
caller holds it to a manifest's digest. No ``result_id`` moves.

The run is tests/integration/test_surface_feeds.py's book of written puts, with
its registry varied one term at a time.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.backtesting import PausedReplay, ReplayBacktest, RunSchedule
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.conventions.tick import TickSchedule
from alphalab.instrument.borrow import BorrowAvailability, BorrowTerms
from alphalab.instrument.margin import MarginBasis, MarginTerms
from alphalab.instrument.registry import InstrumentRegistry, declare_margin_model
from alphalab.instrument.snapshot import registry_digest
from alphalab.instrument.terms import Deliverable, OptionTerms
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.runtime.checkpoint import checkpoint, restore_checkpoints
from alphalab.runtime.registry_check import RegistryVerification, registry_verification
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import restore as restore_run
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_margin_and_liquidation as margin
from tests.integration import test_surface_feeds as feeds
from tests.integration.harness import context_factory, registry_of, running_strategy_state

D = Decimal
SHARE, PUT = feeds.SHARE, feeds.PUT
ROOT = Path(__file__).resolve().parents[2]
REFUSED = "has terms"


def registry(share: Any = SHARE, put: Any = PUT) -> InstrumentRegistry:
    return declare_margin_model(registry_of(share, put), feeds.model())


def with_registry(held: InstrumentRegistry) -> Any:
    cfg = feeds.config(feeds.model())
    return replace(cfg, pipeline=replace(cfg.pipeline, instruments=held))


SCHEDULE = RunSchedule.of(surfaces=(feeds.delivered("0.30", feeds.T0, feeds.T0 + 30, "v1"),))


def objects(held: InstrumentRegistry) -> RunObjects:
    cfg = with_registry(held)
    return RunObjects(
        RuntimeObjects(
            cfg.pipeline.sizing_model, cfg.pipeline.simulator, {"S": feeds.Watcher()}, held
        ),
        cfg.fill_policy,
    )


def paused(boundary: int = 2) -> PausedReplay:
    result = ReplayBacktest.run(
        with_registry(registry()), feeds.dataset(), running_strategy_state("S", feeds.Watcher()),
        context_factory, schedule=SCHEDULE, pause_when=margin._at(boundary),
    )  # fmt: skip
    assert isinstance(result, PausedReplay)
    return result


def payload(boundary: int = 2) -> dict[str, Any]:
    loaded: dict[str, Any] = deserialize(serialize(capture_replay(paused(boundary))))
    return loaded


def resumed(data: dict[str, Any], held: InstrumentRegistry) -> str:
    restored = restore_replay(
        replay_from_primitives(data), objects(held), feeds.dataset(), SCHEDULE
    )
    return digest_run(ReplayBacktest.resume(restored, context_factory).backtest).result_id


def reference() -> str:
    whole = ReplayBacktest.run(
        with_registry(registry()), feeds.dataset(), running_strategy_state("S", feeds.Watcher()),
        context_factory, schedule=SCHEDULE,
    )  # fmt: skip
    return digest_run(whole.backtest).result_id


def half_deliverable() -> Any:
    """The put delivering 50 shares a contract, not 100."""

    assert isinstance(PUT.terms, OptionTerms)
    return replace(PUT, terms=replace(PUT.terms, deliverable=Deliverable(SHARE.asset_id, D("50"))))


#: Registries of the right type with one term changed.
CHANGED = {
    "borrow terms": lambda: registry(
        share=replace(
            SHARE,
            borrow=BorrowTerms((BorrowAvailability(1.0, D("100"), D("0.1")),), DayCount.ACT_360),
        )
    ),
    "margin terms": lambda: registry(
        share=replace(SHARE, margin=MarginTerms(MarginBasis.VALUE_RATE, D("0.6"), D("0.3")))
    ),
    "a settlement rule": lambda: registry(
        share=replace(SHARE, settlement=SettlementRule(SettlementBasis.CALENDAR_DAYS, 2))
    ),
    "a tick schedule": lambda: registry(share=replace(SHARE, tick=TickSchedule.flat(D("0.05")))),
    "an option's deliverable": lambda: registry(put=half_deliverable()),
    "the margin model": lambda: declare_margin_model(
        registry_of(SHARE, PUT), feeds.model(surface_max_age=30.0)
    ),
}


# --------------------------------------------------------------------------- #
# A paused replay
# --------------------------------------------------------------------------- #


def test_a_registry_with_the_same_terms_resumes_to_the_uninterrupted_result() -> None:
    data = payload()
    assert data["instruments_digest"] == registry_digest(registry())
    rebuilt = registry()  # another object, the same terms
    snapshot = replay_from_primitives(data)
    assert registry_verification(snapshot, rebuilt) is RegistryVerification.VERIFIED
    assert resumed(data, rebuilt) == reference()


@pytest.mark.parametrize("what", sorted(CHANGED))
def test_a_registry_of_the_right_type_with_other_terms_is_refused(what: str) -> None:
    changed = CHANGED[what]()
    assert type(changed) is type(registry()) and registry_digest(changed) != registry_digest(
        registry()
    )
    with pytest.raises(StateDecodeError, match=REFUSED):
        resumed(payload(), changed)


def test_a_payload_written_before_phase_6_restores_by_type_and_says_so() -> None:
    legacy = {**payload(), "schema_version": 2}
    del legacy["instruments_digest"]
    snapshot = replay_from_primitives(legacy)
    changed = CHANGED["margin terms"]()
    # Not refused -- it recorded nothing to hold the registry to -- and not
    # called verified: the limitation stated, not hidden.
    assert registry_verification(snapshot, changed) is RegistryVerification.UNRECORDED
    assert registry_verification(snapshot, registry()) is RegistryVerification.UNRECORDED
    assert resumed(legacy, registry()) == reference()


def test_a_replay_holding_no_registry_records_none() -> None:
    from tests.integration import replay_workload as workload

    plain = ReplayBacktest.run(
        workload.config(), workload.dataset(), workload.strategy_state(), context_factory,
        pause_when=workload.pause_at(6),
    )  # fmt: skip
    assert isinstance(plain, PausedReplay)
    snapshot = capture_replay(plain)
    assert snapshot.instruments_digest is None
    assert registry_verification(snapshot, None) is RegistryVerification.NONE_HELD


def test_a_payload_resumes_in_a_fresh_process_held_to_its_terms(tmp_path: Path) -> None:
    stored = tmp_path / "paused.json"
    stored.write_text(serialize(capture_replay(paused())), encoding="utf-8")
    script = (
        "import sys, json\n"
        "from alphalab.persistence import deserialize\n"
        "from alphalab.persistence.exceptions import StateDecodeError\n"
        "from tests.integration import test_registry_on_resume as t\n"
        "data = deserialize(open(sys.argv[1], encoding='utf-8').read())\n"
        "out = {'same': t.resumed(data, t.registry())}\n"
        "try:\n"
        "    t.resumed(data, t.CHANGED['borrow terms']())\n"
        "    out['changed'] = 'resumed'\n"
        "except StateDecodeError:\n"
        "    out['changed'] = 'refused'\n"
        "print(json.dumps(out))\n"
    )
    ran = subprocess.run(
        [sys.executable, "-c", script, str(stored)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert json.loads(ran.stdout) == {"same": reference(), "changed": "refused"}


# --------------------------------------------------------------------------- #
# A run snapshot, a checkpoint chain: held to a digest the caller supplies
# --------------------------------------------------------------------------- #


def test_a_run_snapshot_records_no_digest_and_its_result_id_is_unmoved() -> None:
    run = paused().run
    record = serialize(capture_run(run))
    assert "instruments_digest" not in record
    digest = registry_digest(registry())
    restored = restore_run(capture_run(run), objects(registry()), instruments_digest=digest)
    assert serialize(capture_run(restored)) == record
    with pytest.raises(StateDecodeError, match=REFUSED):
        restore_run(
            capture_run(run), objects(CHANGED["a tick schedule"]()), instruments_digest=digest
        )
    # Without a digest, as before: by type.
    restore_run(capture_run(run), objects(CHANGED["a tick schedule"]()))


def test_a_checkpoint_chain_is_held_to_the_digest_supplied() -> None:
    run = paused().run
    base, _ = checkpoint(run)
    digest = registry_digest(registry())
    restore_checkpoints([base], objects(registry()), instruments_digest=digest)
    with pytest.raises(StateDecodeError, match=REFUSED):
        restore_checkpoints(
            [base], objects(CHANGED["a settlement rule"]()), instruments_digest=digest
        )


def test_a_digest_for_a_run_that_held_no_registry_is_refused() -> None:
    from tests.integration import replay_workload as workload

    plain = ReplayBacktest.run(
        workload.config(), workload.dataset(), workload.strategy_state(), context_factory,
        pause_when=workload.pause_at(6),
    )  # fmt: skip
    assert isinstance(plain, PausedReplay)
    with pytest.raises(StateDecodeError, match="held no instrument registry"):
        restore_run(capture_run(plain.run), workload.objects(), instruments_digest="0" * 64)


# --------------------------------------------------------------------------- #
# A live run: refused, unless the host declares its reference data changed
# --------------------------------------------------------------------------- #


def live_state() -> Any:
    from alphalab.broker import BrokerEngine
    from alphalab.runtime.broker_routing import RoutingConfig
    from alphalab.runtime.execution_pipeline import ExecutionRouting
    from alphalab.runtime.live import LiveSession
    from alphalab.runtime.run import ExecutionMode, RunConfig

    cfg = with_registry(registry())
    live = RunConfig(
        pipeline=replace(cfg.pipeline, routing=ExecutionRouting.EXTERNAL),
        mode=ExecutionMode.LIVE,
        seed=7,
        start_timestamp=1.0,
        compile_analytics=False,
    )
    return LiveSession.initialize(
        live,
        running_strategy_state("S", feeds.Watcher()),
        BrokerEngine.initialize("VENUE-X", D("1000000"), "USD"),
        RoutingConfig(venue="VENUE-X", currency="USD"),
    )


def test_a_live_run_is_held_to_its_terms_unless_the_host_declares_them_changed() -> None:
    from alphalab.runtime.live_snapshot import capture as capture_live
    from alphalab.runtime.live_snapshot import from_primitives as live_from_primitives
    from alphalab.runtime.live_snapshot import restore as restore_live

    data = deserialize(serialize(capture_live(live_state())))
    assert data["instruments_digest"] == registry_digest(registry())
    restore_live(live_from_primitives(data), objects(registry()))
    changed = CHANGED["borrow terms"]()
    with pytest.raises(StateDecodeError, match=REFUSED):
        restore_live(live_from_primitives(data), objects(changed))
    # The host updated its reference data between sessions, and says so: the
    # run continues under the new terms, and its next payload records them.
    updated = restore_live(live_from_primitives(data), objects(changed), registry_changed=True)
    assert capture_live(updated).instruments_digest == registry_digest(changed)
    legacy = {**data, "schema_version": 2}
    del legacy["instruments_digest"]
    snapshot = live_from_primitives(legacy)
    assert registry_verification(snapshot, changed) is RegistryVerification.UNRECORDED
