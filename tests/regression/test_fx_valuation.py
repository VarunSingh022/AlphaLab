"""A mixed book values as one figure, and says which rates got it there.

Four ADRs deferred to "the release that supplies the rate source". This is that
release, and the shape of what it supplies is decided almost entirely by
ADR-0020's rejected alternative:

> **Introduce a rate source with a fixed or configurable rate.** Rejected. A
> configured rate is an invented one, and a figure derived from it is exactly as
> wrong as the figure being removed, with the added cost of looking
> authoritative.

So the tests below are as much about what FX *refuses* as about what it
converts. A rate with no source, a rate AlphaLab computed for itself, a rate too
old to be true, a pair nobody supplied -- each is refused, and each refusal says
which one it is, because they call for different fixes.

The three properties that carry the release:

1. **The rule did not fork.** ``assert_single_currency_book`` is still the one
   implementation, and every helper still reaches it. A currency it can convert
   stopped being a currency it must refuse; nothing else moved.
2. **The homogeneous path is untouched.** ADR-0028 decision 7 measured guarding
   the component sums at +1.78% end-to-end. A single-currency book still takes
   the unguarded path and converts nothing.
3. **A converted figure is attributable.** ADR-0020 removed a number in no
   currency; a number in a currency the book is not wholly in, with no statement
   of how it got there, would be the same defect wearing a rate.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.portfolio.account import Account
from alphalab.portfolio.cash import CashLedger
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.exceptions import MixedCurrencyValuationError, PortfolioError
from alphalab.portfolio.fx import (
    NO_RATES,
    FxRate,
    FxRates,
    MissingRateError,
    StaleRateError,
)
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.position import Position
from alphalab.portfolio.valuation import (
    PortfolioValuation,
    assert_single_currency,
    assert_single_currency_book,
    foreign_currencies,
)

ZERO = Decimal("0.00")

#: 1 EUR buys 1.10 USD, quoted by a named source at t=1.0.
EURUSD = FxRate("EUR", "USD", Decimal("1.10"), 1.0, "ECB")
GBPUSD = FxRate("GBP", "USD", Decimal("1.25"), 1.0, "ECB")
RATES = FxRates.of([EURUSD, GBPUSD])


def _position(asset_id: str, currency: str, quantity: str = "10", mark: str = "110") -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal("100"),
        market_price=Decimal(mark),
        realized_pnl=ZERO,
        currency=currency,
        last_updated=1.0,
    )


def _state(
    *,
    balances: dict[str, Decimal] | None = None,
    positions: dict[str, Position] | None = None,
    base: str = "USD",
) -> PortfolioState:
    return PortfolioState(
        account=Account("acct", base, "FX Account", 1.0),
        cash=CashLedger(balances=dict(balances or {})),
        positions=dict(positions or {}),
    )


# --------------------------------------------------------------------------- #
# 1 -- a rate is a quote, with provenance
# --------------------------------------------------------------------------- #


def test_a_rate_with_no_source_is_refused() -> None:
    """The configured rate ADR-0020 will not have."""

    with pytest.raises(PortfolioError, match="no source"):
        FxRate("EUR", "USD", Decimal("1.10"), 1.0, "")


def test_a_non_positive_rate_is_refused() -> None:
    for bad in (Decimal("0"), Decimal("-1.1")):
        with pytest.raises(PortfolioError, match="not an exchange rate"):
            FxRate("EUR", "USD", bad, 1.0, "ECB")


def test_a_currency_against_itself_is_not_a_rate() -> None:
    with pytest.raises(PortfolioError, match="not a conversion"):
        FxRate("USD", "USD", Decimal("1"), 1.0, "ECB")


def test_two_rates_for_one_pair_are_refused_rather_than_picked_between() -> None:
    other = FxRate("EUR", "USD", Decimal("1.20"), 2.0, "Reuters")

    with pytest.raises(PortfolioError, match="Two rates quote EUR/USD"):
        FxRates.of([EURUSD, other])


def test_a_rate_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        EURUSD.rate = Decimal("9")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 2 -- no triangulation, no silent inversion
# --------------------------------------------------------------------------- #


def test_an_unsupplied_pair_is_refused_and_names_what_is_held() -> None:
    with pytest.raises(MissingRateError, match=r"No rate for JPY/USD"):
        RATES.convert(Decimal("100"), "JPY", "USD")


def test_the_inverse_is_not_assumed() -> None:
    """EUR/USD at 1.10 does not make USD/EUR 1/1.10 -- a real quote has two sides."""

    assert RATES.rate_for("USD", "EUR") is None

    with pytest.raises(MissingRateError):
        RATES.convert(Decimal("100"), "USD", "EUR")


def test_inverses_are_available_but_only_deliberately_and_marked_as_derived() -> None:
    inverted = RATES.with_inverses()

    derived = inverted.rate_for("USD", "EUR")
    assert derived is not None
    assert derived.derived, "a computed rate presented itself as a quote"
    assert derived.source == "ECB", "the derivation keeps the provenance it came from"
    assert derived.rate == Decimal("1") / Decimal("1.10")

    # And the original quote is untouched.
    quoted = inverted.rate_for("EUR", "USD")
    assert quoted is not None and not quoted.derived


def test_a_real_quote_is_never_replaced_by_a_derived_one() -> None:
    usdeur = FxRate("USD", "EUR", Decimal("0.91"), 1.0, "Reuters")
    table = FxRates.of([EURUSD, usdeur]).with_inverses()

    kept = table.rate_for("USD", "EUR")
    assert kept is not None and kept.rate == Decimal("0.91")
    assert not kept.derived


def test_triangulation_is_not_performed() -> None:
    """EUR/GBP from EUR/USD and GBP/USD is a rate nobody quoted."""

    assert RATES.rate_for("EUR", "GBP") is None
    with pytest.raises(MissingRateError):
        RATES.convert(Decimal("100"), "EUR", "GBP")


# --------------------------------------------------------------------------- #
# 3 -- staleness
# --------------------------------------------------------------------------- #


def test_a_stale_rate_is_refused_rather_than_used() -> None:
    table = FxRates.of([EURUSD], max_age_seconds=60.0)

    with pytest.raises(StaleRateError, match="is refused rather than used"):
        table.convert(Decimal("100"), "EUR", "USD", as_of=1000.0)


def test_a_fresh_rate_passes_the_same_check() -> None:
    table = FxRates.of([EURUSD], max_age_seconds=60.0)

    assert table.convert(Decimal("100"), "EUR", "USD", as_of=30.0).converted == Decimal("110.00")


def test_no_tolerance_means_no_check_and_that_is_the_default() -> None:
    """AlphaLab does not know what tolerance a desk runs to."""

    assert FxRates.of([EURUSD]).max_age_seconds is None
    assert RATES.convert(Decimal("100"), "EUR", "USD", as_of=1_000_000.0).converted == Decimal(
        "110.00"
    )


def test_naming_no_instant_skips_the_check_even_with_a_tolerance() -> None:
    """A caller that names no instant is not claiming one."""

    table = FxRates.of([EURUSD], max_age_seconds=60.0)

    assert table.convert(Decimal("100"), "EUR", "USD").converted == Decimal("110.00")


# --------------------------------------------------------------------------- #
# 4 -- the identity, and what a conversion records
# --------------------------------------------------------------------------- #


def test_converting_a_currency_into_itself_needs_no_rate() -> None:
    """Requiring a USD/USD row would make every table carry a meaningless one."""

    conversion = NO_RATES.convert(Decimal("100"), "USD", "USD")

    assert conversion.converted == Decimal("100")


def test_a_conversion_says_what_it_did() -> None:
    conversion = RATES.convert(Decimal("100"), "EUR", "USD")

    assert conversion.amount == Decimal("100")
    assert conversion.converted == Decimal("110.00")
    assert conversion.rate is EURUSD
    assert "100 EUR -> 110.00 USD at 1.10 from 'ECB'" in conversion.summary


def test_a_derived_conversion_says_so_in_its_summary() -> None:
    conversion = RATES.with_inverses().convert(Decimal("110"), "USD", "EUR")

    assert "(derived)" in conversion.summary


# --------------------------------------------------------------------------- #
# 5 -- the rule did not fork
# --------------------------------------------------------------------------- #


def test_with_no_rates_the_refusal_is_exactly_what_it_was() -> None:
    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "EUR")})

    with pytest.raises(MixedCurrencyValuationError) as error:
        PortfolioValuation.snapshot(state, 1.0, "USD")

    message = str(error.value)
    assert "No FX rates were supplied" in message
    assert "not a rule that foreign-currency instruments are invalid" in message


def test_supplying_rates_that_miss_the_pair_is_a_different_refusal() -> None:
    """Rates were supplied and this pair is not among them -- a different fix."""

    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "JPY")})

    with pytest.raises(MixedCurrencyValuationError) as error:
        PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    message = str(error.value)
    assert "none converts ['JPY']" in message
    assert "does not triangulate or invert" in message
    assert "No FX rates were supplied" not in message


def test_the_state_form_and_the_component_form_still_agree_under_rates() -> None:
    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "JPY")})

    with pytest.raises(MixedCurrencyValuationError) as from_state:
        assert_single_currency(state, "USD", RATES)
    with pytest.raises(MixedCurrencyValuationError) as from_components:
        assert_single_currency_book(state.cash, state.positions, "USD", RATES)

    assert str(from_state.value) == str(from_components.value)


def test_one_place_decides_what_another_currency_means() -> None:
    """The refusal and the conversion cannot disagree about it."""

    ledger = CashLedger(balances={"USD": Decimal("1000.00")}).deposit(Decimal("50"), "EUR")
    ledger = ledger.withdraw(Decimal("50"), "EUR")
    positions = {"a": _position("a", "USD"), "b": _position("b", "GBP", quantity="0")}

    foreign_positions, foreign_cash = foreign_currencies(ledger, positions, "USD")

    assert foreign_positions == ("GBP",), "a flat position still declares its currency"
    assert foreign_cash == (), "a spent currency is residue, not a second currency"


# --------------------------------------------------------------------------- #
# 6 -- a mixed book values
# --------------------------------------------------------------------------- #


def test_a_mixed_book_values_as_one_figure() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    # 1000 USD + (500 EUR * 1.10) = 1550 cash
    assert snapshot.cash == Decimal("1550.00")
    # 1100 USD + (1100 EUR * 1.10) = 1100 + 1210 = 2310 positions
    assert snapshot.positions_value == Decimal("2310.00")
    assert snapshot.equity == Decimal("3860.00")
    assert snapshot.currency == "USD"


def test_the_v27_defect_is_not_what_a_converted_figure_produces() -> None:
    """The exact book ADR-0020 reported at 3200.00 in no currency at all."""

    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={
            "a": _position("a", "USD", quantity="10", mark="110"),
            "b": _position("b", "EUR", quantity="10", mark="110"),
        },
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    assert snapshot.equity != Decimal("3200.00"), (
        "the converted total equals the old unlabelled sum, which would mean the "
        "rate was not applied"
    )
    assert snapshot.equity == Decimal("3860.00")


def test_a_converted_valuation_records_every_rate_it_used() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"b": _position("b", "EUR")},
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    assert snapshot.converted
    assert snapshot.rate_sources == ("ECB",)
    assert all(conversion.rate.base == "EUR" for conversion in snapshot.conversions)
    # cash, market value and unrealized P&L were each converted.
    assert len(snapshot.conversions) == 3


def test_a_single_currency_book_converts_nothing_and_says_so() -> None:
    """Property 2: the homogeneous path is untouched."""

    state = _state(balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "USD")})

    with_rates = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)
    without = PortfolioValuation.snapshot(state, 1.0, "USD")

    assert with_rates == without, "supplying rates changed a homogeneous valuation"
    assert with_rates.conversions == ()
    assert not with_rates.converted


def test_unrealized_pnl_is_converted_too() -> None:
    """A EUR position's open P&L is not USD P&L until a rate says so."""

    state = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"b": _position("b", "EUR", quantity="10", mark="110")},
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    # 10 * (110 - 100) = 100 EUR, at 1.10 = 110 USD
    assert snapshot.unrealized_pnl == Decimal("110.00")


