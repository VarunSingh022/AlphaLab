"""
AlphaLab Examples
=================

Example 53 : Regime Detection

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 17 (feature engineering)
✓ Example 19 (signal diagnostics)

Topics
------

• Regimes from a declared rule, with labels that are the caller's words
• A threshold on a volatility feature, with persistence against flicker
• A trailing-quantile rule that adapts to the series using only its past
• A composite: trend and volatility into risk-on and risk-off
• A macro regime from point-in-time data, growth against inflation
• Transitions, durations and what each regime held
• A detector resumed from its recorded state, reproducing one pass exactly
• A signal diagnostic conditioned on the detected regime

What this shows
---------------

Which state the market is in is a research question, so AlphaLab ships no
taxonomy: "calm", "turbulent", "risk_on" and "stagflation" below are labels this
example chooses. What AlphaLab supplies is a definition with a derived identity,
a classification that reads only the past, a persistence rule that stops a
regime flipping on every observation near a threshold, and a detector state that
can be recorded and resumed -- so a regime series can be continued tomorrow and
come out exactly as if it had run in one pass.

Run

    python examples/53_regime_detection.py
"""

from decimal import Decimal

from _point_in_time import (
    SYMBOLS,
    banner,
    ingest_prices,
    label,
    section,
    sentiment_rows,
    vendor_file,
)

from alphalab.alt_data import (
    ExternalObservation,
    ReferencePeriod,
    VintagePolicy,
    build_observation_set,
)
from alphalab.api import (
    AvailabilityAfterLag,
    ObservationColumns,
    TimestampReading,
    ingest_observations,
    observation_source,
)
from alphalab.common import PointInTimeStamp, VisibilityRule
from alphalab.data.time import TimestampFormat
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ResearchClock,
    align_prices,
    compute_feature,
    compute_panel,
    forward_returns,
    observation_frame,
    observations_from_dataset,
)
from alphalab.research import (
    CompositeRule,
    RegimeDefinition,
    ThresholdRule,
    TrailingQuantileRule,
    classify_regimes,
    conditional_diagnostics,
    regime_profile,
    regime_series_from_features,
)

PUB = VisibilityRule.PUBLICATION


