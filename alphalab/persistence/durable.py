"""Making a completed rename survive a crash.

Both of AlphaLab's file stores -- :class:`~alphalab.persistence.run_store.FileRunStateStore`
and :class:`~alphalab.model_registry.artifact_store.FileArtifactStore` -- write a
temporary file, ``fsync`` it, and :func:`os.replace` it over its final name. That
makes the write *atomic*: a reader sees the whole file or none of it. It does not
make it *durable*: the rename is a change to the directory, and until the
directory itself is flushed a crash can undo it, losing a checkpoint or an
artifact whose write had already been reported complete. Until v3.12 neither
store flushed the directory (ledger PER-003). The same holds one level up: a
directory a store creates is itself an entry in its parent, so
:func:`ensure_directory` flushes each parent a new directory lands in.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Final

__all__ = ["ensure_directory", "fsync_directory"]

#: ``O_DIRECTORY`` where the platform has it, so opening a path that is not a
#: directory fails rather than flushing some other file.
_DIRECTORY_FLAGS: Final = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)

#: What a filesystem answers when it cannot flush a directory at all -- some
#: network and virtual filesystems. The rename is then as durable as that
#: filesystem makes it, and refusing the write would lose it outright.
_UNSUPPORTED: Final = frozenset({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})


def fsync_directory(directory: str | os.PathLike[str]) -> None:
    """Flush ``directory``'s entries, so a rename into it survives a crash.

    Call it after :func:`os.replace` into ``directory``. On Windows a directory
    cannot be opened for flushing, and a completed ``MoveFileEx`` rename is the
    platform's durability guarantee, so there it does nothing.

    Raises:
        OSError: If the directory cannot be opened, or flushing it fails for a
            reason other than the filesystem not supporting it.
    """

    if os.name == "nt":
        return
    descriptor = os.open(directory, _DIRECTORY_FLAGS)
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in _UNSUPPORTED:
            raise
    finally:
        os.close(descriptor)


def ensure_directory(directory: str | os.PathLike[str]) -> None:
    """Create ``directory`` and any missing parents, each one durably.

    Every directory created is flushed into its parent, from the top down, so a
    crash cannot keep a file whose directory entry was never written. A
    directory that already exists -- or that another writer creates first -- is
    left as it is.

    Raises:
        OSError: If a directory cannot be created or flushed.
    """

    missing: list[Path] = []
    current = Path(directory)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for created in reversed(missing):
        try:
            created.mkdir()
        except FileExistsError:
            continue
        fsync_directory(created.parent)
