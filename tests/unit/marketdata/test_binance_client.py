"""Tests for the real Binance client, using StaticTransport instead of live network.

Fixture payloads match Binance's documented public REST API response shapes for
/api/v3/klines, /api/v3/ticker/bookTicker, /api/v3/trades, and /api/v3/depth.
"""

import json

import pytest

from alphalab.marketdata.binance.client import binanceClient
from alphalab.marketdata.binance.config import binanceConfig
from alphalab.marketdata.exceptions import MarketDataValidationError
from alphalab.marketdata.timeframe import Timeframe
from alphalab.marketdata.transport import StaticTransport

BASE_URL = "https://api.binance.com"


def _config() -> binanceConfig:
    return binanceConfig(provider_id="binance-1", api_key="test-key")


# --------------------------------------------------------------------------- #
# Config bug fix
# --------------------------------------------------------------------------- #


def test_config_default_base_url_is_binance_not_yahoo() -> None:
    """Regression test for the copy-paste bug: base_url defaulted to Yahoo Finance."""
    config = binanceConfig(provider_id="binance-1", api_key="test-key")
    assert config.base_url == "https://api.binance.com"
    assert "yahoo" not in config.base_url.lower()


# --------------------------------------------------------------------------- #
# request_history / klines
# --------------------------------------------------------------------------- #


def test_request_history_parses_klines_response() -> None:
    klines_payload = json.dumps(
        [
            [
                1700000000000,
                "50000.00",
                "50500.00",
                "49800.00",
                "50200.00",
                "123.456",
                0,
                "0",
                0,
                "0",
                "0",
                "0",
            ],
            [
                1700000060000,
                "50200.00",
                "50300.00",
                "50100.00",
                "50250.00",
                "45.678",
                0,
                "0",
                0,
                "0",
                "0",
                "0",
            ],
        ]
    ).encode()
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/klines": klines_payload})
    client = binanceClient(_config(), transport)

    bars = client.request_history("BTCUSDT", Timeframe.MINUTE, 1700000000.0, 1700000120.0)

    assert len(bars) == 2
    assert bars[0].symbol == "BTCUSDT"
    assert bars[0].open == 50000.00
    assert bars[0].high == 50500.00
    assert bars[0].low == 49800.00
    assert bars[0].close == 50200.00
    assert bars[0].volume == 123.456
    assert bars[0].timestamp == pytest.approx(1700000000.0)


def test_request_history_empty_response_returns_empty_tuple() -> None:
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/klines": b"[]"})
    client = binanceClient(_config(), transport)
    bars = client.request_history("BTCUSDT", Timeframe.DAILY, 0.0, 1000.0)
    assert bars == ()


def test_request_history_raises_for_tick_timeframe() -> None:
    """Binance klines has no tick-level interval; TICK must raise, not silently
    map to something wrong."""
    transport = StaticTransport()
    client = binanceClient(_config(), transport)
    with pytest.raises(MarketDataValidationError):
        client.request_history("BTCUSDT", Timeframe.TICK, 0.0, 1000.0)


def test_request_history_maps_daily_and_hourly_intervals_distinctly() -> None:
    """Confirms different timeframes actually produce different requests, not the
    same hardcoded call regardless of input -- the exact bug being fixed."""
    daily_body = json.dumps([[0, "1", "1", "1", "1", "1", 0, "0", 0, "0", "0", "0"]]).encode()
    hourly_body = json.dumps([[0, "2", "2", "2", "2", "2", 0, "0", 0, "0", "0", "0"]]).encode()

    class RecordingTransport:
        def __init__(self) -> None:
            self.last_params: dict[str, str] = {}

        def get(self, url: str, params: dict[str, str]) -> bytes:
            self.last_params = dict(params)
            return daily_body if params.get("interval") == "1d" else hourly_body

    transport = RecordingTransport()
    client = binanceClient(_config(), transport)  # type: ignore[arg-type]

    daily_bars = client.request_history("BTCUSDT", Timeframe.DAILY, 0.0, 1000.0)
    assert transport.last_params["interval"] == "1d"
    assert daily_bars[0].open == 1.0

    hourly_bars = client.request_history("BTCUSDT", Timeframe.HOURLY, 0.0, 1000.0)
    assert transport.last_params["interval"] == "1h"
    assert hourly_bars[0].open == 2.0


