"""A strategy fingerprint: what defines it, what does not, and what it refuses.

The identity is the whole point, so most of this file is about it: equal inputs
must identify equally, each defining input must be able to change it on its own,
and nothing that is only presentation -- a mapping's order, a directory
listing's order, a distribution name's spelling -- may reach it.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import MappingProxyType

import pytest

from alphalab.lifecycle import (
    NO_DEPENDENCIES,
    STRATEGY_FINGERPRINT_SCHEME,
    UNDECLARED_DEPENDENCIES,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    LifecycleInputError,
    ResearchConfiguration,
    build_fingerprint,
    canonical_fingerprint_key,
    code_identity_for,
    derive_strategy_fingerprint,
    differing_parameters,
    fingerprint_differences,
    fingerprint_for_version,
    normalize_distribution_name,
    research_configuration,
    research_configuration_for_study,
    running_engine,
    source_digest,
    verify_fingerprint,
)
from alphalab.lifecycle.strategy_version import StrategyVersion
from alphalab.research.study import ResearchStudy
from alphalab.strategy.registry import StrategyClassRegistry
from alphalab.studio.strategy import StrategyDefinition
from tests.unit.lifecycle.evidence_harness import (
    CODE,
    DEFINITION,
    ENGINE,
    RESEARCH,
    SOURCES,
    VERSION,
    BarStrategy,
    fingerprint,
)

PINS = (
    DependencyPin("numpy", "1.26.4", "a" * 64),
    DependencyPin("pandas", "2.2.2"),
)
EXACT = DependencyManifest(DependencyCompleteness.EXACT_CLOSURE, PINS)


# --------------------------------------------------------------------------- #
# Construction and derivation
# --------------------------------------------------------------------------- #


def test_a_fingerprint_is_the_line_name_and_a_full_sha256_digest() -> None:
    fp = fingerprint()

    name, separator, digest = fp.fingerprint.partition("@")
    assert (name, separator) == (VERSION.name, "@")
    assert len(digest) == 64
    assert fp.digest == digest
    assert verify_fingerprint(fp)


def test_the_version_supplies_the_name_the_strategy_id_and_the_parameters() -> None:
    """ADR-0017's rule: derived from the registered version, not typed again."""

    fp = fingerprint()

    assert fp.name == VERSION.name
    assert fp.strategy_id == DEFINITION.strategy_id
    assert dict(fp.parameters) == dict(DEFINITION.parameters)


def test_the_key_starts_with_the_scheme_and_renders_every_section() -> None:
    fp = fingerprint()
    key = canonical_fingerprint_key(
        fp.name, fp.strategy_id, fp.code, fp.dependencies, fp.parameters, fp.research, fp.engine
    )
    lines = key.split("\n")

    assert lines[0] == STRATEGY_FINGERPRINT_SCHEME
    for section in ("code", "dependencies", "parameters", "research", "engine"):
        assert section in lines


def test_the_rendering_is_pinned_to_a_golden_digest() -> None:
    """A change to the construction must be a decision, and this is where it shows."""

    arguments = (
        "golden",
        "GOLDEN",
        CodeIdentity("pkg", "1.0.0", "pkg.Strategy", None),
        NO_DEPENDENCIES,
        {"fast": 10.0, "slow": 30.0},
        research_configuration({"validation": "walk-forward"}),
        EngineIdentity("alphalab", "3.6.0"),
    )
    fp = build_fingerprint(*arguments)
    key = canonical_fingerprint_key(*arguments)

    assert fp.digest == hashlib.sha256(key.encode("utf-8")).hexdigest()
    assert fp.fingerprint == derive_strategy_fingerprint(*arguments)
    assert fp.fingerprint == (
        "golden@648e315807f6bfb22a99f8bdb27b361e861a9c079d14c3f7870a78e2c2aaf6d4"
    )


# --------------------------------------------------------------------------- #
# Every defining input changes the identity, independently
# --------------------------------------------------------------------------- #


def test_changed_code_content_changes_the_fingerprint() -> None:
    changed = replace(CODE, source_digest=source_digest({**SOURCES, "v36_strategies/x.py": b"1"}))

    assert fingerprint(code=changed).fingerprint != fingerprint().fingerprint


@pytest.mark.parametrize(
    "changed",
    [
        replace(CODE, package="another-package"),
        replace(CODE, version="1.0.1"),
        replace(CODE, version=None),
        replace(CODE, entry_point="v36_strategies.momentum.Other"),
        replace(CODE, source_digest=None),
    ],
    ids=["package", "version", "unversioned", "entry_point", "unhashed"],
)
def test_each_code_field_changes_the_fingerprint(changed: CodeIdentity) -> None:
    assert fingerprint(code=changed).fingerprint != fingerprint().fingerprint


