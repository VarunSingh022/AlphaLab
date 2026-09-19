# AlphaLab Examples

Sixteen runnable scripts, each demonstrating one part of AlphaLab against its
real public API. Every one of them runs:

```bash
python examples/01_research.py
```

They are **not** part of the automated test suite — the suite covers the same
paths far more thoroughly under `tests/` — but all sixteen are executed as a
release gate, and a change that breaks one is a change that breaks a documented
API.

---

## The examples

| # | File | What it shows |
|---|------|---------------|
| 01 | `01_research.py` | The research engine: a payload in, an immutable `ResearchState` and a score out |
| 02 | `02_backtest.py` | Strategy Studio's backtest bookkeeping over the sample datasets |
| 03 | `03_replay.py` | The replay cursor: sessions, chronological validation, replay metrics |
| 04 | `04_market_data.py` | Registering market-data providers and reading provider state |
| 05 | `05_broker_connection.py` | The **two** broker boundaries — one venue (`BrokerProtocol`), or a registry of many (`BrokerConnectorProtocol`) |
| 06 | `06_portfolio_optimizer.py` | Portfolio construction, constraints, exposure and rebalancing |
| 07 | `07_universal_data.py` | The Universal Data Engine: ingestion, schema, quality, catalogue |
| 08 | `08_strategy_studio.py` | Strategy Studio orchestration: projects, strategies, pipelines, reports |
| 09 | `09_workbench.py` | The Workbench workspace: projects, tabs, dashboards |
| 10 | `10_complete_pipeline.py` | A multi-engine walkthrough, composing the standalone engines by hand |
| 11 | `11_unified_backtest.py` | **The integrated execution path**: dataset → intents → orders → fills → P&L → analytics, and replay parity against the same dataset |
| 12 | `12_model_lifecycle.py` | **The lifecycle path**: research candidate → experiment run → evidence → model version → strategy version → gated promotion → deployment → rollback, with governance |
| 13 | `13_durable_run_state.py` | Stopping a seeded run, storing it with `FileRunStateStore`, continuing it in a **separate interpreter**, and comparing byte-for-byte against a run that never stopped |
| 14 | `14_multi_currency_settlement.py` | An FX feed's three rules, a run settling **two** currencies, one reported figure with every rate that produced it, and the refusals |
| 15 | `15_data_ingestion.py` | **The v3.1 data path**: a deliberately broken CSV through schema detection, validation, an explicit cleaning policy, a quality report, and out the other side as a canonical dataset with a derived, immutable version |
| 16 | `16_research_from_dataset.py` | Research access by dataset version, point-in-time selection, and a backtest whose result names the **exact bytes** it was measured on |

## Reading order

`01`–`10` date from v1.0.0 and exercise the **standalone** engine APIs, which is
the gentler introduction. `11`–`14` drive the wired paths and are where the
architecture actually shows:

- start at **11** for the execution path,
- **12** for the lifecycle path and how it joins the first,
- **13** for durability,
- **14** for money,
- **15** for where data comes from, and **16** for how it reaches a run.

`15` and `16` are the v3.1 pair and read best together: `15` ends with a dataset
version, and `16` starts from one and carries it into a `BacktestResult`.

`05_broker_connection.py` was rewritten in v2.17 against the canonical broker
boundary, having used `alphalab.integrations` until that package was removed
(ADR-0034).

---

## Datasets

`01`–`10` are supported by the small synthetic CSV files in `examples/data/`;
`02`, `07`, `08` and `10` read them directly. `11`–`14` build their own in-memory
data and use none of them. `15` reads `messy_ohlcv.csv`, which is broken on
purpose, and `16` reads `sample_ohlcv.csv`.

See `examples/data/README.md` for what each dataset contains.

---

## What the examples are not

They demonstrate recommended usage patterns rather than every available API, and
they are deliberately concise. They are not performance benchmarks — those are in
`benchmarks/` — and the datasets are not suitable for research or production
trading.
