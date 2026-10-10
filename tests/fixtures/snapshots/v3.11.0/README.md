# Golden v3.11.0 snapshot payloads

Every file here is the exact text AlphaLab **v3.11.0** wrote, produced by running
`docs/audit/scripts/generate_v3_11_0_snapshot_fixtures.py` against a checkout of
the `v3.11.0` tag (commit `a8a17b1f`). They are frozen: a later build either reads
each one through its subsystem's schema upgrades or refuses it with the reason
the upgrade documents. `tests/regression/test_schema_upgrades_v3_11.py` holds that
contract, as `tests/regression/test_schema_upgrades.py` does for v3.9.0.

| File | Subsystem | Schema at v3.11.0 | At v3.12.0 |
| --- | --- | --- | --- |
| `portfolio.json` | portfolio | 5 | 5 (unchanged) |
| `oms.json` | oms | 2 | 2 (unchanged) |
| `allocation.json` | allocation | 2 | 3 |
| `allocation_eur.json` | allocation, its budget in EUR (ledger PER-006) | 2 | 3 |
| `instrument.json` | instrument registry, a dated alias included | 2 | 3 |
| `lifecycle.json` | lifecycle | 2 | 2 (unchanged) |
| `fx_feed.json` | FX feed | 1 | 1 (unchanged) |
| `run_backtest.json` | run envelope (pipeline 5) | 3 | 4 (pipeline 6) |
| `run_ticks.json` | run envelope whose market history holds trade prints | 3 | 4 (pipeline 6) |
| `live.json` | live envelope (run 3, broker 2) | 2 | 2 (run 4) |
| `broker.json` | broker | 2 | 2 (unchanged) |
| `run_store/…/00000000000000000001.runstate` | `FileRunStateStore` envelope around `run_backtest.json` | — | — |

v3.12 adds nothing a v3.11 payload has to be guessed for: an empty venue
calendar, no strategy ceiling enforced, no classification limit, a retention
policy that keeps everything with nothing dropped, no observation delivered,
and -- for every trade print -- no aggressor side, which is what v3.11 recorded
(ledger FEA-004).
