"""The engine build a manifest records (ledger REP-002, DAT-007).

A version string cannot tell two builds of one version apart, and every
local-time computation depends on the host's time-zone database. The build
records both -- in a manifest, never in a strategy fingerprint.
"""

import importlib
import re
import zoneinfo
from pathlib import Path

import pytest

from alphalab.lifecycle import (
    EngineBuild,
    RerunOutcome,
    assess_reproducibility,
    canonical_manifest_key,
    engine_source_digest,
    manifest_for_run,
    manifest_gaps,
    running_build,
    source_tree_digest,
    tz_database_version,
    verify_manifest,
)
from alphalab.lifecycle.exceptions import LifecycleInputError
from tests.unit.lifecycle.evidence_harness import BUILD, ENGINE, fingerprint, ingest, run_backtest


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_source_digest_follows_every_byte_and_every_path(tmp_path: Path) -> None:
    base = _tree(tmp_path / "a", {"pkg/__init__.py": "", "pkg/m.py": "x = 1\n"})
    same = _tree(tmp_path / "b", {"pkg/m.py": "x = 1\n", "pkg/__init__.py": ""})
    edited = _tree(tmp_path / "c", {"pkg/__init__.py": "", "pkg/m.py": "x = 1  # note\n"})
    moved = _tree(tmp_path / "d", {"pkg/__init__.py": "", "pkg/n.py": "x = 1\n"})
    cached = _tree(
        tmp_path / "e",
        {"pkg/__init__.py": "", "pkg/m.py": "x = 1\n", "pkg/__pycache__/m.py": "stale"},
    )
    digest = source_tree_digest(base)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert source_tree_digest(same) == digest
    assert source_tree_digest(cached) == digest
    assert source_tree_digest(edited) != digest
    assert source_tree_digest(moved) != digest


def test_the_running_build_is_this_package_and_this_host() -> None:
    build = running_build()
    assert build.source_digest == engine_source_digest()
    assert build.tz_database == tz_database_version()
    assert running_build() == build


def test_the_tz_version_is_read_where_zoneinfo_reads_zones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiled = tmp_path / "compiled"
    compiled.mkdir()
    (compiled / "tzdata.zi").write_text("# version 2099z\n# more\n", encoding="utf-8")
    marker = tmp_path / "marker"
    marker.mkdir()
    (marker / "+VERSION").write_text("2098y\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()

    monkeypatch.setattr(zoneinfo, "TZPATH", (str(empty), str(compiled), str(marker)))
    assert tz_database_version() == "2099z"
    monkeypatch.setattr(zoneinfo, "TZPATH", (str(marker), str(compiled)))
    assert tz_database_version() == "2098y"


def test_no_discoverable_database_is_none_not_a_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_tzdata(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(zoneinfo, "TZPATH", (str(tmp_path),))
    monkeypatch.setattr(importlib, "import_module", no_tzdata)
    assert tz_database_version() is None


@pytest.mark.parametrize(
    ("digest", "tz"), [("abc", "2025b"), ("A" * 64, "2025b"), ("ab" * 32, " "), ("ab" * 32, "")]
)
def test_a_malformed_build_is_refused(digest: str, tz: str) -> None:
    with pytest.raises(LifecycleInputError):
        EngineBuild(source_digest=digest, tz_database=tz)


# --------------------------------------------------------------------------- #
# In a manifest
# --------------------------------------------------------------------------- #


def _manifest(build: EngineBuild | None):  # type: ignore[no-untyped-def]
    dataset = ingest()
    return manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE, build=build)


def test_the_build_enters_the_manifest_identity_only_when_recorded() -> None:
    recorded = _manifest(BUILD)
    unrecorded = _manifest(None)
    assert verify_manifest(recorded) and verify_manifest(unrecorded)
    assert recorded.manifest_id != unrecorded.manifest_id
    args = (
        recorded.kind,
        recorded.result_id,
        recorded.dataset_version,
        recorded.dataset_content_hash,
        recorded.configuration_id,
        recorded.seed,
        recorded.seed_role,
        recorded.engine,
        recorded.fingerprint.fingerprint if recorded.fingerprint else None,
    )
    with_build = canonical_manifest_key(*args, BUILD)
    without = canonical_manifest_key(*args)
    assert with_build == without + (
        f"\nengine.source={BUILD.source_digest!r}\nengine.tz_database={BUILD.tz_database!r}"
    )


def test_an_unrecorded_build_and_an_unknown_database_are_gaps() -> None:
    assert any("no engine build recorded" in gap for gap in manifest_gaps(_manifest(None)))
    unknown = EngineBuild(source_digest=BUILD.source_digest, tz_database=None)
    assert any("time-zone database" in gap for gap in manifest_gaps(_manifest(unknown)))
    assert not any("engine" in gap for gap in manifest_gaps(_manifest(BUILD)))


def test_a_rerun_on_another_build_is_not_a_rerun_of_the_same_inputs() -> None:
    original = _manifest(BUILD)
    patched = _manifest(EngineBuild(source_digest="cd" * 32, tz_database="2025b"))
    other_zone = _manifest(EngineBuild(source_digest=BUILD.source_digest, tz_database="2024a"))
    for rerun, label in ((patched, "engine source"), (other_zone, "time-zone database")):
        assessment = assess_reproducibility(original, rerun)
        assert assessment.rerun is RerunOutcome.INPUTS_DIFFER
        assert any(line.startswith(label) for line in assessment.rerun_detail)
    assert assess_reproducibility(original, _manifest(BUILD)).rerun is RerunOutcome.REPRODUCED


def test_the_strategy_fingerprint_does_not_see_the_build() -> None:
    assert _manifest(BUILD).fingerprint == _manifest(None).fingerprint
