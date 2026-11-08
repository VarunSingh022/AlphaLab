"""Release-facing text names the release it ships with; history keeps its versions (v4.0).

``test_version_markers_agree.py`` holds the designated version markers -- the
README's badge and status table, ``docs/README.md``'s Version block, the
architecture and ``nowandfuture.md`` identity lines -- to the package. The v4.0
audit read every version string in the repository and found stale ones those
markers did not reach: ``SECURITY.md``'s supported-versions table still marked
3.x as the supported line, it told readers the library is "installed by ``pip
install alphalab``" (AlphaLab is not on PyPI), and seven benchmarks printed a
banner such as "AlphaLab v3.7 --" on every v4.0 run. This module holds the
release-facing rules those findings came from, deriving the expected version
from ``alphalab/common/_version.py`` (REP-001) and naming the file and line of
each breach.

Deliberately outside these rules, because their versions are history:
``CHANGELOG.md``, ``docs/ADR/``, ``docs/audit/``, ``docs/work/``,
``docs/api/history/``, ``tests/fixtures/`` (payloads earlier releases wrote),
and prose stating when something happened ("as of v3.0.0 the architecture is
frozen"). The examples' ``EngineIdentity("alphalab", "3.6.0")`` and
``code_identity_for(..., "3.13.0", ...)`` are pinned illustrative identities
whose fingerprints the examples print, not claims about the running release.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from alphalab.common._version import __version__ as VERSION

ROOT = Path(__file__).resolve().parents[2]
MAJOR = VERSION.split(".", 1)[0]

#: The documents a reader takes as describing the release they hold.
RELEASE_FACING = (
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "ROADMAP.md",
    "nowandfuture.md",
    "configs/README.md",
    "examples/README.md",
    "docs/README.md",
    "docs/GETTING_STARTED.md",
    "docs/INTEGRATION.md",
    "docs/ENGINEERING_GUIDELINES.md",
    "docs/EXAMPLES.md",
    "docs/ARCHITECTURE.md",
    "docs/STATE_MODEL.md",
    "docs/SYSTEM_DESIGN.md",
    "docs/EVENT_MODEL.md",
    "docs/VISION.md",
    "docs/api/PUBLIC_API.md",
)

#: A reference that installs or downloads one particular release, and the version it names.
_RELEASE_REFERENCES = (
    re.compile(r"releases/(?:tag|download)/v(\d+\.\d+\.\d+)"),
    re.compile(r"github\.com/[\w.-]+/AlphaLab(?:\.git)?@v?(\d+\.\d+\.\d+)"),
    re.compile(r"\balphalab-(\d+\.\d+\.\d+)(?:-py3-none-any\.whl|\.tar\.gz)"),
    re.compile(r"\balphalab==(\d+\.\d+\.\d+)"),
)

#: A line that calls a version the current or latest one. Every version on it is read, not
#: only the first: v4.2.0's README said "Current Release: v4.2.0 (release candidate, not yet
#: published; the published tag is v4.1.0)", and its first version agreed (ledger TST-024).
_CURRENT_CLAIM = re.compile(
    r"(?i)\b(?:current|latest|newest)\s+(?:stable\s+)?(?:release|version)\b"
)

#: Every version a line names.
_VERSION_NAMED = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")


def _other_versions_claimed_current(line: str) -> list[str]:
    """The versions other than this release on a line that calls a version current."""

    if not _CURRENT_CLAIM.search(line):
        return []
    return [found for found in _VERSION_NAMED.findall(line) if found != VERSION]


def _lines(document: str) -> list[tuple[int, str]]:
    text = (ROOT / document).read_text(encoding="utf-8")
    return list(enumerate(text.splitlines(), start=1))


def _breaches(document: str, pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{document}:{number}: {line.strip()[:120]}"
        for number, line in _lines(document)
        for found in pattern.findall(line)
        if found != VERSION
    ]


def test_the_version_is_the_one_the_package_declares() -> None:
    """The guard on the guard: the expected version is read, not written here."""

    import alphalab

    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)
    assert alphalab.__version__ == VERSION


@pytest.mark.parametrize("document", RELEASE_FACING)
def test_every_release_link_and_pin_names_this_release(document: str) -> None:
    """A download URL, a tag reference, an artifact name or a pin names this release."""

    breaches = [line for pattern in _RELEASE_REFERENCES for line in _breaches(document, pattern)]
    assert not breaches, f"release references to another version than {VERSION}:\n" + "\n".join(
        breaches
    )


@pytest.mark.parametrize("document", RELEASE_FACING)
def test_no_document_calls_another_version_current(document: str) -> None:
    breaches = [
        f"{document}:{number}: {line.strip()[:120]}"
        for number, line in _lines(document)
        if _other_versions_claimed_current(line)
    ]
    assert not breaches, f"'current'/'latest' claims for another version than {VERSION}:\n" + (
        "\n".join(breaches)
    )


def test_every_version_on_a_current_release_line_is_read() -> None:
    """v4.2.0's README line, with this release first: the version after it is found (TST-024)."""

    published = (
        f"**Current Release:** **v{VERSION} (release candidate, not yet published; the "
        "published tag is v4.1.0) — every timed input on every driver**"
    )

    assert _other_versions_claimed_current(published) == ["4.1.0"]
    assert _other_versions_claimed_current(f"**Current Release:** **v{VERSION} — additive**") == []
    assert _other_versions_claimed_current("v4.1.0 added replay pause and resume.") == []


