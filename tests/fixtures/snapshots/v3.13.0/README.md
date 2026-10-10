# Golden v3.13.0 snapshot payloads

Every file here is the exact text AlphaLab **v3.13.0** wrote, produced by running
`docs/audit/scripts/generate_v3_13_0_snapshot_fixtures.py` against a checkout of
the `v3.13.0` tag (commit `6e9daea`):

    git archive v3.13.0 | tar -x -C /tmp/alphalab-v3.13.0
    cd /tmp/alphalab-v3.13.0 && PYTHONPATH=. python3.12 \
        docs/audit/scripts/generate_v3_13_0_snapshot_fixtures.py <out>

That script runs `generate_v3_11_0_snapshot_fixtures.py` unchanged -- every API
it calls is the same at v3.13.0 -- and adds a checkpoint chain of checkpoint
schema 2, which v3.13 introduced (ledger PRF-011). The generator mints some
identifiers from `uuid4`, so two runs differ in those and nothing else; these
files are one run, frozen. A later build either reads each one through its
subsystem's schema upgrades or refuses it with the reason the upgrade documents.
`tests/regression/test_schema_upgrades_v3_13.py` holds that contract, as
`test_schema_upgrades_v3_12.py`, `test_schema_upgrades_v3_11.py` and
`test_schema_upgrades.py` do for v3.12.0, v3.11.0 and v3.9.0.

| File | Subsystem | Schema at v3.13.0 | At v4.0.0 |
| --- | --- | --- | --- |
| `portfolio.json` | portfolio | 5 | 5 (unchanged) |
| `oms.json` | oms | 2 | 2 (unchanged) |
| `allocation.json` | allocation | 3 | 3 (unchanged) |
| `allocation_eur.json` | allocation, its budget in EUR | 3 | 3 (unchanged) |
| `instrument.json` | instrument registry, a dated alias included | 3 | 3 (unchanged) |
| `lifecycle.json` | lifecycle | 2 | 2 (unchanged) |
| `fx_feed.json` | FX feed | 1 | 1 (unchanged) |
| `run_backtest.json` | run envelope (pipeline 7) | 4 | 5 (pipeline 8) |
| `run_ticks.json` | run envelope whose market history holds trade prints | 4 | 5 (pipeline 8) |
| `live.json` | live envelope (run 4, broker 2) | 2 | 2 (run 5, pipeline 8) |
| `broker.json` | broker | 2 | 2 (unchanged) |
| `run_store/…/00000000000000000001.runstate` | `FileRunStateStore` envelope around `run_backtest.json` | — | — |
| `checkpoints/chain_{0,1,2}.json` | a base and two segments, taken at records 8, 28 and 48 | checkpoint 2 (run 4) | 2 (unchanged; run 5 inside once read) |
| `checkpoints/full_47.json` | the full capture of the state the chain's last link reaches | run 4 | 5 |

Why they exist (ledger PER-008): v3.13.0 recorded a run's sizing model, simulator
and fill policy by type alone, and v4.0 records how each was configured as well
(pipeline 7 -> 8, run 4 -> 5). The upgrade reads a v3.13.0 payload as one that
did not record the configuration -- `None` -- and its objects are then checked by
type alone, as the release that wrote it did. These payloads are the evidence it
does, on what v3.13.0 actually wrote rather than on a payload rewritten by hand.
