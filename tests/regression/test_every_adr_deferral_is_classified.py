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
"""

from __future__ import annotations

import re
from pathlib import Path

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
        assert status.group(1).startswith(("implemented", "kept")), (
            f"{item}: {finding} is {status.group(1)!r}"
        )
