"""
AlphaLab Examples
=================

Example 47 : Reproducible Research Artifacts

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Example 16 (research from a dataset version)
✓ Example 24 (the research study)
✓ Example 46 (strategy fingerprints)

Topics
------

• What exact inputs produced this result?
• A manifest whose every identity is read from the authority that owns it
• A rerun that reproduces, one that diverges, and one of different inputs
• Why an unseeded run is refused rather than called reproducible
• What a rerun needs that AlphaLab does not hold
• A research study's result, with its seed -- or its absence -- stated

What this shows
---------------

A ``ReproducibilityManifest`` names the dataset version and the digest of its
bytes, the strategy fingerprint, the run's own recorded configuration, the seed
and what that seed does, the engine, and the result's identity -- the digest of
the run's complete canonical record. Nothing in it is typed by the caller: each
field is read from the run, the dataset's provenance, or the fingerprint.

``assess_reproducibility`` keeps four questions apart: does the identity
recompute, is anything it rests on approximate, did a rerun reproduce it, and
what would a rerun need from outside. "A digest exists" answers only the first.

Run

    python examples/47_reproducible_research_artifacts.py
"""

import json
from dataclasses import replace
from decimal import Decimal

from _research_panel import load_panel
from _strategy_evidence import (
    CLASSES,
    DEFINITION,
    SOURCES,
    STRATEGY_ID,
    banner,
    ingest_prices,
    run_backtest,
)

from alphalab.api import run_study
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import FixedSlippage
from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    LifecycleInputError,
    LifecycleState,
    ReproducibilityManifest,
    assess_reproducibility,
    code_identity_for,
    external_requirements,
    fingerprint_for_version,
    get_strategy_version,
    manifest_for_run,
    manifest_for_study,
    manifest_gaps,
    register_strategy,
    research_configuration,
    verify_manifest,
)
from alphalab.persistence.serializer import serialize
from alphalab.research import ResearchStudy

ENGINE = EngineIdentity("alphalab", "3.6.0")


def show(manifest: ReproducibilityManifest) -> None:
    print(f"  kind          : {manifest.kind.name}")
    print(f"  dataset       : {manifest.dataset_version}")
    print(f"  bytes         : {manifest.dataset_content_hash}")
    print(f"  configuration : {manifest.configuration_id}")
    print(f"  seed          : {manifest.seed} ({manifest.seed_role.name})")
    print(f"  engine        : {manifest.engine}")
    strategy = None if manifest.fingerprint is None else manifest.fingerprint.fingerprint
    print(f"  strategy      : {strategy}")
    print(f"  result        : {manifest.result_id}")
    print(f"  manifest id   : {manifest.manifest_id}")


