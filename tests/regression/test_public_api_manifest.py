"""The public API is exactly what ``docs/api/public_api.json`` records (ledger API-002).

Until v3.13 nothing said what AlphaLab's public API *was*: 44 packages
exported some 2,450 names through ``__all__``, and an addition, a removal or a
rebinding reached a release unreviewed unless a test happened to import the
name. The manifest is that API as data -- every package's exported names, each
with what it is bound to -- and this module fails on any difference, naming it.
A deliberate change regenerates the manifest (``python
docs/api/generate_public_api.py``) and is reviewed as a diff of it.

It also holds the rule ledger API-001 settled: **one name, one contract**. A
name two packages export is the same object in both, or the manifest lists it
under ``shared_names`` with the reason the two are deliberately different.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import alphalab

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "api" / "public_api.json"
RECORDED: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))

_REGENERATE = "regenerate with `python docs/api/generate_public_api.py` and review the diff"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_public_api", ROOT / "docs" / "api" / "generate_public_api.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    built = _generator().build(RECORDED)
    assert isinstance(built, dict)
    return built


def test_the_manifest_is_this_releases() -> None:
    assert RECORDED["release"] == alphalab.__version__, (
        f"docs/api/public_api.json records release {RECORDED['release']}: {_REGENERATE}"
    )


def test_no_package_was_added_or_removed(current: dict[str, Any]) -> None:
    added = sorted(set(current["packages"]) - set(RECORDED["packages"]))
    removed = sorted(set(RECORDED["packages"]) - set(current["packages"]))
    assert not added and not removed, f"packages added {added}, removed {removed}: {_REGENERATE}"


@pytest.mark.parametrize("package", sorted(RECORDED["packages"]))
def test_every_export_is_the_one_the_manifest_records(
    package: str, current: dict[str, Any]
) -> None:
    expected: dict[str, str] = RECORDED["packages"][package]
    actual: dict[str, str] = current["packages"].get(package, {})
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    rebound = sorted(
        f"{name}: {expected[name]} -> {actual[name]}"
        for name in set(expected) & set(actual)
        if expected[name] != actual[name]
    )
    assert not (added or removed or rebound), (
        f"{package}: added {added}, removed {removed}, rebound {rebound}: {_REGENERATE}"
    )


def test_every_shared_name_is_listed_with_its_reason(current: dict[str, Any]) -> None:
    found = {name: entry["bound_to"] for name, entry in current["shared_names"].items()}
    listed = {name: entry["bound_to"] for name, entry in RECORDED["shared_names"].items()}
    unlisted = sorted(set(found) - set(listed))
    stale = sorted(set(listed) - set(found))
    assert not unlisted, (
        f"names exported as different objects by different packages, with no reason recorded: "
        f"{unlisted}. Make them one object, rename one, or {_REGENERATE} and write the reason."
    )
    assert not stale, f"shared_names lists names no longer shared: {stale}: {_REGENERATE}"
    assert found == listed
    unexplained = sorted(
        name for name, entry in RECORDED["shared_names"].items() if not entry["reason"].strip()
    )
    assert not unexplained, f"shared names without a reason: {unexplained}"


def test_the_manifest_is_exactly_what_the_generator_writes(current: dict[str, Any]) -> None:
    assert MANIFEST.read_text(encoding="utf-8") == _generator().render(current), _REGENERATE


def test_the_readme_states_the_manifest_it_describes() -> None:
    """``docs/api/PUBLIC_API.md`` counts the manifest in prose; the prose had drifted.

    At 3.13's first writing it gave 2,466 exports while the manifest grew to
    2,479 (ledger DOC-008). The sentence is read here and held to the manifest.
    """

    text = (ROOT / "docs" / "api" / "PUBLIC_API.md").read_text(encoding="utf-8")
    stated = re.search(
        r"At v(?P<release>[\d.]+) the manifest records (?P<packages>\d+) packages, "
        r"(?P<exports>[\d,]+) exports and (?P<shared>\d+) shared names",
        text,
    )
    assert stated is not None, "the counting sentence moved; this test reads it"
    assert stated["release"] == RECORDED["release"] == alphalab.__version__
    assert int(stated["packages"]) == len(RECORDED["packages"])
    exports = sum(len(names) for names in RECORDED["packages"].values())
    assert int(stated["exports"].replace(",", "")) == exports
    assert int(stated["shared"]) == len(RECORDED["shared_names"])
