"""Every document that states the current version states the package's (v3.13, ledger DOC-008).

The version is declared once, in ``alphalab/common/_version.py`` (REP-001), and
``test_one_version_source.py`` holds the build and the CHANGELOG to it. Four
documents restate it for a reader, and nothing held them: at 3.12.0
``nowandfuture.md``'s identity table still said 3.9.0, three releases on, and
``docs/README.md``'s Version block once read ``v2.5.0`` for twelve releases.
Each marker is read here and held to ``alphalab.__version__`` -- or, where a
document names the release series, to its major and minor version.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import alphalab

ROOT = Path(__file__).resolve().parents[2]
VERSION = alphalab.__version__
SERIES = ".".join(VERSION.split(".")[:2])

#: (document, a pattern whose one group is the full version it states).
MARKERS: tuple[tuple[str, str], ...] = (
    ("README.md", r"^\| Version \| (\d+\.\d+\.\d+) \|$"),
    ("README.md", r"^\*\*Current Release:\*\* \*\*v(\d+\.\d+\.\d+) "),
    # The badge read 3.11.0 through two releases, and nothing read it (v4.0).
    ("README.md", r"badge/Version-(\d+\.\d+\.\d+)-blue"),
    ("docs/README.md", r"^# Version\n\n```\nv(\d+\.\d+\.\d+)\n```$"),
    ("docs/ARCHITECTURE.md", r"^Version: v(\d+\.\d+\.\d+)$"),
    (
        "nowandfuture.md",
        r"^\*\*A long-term project reference, written at v3\.0\.0 and updated at "
        r"v(\d+\.\d+\.\d+)\.\*\*$",
    ),
    ("nowandfuture.md", r"^\| Version \| \*\*(\d+\.\d+\.\d+)\*\* \|$"),
)

#: (document, a pattern whose one group is the release series it states).
SERIES_MARKERS: tuple[tuple[str, str], ...] = (
    ("docs/ARCHITECTURE.md", r"^# Implementation Status \(v(\d+\.\d+)\)$"),
    ("docs/ARCHITECTURE.md", r"See the \*\*Implementation Status \(v(\d+\.\d+)\)\*\* section"),
    ("docs/ARCHITECTURE.md", r"^Status: Implementation Status \(v(\d+\.\d+)\) describes"),
)


def _stated(document: str, pattern: str) -> set[str]:
    text = (ROOT / document).read_text(encoding="utf-8")
    found = set(re.findall(pattern, text, re.MULTILINE))
    assert found, f"{document}: nothing matches {pattern!r}; the marker moved or was removed"
    return found


@pytest.mark.parametrize(("document", "pattern"), MARKERS)
def test_every_version_marker_names_the_package_version(document: str, pattern: str) -> None:
    stated = _stated(document, pattern)
    assert stated == {VERSION}, f"{document} states {sorted(stated)}; the package is {VERSION}"


@pytest.mark.parametrize(("document", "pattern"), SERIES_MARKERS)
def test_every_series_marker_names_the_package_series(document: str, pattern: str) -> None:
    stated = _stated(document, pattern)
    assert stated == {SERIES}, f"{document} states v{sorted(stated)}; the package is v{SERIES}"