def main() -> None:
    banner(53, "Regime Detection")
    prices = ingest_prices()
    closes = observations_from_dataset(prices, FeatureField.CLOSE)

    section("1. A volatility regime from a feature, with persistence")
    volatility = FeatureDefinition(
        "vol_regime",
        FeatureKind.VOLATILITY_REGIME,
        FeatureField.CLOSE,
        window=5,
        parameters={"long_window": 15},
    )
    (vol,) = [row for row in compute_feature(volatility, closes) if row.symbol == "AAA"]
    calm_or_not = RegimeDefinition(
        "aaa-volatility", ThresholdRule("vol", (1.0,), ("calm", "turbulent")), 2
    )
    detected = regime_series_from_features(calm_or_not, {"vol": vol})
    print(f"model {calm_or_not.definition_id}")
    print(f"input lineage {detected.input_id}")
    for transition in detected.transitions:
        print(f"  {label(transition.at)}: {transition.previous} -> {transition.current}")
    flicker = [
        row for row in zip(detected.raw_labels, detected.regimes, strict=True) if row[0] != row[1]
    ]
    print(f"{len(flicker)} observation(s) where the raw label and the confirmed regime differ")

    section("2. A trailing quantile: thresholds from the series' own past")
    quantile = RegimeDefinition(
        "aaa-vol-quantile",
        TrailingQuantileRule("vol", 10, (0.3, 0.7), ("low_vol", "normal", "high_vol")),
        1,
    )
    by_quantile = regime_series_from_features(quantile, {"vol": vol})
    first_label = label(by_quantile.timestamps[quantile.warmup])
    print(f"warmup {quantile.warmup} observations; first label at {first_label}")
    print(f"labels seen: {sorted({regime for regime in by_quantile.regimes if regime})}")

    section("3. A composite: trend crossed with volatility")
    trend = FeatureDefinition("trend", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=10)
    (momentum,) = [row for row in compute_feature(trend, closes) if row.symbol == "AAA"]
    common = sorted(set(momentum.timestamps) & set(vol.timestamps))
    trend_values = dict(zip(momentum.timestamps, momentum.values, strict=True))
    vol_values = dict(zip(vol.timestamps, vol.values, strict=True))
    risk = RegimeDefinition(
        "risk-appetite",
        CompositeRule.of(
            (
                ThresholdRule("trend", (0.0,), ("bear", "bull")),
                ThresholdRule("vol", (1.1,), ("quiet", "volatile")),
            ),
            {
                ("bear", "quiet"): "risk_off",
                ("bear", "volatile"): "risk_off",
                ("bull", "quiet"): "risk_on",
                ("bull", "volatile"): "neutral",
            },
        ),
        2,
    )
    appetite = classify_regimes(
        risk,
        {
            "trend": [(stamp, trend_values[stamp]) for stamp in common],
            "vol": [(stamp, vol_values[stamp]) for stamp in common],
        },
        input_id=None,
    )
    profile = regime_profile(appetite, None)
    for entry in profile.statistics:
        print(
            f"  {entry.label:<9} {entry.observations:>3} observations, {entry.spells} spell(s), "
            f"mean duration {entry.mean_duration:.1f}"
        )

    section("4. A macro regime from point-in-time data")
    source = observation_source(vendor_file("macro.csv", [{"x": 1}], 1.0), "macro.monthly", "1")
    prints = []
    for month, (growth, inflation) in enumerate(
        ((52.0, 3.4), (49.0, 3.6), (48.5, 2.9), (51.0, 2.4))
    ):
        start = prices.records[0].timestamp + month * 21 * 86_400.0
        period = ReferencePeriod(start - 30 * 86_400.0, start, f"M{month}")
        for metric, value in (("pmi", growth), ("cpi_yoy", inflation)):
            prints.append(
                ExternalObservation(
                    category="economic",
                    subject="US",
                    metric=metric,
                    value=Decimal(str(value)),
                    unit="index" if metric == "pmi" else "percent",
                    stamp=PointInTimeStamp.declared(start, start + 5 * 86_400.0),
                    source=source,
                    period=period,
                    revision=0,
                )
            )
    macro = build_observation_set("macro", prints, source).view(PUB)
    clock = ResearchClock.of_frame(closes)
    frames = {
        metric: observation_frame(
            macro,
            "economic",
            metric,
            clock,
            ("US",),
            max_age_seconds=None,
            policy=VintagePolicy.AS_KNOWN,
        )
        for metric in ("pmi", "cpi_yoy")
    }
    pmi, cpi = frames["pmi"].frame.series["US"], frames["cpi_yoy"].frame.series["US"]
    macro_regime = RegimeDefinition(
        "growth-inflation",
        CompositeRule.of(
            (
                ThresholdRule("pmi", (50.0,), ("contraction", "expansion")),
                ThresholdRule("cpi", (3.0,), ("low", "high")),
            ),
            {
                ("contraction", "high"): "stagflation",
                ("contraction", "low"): "slowdown",
                ("expansion", "high"): "overheating",
                ("expansion", "low"): "goldilocks",
            },
        ),
        1,
    )
    quarters = classify_regimes(
        macro_regime,
        {
            "pmi": list(zip(pmi.timestamps, pmi.values, strict=True)),
            "cpi": list(zip(cpi.timestamps, cpi.values, strict=True)),
        },
        input_id=frames["pmi"].frame_id,
    )
    for transition in quarters.transitions:
        print(f"  {label(transition.at)}: {transition.previous} -> {transition.current}")

    section("5. Resumed from its recorded state, exactly")
    split = len(vol) // 2
    head = regime_series_from_features(calm_or_not, {"vol": vol})
    first = classify_regimes(
        calm_or_not,
        {"vol": list(zip(vol.timestamps[:split], vol.values[:split], strict=True))},
        input_id=head.input_id,
    )
    rest = classify_regimes(
        calm_or_not,
        {"vol": list(zip(vol.timestamps[split:], vol.values[split:], strict=True))},
        input_id=head.input_id,
        initial=first.final_state,
    )
    print(f"one pass and two passes agree: {first.regimes + rest.regimes == head.regimes}")
    print(f"final states identical: {rest.final_state.state_id == head.final_state.state_id}")

    section("6. A signal diagnostic conditioned on the detected regime")
    rows = sentiment_rows()
    news = ingest_observations(
        rows,
        "news",
        observation_source(vendor_file("news.csv", rows, 2.0), "news.sentiment", "3.1"),
        ObservationColumns(
            category="sentiment",
            unit="score",
            subject="ticker",
            metric="metric",
            value="value",
            observed_at="observed",
            availability=AvailabilityAfterLag(3600.0),
            effective_at=None,
            revision=None,
            period=None,
            ingested_at_retrieval=False,
        ),
        TimestampReading(TimestampFormat.ISO_8601_NAIVE, "America/New_York", None),
    ).observations
    knowledge = observation_frame(
        news.view(PUB),
        "sentiment",
        "news_score",
        clock,
        SYMBOLS,
        max_age_seconds=None,
        policy=VintagePolicy.AS_KNOWN,
    )
    signal = compute_panel(
        FeatureDefinition(
            "news_rank",
            FeatureKind.CROSS_SECTIONAL_RANK,
            FeatureField.OBSERVATION,
            scope=FeatureScope.CROSS_SECTIONAL,
        ),
        knowledge.frame,
    )
    returns = forward_returns(align_prices(closes, knowledge), 1)
    by_regime = conditional_diagnostics(
        signal, returns, detected.labels_by_instant(), buckets=2, minimum_assets=5
    )
    for name, diagnostics in by_regime.items():
        rank_ic = diagnostics.rank_ic.mean_rank
        shown = "unmeasurable" if rank_ic is None else f"{rank_ic:+.4f}"
        print(f"  {name:<10} rank IC {shown} over {diagnostics.rank_ic.instants_measured} instants")


if __name__ == "__main__":
    main()
