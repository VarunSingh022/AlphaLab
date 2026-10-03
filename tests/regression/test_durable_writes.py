"""Regression guard for PER-003: a completed write survives a crash.

Both file stores write a temporary file, flush it, and rename it over its final
name. The rename makes the write atomic; only flushing the *directory* after it
makes the rename itself durable. Until v3.12 neither store did, so a crash after
``put`` returned could lose a checkpoint or an artifact the caller had been told
was stored.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from alphalab.model_registry.artifact_store import FileArtifactStore
from alphalab.persistence import FileRunStateStore, ensure_directory, fsync_directory


def _recording_fsync(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, int]]:
    """Record, in order, every fsync: whether it flushed a directory, and which inode."""

    flushed: list[tuple[bool, int]] = []
    real = os.fsync

    def recording(descriptor: int) -> None:
        status = os.fstat(descriptor)
        flushed.append((stat.S_ISDIR(status.st_mode), status.st_ino))
        real(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    return flushed


def _assert_every_new_entry_is_flushed_into_its_parent(
    root: Path, flushed: list[tuple[bool, int]]
) -> None:
    """Each file created under ``root`` was flushed, then its directory; each
    directory created under ``root`` was flushed into its parent."""

    entries = sorted(root.rglob("*"))
    assert entries, "nothing was written"
    for entry in entries:
        parent = os.stat(entry.parent).st_ino
        if entry.is_file():
            own = os.stat(entry).st_ino
            assert (False, own) in flushed, f"{entry} was never flushed"
            after = flushed[flushed.index((False, own)) :]
            assert (True, parent) in after, f"{entry.parent} was not flushed after {entry.name}"
        else:
            assert (True, parent) in flushed, f"{entry.parent} was not flushed for {entry.name}"


def test_a_checkpoint_and_its_directories_survive_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileRunStateStore(tmp_path)
    flushed = _recording_fsync(monkeypatch)

    store.put("run-1", 1, '{"state": 1}')

    _assert_every_new_entry_is_flushed_into_its_parent(tmp_path, flushed)


def test_an_artifact_and_its_shard_directory_survive_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileArtifactStore(tmp_path)
    flushed = _recording_fsync(monkeypatch)

    store.put(b"model bytes")

    _assert_every_new_entry_is_flushed_into_its_parent(tmp_path, flushed)


def test_ensure_directory_creates_missing_parents_top_down_and_leaves_existing_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed = _recording_fsync(monkeypatch)
    deep = tmp_path / "a" / "b" / "c"

    ensure_directory(deep)
    ensure_directory(deep)  # already there: nothing more to do

    assert deep.is_dir()
    parents = [os.stat(path).st_ino for path in (tmp_path, tmp_path / "a", tmp_path / "a" / "b")]
    assert [inode for is_dir, inode in flushed if is_dir] == parents


def test_a_filesystem_that_cannot_flush_a_directory_does_not_lose_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unsupported(descriptor: int) -> None:
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(os, "fsync", unsupported)
    fsync_directory(tmp_path)  # tolerated: the rename is as durable as it can be


def test_a_real_flush_failure_is_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(descriptor: Any) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "fsync", failing)
    with pytest.raises(OSError, match="Input/output error"):
        fsync_directory(tmp_path)


def test_a_path_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "plain.txt"
    file.write_text("x")
    with pytest.raises(NotADirectoryError):
        fsync_directory(file)
