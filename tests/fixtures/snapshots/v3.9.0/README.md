# Golden v3.9.0 snapshot payloads

Every file here is the exact text AlphaLab **v3.9.0** wrote, produced by running
`docs/audit/scripts/generate_v3_9_0_snapshot_fixtures.py` against a checkout of
the `v3.9.0` tag (commit `c616978`). They are frozen: a later build either reads
each one through its subsystem's schema upgrades or refuses it with the reason
the upgrade documents. `tests/regression/test_schema_upgrades.py` holds that
contract.

| File | Subsystem | Schema at v3.9.0 |
| --- | --- | --- |
| `portfolio.json` | portfolio | 3 |
| `portfolio_fractional_yen.json` | portfolio (a JPY commission of 1.50 yen) | 3 |
| `oms.json` | oms | 1 |
| `allocation.json` | allocation | 1 |
| `instrument.json` | instrument registry | 1 |
| `lifecycle.json` | lifecycle | 2 |
| `fx_feed.json` | FX feed | 1 |
| `run_backtest.json` | run envelope (pipeline 3, portfolio 3, oms 1, allocation 1) | 1 |
| `live.json` | live envelope (run 1, broker 1) | 1 |
| `broker.json` | broker | 1 |
| `run_store/…/00000000000000000001.runstate` | `FileRunStateStore` envelope around `run_backtest.json` | — |

The identifiers in `allocation.json` are random: the v3.9.0 test the state was
built with mints them with `uuid4`. Nothing reads them as anything but opaque
strings.
