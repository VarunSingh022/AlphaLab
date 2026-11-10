"""The v4.3.0 progress register holds its own rules (plan section U.5, G0 item 17).

``docs/work/v4.3.0/progress.json`` records the owner's G0 decisions, how the
plan changed and why, and where every milestone, workstream, gap and ADR of the
programme stands; ``PROGRESS.md`` is its rendering. A register that drifts from
its rendering, loses an identifier the plan defines, or records an approval the
owner did not give misreports the programme, so the suite holds all three.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script() -> ModuleType:
    path = ROOT / "docs" / "audit" / "scripts" / "progress_register.py"
    spec = importlib.util.spec_from_file_location("progress_register", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_register_is_consistent_and_its_rendering_current(script: ModuleType) -> None:
    register = script.load()

    assert script.problems(register, script.RENDERED.read_text(encoding="utf-8")) == []


def test_only_the_owners_approvals_are_recorded_as_approved(script: ModuleType) -> None:
    """Items 1-3, and the directions of 23 and 24, were approved on 2026-11-09; no other.

    An approval changes what may be built, so recording one is a deliberate
    edit of this test as well as of the register.
    """

    decisions = script.load()["decisions"]
    approved = {d["item"] for d in decisions if d["status"].startswith("Approved")}

    assert approved == {1, 2, 3, 23, 24}
    assert all(d["status"] != "Approved" for d in decisions if d["item"] in (23, 24))


@pytest.mark.parametrize(
    ("edit", "found"),
    [
        (lambda r: r["workstreams"][1].update(status="Done"), "vocabulary"),
        (lambda r: r["workstreams"].pop(29), "workstreams"),
        (lambda r: r["gaps"].insert(0, copy.deepcopy(r["gaps"][0])), "repeated"),
        (lambda r: r["milestones"][1]["workstreams"].append("WS-12"), "disagree"),
        (lambda r: r["gaps"][2].update(status="Fixed"), "GAP-03"),
        (lambda r: r["workstreams"][1]["items"][0].update(milestone="M7"), "not one of its own"),
        (lambda r: r["log"].clear(), "continuation log is empty"),
        (lambda r: r["log"].reverse(), "not in date order"),
        (lambda r: r["state"].pop("remote_head"), "current-state record lacks"),
        (lambda r: r["authority"].update(forbidden=[]), "what it forbids"),
    ],
)
def test_a_broken_register_is_refused(script: ModuleType, edit: object, found: str) -> None:
    register = copy.deepcopy(script.load())
    assert callable(edit)
    edit(register)

    assert any(found in problem for problem in script.problems(register))


def test_a_stale_rendering_is_refused(script: ModuleType) -> None:
    register = script.load()
    stale = script.render(register).replace("In progress", "Accepted", 1)

    assert any("PROGRESS.md" in problem for problem in script.problems(register, stale))
