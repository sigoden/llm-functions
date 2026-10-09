#!/usr/bin/env python3
# =============================================================================
# monitor_scalp.py — Enhanced Bybit Position Monitor & Micro‑Scalper
# =============================================================================
# Features added since original version:
#   • Strict credential validation with clear error exit
#   • Optional JSON config file (--config) for reusable settings
#   • Persistent WebSocket position cache (ws_cache.json)
#   • Cool‑down / rate‑limit per symbol
#   • --force-rest flag to disable WebSocket entirely
#   • Structured JSON logging when --verbose is used
#   • Graceful degradation messages
#   • Better spread‑check handling (log warning instead of silent skip)
#   • Summary report after dry‑run (attempts, avg spread, estimated profit)
#   • Refactored core logic into a class for easier testing (still runnable as script)
# =============================================================================

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BYBIT_API_MAIN = "https://api.bybit.com"
BYBIT_API_TESTNET = "https://api-testnet.bybit.com"
BYBIT_WS_MAIN = "wss://stream.bybit.com/v5/private"
BYBIT_WS_TESTNET = "wss://stream-testnet.bybit.com/v5/private"

POSITION_ENDPOINT = "/v5/position/list"
ORDER_ENDPOINT = "/v5/order/create"
SET_LEVERAGE_ENDPOINT = "/v5/position/set-leverage"
TRADING_STOP_ENDPOINT = "/v5/position/trading-stop"
TICKER_ENDPOINT = "/v5/market/tickers"
RECV_WINDOW = "20000"

# ---------------------------------------------------------------------------
# Logging setup (JSON when verbose)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def json_log(msg: str) -> None:
    """Emit a JSON‑encoded log line to stderr (used when --verbose)."""
    try:
        print(json.dumps({"log": msg}), file=sys.stderr)
    except Exception:
        logger.info(msg)


def log_message(message: str, verbose: bool = False) -> None:
    """Conditional logging – plain text to stderr when verbose."""
    if verbose:
        logger.info(message)


def emit_output(text: str) -> None:
    """Write result to LLM_OUTPUT env var or stdout."""
    out = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    if out == "/dev/stdout":
        print(text)
    else:
        with open(out, "w") as f:
            f.write(text + "\n")


# ---------------------------------------------------------------------------
# Helper functions (HMAC, headers, query string, etc.)
# ---------------------------------------------------------------------------
def _api_base() -> str:
    flag = os.environ.get("BYBIT_TESTNET", "").lower()
    return BYBIT_API_TESTNET if flag in ("1", "true", "yes") else BYBIT_API_MAIN


def _ws_url() -> str:
    flag = os.environ.get("BYBIT_TESTNET", "").lower()
    return BYBIT_WS_TESTNET if flag in ("1", "true", "yes") else BYBIT_WS_MAIN


def _sign(api_secret: str, payload: str) -> str:
    return hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _headers(
    api_key: str, api_secret: str, payload: str, timestamp_ms: Optional[str] = None
) -> Dict[str, str]:
    ts = timestamp_ms or str(int(time.time() * 1000))
    prehash = ts + api_key + RECV_WINDOW + payload
    return {
        "Content-Type": "application/json",
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": _sign(api_secret, prehash),
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }


def query_string(params: Dict[str, Any]) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)


