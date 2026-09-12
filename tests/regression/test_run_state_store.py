"""A captured run state is written down, read back byte for byte, and costs no run id.

This is the v2.13 boundary ADR-0029 locks. Before it, ``capture`` produced a
projection and ``serialize`` produced a string and nothing in AlphaLab wrote that
string anywhere -- zero filesystem calls in the package, and one in-memory
``PersistenceProtocol`` implementation that lost everything on exit.

Three properties are load-bearing here and each has its own section below.

**The payload is preserved exactly.** The store moves a ``str``. It does not
parse, reorder, re-encode or validate what is inside, so what ``get`` returns is
what ``put`` was handed, byte for byte, including for a real
``serialize(capture(state))`` payload from a live session.

**The store consumes no run identifier.** This is the invariant the
characterization in ``test_persistence_draws_from_the_run_stream`` exists to
contrast with. The old store drew one identifier per operation from the ambient
source, so persisting inside :func:`~alphalab.common.ids.id_scope` consumed the
run's own next identifiers -- measured 0 -> 2 for one save plus one append, with
``load_snapshot`` drawing too. The new store draws **zero**, for both backends,
across a full cycle. That characterization test is deliberately left asserting
the old behaviour; it is not rewritten to make the legacy store look fixed.

**Nothing falls back.** A :class:`FileRunStateStore` whose root is missing, is a
file, or cannot be written refuses at construction. It never quietly becomes a
:class:`MemoryRunStateStore`, which a caller must name to get.

The shared-contract tests are parametrized over both backends rather than
written twice, which is also the shape that shows the protocol is
payload-agnostic: nothing in this file's contract section imports a snapshot
type, and a later release that reshapes ``SessionState`` or ``BacktestState``
would not change one line of it.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab.common.ids import current_id_position, id_scope, new_id
from alphalab.persistence import (
    RUN_STATE_ENVELOPE_SCHEMA,
    FileRunStateStore,
    MemoryRunStateStore,
    PersistenceValidationError,
    RunStateRef,
    RunStateStore,
    StorageError,
    deserialize,
    serialize,
)
from alphalab.runtime.session import ExecutionMode, SessionConfig, TradingSession
from alphalab.runtime.session_snapshot import capture as capture_session
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

SEED = 20260913
RUN = "run-2026-09-13T10:00:00Z"
PAYLOAD = '{"a":1,"schema_version":2}'

#: Every backend that must satisfy the same contract, named by what it is.
BACKENDS = ("file", "memory")


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[Callable[[str], RunStateStore]]:
    """Build either backend by name, so one contract is asserted twice."""

    def build(kind: str) -> RunStateStore:
        if kind == "file":
            root = tmp_path / "runs"
            root.mkdir(exist_ok=True)
            return FileRunStateStore(root)
        return MemoryRunStateStore()

    yield build


@pytest.fixture
def store(request: pytest.FixtureRequest, stores: Callable[[str], RunStateStore]) -> RunStateStore:
    """The backend named by an indirect ``store`` parametrization."""

    kind: str = request.param
    return stores(kind)


def _file_store(tmp_path: Path) -> FileRunStateStore:
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    return FileRunStateStore(root)


# ---------------------------------------------------------------------------
# RunStateRef: an identity, and not a location
# ---------------------------------------------------------------------------


def test_a_reference_renders_as_run_at_sequence() -> None:
    assert str(RunStateRef(RUN, 7)) == f"{RUN}@7"


def test_a_reference_is_a_value() -> None:
    assert RunStateRef(RUN, 1) == RunStateRef(RUN, 1)
    assert RunStateRef(RUN, 1) != RunStateRef(RUN, 2)
    assert len({RunStateRef(RUN, 1), RunStateRef(RUN, 1)}) == 1


def test_a_reference_carries_no_storage_detail() -> None:
    """The whole of ADR-0029 decision 4, asserted rather than trusted."""

    from dataclasses import fields

    assert [field.name for field in fields(RunStateRef)] == ["run_id", "sequence"]


@pytest.mark.parametrize("run_id", ["", "   ", "\t"])
def test_an_empty_run_id_is_refused(run_id: str) -> None:
    with pytest.raises(PersistenceValidationError, match="cannot be empty"):
        RunStateRef(run_id, 0)


def test_a_run_id_containing_the_separator_is_refused() -> None:
    """It would render to a reference that parses back as a different one."""

    with pytest.raises(PersistenceValidationError, match="cannot contain"):
        RunStateRef("run@1", 0)


def test_a_negative_sequence_is_refused() -> None:
    with pytest.raises(PersistenceValidationError, match="cannot be negative"):
        RunStateRef(RUN, -1)


@pytest.mark.parametrize("sequence", [True, 1.0, "1", None])
def test_a_non_integer_sequence_is_refused(sequence: Any) -> None:
    with pytest.raises(PersistenceValidationError, match="must be an integer"):
        RunStateRef(RUN, sequence)


def test_zero_is_a_valid_first_sequence() -> None:
    assert RunStateRef(RUN, 0).sequence == 0


# ---------------------------------------------------------------------------
# The contract both backends satisfy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_payload_survives_a_round_trip_exactly(store: RunStateStore) -> None:
    ref = store.put(RUN, 0, PAYLOAD)

    assert ref == RunStateRef(RUN, 0)
    assert store.get(ref) == PAYLOAD


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
@pytest.mark.parametrize(
    "payload",
    [
        "",
        " ",
        "{}",
        '{"nested":{"deep":[1,2,3]}}',
        '{"unicode":"café — 日本語 — \\u0000"}',
        "line one\nline two\nline three",
        '{"has":"a\\nnewline"}',
        "\n",
        "\n\n\n",
    ],
    ids=["empty", "space", "object", "nested", "unicode", "newlines", "escaped", "nl", "nls"],
)
def test_any_payload_is_stored_verbatim(store: RunStateStore, payload: str) -> None:
    """Including newlines: the store makes no assumption about what it holds."""

    assert store.get(store.put(RUN, 0, payload)) == payload


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_duplicate_sequence_is_refused_rather_than_overwritten(store: RunStateStore) -> None:
    """Silently replacing a checkpoint would lose history and make latest() a guess."""

    store.put(RUN, 0, PAYLOAD)

    with pytest.raises(StorageError, match="already stored"):
        store.put(RUN, 0, '{"different":true}')

    assert store.get(RunStateRef(RUN, 0)) == PAYLOAD, "the original is untouched"


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_an_unknown_run_is_refused(store: RunStateStore) -> None:
    store.put(RUN, 0, PAYLOAD)

    with pytest.raises(StorageError, match="No run state is stored"):
        store.get(RunStateRef("never-stored", 0))


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_an_unwritten_sequence_is_refused_and_says_what_exists(store: RunStateStore) -> None:
    store.put(RUN, 0, PAYLOAD)
    store.put(RUN, 3, PAYLOAD)

    with pytest.raises(StorageError, match=r"no checkpoint at sequence 2.*\[0, 3\]"):
        store.get(RunStateRef(RUN, 2))


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_an_invalid_identity_is_refused_by_put(store: RunStateStore) -> None:
    with pytest.raises(PersistenceValidationError):
        store.put("bad@id", 0, PAYLOAD)
    with pytest.raises(PersistenceValidationError):
        store.put(RUN, -1, PAYLOAD)


# ---------------------------------------------------------------------------
# Determinism: latest() and list_runs()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_latest_is_none_for_a_run_holding_nothing(store: RunStateStore) -> None:
    """An honest absence, not an error: asking what is held can answer 'nothing'."""

    assert store.latest("never-stored") is None


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_latest_is_the_highest_sequence_whatever_order_it_was_written(
    store: RunStateStore,
) -> None:
    for sequence in (3, 0, 11, 2):
        store.put(RUN, sequence, PAYLOAD)

    assert store.latest(RUN) == RunStateRef(RUN, 11)


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_latest_orders_numerically_not_lexicographically(store: RunStateStore) -> None:
    """``"9" > "10"`` as strings, and a checkpoint ordering must not believe that."""

    for sequence in (9, 10):
        store.put(RUN, sequence, PAYLOAD)

    assert store.latest(RUN) == RunStateRef(RUN, 10)


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_latest_refuses_an_invalid_identity(store: RunStateStore) -> None:
    with pytest.raises(PersistenceValidationError):
        store.latest("bad@id")


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_list_runs_is_empty_sorted_and_stable(store: RunStateStore) -> None:
    assert store.list_runs() == ()

    for run_id in ("zulu", "alpha", "mike"):
        store.put(run_id, 0, PAYLOAD)

    assert store.list_runs() == ("alpha", "mike", "zulu")
    assert store.list_runs() == store.list_runs(), "two listings of one store agree"


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_run_is_listed_once_however_many_checkpoints_it_holds(store: RunStateStore) -> None:
    for sequence in range(5):
        store.put(RUN, sequence, PAYLOAD)

    assert store.list_runs() == (RUN,)


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_opaque_run_ids_do_not_collide_or_leak(store: RunStateStore) -> None:
    """A run_id is opaque: separators, dots and unicode are identities, not paths."""

    identities = ("../escape", "a/b/c", "..", "  padded  ", "日本語", ".hidden", "a.b")
    for index, run_id in enumerate(identities):
        store.put(run_id, index, f'{{"n":{index}}}')

    assert store.list_runs() == tuple(sorted(identities))
    for index, run_id in enumerate(identities):
        assert store.get(RunStateRef(run_id, index)) == f'{{"n":{index}}}'


# ---------------------------------------------------------------------------
# The identifier invariant: ADR-0029 decision 7
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_full_cycle_inside_a_run_scope_draws_zero_identifiers(store: RunStateStore) -> None:
    """The inversion of ``test_persistence_draws_from_the_run_stream``."""

    with id_scope(SEED):
        before = current_id_position().draws
        ref = store.put(RUN, 0, PAYLOAD)
        store.get(ref)
        store.latest(RUN)
        store.list_runs()

        assert current_id_position().draws - before == 0


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
@pytest.mark.parametrize("method", ["put", "get", "latest", "list_runs"])
def test_each_operation_on_its_own_draws_zero(store: RunStateStore, method: str) -> None:
    """Every method, in isolation, so a future one cannot regress unnoticed.

    The mirror image of ``test_every_operation_that_emits_a_system_event_costs_one_draw``
    in ``test_persistence_draws_from_the_run_stream``, which measures the legacy
    store answering +1 to five of these.
    """

    operations: dict[str, Any] = {
        "put": lambda: store.put(RUN, 1, PAYLOAD),
        "get": lambda: store.get(RunStateRef(RUN, 0)),
        "latest": lambda: store.latest(RUN),
        "list_runs": store.list_runs,
    }

    with id_scope(SEED):
        store.put(RUN, 0, PAYLOAD)
        before = current_id_position().draws
        operations[method]()

        assert current_id_position().draws - before == 0, method


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_refused_operation_also_draws_zero(store: RunStateStore) -> None:
    """A failure path must not mint either -- that is where a stray id hides."""

    with id_scope(SEED):
        store.put(RUN, 0, PAYLOAD)
        before = current_id_position().draws

        for refused in (
            lambda: store.put(RUN, 0, PAYLOAD),
            lambda: store.get(RunStateRef(RUN, 99)),
            lambda: store.get(RunStateRef("absent", 0)),
        ):
            with pytest.raises(StorageError):
                refused()

        assert current_id_position().draws - before == 0


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_many_cycles_still_draw_zero(store: RunStateStore) -> None:
    with id_scope(SEED):
        for sequence in range(25):
            store.get(store.put(RUN, sequence, PAYLOAD))

        assert current_id_position() == current_id_position()
        assert current_id_position().draws == 0


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_identifiers_are_identical_to_a_run_that_never_persisted(store: RunStateStore) -> None:
    """The observable consequence, stated without reference to any counter."""

    with id_scope(SEED):
        control = [str(new_id()) for _ in range(6)]

    with id_scope(SEED):
        before = [str(new_id()) for _ in range(3)]
        store.get(store.put(RUN, 0, PAYLOAD))
        store.latest(RUN)
        after = [str(new_id()) for _ in range(3)]

    assert before + after == control, "persistence is invisible to the run's stream"


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_no_storage_local_name_leaks_into_the_run_stream(store: RunStateStore) -> None:
    """Whatever the backend names its files, none of it is drawn from the run."""

    with id_scope(SEED):
        store.put(RUN, 0, PAYLOAD)
        store.put(RUN, 1, PAYLOAD)
        position = current_id_position()

    assert position.seed == SEED
    assert position.draws == 0


def test_the_store_modules_never_call_new_id() -> None:
    """Structural, so a future edit cannot reintroduce the defect quietly."""

    import inspect

    from alphalab.persistence import run_state, run_store

    for module in (run_state, run_store):
        source = inspect.getsource(module)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith(("#", "*"))
        )
        assert "new_id(" not in code, f"{module.__name__} mints an identifier"


# ---------------------------------------------------------------------------
# The real boundary: a live snapshot through the store, undecoded
# ---------------------------------------------------------------------------


def _session_payload() -> str:
    """``serialize(capture(...))`` of a real session that processed records."""

    strategy_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    strategy = ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("5"), 3.0: Decimal("-3")})
    config = SessionConfig(
        pipeline=pipeline_config(strategy_id),
        mode=ExecutionMode.BACKTEST,
        seed=SEED,
        start_timestamp=1.0,
    )
    with id_scope(SEED):
        state = TradingSession.initialize(config, running_strategy_state(strategy_id, strategy))
        for index in range(3):
            record = sized_quote(asset_id, 2.0 + index, Decimal(100 + index), Decimal("100"))
            state, _ = TradingSession.advance(
                state,
                __import__("alphalab.market.record", fromlist=["MarketRecord"]).MarketRecord(
                    event_id=f"REC-{index}", timestamp=2.0 + index, payload=record
                ),
                context_factory,
            )
    return serialize(capture_session(state))


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_a_real_session_snapshot_survives_the_store_byte_for_byte(store: RunStateStore) -> None:
    """serialize -> put -> get -> the identical string. The store decodes nothing."""

    payload = _session_payload()
    assert len(payload) > 1_000, "a payload substantial enough to mean something"

    assert store.get(store.put(RUN, 0, payload)) == payload


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_the_retrieved_payload_still_decodes_through_its_own_owner(store: RunStateStore) -> None:
    """The store is transparent: the snapshot's own decoder still reads it."""

    payload = _session_payload()
    retrieved = store.get(store.put(RUN, 0, payload))

    decoded = deserialize(retrieved)
    assert isinstance(decoded, dict)
    assert decoded["schema_version"] == 1, "SESSION_SNAPSHOT_SCHEMA, untouched by the store"
    assert decoded["pipeline"]["schema_version"] == 2, "PIPELINE_SNAPSHOT_SCHEMA, untouched"


