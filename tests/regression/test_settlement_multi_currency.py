"""A run may settle in more than one currency, and every figure says which.

ADR-0033 decision 13 closed v2.16's FX work at the **valuation** boundary and
named exactly what stood between there and settlement:

    ``PortfolioState.realized_pnl`` and ``commission_paid`` are single cumulative
    scalars that name no currency, and a run trading in two would sum them
    across both. Allocation sizes against a capital budget in one currency and
    risk limits are stated in one.

Four blockers, and this file is the evidence each is gone (ADR-0035):

=========================== ==================================================
Blocker                     What v2.17 does
=========================== ==================================================
Currency-less realized P&L  :class:`~alphalab.portfolio.amounts.CurrencyAmounts`
Currency-less commissions   the same
Budget in one currency      ``CapitalBudget.currency``, refused when ambiguous
Risk limits in one currency ``cash_in`` / NAV / exposure convert, or refuse
=========================== ==================================================

The property the whole design turns on
---------------------------------------

**Settlement truth and reporting truth are different numbers and stay apart.**
A EUR fill accrues EUR P&L on the state, for ever, in the currency it happened
in. A valuation names one currency and converts into it, recording every rate it
used. Neither is derived from the other by assumption, and the join is always a
rate a caller supplied.

That is why ``realized_pnl`` is not simply translated into the reporting
currency at fill time, which would have been a smaller change: doing so destroys
the only record of what was actually earned, and bakes one instant's rate into a
cumulative figure that is then wrong at every later instant.

What has no implicit form
--------------------------

Nothing converts itself. A pipeline settles the currencies it was configured to
settle and refuses the rest; cash in a currency the book does not hold is an
``InsufficientFundsError`` and not an automatic conversion; and
:meth:`~alphalab.runtime.execution_pipeline.ExecutionPipeline.convert_cash` is a
deliberate act that records its rate. A fill that financed itself at a rate
nobody asked for is the "invented figure that looks authoritative" ADR-0020
refuses, and there is no path to one.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.common.ids import id_scope
from alphalab.core.enums import AssetType
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.instrument.record import InstrumentRecord
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.exceptions import (
    InsufficientFundsError,
    InvalidTransactionError,
    MixedCurrencyValuationError,
)
from alphalab.portfolio.fx import FxRate, FxRates, MissingRateError, StaleRateError
from alphalab.portfolio.snapshot import (
    PORTFOLIO_SNAPSHOT_SCHEMA,
    capture,
    from_primitives,
    restore,
)
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

_SEED = 20260917

_APPLE = InstrumentRecord("AAPL", AssetType.EQUITY, "XNAS", "USD", sector="Technology")
_SAP = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", sector="Technology")
_TOYOTA = InstrumentRecord("7203", AssetType.EQUITY, "XTKS", "JPY", sector="Consumer")

#: Rates both ways, so a test can fund EUR out of USD and value the result in USD.
_RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.10"), 2.0, "ECB"),
        FxRate("USD", "EUR", Decimal("0.909091"), 2.0, "ECB"),
    ]
)


def _config(
    strategy_id: str,
    *,
    reporting: str = "USD",
    also_settles: frozenset[str] = frozenset({"EUR"}),
    budget_currency: str | None = "USD",
    instruments: tuple[InstrumentRecord, ...] = (_APPLE, _SAP),
) -> ExecutionPipelineConfig:
    base = pipeline_config(strategy_id)
    budget = base.budget if budget_currency is None else base.budget.in_currency(budget_currency)
    return replace(
        base,
        account=Account("acct-mc", reporting, "Multi-currency Account", 1.0),
        currency=reporting,
        also_settles=also_settles,
        budget=budget,
        instruments=registry_of(*instruments),
    )


def _funded(
    strategy_id: str,
    strategy: ScriptedStrategy,
    *,
    config: ExecutionPipelineConfig | None = None,
    eur: Decimal = Decimal("11000"),
) -> ExecutionPipelineState:
    """A pipeline funded in USD, with part of it converted into EUR."""

    state = ExecutionPipeline.initialize(
        config if config is not None else _config(strategy_id),
        running_strategy_state(strategy_id, strategy),
        1.0,
    )
    # At 2.0 rather than 1.5: that is when ``_RATES`` became true, and since
    # v3.4 ``FxRates.convert`` refuses a rate dated after the conversion instant
    # (``FutureDatedRateError``). The half-second of look-ahead this fixture
    # carried was invisible while nothing checked for it.
    state, _ = ExecutionPipeline.convert_cash(state, eur, "USD", "EUR", _RATES, 2.0)
    return state


def _run_eur_trade() -> tuple[ExecutionPipelineResult, ExecutionPipelineResult]:
    """Buy 10 SAP at EUR 100, sell 4 at EUR 110. Realizes EUR 40."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(
        strategy_id, _SAP.asset_id, {2.0: Decimal("10"), 4.0: Decimal("-4")}
    )
    with id_scope(_SEED):
        state = _funded(strategy_id, strategy)
        first = ExecutionPipeline.process_quote(
            state, quote(_SAP.asset_id, 2.0, Decimal("100")), context_factory, rates=_RATES
        )
        second = ExecutionPipeline.process_quote(
            first.state, quote(_SAP.asset_id, 4.0, Decimal("110")), context_factory, rates=_RATES
        )
    return first, second