def main() -> None:
    banner(47, "Reproducible Research Artifacts")

    dataset = ingest_prices()
    state, reference = register_strategy(LifecycleState(), "ex-momentum", DEFINITION, 1.0)
    version = get_strategy_version(state.strategies, reference.name, reference.version)
    code = code_identity_for(CLASSES.require(STRATEGY_ID), "alphalab-examples", "3.6.0", SOURCES)
    research = research_configuration({"validation": "single in-sample backtest"})
    fingerprint = fingerprint_for_version(version, code, NO_DEPENDENCIES, research, ENGINE)

    # ----------------------------------------------------------------- #
    # 1. A manifest for a real backtest
    # ----------------------------------------------------------------- #

    print("\n[1] What exact inputs produced this result?")
    result = run_backtest(dataset)
    manifest = manifest_for_run(result, dataset, fingerprint, ENGINE)
    show(manifest)
    recorded = json.loads(manifest.configuration)
    print(f"  (mode {recorded['mode']}, fill policy {recorded['fill_policy_type']},")
    print(f"   leverage cap {recorded['pipeline']['risk_limits']['leverage']['max_leverage']}x --")
    print("   read from the run's own snapshot, not rendered a second time)")

    # ----------------------------------------------------------------- #
    # 2. What a rerun needs from outside
    # ----------------------------------------------------------------- #

    print("\n[2] What AlphaLab does not hold")
    for requirement in external_requirements(manifest):
        print(f"  {requirement.input.name:20} {requirement.detail[:62]}...")
    print("  (Never empty: AlphaLab stores no dataset bytes and no strategy code, so")
    print("   no manifest describes a self-contained reproduction.)")

    # ----------------------------------------------------------------- #
    # 3. Four outcomes of a rerun
    # ----------------------------------------------------------------- #

    print("\n[3] Produce it again")
    rerun = manifest_for_run(run_backtest(dataset), dataset, fingerprint, ENGINE)
    reproduced = assess_reproducibility(manifest, rerun)
    print(f"  same declared inputs       : {reproduced.rerun.name}")

    other_seed = manifest_for_run(run_backtest(dataset, seed=1), dataset, fingerprint, ENGINE)
    differ = assess_reproducibility(manifest, other_seed)
    print(f"  another seed               : {differ.rerun.name} -- {differ.rerun_detail[0]}")

    slipped = manifest_for_run(
        run_backtest(
            dataset, simulator=ExecutionSimulator(slippage_model=FixedSlippage(Decimal("0.05")))
        ),
        dataset,
        fingerprint,
        ENGINE,
    )
    diverged = assess_reproducibility(manifest, slipped)
    print(f"  a simulator's hidden cost  : {diverged.rerun.name}")
    print("    (The record keeps the simulator by type, so the configuration matched;")
    print("     the result did not. Only a rerun could have caught that.)")
    print(f"    same configuration id    : {slipped.configuration_id == manifest.configuration_id}")

    # ----------------------------------------------------------------- #
    # 4. Metadata completeness
    # ----------------------------------------------------------------- #

    print("\n[4] What a manifest rests on that is approximate")
    loose = fingerprint_for_version(
        version,
        replace(code, source_digest=None),
        DependencyManifest(DependencyCompleteness.DIRECT_ONLY, (DependencyPin("numpy", "1.26.4"),)),
        research,
        ENGINE,
    )
    loose_manifest = manifest_for_run(result, dataset, loose, ENGINE)
    print(f"  complete manifest gaps : {manifest_gaps(manifest)}")
    for gap in manifest_gaps(loose_manifest):
        print(f"  loose manifest gap     : {gap[:70]}...")

    # ----------------------------------------------------------------- #
    # 5. The seed is load-bearing
    # ----------------------------------------------------------------- #

    print("\n[5] An unseeded run")
    try:
        manifest_for_run(run_backtest(dataset, seed=None), dataset, fingerprint, ENGINE)
    except LifecycleInputError as error:
        print(f"  refused: {str(error)[:96]}...")

    # ----------------------------------------------------------------- #
    # 6. A research study
    # ----------------------------------------------------------------- #

    print("\n[6] A research study's result")
    panel = load_panel()
    study = ResearchStudy(
        study_name="panel-momentum",
        dataset_version=panel.require_provenance().dataset_version,
        universe=tuple(sorted({record.symbol for record in panel.records})),
        features=(
            FeatureDefinition("mom_10", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=10),
        ),
        horizons=(1, 5),
    )
    study_result = run_study(study, panel, produced_at=1.0)
    study_manifest = manifest_for_study(study_result, panel, ENGINE)
    print(f"  study         : {study_result.study_id[:40]}...")
    print(f"  metrics       : {len(study_result.metrics)}")
    print(f"  seed          : {study_manifest.seed} ({study_manifest.seed_role.name})")
    print("  (No stochastic step, so no seed -- recorded as absent, never invented.)")
    again = assess_reproducibility(
        study_manifest, manifest_for_study(run_study(study, panel, produced_at=99.0), panel, ENGINE)
    )
    print(f"  rerun a day later : {again.rerun.name}  (produced_at is not an input)")

    # ----------------------------------------------------------------- #
    # 7. Machine-readable
    # ----------------------------------------------------------------- #

    print("\n[7] The manifest as an application would receive it")
    payload = serialize(manifest)
    print(f"  deterministic JSON, {len(payload)} bytes; top-level fields:")
    print(f"  {sorted(json.loads(payload))}")

    # ----------------------------------------------------------------- #

    print("\n[8] Invariants")
    checks = (
        ("the manifest's identity recomputes", verify_manifest(manifest)),
        ("the same inputs reproduce the result", reproduced.rerun.name == "REPRODUCED"),
        ("another seed is another input, not a failure", differ.rerun.name == "INPUTS_DIFFER"),
        ("a hidden change diverges", diverged.rerun.name == "DIVERGED"),
        ("a complete manifest has no gaps", manifest_gaps(manifest) == ()),
        ("an approximate one says so", len(manifest_gaps(loose_manifest)) == 2),
        ("the study reproduces a day later", again.rerun.name == "REPRODUCED"),
        ("an edited manifest stops verifying", not verify_manifest(replace(manifest, seed=7))),
        ("the dataset is named, not located", "examples/data" not in serialize(study_manifest)),
    )
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAILED'}] {label}")
    assert all(passed for _, passed in checks)

    print("\n" + "=" * 68)
    print("Example 47 complete.")
    print("(No result was stored anywhere: a manifest identifies a result and its")
    print(" inputs, and a rerun is the only evidence that it can be produced again.)")


if __name__ == "__main__":
    main()