def test_a_short_foreign_position_converts_into_short_value() -> None:
    state = _state(
        balances={"USD": Decimal("1000.00")},
        positions={"b": _position("b", "EUR", quantity="-10", mark="110")},
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)

    assert snapshot.long_value == ZERO
    assert snapshot.short_value == Decimal("-1210.00")
    assert snapshot.equity == Decimal("-210.00")


# --------------------------------------------------------------------------- #
# 7 -- every currency-claiming helper agrees
# --------------------------------------------------------------------------- #


def test_the_three_valuation_helpers_agree_on_a_mixed_book() -> None:
    """They reach the same rule and now the same rates. ADR-0028 decision 6."""

    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", RATES)
    value = PortfolioValuation.portfolio_value(state.cash, state.positions, "USD", RATES)
    nav = NAVCalculator.calculate(state.cash, state.positions, "USD", RATES)

    assert snapshot.equity == value == nav == Decimal("3860.00")


def test_all_three_refuse_together_when_a_pair_is_missing() -> None:
    state = _state(balances={"USD": Decimal("1000.00")}, positions={"b": _position("b", "JPY")})

    for call in (
        lambda: PortfolioValuation.snapshot(state, 1.0, "USD", RATES),
        lambda: PortfolioValuation.portfolio_value(state.cash, state.positions, "USD", RATES),
        lambda: NAVCalculator.calculate(state.cash, state.positions, "USD", RATES),
    ):
        with pytest.raises(MixedCurrencyValuationError):
            call()


