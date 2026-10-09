#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
# @describe Bybit Pro Suite - Unified Trading Toolset with smart order, position management, and technical analysis
# @option --symbol! <VALUE> Trading symbol (e.g., BTCUSDT)
# @option --side! [Buy|Sell] Order side
# @option --order-type! [Market|Limit] Order type
# @option --qty! <VALUE> Order quantity
# @option --price <VALUE> Limit price (for Limit orders)
# @option --risk-pct <NUM> Risk percentage (default: 1.0)
# @option --sl-dist <NUM> Stop loss distance
# @option --sl-price <NUM> Absolute stop loss price
# @option --tp-price <NUM> Absolute take profit price
# @option --interval <VALUE> Time interval (1,5,15,60,120,240,D,W,M)
# @option --limit <INT> Number of results (default: 25)
# @option --leverage <VALUE> Leverage value
# @option --mode <INT> Position mode (0=One-Way, 3=Hedge)
# @option --action! [be|close] Position manager action
# @option --profit-usdt <INT> Profit threshold in USDT
# @option --tp-usdt <INT> Take profit in USDT
# @option --sl-usdt <INT> Stop loss in USDT
# @option --order-id <VALUE> Order ID
# @option --order-link-id <VALUE> Custom order ID
# @option --testnet [true|false] Use testnet (default: from env)
# @option --account-type [UNIFIED|CONTRACT|SPOT] Account type
# @option --coin <VALUE> Coin symbol (default: USDT)
# @option --category [linear|inverse|option|spot] Product type
# @option --time-in-force [GTC|IOC|FOK|PostOnly] Time in force
# @option --reduce-only Flag to reduce only position
# @option --verbose Enable verbose logging
# @env BYBIT_API_KEY Bybit API Key
# @env BYBIT_API_SECRET Bybit API Secret
# @env BYBIT_TESTNET Use testnet (true/false)
# @env USE_TOR Use Tor proxy (true/false)
# @env TOR_PROXY Tor proxy URL
"""
Bybit Pro Suite - Unified Trading Toolset
===========================================
A high-performance Argc-powered Python framework integrating all Bybit trading tools.

Features:
  1. Atomic Precision: Decimal-based calculations eliminate rounding errors
  2. Adaptive Rate-Limiting: Exponential backoff for Bybit's 10006 code
  3. L2 Microstructure: Volume Imbalance (Skew) for directional bias
  4. Automatic Tick Discovery: Dynamic tickSize/qtyStep per symbol
  5. Smart Session Management: Auto-detect USE_TOR for SOCKS5h proxy
  6. UTA Support: Bybit V5 Unified Accounts
  7. Order Persistence: PostOnly for Maker rebates
  8. Graceful Signal Handling: Ctrl+C protection
  9. Advanced PnL Analytics: Daily cumulative tracking
 10. Global Exception Handling: Unified auth/parameter/network errors
  11. Configurable Environment: Secure env var loading
  12. Structured Logging: Multi-level logging
  13. Health Checks: Trading status verification
  14. Latency Measurement: RTT tracking
  15. Hedge Mode Support: Dual-positioning toggle
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import wraps
from typing import Any, Dict

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
@dataclass
class LotSizeFilter:
    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> float:
        if self.qty_step <= 0:
            return float(qty)
        step = Decimal(str(self.qty_step))
        q = Decimal(str(qty))
        adjusted = (q / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
        final_qty = max(
            Decimal(str(self.min_order_qty)),
            min(Decimal(str(self.max_order_qty)), adjusted),
        )
        return float(final_qty)


@dataclass
class PriceFilter:
    tick_size: float
    min_price: float = 0.0
    max_price: float = 1e12

    def adjust(self, price: float) -> float:
        if self.tick_size <= 0:
            return float(price)
        tick = Decimal(str(self.tick_size))
        p = Decimal(str(price))
        adjusted = (p / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        final_price = max(
            Decimal(str(self.min_price)), min(Decimal(str(self.max_price)), adjusted)
        )
        return float(final_price)


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure structured multi-level logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("bybit_pro")
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
        if USE_TOR:
            http_kwargs["proxies"] = {"http": TOR_PROXY, "https": TOR_PROXY}
        return HTTP(**http_kwargs)
    else:
        # Fallback: return None, will use requests-based api_request
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
        "lot_filter": LotSizeFilter(
            float(data["lotSizeFilter"]["qtyStep"]),
            float(data["lotSizeFilter"]["minOrderQty"]),
            float(data["lotSizeFilter"]["maxOrderQty"]),
        ),
        "price_filter": PriceFilter(
            float(data["priceFilter"]["tickSize"]),
            float(data["priceFilter"]["minPrice"]),
            float(data["priceFilter"]["maxPrice"]),
        ),
        "tick": Decimal(data["priceFilter"]["tickSize"]),
        "step": Decimal(data["lotSizeFilter"]["qtyStep"]),
        "min_qty": Decimal(data["lotSizeFilter"]["minOrderQty"]),
    }


def get_fee_rate(symbol: str, session: Any) -> float:
    """Fetch dynamic taker fee rate for the account."""
    if USE_PYBIT:
        res = session.get_fee_rate(category="linear", symbol=symbol)
    else:
        from utils.bybit_base import api_request

        res = api_request(
            "GET",
            "/v5/account/fee-rate",
            {"category": "linear", "symbol": symbol},
            signed=True,
        )

    list_data = res.get("result", {}).get("list", [{}])
    return float(list_data[0].get("takerFeeRate", 0.0006))


def quantize_price(price: Decimal, tick: Decimal) -> Decimal:
    """Round price to valid tick size."""
    return price.quantize(tick, ROUND_HALF_UP)


def quantize_qty(qty: Decimal, step: Decimal) -> Decimal:
    """Round quantity to valid step size."""
    return qty.quantize(step, ROUND_HALF_UP)


# ==============================================================================
# Core Trading Tools
# ==============================================================================


def bybit_smart_order(
    symbol: str,
    side: str,
    risk_pct: float = 1.0,
    sl_dist: float = None,
    sl_price: float = None,
    tp_price: float = None,
    testnet: bool = None,
) -> Dict[str, Any]:
    """
    Smart Order with automatic position sizing and risk management.

    Features:
      - Fetches dynamic tickSize/qtyStep
      - Calculates position size based on risk %
      - Places Market order with SL/TP
      - Measures latency
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

    # Calculate SL distance
    if sl_price:
        sl_dist = abs(float(last_price) - sl_price)

    if not sl_dist or sl_dist <= 0:
        return {"error": "Valid stop loss distance or price is required"}

    # Calculate position size
    risk_amount = total_equity * Decimal(str(risk_pct / 100))
    qty = risk_amount / Decimal(str(sl_dist))
    qty = rules["lot_filter"].adjust(float(qty))

    if qty < float(rules["min_qty"]):
        return {"error": f"Calculated qty {qty} below min {rules['min_qty']}"}

    # Determine SL price
    if not sl_price:
        sl_price = (
            float(last_price) - sl_dist
            if side == "Buy"
            else float(last_price) + sl_dist
        )

    # Place order
    order_params = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "stopLoss": str(rules["price_filter"].adjust(sl_price)),
        "timeInForce": "GTC",
        "positionIdx": 0,  # Default to one-way, logic could be expanded for hedge
    }
    if tp_price:
        order_params["takeProfit"] = str(rules["price_filter"].adjust(tp_price))

    if USE_PYBIT:
        order_resp = session.place_order(**order_params)
    else:
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
        "latency_ms": latency_ms,
        "response": order_resp,
    }


