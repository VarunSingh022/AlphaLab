"""The committed release certificate is this build's, and its page renders it (ledger FEA-006).

``docs/audit/scripts/certify_release.py`` runs every certified check -- seeded
runs across processes and decimal contexts, backtest/replay/paper/live parity,
a rerun from a manifest, a stop-and-continue, every golden payload, published
numerical references and the public API -- and ``--check`` compares the
result with ``docs/audit/release_certification.json``. CI runs it as a step of
its own; this test runs it here too, so a change in what the engine computes is
caught by ``make check`` before it reaches CI.

The certificate also names the engine source it certified, and until v3.13.0's
release candidate nothing held the name to the build: ``--check`` compared what
the build computes, not which build it is, and the committed certificate there
named the source as it stood before two commits that changed only docstrings
(ledger REP-004).
"""

from __future__ import annotations

import copy
import functools
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import alphalab
from alphalab.lifecycle import engine_source_digest

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


def _committed() -> dict[str, Any]:
    certificate: dict[str, Any] = json.loads(_certifier().REPORT_JSON.read_text(encoding="utf-8"))
    return certificate


def test_the_build_matches_its_committed_certificate() -> None:
    """What the build computes is what its committed certificate records.

    Which source the certificate certified is held by the next test, on its own:
    that answer changes with every byte of the engine, so it says nothing of
    behaviour, and the defect-injection harness leaves it out.
    """

    certifier = _certifier()
    assert certifier.moved_evidence(_committed(), certifier.certify()) == []


def test_the_committed_certificate_certifies_this_source() -> None:
    assert _certifier().certified_source(_committed()) == engine_source_digest()


def _other_source(certificate: dict[str, Any]) -> None:
    certificate["build"]["source_digest"] = "0" * 64


def _other_evidence(certificate: dict[str, Any]) -> None:
    certificate["checks"][-1]["evidence"]["added"] = "a value the build does not compute"


def _another_host(certificate: dict[str, Any]) -> None:
    certificate["environment"].update(python="3.99.0", platform="elsewhere")
    certificate["build"]["tz_database"] = "1970a"


def _unchanged(certificate: dict[str, Any]) -> None:
    """The committed certificate as the build computes it."""


@pytest.mark.parametrize(
    ("edit", "refused", "named"),
    [
        (_unchanged, False, "matching the committed one"),
        (_another_host, False, "matching the committed one"),
        (_other_source, True, "it certifies engine source " + "0" * 64 + ", this build's is "),
        (_other_evidence, True, "check API-1"),
    ],
    ids=["unchanged", "another-host", "other-source", "other-evidence"],
)
def test_check_refuses_a_certificate_of_other_source_or_evidence(
    edit: Callable[[dict[str, Any]], None],
    refused: bool,
    named: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` against a certificate edited one way: the host is recorded, not held."""

    certifier = _certifier()
    build = _committed()
    committed = copy.deepcopy(build)
    edit(committed)
    written = tmp_path / "release_certification.json"
    written.write_text(json.dumps(committed), encoding="utf-8")
    monkeypatch.setattr(certifier, "REPORT_JSON", written)
    monkeypatch.setattr(certifier, "certify", lambda: copy.deepcopy(build))

    assert certifier.main(["--check"]) == (1 if refused else 0)
    said = capsys.readouterr().out
    assert named in said
    assert ("engine source" in said) == (edit is _other_source)


def test_a_check_failing_on_another_host_names_the_value_and_both_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hosted runner's ``--check`` is read from its log: it must say what moved, and where.

    The case this anticipates is LIM-005's: a certificate made on one host class
    and checked on another, where a float's last bit differs. The host is still
    not compared -- the same evidence on another host passes (above) -- but when
    evidence moves, the log names each moved value and both hosts (v4.0).
    """

    certifier = _certifier()
    build = _committed()
    committed = copy.deepcopy(build)
    _another_host(committed)
    committed["checks"][0]["evidence"]["result_id"] = "f" * 64
    written = tmp_path / "release_certification.json"
    written.write_text(json.dumps(committed), encoding="utf-8")
    monkeypatch.setattr(certifier, "REPORT_JSON", written)
    monkeypatch.setattr(certifier, "certify", lambda: copy.deepcopy(build))

    assert certifier.main(["--check"]) == 1
    said = capsys.readouterr().out
    moved = build["checks"][0]
    assert f"{moved['id']}.result_id: {'f' * 64!r} -> {moved['evidence']['result_id']!r}" in said
    assert "made on elsewhere (Python 3.99.0, tz database 1970a)" in said
    assert build["environment"]["platform"] in said and "LIM-005" in said


def test_every_checkout_reads_the_engine_source_byte_for_byte() -> None:
    """The source digest is over bytes, so a checkout must not rewrite them (REP-004).

    Git rewrites line endings on checkout when ``core.autocrlf`` is set, as Git
    for Windows sets it by default: the same commit would then be other engine
    source -- to this certificate and to a rerun from a manifest. ``.gitattributes``
    checks every Python file out with LF, and no engine source holds a carriage
    return.
    """

    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.py text eol=lf" in attributes
    sources = sorted((ROOT / "alphalab").rglob("*.py"))
    assert sources and not [path for path in sources if b"\r" in path.read_bytes()]


def test_the_certificate_is_this_releases_and_it_passed() -> None:
    certificate = json.loads((ROOT / "docs" / "audit" / "release_certification.json").read_text())

    assert certificate["release"] == alphalab.__version__
    assert certificate["passed"] is True
    assert all(check["passed"] for check in certificate["checks"])


def test_the_page_is_the_rendering_of_the_certificate() -> None:
    certifier = _certifier()
    certificate = json.loads(certifier.REPORT_JSON.read_text(encoding="utf-8"))

    assert certifier.REPORT_MD.read_text(encoding="utf-8") == certifier.render(certificate)
