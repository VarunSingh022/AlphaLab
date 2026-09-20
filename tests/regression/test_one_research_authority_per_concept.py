"""One authority per research concept, measured rather than asserted in a note.

The v3.1 lesson, applied to v3.2: *"do not rely only on runtime tests to
discover architectural errors"*. v3.1 added a joining layer inside
``alphalab.data`` that closed three package cycles while every behavioural test
passed, because a cycle that resolves at import time is invisible at runtime.
The answer then was to measure the import graph on every run; the answer here
is to measure ownership the same way.

v3.2 adds a large research surface across two packages, which is exactly the
shape of change that grows a second authority for something that already had
one. Each check below names the concept, names its one owner, and fails with
the duplicate if another appears.

What "duplicate" means here
---------------------------

Not "two things with similar names" -- this repository deliberately keeps
several of those, and
``tests/regression/test_shared_names_stay_distinct.py`` records why for each
pair. A duplicate is a *second definition of the same concept*: a second class
that means "a computed feature value", a second function that derives a dataset
identity, a second unbiased sample variance.

:data:`PERMITTED_NAMESAKES` is where a deliberate reuse of a name is recorded,
following ``PERMITTED_MODULE_CYCLES`` in
``test_import_graph_stays_acyclic.py``: each entry is listed individually with
the reason, so adding one is a decision rather than an accident.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "alphalab"

#: Names defined in more than one place on purpose, with the reason for each.
#:
#: A concept below is still checked for exactly one definition *at its owner*;
#: this records the other definitions that share the spelling and are not the
#: same thing. ``test_shared_names_stay_distinct.py`` holds the argument.
PERMITTED_NAMESAKES: dict[str, dict[str, str]] = {
    "Dataset": {
        "alphalab/ml/dataset.py": (
            "an ML design matrix -- feature columns, a target vector and the "
            "asset each row belongs to. It holds no records, no schema, no "
            "provenance and no version, and it is built *from* Feature Store "
            "rather than from a source. Merging it with the canonical dataset "
            "would give one type two jobs."
        )
    },
}


def _classes_named(name: str) -> list[str]:
    """Every ``class <name>`` in the package, as ``module:line``."""

    found: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and node.name == name:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def _functions_named(name: str) -> list[str]:
    found: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


# --------------------------------------------------------------------------- #
# One definition per concept
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("concept", "owner"),
    [
        ("Dataset", "alphalab/data/dataset.py"),
        ("DatasetProvenance", "alphalab/data/provenance.py"),
        ("FeatureMetadata", "alphalab/feature_store/metadata.py"),
        ("FeatureValue", "alphalab/feature_store/value.py"),
        ("FeatureDefinition", "alphalab/factor_library/definition.py"),
        ("FeatureSeries", "alphalab/factor_library/series.py"),
        ("FeaturePanel", "alphalab/factor_library/panel.py"),
        ("FactorResult", "alphalab/factor_library/result.py"),
        ("ForwardReturnPanel", "alphalab/factor_library/forward_returns.py"),
        ("InformationCoefficient", "alphalab/factor_library/ic.py"),
        ("ObservationFrame", "alphalab/factor_library/observations.py"),
        ("TimeSplit", "alphalab/research/splits.py"),
        ("SplitInterval", "alphalab/research/splits.py"),
        ("PurgePolicy", "alphalab/research/purging.py"),
        ("SignalDiagnostics", "alphalab/research/signals.py"),
        ("ResearchStudy", "alphalab/research/study.py"),
        ("StudyResult", "alphalab/research/study.py"),
        ("OverfittingReport", "alphalab/research/overfitting.py"),
        ("Perturbation", "alphalab/research/perturbation.py"),
        ("ValidationEvidence", "alphalab/lifecycle/evidence.py"),
        ("ResearchState", "alphalab/research/state.py"),
        ("ResearchPayload", "alphalab/research/protocol.py"),
    ],
)
def test_each_research_concept_is_defined_exactly_once(concept: str, owner: str) -> None:
    permitted = PERMITTED_NAMESAKES.get(concept, {})
    found = [
        location
        for location in _classes_named(concept)
        if not any(location.startswith(allowed) for allowed in permitted)
    ]

    assert len(found) == 1, (
        f"{concept} is defined {len(found)} times outside its owner: {found}. If one of "
        "them is a deliberate namesake, record it in PERMITTED_NAMESAKES with the reason "
        "and state the argument in test_shared_names_stay_distinct.py."
    )
    assert found[0].startswith(owner), f"{concept} moved: expected {owner}, found {found[0]}"


@pytest.mark.parametrize(
    ("concept", "owner"),
    [
        ("derive_dataset_version", "alphalab/data/provenance.py"),
        ("derive_feature_version", "alphalab/factor_library/definition.py"),
        ("derive_series_id", "alphalab/factor_library/series.py"),
        ("derive_study_id", "alphalab/research/study.py"),
        ("derive_result_id", "alphalab/research/study.py"),
        ("evidence_id_for", "alphalab/lifecycle/evidence.py"),
        ("information_coefficient", "alphalab/factor_library/ic.py"),
        ("forward_returns", "alphalab/factor_library/forward_returns.py"),
        ("apply_purge_and_embargo", "alphalab/research/purging.py"),
        ("walk_forward_splits", "alphalab/research/walk_forward.py"),
        ("cross_validation_splits", "alphalab/research/time_series_cv.py"),
        ("signal_diagnostics", "alphalab/research/signals.py"),
        ("sample_variance", "alphalab/common/statistics.py"),
        ("pearson_correlation", "alphalab/common/statistics.py"),
        ("rank_correlation", "alphalab/common/statistics.py"),
        ("ranks", "alphalab/common/statistics.py"),
    ],
)
def test_each_derivation_has_exactly_one_implementation(concept: str, owner: str) -> None:
    found = _functions_named(concept)

    assert len(found) == 1, f"{concept} is implemented {len(found)} times: {found}"
    assert found[0].startswith(owner)


# --------------------------------------------------------------------------- #
# The consolidation v3.2 performed, and the rule that keeps it
# --------------------------------------------------------------------------- #


def test_nothing_outside_common_spells_the_unbiased_variance_again() -> None:
    """v3.2 replaced three private copies; a fourth must not appear.

    Read from the source, because a fourth copy would agree with the shared one
    on every test value and diverge only when somebody changed one of them.
    """

    pattern = "/ (len("
    offenders: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path) or path.name == "statistics.py":
            continue
        text = path.read_text()
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if ") ** 2 for" in stripped and ("- 1)" in stripped or pattern in stripped):
                offenders.append(f"{path.relative_to(ROOT)}:{number} {stripped[:70]}")

    assert not offenders, (
        "a module computes a sum of squared deviations divided by n-1 rather than calling "
        "alphalab.common.statistics.sample_variance:\n  " + "\n  ".join(offenders)
    )


def test_the_feature_store_still_computes_nothing() -> None:
    """Its stated architecture: registration and lifecycle, never computation.

    v3.2 added a computation layer and put it in ``factor_library``, which is
    the consumer this seam was designed for. If it had gone into Feature Store
    instead there would be two owners of "what a feature is".
    """

    store = PACKAGE / "feature_store"
    offenders: list[str] = []

    for path in sorted(store.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "alphalab.factor_library"
            ):
                offenders.append(f"{path.relative_to(ROOT)} imports {node.module}")

    assert not offenders, (
        "Feature Store imports the computation engine, which reverses the seam that keeps "
        f"them independent: {offenders}"
    )


def test_the_factor_library_does_not_import_the_feature_store_either() -> None:
    """The decoupling is structural in both directions.

    ``FactorResult`` satisfies ``FeatureValueProtocol`` *structurally*; neither
    package imports the other, which is what lets either be used alone.
    """

    library = PACKAGE / "factor_library"
    offenders: list[str] = []

    for path in sorted(library.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "alphalab.feature_store"
            ):
                offenders.append(f"{path.relative_to(ROOT)} imports {node.module}")

    assert not offenders, f"the structural seam became an import: {offenders}"


def test_the_research_package_owns_no_second_dataset_reader() -> None:
    """A study is handed its data; it never goes and gets its own.

    The rule ``alphalab.api.DataRequest`` states for selection, enforced for the
    research layer: two functions that could fetch independently could fetch
    different data, and no two results would be comparable.
    """

    research = PACKAGE / "research"
    forbidden = {"ingest_csv", "ingest_rows", "ingest_table", "read_delimited", "open"}
    offenders: list[str] = []

    for path in sorted(research.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.func.id}()")

    assert not offenders, f"a research module reads data of its own: {offenders}"


def test_only_the_application_api_joins_data_to_the_research_layer() -> None:
    """The v3.1 lesson: a joining layer sits above the things it joins.

    ``alphalab.api`` is the only module that imports both ``alphalab.data`` and
    ``alphalab.research``. Anything else doing so would be a second, lower
    joining point, and lowering it is exactly what closed three package cycles
    in v3.1.
    """

    joiners: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        modules = {
            node.module
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        reads_data = any(module.startswith("alphalab.data") for module in modules)
        reads_research = any(module.startswith("alphalab.research") for module in modules)
        if reads_data and reads_research:
            joiners.append(str(path.relative_to(ROOT)))

    assert joiners == ["alphalab/api.py"], (
        f"more than the application API joins data to research: {joiners}"
    )


def test_no_module_invents_an_overfitting_score() -> None:
    """The roadmap forbids it, and a name is how one would arrive.

    A blended figure is unfalsifiable: there is no experiment that shows an
    overfit score of 63 to be wrong. The report names measurements, thresholds
    and findings, and nothing that reads as a verdict.
    """

    # Deliberately narrow: ``quality_score`` in ``alphalab.data.quality`` is a
    # v3.1 measurement of a *dataset's* completeness, not a verdict on a
    # strategy, and is unrelated.
    forbidden = {
        "overfit_score",
        "overfitting_score",
        "overfitting_grade",
        "robustness_grade",
        "research_grade",
        "confidence_grade",
    }
    offenders: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in forbidden:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
            if isinstance(node, ast.AnnAssign):
                target = node.target
                line = node.lineno
                if isinstance(target, ast.Name) and target.id in forbidden:
                    offenders.append(f"{path.relative_to(ROOT)}:{line} {target.id}")

    assert not offenders, f"v3.2 invented a blended overfitting verdict: {offenders}"


# --------------------------------------------------------------------------- #
# No forbidden coupling
# --------------------------------------------------------------------------- #


#: Every external product, vendor and framework AlphaLab must not integrate.
#:
#: Declared once, here, because it is checked twice: this file reads **import
#: statements** for any of these roots, and
#: ``tests/regression/test_v34_invariants.py`` reads the **raw source text** for
#: the same names. The two catch different things -- an import, and a vendor
#: named in a docstring or a URL -- and a second copy of the list is how one of
#: them comes to be shorter than the other.
FORBIDDEN_VENDORS = (
    "quant_mind",
    "quantmind",
    "openbb",
    "knight",
    "iluvtrade",
    "reddesk",
    "marketplace",
    "langchain",
    "openai",
    "anthropic",
    "transformers",
    "torch",
    "tensorflow",
)


def test_the_package_integrates_no_external_product_or_vendor() -> None:
    """AlphaLab stays an independent engine, and this is measured every run.

    The boundary is permanent: AlphaLab must not know who is calling it. A
    research application, an AI system or a marketplace may sit above it and
    hand it a structured plan, but nothing here may import, adapt to or name
    any of them.
    """

    offenders: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            line = 0
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                line = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
                line = node.lineno
            for name in names:
                root = name.split(".")[0].lower()
                if root in FORBIDDEN_VENDORS:
                    offenders.append(f"{path.relative_to(ROOT)}:{line} imports {name}")

    assert not offenders, f"AlphaLab imports an external product or vendor SDK: {offenders}"


def test_the_runtime_still_depends_on_nothing_outside_the_standard_library() -> None:
    """v3.2 adds statistics, and adds no dependency to compute them with."""

    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert pyproject["project"]["dependencies"] == [], (
        "AlphaLab's runtime dependency list is not empty; v3.2's statistics are pure "
        "standard library and must stay that way"
    )


def test_the_computation_engine_is_reached_by_a_wired_path() -> None:
    """``factor_library`` left the standalone-engine list in v3.2, and stays off it.

    ``docs/ARCHITECTURE.md`` lists the packages reached by **neither** wired
    path. v3.2 took ``factor_library`` off that list, because
    ``alphalab.research`` imports it and ``alphalab.lifecycle`` imports
    ``research``. A claim in a document decays the moment somebody changes an
    import, which is the v3.1 lesson this file exists to apply, so the claim is
    measured here instead.

    The direction matters as much as the existence: nothing in
    ``factor_library`` may import ``research``, ``lifecycle`` or ``api``, or the
    edge becomes a cycle. ``test_import_graph_stays_acyclic.py`` measures that
    globally; this states it for the one edge v3.2 added.
    """

    importers: set[str] = set()
    outward: set[str] = set()

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        package = path.relative_to(PACKAGE).parts[0] if path.parent != PACKAGE else path.stem
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            target = ".".join(node.module.split(".")[:2])
            if target == "alphalab.factor_library" and package != "factor_library":
                importers.add(package)
            if package == "factor_library":
                outward.add(target)

    assert "research" in importers, (
        "alphalab.research no longer imports the computation engine, so "
        "factor_library is a standalone engine again and ARCHITECTURE.md is wrong"
    )
    assert "api" in importers, "the application API no longer reaches the computation engine"

    forbidden = {"alphalab.research", "alphalab.lifecycle", "alphalab.api"}
    assert not outward & forbidden, (
        f"factor_library imports {sorted(outward & forbidden)}, which closes a cycle with "
        "the packages that import it"
    )


def test_the_feature_store_is_still_a_standalone_engine() -> None:
    """And it stays one, which is the seam working rather than an oversight.

    Feature Store computes nothing, so the computation engine writes *through*
    it structurally rather than being imported by it. Its only importer is
    ``ml``. If ``factor_library`` or ``research`` ever appeared here, the
    decoupling that lets either package be used alone would be gone.
    """

    importers: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        package = path.relative_to(PACKAGE).parts[0] if path.parent != PACKAGE else path.stem
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("alphalab.feature_store")
                and package != "feature_store"
            ):
                importers.add(package)

    assert importers == {"ml"}, (
        f"alphalab.feature_store is imported by {sorted(importers)}; it was {{'ml'}} at "
        "v3.2, and a new importer means the compute/registry seam has been crossed"
    )
