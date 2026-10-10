"""Every limitation and deferral an ADR states is classified in the ledger (v3.13).

The pre-v4 audit inventoried ROADMAP's boundaries and its optional list, but not
the "Known limitations" and "DEFERRED" lists the release ADRs carry; v3.13
found that two deferred items -- exchange-rate return factors (ADR-0043) and
implementation shortfall with an intraday market-impact model (ADR-0044) --
and several stated limitations had no ledger entry at all (TST-015). Each is
now in ``docs/audit/PRE_V4_COMPLETION_LEDGER.yaml``, and this test holds the
ADRs and the ledger together: every item an ADR states must be listed below
with the entry that classifies it, that entry must exist, and it must be
closed -- implemented, or kept with its reason.

A later ADR that states a limitation or defers something fails this test until
the ledger says what happens to it.

v4.0 found the reading incomplete (TST-017): the v2-era ADRs state their
deferrals under "Explicit non-goals", "Not in scope" and "Consequences of
deferring", and the CHANGELOG under "Known gaps", "Known Limitations", "Not in
scope", "Deferred, unchanged", "Recorded" and "Still open" -- 257 items no entry
classified, among them the gap behind PER-008 and the replay resumability that
is now FUT-001. ``docs/audit/scripts/historical_inventory_v4.py`` classifies
each, and the last two tests below hold every one of them to exactly one
classification and the generated ledger entries and inventory to the script.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADR = ROOT / "docs" / "ADR"
LEDGER = ROOT / "docs" / "audit" / "PRE_V4_COMPLETION_LEDGER.yaml"

#: (ADR, item as the ADR states it) -> the ledger entry that classifies it.
CLASSIFIED: dict[tuple[str, str], str] = {
    # ADR-0042, v3.7: point-in-time research and adaptive strategies.
    ("0042", "The execution path dispatches market events only"): "OFE-009",
    ("0042", "Single-instant fundamental helpers scan the series"): "LIM-001",
    ("0042", "`select` materializes what was visible"): "LIM-001",
    ("0042", "`DataRequest.as_of` on wire records cuts on their one timestamp"): "LIM-001",
    ("0042", "A rule is caller code"): "LIM-001",
    ("0042", "Per-share figures and share counts are read as published"): "OFE-011",
    ("0042", "Event studies report no significance test"): "BDY-022",
    (
        "0042",
        "statistical regime models (hidden Markov, Markov switching), which need estimation "
        "with an identity and a stated fitting window",
    ): "OFE-010",
    ("0042", "execution-path delivery of external observations (above)"): "OFE-009",
    ("0042", "a streaming or incremental observation set for data arriving during a live run"): (
        "OFE-011"
    ),
    # ADR-0043, v3.8: portfolio construction and risk.
    ("0043", "Share semantics"): "LIM-002",
    ("0043", "The `CURRENCY` risk dimension is not exchange-rate risk alone"): "FEA-007",
    ("0043", "Crowding is measured within the portfolio"): "LIM-002",
    ("0043", "Return series must already be in one currency"): "LIM-002",
    ("0043", "A box uncertainty set is long-only"): "FEA-009",
    ("0043", "Maximum diversification takes no turnover limit or volatility cap"): "LIM-002",
    ("0043", "Risk parity takes exact budgets and is long-only"): "LIM-002",
    ("0043", "Risk budgets enter construction as exact budgets or a volatility cap"): "LIM-002",
    ("0043", "No shrinkage-intensity estimator"): "OFE-002",
    ("0043", "`validate_risk_constraints` (v1) checks three of `RiskConstraints`' six fields"): (
        "RSK-007"
    ),
    ("0043", "Capital limit shares are measured against total free capital"): "LIM-002",
    ("0043", "Base-currency capital figures are translations"): "LIM-002",
    (
        "0043",
        "A per-strategy amount in an account's `CapitalBudget` is what weight-based sizing "
        "reads, not a separate ceiling",
    ): "OFE-003",
    ("0043", "A netted run is not split into sleeves"): "OFE-015",
    ("0043", "The solvers are pure Python"): "PRF-005",
    ("0043", "The v1 cost estimate now includes the spread"): "LIM-002",
    (
        "0043",
        "Covariance estimators beyond the sample covariance (estimated shrinkage intensity, "
        "EWMA, factor-model covariance)",
    ): "OFE-002",
    ("0043", "cardinality and lot-size constraints"): "OFE-002",
    ("0043", "transaction costs in the objective"): "OFE-002",
    ("0043", "CVaR or drawdown objectives"): "OFE-002",
    ("0043", "multi-period construction"): "OFE-002",
    ("0043", "per-strategy sub-ledgers inside the accounting engine"): "OFE-015",
    ("0043", "exchange-rate return factors"): "FEA-007",
    ("0043", "per-strategy capital ceilings enforced on the execution path"): "OFE-003",
    # ADR-0044, v3.9: the universal execution contract.
    ("0044", "Amendments, positions and balances are not sequence-ordered"): "BRK-002",
    ("0044", "`reconcile_execution_state` compares a pipeline's book with one broker mirror"): (
        "BRK-004"
    ),
    ("0044", "The split is greedy"): "BRK-005",
    ("0044", "The projection refuses what is undeclared"): "LIM-003",
    ("0044", "`ChildOrderBindings` and `RequestLedger` are not persisted"): "BRK-003",
    ("0044", "VWAP assumes volume is uniform within an interval"): "LIM-003",
    ("0044", "Participation's reference volume includes AlphaLab's own fills"): "LIM-003",
    ("0044", "No iceberg randomization and no native reserve-order emulation"): "BRK-006",
    ("0044", "Urgency is stated, not estimated"): "BRK-006",
    ("0044", "Measured amounts are unrounded analytics"): "LIM-003",
    (
        "0044",
        "A child's venue fills are booked on the parent in the order the caller applies them",
    ): "LIM-003",
    (
        "0044",
        "A venue sequence number on orders (and ordering of amendments, positions and balances "
        "by it)",
    ): "BRK-002",
    ("0044", "persisting `ChildOrderBindings` and `RequestLedger` in a snapshot"): "BRK-003",
    ("0044", "a book-to-mirror reconciliation across several brokers' accounts"): "BRK-004",
    ("0044", "an optimal (non-greedy) split under fixed fees or non-linear impact"): "BRK-005",
    ("0044", "estimating urgency from risk aversion, volatility and impact"): "BRK-006",
    ("0044", "randomized iceberg tranches"): "BRK-006",
    ("0044", "implementation shortfall with an intraday market-impact model"): "FEA-008",
    # ADR-0049, v4.0: the freeze.
    ("0049", "A strategy's configuration is persisted as JSON reads it"): "LIM-004",
    ("0049", "Certified identities are per host class"): "LIM-005",
    ("0049", "The operating envelope is measured, not unbounded"): "LIM-006",
    ("0049", "Only CPython 3.12 is tested"): "LIM-007",
    ("0049", "a resumable replay"): "FUT-001",
}


def _stated() -> set[tuple[str, str]]:
    """Every known limitation and deferred item the ADRs state."""

    found: set[tuple[str, str]] = set()
    for path in sorted(ADR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        adr = path.name[:4]
        for block in re.finditer(
            r"\*\*Known limitations\*\*\n\n(.*?)(?=\n\n(?!- |  ))", text, re.S
        ):
            for title in re.findall(r"^- \*\*([^*]+?)\*\*", block.group(1), re.M):
                found.add((adr, " ".join(title.split()).rstrip(".")))
        for row in re.findall(r"^\| (.+?) \| DEFERRED \|$", text, re.M):
            for item in row.split("; "):
                found.add((adr, " ".join(item.split())))
        for paragraph in re.findall(r"^\*\*Deferred\*\* — [^:]*: (.+?)(?=\n\n)", text, re.M | re.S):
            for item in " ".join(paragraph.split()).rstrip(".").split("; "):
                found.add((adr, item))
    return found


def _entries() -> dict[str, str]:
    text = LEDGER.read_text(encoding="utf-8")
    return {entry.split('"', 1)[0]: entry for entry in text.split('\n  - id: "')[1:]}


def test_the_reading_finds_what_the_adrs_state() -> None:
    """A guard on the guard: the two items v3.13 found missing are read."""

    stated = _stated()
    assert len(stated) >= 52
    assert ("0043", "exchange-rate return factors") in stated
    assert ("0044", "implementation shortfall with an intraday market-impact model") in stated


def test_every_stated_limitation_and_deferral_is_listed_here() -> None:
    stated = _stated()
    unlisted = sorted(stated - set(CLASSIFIED))
    stale = sorted(set(CLASSIFIED) - stated)
    assert not unlisted, f"stated by an ADR and classified nowhere: {unlisted}"
    assert not stale, f"listed here and stated by no ADR: {stale}"


def test_every_classifying_entry_exists_and_is_closed() -> None:
    entries = _entries()
    for item, finding in sorted(CLASSIFIED.items()):
        assert finding in entries, f"{item} names {finding}, which the ledger does not have"
        status = re.search(r'\n    implementation_status: "([^"]*)"', entries[finding])
        assert status is not None, finding
        # Accepted future work is closed too, with the fields the ledger guard
        # requires of it (v4.0, test_nothing_is_left_for_later.py).
        assert status.group(1).startswith(("implemented", "kept", "accepted as future work")), (
            f"{item}: {finding} is {status.group(1)!r}"
        )


def _inventory() -> ModuleType:
    path = ROOT / "docs" / "audit" / "scripts" / "historical_inventory_v4.py"
    spec = importlib.util.spec_from_file_location("historical_inventory_v4", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_historical_item_is_classified_once() -> None:
    """Every item a v2-era ADR or the CHANGELOG states matches exactly one row (TST-017)."""

    inventory = _inventory()
    stated = inventory.stated_items()
    assert sum(len(items) for items in stated.values()) == HISTORICAL_ITEMS, (
        "the reading found another number of items than the history states"
    )
    assert "No replay resumability." in " ".join(stated["HIS-019"]), "a guard on the guard"
    assert not inventory.problems(), "\n".join(inventory.problems())

    entries = _entries()
    for section in inventory.SECTIONS:
        assert section.ledger_id in entries, f"{section.ledger_id} is not in the ledger"
    for rows in inventory.CLASSIFICATION.values():
        for _, status, evidence, _ in rows:
            for finding in re.findall(r"\b[A-Z]{3}-\d{3}\b", evidence):
                assert finding in entries, f"{finding} is cited and not in the ledger"
            if status == "FUTURE":
                assert re.search(r"\bFUT-\d{3}\b", evidence), "future work names its entry"


def test_an_item_classified_by_no_row_or_by_two_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guard on the guard: the inventory reports what it cannot classify exactly once.

    Every item the history states is classified once, so the real reading never
    exercises the report; v4.0's mutation run found that deleting it left every
    test passing (mutation Z13). Here the reading is given an item no row
    classifies, and then a section in which two rows classify the same item.
    """

    inventory = _inventory()
    stated = inventory.stated_items()
    unclassified = "an item the history states and nobody classified"
    monkeypatch.setattr(
        inventory,
        "stated_items",
        lambda: {**stated, "HIS-019": [*stated["HIS-019"], unclassified]},
    )
    assert f"HIS-019: 0 rows match {unclassified!r}" in inventory.problems()

    monkeypatch.setattr(inventory, "stated_items", lambda: stated)
    rows = inventory.CLASSIFICATION["HIS-020"]
    monkeypatch.setattr(
        inventory, "CLASSIFICATION", {**inventory.CLASSIFICATION, "HIS-020": (*rows, rows[0])}
    )
    assert any(problem.startswith("HIS-020: 2 rows match") for problem in inventory.problems())


