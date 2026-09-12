"""Where a captured run state is durably kept, and the two backends that keep it.

This is the boundary ADR-0029 exists for. Until v2.13, ``capture`` produced a
projection and ``serialize`` produced a string, and nothing in AlphaLab wrote
that string anywhere: there were zero filesystem calls in the package, and the
only :class:`~alphalab.persistence.protocol.PersistenceProtocol` implementation
held everything in memory and lost it on exit.

The shape is not new
--------------------
:mod:`alphalab.marketdata.transport` already solved this class of problem here,
and this follows it exactly: a narrow :class:`RunStateStore` protocol naming the
minimal effectful operation, one real implementation
(:class:`FileRunStateStore`, as ``HttpTransport`` is), and one deterministic
double the caller constructs **by name** (:class:`MemoryRunStateStore`, as
``StaticTransport`` is). That module's docstring names the failure the shape
avoids -- "silently fake data with no seam to ever make it real" -- and
``MemoryStorage`` was that failure for persistence.

The store knows nothing about what it stores
--------------------------------------------
:class:`RunStateStore` moves a ``str``. It does not import, name, inspect or
parse any snapshot type, and it never decodes a payload. Two things follow, and
both are the point (ADR-0029 decision 2):

* A future release that reshapes ``SessionState`` and ``BacktestState`` -- which
  ADR-0023 decision 1 says is expected, and split the envelopes to allow --
  cannot reach this protocol, because there is nothing here for it to reach. A
  store with ``save_session`` and ``save_backtest`` methods would have to be
  redesigned by that release.
* A nested pipeline, OMS or portfolio version failure still surfaces from the
  decoder that owns it, with that decoder's own error type, exactly as it does
  when no store is involved.

Turning a payload back into typed values is the owning snapshot module's
``from_primitives``; continuing a run is
:meth:`~alphalab.runtime.session.TradingSession.resume` plus ``advance``. This
module calls neither, and imports neither.

Nothing here mints an identifier
--------------------------------
Every operation on the legacy store draws one identifier from whatever source is
installed, because it stamps a system event with
:func:`~alphalab.common.ids.new_id`. Inside a run's
:func:`~alphalab.common.ids.id_scope` that is the *run's* stream: measured, one
``save_snapshot`` plus one ``append_event`` advances ``draws`` 0 -> 2, and the
two identifiers it consumed are the run's own next two. ``load_snapshot`` draws
as well, so a durable store built on that protocol could not read a run back
without changing it.

**No function in this module calls ``new_id``, and none has an event log to
stamp.** ``run_id`` is the caller's, ``sequence`` is an integer, and the
filesystem supplies its own names. A complete ``put`` / ``get`` cycle inside
``id_scope`` advances ``draws`` by exactly zero -- ADR-0029 decision 7, asserted
in ``tests/regression/test_run_state_store.py`` and characterized in its
pre-v2.13 form in ``tests/regression/test_persistence_draws_from_the_run_stream.py``.

Persistence is a caller action
------------------------------
Nothing in the execution path calls this. A caller persists between ``advance``
calls -- the boundary ADR-0023 decision 7 placed continuation at, and where
``id_position`` is accurate. There is deliberately **no append-per-event API**:
one capture plus serialize of a 1,600-event run costs 1.43s against 0.40s for
the run itself, so capturing per event would make a run quadratic in its own
length (ADR-0029 decision 8).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Protocol

from alphalab.common.constants import DEFAULT_ENCODING
from alphalab.persistence.exceptions import StorageError
from alphalab.persistence.run_state import RUN_STATE_ENVELOPE_SCHEMA, RunStateRef
from alphalab.persistence.serializer import deserialize, serialize

__all__ = [
    "FileRunStateStore",
    "MemoryRunStateStore",
    "RunStateStore",
]

#: Suffix of the file holding one checkpoint: its envelope line, then its payload.
_STATE_SUFFIX: Final = ".runstate"

#: File in a run's directory recording the caller's real ``run_id``.
_IDENTITY_FILE: Final = "run.identity"

#: Width the sequence is zero-padded to in a filename. Cosmetic only -- listings
#: parse the integer back rather than relying on lexicographic order, so a
#: sequence wider than this still sorts correctly.
_SEQUENCE_WIDTH: Final = 20

#: Length of the hex digest a run directory is named by.
_RUN_DIR_WIDTH: Final = 32


class RunStateStore(Protocol):
    """Durable storage for captured run state, addressed by run and checkpoint.

    The complete interface, and deliberately no more: four methods over
    ``(run_id, sequence) -> payload``. There is no ``delete``, no ``append``, no
    query language and no transaction, because no caller in this repository
    needs one and ADR-0029 driver D4 refuses infrastructure without a caller.
    """

    def put(self, run_id: str, sequence: int, payload: str) -> RunStateRef:
        """Durably store ``payload`` as checkpoint ``sequence`` of ``run_id``.

        The payload is stored **exactly**: not parsed, not re-encoded, not
        reordered. What :meth:`get` returns is what was handed in.

        Raises:
            PersistenceValidationError: If the identity is invalid.
            StorageError: If this ``(run_id, sequence)`` is already stored, or
                if the payload cannot be written.
        """
        ...

    def get(self, ref: RunStateRef) -> str:
        """Return the payload stored at ``ref``, byte for byte.

        Raises:
            StorageError: If the run is unknown, the sequence was never written,
                or the stored bytes do not match the digest recorded with them.
        """
        ...

    def latest(self, run_id: str) -> RunStateRef | None:
        """The highest-numbered checkpoint of ``run_id``, or ``None``.

        ``None`` is an honest absence -- this run has nothing stored -- and not
        an error, which is why this does not refuse an unknown run the way
        :meth:`get` does. Asking what a store holds is a question that can be
        answered with "nothing"; asking for a specific checkpoint is not.

        Raises:
            PersistenceValidationError: If ``run_id`` is not a valid identity.
        """
        ...

    def list_runs(self) -> tuple[str, ...]:
        """Every ``run_id`` this store holds a checkpoint for, sorted."""
        ...


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()


def _envelope(ref: RunStateRef, payload: str) -> str:
    """What the store records *about* a payload, on one line.

    Written with the same deterministic encoder every other payload in the
    repository uses, so the package holds one JSON dialect rather than two.
    """

    return serialize(
        {
            "schema_version": RUN_STATE_ENVELOPE_SCHEMA,
            "run_id": ref.run_id,
            "sequence": ref.sequence,
            "digest": _digest(payload),
            "byte_size": len(payload.encode(DEFAULT_ENCODING)),
        }
    )


def _verified(ref: RunStateRef, header: str, payload: str, where: str) -> str:
    """Check a payload against the envelope stored with it, before anything decodes it.

    Refuses rather than repairs. A truncated or altered payload is not a payload
    that can be partly used, and returning it for a decoder to fail on later
    would report the wrong error at the wrong boundary.
    """

    try:
        decoded = deserialize(header)
    except Exception as exc:
        raise StorageError(
            f"The run-state envelope for {ref} at {where} is not readable: {exc}"
        ) from exc

    if not isinstance(decoded, Mapping):
        raise StorageError(f"The run-state envelope for {ref} at {where} is not an object.")

    version = decoded.get("schema_version")
    if version != RUN_STATE_ENVELOPE_SCHEMA:
        raise StorageError(
            f"The run-state envelope for {ref} at {where} declares schema version "
            f"{version!r}, and this build reads version {RUN_STATE_ENVELOPE_SCHEMA}. "
            "There is no migration path; read it with the build that wrote it."
        )

    actual = _digest(payload)
    if actual != decoded.get("digest"):
        raise StorageError(
            f"The run state at {where} does not match the digest recorded with it: "
            f"expected {decoded.get('digest')!r}, found {actual!r}. The stored bytes "
            f"for {ref} are truncated or altered, and nothing is returned from them."
        )
    return payload


def _split(contents: str, ref: RunStateRef, where: str) -> tuple[str, str]:
    """Separate the envelope line from the payload that follows it.

    Split on the *first* newline only, so a payload containing newlines is
    returned unchanged. Nothing about the payload is assumed.
    """

    header, separator, payload = contents.partition("\n")
    if not separator:
        raise StorageError(
            f"The run state at {where} has no envelope line, so nothing can be said "
            f"about the bytes it holds. {ref} is refused rather than read."
        )
    return header, payload


class FileRunStateStore:
    """A run-state store on the local filesystem. Standard library only.

    The real backend, in the sense ``HttpTransport`` is: it does the effectful
    thing, and it says so. ``dependencies = []`` stays true.

    **The root is the caller's and must already exist.** A store pointed at a
    missing directory, at a file, or at a directory it cannot write **raises**.
    It does not create the root, does not warn and continue, and does not hold
    the payload in memory instead: a store that reports success for a run nobody
    can read back is the failure ADR-0029 exists to remove, and
    :class:`MemoryRunStateStore` is never reached by accident (decision 5).

    **The layout under the root is an implementation detail** and is not part of
    any contract. Today each run is a directory named by a digest of its
    ``run_id`` -- so an opaque, caller-supplied identity containing a separator,
    a dot-dot or a non-ASCII character cannot shape a path -- holding one file
    per checkpoint and one recording the run's real identity. None of that
    reaches :class:`~alphalab.persistence.run_state.RunStateRef`, which is why
    changing it later breaks no caller.

    Writes are atomic: the bytes go to a temporary file in the destination
    directory, are flushed to disk, and are then moved into place with
    :func:`os.replace`. A reader therefore sees a whole checkpoint or no
    checkpoint, never half of one.

    Concurrency: one writer. Two processes writing the *same*
    ``(run_id, sequence)`` at the same instant are outside this contract, and
    the duplicate check below is not a lock.
    """

    __slots__ = ("_root",)

    def __init__(self, root: Path | str) -> None:
        """Bind the store to an existing, writable directory.

        Raises:
            StorageError: If ``root`` does not exist, is not a directory, or
                cannot be written. The message names the path and what to do.
        """

        path = Path(root)
        if not path.exists():
            raise StorageError(
                f"Run-state store root {str(path)!r} does not exist. Create the "
                "directory and pass it again; this store does not create its own root "
                "and does not fall back to memory."
            )
        if not path.is_dir():
            raise StorageError(
                f"Run-state store root {str(path)!r} is not a directory. A store needs "
                "a directory it can write checkpoints into."
            )
        if not os.access(path, os.W_OK | os.X_OK):
            raise StorageError(
                f"Run-state store root {str(path)!r} is not writable. Fix the "
                "permissions or pass a different root; this store does not fall back "
                "to memory."
            )
        self._root = path

    @property
    def root(self) -> Path:
        """The directory this store was bound to."""

        return self._root

    def _run_dir(self, run_id: str) -> Path:
        digest = hashlib.sha256(run_id.encode(DEFAULT_ENCODING)).hexdigest()
        return self._root / digest[:_RUN_DIR_WIDTH]

    @staticmethod
    def _state_file(run_dir: Path, sequence: int) -> Path:
        return run_dir / f"{sequence:0{_SEQUENCE_WIDTH}d}{_STATE_SUFFIX}"

    @staticmethod
    def _write_atomically(destination: Path, contents: str) -> None:
        """Write ``contents`` to ``destination`` so a reader never sees it partly written.

        The temporary file is created in the destination's own directory, so the
        final :func:`os.replace` is a rename within one filesystem and therefore
        atomic. :func:`tempfile.mkstemp` draws its name from its own entropy and
        never from :mod:`alphalab.common.ids`.
        """

        handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding=DEFAULT_ENCODING) as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            # A failed write leaves nothing behind, including on interrupt.
            Path(temporary).unlink(missing_ok=True)
            raise

    def put(self, run_id: str, sequence: int, payload: str) -> RunStateRef:
        """Store ``payload`` as checkpoint ``sequence`` of ``run_id``.

        A ``(run_id, sequence)`` already stored is **refused**, not overwritten.
        Silently replacing a checkpoint would lose a run's history and make
        :meth:`latest` a claim about whichever write happened last, which is the
        duplicate rule ``validate_snapshot_save`` has always applied here.

        Raises:
            PersistenceValidationError: If the identity is invalid.
            StorageError: On a duplicate, or if the write fails.
        """

        ref = RunStateRef(run_id, sequence)
        run_dir = self._run_dir(run_id)
        destination = self._state_file(run_dir, sequence)

        try:
            run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Cannot create storage for run {run_id!r}: {exc}") from exc

        if destination.exists():
            raise StorageError(
                f"Run state {ref} is already stored. A checkpoint is written once; "
                "storing a different payload under the same sequence would discard "
                "the one already there."
            )

        identity = run_dir / _IDENTITY_FILE
        try:
            if not identity.exists():
                self._write_atomically(
                    identity,
                    serialize({"schema_version": RUN_STATE_ENVELOPE_SCHEMA, "run_id": run_id}),
                )
            self._write_atomically(destination, f"{_envelope(ref, payload)}\n{payload}")
        except OSError as exc:
            raise StorageError(f"Cannot write run state {ref}: {exc}") from exc

        return ref

    def get(self, ref: RunStateRef) -> str:
        """Return the payload stored at ``ref``, verified against its digest.

        Raises:
            StorageError: If the run is unknown, the sequence was never written,
                the file cannot be read, or the digest does not match. Nothing
                partial is returned by any of them.
        """

        run_dir = self._run_dir(ref.run_id)
        if not run_dir.is_dir():
            raise StorageError(
                f"No run state is stored for run {ref.run_id!r}. Nothing is returned "
                "for a run this store has never held."
            )

        destination = self._state_file(run_dir, ref.sequence)
        if not destination.is_file():
            raise StorageError(
                f"Run {ref.run_id!r} has no checkpoint at sequence {ref.sequence}. "
                f"Stored sequences: {list(self._sequences(run_dir))}."
            )

        try:
            contents = destination.read_text(encoding=DEFAULT_ENCODING)
        except OSError as exc:
            raise StorageError(f"Cannot read run state {ref}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise StorageError(
                f"The stored bytes for {ref} are not valid {DEFAULT_ENCODING}: {exc}"
            ) from exc

        where = f"sequence {ref.sequence} of run {ref.run_id!r}"
        header, payload = _split(contents, ref, where)
        return _verified(ref, header, payload, where)

    @staticmethod
    def _sequences(run_dir: Path) -> tuple[int, ...]:
        """Every stored sequence in ``run_dir``, ascending.

        Sorted numerically rather than by filename, so the ordering does not
        depend on the zero-padding width, and read from the directory rather than
        from an index, so it cannot disagree with what is on disk.
        """

        found = []
        for entry in run_dir.iterdir():
            if entry.suffix != _STATE_SUFFIX or not entry.is_file():
                continue
            try:
                found.append(int(entry.stem))
            except ValueError:
                # Not a checkpoint this store wrote. Ignored for ordering, and
                # never returned: `get` only ever resolves a name it composes.
                continue
        return tuple(sorted(found))

    def latest(self, run_id: str) -> RunStateRef | None:
        """The highest stored sequence for ``run_id``, or ``None`` if it holds none."""

        # Constructed for its validation: an invalid identity is a caller error
        # whether or not anything is stored under it.
        RunStateRef(run_id, 0)

        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            return None
        sequences = self._sequences(run_dir)
        return RunStateRef(run_id, sequences[-1]) if sequences else None

    def list_runs(self) -> tuple[str, ...]:
        """Every run this store holds a checkpoint for, sorted by ``run_id``.

        Read from each run's recorded identity rather than from its directory
        name, because the directory name is a digest and cannot be reversed. The
        sort is what makes two listings of one store agree.
        """

        found = []
        for entry in self._root.iterdir():
            identity = entry / _IDENTITY_FILE
            if not entry.is_dir() or not identity.is_file():
                continue
            if not self._sequences(entry):
                continue
            decoded: Any = deserialize(identity.read_text(encoding=DEFAULT_ENCODING))
            if isinstance(decoded, Mapping) and isinstance(decoded.get("run_id"), str):
                found.append(decoded["run_id"])
        return tuple(sorted(found))


class MemoryRunStateStore:
    """A run-state store held in memory, for tests and deterministic use.

    The ``StaticTransport`` of this boundary: it satisfies the same
    :class:`RunStateStore` contract with the same refusals, and it is **never
    selected automatically**. A caller constructs it by name, which is what makes
    the choice to hold state in memory visible in the code that made it.
    :class:`FileRunStateStore` never falls back to this, under any failure
    (ADR-0029 decision 5).

    It is not durable and does not pretend to be. Everything it holds is lost
    when the process exits, which is exactly why the name says ``Memory`` and
    the type is not the default anywhere.

    Determinism: sequences are ordered numerically and runs are listed sorted, so
    it answers :meth:`latest` and :meth:`list_runs` exactly as
    :class:`FileRunStateStore` does. It mints no identifier either.
    """

    __slots__ = ("_runs",)

    def __init__(self) -> None:
        self._runs: dict[str, dict[int, str]] = {}

    def put(self, run_id: str, sequence: int, payload: str) -> RunStateRef:
        """Store ``payload``, refusing a duplicate exactly as the file store does."""

        ref = RunStateRef(run_id, sequence)
        checkpoints = self._runs.setdefault(run_id, {})
        if sequence in checkpoints:
            raise StorageError(
                f"Run state {ref} is already stored. A checkpoint is written once; "
                "storing a different payload under the same sequence would discard "
                "the one already there."
            )
        checkpoints[sequence] = payload
        return ref

    def get(self, ref: RunStateRef) -> str:
        """Return the payload stored at ``ref``.

        Raises:
            StorageError: If the run is unknown or the sequence was never
                written. There is no digest check: nothing has been to disk, so
                there is no corruption for one to detect, and a check that can
                never fail is a check that teaches nothing.
        """

        checkpoints = self._runs.get(ref.run_id)
        if checkpoints is None:
            raise StorageError(
                f"No run state is stored for run {ref.run_id!r}. Nothing is returned "
                "for a run this store has never held."
            )
        if ref.sequence not in checkpoints:
            raise StorageError(
                f"Run {ref.run_id!r} has no checkpoint at sequence {ref.sequence}. "
                f"Stored sequences: {sorted(checkpoints)}."
            )
        return checkpoints[ref.sequence]

    def latest(self, run_id: str) -> RunStateRef | None:
        """The highest stored sequence for ``run_id``, or ``None`` if it holds none."""

        RunStateRef(run_id, 0)

        checkpoints = self._runs.get(run_id)
        if not checkpoints:
            return None
        return RunStateRef(run_id, max(checkpoints))

    def list_runs(self) -> tuple[str, ...]:
        """Every run this store holds a checkpoint for, sorted by ``run_id``."""

        return tuple(sorted(run_id for run_id, held in self._runs.items() if held))
