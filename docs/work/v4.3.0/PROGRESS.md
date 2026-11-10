# AlphaLab v4.3.0 development programme: progress register

<!-- Rendered from progress.json by docs/audit/scripts/progress_register.py. Edit progress.json and render again; never edit this file by hand. -->

A complete universal, strategy-agnostic, global quantitative research engine: the researcher brings the idea, AlphaLab gives it a deterministic, reproducible, auditable path from data to validated result. The version number is not a ceiling on the agreed scope.

| | |
| --- | --- |
| Branch | `work/v4.3.0-development` |
| Pull request | https://github.com/VarunSingh022/AlphaLab/pull/22 (Draft, opened 2026-11-10) |
| Baseline | v4.2.1 (AlphaLab 4.2.1), commit `8f33234fe64e0558b3a0146fe23e1364fc6b802f`, tree `cb7b743ae4f8e9a431590f322efc8ffd09274119` |
| Plan | AlphaLab v4.3.0 Master Development Plan (sections A-Z, AA), sha256 `b4a6775db5607a23a6125112286dac6e4a7aa129c21f52f900ba220ac448e047`, approved 2026-11-09, held by the owner, outside the repository |
| Decision record | AlphaLab v4.3.0 G0 decision register, sha256 `724a60b1c3175221dc22109e631ebf61cbef365964b2adb160035a904e287291` |

Statuses: Not started · In progress · Blocked · Implemented locally · Local validation passed · Pushed · Hosted CI passed · Accepted · Deferred by owner.

## Authority (delegated 2026-11-10)

The owner delegated implementation of the approved plan: technical and architectural decisions (the open G0 items included), commits and pushes to work/v4.3.0-development, maintaining Draft PR #22, and monitoring and fixing hosted CI, through M0-M13 and all 30 workstreams. Each significant decision is recorded with its problem, alternatives, rationale and compatibility consequences, in an ADR where it is architectural.

Binding:

- AlphaLab stays a universal, strategy-agnostic quantitative research engine.
- Global markets; no American-market default.
- The core keeps zero mandatory runtime dependencies.
- Optional numerical, statistical, ML and DL dependencies integrate separately through stable interfaces.
- A separate process alone is not a secure sandbox.
- The host or deployment environment enforces the isolation of untrusted research code.
- Numerical precision follows error analysis, not arbitrary fixed rounding.
- The no-single-runtime boundary holds.
- No silent change to a historical result_id, configuration_id, schema or persisted format.
- No breaking change for convenience: stage it, migrate it, or hold it for 5.0.

Never:

- Merging PR #22.
- Publishing a v4.3.0 GitHub Release or a PyPI distribution.
- Creating the final v4.3.0 tag.
- Changing the v4.2.1 tag or release.
- Modifying other repositories (iluvtrade, RedDesk).
- Deleting Git history or force-pushing.
- Disabling security controls or rulesets to bypass a failure.

## Current state (2026-11-10)

| | |
| --- | --- |
| Remote head | `643d947c0f4fad2cb2a149bd5f446ab53dc32545` |
| Pull request | #22, Draft, mergeable |
| Monitoring | Auto-fix for PR #22 could not be enabled: the app's permission classifier refused the switch. Checks are read once at each increment boundary instead. |
| Next | M0: the charter reconciliation and its ADR (WS-00, ADR-0056), the integration-branch model (item 21), the release record (WS-29) and the test scaffolds (WS-21); then the M0 gate. |

| Commit | Workflow | Run | Result |
| --- | --- | --- | --- |
| `dc61a75` | Continuous Integration | 42758278604 | success: all six quality-gate cells |
| `dc61a75` | CodeQL | 42758278243 | success |
| `643d947` | CodeQL | 42763156687 | success |
| `643d947` | Release Preflight | 42763156539 | all six jobs skipped by design: they run only with the release-preflight label or by dispatch (D-W1); not a pass |
| `643d947` | Continuous Integration | 42763156527 | in progress when recorded |

## Continuation log