# --------------------------------------------------------------------------- #
# 1. Settlement truth: a fill accrues in the currency it settled in
# --------------------------------------------------------------------------- #


def test_a_foreign_fill_settles_in_the_instruments_own_currency() -> None:
    """Not the pipeline's. Until v2.17 the instruction was stamped with the
    pipeline's currency and a foreign instrument could not reach it at all.
    """

    first, second = _run_eur_trade()
    reports = [*first.execution_reports, *second.execution_reports]

    assert [report.currency for report in reports] == ["EUR", "EUR"]
    assert not first.settlement_refusals and not second.settlement_refusals


def test_realized_pnl_and_commission_accrue_against_the_settlement_currency() -> None:
    """The first two blockers, closed. EUR results live in the EUR bucket."""

    _, second = _run_eur_trade()
    portfolio = second.state.portfolio

    # (110 - 100) * 4 = 40, earned in EUR and recorded as EUR.
    assert portfolio.realized_pnl.of("EUR") == Decimal("40.00")
    assert portfolio.realized_pnl.of("USD") == Decimal("0.00")
    assert portfolio.realized_pnl.currencies == ("EUR",)
    assert "EUR" in portfolio.commission_paid


def test_the_book_holds_both_currencies_and_says_so() -> None:
    _, second = _run_eur_trade()
    portfolio = second.state.portfolio

    assert portfolio.settlement_currencies == ("EUR", "USD")
    assert portfolio.cash.balance("USD") > Decimal("0")
    assert portfolio.cash.balance("EUR") > Decimal("0")
    assert {position.currency for position in portfolio.positions.values()} == {"EUR"}


def test_nothing_is_summed_across_two_currencies() -> None:
    """The defect the whole change exists to remove, asserted directly."""

    amounts = CurrencyAmounts().add(Decimal("40"), "EUR").add(Decimal("5000"), "JPY")

    with pytest.raises(MixedCurrencyValuationError, match=r"\['EUR', 'JPY'\]"):
        amounts.total_in("USD")


# --------------------------------------------------------------------------- #
# 2. Reporting truth: one currency, and the rates that produced it
# --------------------------------------------------------------------------- #


def test_a_valuation_converts_settlement_results_and_records_every_rate() -> None:
    _, second = _run_eur_trade()
    valuation = PortfolioValuation.snapshot(second.state.portfolio, 5.0, "USD", _RATES)

    assert valuation.currency == "USD"
    assert valuation.realized_pnl == Decimal("44.00"), "EUR 40 at 1.10"
    assert valuation.converted
    assert valuation.rate_sources == ("ECB",)
    assert any(
        conversion.rate.base == "EUR" and conversion.rate.quote == "USD"
        for conversion in valuation.conversions
    )


def test_the_accounting_identity_holds_across_two_currencies() -> None:
    """equity == deposits - withdrawals + realized + unrealized - commission,
    with every term expressed in the reporting currency at a stated rate.
    """

    _, second = _run_eur_trade()
    valuation = PortfolioValuation.snapshot(second.state.portfolio, 5.0, "USD", _RATES)

    deposits = Decimal("1000000.00")  # starting_cash; the EUR came out of it
    identity = (
        deposits + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )

    assert valuation.equity == identity


