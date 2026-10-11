"""Write the v4.1.0 golden replay payload that every later build must read (ledger FUT-001).

v4.1 adds one durable format -- a paused replay, ``REPLAY_SNAPSHOT_SCHEMA = 1``
-- and moves no existing one, so this freezes that format alone: the workload
of ``tests/integration/replay_workload.py`` paused after eleven records (inside
an instant: its first record is in, its second is due), captured and written as
``alphalab.persistence.serialize`` writes it. The workload is seeded and mints
nothing from ``uuid4``, so re-running this against the same source writes the
same bytes. Run from the repository root of the release it freezes::

    PYTHONPATH=. python docs/audit/scripts/generate_v4_1_0_snapshot_fixtures.py \\
        tests/fixtures/snapshots/v4.1.0

``tests/regression/test_replay_pause_and_resume.py`` reads it and resumes it, and
the release certificate's REP-3 reads every payload frozen beside it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(root: Path) -> None:
    from alphalab.backtesting.replay import PausedReplay
    from alphalab.backtesting.replay_snapshot import REPLAY_SNAPSHOT_SCHEMA, capture
    from alphalab.persistence import serialize
    from tests.integration import replay_workload

    assert REPLAY_SNAPSHOT_SCHEMA == 1, "this script freezes replay schema 1"
    paused = replay_workload.run(replay_workload.pause_at(11))
    assert isinstance(paused, PausedReplay)
    payload = serialize(capture(paused))
    root.mkdir(parents=True, exist_ok=True)
    (root / "replay_paused.json").write_text(payload + "\n", encoding="utf-8")
    print("wrote replay_paused.json", len(payload), "bytes")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
