"""Evidence is kept, found by what it is, and read back verified (ledger OFE-016, BDY-008).

Until v3.12 a reproducibility manifest, a strategy fingerprint or a performance
report had nowhere durable to live, and each application invented a format.
:class:`~alphalab.model_registry.evidence.EvidenceStore` keeps the bytes in the
content-addressed artifact store and files each value under its own identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from alphalab.lifecycle import ReproducibilityManifest, manifest_for_run
from alphalab.model_registry.evidence import (
    EvidenceRef,
    FileEvidenceIndex,
    SelfIdentified,
    file_evidence_store,
    memory_evidence_store,
)
from alphalab.persistence.exceptions import PersistenceValidationError, StorageError
from tests.unit.lifecycle.evidence_harness import BUILD, ENGINE, fingerprint, ingest, run_backtest


@dataclass(frozen=True)
class _Note:
    """A value with no identity of its own."""

    text: str
    weight: int


def _manifest() -> ReproducibilityManifest:
    dataset = ingest()
    return manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE, build=BUILD)


def test_a_manifest_and_a_fingerprint_are_filed_under_their_own_identities() -> None:
    store = memory_evidence_store()
    manifest = _manifest()
    strategy = fingerprint()

    identified: tuple[object, ...] = (manifest, strategy)
    assert all(isinstance(value, SelfIdentified) for value in identified)
    kept = store.put(manifest)
    filed = store.put(strategy)

    assert (kept.kind, kept.identity) == ("reproducibility_manifest", manifest.manifest_id)
    assert (filed.kind, filed.identity) == ("strategy_fingerprint", strategy.fingerprint)
    assert store.find("reproducibility_manifest", manifest.manifest_id) == kept
    item = store.get(kept)
    assert item.value["manifest_id"] == manifest.manifest_id
    assert store.identities("strategy_fingerprint") == (strategy.fingerprint,)


def test_keeping_a_value_twice_is_one_piece_of_evidence() -> None:
    store = memory_evidence_store()
    manifest = _manifest()

    assert store.put(manifest) == store.put(manifest)
    assert store.identities("reproducibility_manifest") == (manifest.manifest_id,)


def test_a_different_value_under_a_filed_identity_is_refused() -> None:
    store = memory_evidence_store()
    store.put(_Note("first", 1), kind="note", identity="n-1")

    with pytest.raises(PersistenceValidationError, match="An identity names one value"):
        store.put(_Note("second", 2), kind="note", identity="n-1")


def test_a_refused_value_leaves_no_bytes_behind(tmp_path: Path) -> None:
    """The conflict is found before anything is written (mutation X19)."""

    store = file_evidence_store(tmp_path)
    store.put(_Note("first", 1), kind="note", identity="n-1")
    held = sorted(path for path in (tmp_path / "artifacts").rglob("*") if path.is_file())

    with pytest.raises(PersistenceValidationError, match="An identity names one value"):
        store.put(_Note("second", 2), kind="note", identity="n-1")

    assert sorted(path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()) == held


def test_a_value_with_no_identity_is_filed_under_its_content() -> None:
    store = memory_evidence_store()
    one = store.put(_Note("text", 1), kind="note")
    same = store.put(_Note("text", 1), kind="note")
    other = store.put(_Note("text", 2), kind="note")

    assert one == same
    assert one.identity != other.identity and len(one.identity) == 64
    assert store.get(other).value == {"text": "text", "weight": 2}


def test_the_bytes_are_the_same_in_every_store() -> None:
    first = memory_evidence_store().put(_Note("text", 1), kind="note", identity="n")
    second = memory_evidence_store().put(_Note("text", 1), kind="note", identity="n")

    assert first.digest == second.digest


def test_a_file_store_is_read_by_a_later_process(tmp_path: Path) -> None:
    manifest = _manifest()
    kept = file_evidence_store(tmp_path).put(manifest)

    later = file_evidence_store(tmp_path)

    assert later.find(kept.kind, kept.identity) == kept
    assert later.get(kept).value["manifest_id"] == manifest.manifest_id
    with pytest.raises(PersistenceValidationError, match="An identity names one value"):
        later.put(_Note("not the manifest", 0), kind=kept.kind, identity=kept.identity)


def test_changed_bytes_are_refused_on_read(tmp_path: Path) -> None:
    store = file_evidence_store(tmp_path)
    kept = store.put(_Note("text", 1), kind="note", identity="n")
    (stored,) = [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    stored.write_bytes(stored.read_bytes().replace(b'"text"', b'"edit"'))

    with pytest.raises(StorageError):
        store.get(kept)


def test_a_reference_to_someone_elses_bytes_is_refused() -> None:
    store = memory_evidence_store()
    note = store.put(_Note("text", 1), kind="note", identity="n")

    with pytest.raises(StorageError, match="says it is note 'n'"):
        store.get(EvidenceRef("note", "m", note.digest))


def test_an_index_entry_naming_another_identity_is_refused(tmp_path: Path) -> None:
    index = FileEvidenceIndex(tmp_path)
    index.set("note", "a", "f" * 64)
    (entry,) = (tmp_path / "note").glob("*.json")
    entry.write_text(entry.read_text().replace('"a"', '"b"'), encoding="utf-8")

    with pytest.raises(StorageError, match="not note 'a'"):
        index.get("note", "a")


@pytest.mark.parametrize(
    ("kind", "identity", "message"),
    [
        ("Has Space", "x", "evidence kind"),
        ("", "x", "evidence kind"),
        ("note", " ", "evidence identity"),
        ("note", "line\nbreak", "evidence identity"),
    ],
)
def test_a_malformed_address_is_refused(kind: str, identity: str, message: str) -> None:
    with pytest.raises(PersistenceValidationError, match=message):
        memory_evidence_store().put(_Note("text", 1), kind=kind, identity=identity)


def test_a_value_that_does_not_say_its_kind_must_be_told_it() -> None:
    with pytest.raises(PersistenceValidationError, match="name the kind"):
        memory_evidence_store().put(_Note("text", 1))


def test_a_store_does_not_create_its_own_root(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="does not exist"):
        file_evidence_store(tmp_path / "mistyped")
