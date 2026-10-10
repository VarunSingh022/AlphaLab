"""Write the v3.13.0 golden snapshot payloads that v4.0's upgrade tests read.

This script targets the **v3.13.0 API** and is kept for provenance, not run by
the suite: the payloads it writes are frozen in
``tests/fixtures/snapshots/v3.13.0`` and every later build must keep reading
them (or refuse them for the documented reason). Re-running it requires a
checkout of the v3.13.0 tag::

    git archive v3.13.0 | tar -x -C /tmp/alphalab-v3.13.0
    cd /tmp/alphalab-v3.13.0 && PYTHONPATH=. python3.12 \\
        <repo>/docs/audit/scripts/generate_v3_13_0_snapshot_fixtures.py \\
        <repo>/tests/fixtures/snapshots/v3.13.0

It writes what ``generate_v3_11_0_snapshot_fixtures.py`` writes -- that script
is run unchanged, as it was for v3.12.0, since every API it calls is the same at
v3.13.0 -- and one thing more: a checkpoint chain of checkpoint schema 2, which
v3.13 introduced (ledger PRF-011), with the full capture of the state its last
link reaches. Every payload is the exact text ``alphalab.persistence.serialize``
wrote at v3.13.0. v4.0 moved two subsystems -- pipeline 7 -> 8 and run 4 -> 5,
to record how the live objects were configured (ledger PER-008) -- and left the
rest, the checkpoint envelope included, where they were.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _checkpoint_chain(root: Path) -> None:
    from alphalab.persistence import serialize
    from alphalab.runtime.checkpoint import checkpoint
    from alphalab.runtime.run_snapshot import capture as capture_run
    from tests.regression.test_retention import POLICY, _run

    states = _run(POLICY)
    folder = root / "checkpoints"
    folder.mkdir(exist_ok=True)
    mark = None
    for index, taken in enumerate((7, 27, 47)):
        payload, mark = checkpoint(states[taken], mark)
        (folder / f"chain_{index}.json").write_text(payload + "\n", encoding="utf-8")
        print("wrote", f"checkpoints/chain_{index}.json", len(payload), "bytes")
    full = serialize(capture_run(states[47]))
    (folder / "full_47.json").write_text(full + "\n", encoding="utf-8")
    print("wrote checkpoints/full_47.json", len(full), "bytes")


def main(root: Path) -> None:
    import alphalab

    assert alphalab.__version__ == "3.13.0", (
        f"run against the v3.13.0 tag, not {alphalab.__version__}"
    )
    spec = importlib.util.spec_from_file_location(
        "generate_v3_11_0_snapshot_fixtures",
        Path(__file__).with_name("generate_v3_11_0_snapshot_fixtures.py"),
    )
    assert spec is not None and spec.loader is not None
    earlier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = earlier
    spec.loader.exec_module(earlier)
    earlier.main(root)
    _checkpoint_chain(root)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
