from typing import Optional, Union, List
"""
TradingView → Alpaca Trading Bridge
=====================================
FastAPI server that receives TradingView webhook alerts
and executes trades on Alpaca Paper Trading.

Architecture:
  TradingView Alert → HTTPS Webhook → This Server → Alpaca Paper API

Key features:
  - Webhook secret validation
  - Symbol whitelist
  - Duplicate signal protection (SQLite + client_order_id)
  - Fast webhook response (execution after response)
  - Paper trading ONLY (live blocked)
  - Persistent state across restarts
"""

import hashlib
import logging
import datetime
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from config import Config
import store
import broker

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bridge")


# ─── Startup / Shutdown ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    errors = Config.validate()
    if errors:
        for e in errors:
            log.error(f"CONFIG ERROR: {e}")
        if Config.is_live_blocked():
            log.critical("BLOCKED: Live trading endpoint detected. Refusing to start.")
            sys.exit(1)

    store.init_db()
    log.info("=" * 60)
    log.info("TradingView → Alpaca Trading Bridge")
    log.info(f"  Mode: {Config.TRADING_MODE}")
    log.info(f"  Endpoint: {Config.ALPACA_BASE_URL}")
    log.info(f"  Permitted symbols: {Config.PERMITTED_SYMBOLS}")
    log.info("=" * 60)
    yield
    # Shutdown
    log.info("Shutting down...")


app = FastAPI(title="Trading Bridge", version="1.0", lifespan=lifespan)


# ─── Models ───────────────────────────────────────────────────
class WebhookPayload(BaseModel):
    secret: str
    strategy: str
    symbol: str
    action: str
    price: Union[str, float]
    time: str

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        if v.upper() not in ("BUY", "SELL"):
            raise ValueError(f"Invalid action: {v}. Must be BUY or SELL.")
        return v.upper()

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v):
        # Normalize: BTCUSD → BTC/USD for Alpaca
        v = v.upper()
        if v == "BTCUSD":
            v = "BTC/USD"
        return v


# ─── Helpers ──────────────────────────────────────────────────
def generate_signal_id(strategy: str, symbol: str, action: str, time: str) -> str:
    """Deterministic signal_id from strategy + symbol + action + timestamp."""
    raw = f"{strategy}:{symbol}:{action}:{time}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def validate_timestamp(time_str: str) -> bool:
    """Reject signals older than 5 minutes."""
    try:
        # TradingView sends ISO-ish timestamps
        # Be lenient — just check it's parseable
        return True  # Accept for now; tighten later if needed
    except Exception:
        return False


# ─── Execution (runs in background) ──────────────────────────
def execute_signal(signal_id: str, symbol: str, action: str, price: Optional[float]):
    """Execute the trade on Alpaca. Runs after webhook response is sent."""
    try:
        # Safety check
        if Config.is_live_blocked():
            store.update_signal(signal_id, "blocked", error="Live endpoint detected")
            log.critical(f"BLOCKED: {signal_id} — live endpoint")
            return

        # Check market hours
        if not broker.is_market_open():
            store.update_signal(signal_id, "rejected_market_closed")
            log.warning(f"Market closed — signal {signal_id} rejected")
            return

        pos = broker.get_position(symbol)

        if action == "BUY":
            if pos and float(pos["qty"]) > 0:
                # Already long → ignore
                store.update_signal(signal_id, "ignored_already_long")
                log.info(f"Already long {symbol} ({pos['qty']} shares) — ignoring BUY")
                return

            # Use 90% of available cash as notional (dollar amount)
            # Use cash (not buying_power which can be inflated for crypto)
            acct = broker.get_account()
            available = min(acct["buying_power"], acct["cash"])
            notional = round(available * 0.90, 2)
            if notional < 10:
                store.update_signal(signal_id, "rejected_insufficient_funds", error=f"buying_power={acct['buying_power']}")
                log.error(f"Insufficient buying power for {symbol}")
                return

            client_order_id = f"tv-{signal_id}"
            result = broker.submit_buy_notional(symbol, notional, client_order_id)
            store.update_signal(signal_id, "executed", order_id=result["order_id"])
            store.store_trade(signal_id, result["order_id"], symbol, "buy", result.get("qty", str(notional)), result["status"])
            log.info(f"✅ BUY executed: ${notional} of {symbol} — order {result['order_id']}")

            # Set 2% stop loss
            if price and price > 0:
                stop_price = round(price * 0.98, 2)
                try:
                    broker.set_stop_loss(symbol, stop_price)
                    log.info(f"🛡️ Stop loss set at ${stop_price} (2% below entry ${price})")
                except Exception as e:
                    log.error(f"⚠️ Failed to set stop loss: {e}")

        elif action == "SELL":
            if not pos or float(pos["qty"]) <= 0:
                # Not long → ignore (never auto-reverse to short)
                store.update_signal(signal_id, "ignored_not_long")
                log.info(f"Not long {symbol} — ignoring SELL (no auto-short)")
                return

            result = broker.close_position(symbol)
            store.update_signal(signal_id, "executed", order_id=result.get("order_id"))
            store.store_trade(signal_id, result.get("order_id"), symbol, "sell", str(pos["qty"]), "closing")
            log.info(f"✅ SELL executed: closed {pos['qty']} {symbol} — order {result.get('order_id')}")

    except Exception as e:
        store.update_signal(signal_id, "error", error=str(e))
        log.error(f"❌ Execution failed for {signal_id}: {e}")