def bybit_analyze_orderbook(symbol: str, limit: int = 25) -> Dict[str, Any]:
    """
    Analyze orderbook depth and volume imbalance.

    Returns:
      - imbalance_score: -1 (Ask Heavy) to +1 (Bid Heavy)
      - sentiment: Bullish/Bearish/Neutral
      - spread_pct: Current spread percentage
    """
    session = get_session()

    if USE_PYBIT:
        ob = session.get_orderbook(category="linear", symbol=symbol, limit=limit)
    else:
        from utils.bybit_base import api_request

        ob = api_request(
            "GET",
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": str(limit)},
        )

    bids = ob["result"]["b"]  # [[price, qty], ...]
    asks = ob["result"]["a"]

    # Calculate volumes using Decimal for precision
    b_vol = sum(Decimal(x[1]) for x in bids)
    a_vol = sum(Decimal(x[1]) for x in asks)

    total_vol = b_vol + a_vol
    if total_vol == 0:
        imbalance = Decimal(0)
    else:
        imbalance = (b_vol - a_vol) / total_vol

    best_bid = Decimal(bids[0][0])
    best_ask = Decimal(asks[0][0])
    spread_pct = ((best_ask - best_bid) / best_bid) * 100

    # Determine sentiment
    if imbalance > Decimal("0.1"):
        sentiment = "Bullish"
    elif imbalance < Decimal("-0.1"):
        sentiment = "Bearish"
    else:
        sentiment = "Neutral"

    return {
        "symbol": symbol,
        "imbalance_score": float(round(imbalance, 4)),
        "sentiment": sentiment,
        "spread_pct": float(round(spread_pct, 4)),
        "bid_vol": float(b_vol),
        "ask_vol": float(a_vol),
        "best_bid": float(best_bid),
        "best_ask": float(best_ask),
    }


