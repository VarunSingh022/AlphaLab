"""The checks a release must pass before it is published, runnable anywhere (v4.0).

Usage, from the repository root::

    python docs/audit/scripts/release_preflight.py version (--expected 4.0.0 | --tag v4.0.0)
    python docs/audit/scripts/release_preflight.py distributions DIST --version 4.0.0 \\
        [--record PATH] [--python PYTHON]
    python docs/audit/scripts/release_preflight.py codeql SARIF_DIR

``.github/workflows/preflight.yml`` runs all three against a candidate commit
before a release is published, and ``release.yml`` runs the first two again on
the published tag. The same script, run locally, produces the record kept
beside a local build -- so a hosted run and a local one check the same things.

``version``
    The package's version (``alphalab/common/_version.py``, the one source,
    ledger REP-001) is the one expected, the CHANGELOG leads with it, the
    release certificate and the public-API manifest are this release's, and the
    manifest's history holds this release's copy. The expected version is
    required, so references that merely agree with one another -- all of them
    4.0.1, say -- do not pass for 4.0.0. It is given as ``--expected
    MAJOR.MINOR.PATCH``, or as the release tag, ``--tag vMAJOR.MINOR.PATCH``:
    every AlphaLab release is tagged ``v`` and the version, and a tag of any
    other shape (``4.0.0``, ``V4.0.0``, ``v4.0.0-rc1``) is refused rather than
    read.

``distributions``
    ``DIST`` holds exactly ``alphalab-<version>.tar.gz`` and
    ``alphalab-<version>-py3-none-any.whl`` and no other AlphaLab artifact; each
    archive's metadata names that version and no runtime requirement, carries
    its licence and ``py.typed``, and holds no bytecode or cache. Their SHA-256
    sums are written to ``DIST/SHA256SUMS-<version>``. Then each artifact is
    installed alone into a fresh virtual environment and checked from a
    directory outside the source tree by ``tests/installed_smoke.py`` -- which
    runs ``pip check``, holds the import location, the version, every module and
    every public name to the source, and runs examples 11 and 70 with the
    network refused. ``--record`` writes what was checked, and its outcome.

``codeql``
    Reads the SARIF a CodeQL analysis wrote and fails on any result whose level
    is ``error`` or whose security severity is high or critical (7.0 or more),
    printing every result either way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import runpy
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
from email.parser import HeaderParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

#: A failure a check names; the command collects them and exits 1 if any.
Problems = list[str]


def package_version(root: Path = ROOT) -> str:
    """The version ``alphalab/common/_version.py`` declares, read without importing the package."""

    version = runpy.run_path(str(root / "alphalab" / "common" / "_version.py"))["__version__"]
    assert isinstance(version, str)
    return version


_VERSION = re.compile(r"\d+\.\d+\.\d+")
_TAG = re.compile(r"v(\d+\.\d+\.\d+)")


def tag_version(tag: str) -> str:
    """The version a release tag names. The tag is ``v`` and ``MAJOR.MINOR.PATCH``, or refused.

    Raises:
        ValueError: If ``tag`` has any other shape.
    """

    match = _TAG.fullmatch(tag)
    if match is None:
        raise ValueError(
            f"the release tag {tag!r} is not v<MAJOR>.<MINOR>.<PATCH>; every AlphaLab release "
            "is tagged so (v3.13.0, v4.0.0)"
        )
    return match.group(1)


def version_problems(expected: str | None, root: Path = ROOT) -> Problems:
    """Every place this release's identity disagrees with the package's version.

    ``expected`` is a bare ``MAJOR.MINOR.PATCH``; a tag goes through
    :func:`tag_version` first. ``None`` checks only that the references agree,
    which the command line does not offer: a release is checked against the
    version it is meant to be.
    """

    version = package_version(root)
    problems: Problems = []
    if expected is not None:
        if _VERSION.fullmatch(expected) is None:
            problems.append(
                f"the expected version {expected!r} is not MAJOR.MINOR.PATCH "
                "(a release tag such as v4.0.0 is given with --tag)"
            )
        elif expected != version:
            problems.append(f"the package is {version}, but {expected} was expected")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^# \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    if first is None or first.group(1) != version:
        problems.append(
            f"CHANGELOG.md leads with {first.group(1) if first else 'no release'}, not {version}"
        )
    for name, path in (
        ("the release certificate", root / "docs" / "audit" / "release_certification.json"),
        ("the public-API manifest", root / "docs" / "api" / "public_api.json"),
    ):
        recorded = json.loads(path.read_text(encoding="utf-8")).get("release")
        if recorded != version:
            problems.append(f"{name} ({path.relative_to(root)}) is {recorded}'s, not {version}'s")
    if not (root / "docs" / "api" / "history" / f"{version}.json").is_file():
        problems.append(f"docs/api/history/{version}.json is missing")
    return problems


def artifact_names(version: str) -> tuple[str, str]:
    """The sdist's and the wheel's file names for ``version``."""

    return f"alphalab-{version}.tar.gz", f"alphalab-{version}-py3-none-any.whl"


def name_problems(dist: Path, version: str) -> Problems:
    """``dist`` holds this release's two artifacts and no other AlphaLab artifact."""

    expected = set(artifact_names(version))
    present = {path.name for path in dist.iterdir() if path.name.startswith("alphalab-")}
    problems: Problems = []
    if missing := sorted(expected - present):
        problems.append(f"{dist} lacks {missing}")
    if extra := sorted(present - expected):
        problems.append(f"{dist} holds other AlphaLab artifacts {extra}; build into an empty one")
    return problems


