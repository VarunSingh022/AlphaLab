"""The version is written once, and everything reports that one (ledger REP-001).

Until v3.10 ``pyproject.toml`` declared the version, and the package reported
the *installed distribution's* metadata with a hard-coded fallback -- three
places, and a stale editable install reported the version it was installed at
rather than the source actually imported. Strategy fingerprints and dataset
provenance inherited the wrong one.
"""

import ast
import re
import tomllib
from pathlib import Path

import alphalab
from alphalab.common import _version
from alphalab.common.version import __version__ as common_version
from alphalab.lifecycle.fingerprint import running_engine

ROOT = Path(__file__).resolve().parents[2]
#: A release heading: ``# [3.9.0] - 2026-09-27``.
RELEASE = re.compile(r"^#+ \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def test_the_package_reports_the_version_its_source_declares() -> None:
    assert alphalab.__version__ == common_version == _version.__version__
    assert running_engine().version == _version.__version__


def test_the_build_reads_the_same_file() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" not in project["project"], "a second declaration would be a second source"
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "alphalab/common/_version.py"


def test_nothing_reads_installed_metadata_for_the_version() -> None:
    offenders = []
    for path in sorted((ROOT / "alphalab").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "importlib.metadata":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_the_changelog_leads_with_this_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases = [match.group(1) for match in RELEASE.finditer(changelog)]

    assert releases[0] == _version.__version__