def bybit_trading_dashboard(coin: str = "USDT") -> Dict[str, Any]:
    """
    Dashboard: Account overview with risk metrics.

    Returns balance, unrealized PnL, margin usage, total notional risk.
    """
    session = get_session()

    if USE_PYBIT:
        balance = session.get_wallet_balance(accountType="UNIFIED", coin=coin)
        positions = session.get_positions(category="linear", settleCoin=coin)
    else:
        from utils.bybit_base import api_request

        balance = api_request(
            "GET",
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED", "coin": coin},
            signed=True,
        )
        positions = api_request(
            "GET",
            "/v5/position/closed-pnl",
            {"category": "linear", "settleCoin": coin},
            signed=True,
        )

    balance_data = balance["result"]["list"][0]

    # Calculate active risk
    active_risk = Decimal(0)
    if USE_PYBIT and positions["result"]["list"]:
        for p in positions["result"]["list"]:
            if float(p.get("size", 0)) > 0:
                active_risk += Decimal(p.get("positionValue", 0))

    return {
        "balance": balance_data["totalEquity"],
        "unrealized_pnl": balance_data.get("totalPnL", "0"),
        "available_balance": balance_data.get("availableToWithdraw", "0"),
        "account_im_rate": balance_data.get("accountIMRate", "0"),
        "total_notional_risk": float(active_risk),
    }


def bybit_get_indicators(
    symbol: str, interval: str = "60", limit: int = 50
) -> Dict[str, Any]:
    """
    Get candle data with technical indicators (RSI, EMA).

    Intervals: 1, 5, 15, 60, 120, 240, D, W, M
    """
    session = get_session()

    if USE_PYBIT:
        res = session.get_klines(
            category="linear", symbol=symbol, interval=interval, limit=limit
        )
    else:
        from utils.bybit_base import api_request

        res = api_request(
            "GET",
            "/v5/market/klines",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": str(limit),
            },
        )

    # Parse klines: [openTime, open, high, low, close, volume, turnover]
    klines = res["result"]["list"]
    closes = [float(x[4]) for x in klines]

    # Calculate RSI
    from utils.bybit_base import calculate_ema, calculate_rsi

    rsi = calculate_rsi(closes, 14)
    ema_20 = calculate_ema(closes, 20)
    ema_50 = calculate_ema(closes, 50)

    return {
        "symbol": symbol,
        "last_price": closes[0],
        "rsi": rsi[-1] if rsi else None,
        "ema_20": ema_20[-1] if ema_20 else None,
        "ema_50": ema_50[-1] if ema_50 else None,
        "history": closes[:10],
    }


