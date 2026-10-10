"""A live object's description states every parameter, containers included (ledger PER-009).

v4.0 made a restore compare each live object's description with the one its
snapshot recorded, and made the description part of a run's
``configuration_id`` (PER-008). The final v4.0 review found the description
blind to containers: :func:`~alphalab.runtime.assumptions.describe` wrote a
mapping or a set by its type name alone, so ``VolatilityTargetSizing`` with
other per-asset volatilities, or a ``ProportionalTax`` on purchases rather than
on sales, described exactly as the original -- a restore accepted it and two
such runs shared one configuration. A container is now written by its contents,
in an order that depends on neither insertion nor the interpreter's hash seed.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pytest

from alphalab.allocation.sizing import FixedQuantitySizing, SizingModel, VolatilityTargetSizing
from alphalab.core.enums import Side
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.costs import (
    ExecutionCostModel,
    NoFee,
    NoImpact,
    NoSlippage,
    NoSpread,
    ProportionalTax,
)
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.persistence import StateDecodeError, deserialize, serialize
from alphalab.runtime.assumptions import describe
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration.harness import ScriptedStrategy, scripted_run

ROOT = Path(__file__).resolve().parents[2]
ASSET = "00000000-0000-0000-0000-00000000c0de"


def _vol_target(**asset_vols: str) -> VolatilityTargetSizing:
    return VolatilityTargetSizing(
        Decimal("0.10"), {asset: Decimal(vol) for asset, vol in asset_vols.items()}
    )


def _taxed(*sides: Side) -> ExecutionSimulator:
    return ExecutionSimulator(
        cost_model=ExecutionCostModel(
            spread_model=NoSpread(),
            slippage_model=NoSlippage(),
            impact_model=NoImpact(),
            commission_model=PerShareCommission(Decimal("0.01")),
            fee_model=NoFee(),
            tax_model=ProportionalTax(Decimal("0.005"), frozenset(sides)),
        )
    )


def test_a_mapping_parameter_is_described_by_its_contents() -> None:
    assert describe(_vol_target(A="0.20", B="0.30")) != describe(_vol_target(A="0.90", B="0.30"))
    assert "A" in describe(_vol_target(A="0.20")) and "0.20" in describe(_vol_target(A="0.20"))


def test_a_mapping_is_described_whatever_order_it_was_built_in() -> None:
    forwards = {"A": Decimal("0.2"), "B": Decimal("0.3")}
    backwards = {"B": Decimal("0.3"), "A": Decimal("0.2")}

    assert describe(forwards) == describe(backwards) == describe(MappingProxyType(backwards))


def test_a_set_parameter_is_described_by_its_contents() -> None:
    assert describe(_taxed(Side.BUY)) != describe(_taxed(Side.SELL))
    assert describe(_taxed(Side.BUY, Side.SELL)) == describe(_taxed(Side.SELL, Side.BUY))


def test_a_set_of_strings_is_described_alike_under_every_hash_seed() -> None:
    """A ``str``'s hash differs per process, and with it a set's iteration order."""

    program = (
        "from alphalab.runtime.assumptions import describe;"
        "print(describe(frozenset({'XNYS', 'XLON', 'XTKS', 'XHKG', 'XETR', 'XPAR'})))"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", program],
            cwd=ROOT,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("0", "1", "4242")
    }
    assert len(seen) == 1, seen


def _captured_under(sizing: SizingModel, simulator: ExecutionSimulator) -> str:
    from alphalab.backtesting.engine import BacktestEngine
    from alphalab.common.ids import id_scope
    from tests.integration.harness import context_factory

    config, dataset, strategies = scripted_run(
        {2.0: Decimal("10")},
        [Decimal("100"), Decimal("101"), Decimal("102")],
        "PER-009",
        ASSET,
        simulator=simulator,
    )
    config = replace(config, pipeline=replace(config.pipeline, sizing_model=sizing))
    with id_scope(9):
        run = BacktestEngine.run(config, dataset, strategies, context_factory).run
    return serialize(capture(run))


def _objects(sizing: SizingModel, simulator: ExecutionSimulator) -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=sizing,
            simulator=simulator,
            strategies={"PER-009": ScriptedStrategy("PER-009", ASSET, {})},
        ),
        fill_policy=ImmediateFill(),
    )


def test_a_restore_refuses_a_sizing_model_with_other_asset_volatilities() -> None:
    payload = _captured_under(_vol_target(**{ASSET: "0.20"}), ExecutionSimulator())

    restore(
        from_primitives(deserialize(payload)),
        _objects(_vol_target(**{ASSET: "0.20"}), ExecutionSimulator()),
    )
    with pytest.raises(StateDecodeError, match="sizing model supplied is configured as"):
        restore(
            from_primitives(deserialize(payload)),
            _objects(_vol_target(**{ASSET: "0.90"}), ExecutionSimulator()),
        )


def test_a_restore_refuses_a_tax_on_the_other_side() -> None:
    payload = _captured_under(FixedQuantitySizing(), _taxed(Side.BUY))

    restore(
        from_primitives(deserialize(payload)), _objects(FixedQuantitySizing(), _taxed(Side.BUY))
    )
    with pytest.raises(StateDecodeError, match="simulator supplied is configured as"):
        restore(
            from_primitives(deserialize(payload)),
            _objects(FixedQuantitySizing(), _taxed(Side.SELL)),
        )
