# Depending on AlphaLab 4.0 from an application

How a host application -- iluvtrade is the one this release was prepared for --
installs AlphaLab 4.0.0 and what it may rely on. Every statement here is held by
a test, a release gate or the certificate; where something is *not* established,
this page says so.

---

## What you install

| | |
| --- | --- |
| Distribution | `alphalab` **4.0.0**: a wheel `alphalab-4.0.0-py3-none-any.whl` (pure Python) and an sdist `alphalab-4.0.0.tar.gz` |
| Python | `Requires-Python: >=3.12`. **Tested on CPython 3.12 only** -- CI and the release verification run 3.12; 3.13 and later install but have never been tested (ledger LIM-007, target v4.1) |
| Runtime dependencies | **None.** The standard library is the whole of it. The `dev` extra is the lint, type-check, test and build toolchain and is not needed to use the library |
| Type information | `py.typed` ships in the wheel; the package is checked with `mypy --strict` |
| Network | None at import or on the research, backtest or replay paths: examples `02`, `11`, `16` and `70` run with every socket operation refused (`docs/audit/V4_RELEASE_AUDIT.md`). Live connectivity is the application's: AlphaLab defines the broker and market-data contracts an adapter implements, and holds no vendor client and no credential |

`tests/installed_smoke.py` is what the release gates -- CI, Release Preflight
before publication and Release Validation after it -- run against each built
distribution in a fresh environment, from outside any checkout: `pip check`
finds nothing broken, no runtime requirement is declared, every module imports
without a warning, the version is 4.0.0, `py.typed` is present, every public
name is the one the manifest records, and examples `11` and `70` run end to end
with every socket operation refused.

## How to install it

AlphaLab 4.0.0 is **not published on PyPI**, and no project of that name there
belongs to it. Do not write a bare `alphalab==4.0.0` against a public index: if
somebody registers the name, that requirement installs their package. Install
the released artifact by a direct reference instead, and pin its hash.

From the release's wheel (recommended), in the application's requirements --
the URL resolves once the `v4.0.0` release has been published on GitHub, and
the hash to pin is the one published with it (`SHA256SUMS-4.0.0`):

```text
alphalab @ https://github.com/VarunSingh022/AlphaLab/releases/download/v4.0.0/alphalab-4.0.0-py3-none-any.whl --hash=sha256:<the wheel's SHA-256 from the release>
```

or from a copy kept with the application:

```bash
python -m pip install --require-hashes -r requirements.txt
```

with the same line pointing at the local file. From the tag, building the sdist
locally (needs network access to fetch `hatchling` for the build):

```bash
python -m pip install "alphalab @ git+https://github.com/VarunSingh022/AlphaLab@v4.0.0"
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

The version must be `4.0.0` and the path must be inside the environment's
`site-packages`.

## Which version range to declare

`docs/api/PUBLIC_API.md` states the policy: from 4.0, a minor release adds and
corrects and does not break; a breaking change waits for 5.0 with an ADR and a
migration. An application that takes AlphaLab only from these release artifacts
can therefore accept `>=4.0,<5`; pinning `4.0.0` exactly, with its hash, is what
makes a deployment reproducible, and is what this release recommends.

## What the application may rely on

**The public API.** Every name in `docs/api/public_api.json`: each package's
`__all__`, and the `__all__` of `alphalab.api` and of each durable state's
snapshot module -- 2,617 names. `tests/regression/test_public_api_manifest.py`
fails on any name added, removed or re-signed without the manifest changing,
and each release's CHANGELOG names every removal or rebinding. A name with a
leading underscore, or one a module defines outside its `__all__`, is not
public and may change in any release.

**Building and running a strategy.** `docs/GETTING_STARTED.md`, *Build your first
strategy*, and `examples/70_build_a_strategy.py` -- which the end-to-end test
`tests/integration/test_strategy_building_end_to_end.py` holds to arithmetic
done by hand, and which the release gate runs from each installed
distribution. The entry points it uses are `alphalab.api.ingest_rows`,
`alphalab.strategy.start_strategy` and `context_factory`, and
`alphalab.backtesting.BacktestEngine`.

**Determinism.** A seeded run produces the byte-identical record in one process,
in fresh interpreters under other hash seeds, and under a hostile ambient
`decimal` context (certificate DET-1, DET-2). Backtest, replay and paper runs of
one dataset produce the same orders, fills and accounting (PAR-1). Compare a
run's `result_id` only between hosts of one class: a float computed through
another platform's maths library can differ in its last bit (LIM-005); money is
`Decimal` and does not.

**Persistence.** A run stopped, serialized with `alphalab.persistence.serialize`,
read back and continued finishes byte-identical to one never stopped
(certificate REP-2). Every payload v3.9.0, v3.11.0, v3.12.0 and v3.13.0 wrote is
read by 4.0.0 (REP-3). That a later 4.x will read what 4.0.0 writes is a
commitment of the stability policy (`docs/api/PUBLIC_API.md`: a persisted format
moves only by a schema step that reads every older payload), tested when that
release freezes 4.0.0's payloads, as 4.0.0 froze 3.13.0's. A restore must be given the same sizing model,
simulator and fill policy, configured the same way, as the run was captured
with; it refuses anything else and names both (ledger PER-008, PER-009). A
strategy's `configure` value is persisted as JSON reads it: keep durable state
in `StrategyStateProtocol`, which round-trips exactly (LIM-004).

**What the application owns.** The process and its scheduling, broker and data
adapters, credentials, persistence locations, and any concurrency. The engine
is single-threaded by design (LIM-006). Its seeded identifier stream
lives in a `contextvars.ContextVar`, so it is not shared between threads -- but
running several runs concurrently in one process is **not tested**; run them in
separate processes.

## What this release does not provide

A resumable *replay* (FUT-001, v4.1 -- a backtest resumes; a replay restarts
deterministically), a vendor adapter or credential of any kind, a running
service, and testing on any interpreter but CPython 3.12. `ROADMAP.md` lists
each boundary and limitation with its reason.