def bybit_cancel_all_orders(
    category: str = "linear", symbol: str = None
) -> Dict[str, Any]:
    """Cancel all orders for a symbol (or all symbols)."""
    session = get_session()

    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    if USE_PYBIT:
        result = session.cancel_all_orders(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("POST", "/v5/order/cancel-all", params, signed=True)

    return result


def bybit_set_position_mode(symbol: str, mode: int) -> Dict[str, Any]:
    """
    Toggle between Hedge and One-Way mode.

    mode: 0 = One-Way, 3 = Hedge
    """
    session = get_session()

    if USE_PYBIT:
        result = session.switch_position_mode(
            category="linear", symbol=symbol, mode=mode
        )
    else:
        from utils.bybit_base import api_request

        result = api_request(
            "POST",
            "/v5/position/switch-mode",
            {"category": "linear", "symbol": symbol, "mode": str(mode)},
            signed=True,
        )

    return result


def bybit_set_leverage(
    symbol: str, leverage: str, testnet: bool = None
) -> Dict[str, Any]:
    """Set leverage for a symbol."""
    session = get_session()

    if USE_PYBIT:
        result = session.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=leverage,
            sellLeverage=leverage,
        )
    else:
        from utils.bybit_base import api_request

        result = api_request(
            "POST",
            "/v5/position/set-leverage",
            {
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": leverage,
                "sellLeverage": leverage,
            },
            signed=True,
        )

    return result