@pytest.mark.parametrize("store", BACKENDS, indirect=True)
def test_the_store_imports_no_snapshot_type(store: RunStateStore) -> None:
    """Payload-agnosticism, asserted structurally rather than promised.

    A later release that reshapes ``SessionState`` or ``BacktestState`` -- which
    ADR-0023 decision 1 anticipates -- must not have to touch the store. It
    cannot have to, if the store names none of it.
    """

    import inspect

    from alphalab.persistence import run_state, run_store

    forbidden = ("snapshot", "runtime", "session", "backtesting", "portfolio", "oms", "strategy")
    for module in (run_state, run_store):
        imports = [
            line
            for line in inspect.getsource(module).splitlines()
            if line.startswith(("import ", "from ")) and "alphalab" in line
        ]
        for line in imports:
            assert not any(name in line for name in forbidden), f"{module.__name__}: {line}"


# ---------------------------------------------------------------------------
# FileRunStateStore: durability, and the refusals that make it honest
# ---------------------------------------------------------------------------


def test_a_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        FileRunStateStore(tmp_path / "absent")


def test_a_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(StorageError, match="not a directory"):
        FileRunStateStore(target)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_an_unwritable_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "readonly"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(StorageError, match="not writable"):
            FileRunStateStore(root)
    finally:
        root.chmod(0o700)


