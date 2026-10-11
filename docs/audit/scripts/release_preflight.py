"""The checks a release must pass before it is published, runnable anywhere (v4.0).

Usage, from the repository root::

    python docs/audit/scripts/release_preflight.py version (--expected 4.0.0 | --tag v4.0.0)
    python docs/audit/scripts/release_preflight.py distributions DIST --version 4.0.0 \\
        [--record PATH] [--python PYTHON]
    python docs/audit/scripts/release_preflight.py codeql SARIF_DIR
    python docs/audit/scripts/release_preflight.py package-version
    python docs/audit/scripts/release_preflight.py approval --sha SHA --version V --repo OWNER/REPO

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

``approval`` (v4.1)
    Run by ``release.yml`` on a published tag: a successful Release Preflight run
    must have validated the tagged commit's source -- that commit, or one with an
    identical tree -- as the tag's version, which the run's kept artifact
    ``alphalab-<version>-<commit>`` records. A tag placed on any other commit is
    refused, naming what was considered.

Paths (v4.1)
    Every path given -- ``DIST``, ``--record``, ``--python`` -- may be relative to
    the directory the command runs in, and each is resolved to an absolute path,
    and checked to exist, **before** anything changes directory. Until v4.1 the
    artifact path reached ``pip`` as given -- ``dist/alphalab-4.0.0.tar.gz`` --
    after the install had moved into its isolated ``outside`` directory, where
    no such file exists, so validating the published v4.0.0 release failed with
    ``No such file or directory`` although the artifact was there. A path that
    names nothing is now refused by name, and an artifact is installed only if
    it is one of this release's two, inside ``DIST``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from email.parser import HeaderParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

#: What runs inside each fresh environment, from outside the source tree.
SMOKE = ROOT / "tests" / "installed_smoke.py"

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


class PathRefused(ValueError):
    """A path given to the preflight does not name what it must; nothing was run."""


def existing_directory(path: Path, what: str) -> Path:
    """``path``, absolute and with every link resolved, if it is an existing directory.

    Resolved against the directory the command runs in *now*, before anything
    changes directory -- which is the whole of the v4.0.0 defect this guards.

    Raises:
        PathRefused: If nothing is there, or something other than a directory is.
    """

    try:
        found = path.resolve(strict=True)
    except OSError:
        raise PathRefused(
            f"{what} {str(path)!r} does not exist (looked for {Path.cwd() / path})"
        ) from None
    if not found.is_dir():
        raise PathRefused(f"{what} {str(path)!r} is not a directory ({found})")
    return found


def writable_file(path: Path, what: str) -> Path:
    """``path`` made absolute, if its directory exists; the file itself may not yet.

    Raises:
        PathRefused: If the directory it would be written into does not exist.
    """

    absolute = Path.cwd() / path if not path.is_absolute() else path
    parent = existing_directory(absolute.parent, f"the directory of {what}")
    return parent / absolute.name


def interpreter(python: str) -> str:
    """The absolute path of the interpreter ``python`` names: a path, or a command on ``PATH``.

    Raises:
        PathRefused: If it names no file, or no command on ``PATH``.
    """

    if os.sep in python or (os.altsep is not None and os.altsep in python):
        candidate = Path(python)
        try:
            found = candidate.resolve(strict=True)
        except OSError:
            raise PathRefused(
                f"the interpreter {python!r} does not exist (looked for {Path.cwd() / candidate})"
            ) from None
        if not found.is_file():
            raise PathRefused(f"the interpreter {python!r} is not a file ({found})")
        return str(found)
    located = shutil.which(python)
    if located is None:
        raise PathRefused(f"the interpreter {python!r} is not a command on PATH")
    return str(Path(located).absolute())


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


#: Variables that decide where an interpreter finds its modules. A fresh
#: environment's interpreter must import what was installed into it and nothing
#: else, so none of these reaches it (v4.1): with ``PYTHONPATH`` naming a checkout
#: -- as any harness that runs the suite against a copy sets it -- the "installed"
#: package checked was the checkout's, which passes whenever its version matches.
_IMPORT_VARIABLES = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSAFEPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONPLATLIBDIR",
        "PYTHONEXECUTABLE",
        "__PYVENV_LAUNCHER__",
    }
)


def isolated_environment() -> dict[str, str]:
    """This process's environment, without what would change where an interpreter imports from."""

    return {name: value for name, value in os.environ.items() if name not in _IMPORT_VARIABLES}


