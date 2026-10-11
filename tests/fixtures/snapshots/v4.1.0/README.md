# Golden v4.1.0 snapshot payload

v4.1.0 added one durable format -- a paused replay, `REPLAY_SNAPSHOT_SCHEMA = 1`
(ledger FUT-001) -- and moved no existing one, so this directory freezes that
format alone. `replay_paused.json` is the exact text `alphalab.persistence.serialize`
wrote for the workload of `tests/integration/replay_workload.py` paused after
eleven of its twenty-four records, produced by
`docs/audit/scripts/generate_v4_1_0_snapshot_fixtures.py`:

    PYTHONPATH=. python docs/audit/scripts/generate_v4_1_0_snapshot_fixtures.py \
        tests/fixtures/snapshots/v4.1.0

The workload is seeded and mints nothing from `uuid4`, so the script writes the
same bytes every time it runs against the same source.

| File | Subsystem | Schema at v4.1.0 |
| --- | --- | --- |
| `replay_paused.json` | replay envelope (run 5, pipeline 8 nested) | 1 |

`tests/regression/test_replay_pause_and_resume.py` restores it with the
workload's dataset and freshly built objects and resumes it to the
uninterrupted replay's result; the release certificate's REP-3 reads it with
every other release's frozen payloads. A later build reads it through
`REPLAY_SCHEMA_HISTORY`, or refuses it for the reason its upgrade documents.
