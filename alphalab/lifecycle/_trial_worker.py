"""The process a spawned trial runs in (v4.3, ADR-0072 section 6).

Run as ``python -m alphalab.lifecycle._trial_worker`` by
:class:`~alphalab.lifecycle.experiments.SpawnExecutor`, never imported for its
effects: importing it does nothing. The process reads one JSON request from its
standard input and, in this order:

1. installs an audit hook that refuses the network -- the mechanism the
   installed smoke test uses -- before anything of the researcher's is
   imported, so a module that reaches for the network as it is imported is
   refused as surely as a runner that does so while it runs;
2. imports the experiment's definition from where the executor was told, in
   code, it lives, and holds it to the experiment the parent submitted;
3. runs the one trial the request names, by its identity, through the runner
   the request names, looked up in the definition's registry -- under the
   experiment's time limit, timed from here, so that the interpreter's start and
   its imports are never the trial's: a trial still running at the limit ends
   the process, with status :data:`TIMED_OUT`, and is interrupted;
4. writes what the trial left, as one JSON line, to the standard output it was
   given. Anything the trial prints goes to the standard error.

A separate process is not a secure sandbox (ADR-0056, ledger BDY-029): the hook
guards against accidents and records intent, and isolating untrusted code is
the host's.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from typing import Any, TextIO

#: The audit events a trial may not raise: the network, by any route Python takes.
NETWORK_EVENTS = ("socket.", "http.client.connect", "urllib.Request", "ssl.")

#: The status a worker ends with when its trial outran the experiment's time limit.
TIMED_OUT = 124


def refuse_the_network(event: str, args: tuple[Any, ...]) -> None:
    """The audit hook: a network event raises, naming it."""

    if event.startswith(NETWORK_EVENTS):
        raise PermissionError(f"the network is refused to a trial: {event}")


def _load(location: str) -> Any:
    module, _, attribute = location.partition(":")
    return getattr(importlib.import_module(module), attribute)


def serve(request: dict[str, Any], output: TextIO) -> None:
    """Run the trial ``request`` names and write what it left to ``output``."""

    from alphalab.lifecycle.experiments import ExperimentDefinition, _ran_to_payload, run_trial

    try:
        definition = _load(request["definition"])
        if not isinstance(definition, ExperimentDefinition):
            raise TypeError(
                f"{request['definition']} is a {type(definition).__name__}, not an "
                "ExperimentDefinition"
            )
        spec = definition.spec
        if spec.experiment_id != request["experiment_id"]:
            output.write(
                json.dumps(
                    {
                        "mismatch": f"{request['definition']} defines experiment "
                        f"{spec.experiment_id}, not {request['experiment_id']}"
                    }
                )
                + "\n"
            )
            return
        trial = next(t for t in spec.trials() if t.trial_id == request["trial_id"])
        runner = definition.registry.runner(request["runner"])
    except Exception as error:
        output.write(json.dumps({"failed": f"{type(error).__name__}: {error}"}) + "\n")
        return
    watchdog = _watch(spec.limits.seconds)
    ran = run_trial(spec, runner, trial)
    if watchdog is not None:
        watchdog.cancel()
    output.write(json.dumps(_ran_to_payload(ran), allow_nan=False) + "\n")


def _watch(seconds: float | None) -> threading.Timer | None:
    """End the process if the trial is still running ``seconds`` from now."""

    if seconds is None:
        return None
    watchdog = threading.Timer(seconds, os._exit, args=(TIMED_OUT,))
    watchdog.daemon = True
    watchdog.start()
    return watchdog


def main() -> int:
    request = json.loads(sys.stdin.read())
    # Before anything of the researcher's is imported: the definition's module
    # and everything it imports load with the network already refused.
    sys.addaudithook(refuse_the_network)
    sys.path[:0] = [entry for entry in request.get("path", ()) if entry not in sys.path]
    output = sys.stdout
    sys.stdout = sys.stderr
    try:
        serve(request, output)
    finally:
        sys.stdout = output
    output.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
