"""Statements the code contradicted stay corrected (plan WS-20, the High items of GAP-30).

The v4.2.1 audits found documents telling strategy authors and security readers
things the code does not do: that a refused amend loses the original order,
that three strategy hooks are never invoked, that the position check adds a
notional to a quantity, that the library ships an HMAC-signed venue transport,
that vendor adapters are good first contributions here, that the HTTP
transport's schemes need no mention, and that nothing in AlphaLab resolves a
name to code. Each was corrected in v4.3 against the code that contradicts it.

A correction nothing holds drifts back, so each false statement is pinned
absent, beside the evidence that made it false, and the two corrections that
must be repeated at every release -- the authoritative status section, and the
security policy's account of the transports -- are pinned present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import alphalab

ROOT = Path(__file__).resolve().parents[2]

#: (document, the statement that was false, what makes it false).
CORRECTED = [
    (
        "docs/COMPOSING_STRATEGIES.md",
        "the original is gone",
        "a refused amend keeps the order working (FUT-031, _amends_requested)",
    ),
    (
        "nowandfuture.md",
        "never invoked by the runtime",
        "on_start, on_shutdown and on_stop are delivered (EXE-005, Dispatcher.start/stop)",
    ),
    (
        "docs/audit/STRATEGY_CAPABILITY_MATRIX.md",
        "adds notional exposure to order quantity",
        "check_position_limit holds units, then value (KD-001, v3.10)",
    ),
    (
        "examples/_strategy_evidence.py",
        "position check adds",
        "check_position_limit holds units, then value (KD-001, v3.10)",
    ),
    (
        "docs/VISION.md",
        "An HMAC-signed venue transport",
        "the venue transport left alphalab.broker in v3.11 (BRK-007)",
    ),
    (
        "CONTRIBUTING.md",
        "- Additional broker adapters",
        "a vendor adapter belongs to the application (ADR-0046, BDY-023)",
    ),
    (
        "CONTRIBUTING.md",
        "- New market data providers",
        "a vendor adapter belongs to the application (ADR-0046, BDY-023)",
    ),
]


@pytest.mark.parametrize(("document", "statement", "evidence"), CORRECTED)
def test_a_corrected_statement_does_not_return(
    document: str, statement: str, evidence: str
) -> None:
    text = (ROOT / document).read_text(encoding="utf-8")

    assert statement not in text, f"{document} says {statement!r}, but {evidence}"


def test_the_status_section_describes_the_current_minor_release() -> None:
    """ARCHITECTURE's authoritative section omitted v4.2.0 (CON-A15); it names each minor now."""

    major, minor, _ = alphalab.__version__.split(".")
    text = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    section = text.split("# Implementation Status", 1)[1].split("\n# ", 1)[0]

    assert f"\n**v{major}.{minor}**" in section


def test_the_security_policy_states_the_transports_rules() -> None:
    """SECURITY.md named TLS and left the schemes unsaid (CON-X16); the rule is stated."""

    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "`file://`" in text and "SEC-002" in text and "redirect" in text


def test_the_one_place_a_name_becomes_code_is_named_where_the_rule_is_stated() -> None:
    """Loading code is the host's, except ``cloud_research.resolve_task`` (CON-A5, BDY-028)."""

    for document in ("docs/ARCHITECTURE.md", "nowandfuture.md"):
        text = (ROOT / document).read_text(encoding="utf-8")
        assert "resolve_task" in text and "BDY-028" in text, document
