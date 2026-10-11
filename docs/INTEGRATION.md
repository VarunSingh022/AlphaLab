# Depending on AlphaLab 4.1 from an application

How a host application -- iluvtrade is the one this release was prepared for --
installs AlphaLab 4.1.0 and what it may rely on. Every statement here is held by
a test, a release gate or the certificate; where something is *not* established,
this page says so. The checklist at the end is the order to wire it in.

---

## What you install

| | |
| --- | --- |
| Distribution | `alphalab` **4.1.0**: a wheel `alphalab-4.1.0-py3-none-any.whl` (pure Python) and an sdist `alphalab-4.1.0.tar.gz` |
| Python | `Requires-Python: >=3.12`. **Tested on CPython 3.12, 3.13 and 3.14** -- every release gate on each, in CI and in the 4.1.0 verification (ledger LIM-007). Other implementations are not tested |
| Operating systems | **Linux and macOS**, each a required CI cell under all three interpreters. Windows has never run a gate and is not claimed (ledger BDY-027) |
| Runtime dependencies | **None.** The standard library is the whole of it. The `dev` extra is the lint, type-check, test and build toolchain and is not needed to use the library |
| Type information | `py.typed` ships in the wheel; the package is checked with `mypy --strict` |
| Network | None at import or on the research, backtest or replay paths: the examples the release gates run from each installed artifact run with every socket operation refused. Live connectivity is the application's: AlphaLab defines the broker and market-data contracts an adapter implements, and holds no vendor client and no credential |

`tests/installed_smoke.py` is what the release gates -- CI on every push, Release
Preflight before publication and Release Validation after it -- run against each
built distribution in a fresh environment, from outside any checkout: `pip
check` finds nothing broken, no runtime requirement is declared, every module
imports without a warning, the version is 4.1.0, `py.typed` is present, every
public name is the one the manifest records, examples `11`, `70` and `71` run end
to end with every socket operation refused, and `tests/consumer_contract.py` --
the contract below, exercised through public names, a replay paused in one
process and resumed in another included -- passes against the artifact.

## How to install it

AlphaLab 4.1.0 is **not published on PyPI**, and no project of that name there
belongs to it. Do not write a bare `alphalab==4.1.0` against a public index: if
somebody registers the name, that requirement installs their package. Install
the released artifact by a direct reference instead, and pin its hash.

From the release's wheel (recommended). Once the `v4.1.0` release is published
on GitHub, download its `SHA256SUMS-4.1.0` beside the wheel and write the pinned
requirement from it -- no value is typed by hand:

```bash
RELEASE="https://github.com/VarunSingh022/AlphaLab/releases/download/v4.1.0"
curl -fsSLO "${RELEASE}/SHA256SUMS-4.1.0"
WHEEL_SHA256="$(grep ' alphalab-4.1.0-py3-none-any.whl$' SHA256SUMS-4.1.0 | cut -d ' ' -f 1)"
printf 'alphalab @ %s/alphalab-4.1.0-py3-none-any.whl --hash=sha256:%s\n' "${RELEASE}" "${WHEEL_SHA256}" > requirements-alphalab.txt
python -m pip install --require-hashes -r requirements-alphalab.txt
```

From the tag, building the sdist locally (needs network access to fetch
`hatchling` for the build):

```bash
python -m pip install "alphalab @ git+https://github.com/VarunSingh022/AlphaLab@v4.1.0"
```

An editable install or a checkout on `sys.path` is for working on AlphaLab, not
for depending on it: a checkout can hold files no release carries.

After installing, check what you got:

```bash
python -m pip check
```

```bash
python -c "import alphalab, alphalab.api; print(alphalab.__version__, alphalab.__file__)"
```

The version must be `4.1.0` and the path must be inside the environment's
`site-packages`.

## Which version range to declare

`docs/api/PUBLIC_API.md` states the policy: from 4.0, a minor release adds and
corrects and does not break; a breaking change waits for 5.0 with an ADR and a
migration. An application using replay pause and resume needs 4.1, so it can
accept `>=4.1,<5`; pinning `4.1.0` exactly, with its hash, is what makes a
deployment reproducible, and is what this release recommends.

## What the application may rely on

