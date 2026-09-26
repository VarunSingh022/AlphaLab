"""The v3.7 invariants, measured from the code rather than claimed in a release note.

Same shape as ``test_v36_invariants.py``. Each section is one property the
point-in-time research work depends on. The leakage section checks every
selection against a brute-force reading of the rule on randomly generated
histories -- the rule is simple, and an optimization that disagreed with it
anywhere would be a look-ahead. The determinism section spawns fresh
interpreters with different hash seeds *and different working directories*,
because a salted ``hash()`` or a relative path is invisible inside one process.
"""

from __future__ import annotations

import ast
import enum
import hashlib
import inspect
import json
import os
import pathlib
import random
import re
import subprocess
import sys
from decimal import Decimal
from typing import Protocol

import pytest

from alphalab.alt_data import ExternalObservation

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
ROOT = PACKAGE.parent

#: The modules v3.7 added. Every sweep is scoped to them.
V37_MODULES = (
    "alphalab/alt_data/fundamentals.py",
    "alphalab/alt_data/identity.py",
    "alphalab/alt_data/information.py",
    "alphalab/alt_data/observation.py",
    "alphalab/alt_data/observation_set.py",
    "alphalab/alt_data/sessions.py",
    "alphalab/alt_data/source.py",
    "alphalab/alt_data/validation.py",
    "alphalab/factor_library/fundamentals.py",
    "alphalab/factor_library/knowledge.py",
    "alphalab/research/event_study.py",
    "alphalab/research/regimes.py",
    "alphalab/strategy/adaptive.py",
    "alphalab/strategy/adaptive_rules.py",
    "alphalab/strategy/adaptive_strategy.py",
)


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(ROOT)), path.read_text()))
    return found


def _v37_sources() -> list[tuple[str, str]]:
    return [(name, text) for name, text in _sources() if name in V37_MODULES]


def test_every_v37_module_exists() -> None:
    """So a rename cannot silently empty every sweep in this file."""

    assert len(_v37_sources()) == len(V37_MODULES)


# --------------------------------------------------------------------------- #
# 1. One authority per concept, and the layering that lets everyone read it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("marker", "home"),
    [
        ("class PointInTimeStamp", "common/point_in_time.py"),
        ("class PointInTimeIndex", "common/point_in_time.py"),
        ("class AvailabilityBasis", "common/point_in_time.py"),
        ("class VisibilityRule", "common/point_in_time.py"),
        ("class ObservationSource", "alt_data/source.py"),
        ("class ExternalObservation", "alt_data/observation.py"),
        ("class InformationEvent", "alt_data/information.py"),
        ("class FundamentalObservation", "alt_data/fundamentals.py"),
        ("class ObservationSet", "alt_data/observation_set.py"),
        ("class ObservationView", "alt_data/observation_set.py"),
        ("class VintagePolicy", "alt_data/observation_set.py"),
        ("class SessionTiming", "alt_data/sessions.py"),
        ("class KnowledgeFrame", "factor_library/knowledge.py"),
        ("class EventStudyResult", "research/event_study.py"),
        ("class RegimeDefinition", "research/regimes.py"),
        ("class RegimeSeries", "research/regimes.py"),
        ("class AdaptiveState", "strategy/adaptive.py"),
        ("class AdaptiveConfiguration", "strategy/adaptive.py"),
        ("class AdaptiveStrategy", "strategy/adaptive_strategy.py"),
        ("def derive_set_version", "alt_data/observation_set.py"),
        ("def trailing_twelve_months", "alt_data/fundamentals.py"),
        ("def place_in_session", "alt_data/sessions.py"),
        ("def derive_frame_id", "factor_library/knowledge.py"),
        ("def event_study", "research/event_study.py"),
        ("def classify_regimes", "research/regimes.py"),
        ("def apply_update", "strategy/adaptive.py"),
        ("def replay_updates", "strategy/adaptive.py"),
        ("def assess_adaptive_replay", "lifecycle/reproducibility.py"),
    ],
)
def test_each_new_concept_has_exactly_one_home(marker: str, home: str) -> None:
    pattern = re.compile(rf"^{re.escape(marker)}\b", re.MULTILINE)
    found = [name for name, source in _sources() if pattern.search(source)]

    assert found == [f"alphalab/{home}"], f"{marker!r} is defined in {found}"


