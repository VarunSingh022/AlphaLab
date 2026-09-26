"""The v3.6 invariants, measured from the code rather than claimed in a release note.

Same shape as ``test_v35_invariants.py``. Each section is one property the
strategy-evaluation work depends on, and the determinism section spawns **fresh
interpreters with different hash seeds**: a salted ``hash()`` is perfectly stable
inside one process, so only a second process -- started with another
``PYTHONHASHSEED`` -- can tell a reproducible identity from a merely consistent
one.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import pathlib
import re
import subprocess
import sys
from decimal import Decimal

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"
ROOT = PACKAGE.parent

#: The modules v3.6 added. Every sweep is scoped to them, so the file polices
#: what this release wrote and nothing it did not.
V36_MODULES = (
    "alphalab/lifecycle/fingerprint.py",
    "alphalab/lifecycle/reproducibility.py",
    "alphalab/lifecycle/certification.py",
    "alphalab/lifecycle/portability.py",
)


def _sources() -> list[tuple[str, str]]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        found.append((str(path.relative_to(ROOT)), path.read_text()))
    return found


def _v36_sources() -> list[tuple[str, str]]:
    return [(name, text) for name, text in _sources() if name in V36_MODULES]


def _code(source: str) -> str:
    """The module with its docstrings removed, and so its comments too.

    A sweep for a forbidden construct has to read code: these modules say
    things like "nothing here reads a clock", and a grep over raw text would
    fail on the sentence stating the property it checks.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_every_v36_module_exists() -> None:
    """So a rename cannot silently empty every sweep in this file."""

    assert len(_v36_sources()) == len(V36_MODULES)


# --------------------------------------------------------------------------- #
# 1. One authority per concept
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("marker", "home"),
    [
        ("class StrategyFingerprint", "lifecycle/fingerprint.py"),
        ("class CodeIdentity", "lifecycle/fingerprint.py"),
        ("class DependencyManifest", "lifecycle/fingerprint.py"),
        ("class EngineIdentity", "lifecycle/fingerprint.py"),
        ("class ResearchConfiguration", "lifecycle/fingerprint.py"),
        ("class ReproducibilityManifest", "lifecycle/reproducibility.py"),
        ("class RunDigest", "lifecycle/reproducibility.py"),
        ("class CertificationReport", "lifecycle/certification.py"),
        ("class CertificationStatus", "lifecycle/certification.py"),
        ("class PropertyAssessment", "lifecycle/certification.py"),
        ("class PortabilityReport", "lifecycle/portability.py"),
        ("class TargetEnvironment", "lifecycle/portability.py"),
        ("def derive_strategy_fingerprint", "lifecycle/fingerprint.py"),
        ("def source_digest", "lifecycle/fingerprint.py"),
        ("def digest_run", "lifecycle/reproducibility.py"),
        ("def certify_strategy", "lifecycle/certification.py"),
        ("def evaluate_portability", "lifecycle/portability.py"),
    ],
)
def test_each_new_concept_has_exactly_one_home(marker: str, home: str) -> None:
    pattern = re.compile(rf"^{re.escape(marker)}\b", re.MULTILINE)
    found = [name for name, source in _sources() if pattern.search(source)]

    assert found == [f"alphalab/{home}"], f"{marker!r} is defined in {found}"


def test_v36_redefines_no_canonical_type() -> None:
    """Every compared or carried value is read from the authority that owns it."""

    canonical = {
        "Order",
        "BrokerOrder",
        "Fill",
        "ExecutionReport",
        "Position",
        "Account",
        "Trade",
        "RiskLimits",
        "BrokerCapabilities",
        "MarketAvailability",
        "MarketConvention",
        "HealthReport",
        "Dataset",
        "DatasetProvenance",
        "ValidationEvidence",
        "ResearchStudy",
        "StudyResult",
        "StrategyDefinition",
        "StrategyVersion",
        "DeploymentSpecification",
        "ReleasePackage",
        "ArtifactRef",
    }
    offenders = [
        f"{name}: {node.name}"
        for name, source in _v36_sources()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and node.name in canonical
    ]
    assert offenders == [], f"v3.6 redefined a canonical type: {offenders}"


