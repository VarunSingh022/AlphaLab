"""Every release gate can run before publication, on the commit meant (v4.0).

Until v4.0 the only release-specific workflow, ``release.yml``, ran on
``release: published`` -- after the release existed -- and checked a build with
``twine check`` and nothing more. CI ran the quality gates only on a push or a
pull request into ``main``, and nothing checked the artifacts' names, their
hashes, the version against the intended release, or kept what was built.
``.github/workflows/preflight.yml`` runs all of it against one exact candidate
commit beforehand, through ``docs/audit/scripts/release_preflight.py``. These
tests hold the workflows to that contract -- which commit, which gates, what it
may not do -- and the script to its checks, on inputs built here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PREFLIGHT = (WORKFLOWS / "preflight.yml").read_text(encoding="utf-8")
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
BENCHMARKS = (WORKFLOWS / "benchmarks.yml").read_text(encoding="utf-8")
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")


def _script() -> ModuleType:
    path = ROOT / "docs" / "audit" / "scripts" / "release_preflight.py"
    spec = importlib.util.spec_from_file_location("release_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _job(name: str) -> str:
    """The text of one preflight job, from its key to the next job's."""

    start = PREFLIGHT.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z]+:\n", PREFLIGHT[start + 1 :])
    return PREFLIGHT[start : start + 1 + following.start()] if following else PREFLIGHT[start:]


# --------------------------------------------------------------------------- #
# The workflow: which commit, which gates, and what it may not do
# --------------------------------------------------------------------------- #


#: The candidate branch a push may validate: named once in the trigger, once in the guard.
CANDIDATE_BRANCH = "work/v4.3.0-release"


def _triggers() -> str:
    return PREFLIGHT[PREFLIGHT.index("\non:\n") : PREFLIGHT.index("\npermissions:")]


def test_it_runs_on_a_candidate_push_on_demand_or_on_a_labelled_pull_request() -> None:
    """Never on publication, and a push only to the candidate branch -- never to main."""

    triggers = _triggers()
    assert "workflow_dispatch:" in triggers and "pull_request:" in triggers
    assert "release:" not in triggers and "schedule:" not in triggers
    push = triggers[triggers.index("  push:\n") : triggers.index("  pull_request:\n")]
    assert re.findall(r"^      - (\S+)$", push, re.MULTILINE) == [CANDIDATE_BRANCH]
    assert "main" not in push.replace("never main", "")
    condition = _job("candidate")
    assert "(github.event_name == 'push' && !github.event.deleted)" in condition
    assert "contains(github.event.pull_request.labels.*.name, 'release-preflight')" in condition


def test_the_guard_names_the_branch_the_trigger_does() -> None:
    assert f"PUSH_BRANCH: {CANDIDATE_BRANCH}\n" in _job("candidate")


def test_a_manual_run_must_name_its_candidate_and_version_and_has_no_default() -> None:
    for name in ("candidate", "expected_version"):
        block = PREFLIGHT[PREFLIGHT.index(f"      {name}:\n") :].split("\n      ", 2)[1]
        assert "required: true" in PREFLIGHT[PREFLIGHT.index(f"      {name}:\n") :][:400]
        assert "default:" not in block


def test_a_manual_run_left_on_another_branch_validates_nothing() -> None:
    """The guard: the typed candidate must be the ref the run is on, or the run stops."""

    resolve = _job("candidate")
    assert 'if [ "${CANDIDATE}" != "${REF_NAME}" ]; then' in resolve
    assert "Nothing was validated." in resolve and "exit 1" in resolve
    assert "REF_NAME: ${{ github.ref_name }}" in resolve


def test_a_pull_request_is_validated_at_its_head_not_at_the_merge_commit() -> None:
    resolve = _job("candidate")
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in resolve
    assert 'sha="${HEAD_SHA}"' in resolve


def _step(workflow: str, step_id: str) -> tuple[str, dict[str, str]]:
    """A step's shell script and its fixed environment, exactly as ``workflow`` states them."""

    lines = workflow[workflow.index(f"        id: {step_id}\n") :].splitlines()
    head = "\n".join(lines[: lines.index("        run: |")])
    fixed = {
        name: value.strip('"')
        for name, value in re.findall(r"^          ([A-Z_]+): ([^$\n].*)$", head, re.MULTILINE)
    }
    body: list[str] = []
    for line in lines[lines.index("        run: |") + 1 :]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:])
    return "\n".join(body), fixed


def _guard() -> tuple[str, dict[str, str]]:
    """The commit guard's shell script and its fixed environment, as the workflow states them."""

    return _step(PREFLIGHT, "resolve")


