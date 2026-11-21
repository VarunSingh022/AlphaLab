"""The slow experiment's definition, behind an import longer than its trials' time limit (ADR-0072).

Only a spawned trial worker imports this. The import takes longer than the
experiment's two-second limit, and the trials do not: a limit times the trial
from the moment its worker has loaded the experiment, so an interpreter's start
and its imports -- which a busy host slows -- are never the trial's, and every
trial completes.
"""

from __future__ import annotations

import time

from tests.integration.experiment_workload import SLOW_DEFINITION

time.sleep(2.5)

DEFINITION = SLOW_DEFINITION

__all__ = ["DEFINITION"]
