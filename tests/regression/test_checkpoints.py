"""Checkpoints that grow with what changed: a base and its segments (ledger PRF-004).

A chain read back must be the full capture of the same state -- value for value,
whatever the cadence, with or without a retention policy -- a segment must hold
only what the run appended, and a chain that has lost, gained, reordered or
altered a link must be refused at the link.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from alphalab.common.serialization import to_serializable
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.persistence.run_state import RunStateRef
from alphalab.persistence.run_store import MemoryRunStateStore
from alphalab.runtime.checkpoint import (
    _LOGS,
    CheckpointMark,
    checkpoint,
    read_checkpoints,
    restore_checkpoints,
)
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import RunState
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.run_snapshot import from_primitives as run_from_primitives
from alphalab.runtime.session import TradingSession
from alphalab.runtime.snapshot import _RETAINED, RuntimeObjects
from tests.integration.harness import context_factory
from tests.regression.test_retention import (
    POLICY,
    RECORDS,
    _records,
    _run,
)


def _logs(state: RunState) -> dict[str, Any]:
    logs = {name: read(state.pipeline) for name, read, _ in _RETAINED}
    logs["steps"] = state.steps
    logs["skipped"] = state.skipped
    return logs


def _chain(states: list[RunState], every: int) -> tuple[list[str], list[int]]:
    """A base at the first state, then a segment every ``every`` records."""

    payloads: list[str] = []
    taken: list[int] = []
    mark: CheckpointMark | None = None
    for index in range(0, len(states), every):
        payload, mark = checkpoint(states[index], mark)
        payloads.append(payload)
        taken.append(index)
    return payloads, taken


def _objects(state: RunState) -> RunObjects:
    """The live objects ``state`` was run with -- what a restore is given back."""

    config = state.config
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={
                strategy_id: held.instance
                for strategy_id, held in state.pipeline.strategy.strategies.items()
            },
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )


def test_every_cut_log_sits_where_the_checkpoint_says() -> None:
    state = _run(POLICY)[-1]
    primitives = to_serializable(capture_run(state))
    logs = _logs(state)

    assert {name for name, _ in _LOGS} == set(logs)
    for name, path in _LOGS:
        node: Any = primitives
        for key in path:
            node = node[key]
        assert len(node) == len(logs[name]), name


@pytest.mark.parametrize("policy", [None, POLICY], ids=["keeps-everything", "retained"])
@pytest.mark.parametrize("every", [1, 7, 40])
def test_a_chain_reads_back_as_the_full_capture(policy: RetentionPolicy | None, every: int) -> None:
    states = _run(policy)
    payloads, taken = _chain(states, every)

    snapshot = read_checkpoints(payloads)
    full = capture_run(states[taken[-1]])
    assert to_serializable(snapshot) == to_serializable(full)
    assert restore_checkpoints(payloads, _objects(states[0])) == states[taken[-1]]
    # Every prefix of the chain is a chain too.
    middle = len(payloads) // 2
    assert to_serializable(read_checkpoints(payloads[: middle + 1])) == to_serializable(
        capture_run(states[taken[middle]])
    )


def test_a_segment_holds_only_what_the_run_appended() -> None:
    states = _run(None)
    base, mark = checkpoint(states[99])
    segment, _ = checkpoint(states[109], mark)
    header = deserialize(segment)["logs"]

    for name, _ in _LOGS:
        start, end = header[name]["start"], header[name]["end"]
        assert start == mark.ends[name]
        assert end - start == len(_logs(states[109])[name]) - len(_logs(states[99])[name])
    assert len(segment) < len(base) / 3


def _log_bytes(payload: str) -> int:
    """How much of a checkpoint is log entries, serialized."""

    run = deserialize(payload)["run"]
    total = 0
    for _, path in _LOGS:
        node: Any = run
        for key in path:
            node = node[key]
        total += len(serialize(node))
    return total


def test_a_segment_costs_the_same_late_in_a_run_as_early() -> None:
    """A segment does not grow with the run -- its history or its orders.

    This workload places an order every second record. Until v3.13 a segment
    wrote the order book and the execution reports by order whole, so that part
    grew with every order the run had placed; since v3.13 (ledger PRF-011) it
    writes what changed, and only the run's current state -- positions, cash,
    latest quotes, working orders -- is written whole.
    """

    states = _run(None)
    _, early_mark = checkpoint(states[29])
    early, _ = checkpoint(states[39], early_mark)
    _, late_mark = checkpoint(states[RECORDS - 11])
    late, _ = checkpoint(states[RECORDS - 1], late_mark)

    full_early = len(serialize(to_serializable(capture_run(states[39]))))
    full_late = len(serialize(to_serializable(capture_run(states[RECORDS - 1]))))
    assert full_late > 3 * full_early
    assert _log_bytes(late) < 1.3 * _log_bytes(early)
    assert len(late) < 1.3 * len(early)
    assert len(late) < full_late / 3


def _order_ids(state: RunState) -> set[str]:
    return {str(order.order_id.value) for order in state.pipeline.oms.orders.orders()}


def test_a_segment_writes_the_orders_that_changed_and_no_others() -> None:
    states = _run(None)
    _, mark = checkpoint(states[RECORDS - 11])
    segment, _ = checkpoint(states[RECORDS - 1], mark)
    payload = deserialize(segment)

    assert {header["whole"] for header in payload["maps"].values()} == {False}
    written = {order["order_id"]["value"] for order in payload["run"]["pipeline"]["oms"]["orders"]}
    placed_since = _order_ids(states[RECORDS - 1]) - _order_ids(states[RECORDS - 11])
    assert placed_since and placed_since <= written
    assert len(written) < len(_order_ids(states[RECORDS - 1])) / 4
    held = payload["maps"]["oms.orders"]["size"]
    assert held == len(_order_ids(states[RECORDS - 1]))


def test_a_mark_rebuilt_from_its_digest_and_ends_writes_the_maps_whole() -> None:
    """After a restart a caller holds the mark's data, not the objects it compared."""

    states = _run(POLICY)
    base, mark = checkpoint(states[40])
    rebuilt = CheckpointMark(mark.sequence, mark.digest, mark.ends)
    segment, _ = checkpoint(states[60], rebuilt)

    assert {header["whole"] for header in deserialize(segment)["maps"].values()} == {True}
    assert to_serializable(read_checkpoints([base, segment])) == to_serializable(
        capture_run(states[60])
    )