def test_the_components_gained_no_guard() -> None:
    """ADR-0028 decision 7, measured at +1.78%. Still unguarded, still unlabelled."""

    import inspect

    for component in (PortfolioValuation.long_value, PortfolioValuation.short_value):
        parameters = inspect.signature(component).parameters
        assert list(parameters) == ["positions"], f"{component.__name__} gained a currency"
        body = inspect.getsource(component).split('"""')[-1]
        assert "assert_single_currency" not in body
        assert "rates" not in body


def test_cash_value_is_still_a_keyed_lookup() -> None:
    """It names a currency and aggregates nothing, so it converts nothing."""

    import inspect

    assert list(inspect.signature(PortfolioValuation.cash_value).parameters) == [
        "cash_ledger",
        "base_currency",
    ]


# --------------------------------------------------------------------------- #
# 8 -- what FX deliberately does not change
# --------------------------------------------------------------------------- #


def test_the_default_table_is_empty_so_nothing_changes_without_one() -> None:
    assert not NO_RATES
    assert len(NO_RATES) == 0
    assert NO_RATES.pairs == ()


def test_the_settlement_boundary_is_unchanged() -> None:
    """ADR-0028 decision 2 said a permissive mode "is FX, and it is deferred".

    FX arriving does not by itself make a pipeline able to *trade* two
    currencies: ``realized_pnl`` and ``commission_paid`` are single cumulative
    scalars that name no currency, and a run that traded in two would sum them
    across both. v2.16 closes the *valuation* gap that was documented and leaves
    the settlement boundary where ADR-0028 put it.
    """

    import inspect

    from alphalab.runtime import execution_pipeline

    assert not hasattr(execution_pipeline, "SettlementPolicy")
    source = inspect.getsource(execution_pipeline._settlement_refusal)
    assert "cannot be traded here" in source

    fields = PortfolioState.__dataclass_fields__
    assert "realized_pnl" in fields
    assert "realized_pnl_by_currency" not in fields, (
        "the settlement boundary moved without this test being updated"
    )