def test_v37_redefines_no_canonical_type() -> None:
    """Every carried value is the owning authority's type, not a copy of it."""

    canonical = {
        "Dataset",
        "DatasetProvenance",
        "RawSource",
        "MarketCalendar",
        "ObservationFrame",
        "FeatureSeries",
        "FeaturePanel",
        "FundamentalSnapshot",
        "ResearchStudy",
        "StudyResult",
        "StrategyFingerprint",
        "ReproducibilityManifest",
        "RowRejection",
        "Intent",
        "DataProvenance",
    }
    offenders = [
        f"{name}: {node.name}"
        for name, source in _v37_sources()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and node.name in canonical
    ]
    assert offenders == []


def _package_edges(package: str) -> tuple[set[str], set[str]]:
    edges: set[str] = set()
    importers: set[str] = set()
    for name, source in _sources():
        owner = ".".join(name[: -len(".py")].replace("/", ".").split(".")[:2])
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "alphalab."
            ):
                continue
            assert node.module is not None
            target = ".".join(node.module.split(".")[:2])
            if owner == package and target != package:
                edges.add(target)
            if target == package and owner != package:
                importers.add(owner)
    return edges, importers


def test_the_alternative_data_package_is_a_leaf_over_common() -> None:
    """What lets the feature, research and application layers all read it.

    The point-in-time foundation is read from both sides of the graph --
    ``factor_library`` beside ``data``, ``research`` above it, ``api`` at the
    top -- so it may import nothing but ``common``, the rule
    ``alphalab.conventions`` follows for the same reason.
    """

    edges, importers = _package_edges("alphalab.alt_data")

    assert edges == {"alphalab.common"}
    assert {"alphalab.factor_library", "alphalab.research", "alphalab.api"} <= importers


def test_the_strategy_package_still_imports_only_common() -> None:
    edges, _ = _package_edges("alphalab.strategy")

    assert edges == {"alphalab.common"}


def test_the_research_layer_reads_events_and_never_the_data_package() -> None:
    edges, _ = _package_edges("alphalab.research")

    assert "alphalab.alt_data" in edges
    assert "alphalab.data" not in edges