#: The items the v2.0.0 to v3.13.0 history states in the sections the inventory reads.
HISTORICAL_ITEMS = 257

#: Where a document states that count, and the phrase it states it in.
STATED_COUNTS = (
    ("docs/audit/V4_HISTORICAL_INVENTORY.md", r"\*\*(\d+) items in 39 sections:\*\*"),
    ("docs/audit/V4_RELEASE_AUDIT.md", r"\*\*39 sections, (\d+) items\*\*"),
    ("docs/ADR/0049-*.md", r"(\d+) items in 39"),
    ("CHANGELOG.md", r"-- (\d+) items in\s+39 sections"),
    ("ROADMAP.md", r"(\d+) items in 39 sections"),
    ("nowandfuture.md", r"-- (\d+) items the pre-v4"),
    ("docs/audit/PRE_V4_MASTER_AUDIT.md", r"open questions, (\d+) items classified"),
)


def test_every_count_of_the_inventory_agrees() -> None:
    """The inventory's rows, its status counts and every document's count are one number.

    v4.0's first inventory counted 258 rows -- a bullet v4.0 itself had added to
    ``nowandfuture.md``'s open questions -- while every document said 257. The
    bullet is prose beside the list now, and this test holds the numbers together.
    """

    inventory = _inventory()
    rows = sum(len(items) for items in inventory.matches().values())
    document = (ROOT / "docs" / "audit" / "V4_HISTORICAL_INVENTORY.md").read_text(encoding="utf-8")
    names = "|".join(re.escape(status) for status in inventory.STATUSES)
    statuses = sum(int(count) for count in re.findall(rf"\b(?:{names}) (\d+)\b", document))
    tabulated = len(re.findall(rf"^\| .+ \| (?:{names}) \| .+ \| .+ \|$", document, re.M))
    assert rows == statuses == tabulated == HISTORICAL_ITEMS, (rows, statuses, tabulated)
    for path, phrase in STATED_COUNTS:
        (source,) = ROOT.glob(path)
        found = re.findall(phrase, source.read_text(encoding="utf-8"))
        assert found, f"{path} no longer states the count as the test reads it"
        assert {int(count) for count in found} == {HISTORICAL_ITEMS}, f"{path} says {found}"


def test_the_inventory_and_its_ledger_entries_are_current() -> None:
    """The HIS entries and V4_HISTORICAL_INVENTORY.md are the script's output, not hand edits."""

    assert _inventory().main(["--check"]) == 0
