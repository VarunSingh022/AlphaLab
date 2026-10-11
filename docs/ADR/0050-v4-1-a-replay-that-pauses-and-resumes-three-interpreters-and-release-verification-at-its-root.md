# ADR-0050: v4.1 — a Replay that Pauses and Resumes, Three Interpreters, and Release Verification at Its Root

## Status

**Accepted and implemented in v4.1.0.**

The first minor release under the v4.0 stability policy (ADR-0049 decision 4,
`docs/api/PUBLIC_API.md`). It adds **no package** and no package edge, and moves
**no existing schema**: one durable format is added, the paused replay
(`REPLAY_SNAPSHOT_SCHEMA = 1`). Public API: names added, one keyword-only
parameter with no effect unless given, nothing removed or re-signed. Closes the
future work ADR-0049 decision 6 accepted (FUT-001) and two of its limitations
(LIM-005 for a run's record, LIM-007).

---

# Context

v4.0.0 was merged and tagged, and its post-publication validation failed:
`release.yml` handed the verifier a relative `dist`, and the verifier handed it to
`pip` after changing directory (PKG-002). The hosted benchmark job had failed
the same week on an import its environment lacked (TST-019). Both were found by
the hosted run, after every local gate had passed. v4.0 had accepted one item of
future work -- a resumable replay -- and stated four limitations; v4.1 was to
deliver the first and re-examine the rest, and to be the release an application
(iluvtrade) is built on.

---

# Decisions

**1. A paused replay is a durable state of its own, around the run's.**
`PausedReplay` holds the run (not finalized), the cursor (`PAUSED`) and the
cursor stream's position. Its envelope nests the run snapshot exactly as
`run_snapshot.capture` writes it, so the run (5) and pipeline (8) schemas do not
move and the run's history upgrades a nested older payload. The dataset is the
replay's input and is supplied back, like the live objects, and held to what
the envelope recorded: id, length, span, the records either side of the pause
and a digest of every record. The cursor never enters `RunState` (ADR-0030).

**2. A pause is at a boundary between records, asked of the caller.**
`pause_when(progress)` is asked before each record is taken -- the boundary
`RunEngine.resume` already defines -- with `uuid4` installed, so caller code
mints nothing from either stream. After the last record there is no boundary,
and the replay finishes. A resumed replay is not asked about the boundary it
resumes from. `run` and `resume` are one loop.

**3. One owner of continuation, two streams.** `RunEngine.id_source_at(position)`
is the only function that derives an identifier source from a stream position;
`RunEngine.resume` and the replay driver both go through it
(`test_run_runtime_boundary.py`).

**4. A declared retention reaches the cursor's log.** Its `audit_events` bound
applies to the cursor's lifecycle log, which every replay checkpoint carries;
the envelope records the count dropped (PER-010).

**5. A run's record is host-independent.** The one host-dependent operation in
it -- `math.pow` in CAGR and the geometric return -- is a correctly rounded power
computed in `decimal` (REP-005). The standalone engines keep the host's `libm`.

**6. A strategy's configuration keeps its format and gains a stated contract.**
A typed form would move every run's `result_id`, which decision 4 of ADR-0049
does not allow a minor release; the conversion is tabulated and tested, and the
typed form is accepted future work for the maintainer's decision (FUT-002).

**7. Supported: CPython 3.12, 3.13 and 3.14 on Linux and macOS.** Every cell a
required CI gate; the manifest written in one canonical spelling so one
signature reads one way on every interpreter (API-008).

**8. Release verification is fixed where it breaks, and runs where it ran last.**
Every path resolved before a directory changes (PKG-002); CI runs release
validation's own command on every push; release validation refuses a tag no
passing preflight approved (TST-020); every workflow's environment is held to
what its code imports (TST-019); documented commands run as pasted (DOC-011).

---

# Consequences

**Deferred** — to 5.0, pending the maintainer's decision: a strategy
configuration that reads back typed.

**Known limitations**

- **A strategy's configuration reads back by a stated contract**: a `Decimal` as
  its text, a tuple as a list, and the rest as `docs/STATE_MODEL.md` tabulates.
- **The standalone engines use the host's libm**: options pricing, rates
  conventions, the EWMA risk model and FX research may differ in a last bit
  between hosts; none enters a run's record.
- **A paused replay's checkpoint is a whole capture**: linear in what the run
  recorded; a retention policy bounds it.
- **Windows is untested**: the supported systems are Linux and macOS.

Each is a ledger entry with its reason and its test: LIM-004, LIM-005, LIM-006
and BDY-027; the deferral is FUT-002, on `ROADMAP.md`'s Future work.
