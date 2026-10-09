#!/usr/bin/env python3
# ==============================================================================
# bybit_smart_order.py — Standalone Bybit Smart Order Tool
#
# @describe Place a smart order with automatic position sizing and risk management.
# @option --symbol! <VALUE> Trading pair (e.g., BTCUSDT)
# @option --side! [Buy|Sell] Order side
# @option --order-type [Market|Limit] Order type (default: Market)
# @option --risk-pct <NUM> Risk percentage (default: 1.0)
# @option --sl-dist <NUM> Stop loss distance
# @option --sl-price <NUM> Absolute stop loss price
# @option --tp-price <NUM> Absolute take profit price
# @option --sl-usdt <NUM> Stop loss in USDT (absolute value)
# @option --tp-usdt <NUM> Take profit in USDT (absolute value)
# @option --time-in-force [GTC|IOC|FOK|PostOnly] Time in force (default: GTC)
# @option --reduce-only <BOOL> Submit order as reduce-only (close position only)
# @option --margin-mode [isolated|cross] Margin mode (default: cross)
# @option --position-idx <NUM> Position index: 0=One-Way, 1=Buy hedge, 2=Sell hedge (default: 0)
# @option --order-link-id <TEXT> Custom order link ID (max 36 chars) for idempotency
# @option --dry-run <BOOL> Calculate everything and return the order payload without submitting
# @option --max-position-usdt <NUM> Safety cap: refuse if notional > this USDT value
# @env BYBIT_API_KEY Bybit API Key
# @env BYBIT_API_SECRET Bybit API Secret
# @env BYBIT_TESTNET Use testnet (true/false)
# @env USE_TOR Use Tor proxy (true/false)
# @env TOR_PROXY Tor proxy URL
# ==============================================================================

import json
import logging
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict, Optional

# Try to import pybit, fallback to requests
try:
    from pybit.exceptions import FailedRequestError, InvalidRequestError
    from pybit.unified_trading import HTTP

    USE_PYBIT = True
except ImportError:
    USE_PYBIT = False
    InvalidRequestError = Exception
    FailedRequestError = Exception


# ==============================================================================
# Configuration & Logging
# ==============================================================================
def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure structured multi-level logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("bybit_smart_order")
    logger.setLevel(level)

    if not logger.handlers:
        # Console handler with color
        console = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "\033[96m[BYBIT] %(asctime)s [%(levelname)s] %(message)s\033[0m",
            datefmt="%H:%M:%S",
        )
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger


logger = setup_logging()

# Environment Configuration
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
USE_TOR = os.getenv("USE_TOR", "false").lower() == "true"
TOR_PROXY = os.getenv("TOR_PROXY", "socks5h://127.0.0.1:9050")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"