V3_12_CHAIN = (
    Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "v3.12.0" / "checkpoints"
)


def test_a_chain_v3_12_wrote_reads_back_as_its_full_capture() -> None:
    """Checkpoint schema 1 wrote every per-order map whole, and is read as such."""

    chain = [(V3_12_CHAIN / f"chain_{index}.json").read_text().rstrip("\n") for index in range(3)]
    assert {deserialize(payload)["schema_version"] for payload in chain} == {1}
    full = deserialize((V3_12_CHAIN / "full_47.json").read_text())

    from_chain = read_checkpoints(chain)
    assert to_serializable(from_chain) == to_serializable(run_from_primitives(full))
    assert from_chain.pipeline.schema_version == 7, "upgraded through pipeline 6 -> 7"


@pytest.mark.parametrize(
    ("index", "edit", "match"),
    [
        (1, lambda header: header["oms.orders"].update(size=0), "a change was lost"),
        (0, lambda header: header["oms.orders"].update(whole=False), "writes oms.orders whole"),
        (1, lambda header: header.pop("execution.reports"), "per-order maps"),
    ],
)
def test_a_tampered_map_header_is_refused(index: int, edit: Any, match: str) -> None:
    states = _run(None)
    payloads, _ = _chain(states[:30], 10)
    tampered = deserialize(payloads[index])
    edit(tampered["maps"])
    payloads[index] = serialize(tampered)
    for later in range(index + 1, len(payloads)):
        relinked = deserialize(payloads[later])
        relinked["previous"] = hashlib.sha256(payloads[later - 1].encode("utf-8")).hexdigest()
        payloads[later] = serialize(relinked)

    with pytest.raises(StateDecodeError, match=match):
        read_checkpoints(payloads)