def test_no_v37_module_calls_itself_a_score() -> None:
    """Measurements and findings, never a blended verdict (ADR-0037 decision 10).

    A z-score is a standardized statistic, not a verdict, and is the one
    spelling of "score" allowed.
    """

    offenders = [
        f"{name}: {node.name}"
        for name, source in _v37_sources()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
        and "score" in node.name.lower().replace("zscore", "")
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# 2. Point-in-time: every selection agrees with the rule, on random histories
# --------------------------------------------------------------------------- #


def _random_history(seed: int, count: int = 200) -> list[ExternalObservation]:
    """Random observations whose revisions are numbered in publication order.

    Availability, ingestion and the share of unknown availability are random;
    revision numbers are then assigned per figure in the order the vintages
    became available, which is the one consistency a set refuses to be without.
    """

    from alphalab.alt_data import ReferencePeriod
    from tests.unit.alt_data.pit_harness import observation

    rng = random.Random(seed)
    drafts: dict[tuple[str, int], list[tuple[float | None, float | None, str]]] = {}
    for _ in range(count):
        subject = rng.choice(("AAA", "BBB", "CCC"))
        month = rng.randrange(24)
        observed = 1_000.0 * month + 500.0
        available: float | None = observed + rng.uniform(0.0, 900.0)
        if rng.random() < 0.1:
            available = None
        ingested = None if rng.random() < 0.3 else observed + rng.uniform(0.0, 5_000.0)
        drafts.setdefault((subject, month), []).append(
            (available, ingested, f"{rng.uniform(-5, 5):.3f}")
        )
    records: list[ExternalObservation] = []
    for (subject, month), vintages in sorted(drafts.items()):
        ordered = sorted(vintages, key=lambda draft: (draft[0] is None, draft[0] or 0.0))
        for revision, (available, ingested, value) in enumerate(ordered):
            observed = 1_000.0 * month + 500.0
            records.append(
                observation(
                    subject,
                    "metric",
                    value,
                    observed,
                    available,
                    revision=revision,
                    period=ReferencePeriod(1_000.0 * month, observed, f"M{month:02d}"),
                    ingested_at=ingested,
                )
            )
    return records


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("rule_name", ["PUBLICATION", "INGESTION"])
def test_a_selection_is_exactly_what_the_rule_says_was_knowable(seed: int, rule_name: str) -> None:
    from alphalab.alt_data import build_observation_set
    from alphalab.common import VisibilityRule
    from tests.unit.alt_data.pit_harness import SOURCE

    rule = VisibilityRule[rule_name]
    history = _random_history(seed)
    view = build_observation_set("random", history, SOURCE).view(rule)
    rng = random.Random(seed * 101)

    for as_of in [rng.uniform(-100.0, 30_000.0) for _ in range(150)]:
        selected = {record.record_id for record in view.select(as_of).records}
        expected = {record.record_id for record in history if record.stamp.is_visible(as_of, rule)}
        assert selected == expected
        for record in view.select(as_of).records:
            known = record.stamp.known_at(rule)
            assert known is not None and known <= as_of, "a record visible before it was known"
            assert record.stamp.verifiable, "an UNKNOWN availability was selected"


@pytest.mark.parametrize("seed", [4, 5])
def test_no_vintage_read_is_one_published_after_the_research_instant(seed: int) -> None:
    from alphalab.alt_data import VintagePolicy, build_observation_set
    from alphalab.common import VisibilityRule
    from tests.unit.alt_data.pit_harness import SOURCE

    rule = VisibilityRule.PUBLICATION
    view = build_observation_set("random", _random_history(seed), SOURCE).view(rule)
    rng = random.Random(seed)

    for series in view.series_keys:
        for as_of in [rng.uniform(0.0, 30_000.0) for _ in range(40)]:
            for policy in VintagePolicy:
                latest = view.latest_in_series(series, as_of, policy)
                if latest is None:
                    continue
                known = latest.stamp.known_at(rule)
                assert known is not None and known <= as_of
                assert policy is VintagePolicy.AS_KNOWN or latest.revision == 0


def test_every_knowledge_frame_point_was_knowable_at_its_instant() -> None:
    from alphalab.alt_data import VintagePolicy, build_observation_set
    from alphalab.common import VisibilityRule
    from alphalab.factor_library import ResearchClock, observation_frame
    from tests.unit.alt_data.pit_harness import SOURCE

    history = _random_history(9)
    obs_set = build_observation_set("random", history, SOURCE)
    by_id = {record.record_id: record for record in obs_set.records}
    for rule in VisibilityRule:
        knowledge = observation_frame(
            obs_set.view(rule),
            "economic",
            "metric",
            ResearchClock.of_instants(tuple(float(i) for i in range(0, 30_000, 97)), "UTC"),
            ("AAA", "BBB", "CCC"),
            max_age_seconds=None,
            policy=VintagePolicy.AS_KNOWN,
        )
        for subject, row in knowledge.frame.series.items():
            for instant, ids in zip(row.timestamps, knowledge.sources[subject], strict=True):
                for identity in ids:
                    known = by_id[identity].stamp.known_at(rule)
                    assert known is not None and known <= instant


def test_every_event_is_anchored_at_or_after_it_could_be_traded() -> None:
    from alphalab.alt_data import build_observation_set
    from alphalab.research import event_study
    from tests.unit.alt_data.pit_harness import NYSE, SOURCE, event
    from tests.unit.research.test_event_study import SESSIONS, _definition, _prices

    rng = random.Random(12)
    closes = [NYSE.session_bounds(day)[1] for day in SESSIONS[5:-5]]  # type: ignore[index]
    events = []
    for number in range(40):
        known = rng.choice(closes) + rng.uniform(-8 * 3600, 16 * 3600)
        events.append(
            event("earnings.release", rng.choice(("AAA", "BBB")), known - 60.0, known + number)
        )
    result = event_study(
        build_observation_set("random-events", events, SOURCE),
        _prices({"AAA": set(), "BBB": set()}),
        NYSE,
        _definition(),
    )

    assert result.outcomes
    for outcome in result.outcomes:
        assert outcome.known_at <= outcome.tradable_at <= outcome.anchor


def test_a_restatement_never_reaches_a_trailing_figure_before_it_was_published() -> None:
    from alphalab.alt_data import VintagePolicy, trailing_twelve_months
    from alphalab.common import VisibilityRule
    from tests.unit.alt_data.pit_harness import INCOME, RESTATED_AT, issuer_set

    obs_set = issuer_set()
    view = obs_set.view(VisibilityRule.PUBLICATION)
    by_id = {record.record_id: record for record in obs_set.records}
    for as_of in (RESTATED_AT - 1.0, RESTATED_AT, RESTATED_AT + 1e7):
        figure = trailing_twelve_months(
            view, "AAA", INCOME, "revenue", as_of, VintagePolicy.AS_KNOWN
        )
        for identity in figure.record_ids:
            assert by_id[identity].stamp.available_at <= as_of  # type: ignore[operator]


def test_a_regime_label_never_depends_on_a_later_observation() -> None:
    from alphalab.research import RegimeDefinition, TrailingQuantileRule, classify_regimes

    model = RegimeDefinition("pit", TrailingQuantileRule("x", 6, (0.3, 0.7), ("l", "m", "h")), 2)
    rng = random.Random(21)
    values = [(float(index), rng.uniform(0.0, 10.0)) for index in range(120)]
    whole = classify_regimes(model, {"x": values}, input_id=None)

    for cut in (1, 7, 30, 119):
        prefix = classify_regimes(model, {"x": values[:cut]}, input_id=None)
        assert prefix.regimes == whole.regimes[:cut]


def test_the_wire_record_as_of_cut_is_documented_as_timestamp_based() -> None:
    """``DataRequest.as_of`` cuts on a record's one timestamp, which is right for a
    price and is the gap v3.7's stamps close for everything else. The docstring
    must keep saying which timestamp it reads."""

    from alphalab.api import DataRequest

    assert "timestamped" in (DataRequest.__doc__ or "")


# --------------------------------------------------------------------------- #
# 3. Determinism: no clock, no salted hash, no environment
# --------------------------------------------------------------------------- #


_NONDETERMINISTIC_MODULES = frozenset(
    {
        "datetime",
        "getpass",
        "importlib",
        "os",
        "platform",
        "random",
        "secrets",
        "socket",
        "tempfile",
        "time",
        "uuid",
    }
)
_NONDETERMINISTIC_CALLS = frozenset({"hash", "id", "new_id", "uuid4"})


def test_no_v37_module_reads_a_clock_entropy_or_the_environment() -> None:
    offenders: list[str] = []
    for name, source in _v37_sources():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                roots = set()
            offenders.extend(
                f"{name}: imports {root}" for root in roots & _NONDETERMINISTIC_MODULES
            )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _NONDETERMINISTIC_CALLS
            ):
                offenders.append(f"{name}: calls {node.func.id}()")
    assert offenders == [], f"a v3.7 path is non-deterministic: {offenders}"