def _run(command: Sequence[str], cwd: Path) -> tuple[int, str]:
    done = subprocess.run(
        command,
        cwd=cwd,
        env=isolated_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, (done.stdout + done.stderr).strip()


def install_and_smoke(
    artifact: Path, version: str, python: str, smoke: Path | None = None
) -> tuple[Problems, list[str]]:
    """Install ``artifact`` alone into a fresh environment and run the installed smoke test.

    ``artifact`` may be relative to the directory this runs in: it is resolved
    here, before the install moves into its isolated directory, and installed
    only if it exists and is one of this release's two artifacts -- so ``pip``
    is never handed a path that names nothing from where it runs, and never one
    that names something else. ``smoke`` is the script run inside the
    environment (:data:`SMOKE`, the installed smoke test, unless a test passes
    its own).
    """

    problems: Problems = []
    log: list[str] = []
    try:
        artifact = artifact.resolve(strict=True)
    except OSError:
        return [
            f"{artifact}: no such artifact (looked for {Path.cwd() / artifact}); nothing was "
            "installed"
        ], log
    if not artifact.is_file() or artifact.name not in artifact_names(version):
        return [
            f"{artifact}: not one of alphalab {version}'s artifacts "
            f"{list(artifact_names(version))}; nothing was installed"
        ], log
    script = (SMOKE if smoke is None else smoke).resolve(strict=True)
    python = interpreter(python)
    with tempfile.TemporaryDirectory(prefix="alphalab-preflight-") as scratch:
        work = Path(scratch)
        environment, outside = work / "env", work / "outside"
        outside.mkdir()
        code, output = _run([python, "-m", "venv", str(environment)], work)
        if code != 0:
            return [f"{artifact.name}: could not create a virtual environment: {output}"], log
        environment_python = str(
            environment / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        )
        steps = (
            (
                "install",
                [environment_python, "-m", "pip", "install", "--no-cache-dir", str(artifact)],
            ),
            ("installed", [environment_python, "-m", "pip", "list", "--format=freeze"]),
            (
                "smoke",
                [environment_python, "-W", "error", str(script), version],
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


def distributions(
    dist: Path, version: str, record: Path | None, python: str, smoke: Path | None = None
) -> Problems:
    """Check, hash, install and smoke-test this release's two artifacts in ``dist``.

    Every path is resolved and checked here, first, so a relative ``dist`` --
    ``release.yml`` passes ``dist`` -- means the same thing to every step after.

    Raises:
        PathRefused: If ``dist`` is not an existing directory, ``record``'s
            directory does not exist, or ``python`` names no interpreter.
    """

    if _VERSION.fullmatch(version) is None:
        # It becomes part of every artifact's file name.
        return [f"the version {version!r} is not MAJOR.MINOR.PATCH"]
    dist = existing_directory(dist, "the distribution directory")
    record = None if record is None else writable_file(record, "the verification record")
    python = interpreter(python)
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
        found, log = install_and_smoke(dist / name, version, python, smoke)
        problems += found
        lines += log
    if record is not None:
        _record(record, version, dist, lines, problems)
    for line in lines:
        print(line)
    return problems


_SHA = re.compile(r"[0-9a-f]{40}")

#: The workflow whose passing run approves a commit for release.
PREFLIGHT_WORKFLOW = ".github/workflows/preflight.yml"


def preflight_artifact(version: str, sha: str) -> str:
    """The name of the artifact a passing preflight keeps for ``version`` at ``sha``.

    ``preflight.yml`` names its upload so; it is the run's own record of which
    version it validated at which commit, and what :func:`approval_problems`
    reads.
    """

    return f"alphalab-{version}-{sha}"


def approval_problems(
    sha: str,
    version: str,
    runs: Sequence[Mapping[str, Any]],
    tree_of: Callable[[str], str | None],
    artifacts_of: Callable[[int], Sequence[str]],
) -> tuple[Problems, list[str]]:
    """Whether a passing release preflight validated this source as this version (v4.1).

    ``sha`` is the published tag's commit. It is approved by a successful run
    of :data:`PREFLIGHT_WORKFLOW` whose commit is ``sha`` -- or whose commit has
    the same tree, so a merge that changed no file is the source the preflight
    validated -- and that kept :func:`preflight_artifact` for ``version`` at its
    commit. A tag on any other commit, or a preflight that validated another
    version or failed, approves nothing.

    Returns:
        The problems (empty when approved) and what was considered.
    """

    if _SHA.fullmatch(sha) is None:
        return [f"the tagged commit {sha!r} is not a commit"], []
    tree = tree_of(sha)
    if tree is None:
        return [f"the tagged commit {sha} is not in this repository"], []
    considered: list[str] = []
    for run in runs:
        head = str(run.get("head_sha", ""))
        where = f"preflight run {run.get('id')} at {head[:12]}"
        if run.get("conclusion") != "success" or not str(run.get("path", "")).endswith(
            PREFLIGHT_WORKFLOW
        ):
            continue
        if head != sha and (_SHA.fullmatch(head) is None or tree_of(head) != tree):
            continue
        names = artifacts_of(int(run["id"]))
        how = "the tagged commit" if head == sha else f"a commit with the same tree {tree[:12]}"
        if preflight_artifact(version, head) in names:
            return [], [
                *considered,
                f"approved: {where}, {how}, kept {preflight_artifact(version, head)}",
            ]
        considered.append(f"{where} ({how}) validated {sorted(names)}, not {version}")
    return [
        f"no successful Release Preflight validated alphalab {version} at {sha} or at a commit "
        f"with its tree {tree}; run the preflight on the commit to be tagged, then tag it"
    ], considered


def _gh_json_lines(arguments: Sequence[str]) -> list[Any]:
    done = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=True)
    return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def _git_tree(commit: str) -> str | None:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def approval(sha: str, version: str, repo: str) -> Problems:
    """:func:`approval_problems` against the repository's own runs, through ``gh`` and ``git``."""

    runs = _gh_json_lines(
        [
            "api",
            "--paginate",
            f"repos/{repo}/actions/workflows/preflight.yml/runs?status=success&per_page=100",
            "--jq",
            ".workflow_runs[] | {id, head_sha, conclusion, path}",
        ]
    )

    def artifacts_of(run_id: int) -> list[str]:
        return [
            str(name)
            for name in _gh_json_lines(
                [
                    "api",
                    "--paginate",
                    f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
                    "--jq",
                    ".artifacts[].name | tojson",
                ]
            )
        ]

    problems, considered = approval_problems(sha, version, runs, _git_tree, artifacts_of)
    print(f"successful preflight runs: {len(runs)}")
    for line in considered:
        print(f"  {line}")
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
    commands.add_parser(
        "package-version", help="print the version alphalab/common/_version.py declares"
    )
    approve = commands.add_parser(
        "approval", help="a passing release preflight validated the tagged commit's source"
    )
    approve.add_argument("--sha", required=True, help="the published tag's commit")
    approve.add_argument("--version", required=True, help="the version the tag names")
    approve.add_argument("--repo", required=True, help="OWNER/REPO, whose runs are read")
    codeql = commands.add_parser("codeql", help="gate on a CodeQL analysis's SARIF")
    codeql.add_argument("sarif_dir", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "package-version":
        print(package_version())
        return 0
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
    elif arguments.command == "approval":
        problems = approval(arguments.sha, arguments.version, arguments.repo)
        if not problems:
            print(f"release approval: {arguments.version} at {arguments.sha}")
    elif arguments.command == "distributions":
        try:
            problems = distributions(
                arguments.dist, arguments.version, arguments.record, arguments.python
            )
        except PathRefused as refused:
            problems = [str(refused)]
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
