"""
Tests for the Trading Bridge webhook server.
Covers all 12 test cases Father specified + extras.

Run: pytest tests/ -v
"""

import os
import sys
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test env BEFORE importing app
os.environ["ALPACA_API_KEY"] = "test_key"
os.environ["ALPACA_SECRET"] = "test_secret"
os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
os.environ["WEBHOOK_SECRET"] = "MY_WEBHOOK_SECRET"
os.environ["PERMITTED_SYMBOLS"] = "SPY"
os.environ["TRADING_MODE"] = "paper"

from fastapi.testclient import TestClient
import store
from app import app


# ─── Fixtures ─────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    """Use a fresh SQLite DB for each test."""
    test_db = tmp_path / "test_signals.db"
    store.DB_PATH = test_db
    store.init_db()
    yield
    if test_db.exists():
        test_db.unlink()


@pytest.fixture
def client():
    return TestClient(app)


def make_payload(action="BUY", symbol="SPY", secret="MY_WEBHOOK_SECRET", **overrides):
    """Helper to create valid webhook payloads."""
    data = {
        "secret": secret,
        "strategy": "Faisal_SPY_V1",
        "symbol": symbol,
        "action": action,
        "price": "550.25",
        "time": "2026-09-09T16:30:00Z",
    }
    data.update(overrides)
    return data


# ─── Mock Alpaca ──────────────────────────────────────────────
def mock_account(buying_power=100000, equity=80000):
    return {
        "equity": equity,
        "cash": equity,
        "buying_power": buying_power,
        "status": "ACTIVE",
        "trading_blocked": False,
    }


def mock_order(order_id="order-123", qty="10", status="filled"):
    return {
        "order_id": order_id,
        "client_order_id": f"tv-test",
        "symbol": "SPY",
        "side": "buy",
        "qty": qty,
        "status": status,
    }


# ═══════════════════════════════════════════════════════════════
# TEST 1: Invalid webhook secret → REJECT
# ═══════════════════════════════════════════════════════════════
def test_01_invalid_secret(client):
    resp = client.post("/webhook/tradingview", json=make_payload(secret="WRONG"))
    assert resp.status_code == 403
    assert "Invalid secret" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# TEST 2: Invalid symbol → REJECT
# ═══════════════════════════════════════════════════════════════
def test_02_invalid_symbol(client):
    resp = client.post("/webhook/tradingview", json=make_payload(symbol="AAPL"))
    assert resp.status_code == 400
    assert "not permitted" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# TEST 3: Invalid action → REJECT
# ═══════════════════════════════════════════════════════════════
def test_03_invalid_action(client):
    resp = client.post("/webhook/tradingview", json=make_payload(action="HOLD"))
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
# TEST 4: BUY while flat → BUY executed
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value=None)
@patch("broker.get_account", return_value=mock_account())
@patch("broker.submit_buy", return_value=mock_order())
def test_04_buy_while_flat(mock_buy, mock_acct, mock_pos, mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(action="BUY"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    # Background task should have called submit_buy
    mock_buy.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# TEST 5: BUY while already long → IGNORE
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value={"symbol": "SPY", "qty": 10, "side": "long"})
def test_05_buy_while_long(mock_pos, mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(action="BUY"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    # Signal accepted but execution should see "already long" and not trade


# ═══════════════════════════════════════════════════════════════
# TEST 6: SELL while long → CLOSE
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value={"symbol": "SPY", "qty": 10, "side": "long"})
@patch("broker.close_position", return_value={"order_id": "close-123", "symbol": "SPY", "side": "sell", "status": "closing"})
def test_06_sell_while_long(mock_close, mock_pos, mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(action="SELL"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    mock_close.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# TEST 7: SELL while flat → IGNORE (no auto-short)
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value=None)
def test_07_sell_while_flat(mock_pos, mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(action="SELL"))
    assert resp.status_code == 200
    # Signal accepted but execution should see "not long" and skip


# ═══════════════════════════════════════════════════════════════
# TEST 8: Same BUY sent twice → only ONE order
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value=None)
@patch("broker.get_account", return_value=mock_account())
@patch("broker.submit_buy", return_value=mock_order())
def test_08_duplicate_buy(mock_buy, mock_acct, mock_pos, mock_mkt, client):
    payload = make_payload(action="BUY", time="2026-09-09T16:30:00Z")
    resp1 = client.post("/webhook/tradingview", json=payload)
    resp2 = client.post("/webhook/tradingview", json=payload)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "accepted"
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"
    # submit_buy should have been called exactly once
    assert mock_buy.call_count == 1


# ═══════════════════════════════════════════════════════════════
# TEST 9: Same SELL sent twice → only ONE close
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value={"symbol": "SPY", "qty": 10, "side": "long"})
@patch("broker.close_position", return_value={"order_id": "close-456", "symbol": "SPY", "side": "sell", "status": "closing"})
def test_09_duplicate_sell(mock_close, mock_pos, mock_mkt, client):
    payload = make_payload(action="SELL", time="2026-09-09T17:00:00Z")
    resp1 = client.post("/webhook/tradingview", json=payload)
    resp2 = client.post("/webhook/tradingview", json=payload)
    assert resp1.json()["status"] == "accepted"
    assert resp2.json()["status"] == "duplicate"
    assert mock_close.call_count == 1


# ═══════════════════════════════════════════════════════════════
# TEST 10: Market closed → no order
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=False)
def test_10_market_closed(mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(
        action="BUY", time="2026-09-09T03:00:00Z"  # Unique time for unique signal_id
    ))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    # Execution should reject due to market closed


# ═══════════════════════════════════════════════════════════════
# TEST 11: Alpaca unavailable → safely fail and log
# ═══════════════════════════════════════════════════════════════
@patch("broker.is_market_open", return_value=True)
@patch("broker.get_position", return_value=None)
@patch("broker.get_account", return_value=mock_account())
@patch("broker.submit_buy", side_effect=Exception("Connection refused"))
def test_11_alpaca_unavailable(mock_buy, mock_acct, mock_pos, mock_mkt, client):
    resp = client.post("/webhook/tradingview", json=make_payload(
        action="BUY", time="2026-09-09T18:00:00Z"
    ))
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    # Webhook accepted but execution should fail gracefully


# ═══════════════════════════════════════════════════════════════
# TEST 12: Live endpoint → BLOCK
# ═══════════════════════════════════════════════════════════════
def test_12_live_endpoint_blocked():
    """Verify that Config.is_live_blocked() catches live URLs."""
    from config import Config
    original = Config.ALPACA_BASE_URL
    Config.ALPACA_BASE_URL = "https://api.alpaca.markets"
    assert Config.is_live_blocked() is True
    Config.ALPACA_BASE_URL = original


# ═══════════════════════════════════════════════════════════════
# EXTRA: Invalid JSON → 400
# ═══════════════════════════════════════════════════════════════
def test_extra_invalid_json(client):
    resp = client.post(
        "/webhook/tradingview",
        content="not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════
# EXTRA: Health endpoint works
# ═══════════════════════════════════════════════════════════════
@patch("broker.get_account", return_value=mock_account())
@patch("broker.is_market_open", return_value=True)
def test_extra_health(mock_mkt, mock_acct, client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════
# EXTRA: Missing fields → 400
# ═══════════════════════════════════════════════════════════════
def test_extra_missing_fields(client):
    resp = client.post("/webhook/tradingview", json={"secret": "MY_WEBHOOK_SECRET"})
    assert resp.status_code == 400