def test_the_point_in_time_core_reads_no_clock_either() -> None:
    source = (PACKAGE / "common" / "point_in_time.py").read_text()
    imported = {
        node.module.split(".")[0] if isinstance(node, ast.ImportFrom) and node.module else ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom | ast.Import)
    }

    assert not imported & _NONDETERMINISTIC_MODULES


def identities() -> list[str]:
    """Every v3.7 identity, built from fixed inputs -- run in fresh interpreters."""

    from alphalab.alt_data import (
        Aggregation,
        FundamentalInput,
        VintagePolicy,
        build_observation_set,
    )
    from alphalab.common import VisibilityRule
    from alphalab.factor_library import ResearchClock, fundamental_frame, observation_frame
    from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
    from alphalab.lifecycle import (
        NO_DEPENDENCIES,
        fingerprint_for_version,
        research_configuration_with_adaptive,
    )
    from alphalab.research import (
        RegimeDefinition,
        ResearchStudy,
        ThresholdRule,
        classify_regimes,
        event_study,
    )
    from alphalab.strategy import (
        AdaptationMode,
        AdaptiveConfiguration,
        DecisionTiming,
        TrailingZScoreRule,
        UpdateCadence,
        initial_state,
        observation_stream,
        replay_updates,
    )
    from tests.unit.alt_data.pit_harness import (
        INCOME,
        NYSE,
        SOURCE,
        event,
        issuer_set,
        ny,
        observation,
    )
    from tests.unit.lifecycle.evidence_harness import CODE, ENGINE, VERSION
    from tests.unit.research.test_event_study import AFTER_CLOSE, PRE_MARKET, _definition, _prices

    fundamentals = issuer_set()
    macro = build_observation_set(
        "macro",
        [
            observation(country, "cpi", str(3 + i), 10.0 * i, 10.0 * i + 5)
            for i, country in enumerate(("US", "EU", "JP"))
        ],
        SOURCE,
    )
    events = build_observation_set("events", [AFTER_CLOSE, PRE_MARKET], SOURCE)
    knowledge = observation_frame(
        macro.view(VisibilityRule.PUBLICATION),
        "economic",
        "cpi",
        ResearchClock.of_instants((5.0, 15.0, 25.0, 35.0), "UTC"),
        ("US", "EU", "JP"),
        max_age_seconds=None,
        policy=VintagePolicy.AS_KNOWN,
    )
    revenue = fundamental_frame(
        fundamentals.view(VisibilityRule.PUBLICATION),
        FundamentalInput("revenue", INCOME, "revenue", Aggregation.TRAILING_TWELVE_MONTHS),
        ResearchClock.of_instants(
            (ny("2024-06-03 16:00"), ny("2024-12-02 16:00")), "America/New_York"
        ),
        ("AAA",),
        max_age_seconds=None,
        policy=VintagePolicy.AS_KNOWN,
    )
    study = ResearchStudy(
        study_name="v37",
        dataset_version="prices@1",
        universe=("AAA",),
        features=(FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2),),
        inputs={"events": events.version, "fundamentals": fundamentals.version},
    )
    study_result = event_study(events, _prices({"AAA": set(), "BBB": set()}), NYSE, _definition())
    regime = RegimeDefinition("vol", ThresholdRule("x", (1.0,), ("calm", "wild")), 2)
    regimes = classify_regimes(
        regime, {"x": [(float(i), float(i % 3)) for i in range(20)]}, input_id=knowledge.frame_id
    )
    configuration = AdaptiveConfiguration(
        name="z",
        rule_id="trailing_zscore",
        rule_version=1,
        inputs=("price",),
        parameters={"window": 3, "entry": 1.0},
        cadence=UpdateCadence.EVERY_OBSERVATION,
        cadence_every=None,
        cadence_seconds=None,
        decision_timing=DecisionTiming.BEFORE_UPDATE,
        warmup=2,
    )
    rule = TrailingZScoreRule()
    adapted = replay_updates(
        configuration,
        rule,
        observation_stream(
            "s", [(float(i), {"price": float(i * i % 7)}) for i in range(30)], first_sequence=0
        ),
        AdaptationMode.LEARNING,
    )
    fingerprint = fingerprint_for_version(
        VERSION,
        CODE,
        NO_DEPENDENCIES,
        research_configuration_with_adaptive(
            {"validation": "x"}, [(configuration, initial_state(configuration, rule))]
        ),
        ENGINE,
    )
    return [
        fundamentals.version,
        fundamentals.records[0].record_id,
        macro.version,
        events.version,
        knowledge.frame_id,
        revenue.frame_id,
        study.study_id,
        study_result.definition.study_id,
        study_result.result_id,
        regime.definition_id,
        regimes.series_id,
        regimes.final_state.state_id,
        configuration.configuration_id,
        adapted.replay_id,
        adapted.final.state_id,
        fingerprint.fingerprint,
        repr(event("x.y", "S", 1.0, 2.0).record_id),
    ]