def test_changed_dependencies_change_the_fingerprint() -> None:
    base = fingerprint(dependencies=EXACT).fingerprint
    bumped = DependencyManifest(
        DependencyCompleteness.EXACT_CLOSURE,
        (DependencyPin("numpy", "1.26.5", "a" * 64), PINS[1]),
    )
    rehashed = DependencyManifest(
        DependencyCompleteness.EXACT_CLOSURE,
        (DependencyPin("numpy", "1.26.4", "b" * 64), PINS[1]),
    )
    added = DependencyManifest(
        DependencyCompleteness.EXACT_CLOSURE, (*PINS, DependencyPin("scipy", "1.13.0"))
    )
    weaker = DependencyManifest(DependencyCompleteness.DIRECT_ONLY, PINS)

    for manifest in (bumped, rehashed, added, weaker):
        assert fingerprint(dependencies=manifest).fingerprint != base


def test_changed_parameters_change_the_fingerprint() -> None:
    changed = replace(
        VERSION, definition=replace(DEFINITION, parameters={"entry": 11.0, "exit": -4.0})
    )
    added = replace(
        VERSION,
        definition=replace(DEFINITION, parameters={"entry": 10.0, "exit": -4.0, "stop": 2.0}),
    )

    assert fingerprint(changed).fingerprint != fingerprint().fingerprint
    assert fingerprint(added).fingerprint != fingerprint().fingerprint


def test_changed_research_configuration_changes_the_fingerprint() -> None:
    edited = research_configuration({**RESEARCH.settings, "validation": "walk-forward 5x"})
    added = research_configuration({**RESEARCH.settings, "universe": "one name"})
    with_study = research_configuration(RESEARCH.settings, study_id="study@" + "c" * 64)

    for research in (edited, added, with_study):
        assert fingerprint(research=research).fingerprint != fingerprint().fingerprint


def test_a_changed_engine_version_changes_the_fingerprint() -> None:
    assert (
        fingerprint(engine=EngineIdentity("alphalab", "3.6.1")).fingerprint
        != fingerprint().fingerprint
    )


def test_the_strategy_id_and_the_line_name_are_part_of_the_identity() -> None:
    renamed = replace(VERSION, name="renamed")
    rekeyed = replace(VERSION, definition=replace(DEFINITION, strategy_id="OTHER"))

    assert fingerprint(renamed).fingerprint != fingerprint().fingerprint
    assert fingerprint(rekeyed).fingerprint != fingerprint().fingerprint


def test_the_version_number_is_not_an_input() -> None:
    """``name@N`` is registration order; two registries number one content differently."""

    assert fingerprint(replace(VERSION, version=7)).fingerprint == fingerprint().fingerprint


def test_a_numbers_spelling_is_kept_as_the_specification_keeps_it() -> None:
    """``10`` and ``10.0`` render differently in specification_id_for, and here."""

    integer = replace(
        VERSION, definition=replace(DEFINITION, parameters={"entry": 10, "exit": -4.0})
    )

    assert fingerprint(integer).fingerprint != fingerprint().fingerprint
    assert differing_parameters({"entry": 10}, {"entry": 10.0}) == ("entry",)


# --------------------------------------------------------------------------- #
# Presentation does not reach the identity
# --------------------------------------------------------------------------- #


def test_mapping_order_does_not_change_the_fingerprint() -> None:
    forward = replace(
        VERSION, definition=replace(DEFINITION, parameters={"entry": 10.0, "exit": -4.0})
    )
    backward = replace(
        VERSION, definition=replace(DEFINITION, parameters={"exit": -4.0, "entry": 10.0})
    )
    settings = dict(reversed(list(RESEARCH.settings.items())))

    assert fingerprint(forward).fingerprint == fingerprint(backward).fingerprint
    assert (
        fingerprint(research=research_configuration(settings)).fingerprint
        == fingerprint().fingerprint
    )


def test_pin_order_and_name_spelling_do_not_change_the_fingerprint() -> None:
    reordered = DependencyManifest(DependencyCompleteness.EXACT_CLOSURE, tuple(reversed(PINS)))
    respelled = DependencyManifest(
        DependencyCompleteness.EXACT_CLOSURE,
        (DependencyPin("NumPy", "1.26.4", "a" * 64), DependencyPin("Pandas", "2.2.2")),
    )

    base = fingerprint(dependencies=EXACT).fingerprint
    assert fingerprint(dependencies=reordered).fingerprint == base
    assert fingerprint(dependencies=respelled).fingerprint == base
    assert normalize_distribution_name("Zope.Interface__x") == "zope-interface-x"


