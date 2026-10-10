# Golden v3.12.0 snapshot payloads

Every file here is the exact text AlphaLab **v3.12.0** wrote, produced by running
`docs/audit/scripts/generate_v3_11_0_snapshot_fixtures.py` -- unchanged, since
every API it calls is the same at v3.12.0 -- against a checkout of the `v3.12.0`
tag (commit `d389078`):

    git archive v3.12.0 | tar -x -C /tmp/alphalab-v3.12.0
    cd /tmp/alphalab-v3.12.0 && PYTHONPATH=. python3.12 \
        docs/audit/scripts/generate_v3_11_0_snapshot_fixtures.py <out>

The generator mints some identifiers from `uuid4`, so two runs differ in those
and nothing else; these files are one run, frozen. A later build either reads
each one through its subsystem's schema upgrades or refuses it with the reason
the upgrade documents. `tests/regression/test_schema_upgrades_v3_12.py` holds
that contract, as `test_schema_upgrades_v3_11.py` and `test_schema_upgrades.py`
do for v3.11.0 and v3.9.0.

| File | Subsystem | Schema at v3.12.0 | At v3.13.0 |
| --- | --- | --- | --- |
| `portfolio.json` | portfolio | 5 | 5 (unchanged) |
| `oms.json` | oms | 2 | 2 (unchanged) |
| `allocation.json` | allocation | 3 | 3 (unchanged) |
| `allocation_eur.json` | allocation, its budget in EUR | 3 | 3 (unchanged) |
| `instrument.json` | instrument registry, a dated alias included | 3 | 3 (unchanged) |
| `lifecycle.json` | lifecycle | 2 | 2 (unchanged) |
| `fx_feed.json` | FX feed | 1 | 1 (unchanged) |
| `run_backtest.json` | run envelope (pipeline 6) | 4 | 4 (pipeline 7) |
| `run_ticks.json` | run envelope whose market history holds trade prints | 4 | 4 (pipeline 7) |
| `live.json` | live envelope (run 4, broker 2) | 2 | 2 (pipeline 7) |
| `broker.json` | broker | 2 | 2 (unchanged) |
| `run_store/…/00000000000000000001.runstate` | `FileRunStateStore` envelope around `run_backtest.json` | — | — |

Why they exist (ledger PER-004): a plain `Enum` member is written as
`ClassName.MEMBER` -- `"LifecycleState.RUNNING"`, `"FillStatus.FULL_FILL"` -- so
an enum's class name is part of the format. v3.13 renamed the strategy runtime's
`LifecycleState` to `StrategyStatus` (ledger API-001), and pipeline 6 -> 7 is
the upgrade that rewrites the name; these payloads are the evidence it does.