_CROSS_PROCESS = """
from tests.regression.test_v37_invariants import identities
for value in identities():
    print(value)
"""


def _fresh(hash_seed: str, cwd: pathlib.Path) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(cwd),
        env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(ROOT)},
    )
    return completed.stdout.split()


def test_every_v37_identity_is_the_same_across_hash_seeds_and_working_directories(
    tmp_path: pathlib.Path,
) -> None:
    first = _fresh("1", ROOT)
    second = _fresh("4242", ROOT)
    elsewhere = _fresh("99", tmp_path)

    assert first == second == elsewhere
    assert len(first) == 17


def test_the_fresh_processes_agree_with_this_one() -> None:
    assert _fresh("7", ROOT) == identities()


def test_nothing_machine_local_reaches_a_v37_identity_or_artifact() -> None:
    from alphalab.alt_data import canonical_fundamental_key, canonical_set_key
    from alphalab.persistence.serializer import serialize
    from alphalab.research import canonical_event_study_key
    from tests.unit.alt_data.pit_harness import issuer_set
    from tests.unit.research.test_event_study import _definition

    obs_set = issuer_set()
    rendered = "\n".join(
        [
            canonical_set_key(
                obs_set.name,
                obs_set.kind,
                obs_set.source,
                obs_set.parent_version,
                obs_set.transformations,
                obs_set.record_ids,
            ),
            canonical_fundamental_key(obs_set.records[0]),
            canonical_event_study_key(_definition()),
            serialize(obs_set.records[0]),
            *identities(),
        ]
    )
    local = {str(ROOT), str(pathlib.Path.home()), os.uname().nodename}
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        local.add(f"/{user}/")
    for marker in local:
        assert marker not in rendered, f"{marker!r} leaked into an identity"


