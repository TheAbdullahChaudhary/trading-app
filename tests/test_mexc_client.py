"""Unit tests for MEXC client (mock-based)."""
import pytest
from unittest.mock import MagicMock, patch
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot.mexc_client import MEXCClient


@pytest.fixture
def client():
    return MEXCClient("test_key", "test_secret", dry_run=True)


def test_dry_run_place_order(client):
    result = client.place_order("BTC_USDT", side=1, vol=1)
    assert result.get("success") is True
    assert "DRY_" in str(result.get("data", ""))


def test_dry_run_cancel_order(client):
    result = client.cancel_order("order123")
    assert result.get("success") is True


@patch("requests.Session.get")
def test_get_ticker(mock_get, client):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"success": True, "data": {"lastPrice": "65000.0"}}
    )
    mock_get.return_value.raise_for_status = lambda: None
    ticker = client.get_ticker("BTC_USDT")
    assert ticker.get("lastPrice") == "65000.0"


@patch("requests.Session.get")
def test_get_klines_empty(mock_get, client):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"success": True, "data": {}}
    )
    mock_get.return_value.raise_for_status = lambda: None
    candles = client.get_klines("BTC_USDT")
    assert candles == []