def test_the_pipeline_config_carries_no_rate_table() -> None:
    """A rate is time-varying data, not run configuration.

    Putting a table on ``ExecutionPipelineConfig`` would fix one rate for a whole
    run and move ``PIPELINE_SNAPSHOT_SCHEMA`` to serve a book the settlement
    boundary prevents the pipeline from producing.
    """

    from dataclasses import fields as dataclass_fields

    from alphalab.runtime.execution_pipeline import ExecutionPipelineConfig
    from alphalab.runtime.snapshot import PIPELINE_SNAPSHOT_SCHEMA

    names = {f.name for f in dataclass_fields(ExecutionPipelineConfig)}
    assert "fx_rates" not in names
    assert "rates" not in names
    assert PIPELINE_SNAPSHOT_SCHEMA == 2


# --------------------------------------------------------------------------- #
# 9 -- a conversion produces money, and rounds like money
# --------------------------------------------------------------------------- #


def _is_money(amount: Decimal) -> bool:
    """Exact at the minor unit: the right value *and* no sub-cent digits."""

    from alphalab.portfolio.money import CURRENCY_QUANT

    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int):  # NaN / Infinity are not money either
        return False
    return amount == amount.quantize(CURRENCY_QUANT) and -exponent <= 2


def test_a_converted_figure_is_money_shaped_like_every_other_figure() -> None:
    """`money.py`: every monetary amount is an exact multiple of the minor unit.

    A rate is a *price*, not money -- 500.00 EUR at 1.087343 is 543.6715 USD,
    which is not a number of cents. Before the v2.16 acceptance review the
    conversion never passed through `to_money`, so a converted `equity` came
    back as `3839.74880000` while a single-currency one came back as `2100.00`:
    the same field with two shapes, and sub-cent digits in a figure a human
    reads.
    """

    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )
    awkward = FxRates.of([FxRate("EUR", "USD", Decimal("1.087343"), 1.0, "ECB")])

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", awkward)

    for name in (
        "cash",
        "long_value",
        "short_value",
        "positions_value",
        "unrealized_pnl",
        "equity",
    ):
        value = getattr(snapshot, name)
        assert _is_money(value), f"{name} is {value!r}, which is not money-shaped"


