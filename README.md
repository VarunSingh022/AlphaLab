<div align="center">

# AlphaLab

### A deterministic quantitative research and execution engine for Python

**Deterministic • Immutable • Fully typed • Zero runtime dependencies**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-4.0.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Typing](https://img.shields.io/badge/MyPy-Strict-blue)]()
[![Style](https://img.shields.io/badge/Ruff-Clean-red)]()

</div>

---

# What is AlphaLab?

AlphaLab is an open-source Python **library** for building, testing and running
systematic trading strategies whose results can be trusted and reproduced. It is
an engine, not an application: there is no server, daemon, CLI or user
interface, and it declares **no runtime dependencies** -- the standard library is
the whole of it. You import the packages you need and call pure functions over
immutable state.

It exists to answer the questions that decide whether a backtest means anything:

- **Is the result reproducible?** Seeded identifiers, derived dataset versions
  and content digests mean the same bytes, configuration and seed produce the
  same run, byte for byte, in another process -- and a stopped run continued
  from its snapshot equals one that never stopped.
- **Is the accounting right?** Money is exact at each currency's minor unit,
  cash and P&L are kept per settlement currency, and nothing converts without a
  stated rate.
- **Did the research see the future?** Bars are stamped at their close,
  information carries the instant it became knowable, and fills can be taken at
  the next event rather than the one that decided them.
- **Was anything invented?** No silent default for a currency, a rate, a
  tolerance or a cleaning policy; an input the engine cannot use is refused with
  a reason, not repaired.

---

# Release Status

**Current Release:** **v4.0.0 — the universal engine contract, frozen: the v2.0–v3.13 history read and classified, the canonical path re-audited and four defects fixed, a strategy built end to end through the public API, and the public API held stable from here**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 4.0.0 |
| Runtime dependencies | **None** (standard library only) |
| Tests | **9,325 passing, 0 skipped, 0 warnings** |
| Static typing | **Strict mypy**, repository-wide |
| Examples | **70 / 70** |
| Release certificate | [11 / 11 checks](docs/audit/RELEASE_CERTIFICATION.md) |
| License | MIT |

---

# Milestones

| Release | What it established |
| --- | --- |
| **v1.0.0** | The foundation: immutable domain models and deterministic engine APIs; a strategy runtime with lifecycle and intents; the universal data engine; historical replay; the portfolio optimizer |
| **v2.0.0** | One canonical execution domain -- one `Side`, one `OrderRequest`, one lifecycle `Order` -- with `ExecutionPipeline` as the single spine every run takes. The v2 line built on it: backtest and replay on one step, one market-data model, one broker boundary, the strategy lifecycle, durable run state and multi-currency settlement |
| **v3.0.0** | The architecture frozen and the documentation made true. The v3 line then added data ingestion with provenance, research methodology without look-ahead, institutional backtesting, global markets and derivatives, point-in-time research, portfolio and risk construction, and the universal execution contract, and its four pre-v4 releases made the canonical path correct, complete and hardened |
| **v4.0.0** | The freeze: every deferral since v2.0 classified with evidence, the canonical path re-audited (four defects fixed), a strategy built and reproduced end to end through the public API, and the public API stable until 5.0 |

Every release is in [`CHANGELOG.md`](CHANGELOG.md); every decision in
[`docs/ADR/`](docs/ADR).

---

# What it does

| Area | Capabilities |
| --- | --- |
| Data | CSV and in-memory ingestion with schema detection, structured validation, cleaning under an explicit policy, market calendars, corporate actions, and a dataset version derived from the content |
| Research | Features and factors with identity and lineage, signal diagnostics, walk-forward and purged cross-validation, overfitting and multiple-testing corrections, event studies, point-in-time fundamentals and alternative data |
| Strategies | A small strategy protocol, target-position and delta intents, subscriptions, slices, timers, durable strategy state, adaptive strategies that replay exactly |
| Execution | Market, limit, stop and auction orders with time in force; simulated fills under a stated timing and cost model; algorithms (TWAP, VWAP, participation, iceberg), routing and execution analytics; a venue contract any broker adapter can implement |
| Portfolio and risk | Exact multi-currency accounting, instrument economics (futures, options, perpetuals), corporate actions; pre-trade risk on the projected book; a risk model, constrained construction, risk budgets, multi-strategy books and capital allocation |
| Reproducibility | Run snapshots and checkpoints with schema upgrades, a durable run store, strategy fingerprints, reproducibility manifests, a rerun harness and a release certificate |
| Asset classes | Options (Black–Scholes–Merton, an American lattice, implied volatility, surfaces), futures and continuous contracts, crypto perpetuals, FX, a fixed-income foundation |

What it deliberately does **not** do -- vendor adapters, credentials, market or
reference data, user accounts, a UI, a running process -- is listed with the
reason for each in [`ROADMAP.md`](ROADMAP.md).

---

# Architecture in brief

```text
dataset / live feed
      │
      ▼
mark → strategy → allocation → risk → OMS → execution → portfolio → analytics
      ▲                                                   runtime.ExecutionPipeline
      │                                                   (the step)
BacktestEngine · ReplayBacktest · TradingSession · LiveSession
      (drivers: each chooses the next record; none holds state)
```

- **The execution path** is one spine. `ExecutionPipeline` owns the step and
  `RunEngine` the run; a backtest, a replay, a paper run and a live run of one
  dataset take the same step, and produce the same orders, fills and P&L
  wherever the venue is the same.
- **The lifecycle path** (`alphalab.lifecycle`) takes a research candidate to a
  deployment and back, and refuses a run that would serve a version the
  deployment ledger does not name.
- **Standalone engines** -- options, futures, crypto, macro, the optimizer,
  learning, scenario analysis and the rest -- are deterministic libraries that
  neither path chains together, on purpose (ADR-0009).
- **The boundary.** A broker or data vendor connects through an adapter the
  host application writes against AlphaLab's contracts; AlphaLab names no
  vendor, holds no credential and opens no connection on the research path.

More: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/STATE_MODEL.md`](docs/STATE_MODEL.md),
[`nowandfuture.md`](nowandfuture.md).

---

# Installation

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git
cd AlphaLab
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # or `pip install .` for the library alone
```

AlphaLab needs only the standard library; the `[dev]` extra installs the
lint, type-check, test and build toolchain.

**Depending on AlphaLab from an application?** Install the release's wheel by a
pinned direct reference, not a checkout and not a bare name from a public index
(AlphaLab is not on PyPI): [`docs/INTEGRATION.md`](docs/INTEGRATION.md) gives
the requirement line, the tested Python (CPython 3.12) and what the application
may rely on.

---

# Your first strategy

A strategy is a class with a hook. This one holds ten shares while the fast
average of closes is above the slow one -- an excerpt of
[`examples/70_build_a_strategy.py`](examples/70_build_a_strategy.py), which runs
it end to end on synthetic data, checks every fill against arithmetic done by
hand, refuses invalid input, and continues a stopped run byte for byte:

```python
class MovingAverageCrossover(BaseStrategy):
    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return ()
        fast = sum(self._closes[-self._fast :]) / self._fast
        slow = sum(self._closes[-self._slow :]) / self._slow
        return (
            Intent(
                strategy_id=STRATEGY_ID,
                instrument=self._asset_id,
                target=self._size if fast > slow else Decimal("0"),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )
```

Started, run and read through the public API:

```python
    return start_strategy(
        create_runtime(),
        STRATEGY_ID,
        instance,
        config={},
        subscriptions={"bars"},
        at=FIRST_BAR - 1.0,
    )
```

```bash
python examples/70_build_a_strategy.py
```

[`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) walks through every step --
ingesting data, stating capital, limits and costs, running, inspecting fills and
P&L, and reproducing the run. The other 69 examples cover one capability each
([`examples/README.md`](examples/README.md)).

---

# Documentation

| Document | What it covers |
| --- | --- |
| [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) | Installation, the repository, and building a first strategy |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | What is built, how the packages relate, and what each owns |
| [`docs/STATE_MODEL.md`](docs/STATE_MODEL.md) | Immutable state, snapshots, schemas and upgrades |
| [`docs/EVENT_MODEL.md`](docs/EVENT_MODEL.md) | Events and their routing |
| [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) | Subsystem interaction |
| [`docs/ENGINEERING_GUIDELINES.md`](docs/ENGINEERING_GUIDELINES.md) | Engineering standards and the release checklist |
| [`docs/api/PUBLIC_API.md`](docs/api/PUBLIC_API.md) | The public API, its manifest and its stability policy |
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Depending on AlphaLab from an application: installation, versions and guarantees |
| [`docs/ADR/`](docs/ADR) | The architectural decision records |
| [`ROADMAP.md`](ROADMAP.md) | Boundaries, external dependencies, limitations and future work |
| [`nowandfuture.md`](nowandfuture.md) | The long-form reference: ownership, invariants, what must not change casually |
| [`docs/audit/`](docs/audit) | The pre-v4 and v4.0 audits, the completion ledger, the historical inventory and the release certificate |

---

# API stability

The public API is every name a package lists in `__all__`, and every name
`alphalab.api` and the snapshot modules list in theirs -- 2,617 names, recorded
as data in [`docs/api/public_api.json`](docs/api/public_api.json) and held to the
package by a test. From v4.0 a minor release adds and corrects but does not break; an
incompatible change waits for 5.0 with an ADR and a migration, and a persisted
format moves only by a schema step that reads every older payload
([`docs/api/PUBLIC_API.md`](docs/api/PUBLIC_API.md)).

---

# Quality

Every gate below runs in CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
and can be run locally:

```bash
python -m ruff check . && python -m ruff format --check .
python -m mypy .
python -m pytest -W error
python docs/audit/scripts/certify_release.py --check
```

- The suite reports **0 skipped and 0 warnings**, enforced by tests that refuse
  a skip or an expected failure in any test module and import every module in a
  fresh interpreter with deprecation warnings as errors; CI runs the suite
  itself with `-W error`.
- Every example runs under `-W error`; the benchmarks run weekly.
- A defect-injection harness (`docs/audit/scripts/mutation_v4_0.py`) runs each
  of its mutations against the whole suite, and a stress program
  (`docs/audit/scripts/stress_v4_0.py`) measures the canonical path at scale;
  their results are in [`docs/audit/V4_RELEASE_AUDIT.md`](docs/audit/V4_RELEASE_AUDIT.md).
- Each built distribution is installed into a clean environment and exercised
  from outside the checkout (`tests/installed_smoke.py`).
- Before a release is published, **Release Preflight**
  ([`.github/workflows/preflight.yml`](.github/workflows/preflight.yml)) runs
  every gate above, CodeQL and the benchmarks against the exact candidate
  commit, and keeps the artifacts it built with their SHA-256 sums
  ([`docs/ENGINEERING_GUIDELINES.md`](docs/ENGINEERING_GUIDELINES.md)).

---

# Contributing

Contributions are welcome -- see [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`docs/ENGINEERING_GUIDELINES.md`](docs/ENGINEERING_GUIDELINES.md). A change to an
ownership boundary, a schema or the public API needs an ADR; a new engine, a
strategy, an example, a test or a documentation fix follows the ordinary
workflow. Security reports: [`SECURITY.md`](SECURITY.md).

# License

MIT -- see [`LICENSE`](LICENSE).
