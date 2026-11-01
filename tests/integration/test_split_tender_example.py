"""Example 78 does what it prints: a tender split between holders (phase 4).

Every figure is recomputed from the example's declared terms -- the holdings,
the elections, the proration, the issuer's delay and correction -- so the test
fails if the engine and the arithmetic part company, not if the prose changes
(ledgers FUT-030 and FUT-034).
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.portfolio.events import PaymentAmended
from alphalab.portfolio.settlement import EntitlementStatus, entitlements

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "78_a_tender_split_between_holders.py"
D = Decimal


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_78", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_each_holder_takes_its_own_election_and_the_proration(example: ModuleType) -> None:
    result = example.run()
    # CASHY's 100 elect cash, 60% accepted: 60 at 120. CASHY's other 40 and
    # STOCKY's 60 take the default, half a share each: 20 and 30 BIDDERCO.
    shares = dict(AllocationEngine.shares_of(result.state.allocation, example.BIDDER.asset_id))
    assert shares == {"CASHY": D("20"), "STOCKY": D("30")}
    assert result.state.portfolio.positions.get(example.TARGET.asset_id) is None


def test_the_payment_is_delayed_then_corrected_and_never_early(example: ModuleType) -> None:
    result = example.run()
    ledger = result.state.portfolio.ledger.transactions
    (before,) = [e for e in entitlements(ledger, example.at(6.5)) if e.currency == "USD"]
    (after,) = [e for e in entitlements(ledger, example.at(8.5)) if e.currency == "USD"]
    # 60 x 120 owed, delayed past hour 6 to hour 8; corrected at hour 7 to 118.
    assert (before.owed, before.status, before.settles_at) == (
        D("7200.00"),
        EntitlementStatus.PENDING,
        example.at(8),
    )
    assert (after.declared, after.owed, after.status) == (
        D("7200.00"),
        D("7080.00"),
        EntitlementStatus.PAID,
    )
    changes = [e.change for e in result.state.portfolio.events if isinstance(e, PaymentAmended)]
    assert changes == ["DELAYED", "CORRECTED"]


def test_the_example_runs_as_printed(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    example.main()
    printed = capsys.readouterr().out
    assert "60 for cash" in printed and "50 BIDDERCO" in printed
    assert "owed 7,080.00 USD, paid" in printed