def test_the_same_book_values_in_either_currency() -> None:
    """A reporting currency is a choice, not a property of the book."""

    _, second = _run_eur_trade()
    portfolio = second.state.portfolio

    in_usd = PortfolioValuation.snapshot(portfolio, 5.0, "USD", _RATES)
    in_eur = PortfolioValuation.snapshot(portfolio, 5.0, "EUR", _RATES)

    assert in_usd.currency == "USD" and in_eur.currency == "EUR"
    assert in_eur.realized_pnl == Decimal("40.00"), "already EUR: no conversion"
    assert in_usd.realized_pnl == Decimal("44.00")
    # The settlement record is untouched by either reading of it.
    assert portfolio.realized_pnl.of("EUR") == Decimal("40.00")


def test_a_valuation_without_a_rate_is_refused_rather_than_estimated() -> None:
    _, second = _run_eur_trade()

    with pytest.raises(MixedCurrencyValuationError, match="No FX rates were supplied"):
        PortfolioValuation.snapshot(second.state.portfolio, 5.0, "USD")


def test_a_stale_rate_is_refused_at_settlement_valuation_too() -> None:
    _, second = _run_eur_trade()
    stale = FxRates.of([FxRate("EUR", "USD", Decimal("1.10"), 0.0, "ECB")], max_age_seconds=1.0)

    with pytest.raises(StaleRateError):
        PortfolioValuation.snapshot(second.state.portfolio, 5_000.0, "USD", stale)


# --------------------------------------------------------------------------- #
# 3. The settlement boundary: what may be settled, and what may not
# --------------------------------------------------------------------------- #


def test_an_instrument_in_an_unsettled_currency_is_still_refused() -> None:
    """``also_settles`` widens the boundary; it does not remove it."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _TOYOTA.asset_id, {2.0: Decimal("10")})
    config = _config(strategy_id, instruments=(_APPLE, _SAP, _TOYOTA))

    with id_scope(_SEED):
        state = _funded(strategy_id, strategy, config=config)
        result = ExecutionPipeline.process_quote(
            state, quote(_TOYOTA.asset_id, 2.0, Decimal("2000")), context_factory, rates=_RATES
        )

    assert len(result.settlement_refusals) == 1
    refusal = result.settlement_refusals[0]
    assert refusal.instrument_currency == "JPY"
    assert "also_settles" in refusal.detail
    assert not result.execution_reports


def test_a_venue_report_in_an_unsettled_currency_raises() -> None:
    """Seam 2: a fill that already happened cannot be declined, only refused.

    Driven under ``EXTERNAL`` routing so the order is still working when the
    venue reports -- which is the only shape in which a report reaches this
    method, and the shape a live session produces.
    """

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {2.0: Decimal("10")})
    config = replace(_config(strategy_id), routing=ExecutionRouting.EXTERNAL)

    with id_scope(_SEED):
        state = _funded(strategy_id, strategy, config=config)
        result = ExecutionPipeline.process_quote(
            state, quote(_SAP.asset_id, 2.0, Decimal("100")), context_factory, rates=_RATES
        )

    order = result.oms_orders[0]
    assert not result.execution_reports, "EXTERNAL invents no fill"

    foreign = ExecutionReport(
        execution_id=str(uuid4()),
        order_id=str(order.order_id.value),
        asset_id=_SAP.asset_id,
        strategy_id=strategy_id,
        timestamp=9.0,
        fill_price=Decimal("100"),
        fill_quantity=Decimal("10"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="TAKER",
        venue="SIM",
        currency="JPY",
        status=FillStatus.FULL_FILL,
    )

    with pytest.raises(RuntimeValidationError, match="JPY"):
        ExecutionPipeline.apply_execution_report(result.state, order, foreign, _RATES)

    # And the same report in a settled currency is booked, so the refusal is
    # about the currency and not about the path.
    settled = replace(foreign, currency="EUR", execution_id=str(uuid4()))
    booked, fills, _ = ExecutionPipeline.apply_execution_report(
        result.state, order, settled, _RATES
    )
    assert len(fills) == 1
    assert booked.portfolio.positions[_SAP.asset_id].currency == "EUR"


def test_a_pipeline_cannot_be_funded_in_a_currency_it_does_not_settle() -> None:
    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {})

    with id_scope(_SEED):
        state = ExecutionPipeline.initialize(
            _config(strategy_id), running_strategy_state(strategy_id, strategy), 1.0
        )

    with pytest.raises(RuntimeValidationError, match="cannot be funded in 'JPY'"):
        ExecutionPipeline.fund(state, Decimal("1000"), "JPY", 2.0)


def test_a_single_currency_pipeline_is_unchanged() -> None:
    """The default, and every run before v2.17: one currency, no rates, no cost."""

    config = _config(str(uuid4()), also_settles=frozenset(), budget_currency=None)

    assert config.settlement_currencies == frozenset({"USD"})
    assert not config.is_multi_currency
    assert not config.budget.states_currency


# --------------------------------------------------------------------------- #
# 4. Settlement FX conversion is an act, never a consequence
# --------------------------------------------------------------------------- #


def test_settling_a_currency_the_book_does_not_hold_is_refused_not_financed() -> None:
    """No fill converts cash to cover itself. The refusal is the whole point."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {2.0: Decimal("10")})

    with id_scope(_SEED):
        unfunded = ExecutionPipeline.initialize(
            _config(strategy_id), running_strategy_state(strategy_id, strategy), 1.0
        )
        with pytest.raises(InsufficientFundsError, match="EUR"):
            ExecutionPipeline.process_quote(
                unfunded, quote(_SAP.asset_id, 2.0, Decimal("100")), context_factory, rates=_RATES
            )