def _metadata_problems(text: str, version: str, where: str) -> Problems:
    headers = HeaderParser().parsestr(text)
    problems: Problems = []
    if headers["Name"] != "alphalab":
        problems.append(f"{where}: Name is {headers['Name']!r}")
    if headers["Version"] != version:
        problems.append(f"{where}: Version is {headers['Version']!r}, not {version!r}")
    if not headers["Requires-Python"]:
        problems.append(f"{where}: no Requires-Python")
    runtime = [entry for entry in headers.get_all("Requires-Dist") or [] if "extra ==" not in entry]
    if runtime:
        problems.append(f"{where}: runtime requirements {runtime}; AlphaLab declares none")
    return problems


def _cached(names: Iterable[str], where: str) -> Problems:
    return [
        f"{where} carries {name}"
        for name in names
        if "__pycache__" in name or name.endswith((".pyc", ".pyo")) or "/.mypy_cache/" in name
    ]


def archive_problems(dist: Path, version: str) -> Problems:
    """What each archive carries: its metadata, its licence, ``py.typed``, and no cache."""

    sdist_name, wheel_name = artifact_names(version)
    problems: Problems = []
    with zipfile.ZipFile(dist / wheel_name) as wheel:
        names = wheel.namelist()
        info = f"alphalab-{version}.dist-info"
        problems += _metadata_problems(wheel.read(f"{info}/METADATA").decode(), version, wheel_name)
        if not any(name.startswith(f"{info}/licenses/") for name in names):
            problems.append(f"{wheel_name} carries no licence file")
        if "alphalab/py.typed" not in names:
            problems.append(f"{wheel_name} carries no py.typed")
        problems += _cached(names, wheel_name)
    with tarfile.open(dist / sdist_name) as sdist:
        names = sdist.getnames()
        top = f"alphalab-{version}"
        if outside := [name for name in names if name.split("/", 1)[0] != top]:
            problems.append(f"{sdist_name} holds entries outside {top}/: {outside[:3]}")
        member = sdist.extractfile(f"{top}/PKG-INFO")
        assert member is not None
        problems += _metadata_problems(member.read().decode(), version, sdist_name)
        for required in ("LICENSE", "pyproject.toml", "alphalab/py.typed"):
            if f"{top}/{required}" not in names:
                problems.append(f"{sdist_name} lacks {required}")
        problems += _cached(names, sdist_name)
    return problems