- **2026-11-09** G0: the owner approved items 1-3 and the directions of items 23 and 24, and authorised M0. Branch work/v4.3.0-development created from 8f33234.
- **2026-11-09** M0 group 1 (WS-01) committed: 19169cb register, 06e8847 v4.2.1 fixtures, b1a2c93 reading tests, dc61a75 schedule-digest pins; full local gate green.
- **2026-11-10** The owner pushed dc61a75; Draft PR #22 opened; hosted CI (6 cells) and CodeQL green.
- **2026-11-10** M0 group 2 (DET-007, WS-02) committed: 58920fc scope isolation, 04b3e10 derived registry ids, c48f5f7 parallel probe, 643d947 register; full local gate green.
- **2026-11-10** The owner delegated autonomous implementation (register 'authority'). Claude pushed 643d947 as a fast-forward; ruleset 'protect' disabled, no rules on the branch.
- **2026-11-10** G0 item 16 decided under delegation: the plan's ADR numbers are final; plan 0054A is ADR-0054 and plan 0054B is ADR-0072.
- **2026-11-10** SEC-002 (WS-23) committed: HttpTransport refuses every scheme but http/https, and redirects to any other; SECURITY.md corrected (CON-X16).
- **2026-11-10** WS-20 High documentation corrections committed (CON-X4, CON-X1, CON-N2, CON-X14, CON-A5, CON-A15, VISION's venue transport) with a guard test.

## Milestones

| Milestone | Status | Workstreams | Exit gate (end-to-end proof) |
| --- | --- | --- | --- |
| G0 | In progress | WS-00 | §Z G0 items all decided |
| M0 | In progress | WS-01, WS-02, WS-23, WS-20, WS-29, WS-21 | v4.2.1 payloads resume to identical `result_id`s. DET-007 probes pass. `file://` refused. Certificate unchanged |
| M1 | Not started | WS-02 | S-11: cross-OS rerun REPRODUCED; a wrong schedule is refused at continuation; CR-53-1..9 |
| M2 | Not started | WS-03, WS-13, WS-06 | Published-table tests; GR-2; oracle suite; cross-host identity equality |
| M3 | Not started | WS-04, WS-06, WS-13 | 16-variant PBO from captures = from full history; capture ≤ 10 %; GR-5 |
| M4 | Not started | WS-05, WS-23, WS-19 | 48-trial ledgered experiment with failures and an insolvency counted; spawn = sequential; spy-on-import refusal |
| M5 | Not started | WS-24, WS-06 | S-13; leak mutants caught; promotion refusal |
| M6 | Not started | WS-07, WS-25 | Known-DGP recovery; MacKinnon values; S-02 screening stage |
| M7 | Not started | WS-08, WS-09, WS-20 | S-07 with a boosting adapter; counterfactual tests |
| M8 | Not started | WS-11, WS-26, WS-10 | S-09; GR-4; leg atomicity; certificate unchanged for default runs |
| M9 | Not started | WS-12 | A rebalancing backtest with CVaR, certified |
| M10 | Not started | WS-13, WS-27, WS-14, WS-28 | S-03, S-04, S-05, S-06, S-14; GR-1, GR-3, GR-6 |
| M11 | Not started | WS-15, WS-09, WS-25, WS-21 | Benchmarks; the 500-asset × 10-year run ≤ 5 minutes with the same `result_id` |
| M12 | Not started | WS-16, WS-18, WS-20 | S-08; a stress family reproduced |
| M13 | Not started | WS-19, WS-22, WS-29, WS-20, WS-01 | S-01, S-10, S-12; FUT-044 criteria; RQ-01..09 rehearsal |
| After M13 | Not started | WS-17 | Depth oracles |

- **G0:** Items 1, 2, 3 approved and the directions of items 23 and 24 approved on 2026-11-09, with M0 authorised; every other item stays gated at its milestone.
- **M0:** Group 1 (WS-01) pushed as dc61a75 and green on hosted CI; group 2 (DET-007, WS-02) validated locally. v4.3 findings such as DET-007 are recorded here, not in the ledger, until the version decision: the ledger's guard refuses an entry for a release beyond the package's (4.2.1).

## Decisions (G0 sign-off)

| # | Ref | Decision | Status | Due | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | Plan | The master development plan as the implementation blueprint | Approved | G0 | Approved on 2026-11-09 with its 30 workstreams and M0-M13 milestones. Every existing identifier, dependency and acceptance criterion is preserved. The plan may evolve on verified implementation findings; each change is recorded under plan_changes with its reason. |
| 2 | D-V1 | Universal quantitative-engine charter revision | Approved | G0; its documents in M0 | Reconcile the five documents that state the product's direction -- ROADMAP.md, docs/VISION.md, nowandfuture.md, docs/ARCHITECTURE.md and docs/SYSTEM_DESIGN.md -- around one authoritative product vision, preserving the no-single-runtime architectural boundary. The charter ADR's number waits for item 16. |
| 3 | D-V2 | Optional, separately installable research and computational dependencies | Approved | M7 | The AlphaLab core stays free of mandatory runtime dependencies; optional packages integrate through stable core interfaces (AC-38). |
| 4 | D-V3 | Boundary reopenings (plan section P.2) | Delegated (2026-11-10): decided at its milestone | Each before its milestone (M2-M10) | Each reopening gets its own ADR before its milestone. Not to be assumed. |
| 5 | D-V4 | Fill timing: NEXT_OPEN and an intraday rule in 4.3; fill_timing required in 5.0 | Delegated (2026-11-10): decided at its milestone | Before M8 | — |
| 6 | ADR-0053 | ADR-0053 revised (R53-1..8, CR-53-1..9) | Delegated (2026-11-10): decided at its milestone | Before M1 | M0's golden schedule-digest tests do not need it; the RUN_SCHEDULE_DIGEST_SCHEME constant does. |
| 7 | ADR-0054 | ADR-0054 split into 0054A and 0054B; D-54-5 revised | Delegated (2026-11-10): decided at its milestone | 0054A before M2; 0054B before M4 | — |
| 8 | ADR-0055 | Streaming capture and trace identity | Delegated (2026-11-10): decided at its milestone | Before M3 | — |
| 9 | D-54-R3 | Researcher entry point alphalab.lab | Delegated (2026-11-10): decided at its milestone | Before M4 | — |
| 10 | D-F45 | FUT-045 in M8 | Confirmed by the owner's brief | Before M8 | The implementation brief (Part 18) places FUT-045 in its agreed programme position. The owner is asked to confirm this reading. |
| 11 | D-F42 | FUT-042 opt-in in 4.3; default flip in 5.0 | Delegated (2026-11-10): decided at its milestone | Before M1 | — |
| 12 | D-F44 | FUT-044 in M13 | Confirmed by the owner's brief | Before M13 | Inclusion confirmed by the brief (Part 18). Whether it is the cut line stays the owner's decision. The cut-line clause is delegated (2026-11-10). |
| 13 | D-W1 | Release Preflight by label or dispatch at release time | Delegated (2026-11-10): decided at its milestone | Before the first release candidate | — |
| 14 | D-W2 | Release rehearsal: fake-API tests and a scratch-repository rehearsal | Delegated (2026-11-10): decided at its milestone | M13 | — |
| 15 | D-W3 | Residual UI-publish risk; release permissions; optional distributions | Delegated (2026-11-10): decided at its milestone | M13 | — |
| 16 | ADR numbering | ADR numbers 0055-0071 are provisional | Decided under delegation (2026-11-10) | When the first new ADR is drafted | The plan's ADR numbers are final, because the plan, the audit, this register and the drafts already cite them: ADR-0053 and ADR-0055 to ADR-0071 as planned. Plan 0054A (selection statistics) is ADR-0054 and plan 0054B (experiments and trial ledger) is ADR-0072, one file per number. No test requires contiguous numbers; an ADR is created when it is drafted, so numbers may land out of order. |
| 17 | Register | Progress register location and form | Applied; confirmed by delegation (2026-11-10) | M0 | The approved plan names docs/work/v4.3.0/PROGRESS.md (section U.5). This register adds the machine-readable progress.json and the brief's status vocabulary (G0 register C-03). |
| 18 | GR-8 | Windows CI cell | Delegated (2026-11-10): decided at its milestone | M11 | — |
| 19 | WS-17 | Depth and microstructure timing | Delegated (2026-11-10): decided at its milestone | Before M11 ends | — |
| 20 | Memo | Decisions carried from the 2026-11-07 memo | Partly resolved; the rest delegated (2026-11-10) | M0 and later | Each part is listed with its own status. |
| — | D-S1 | (part of item 20) | Resolved | — | Resolved by BDY-028 in v4.2.1. |
| — | D-S2 | (part of item 20) | Delegated (2026-11-10): decided at its milestone | — | Recommended: a registry runner in 4.3, removal at 5.0 (WS-23). |
| — | D-C1 | (part of item 20) | Applied; confirmed by delegation (2026-11-10) | — | The approved plan freezes v4.2.1 payloads; the generator also proves that v4.2.0 writes the same bytes, so one fixture set covers both releases. |
| — | D-C2 | (part of item 20) | Delegated (2026-11-10): decided at its milestone | — | Recommended: accept v4.2.0's F4 and F7 as v4.1.0 coverage. |
| — | D-C3 | (part of item 20) | Delegated (2026-11-10): decided at its milestone | — | Recommended: keep reading replay schema 2, which no release wrote, and document it. |
| — | D-K2 | (part of item 20) | Delegated (2026-11-10): decided at its milestone | — | Recommended: approve (WS-20). |
| — | D-K3 | (part of item 20) | Delegated (2026-11-10): decided at its milestone | — | Folded into WS-20 as a documentation task. |
| 21 | Branch model | Write the integration-branch model into CONTRIBUTING and ENGINEERING_GUIDELINES | Delegated (2026-11-10): decided at its milestone | M0 | Correcting CON-X14 does not depend on it. |
| 22 | Authority | Branch, Draft PR, commits and pushes | Superseded (2026-11-10) | M0 | Superseded by the owner's autonomous authorization: Claude pushes coherent commit groups, maintains Draft PR #22 and monitors CI; never merges, tags, publishes or force-pushes. |
| 23 | D-G0-1 | Isolation of untrusted researcher code | Approved (direction) | M4 | AlphaLab provides safe research-execution interfaces and process orchestration; the isolation of untrusted researcher code is enforced by the host or deployment environment. A separate Python process alone is never represented as a secure sandbox. |
| 24 | D-G0-2 | Numerical precision and canonical serialization policy | Approved (direction) | M2 | A documented policy: deterministic calculations first, appropriate precision preserved, rounding only where numerical error analysis justifies it, cross-platform behaviour proved by reference tests. No arbitrary fixed rounding to conceal numerical differences. |

## Plan changes

| ID | Date | Decision | Change | Reason |
| --- | --- | --- | --- | --- |
| PC-01 | 2026-11-09 | Item 17 | The progress register is docs/work/v4.3.0/progress.json, the source, rendered to PROGRESS.md by docs/audit/scripts/progress_register.py, with the brief's nine statuses in place of the plan's five (section U.5). | The brief requires a machine-readable and a human-readable register; one source rendered to the other cannot disagree with it. |
| PC-02 | 2026-11-09 | Item 24 | AC-37 ('identity-bearing statistics rounded to decimal strings, cross-host equal') is read through the approved precision policy: rounding only where numerical error analysis justifies it, never a fixed rounding that conceals a difference. The policy ADR states the rule before M2's statistics fix any identity. | The owner's direction on item 24 (2026-11-09). |

## Workstreams

| WS | Title | Milestones | Status |
| --- | --- | --- | --- |
| WS-00 | Governance, charter and decision records | G0 | In progress |
| WS-01 | Compatibility and historical-contract integrity | M0, M13 | In progress |
| WS-02 | Deterministic identity and provenance | M0, M1 | In progress |
| WS-03 | Scientific computing and numerical foundations | M2 | Not started |
| WS-04 | Streaming research-result capture | M3 | Not started |
| WS-05 | Experiment and trial ledger, executor, runner registry | M4 | Not started |
| WS-06 | Statistical research functions | M2, M3, M5 | Not started |
| WS-07 | Econometrics and time series | M6 | Not started |
| WS-08 | Research model interfaces, ML datasets, hyperparameter search | M7 | Not started |
| WS-09 | Optional distributions and backends | M7, M11 | Not started |
| WS-10 | Strategy authoring and safe extensibility | M8 | Not started |
| WS-11 | Execution realism: fills, partial fills, latency, plan+queue, combos | M8 | Not started |
| WS-12 | Portfolio construction and rebalancing | M9 | Not started |
| WS-13 | Global conventions, reference data and historical universes | M2, M3, M10 | Not started |
| WS-14 | Fixed income and rates | M10 | Not started |
| WS-15 | Performance and scale | M11 | Not started |
| WS-16 | ML/DL/RL research environments | M12 | Not started |
| WS-17 | Market microstructure (depth, L2/L3) | After M13 | Not started |
| WS-18 | Robustness, stress and regime analysis | M12 | Not started |
| WS-19 | Researcher entry points, composition, reproducibility APIs | M4, M13 | Not started |
| WS-20 | Documentation, guides and examples | M0, M7, M12, M13 | In progress |
| WS-21 | Platform, CI and test infrastructure | M0, M11 | Not started |
| WS-22 | Live venue controls (FUT-044) | M13 | Not started |
| WS-23 | Security and trust boundaries | M0, M4 | In progress |
| WS-24 | Validation over actual backtests | M5 | Not started |
| WS-25 | Factor and feature research | M6, M11 | Not started |
| WS-26 | Per-venue execution (FUT-045) | M8 | Not started |
| WS-27 | FX and multi-currency correctness | M10 | Not started |
| WS-28 | Options and futures depth | M10 | Not started |
| WS-29 | Release engineering and v4.3.0 artifacts | M0, M13 | Not started |

### WS-00: Governance, charter and decision records

- [In progress] G0: Record the G0 approvals in this register
- [Not started] G0: Charter reconciliation of the five direction documents and its ADR (number per item 16)

### WS-01: Compatibility and historical-contract integrity

- [Hosted CI passed] M0: v4.2.1 payloads of every durable kind frozen, generated from the tag, read by REP-3
- [Hosted CI passed] M0: Reading tests: every v4.2.1 payload decoded and re-captured byte for byte; paused replays and a checkpoint chain resumed to the result_id v4.2.1 computed
- [Hosted CI passed] M0: Golden RunSchedule.digest per entry kind and the persisted kind values pinned
- [Not started] M0: RUN_SCHEDULE_DIGEST_SCHEME constant (waits for item 6, ADR-0053)

Evidence:

- tests/fixtures/snapshots/v4.2.1: 42 files written by docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py from the v4.2.1 tag; two runs and a run against v4.2.0 wrote identical bytes (D-C1).
- Every durable kind of STATE_MODEL's table and its three envelopes: 11 base payloads, 11 paused replays with the 9 registries they held, a checkpoint chain, the run-state store and an evidence store.
- The generator held v4.2.1 to its contract before writing: each paused replay and the chain, read back and resumed with fresh objects, finished with the uninterrupted run's result_id (expected/identities.json).
- Certificate REP-3 reads v4.2.1 (read_v4.2.1: 31); the source digest and every other check unchanged.
- tests/regression/test_schema_upgrades_v4_2_1.py (57 tests, under 2 s): every payload read and written back byte for byte; every file the bytes v4.2.1 wrote; each of the 11 paused replays and the checkpoint chain resumed to the result_id v4.2.1 computed, one of them in a fresh process under another hash seed; each registry's digest the one its replay recorded; the stored manifest the one this build makes.
- tests/regression/test_schedule_digest_pins.py (34 tests): a golden RunSchedule.digest for each of the 20 entry kinds, computed identically by v4.2.1 and v4.2.0; the persisted kind values; the formula as documented; each frozen replay's recorded schedule digest. Seen to fail on a renamed kind value and on two changes to the formula.
- Pushed by the owner as dc61a75 on work/v4.3.0-development (2026-11-10); Draft PR #22 opened; its hosted CI and CodeQL runs are recorded once they finish.
- Hosted CI passed on dc61a75 (PR #22): Continuous Integration run 42758278604, all six quality-gate cells (ubuntu-latest and macos-latest, Python 3.12, 3.13, 3.14); CodeQL run 42758278243.

### WS-02: Deterministic identity and provenance

- [Local validation passed] M0: DET-007: isolated identifier scopes, with nested, parallel and repeated probes
- [Local validation passed] M0: DET-007 and D-54-7: the experiment tracker's run ids and the research cluster's job ids derived (uuid5 over a canonical key), never drawn from the ambient stream

Evidence:

- DET-007: id_scope(None) installs uuid4 instead of inheriting the stream around it (alphalab/common/ids.py). tests/regression/test_unseeded_scopes_are_isolated.py (12 tests): for the backtest, replay and session, a nested unseeded run draws nothing from the outer scope, the outer scope mints what it would have without it (also in fresh interpreters), two nested unseeded runs differ, and a seeded run is its seed's anywhere. 11 of the 12 fail on the parent dc61a75. Certificate: source digest re-made; no evidence moved.
- Registries: experiment_tracking.start_run and cloud_research.submit_research_job derive their ids (schemes alphalab.experiment_run.v1 and alphalab.research_job.v1). tests/regression/test_registries_derive_their_ids.py (11 tests; 5 fail on the parent 58920fc): zero draws for a run start, one draw (the queue's event stamp) for a submission, scope independence, fresh-interpreter equality, every declared field in the key. An unseeded lifecycle now reproduces byte for byte; test_lifecycle_serialization held the old limit and now holds the guarantee. Certificate: source digest re-made; no evidence moved.
- Parallel probe: three unseeded runs in worker threads given the scope's context (copy_context) leave the outer stream untouched; fails on dc61a75 (before the fix), stable over 15 repeated runs.
- Local gate on c48f5f7 (clean clone, CPython 3.12.4): ruff, format, strict mypy, pytest -W error (10,987 passed), every example under -W error, certificate check, register check, build, twine --strict, fresh-environment install and smoke of the wheel and the sdist. New tests also pass on CPython 3.13.18 and 3.14.10, where the certificate check matches.

### WS-20: Documentation, guides and examples

- [Implemented locally] M0: High documentation corrections: CON-X4, CON-X1, CON-N2, CON-X16, CON-X14, CON-A5, CON-A15

Evidence:

- CON-X16 corrected with SEC-002: SECURITY.md states the HTTP transport's scheme and redirect rule.
- High corrections against the code: CON-X4 (COMPOSING: a refused amend keeps the order working), CON-N2 (nowandfuture: all ten hooks delivered), CON-X1 (capability matrix 2.5(10) and examples/_strategy_evidence.py: the position check is correct since v3.10), CON-X14 (CONTRIBUTING: no vendor adapters as first contributions), CON-A5 (ARCHITECTURE and nowandfuture name cloud_research.resolve_task), CON-A15 (ARCHITECTURE's status section gains v4.2), CON-X16 (with SEC-002), and VISION's current state (no HMAC venue transport; eleven snapshot owners). tests/regression/test_corrected_statements_stay_corrected.py pins each false statement absent and the status section and security policy present.

### WS-21: Platform, CI and test infrastructure

- [Not started] M0: v4.3 mutation and stress scaffolds

### WS-23: Security and trust boundaries

- [Implemented locally] M0: SEC-002: HttpTransport opens http:// and https:// only, and follows a redirect only to one of them

Evidence:

- alphalab/marketdata/transport.py: _require_http refuses every other scheme and a URL with no host before anything is opened; _HttpRedirectsOnly refuses a redirect to any other scheme before urllib's own rules (which follow ftp://). tests/unit/marketdata/test_http_transport.py (15 tests against a loopback provider; 11 fail on the parent): seven refused URL forms with no opener used, a hostless URL, a local file never returned, http with its query, a followed http redirect, refused ftp/data/file redirects, and an https request carrying tls_context(). Certificate: source digest re-made; no evidence moved.

### WS-29: Release engineering and v4.3.0 artifacts

- [Not started] M0: VERIFICATION record names the commit and tree; reproducible build

## Gaps

48 open, 1 closed, 4 discarded.

| Gap | Problem | Severity | Status | Workstreams | Milestone |
| --- | --- | --- | --- | --- | --- |
| GAP-01 | No experiment or trial ledger | C/P0 | Open | WS-05 | M4 |
| GAP-02 | Validation not over backtests | C/P0 | Open | WS-24 | M5 |
| GAP-03 | DET-007 ambient ids | H/P0 | Open: scopes isolated and registry ids derived; awaiting hosted CI | WS-02 | M0 |
| GAP-04 | FUT-046 schedule provenance (P1, P2) | H/P0 | Open | WS-02 | M1 |
| GAP-05 | SEC-001 dotted-path resolution | H/P0 | Open | WS-23, WS-05 | M4 |
| GAP-06 | SEC-002 `HttpTransport` any scheme | M/P0 | Open: refused in code and documented; closes when M0 is accepted | WS-23 | M0 |
| GAP-07 | v4.2.x formats not frozen | H/P0 | Open: acceptance met (REP-3 reads v4.2.1; hosted CI passed on dc61a75); closes when M0 is accepted | WS-01 | M0 |
| GAP-08 | Return extraction; → retention trims equity | H/P0 | Open (scope = ADR-0055) | WS-04 | M3 |
| GAP-09 | Selection statistics missing | C/P1 | Open | WS-06 | M2 (bridge M3) |
| GAP-10 | No numerical foundation | C/P1 | Open | WS-03 | M2 |
| GAP-11 | No econometrics | H/P1 | Open | WS-07 | M6 |
| GAP-12 | No model protocol | C/P1 | Open | WS-08 | M7 |
| GAP-13 | ML dataset not PIT | H/P1 | Open | WS-08 | M7 |
| GAP-14 | Throughput | H/P2 | Open | WS-15 | M11 |
| GAP-15 | Charter conflict | H/P0 | Open | WS-00 | G0 |
| GAP-16 | No reference-data packs | H/P2 | Open (D-V3) | WS-13 | M10 |
| GAP-17 | One execution model per run | H/P1 | Open | WS-26 | M8 |
| GAP-18 | No run-level or intraday next-open | L-M/P1 | Open | WS-11 | M8 |
| GAP-19 | Latency costs nothing | M/P2 | Open | WS-11 | M8 |
| GAP-20 | Optimiser not on the path | M/P1 | Open (D-V3) | WS-12 | M9 |
| GAP-21 | DL and RL not end to end | M/P2 | Open | WS-16 | M12 |
| GAP-22 | No depth, L2 or ns time | M/P3 | Open (after M13; ns in 5.0) | WS-17 | After M13 |
| GAP-23 | FX booked as units | M/P2 | Open | WS-27 | M10 |
| GAP-24 | Fixed income foundation only | H/P2 | Open (D-V3) | WS-14 | M10 |
| GAP-25 | No volatility models | M/P3 → P2 (needed for S-04) | Open (D-V3) | WS-28 | M10 |
| GAP-26 | No combo orders | M/P2 | Open | WS-11 | M8 |
| GAP-27 | No hypothesis record | L/P1 | Open | WS-05 | M4 |
| GAP-28 | Researcher boilerplate | M/P2 | Open | WS-19 | M13 |
| GAP-29 | REL-001 v4.2.1 assets | — | Closed (D-V5): historical limitation | — | — |
| GAP-30 | Stale documentation (CON-A/N/V/X) | M/P1 | Open: the seven High items corrected and guarded in M0; Medium and Low items follow by M13 | WS-20 | M0 (High) + every M |
| GAP-31 | Three orchestration layers unbound | M/P2 | Open | WS-05 | M4 |
| GAP-32 | No vectorised screening | M/P3 | Open (D-V3 via L4) | WS-09, WS-15 | M11 |
| GAP-33 | Non-normal distributions missing | M/P1 | Open | WS-03 | M2 |
| GAP-34 | 9,248-line pipeline module | L/P3 | Open | WS-15 | M11 |
| GAP-35 | Windows untested | M/P2 | Open | WS-21 | M11 [OD] |
| GAP-36 | US-centric day counts | H/P1 | Open | WS-13 | M2 |
| GAP-37 | Closed construction objectives; one solver | M/P2 | Open (D-V3) | WS-12 | M9 |
| GAP-38 | Remainders cancelled; size-less fills | M/P2 | Open | WS-11 | M8 |
| GAP-39 | (candidate) silent zero fill | — | Discarded | — | — |
| GAP-40 | (candidate) no history accessor | — | Discarded | — | — |
| GAP-41 | (candidate) no fill or order hooks | — | Discarded | — | — |
| GAP-42 | (candidate) no multi-currency trading | — | Discarded | — | — |
| GAP-43 | No PIT universe membership | H/P1 | Open | WS-13 | M3 |
| GAP-44 | Plan + queue mutually exclusive | M/P2 | Open | WS-11 | M8 |
| GAP-45 | Child fills don't reach `on_fill` | L-M/P2 | Open | WS-10, WS-11 | M8 |
| GAP-46 | RL environment invents limits | M/P2 | Open | WS-16 | M12 |
| GAP-47 | No incremental feature evaluator | M/P2 | Open | WS-25 | M11 |
| GAP-48 | → Expanded: duplicate authorities in `ml`: `ml.linalg` *and* a second walk-forward splitter | L/P3 | Open | WS-03, WS-08 | M7 |
| GAP-49 | No ML/DL/RL/distributed guides | M/P2 | Open | WS-20 | M7, M12 |
| GAP-50 | No lot-relief methods (FIFO, LIFO, specific identification, pooling); proportional cost basis only | M/P2 | New (audit §F "to verify": verified absent) | WS-13 | M10 |
| GAP-51 | Missing instrument classes: swaps (IRS, CDS, TRS), non-FX forwards, funds/ETFs as a booking class, inflation-linked, repos, securities lending as an instrument, structured products | M/P2 | New (audit §F row "Missing classes") | WS-14, WS-28, WS-13 | M10 |
| GAP-52 | No native pegged or trailing orders (trailing is strategy logic by design) | L/P3 | New (audit §H) | WS-20, WS-26 | After M13 |
| GAP-53 | Optimistic default execution choices (same-event, frictionless, fill-on-touch, static full fill), all stated in `execution_assumptions` | M/P1 | New (audit §H grouping) | WS-11 | M8 |

## ADR programme

Numbers are final (G0 item 16): the plan's, with plan 0054A as ADR-0054 and plan 0054B as ADR-0072.

| ADR | Subject | Workstream | Needed | Status |
| --- | --- | --- | --- | --- |
| ADR-0053 | Run and input provenance | WS-02 | G0 | Not drafted in the repository |
| ADR-0054 | Selection statistics (pure) (plan 0054A) | WS-06 | G0 | Not drafted in the repository |
| ADR-0072 | Experiments and trial ledger (plan 0054B) | WS-05 | Before M4 | Not drafted in the repository |
| ADR-0055 | Streaming research capture, extractor identity, trace identity, retention | WS-04 | Before M3 | Not drafted in the repository |
| ADR-0056 | Product charter (D-V1) | WS-00 | G0 | Not drafted in the repository |
| ADR-0057 | Optional distributions and backend protocols (D-V2); includes the screening evaluator (B9) | WS-09 | G0 | Not drafted in the repository |
| ADR-0058 | Numerical foundation, record-path host independence, estimation identity (reopens OFE-007, OFE-010 under identity) | WS-03 | Before M2 | Not drafted in the repository |
| ADR-0059 | Historical universes and reference-data packs (reopens BDY-013 "permanently") | WS-13 | Before M3 | Not drafted in the repository |
| ADR-0060 | Fill timing `NEXT_OPEN`, intraday opening, `RemainderPolicy`, latency pricing (D-V4) | WS-11 | Before M8 | Not drafted in the repository |
| ADR-0061 | Per-venue execution (FUT-045) and plan+queue composition | WS-26 | Before M8 | Not drafted in the repository |
| ADR-0062 | Combo and multi-leg intents (N16) | WS-11 | Before M8 | Not drafted in the repository |
| ADR-0063 | Rebalancing bridge and certified additional solvers (reopens ADR-0043's non-goal and §19) | WS-12 | Before M9 | Not drafted in the repository |
| ADR-0064 | Research model protocol and artifacts (N8) | WS-08 | Before M7 | Not drafted in the repository |
| ADR-0065 | Fixed income and rates (reopens BDY-014) | WS-14 | Before M10 | Not drafted in the repository |
| ADR-0066 | Volatility models (reopens BDY-015) | WS-28 | Before M10 | Not drafted in the repository |
| ADR-0067 | FX instruments and value dates (N15) | WS-27 | Before M10 | Not drafted in the repository |
| ADR-0068 | Researcher entry module `alphalab.lab` (D-54-R3, N20) | WS-19 | Before M13 (its documented-module part before M4) | Not drafted in the repository |
| ADR-0069 | RL and DL episode driver (N24) | WS-16 | Before M12 | Not drafted in the repository |
| ADR-0070 | Depth protocol and microstructure (reopens BDY-003; ns time stays 5.0) | WS-17 | After M13 | Not drafted in the repository |
| ADR-0071 | Release integrity and guarded publication | WS-29 | G0 | Not drafted in the repository |