def test_a_cash_conversion_records_the_rate_that_produced_it() -> None:
    from alphalab.portfolio.events import CashConverted

    state = PortfolioState(account=Account("A", "USD", "n", 1.0))
    state = PortfolioEngine.apply_deposit(state, Decimal("100000"), "USD", 1.0)
    state, conversion = PortfolioEngine.convert_cash(
        state, Decimal("11000"), "USD", "EUR", _RATES, 2.0
    )

    assert state.cash.balance("USD") == Decimal("89000.00")
    assert state.cash.balance("EUR") == conversion.converted

    event = next(e for e in state.events if isinstance(e, CashConverted))
    assert (event.from_currency, event.to_currency) == ("USD", "EUR")
    assert event.rate == Decimal("0.909091")
    assert event.rate_source == "ECB"
    assert event.rate_as_of == 2.0
    assert event.amount == Decimal("11000.00")


def test_a_cash_conversion_without_a_rate_moves_no_money() -> None:
    """Refused *before* the debit: a half-completed conversion is worse than none."""

    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("A", "USD", "n", 1.0)), Decimal("100000"), "USD", 1.0
    )

    with pytest.raises(MissingRateError):
        PortfolioEngine.convert_cash(state, Decimal("1000"), "USD", "JPY", _RATES, 2.0)

    assert state.cash.balance("USD") == Decimal("100000.00")
    assert state.cash.balance("JPY") == Decimal("0.00")


@pytest.mark.parametrize(
    ("amount", "source", "target"),
    [(Decimal("0"), "USD", "EUR"), (Decimal("-1"), "USD", "EUR"), (Decimal("1"), "USD", "USD")],
)
def test_a_meaningless_cash_conversion_is_refused(
    amount: Decimal, source: str, target: str
) -> None:
    state = PortfolioEngine.apply_deposit(
        PortfolioState(account=Account("A", "USD", "n", 1.0)), Decimal("100"), "USD", 1.0
    )

    with pytest.raises(InvalidTransactionError):
        PortfolioEngine.convert_cash(state, amount, source, target, _RATES, 2.0)


# --------------------------------------------------------------------------- #
# 5. The budget and the risk limits, which are the other two blockers
# --------------------------------------------------------------------------- #


def test_a_multi_currency_pipeline_refuses_a_budget_in_no_currency() -> None:
    """Blocker 3. One currency determines it; two do not."""

    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {})
    config = _config(strategy_id, budget_currency=None)

    with pytest.raises(RuntimeValidationError, match="names no currency"):
        ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)


def test_a_budget_in_a_currency_the_pipeline_cannot_settle_is_refused() -> None:
    strategy_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, _SAP.asset_id, {})
    config = _config(strategy_id, budget_currency="JPY")

    with pytest.raises(RuntimeValidationError, match=r"CapitalBudget\.currency is 'JPY'"):
        ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)