def test_certification_accepts_observations_and_never_a_verdict() -> None:
    """A caller cannot hand in a HEALTHY report or a REPRODUCED assessment."""

    from alphalab.lifecycle.certification import CertificationEvidence

    verdicts = (
        "HealthReport",
        "HealthStatus",
        "ReproducibilityAssessment",
        "RerunOutcome",
        "PropertyAssessment",
        "CertificationReport",
        "CertificationStatus",
        "PortabilityReport",
    )
    for field in dataclasses.fields(CertificationEvidence):
        assert not any(verdict in str(field.type) for verdict in verdicts), field


def test_portability_checks_capabilities_through_the_v35_authorities() -> None:
    from alphalab.lifecycle import portability

    code = _code(dict(_v36_sources())["alphalab/lifecycle/portability.py"])
    assert "unmet_broker_requirements(" in code
    assert "unmet_market_requirements(" in code
    for field in ("broker", "market"):
        declared = {f.name: str(f.type) for f in dataclasses.fields(portability.TargetEnvironment)}
        assert declared[field] in ("BrokerCapabilities", "MarketAvailability")


def test_leverage_and_drawdown_are_the_gates_own_readings() -> None:
    """No new leverage formula: the gate's RiskState readings, nothing from positions."""

    code = _code(dict(_v36_sources())["alphalab/lifecycle/certification.py"])
    assert "RiskState(" in code
    assert ".current_leverage" in code
    assert ".current_drawdown_pct" in code
    for marker in ("market_value", "multiplier", "def _leverage", "/ snapshot.total_equity"):
        assert marker not in code, f"certification computes its own {marker}"


def test_run_identity_is_the_runs_own_canonical_record() -> None:
    """One record, captured and serialized by the owners, never re-rendered here."""

    code = _code(dict(_v36_sources())["alphalab/lifecycle/reproducibility.py"])
    assert "capture_run(result.run)" in code
    assert "serialize(snapshot)" in code
    assert "json.dumps" not in code, "a second serializer for a run's record"


def test_one_parameter_comparison_serves_every_v36_module() -> None:
    for name in ("certification.py", "portability.py"):
        code = _code(dict(_v36_sources())[f"alphalab/lifecycle/{name}"])
        assert "differing_parameters(" in code, name
        assert ".parameters) !=" not in code and "!= dict(" not in code, name


# --------------------------------------------------------------------------- #
# 2. Determinism: no clock, no salted hash, no environment
# --------------------------------------------------------------------------- #


#: Standard-library modules that read a clock, entropy, the process
#: environment or the machine. None of them may be imported by a v3.6 module.
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

#: Calls whose answer depends on the process: a salted hash, a memory address,
#: a minted identifier.
_NONDETERMINISTIC_CALLS = frozenset({"hash", "id", "new_id", "uuid4"})


def test_no_v36_module_reads_a_clock_entropy_or_the_environment() -> None:
    """Measured on the syntax tree: what is imported, and what is called.

    Read as code rather than text, because these modules *name* the things they
    refuse -- "its identifiers came from uuid4" is a sentence in a refusal, and
    a text sweep would fail on the sentence rather than on a call.
    """

    offenders: list[str] = []
    for name, source in _v36_sources():
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
    assert offenders == [], f"a v3.6 path is non-deterministic: {offenders}"


def test_the_sweep_would_catch_what_it_forbids() -> None:
    """A sweep that finds nothing on purpose must still be able to find something."""

    probe = "import time\nfrom uuid import uuid4\nvalue = hash('x') + id(object())\n"
    tree = ast.parse(probe)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "time" in imported & _NONDETERMINISTIC_MODULES
    assert {"hash", "id"} <= called & _NONDETERMINISTIC_CALLS


