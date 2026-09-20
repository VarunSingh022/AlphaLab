"""
AlphaLab Examples
=================

Example 24 : The Complete Strategy Research Validation Pipeline

Difficulty : Advanced

Estimated Time : 15 minutes

Prerequisites
-------------

✓ Examples 17-23

Topics
------

• A ResearchStudy: the experiment stated completely, with a derived identity
• Running it end to end from a canonical dataset
• A StudyResult whose identity is derived from the numbers it produced
• Validation, robustness and overfitting diagnostics over the same study
• Recording the result as ValidationEvidence and gating a promotion on it
• Tamper evidence: numbers edited after the fact stop verifying

What this shows
---------------

The whole v3.2 path, once::

    CSV bytes
      -> Dataset              identity derived from content + configuration
      -> ObservationFrame     one field, carrying that identity
      -> FeaturePanel         identity derived from dataset + definition
      -> forward returns      the one quantity that looks ahead
      -> diagnostics          with the sample counts attached
      -> StudyResult          identity derived from the study + the numbers
      -> ValidationEvidence   the frozen digest, unchanged since v2.6

Nothing in that chain can be substituted: a study names its dataset version and
`run_study` refuses one that does not match.

Run

    python examples/24_strategy_research_pipeline.py
"""

from dataclasses import replace

from _research_panel import banner, load_panel

from alphalab.api import ResearchStudy, observe, run_study, study_panels
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    factor_decay,
    forward_returns,
)
from alphalab.lifecycle import (
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    evaluate_policy,
    evidence_from_study,
    verify_evidence_id,
)
from alphalab.lifecycle.exceptions import LifecycleInputError
from alphalab.research import (
    CVMethod,
    OverfittingPolicy,
    PurgePolicy,
    ResearchValidationError,
    WindowMode,
    build_overfitting_report,
    cross_validation_splits,
    delay_signal,
    label_ends_from_horizon,
    parameter_sweep,
    period_stability,
    sample_degradation,
    signal_diagnostics,
    walk_forward_splits,
)

SEED = 20260920
PRODUCED_AT = 1_726_000_100.0
SUBJECT = "strategy://research-panel-momentum@1"

FEATURES = (
    FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20),
    FeatureDefinition("rev_10", FeatureKind.MEAN_REVERSION, FeatureField.CLOSE, window=10),
    FeatureDefinition("vol_20", FeatureKind.REALIZED_VOLATILITY, FeatureField.CLOSE, window=20),
)


