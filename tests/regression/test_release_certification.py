"""The committed release certificate is this build's, and its page renders it (ledger FEA-006).

``docs/audit/scripts/certify_release.py`` runs every certified check -- seeded
runs across processes and decimal contexts, backtest/replay/paper/live parity,
a rerun from a manifest, a stop-and-continue, every golden payload, published
numerical references and the public API -- and ``--check`` compares the
result with ``docs/audit/release_certification.json``. CI runs it as a step of
its own; this test runs it here too, so a change in what the engine computes is
caught by ``make check`` before it reaches CI.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import alphalab

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "docs" / "audit" / "scripts" / "certify_release.py"


@functools.cache
def _certifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("certify_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass resolves its module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_build_matches_its_committed_certificate() -> None:
    assert _certifier().main(["--check"]) == 0


def test_the_certificate_is_this_releases_and_it_passed() -> None:
    certificate = json.loads((ROOT / "docs" / "audit" / "release_certification.json").read_text())

    assert certificate["release"] == alphalab.__version__
    assert certificate["passed"] is True
    assert all(check["passed"] for check in certificate["checks"])


def test_the_page_is_the_rendering_of_the_certificate() -> None:
    certifier = _certifier()
    certificate = json.loads(certifier.REPORT_JSON.read_text(encoding="utf-8"))

    assert certifier.REPORT_MD.read_text(encoding="utf-8") == certifier.render(certificate)
