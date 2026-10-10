"""Comprehensive tests for the Crypto Engine: instruments, funding, symbols, perpetuals."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from alphalab.crypto import (
    CryptoInputError,
    CryptoInstrument,
    FundingRate,
    FundingRateHistory,
    InstrumentType,
    MaintenanceBasis,
    annualized_funding_rate,
    average_funding_rate,
    compute_funding_payment,
    compute_liquidation_price,
    crypto_symbol,
    mark_to_market,
    open_crypto_position,
    to_canonical_symbol,
)
from alphalab.portfolio.position import Position
from alphalab.portfolio.types import PositionSide

ONE_MONTH = 30 * 86400


def _spot(exchange: str = "binance") -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.SPOT,
        exchange=exchange,
        contract_size=Decimal("1"),
    )


def _perpetual(exchange: str = "binance") -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.PERPETUAL,
        exchange=exchange,
        contract_size=Decimal("1"),
    )


def _future(exchange: str = "binance", expiry: float = ONE_MONTH) -> CryptoInstrument:
    return CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.FUTURE,
        exchange=exchange,
        expiry=expiry,
        contract_size=Decimal("1"),
    )


# --------------------------------------------------------------------------- #
# CryptoInstrument
# --------------------------------------------------------------------------- #


def test_instrument_is_immutable() -> None:
    instrument = _spot()
    with pytest.raises(FrozenInstanceError):
        instrument.base_asset = "ETH"  # type: ignore[misc]


def test_future_requires_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.FUTURE,
            exchange="binance",
            contract_size=Decimal("1"),
        )


def test_spot_rejects_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.SPOT,
            exchange="binance",
            expiry=ONE_MONTH,
            contract_size=Decimal("1"),
        )


def test_perpetual_rejects_expiry() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.PERPETUAL,
            exchange="binance",
            expiry=ONE_MONTH,
            contract_size=Decimal("1"),
        )


def test_instrument_rejects_non_positive_contract_size() -> None:
    with pytest.raises(CryptoInputError):
        CryptoInstrument(
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=InstrumentType.SPOT,
            exchange="binance",
            contract_size=Decimal("0"),
        )


def test_crypto_symbol_differs_by_instrument_type() -> None:
    assert crypto_symbol(_spot()) != crypto_symbol(_perpetual())


def test_crypto_symbol_includes_expiry_for_futures_only() -> None:
    assert crypto_symbol(_future()).endswith("_197001")
    assert "_" not in crypto_symbol(_perpetual()).split("PERPETUAL")[-1]


# --------------------------------------------------------------------------- #
# Position bridge: same principle as Options and Futures
# --------------------------------------------------------------------------- #


def test_open_crypto_position_returns_unmodified_portfolio_position() -> None:
    instrument = _spot()
    position = open_crypto_position(
        instrument, Decimal("0.5"), Decimal("50000.00"), timestamp=1000.0
    )

    assert type(position) is Position
    assert position.asset_id == crypto_symbol(instrument)
    assert position.quantity == Decimal("0.5")
    assert position.currency == "USDT"


def test_crypto_position_supports_apply_fill_from_portfolio_package() -> None:
    instrument = _perpetual()
    position = open_crypto_position(
        instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0, minor_units=6
    )
    updated, realized = position.apply_fill(Decimal("-1"), Decimal("52000.00"), timestamp=2000.0)

    assert updated.quantity == Decimal("0")
    assert realized == Decimal("2000.00")


def test_a_settlement_asset_outside_iso_4217_states_its_minor_units() -> None:
    """v3.10: USDT has no ISO minor unit, so money read off an undeclared one refuses."""

    from alphalab.common.currency_units import UnknownCurrencyUnitsError

    instrument = _perpetual()
    undeclared = open_crypto_position(instrument, Decimal("1"), Decimal("50000"), 1000.0)
    with pytest.raises(UnknownCurrencyUnitsError, match="USDT"):
        _ = undeclared.market_value

    declared = open_crypto_position(
        instrument, Decimal("0.1234567"), Decimal("50000"), 1000.0, minor_units=6
    )
    assert declared.market_value == Decimal("6172.835000")


def test_short_crypto_position_quantity_is_negative() -> None:
    instrument = _perpetual()
    position = open_crypto_position(
        instrument, Decimal("-2"), Decimal("50000.00"), timestamp=1000.0
    )
    assert position.quantity < 0


# --------------------------------------------------------------------------- #
# The canonical pair symbol (venue spellings are the host's: ledger BND-004)
# --------------------------------------------------------------------------- #


def test_to_canonical_symbol_format() -> None:
    assert to_canonical_symbol("btc", "usdt") == "BTC-USDT"


def test_the_package_formats_no_exchange_symbol() -> None:
    import alphalab.crypto as crypto

    assert not {"to_exchange_symbol", "parse_exchange_symbol"} & set(dir(crypto))


# --------------------------------------------------------------------------- #
# Funding rates
# --------------------------------------------------------------------------- #


def test_funding_rate_rejects_non_positive_interval() -> None:
    with pytest.raises(CryptoInputError):
        FundingRate(
            instrument_symbol="X", rate=Decimal("0.0001"), timestamp=1000.0, interval_hours=0
        )


def test_compute_funding_payment_long_pays_when_rate_positive() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        contract_size=Decimal("1"),
    )
    assert payment == Decimal("-5.00000")  # long pays 50000 * 0.0001


def test_compute_funding_payment_short_receives_when_rate_positive() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("-1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        contract_size=Decimal("1"),
    )
    assert payment == Decimal("5.00000")


def test_compute_funding_payment_scales_with_contract_size() -> None:
    payment = compute_funding_payment(
        position_quantity=Decimal("1"),
        mark_price=Decimal("50000"),
        funding_rate=Decimal("0.0001"),
        contract_size=Decimal("10"),
    )
    assert payment == Decimal("-50.00000")


def test_average_funding_rate() -> None:
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0, interval_hours=8
            ),
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0003"), timestamp=28800.0, interval_hours=8
            ),
        ),
    )
    assert average_funding_rate(history) == Decimal("0.0002")


def test_average_funding_rate_raises_on_empty_history() -> None:
    with pytest.raises(CryptoInputError):
        average_funding_rate(FundingRateHistory(instrument_symbol="X", rates=()))


def test_annualized_funding_rate_matches_hand_computed_value() -> None:
    """0.0001 average rate every 8 hours -> 0.0001 * (24*365/8) = 0.1095."""
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0, interval_hours=8
            ),
        ),
    )
    result = annualized_funding_rate(history)
    assert result == pytest.approx(Decimal("0.1095"), abs=Decimal("0.0001"))


def test_annualized_funding_rate_rejects_mixed_intervals() -> None:
    history = FundingRateHistory(
        instrument_symbol="X",
        rates=(
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=0.0, interval_hours=8
            ),
            FundingRate(
                instrument_symbol="X", rate=Decimal("0.0001"), timestamp=3600.0, interval_hours=1
            ),
        ),
    )
    with pytest.raises(CryptoInputError):
        annualized_funding_rate(history)


# --------------------------------------------------------------------------- #
# Perpetual mark-to-market and liquidation price
# --------------------------------------------------------------------------- #


def test_mark_to_market_updates_price_and_returns_unrealized_pnl() -> None:
    instrument = _perpetual()
    position = open_crypto_position(
        instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0, minor_units=6
    )

    updated, pnl = mark_to_market(position, Decimal("52000.00"), timestamp=2000.0)

    assert updated.market_price == Decimal("52000.0000")
    assert pnl == Decimal("2000.00")


def test_mark_to_market_does_not_mutate_input_position() -> None:
    instrument = _perpetual()
    position = open_crypto_position(
        instrument, Decimal("1"), Decimal("50000.00"), timestamp=1000.0, minor_units=6
    )
    mark_to_market(position, Decimal("52000.00"), timestamp=2000.0)
    assert position.market_price == Decimal("50000.0000")


def _liquidation(
    side: PositionSide,
    leverage: str,
    rate: str,
    basis: MaintenanceBasis,
    *,
    fees: str = "0",
    funding: str = "0",
    quantity: str = "2",
) -> Decimal | None:
    return compute_liquidation_price(
        Decimal("50000"),
        side,
        Decimal(leverage),
        Decimal(rate),
        basis=basis,
        quantity=Decimal(quantity),
        fees=Decimal(fees),
        funding=Decimal(funding),
    )


def test_on_entry_notional_the_familiar_formula_is_exact() -> None:
    """10x at 50000, 0.5% maintenance: 50000(1 - 0.1 + 0.005) and 50000(1 + 0.1 - 0.005)."""

    entry = MaintenanceBasis.ENTRY_NOTIONAL
    assert _liquidation(PositionSide.LONG, "10", "0.005", entry) == Decimal("45250")
    assert _liquidation(PositionSide.SHORT, "10", "0.005", entry) == Decimal("54750")


def test_on_mark_notional_the_requirement_moves_with_the_price() -> None:
    """50000(1 - 0.1)/(1 - 0.005) and 50000(1 + 0.1)/(1 + 0.005).

    There, equity equals the maintenance margin charged at the mark.
    """

    mark = MaintenanceBasis.MARK_NOTIONAL
    long_price = _liquidation(PositionSide.LONG, "10", "0.005", mark)
    short_price = _liquidation(PositionSide.SHORT, "10", "0.005", mark)
    assert long_price is not None and short_price is not None
    assert abs(long_price - Decimal("45000") / Decimal("0.995")) < Decimal("1e-20")
    assert abs(short_price - Decimal("55000") / Decimal("1.005")) < Decimal("1e-20")
    # At that price the posted margin plus the loss is exactly the requirement.
    posted, quantity = Decimal("10000"), Decimal("2")
    long_equity = posted + quantity * (long_price - Decimal("50000"))
    assert abs(long_equity - Decimal("0.005") * quantity * long_price) < Decimal("1e-20")


def test_fees_bring_liquidation_closer_and_received_funding_pushes_it_away() -> None:
    entry = MaintenanceBasis.ENTRY_NOTIONAL
    base = _liquidation(PositionSide.LONG, "10", "0.005", entry)
    assert _liquidation(PositionSide.LONG, "10", "0.005", entry, fees="100") == Decimal("45300")
    assert _liquidation(PositionSide.LONG, "10", "0.005", entry, funding="100") == Decimal("45200")
    assert _liquidation(PositionSide.LONG, "10", "0.005", entry, funding="-100") == Decimal("45300")
    assert base == Decimal("45250")
    short = _liquidation(PositionSide.SHORT, "10", "0.005", entry, fees="100")
    assert short == Decimal("54700")


@pytest.mark.parametrize("basis", list(MaintenanceBasis))
@pytest.mark.parametrize("side", [PositionSide.LONG, PositionSide.SHORT])
def test_at_the_price_returned_equity_is_exactly_the_requirement(
    side: PositionSide, basis: MaintenanceBasis
) -> None:
    """Fees and funding included, on either basis, checked from the equation (NUM-014).

    Equity is the posted margin, plus the position's P&L at the price, plus
    funding received, less fees; the requirement is the maintenance rate times
    the notional the basis names -- the entry's or the mark's. Not the closed
    form re-derived: the condition it solves.
    """

    entry, quantity, rate = Decimal("50000"), Decimal("2"), Decimal("0.005")
    posted, fees, funding = Decimal("10000"), Decimal("180"), Decimal("45")
    price = _liquidation(side, "10", "0.005", basis, fees="180", funding="45")
    assert price is not None
    sign = 1 if side is PositionSide.LONG else -1
    equity = posted + sign * quantity * (price - entry) + funding - fees
    notional = quantity * (price if basis is MaintenanceBasis.MARK_NOTIONAL else entry)
    assert abs(equity - rate * notional) < Decimal("1e-18")


def test_a_long_at_low_leverage_has_no_liquidation_price() -> None:
    assert _liquidation(PositionSide.LONG, "1", "0", MaintenanceBasis.MARK_NOTIONAL) is None
    assert _liquidation(PositionSide.LONG, "0.5", "0.005", MaintenanceBasis.ENTRY_NOTIONAL) is None


def test_a_position_already_at_its_maintenance_margin_is_refused() -> None:
    with pytest.raises(CryptoInputError, match="moment it opened"):
        _liquidation(PositionSide.LONG, "100", "0.01", MaintenanceBasis.ENTRY_NOTIONAL)
    with pytest.raises(CryptoInputError, match="moment it opened"):
        _liquidation(
            PositionSide.SHORT, "10", "0.005", MaintenanceBasis.ENTRY_NOTIONAL, fees="9950"
        )


def test_the_result_does_not_depend_on_the_callers_decimal_context() -> None:
    import decimal

    expected = _liquidation(PositionSide.LONG, "3", "0.005", MaintenanceBasis.MARK_NOTIONAL)
    with decimal.localcontext() as hostile:
        hostile.prec = 5
        hostile.rounding = decimal.ROUND_FLOOR
        found = _liquidation(PositionSide.LONG, "3", "0.005", MaintenanceBasis.MARK_NOTIONAL)
    assert found == expected


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"side": PositionSide.FLAT}, "FLAT"),
        ({"leverage": Decimal("0")}, "leverage"),
        ({"maintenance_margin_rate": Decimal("-0.01")}, r"\[0, 1\)"),
        ({"maintenance_margin_rate": Decimal("1")}, r"\[0, 1\)"),
        ({"quantity": Decimal("0")}, "quantity"),
        ({"fees": Decimal("-1")}, "fees"),
        ({"funding": Decimal("NaN")}, "finite"),
        ({"basis": "mark"}, "MaintenanceBasis"),
    ],
)
def test_terms_that_state_no_position_are_refused(overrides: dict[str, object], match: str) -> None:
    terms: dict[str, object] = {
        "entry_price": Decimal("50000"),
        "side": PositionSide.LONG,
        "leverage": Decimal("10"),
        "maintenance_margin_rate": Decimal("0.005"),
        "basis": MaintenanceBasis.ENTRY_NOTIONAL,
        "quantity": Decimal("1"),
        "fees": Decimal("0"),
        "funding": Decimal("0"),
        **overrides,
    }
    with pytest.raises(CryptoInputError, match=match):
        compute_liquidation_price(**terms)  # type: ignore[arg-type]


def test_higher_leverage_moves_liquidation_price_closer_to_entry() -> None:
    """Higher leverage means less room before liquidation -- a basic sanity check
    on the formula's directional behavior, not just its exact output."""
    for basis in MaintenanceBasis:
        low_leverage_liq = _liquidation(PositionSide.LONG, "2", "0.005", basis)
        high_leverage_liq = _liquidation(PositionSide.LONG, "20", "0.005", basis)
        assert low_leverage_liq is not None and high_leverage_liq is not None
        assert high_leverage_liq > low_leverage_liq