def test_no_refusal_falls_back_to_memory(tmp_path: Path) -> None:
    """Every refusal above raises. None of them returns a working store."""

    for root in (tmp_path / "absent", tmp_path / "file"):
        if root.name == "file":
            root.write_text("x", encoding="utf-8")
        with pytest.raises(StorageError):
            FileRunStateStore(root)


def test_state_survives_a_new_store_over_the_same_root(tmp_path: Path) -> None:
    """Durability, which is the entire point: a different object reads it back."""

    _file_store(tmp_path).put(RUN, 0, PAYLOAD)

    reopened = _file_store(tmp_path)
    assert reopened.get(RunStateRef(RUN, 0)) == PAYLOAD
    assert reopened.latest(RUN) == RunStateRef(RUN, 0)
    assert reopened.list_runs() == (RUN,)


def test_the_payload_is_on_disk_verbatim(tmp_path: Path) -> None:
    """Stored after the envelope line, not escaped into it."""

    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)

    written = list(store.root.rglob("*.runstate"))
    assert len(written) == 1
    contents = written[0].read_text(encoding="utf-8")
    header, _, body = contents.partition("\n")

    assert body == PAYLOAD, "the payload is the file's tail, byte for byte"
    assert deserialize(header)["schema_version"] == RUN_STATE_ENVELOPE_SCHEMA


