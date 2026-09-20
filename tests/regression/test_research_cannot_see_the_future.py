"""Look-ahead leakage: the property, checked mechanically on every kind and scheme.

The v3.2 roadmap makes these release-blocking correctness contracts and is
specific about how they must be written: *"Do not merely test that an exception
occurred. Assert the exact split, timestamps, included rows, and outputs."*

So none of these tests checks that something raised. Each establishes a
property by constructing the situation a leak would show up in and comparing
actual values.

The truncation argument
-----------------------

The central test is :func:`test_no_feature_kind_changes_when_the_future_is_removed`,
and the argument behind it is worth stating because it is stronger than
inspecting the code. Compute a feature over the whole series, then compute it
again over a *prefix* of that series. If any value at an instant inside the
prefix differs between the two runs, that value depended on data after the
prefix -- which is exactly what look-ahead means. The check needs no knowledge
of how any kind is implemented, so it stays valid when one changes.

The forward-return counter-check
--------------------------------

:func:`test_the_truncation_fixture_can_detect_a_quantity_that_looks_ahead` runs
the same truncation against the one quantity that is *supposed* to look ahead:
a forward return. It shows that removing the future does change what is
reachable, which is what makes the passing results above mean something rather
than reflecting a fixture in which nothing could have differed.
"""

from __future__ import annotations

import math

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    KIND_REQUIREMENTS,
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeatureScope,
    ObservationFrame,
    compute_feature,
    compute_panel,
    forward_returns,
    observations_from_records,
)
from alphalab.research import (
    CVMethod,
    PurgePolicy,
    SplitReport,
    WindowMode,
    cross_validation_splits,
    label_ends_from_horizon,
    walk_forward_splits,
)

DAY = 86400.0
START = 1_735_689_600.0
LENGTH = 60
PREFIX = 40


def _paths(assets: int = 6) -> dict[str, list[float]]:
    """Deterministic, uneven, strictly positive series with real dispersion.

    Uneven matters: a series whose every return is identical would make the
    truncation test pass for a feature that *did* look ahead, because there
    would be nothing for a peek to reveal.
    """

    paths: dict[str, list[float]] = {}
    for index in range(assets):
        level = 100.0 + 7.0 * index
        series = []
        for step in range(LENGTH):
            level *= 1.0 + 0.004 * math.sin(step * (1.0 + index) * 0.7) + 0.0011 * index
            series.append(level)
        paths[f"S{index}"] = series
    return paths


def _records(paths: dict[str, list[float]], length: int) -> list[WireBar]:
    return [
        WireBar(
            symbol,
            START + index * DAY,
            close,
            close * 1.002,
            close * 0.998,
            close,
            1_000_000.0 + index,
        )
        for symbol, closes in paths.items()
        for index, close in enumerate(closes[:length])
    ]


def _frame(
    length: int, field: FeatureField = FeatureField.CLOSE, assets: int = 6
) -> ObservationFrame:
    return observations_from_records(_records(_paths(assets), length), field, "UTC", "ds@v1")


#: Every kind, in enum-definition order, which is deterministic.
ALL_KINDS = list(FeatureKind)


def _definition(kind: FeatureKind) -> FeatureDefinition:
    requirement = KIND_REQUIREMENTS[kind]
    parameters: dict[str, float] = {}
    if kind is FeatureKind.VOLATILITY_REGIME:
        parameters["long_window"] = 12.0
    if kind is FeatureKind.MOMENTUM:
        parameters["skip_periods"] = 1.0
    return FeatureDefinition(
        feature_id=kind.name.lower(),
        kind=kind,
        source_field=FeatureField.CLOSE,
        window=5 if requirement.needs_window else None,
        parameters=parameters,
        scope=requirement.scope,
    )


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_no_feature_kind_changes_when_the_future_is_removed(kind: FeatureKind) -> None:
    """Every value inside the prefix is identical with and without later data."""

    definition = _definition(kind)

    full = compute_feature(definition, _frame(LENGTH))
    truncated = compute_feature(definition, _frame(PREFIX))

    by_symbol = {series.symbol: series for series in full}
    compared = 0

    for short in truncated:
        long = by_symbol[short.symbol]
        for stamp, value in zip(short.timestamps, short.values, strict=True):
            assert long.value_at(stamp) == value, (
                f"{kind.name} on {short.symbol} at {stamp!r} changed when later data was "
                f"removed: {value!r} against {long.value_at(stamp)!r}. The value depends on "
                "observations after it, which is look-ahead."
            )
            compared += 1

    assert compared > 0, f"{kind.name} produced no overlapping values to compare"


