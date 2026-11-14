"""Render and check the v4.3.0 progress register (plan section U.5, G0 item 17).

Usage, from the repository root::

    python docs/audit/scripts/progress_register.py           # render PROGRESS.md
    python docs/audit/scripts/progress_register.py --check   # fail when stale or inconsistent

``docs/work/v4.3.0/progress.json`` is the register: the decisions the owner
took at G0, the technical decisions taken under the authority the owner
delegated -- each with its problem, the alternatives weighed, the rationale and
what it does to compatibility -- how the plan changed and why, and where every
milestone, workstream, gap and ADR of the programme stands. ``PROGRESS.md``
beside it is its rendering and is never edited by hand, so the two cannot
disagree.

``--check`` -- which ``tests/regression/test_progress_register.py`` runs --
fails when the rendering is stale, or when the register uses a status outside
its vocabulary, loses or repeats an identifier the plan defines, or lets a
milestone and a workstream disagree about each other. Identifiers are the
plan's and are never renumbered: WS-00 to WS-29, G0 and M0 to M13, GAP-01 to
GAP-53.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REGISTER = ROOT / "docs" / "work" / "v4.3.0" / "progress.json"
RENDERED = REGISTER.with_name("PROGRESS.md")

#: The statuses the owner's implementation brief names (Part 15), in order.
STATUSES = (
    "Not started",
    "In progress",
    "Blocked",
    "Implemented locally",
    "Local validation passed",
    "Pushed",
    "Hosted CI passed",
    "Accepted",
    "Deferred by owner",
)
MILESTONES = ("G0", *(f"M{number}" for number in range(14)), "After M13")
WORKSTREAMS = tuple(f"WS-{number:02d}" for number in range(30))
GAPS = tuple(f"GAP-{number:02d}" for number in range(1, 54))
GAP_STATUSES = ("Open", "Closed", "Discarded")
DECISIONS = tuple(range(1, 25))
KEYS = (
    "register_version",
    "programme",
    "objective",
    "plan",
    "decision_record",
    "baseline",
    "branch",
    "pull_request",
    "statuses",
    "decisions",
    "technical_decisions",
    "plan_changes",
    "milestones",
    "workstreams",
    "gaps",
    "adrs",
    "authority",
    "state",
    "log",
)
#: What the current-state record must say, so a new session resumes from it.
STATE_KEYS = ("updated", "remote_head", "pull_request", "hosted_ci", "monitoring", "next")
#: What every delegated technical decision states (the owner's authorization, 2026-11-10).
TECHNICAL_KEYS = (
    "id",
    "date",
    "workstream",
    "title",
    "problem",
    "alternatives",
    "decision",
    "rationale",
    "compatibility",
    "record",
    "tests",
)


def load(path: Path = REGISTER) -> dict[str, Any]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return decoded


def _ids(entries: Sequence[Mapping[str, Any]], key: str = "id") -> list[Any]:
    return [entry[key] for entry in entries]


def _ordered(name: str, found: Sequence[Any], expected: Sequence[Any]) -> list[str]:
    if list(found) == list(expected):
        return []
    missing = [item for item in expected if item not in found]
    extra = [item for item in found if item not in expected]
    repeated = sorted({str(item) for item in found if list(found).count(item) > 1})
    return [
        f"{name}: expected {len(expected)} in the plan's order; "
        f"missing {missing}, unknown {extra}, repeated {repeated}"
    ]


def problems(register: Mapping[str, Any], rendered: str | None = None) -> list[str]:
    """Every way ``register`` breaks its own rules; empty when it holds.

    ``rendered`` is the committed rendering, compared with what the register
    renders to when given.
    """

    found: list[str] = []
    missing = [key for key in KEYS if key not in register]
    if missing:
        return [f"the register lacks {missing}"]
    if tuple(register["statuses"]) != STATUSES:
        found.append(f"statuses are {register['statuses']}, not the brief's {list(STATUSES)}")

    found += _ordered("decisions", _ids(register["decisions"], "item"), DECISIONS)
    found += _ordered("milestones", _ids(register["milestones"]), MILESTONES)
    found += _ordered("workstreams", _ids(register["workstreams"]), WORKSTREAMS)
    found += _ordered("gaps", _ids(register["gaps"]), GAPS)
    changes = _ids(register["plan_changes"])
    found += _ordered(
        "plan changes", changes, [f"PC-{number:02d}" for number in range(1, len(changes) + 1)]
    )
    technical = _ids(register["technical_decisions"])
    found += _ordered(
        "technical decisions",
        technical,
        [f"TD-{number:02d}" for number in range(1, len(technical) + 1)],
    )
    for decision in register["technical_decisions"]:
        unstated = [key for key in TECHNICAL_KEYS if not decision.get(key)]
        if unstated:
            found.append(f"{decision.get('id')}: does not state {unstated}")
        if decision.get("workstream") not in WORKSTREAMS:
            found.append(f"{decision.get('id')}: names an unknown workstream")
    adrs = _ids(register["adrs"])
    if len(set(adrs)) != len(adrs):
        found.append(f"an ADR is listed twice: {adrs}")

    for milestone in register["milestones"]:
        if milestone["status"] not in STATUSES:
            found.append(
                f"{milestone['id']}: status {milestone['status']!r} is not in the vocabulary"
            )
        for workstream in milestone["workstreams"]:
            if workstream not in WORKSTREAMS:
                found.append(f"{milestone['id']}: names an unknown workstream {workstream}")
    for workstream in register["workstreams"]:
        name = workstream["id"]
        if workstream["status"] not in STATUSES:
            found.append(f"{name}: status {workstream['status']!r} is not in the vocabulary")
        for item in workstream["items"]:
            if item["status"] not in STATUSES:
                found.append(f"{name}: item status {item['status']!r} is not in the vocabulary")
            if item["milestone"] not in workstream["milestones"]:
                found.append(
                    f"{name}: an item is placed in {item['milestone']}, not one of its own"
                )
        for milestone in workstream["milestones"]:
            if milestone not in MILESTONES:
                found.append(f"{name}: names an unknown milestone {milestone}")
    pairs_by_milestone = {
        (milestone["id"], workstream)
        for milestone in register["milestones"]
        for workstream in milestone["workstreams"]
    }
    pairs_by_workstream = {
        (milestone, workstream["id"])
        for workstream in register["workstreams"]
        for milestone in workstream["milestones"]
    }
    for milestone, workstream in sorted(pairs_by_milestone ^ pairs_by_workstream):
        found.append(f"{milestone} and {workstream} disagree about whether one holds the other")
    for gap in register["gaps"]:
        if gap["status"] not in GAP_STATUSES:
            found.append(f"{gap['id']}: status {gap['status']!r} is not one of {GAP_STATUSES}")
        unknown = [name for name in gap["workstreams"] if name not in WORKSTREAMS]
        if unknown:
            found.append(f"{gap['id']}: names unknown workstreams {unknown}")
    for adr in register["adrs"]:
        if adr["workstream"] not in WORKSTREAMS:
            found.append(f"{adr['id']}: names an unknown workstream {adr['workstream']}")
    missing_state = [key for key in STATE_KEYS if key not in register["state"]]
    if missing_state:
        found.append(f"the current-state record lacks {missing_state}")
    if not register["log"]:
        found.append("the continuation log is empty")
    dates = [entry["date"] for entry in register["log"]]
    if dates != sorted(dates):
        found.append("the continuation log is not in date order")
    if not register["authority"]["binding"] or not register["authority"]["forbidden"]:
        found.append("the authority record must state what binds it and what it forbids")

    if rendered is not None and rendered != render(register):
        found.append(
            "PROGRESS.md is not the rendering of progress.json: run "
            "`python docs/audit/scripts/progress_register.py` and commit both"
        )
    return found


def _cell(value: Any) -> str:
    text = "—" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _row(*values: Any) -> str:
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def render(register: Mapping[str, Any]) -> str:
    """The register as Markdown: one table per kind of entry, in the plan's order."""

    plan, record, baseline = register["plan"], register["decision_record"], register["baseline"]
    pull_request = (
        register["pull_request"] or "Not opened (opened after the first pushed increment)"
    )
    lines = [
        f"# {register['programme']}: progress register",
        "",
        "<!-- Rendered from progress.json by docs/audit/scripts/progress_register.py. "
        "Edit progress.json and render again; never edit this file by hand. -->",
        "",
        register["objective"],
        "",
        "| | |",
        "| --- | --- |",
        _row("Branch", f"`{register['branch']}`"),
        _row("Pull request", pull_request),
        _row(
            "Baseline",
            f"{baseline['tag']} (AlphaLab {baseline['release']}), commit `{baseline['commit']}`, "
            f"tree `{baseline['tree']}`",
        ),
        _row(
            "Plan",
            f"{plan['title']}, sha256 `{plan['sha256']}`, approved {plan['approved']}, "
            f"held {plan['held']}",
        ),
        _row("Decision record", f"{record['title']}, sha256 `{record['sha256']}`"),
        "",
        "Statuses: " + " · ".join(register["statuses"]) + ".",
        "",
        *_authority_and_state(register),
        "## Milestones",
        "",
        "| Milestone | Status | Workstreams | Exit gate (end-to-end proof) |",
        "| --- | --- | --- | --- |",
    ]
    for milestone in register["milestones"]:
        lines.append(
            _row(
                milestone["id"],
                milestone["status"],
                ", ".join(milestone["workstreams"]),
                milestone["exit_gate"],
            )
        )
    notes = [m for m in register["milestones"] if m.get("note")]
    if notes:
        lines.append("")
        lines += [f"- **{m['id']}:** {m['note']}" for m in notes]
    lines += [
        "",
        "## Decisions (G0 sign-off)",
        "",
        "| # | Ref | Decision | Status | Due | Note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for decision in register["decisions"]:
        lines.append(
            _row(
                decision["item"],
                decision["ref"],
                decision["title"],
                decision["status"],
                decision["due"],
                decision["note"],
            )
        )
        for part in decision.get("parts", ()):
            lines.append(
                _row(
                    "",
                    part["ref"],
                    f"(part of item {decision['item']})",
                    part["status"],
                    "",
                    part["note"],
                )
            )
    lines += [
        "",
        "## Technical decisions (delegated)",
        "",
        "Taken under the authority the owner delegated on 2026-11-10; each is the "
        "programme's to revisit, and none reopens a G0 decision.",
    ]
    for decision in register["technical_decisions"]:
        lines += [
            "",
            f"### {decision['id']}: {decision['title']} ({decision['workstream']}, "
            f"{decision['date']})",
            "",
            f"- **Problem:** {decision['problem']}",
            f"- **Alternatives:** {decision['alternatives']}",
            f"- **Decision:** {decision['decision']}",
            f"- **Rationale:** {decision['rationale']}",
            f"- **Compatibility:** {decision['compatibility']}",
            f"- **Recorded in:** {decision['record']}",
            f"- **Tests:** {decision['tests']}",
        ]
    lines += [
        "",
        "## Plan changes",
        "",
        "| ID | Date | Decision | Change | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for change in register["plan_changes"]:
        lines.append(
            _row(
                change["id"], change["date"], change["decision"], change["change"], change["reason"]
            )
        )
    lines += [
        "",
        "## Workstreams",
        "",
        "| WS | Title | Milestones | Status |",
        "| --- | --- | --- | --- |",
    ]
    for workstream in register["workstreams"]:
        lines.append(
            _row(
                workstream["id"],
                workstream["title"],
                ", ".join(workstream["milestones"]),
                workstream["status"],
            )
        )
    for workstream in register["workstreams"]:
        if not workstream["items"] and not workstream["evidence"]:
            continue
        lines += ["", f"### {workstream['id']}: {workstream['title']}", ""]
        lines += [
            f"- [{item['status']}] {item['milestone']}: {item['item']}"
            for item in workstream["items"]
        ]
        if workstream["evidence"]:
            lines += ["", "Evidence:", ""]
            lines += [f"- {evidence}" for evidence in workstream["evidence"]]
    counts = {
        status: sum(gap["status"] == status for gap in register["gaps"]) for status in GAP_STATUSES
    }
    lines += [
        "",
        "## Gaps",
        "",
        ", ".join(f"{count} {status.lower()}" for status, count in counts.items()) + ".",
        "",
        "| Gap | Problem | Severity | Status | Workstreams | Milestone |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for gap in register["gaps"]:
        status = gap["status_note"] or gap["status"]
        lines.append(
            _row(
                gap["id"],
                gap["problem"],
                gap["severity"],
                status,
                ", ".join(gap["workstreams"]),
                gap["milestone"],
            )
        )
    lines += [
        "",
        "## ADR programme",
        "",
        "Numbers are final (G0 item 16): the plan's, with plan 0054A as ADR-0054 and plan "
        "0054B as ADR-0072.",
        "",
        "| ADR | Subject | Workstream | Needed | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for adr in register["adrs"]:
        lines.append(
            _row(adr["id"], adr["subject"], adr["workstream"], adr["needed"], adr["status"])
        )
    return "\n".join(lines) + "\n"


def _authority_and_state(register: Mapping[str, Any]) -> list[str]:
    """The delegated authority, where the programme stands now, and how it got there."""

    authority, state = register["authority"], register["state"]
    lines = [
        f"## Authority (delegated {authority['granted']})",
        "",
        authority["summary"],
        "",
        "Binding:",
        "",
        *[f"- {rule}" for rule in authority["binding"]],
        "",
        "Never:",
        "",
        *[f"- {rule}" for rule in authority["forbidden"]],
        "",
        f"## Current state ({state['updated']})",
        "",
        "| | |",
        "| --- | --- |",
        _row("Remote head", f"`{state['remote_head']}`"),
        _row("Pull request", state["pull_request"]),
        _row("Monitoring", state["monitoring"]),
        _row("Next", state["next"]),
        "",
        "| Commit | Workflow | Run | Result |",
        "| --- | --- | --- | --- |",
        *[
            _row(f"`{check['sha']}`", check["workflow"], check["run"], check["result"])
            for check in state["hosted_ci"]
        ],
        "",
    ]
    if state.get("open_questions"):
        lines += ["Open questions:", ""]
        lines += [f"- {question}" for question in state["open_questions"]]
        lines.append("")
    lines += ["## Continuation log", ""]
    lines += [f"- **{entry['date']}** {entry['entry']}" for entry in register["log"]]
    lines.append("")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when stale or inconsistent")
    arguments = parser.parse_args(argv)
    register = load()
    if arguments.check:
        rendered = RENDERED.read_text(encoding="utf-8") if RENDERED.exists() else ""
        found = problems(register, rendered)
        for problem in found:
            print(problem)
        if not found:
            print(f"{REGISTER.relative_to(ROOT)}: consistent, and PROGRESS.md is its rendering")
        return 1 if found else 0
    found = problems(register)
    if found:
        for problem in found:
            print(problem)
        return 1
    RENDERED.write_text(render(register), encoding="utf-8")
    print(f"{RENDERED.relative_to(ROOT)}: rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