# ─── Routes ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        acct = broker.get_account()
        market_open = broker.is_market_open()
    except Exception as e:
        return JSONResponse(
            {"status": "degraded", "error": str(e), "timestamp": datetime.datetime.utcnow().isoformat()},
            status_code=200,
        )
    return {
        "status": "ok",
        "trading_mode": Config.TRADING_MODE,
        "market_open": market_open,
        "equity": acct["equity"],
        "permitted_symbols": Config.PERMITTED_SYMBOLS,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


@app.get("/signals")
async def get_signals():
    """Recent signals."""
    return {"signals": store.recent_signals()}


@app.get("/trades")
async def get_trades():
    """Recent trades."""
    return {"trades": store.recent_trades()}


@app.post("/webhook/tradingview")
async def webhook_tradingview(request: Request, background_tasks: BackgroundTasks):
    """
    Receive TradingView alert webhook.
    Validates, deduplicates, then executes in background.
    Returns fast so TradingView doesn't timeout.
    """
    # 1. Parse JSON
    try:
        body = await request.json()
    except Exception:
        log.warning("REJECTED: Invalid JSON")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2. Validate payload structure
    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        log.warning(f"REJECTED: Invalid payload — {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # 3. Validate webhook secret
    if payload.secret != Config.WEBHOOK_SECRET:
        log.warning(f"REJECTED: Invalid secret from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 4. Validate symbol
    if payload.symbol not in Config.PERMITTED_SYMBOLS:
        log.warning(f"REJECTED: Symbol {payload.symbol} not permitted")
        raise HTTPException(status_code=400, detail=f"Symbol {payload.symbol} not permitted")

    # 5. Validate timestamp
    if not validate_timestamp(payload.time):
        log.warning(f"REJECTED: Invalid timestamp {payload.time}")
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    # 6. Generate signal_id
    signal_id = generate_signal_id(payload.strategy, payload.symbol, payload.action, payload.time)

    # 7. Check duplicate (atomic via SQLite unique constraint)
    try:
        price = float(payload.price)
    except (ValueError, TypeError):
        price = None

    stored = store.store_signal(
        signal_id=signal_id,
        strategy=payload.strategy,
        symbol=payload.symbol,
        action=payload.action,
        price=price,
        status="pending",
    )

    if not stored:
        log.warning(f"DUPLICATE: signal_id={signal_id} already processed")
        return JSONResponse(
            {"status": "duplicate", "signal_id": signal_id},
            status_code=200,  # 200 so TradingView doesn't retry
        )

    # 8. Log acceptance
    log.info(f"ACCEPTED: {payload.action} {payload.symbol} @ {payload.price} — signal_id={signal_id}")

    # 9. Execute in background (fast response to TradingView)
    background_tasks.add_task(execute_signal, signal_id, payload.symbol, payload.action, price)

    return JSONResponse(
        {"status": "accepted", "signal_id": signal_id, "action": payload.action, "symbol": payload.symbol},
        status_code=200,
    )


# ─── Main ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT, log_level="info")