def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    for sequence in range(4):
        store.put(RUN, sequence, PAYLOAD)

    assert not list(store.root.rglob("*.tmp"))


def _corrupt(store: FileRunStateStore, mutate: Callable[[str], str]) -> None:
    target = next(iter(store.root.rglob("*.runstate")))
    target.write_text(mutate(target.read_text(encoding="utf-8")), encoding="utf-8")


def test_an_altered_payload_fails_its_digest(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    _corrupt(store, lambda text: text.replace('"a":1', '"a":2'))

    with pytest.raises(StorageError, match="does not match the digest"):
        store.get(RunStateRef(RUN, 0))


def test_a_truncated_payload_fails_its_digest(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    _corrupt(store, lambda text: text[: len(text) - 5])

    with pytest.raises(StorageError, match="does not match the digest"):
        store.get(RunStateRef(RUN, 0))


def test_a_file_with_no_envelope_line_is_refused(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    _corrupt(store, lambda _: PAYLOAD)

    with pytest.raises(StorageError, match="no envelope line"):
        store.get(RunStateRef(RUN, 0))


def test_an_unreadable_envelope_is_refused(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    _corrupt(store, lambda text: f"not json\n{text.partition(chr(10))[2]}")

    with pytest.raises(StorageError, match="not readable"):
        store.get(RunStateRef(RUN, 0))


def test_an_unreadable_envelope_version_is_refused(tmp_path: Path) -> None:
    """No migration path, and the message says so -- the house rule."""

    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    _corrupt(store, lambda text: text.replace('"schema_version":1', '"schema_version":2', 1))

    with pytest.raises(StorageError, match="declares schema version 2"):
        store.get(RunStateRef(RUN, 0))


def test_corruption_is_refused_before_anything_decodes_the_payload(tmp_path: Path) -> None:
    """The error names storage, not a snapshot field: nothing tried to read it."""

    store = _file_store(tmp_path)
    store.put(RUN, 0, _session_payload())
    _corrupt(store, lambda text: text[:-40])

    with pytest.raises(StorageError) as caught:
        store.get(RunStateRef(RUN, 0))

    assert "digest" in str(caught.value)
    assert "pipeline" not in str(caught.value), "no decoder was reached"


def test_a_foreign_file_in_a_run_directory_is_not_mistaken_for_a_checkpoint(
    tmp_path: Path,
) -> None:
    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    run_dir = next(p for p in store.root.iterdir() if p.is_dir())
    (run_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (run_dir / "xx.runstate").write_text("not a sequence", encoding="utf-8")

    assert store.latest(RUN) == RunStateRef(RUN, 0)


def test_an_empty_root_lists_nothing(tmp_path: Path) -> None:
    assert _file_store(tmp_path).list_runs() == ()


# ---------------------------------------------------------------------------
# Complexity: linear in payload, and never in event count
# ---------------------------------------------------------------------------


def test_write_cost_scales_with_payload_size_not_with_checkpoint_count(
    tmp_path: Path,
) -> None:
    """No hidden O(N^2): the 100th checkpoint costs what the 1st did.

    Structural rather than timed. A store that rebuilt an index, rewrote a
    manifest, or rescanned its history on every write would touch a number of
    files that grows with the checkpoint count; this one writes exactly one.
    """

    store = _file_store(tmp_path)
    store.put(RUN, 0, PAYLOAD)
    after_first = len(list(store.root.rglob("*")))

    for sequence in range(1, 51):
        store.put(RUN, sequence, PAYLOAD)

    after_fifty_one = len(list(store.root.rglob("*")))

    assert after_fifty_one - after_first == 50, "one file per checkpoint, and nothing rewritten"


def test_a_large_payload_round_trips_unchanged(tmp_path: Path) -> None:
    payload = serialize({"rows": [{"i": index, "v": Decimal(index)} for index in range(20_000)]})
    store = _file_store(tmp_path)

    assert len(payload) > 400_000, "large enough that a re-encode would show"
    assert store.get(store.put(RUN, 0, payload)) == payload


def test_no_append_per_event_surface_exists() -> None:
    """ADR-0029 decision 8: the store offers no way to persist per event.

    Four methods and no more. An ``append`` would invite exactly the O(N^2) the
    measurement forbids -- one capture plus serialize of a 1,600-event run costs
    1.43s against 0.40s for the run itself.
    """

    public = {name for name in vars(RunStateStore) if not name.startswith("_")}

    assert public == {"put", "get", "latest", "list_runs"}
    for backend in (FileRunStateStore, MemoryRunStateStore):
        surface = {name for name in dir(backend) if not name.startswith("_")}
        assert not surface - {"put", "get", "latest", "list_runs", "root"}, backend.__name__


def test_the_store_never_calls_capture_or_serialize_a_payload() -> None:
    """It stores what it is handed. It does not build a payload, and does not read one."""

    import inspect

    from alphalab.persistence import run_store

    source = inspect.getsource(run_store)
    assert "capture(" not in source
    # ``serialize``/``deserialize`` appear only for the store's own envelope line.
    assert source.count("deserialize(") == 2, "envelope header and run identity, and nothing else"


def test_the_execution_path_gained_no_persistence_call() -> None:
    """ADR-0029 decision 8: no automatic checkpointing anywhere on the hot path."""

    import inspect

    from alphalab.backtesting import engine as backtest_engine
    from alphalab.runtime import execution_pipeline, session

    for module in (execution_pipeline, session, backtest_engine):
        source = inspect.getsource(module)
        for name in ("RunStateStore", "FileRunStateStore", "MemoryRunStateStore", "RunStateRef"):
            assert name not in source, f"{module.__name__} reaches for the store"


# ---------------------------------------------------------------------------
# Compatibility: nothing about the payload's own versioning moved
# ---------------------------------------------------------------------------


def test_the_envelope_schema_is_one_and_is_its_own_constant() -> None:
    from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
    from alphalab.persistence import run_state

    assert RUN_STATE_ENVELOPE_SCHEMA == 1
    assert "DEFAULT_SCHEMA_VERSION" not in inspect_source(run_state), "a literal, not an alias"
    assert DEFAULT_SCHEMA_VERSION == 1, "unchanged, and unrelated"


def inspect_source(module: Any) -> str:
    import inspect

    return "\n".join(
        line
        for line in inspect.getsource(module).splitlines()
        if not line.strip().startswith(("#", '"', "*"))
    )


def test_no_existing_snapshot_schema_moved() -> None:
    """The constants ADR-0029 decision 9 pins, read from where they live."""

    from alphalab.allocation.snapshot import ALLOCATION_SNAPSHOT_SCHEMA
    from alphalab.backtesting.snapshot import BACKTEST_SNAPSHOT_SCHEMA
    from alphalab.common.constants import DEFAULT_SCHEMA_VERSION
    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.oms.snapshot import OMS_SNAPSHOT_SCHEMA
    from alphalab.portfolio.snapshot import PORTFOLIO_SNAPSHOT_SCHEMA
    from alphalab.runtime.session_snapshot import SESSION_SNAPSHOT_SCHEMA
    from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA

    assert (
        PIPELINE_SNAPSHOT_SCHEMA,
        SESSION_SNAPSHOT_SCHEMA,
        BACKTEST_SNAPSHOT_SCHEMA,
        ALLOCATION_SNAPSHOT_SCHEMA,
        OMS_SNAPSHOT_SCHEMA,
        PORTFOLIO_SNAPSHOT_SCHEMA,
        LIFECYCLE_SNAPSHOT_SCHEMA,
        DEFAULT_SCHEMA_VERSION,
    ) == (2, 1, 1, 1, 1, 2, 1, 1)


def test_a_v212_payload_is_unchanged_by_being_stored(tmp_path: Path) -> None:
    """The envelope wraps. It never rewrites, re-encodes or reorders."""

    payload = _session_payload()
    store = _file_store(tmp_path)

    assert store.get(store.put(RUN, 0, payload)) == payload
    assert deserialize(store.get(RunStateRef(RUN, 0))) == deserialize(payload)