_CROSS_PROCESS = """
from alphalab.lifecycle import (
    CertificationEvidence, TargetEnvironment, certify_strategy, evaluate_portability,
    manifest_for_run,
)
from alphalab.runtime.run import ExecutionMode
from tests.unit.lifecycle.evidence_harness import (
    AVAILABLE, ENGINE, EVERYTHING, ASSET_ID, equity_convention, fingerprint, ingest,
    run_backtest, specification,
)

dataset = ingest()
fp = fingerprint()
spec = specification(dataset)
result = run_backtest(dataset)
manifest = manifest_for_run(result, dataset, fp, ENGINE)
rerun = manifest_for_run(run_backtest(dataset), dataset, fp, ENGINE)
evidence = CertificationEvidence(
    runs=(result,),
    repeated_runs=(result, run_backtest(dataset)),
    datasets=(dataset,),
    manifest=manifest,
    rerun=rerun,
)
report = certify_strategy(fp, spec, evidence)
portability = evaluate_portability(
    fp, spec, (TargetEnvironment("research", ExecutionMode.BACKTEST, EVERYTHING, AVAILABLE),),
    {ASSET_ID: equity_convention()},
)
print(dataset.dataset_version)
print(fp.fingerprint)
print(manifest.result_id)
print(manifest.manifest_id)
print(report.report_id)
print(portability.report_id)
"""


def _in_a_fresh_interpreter(hash_seed: str) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    return completed.stdout.split()


def test_every_v36_identity_is_the_same_in_two_processes_with_different_hash_seeds() -> None:
    """A salted hash anywhere in the chain would make these two lists differ."""

    first = _in_a_fresh_interpreter("1")
    second = _in_a_fresh_interpreter("4242")

    assert first == second
    assert len(first) == 6
    assert all(len(value.rpartition("@")[2]) == 64 for value in first)


def test_the_fresh_process_agrees_with_this_one() -> None:
    from alphalab.lifecycle import manifest_for_run
    from tests.unit.lifecycle.evidence_harness import ENGINE, fingerprint, ingest, run_backtest

    dataset = ingest()
    manifest = manifest_for_run(run_backtest(dataset), dataset, fingerprint(), ENGINE)
    other = _in_a_fresh_interpreter("7")

    assert other[1] == fingerprint().fingerprint
    assert other[2] == manifest.result_id
    assert other[3] == manifest.manifest_id


def test_nothing_machine_local_reaches_an_identity_or_an_artifact() -> None:
    from alphalab.lifecycle import (
        CertificationEvidence,
        canonical_fingerprint_key,
        certify_strategy,
        manifest_for_run,
    )
    from alphalab.persistence.serializer import serialize
    from tests.unit.lifecycle.evidence_harness import (
        ENGINE,
        fingerprint,
        ingest,
        run_backtest,
        specification,
    )

    dataset = ingest()
    fp = fingerprint()
    manifest = manifest_for_run(run_backtest(dataset), dataset, fp, ENGINE)
    report = certify_strategy(fp, specification(dataset), CertificationEvidence(manifest=manifest))
    rendered = "\n".join(
        [
            canonical_fingerprint_key(
                fp.name,
                fp.strategy_id,
                fp.code,
                fp.dependencies,
                fp.parameters,
                fp.research,
                fp.engine,
            ),
            serialize(fp),
            serialize(manifest),
            serialize(report),
        ]
    )

    local = {str(ROOT), str(pathlib.Path.home()), os.uname().nodename}
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        local.add(f"/{user}/")
    for marker in local:
        assert marker not in rendered, f"{marker!r} leaked into an identity or an artifact"