def test_source_order_does_not_change_the_source_digest() -> None:
    assert source_digest(dict(reversed(list(SOURCES.items())))) == source_digest(SOURCES)


def test_a_mapping_proxy_and_a_dict_identify_alike() -> None:
    assert (
        fingerprint(
            research=ResearchConfiguration(MappingProxyType(dict(RESEARCH.settings)))
        ).fingerprint
        == fingerprint(research=ResearchConfiguration(dict(RESEARCH.settings))).fingerprint
    )


# --------------------------------------------------------------------------- #
# Source digests name files in a package, never a machine
# --------------------------------------------------------------------------- #


def test_the_source_digest_depends_on_every_file_and_every_byte() -> None:
    base = source_digest(SOURCES)

    assert source_digest({**SOURCES, "v36_strategies/momentum.py": b"class M: pass\n"}) != base
    assert (
        source_digest({"v36_strategies/momentum.py": SOURCES["v36_strategies/momentum.py"]}) != base
    )
    renamed = {
        "pkg/momentum.py": SOURCES["v36_strategies/momentum.py"],
        **{"v36_strategies/__init__.py": b""},
    }
    assert source_digest(renamed) != base


@pytest.mark.parametrize(
    "path",
    [
        "/Users/someone/strategies/momentum.py",
        "~/strategies/momentum.py",
        "C:/strategies/momentum.py",
        "strategies\\momentum.py",
        "strategies/../momentum.py",
        "strategies/./momentum.py",
        "strategies//momentum.py",
        "",
    ],
)
def test_a_path_that_names_a_machine_is_refused(path: str) -> None:
    with pytest.raises(LifecycleInputError):
        source_digest({path: b"x"})


def test_a_digest_over_no_files_is_refused() -> None:
    with pytest.raises(LifecycleInputError, match="identifies nothing"):
        source_digest({})


# --------------------------------------------------------------------------- #
# Code identity from the class registry
# --------------------------------------------------------------------------- #


def _factory(strategy_id: str, parameters: object) -> BarStrategy:
    return BarStrategy(strategy_id, "asset", {})


def test_the_entry_point_is_read_from_the_class_registry() -> None:
    registry = StrategyClassRegistry().register("V36", _factory)
    registration = registry.require("V36")

    code = code_identity_for(registration, "pkg", "2.0.0", SOURCES)

    assert code.entry_point == registration.qualified_name
    assert code.source_digest == source_digest(SOURCES)
    assert code.content_addressed
    assert not code_identity_for(registration, "pkg", None, None).content_addressed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"package": " "},
        {"entry_point": ""},
        {"entry_point": "a b"},
        {"version": ">=1.0"},
        {"version": "1.*"},
        {"source_digest": "not-a-digest"},
    ],
)
def test_malformed_code_identities_are_refused(kwargs: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "package": "pkg",
        "version": "1.0",
        "entry_point": "pkg.S",
        "source_digest": None,
        **kwargs,
    }
    with pytest.raises(LifecycleInputError):
        CodeIdentity(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Dependency identity is explicit about its own completeness
# --------------------------------------------------------------------------- #


def test_no_dependencies_and_undeclared_dependencies_are_different_claims() -> None:
    assert NO_DEPENDENCIES.exact
    assert not UNDECLARED_DEPENDENCIES.exact
    assert (
        fingerprint(dependencies=NO_DEPENDENCIES).fingerprint
        != fingerprint(dependencies=UNDECLARED_DEPENDENCIES).fingerprint
    )


def test_an_undeclared_manifest_cannot_carry_pins() -> None:
    with pytest.raises(LifecycleInputError, match="UNDECLARED"):
        DependencyManifest(DependencyCompleteness.UNDECLARED, PINS)


def test_direct_only_with_nothing_pinned_has_one_spelling_and_it_is_not_this() -> None:
    with pytest.raises(LifecycleInputError, match="EXACT_CLOSURE"):
        DependencyManifest(DependencyCompleteness.DIRECT_ONLY, ())


def test_one_distribution_pinned_twice_is_refused_after_normalization() -> None:
    with pytest.raises(LifecycleInputError, match="more than once"):
        DependencyManifest(
            DependencyCompleteness.EXACT_CLOSURE,
            (DependencyPin("Requests", "2.31.0"), DependencyPin("requests", "2.32.0")),
        )


@pytest.mark.parametrize("version", [">=2.0", "~=2.0", "2.*", "2.0,<3", "", " 2.0"])
def test_a_specifier_is_not_a_pin(version: str) -> None:
    with pytest.raises(LifecycleInputError):
        DependencyPin("requests", version)


@pytest.mark.parametrize("version", ["2.31.0", "1!2.0", "2.0.0rc1", "1.0+local.7", "0"])
def test_exact_pep440_versions_are_accepted(version: str) -> None:
    assert DependencyPin("requests", version).version == version


def test_an_artifact_digest_must_be_a_sha256() -> None:
    with pytest.raises(LifecycleInputError):
        DependencyPin("requests", "2.31.0", "ABC")


# --------------------------------------------------------------------------- #
# Research configuration and the engine
# --------------------------------------------------------------------------- #


def test_a_research_configuration_that_states_nothing_is_refused() -> None:
    with pytest.raises(LifecycleInputError, match="neither a setting nor a study"):
        ResearchConfiguration({})


def test_a_blank_setting_is_refused() -> None:
    with pytest.raises(LifecycleInputError):
        research_configuration({"validation": " "})


def test_a_study_configuration_carries_the_studys_derived_identity() -> None:
    from alphalab.factor_library.definition import FeatureDefinition, FeatureField, FeatureKind

    study = ResearchStudy(
        study_name="v36",
        dataset_version="d@" + "e" * 64,
        universe=("A",),
        features=(FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5),),
        seed=7,
    )

    research = research_configuration_for_study(study, {"note": "x"})

    assert research.study_id == study.study_id
    assert fingerprint(research=research).fingerprint != fingerprint().fingerprint


