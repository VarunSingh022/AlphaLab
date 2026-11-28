# Golden v4.2.1 snapshot payloads

Every file here is the exact text AlphaLab **v4.2.1** wrote, produced by
`docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py` against an export of
the `v4.2.1` tag (commit `8f33234`), with this repository's copy of the script
and of `tests/regression/v4_2_1_workloads.py`:

```bash
REPO="$(git rev-parse --show-toplevel)"
EXPORT="$(mktemp -d)"
OUT="$(mktemp -d)/v4.2.1"
git archive v4.2.1 | tar -x -C "${EXPORT}"
cd "${EXPORT}" && PYTHONPATH=. python3.12 \
    "${REPO}/docs/audit/scripts/generate_v4_2_1_snapshot_fixtures.py" "${OUT}"
```

Every identifier comes from a seeded stream, so the script writes these bytes on
every run. Run against the `v4.2.0` tag with `--release 4.2.0`, it writes the same
bytes, file for file: v4.2.1 changed docstrings and the version string only, so
this one set is what both releases wrote (G0 decision 20, D-C1). Before writing
each paused replay and the checkpoint chain, the script held the release to its
own contract: read back and resumed with freshly built objects, each finished
as the uninterrupted run did.

`tests/regression/test_schema_upgrades_v4_2_1.py` holds the contract for every
later build: each payload is decoded and written back byte for byte; each paused
replay, restored with its workload's freshly built objects, resumes to the
`result_id` v4.2.1 computed for the run that never paused; the checkpoint chain
continues to its own; each registry's digest is the one its replay recorded; and
the evidence store reads back verified. The certificate's REP-3 decodes every
top-level payload with every other release's. A later build reads each through
its subsystem's `SchemaHistory`, or refuses it for the reason its upgrade
documents.

| File | Subsystem | Schema at v4.2.1 |
| --- | --- | --- |
| `portfolio.json` | portfolio | 5 |
| `oms.json` | OMS | 2 |
| `allocation.json`, `allocation_eur.json` | allocation; the second budgeted in EUR | 3 |
| `instrument.json` | instrument registry, a dated alias included | 4 |
| `lifecycle.json` | lifecycle | 2 |
| `fx_feed.json` | FX feed | 1 |
| `run_backtest.json` | run envelope (pipeline 8) | 5 |
| `run_ticks.json` | run envelope whose market history holds trade prints | 5 |
| `live.json` | live envelope (run 5, broker 2) | 3 |
| `broker.json` | broker | 2 |
| `run_store/…/00000000000000000001.runstate` | `FileRunStateStore` envelope around `run_backtest.json` | run-state envelope 1 |
| `replay_*.json` (eleven) | paused replays, the run nested (run 5, pipeline 8) | 3 |
| `instrument_*.json` (nine) | the instrument registry each replay held | 4 |
| `checkpoints/chain_{0,1,2}.json` | a base and two segments, taken after records 6, 12 and 18 of the replay workload run as a session | checkpoint 2 (run 5) |
| `checkpoints/full_18.json` | the full capture of the state the chain's last link reaches | run 5 |
| `evidence_store/` | a reproducibility manifest and a strategy fingerprint in a file evidence store | evidence envelope 1 |
| `expected/identities.json` | what v4.2.1 computed from them: each uninterrupted run's `result_id`, each registry's digest, each piece of evidence's reference | — |

The first eleven rows are what `generate_v3_11_0_snapshot_fixtures.py` writes,
run unchanged as it was for v3.12.0 and v3.13.0, so each subsystem's payload can
be compared release by release. `expected/` is a directory of its own because
REP-3 decodes every top-level `*.json` as a payload. The run store and the
evidence store are bytes their digests are taken over: a checkout must not
convert their line endings.

Each paused replay is a workload `tests/regression/v4_2_1_workloads.py` names,
paused after the record given:

| File | Workload | Paused after | What no earlier frozen payload held |
| --- | --- | --- | --- |
| `replay_paused.json` | `tests/integration/replay_workload.py` | 11 of 24 | the workload v4.1.0 froze at replay schema 1, written at schema 3 |
| `replay_execution_plan.json` | example 74 | 7 of 60 | an algorithm book: a TWAP parent with children working (FUT-009) |
| `replay_queue.json` | `tests/integration/test_queue_model.py` | 2 of 5 | a queue book: a resting limit order and the quantity ahead of it (FUT-011) |
| `replay_option_life.json` | example 75 | 40 of 95 | options with contract terms and settlement; scheduled exercises and expiries |
| `replay_financed_book.json` | example 76 | 6 of 10 | a margin model, financing, borrow fees and a holding exchange (FUT-017 to FUT-021) |
| `replay_recalled_short.json` | example 77 | 5 of 9 | a recall and its buy-in resting through a halt; daily accruals (FUT-022 to FUT-029) |
| `replay_prorated_tender.json` | example 78 | 10 of 17 | elections, a record instant, a paid exchange and its amendment (FUT-030, FUT-034) |
| `replay_reopening_auction.json` | example 79 | 6 of 7 | declared auction books and indications, and a held reopening (FUT-027, FUT-040) |
| `replay_delivered_surfaces.json` | `tests/integration/test_surface_feeds.py` | 3 of 6 | volatility surfaces delivered as data, kept as they arrived (FUT-039) |
| `replay_delivered_auctions.json` | `tests/integration/test_live_auctions.py` | 4 of 6 | auction books and an indication delivered as data, and a reopening (FUT-043) |
| `replay_observations.json` | `tests/regression/test_heterogeneous_inputs_on_every_driver.py` | 4 of 24 | point-in-time observations delivered by the schedule (FUT-003) |

Why they exist (plan GAP-07): until this directory, the only frozen payload any
4.x release wrote was v4.1.0's `replay_paused.json`, at replay schema 1. v4.1.1
and v4.2.0 moved the instrument registry to 4, the paused replay to 3 and the
live envelope to 3, and added terms each written only when a run declares them.
These are those formats as the release put them on disk, read by every later
build rather than by payloads rewritten by hand.

A frozen payload is restored only with the workload that wrote it. A change to
one of these workloads makes the restore refuse its payload, naming the dataset,
schedule or object that differs; the answer is to keep the v4.2.1 workload, never
to regenerate a fixture.