def test_no_environment_enters_a_fingerprint() -> None:
    """The identity a strategy carries from research to live names no environment."""

    from alphalab.lifecycle import canonical_fingerprint_key
    from tests.unit.lifecycle.evidence_harness import fingerprint

    fp = fingerprint()
    key = canonical_fingerprint_key(
        fp.name, fp.strategy_id, fp.code, fp.dependencies, fp.parameters, fp.research, fp.engine
    )
    for marker in ("BACKTEST", "REPLAY", "PAPER", "LIVE", "routing", "broker", "venue", "account"):
        assert marker not in key
    field_names = {field.name for field in dataclasses.fields(type(fp))}
    assert not {"environment", "mode", "broker", "deployment", "dataset"} & field_names


# --------------------------------------------------------------------------- #
# 3. Missing evidence is never a pass
# --------------------------------------------------------------------------- #


def test_no_evidence_is_never_a_pass_for_any_property() -> None:
    from alphalab.lifecycle import CertificationEvidence, CertificationStatus, certify_strategy
    from tests.unit.lifecycle.evidence_harness import fingerprint, ingest, specification

    dataset = ingest()
    report = certify_strategy(fingerprint(), specification(dataset), CertificationEvidence())

    assert {item.status for item in report.assessments} == {CertificationStatus.NOT_ASSESSED}


def test_an_estimate_or_an_unverified_capability_never_certifies() -> None:
    from alphalab.lifecycle import (
        CertificationEvidence,
        CertificationProperty,
        CertificationStatus,
        MeasurementBasis,
        PortabilityStatus,
        ResourceBudget,
        ResourceMeasurement,
        ResourceMetric,
        TargetEnvironment,
        certify_strategy,
        evaluate_portability,
    )
    from alphalab.runtime.run import ExecutionMode
    from tests.unit.lifecycle.evidence_harness import (
        AVAILABLE,
        EVERYTHING,
        fingerprint,
        ingest,
        specification,
    )

    dataset = ingest()
    fp, spec = fingerprint(), specification(dataset)
    estimate = ResourceMeasurement(
        ResourceMetric.CPU_SECONDS, Decimal("0"), MeasurementBasis.ESTIMATED, "a model", ""
    )
    report = certify_strategy(
        fp,
        spec,
        CertificationEvidence(measurements=(estimate,)),
        (ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("100")),),
    )
    assert report.status_of(CertificationProperty.RESOURCE_USAGE) is not CertificationStatus.PASS

    for mode in ExecutionMode:
        undeclared = TargetEnvironment("bare", mode, EVERYTHING, AVAILABLE)
        result = evaluate_portability(fp, spec, (undeclared,)).environments[0]
        assert result.status is not PortabilityStatus.PORTABLE, mode


# --------------------------------------------------------------------------- #
# 4. Detection is not remediation, and nothing is persisted
# --------------------------------------------------------------------------- #


def test_no_v36_module_mutates_anything_in_place() -> None:
    offenders: list[str] = []
    for name, source in _v36_sources():
        if "object.__setattr__" in _code(source):
            offenders.append(f"{name}: object.__setattr__")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute | ast.Subscript):
                        offenders.append(f"{name}: assignment to {ast.unparse(target)}")
    # Local dictionaries built up inside a function are not state; subscript
    # assignment to one is allowed only where the name is a local accumulator.
    allowed = {
        "alphalab/lifecycle/certification.py: assignment to refusals[violation.rule]",
        "alphalab/lifecycle/certification.py: assignment to evidence['traded']",
        "alphalab/lifecycle/certification.py: assignment to evidence['verified']",
        "alphalab/lifecycle/certification.py: assignment to evidence[f'observed.{metric.name}']",
        "alphalab/lifecycle/certification.py: assignment to evidence[f'basis.{metric.name}']",
        "alphalab/lifecycle/certification.py: assignment to evidence[f'environment.{metric.name}']",
        "alphalab/lifecycle/certification.py: assignment to evidence[f'estimated.{metric.name}']",
        "alphalab/lifecycle/certification.py: assignment to "
        "evidence[f'budget.{budget.metric.name}']",
    }
    unexpected = [offender for offender in offenders if offender not in allowed]
    assert unexpected == [], f"a v3.6 module mutates something: {unexpected}"


