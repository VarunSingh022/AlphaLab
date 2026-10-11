"""Every shell command the documentation gives can be pasted and run as written (v4.1).

Two commands handed to the maintainer during the v4.0.0 release failed before
they ran anything, in zsh:

* ``git add <reviewed-files>`` -- a placeholder written as an executable
  command. A shell reads ``<reviewed-files>`` as two redirections, and stops.
* A command continued across lines whose continuation was malformed -- a
  backslash followed by a space, or by a blank line -- so the shell ran its
  first line alone and treated the rest as another command.

These tests read every fenced ``bash``, ``sh``, ``shell`` or ``zsh`` block in
every tracked Markdown file, and refuse an angle-bracket placeholder, a broken
continuation, and anything ``bash -n`` cannot parse. A value the reader must
supply is written as a shell variable they set first (``"${VERSION}"``), never
as ``<VERSION>``.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: A fenced shell block, at the margin or indented inside a list item.
_FENCE = re.compile(
    r"^(?P<indent>[ \t]*)```(?:bash|sh|shell|zsh)[ \t]*\n(?P<body>.*?)^(?P=indent)```", re.M | re.S
)
_PLACEHOLDER = re.compile(r"<[A-Za-z][A-Za-z0-9 _./-]*>")


def _documents() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode == 0 and listed.stdout.strip():
        return [ROOT / name for name in listed.stdout.split()]
    # Outside a checkout -- the unpacked sdist -- every Markdown file shipped.
    return sorted(path for path in ROOT.rglob("*.md") if ".venv" not in path.parts)


def _blocks() -> list[tuple[str, str]]:
    found = []
    for path in _documents():
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append((f"{path.relative_to(ROOT)}:{line}", textwrap.dedent(match["body"])))
    return found


def problems(block: str) -> list[str]:
    """Why ``block`` would not run as pasted; empty when it would."""

    found = [f"placeholder {item}" for item in _PLACEHOLDER.findall(block)]
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if re.search(r"\\[ \t]+$", line):
            found.append(f"line {index + 1}: a backslash followed by whitespace continues nothing")
        elif line.endswith("\\"):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if not following.strip() or following.lstrip().startswith("#"):
                found.append(f"line {index + 1}: continued onto a blank line, a comment or nothing")
    parsed = subprocess.run(
        ["bash", "-n"], input=block, capture_output=True, text=True, check=False
    )
    if parsed.returncode != 0:
        found.append(f"bash -n: {parsed.stderr.strip()}")
    return found


def test_the_documentation_has_commands_to_check() -> None:
    assert len(_blocks()) >= 20, "the fence pattern no longer matches the documentation"


def test_an_indented_block_is_read_too() -> None:
    """A guard on the guard: a fence inside a list item is a command a reader pastes."""

    text = "1. Do this:\n\n   ```bash\n   git add <reviewed-files>\n   ```\n"
    (match,) = _FENCE.finditer(text)
    assert problems(textwrap.dedent(match["body"]))


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        ("git add <reviewed-files>\n", "placeholder <reviewed-files>"),
        ("git commit -m 'x' \\ \n  --no-verify\n", "a backslash followed by whitespace"),
        ("python -m build \\\n\npython -m twine check dist/*\n", "continued onto a blank line"),
        ("python -m build \\\n# then check\npython -m twine check\n", "a comment"),
        ("echo 'unterminated\n", "bash -n"),
    ],
)
def test_each_way_a_pasted_command_breaks_is_found(block: str, reason: str) -> None:
    """A guard on the guard: the two shapes that failed in v4.0.0's release, and two more."""

    assert any(reason in problem for problem in problems(block)), problems(block)


def test_a_command_written_as_it_should_be_passes() -> None:
    block = (
        'VERSION="4.1.0"\n'
        'python docs/audit/scripts/release_preflight.py version --expected "${VERSION}" \\\n'
        '  && git tag -a "v${VERSION}" -m "AlphaLab ${VERSION}"\n'
    )
    assert problems(block) == []


@pytest.mark.parametrize(("where", "block"), _blocks(), ids=[where for where, _ in _blocks()])
def test_every_documented_command_runs_as_pasted(where: str, block: str) -> None:
    assert problems(block) == [], where