# --------------------------------------------------------------------------- #
# 4. Value semantics
# --------------------------------------------------------------------------- #


#: The one v3.7 class that is deliberately not a frozen value: a strategy, which
#: the runtime holds and dispatches, and whose learned state is itself an
#: immutable value it replaces on every observation.
_NOT_VALUES = {"AdaptiveStrategy"}


def _public_classes() -> list[tuple[str, type]]:
    import importlib

    found: list[tuple[str, type]] = []
    for name in V37_MODULES:
        module = importlib.import_module(name[: -len(".py")].replace("/", "."))
        for attribute, value in vars(module).items():
            if (
                inspect.isclass(value)
                and value.__module__ == module.__name__
                and not attribute.startswith("_")
            ):
                found.append((f"{module.__name__}.{attribute}", value))
    return found


def test_every_v37_type_is_a_frozen_dataclass_an_enum_or_a_protocol() -> None:
    offenders = []
    for qualified, value in _public_classes():
        if value.__name__ in _NOT_VALUES:
            continue
        if issubclass(value, enum.Enum) or Protocol in getattr(value, "__mro__", ()):
            continue
        if getattr(value, "_is_protocol", False):
            continue
        params = getattr(value, "__dataclass_params__", None)
        if params is None or not params.frozen:
            offenders.append(qualified)
    assert offenders == [], f"not a frozen value: {offenders}"


def test_object_setattr_is_used_only_while_constructing() -> None:
    """The ``MappingProxyType`` idiom (ADR-0034), and derived identities computed once."""

    offenders: list[str] = []
    for name, source in _v37_sources():
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "__setattr__"
                    and function.name != "__post_init__"
                ):
                    offenders.append(f"{name}: {function.name}")
    assert offenders == []


def test_every_v37_value_is_machine_readable_as_deterministic_json() -> None:
    from alphalab.alt_data import VintagePolicy, statement_as_of
    from alphalab.common import VisibilityRule
    from alphalab.persistence.serializer import serialize
    from alphalab.strategy import (
        AdaptationMode,
        AdaptiveConfiguration,
        DecisionTiming,
        ExponentialMeanRule,
        UpdateCadence,
        checkpoint,
        observation_stream,
        replay_updates,
    )
    from tests.unit.alt_data.pit_harness import INCOME, RESTATED_AT, issuer_set

    obs_set = issuer_set()
    statement = statement_as_of(
        obs_set.view(VisibilityRule.PUBLICATION),
        "AAA",
        INCOME,
        "FY2024Q1",
        RESTATED_AT,
        VintagePolicy.AS_KNOWN,
    )

    configuration = AdaptiveConfiguration(
        "e", "exponential_mean", 1, ("p",), {"smoothing": 0.5},
        UpdateCadence.EVERY_OBSERVATION, None, None, DecisionTiming.AFTER_UPDATE, 0,
    )  # fmt: skip
    replayed = replay_updates(
        configuration,
        ExponentialMeanRule(),
        observation_stream("s", [(1.0, {"p": 1.0}), (2.0, {"p": 2.0})], first_sequence=0),
        AdaptationMode.LEARNING,
    )
    for value in (obs_set, statement, replayed, checkpoint(replayed.final)):
        payload = serialize(value)
        assert json.loads(payload) is not None
        assert serialize(value) == payload


# --------------------------------------------------------------------------- #
# 5. Nothing durable was added, and nothing frozen moved
# --------------------------------------------------------------------------- #


def test_v37_added_no_snapshot_owner_and_no_schema_constant() -> None:
    constants = sorted(
        {
            match
            for _, source in _sources()
            for match in re.findall(r"^([A-Z_]*_SCHEMA)\s*(?::[^=]*)?=\s*\d", source, re.MULTILINE)
        }
    )

    assert constants == [
        "ALLOCATION_SNAPSHOT_SCHEMA",
        "BROKER_SNAPSHOT_SCHEMA",
        "FX_FEED_SNAPSHOT_SCHEMA",
        "INSTRUMENT_SNAPSHOT_SCHEMA",
        "LIFECYCLE_SNAPSHOT_SCHEMA",
        "LIVE_SNAPSHOT_SCHEMA",
        "OMS_SNAPSHOT_SCHEMA",
        "PIPELINE_SNAPSHOT_SCHEMA",
        "PORTFOLIO_SNAPSHOT_SCHEMA",
        "RUN_SNAPSHOT_SCHEMA",
        "RUN_STATE_ENVELOPE_SCHEMA",
    ]
    for name, source in _v37_sources():
        assert "SNAPSHOT_SCHEMA" not in source, name


