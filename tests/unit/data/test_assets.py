"""Asset classes keep the fields they need, and are not flattened into one shape.

The failure this prevents: a futures price series read as though it were an
equity series, producing P&L that is wrong by the contract multiplier -- 1,000x
for a crude contract -- with nothing raised anywhere.

Each spec below carries what its class needs and refuses what it cannot mean.
"""

from __future__ import annotations

import pytest

from alphalab.data import (
    CommoditySpec,
    CryptoSpec,
    DataAssetClass,
    DataValidationError,
    EquitySpec,
    FutureSpec,
    FxSpec,
    IndexSpec,
    InstrumentSpec,
    OptionSpec,
    RateSpec,
    asset_class_of,
)
from alphalab.options.enums import ExerciseStyle, OptionType

EXPIRY = 1_767_225_600.0


def test_each_spec_reports_its_own_asset_class() -> None:
    cases: list[tuple[InstrumentSpec, DataAssetClass]] = [
        (EquitySpec("AAPL", "USD", "XNAS"), DataAssetClass.EQUITY),
        (EquitySpec("SPY", "USD", "ARCX", is_fund=True), DataAssetClass.ETF),
        (IndexSpec("SPX", "USD", "S&P Dow Jones"), DataAssetClass.INDEX),
        (FutureSpec("CLZ5", "USD", "XNYM", "CL", EXPIRY, 1000.0, 0.01), DataAssetClass.FUTURE),
        (
            OptionSpec("AAPL_C_200", "USD", "OPRA", "AAPL", 200.0, EXPIRY, OptionType.CALL, 100.0),
            DataAssetClass.OPTION,
        ),
        (FxSpec("EURUSD", "EUR", "USD"), DataAssetClass.FOREX),
        (CryptoSpec("BTCUSDT", "BINANCE", "BTC", "USDT"), DataAssetClass.CRYPTO),
        (RateSpec("SOFR3M", "USD", 90, "ACT/360", quoted_in_percent=True), DataAssetClass.RATE),
        (CommoditySpec("BRENT", "USD", "barrel", "Sullom Voe"), DataAssetClass.COMMODITY),
    ]

    for spec, expected in cases:
        assert asset_class_of(spec) is expected


def test_a_future_carries_the_multiplier_that_makes_its_price_mean_money() -> None:
    contract = FutureSpec("CLZ5", "USD", "XNYM", "CL", EXPIRY, 1000.0, 0.01)

    assert contract.multiplier == 1000.0
    assert contract.root == "CL", "what a continuous series is built of"
    assert contract.expiry == EXPIRY, "what makes a futures series finite"


def test_a_future_with_no_multiplier_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        FutureSpec("CLZ5", "USD", "XNYM", "CL", EXPIRY, 0.0, 0.01)

    assert "controlling nothing" in str(error.value)


def test_a_future_whose_expiry_precedes_its_contract_month_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        FutureSpec("CLZ5", "USD", "XNYM", "CL", EXPIRY, 1000.0, 0.01, contract_month=EXPIRY + 1.0)

    assert "precedes contract month" in str(error.value)


def test_an_option_carries_strike_expiry_and_right() -> None:
    put = OptionSpec(
        "AAPL_P_150",
        "USD",
        "OPRA",
        "AAPL",
        150.0,
        EXPIRY,
        OptionType.PUT,
        100.0,
        style=ExerciseStyle.EUROPEAN,
    )

    assert put.strike == 150.0
    assert put.option_type is OptionType.PUT
    assert put.style is ExerciseStyle.EUROPEAN


def test_call_and_put_are_not_spelled_a_third_time() -> None:
    """``alphalab/common/types.py`` records the lesson: the Order/Side/Status
    fragmentation across broker/brokers/oms/execution was learned the hard way,
    and one more copy of CALL/PUT is how that starts again."""

    from alphalab.data import assets

    assert assets.OptionType is OptionType
    assert assets.ExerciseStyle is ExerciseStyle


def test_a_currency_pair_has_no_single_currency_of_its_own() -> None:
    """The spec that most obviously refuses the equity shape: the price of
    EURUSD is a ratio between two currencies, not a price *in* one."""

    pair = FxSpec("EURUSD", "EUR", "USD")

    assert (pair.base_currency, pair.quote_currency) == ("EUR", "USD")
    assert not hasattr(pair, "currency")


def test_a_pair_of_one_currency_with_itself_is_refused() -> None:
    with pytest.raises(DataValidationError) as error:
        FxSpec("USDUSD", "USD", "usd")

    assert "has no rate" in str(error.value)


def test_a_crypto_pair_must_name_its_venue() -> None:
    """The same pair on two venues is two price series with two order books and
    two prices at the same instant, and neither is the reference."""

    with pytest.raises(DataValidationError):
        CryptoSpec("BTCUSDT", "  ", "BTC", "USDT")

    assert CryptoSpec("BTCUSDT", "BINANCE", "BTC", "USDT").venue == "BINANCE"


def test_a_rate_states_whether_it_is_quoted_in_percent() -> None:
    """The alternative convention differs by 100x and no amount of looking at
    the numbers reliably distinguishes 5.0% from 500%."""

    rate = RateSpec("SOFR3M", "USD", 90, "ACT/360", quoted_in_percent=True)

    assert rate.quoted_in_percent is True
    assert (rate.tenor_days, rate.day_count) == (90, "ACT/360")


def test_a_rate_with_no_tenor_is_refused() -> None:
    with pytest.raises(DataValidationError):
        RateSpec("SOFR", "USD", 0, "ACT/360", quoted_in_percent=True)


def test_an_index_has_no_exchange_because_it_is_calculated_not_listed() -> None:
    level = IndexSpec("SPX", "USD", "S&P Dow Jones")

    assert not hasattr(level, "exchange")
    assert level.publisher == "S&P Dow Jones"


def test_a_commodity_names_its_unit_and_delivery_point() -> None:
    """What makes two 'crude oil' series comparable, or not."""

    brent = CommoditySpec("BRENT", "USD", "barrel", "Sullom Voe")

    assert (brent.unit, brent.delivery_location) == ("barrel", "Sullom Voe")


def test_every_spec_refuses_an_unnamed_symbol() -> None:
    with pytest.raises(DataValidationError):
        EquitySpec("   ", "USD", "XNAS")
    with pytest.raises(DataValidationError):
        IndexSpec("SPX", "USD", "  ")