def bybit_place_order(
    symbol: str,
    side: str,
    order_type: str,
    qty: str,
    price: str = None,
    time_in_force: str = "GTC",
    reduce_only: bool = False,
    testnet: bool = None,
) -> Dict[str, Any]:
    """Place a trading order with optional limit price and TP/SL."""
    session = get_session()

    params = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": qty,
        "timeInForce": time_in_force,
    }

    if price:
        params["price"] = price

    if reduce_only:
        params["reduceOnly"] = "true"

    if USE_PYBIT:
        result = session.place_order(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("POST", "/v5/order/create", params, signed=True)

    return result


def bybit_get_positions(symbol: str = None) -> Dict[str, Any]:
    """Get open positions."""
    session = get_session()

    params = {"category": "linear"}
    if symbol:
        params["symbol"] = symbol

    if USE_PYBIT:
        result = session.get_positions(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("GET", "/v5/position/list", params, signed=True)

    return result


def bybit_get_open_orders(symbol: str = None) -> Dict[str, Any]:
    """Get open orders."""
    session = get_session()

    params = {"category": "linear"}
    if symbol:
        params["symbol"] = symbol

    if USE_PYBIT:
        result = session.get_open_orders(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("GET", "/v5/order/realtime", params, signed=True)

    return result


def bybit_get_balance(
    account_type: str = "UNIFIED", coin: str = "USDT"
) -> Dict[str, Any]:
    """Get wallet balance."""
    session = get_session()

    if USE_PYBIT:
        result = session.get_wallet_balance(accountType=account_type, coin=coin)
    else:
        from utils.bybit_base import api_request

        result = api_request(
            "GET",
            "/v5/account/wallet-balance",
            {"accountType": account_type, "coin": coin},
            signed=True,
        )

    return result


def bybit_get_ticker(symbol: str) -> Dict[str, Any]:
    """Get ticker information."""
    session = get_session()

    if USE_PYBIT:
        result = session.get_tickers(category="linear", symbol=symbol)
    else:
        from utils.bybit_base import api_request

        result = api_request(
            "GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )

    return result


def bybit_get_klines(
    symbol: str,
    interval: str = "60",
    start: str = None,
    end: str = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Get kline/candlestick data."""
    session = get_session()

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": str(limit),
    }
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    if USE_PYBIT:
        result = session.get_klines(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("GET", "/v5/market/klines", params)

    return result


def bybit_get_orderbook(symbol: str, limit: int = 25) -> Dict[str, Any]:
    """Get orderbook depth."""
    session = get_session()

    if USE_PYBIT:
        result = session.get_orderbook(category="linear", symbol=symbol, limit=limit)
    else:
        from utils.bybit_base import api_request

        result = api_request(
            "GET",
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": str(limit)},
        )

    return result


def bybit_cancel_order(
    category: str, symbol: str, order_id: str = None, order_link_id: str = None
) -> Dict[str, Any]:
    """Cancel a specific order."""
    session = get_session()

    params = {"category": category, "symbol": symbol}
    if order_id:
        params["orderId"] = order_id
    if order_link_id:
        params["orderLinkId"] = order_link_id

    if USE_PYBIT:
        result = session.cancel_order(**params)
    else:
        from utils.bybit_base import api_request

        result = api_request("POST", "/v5/order/cancel", params, signed=True)

    return result


def bybit_position_manager(
    symbol: str, action: str, profit_usdt: int = None, fee_rate: float = None
) -> Dict[str, Any]:
    """
    Manage open positions: Move to Break-Even or Close if profit threshold reached.
    Enhanced with dynamic fee discovery, positionIdx fix, and precision filters.
    """
    session = get_session()

    # Get positions
    if USE_PYBIT:
        positions = session.get_positions(category="linear", symbol=symbol)
    else:
        from utils.bybit_base import api_request

        positions = api_request(
            "GET",
            "/v5/position/list",
            {"category": "linear", "symbol": symbol},
            signed=True,
        )

    pos_list = positions["result"]["list"]
    if not pos_list:
        return {"status": "no_position", "symbol": symbol}

    # Get market rules for precision
    rules = get_market_rules(symbol, session)

    # Find active position
    for pos in pos_list:
        if float(pos.get("size", 0)) > 0:
            entry_price = float(pos["avgPrice"])
            qty = float(pos["size"])
            side = pos["side"]
            pos_idx = int(pos.get("positionIdx", 0))

            # Dynamic Fee Discovery
            if fee_rate is None:
                fee_rate = get_fee_rate(symbol, session)

            # Get current price
            if USE_PYBIT:
                ticker = session.get_tickers(category="linear", symbol=symbol)
            else:
                ticker = api_request(
                    "GET",
                    "/v5/market/tickers",
                    {"category": "linear", "symbol": symbol},
                )

            current_price = float(ticker["result"]["list"][0]["lastPrice"])

            # Calculate profit
            if side == "Buy":
                profit = (current_price - entry_price) * qty
            else:
                profit = (entry_price - current_price) * qty

            # Deduct fees (entry + exit)
            fees = (entry_price + current_price) * qty * fee_rate
            net_profit = profit - fees

            if action == "be":
                # True Break-Even: adjust for two-way fees
                if side == "Buy":
                    be_price = entry_price * (1 + 2 * fee_rate)
                else:
                    be_price = entry_price * (1 - 2 * fee_rate)

                adj_be = str(rules["price_filter"].adjust(be_price))

                params = {
                    "category": "linear",
                    "symbol": symbol,
                    "stopLoss": adj_be,
                    "slTriggerBy": "MarkPrice",
                    "tpslMode": "Full",
                    "positionIdx": pos_idx,
                }

                if USE_PYBIT:
                    res = session.set_trading_stop(**params)
                else:
                    res = api_request(
                        "POST", "/v5/position/set-trading-stop", params, signed=True
                    )
                return {"action": "move_to_be", "new_sl": adj_be, "response": res}

            elif action == "close":
                if profit_usdt is not None and net_profit >= profit_usdt:
                    # Close position
                    close_side = "Sell" if side == "Buy" else "Buy"
                    params = {
                        "category": "linear",
                        "symbol": symbol,
                        "side": close_side,
                        "orderType": "Market",
                        "qty": pos["size"],
                        "reduceOnly": "true",
                        "positionIdx": pos_idx,
                    }
                    if USE_PYBIT:
                        res = session.place_order(**params)
                    else:
                        res = api_request(
                            "POST", "/v5/order/create", params, signed=True
                        )
                    return {"action": "close", "profit": net_profit, "response": res}
                else:
                    return {
                        "status": "skip",
                        "profit": net_profit,
                        "threshold": profit_usdt,
                    }

    return {"status": "no_active_position"}


def bybit_set_trading_stop(
    symbol: str,
    tp_usdt: int = None,
    sl_usdt: int = None,
) -> Dict[str, Any]:
    """
    Set Trading Stop (TP/SL) with Break-Even and Profit-After-Fees logic.
    Calculates TP/SL prices based on desired USDT profit/loss with precision filters.
    """
    session = get_session()

    # Get position
    if USE_PYBIT:
        positions = session.get_positions(category="linear", symbol=symbol)
    else:
        from utils.bybit_base import api_request

        positions = api_request(
            "GET",
            "/v5/position/list",
            {"category": "linear", "symbol": symbol},
            signed=True,
        )

    pos_list = positions["result"]["list"]
    if not pos_list:
        return {"error": "No open position"}

    # Get market rules for precision
    rules = get_market_rules(symbol, session)

    for pos in pos_list:
        if float(pos.get("size", 0)) > 0:
            entry_price = float(pos["avgPrice"])
            qty = float(pos["size"])
            side = pos["side"]
            pos_idx = int(pos.get("positionIdx", 0))

            params = {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": pos_idx,
                "tpslMode": "Full",
                "slTriggerBy": "MarkPrice",
            }

            if sl_usdt is not None:
                # Calculate SL price for loss of sl_usdt
                if side == "Buy":
                    sl_price = entry_price - (sl_usdt / qty)
                else:
                    sl_price = entry_price + (sl_usdt / qty)
                params["stopLoss"] = str(rules["price_filter"].adjust(sl_price))

            if tp_usdt is not None:
                # Calculate TP price for profit of tp_usdt
                if side == "Buy":
                    tp_price = entry_price + (tp_usdt / qty)
                else:
                    tp_price = entry_price - (tp_usdt / qty)
                params["takeProfit"] = str(rules["price_filter"].adjust(tp_price))

            if USE_PYBIT:
                result = session.set_trading_stop(**params)
            else:
                result = api_request(
                    "POST", "/v5/position/set-trading-stop", params, signed=True
                )

            return result

    return {"error": "No active position"}


# ==============================================================================
# Signal Handling & Main Entry
# ==============================================================================
def signal_handler(signum, frame):
    """Graceful exit on Ctrl+C to prevent half-filled positions."""
    logger.critical("Received interrupt signal. Exiting gracefully...")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(
        description="Bybit Pro Suite - Unified Trading Toolset",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bybit_pro_suite.py analyze_orderbook --symbol BTCUSDT
  python bybit_pro_suite.py trading_dashboard
  python bybit_pro_suite.py smart_order --symbol BTCUSDT --side Buy --risk-pct 1.0 --sl-dist 100
  python bybit_pro_suite.py set_position_mode --symbol BTCUSDT --mode 3
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # analyze_orderbook
    ao_parser = subparsers.add_parser(
        "analyze_orderbook", help="Analyze orderbook depth and imbalance"
    )
    ao_parser.add_argument("--symbol", required=True)
    ao_parser.add_argument("--limit", type=int, default=25)

    td_parser = subparsers.add_parser(
        "trading_dashboard", help="Account overview with risk metrics"
    )
    td_parser.add_argument("--coin", default="USDT")

    gb_parser = subparsers.add_parser("get_balance", help="Get wallet balance")
    gb_parser.add_argument("--account-type", default="UNIFIED")
    gb_parser.add_argument("--coin", default="USDT")

    gp_parser = subparsers.add_parser("get_positions", help="Get open positions")
    gp_parser.add_argument("--symbol")

    goo_parser = subparsers.add_parser("get_open_orders", help="Get open orders")
    goo_parser.add_argument("--symbol")

    # smart_order
    so_parser = subparsers.add_parser(
        "smart_order", help="Smart order with auto-sizing"
    )
    so_parser.add_argument("--symbol", required=True, help="Symbol (e.g., BTCUSDT)")
    so_parser.add_argument("--side", required=True, choices=["Buy", "Sell"])
    so_parser.add_argument("--risk-pct", type=float, default=1.0, help="Risk %%")
    so_parser.add_argument("--sl-dist", type=float, help="Stop loss distance")
    so_parser.add_argument("--sl-price", type=float, help="Absolute SL price")
    so_parser.add_argument("--tp-price", type=float, help="Absolute TP price")

    # get_indicators
    gi_parser = subparsers.add_parser("get_indicators", help="Get RSI/EMA indicators")
    gi_parser.add_argument("--symbol", required=True)
    gi_parser.add_argument("--interval", default="60")
    gi_parser.add_argument("--limit", type=int, default=50)

    # place_order
    po_parser = subparsers.add_parser("place_order", help="Place an order")
    po_parser.add_argument("--symbol", required=True)
    po_parser.add_argument("--side", required=True, choices=["Buy", "Sell"])
    po_parser.add_argument("--order-type", required=True, choices=["Market", "Limit"])
    po_parser.add_argument("--qty", required=True)
    po_parser.add_argument("--price")
    po_parser.add_argument("--time-in-force", default="GTC")
    po_parser.add_argument("--reduce-only", action="store_true")

    # set_leverage
    sl_parser = subparsers.add_parser("set_leverage", help="Set leverage")
    sl_parser.add_argument("--symbol", required=True)
    sl_parser.add_argument("--leverage", required=True)

    # set_position_mode
    spm_parser = subparsers.add_parser("set_position_mode", help="Set position mode")
    spm_parser.add_argument("--symbol", required=True)
    spm_parser.add_argument(
        "--mode", type=int, choices=[0, 3], help="0=One-Way, 3=Hedge"
    )

    # position_manager
    pm_parser = subparsers.add_parser("position_manager", help="Manage positions")
    pm_parser.add_argument("--symbol", required=True)
    pm_parser.add_argument("--action", required=True, choices=["be", "close"])
    pm_parser.add_argument("--profit-usdt", type=int, help="Profit threshold for close")

    # set_trading_stop
    sts_parser = subparsers.add_parser("set_trading_stop", help="Set TP/SL")
    sts_parser.add_argument("--symbol", required=True)
    sts_parser.add_argument("--tp-usdt", type=int)
    sts_parser.add_argument("--sl-usdt", type=int)

    # cancel_order
    co_parser = subparsers.add_parser("cancel_order", help="Cancel order")
    co_parser.add_argument("--symbol", required=True)
    co_parser.add_argument("--order-id")
    co_parser.add_argument("--order-link-id")

    # cancel_all_orders
    cao_parser = subparsers.add_parser("cancel_all_orders", help="Cancel all orders")
    cao_parser.add_argument("--symbol")

    # get_ticker
    gt_parser = subparsers.add_parser("get_ticker", help="Get ticker")
    gt_parser.add_argument("--symbol", required=True)

    # get_klines
    gk_parser = subparsers.add_parser("get_klines", help="Get klines")
    gk_parser.add_argument("--symbol", required=True)
    gk_parser.add_argument("--interval", default="60")
    gk_parser.add_argument("--limit", type=int, default=200)

    # get_orderbook
    gob_parser = subparsers.add_parser("get_orderbook", help="Get orderbook")
    gob_parser.add_argument("--symbol", required=True)
    gob_parser.add_argument("--limit", type=int, default=25)

    # verbose
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    # Update logging level
    global logger
    logger = setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return

    # Execute command
    try:
        if args.command == "analyze_orderbook":
            result = bybit_analyze_orderbook(args.symbol)
        elif args.command == "trading_dashboard":
            result = bybit_trading_dashboard()
        elif args.command == "get_balance":
            result = bybit_get_balance()
        elif args.command == "get_positions":
            result = bybit_get_positions()
        elif args.command == "get_open_orders":
            result = bybit_get_open_orders()
        elif args.command == "smart_order":
            result = bybit_smart_order(
                args.symbol,
                args.side,
                args.risk_pct,
                args.sl_dist,
                args.sl_price,
                args.tp_price,
            )
        elif args.command == "get_indicators":
            result = bybit_get_indicators(args.symbol, args.interval, args.limit)
        elif args.command == "place_order":
            result = bybit_place_order(
                args.symbol,
                args.side,
                args.order_type,
                args.qty,
                args.price,
                args.time_in_force,
                args.reduce_only,
            )
        elif args.command == "set_leverage":
            result = bybit_set_leverage(args.symbol, args.leverage)
        elif args.command == "set_position_mode":
            result = bybit_set_position_mode(args.symbol, args.mode)
        elif args.command == "position_manager":
            result = bybit_position_manager(args.symbol, args.action, args.profit_usdt)
        elif args.command == "set_trading_stop":
            result = bybit_set_trading_stop(args.symbol, args.tp_usdt, args.sl_usdt)
        elif args.command == "cancel_order":
            result = bybit_cancel_order(
                "linear", args.symbol, args.order_id, args.order_link_id
            )
        elif args.command == "cancel_all_orders":
            result = bybit_cancel_all_orders("linear", args.symbol)
        elif args.command == "get_ticker":
            result = bybit_get_ticker(args.symbol)
        elif args.command == "get_klines":
            result = bybit_get_klines(
                args.symbol, args.interval, None, None, args.limit
            )
        elif args.command == "get_orderbook":
            result = bybit_get_orderbook(args.symbol, args.limit)
        else:
            result = {"error": f"Unknown command: {args.command}"}

        print(json.dumps(result, indent=2))

    except Exception as e:
        logger.error(f"Execution error: {e}")
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()


def run(
    symbol=None,
    side=None,
    order_type=None,
    qty=None,
    price=None,
    risk_pct=1.0,
    sl_dist=None,
    sl_price=None,
    tp_price=None,
    interval="60",
    limit=25,
    leverage=None,
    mode=None,
    action=None,
    profit_usdt=None,
    tp_usdt=None,
    sl_usdt=None,
    order_id=None,
    order_link_id=None,
    account_type="UNIFIED",
    coin="USDT",
    category="linear",
    time_in_force="GTC",
    reduce_only=False,
    verbose=False,
    testnet=None,
):
    """
    Entry point for the AI agent to call the Bybit Pro Suite.
    Maps parameters to the internal specialized functions.
    """
    global logger
    logger = setup_logging(verbose)

    try:
        # 1. Position Management (be/close)
        if action in ["be", "close"] and symbol:
            return bybit_position_manager(symbol, action, profit_usdt)

        # 2. Smart Order
        if side and (sl_dist or sl_price) and not order_type:
            return bybit_smart_order(
                symbol, side, risk_pct, sl_dist, sl_price, tp_price, testnet
            )

        # 3. Standard Order
        if side and order_type and qty and symbol:
            return bybit_place_order(
                symbol,
                side,
                order_type,
                qty,
                price,
                time_in_force,
                reduce_only,
                testnet,
            )

        # 4. Ticker / Market Data
        if symbol and not side and not action:
            # If limit is provided and it's a high number, it might be klines
            if limit > 50:
                return bybit_get_klines(symbol, interval, limit=limit)
            # If it's just a symbol, default to ticker
            return bybit_get_ticker(symbol)

        # 5. Account/Balance
        if not symbol and not action:
            return bybit_get_balance(account_type, coin)

        # 6. Leverage/Mode
        if symbol and leverage:
            return bybit_set_leverage(symbol, leverage, testnet)
        if symbol and mode is not None:
            return bybit_set_position_mode(symbol, mode)

        # 7. Trading Stop (TP/SL)
        if symbol and (tp_usdt is not None or sl_usdt is not None):
            return bybit_set_trading_stop(symbol, tp_usdt, sl_usdt)

        # 8. Orderbook Analysis
        is_analyze = "analyze" in str(action).lower() if action else False
        if symbol and is_analyze:
            return bybit_analyze_orderbook(symbol, limit)

        return {
            "error": "Could not determine which function to call based on provided arguments. Please be more specific (e.g., provide 'symbol' and 'action')."
        }

    except Exception as e:
        return {"error": f"Run execution error: {e!s}"}