def _run_step(
    script: str, environment: dict[str, str], cwd: Path, scratch: Path
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a step's script as the runner would, with ``python`` this interpreter; its output."""

    shims = scratch / "bin"
    shims.mkdir(exist_ok=True)
    if not (shims / "python").exists():
        (shims / "python").symlink_to(sys.executable)
    output = scratch / "github_output"
    output.write_text("", encoding="utf-8")
    done = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env={
            "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
            **environment,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(scratch / "summary"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return done, output.read_text(encoding="utf-8").strip()


_MAIN, _PUSHED, _HEAD, _ZERO = "a" * 40, "c" * 40, "d" * 40, "0" * 40


@pytest.mark.parametrize(
    ("event", "outcome"),
    [
        pytest.param(
            {
                "EVENT": "push",
                "REF": f"refs/heads/{CANDIDATE_BRANCH}",
                "RUN_SHA": _PUSHED,
                "PUSHED_SHA": _PUSHED,
                "PUSH_DELETED": "false",
            },
            _PUSHED,
            id="push-to-the-candidate",
        ),
        pytest.param(
            {
                "EVENT": "push",
                "REF": f"refs/heads/{CANDIDATE_BRANCH}",
                "RUN_SHA": _MAIN,
                "PUSHED_SHA": _ZERO,
                "PUSH_DELETED": "true",
            },
            "This push deleted",
            id="deletion-reports-main",
        ),
        pytest.param(
            {
                "EVENT": "push",
                "REF": "refs/heads/main",
                "RUN_SHA": _MAIN,
                "PUSHED_SHA": _MAIN,
                "PUSH_DELETED": "false",
            },
            "not the candidate branch",
            id="push-to-main",
        ),
        pytest.param(
            {
                "EVENT": "push",
                "REF": f"refs/heads/{CANDIDATE_BRANCH}",
                "RUN_SHA": _MAIN,
                "PUSHED_SHA": _PUSHED,
                "PUSH_DELETED": "false",
            },
            "is not the commit pushed",
            id="run-commit-is-not-the-pushed-tip",
        ),
        pytest.param(
            {
                "EVENT": "push",
                "REF": f"refs/heads/{CANDIDATE_BRANCH}",
                "RUN_SHA": _ZERO,
                "PUSHED_SHA": _ZERO,
                "PUSH_DELETED": "false",
            },
            "is not a commit",
            id="zero-commit",
        ),
        pytest.param(
            {
                "EVENT": "workflow_dispatch",
                "CANDIDATE": CANDIDATE_BRANCH,
                "REF_NAME": "main",
                "RUN_SHA": _MAIN,
            },
            "but this run is on 'main'",
            id="dispatch-left-on-main",
        ),
        pytest.param(
            {
                "EVENT": "workflow_dispatch",
                "CANDIDATE": CANDIDATE_BRANCH,
                "REF_NAME": CANDIDATE_BRANCH,
                "RUN_SHA": _PUSHED,
            },
            _PUSHED,
            id="dispatch-on-the-candidate",
        ),
        pytest.param(
            {
                "EVENT": "pull_request",
                "RUN_SHA": "e" * 40,
                "HEAD_SHA": _HEAD,
                "HEAD_REF": CANDIDATE_BRANCH,
            },
            _HEAD,
            id="labelled-pull-request-head-not-merge",
        ),
        pytest.param(
            {"EVENT": "schedule", "RUN_SHA": _MAIN}, "does not run on", id="any-other-event"
        ),
    ],
)
def test_the_guard_validates_exactly_the_candidate_commit_or_nothing(
    event: dict[str, str], outcome: str, tmp_path: Path
) -> None:
    """The guard's own script, run under each event's variables (v4.0).

    A push run's ``GITHUB_SHA`` is the pushed tip -- except on a deletion, when
    GitHub reports the default branch's commit; that run, a push to any other
    branch, a commit that is not the pushed tip and an unexpected event each
    validate nothing. ``outcome`` is the commit validated, or the reason given for
    validating nothing -- each check is held to its own refusal, so one check
    cannot silently stand in for another.
    """

    script, fixed = _guard()
    output, summary = tmp_path / "output", tmp_path / "summary"
    variables = dict.fromkeys(
        (
            "CANDIDATE",
            "REF",
            "REF_NAME",
            "RUN_SHA",
            "PUSHED_SHA",
            "PUSH_DELETED",
            "HEAD_SHA",
            "HEAD_REF",
        ),
        "",
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        **variables,
        **fixed,
        **event,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    done = subprocess.run(
        ["bash", "-e", "-c", script], env=environment, capture_output=True, text=True, check=False
    )
    written = output.read_text(encoding="utf-8").strip() if output.exists() else ""
    if re.fullmatch(r"[0-9a-f]{40}", outcome):
        assert done.returncode == 0, done.stdout + done.stderr
        assert written == f"sha={outcome}"
    else:
        assert done.returncode != 0 and written == "", (done.returncode, written)
        assert outcome in done.stdout and "Nothing was validated." in done.stdout, done.stdout


def _checkout_refs(workflow: str) -> list[str | None]:
    """The ``ref`` every ``actions/checkout`` step in ``workflow`` is given, or ``None``."""

    refs: list[str | None] = []
    for step in re.split(r"\n(?=\s+- (?:name|uses|run):)", workflow):
        if "uses: actions/checkout@" in step:
            found = re.search(r"^\s+ref: (.+)$", step, re.MULTILINE)
            refs.append(found.group(1).strip() if found else None)
    return refs


def test_the_checkout_reader_sees_a_checkout_that_ignores_its_ref() -> None:
    """The guard on the guard: a checkout without ``ref``, or with another, is found."""

    ignoring = (
        "    steps:\n"
        "      - name: Check out repository\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        "          ref: ${{ inputs.ref }}\n"
        "\n"
        "      - name: A second checkout\n"
        "        uses: actions/checkout@v4\n"
        "\n"
        "      - name: A third, of the event's commit\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        "          ref: ${{ github.sha }}\n"
    )
    assert _checkout_refs(ignoring) == ["${{ inputs.ref }}", None, "${{ github.sha }}"]


@pytest.mark.parametrize("workflow", ["ci.yml", "benchmarks.yml"])
def test_a_called_workflow_checks_out_exactly_the_commit_it_is_given(workflow: str) -> None:
    """CI and the benchmarks are reused by the preflight, so every checkout takes the given SHA.

    The input is optional and defaults to empty, which ``actions/checkout``
    reads as "the triggering event's commit": a workflow's own push, pull
    request or schedule runs are unchanged. Nothing else in it may name the
    event's commit, which under a pull request is the merge, not the candidate.
    """

    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    assert re.search(
        r"  workflow_call:\n    inputs:\n      ref:\n        description: .+\n"
        r'        required: false\n        type: string\n        default: ""\n',
        text,
    ), f"{workflow} declares no optional `ref` input for workflow_call"
    refs = _checkout_refs(text)
    assert refs, f"{workflow} checks nothing out"
    assert refs == ["${{ inputs.ref }}"] * len(refs), f"{workflow} checkouts take {refs}"
    for event_commit in (
        "github.sha",
        "GITHUB_SHA",
        "github.event.pull_request",
        "git checkout",
        "git fetch",
        "git switch",
        "git reset",
    ):
        assert event_commit not in text, f"{workflow} reaches for {event_commit!r}"


def test_the_preflight_hands_every_job_the_candidate_commit() -> None:
    """One SHA -- the guard's -- reaches CI, the benchmarks, CodeQL and the build alike."""

    candidate = "${{ needs.candidate.outputs.sha }}"
    assert _checkout_refs(_job("candidate")) == ["${{ steps.resolve.outputs.sha }}"]
    for job in ("codeql", "distributions"):
        assert _checkout_refs(_job(job)) == [candidate], job
        assert "needs: candidate" in _job(job), job
    for job, called in (("quality", "ci.yml"), ("benchmarks", "benchmarks.yml")):
        text = _job(job)
        assert f"uses: ./.github/workflows/{called}" in text, job
        assert "needs: candidate" in text, job
        assert re.search(rf"    with:\n      ref: {re.escape(candidate)}\n", text), job
    reused = re.findall(r"uses: \./\.github/workflows/(\S+)", PREFLIGHT)
    assert sorted(reused) == ["benchmarks.yml", "ci.yml"], reused
    for event_commit in ("${{ github.sha }}", "GITHUB_SHA"):
        outside_the_guard = PREFLIGHT.replace("RUN_SHA: ${{ github.sha }}", "")
        assert event_commit not in outside_the_guard, event_commit


def test_every_release_gate_runs_before_publication() -> None:
    assert "release_preflight.py version" in _job("candidate")
    distributions = _job("distributions")
    assert "python -m build" in distributions
    assert "twine check --strict" in distributions
    assert "release_preflight.py distributions" in distributions
    assert "retention-days:" in distributions and "if-no-files-found: error" in distributions
    codeql = _job("codeql")
    assert "upload: never" in codeql and "release_preflight.py codeql" in codeql
    # The CI gates it calls: lint, format, types, tests, examples, the
    # certificate, and the installed artifacts.
    for gate in (
        "ruff check .",
        "ruff format --check .",
        "mypy .",
        "pytest -W error",
        "examples/[0-9]*.py",
        "certify_release.py --check",
        "twine check --strict dist/*",
        # Each artifact installed alone and smoke-tested (tests/installed_smoke.py),
        # by the command release.yml runs (v4.1).
        "release_preflight.py distributions dist",
    ):
        assert gate in CI, gate


def test_the_verdict_fails_unless_every_gate_succeeded() -> None:
    verdict = _job("verdict")
    assert "needs: [candidate, quality, benchmarks, codeql, distributions]" in verdict
    assert "if: always() && needs.candidate.result != 'skipped'" in verdict
    assert 'select(.value.result != "success")' in verdict and "exit 1" in verdict


def test_it_publishes_nothing_and_writes_nothing() -> None:
    assert "permissions:\n  contents: read\n" in PREFLIGHT
    granted = re.findall(r"^\s+([a-z-]+): write$", PREFLIGHT, re.MULTILINE)
    assert granted == ["security-events"], granted
    for forbidden in (
        "contents: write",
        "gh release",
        "git push",
        "git tag",
        "create-release",
        "action-gh-release",
        "pypi-publish",
        "twine upload",
        "secrets.",
    ):
        assert forbidden not in PREFLIGHT, forbidden


def test_untrusted_inputs_reach_shell_only_through_the_environment() -> None:
    """``${{ inputs.* }}`` inside ``run:`` would let a typed value inject shell (v4.0)."""

    for script in re.findall(r"run: \|\n((?:          .*\n|\n)+)", PREFLIGHT):
        assert "${{" not in script, script[:200]


def _release_job(name: str) -> str:
    start = RELEASE.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z]+:\n", RELEASE[start + 1 :])
    return RELEASE[start : start + 1 + following.start()] if following else RELEASE[start:]


def test_publication_still_rechecks_what_was_tagged() -> None:
    assert "types: [published]" in RELEASE
    assert 'release_preflight.py version --tag "${TAG}"' in RELEASE
    assert "TAG: ${{ github.event.release.tag_name }}" in RELEASE
    validate = _release_job("validate")
    assert "twine check --strict dist/*" in validate
    assert "release_preflight.py distributions dist" in validate
    # The version is the validated tag's, handed on; nothing strips a v unchecked.
    approval = _release_job("approval")
    assert "version: ${{ steps.tag.outputs.version }}" in approval
    assert "VERSION: ${{ needs.approval.outputs.version }}" in validate
    assert RELEASE.count("${TAG#v}") == 1
    tag_step = RELEASE[RELEASE.index("id: tag") : RELEASE.index("id: commit")]
    assert tag_step.index("version --tag") < tag_step.index("${TAG#v}")


def test_release_validation_needs_the_approval_and_builds_only_the_approved_commit() -> None:
    """v4.1: a tag is validated only once its commit and its approval are established."""

    validate = _release_job("validate")
    assert "needs: approval" in validate
    assert _checkout_refs(validate) == ["${{ needs.approval.outputs.sha }}"]
    approval = _release_job("approval")
    assert _checkout_refs(approval) == ["${{ github.event.release.tag_name }}"]
    assert "fetch-depth: 0" in approval
    assert "sha: ${{ steps.commit.outputs.sha }}" in approval
    commit = approval[approval.index("id: commit") : approval.index("- name: A release preflight")]
    assert "RUN_SHA: ${{ github.sha }}" in commit
    gate = approval[approval.index("- name: A release preflight") :]
    assert "release_preflight.py approval" in gate
    for variable in (
        "SHA: ${{ steps.commit.outputs.sha }}",
        "VERSION: ${{ steps.tag.outputs.version }}",
    ):
        assert variable in gate, variable


def test_release_validation_reads_and_writes_nothing_it_need_not() -> None:
    assert "permissions:\n  contents: read\n" in RELEASE
    granted = re.findall(r"^\s+([a-z-]+): write$", RELEASE, re.MULTILINE)
    assert granted == [], granted
    assert re.findall(r"^\s+([a-z-]+): read$", _release_job("approval"), re.M) == [
        "actions",
        "contents",
    ]
    for forbidden in ("gh release", "git push", "git tag ", "twine upload", "secrets."):
        assert forbidden not in RELEASE, forbidden


def test_ci_runs_the_distributions_command_release_validation_runs() -> None:
    """The v4.0.0 failure was in a command only release.yml ran; CI now runs it too (v4.1)."""

    def command(text: str) -> str:
        found = re.search(r"release_preflight\.py distributions dist \\\n\s+(.+)\n", text)
        assert found is not None
        return found.group(1).replace("version", "VERSION").replace("${VERSION}", "V")

    assert command(CI) == command(RELEASE)
    assert command(CI) == '--VERSION "V" --record "dist/VERIFICATION-V.txt"'


def _git(repository: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    return done.stdout.strip()


@pytest.mark.parametrize(
    ("tag_on", "run_sha", "passes"),
    [
        ("head", "head", True),
        ("first", "head", False),  # the tag names another commit than the checkout
        ("head", "first", False),  # the event names another commit than the tag
    ],
)
def test_the_tag_the_checkout_and_the_event_are_one_commit(
    tag_on: str, run_sha: str, passes: bool, tmp_path: Path
) -> None:
    """``release.yml``'s commit step, run in a repository built here."""

    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "a").write_text("1", encoding="utf-8")
    _git(repository, "add", "a")
    _git(repository, "commit", "-q", "-m", "first")
    first = _git(repository, "rev-parse", "HEAD")
    (repository / "a").write_text("2", encoding="utf-8")
    _git(repository, "commit", "-q", "-am", "second")
    head = _git(repository, "rev-parse", "HEAD")
    commits = {"head": head, "first": first}
    _git(repository, "tag", "v9.9.9", commits[tag_on])

    script, fixed = _step(RELEASE, "commit")
    done, written = _run_step(
        script,
        {**fixed, "TAG": "v9.9.9", "RUN_SHA": commits[run_sha], "PATH": os.environ["PATH"]},
        repository,
        tmp_path,
    )
    if passes:
        assert done.returncode == 0, done.stdout + done.stderr
        assert written == f"sha={head}"
    else:
        assert done.returncode != 0 and written == ""
        assert "they must be one commit. Nothing was validated." in done.stdout


_TAGGED, _OTHER, _TREE = "1" * 40, "2" * 40, "f" * 40


def _approval(
    runs: list[dict[str, object]],
    artifacts: dict[int, list[str]],
    trees: dict[str, str] | None = None,
    sha: str = _TAGGED,
    version: str = "9.9.9",
) -> tuple[list[str], list[str]]:
    known = {_TAGGED: _TREE, **(trees or {})}
    problems, considered = _script().approval_problems(
        sha, version, runs, known.get, lambda run: artifacts.get(run, [])
    )
    return list(problems), list(considered)


def _run(
    identifier: int, head: str, conclusion: str = "success", path: str = ""
) -> dict[str, object]:
    return {
        "id": identifier,
        "head_sha": head,
        "conclusion": conclusion,
        "path": path or ".github/workflows/preflight.yml",
    }


def test_a_passing_preflight_of_the_tagged_commit_approves_it() -> None:
    problems, considered = _approval([_run(7, _TAGGED)], {7: [f"alphalab-9.9.9-{_TAGGED}"]})

    assert problems == []
    assert (
        considered[-1].startswith("approved: preflight run 7")
        and "the tagged commit" in considered[-1]
    )


def test_a_merge_that_changed_no_file_is_the_source_the_preflight_validated() -> None:
    problems, considered = _approval(
        [_run(8, _OTHER)], {8: [f"alphalab-9.9.9-{_OTHER}"]}, trees={_OTHER: _TREE}
    )

    assert problems == [] and "same tree" in considered[-1]


_UNAPPROVED = f"no successful Release Preflight validated alphalab 9.9.9 at {_TAGGED}"


@pytest.mark.parametrize(
    ("runs", "artifacts", "trees", "sha", "refusal"),
    [
        pytest.param([], {}, {}, _TAGGED, _UNAPPROVED, id="no-preflight-at-all"),
        pytest.param(
            [_run(1, _TAGGED, conclusion="failure")],
            {1: [f"alphalab-9.9.9-{_TAGGED}"]},
            {},
            _TAGGED,
            _UNAPPROVED,
            id="a-failed-preflight",
        ),
        pytest.param(
            [_run(2, _TAGGED, path=".github/workflows/ci.yml")],
            {2: [f"alphalab-9.9.9-{_TAGGED}"]},
            {},
            _TAGGED,
            _UNAPPROVED,
            id="another-workflow",
        ),
        pytest.param(
            [_run(3, _TAGGED)],
            {3: [f"alphalab-9.9.8-{_TAGGED}"]},
            {},
            _TAGGED,
            _UNAPPROVED,
            id="another-version",
        ),
        pytest.param(
            [_run(4, _OTHER)],
            {4: [f"alphalab-9.9.9-{_OTHER}"]},
            {_OTHER: "e" * 40},
            _TAGGED,
            _UNAPPROVED,
            id="another-source",
        ),
        pytest.param([_run(5, _TAGGED)], {5: []}, {}, _TAGGED, _UNAPPROVED, id="no-artifact-kept"),
        pytest.param(
            [_run(6, _TAGGED)],
            {6: [f"alphalab-9.9.9-{_TAGGED}"]},
            {},
            "abc",
            "the tagged commit 'abc' is not a commit",
            id="not-a-commit",
        ),
        pytest.param(
            [_run(9, _OTHER)],
            {9: [f"alphalab-9.9.9-{_OTHER}"]},
            {},
            _OTHER,
            f"the tagged commit {_OTHER} is not in this repository",
            id="unknown-commit",
        ),
    ],
)
def test_a_tag_no_passing_preflight_validated_is_refused(
    runs: list[dict[str, object]],
    artifacts: dict[int, list[str]],
    trees: dict[str, str],
    sha: str,
    refusal: str,
) -> None:
    problems, _ = _approval(runs, artifacts, trees, sha=sha)

    assert len(problems) == 1 and problems[0].startswith(refusal), problems


def test_the_preflight_keeps_the_artifact_the_approval_reads() -> None:
    upload = _job("distributions")
    assert (
        "name: alphalab-${{ needs.candidate.outputs.version }}-${{ needs.candidate.outputs.sha }}"
        in upload
    )
    assert _script().preflight_artifact("9.9.9", _TAGGED) == f"alphalab-9.9.9-{_TAGGED}"
    assert "retention-days: 90" in upload


@pytest.mark.parametrize(
    ("tag", "outcome"),
    [
        ("v{version}", None),
        ("v4.0.1", "but 4.0.1 was expected"),
        ("v3.13.0", "but 3.13.0 was expected"),
        ("{version}", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("V{version}", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("v{version}-rc1", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("vv{version}", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("refs/tags/v{version}", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("v4.0", "is not v<MAJOR>.<MINOR>.<PATCH>"),
        ("", "is not v<MAJOR>.<MINOR>.<PATCH>"),
    ],
)
def test_a_release_tag_is_v_and_the_package_s_version(
    tag: str, outcome: str | None, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """``release.yml``: the published tag ``v4.0.0`` names package 4.0.0; nothing else passes.

    Checked in a release's tree with the package's version -- the tree being
    tested may be one developing it, which the gate refuses whatever the tag
    (``test_a_development_tree_is_coherent_and_the_release_gate_refuses_it``).
    """

    version = _script().package_version()
    script = _script_in(_release_tree(tmp_path / "tree", version))
    tag = tag.format(version=version)

    assert script.main(["version", "--tag", tag]) == (0 if outcome is None else 1)
    said = capsys.readouterr()
    if outcome is None:
        assert f"(tag {tag})" in said.out
    else:
        assert outcome in said.err


@pytest.mark.parametrize(
    ("tag", "validates"),
    [("v{version}", True), ("{version}", False), ("v4.0.1", False), ("v{version}-rc1", False)],
)
def test_release_yml_takes_the_version_only_from_a_tag_it_has_checked(
    tag: str, validates: bool, tmp_path: Path
) -> None:
    """The step's own script: it fails on a bad tag before any version is handed on."""

    version = _script().package_version()
    tree = _release_tree(tmp_path / "tree", version)
    script, fixed = _step(RELEASE, "tag")
    done, written = _run_step(script, {**fixed, "TAG": tag.format(version=version)}, tree, tmp_path)
    if validates:
        assert done.returncode == 0, done.stdout + done.stderr
        assert written == f"version={version}"
    else:
        assert done.returncode != 0 and written == "", (done.returncode, written)


def _release_tree(root: Path, version: str) -> Path:
    """A tree whose every release reference agrees on ``version``, with the real script in it."""

    (root / "alphalab" / "common").mkdir(parents=True)
    (root / "alphalab" / "common" / "_version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(f"# [{version}] - 2026-10-11\n", encoding="utf-8")
    for relative in ("docs/audit/release_certification.json", "docs/api/public_api.json"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(json.dumps({"release": version}), encoding="utf-8")
    (root / "docs" / "api" / "history").mkdir()
    (root / "docs" / "api" / "history" / f"{version}.json").write_text(
        json.dumps({"release": version}), encoding="utf-8"
    )
    scripts = root / "docs" / "audit" / "scripts"
    scripts.mkdir(parents=True)
    source = ROOT / "docs" / "audit" / "scripts" / "release_preflight.py"
    (scripts / "release_preflight.py").write_text(source.read_text(encoding="utf-8"))
    return root


def _script_in(tree: Path) -> ModuleType:
    """The script copied into ``tree``, loaded so that its ``ROOT``, every default, is the tree."""

    path = tree / "docs" / "audit" / "scripts" / "release_preflight.py"
    spec = importlib.util.spec_from_file_location(f"release_preflight_in_{tree.name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_references_that_agree_on_another_version_are_not_this_release(tmp_path: Path) -> None:
    """4.0.1 everywhere agrees with itself and is still not 4.0.0."""

    tree = _release_tree(tmp_path / "tree", "4.0.1")
    script = _script()

    assert script.version_problems("4.0.1", tree) == [], "every reference agrees"
    assert script.version_problems("4.0.0", tree) == [
        "the package is 4.0.1, but 4.0.0 was expected"
    ]


def test_the_candidate_s_release_is_the_one_the_push_branch_names() -> None:
    """RELEASE_VERSION is the package's, and the branch the push route runs on is named for it."""

    _, fixed = _step(PREFLIGHT, "identity")
    assert fixed["RELEASE_VERSION"] == _script().package_version()
    assert f"v{fixed['RELEASE_VERSION']}" in CANDIDATE_BRANCH


@pytest.mark.parametrize(
    ("event", "dispatched", "tree_version", "outcome"),
    [
        ("push", "", "{later}", "but {version} was expected"),
        ("pull_request", "", "{later}", "but {version} was expected"),
        ("push", "", "{version}", "version={version}"),
        ("pull_request", "", "{version}", "version={version}"),
        ("workflow_dispatch", "{later}", "{later}", "version={later}"),
        ("workflow_dispatch", "{version}", "{later}", "but {version} was expected"),
        ("workflow_dispatch", "v{version}", "{version}", "is not MAJOR.MINOR.PATCH"),
    ],
)
def test_push_and_pull_request_runs_require_the_release_and_a_manual_run_names_its_own(
    event: str, dispatched: str, tree_version: str, outcome: str, tmp_path: Path
) -> None:
    """The identity step's own script, in a tree whose every reference says ``tree_version``.

    A push or a labelled pull request must find the release RELEASE_VERSION
    names, the package's; a tree that says the next patch throughout fails
    though it agrees with itself. A manual run keeps its own
    ``expected_version``, which the tree must match.
    """

    version = _script().package_version()
    major, minor, patch = version.split(".")
    names = {"version": version, "later": f"{major}.{minor}.{int(patch) + 1}"}
    dispatched, tree_version, outcome = (
        text.format(**names) for text in (dispatched, tree_version, outcome)
    )
    tree = _release_tree(tmp_path / "tree", tree_version)
    script, fixed = _step(PREFLIGHT, "identity")
    done, written = _run_step(
        script, {**fixed, "EVENT": event, "DISPATCHED_VERSION": dispatched}, tree, tmp_path
    )
    if outcome.startswith("version="):
        assert done.returncode == 0, done.stdout + done.stderr
        assert written == outcome
    else:
        assert done.returncode != 0 and written == "", (done.returncode, written)
        assert outcome in done.stderr, done.stderr


# --------------------------------------------------------------------------- #
# The script, on inputs built here
# --------------------------------------------------------------------------- #


#: Release-facing lines v4.2.0 was published with, word for word (ledger DOC-013, TST-024):
#: each document's text, every line of which the version checks of v4.2.0 passed.
V4_2_0_AS_PUBLISHED = {
    "CHANGELOG.md": (
        "# [4.2.0] - unreleased (the development target the maintainer approved; "
        "not yet published or tagged)\n"
    ),
    "README.md": (
        "**Current Release:** **v4.2.0 (release candidate, not yet published; the published tag is "
        "v4.1.0) — every timed input on every driver, several datasets in one run, contracts "
        "declared and carried through exercise, assignment, expiry and delivery, execution "
        "algorithms and a queue model on the historical drivers, settlement, margin, financing and "
        "borrow on declared terms, venue controls and auctions, corporate actions and their "
        "corrections, volatility surfaces and auction information delivered during a run, typed "
        "strategy configuration -- additive on the v4.0 contract**\n"
    ),
    "docs/INTEGRATION.md": (
        "**4.2.0 is the development target and is not yet published.** Until its tag\n"
        "and release exist, the download and `@v4.2.0` references below resolve to\n"
        "nothing; the published tag is v4.1.0, whose own `docs/INTEGRATION.md` describes\n"
        "it.\n"
    ),
    "docs/README.md": (
        "**v4.2.0 -- in development, unpublished: additive on the v4.1 contract.** Every\n"
        "timed input on every driver; execution algorithms and a declared queue model on\n"
        "the historical drivers; a derivative's lifecycle and richer contract terms;\n"
        "strategy-directed exercise and cancellation; settlement-date cash; margin calls\n"
        "and forced liquidation on declared margin terms; a strategy configuration read\n"
        "back typed. The readiness evidence is `audit/V4_2_0_READINESS.md`; the published\n"
        "release is still v4.1.0.\n"
    ),
    "ROADMAP.md": (
        "## v4.2.0 (in development: the v4.1.1 future-readiness program and phases 2 to 6, "
        "its number and scope set by the maintainer; not yet published)\n"
    ),
    "docs/api/history/README.md": (
        "| `4.2.0.json` | v4.2.0 (development; unpublished) | `docs/api/public_api.json` of the "
        "v4.2.0 development tree, copied when the maintainer set 4.2.0 as the development version "
        "and refreshed with it until 4.2.0 is published -- the release preflight requires the "
        "release's own manifest here. Against 4.1.0: names added and defaulted keywords or fields "
        "only (CHANGELOG `[4.2.0]`). |\n"
    ),
}

#: The same places worded as a published 4.2.0 should have had them, shortened: what v4.2.1
#: corrects them to, in a tree that is still 4.2.0's.
V4_2_0_AS_CORRECTED = {
    "CHANGELOG.md": "# [4.2.0] - 2026-11-01\n",
    "README.md": (
        "**Current Release:** **v4.2.0 — every timed input on every driver, several "
        "datasets in one run**\n"
    ),
    "docs/INTEGRATION.md": "Download the `v4.2.0` release's `SHA256SUMS-4.2.0`.\n",
    "docs/README.md": (
        "**v4.2.0 -- additive on the v4.1 contract** (published 2026-11-01). Every\n"
        "timed input on every driver.\n"
    ),
    "ROADMAP.md": "## v4.2.0 (published 2026-11-01: the v4.1.1 future-readiness program)\n",
    "docs/api/history/README.md": (
        "| `4.2.0.json` | v4.2.0 (commit `0fea4c7`) | `docs/api/public_api.json` as released |\n"
    ),
}


def _with_documents(tree: Path, documents: dict[str, str]) -> Path:
    for relative, text in documents.items():
        (tree / relative).parent.mkdir(parents=True, exist_ok=True)
        (tree / relative).write_text(text, encoding="utf-8")
    return tree


@pytest.mark.parametrize("document", sorted(V4_2_0_AS_PUBLISHED))
def test_each_document_v4_2_0_was_published_with_is_refused(document: str, tmp_path: Path) -> None:
    """The v4.2.0 wording, one document at a time, in a tree that otherwise agrees on 4.2.0."""

    tree = _with_documents(
        _release_tree(tmp_path / "tree", "4.2.0"),
        {**V4_2_0_AS_CORRECTED, document: V4_2_0_AS_PUBLISHED[document]},
    )

    problems = _script().version_problems("4.2.0", tree)

    assert problems, f"v4.2.0's {document} passed the release checks again"
    # A problem names its document first; "README.md" is also the end of two other paths.
    assert all(problem.startswith(document) for problem in problems), problems


def test_v4_2_0_as_published_is_refused_in_every_place_it_was_wrong(tmp_path: Path) -> None:
    tree = _with_documents(_release_tree(tmp_path / "tree", "4.2.0"), V4_2_0_AS_PUBLISHED)

    problems = _script().release_status_problems("4.2.0", tree)

    assert [problem.split(" ", 1)[0] for problem in problems] == [
        "CHANGELOG.md's",
        "README.md:1",
        "README.md:1",
        "ROADMAP.md:1",
        "docs/README.md:1",
        "docs/README.md:6",
        "docs/INTEGRATION.md:1",
        "docs/INTEGRATION.md:3",
        "docs/api/history/README.md:1",
    ], problems
    assert problems[0].startswith("CHANGELOG.md's heading for 4.2.0 is not '# [4.2.0] - ")
    published = "names 4.1.0 the published release, not 4.2.0"
    kinds = [problem.split(" ", 1)[1] for problem in problems[1:]]
    assert [
        "unreleased" if kind.startswith("calls 4.2.0 unreleased: ") else kind for kind in kinds
    ] == [
        "unreleased",
        published,
        "unreleased",
        "unreleased",
        published,
        "unreleased",
        published,
        "unreleased",
    ], problems


def test_the_corrected_wording_passes(tmp_path: Path) -> None:
    tree = _with_documents(_release_tree(tmp_path / "tree", "4.2.0"), V4_2_0_AS_CORRECTED)

    assert _script().version_problems("4.2.0", tree) == []


@pytest.mark.parametrize("wording", ["as-published", "as-corrected"])
def test_release_yml_refuses_a_tag_whose_documents_call_the_release_unpublished(
    wording: str, tmp_path: Path
) -> None:
    """``release.yml``'s tag step, run at publication, on v4.2.0's documents (ledger TST-024).

    v4.2.0 was published through this step with every document below calling it
    unreleased, a candidate or not yet published. The step now refuses that tag
    before it hands any version on, and passes the same places once they say what
    is true.
    """

    as_published = wording == "as-published"
    documents = V4_2_0_AS_PUBLISHED if as_published else V4_2_0_AS_CORRECTED
    tree = _with_documents(_release_tree(tmp_path / "tree", "4.2.0"), documents)
    script, fixed = _step(RELEASE, "tag")

    done, written = _run_step(script, {**fixed, "TAG": "v4.2.0"}, tree, tmp_path)

    if as_published:
        assert done.returncode != 0 and written == "", (done.returncode, written)
        assert "FAILED: CHANGELOG.md's heading for 4.2.0 is not" in done.stderr, done.stderr
        assert "FAILED: README.md:1 names 4.1.0 the published release, not 4.2.0" in done.stderr
    else:
        assert done.returncode == 0, done.stdout + done.stderr
        assert written == "version=4.2.0"


@pytest.mark.parametrize(
    ("heading", "dated"),
    [
        ("# [4.2.0] - 2026-11-01", True),
        ("# [4.2.0]", False),
        ("# [4.2.0] - unreleased", False),
        ("# [4.2.0] - 2026-02-30", False),
        ("# [4.2.0] - 2026-11-01 (release candidate)", False),
        ("# [4.2.0] - 01-11-2026", False),
    ],
)
def test_a_release_heading_carries_the_date_it_was_published(
    heading: str, dated: bool, tmp_path: Path
) -> None:
    tree = _with_documents(
        _release_tree(tmp_path / "tree", "4.2.0"), {"CHANGELOG.md": f"{heading}\n"}
    )

    problems = _script().version_problems("4.2.0", tree)

    assert problems == (
        []
        if dated
        else [
            f"CHANGELOG.md's heading for 4.2.0 is not '# [4.2.0] - YYYY-MM-DD', the date it is "
            f"published: {heading!r}"
        ]
    )


def test_a_history_copy_that_is_not_the_manifest_is_refused(tmp_path: Path) -> None:
    """v4.2.0's copy was its manifest at phase 3: 2,730 exports of the 2,754 released."""

    tree = _release_tree(tmp_path / "tree", "4.2.0")
    (tree / "docs" / "api" / "history" / "4.2.0.json").write_text("{}", encoding="utf-8")

    assert _script().version_problems("4.2.0", tree) == [
        "docs/api/history/4.2.0.json is not docs/api/public_api.json: the release's copy of "
        "its manifest is the manifest (ledger TST-024)"
    ]


def test_another_release_named_and_the_release_itself_are_told_apart(tmp_path: Path) -> None:
    """Only the release being made is read for its status; history about others is not."""

    tree = _with_documents(
        _release_tree(tmp_path / "tree", "4.2.1"),
        {
            "README.md": "v4.2.0 was a release candidate before it was published.\n",
            "docs/README.md": "the published release is v4.2.1, and v4.2.10 is not one\n",
        },
    )

    assert _script().release_status_problems("4.2.1", tree) == []


def test_the_release_facing_documents_are_the_ones_the_version_test_holds() -> None:
    from tests.regression.test_release_facing_versions import RELEASE_FACING

    held = (*RELEASE_FACING, "docs/api/history/README.md")
    assert held == _script().RELEASE_FACING


def test_this_release_identity_agrees_everywhere() -> None:
    """The tree is this release, or this release coherently in development (v4.3), never neither."""

    script = _script()
    version = script.package_version()

    if script.developing(version):
        assert script.development_problems(version) == []
        assert any(
            problem.startswith(f"CHANGELOG.md's heading for {version} is not ")
            for problem in script.version_problems(version)
        ), "the release gate refuses a development tree"
        assert script.documented_release() != version
    else:
        assert script.version_problems(version) == []
        assert script.release_status_problems(version) == []
        assert script.documented_release() == version
    assert script.version_problems("0.0.1")[0] == (
        f"the package is {version}, but 0.0.1 was expected"
    )
    assert script.version_problems(f"v{version}")[0] == (
        f"the expected version 'v{version}' is not MAJOR.MINOR.PATCH (a release tag such as "
        "v4.0.0 is given with --tag)"
    ), "a tag is a tag: --expected takes the bare version"


def _development_tree(root: Path, version: str, published: str) -> Path:
    """A tree developing ``version`` after ``published``, every record of it ``version``'s."""

    tree = _release_tree(root, version)
    (tree / "CHANGELOG.md").write_text(
        f"# [{version}] - Unreleased\n\nThe work so far.\n\n# [{published}] - 2026-11-09\n",
        encoding="utf-8",
    )
    return tree


def test_a_development_tree_is_coherent_and_the_release_gate_refuses_it(tmp_path: Path) -> None:
    """v4.3: a release developed under its own version, which ``version`` will not let out."""

    tree = _development_tree(tmp_path / "tree", "4.3.0", "4.2.1")
    (tree / "README.md").write_text(
        "**Current Release:** **v4.2.1**; the published release is 4.2.1.\n", encoding="utf-8"
    )
    script = _script()

    assert script.developing("4.3.0", tree)
    assert script.documented_release(tree) == "4.2.1"
    assert script.development_problems("4.3.0", tree) == []
    assert script.version_problems("4.3.0", tree) == [
        "CHANGELOG.md's heading for 4.3.0 is not '# [4.3.0] - YYYY-MM-DD', the date it is "
        "published: '# [4.3.0] - Unreleased'",
        "README.md:1 names 4.2.1 the published release, not 4.3.0",
    ]
    assert _script_in(tree).main(["version", "--expected", "4.3.0"]) == 1


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (
            {"CHANGELOG.md": "# [4.3.0] - unreleased (in development)\n\n# [4.2.1] - 2026-11-09\n"},
            "CHANGELOG.md does not lead with '# [4.3.0] - Unreleased'",
        ),
        ({"CHANGELOG.md": "# [4.3.0] - Unreleased\n"}, "CHANGELOG.md dates no release"),
        (
            {"CHANGELOG.md": "# [4.3.0] - Unreleased\n\n# [4.3.0] - 2026-11-09\n"},
            "4.3.0 is not later than 4.3.0, the newest release CHANGELOG.md dates",
        ),
        (
            {"docs/api/public_api.json": json.dumps({"release": "4.2.1"})},
            "the public-API manifest (docs/api/public_api.json) is 4.2.1's, not 4.3.0's",
        ),
        (
            {"docs/audit/release_certification.json": json.dumps({"release": "4.2.1"})},
            "the release certificate (docs/audit/release_certification.json) is 4.2.1's",
        ),
        (
            {"docs/api/history/4.3.0.json": json.dumps({"release": "4.3.0", "stale": True})},
            "docs/api/history/4.3.0.json is not docs/api/public_api.json",
        ),
        (
            {"docs/README.md": "v4.3.0 -- in development, unpublished.\n"},
            "docs/README.md:1 calls 4.3.0 unreleased",
        ),
        (
            {"README.md": "The published release is v4.1.0.\n"},
            "README.md:1 names 4.1.0 the published release, not 4.3.0",
        ),
    ],
    ids=[
        "heading not the development one",
        "nothing published before",
        "not later than the published",
        "manifest another release's",
        "certificate another release's",
        "history copy not the manifest",
        "called unreleased",
        "another release named published",
    ],
)
def test_an_incoherent_development_tree_is_refused(
    change: dict[str, str], refused: str, tmp_path: Path
) -> None:
    tree = _development_tree(tmp_path / "tree", "4.3.0", "4.2.1")
    for relative, text in change.items():
        (tree / relative).write_text(text, encoding="utf-8")

    problems = _script().development_problems("4.3.0", tree)

    assert any(refused in problem for problem in problems), problems


def test_a_release_tree_is_not_a_development_tree(tmp_path: Path) -> None:
    tree = _release_tree(tmp_path / "tree", "4.3.0")
    script = _script()

    assert not script.developing("4.3.0", tree)
    assert script.documented_release(tree) == "4.3.0"
    assert script.version_problems("4.3.0", tree) == []
    assert script.development_problems("4.3.0", tree) == [
        "CHANGELOG.md does not lead with '# [4.3.0] - Unreleased'",
        "4.3.0 is not later than 4.3.0, the newest release CHANGELOG.md dates",
    ]


def _metadata(version: str, requires: str = "") -> str:
    return (
        f"Metadata-Version: 2.4\nName: alphalab\nVersion: {version}\n"
        f"Requires-Python: >=3.12\n{requires}\n"
    )


def _artifacts(
    folder: Path, version: str, *, requires: str = "", cached: bool = False
) -> tuple[Path, Path]:
    wheel = folder / f"alphalab-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("alphalab/__init__.py", "")
        archive.writestr("alphalab/py.typed", "")
        archive.writestr(f"alphalab-{version}.dist-info/METADATA", _metadata(version, requires))
        archive.writestr(f"alphalab-{version}.dist-info/licenses/LICENSE", "MIT")
        if cached:
            archive.writestr("alphalab/__pycache__/__init__.cpython-312.pyc", b"")
    sdist = folder / f"alphalab-{version}.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, content in (
            ("PKG-INFO", _metadata(version, requires)),
            ("LICENSE", "MIT"),
            ("pyproject.toml", ""),
            ("alphalab/py.typed", ""),
        ):
            data = content.encode()
            member = tarfile.TarInfo(f"alphalab-{version}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return sdist, wheel


def test_the_artifacts_are_this_release_s_two_and_no_others(tmp_path: Path) -> None:
    script = _script()
    _artifacts(tmp_path, "9.9.9")

    assert script.name_problems(tmp_path, "9.9.9") == []
    (tmp_path / "alphalab-3.9.0.tar.gz").write_bytes(b"")
    assert script.name_problems(tmp_path, "9.9.9") == [
        f"{tmp_path} holds other AlphaLab artifacts ['alphalab-3.9.0.tar.gz']; build into an "
        "empty one"
    ]
    assert script.name_problems(tmp_path, "9.9.8")[0].startswith(f"{tmp_path} lacks")


def test_an_archive_names_its_version_and_carries_no_requirement_or_cache(tmp_path: Path) -> None:
    script = _script()
    _artifacts(tmp_path, "9.9.9")
    assert script.archive_problems(tmp_path, "9.9.9") == []

    broken = tmp_path / "broken"
    broken.mkdir()
    _artifacts(broken, "9.9.9", requires="Requires-Dist: numpy>=2", cached=True)
    found = script.archive_problems(broken, "9.9.9")
    assert any("runtime requirements ['numpy>=2']" in problem for problem in found)
    assert any("__pycache__" in problem for problem in found)


def test_the_checksums_are_what_sha256sum_checks(tmp_path: Path) -> None:
    script = _script()
    sdist, wheel = _artifacts(tmp_path, "9.9.9")

    written = script.write_checksums(tmp_path, "9.9.9")
    assert written.name == "SHA256SUMS-9.9.9"
    expected = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted((sdist, wheel), key=lambda path: path.name)
    ]
    assert written.read_text(encoding="utf-8").splitlines() == expected


def _sarif(results: list[dict[str, object]], rules: list[dict[str, object]]) -> str:
    return json.dumps(
        {"runs": [{"tool": {"driver": {"name": "CodeQL", "rules": rules}}, "results": results}]}
    )


@pytest.mark.parametrize(
    ("level", "severity", "blocks"),
    [
        ("error", None, True),
        ("warning", "8.1", True),
        ("warning", "5.0", False),
        ("note", None, False),
    ],
)
def test_codeql_blocks_on_errors_and_high_severity_alone(
    level: str, severity: str | None, blocks: bool, tmp_path: Path
) -> None:
    script = _script()
    properties = {"security-severity": severity} if severity else {}
    rule: dict[str, object] = {
        "id": "py/rule",
        "defaultConfiguration": {"level": level},
        "properties": properties,
    }
    (tmp_path / "python.sarif").write_text(
        _sarif([{"ruleId": "py/rule", "locations": []}], [rule]), encoding="utf-8"
    )

    problems, seen = script.codeql_problems(tmp_path)
    assert len(seen) == 1
    assert bool(problems) is blocks


def test_codeql_without_an_analysis_is_a_failure(tmp_path: Path) -> None:
    problems, _ = _script().codeql_problems(tmp_path)
    assert problems == [f"no SARIF file under {tmp_path}: the analysis did not run"]


def test_the_installed_smoke_test_checks_what_an_application_relies_on() -> None:
    smoke = (ROOT / "tests" / "installed_smoke.py").read_text(encoding="utf-8")
    assert '"-m", "pip", "check"' in smoke
    assert "sys.addaudithook(_refuse_the_network)" in smoke
    assert "public_api.json" in smoke and 'importlib.metadata.requires("alphalab")' in smoke
