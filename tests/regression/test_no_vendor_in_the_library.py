"""No code in the library names a vendor (ledger BDY-023, enforced since v3.10).

The boundary has been stated since v2.x -- a provider, a broker and an
AI service are the host application's -- and until v3.10 the library broke it:
five vendor-named market-data clients (four of them ``NotImplementedError``
stubs) and exchange symbol spellings for Binance, Coinbase and Kraken. Those were
removed (BND-001, BND-004). This test reads every module's code -- string
literals and identifiers, not docstrings, which may name a vendor as an example
of what a host brings -- and fails on any vendor, broker, data provider or AI
service by name. Against the v3.9.0 tree it reports sixty-five.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "alphalab"

#: Named products and companies whose integration is an application's.
VENDORS = frozenset(
    {
        # Exchanges and brokers
        "binance",
        "coinbase",
        "kraken",
        "bybit",
        "okx",
        "alpaca",
        "ibkr",
        "zerodha",
        "oanda",
        "schwab",
        "fidelity",
        "robinhood",
        "tradier",
        # Market-data providers
        "polygon",
        "databento",
        "yahoo",
        "yfinance",
        "bloomberg",
        "refinitiv",
        "quandl",
        "tiingo",
        "alphavantage",
        # AI services and applications that sit above AlphaLab
        "openbb",
        "openai",
        "anthropic",
        "langchain",
        "reddesk",
        "iluvtrade",
    }
)

#: Vendors whose name is two words, matched in string literals.
PHRASES = ("interactive brokers",)


def _docstrings(tree: ast.Module) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _words(text: str) -> set[str]:
    """``binanceAdapter`` and ``BINANCE_KEY`` both contain the word ``binance``."""

    return {word.lower() for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", text)}


def _named_vendors(text: str) -> set[str]:
    lowered = text.lower()
    return set(VENDORS & _words(text)) | {phrase for phrase in PHRASES if phrase in lowered}


def test_no_module_names_a_vendor_in_its_code() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                text = node.value
            elif isinstance(node, ast.Name):
                text = node.id
            elif isinstance(node, ast.Attribute):
                text = node.attr
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.alias
            ):
                text = node.name
            else:
                continue
            named = _named_vendors(text)
            if named:
                location = f"{path.relative_to(PACKAGE.parent)}:{getattr(node, 'lineno', '?')}"
                offenders.append(f"{location} names {sorted(named)}: {text[:60]!r}")

    assert not offenders, "the library names a vendor:\n  " + "\n  ".join(offenders)


def test_the_word_splitter_sees_a_vendor_inside_an_identifier() -> None:
    assert _named_vendors("binanceAdapter") == {"binance"}
    assert _named_vendors("POLYGON_API_KEY") == {"polygon"}
    assert _named_vendors("route to Interactive Brokers") == {"interactive brokers"}
    assert _named_vendors("polygonal") == set()
