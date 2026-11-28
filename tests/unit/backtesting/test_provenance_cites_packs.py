"""A run's provenance cites the reference packs its inputs came from (v4.3, plan G.1.3).

* **Cited, and identified.** Each pack's citation -- id, version, digest -- is
  a ``pack`` line of the record's canonical key, in order, so a record citing a
  pack is another record than one citing none.
* **Stored and read back.** The citations travel with the stored record.
* **Nothing moves for a record citing none.** Its stored form has no ``packs``
  field and its canonical key no ``pack`` line: the record it always was.
* **Refused**: a citation given twice, and a value that is not one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alphalab.backtesting.provenance import payload_provenance, provenance_from_primitives
from alphalab.data.packs import PackCitation, load_pack
from alphalab.persistence.serializer import serialize

ROOT = Path(__file__).resolve().parents[3]
PACK = load_pack(ROOT / "examples" / "data" / "packs" / "synthetic_venues_2025.json")
OTHER = PackCitation("another/pack", "1", "0" * 64)


def test_a_cited_pack_is_a_line_of_the_records_identity() -> None:
    plain = payload_provenance("{}", schedule=None)
    cited = payload_provenance("{}", schedule=None, packs=[PACK.citation, OTHER])
    assert cited.canonical_key.splitlines()[-2:] == [
        f"pack=alphalab-examples/synthetic-venues|2025.1|{PACK.digest}",
        f"pack=another/pack|1|{'0' * 64}",
    ]
    assert cited.provenance_id != plain.provenance_id
    assert cited.packs == (PACK.citation, OTHER)  # in order of id


def test_the_citations_travel_with_the_stored_record() -> None:
    cited = payload_provenance("{}", schedule=None, packs=[PACK.citation])
    read = provenance_from_primitives(json.loads(serialize(cited)))
    assert read == cited and read.provenance_id == cited.provenance_id


def test_a_record_citing_none_is_the_record_it_always_was() -> None:
    plain = payload_provenance("{}", schedule=None)
    stored = json.loads(serialize(plain))
    assert "packs" not in stored
    assert set(stored) == {
        "subject_kind",
        "subject_digest",
        "schedule",
        "instruments_digest",
        "produced_at",
        "scheme",
        "schema_version",
    }
    assert not any(line.startswith("pack=") for line in plain.canonical_key.splitlines())
    assert provenance_from_primitives(stored) == plain


def test_a_citation_is_given_once_and_is_a_citation() -> None:
    with pytest.raises(ValueError, match="cited once"):
        payload_provenance("{}", schedule=None, packs=[PACK.citation, PACK.citation])
    with pytest.raises(ValueError, match="are PackCitations"):
        payload_provenance("{}", schedule=None, packs=["a pack"])  # type: ignore[list-item]