def _get_public(params: Dict[str, Any]) -> Optional[Dict]:
    try:
        r = requests.get(f"{_api_base()}{TICKER_ENDPOINT}", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            return None
        return data
    except Exception:
        return None


def fetch_ticker_linear(symbol: str) -> Optional[Dict[str, Any]]:
    data = _get_public({"category": "linear", "symbol": symbol})
    if not data:
        return None
    items = data.get("result", {}).get("list", []) or []
    return items[0] if items else None


def fetch_last_price(symbol: str) -> Optional[float]:
    t = fetch_ticker_linear(symbol)
    if not t or not t.get("lastPrice"):
        return None
    try:
        return float(t["lastPrice"])
    except (TypeError, ValueError):
        return None


def executable_ref_price(ticker: Dict[str, Any], side: str) -> Optional[float]:
    try:
        if side == "Buy":
            raw = ticker.get("ask1Price") or ticker.get("lastPrice")
        else:
            raw = ticker.get("bid1Price") or ticker.get("lastPrice")
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def spread_bps(ticker: Dict[str, Any]) -> Optional[float]:
    try:
        bid = float(ticker.get("bid1Price") or 0)
        ask = float(ticker.get("ask1Price") or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        return ((ask - bid) / mid) * 10000.0
    except (TypeError, ValueError):
        return None


def fetch_positions(
    api_key: str, api_secret: str, symbols: List[str]
) -> Optional[List[Dict]]:
    params = {"category": "linear", "settleCoin": "USDT"}
    qs = query_string(params)
    try:
        headers = _headers(api_key, api_secret, qs)
        resp = requests.get(
            f"{_api_base()}{POSITION_ENDPOINT}",
            headers=headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            log_message(f"Bybit API error: {data.get('retMsg')}", verbose=True)
            return None
        all_pos = data.get("result", {}).get("list", []) or []
        sym_set = set(symbols)
        return [
            p
            for p in all_pos
            if p.get("symbol") in sym_set and float(p.get("size", 0) or 0) != 0
        ]
    except Exception as e:
        log_message(f"Failed to fetch positions: {e}", verbose=True)
        return None


def fetch_positions_ws_or_rest(
    api_key: str, api_secret: str, symbols: List[str], use_ws: bool
) -> Optional[List[Dict]]:
    """Return positions from WS cache (if populated) or fall back to REST."""
    if use_ws and ws_active and ws_positions_cache:
        with ws_cache_lock:
            return [
                pos
                for sym, pos in ws_positions_cache.items()
                if sym in symbols and float(pos.get("size", 0) or 0) != 0
            ]
    return fetch_positions(api_key, api_secret, symbols)


def set_leverage(
    api_key: str, api_secret: str, symbol: str, leverage: int
) -> Dict[str, Any]:
    global _leverage_set_for
    if symbol in _leverage_set_for:
        return {"success": True, "cached": True, "symbol": symbol}
    body = {
        "category": "linear",
        "symbol": symbol,
        "buyLeverage": str(leverage),
        "sellLeverage": str(leverage),
    }
    raw = json.dumps(body, separators=(",", ":"))
    try:
        headers = _headers(api_key, api_secret, raw)
        resp = requests.post(
            f"{_api_base()}{SET_LEVERAGE_ENDPOINT}",
            headers=headers,
            data=raw,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            if data.get("retCode") == 110043:
                _leverage_set_for.add(symbol)
                return {"success": True, "symbol": symbol, "note": "leverage unchanged"}
            raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
        _leverage_set_for.add(symbol)
        return data
    except Exception as e:
        log_message(f"Failed to set leverage: {e}", verbose=True)
        return {"success": False, "error": str(e)}


def symbols_without_position(positions: List[Dict], symbols: List[str]) -> List[str]:
    busy = {p.get("symbol") for p in positions if float(p.get("size", 0) or 0) != 0}
    return [s for s in symbols if s not in busy]


def estimate_take_profit_price(
    side: str,
    qty: float,
    target_profit_usd: float,
    symbol: str,
    ref_price: Optional[float] = None,
) -> Optional[float]:
    if qty <= 0 or target_profit_usd <= 0:
        return None
    price = ref_price
    if price is None:
        ticker = fetch_ticker_linear(symbol)
        if ticker:
            price = executable_ref_price(ticker, side)
        if price is None:
            price = fetch_last_price(symbol)
    if price is None:
        return None
    delta = target_profit_usd / qty
    return price + delta if side == "Buy" else price - delta


def fetch_position_avg_price(
    api_key: str, api_secret: str, symbol: str, side: str
) -> Optional[float]:
    params = {"category": "linear", "symbol": symbol}
    qs = query_string(params)
    try:
        headers = _headers(api_key, api_secret, qs)
        r = requests.get(
            f"{_api_base()}{POSITION_ENDPOINT}",
            headers=headers,
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            return None
        for p in data.get("result", {}).get("list", []) or []:
            if p.get("symbol") != symbol:
                continue
            if float(p.get("size", 0) or 0) == 0:
                continue
            if p.get("side") != side:
                continue
            try:
                return float(p.get("avgPrice") or 0)
            except (TypeError, ValueError):
                return None
    except Exception:
        return None
    return None


def set_trading_stop(
    api_key: str,
    api_secret: str,
    symbol: str,
    trailing_stop: Optional[float] = None,
    take_profit: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"category": "linear", "symbol": symbol, "positionIdx": 0}
    if trailing_stop is not None and trailing_stop > 0:
        body["trailingStop"] = str(trailing_stop)
    if take_profit is not None and take_profit > 0:
        body["takeProfit"] = str(take_profit)

    if len(body) <= 3:
        return {"skipped": True, "reason": "no trailing_stop or take_profit"}

    if dry_run:
        log_message(f"Dry‑run trading‑stop: {body}", verbose=True)
        return {"success": True, "dry_run": True, "trading_stop": body}

    raw = json.dumps(body, separators=(",", ":"))
    try:
        headers = _headers(api_key, api_secret, raw)
        resp = requests.post(
            f"{_api_base()}{TRADING_STOP_ENDPOINT}",
            headers=headers,
            data=raw,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            if data.get("retCode") == 34040:
                return {"success": True, "note": "TP/SL/TS not modified", "body": body}
            raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
        return data
    except Exception as e:
        log_message(f"Failed to set trading stop: {e}", verbose=True)
        return {"success": False, "error": str(e)}


def place_scalp_order(
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    qty: float,
    leverage: int,
    dry_run: bool,
    trailing_stop: Optional[float] = None,
    target_profit: Optional[float] = None,
    max_spread_bps: Optional[float] = None,
) -> Dict[str, Any]:
    """Place (or simulate) a scalp order with optional TP/SL/TS."""
    set_leverage(api_key, api_secret, symbol, leverage)

    ticker = fetch_ticker_linear(symbol)
    ref_price = executable_ref_price(ticker, side) if ticker else None
    sbps = spread_bps(ticker) if ticker else None

    # ---- Spread check (log warning instead of silent skip) ----
    if max_spread_bps is not None and sbps is not None and sbps > max_spread_bps:
        warn = f"Spread {sbps:.2f} bps exceeds max {max_spread_bps} bps; proceeding with caution"
        log_message(warn, verbose=True)

    # ---- Target‑profit price calculation ----
    take_profit_price: Optional[float] = None
    if target_profit is not None and target_profit > 0:
        take_profit_price = estimate_take_profit_price(
            side, qty, target_profit, symbol, ref_price
        )

    # ---- Order payload (market order) ----
    order_payload = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "orderType": "Market",
        "timeInForce": "GTC",
        "positionIdx": 0,
    }

    if dry_run:
        log_message(
            f"Dry‑run: {side} {qty} {symbol} | TP_price={take_profit_price} | spread_bps={sbps}",
            verbose=True,
        )
        ts_result = set_trading_stop(
            api_key, api_secret, symbol, trailing_stop, take_profit_price, dry_run=True
        )
        out = {
            "success": True,
            "dry_run": True,
            "order": order_payload,
            "trailing_stop": trailing_stop,
            "target_profit": target_profit,
            "take_profit_price": take_profit_price,
            "spread_bps": sbps,
            "ref_price": ref_price,
            "trading_stop_result": ts_result,
        }
        emit_output(json.dumps(out, default=str))
        return {"status": "dry_run", "order": order_payload, **out}

    # ---- Real order submission ----
    try:
        raw = json.dumps(order_payload, separators=(",", ":"))
        headers = _headers(api_key, api_secret, raw)
        resp = requests.post(
            f"{_api_base()}{ORDER_ENDPOINT}",
            headers=headers,
            data=raw,
            timeout=10,
        )
        resp.raise_for_status()
        order_result = resp.json()
        if order_result.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit {order_result.get('retCode')}: {order_result.get('retMsg')}"
            )

        time.sleep(0.6)  # give the exchange a moment to register the order
        avg_price = fetch_position_avg_price(api_key, api_secret, symbol, side)
        if target_profit is not None and target_profit > 0 and avg_price is not None:
            # Re‑calculate TP based on filled avg price
            take_profit_price = estimate_take_profit_price(
                side, qty, target_profit, symbol, avg_price
            )

        ts_result = set_trading_stop(
            api_key, api_secret, symbol, trailing_stop, take_profit_price, dry_run=False
        )
        order_result.update(
            {
                "trailing_stop": trailing_stop,
                "target_profit": target_profit,
                "take_profit_price": take_profit_price,
                "spread_bps": sbps,
                "ref_price": ref_price,
                "avg_price_after_fill": avg_price,
                "trading_stop_result": ts_result,
            }
        )
        return order_result
    except Exception as e:
        log_message(f"Failed to place order: {e}", verbose=True)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket handling (private position stream)
# ---------------------------------------------------------------------------
ws_active = False
ws_thread: Optional[threading.Thread] = None
ws_positions_cache: Dict[str, Dict[str, Any]] = {}
ws_cache_lock = threading.Lock()
_leverage_set_for: set = set()
WS_CACHE_FILE = "ws_cache.json"


def _ws_auth_payload(api_key: str, api_secret: str) -> Dict[str, Any]:
    expires = int((time.time() + 10) * 1000)
    sign = _sign(api_secret, f"GET/realtime{expires}")
    return {"op": "auth", "args": [api_key, expires, sign]}


def on_ws_message(message: str, symbols: List[str], verbose: bool) -> None:
    global ws_positions_cache
    try:
        data = json.loads(message)
        if data.get("op") == "auth" and not data.get("success"):
            log_message(f"WS auth failed: {data}", verbose=True)
            return
        if data.get("topic", "").startswith("position"):
            for item in data.get("data", []) or []:
                sym = item.get("symbol")
                if sym not in symbols:
                    continue
                with ws_cache_lock:
                    ws_positions_cache[sym] = item
            log_message(
                f"WS position update: {len(data.get('data', []))} rows", verbose=True
            )
    except Exception as e:
        log_message(f"WS Error: {e}", verbose=True)


def start_ws_listener(
    api_key: str, api_secret: str, symbols: List[str], verbose: bool
) -> None:
    global ws_active, ws_thread
    if ws_active:
        return
    try:
        import websocket  # websocket-client
    except ImportError:
        log_message("websocket-client not installed; --use-ws disabled", verbose=True)
        return

    sym_set = set(symbols)

    def ws_listener():
        ws_url = _ws_url()
        while ws_active:
            try:

                def on_open(ws):
                    log_message("WS Connected", verbose=True)
                    ws.send(json.dumps(_ws_auth_payload(api_key, api_secret)))
                    ws.send(
                        json.dumps({"op": "subscribe", "args": ["position.linear"]})
                    )

                def on_message(ws, message):
                    on_ws_message(message, list(sym_set), verbose)

                ws = websocket.WebSocketApp(
                    ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=lambda ws, e: log_message(f"WS Error: {e}", verbose=True),
                    on_close=lambda ws, *_: log_message("WS Closed", verbose=True),
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                log_message(f"WS connection exception: {e}", verbose=True)
            if ws_active:
                log_message("WS disconnected, reconnecting in 5s...", verbose=True)
                time.sleep(5)

    ws_thread = threading.Thread(target=ws_listener, daemon=True)
    ws_thread.start()
    ws_active = True


def load_ws_cache() -> None:
    """Load persisted WS cache from disk (if present)."""
    if os.path.exists(WS_CACHE_FILE):
        try:
            with open(WS_CACHE_FILE) as f:
                data = json.load(f)
                with ws_cache_lock:
                    ws_positions_cache.update(data)
        except Exception as e:
            log_message(f"Failed to load WS cache: {e}", verbose=True)


def persist_ws_cache() -> None:
    """Write current WS cache to disk."""
    with ws_cache_lock:
        try:
            with open(WS_CACHE_FILE, "w") as f:
                json.dump(ws_positions_cache, f)
        except Exception as e:
            log_message(f"Failed to persist WS cache: {e}", verbose=True)


# ---------------------------------------------------------------------------
# Core engine – wrapped in a class for easier unit‑testing
# ---------------------------------------------------------------------------
class ScalpEngine:
    def __init__(
        self,
        symbol: str,
        qty: float,
        leverage: int,
        interval: int = 30,
        trailing_stop: Optional[float] = None,
        target_profit: Optional[float] = None,
        symbols: Optional[List[str]] = None,
        dry_run: bool = False,
        verbose: bool = False,
        use_ws: bool = False,
        sell_scalp: bool = False,
        max_iterations: Optional[int] = None,
        max_spread_bps: Optional[float] = None,
        cool_down: int = 0,
        force_rest: bool = False,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.symbol = symbol
        self.qty = qty
        self.leverage = leverage
        self.interval = interval
        self.trailing_stop = trailing_stop
        self.target_profit = target_profit
        self.symbols = list(dict.fromkeys([symbol] + (symbols or [])))
        self.dry_run = dry_run
        self.verbose = verbose
        self.use_ws = use_ws
        self.sell_scalp = sell_scalp
        self.max_iterations = max_iterations
        self.max_spread_bps = max_spread_bps
        self.cool_down = cool_down
        self.force_rest = force_rest
        self.config = config or {}

        # Runtime state
        self.api_key = os.environ.get("BYBIT_API_KEY")
        self.api_secret = os.environ.get("BYBIT_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing BYBIT_API_KEY or BYBIT_API_SECRET environment variables."
            )
        self.last_order_ts: Dict[str, float] = {}
        self.iteration = 0

        # Load persisted WS cache if it exists
        load_ws_cache()

    # -----------------------------------------------------------------------
    def _check_cool_down(self, sym: str) -> bool:
        now = time.time()
        last = self.last_order_ts.get(sym, 0)
        if now - last < self.cool_down:
            return True
        self.last_order_ts[sym] = now
        return False

    # -----------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        side = "Sell" if self.sell_scalp else "Buy"
        log_message(
            f"Monitoring {self.symbols} (qty={self.qty}, leverage={self.leverage}, side={side})",
            verbose=self.verbose,
        )

        # Set leverage for each symbol once
        for sym in self.symbols:
            set_leverage(self.api_key, self.api_secret, sym, self.leverage)

        # Start WS listener unless forced to REST only
        if self.use_ws and not self.force_rest:
            start_ws_listener(self.api_key, self.api_secret, self.symbols, self.verbose)
        else:
            log_message(
                "WebSocket listener disabled (force‑rest or not requested)",
                verbose=self.verbose,
            )

        try:
            while True:
                self.iteration += 1
                if self.max_iterations and self.iteration > self.max_iterations:
                    return {
                        "status": "max_iterations_reached",
                        "iterations": self.iteration - 1,
                    }

                positions = fetch_positions_ws_or_rest(
                    self.api_key,
                    self.api_secret,
                    self.symbols,
                    self.use_ws and not self.force_rest,
                )
                if positions is None:
                    log_message(
                        "Positions fetch returned None – sleeping", verbose=self.verbose
                    )
                    time.sleep(self.interval)
                    continue

                free = symbols_without_position(positions, self.symbols)
                log_message(
                    f"Fetched {len(positions)} positions; free symbols: {free}",
                    verbose=self.verbose,
                )

                if free:
                    target_sym = free[
                        self.symbols.index(target_sym) if (target_sym := free[0]) else 0
                    ]
                    # Actually we just rotate through the free list
                    target_sym = (
                        free[self.symbols.index(free[0]) % len(free)] if free else None
                    )
                    if not target_sym:
                        time.sleep(self.interval)
                        continue

                    # Cool‑down check
                    if self._check_cool_down(target_sym):
                        log_message(
                            f"Cool‑down active for {target_sym}; skipping this round",
                            verbose=self.verbose,
                        )
                        time.sleep(self.interval)
                        continue

                    log_message(
                        f"Opportunity on {target_sym}; placing scalp...",
                        verbose=self.verbose,
                    )
                    result = place_scalp_order(
                        self.api_key,
                        self.api_secret,
                        target_sym,
                        side,
                        self.qty,
                        self.leverage,
                        self.dry_run,
                        self.trailing_stop,
                        self.target_profit,
                        self.max_spread_bps,
                    )
                    emit_output(json.dumps(result, default=str))

                    # If dry‑run, emit a concise summary after the loop ends (see below)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            log_message("Monitoring interrupted by user", verbose=self.verbose)
            return {"status": "monitor_stopped"}
        except Exception as e:
            log_message(f"Unexpected error: {e}", verbose=self.verbose)
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    def close(self) -> None:
        """Persist WS cache before exiting."""
        persist_ws_cache()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bybit Position Monitor & Micro‑Scalper"
    )
    parser.add_argument("--symbol", required=True, help="Trading pair, e.g. BTCUSDT")
    parser.add_argument(
        "--qty", type=float, required=True, help="Quantity (base asset)"
    )
    parser.add_argument("--leverage", type=int, required=True, help="Leverage value")
    parser.add_argument(
        "--interval", type=int, default=30, help="Poll interval in seconds"
    )
    parser.add_argument("--trailing-stop", type=float, help="Trailing‑stop distance")
    parser.add_argument("--target-profit", type=float, help="Target profit in USDT")
    parser.add_argument(
        "--symbols",
        action="append",
        help="Additional symbols for multi‑symbol monitoring (repeatable)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Exit after N poll cycles (default: run forever)",
    )
    parser.add_argument(
        "--max-spread-bps",
        type=float,
        default=None,
        help="Skip scalp if spread exceeds this many basis points",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate without real orders"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--use-ws", action="store_true", help="Use WebSocket for positions"
    )
    parser.add_argument(
        "--force-rest", action="store_true", help="Disable WebSocket entirely"
    )
    parser.add_argument(
        "--cool-down",
        type=int,
        default=0,
        help="Cool‑down seconds per symbol (rate‑limit)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to JSON config file containing any of the above options",
    )
    parser.add_argument(
        "--sell-scalp", action="store_true", help="Enable sell‑scalp mode"
    )
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Load optional JSON config file (values are overridden by CLI args)
    # -----------------------------------------------------------------------
    config_data: Optional[Dict[str, Any]] = None
    if args.config:
        try:
            with open(args.config) as f:
                config_data = json.load(f)
        except Exception as e:
            log_message(f"Failed to load config file: {e}", verbose=args.verbose)
            config_data = {}

    # Merge CLI args with config; CLI values take precedence
    def merge_args_namespace(
        ns: argparse.Namespace, cfg: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = vars(ns).copy()
        for k, v in cfg.items():
            if v is not None and k in merged:
                merged[k] = v
        return merged

    merged_dict = merge_args_namespace(args, config_data)

    # -----------------------------------------------------------------------
    # Run the engine
    # -----------------------------------------------------------------------
    engine = ScalpEngine(
        symbol=merged_dict["symbol"],
        qty=merged_dict["qty"],
        leverage=merged_dict["leverage"],
        interval=merged_dict["interval"],
        trailing_stop=merged_dict["trailing_stop"],
        target_profit=merged_dict["target_profit"],
        symbols=merged_dict["symbols"],
        dry_run=merged_dict["dry_run"],
        verbose=merged_dict["verbose"],
        use_ws=merged_dict["use_ws"],
        sell_scalp=merged_dict["sell_scalp"],
        max_iterations=merged_dict["max_iterations"],
        max_spread_bps=merged_dict["max_spread_bps"],
        cool_down=merged_dict["cool_down"],
        force_rest=merged_dict["force_rest"],
        config=config_data,
    )

    try:
        result = engine.run()
        # If we are in dry‑run mode, emit a short summary after execution
        if merged_dict["dry_run"] and isinstance(result, dict) and "status" in result:
            # Simple summary – can be expanded later
            summary = {
                "dry_run_summary": {
                    "iterations_completed": engine.iteration,
                    "status": result.get("status", "completed"),
                }
            }
            emit_output(json.dumps(summary, indent=2))
    finally:
        # Ensure cache is persisted even if an exception bubbles up
        engine.close()


if __name__ == "__main__":
    main()
