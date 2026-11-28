"""A reference pack is data with its evidence, and refuses what it cannot vouch for (ADR-0059).

A :class:`~alphalab.data.packs.ReferencePack` states where its data came from,
when it was taken, the range it covers and its licence, and a content digest of
its entries. It refuses entries that do not match that digest, an entry of the
wrong type or shape, one outside its range or announced after it was taken, and
a universe it does not hold. A universe read from it through
:func:`~alphalab.instrument.membership.universe_from_pack` carries the pack's
range and knowledge, so a question outside them is refused in a run too.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from alphalab.data.exceptions import DataValidationError
from alphalab.data.packs import (
    REFERENCE_PACK_SCHEME,
    PackCitation,
    PackSource,
    ReferencePack,
    reference_pack_digest,
)
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.membership import MembershipKind, universe_from_pack


def _entry(
    asset: str, kind: str, effective: float, announced: float, universe: str = "IDX"
) -> dict[str, Any]:
    return {
        "type": "MembershipChange",
        "universe": universe,
        "asset_id": asset,
        "kind": kind,
        "effective_at": effective,
        "announced_at": announced,
    }


ENTRIES = (
    _entry("b", "ADDITION", 0.0, 0.0),
    _entry("a", "ADDITION", 0.0, 0.0),
    _entry("c", "ADDITION", 20.0, 12.0),
    _entry("b", "REMOVAL", 30.0, 25.0),
)


def _pack(**changed: Any) -> ReferencePack:
    fields: dict[str, Any] = {
        "pack_id": "idx-membership",
        "version": "2026.11",
        "source": PackSource("Index Provider", "methodology notice 2026-11"),
        "as_of": 40.0,
        "valid_from": 0.0,
        "valid_to": 50.0,
        "licence": "for research use under the provider's terms",
        "entries": ENTRIES,
        "digest": reference_pack_digest(ENTRIES),
    }
    fields.update(changed)
    return ReferencePack(**fields)


def test_the_digest_is_of_the_entries_canonical_text_whatever_their_order() -> None:
    ordered = sorted(ENTRIES, key=lambda e: (e["universe"], e["effective_at"], e["asset_id"]))
    text = json.dumps(
        {"entries": ordered, "scheme": REFERENCE_PACK_SCHEME},
        sort_keys=True,
        separators=(",", ":"),
    )

    assert REFERENCE_PACK_SCHEME == "alphalab.reference_pack.v1"
    assert reference_pack_digest(ENTRIES) == hashlib.sha256(text.encode()).hexdigest()
    assert reference_pack_digest(tuple(reversed(ENTRIES))) == reference_pack_digest(ENTRIES)
    assert reference_pack_digest(ENTRIES) == (
        "71dfbc90a3110e51729958ea12a2a0d71ae3e726725bc8b8d9bc7f978e97d673"
    )


def test_a_pack_states_where_it_came_from_and_is_cited_by_it() -> None:
    pack = _pack()

    assert pack.citation == PackCitation(
        "idx-membership", "2026.11", reference_pack_digest(ENTRIES)
    )
    assert pack.universes == ("IDX",)
    assert [entry["asset_id"] for entry in pack.membership("IDX")] == ["a", "b", "c", "b"]


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"digest": "0" * 64}, "not the entries it states"),
        ({"valid_to": 0.0}, "empty"),
        ({"source": "Index Provider"}, "PackSource"),
        ({"licence": " "}, "licence"),
        ({"as_of": 20.0}, "announced at 25.0, after it was taken"),
        ({"valid_to": 30.0}, "a pack claims the range it holds"),
    ],
)
def test_a_pack_refuses_what_it_cannot_vouch_for(changed: dict[str, Any], match: str) -> None:
    with pytest.raises(DataValidationError, match=match):
        _pack(**changed)


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ({**_entry("a", "ADDITION", 0.0, 0.0), "type": "FeeSchedule"}, "has type 'FeeSchedule'"),
        ({**_entry("a", "ADDITION", 0.0, 0.0), "note": "x"}, "has exactly"),
        (_entry("a", "MERGER", 0.0, 0.0), "membership kind 'MERGER'"),
        (_entry("a", "ADDITION", float("nan"), 0.0), "finite instant"),
        (_entry("", "ADDITION", 0.0, 0.0), "must be named"),
        ("not a record", "not a record"),
    ],
)
def test_an_entry_is_of_a_type_a_pack_holds_and_of_its_shape(entry: Any, match: str) -> None:
    with pytest.raises(DataValidationError, match=match):
        reference_pack_digest((entry,))


def test_a_missing_universe_is_refused_never_assumed_empty() -> None:
    with pytest.raises(DataValidationError, match="never assumed empty"):
        _pack().membership("OTHER")


def test_a_universe_from_a_pack_carries_its_range_and_its_knowledge() -> None:
    universe = universe_from_pack(_pack(), "IDX")

    assert universe.coverage == (0.0, 50.0)
    assert universe.known_until == 40.0
    assert universe.members(35.0, as_of=35.0) == {"a", "c"}
    assert universe.changes[-1].kind is MembershipKind.REMOVAL
    with pytest.raises(InstrumentInputError, match="assumed, not read"):
        universe.members(55.0, as_of=40.0)
    with pytest.raises(InstrumentInputError, match="nothing was announced since"):
        universe.members(35.0, as_of=41.0)
    with pytest.raises(DataValidationError, match="never assumed empty"):
        universe_from_pack(_pack(), "OTHER")


def test_a_pack_whose_history_could_not_have_happened_is_refused_when_read() -> None:
    entries = (*ENTRIES, _entry("a", "ADDITION", 10.0, 10.0))
    pack = _pack(entries=entries, digest=reference_pack_digest(entries))

    with pytest.raises(InstrumentInputError, match="already a member"):
        universe_from_pack(pack, "IDX")
