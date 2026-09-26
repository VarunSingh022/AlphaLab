"""
AlphaLab Examples
=================

Example 46 : Strategy Fingerprints

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 12 (model and strategy lifecycle)
✓ Example 42 (deployment specifications)

Topics
------

• One immutable identity for one strategy version
• Five defining inputs: code, dependencies, parameters, research, engine
• Each input changing the identity on its own -- and saying which one did
• Presentation that does not: mapping order, file order, name spelling
• How complete a dependency record is, stated rather than assumed
• The same identity in a second interpreter with a different hash seed

What this shows
---------------

A ``StrategyVersionRef`` is ``name@N`` -- a registration-order label. Two
registries number the same content differently, and nothing about ``@3`` says
which code ran. A ``StrategyFingerprint`` is derived from what the strategy *is*:
the code (identified by its source bytes), its dependencies (with how complete
that record is), its parameters (read from the registered version), the research
configuration it was validated under, and the engine version. Nothing about an
environment enters it, so it is the identity the strategy carries unchanged from
research to paper to live.

Nothing here reads the installed environment to decide dependencies: a lock
file's contents are the caller's to declare, and the declaration says whether it
is the whole closure.

Run

    python examples/46_strategy_fingerprints.py
"""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from _strategy_evidence import CLASSES, DEFINITION, SOURCES, STRATEGY_ID, banner

from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    UNDECLARED_DEPENDENCIES,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    LifecycleInputError,
    LifecycleState,
    StrategyFingerprint,
    canonical_fingerprint_key,
    code_identity_for,
    fingerprint_differences,
    fingerprint_for_version,
    get_strategy_version,
    register_strategy,
    research_configuration,
    running_engine,
    source_digest,
    verify_fingerprint,
)

#: The engine the version is fingerprinted under, stated for this example so
#: its printed identities are the same on every machine. In practice a caller
#: records ``running_engine()`` -- see section 1.
ENGINE = EngineIdentity("alphalab", "3.6.0")

#: The methodology the version was validated under, in the caller's words.
RESEARCH = research_configuration(
    {
        "validation": "single in-sample backtest over EX-PRICES",
        "execution": "ExecutionSimulator defaults, ImmediateFill",
    }
)

#: A lock file's worth of pins, declared as the complete closure. AlphaLab has
#: no resolver: it records how complete this is, it cannot check it.
LOCKED = DependencyManifest(
    DependencyCompleteness.EXACT_CLOSURE,
    (
        DependencyPin("numpy", "1.26.4", "4e3d" + "0" * 60),
        DependencyPin("scipy", "1.13.1"),
    ),
)

#: Run in a fresh interpreter by section 7. Everything it prints is derived.
_SECOND_PROCESS = """
import sys
sys.path.insert(0, {examples!r})
from _strategy_evidence import CLASSES, DEFINITION, SOURCES, STRATEGY_ID
from alphalab.lifecycle import (
    NO_DEPENDENCIES, EngineIdentity, LifecycleState, code_identity_for, fingerprint_for_version,
    get_strategy_version, register_strategy, research_configuration,
)
state, ref = register_strategy(LifecycleState(), "ex-momentum", DEFINITION, 1.0)
version = get_strategy_version(state.strategies, ref.name, ref.version)
code = code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.6.0", SOURCES)
research = research_configuration({{
    "validation": "single in-sample backtest over EX-PRICES",
    "execution": "ExecutionSimulator defaults, ImmediateFill",
}})
print(fingerprint_for_version(
    version, code, NO_DEPENDENCIES, research, EngineIdentity("alphalab", "3.6.0")
).fingerprint)
"""