**The public API.** Every name in `docs/api/public_api.json`: each package's
`__all__`, and the `__all__` of `alphalab.api` and of each durable state's
snapshot module -- 2,628 names. `tests/regression/test_public_api_manifest.py`
fails on any name added, removed or re-signed without the manifest changing,
and each release's CHANGELOG names every removal or rebinding. Since v4.1 one
signature reads the same on every supported interpreter (API-008). A name with a
leading underscore, or one a module defines outside its `__all__`, is not public
and may change in any release.

**Building and running a strategy.** `docs/GETTING_STARTED.md`, *Build your first
strategy*, and `examples/70_build_a_strategy.py` -- which the end-to-end test
`tests/integration/test_strategy_building_end_to_end.py` holds to arithmetic
done by hand, and which the release gate runs from each installed distribution.
The entry points it uses are `alphalab.api.ingest_rows`,
`alphalab.strategy.start_strategy` and `context_factory`, and
`alphalab.backtesting.BacktestEngine`. A run says when a strategy failed: read
`result.strategy_failures` before any figure (EXE-006).

**Pausing and resuming a replay** (v4.1, FUT-001). `alphalab.api.replay(...,
pause_when=...)` -- or `ReplayBacktest.run` -- asks `pause_when(progress)` at
every boundary between two records and, when it answers true, returns a
`PausedReplay` instead of a result. `alphalab.backtesting.replay_snapshot.capture`
projects it; `alphalab.persistence.serialize` writes it as a string the
application keeps where it likes (`FileRunStateStore` writes atomically and
refuses truncated or altered bytes by digest). In this process or a new one,
`replay_snapshot.restore(from_primitives(deserialize(payload)), objects,
dataset)` rebuilds it and `alphalab.api.resume_replay` -- or
`ReplayBacktest.resume` -- continues it. The resumed result equals the
uninterrupted replay's byte for byte (`result_id`); the cursor's own log records
the pause. What `restore` is given back, and refuses when it differs, by name:

- the **dataset** -- its id, its length and span, the records either side of the
  pause, and a SHA-256 digest of every record (`replay_snapshot.dataset_digest`);
  a missing, extra, reordered or altered record is refused;
- the **live objects** -- sizing model, simulator, strategies, instrument
  registry and fill policy, configured as the run recorded them (PER-008);
- a payload of another schema, one whose cursor and run disagree about how far
  they have read, or one whose identifier stream does not match its log.

`pause_when` is caller code: it runs with `uuid4` installed, so nothing it mints
moves the replay's identifiers, and an exception it raises propagates with
nothing paused. A paused replay holds the strategy *instances* the run calls; to
continue one pause twice, restore each continuation from the payload, which
carries each strategy's declared state. `examples/71_pause_and_resume_a_replay.py`
shows all of it. **Cost**: a checkpoint is linear in what the run recorded --
about 3.2 KB a record replayed with every history kept, under 300 bytes a record
at 32,000 records with a `RetentionPolicy` bounding the derived histories (since
v4.1 the cursor's log too) -- and about as long to capture as to replay
(`docs/audit/V4_1_RELEASE_AUDIT.md`). Checkpoint at a cadence the history allows.

**Determinism.** A seeded run produces the byte-identical record in one process,
in fresh interpreters under other hash seeds, and under a hostile ambient
`decimal` context (certificate DET-1, DET-2). Backtest, replay and paper runs of
one dataset produce the same orders, fills and accounting (PAR-1). Since v4.1 a
run's record -- and so its `result_id` -- does not depend on the host: money is
`Decimal`, and every float a run records comes from IEEE-exact operations or a
correctly rounded power (LIM-005, REP-005). The standalone engines -- options
pricing, rates conventions, the EWMA risk model -- still use the host's `libm`,
and may differ in the last bit on another host; their figures enter no run
record.

**Persistence.** A run stopped, serialized, read back and continued finishes
byte-identical to one never stopped (certificate REP-2), and so does a paused
replay (FUT-001). Every payload v3.9.0, v3.11.0, v3.12.0, v3.13.0 and v4.1.0 wrote
and froze is read by 4.1.0 (REP-3); 4.1.0 writes the run and pipeline schemas
4.0.0 wrote (5 and 8), so it reads 4.0.0's payloads as 4.0.0 does. A later 4.x
reading what 4.1.0 writes is the stability policy's commitment (a persisted
format moves only by a schema step that reads every older payload). A restore
must be given the same sizing model, simulator and fill policy, configured the
same way, as the run was captured with; it refuses anything else and names both
(ledger PER-008, PER-009).