def test_a_foreign_notional_is_compared_against_the_budget_in_the_budgets_currency() -> None:
    """Blocker 3 again, on the sizing path rather than the configuration one.

    10 SAP at EUR 100 is EUR 1,000, which is USD 1,100 against a USD budget. The
    reservation the allocation ledger holds must be the converted figure, or the
    budget would be compared against a number in the wrong currency.
    """

    from alphalab.allocation.events import AllocationCompleted, AllocationExecutionApplied

    first, _ = _run_eur_trade()
    events = list(first.state.allocation.events)

    # EUR 1,000 of notional, sized against a USD budget as USD 1,100.
    completed = next(e for e in events if isinstance(e, AllocationCompleted))
    assert completed.total_notional == Decimal("1100.00")

    # And the fill that consumed it is converted the same way, or the ledger
    # would free a different amount of capital than it reserved.
    applied = next(e for e in events if isinstance(e, AllocationExecutionApplied))
    assert applied.executed_notional == Decimal("1100.00")
    assert first.state.allocation.notional_allocated == Decimal("0.00")


def test_risk_reads_a_mixed_book_as_one_figure_rather_than_dropping_a_currency() -> None:
    """Blocker 4. ``cash.balance(base)`` silently dropped every other balance."""

    _, second = _run_eur_trade()
    risk = second.state.risk
    portfolio = second.state.portfolio

    usd_only = portfolio.cash.balance("USD")
    assert risk.cash > usd_only, "the EUR balance reaches risk rather than vanishing"
    assert risk.current_nav == PortfolioValuation.snapshot(portfolio, 5.0, "USD", _RATES).equity
    assert risk.buying_power == max(Decimal("0.00"), risk.cash)


def test_risk_refuses_a_book_it_cannot_express_rather_than_under_reporting() -> None:
    _, second = _run_eur_trade()

    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.snapshot(second.state.portfolio, 5.0, "USD", FxRates())


# --------------------------------------------------------------------------- #
# 6. Durability and replay
# --------------------------------------------------------------------------- #


def test_a_multi_currency_book_round_trips_through_its_snapshot() -> None:
    _, second = _run_eur_trade()
    portfolio = second.state.portfolio

    restored = restore(from_primitives(deserialize(serialize(capture(portfolio)))))

    assert restored == portfolio
    assert restored.realized_pnl.of("EUR") == Decimal("40.00")
    assert restored.settlement_currencies == ("EUR", "USD")


def test_the_payload_records_each_currency_separately() -> None:
    _, second = _run_eur_trade()
    payload = deserialize(serialize(capture(second.state.portfolio)))

    assert payload["schema_version"] == PORTFOLIO_SNAPSHOT_SCHEMA == 3
    assert payload["realized_pnl"] == {"EUR": "40.00"}
    assert set(payload["balances"]) == {"USD", "EUR"}


def test_a_version_two_payload_is_refused_rather_than_relabelled() -> None:
    """The migration that would have been a guess about money.

    A v2 payload's ``realized_pnl`` is a number in no currency. Reading it as the
    account's base currency looks like a migration and is an invention -- a v2
    run settling one currency while its account declared another would have its
    whole P&L history relabelled, silently.
    """

    from alphalab.persistence.exceptions import StateDecodeError

    _, second = _run_eur_trade()
    payload = dict(deserialize(serialize(capture(second.state.portfolio))))
    payload["schema_version"] = 2
    payload["realized_pnl"] = "40.00"

    with pytest.raises(StateDecodeError, match="declares schema version 2"):
        from_primitives(payload)


def test_replaying_the_same_fills_reproduces_the_same_settlement() -> None:
    """Determinism across currencies: the same inputs, the same buckets."""

    assert _run_eur_trade()[1].state.portfolio == _run_eur_trade()[1].state.portfolio


def test_the_pipeline_config_carries_its_settlement_set_across_a_restart() -> None:
    from alphalab.runtime.snapshot import _capture_config

    strategy_id = str(uuid4())
    config = _config(strategy_id)
    record = _capture_config(config)

    assert record.also_settles == ("EUR",), "sorted, for a deterministic payload"
    assert record.budget.currency == "USD"
