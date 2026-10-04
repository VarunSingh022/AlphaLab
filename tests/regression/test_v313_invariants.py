"""What v3.13 must not have broken while it added things (the standing constraints).

AlphaLab does not depend on a network, a vendor, a language model, an assistant,
OAuth, credentials or the application that hosts it; it reads no clock and draws
no unseeded randomness on the canonical path. Each release since v3.7 pins those
properties for the modules it added, and this file does so for v3.13's: the
binomial lattice, cron schedules, the lock-file reader, the rerun harness, the
optimal split, urgency estimation and tranche randomization, the liquidation
price, per-order checkpoints and the shared clock protocol.

It also pins a property of the whole research path that v3.13 found was not
held: importing it loaded the market-data transports -- ``socket``, ``ssl``,
``http.client`` -- because one module imported a wire record through the
transports' package rather than from where it is defined. Nothing connected,
but research that is not to need a network should not load the code that
reaches one.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

ROOT = Path(__file__).resolve().parents[2]

#: Every package module v3.13 added or substantially rewrote.
V313_MODULES = (
    "alphalab/options/binomial.py",
    "alphalab/options/implied.py",
    "alphalab/options/volatility_surface.py",
    "alphalab/scheduler/cron.py",
    "alphalab/lifecycle/lockfile.py",
    "alphalab/lifecycle/rerun.py",
    "alphalab/execution/routing.py",
    "alphalab/execution/algorithms.py",
    "alphalab/crypto/perpetual.py",
    "alphalab/runtime/checkpoint.py",
    "alphalab/strategy/validation.py",
    "alphalab/common/time.py",
)

_NETWORK = frozenset(
    {"aiohttp", "ftplib", "http", "httpx", "requests", "smtplib", "socket", "ssl", "urllib"}
)
#: Calls that read the wall clock or draw unseeded randomness.
_AMBIENT = ("time.time(", "time.monotonic(", "datetime.now(", "datetime.utcnow(", "random.")


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", V313_MODULES)
def test_no_v313_module_names_a_vendor_or_reaches_a_network(module: str) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    lowered = source.lower()
    for term in FORBIDDEN_VENDORS:
        assert term not in lowered, f"{term} in {module}"
    assert "http://" not in lowered and "https://" not in lowered, module
    assert not _imported_roots(source) & _NETWORK, module


#: ``common.time`` is in V313_MODULES for its new ``ClockProtocol``; its
#: ``utc_now`` is the one wall-clock helper, asked for by name and called by
#: nothing in the package.
_CLOCK_EXEMPT = frozenset({"alphalab/common/time.py"})


@pytest.mark.parametrize("module", sorted(set(V313_MODULES) - _CLOCK_EXEMPT))
def test_no_v313_module_reads_a_clock_or_draws_unseeded_randomness(module: str) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    found = [call for call in _AMBIENT if call in source]
    assert not found, f"{module} calls {found}"
    assert "random" not in _imported_roots(source), module


def test_the_research_path_loads_no_network_code() -> None:
    """Imported in a fresh interpreter: no transport, no socket, no TLS, no HTTP client."""

    program = (
        "import sys\n"
        "import alphalab.api, alphalab.backtesting, alphalab.research, alphalab.lifecycle\n"
        "import alphalab.options, alphalab.execution, alphalab.runtime, alphalab.portfolio\n"
        "import alphalab.factor_library, alphalab.scheduler, alphalab.crypto\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'socket', 'ssl', 'http', '_socket', '_ssl'} or m in "
        "{'urllib.request', 'alphalab.common.tls'} or m.startswith('alphalab.marketdata'))\n"
        "print(','.join(loaded))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(ROOT),
    )
    assert completed.stdout.strip() == "", f"loaded: {completed.stdout.strip()}"


def test_the_transports_are_still_there_for_whoever_asks() -> None:
    """The fix moved an import; it removed nothing a live session uses."""

    from alphalab.data.feed import Bar as Defined
    from alphalab.market.provider import WireBar  # type: ignore[attr-defined]
    from alphalab.marketdata import feed

    assert WireBar is Defined is feed.Bar


def test_the_one_wall_clock_helper_is_called_by_nothing_in_the_package() -> None:
    callers = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "alphalab").rglob("*.py"))
        if "utc_now(" in path.read_text(encoding="utf-8") and path.name != "time.py"
    ]
    assert callers == []