**A strategy's configuration** (the `configure` value, `start_strategy(config=...)`)
reads back after a restore as `docs/STATE_MODEL.md` states, row by row (LIM-004):
JSON natives exactly; a `Decimal` as its exact text (rebuild it with `Decimal`),
a tuple as a list, a non-string key as its text, an enum, `UUID` or dataclass as
its projection; a set, a non-finite float or any other object is refused at
capture. Keep a strategy's durable parameters and memory in
`StrategyStateProtocol` state, which round-trips exactly. A typed form is
FUT-002, a 5.0 decision.

**What the application owns.** The process and its scheduling, broker and data
adapters, credentials, persistence locations, the decision when to pause, and
any concurrency. The engine is single-threaded by design (LIM-006). Its seeded
identifier stream lives in a `contextvars.ContextVar`, so it is not shared
between threads -- but running several runs concurrently in one process is
**not tested**; run them in separate processes.

## What this release does not provide

A vendor adapter or credential of any kind, a running service, incremental
checkpoints of a paused replay (each is a whole capture), a strategy
configuration that reads back typed (FUT-002), testing on Windows or on any
implementation but CPython, and concurrency within one process. `ROADMAP.md`
lists each boundary and limitation with its reason.

## Checklist for iluvtrade's build

In order; each step names what holds it.

1. **Pin the artifact.** Depend on `alphalab-4.1.0-py3-none-any.whl` by direct
   reference with its SHA-256 from `SHA256SUMS-4.1.0` (above), never a bare
   `alphalab==4.1.0`. Use CPython 3.12, 3.13 or 3.14 on Linux or macOS.
2. **Verify the install** in iluvtrade's own environment: `pip check`, then the
   version and import location (above). Then run the release's consumer
   contract against the installed package, from the release's sdist unpacked
   in a scratch directory -- the script imports `alphalab` from the environment,
   never from the sdist -- and require its last line to read `consumer contract:
   9 checks passed`:

   ```bash
   SCRATCH="$(mktemp -d)"
   curl -fsSL -o "${SCRATCH}/alphalab-4.1.0.tar.gz" "https://github.com/VarunSingh022/AlphaLab/releases/download/v4.1.0/alphalab-4.1.0.tar.gz"
   tar -xzf "${SCRATCH}/alphalab-4.1.0.tar.gz" -C "${SCRATCH}"
   (cd "${SCRATCH}" && python -W error "${SCRATCH}/alphalab-4.1.0/tests/consumer_contract.py" 4.1.0)
   ```
3. **Import public names only** -- those in `docs/api/public_api.json`.
   Anything else may change in a patch release.
4. **Write strategies against `BaseStrategy`**, start them with
   `start_strategy`, and give any strategy with memory the three
   `StrategyStateProtocol` members, or a restore cannot continue it.
5. **State every run's configuration** -- capital, budget, limits, simulator,
   instruments, seed -- in a `RunConfig`; none is defaulted. Record the seed: a
   seeded run is reproducible and an unseeded one is not.
6. **Read `strategy_failures` first** on every result.
7. **Persist with `serialize`** and keep payloads where iluvtrade decides --
   `FileRunStateStore` is the reference (atomic, digest-checked). Record each
   payload's `run_id` and sequence.
8. **To pause a replay**, pass `pause_when` and store the `PausedReplay`'s
   payload. **To resume**, build the same objects again -- configured the same
   way -- load the same dataset, `restore`, and `resume_replay`. Treat
   `StateDecodeError` (wrong dataset, objects, schema or a damaged payload) and
   `StorageError` (missing or altered bytes) as a refusal to continue, never
   retry with other inputs.
9. **Bound long runs** with a `RetentionPolicy`, and checkpoint at a cadence the
   run's history allows (about 3 KB a record unbounded).
10. **Record identities** -- `digest_run(result).result_id` and
    `configuration_id` -- with each result; they are host-independent for a run's
    record.
11. **One run per process** for concurrency.
12. **Upgrade within 4.x** by re-running steps 1, 2 and the application's own
    suite; read the release's CHANGELOG section and its migration table first.