def test_v36_added_no_snapshot_owner_and_no_schema_constant() -> None:
    offenders = [
        f"{name}: {marker}"
        for name, source in _v36_sources()
        for marker in ("SNAPSHOT_SCHEMA", "def capture(", "def restore(", "__serializable__")
        if marker in source
    ]
    assert offenders == [], f"a v3.6 module grew durable state: {offenders}"


def test_the_lifecycle_state_is_unchanged_by_v36() -> None:
    """No fingerprint, manifest or report is a field on a persisted state."""

    from alphalab.lifecycle.snapshot import LIFECYCLE_SNAPSHOT_SCHEMA
    from alphalab.lifecycle.state import LifecycleState
    from alphalab.lifecycle.strategy_version import StrategyVersion

    assert {field.name for field in dataclasses.fields(LifecycleState)} == {
        "experiments",
        "models",
        "strategies",
        "deployments",
        "evidence",
        "releases",
        "approvals",
    }
    assert "fingerprint" not in {field.name for field in dataclasses.fields(StrategyVersion)}
    assert LIFECYCLE_SNAPSHOT_SCHEMA == 2


def test_evidence_id_for_is_untouched_by_v36() -> None:
    """ADR-0017's frozen digest: v3.6 references evidence and never re-derives it."""

    from alphalab.lifecycle.evidence import ValidationMethod, evidence_id_for

    assert evidence_id_for(
        ValidationMethod.BACKTEST, "s@1", "d", 7, {"sharpe_ratio": 1.5}
    ) == evidence_id_for(ValidationMethod.BACKTEST, "s@1", "d", 7, {"sharpe_ratio": 1.5})
    for _, source in _v36_sources():
        assert "def evidence_id_for" not in source


# --------------------------------------------------------------------------- #
# 5. Value semantics and units
# --------------------------------------------------------------------------- #


def test_every_v36_type_is_a_frozen_dataclass_or_an_enum() -> None:
    offenders: list[str] = []
    counted = 0
    for name, source in _v36_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            counted += 1
            if {ast.unparse(base) for base in node.bases} & {"Enum", "Protocol"}:
                continue
            decorators = {ast.unparse(decorator) for decorator in node.decorator_list}
            if "dataclass(frozen=True, slots=True)" not in decorators:
                offenders.append(f"{name}: {node.name} is {sorted(decorators)}")

    assert counted > 25, "the sweep found almost nothing, so it is not measuring"
    assert offenders == [], f"a v3.6 type is not an immutable value: {offenders}"


def test_every_v36_quantity_names_its_unit() -> None:
    from alphalab.lifecycle import ResourceMetric

    offenders: list[str] = []
    for name, source in _v36_sources():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    field = statement.target.id
                    if any(token in field for token in ("latency", "delay", "interval")) and not (
                        field.endswith("_seconds")
                    ):
                        offenders.append(f"{name}: {node.name}.{field}")
    assert offenders == [], f"a duration does not name its unit: {offenders}"

    timed = [metric for metric in ResourceMetric if "SECONDS" in metric.name]
    sized = [metric for metric in ResourceMetric if "MEMORY" in metric.name]
    assert timed and all(metric.name.endswith("_SECONDS") for metric in timed)
    assert sized and all(metric.name.endswith("_BYTES") for metric in sized)


# --------------------------------------------------------------------------- #
# 6. The vendor, marketplace and dependency boundary
# --------------------------------------------------------------------------- #


def test_no_v36_module_reaches_a_venue_or_a_secret() -> None:
    offenders: list[str] = []
    for name, source in _v36_sources():
        lowered = _code(source).lower()
        for marker in ("http", "requests", "websocket", "urllib", "socket", "api_key", "secret"):
            if marker in lowered:
                offenders.append(f"{name}: {marker}")
    assert offenders == [], f"a v3.6 module reaches connectivity or a secret: {offenders}"