def test_the_running_engine_is_an_observation_the_caller_records() -> None:
    import alphalab

    engine = running_engine()

    assert engine == EngineIdentity("alphalab", alphalab.__version__)
    assert str(engine) == f"alphalab=={alphalab.__version__}"


# --------------------------------------------------------------------------- #
# Verification and inspection
# --------------------------------------------------------------------------- #


def test_an_edited_fingerprint_stops_verifying() -> None:
    fp = fingerprint()

    assert not verify_fingerprint(replace(fp, parameters={"entry": 99.0, "exit": -4.0}))
    assert not verify_fingerprint(replace(fp, engine=EngineIdentity("alphalab", "9.9.9")))
    assert not verify_fingerprint(replace(fp, fingerprint=fp.name + "@" + "0" * 64))
    assert not verify_fingerprint(replace(fp, name="bad@name"))


def test_differences_name_every_input_that_changed() -> None:
    before = fingerprint(dependencies=EXACT)
    after = fingerprint(
        replace(VERSION, definition=replace(DEFINITION, parameters={"entry": 12.0, "exit": -4.0})),
        code=replace(CODE, version="1.1.0"),
        dependencies=DependencyManifest(DependencyCompleteness.DIRECT_ONLY, (PINS[0],)),
        engine=EngineIdentity("alphalab", "3.7.0"),
    )

    differences = fingerprint_differences(before, after)

    assert fingerprint_differences(before, before) == ()
    joined = "\n".join(differences)
    for marker in (
        "code.version",
        "dependencies.completeness",
        "dependency 'pandas'",
        "parameter 'entry'",
        "engine",
    ):
        assert marker in joined


@pytest.mark.parametrize(
    "parameters",
    [{"entry": float("nan")}, {"entry": float("inf")}, {"entry": True}, {"": 1.0}],
)
def test_parameters_that_cannot_identify_are_refused(parameters: dict[str, object]) -> None:
    version = StrategyVersion(
        name="bad",
        version=1,
        definition=StrategyDefinition("BAD", "bad", "1", "a", "d", parameters),  # type: ignore[arg-type]
        stage=VERSION.stage,
    )
    with pytest.raises(LifecycleInputError):
        fingerprint_for_version(version, CODE, NO_DEPENDENCIES, RESEARCH, ENGINE)


@pytest.mark.parametrize("name", ["", "a@b", "line\nbreak"])
def test_names_that_cannot_prefix_a_digest_are_refused(name: str) -> None:
    with pytest.raises(LifecycleInputError):
        build_fingerprint(name, "ID", CODE, NO_DEPENDENCIES, {}, RESEARCH, ENGINE)


def test_the_fingerprints_parameters_cannot_be_edited_through_it() -> None:
    parameters = {"entry": 10.0}
    fp = build_fingerprint("n", "ID", CODE, NO_DEPENDENCIES, parameters, RESEARCH, ENGINE)
    parameters["entry"] = 99.0

    assert fp.parameters["entry"] == 10.0
    with pytest.raises(TypeError):
        fp.parameters["entry"] = 1.0  # type: ignore[index]
    assert verify_fingerprint(fp)