def write_checksums(dist: Path, version: str) -> Path:
    """``SHA256SUMS-<version>`` beside the artifacts, in ``sha256sum``'s format."""

    lines = []
    for name in sorted(artifact_names(version)):
        digest = hashlib.sha256((dist / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    path = dist / f"SHA256SUMS-{version}"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _run(command: Sequence[str], cwd: Path) -> tuple[int, str]:
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return done.returncode, (done.stdout + done.stderr).strip()


def install_and_smoke(artifact: Path, version: str, python: str) -> tuple[Problems, list[str]]:
    """Install ``artifact`` alone into a fresh environment and run the installed smoke test."""

    problems: Problems = []
    log: list[str] = []
    with tempfile.TemporaryDirectory(prefix="alphalab-preflight-") as scratch:
        work = Path(scratch)
        environment, outside = work / "env", work / "outside"
        outside.mkdir()
        code, output = _run([python, "-m", "venv", str(environment)], work)
        if code != 0:
            return [f"{artifact.name}: could not create a virtual environment: {output}"], log
        interpreter = str(
            environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        )
        steps = (
            ("install", [interpreter, "-m", "pip", "install", "--no-cache-dir", str(artifact)]),
            ("installed", [interpreter, "-m", "pip", "list", "--format=freeze"]),
            (
                "smoke",
                [
                    interpreter,
                    "-W",
                    "error",
                    str(ROOT / "tests" / "installed_smoke.py"),
                    version,
                ],
            ),
        )
        for label, command in steps:
            code, output = _run(command, outside)
            # pip closes with a notice about its own upgrades; quote what it did.
            said = [
                line
                for line in output.splitlines()
                if line.strip() and not line.startswith("[notice]")
            ]
            last = said[-1] if said else ""
            if label == "installed":
                last = ", ".join(line for line in output.splitlines() if "==" in line)
            log.append(f"{artifact.name} {label}: exit {code}; {last}")
            if code != 0:
                problems.append(f"{artifact.name}: {label} failed (exit {code}):\n{output[-2000:]}")
                break
    return problems, log


def _record(path: Path, version: str, dist: Path, lines: Sequence[str], problems: Problems) -> None:
    sums = (dist / f"SHA256SUMS-{version}").read_text(encoding="utf-8").rstrip()
    verdict = "PASSED" if not problems else "FAILED"
    text = "\n".join(
        [
            f"AlphaLab {version} -- distribution verification: {verdict}",
            "",
            f"Checked on {platform.platform()}, {platform.python_implementation()} "
            f"{platform.python_version()}, by docs/audit/scripts/release_preflight.py.",
            "",
            "SHA-256",
            sums,
            "",
            "Checks",
            *[f"- {line}" for line in lines],
            *([""] + ["Problems"] + [f"- {problem}" for problem in problems] if problems else []),
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")


def distributions(dist: Path, version: str, record: Path | None, python: str) -> Problems:
    problems = name_problems(dist, version)
    if problems:
        return problems
    problems += archive_problems(dist, version)
    checksums = write_checksums(dist, version)
    lines = [
        f"artifacts: {', '.join(artifact_names(version))} and nothing else",
        "archives: metadata names the version and no runtime requirement; licence, py.typed;"
        " no bytecode or cache" + (" -- see Problems" if problems else ""),
        f"checksums written: {checksums.name}",
    ]
    for name in artifact_names(version):
        found, log = install_and_smoke(dist / name, version, python)
        problems += found
        lines += log
    if record is not None:
        _record(record, version, dist, lines, problems)
    for line in lines:
        print(line)
    return problems


def _level(result: dict[str, Any], rules: dict[str, dict[str, Any]]) -> tuple[str, float]:
    rule = rules.get(result.get("ruleId", ""), {})
    level = result.get("level") or rule.get("defaultConfiguration", {}).get("level", "warning")
    severity = rule.get("properties", {}).get("security-severity")
    return str(level), float(severity) if severity is not None else 0.0


def codeql_problems(sarif_dir: Path) -> tuple[Problems, list[str]]:
    """Error-level and high-severity results in every SARIF file under ``sarif_dir``."""

    files = sorted(sarif_dir.rglob("*.sarif"))
    if not files:
        return [f"no SARIF file under {sarif_dir}: the analysis did not run"], []
    problems: Problems = []
    seen: list[str] = []
    for path in files:
        for run in json.loads(path.read_text(encoding="utf-8")).get("runs", []):
            driver = run.get("tool", {}).get("driver", {})
            rules = {rule["id"]: rule for rule in driver.get("rules", []) if "id" in rule}
            for extension in run.get("tool", {}).get("extensions", []):
                rules.update(
                    {rule["id"]: rule for rule in extension.get("rules", []) if "id" in rule}
                )
            for result in run.get("results", []):
                level, severity = _level(result, rules)
                location = (
                    (result.get("locations") or [{}])[0]
                    .get("physicalLocation", {})
                    .get("artifactLocation", {})
                    .get("uri", "?")
                )
                line = f"{level} (severity {severity}) {result.get('ruleId')} at {location}"
                seen.append(line)
                if level == "error" or severity >= 7.0:
                    problems.append(line)
    return problems, seen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version", help="the package is the release expected, everywhere")
    expectation = version.add_mutually_exclusive_group(required=True)
    expectation.add_argument("--expected", help="the version the release must be, e.g. 4.0.0")
    expectation.add_argument("--tag", help="the release's tag, e.g. v4.0.0")
    dist = commands.add_parser("distributions", help="check, hash, install and smoke-test")
    dist.add_argument("dist", type=Path)
    dist.add_argument("--version", required=True)
    dist.add_argument("--record", type=Path)
    dist.add_argument("--python", default=sys.executable, help="interpreter to create venvs with")
    codeql = commands.add_parser("codeql", help="gate on a CodeQL analysis's SARIF")
    codeql.add_argument("sarif_dir", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "version":
        if arguments.tag is not None:
            try:
                expected = tag_version(arguments.tag)
            except ValueError as refused:
                expected, problems = None, [str(refused)]
            else:
                problems = version_problems(expected)
        else:
            expected = arguments.expected
            problems = version_problems(expected)
        if not problems:
            named = f" (tag {arguments.tag})" if arguments.tag else ""
            print(f"release identity: {expected}{named}, the package's version, everywhere")
    elif arguments.command == "distributions":
        problems = distributions(
            arguments.dist, arguments.version, arguments.record, arguments.python
        )
    else:
        problems, seen = codeql_problems(arguments.sarif_dir)
        print(f"CodeQL results: {len(seen)}")
        for line in seen:
            print(f"  {line}")
    for problem in problems:
        print(f"FAILED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
