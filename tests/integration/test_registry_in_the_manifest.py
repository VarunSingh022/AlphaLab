"""A run's manifest can name the instrument registry it held (phase 4 global gap audit).

A run's record names its registry by type only (ADR-0023): the records, venue
controls, borrow, margin model and volatility surfaces it was given are a live
object the caller supplies back. ``manifest_for_run(instruments=True)`` records
the SHA-256 of the registry the run held, as its snapshot writes it -- read off
the run, never supplied -- and renders it into the manifest's identity, so two
runs given different terms have different manifests even where their records
agree. Asked for nothing, a manifest keeps the identity it had.

Example 72's multi-asset run supplies the dataset, strategy and engine.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.backtesting import BacktestResult
from alphalab.lifecycle import (
    canonical_manifest_key,
    manifest_for_run,
    running_build,
    running_engine,
    verify_manifest,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "72_compose_a_multi_asset_strategy.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_72_registry", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(example: ModuleType) -> Any:
    return example.inputs()


@pytest.fixture(scope="module")
def result(example: ModuleType, data: Any) -> BacktestResult:
    run: BacktestResult = example.run(data)
    return run


def manifest(example: ModuleType, data: Any, result: BacktestResult, **kw: Any) -> Any:
    return manifest_for_run(
        result,
        data.prices,
        example.fingerprint(data),
        running_engine(),
        build=running_build(),
        schedule=data.schedule(),
        **kw,
    )


def test_asked_for_nothing_a_manifest_keeps_its_identity(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    plain = manifest(example, data, result)
    assert plain.instruments_digest is None
    assert plain == example.manifest(data, result)
    assert "inputs.instruments" not in canonical_manifest_key(
        plain.kind, plain.result_id, plain.dataset_version, plain.dataset_content_hash,
        plain.configuration_id, plain.seed, plain.seed_role, plain.engine,
        plain.fingerprint.fingerprint, plain.build, plain.schedule_digest, plain.dataset_parts,
    )  # fmt: skip


def test_the_registry_the_run_held_is_named_and_verified(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    named = manifest(example, data, result, instruments=True)
    plain = manifest(example, data, result)
    assert named.instruments_digest is not None and len(named.instruments_digest) == 64
    assert named.manifest_id != plain.manifest_id
    assert verify_manifest(named)
    # A digest the manifest was not derived from does not verify.
    assert not verify_manifest(replace(named, instruments_digest="0" * 64))


def test_runs_given_different_terms_have_different_manifests(
    example: ModuleType, data: Any, result: BacktestResult
) -> None:
    from alphalab.instrument.record import InstrumentRecord
    from alphalab.instrument.registry import register_instruments

    registry = result.run.config.pipeline.instruments
    assert registry is not None
    extra = InstrumentRecord(
        "SPARECO", next(iter(registry.instruments.values())).asset_type, "XSPR", "USD"
    )
    other = replace(
        result,
        run=replace(
            result.run,
            config=replace(
                result.run.config,
                pipeline=replace(
                    result.run.config.pipeline,
                    instruments=register_instruments(registry, (extra,)),
                ),
            ),
        ),
    )
    a = manifest(example, data, result, instruments=True)
    b = manifest(example, data, other, instruments=True)
    assert a.instruments_digest != b.instruments_digest