def main() -> None:
    banner(46, "Strategy Fingerprints")

    # ----------------------------------------------------------------- #
    # 1. Everything a fingerprint is derived from
    # ----------------------------------------------------------------- #

    print("\n[1] A registered version, its code, and the engine")
    state, reference = register_strategy(LifecycleState(), "ex-momentum", DEFINITION, 1.0)
    version = get_strategy_version(state.strategies, reference.name, reference.version)
    registration = CLASSES.require(STRATEGY_ID)
    code = code_identity_for(registration, "alphalab-examples", "3.6.0", SOURCES)
    print(f"  version       : {version.ref} (a registration-order label)")
    print(f"  entry point   : {code.entry_point}  (read from the class registry)")
    print(f"  source digest : {code.source_digest}")
    print(f"  this engine   : {running_engine()}  (what a caller would record)")

    fingerprint = fingerprint_for_version(version, code, NO_DEPENDENCIES, RESEARCH, ENGINE)
    print(f"\n  fingerprint   : {fingerprint.fingerprint}")
    print(f"  parameters    : {dict(fingerprint.parameters)}  (from the version, not retyped)")

    key = canonical_fingerprint_key(
        fingerprint.name,
        fingerprint.strategy_id,
        fingerprint.code,
        fingerprint.dependencies,
        fingerprint.parameters,
        fingerprint.research,
        fingerprint.engine,
    )
    print("\n  the canonical key it hashes (every value rendered with repr):")
    for line in key.split("\n"):
        print(f"    {line}")

    # ----------------------------------------------------------------- #
    # 2. Each defining input, changed on its own
    # ----------------------------------------------------------------- #

    print("\n[2] Change one input at a time")
    edited_source = {**SOURCES, "_strategy_evidence.py": SOURCES["_strategy_evidence.py"] + b"#"}
    variants: dict[str, StrategyFingerprint] = {
        "code": fingerprint_for_version(
            version,
            replace(code, source_digest=source_digest(edited_source)),
            NO_DEPENDENCIES,
            RESEARCH,
            ENGINE,
        ),
        "dependencies": fingerprint_for_version(version, code, LOCKED, RESEARCH, ENGINE),
        "parameters": fingerprint_for_version(
            replace(
                version,
                definition=replace(DEFINITION, parameters={"entry": 1200.0, "exit": -400.0}),
            ),
            code,
            NO_DEPENDENCIES,
            RESEARCH,
            ENGINE,
        ),
        "research": fingerprint_for_version(
            version,
            code,
            NO_DEPENDENCIES,
            research_configuration({**RESEARCH.settings, "validation": "walk-forward 5x"}),
            ENGINE,
        ),
        "engine": fingerprint_for_version(
            version, code, NO_DEPENDENCIES, RESEARCH, EngineIdentity("alphalab", "3.6.1")
        ),
    }
    for label, variant in variants.items():
        # fingerprint_differences names each input that moved; the first is enough here.
        changed = fingerprint_differences(fingerprint, variant)[0].split(":")[0]
        print(f"  {label:13}: {variant.digest[:16]}...  changed: {changed}")

    # ----------------------------------------------------------------- #
    # 3. What does not change it
    # ----------------------------------------------------------------- #

    print("\n[3] Presentation is not identity")
    reordered = fingerprint_for_version(
        replace(
            version,
            definition=replace(DEFINITION, parameters={"exit": -400.0, "entry": 1000.0}),
        ),
        replace(code, source_digest=source_digest(dict(reversed(list(SOURCES.items()))))),
        NO_DEPENDENCIES,
        research_configuration(dict(reversed(list(RESEARCH.settings.items())))),
        ENGINE,
    )
    respelled = DependencyManifest(
        DependencyCompleteness.EXACT_CLOSURE,
        (DependencyPin("SciPy", "1.13.1"), DependencyPin("NumPy", "1.26.4", "4e3d" + "0" * 60)),
    )
    respelled_matches = (
        fingerprint_for_version(version, code, respelled, RESEARCH, ENGINE).fingerprint
        == variants["dependencies"].fingerprint
    )
    renumbered = fingerprint_for_version(
        replace(version, version=9), code, NO_DEPENDENCIES, RESEARCH, ENGINE
    )
    print(f"  mapping and file order reversed : {reordered.fingerprint == fingerprint.fingerprint}")
    print(f"  pins respelled and reordered    : {respelled_matches}")
    print(
        f"  the version number (@1 -> @9)   : {renumbered.fingerprint == fingerprint.fingerprint}"
    )

    # ----------------------------------------------------------------- #
    # 4. How complete the dependency record is
    # ----------------------------------------------------------------- #

    print("\n[4] Three dependency claims, three identities")
    direct = DependencyManifest(
        DependencyCompleteness.DIRECT_ONLY, (DependencyPin("numpy", "1.26.4"),)
    )
    for label, manifest in (
        ("no dependencies (exact)", NO_DEPENDENCIES),
        ("a locked closure", LOCKED),
        ("direct pins only", direct),
        ("nothing declared", UNDECLARED_DEPENDENCIES),
    ):
        derived = fingerprint_for_version(version, code, manifest, RESEARCH, ENGINE)
        print(f"  {label:24}: {manifest.completeness.name:14} {derived.digest[:16]}...")
    try:
        DependencyPin("numpy", ">=1.26")
    except LifecycleInputError as error:
        print(f"  a range is refused: {error}")

    # ----------------------------------------------------------------- #
    # 5. Where the source digest refuses to go
    # ----------------------------------------------------------------- #

    print("\n[5] A path that names a machine never enters an identity")
    try:
        source_digest({"/home/someone/strategies/momentum.py": b"..."})
    except LifecycleInputError as error:
        print(f"  refused: {error}")

    # ----------------------------------------------------------------- #
    # 6. Tampering
    # ----------------------------------------------------------------- #

    print("\n[6] An edited fingerprint stops verifying")
    edited = replace(fingerprint, parameters={"entry": 5000.0, "exit": -400.0})
    print(f"  original verifies : {verify_fingerprint(fingerprint)}")
    print(f"  edited verifies   : {verify_fingerprint(edited)}")

    # ----------------------------------------------------------------- #
    # 7. A second interpreter, another hash seed
    # ----------------------------------------------------------------- #

    print("\n[7] The same fingerprint from a fresh interpreter")
    examples = str(Path(__file__).resolve().parent)
    completed = subprocess.run(
        [sys.executable, "-c", _SECOND_PROCESS.format(examples=examples)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "271828"},
    )
    remote = completed.stdout.strip()
    print(f"  this process   : {fingerprint.fingerprint}")
    print(f"  second process : {remote}")

    # ----------------------------------------------------------------- #

    print("\n[8] Invariants")
    checks = (
        ("the fingerprint verifies", verify_fingerprint(fingerprint)),
        (
            "every defining input changes it",
            len({fingerprint.fingerprint, *(v.fingerprint for v in variants.values())}) == 6,
        ),
        ("presentation does not", reordered.fingerprint == fingerprint.fingerprint),
        ("an edit is detected", not verify_fingerprint(edited)),
        ("another process agrees", remote == fingerprint.fingerprint),
        ("no environment is an input", "environment" not in key and "BACKTEST" not in key),
    )
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    assert all(passed for _, passed in checks)

    print("\n" + "=" * 68)
    print("Example 46 complete.")
    print("(Nothing here read the installed environment to decide an identity: the")
    print(" dependencies, the engine and the research configuration were declared.)")


if __name__ == "__main__":
    main()