def test_a_study_without_inputs_has_its_pre_v37_identity() -> None:
    """Pinned by digest: the v3.6 rendering of a fixed study, recomputed."""

    from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind
    from alphalab.research import ResearchStudy

    study = ResearchStudy(
        study_name="pinned",
        dataset_version="panel@1",
        universe=("AAA",),
        features=(FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=2),),
    )

    assert study.study_id == (
        "pinned@"
        + hashlib.sha256(
            "\n".join(
                [
                    "alphalab.study.v1",
                    "name=pinned",
                    "dataset=panel@1",
                    "splits=none",
                    "seed=none",
                    "notes=",
                    "universe",
                    "AAA",
                    "features",
                    study.features[0].feature_version,
                    "horizons",
                    "parameters",
                ]
            ).encode()
        ).hexdigest()
    )


def test_a_non_adaptive_fingerprint_renders_exactly_as_at_v36() -> None:
    from alphalab.lifecycle import canonical_fingerprint_key
    from tests.unit.lifecycle.evidence_harness import fingerprint

    fp = fingerprint()
    key = canonical_fingerprint_key(
        fp.name, fp.strategy_id, fp.code, fp.dependencies, fp.parameters, fp.research, fp.engine
    )

    assert [line for line in key.splitlines() if "=" not in line] == [
        "alphalab.strategy_fingerprint.v1",
        "code",
        "dependencies",
        "parameters",
        "research",
        "engine",
    ]


# --------------------------------------------------------------------------- #
# 6. The vendor, network and application boundary
# --------------------------------------------------------------------------- #


def test_no_v37_module_names_a_prohibited_integration() -> None:
    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    offenders = [
        f"{name}: {term}"
        for name, source in _v37_sources()
        for term in FORBIDDEN_VENDORS
        if term in source.lower() and term not in ("torch", "transformers")
    ]
    assert offenders == []


def test_no_v37_module_reaches_a_network_or_a_secret() -> None:
    for name, source in _v37_sources():
        lowered = source.lower()
        for marker in ("http://", "https://", "socket", "urllib", "requests.", "api_key", "secret"):
            assert marker not in lowered, f"{marker} in {name}"


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"] == []


def test_the_public_surfaces_advertise_every_new_name() -> None:
    import alphalab.alt_data as alt_data
    import alphalab.api as api
    import alphalab.common as common
    import alphalab.factor_library as factor_library
    import alphalab.lifecycle as lifecycle
    import alphalab.research as research
    import alphalab.strategy as strategy

    expected: dict[object, tuple[str, ...]] = {
        common: ("PointInTimeStamp", "PointInTimeIndex", "AvailabilityBasis", "VisibilityRule"),
        alt_data: (
            "ExternalObservation",
            "InformationEvent",
            "FundamentalObservation",
            "ObservationSet",
            "ObservationView",
            "ObservationSource",
            "place_in_session",
            "trailing_twelve_months",
            "valuation_metrics",
        ),
        factor_library: ("KnowledgeFrame", "observation_frame", "fundamental_snapshot_as_of"),
        research: ("event_study", "classify_regimes", "regime_profile", "EventStudyDefinition"),
        strategy: ("AdaptiveStrategy", "apply_update", "replay_updates", "checkpoint", "restore"),
        lifecycle: ("assess_adaptive_replay", "research_configuration_with_adaptive"),
        api: ("ingest_observations", "ingest_events", "ingest_fundamentals", "lift_wire_records"),
    }
    for module, names in expected.items():
        for name in names:
            assert name in module.__all__, f"{module.__name__} does not advertise {name}"  # type: ignore[attr-defined]


def test_financial_values_stay_exact_decimals() -> None:
    """A reported figure is never rounded through a binary float on the way in."""

    from tests.unit.alt_data.pit_harness import issuer_records

    assert all(isinstance(record.value, Decimal) for record in issuer_records())