def test_the_truncation_fixture_can_detect_a_quantity_that_looks_ahead() -> None:
    """The counter-check that makes the parametrized test above mean something.

    A forward return is the one quantity in the package that is *supposed* to
    depend on later data, so the fixture must be able to show that. It is shown
    at the boundary: the instant five observations before the prefix ends has a
    realized 5-period forward return in the full series and **none** in the
    truncated one, because its outcome lies outside the prefix.

    If this did not hold -- if truncation left every quantity reachable -- the
    passing results above would be vacuous.
    """

    horizon = 5
    boundary = START + (PREFIX - horizon) * DAY

    full = forward_returns(_frame(LENGTH), horizon)
    truncated = forward_returns(_frame(PREFIX), horizon)

    assert full.cross_section(boundary), "the full series realizes this instant's outcome"
    assert not truncated.cross_section(boundary), (
        "the truncated series must not realize an outcome that lies beyond its own end; "
        "if it did, the truncation fixture could not reveal a look-ahead at all"
    )
    assert len(full) > len(truncated)


def test_a_feature_value_never_precedes_the_window_it_needs() -> None:
    """The first value appears at exactly the warmup index, never before it."""

    frame = _frame(LENGTH)
    for kind in FeatureKind:
        definition = _definition(kind)
        if definition.scope is FeatureScope.CROSS_SECTIONAL:
            continue
        series = compute_feature(definition, frame)[0]
        if not series.timestamps:
            continue
        first_index = round((series.timestamps[0] - START) / DAY)
        assert first_index == definition.warmup_periods, (
            f"{kind.name} produced its first value at index {first_index}, and its warmup "
            f"is {definition.warmup_periods}"
        )


def test_a_cross_sectional_feature_reads_one_instant_and_no_history() -> None:
    """Changing an earlier instant must not move a later cross-section."""

    definition = FeatureDefinition(
        "rank",
        FeatureKind.CROSS_SECTIONAL_RANK,
        FeatureField.CLOSE,
        scope=FeatureScope.CROSS_SECTIONAL,
    )
    paths = _paths(4)
    altered = {symbol: list(closes) for symbol, closes in paths.items()}
    altered["S0"][0] = 1_000_000.0

    base = compute_panel(
        definition,
        observations_from_records(_records(paths, 10), FeatureField.CLOSE, "UTC", "ds@v1"),
    )
    changed = compute_panel(
        definition,
        observations_from_records(_records(altered, 10), FeatureField.CLOSE, "UTC", "ds@v1"),
    )

    assert base.cross_section(START) != changed.cross_section(START), "the edited instant moves"
    for stamp in base.timestamps[1:]:
        assert base.cross_section(stamp) == changed.cross_section(stamp), (
            f"the cross-section at {stamp!r} moved when an earlier instant was edited, so it "
            "reads history it should not"
        )


# --------------------------------------------------------------------------- #
# Folds
# --------------------------------------------------------------------------- #


INSTANTS = [START + index * DAY for index in range(120)]


def _policy(horizon: int, embargo_days: float = 0.0) -> PurgePolicy:
    return PurgePolicy(label_ends_from_horizon(INSTANTS, horizon), embargo_days * DAY)


def _every_scheme() -> list[SplitReport]:
    return [
        walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5)),
        walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.EXPANDING, policy=_policy(5)),
        cross_validation_splits(INSTANTS, 4, CVMethod.ROLLING, _policy(5)),
        cross_validation_splits(INSTANTS, 4, CVMethod.EXPANDING, _policy(5)),
        cross_validation_splits(INSTANTS, 5, CVMethod.PURGED, _policy(5)),
        cross_validation_splits(INSTANTS, 5, CVMethod.EMBARGOED, _policy(5, embargo_days=4)),
    ]