# ==============================================================================
# Decorators & Helpers
# ==============================================================================
def handle_api_errors(func):
    """Decorator with exponential backoff for rate limits."""
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        retries = 3
        backoff = 1
        for i in range(retries):
            try:
                return func(*args, **kwargs)
            except (FailedRequestError, InvalidRequestError) as e:
                error_str = str(e)
                if "10006" in error_str and i < retries - 1:
                    logger.warning(f"Rate limited (10006). Backing off {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                logger.error(f"API Error in {func.__name__}: {error_str}")
                return {"status": "error", "message": error_str}
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {e}")
                return {"status": "error", "message": str(e)}

    return wrapper


def get_session() -> Any:
    """Create Bybit HTTP session with optional Tor proxy."""
    if USE_PYBIT:
        http_kwargs = {
            "testnet": TESTNET,
            "api_key": API_KEY,
            "api_secret": API_SECRET,
        }
        # Handle Tor proxy by setting environment variables
        if USE_TOR:
            os.environ["HTTP_PROXY"] = TOR_PROXY
            os.environ["HTTPS_PROXY"] = TOR_PROXY
        return HTTP(**http_kwargs)
    else:
        return None


def get_market_rules(symbol: str, session: Any) -> Dict[str, Any]:
    """Fetch tickSize, qtyStep, minOrderQty for a symbol and return filters."""
    if USE_PYBIT:
        res = session.get_instruments_info(category="linear", symbol=symbol)
    else:
        from utils.bybit_base import api_request

        res = api_request(
            "GET",
            "/v5/market/instruments-info",
            {"category": "linear", "symbol": symbol},
        )

    data = res["result"]["list"][0]

    # Health check: ensure instrument is in Trading state
    if data["status"] != "Trading":
        raise Exception(f"{symbol} is in {data['status']} mode, not Trading.")

    return {
        "lot_filter": {
            "qty_step": float(data["lotSizeFilter"]["qtyStep"]),
            "min_order_qty": float(data["lotSizeFilter"]["minOrderQty"]),
            "max_order_qty": float(data["lotSizeFilter"]["maxOrderQty"]),
        },
        "price_filter": {
            "tick_size": float(data["priceFilter"]["tickSize"]),
            "min_price": float(data["priceFilter"]["minPrice"]),
            "max_price": float(data["priceFilter"]["maxPrice"]),
        },
        "tick": Decimal(data["priceFilter"]["tickSize"]),
        "step": Decimal(data["lotSizeFilter"]["qtyStep"]),
        "min_qty": Decimal(data["lotSizeFilter"]["minOrderQty"]),
    }


# ==============================================================================
# Core Trading Tools
# ==============================================================================
@handle_api_errors
def bybit_smart_order(
    symbol: str,
    side: str,
    order_type: str = "Market",
    risk_pct: float = 1.0,
    sl_dist: Optional[float] = None,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_usdt: Optional[float] = None,
    tp_usdt: Optional[float] = None,
    time_in_force: str = "GTC",
    reduce_only: bool = False,
    margin_mode: str = "cross",
    position_idx: int = 0,
    order_link_id: Optional[str] = None,
    dry_run: bool = False,
    max_position_usdt: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Smart Order with automatic position sizing and risk management.
    Supports USDT-based TP/SL, time_in_force, and order_type.
    """
    start_time = time.time()
    session = get_session()

    # Get balance
    if USE_PYBIT:
        balance_resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    else:
        from utils.bybit_base import api_request

        balance_resp = api_request(
            "GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True
        )

    if balance_resp.get("retCode") != 0:
        return {"error": f"Balance check failed: {balance_resp.get('retMsg')}"}

    total_equity = Decimal(str(balance_resp["result"]["list"][0]["totalEquity"]))

    # Get ticker for current price
    if USE_PYBIT:
        ticker_resp = session.get_tickers(category="linear", symbol=symbol)
    else:
        ticker_resp = api_request(
            "GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )

    if ticker_resp.get("retCode") != 0:
        return {"error": f"Ticker fetch failed: {ticker_resp.get('retMsg')}"}

    last_price = Decimal(ticker_resp["result"]["list"][0]["lastPrice"])

    # Get market rules (tick size, qty step)
    rules = get_market_rules(symbol, session)

    # Calculate qty directly based on risk_pct and current price
    risk_amount = total_equity * Decimal(str(risk_pct / 100))
    qty = risk_amount / Decimal(str(last_price))

    # Ensure minimum order quantity
    if qty < rules["lot_filter"]["min_order_qty"]:
        logger.warning(
            f"Calculated qty {qty} below min {rules['lot_filter']['min_order_qty']}. Forcing minimum quantity."
        )
        qty = rules["lot_filter"]["min_order_qty"]

    # Round to qty step
    qty = rules["lot_filter"]["qty_step"] * round(
        float(qty) / rules["lot_filter"]["qty_step"]
    )

    # Calculate SL and TP prices from sl_usdt and tp_usdt
    if sl_usdt:
        sl_price = (
            float(last_price) - (sl_usdt / qty)
            if side == "Buy"
            else float(last_price) + (sl_usdt / qty)
        )

    if tp_usdt:
        tp_price = (
            float(last_price) + (tp_usdt / qty)
            if side == "Buy"
            else float(last_price) - (tp_usdt / qty)
        )

    # Calculate notional value
    notional = qty * float(last_price)
    if max_position_usdt and notional > max_position_usdt:
        return {
            "error": f"Notional {notional} exceeds max_position_usdt {max_position_usdt}"
        }

    # Place order
    order_params = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "timeInForce": time_in_force,
        "positionIdx": position_idx,
        "reduceOnly": reduce_only,
        "marginMode": margin_mode,
    }

    if order_link_id:
        order_params["orderLinkId"] = order_link_id

    if sl_price:
        order_params["stopLoss"] = str(
            rules["price_filter"]["tick_size"]
            * round(sl_price / rules["price_filter"]["tick_size"])
        )

    if tp_price:
        order_params["takeProfit"] = str(
            rules["price_filter"]["tick_size"]
            * round(tp_price / rules["price_filter"]["tick_size"])
        )

    if dry_run:
        latency_ms = int((time.time() - start_time) * 1000)
        return {
            "action": "smart_order",
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "entry_price": float(last_price),
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "time_in_force": time_in_force,
            "order_type": order_type,
            "latency_ms": latency_ms,
            "order_params": order_params,
            "dry_run": True,
        }

    if USE_PYBIT:
        order_resp = session.place_order(**order_params)
    else:
        from utils.bybit_base import api_request

        order_resp = api_request("POST", "/v5/order/create", order_params, signed=True)

    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "action": "smart_order",
        "symbol": symbol,
        "side": side,
        "qty": float(qty),
        "entry_price": float(last_price),
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "time_in_force": time_in_force,
        "order_type": order_type,
        "latency_ms": latency_ms,
        "response": order_resp,
    }


# ==============================================================================
# Main Entry
# ==============================================================================
def run(args: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point for argc runner."""
    logger = setup_logging(args.get("verbose", False))

    result = bybit_smart_order(
        symbol=args.get("symbol"),
        side=args.get("side"),
        order_type=args.get("order_type", "Market"),
        risk_pct=args.get("risk_pct", 1.0),
        sl_dist=args.get("sl_dist"),
        sl_price=args.get("sl_price"),
        tp_price=args.get("tp_price"),
        sl_usdt=args.get("sl_usdt"),
        tp_usdt=args.get("tp_usdt"),
        time_in_force=args.get("time_in_force", "GTC"),
        reduce_only=args.get("reduce_only", False),
        margin_mode=args.get("margin_mode", "cross"),
        position_idx=args.get("position_idx", 0),
        order_link_id=args.get("order_link_id"),
        dry_run=args.get("dry_run", False),
        max_position_usdt=args.get("max_position_usdt"),
    )

    return result


# ==============================================================================
# Legacy CLI Entry (for direct execution)
# ==============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bybit Smart Order Tool")
    parser.add_argument("--symbol", required=True, help="Trading pair (e.g., BTCUSDT)")
    parser.add_argument(
        "--side", required=True, choices=["Buy", "Sell"], help="Order side"
    )
    parser.add_argument(
        "--order-type", default="Market", choices=["Market", "Limit"], help="Order type"
    )
    parser.add_argument("--risk-pct", type=float, default=1.0, help="Risk percentage")
    parser.add_argument("--sl-dist", type=float, help="Stop loss distance")
    parser.add_argument("--sl-price", type=float, help="Absolute stop loss price")
    parser.add_argument("--tp-price", type=float, help="Absolute take profit price")
    parser.add_argument("--sl-usdt", type=float, help="Stop loss in USDT")
    parser.add_argument("--tp-usdt", type=float, help="Take profit in USDT")
    parser.add_argument("--time-in-force", default="GTC", help="Time in force")
    parser.add_argument(
        "--reduce-only", action="store_true", help="Submit order as reduce-only"
    )
    parser.add_argument("--margin-mode", default="cross", help="Margin mode")
    parser.add_argument("--position-idx", type=int, default=0, help="Position index")
    parser.add_argument("--order-link-id", help="Custom order link ID")
    parser.add_argument("--dry-run", action="store_true", help="Dry run")
    parser.add_argument("--max-position-usdt", type=float, help="Safety cap in USDT")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Convert argparse args to dict
    args_dict = vars(args)
    result = run(args_dict)
    print(json.dumps(result, indent=2))