# --------------------------------------------------------------------------- #
# latest_quote / bookTicker
# --------------------------------------------------------------------------- #


def test_latest_quote_parses_book_ticker_response() -> None:
    payload = json.dumps(
        {
            "symbol": "BTCUSDT",
            "bidPrice": "50000.00",
            "bidQty": "1.5",
            "askPrice": "50001.00",
            "askQty": "2.0",
        }
    ).encode()
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/ticker/bookTicker": payload})
    client = binanceClient(_config(), transport)

    quote = client.latest_quote("BTCUSDT")

    assert quote is not None
    assert quote.symbol == "BTCUSDT"
    assert quote.bid == 50000.00
    assert quote.ask == 50001.00
    assert quote.bid_size == 1.5
    assert quote.ask_size == 2.0


def test_latest_quote_returns_none_for_malformed_response() -> None:
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/ticker/bookTicker": b"{}"})
    client = binanceClient(_config(), transport)
    assert client.latest_quote("BTCUSDT") is None


# --------------------------------------------------------------------------- #
# latest_trade / trades
# --------------------------------------------------------------------------- #


def test_latest_trade_parses_trades_response() -> None:
    payload = json.dumps(
        [{"id": 1, "price": "50000.00", "qty": "0.1", "time": 1700000000000, "isBuyerMaker": True}]
    ).encode()
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/trades": payload})
    client = binanceClient(_config(), transport)

    trade = client.latest_trade("BTCUSDT")

    assert trade is not None
    assert trade.price == 50000.00
    assert trade.size == 0.1
    assert trade.timestamp == pytest.approx(1700000000.0)


def test_latest_trade_returns_none_for_empty_response() -> None:
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/trades": b"[]"})
    client = binanceClient(_config(), transport)
    assert client.latest_trade("BTCUSDT") is None


# --------------------------------------------------------------------------- #
# order_book / depth
# --------------------------------------------------------------------------- #


def test_order_book_parses_depth_response() -> None:
    payload = json.dumps(
        {
            "lastUpdateId": 123,
            "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
            "asks": [["50001.00", "1.0"], ["50002.00", "3.0"]],
        }
    ).encode()
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/depth": payload})
    client = binanceClient(_config(), transport)

    book = client.order_book("BTCUSDT")

    assert book is not None
    assert len(book.bids) == 2
    assert book.bids[0].price == 50000.00
    assert book.bids[0].size == 1.5
    assert len(book.asks) == 2
    assert book.asks[0].price == 50001.00


def test_order_book_returns_none_for_malformed_response() -> None:
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/depth": b"{}"})
    client = binanceClient(_config(), transport)
    assert client.order_book("BTCUSDT") is None


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #


def test_health_reports_healthy_on_successful_ping() -> None:
    transport = StaticTransport(responses={f"{BASE_URL}/api/v3/ping": b"{}"})
    client = binanceClient(_config(), transport)
    assert client.health() == {"status": "healthy"}


def test_health_reports_unhealthy_when_transport_raises() -> None:
    class FailingTransport:
        def get(self, url: str, params: dict[str, str]) -> bytes:
            raise ConnectionError("simulated network failure")

    client = binanceClient(_config(), FailingTransport())  # type: ignore[arg-type]
    result = client.health()
    assert result["status"] == "unhealthy"
    assert "simulated network failure" in result["error"]


# --------------------------------------------------------------------------- #
# The four still-unimplemented providers now fail loudly instead of lying
# --------------------------------------------------------------------------- #


def test_remaining_providers_raise_not_implemented_instead_of_returning_fake_data() -> None:
    from alphalab.marketdata.databento.client import databentoClient
    from alphalab.marketdata.nse.client import nseClient
    from alphalab.marketdata.polygon.client import polygonClient
    from alphalab.marketdata.yahoo.client import YahooClient

    for client_cls in (databentoClient, nseClient, polygonClient, YahooClient):
        client = client_cls()
        with pytest.raises(NotImplementedError):
            client.request_history("AAPL", Timeframe.DAILY, 0.0, 1000.0)
        with pytest.raises(NotImplementedError):
            client.latest_quote("AAPL")
