"""The documents that state AlphaLab's direction state one charter, ADR-0056 (plan WS-00, GAP-15).

Until v4.3 they disagreed. ``ROADMAP.md`` called the engine "complete and
frozen"; ``nowandfuture.md`` said "nothing is planned any more" and that a new
engine would have "no in-repo consumer, and that is correct";
``docs/SYSTEM_DESIGN.md`` called joining the standalone engines "not pending
work". The charter separates the contract, which stays stable, from the
capability, which grows toward a universal research engine, and keeps the
boundary that no single runtime spans the engines (ADR-0009). These tests hold
the five direction documents, and the charter itself, to that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHARTER = (
    ROOT
    / "docs"
    / "ADR"
    / "0056-the-product-charter-a-universal-research-engine-on-a-stable-contract.md"
)
#: The documents that state where AlphaLab is going.
DIRECTION = (
    "ROADMAP.md",
    "docs/VISION.md",
    "nowandfuture.md",
    "docs/ARCHITECTURE.md",
    "docs/SYSTEM_DESIGN.md",
)
#: The claims the charter retired, each as it read once whitespace is folded.
RETIRED = (
    "complete and frozen",
    "nothing is planned any more",
    "no in-repo consumer, and that is correct",
    "is not pending work",
)


def _folded(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split()).lower()


@pytest.mark.parametrize("document", DIRECTION)
def test_each_direction_document_cites_the_charter(document: str) -> None:
    assert "ADR-0056" in (ROOT / document).read_text(encoding="utf-8")


@pytest.mark.parametrize("claim", RETIRED)
@pytest.mark.parametrize("document", DIRECTION)
def test_no_direction_document_says_the_engine_is_finished(document: str, claim: str) -> None:
    assert claim not in _folded(ROOT / document)


def test_the_no_single_runtime_boundary_is_kept_where_it_was_stated() -> None:
    assert "No single runtime spanning all engines" in (ROOT / "ROADMAP.md").read_text(
        encoding="utf-8"
    )
    assert "not fused into a single runtime" in _folded(ROOT / "docs" / "VISION.md")
    assert "no single runtime spans the engines** (adr-0009)" in _folded(CHARTER)


def test_the_charter_binds_every_reopening_to_the_owners_principles() -> None:
    """A boundary may be reopened by its own ADR; these may not be traded away by one."""

    charter = _folded(CHARTER)

    assert "**accepted** (2026-11-10)" in charter
    for principle in (
        "zero mandatory runtime dependencies",
        "through stable core interfaces",
        "a separate process alone is not a secure sandbox",
        "enforced by the host or deployment environment",
        "numerical precision follows error analysis",
        "no historical `result_id`, `configuration_id`, schema or persisted format",
    ):
        assert principle in charter, principle


def test_an_engine_joins_the_research_path_by_a_bridge_or_stays_a_library_by_choice() -> None:
    folded = _folded(ROOT / "nowandfuture.md")

    assert "integrate it through a tested bridge" in folded
    assert "keep it a library by stated choice" in folded
