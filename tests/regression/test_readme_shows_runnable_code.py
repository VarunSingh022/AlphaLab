"""The code the README shows is code that runs (v4.0).

The README's first-strategy section shows two excerpts of
``examples/70_build_a_strategy.py``, which CI runs, which an end-to-end test
asserts, and which the installed-package smoke test runs from outside the
checkout. An excerpt copied into a document drifts from its source the first
time the source changes; this holds every line of each excerpt to the example,
in the example's order -- an excerpt may leave lines out, as the README's leaves
out the class's docstring and constructor, but may not add or change one -- so
the README can only show what the example runs.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _python_blocks(text: str) -> list[list[str]]:
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in re.findall(r"```python\n(.*?)```", text, re.S)
    ]


def _in_order(block: list[str], source: list[str]) -> bool:
    """Whether every line of ``block`` occurs in ``source``, in ``block``'s order."""

    position = 0
    for line in block:
        try:
            position = source.index(line, position) + 1
        except ValueError:
            return False
    return True


def test_every_python_block_in_the_readme_is_an_excerpt_of_example_70() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = [
        line
        for line in (ROOT / "examples" / "70_build_a_strategy.py")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    blocks = _python_blocks(readme)
    assert blocks, "the README shows no Python; this test reads its python blocks"
    for block in blocks:
        assert _in_order(block, example), (
            f"README excerpt starting {block[0]!r} is not an excerpt of example 70"
        )
