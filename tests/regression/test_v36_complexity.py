"""The v3.6 paths stay near-linear, measured on every run.

Same shape as ``test_v35_complexity.py``: assert on the **growth ratio** between
two input sizes rather than on an absolute duration, so the test is about the
algorithm and not the machine. Quadrupling the input quadruples a linear path and
multiplies a quadratic one by sixteen; the bound sits between the two.

Which paths, and why these
---------------------------

Each is one where the obvious implementation is quadratic, or where the input
can be large in practice:

* **A fingerprint** over a large parameter set, a long lock file and many
  research settings. Sorting is ``n log n``; a per-entry scan of another
  section, or re-sorting inside a loop, would not be.
* **A source digest** over a large package.
* **A run digest** -- one capture and one serialization of the whole run. The
  identity covers everything the run did, so it has to stay linear in it.
* **Certification** over a long run: the risk, drawdown and leverage readings
  walk every recorded snapshot and every pre-trade decision once.
* **Portability** across many environments and many instruments, where a
  per-instrument scan of the other side would be quadratic.
* **Resource usage** over many measurements, grouped by metric once.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alphalab.api import backtest, ingest_rows
from alphalab.backtesting.state import BacktestResult
from alphalab.data.corporate_actions import PriceBasis
from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import IngestionRequest
from alphalab.data.source import SourceKind, raw_source_from_bytes
from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency
from alphalab.lifecycle import (
    CertificationEvidence,
    CodeIdentity,
    DependencyCompleteness,
    DependencyManifest,
    DependencyPin,
    EngineIdentity,
    MarketAvailability,
    MarketRequirements,
    MeasurementBasis,
    ResourceBudget,
    ResourceMeasurement,
    ResourceMetric,
    TargetEnvironment,
    build_fingerprint,
    certify_strategy,
    digest_run,
    evaluate_portability,
    fingerprint_differences,
    research_configuration,
    source_digest,
    verify_fingerprint,
)
from alphalab.runtime.run import ExecutionMode
from tests.integration.harness import context_factory, running_strategy_state
from tests.unit.lifecycle.evidence_harness import (
    ASSET_ID,
    CLEANING,
    EVERYTHING,
    NORMALIZATION,
    STRATEGY_ID,
    SYMBOL,
    BarStrategy,
    csv_payload,
    equity_convention,
    fingerprint,
    run_config,
    specification,
)

#: A ratio above linear and well below quadratic.
LINEAR_BOUND = 8.0

START = datetime(2020, 1, 1, tzinfo=UTC)


def _elapsed(work: Callable[[], object]) -> float:
    """Best of three, so one scheduling hiccup does not fail the suite."""

    return min(_once(work) for _ in range(3))


def _once(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def _growth(small: Callable[[], object], large: Callable[[], object]) -> float:
    return _elapsed(large) / max(_elapsed(small), 1e-4)


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #


def _fingerprint_inputs(size: int) -> tuple[object, ...]:
    pins = tuple(DependencyPin(f"package-{index:05d}", f"1.{index}.0") for index in range(size))
    return (
        "scaled",
        "SCALED",
        CodeIdentity("pkg", "1.0.0", "pkg.Strategy", None),
        DependencyManifest(DependencyCompleteness.EXACT_CLOSURE, pins),
        {f"p{index:05d}": float(index) for index in range(size)},
        research_configuration({f"s{index:05d}": f"value {index}" for index in range(size)}),
        EngineIdentity("alphalab", "3.6.0"),
    )


def test_fingerprinting_is_linear_in_parameters_pins_and_settings() -> None:
    small, large = _fingerprint_inputs(1_000), _fingerprint_inputs(4_000)

    def derive(inputs: tuple[object, ...]) -> Callable[[], object]:
        return lambda: verify_fingerprint(build_fingerprint(*inputs))  # type: ignore[arg-type]

    growth = _growth(derive(small), derive(large))
    assert growth < LINEAR_BOUND, f"fingerprinting grew {growth:.1f}x for 4x input"


def test_fingerprint_differences_are_linear() -> None:
    small = build_fingerprint(*_fingerprint_inputs(1_000))  # type: ignore[arg-type]
    large = build_fingerprint(*_fingerprint_inputs(4_000))  # type: ignore[arg-type]
    small_other = replace(small, parameters={**small.parameters, "p00000": -1.0})
    large_other = replace(large, parameters={**large.parameters, "p00000": -1.0})

    growth = _growth(
        lambda: fingerprint_differences(small, small_other),
        lambda: fingerprint_differences(large, large_other),
    )
    assert growth < LINEAR_BOUND


def test_source_digests_are_linear_in_files() -> None:
    def sources(count: int) -> dict[str, bytes]:
        return {f"pkg/module_{index:05d}.py": b"x = 1\n" * 20 for index in range(count)}

    small, large = sources(1_000), sources(4_000)

    growth = _growth(lambda: source_digest(small), lambda: source_digest(large))
    assert growth < LINEAR_BOUND


# --------------------------------------------------------------------------- #
# Runs, certification, portability
# --------------------------------------------------------------------------- #


def _dataset(days: int) -> Dataset:
    rows = [
        {
            "symbol": SYMBOL,
            "timestamp": (START + timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": f"{100 + (day % 7):.3f}",
            "high": f"{101 + (day % 7):.3f}",
            "low": f"{99 + (day % 7):.3f}",
            "close": f"{100.5 + (day % 7):.3f}",
            "volume": 1_000 + day,
        }
        for day in range(days)
    ]
    request = IngestionRequest(
        name=f"SCALED-{days}",
        source=raw_source_from_bytes(
            SourceKind.IN_MEMORY, "v36-complexity", csv_payload(rows), 1.0, "text/csv", "utf-8"
        ),
        frequency=TimeFrequency.DAILY,
        asset_class=DataAssetClass.EQUITY,
        cleaning_policy=CLEANING,
        price_basis=PriceBasis.RAW,
        timezone_name="UTC",
    )
    return ingest_rows(rows, request).dataset


def _run(days: int) -> tuple[Dataset, BacktestResult]:
    dataset = _dataset(days)
    plan = {
        index: Decimal("1") if index % 20 == 0 else Decimal("-1") for index in range(1, days, 10)
    }
    result = backtest(
        run_config(),
        dataset,
        running_strategy_state(STRATEGY_ID, BarStrategy(STRATEGY_ID, ASSET_ID, plan)),
        context_factory,
        NORMALIZATION,
    )
    return dataset, result


def test_a_run_digest_is_linear_in_the_run() -> None:
    _, small = _run(150)
    _, large = _run(600)

    growth = _growth(lambda: digest_run(small), lambda: digest_run(large))
    assert growth < LINEAR_BOUND, f"digesting a run grew {growth:.1f}x for 4x records"


def test_certifying_a_long_run_is_linear_in_it() -> None:
    small_data, small = _run(150)
    large_data, large = _run(600)
    fp = fingerprint()

    def certify(dataset: Dataset, result: BacktestResult) -> Callable[[], object]:
        spec = specification(dataset)
        evidence = CertificationEvidence(runs=(result,), datasets=(dataset,))
        return lambda: certify_strategy(fp, spec, evidence)

    growth = _growth(certify(small_data, small), certify(large_data, large))
    assert growth < LINEAR_BOUND, f"certification grew {growth:.1f}x for 4x records"


def test_resource_usage_is_linear_in_measurements() -> None:
    dataset = _dataset(20)
    fp, spec = fingerprint(), specification(dataset)

    def measurements(count: int) -> tuple[ResourceMeasurement, ...]:
        return tuple(
            ResourceMeasurement(
                ResourceMetric.CPU_SECONDS,
                Decimal(index) / Decimal(1000),
                MeasurementBasis.MEASURED,
                "process time",
                f"agent {index % 7}",
            )
            for index in range(count)
        )

    budgets = (ResourceBudget(ResourceMetric.CPU_SECONDS, Decimal("10")),)
    small = CertificationEvidence(measurements=measurements(2_000))
    large = CertificationEvidence(measurements=measurements(8_000))

    growth = _growth(
        lambda: certify_strategy(fp, spec, small, budgets),
        lambda: certify_strategy(fp, spec, large, budgets),
    )
    assert growth < LINEAR_BOUND


def test_portability_is_linear_in_environments_and_instruments() -> None:
    dataset = _dataset(20)
    fp = fingerprint()

    def workload(size: int) -> Callable[[], object]:
        instruments = (ASSET_ID, *(f"asset-{index:05d}" for index in range(size)))
        market = MarketRequirements(instruments, ("XNYS",), ("XNYS",), ("USD",))
        spec = specification(dataset, market=market)
        availability = MarketAvailability(
            frozenset(instruments), frozenset({"XNYS"}), frozenset({"XNYS"}), frozenset({"USD"})
        )
        conventions = {asset: equity_convention() for asset in instruments}
        environments = tuple(
            TargetEnvironment(
                f"env-{index:04d}",
                ExecutionMode.BACKTEST,
                EVERYTHING,
                availability,
                datasets=frozenset({dataset.require_provenance().dataset_version}),
                conventions=conventions,
            )
            for index in range(max(1, size // 50))
        )
        return lambda: evaluate_portability(fp, spec, environments, conventions)

    growth = _growth(workload(500), workload(1_000))
    # Environments and instruments both double, so linear in their product is 4x.
    assert growth < LINEAR_BOUND * 2, f"portability grew {growth:.1f}x"
