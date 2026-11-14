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

## Current state (2026-11-14)

| | |
| --- | --- |
| Remote head | `01cfc3c46903e5e378a4743c4a048f421ce5928b` |
| Pull request | #22, Draft, mergeable |
| Monitoring | Auto-fix for PR #22 could not be enabled: the app's permission classifier refused the switch. Checks are read once at each increment boundary instead. |
| Next | M2 (WS-03, WS-06 math, WS-13 N21): ADR-0058 (the numerical foundation and the precision policy, G0 item 24's direction) and ADR-0054 (plan 0054A, selection statistics); then common.distributions and special functions, the eigensystem and SVD with vectors, Cholesky as one authority, optimisers and quadrature, the Estimate type; the selection statistics; global day counts. |

| Commit | Workflow | Run | Result |
| --- | --- | --- | --- |
| `dc61a75` | Continuous Integration | 42758278604 | success: all six quality-gate cells |
| `dc61a75` | CodeQL | 42758278243 | success |
| `643d947` | Continuous Integration | 42763156527 | success: all six quality-gate cells |
| `643d947` | CodeQL | 42763156687 | success |
| `7574b87` | Continuous Integration | 42764459914 | success: all six quality-gate cells |
| `7574b87` | CodeQL | 42764459942 | success |
| `b48bfc3` | Continuous Integration | 42765682898 | success: all six quality-gate cells |
| `b48bfc3` | CodeQL | 42765683356 | success |
| `1593381` | Continuous Integration | 42813534367 | success: all six quality-gate cells, the distributions step holding the artifacts to the checkout's commit and rebuilding them |
| `1593381` | CodeQL | 42813534369 | success |
| `1508966` | Continuous Integration | 42818861870 | success: all six quality-gate cells, carrying M0's close (7dacb33 to 1508966: the version state, the ledger, the N04 wiring test) |
| `1508966` | CodeQL | 42818862101 | success |
| `01cfc3c` | Continuous Integration | 43560463598 | success: all six quality-gate cells, S-11's pinned identities held on Linux and macOS |
| `01cfc3c` | CodeQL | 43560463619 | success |
| `every push` | Release Preflight | 43560463601 | skipped by design on every push: it runs only with the release-preflight label or by dispatch (D-W1); not a pass |

## Continuation log

- **2026-11-09** G0: the owner approved items 1-3 and the directions of items 23 and 24, and authorised M0. Branch work/v4.3.0-development created from 8f33234.
- **2026-11-09** M0 group 1 (WS-01) committed: 19169cb register, 06e8847 v4.2.1 fixtures, b1a2c93 reading tests, dc61a75 schedule-digest pins; full local gate green.
- **2026-11-10** The owner pushed dc61a75; Draft PR #22 opened; hosted CI (6 cells) and CodeQL green.
- **2026-11-10** M0 group 2 (DET-007, WS-02) committed: 58920fc scope isolation, 04b3e10 derived registry ids, c48f5f7 parallel probe, 643d947 register; full local gate green.
- **2026-11-10** The owner delegated autonomous implementation (register 'authority'). Claude pushed 643d947 as a fast-forward; ruleset 'protect' disabled, no rules on the branch.
- **2026-11-10** G0 item 16 decided under delegation: the plan's ADR numbers are final; plan 0054A is ADR-0054 and plan 0054B is ADR-0072.
- **2026-11-10** SEC-002 (WS-23) committed: HttpTransport refuses every scheme but http/https, and redirects to any other; SECURITY.md corrected (CON-X16).
- **2026-11-10** WS-20 High documentation corrections committed (CON-X4, CON-X1, CON-N2, CON-X14, CON-A5, CON-A15, VISION's venue transport) with a guard test.
- **2026-11-10** Charter (WS-00) committed: ADR-0056 accepted, the five direction documents reconciled, the integration-branch model written (item 21), and the charter guard added.
- **2026-11-10** WS-29 M0 item committed: the verification record names the commit and tree, artifacts are held to the commit's bytes, and the build is shown reproducible.
- **2026-11-10** M0 checkpoint 2 pushed (d2426ef..1593381); hosted CI green on 1593381, the first hosted run of the commit check and rebuild. The mutation run's baseline found the distributions path-ordering defect, fixed in 7dacb33. The register records the technical decisions taken under the delegated authority (TD-01 to TD-04, M0's).
- **2026-11-10** The development-version state (TD-05, PC-04): the package is 4.3.0 while 4.3.0 is developed, the release gate refuses the tree, and the release-facing documents describe 4.2.1 until the release commit.
- **2026-11-10** M0's six findings recorded in the completion ledger under v4.3.0: DET-007, SEC-002, DOC-014, TST-025, TST-026 and PKG-004.
- **2026-11-10** M0 closed locally: the mutation series ran (23 of 24 caught; the N04 survivor's gap closed in 31324fd), the stress scenario passed, and the full gate passed on 31324fd. The M0 commits after 1593381 are pushed next, then M1 begins with ADR-0053 revision 3.
- **2026-11-10** M1 begun: ADR-0053 revision 3 accepted under G0 item 6's delegation (TD-06 records where it departs from R53-1's letter); its three known limitations classified in the ledger (LIM-009, FUT-047, FUT-042).
- **2026-11-10** M1's first increment built: the schedule binding and vocabulary, checkpoint schema 3, run provenance; ADR-0053's limitations in the ledger (LIM-009, FUT-047).
- **2026-11-14** M1's second increment built: manifest provenance (D-53-3) and the opt-in identity version 2; D-F42 decided under delegation (G0 item 11); TD-07 recorded; FUT-046 delivered. The scratch toolchain was rebuilt after macOS's /tmp cleaning removed parts of the old one.
- **2026-11-14** S-11 built: a fresh-interpreter rerun reproduces example 72's run, provenance and version-2 manifest against pinned identities; rerun_from_manifest follows the original's identity version and provenance (TD-08 recorded). M1's mutation series P01-P14 written (271 mutations, each applying once).
- **2026-11-14** M2 begun: ADR-0058 (the numerical foundation and the precision policy, G0 item 24's direction; TD-09) and ADR-0054 (plan 0054A, selection statistics; TD-10) accepted; their limitations in the ledger (LIM-010, LIM-011, and LIM-005's class).
- **2026-11-14** M1 closed on hosted CI: 01cfc3c green on all six cells (CI run 43560463598), S-11 reproduced across Linux and macOS, and M1's mutation series P01-P14 all caught. The ADR-0054 draft, deleted from scratch by macOS's /tmp cleaning, was recovered from its writing session's transcript at its recorded digest (083b725b...).
- **2026-11-14** M2's first module: common.distributions with the special functions, 186 oracle tests; ADR-0058 section 2 restated to the implemented line between caller tolerances and stated constants.
- **2026-11-14** NUM-017 fixed: the normal CDF keeps its lower tail (it returned 0 below about -8.3).

## Milestones

| Milestone | Status | Workstreams | Exit gate (end-to-end proof) |
| --- | --- | --- | --- |
| G0 | In progress | WS-00 | §Z G0 items all decided |
| M0 | Hosted CI passed | WS-01, WS-02, WS-23, WS-20, WS-29, WS-21 | v4.2.1 payloads resume to identical `result_id`s. DET-007 probes pass. `file://` refused. Certificate unchanged |
| M1 | Hosted CI passed | WS-02 | S-11: cross-OS rerun REPRODUCED; a wrong schedule is refused at continuation; CR-53-1..9 |
| M2 | In progress | WS-03, WS-13, WS-06 | Published-table tests; GR-2; oracle suite; cross-host identity equality |
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

- **G0:** Items 1-3 and the directions of 23-24 approved by the owner; the charter accepted as ADR-0056; the rest delegated on 2026-11-10 and decided at their milestones (PC-03).
- **M0:** M0's findings are in the completion ledger under v4.3.0 (DET-007, SEC-002, DOC-014, TST-025, TST-026, PKG-004), which the development-version state (TD-05) made possible: the ledger's guard refuses an entry for a release beyond the package's.
- **M1:** Decided under delegation at M1: ADR-0053 revision 3 (G0 item 6), D-F42 (item 11); TD-06 to TD-08.

## Decisions (G0 sign-off)

| # | Ref | Decision | Status | Due | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | Plan | The master development plan as the implementation blueprint | Approved | G0 | Approved on 2026-11-09 with its 30 workstreams and M0-M13 milestones. Every existing identifier, dependency and acceptance criterion is preserved. The plan may evolve on verified implementation findings; each change is recorded under plan_changes with its reason. |
| 2 | D-V1 | Universal quantitative-engine charter revision | Approved | G0; its documents in M0 | Reconcile the five documents that state the product's direction -- ROADMAP.md, docs/VISION.md, nowandfuture.md, docs/ARCHITECTURE.md and docs/SYSTEM_DESIGN.md -- around one authoritative product vision, preserving the no-single-runtime architectural boundary. The charter ADR's number waits for item 16. Delivered as ADR-0056 (accepted 2026-11-10). |
| 3 | D-V2 | Optional, separately installable research and computational dependencies | Approved | M7 | The AlphaLab core stays free of mandatory runtime dependencies; optional packages integrate through stable core interfaces (AC-38). |
| 4 | D-V3 | Boundary reopenings (plan section P.2) | Delegated (2026-11-10): decided at its milestone | Each before its milestone (M2-M10) | Each reopening gets its own ADR before its milestone. Not to be assumed. |
| 5 | D-V4 | Fill timing: NEXT_OPEN and an intraday rule in 4.3; fill_timing required in 5.0 | Delegated (2026-11-10): decided at its milestone | Before M8 | — |
| 6 | ADR-0053 | ADR-0053 revised (R53-1..8, CR-53-1..9) | Decided under delegation (2026-11-10) | Before M1 | ADR-0053 revision 3 accepted: the draft's option C-e with R53-1 to R53-8 applied and CR-53-1 to CR-53-9 held, departing from R53-1's letter where TD-06 records. Implemented in M1. |
| 7 | ADR-0054 | ADR-0054 split into 0054A and 0054B; D-54-5 revised | Delegated (2026-11-10): decided at its milestone | 0054A before M2; 0054B before M4 | 0054A accepted as ADR-0054 on 2026-11-14 (TD-10), applying R54-8, D-54-2, D-54-4 and D-54-12; 0054B, the experiment and trial ledger, is ADR-0072, before M4. |
| 8 | ADR-0055 | Streaming capture and trace identity | Delegated (2026-11-10): decided at its milestone | Before M3 | — |
| 9 | D-54-R3 | Researcher entry point alphalab.lab | Delegated (2026-11-10): decided at its milestone | Before M4 | — |
| 10 | D-F45 | FUT-045 in M8 | Confirmed by the owner's brief | Before M8 | The implementation brief (Part 18) places FUT-045 in its agreed programme position. The owner is asked to confirm this reading. |
| 11 | D-F42 | FUT-042 opt-in in 4.3; default flip in 5.0 | Decided under delegation (2026-11-14) | Before M1 | FUT-042 opt-in in 4.3: ADR-0052 accepted as option C, manifest_for_run(identity_version=2) records the registry and cites the run's provenance; run provenance records the registry by default. The default flip stays FUT-042, for 5.0. |
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
| 21 | Branch model | Write the integration-branch model into CONTRIBUTING and ENGINEERING_GUIDELINES | Decided under delegation (2026-11-10) | M0 | Written into CONTRIBUTING (Pull Requests) and ENGINEERING_GUIDELINES: an approved programme is delivered on one long-lived integration branch through one Draft PR, every commit one logical change that leaves every gate green; merging and releasing stay the maintainer's decision. |
| 22 | Authority | Branch, Draft PR, commits and pushes | Superseded (2026-11-10) | M0 | Superseded by the owner's autonomous authorization: Claude pushes coherent commit groups, maintains Draft PR #22 and monitors CI; never merges, tags, publishes or force-pushes. |
| 23 | D-G0-1 | Isolation of untrusted researcher code | Approved (direction) | M4 | AlphaLab provides safe research-execution interfaces and process orchestration; the isolation of untrusted researcher code is enforced by the host or deployment environment. A separate Python process alone is never represented as a secure sandbox. |
| 24 | D-G0-2 | Numerical precision and canonical serialization policy | Approved (direction) | M2 | A documented policy: deterministic calculations first, appropriate precision preserved, rounding only where numerical error analysis justifies it, cross-platform behaviour proved by reference tests. No arbitrary fixed rounding to conceal numerical differences. The policy is ADR-0058 (accepted 2026-11-14): identity-bearing values are computed on exact or correctly rounded paths only, every value is recorded at full precision, and nothing is rounded to make two hosts agree (TD-09). |

## Technical decisions (delegated)

Taken under the authority the owner delegated on 2026-11-10; each is the programme's to revisit, and none reopens a G0 decision.

### TD-01: An unseeded identifier scope is isolated (DET-007) (WS-02, 2026-11-10)

- **Problem:** id_scope(None) returned a null context, so a run started without a seed inside a seeded scope drew its identifiers from that scope's stream: the enclosing run's later identifiers depended on whether, and how much, a nested run had drawn.
- **Alternatives:** (a) Keep inheriting and document it. (b) An unseeded scope installs its own uuid4 source. (c) An unseeded scope derives a child stream from the enclosing one.
- **Decision:** (b): id_scope(None) installs use_id_source(id_source(None)), a uuid4 source of its own.
- **Rationale:** A scope must draw nothing from the scope around it. (c) still advances or depends on the outer stream; (a) leaves a seeded run's identifiers hostage to unrelated code.
- **Compatibility:** A seeded run's identifiers are unchanged, and the enclosing scope now mints exactly what it would have without the nested run. An unseeded run's identifiers were never reproducible. No recorded result_id, configuration_id, schema or format moves; the certificate's evidence is unchanged.
- **Recorded in:** CHANGELOG [4.3.0] Fixed; the ledger, once the package version admits v4.3.0 entries.
- **Tests:** tests/regression/test_unseeded_scopes_are_isolated.py (backtest, replay and session drivers; nested, repeated, fresh-interpreter and parallel copy_context probes); mutation K01.

### TD-02: Registries derive their identifiers (DET-007, plan D-54-7) (WS-02, 2026-11-10)

- **Problem:** experiment_tracking.start_run and cloud_research.submit_research_job drew their identifiers from the ambient stream, so starting a run or queuing a job inside a seeded backtest moved that backtest's stream, and neither identifier could be recomputed from what was declared.
- **Alternatives:** (a) A dedicated seeded stream per registry. (b) uuid5 over a canonical, scheme-tagged key of the declaration and its place in the registry (ADR-0016's pattern). (c) A content hash of the declaration alone.
- **Decision:** (b), with schemes alphalab.experiment_run.v1 and alphalab.research_job.v1. A run's key holds its place (runs already held), name, canonical parameters, timestamp, parent and canonical tags. A job's key holds the cluster, its submission count, type, task path, priority and instant, and not its kwargs, which may be any objects with no canonical text.
- **Rationale:** A derived identifier draws nothing and is recomputable anywhere. (c) collides when a declaration is repeated, and (a) still depends on the order in which unrelated registries are used.
- **Compatibility:** New runs and jobs get UUID strings as before, now derived; identifiers already recorded are read as written. No recorded result_id, configuration_id, schema or format moves.
- **Recorded in:** CHANGELOG [4.3.0] Fixed; the ledger, once the package version admits v4.3.0 entries.
- **Tests:** tests/regression/test_registries_derive_their_ids.py (12 tests); stress scenario registries (stress_v4_3.py); mutations K02 to K05.

### TD-03: The HTTP transport opens http and https only (SEC-002) (WS-23, 2026-11-10)

- **Problem:** HttpTransport.get handed any URL to urllib, which opens file:// (a local file read) and follows a redirect to ftp://.
- **Alternatives:** (a) Document the risk. (b) Allow http and https, refuse a URL with no host, and check every redirect's scheme before urllib's own rules. (c) Allow https only.
- **Decision:** (b), with https requests carrying tls_context().
- **Rationale:** (a) leaves the file read open. (c) refuses loopback and plain-http providers that the transport legitimately serves.
- **Compatibility:** A caller passing another scheme now gets an error before anything is opened. Reading another scheme was never a stated capability (SECURITY.md stated TLS only), so nothing documented changes.
- **Recorded in:** CHANGELOG [4.3.0] Security; SECURITY.md; the ledger, once the package version admits v4.3.0 entries.
- **Tests:** tests/unit/marketdata/test_http_transport.py (15 tests against a loopback provider); mutations L01 to L04.

### TD-04: Release artifacts held to their commit, and the build shown reproducible (WS-29, 2026-11-10)

- **Problem:** The verification record named no commit, and nothing showed that the artifacts held the tagged source or that another builder could produce them (plan W.1, RQ-04, RQ-07).
- **Alternatives:** (a) Record the commit only. (b) Also hold every archived file to the commit's object store. (c) (b), plus a rebuild from git archive of the commit, compared byte for byte.
- **Decision:** (c). The default source is the checkout's HEAD. Outside a checkout the command fails rather than skip the check, after every path is checked. --no-source, for downloaded assets, records that the source was not checked, and no workflow may pass it.
- **Rationale:** RQ-07 compares published assets with a rebuild, which needs byte reproducibility; a fresh rebuild of v4.2.1 was already byte-identical (W.1).
- **Compatibility:** The command line gains --commit and --no-source. The workflows' commands are unchanged (their checkouts are git), and hosted CI passed with the check on 1593381.
- **Recorded in:** CHANGELOG [4.3.0] Added; ENGINEERING_GUIDELINES (Distributions); RELEASE_RUNBOOK step 7. ADR-0071 (M13) will cite it.
- **Tests:** tests/regression/test_release_correspondence.py (14 tests on repositories made in the test); mutations N01 to N05.

### TD-05: A release is developed under its own version (WS-29, 2026-11-10)

- **Problem:** From M1 the programme changes the public API and records v4.3 findings, and the records that take them are keyed to the package version: the manifest and its history copy, the certificate, and the ledger guard. Since v4.2.1 the suite also held every commit to the release state. At 4.2.1, an API change would have rewritten history/4.2.1.json, the record of what 4.2.1 shipped, and a 4.3 ledger entry fails the guard.
- **Alternatives:** (a) Stay at 4.2.1 and bump at the end, with gates red or records wrong meanwhile. (b) A PEP 440 development version, 4.3.0.dev0; every gate, artifact name and tag check assumes MAJOR.MINOR.PATCH. (c) 4.3.0 everywhere at once; the README and INTEGRATION would name a current release, and download URLs, that do not exist until publication. (d) 4.3.0 in an explicit development state.
- **Decision:** (d). The package and its records are 4.3.0, and the CHANGELOG leads with '# [4.3.0] - Unreleased'. release_preflight.development_problems (run by the suite) holds the tree to everything a release is held to but that heading's date, and the documents a reader installs from describe 4.2.1, the newest dated release, until the release commit. The release gate (release_preflight.py version) is unchanged and refuses the tree for the undated heading.
- **Rationale:** Every commit stays green and truthful, and the gate that matters is untouched. No document ever names a release that cannot be installed, and no line calls 4.3.0 unreleased (DOC-013). The release commit is one explicit step that the suite verifies.
- **Compatibility:** No identity moves: the engine keeps its version out of every identity, and the certificate moved only its release labels, its source digest and API-1's digest of the manifest file. running_engine() and dataset provenance record 4.3.0 for runs this tree makes, which is true. Preflight.yml expects 4.3.0, and its push route names work/v4.3.0-release; the development branch never triggers it (D-W1).
- **Recorded in:** CHANGELOG [4.3.0] Changed; ENGINEERING_GUIDELINES (Pull Requests); RELEASE_RUNBOOK (the release commit); docs/api/history/README.md; plan change PC-04.
- **Tests:** tests/regression/test_release_preflight.py (development-tree, incoherent-tree and release-tree cases; the state-aware identity test); test_release_facing_versions.py and test_version_markers_agree.py, held to the documented release. Shown on a scratch clone with the version alone bumped: 29 version-keyed tests failed there, and no identity, fixture or evidence test.

### TD-06: ADR-0053 revision 3: where a continuation is held to its schedule (WS-02, 2026-11-10)

- **Problem:** R53-1 asks for a supplied schedule digest on run_snapshot.restore and restore_checkpoints, as FUT-041 does for the registry. A bare run snapshot records no binding, so the parameter would have nothing to check there, and a caller could believe a continuation was held to its schedule when it was not. A bare digest also drops the scheme it was computed under.
- **Alternatives:** (a) Add schedule_digest to both restores, as written; on a bare snapshot it checks nothing. (b) Hold a chain by its own envelope (checkpoint schema 3), and a bare snapshot by the cursor given the binding its provenance records; pass a ScheduleBinding, not a digest. (c) Put the binding into the run snapshot; this moves identity, which is option B, 5.0.
- **Decision:** (b): ScheduleCursor.start(expected=), checkpoint(schedule=) and restore_checkpoints(schedule_binding=) take a ScheduleBinding, and run_snapshot.restore gains nothing. VERIFIED needs a result whose own delivery record is read; a payload's or a chain's check answers DECLARED. ScheduleBinding and ScheduleVerification live in alphalab.runtime, beside RegistryVerification, since a checkpoint (runtime) records a binding and runtime may not import backtesting.
- **Rationale:** Every check named is one that actually happens, the scheme is compared with the digest, and no package edge is added.
- **Compatibility:** Additive only: keyword-only parameters defaulting to None on checkpoint, restore_checkpoints and ScheduleCursor.start. Version-2 chains are byte-identical and restore as before. No identity moves.
- **Recorded in:** docs/ADR/0053-run-and-input-provenance.md (Departures from the plan's text); CHANGELOG [4.3.0].
- **Tests:** tests/integration/test_run_provenance.py (25 tests).

### TD-07: Manifest provenance: what identity version 2 records, and where the unattested finding belongs (WS-02, 2026-11-14)

- **Problem:** Three questions were left open. (1) D-53-3 asks for 'unchanged behaviour plus a finding' when no provenance is given, but a version-1 manifest can never cite provenance, so the finding would land on every manifest ever made, changing every assessment. (2) ADR-0052's migration step refused instruments=False under version 2, and instruments defaults to False, so a refusal cannot be told apart from the default. (3) Provenance carries the result id, an output; comparing it as an input would report a rerun that did not reproduce as one whose inputs differ.
- **Alternatives:** (1) The finding on every manifest without provenance; only under version 2; or none. (2) Refuse instruments=False under version 2, or record the registry under version 2 whatever instruments says. (3) Compare provenance_id between original and rerun, or compare only the inputs it binds -- the schedule's and the registry's digests, which are already compared.
- **Decision:** (1) The finding only under version 2, the contract that asks for provenance. (2) Version 2 records the registry the run held whatever instruments says. (3) provenance_id is not an input; a version-2 rerun of a version-1 original is compared by version 1's rules, and a detail line says so.
- **Rationale:** Version-1 manifests, their assessments and the certificate's evidence stay exactly as they were (CR-53-7, CR-53-8). A version-2 producer is told what it did not attest, and an outcome names what actually differed.
- **Compatibility:** Version 1 is unchanged in identity, verification, gaps and findings; version 2 is opt-in. ReproducibilityManifest, canonical_manifest_key and derive_manifest_id gain trailing fields and parameters with defaults, rendered only when given. A version-1 manifest is also stored as v4.2.1 stored it: ReproducibilityManifest projects without the two new fields while they hold their defaults, because an evidence store refuses another value under an identity it holds (the frozen v4.2.1 evidence store's test found the first draft writing them).
- **Recorded in:** docs/ADR/0053-run-and-input-provenance.md section 6; docs/ADR/0052 (status: accepted, option C, opt-in); CHANGELOG [4.3.0].
- **Tests:** tests/integration/test_manifest_provenance.py (12 tests); tests/regression/test_schema_upgrades_v4_2_1.py::test_the_evidence_store_reads_back_verified_and_this_build_computes_the_same.

### TD-08: Provenance refuses a binding of no schedule for a run that delivered observations (WS-02, 2026-11-14)

- **Problem:** The delivery-record rule (REP-009) checks observations only when a schedule is named, because a manifest naming none for such a run has never been refused. Read the same way, forged provenance binding no schedule verified NONE_GIVEN for a run that delivered observations.
- **Alternatives:** (a) Tighten the shared rule, which refuses a manifest_for_run call that succeeds today, a change in what a valid call computes. (b) A stricter rule for provenance alone, which is new API. (c) Leave it, and let NONE_GIVEN mean less than it says.
- **Decision:** (b): run_provenance and verify_provenance refuse a binding of no schedule for a run whose record shows delivered observations; manifests keep the 4.x rule.
- **Rationale:** The vocabulary must never claim more than the run's record allows, and an existing call's result must not change in a minor release.
- **Compatibility:** No existing call changes; provenance is new in v4.3.
- **Recorded in:** alphalab/backtesting/provenance.py (_provenance_disagreement); CHANGELOG [4.3.0].
- **Tests:** tests/integration/test_provenance_end_to_end.py::test_forged_provenance_the_runs_own_record_contradicts_is_never_verified.

### TD-09: ADR-0058: host independence by construction, not by rounding (WS-03, 2026-11-14)

- **Problem:** Plan section H.1 proposed recording identity-bearing statistics as decimal strings rounded to twelve significant digits, so hosts whose libm differs in the last bit would agree. The owner's direction for G0 item 24 forbids arbitrary fixed rounding that conceals numerical differences, and rounding cannot make two hosts agree anyway: values a last bit apart that straddle a rounding boundary round to different strings at any number of digits.
- **Alternatives:** (a) Twelve significant digits, as H.1 proposed. (b) Fewer digits, chosen per statistic, with the same boundary failure. (c) Three classes of result -- exact, correctly rounded, host libm -- with identities allowed over the first two only, every value recorded at full precision, class-3 values compared by tolerances derived from error analysis, and host independence proved by pinned reference tests on Linux and macOS.
- **Decision:** (c), with the transcendental an identity needs computed on an exact or correctly rounded path instead, such as the stationary bootstrap's block lengths by integer comparison of hash-counter draws.
- **Rationale:** It follows item 24's direction exactly, extends what REP-005 already does for run records, and makes cross-host equality a property a test proves rather than an outcome a rounding hopes for.
- **Compatibility:** No existing identity or figure moves; LIM-005 stays a stated limitation, now a named class with a rule.
- **Recorded in:** docs/ADR/0058-numerical-foundation-host-independence-and-the-identity-of-an-estimate.md; plan change PC-02's reading of AC-37.
- **Tests:** Each M2 module's pinned reference tests on CI's Linux and macOS cells (built in M2).

### TD-10: ADR-0054: where the selection statistics live, and the bounds their required arguments take (WS-06, 2026-11-14)

- **Problem:** R54-8 makes CSCV's S and the bootstrap's block length required and bounded, without saying how; the draft placed the statistics in research but left their numerical class and their bounds open.
- **Alternatives:** (a) Defaults for S, q and B, which the draft's review refused. (b) Required arguments, unbounded. (c) Required arguments with stated bounds: S even, 2 <= S <= T and S <= 20 (C(20, 10) = 184,756 combinations), trimming of T declared; q >= 1, B >= 1 and the seed required; the adjusted PSR's lag count required.
- **Decision:** (c), with the statistics pure in alphalab.research over common only, bootstrap positions from a hash-counter stream (class 1), p-values as exact fractions, RC, SPA and PBO class 2, and PSR, DSR, MinTRL and the adjusted PSR class 3, never in an identity.
- **Rationale:** A bound makes a required argument's cost and meaning explicit; S above 20 makes CSCV's combinations impractical in pure Python, and nothing about the method needs more.
- **Compatibility:** Additive: new modules and names; the existing PSR, DSR, expected maximum Sharpe and multiple-testing corrections unchanged.
- **Recorded in:** docs/ADR/0054-selection-statistics-measuring-a-search-over-backtests.md.
- **Tests:** The oracle suite built in M2 (exact-enumeration CSCV, closed forms, published examples).

## Plan changes

| ID | Date | Decision | Change | Reason |
| --- | --- | --- | --- | --- |
| PC-01 | 2026-11-09 | Item 17 | The progress register is docs/work/v4.3.0/progress.json, the source, rendered to PROGRESS.md by docs/audit/scripts/progress_register.py, with the brief's nine statuses in place of the plan's five (section U.5). | The brief requires a machine-readable and a human-readable register; one source rendered to the other cannot disagree with it. |
| PC-02 | 2026-11-09 | Item 24 | AC-37 ('identity-bearing statistics rounded to decimal strings, cross-host equal') is read through the approved precision policy: rounding only where numerical error analysis justifies it, never a fixed rounding that conceals a difference. The policy ADR states the rule before M2's statistics fix any identity. | The owner's direction on item 24 (2026-11-09). |
| PC-03 | 2026-11-10 | Delegated authority | G0's ADR deliverables other than the charter are written before the milestone that first needs each, as the plan's own ADR table already dates most of them: ADR-0053 before M1, ADR-0054 before M2, ADR-0055 before M3, ADR-0057 before the first optional distribution (M7), ADR-0071 before the release machinery (M13). M0 proceeded on the owner's authorisation of 2026-11-09. | An ADR accepted long before its first implementation is decided on less evidence; each is decided with the code that tests it. |
| PC-04 | 2026-11-10 | Delegated authority (TD-05) | The package version moves to 4.3.0 before M1, in a development state the release gate refuses: the CHANGELOG leads with '# [4.3.0] - Unreleased', the package and its records are 4.3.0's, and the release-facing documents describe 4.2.1. The release commit at M13 dates the heading and moves those documents (W.3 runbook, before step 1). | The plan did not say when the version moves, and M1 is the first milestone to change the public API. Since v4.2.1 the suite held every commit to the release state, so no commit after it could have passed before the version moved. |

## Workstreams

| WS | Title | Milestones | Status |
| --- | --- | --- | --- |
| WS-00 | Governance, charter and decision records | G0 | In progress |
| WS-01 | Compatibility and historical-contract integrity | M0, M13 | In progress |
| WS-02 | Deterministic identity and provenance | M0, M1 | Hosted CI passed |
| WS-03 | Scientific computing and numerical foundations | M2 | In progress |
| WS-04 | Streaming research-result capture | M3 | Not started |
| WS-05 | Experiment and trial ledger, executor, runner registry | M4 | Not started |
| WS-06 | Statistical research functions | M2, M3, M5 | In progress |
| WS-07 | Econometrics and time series | M6 | Not started |
| WS-08 | Research model interfaces, ML datasets, hyperparameter search | M7 | Not started |
| WS-09 | Optional distributions and backends | M7, M11 | Not started |
| WS-10 | Strategy authoring and safe extensibility | M8 | Not started |
| WS-11 | Execution realism: fills, partial fills, latency, plan+queue, combos | M8 | Not started |
| WS-12 | Portfolio construction and rebalancing | M9 | Not started |
| WS-13 | Global conventions, reference data and historical universes | M2, M3, M10 | In progress |
| WS-14 | Fixed income and rates | M10 | Not started |
| WS-15 | Performance and scale | M11 | Not started |
| WS-16 | ML/DL/RL research environments | M12 | Not started |
| WS-17 | Market microstructure (depth, L2/L3) | After M13 | Not started |
| WS-18 | Robustness, stress and regime analysis | M12 | Not started |
| WS-19 | Researcher entry points, composition, reproducibility APIs | M4, M13 | Not started |
| WS-20 | Documentation, guides and examples | M0, M7, M12, M13 | In progress |
| WS-21 | Platform, CI and test infrastructure | M0, M11 | In progress |
| WS-22 | Live venue controls (FUT-044) | M13 | Not started |
| WS-23 | Security and trust boundaries | M0, M4 | In progress |
| WS-24 | Validation over actual backtests | M5 | Not started |
| WS-25 | Factor and feature research | M6, M11 | Not started |
| WS-26 | Per-venue execution (FUT-045) | M8 | Not started |
| WS-27 | FX and multi-currency correctness | M10 | Not started |
| WS-28 | Options and futures depth | M10 | Not started |
| WS-29 | Release engineering and v4.3.0 artifacts | M0, M13 | In progress |

### WS-00: Governance, charter and decision records

- [Hosted CI passed] G0: Record the G0 approvals in this register
- [Hosted CI passed] G0: Charter reconciliation of the five direction documents and its ADR (ADR-0056), with the integration-branch model (item 21)

Evidence:

- ADR-0056 (accepted 2026-11-10): the objective, the stable contract, growing capability, boundaries reopened only by their own ADR under six binding principles, and the bridge-or-library rule. ROADMAP's long-term vision and boundary preamble, VISION's statement and long-term direction, nowandfuture sections 14, 17 and 19, ARCHITECTURE's reading note and SYSTEM_DESIGN (CON-X2) cite it. CONTRIBUTING and ENGINEERING_GUIDELINES state the integration-branch model. tests/regression/test_charter_is_stated_consistently.py: each document cites the charter, none repeats a retired claim, the no-single-runtime boundary is still stated, the principles bind.
- Hosted CI passed on b48bfc3 (CI run 42765682898, six cells; CodeQL 42765683356).

### WS-01: Compatibility and historical-contract integrity

- [Hosted CI passed] M0: v4.2.1 payloads of every durable kind frozen, generated from the tag, read by REP-3
- [Hosted CI passed] M0: Reading tests: every v4.2.1 payload decoded and re-captured byte for byte; paused replays and a checkpoint chain resumed to the result_id v4.2.1 computed
- [Hosted CI passed] M0: Golden RunSchedule.digest per entry kind and the persisted kind values pinned
- [Local validation passed] M0: RUN_SCHEDULE_DIGEST_SCHEME constant (waits for item 6, ADR-0053)

Evidence:

- tests/fixtures/snapshots/v4.2.1: 42 files written by docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py from the v4.2.1 tag; two runs and a run against v4.2.0 wrote identical bytes (D-C1).
- Every durable kind of STATE_MODEL's table and its three envelopes: 11 base payloads, 11 paused replays with the 9 registries they held, a checkpoint chain, the run-state store and an evidence store.
- The generator held v4.2.1 to its contract before writing: each paused replay and the chain, read back and resumed with fresh objects, finished with the uninterrupted run's result_id (expected/identities.json).
- Certificate REP-3 reads v4.2.1 (read_v4.2.1: 31); the source digest and every other check unchanged.
- tests/regression/test_schema_upgrades_v4_2_1.py (57 tests, under 2 s): every payload read and written back byte for byte; every file the bytes v4.2.1 wrote; each of the 11 paused replays and the checkpoint chain resumed to the result_id v4.2.1 computed, one of them in a fresh process under another hash seed; each registry's digest the one its replay recorded; the stored manifest the one this build makes.
- tests/regression/test_schedule_digest_pins.py (34 tests): a golden RunSchedule.digest for each of the 20 entry kinds, computed identically by v4.2.1 and v4.2.0; the persisted kind values; the formula as documented; each frozen replay's recorded schedule digest. Seen to fail on a renamed kind value and on two changes to the formula.
- Pushed by the owner as dc61a75 on work/v4.3.0-development (2026-11-10); Draft PR #22 opened; its hosted CI and CodeQL runs are recorded once they finish.
- Hosted CI passed on dc61a75 (PR #22): Continuous Integration run 42758278604, all six quality-gate cells (ubuntu-latest and macos-latest, Python 3.12, 3.13, 3.14); CodeQL run 42758278243.
- RUN_SCHEDULE_DIGEST_SCHEME = 'alphalab.run_schedule.v1' (M1, ADR-0053 section 1) names the formula M0's golden digests pin, and is never rendered into a digest.

### WS-02: Deterministic identity and provenance

- [Hosted CI passed] M0: DET-007: isolated identifier scopes, with nested, parallel and repeated probes
- [Hosted CI passed] M0: DET-007 and D-54-7: the experiment tracker's run ids and the research cluster's job ids derived (uuid5 over a canonical key), never drawn from the ambient stream
- [Hosted CI passed] M1: ADR-0053 revision 3 accepted (R53-1 to R53-8, CR-53-1 to CR-53-9; TD-06)
- [Hosted CI passed] M1: ScheduleBinding and the verification vocabulary (VERIFIED, DECLARED, UNRECORDED, NONE_GIVEN); RunSchedule.binding, ScheduleCursor.binding
- [Hosted CI passed] M1: A continuation held to its schedule: ScheduleCursor.start(expected=), checkpoint schema 3 written only when given, restore_checkpoints(schedule_binding=)
- [Hosted CI passed] M1: Run provenance: RunProvenance, run_provenance, payload_provenance, verify_provenance, filed as evidence (RUN_PROVENANCE_SCHEMA 1)
- [Hosted CI passed] M1: manifest_for_run(provenance=) with the D-53-3 refusals, and the opt-in identity version 2 (ADR-0052 option C, FUT-042)
- [Hosted CI passed] M1: S-11 end to end: a rerun in a fresh process REPRODUCED against pinned identities on Linux and macOS; a wrong schedule refused at continuation
- [Hosted CI passed] M1: CR-53-9: M1's mutation series P01-P14 run, every mutation caught or its survivor explained

Evidence:

- DET-007: id_scope(None) installs uuid4 instead of inheriting the stream around it (alphalab/common/ids.py). tests/regression/test_unseeded_scopes_are_isolated.py (12 tests): for the backtest, replay and session, a nested unseeded run draws nothing from the outer scope, the outer scope mints what it would have without it (also in fresh interpreters), two nested unseeded runs differ, and a seeded run is its seed's anywhere. 11 of the 12 fail on the parent dc61a75. Certificate: source digest re-made; no evidence moved.
- Registries: experiment_tracking.start_run and cloud_research.submit_research_job derive their ids (schemes alphalab.experiment_run.v1 and alphalab.research_job.v1). tests/regression/test_registries_derive_their_ids.py (11 tests; 5 fail on the parent 58920fc): zero draws for a run start, one draw (the queue's event stamp) for a submission, scope independence, fresh-interpreter equality, every declared field in the key. An unseeded lifecycle now reproduces byte for byte; test_lifecycle_serialization held the old limit and now holds the guarantee. Certificate: source digest re-made; no evidence moved.
- Parallel probe: three unseeded runs in worker threads given the scope's context (copy_context) leave the outer stream untouched; fails on dc61a75 (before the fix), stable over 15 repeated runs.
- Local gate on c48f5f7 (clean clone, CPython 3.12.4): ruff, format, strict mypy, pytest -W error (10,987 passed), every example under -W error, certificate check, register check, build, twine --strict, fresh-environment install and smoke of the wheel and the sdist. New tests also pass on CPython 3.13.18 and 3.14.10, where the certificate check matches.
- Hosted CI passed on 643d947 (CI run 42763156527, six cells; CodeQL 42763156687), carrying 58920fc, 04b3e10 and c48f5f7.
- M1, first increment: alphalab/runtime/schedule_check.py, alphalab/backtesting/provenance.py, ScheduleCursor.start(expected=), checkpoint schema 3. tests/integration/test_run_provenance.py (25 tests): chains checkpointed after 1, 3 and 4 records continue under their schedule to the uninterrupted result_id; another schedule, none, and two bindings in one chain are refused; an unbound chain is UNRECORDED; provenance is the result's id, draws nothing, refuses another schedule, run, registry and an edit; a payload's provenance is DECLARED; round trip and evidence filing. The public API grew by 13 names (2,767 exports); checkpoint and restore_checkpoints each gained one keyword-only parameter defaulting to None (CR-53-2). Certificate: the source digest and API-1's manifest digest and count moved; no DET, REP or PAR evidence did (CR-53-8). Suite: 11,097 passed once the two schema-constant inventories admitted RUN_PROVENANCE_SCHEMA.
- M1, second increment: manifest_for_run(provenance=, identity_version=) in alphalab/lifecycle/reproducibility.py; the delivery-record rule (REP-009) is now one function, backtesting.provenance.delivery_disagreement, used by both manifests and provenance, its messages unchanged. tests/integration/test_manifest_provenance.py (12 tests, on example 72's registry-holding run): under version 1 a manifest made with provenance is the manifest made without it; provenance of another run, another schedule, none named, one named where none, and another registry are refused; version 2 records the registry and cites the provenance, verifies, and has a new identity by design; a version-2 manifest lacking its registry is a gap; an unknown version is refused and does not verify; a version-2 rerun of a version-1 original REPRODUCED by version 1's rules; the unattested finding under version 2 alone; a version-1 manifest stored as v4.2.1 stored it, which the frozen v4.2.1 evidence store holds. The full gate's first run on this increment found the draft writing the two new fields into every stored manifest -- a value an evidence store holding v4.2.1's copy would refuse -- and the projection was fixed before the push. Ledger: FUT-046 implemented (v4.3.0) with its criteria amended (D-53-7); FUT-042 restated as the default flip for 5.0; ADR-0052 accepted (option C, opt-in). Certificate: only the source digest and API-1 moved, so no version-1 manifest identity in the certificate's evidence moved (CR-53-7).
- S-11, M1's exit gate (tests/integration/test_provenance_end_to_end.py, 6 tests): example 72's run, its provenance and a version-2 manifest citing it are made; a fresh interpreter under another hash seed reproduces all three; the result_id 0e3be7cb... and provenance_id 218292b0... are pinned, so CI's Linux and macOS cells each hold the host to them; rerun_from_manifest REPRODUCED the run under the same contract, its manifest version 2 and citing the rerun's own provenance; a continuation under a wrong schedule is refused by the cursor and by verify_provenance; forged provenance binding a schedule the run's own record contradicts -- one without its last delivery, or none -- is never VERIFIED. Provenance now refuses a binding of no schedule for a run that delivered observations, a rule a manifest cannot adopt in 4.x (TD-08).
- CR-53-9, M1's mutation series (docs/audit/scripts/mutation_v4_3.py, P01-P14, run on 01cfc3c's tree with three workers): the unmutated baseline passed, 11,025 tests with the 90 clock-reading tests deselected, and all 14 mutations were caught, each by the test written for it -- among them P01 and P02 (the binding not checked), P03 (the scheme rendered into the digest, caught by M0's golden pins), P04 (VERIFIED without the run's record, caught by the forgery test), P11 (an unbound checkpoint written as version 3) and P13 (a rerun under version 1).
- Hosted CI passed on 01cfc3c (CI run 43560463598): all six cells -- ubuntu-latest and macos-latest under CPython 3.12, 3.13 and 3.14 -- ran S-11 against its pinned result_id 0e3be7cb... and provenance_id 218292b0..., so the run and its provenance reproduce across both operating systems; CodeQL 43560463619 green.

### WS-03: Scientific computing and numerical foundations

- [Local validation passed] M2: ADR-0058 accepted: one authority, required tolerances, three numerical classes, precision by error analysis, the estimate's identity
- [Local validation passed] M2: common.distributions: special functions (log-gamma, incomplete gamma and beta) and the t, chi-squared, F, gamma, beta, binomial and Poisson distributions
- [Not started] M2: common.linalg: symmetric eigensystem and thin SVD with vectors, pseudo-inverse; Cholesky as one authority
- [Not started] M2: common.optimize and common.quadrature, with required tolerances and ConvergenceError
- [Not started] M2: The Estimate type and its identity
- [Not started] M2: One authority: ml.linalg and ml's splits delegate (GAP-48)

Evidence:

- alphalab/common/distributions.py: Student's t, chi-squared, F, gamma, beta, binomial and Poisson, each with CDF, survival function and quantile, beside the normal; log-gamma, log-beta and the regularised incomplete gamma and beta functions; ConvergenceError. Survival functions computed directly; quantiles by Newton kept in a bisection bracket, stopping on a converged step or a narrow bracket. tests/unit/common/test_distributions.py (186 tests): exact oracles -- finite closed forms in 80-digit Decimal (t and chi-squared for even degrees of freedom, the Erlang and Poisson sums) and exact Fraction (binomial, integer-parameter beta, F through it) -- held within a tolerance from each formula's error bound (the log-gamma prefix's terms, ADR-0058 section 4); inverse identities against the exact CDFs; published tables at the digits they print; deep tails; refusals; ConvergenceError at the limit. Writing it found a remembered t quantile wrong in its 11th digit, a quantile search that could stall once converged (fixed), the existing normal_cdf's lost lower tail (NUM-017), and that the test's own decimal precision had leaked into the suite's shared context (fixed with local contexts). Whole suite: 11,301 passed.
- NUM-017 fixed: normal_cdf's lower half by erfc, its upper half the standard library's value for value; within 1e-13 of an independent continued-fraction oracle to -37 (tests/regression/test_normal_cdf_keeps_its_lower_tail.py, 17 tests); the certificate's evidence unchanged, only its source digest moved. Whole suite 11,318 passed.

### WS-06: Statistical research functions

- [Local validation passed] M2: ADR-0054 accepted (plan 0054A)
- [Not started] M2: TrialMatrix and the stationary bootstrap (hash-counter positions)
- [Not started] M2: White's Reality Check and Hansen's SPA
- [Not started] M2: PBO by CSCV, with an exact-enumeration oracle
- [Not started] M2: MinTRL and the autocorrelation-adjusted PSR

### WS-13: Global conventions, reference data and historical universes

- [Not started] M2: Global day counts (N21)

### WS-20: Documentation, guides and examples

- [Hosted CI passed] M0: High documentation corrections: CON-X4, CON-X1, CON-N2, CON-X16, CON-X14, CON-A5, CON-A15

Evidence:

- CON-X16 corrected with SEC-002: SECURITY.md states the HTTP transport's scheme and redirect rule.
- High corrections against the code: CON-X4 (COMPOSING: a refused amend keeps the order working), CON-N2 (nowandfuture: all ten hooks delivered), CON-X1 (capability matrix 2.5(10) and examples/_strategy_evidence.py: the position check is correct since v3.10), CON-X14 (CONTRIBUTING: no vendor adapters as first contributions), CON-A5 (ARCHITECTURE and nowandfuture name cloud_research.resolve_task), CON-A15 (ARCHITECTURE's status section gains v4.2), CON-X16 (with SEC-002), and VISION's current state (no HMAC venue transport; eleven snapshot owners). tests/regression/test_corrected_statements_stay_corrected.py pins each false statement absent and the status section and security policy present.
- Hosted CI passed on b48bfc3 (CI run 42765682898, six cells), carrying 144ad78.

### WS-21: Platform, CI and test infrastructure

- [Hosted CI passed] M0: v4.3 mutation and stress scaffolds, and M0's series

Evidence:

- docs/audit/scripts/mutation_v4_3.py (40b0d0a): v4.1's 236 mutations, with X15, A04 and G02 re-targeted to code v4.2.0 restructured, and M0's series J01-J04 (payloads and schedule digest), K01-K05 (DET-007 and the registries), L01-L04 (SEC-002), N01-N05 (release correspondence) and O01-O03 (corrected statements and the charter).
- M0's run (24 mutations, three workers, from 7dacb33's tree): the unmutated baseline passed, 10,968 tests with the 90 clock-reading tests deselected, and 23 of the 24 were caught. N04, the rebuild never called by distributions(), survived; the test that closes it (31324fd) fails under N04. Results in the session's scratch record, summarised here.
- The run's first attempt was refused at its baseline: its scratch copies are not git checkouts, which exposed the distributions path-ordering defect, fixed in 7dacb33.
- docs/audit/scripts/stress_v4_3.py (1593381): v4.1's scenarios and M0's registries scenario. 10,000 and 100,000 experiment-run starts in one seeded scope: 12.2 and 12.1 microseconds of CPU a start, every id distinct, the scope's stream unmoved, a cost ratio of 0.99 at ten times the tracker.
- Hosted CI passed on 1593381 (CI run 42813534367): both scripts held to ruff, format and strict mypy with the rest of the tree.

### WS-23: Security and trust boundaries

- [Hosted CI passed] M0: SEC-002: HttpTransport opens http:// and https:// only, and follows a redirect only to one of them

Evidence:

- alphalab/marketdata/transport.py: _require_http refuses every other scheme and a URL with no host before anything is opened; _HttpRedirectsOnly refuses a redirect to any other scheme before urllib's own rules (which follow ftp://). tests/unit/marketdata/test_http_transport.py (15 tests against a loopback provider; 11 fail on the parent): seven refused URL forms with no opener used, a hostless URL, a local file never returned, http with its query, a followed http redirect, refused ftp/data/file redirects, and an https request carrying tls_context(). Certificate: source digest re-made; no evidence moved.
- Hosted CI passed on 7574b87 (CI run 42764459914, six cells; CodeQL 42764459942).

### WS-29: Release engineering and v4.3.0 artifacts

- [Hosted CI passed] M0: VERIFICATION record names the commit and tree; reproducible build
- [Hosted CI passed] M0: The development-version state: the package is 4.3.0 while 4.3.0 is developed; the release gate refuses the tree; release-facing documents describe the published release (TD-05, PC-04)

Evidence:

- release_preflight.py distributions: commit_identity, commit_files (from the object store), correspondence_problems (wheel package exactly the commit's; every sdist file but PKG-INFO the commit's), reproduction_problems (both artifacts rebuilt from git archive of the commit in a fresh directory, same bytes); the record names the commit and tree; an unresolvable commit fails before anything is installed. Real build of b48bfc3: 627 package and 1,528 source files correspond, rebuild byte-identical, installs smoke-tested. tests/regression/test_release_correspondence.py (11 tests on repositories made in the test, no network).
- Development-version state (TD-05): release_preflight.developing, documented_release and development_problems. The version moved to 4.3.0; the CHANGELOG leads with '# [4.3.0] - Unreleased'; public_api.json was regenerated, with no export added, removed or rebound, and copied to docs/api/history/4.3.0.json; the certificate was re-made, moving only its release labels, its source digest and API-1's manifest digest. Preflight.yml expects 4.3.0. With the version alone bumped on a scratch clone, 29 version-keyed tests failed and nothing else: no identity, fixture or evidence test.
- The v4.3 mutation run's scratch copies are not git checkouts, and there 'distributions dist' reported the missing checkout before the missing directory. Fixed in 7dacb33: the commit is resolved after every path is checked, and both refusals are tested outside a checkout.
- Hosted CI passed on 1593381 (CI run 42813534367, six cells): CI's distributions step resolved the checkout's commit, held both artifacts to it and rebuilt them.
- Mutation N04 (the rebuild never called) survived M0's run; the gap is closed by test_the_distributions_check_holds_and_rebuilds_from_the_commit_it_names (31324fd), which fails under N04 and passes on the code.
- Hosted CI passed on 1508966 (CI run 42818861870), carrying e23cf13 (the version state), 327db00 (the ledger) and 31324fd (the distributions wiring test).

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
| GAP-15 | Charter conflict | H/P0 | Open: the charter is accepted and the five documents reconciled; closes when G0 and M0 are accepted | WS-00 | G0 |
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
| ADR-0053 | Run and input provenance | WS-02 | G0 | Accepted (2026-11-10), revision 3; implemented in M1 |
| ADR-0054 | Selection statistics (pure) (plan 0054A) | WS-06 | G0 | Accepted (2026-11-14) |
| ADR-0072 | Experiments and trial ledger (plan 0054B) | WS-05 | Before M4 | Not drafted in the repository |
| ADR-0055 | Streaming research capture, extractor identity, trace identity, retention | WS-04 | Before M3 | Not drafted in the repository |
| ADR-0056 | Product charter (D-V1) | WS-00 | G0 | Accepted (2026-11-10) |
| ADR-0057 | Optional distributions and backend protocols (D-V2); includes the screening evaluator (B9) | WS-09 | G0 | Not drafted in the repository |
| ADR-0058 | Numerical foundation, record-path host independence, estimation identity (reopens OFE-007, OFE-010 under identity) | WS-03 | Before M2 | Accepted (2026-11-14) |
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