def test_a_restored_chain_continues_where_the_run_stopped() -> None:
    reference = _run(POLICY)[-1]
    states = _run(POLICY)
    stop = 113
    payloads, taken = _chain(states[: stop + 1], 16)
    if taken[-1] != stop:
        payload, _ = checkpoint(states[stop], _marks(payloads, states, taken)[-1])
        payloads.append(payload)

    restored = restore_checkpoints(payloads, _objects(states[0]))
    assert restored == states[stop]
    with TradingSession.resume(restored):
        for record in _records()[stop + 1 :]:
            restored, _ = TradingSession.advance(restored, record, context_factory)
    assert to_serializable(capture_run(restored)) == to_serializable(capture_run(reference))


def _marks(payloads: list[str], states: list[RunState], taken: list[int]) -> list[CheckpointMark]:
    """The marks a chain's checkpoints returned, taken again."""

    marks: list[CheckpointMark] = []
    mark: CheckpointMark | None = None
    for index in taken:
        _, mark = checkpoint(states[index], mark)
        marks.append(mark)
    assert len(marks) == len(payloads)
    return marks


def test_a_chain_goes_through_a_run_state_store_unchanged() -> None:
    states = _run(POLICY)
    payloads, taken = _chain(states, 25)
    store = MemoryRunStateStore()
    for sequence, payload in enumerate(payloads):
        store.put("run-1", sequence, payload)
    latest = store.latest("run-1")
    assert latest is not None

    read = [store.get(RunStateRef("run-1", sequence)) for sequence in range(latest.sequence + 1)]
    assert restore_checkpoints(read, _objects(states[0])) == states[taken[-1]]


def test_the_same_run_writes_the_same_chain() -> None:
    first, _ = _chain(_run(POLICY), 30)
    second, _ = _chain(_run(POLICY), 30)

    assert first == second


# --------------------------------------------------------------------------- #
# A broken chain is refused at the link
# --------------------------------------------------------------------------- #


def _sample() -> list[str]:
    payloads, _ = _chain(_run(POLICY), 30)
    assert len(payloads) >= 4
    return payloads


def test_a_missing_segment_is_refused() -> None:
    payloads = _sample()

    with pytest.raises(StateDecodeError, match="numbered 2"):
        read_checkpoints([payloads[0], payloads[2], payloads[3]])


def test_a_reordered_chain_is_refused() -> None:
    payloads = _sample()

    with pytest.raises(StateDecodeError, match=r"segment numbered 0|base"):
        read_checkpoints([payloads[1], payloads[0]])


def test_an_altered_checkpoint_breaks_the_next_link() -> None:
    payloads = _sample()
    altered = payloads[1].replace('"processed":', '"processed": ', 1)
    assert altered != payloads[1]

    with pytest.raises(StateDecodeError, match="checkpoint 2 follows"):
        read_checkpoints([payloads[0], altered, *payloads[2:]])


def test_a_substituted_segment_from_another_chain_is_refused() -> None:
    payloads = _sample()
    other, _ = _chain(_run(None), 30)

    with pytest.raises(StateDecodeError, match="chain is broken here"):
        read_checkpoints([payloads[0], other[1]])


def test_edited_ranges_are_refused() -> None:
    payloads = _sample()
    edited = deserialize(payloads[0])
    edited["logs"]["steps"]["end"] += 1

    with pytest.raises(StateDecodeError, match="the payload holds"):
        read_checkpoints([serialize(edited)])


def test_an_empty_chain_or_an_unknown_version_is_refused() -> None:
    payloads = _sample()
    future = deserialize(payloads[0])
    future["schema_version"] = 99

    with pytest.raises(StateDecodeError, match="starts with its base"):
        read_checkpoints([])
    with pytest.raises(StateDecodeError, match="checkpoint schema 99"):
        read_checkpoints([serialize(future)])


def test_a_mark_from_a_later_state_is_refused() -> None:
    states = _run(None)
    _, late = checkpoint(states[-1])

    with pytest.raises(StateDecodeError, match="no later than the state"):
        checkpoint(states[10], late)
