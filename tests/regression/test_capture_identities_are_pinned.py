"""A captured run's identities are pinned, as every host computes them (ADR-0055, ADR-0058).

``series_id`` and ``trace_id`` are SHA-256 over canonical text: the returns are
class 2 (exact ``Decimal`` arithmetic, rounded once), and the trace hashes the
run's own canonical serialization of each entry. Each value below was computed
identically under CPython 3.12, 3.13 and 3.14 before it was pinned, and hosted CI
holds it on Linux and macOS. A change here is a change to the capture's format,
or to the run's -- never a host's rounding -- and needs its own record.
"""

from __future__ import annotations

from decimal import Decimal

from alphalab.backtesting.capture import (
    EVERY_INSTANT,
    CaptureRecord,
    CaptureSpec,
    CashFlows,
    Costs,
    FixedInterval,
    Insolvency,
    ReturnExtractor,
    run_captured,
)
from tests.integration.harness import context_factory, scripted_run

STRATEGY_ID = "5d3c7a10-6f0e-4c8b-9a51-2b7e8f9d4c31"
ASSET_ID = "9b2e4f60-1c7d-4e3a-8f25-6d0a1b3c5e77"
MIDS = [Decimal(100) + Decimal(i % 9) - Decimal(i) / 50 for i in range(120)]
PLAN = {2.0 + i: (Decimal(5) if i % 3 == 0 else Decimal(-2)) for i in range(0, 120, 2)}

TRACE_ID = "759ace4047818c3ece6e8563a965791ae80da3513c7183e9af65030fe6b8789e"
CONFIGURATION_ID = "9dde38f55d7bd45bfdc330b0dc7a04f2ecb4ff2af65b3466e4b0128f5e5460a0"


def _capture(extractor: ReturnExtractor) -> CaptureRecord:
    run = scripted_run(PLAN, MIDS, STRATEGY_ID, ASSET_ID)
    return run_captured(*run, context_factory, capture=CaptureSpec(extractor, True)).record


def test_every_instant_net_included_is_pinned() -> None:
    record = _capture(
        ReturnExtractor(EVERY_INSTANT, Costs.NET, CashFlows.INCLUDED, "USD", Insolvency.REFUSE)
    )

    assert record.series_id == "43a3517c920e1fc9ae5f1441b3e44f3eefaa4651356df30f2a853f445e29f3fa"
    assert record.trace_id == TRACE_ID
    assert record.configuration_id == CONFIGURATION_ID


def test_a_fixed_interval_gross_excluded_series_is_pinned_over_the_same_trace() -> None:
    record = _capture(
        ReturnExtractor(
            FixedInterval(7.5, 0.25), Costs.GROSS, CashFlows.EXCLUDED, "USD", Insolvency.TRUNCATE
        )
    )

    assert record.series_id == "ea161ffc3207fcde5b2ee41af18dc536b0f8f4a5627f2186ddf62f92e5b03b59"
    # The trace is the run's history, not the series: one run, one trace.
    assert record.trace_id == TRACE_ID
    assert record.configuration_id == CONFIGURATION_ID
