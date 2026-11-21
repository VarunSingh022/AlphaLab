"""The reference experiment's definition, counting the workers that load it (ADR-0072).

Only a spawned trial worker imports this. Each import appends the worker's
process id to the file ``ALPHALAB_DEFINITION_IMPORTS`` names, so a test can
count how many workers an experiment started: a worker loads the experiment
once, however many trials it runs.
"""

from __future__ import annotations

import os

from tests.integration.experiment_workload import DEFINITION

_LOG = os.environ.get("ALPHALAB_DEFINITION_IMPORTS")
if _LOG:
    with open(_LOG, "a", encoding="utf-8") as log:
        log.write(f"{os.getpid()}\n")

__all__ = ["DEFINITION"]