def test_no_fold_of_any_scheme_shares_an_instant_between_its_parts() -> None:
    for report in _every_scheme():
        for fold in report.folds:
            shared = set(fold.train) & set(fold.validation)
            assert not shared, f"{report.scheme} fold {fold.index} shares {sorted(shared)[:3]}"
            if fold.test:
                assert not set(fold.train) & set(fold.test)
                assert not set(fold.validation) & set(fold.test)


def test_purging_leaves_a_gap_wider_than_the_label_horizon_in_every_scheme() -> None:
    """The exact property purging exists to create, on every scheme that ships.

    For a fold whose training data precedes its validation, the last training
    instant must be more than ``horizon`` observations before the first
    validation instant -- otherwise that row's label was realized inside the
    validation window.
    """

    horizon = 5
    for report in _every_scheme():
        for fold in report.folds:
            before = [stamp for stamp in fold.train if stamp < min(fold.validation)]
            if not before:
                continue
            last_before = INSTANTS.index(max(before))
            first_validation = INSTANTS.index(min(fold.validation))
            assert first_validation - last_before > horizon, (
                f"{report.scheme} fold {fold.index}: the last training instant is "
                f"{first_validation - last_before} observations before validation, which is "
                f"not more than the {horizon}-observation label horizon"
            )


def test_no_training_instant_has_a_label_that_reaches_into_its_validation_window() -> None:
    """The same property stated on the labels themselves rather than on the gap."""

    horizon = 5
    ends = label_ends_from_horizon(INSTANTS, horizon)

    for report in _every_scheme():
        for fold in report.folds:
            start = min(fold.validation)
            for stamp in fold.train:
                if stamp < start:
                    assert ends[stamp] < start, (
                        f"{report.scheme} fold {fold.index}: a training instant's label ends "
                        f"at {ends[stamp]!r}, inside the validation window starting "
                        f"{start!r}"
                    )


def test_an_embargoed_scheme_removes_every_instant_inside_its_embargo() -> None:
    """Asserted as membership, not as a count."""

    embargo = 4 * DAY
    report = cross_validation_splits(INSTANTS, 5, CVMethod.EMBARGOED, _policy(5, embargo_days=4))

    for fold in report.folds:
        block_end = max(fold.validation)
        forbidden = {stamp for stamp in INSTANTS if block_end < stamp < block_end + embargo}
        assert not forbidden & set(fold.train), (
            f"fold {fold.index} trains on {sorted(forbidden & set(fold.train))[:3]}, which "
            "fall inside its embargo"
        )


def test_a_walk_forward_test_window_is_never_used_for_selection() -> None:
    """Train precedes validation precedes test, on the instants themselves."""

    for mode in (WindowMode.ROLLING, WindowMode.EXPANDING):
        report = walk_forward_splits(INSTANTS, 40, 10, 10, mode, policy=_policy(5))
        for fold in report.folds:
            assert max(fold.train) < min(fold.validation)
            assert max(fold.validation) < min(fold.test)


def test_removing_the_purge_policy_visibly_reintroduces_the_overlap() -> None:
    """The control: without purging the gap closes, so purging is doing the work."""

    unpurged = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING)
    purged = walk_forward_splits(INSTANTS, 40, 10, 10, WindowMode.ROLLING, policy=_policy(5))

    unpurged_gap = INSTANTS.index(min(unpurged.folds[0].validation)) - INSTANTS.index(
        max(unpurged.folds[0].train)
    )
    purged_gap = INSTANTS.index(min(purged.folds[0].validation)) - INSTANTS.index(
        max(purged.folds[0].train)
    )

    assert unpurged_gap == 1, "without purging the training set runs right up to validation"
    assert purged_gap > 5
    assert unpurged.total_purged == 0
    assert purged.total_purged > 0
