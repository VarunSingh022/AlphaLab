"""One account currency, named twice, and what happened when the two disagreed.

``ExecutionPipelineConfig.currency`` funds the cash ledger and denominates every
fill. ``Account.base_currency`` is what the risk resync and ``NAVCalculator``
read. They name one thing, and until v2.8 nothing checked that they agreed.

A disagreement did not fail. Measured at v2.7.0, a pipeline configured
``currency="EUR"`` against ``base_currency="USD"`` and driven with six quotes
produced::

    fills             = 0
    risk rejections   = 6
    risk.cash         = 0.00
    risk.current_nav  = 0.00
    reported equity   = 10,000,000.00

Cash lands under ``config.currency``, so ``balance(account.base_currency)`` is
zero. ``check_buying_power`` then refuses every order; ``check_leverage``
returns early because NAV is not positive; ``check_margin`` passes vacuously
against zero available margin. Two risk limits stop checking and nothing says
so.

The refusal is the first statement of ``initialize`` -- before the portfolio
exists, so nothing is funded against a configuration that is about to be
refused. That ordering is asserted here directly rather than inferred from the
exception.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.engine import BacktestEngine
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineConfig
from alphalab.runtime.session import ExecutionMode, SessionConfig, TradingSession
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    running_strategy_state,
)


def _parts() -> tuple[str, str, ExecutionPipelineConfig]:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    return strategy_id, asset_id, pipeline_config(strategy_id)


def _running(strategy_id: str, asset_id: str) -> StrategyRuntimeState:
    plan = {2.0: Decimal("1")}
    return running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan))


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #


def test_a_config_naming_two_account_currencies_is_refused() -> None:
    strategy_id, asset_id, config = _parts()

    with pytest.raises(RuntimeValidationError) as caught:
        ExecutionPipeline.initialize(
            replace(config, currency="EUR"), _running(strategy_id, asset_id), 1.0
        )

    message = str(caught.value)
    assert "ExecutionPipelineConfig.currency" in message
    assert "Account.base_currency" in message
    assert "'EUR'" in message
    assert "'USD'" in message


def test_the_refusal_says_what_would_have_gone_wrong() -> None:
    """The failure it prevents is invisible, so the message has to describe it."""

    strategy_id, asset_id, config = _parts()

    with pytest.raises(RuntimeValidationError) as caught:
        ExecutionPipeline.initialize(
            replace(config, currency="EUR"), _running(strategy_id, asset_id), 1.0
        )

    message = str(caught.value).lower()
    assert "buying power" in message
    assert "nav" in message
    assert "no fills" in message


def test_the_mismatch_is_refused_whichever_side_differs() -> None:
    strategy_id, asset_id, config = _parts()
    flipped = replace(config, account=Account("acct-eur", "EUR", "EUR Account", 1.0))

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.initialize(flipped, _running(strategy_id, asset_id), 1.0)


def test_case_differences_are_refused_rather_than_normalized() -> None:
    """``CashLedger`` keys balances by the exact string it is given.

    Normalizing here would let the check pass while the ledger still kept
    ``"usd"`` and ``"USD"`` as two separate balances.
    """

    strategy_id, asset_id, config = _parts()

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.initialize(
            replace(config, currency="usd"), _running(strategy_id, asset_id), 1.0
        )


# --------------------------------------------------------------------------- #
# Nothing is funded before the refusal
# --------------------------------------------------------------------------- #


def test_no_portfolio_is_funded_before_the_configuration_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering guarantee, asserted rather than inferred.

    ``initialize`` returns nothing on the raise, so "it raised" does not by
    itself prove the deposit never happened. This watches the deposit.
    """

    strategy_id, asset_id, config = _parts()
    calls: list[tuple[Decimal, str]] = []
    original = PortfolioEngine.apply_deposit

    def _watched(state, amount, currency, timestamp):  # type: ignore[no-untyped-def]
        calls.append((amount, currency))
        return original(state, amount, currency, timestamp)

    monkeypatch.setattr(PortfolioEngine, "apply_deposit", staticmethod(_watched))

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.initialize(
            replace(config, currency="EUR"), _running(strategy_id, asset_id), 1.0
        )

    assert calls == [], "the portfolio was funded before the configuration was refused"


def test_a_matching_configuration_still_funds_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same watch, proving the guard did not remove the funding it guards."""

    strategy_id, asset_id, config = _parts()
    calls: list[tuple[Decimal, str]] = []
    original = PortfolioEngine.apply_deposit

    def _watched(state, amount, currency, timestamp):  # type: ignore[no-untyped-def]
        calls.append((amount, currency))
        return original(state, amount, currency, timestamp)

    monkeypatch.setattr(PortfolioEngine, "apply_deposit", staticmethod(_watched))

    state = ExecutionPipeline.initialize(config, _running(strategy_id, asset_id), 1.0)

    assert calls == [(config.starting_cash, "USD")]
    assert state.portfolio.cash.balance("USD") == config.starting_cash


# --------------------------------------------------------------------------- #
# The matching configuration is unchanged
# --------------------------------------------------------------------------- #


def test_a_matching_configuration_initializes_exactly_as_it_did() -> None:
    strategy_id, asset_id, config = _parts()

    state = ExecutionPipeline.initialize(config, _running(strategy_id, asset_id), 1.0)

    assert state.portfolio.cash.balance("USD") == config.starting_cash
    assert state.risk.cash == config.starting_cash
    assert state.risk.current_nav == config.starting_cash
    assert state.risk.buying_power == config.starting_cash
    assert state.portfolio_snapshots[-1].total_equity == config.starting_cash


def test_the_mismatch_v2_7_accepted_would_have_traded_nothing() -> None:
    """What the refused configuration did at v2.7.0, held as the reason for it.

    Driven through the matching configuration the same six quotes fill six
    times. The mismatched one is now refused before it can produce the silent
    zero-fill run recorded in this module's docstring.
    """

    strategy_id, asset_id, config = _parts()
    plan = {2.0 + index: Decimal("1") for index in range(6)}
    strategy_state = running_strategy_state(
        strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)
    )

    state = ExecutionPipeline.initialize(config, strategy_state, 1.0)
    for index in range(6):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, 2.0 + index, Decimal("100")), context_factory
        ).state

    assert len(state.fills) == 6, "the matching configuration trades"

    with pytest.raises(RuntimeValidationError):
        ExecutionPipeline.initialize(replace(config, currency="EUR"), strategy_state, 1.0)


# --------------------------------------------------------------------------- #
# Every environment goes through the same boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", list(ExecutionMode))
def test_every_execution_mode_is_refused_the_same_way(mode: ExecutionMode) -> None:
    """The check is in ``initialize``, below ``SessionConfig``, so all four share it."""

    strategy_id, asset_id, config = _parts()
    session = SessionConfig(pipeline=replace(config, currency="EUR"), mode=mode)

    with pytest.raises(RuntimeValidationError):
        TradingSession.initialize(session, _running(strategy_id, asset_id))


def test_a_backtest_is_refused_at_the_same_boundary() -> None:
    strategy_id, asset_id, config = _parts()
    backtest = BacktestConfig(pipeline=replace(config, currency="EUR"))

    with pytest.raises(RuntimeValidationError):
        BacktestEngine.initialize(backtest, _running(strategy_id, asset_id))
