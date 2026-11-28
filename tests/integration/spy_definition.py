"""An experiment definition that reaches for the network as it is imported (ADR-0072).

Only a spawned trial worker ever imports this, after it has refused the
network, so the attempt below is refused before it reaches anything. The
definition is the reference experiment's, so the worker holds it to the
experiment the parent submitted and fails only on the import.
"""

from __future__ import annotations

import socket

from tests.integration.experiment_workload import DEFINITION

# The spy: a socket opened at import, before any trial runs.
socket.socket().close()

__all__ = ["DEFINITION"]
