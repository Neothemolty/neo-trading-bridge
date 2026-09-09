from typing import Optional, Union, List
"""Alpaca Paper Trading broker interface."""

import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest
from config import Config

log = logging.getLogger("bridge.broker")

_client: Optional[TradingClient] = None


def get_client() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(
            Config.ALPACA_API_KEY,
            Config.ALPACA_SECRET,
            paper=True,  # Hardcoded paper
            url_override=Config.ALPACA_BASE_URL,
        )
    return _client


def get_account() -> dict:
    """Get account summary."""
    acct = get_client().get_account()
    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buying_power": float(acct.buying_power),
        "status": str(acct.status),
        "trading_blocked": acct.trading_blocked,
    }


def get_position(symbol: str) -> Optional[dict]:
    """Get current position for symbol, or None if flat."""
    try:
        # Try both formats: BTC/USD and BTCUSD (Alpaca accepts either but may vary)
        try:
            pos = get_client().get_open_position(symbol)
        except Exception:
            alt = symbol.replace("/", "")
            pos = get_client().get_open_position(alt)
        return {
            "symbol": symbol,
            "qty": float(pos.qty),
            "side": pos.side.value if pos.side else ("long" if float(pos.qty) > 0 else "short"),
            "avg_entry_price": float(pos.avg_entry_price),
            "market_value": float(pos.market_value),
            "unrealized_pl": float(pos.unrealized_pl),
        }
    except Exception:
        return None


def is_market_open() -> bool:
    """Check if market is currently open."""
    try:
        clock = get_client().get_clock()
        return clock.is_open
    except Exception as e:
        log.error(f"Failed to check market clock: {e}")
        return False


def submit_buy_notional(symbol: str, notional: float, client_order_id: str) -> dict:
    """Submit a market BUY order by dollar amount (notional). Works for fractional/crypto."""
    log.info(f"Submitting BUY ${notional} of {symbol} (client_order_id={client_order_id})")
    tif = TimeInForce.GTC if "/" in symbol else TimeInForce.DAY
    try:
        order = get_client().submit_order(
            MarketOrderRequest(
                symbol=symbol,
                notional=notional,
                side=OrderSide.BUY,
                time_in_force=tif,
                client_order_id=client_order_id,
            )
        )
        result = {
            "order_id": str(order.id),
            "client_order_id": str(order.client_order_id),
            "symbol": order.symbol,
            "side": "buy",
            "qty": str(order.qty) if order.qty else str(notional),
            "status": str(order.status),
        }
        log.info(f"BUY notional order submitted: {result}")
        return result
    except Exception as e:
        log.error(f"BUY notional order failed: {e}")
        raise


def submit_buy(symbol: str, qty: float, client_order_id: str) -> dict:
    """Submit a market BUY order. Returns order info."""
    log.info(f"Submitting BUY {qty} {symbol} (client_order_id={client_order_id})")
    # Crypto uses GTC, stocks use DAY
    tif = TimeInForce.GTC if "/" in symbol else TimeInForce.DAY
    try:
        order = get_client().submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=tif,
                client_order_id=client_order_id,
            )
        )
        result = {
            "order_id": str(order.id),
            "client_order_id": str(order.client_order_id),
            "symbol": order.symbol,
            "side": "buy",
            "qty": str(order.qty),
            "status": str(order.status),
        }
        log.info(f"BUY order submitted: {result}")
        return result
    except Exception as e:
        log.error(f"BUY order failed: {e}")
        raise


def set_stop_loss(symbol: str, stop_price: float) -> dict:
    """Set a stop loss order for the current position."""
    log.info(f"Setting stop loss for {symbol} at ${stop_price}")
    pos = get_position(symbol)
    if not pos:
        raise ValueError(f"No position found for {symbol}")
    
    qty = float(pos["qty"])
    tif = TimeInForce.GTC if "/" in symbol else TimeInForce.DAY
    
    from alpaca.trading.requests import StopLimitOrderRequest
    # For crypto: use stop_limit (stop not supported), limit 0.5% below stop
    limit_price = round(stop_price * 0.995, 2)
    try:
        order = get_client().submit_order(
            StopLimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=tif,
                stop_price=stop_price,
                limit_price=limit_price,
            )
        )
        result = {
            "order_id": str(order.id),
            "symbol": symbol,
            "stop_price": stop_price,
            "qty": str(qty),
            "status": str(order.status),
        }
        log.info(f"Stop loss order submitted: {result}")
        return result
    except Exception as e:
        log.error(f"Stop loss order failed: {e}")
        raise


def cancel_open_orders(symbol: str):
    """Cancel all open orders for a symbol (e.g. stop losses before closing position)."""
    log.info(f"Cancelling open orders for {symbol}")
    try:
        orders = get_client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))
    except Exception:
        alt = symbol.replace("/", "")
        orders = get_client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[alt]))
    
    for order in orders:
        try:
            get_client().cancel_order_by_id(order.id)
            log.info(f"Cancelled order {order.id} ({order.order_type} {order.side})")
        except Exception as e:
            log.warning(f"Failed to cancel order {order.id}: {e}")


def close_position(symbol: str) -> dict:
    """Close entire position for symbol. Returns order info."""
    log.info(f"Closing position for {symbol}")
    try:
        try:
            order = get_client().close_position(symbol)
        except Exception:
            alt = symbol.replace("/", "")
            order = get_client().close_position(alt)
        result = {
            "order_id": str(order.id) if hasattr(order, "id") else None,
            "symbol": symbol,
            "side": "sell",
            "status": "closing",
        }
        log.info(f"Close order submitted: {result}")
        return result
    except Exception as e:
        log.error(f"Close position failed: {e}")
        raise