def main() -> None:
    banner(24, "The Complete Strategy Research Validation Pipeline")

    # ------------------------------------------------------------------
    # Step 01 : The dataset, and the study written against it
    # ------------------------------------------------------------------

    dataset = load_panel()
    version = dataset.require_provenance().dataset_version
    frame = observe(dataset, FeatureField.CLOSE)
    instants = list(frame.timestamps)

    study = ResearchStudy(
        study_name="momentum-reversion-panel",
        dataset_version=version,
        universe=frame.symbols,
        features=FEATURES,
        horizons=(1, 5, 20),
        splits="walk_forward(mode=ROLLING,train=60,validation=20,test=20,step=20)",
        seed=SEED,
        notes="v3.2 complete pipeline example",
    )

    print()
    print("Step 01 - The experiment, stated completely")
    print(f"  study           : {study.study_name}")
    print(f"  dataset version : {version[:54]}...")
    print(f"  universe        : {len(study.universe)} symbols")
    print(f"  features        : {[definition.feature_id for definition in study.features]}")
    print(f"  horizons        : {study.horizons}")
    print(f"  seed            : {study.seed}")
    print(f"  study id        : {study.study_id[:54]}...")
    print()
    print("  The id is derived from that description, so two processes that")
    print("  describe the same experiment agree on it with no shared state.")
    identical = replace(study)
    renamed = replace(study, notes="a different note")
    print(f"  same description -> same id : {identical.study_id == study.study_id}")
    print(f"  changed notes    -> new id  : {renamed.study_id != study.study_id}")

    # ------------------------------------------------------------------
    # Step 02 : The dataset cannot be switched
    # ------------------------------------------------------------------

    print()
    print("Step 02 - The dataset cannot be switched")
    try:
        run_study(replace(study, dataset_version="OTHER-PANEL@deadbeef"), dataset)
    except ResearchValidationError as error:
        print(f"  {str(error)[:70]}...")
    print("  The study names its data and run_study compares rather than trusts.")
    print("  This is the substitution ADR-0017 closed for evidence, closed here")
    print("  for studies: an identity that hashes a string somebody typed is")
    print("  only as trustworthy as the typing.")

    # ------------------------------------------------------------------
    # Step 03 : Run it
    # ------------------------------------------------------------------

    result = run_study(study, dataset, buckets=5, minimum_assets=5, produced_at=PRODUCED_AT)

    print()
    print("Step 03 - The result")
    print(f"  result id       : {result.result_id[:54]}...")
    print(f"  metrics         : {len(result.metrics)}")
    print(f"  findings        : {len(result.findings)}")
    print(f"  warnings        : {len(result.warnings)}")
    print(f"  verifies        : {result.verify()}")
    print()
    print(f"  {'feature':<10} {'h':>4} {'rank IC':>9} {'spread':>10} {'obs':>7}")
    for definition in study.features:
        for horizon in study.horizons:
            prefix = f"{definition.feature_id}.h{horizon}"
            if f"{prefix}.rank_ic" not in result.metrics:
                continue
            print(
                f"  {definition.feature_id:<10} {horizon:>4} "
                f"{result.metrics[f'{prefix}.rank_ic']:>+9.4f} "
                f"{result.metrics[f'{prefix}.spread']:>+10.4%} "
                f"{int(result.metrics[f'{prefix}.observations']):>7}"
            )

    print()
    print("  Feature lineage recorded on the result:")
    for feature_version, lineage_string in sorted(result.feature_lineage.items()):
        print(f"    {feature_version.split('@')[0]:<10} {lineage_string[:52]}...")

    # ------------------------------------------------------------------
    # Step 04 : Reproducibility
    # ------------------------------------------------------------------

    again = run_study(study, dataset, produced_at=PRODUCED_AT + 999_999.0)

    print()
    print("Step 04 - Reproducibility")
    print(
        f"  same result id across a different produced_at : {again.result_id == result.result_id}"
    )
    print(f"  identical metrics                             : {again.metrics == result.metrics}")
    print("  The clock is recorded as a fact and never hashed, following")
    print("  DatasetProvenance, which records retrieved_at and excludes it")
    print("  from the dataset version for exactly this reason.")

    # ------------------------------------------------------------------
    # Step 05 : Validation
    # ------------------------------------------------------------------

    horizon = 5
    policy = PurgePolicy(label_ends_from_horizon(instants, horizon))
    folds = walk_forward_splits(instants, 60, 20, 20, WindowMode.ROLLING, policy=policy)
    blocked = cross_validation_splits(
        instants,
        5,
        CVMethod.EMBARGOED,
        PurgePolicy(label_ends_from_horizon(instants, horizon), embargo_seconds=5 * 86400.0),
    )

    panels = study_panels(study, dataset)
    signal = panels[FEATURES[0].feature_version]
    realized = forward_returns(frame, horizon)

    print()
    print("Step 05 - Validation")
    print(f"  walk-forward    : {len(folds)} folds, {folds.total_purged} train instants purged,")
    print(f"                    {folds.total_validation_purged} validation instants purged,")
    print(f"                    forward-only {folds.is_forward_only}")
    print(
        f"  purged k-fold   : {len(blocked)} folds, {blocked.total_purged} purged, "
        f"{blocked.total_embargoed} embargoed"
    )
    print()
    print(f"  {'fold':>5} {'validation IC':>14} {'test IC':>10}")
    fold_scores: dict[str, float] = {}
    for fold in folds.folds:
        validation = signal_diagnostics(
            signal, realized, minimum_assets=5, instants=fold.validation
        )
        test = signal_diagnostics(signal, realized, minimum_assets=5, instants=fold.test)
        fold_scores[f"fold{fold.index}"] = test.rank_ic.mean_rank or 0.0
        val = (
            "-" if validation.rank_ic.mean_rank is None else f"{validation.rank_ic.mean_rank:+.4f}"
        )
        tst = "-" if test.rank_ic.mean_rank is None else f"{test.rank_ic.mean_rank:+.4f}"
        print(f"  {fold.index:>5} {val:>14} {tst:>10}")

    # ------------------------------------------------------------------
    # Step 06 : Robustness
    # ------------------------------------------------------------------

    baseline = signal_diagnostics(signal, realized, buckets=5, minimum_assets=5)
    baseline_ic = baseline.rank_ic.mean_rank or 0.0

    print()
    print("Step 06 - Robustness")
    print(f"  {'perturbation':<26} {'rank IC':>9} {'change':>9}")
    print(f"  {'baseline':<26} {baseline_ic:>+9.4f} {'-':>9}")
    for periods in (1, 3, 5):
        delayed = signal_diagnostics(
            delay_signal(signal, periods), realized, buckets=5, minimum_assets=5
        )
        value = delayed.rank_ic.mean_rank or 0.0
        print(
            f"  {'delay ' + str(periods) + ' period(s)':<26} {value:>+9.4f} "
            f"{value - baseline_ic:>+9.4f}"
        )

    # ------------------------------------------------------------------
    # Step 07 : Overfitting
    # ------------------------------------------------------------------

    half = len(instants) // 2

    def sweep_score(name: str) -> float:
        window = int(name.split("=")[1])
        definition = FeatureDefinition(
            f"sweep_{window}", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=window
        )
        panel = study_panels(replace(study, features=(definition,)), dataset)[
            definition.feature_version
        ]
        measured = signal_diagnostics(
            panel, realized, buckets=5, minimum_assets=5, instants=instants[:half]
        )
        return measured.rank_ic.mean_rank or 0.0

    sweep = parameter_sweep(
        "rank_ic", [f"window={window}" for window in (10, 15, 20, 25, 30)], sweep_score
    )
    out_of_sample = (
        signal_diagnostics(
            signal, realized, buckets=5, minimum_assets=5, instants=instants[half:]
        ).rank_ic.mean_rank
        or 0.0
    )

    stability = period_stability("rank_ic", fold_scores)
    overfitting = build_overfitting_report(
        metric="rank_ic",
        in_sample=sweep.best_score,
        out_of_sample=out_of_sample,
        policy=OverfittingPolicy(
            maximum_degradation=0.5, maximum_sensitivity=1.0, minimum_positive_share=0.5
        ),
        effective_parameters=1,
        sweep=sweep,
        period=stability,
    )

    print()
    print("Step 07 - Overfitting diagnostics")
    print(f"  trials          : {overfitting.trials}")
    print(f"  best in sample  : {sweep.best} at {sweep.best_score:+.4f}")
    print(f"  out of sample   : {out_of_sample:+.4f}")
    degradation = sample_degradation(sweep.best_score, out_of_sample)
    print(f"  degradation     : {'-' if degradation is None else format(degradation, '+.2%')}")
    print(f"  sensitivity     : {overfitting.sensitivity:.4f}")
    print(f"  fold positive share : {stability.positive_share:.0%}")
    print(f"  Bonferroni alpha    : {overfitting.bonferroni_alpha:.6f}")
    print()
    print("  FINDINGS")
    for finding in overfitting.findings or ("(none crossed)",):
        print(f"    - {finding}")

    # ------------------------------------------------------------------
    # Step 08 : Evidence, and a promotion gate
    # ------------------------------------------------------------------

    evidence = evidence_from_study(result, SUBJECT, PRODUCED_AT)

    print()
    print("Step 08 - Evidence")
    print(f"  evidence id     : {evidence.evidence_id[:54]}...")
    print(f"  method          : {evidence.method.name}")
    print(f"  dataset         : {evidence.dataset_id[:54]}...")
    print(f"  seed            : {evidence.seed}")
    print(f"  source          : {evidence.source_id[:40]}... (the study result)")
    print(f"  verifies        : {verify_evidence_id(evidence)}")
    print()
    print("  The dataset is DERIVED from the study, never supplied: a caller")
    print("  cannot name a dataset the study did not run on. evidence_id_for")
    print("  did not move in v3.2 -- a new ValidationMethod member changes no")
    print("  existing digest, so every promotion recorded since v2.6 still")
    print("  verifies.")

    gate = ValidationPolicy(
        policy_id="v32-research-gate",
        thresholds=(
            MetricThreshold("mom_20.h5.observations", minimum=500.0),
            MetricThreshold("mom_20.h5.rank_ic", minimum=0.02),
        ),
        required_method=ValidationMethod.STUDY,
    )
    outcome = evaluate_policy(gate, evidence)

    print()
    print("Step 09 - The promotion gate")
    print(f"  policy          : {gate.policy_id}")
    print(f"  passed          : {outcome.passed}")
    for failure in outcome.failures:
        print(f"    - {failure}")
    print()
    print("  A passing outcome states exactly one thing: every threshold in the")
    print("  policy was met by the numbers in the evidence. It is not a")
    print("  significance test, and it does not correct for how many candidates")
    print(f"  were searched before this one -- {overfitting.trials} were, and the")
    print("  Bonferroni threshold above is where that is recorded.")

    # ------------------------------------------------------------------
    # Step 10 : Tamper evidence
    # ------------------------------------------------------------------

    tampered = replace(result, metrics={**dict(result.metrics), "mom_20.h5.rank_ic": 0.95})

    print()
    print("Step 10 - Tamper evidence")
    print(f"  original verifies : {result.verify()}")
    print(f"  edited verifies   : {tampered.verify()}")
    try:
        evidence_from_study(tampered, SUBJECT, PRODUCED_AT)
    except LifecycleInputError as error:
        print(f"  evidence refused  : {str(error)[:62]}...")

    # ------------------------------------------------------------------
    # The lineage, end to end
    # ------------------------------------------------------------------

    decay = factor_decay(signal, frame, [1, 5, 20], minimum_assets=5)

    print()
    print("=" * 74)
    print("  Lineage, end to end")
    print(f"    bytes          : {dataset.require_provenance().content_hash[:48]}...")
    print(f"    dataset        : {version[:48]}...")
    print(f"    feature        : {FEATURES[0].feature_version[:48]}...")
    print(f"    study          : {study.study_id[:48]}...")
    print(f"    result         : {result.result_id[:48]}...")
    print(f"    evidence       : {evidence.evidence_id[:48]}...")
    print()
    print(
        f"    decay profile  : "
        f"{ {h: round(v, 4) for h, v in decay.mean_rank_by_horizon.items() if v is not None} }"
    )
    print()
    print("  Every identity above is DERIVED from content and configuration.")
    print("  Re-running this file reproduces all of them; changing the data,")
    print("  a window, a parameter or the study's description changes the ones")
    print("  downstream of the change and nothing else.")
    print("=" * 74)


if __name__ == "__main__":
    main()