def test_the_rounding_point_is_the_conversion_and_there_is_only_one() -> None:
    """money.py rule 2: round once at entry, then exact addition downstream.

    Rounding the *total* instead would be the second independent rounding that
    policy exists to remove.
    """

    awkward = FxRates.of([FxRate("EUR", "USD", Decimal("1.087343"), 1.0, "ECB")])

    conversion = awkward.convert(Decimal("500.00"), "EUR", "USD")

    assert conversion.converted == Decimal("543.67")
    assert _is_money(conversion.converted)
    # The raw product, kept recoverable rather than discarded.
    assert conversion.rounding == Decimal("543.67") - Decimal("500.00") * Decimal("1.087343")
    assert abs(conversion.rounding) < Decimal("0.005")


def test_the_identity_conversion_is_money_too() -> None:
    conversion = NO_RATES.convert(Decimal("100.005"), "USD", "USD")

    assert _is_money(conversion.converted)


def test_converted_and_homogeneous_valuations_have_the_same_shape() -> None:
    """The defect, stated as the property that would have caught it."""

    homogeneous = _state(
        balances={"USD": Decimal("1000.00")}, positions={"a": _position("a", "USD")}
    )
    mixed = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )
    awkward = FxRates.of([FxRate("EUR", "USD", Decimal("1.087343"), 1.0, "ECB")])

    plain = PortfolioValuation.snapshot(homogeneous, 1.0, "USD")
    converted = PortfolioValuation.snapshot(mixed, 1.0, "USD", awkward)

    assert _is_money(plain.equity) and _is_money(converted.equity)
    assert plain.equity.as_tuple().exponent == converted.equity.as_tuple().exponent


def test_the_three_helpers_still_agree_after_rounding() -> None:
    """Per-conversion rounding must not make them disagree by a cent."""

    state = _state(
        balances={"USD": Decimal("1000.00"), "EUR": Decimal("500.00")},
        positions={"a": _position("a", "USD"), "b": _position("b", "EUR")},
    )
    awkward = FxRates.of([FxRate("EUR", "USD", Decimal("1.087343"), 1.0, "ECB")])

    snapshot = PortfolioValuation.snapshot(state, 1.0, "USD", awkward)
    value = PortfolioValuation.portfolio_value(state.cash, state.positions, "USD", awkward)
    nav = NAVCalculator.calculate(state.cash, state.positions, "USD", awkward)

    assert snapshot.equity == value == nav