@pytest.mark.parametrize("document", ["README.md", "docs/README.md", "docs/INTEGRATION.md"])
def test_a_header_and_a_footer_state_no_other_release(document: str) -> None:
    """A document's header (title, badges) and footer name no release but this one.

    The header is its first fifteen lines and the footer its last eight: the
    places a stale release label sat unnoticed before, and narrow enough that a
    compatibility statement in the body ("every payload v3.13.0 wrote is read")
    is not mistaken for one.
    """

    lines = _lines(document)
    edges = lines[:15] + lines[-8:]
    breaches = [
        f"{document}:{number}: {line.strip()[:120]}"
        for number, line in edges
        for found in re.findall(r"\bv?(\d+\.\d+\.\d+)\b", line)
        if found != VERSION
    ]
    assert not breaches, f"a header or footer names another release than {VERSION}:\n" + (
        "\n".join(breaches)
    )


def test_the_security_policy_supports_this_major_line_alone() -> None:
    """SECURITY.md: "Only the latest stable release receives security updates" (v4.0).

    The table read ``3.x | ✅ Yes`` after 4.0.0 was prepared; it now has to mark
    this release's major line supported and no other.
    """

    rows = dict(
        re.findall(
            r"^\| ([^|]+?) \| (✅ Yes|❌ No) \|$",
            (ROOT / "SECURITY.md").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert rows, "SECURITY.md's supported-versions table moved; this test reads it"
    supported = sorted(version for version, answer in rows.items() if answer == "✅ Yes")
    assert supported == [f"{MAJOR}.x"], (
        f"SECURITY.md supports {supported}; the package is {VERSION}"
    )


def test_the_security_policy_states_that_a_task_path_selects_code() -> None:
    """SECURITY.md and the code it describes state the task-resolution boundary (BDY-028).

    ``cloud_research`` imports the module a job's ``task_path`` names and calls
    what it names with the job's ``kwargs``; nothing restricts which. Only the
    statement protects a host from taking either from untrusted input, so the
    statement is held here, in the policy and in the docstrings installed users
    read.
    """

    from alphalab.cloud_research import cluster, sweep, task

    policy = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "**`task_path` and `kwargs` must never come from untrusted input**" in policy
    for documented in (
        task,
        task.resolve_task,
        task.run_task,
        cluster.submit_research_job,
        sweep.submit_parameter_sweep,
    ):
        text = " ".join((documented.__doc__ or "").split())
        assert "untrusted input" in text and "BDY-028" in text, documented


def test_no_document_says_the_library_installs_from_a_public_index() -> None:
    """AlphaLab is not on PyPI; ``pip install alphalab`` would install whatever owns the name."""

    breaches = [
        f"{document}:{number}: {line.strip()[:120]}"
        for document in RELEASE_FACING
        for number, line in _lines(document)
        if re.search(r"pip install alphalab\b(?![-\w])", line)
    ]
    assert not breaches, "\n".join(breaches)


def test_no_benchmark_or_example_banner_names_another_release() -> None:
    """A printed "AlphaLab vX.Y" banner is a header every run shows; it names this release or none.

    Seven benchmarks printed "AlphaLab v3.5" to "v3.9" on every v4.0 run; each now
    says which release added it ("added in v3.7"), which stays true.
    """

    breaches: list[str] = []
    for folder in ("benchmarks", "examples"):
        for path in sorted((ROOT / folder).glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found = re.match(r"AlphaLab v(\d+\.\d+(?:\.\d+)?)\b", node.value)
                    if found and not VERSION.startswith(found.group(1)):
                        breaches.append(f"{folder}/{path.name}:{node.lineno}: {node.value[:80]}")
    assert not breaches, "\n".join(breaches)
