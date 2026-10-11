# AlphaLab v4.1.0 Release Audit

**Candidate:** branch `work/v4.1.0-release-hardening`, created from `main` at
`11d68dec7501bbfbc1c34e6a485d5b99a1250c1f` -- the commit the `v4.0.0` tag names
(the merge of PR #16). The tag was verified locally against `main` and
`origin/main` before any change, and was not created, moved or deleted.
**Role:** implement FUT-001, repair the release verification that failed after
v4.0.0 was published, resolve the stated limitations where the evidence
required it, extend the supported interpreters, and certify the candidate as
the engine an application (iluvtrade) is built on.
**Companions:** [`PRE_V4_COMPLETION_LEDGER.yaml`](PRE_V4_COMPLETION_LEDGER.yaml)
(284 entries), [ADR-0050](../ADR/0050-v4-1-a-replay-that-pauses-and-resumes-three-interpreters-and-release-verification-at-its-root.md),
[`RELEASE_RUNBOOK.md`](RELEASE_RUNBOOK.md), `scripts/mutation_v4_1.py`,
`scripts/stress_v4_1.py`, `scripts/generate_v4_1_0_snapshot_fixtures.py`, and
the integration contract [`../INTEGRATION.md`](../INTEGRATION.md).

This document records what was examined, what was found, what was changed and
with which test, what was measured, and what was **not** established. Every
figure in it was read from a command's own output in this session.

---

## 1. Verdict

**CANDIDATE READY FOR HOSTED VALIDATION.** Every local gate passes on the final
tree under CPython 3.12.15, 3.13.16 and 3.14.8 on macOS arm64 (section 9).
Nothing has run on GitHub: the branch has not been pushed, so CI on Linux,
CodeQL, the Release Preflight and -- after publication -- Release Validation are
the remaining evidence, and the runbook says how to obtain it. Two decisions are
the maintainer's (section 12): whether Windows is to be supported, and whether a
typed strategy configuration (FUT-002) waits for 5.0.

---

## 2. Starting state

| | |
| --- | --- |
| Repository | `/Users/varunkumarsingh/developer/AlphaLab`, branch `main`, working tree clean, no stash |
| `main`, `origin/main`, `v4.0.0` | all `11d68dec7501bbfbc1c34e6a485d5b99a1250c1f` |
| Earlier v4.0 branch | `work/v4.0.0-qa-hardening` at `de793f4`, merged; left as it is |
| Uncommitted work | none -- the `release_preflight.py` investigation the brief mentions had left no change in this checkout |
| Stale namespace directories | gone (the v4.0 audit's section 2); the checkout's suite runs as CI's does |
| Baseline suite | **9,325 passed**, 0 failed, `pytest -W error`, CPython 3.12.4 -- v4.0.0's own count |

---

## 3. Findings and their dispositions

Every item the brief named and every one this release found, each in one of
the five classes the brief defines: **(1)** fixed with a regression test,
**(2)** not a defect, with evidence, **(3)** an intentional boundary, **(4)**
external, **(5)** unresolved.

| ID | Severity | Finding | Class | Test |
| --- | --- | --- | --- | --- |
| FUT-001 | -- | A replay could not pause and resume | (1) delivered | `test_replay_pause_and_resume.py` (135) |
| PKG-002 | high | v4.0.0's post-publication validation handed `pip` a relative path from another directory | (1) | `test_release_paths.py` (21) |
| PKG-003 | medium | The verifier passed `PYTHONPATH` to the fresh environment, so the installed check could import a checkout | (1) | `test_release_paths.py` (1 new); found by the mutation harness's baseline |
| TST-019 | medium | The benchmark job's environment was held to nothing; a benchmark imported a test module | (1) | `test_workflow_environments.py` |
| TST-020 | medium | Release validation did not tie the tag to an approved commit | (1) | `test_release_preflight.py` (10 new) |
| REP-005 | medium | `math.pow` made a run's report host-dependent | (1) | `test_run_record_is_host_independent.py` (14) |
| PER-010 | medium | A declared retention did not reach the replay cursor's log | (1) | `test_replay_pause_and_resume.py`, `test_replay_checkpoint_complexity.py` |
| API-008 | medium | The manifest's text differed on CPython 3.14 | (1) | `test_public_api_manifest.py` (7 new) |
| TST-021 | low | Test support leaked an `HTTPError` body (3.14 `ResourceWarning`) | (1) | `test_real_execution.py` (7, now passing on 3.14) |
| TST-022 | low | A test pinned 3.12's exception type for `dataclasses.replace` | (1) | `test_instrument_classification.py` |
| DOC-011 | low | Documented commands could not be pasted and run | (1) | `test_documented_commands_parse.py` (47) |
| LIM-007 | low | Only CPython 3.12 was tested | (1) closed | `test_release_gates_are_wired.py` |
| LIM-005 | low | Certified identities per host class | (1) for a run's record; (3) for the standalone engines | as REP-005 |
| LIM-004 | low | A strategy's configuration reads back as JSON reads it | (3) stated contract; typed form FUT-002 | `test_strategy_configuration_contract.py` (37) |
| LIM-006 | low | The operating envelope is measured, not unbounded | (3) re-measured | `test_replay_checkpoint_complexity.py` (3) |
| BDY-027 | low | No document stated the supported operating systems | (3) Linux and macOS; Windows untested | `test_release_gates_are_wired.py` |
| FUT-002 | -- | A strategy configuration that reads back typed | accepted future work, the maintainer's decision | -- |
| PRF-011, PRF-012 | -- | Per-order memory; the one-asset path's cost | (3) unchanged, re-read | as recorded |
| DAT-007, DAT-008 | -- | Time-zone database; float-second instants | (3) unchanged, re-read | as recorded |
| PER-004 | -- | Persisted enum names are format | (3) extended: `ReplayStatus` and the replay event tags pinned | `test_persisted_enum_names.py` |
| LIM-001 to LIM-003 | -- | The release ADRs' limitations | (3) unchanged, re-read | as recorded |

**The sweep the brief asked for.** No `TODO`, `FIXME`, `XXX` or `HACK` in any
tracked source, test, workflow or configuration file; `NotImplementedError`
appears only in one historical docstring; the three `except ...: pass` blocks
in `broker/snapshot.py` each try an alternative decoding and then raise a named
`BrokerSnapshotDecodeError` -- not a silent fallback (class 2); no import of a
removed package; no current-state document still calls replay non-resumable or
3.12 the only tested interpreter (two v4.0 paragraphs that read as current now
say v4.1 delivered them). Nothing in class (5).

---

## 4. FUT-001: the design record

*Where progress is captured.* At a boundary between two records -- the boundary
`RunEngine.resume` already defines for a run -- before the next record is taken.
`ReplayBacktest.run` and `ReplayBacktest.resume` are one loop (`_drive`), which
asks `pause_when(ReplayProgress)` there.

*What survives a pause.* `PausedReplay`: the `RunState` exactly as it stood
after its last record, **not finalized** (an instant's slice may still be open,
because the next record may share it); the replay cursor (`ReplayState`), moved
to `PAUSED` by the cursor's own `pause` transition; and the cursor stream's
`IdStreamPosition`. Strategy memory travels inside the run snapshot through
`StrategyStateProtocol`, as it does for a backtest.

*What is fingerprinted.* The dataset is the replay's input and is supplied back
on restore, like the live objects. The envelope records its id, its length, its
first and last instants, the ids of the records either side of the pause, and a
SHA-256 over every record's canonical JSON (`replay_snapshot.dataset_digest`).
The configuration is the run snapshot's own, checked against the live objects
by PER-008's descriptions; the cursor's stream seed must be the run's seed plus
`REPLAY_CURSOR_SEED_OFFSET`.

*How the next event is identified.* By position: the cursor's `current_index`,
which must equal the records the run processed plus those it skipped (dropped
ones included), and must name, in the supplied dataset, the record the envelope
says is next.

*How duplicates and skips are prevented.* The run and the cursor must agree on
how far they have read (`PausedReplay` refuses otherwise, and so does
`restore`); the dataset must be the one read, record for record; the run's
identifier stream continues through `RunEngine.resume` and the cursor's through
`RunEngine.id_source_at` -- still the single owner of continuation -- so no
identifier is minted twice; a resumed replay is not asked to pause where it
already is, so it always moves; and the workload the tests use is built so a
duplicated or missing record, slice or fill moves an order (two instruments an
instant, a strategy whose sizes read its own memory, partial fills).

*How incompatible or corrupt state is refused.* `from_primitives` refuses
another schema, a missing field (named) and a value of the wrong type; `restore`
refuses a cursor not `PAUSED`, a run of another mode or dataset, a cursor and run
that disagree, a stream whose draws do not match its log (each lifecycle event
is one draw), a log that does not begin with `ReplayStarted` and end with
`ReplayPaused`, a clock that disagrees with the last record, another dataset in
any of the recorded respects, and live objects configured otherwise. All with
`StateDecodeError`; bytes altered in a `FileRunStateStore` are refused earlier,
by digest, with `StorageError`. A refused restore changes nothing and can be
retried with the right inputs (tested).

*How determinism is kept.* The resumed run takes the same steps the
uninterrupted one takes, with the same streams at the same positions; `pause_when`
runs with `uuid4` installed, so caller code cannot move either stream. The
result -- every order, fill, trade, step, position, cash balance, P&L figure,
strategy lifecycle event, and the `result_id` that digests the whole recorded run
-- equals the uninterrupted replay's. The cursor's lifecycle log records the
pause (`ReplayPaused`, `ReplayResumed`), which a replay that never paused does
not have; the cursor is in no result.

*How persistence contracts and migrations are kept.* No existing schema moves:
the envelope (`REPLAY_SNAPSHOT_SCHEMA = 1`, `REPLAY_SCHEMA_HISTORY`) nests the
run snapshot as `run_snapshot.capture` writes it, and the run's own history
upgrades an older nested payload (tested with v3.13.0's run payload). The first
replay payload is frozen under `tests/fixtures/snapshots/v4.1.0` and read by
every later build (and by the certificate's REP-3). The store is the existing
`RunStateStore`: atomic writes, digest-checked reads.

*What the tests establish* (`test_replay_pause_and_resume.py`, 135 tests):
every one of the workload's 25 boundaries -- before the first record, inside an
instant, before the last, after it (which finishes) -- through a payload and
fresh objects; a pause at every boundary in one chain; 24 generated pause
schedules; a one-record replay; an empty dataset refused before a replay
exists; the unseeded case (figures equal, identifiers `uuid4`); a fresh
interpreter resuming what another paused, under three hash seeds; a process
that ends abruptly (`os._exit`) after storing checkpoints, resumed from the
last; a failed checkpoint write leaving the previous one and no partial file; a
strategy state the encoder cannot write refused at capture with nothing stored;
truncated and altered stored bytes; five schema versions; 24 inconsistent
payloads, each refused by name; seven changed datasets; another commission and
another fill policy; and the frozen payload read and resumed.

---

## 5. The release verification and the workflows

**PKG-002.** `release_preflight.py` now resolves every path it is given -- the
distribution directory, the record, the interpreter -- to an absolute path and
checks it exists before anything changes directory, and `install_and_smoke`
installs only an existing file that is one of the release's two artifacts.
`tests/regression/test_release_paths.py` builds a real wheel and a real sdist --
the sdist with an in-tree PEP 517 backend so the install needs no index -- and
installs both through the script from relative paths with spaces in them, from
outside the source tree, through the function and through the command line as
`release.yml` gives it; with the fix reverted (in a scratch copy), four of its
tests fail with v4.0.0's exact error, `Could not install packages due to an
OSError: [Errno 2] No such file or directory: '.../outside/built dist/...'`.
`ci.yml` now runs the command `release.yml` runs, relative `dist` included,
on every push, so this path is no longer exercised only after publication.

**What each workflow does now.**

| Workflow | Trigger | v4.1 change |
| --- | --- | --- |
| `ci.yml` | push and PR to `main`; called by the preflight | matrix: CPython 3.12, 3.13, 3.14 x `ubuntu-latest`, `macos-latest`, `fail-fast: false`; artifacts verified by `release_preflight.py distributions dist` |
| `benchmarks.yml` | weekly, on demand; called by the preflight | matrix 3.12, 3.13, 3.14; the environment held to the benchmarks' imports by a test (TST-019) |
| `preflight.yml` | push to `work/v4.1.0-release-hardening`, on demand, labelled PR | release `4.1.0`; artifact kept 90 days for release validation's approval check |
| `release.yml` | release published | an `approval` job (tag = checkout = event commit; a passing preflight of that source as that version), then validation on 3.12, 3.13, 3.14 |
| `codeql.yml` | push and PR to `main`, weekly | unchanged |

`actionlint` 1.7.12 with ShellCheck 0.11.0 (given explicitly, and shown live on
a control copy with an unquoted variable, which it flagged): **0 findings** in
the five workflows. No workflow contains `continue-on-error`, an allowed failure
or `|| true` (`test_no_gate_is_allowed_to_fail`). The preflight's verdict job
requires every gate's result to be `success`, and a failing matrix cell fails
its reused workflow.

**The six failures the brief requires protection against.**

| Failure | Protection |
| --- | --- |
| A relative artifact path invalid after a directory change | `test_release_paths.py` (real installs); CI runs release validation's command |
| A benchmark missing `pytest` | helpers moved out of test modules; `test_workflow_environments.py` holds every benchmark's import closure to `benchmarks.yml`'s install |
| A placeholder pasted as a command | `test_documented_commands_parse.py`: placeholders refused in every shell block, indented ones included |
| A malformed multi-line continuation | the same test: a backslash before whitespace or a blank line, and `bash -n` |
| A tag selected without matching the approved commit | `release.yml`'s commit and approval steps; `test_the_tag_the_checkout_and_the_event_are_one_commit`, `test_a_tag_no_passing_preflight_validated_is_refused` (eight cases) |
| Release validation building another revision than the approved one | the validation jobs check out `needs.approval.outputs.sha` only (`test_release_validation_needs_the_approval_and_builds_only_the_approved_commit`) |

A hosted workflow cannot be run here. Each step's own shell script was run by
the suite under the variables GitHub sets (the preflight guard, the identity
step, the release tag and commit steps); what only GitHub can show -- that the
matrix runs, that the approval step reads the API as written -- is the hosted
evidence still to come.

---

## 6. The limitations, re-examined

**LIM-007 -- interpreters (closed).** The suite on CPython 3.13.16 and 3.14.8
first failed in ways 3.12 never showed: the public-API manifest's text
(API-008), an unclosed `HTTPError` in test support (TST-021), and a pinned
exception type (TST-022). Each fixed at its cause; then every gate passed on all
three (section 9). Every certified figure was identical across the three
interpreters, and so was every example's output but the four that print a random
id, a process id or CPU time.

**LIM-005 -- host independence (closed for a run's record).** Every float a run
records was traced: money is `Decimal`; the report uses `+ - * /`, `sqrt` and
`fsum`, exact under IEEE 754 and CPython, and `math.pow` for CAGR and the
geometric return. 300,000 seeded inputs: `math.pow` on this host differed from
the correctly rounded power in **409** (one or two ulps), while decimal powers at
60 and 120 digits agreed on every input checked. The power is now computed at
60 digits and rounded once (REP-005); no certified figure moved. The standalone
engines' `exp`, `log` and `pow` remain the host's (class 3): they feed no run
record, and the certificate records them rounded.

**LIM-004 -- configuration (stated contract; typed form deferred).** Only
capture reads `StrategyState.config`; nothing on the execution path does; a
run's `configuration_id` and `result_id` are the same before and after a
restore. The contradiction with `restore(capture(s)) == s` is real, and the fix
-- a typed form -- needs a pipeline schema step that would move every run's
`result_id`, which the v4.0 stability policy forbids a minor release. v4.1
states the conversion as a table (`docs/STATE_MODEL.md`) and tests each row
through the real restore path; FUT-002 records the typed form for the
maintainer's decision.

**LIM-006 -- scale (re-measured).** `stress_v4_1.py`'s `replay` scenario, a
paused replay at 2,000, 8,000 and 32,000 records:

| | 2,000 | 8,000 | 32,000 |
| --- | ---: | ---: | ---: |
| Replay, every history kept | 128.3 us/record | 130.3 | 140.3 |
| Checkpoint size | 6.41 MB (3,203.6 B/record) | 25.68 MB (3,210.1) | 103.25 MB (3,226.7) |
| Capture + serialize | 125.6 us/record | 142.4 | 147.7 |
| Restore | 79.4 us/record | 126.2 | 133.1 |
| Checkpoint, every history bounded at 500 | 2.68 MB (1,337.6 B/record) | 4.03 MB (503.9) | 8.60 MB (268.7) |

Linear with everything kept (1.01x the bytes and 1.18x the capture cost a record
at 16x the run); bounded, the checkpoint stops growing with the records and
grows only with the orders (PRF-011). Before PER-010 the bounded figure at
32,000 records was 443.9 B/record, the difference being the cursor's log. Every
resumed replay equalled the uninterrupted one. The rest of the stress program --
v4.0's, v3.13's and v3.12's scenarios -- was re-run as written (section 9).

---

## 7. The consumer contract

`tests/consumer_contract.py` is what `docs/INTEGRATION.md` says an application
may rely on, exercised through public names only: identity and version;
strategy lifecycle; backtest and replay agreeing fill for fill; cash equal to
the start less what the fills say, and equity equal to cash plus the position
at its mark; an order over the pre-trade size limit refused before execution;
pause and resume to the uninterrupted `result_id`; another dataset, another
commission, another schema and corrupt JSON refused; every older run payload
read (the documented upgrade warnings, and nothing else); and a replay paused in
one interpreter and resumed in a fresh one. `tests/installed_smoke.py` runs it
inside every installed wheel and sdist; a regression test runs it against the
tree. iluvtrade's repository is not in this workspace: the actual end-to-end
integration has **not** been run, and `docs/INTEGRATION.md` ends with the
checklist for iluvtrade's build.

---

## 8. Public API, schemas and migration

Against `history/4.0.0.json`, read canonically: **added** `alphalab.api.resume_replay`,
`alphalab.backtesting.PausedReplay` and `ReplayProgress`, and the documented
module `alphalab.backtesting.replay_snapshot` (eight names); **changed**
`alphalab.api.replay`, which gained a keyword-only `pause_when=None` with no
effect unless given; **removed** nothing. Methods the manifest does not record:
`ReplayBacktest.run(pause_when=)`, `ReplayBacktest.resume`,
`RunEngine.id_source_at`. Fourteen recorded signatures change spelling only
(API-008). 44 packages and 12 documented modules, 2,628 exports, 35 shared names.

Schemas: **no existing schema moved** (pipeline 8, run 5, and the rest as
v4.0.0); one added, `REPLAY_SNAPSHOT_SCHEMA = 1`. Every payload v3.9.0, v3.11.0,
v3.12.0, v3.13.0 and v4.1.0 froze is read (REP-3); v4.0.0's payloads are the
schemas 4.1.0 writes. The certificate's DET-1, DET-2, PAR-1, PAR-2, NUM-1 to
NUM-3, REP-1 and REP-2 evidence is **identical to v4.0.0's**: no certified
identity moved between the releases.

The CHANGELOG's "Migrating from 4.0" table has one row per behaviour change.

---

## 9. Release gates

Local, on macOS 26.6.2 arm64, in a clean mirror of the candidate (`git ls-files
-co --exclude-standard`, copied), with a fresh environment per interpreter
holding the `[dev]` extras as CI installs them (`pip install -e ".[dev]"`;
ruff 0.16.10, mypy 2.4.0, pytest 9.1.1). **Hosted CI has not run.**

| Gate | CPython 3.12.15 | CPython 3.13.16 | CPython 3.14.8 |
| --- | --- | --- | --- |
| `ruff check .` | clean | clean | clean |
| `ruff format --check .` | 1,283 files formatted | the same | the same |
| `mypy .` (strict, repository-wide) | no issues, 1,186 files | the same | the same |
| `pytest -W error` | **9,672 passed** | **9,672 passed** | **9,672 passed** |
| every example, `-W error`, `PYTHONHASHSEED` 0 and 4242 | 71 of 71 under each | 71 of 71 under each | 71 of 71 under each |
| `certify_release.py --check` | 11 of 11, matching | 11 of 11, matching | 11 of 11, matching |
| every benchmark, `-W error` | 53 of 53, 297 s | 53 of 53, 437 s | 53 of 53, 432 s |

The pytest figure is 4,858 unit, 658 integration and 4,156 regression tests,
none skipped, no warnings (v4.0.0: 9,325). The first run of the three found two
things, each fixed and the gates run again: one test file left unformatted by
an edit (`ruff format --check`), and a test pinning the release version to
`4.0.0` (`test_the_declared_version_is_the_release_version`) -- each caught by
the gate, neither a change to the engine.

**Examples.** Under each interpreter, 67 of the 71 print byte-identical output
under the two hash seeds; `12`, `13`, `45` and `48` print a random run or order
id, a process id or CPU time, as since v3.13. Against the `v4.0.0` tag's own 70
examples (seed 0): 61 byte-identical; the four above; `15`, which prints the
absolute path of the tree it runs from; and `46`, `47`, `55` and `68`, which
record the engine's version and source digest in the fingerprints and manifests
they print -- `55`'s run `result` id is unchanged. Across the three
interpreters, under each seed, the same 67 outputs are byte-identical and only
`12`, `13`, `45` and `48` differ, for the reason above.

**Stress** (`stress_v4_1.py`, every scenario, CPython 3.12.15): every scenario
passed. 10,000 assets at 1.21x the per-record cost of 400 (peak RSS 293 MB);
1,000 strategies, 1,665 identical fills either way; 100 venues, 0 mismatches;
construction at 10,000 assets OPTIMAL in 1.61 s; a 20,000-record checkpoint
chain flat (base 1.57 MB, last segment 1.61 MB); 2,658 bytes held per additional
order (PRF-011); restore per record 0.20x at 16x the run; ingestion 1.10x per row
at 16x; and the replay scenario of section 6 (on this interpreter build every
per-record figure is about 1.6x the CPython 3.12.4 run's; the ratios are the
same).

**Workflows.** actionlint 1.7.12 with ShellCheck 0.11.0: 0 findings in five
workflows; the release and preflight steps' shell scripts executed by the
suite under GitHub's variables.

**Documents.** Every shell block in every tracked Markdown file -- 39, the
runbook's included -- parses with `bash -n` and holds no placeholder or broken
continuation.

**Distributions.** The wheel and sdist are built from the candidate as CI builds
them, checked with `twine check --strict`, and verified with the command
`release.yml` runs -- `release_preflight.py distributions dist`, relative `dist`
-- under each of the three interpreters: each artifact installed alone into a
fresh environment and checked from outside the source tree by
`tests/installed_smoke.py`, which runs `pip check`, the version, the import
location, every public name, examples 11, 70 and 71 with the network refused,
and the consumer contract. The sdist's own suite is run from the unpacked
archive. The result and the SHA-256 sums are recorded beside the artifacts
(`VERIFICATION-4.1.0.txt`, `SHA256SUMS-4.1.0`), not here: this document ships
inside the sdist whose hash they record. The maintainer's publication uses the
artifacts the hosted preflight builds and records (`RELEASE_RUNBOOK.md`).

---

## 10. What was tested, and what was not

**Tested.** Everything in sections 4 to 9, on macOS arm64 under three CPython
interpreters.

**Not tested here.** Linux: no Linux host or container is available on this
machine, so the first Linux evidence for this code is the hosted CI matrix
(the v3.13 certificate's identical figures on Linux x86-64 and macOS arm64 are
the closest earlier evidence, and v4.1 removed the one operation in a run's
record that could differ). Windows: never (BDY-027). CodeQL: a hosted service.
The workflows' GitHub-only behaviour -- the matrix expansion, the reusable
workflows' outputs, the approval step reading the Actions API: verified by
actionlint and by running each step's script, not by GitHub. iluvtrade itself:
its repository is not in this workspace (section 7). Concurrency within one
process: the engine is single-threaded by design. Workloads beyond the stress
program's sizes.

---

## 11. Mutation testing

`docs/audit/scripts/mutation_v4_1.py` holds v4.0's 201 mutations and 35 new
ones (`--check`: all 236 apply exactly once). Applying is not catching: the
results below are runs -- one mutation at a time on a copy of the candidate,
the whole suite with `-x`, an unmutated baseline first.

**Run** (`mutation_v4_1.py <scratch> --worktree --workers 4 --only ...`,
CPython 3.12.15): v4.1's 35 mutations and the 6 inherited ones on files v4.1
changed (`alphalab/api.py`, `alphalab/runtime/run.py`, `certify_release.py`:
M14, V17, W12, Y57, Y58, Z15). Baseline: 9,582 passed, 90 deselected.
**41 of 41 caught**, each by a failing test, none on a crash or a timeout:

| Series | Mutations | Caught | The test that fails first, for example |
| --- | ---: | ---: | --- |
| A (FUT-001, PER-010) | 15 | 15 | A04, a pause closing the open slice early: `test_every_boundary_resumes_to_the_uninterrupted_replay[3]` |
| B (PKG-002, PKG-003) | 6 | 6 | B01, an artifact reaching pip as given: `test_pip_is_handed_a_path_that_names_the_artifact_from_where_it_runs` |
| C (TST-020) | 4 | 4 | C04, release validation proceeding on another commit: `test_the_tag_the_checkout_and_the_event_are_one_commit` |
| D (REP-005) | 1 | 1 | D01, CAGR back on `math.pow`: `test_the_power_is_the_correctly_rounded_one` |
| E (API-008) | 2 | 2 | E01, the interpreter's own spelling: `test_every_export_is_the_one_the_manifest_records` |
| F, G, H, I | 7 | 7 | H03, `continue-on-error` on the test step: `test_no_gate_is_allowed_to_fail` |
| inherited | 6 | 6 | Z15, a name leaving `alphalab.api`: the manifest test |

Two catches depend on the interpreter that runs them, and are stated: D01 is
caught where the host's `pow` is not correctly rounded for the pinned inputs (it
is not on macOS arm64; the inputs were chosen there), and E01 where the
interpreter prints the non-canonical spelling (3.12 and 3.13; CPython 3.14
already prints the canonical one). A03, the resumed run restarting its
identifier stream, was first caught by the consumer contract
(`test_the_consumer_contract_holds_against_this_tree`).

**What the baseline found.** The first attempt's unmutated baseline failed --
three `test_release_paths.py` tests, because the harness sets `PYTHONPATH` to its
copy and the verifier passed it on to the fresh environment, which then
imported the copy instead of the installed artifact. That is PKG-003: fixed,
given a regression test that fails without the fix, and a mutation (B06), and
the run above is the second attempt. The full table of 236 was not re-run: the
195 mutations on files v4.1 did not change ran in v4.0's release against a suite
that has since only grown, and `--check` shows all 236 still apply.

---

## 12. Decisions for the maintainer, and remaining risks

1. **Hosted validation** (the remaining gate): push the branch; the Release
   Preflight, CI on Linux and macOS under three interpreters, CodeQL and the
   benchmarks must pass on the exact candidate commit; then tag and publish by
   `RELEASE_RUNBOOK.md`, and Release Validation must pass. Only then is the
   release published and verified.
2. **Windows** (BDY-027): unsupported and unclaimed. Supporting it means a
   Windows CI cell with every gate passing; the library has one Windows branch
   (`persistence.durable`), and the test suite has POSIX assumptions that would
   need review (subprocess workers, file permissions, `os._exit` codes).
3. **A typed strategy configuration** (FUT-002): 5.0, or a stated 4.x exception
   that moves every `result_id`.
4. **The first Linux run of this code** is hosted CI. LIM-005's fix removed the
   only host-dependent operation in a run's record; the certificate's `--check`
   on Linux is the evidence that nothing else differs.
5. **A paused replay's checkpoint is a whole capture**, about 3.2 KB a record
   with every history kept: an application checkpointing a long replay often
   should declare a retention policy (`docs/INTEGRATION.md`).
6. **`ubuntu-latest` moves to a new image** from 2026-10-19 (v4.0 audit, section
   18): a run on or after that date meets a new glibc and time-zone database.

---

## 13. Environment

| | |
| --- | --- |
| Host | Apple M2, macOS 26.6.2 arm64 |
| Interpreters | CPython 3.12.15, 3.13.16, 3.14.8 (python-build-standalone, installed by `uv` into a scratch directory); CPython 3.12.4 (the development environment) for the baseline and the measurements of sections 4 to 6 |
| Tools | ruff 0.16.10, mypy 2.4.0, pytest 9.1.1 (the `[dev]` extras as pip resolved them); actionlint 1.7.12; ShellCheck 0.11.0 |
| Time-zone database | as the certificate records |

---

## 14. For iluvtrade

`docs/INTEGRATION.md` is the contract and ends with the build checklist: the
pinned wheel by its published hash, CPython 3.12 to 3.14 on Linux or macOS,
public names only, strategies with declared state, every run configuration
stated, `strategy_failures` read first, payloads kept by `serialize` and a
store, a replay paused with `pause_when` and resumed with the same dataset and
objects, refusals treated as refusals, retention for long runs, identities
recorded, one run per process, and `tests/consumer_contract.py` run against the
installed release before building on it.
