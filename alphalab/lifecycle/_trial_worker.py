"""The process spawned trials run in (v4.3, ADR-0072 section 6).

Run as ``python -m alphalab.lifecycle._trial_worker`` by
:class:`~alphalab.lifecycle.experiments.SpawnExecutor` and
:class:`~alphalab.lifecycle.experiments.ClusterExecutor`, never imported for its
effects: importing it does nothing. The process reads JSON lines from its
standard input -- first a header naming where the experiment is defined, then
one request per trial -- and, in this order:

1. installs an audit hook that refuses the network -- the mechanism the
   installed smoke test uses -- before anything of the researcher's is
   imported, so a module that reaches for the network as it is imported is
   refused as surely as a runner that does so while it runs;
2. imports the experiment's definition, once, from where the executor was told,
   in code, it lives;
3. for each request, holds the definition to the experiment the parent
   submitted and runs the one trial the request names, by its identity,
   through the runner the request names, looked up in the definition's
   registry -- under the experiment's time limit, timed from the request, so
   that the interpreter's start and its imports are never a trial's: a trial
   still running at the limit ends the process, with status
   :data:`TIMED_OUT`, and is interrupted;
4. writes what each trial left, as one JSON line, to the standard output it was
   given, and ends when its input does. Anything a trial writes to the standard
   output, by Python or not, goes to the standard error.

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
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from alphalab.lifecycle.experiments import ExperimentDefinition, TrialSpec

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


class _Loaded:
    """An experiment's definition, loaded once, or why it did not load."""

    def __init__(self, location: str) -> None:
        from alphalab.lifecycle.experiments import ExperimentDefinition, every_trial

        self.location = location
        self.definition: ExperimentDefinition | None = None
        self.trials: dict[str, TrialSpec] = {}
        self.failure = ""
        try:
            definition = _load(location)
            if not isinstance(definition, ExperimentDefinition):
                raise TypeError(
                    f"{location} is a {type(definition).__name__}, not an ExperimentDefinition"
                )
            # Every trial the experiment can name -- a fold's test-window trial
            # among them, whichever candidate the parent's ledger selected.
            self.trials = {trial.trial_id: trial for trial in every_trial(definition.spec)}
            self.definition = definition
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"


def serve(loaded: _Loaded, request: dict[str, Any], output: TextIO) -> None:
    """Run the trial ``request`` names and write what it left to ``output``."""

    from alphalab.lifecycle.experiments import _ran_to_payload, run_trial

    definition = loaded.definition
    if definition is None:
        output.write(json.dumps({"failed": loaded.failure}) + "\n")
        return
    spec = definition.spec
    if spec.experiment_id != request["experiment_id"]:
        output.write(
            json.dumps(
                {
                    "mismatch": f"{loaded.location} defines experiment "
                    f"{spec.experiment_id}, not {request['experiment_id']}"
                }
            )
            + "\n"
        )
        return
    try:
        trial = loaded.trials[request["trial_id"]]
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
    header = json.loads(sys.stdin.readline())
    # Before anything of the researcher's is imported: the definition's module
    # and everything it imports load with the network already refused.
    sys.addaudithook(refuse_the_network)
    sys.path[:0] = [entry for entry in header.get("path", ()) if entry not in sys.path]
    # Answers go to a copy of the standard output, and the standard output
    # itself to the standard error, so nothing a trial writes reaches them.
    answers = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    loaded = _Loaded(header["definition"])
    for line in sys.stdin:
        if line.strip():
            serve(loaded, json.loads(line), answers)
            answers.flush()
    answers.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
