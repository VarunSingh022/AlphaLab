"""What this interpreter is running: the one place the lifecycle reads its environment.

:func:`~alphalab.lifecycle.fingerprint.running_engine` reads a version the
imported source declares. The observations here go further (v3.11, ledger
REP-002/DAT-007): the bytes of the imported package and the time-zone database
``zoneinfo`` reads. Both are facts about *this process on this host*, recorded
into a manifest when a result is produced and compared, later, only against
what was recorded -- never against the present.

They live in their own module because they read the filesystem, and every
identity module of the lifecycle is required not to (``tests/regression/
test_v36_invariants.py``): an identity that depended on the machine computing
it would not be an identity. Nothing in :mod:`alphalab.lifecycle` calls these
on a caller's behalf.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import zoneinfo
from pathlib import Path
from typing import Final

from alphalab.lifecycle.fingerprint import EngineBuild

__all__ = [
    "engine_source_digest",
    "running_build",
    "source_tree_digest",
    "tz_database_version",
]


def source_tree_digest(root: Path) -> str:
    """SHA-256 over every ``.py`` file under ``root``, by relative path and content.

    Each file contributes ``<relative posix path> NUL <sha256 of its bytes>``,
    and the lines are sorted by path, so the digest depends on what the files
    say and where they sit -- not on the order a filesystem lists them in.
    ``__pycache__`` is skipped: compiled bytecode is a product of the source,
    and of the interpreter that compiled it.
    """

    lines = []
    for path in sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix()):
        if "__pycache__" in path.parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{path.relative_to(root).as_posix()}\0{digest}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def engine_source_digest() -> str:
    """:attr:`~alphalab.lifecycle.fingerprint.EngineBuild.source_digest` of the imported package."""

    return source_tree_digest(Path(__file__).resolve().parents[1])


_TZ_VERSION: Final = re.compile(r"^#\s*version\s+(\S+)")


def tz_database_version() -> str | None:
    """The IANA release of the time-zone database ``zoneinfo`` uses here, or ``None``.

    ``zoneinfo`` searches :data:`zoneinfo.TZPATH` first and falls back to the
    ``tzdata`` package, so this does the same: the first ``TZPATH`` directory
    holding a ``tzdata.zi`` (whose first line reads ``# version 2025b``) or a
    ``+VERSION`` file answers; failing every one, the ``tzdata`` package's
    ``IANA_VERSION`` does. ``None`` means neither says -- which a manifest
    records as a gap rather than a guess.
    """

    for directory in zoneinfo.TZPATH:
        base = Path(directory)
        compiled = base / "tzdata.zi"
        if compiled.is_file():
            with compiled.open(encoding="utf-8", errors="replace") as handle:
                match = _TZ_VERSION.match(handle.readline())
            if match:
                return match.group(1)
        marker = base / "+VERSION"
        if marker.is_file():
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    try:
        tzdata = importlib.import_module("tzdata")
    except ImportError:
        return None
    version = getattr(tzdata, "IANA_VERSION", None)
    return version if isinstance(version, str) and version else None


def running_build() -> EngineBuild:
    """The build of the engine running in this interpreter, observed now.

    Like :func:`running_engine`, an observation to record, never something a
    verification compares against the present.
    """

    return EngineBuild(source_digest=engine_source_digest(), tz_database=tz_database_version())