def test_no_v36_module_names_a_prohibited_integration() -> None:
    from tests.regression.test_one_research_authority_per_concept import FORBIDDEN_VENDORS

    offenders = [
        f"{name}: {term}"
        for name, source in _v36_sources()
        for term in FORBIDDEN_VENDORS
        if term not in ("torch", "knight") and term in source.lower()
    ]  # raw text, including "marketplace": v3.6 contracts must not know who consumes them
    assert offenders == [], f"a prohibited integration is named in {offenders}"


def test_v36_contains_no_marketplace_operation() -> None:
    """Listing, selling, ranking and licensing are an application's, never AlphaLab's."""

    forbidden = re.compile(
        r"(listing|publish|purchase|payment|subscription|seller|buyer|storefront|"
        r"licen[cs]e|ranking|rank_|price_plan|checkout)"
    )
    offenders = [
        f"{name}: {node.name}"
        for name, source in _v36_sources()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and forbidden.search(node.name.lower())
    ]
    assert offenders == [], f"marketplace logic entered AlphaLab: {offenders}"


def test_the_package_still_has_no_runtime_dependency() -> None:
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == []


def test_the_lifecycle_package_is_still_imported_by_nothing() -> None:
    importers: set[str] = set()
    edges: set[str] = set()
    for name, source in _sources():
        package = ".".join(name[: -len(".py")].replace("/", ".").split(".")[:2])
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "alphalab"
            ):
                continue
            assert node.module is not None
            target = ".".join(node.module.split(".")[:2])
            if target == "alphalab.lifecycle" and package != target:
                importers.add(package)
            if package == "alphalab.lifecycle" and target != package:
                edges.add(target)

    assert importers == set()
    # The two edges v3.6 adds: the class registry (code identity) and the leaf
    # convention authority (contract terms). Neither imports anything above it.
    assert {"alphalab.strategy", "alphalab.conventions"} <= edges
    assert not {"alphalab.brokers", "alphalab.marketdata", "alphalab.api"} & edges


def test_the_public_surface_advertises_every_new_name() -> None:
    from alphalab import lifecycle

    for module in V36_MODULES:
        imported = sys.modules.get("alphalab.lifecycle." + module.rsplit("/", 1)[-1][: -len(".py")])
        assert imported is not None
        for name in imported.__all__:
            assert hasattr(lifecycle, name), f"{name} is not reachable from alphalab.lifecycle"
            assert name in lifecycle.__all__, f"{name} is not advertised by __all__"


def test_every_v36_value_is_machine_readable_as_deterministic_json() -> None:
    """The contract an external application consumes: persistence.serialize, unchanged."""

    from alphalab.lifecycle import (
        CertificationEvidence,
        TargetEnvironment,
        certify_strategy,
        evaluate_portability,
        manifest_for_run,
    )
    from alphalab.persistence.serializer import deserialize, serialize
    from alphalab.runtime.run import ExecutionMode
    from tests.unit.lifecycle.evidence_harness import (
        AVAILABLE,
        ENGINE,
        EVERYTHING,
        fingerprint,
        ingest,
        run_backtest,
        specification,
    )

    dataset = ingest()
    fp, spec = fingerprint(), specification(dataset)
    manifest = manifest_for_run(run_backtest(dataset), dataset, fp, ENGINE)
    report = certify_strategy(fp, spec, CertificationEvidence(manifest=manifest))
    portability = evaluate_portability(
        fp, spec, (TargetEnvironment("paper", ExecutionMode.PAPER, EVERYTHING, AVAILABLE),)
    )

    for value in (fp, manifest, report, portability):
        payload = serialize(value)
        assert serialize(value) == payload
        assert isinstance(deserialize(payload), dict)
