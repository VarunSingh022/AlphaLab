"""The consumer contract script holds against the source tree as well (v4.1).

``tests/consumer_contract.py`` is what ``tests/installed_smoke.py`` runs against
each built artifact, from outside the checkout. Run here against the tree, in a
fresh interpreter and from outside the repository, so that the script cannot
fall out of step with the engine between releases -- which is when an artifact
check that never ran would be found broken.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import alphalab

ROOT = Path(__file__).resolve().parents[2]


def test_the_consumer_contract_holds_against_this_tree(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), environment.get("PYTHONPATH")])
    )
    done = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            str(ROOT / "tests" / "consumer_contract.py"),
            alphalab.__version__,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "consumer contract: 9 checks passed" in done.stdout


def test_the_installed_smoke_runs_the_consumer_contract() -> None:
    smoke = (ROOT / "tests" / "installed_smoke.py").read_text(encoding="utf-8")

    assert (
        '"tests" / "consumer_contract.py"' in smoke and "contract.main(expected_version)" in smoke
    )
    assert "71_pause_and_resume_a_replay.py" in smoke
