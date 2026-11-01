"""Different instrument terms cannot pass for the same experiment (phase 5 registry audit).

What a run's registry holds -- its records and their economics, settlement,
margin, borrow and tick terms, classifications and dated aliases, the account's
margin model and its surfaces, the venues' controls -- changes what the run
does. Its record names the registry by type only (ADR-0023); so:

* terms that change the result change ``result_id``, under any manifest;
* terms that change nothing the record holds leave ``result_id`` and a manifest
  asked for nothing new as they were -- the limitation this audit found, kept
  so no published identity moves -- and an assessment says so as a finding;
* ``manifest_for_run(instruments=True)`` names every one of them, so a rerun
  under other terms is reported as one of different inputs, never as a
  reproduction.

Example 72's multi-asset run supplies the dataset, strategy and engine; each
rerun is a genuine run under the registry named.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.api import combined_market_dataset
from alphalab.backtesting import BacktestEngine, BacktestResult
from alphalab.instrument.controls import TradingControls, TradingHalt
from alphalab.instrument.margin import MarginBasis, MarginTerms, PerPositionMargin
from alphalab.instrument.record import DatedAlias, InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_dimension,
    classify_instrument,
    declare_margin_model,
    declare_trading_controls,
    register_dated_alias,
    register_instruments,
)
from alphalab.instrument.snapshot import capture, restore
from alphalab.lifecycle import (
    RerunOutcome,
    assess_reproducibility,
    digest_run,
    manifest_for_run,
    running_build,
    running_engine,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "72_compose_a_multi_asset_strategy.py"
REGISTRY_FINDING = "names by type alone"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_72_identity", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(example: ModuleType) -> Any:
    return example.inputs()


@pytest.fixture(scope="module")
def original(example: ModuleType, data: Any) -> BacktestResult:
    result: BacktestResult = example.run(data)
    return result


def run_under(example: ModuleType, data: Any, registry: InstrumentRegistry) -> BacktestResult:
    config = example.run_config()
    config = replace(config, pipeline=replace(config.pipeline, instruments=registry))
    return BacktestEngine.run(
        config,
        combined_market_dataset(data.prices),
        example.running(data),
        example.CONTEXTS,
        schedule=data.schedule(),
    )


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


def held_under(result: BacktestResult, registry: InstrumentRegistry) -> BacktestResult:
    """``result`` as if its run had held ``registry``: for the digest alone."""

    pipeline = replace(result.run.config.pipeline, instruments=registry)
    return replace(
        result, run=replace(result.run, config=replace(result.run.config, pipeline=pipeline))
    )


def spare(example: ModuleType) -> InstrumentRecord:
    return InstrumentRecord("SPARECO", example.ACME.asset_type, "XSPR", "USD")


# --------------------------------------------------------------------------- #
# Every term the registry holds is named
# --------------------------------------------------------------------------- #


def variants(example: ModuleType) -> dict[str, InstrumentRegistry]:
    base: InstrumentRegistry = example.REGISTRY
    acme, idx = example.ACME, example.IDX
    economics = idx.economics
    assert economics is not None
    halt = TradingControls(halts=(TradingHalt(0.0, None, asset_id=acme.asset_id),))
    margined = replace(acme, margin=MarginTerms(MarginBasis.VALUE_RATE, Decimal("0.5"),
                                                 Decimal("0.25")))  # fmt: skip
    return {
        "an extra record": register_instruments(base, (spare(example),)),
        "a contract multiplier": register_instruments(
            InstrumentRegistry(),
            (acme, replace(idx, economics=replace(economics, multiplier=Decimal("25")))),
        ),
        "a record's margin terms": register_instruments(InstrumentRegistry(), (margined, idx)),
        "a classification": classify_instrument(base, acme.asset_id, "Technology"),
        "a dimension": classify_dimension(base, acme.asset_id, "issuer", "Acme Corp"),
        "a dated alias": register_dated_alias(
            base, acme.asset_id, DatedAlias("other-vendor", "ACM", 0.0, None)
        ),
        "a margin model": declare_margin_model(base, PerPositionMargin()),
        "venue controls": declare_trading_controls(base, halt),
    }


def test_every_term_a_registry_holds_moves_its_digest(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    base = manifest(example, data, original, instruments=True).instruments_digest
    named = {
        what: manifest(example, data, held_under(original, registry), instruments=True)
        for what, registry in variants(example).items()
    }
    digests = {what: m.instruments_digest for what, m in named.items()}
    assert base not in digests.values(), digests
    assert len(set(digests.values())) == len(digests), digests


def test_the_digest_is_of_the_terms_not_the_object(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    rebuilt = restore(capture(example.REGISTRY))
    assert rebuilt is not example.REGISTRY
    named = manifest(example, data, held_under(original, rebuilt), instruments=True)
    assert named == manifest(example, data, original, instruments=True)


# --------------------------------------------------------------------------- #
# Reruns under other terms
# --------------------------------------------------------------------------- #


def test_terms_that_change_the_run_change_its_result_under_any_manifest(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    halted = run_under(example, data, variants(example)["venue controls"])
    assert digest_run(halted).result_id != digest_run(original).result_id
    plain = assess_reproducibility(
        manifest(example, data, original), manifest(example, data, halted)
    )
    # Named by type alone, the cause is not seen -- but the result is not the same.
    assert plain.rerun is RerunOutcome.DIVERGED
    named = assess_reproducibility(
        manifest(example, data, original, instruments=True),
        manifest(example, data, halted, instruments=True),
    )
    assert named.rerun is RerunOutcome.INPUTS_DIFFER
    assert any(line.startswith("instruments:") for line in named.rerun_detail)


def test_terms_the_record_does_not_show_are_told_apart_only_when_named(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    other = run_under(example, data, variants(example)["an extra record"])
    # The limitation, pinned: the record agrees, and so does a manifest asked
    # for nothing new -- its identity is the one it was.
    assert digest_run(other).result_id == digest_run(original).result_id
    plain = assess_reproducibility(
        manifest(example, data, original), manifest(example, data, other)
    )
    assert plain.rerun is RerunOutcome.REPRODUCED
    assert any(REGISTRY_FINDING in finding for finding in plain.findings)
    named = assess_reproducibility(
        manifest(example, data, original, instruments=True),
        manifest(example, data, other, instruments=True),
    )
    assert named.rerun is RerunOutcome.INPUTS_DIFFER
    assert "nevertheless matched" in named.rerun_detail[-1]


def test_a_rerun_under_the_same_terms_reproduces_with_them_named(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    again = run_under(example, data, restore(capture(example.REGISTRY)))
    named = assess_reproducibility(
        manifest(example, data, original, instruments=True),
        manifest(example, data, again, instruments=True),
    )
    assert named.rerun is RerunOutcome.REPRODUCED
    assert not any(REGISTRY_FINDING in finding for finding in named.findings)


def test_a_rerun_from_a_manifest_naming_its_registry_reproduces_under_the_same_terms(
    example: ModuleType, data: Any, original: BacktestResult
) -> None:
    from alphalab.lifecycle import rerun_from_manifest

    named = manifest(example, data, original, instruments=True)
    same = rerun_from_manifest(
        named, lambda: run_under(example, data, restore(capture(example.REGISTRY))),
        dataset=data.prices, fingerprint=example.fingerprint(data), engine=running_engine(),
        build=running_build(), schedule=data.schedule(),
    )  # fmt: skip
    # REP-008: the rerun's manifest names its registry and its schedule as the
    # original's does, so the same terms reproduce, and other ones are other inputs.
    assert same.outcome is RerunOutcome.REPRODUCED, same.detail
    other = rerun_from_manifest(
        named, lambda: run_under(example, data, variants(example)["an extra record"]),
        dataset=data.prices, fingerprint=example.fingerprint(data), engine=running_engine(),
        build=running_build(), schedule=data.schedule(),
    )  # fmt: skip
    assert other.outcome is RerunOutcome.INPUTS_DIFFER


def test_a_manifest_names_the_schedule_that_delivered_what_the_run_kept(
    example: ModuleType, data: Any
) -> None:
    from alphalab.backtesting import RunSchedule
    from alphalab.instrument import AuctionIndication, DeliveredAuction
    from alphalab.lifecycle import LifecycleInputError

    acme = example.ACME.asset_id
    first_bar = min(r.timestamp for r in combined_market_dataset(data.prices).records)
    sent = DeliveredAuction(
        AuctionIndication(acme, first_bar, imbalance=Decimal("5")), first_bar, "venue", "v1"
    )
    given = RunSchedule.of(observations=data.deliveries(), auctions=(sent,))
    config = example.run_config()
    result = BacktestEngine.run(
        config, combined_market_dataset(data.prices), example.running(data), example.CONTEXTS,
        schedule=given,
    )  # fmt: skip
    assert result.state.market.auctions[acme] == (sent,)

    def named(schedule: Any) -> Any:
        return manifest_for_run(
            result, data.prices, example.fingerprint(data), running_engine(),
            build=running_build(), schedule=schedule,
        )  # fmt: skip

    # REP-009: a manifest naming no schedule, or one without the delivery the
    # run kept, would claim inputs the run did not read.
    with pytest.raises(LifecycleInputError, match="no schedule was given"):
        named(None)
    with pytest.raises(LifecycleInputError, match="does not hold"):
        named(data.schedule())
    assert named(given).schedule_digest == given.digest
