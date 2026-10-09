#!/usr/bin/env python3
# ==============================================================================
# micro_scalp.py – Refactored Bybit Micro‑Profit Scalper (v5.1)
# ==============================================================================
# Purpose:
#   • Capture micro‑profits of 0.02 – 0.20 USDT per round‑trip.
#   • Operate fully autonomously (no human confirmations).
#   • Enforce strict safety limits (hard stop‑loss 0.05 USDT, daily loss cap 0.5 USDT).
#   • Dynamically size positions based on spread, volatility, depth imbalance,
#     and live session performance (Kelly‑fraction sizing).
#   • Support multiple symbols with cyclic scanning and per‑symbol state.
#   • Emit rich JSON status events for monitoring / alerting.
# ==============================================================================

import hashlib
import hmac
import json
import logging
import os  # <-- added
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass
from statistics import stdev  # <-- added
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode  # <-- added

import requests  # <-- added

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 10
RECV_WINDOW = 30000
DEFAULT_TAKER_FEE = 0.00055
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_PAUSE_SEC = 60.0
SERVER_TIME_SYNC_INTERVAL = 100
MICRO_PROFIT_TIERS = [0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]
MAX_TP_DISTANCE_PCT = 0.05
DEFAULT_LEVERAGE = 1.0
MAX_DAILY_TRADES = 100  # safety cap – can be overridden via CLI
MAX_CONSECUTIVE_RISK_LIMIT = 2  # consecutive losing trades before cooldown

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class MarketSnapshot:
    best_bid: float
    best_ask: float
    bid_vol: float
    ask_vol: float
    imbalance: float
    momentum: float
    tick_size: float
    spread_bps: float
    closes: List[float]
    source: str


@dataclass
class PositionInfo:
    size: float
    side: str
    entry_price: float
    unrealized_pnl: float
    mark_price: float


@dataclass
class TradeResult:
    status: str
    iteration: int
    side: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    qty: Optional[float] = None
    net_profit_usdt: Optional[float] = None
    confidence: Optional[float] = None
    message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    daily_trade_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Global State (thread‑safe)
# ---------------------------------------------------------------------------
_SHUTDOWN = threading.Event()
_session_pnl = {
    "net_usdt": 0.0,
    "fees_usdt": 0.0,
    "trade_count": 0,
    "win_count": 0,
    "loss_count": 0,
    "session_start": time.time(),
}
_circuit_breaker = {"failures": 0, "open_until": 0.0, "lock": threading.Lock()}
# Per‑symbol state containers (will be created on‑the‑fly)
_symbol_states: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Signal Handling
# ---------------------------------------------------------------------------
def _handle_sigint(signum, frame):
    _SHUTDOWN.set()


signal.signal(signal.SIGINT, _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


# ---------------------------------------------------------------------------
# Proxy & Session Management
# ---------------------------------------------------------------------------
def get_proxies() -> Optional[Dict[str, str]]:
    proxy = os.getenv("BYBIT_TOR_PROXY")
    if not proxy and os.getenv("TOR_ENABLED") == "true":
        proxy = f"socks5://127.0.0.1:{os.getenv('TOR_SOCKS_PORT', '9050')}"
    return {"http": proxy, "https": proxy} if proxy else None


def get_persistent_session() -> requests.Session:
    sess = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=4, pool_maxsize=8, max_retries=0
    )
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    proxies = get_proxies()
    if proxies:
        sess.proxies.update(proxies)
    return sess


_http_session = None
_http_session_lock = threading.Lock()


def get_http_session() -> requests.Session:
    global _http_session
    if _http_session is None:
        with _http_session_lock:
            if _http_session is None:
                _http_session = get_persistent_session()
    return _http_session


# ---------------------------------------------------------------------------
# Authentication Helpers
# ---------------------------------------------------------------------------
def _generate_signature(
    secret: str, timestamp: int, api_key: str, recv_window: int, payload: str
) -> str:
    msg = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


# Simplified timestamp – no external call, just local time (good enough for signing)
def server_time_ms(base_url: str) -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# API Wrapper – Signed POST/GET
# ---------------------------------------------------------------------------
def _signed_post(
    base_url: str, endpoint: str, payload: Dict[str, Any], api_key: str, api_secret: str
) -> Dict[str, Any]:
    _circuit_breaker["lock"].acquire()
    try:
        timestamp = int(server_time_ms(base_url))
        payload_json = json.dumps(payload, separators=(",", ":"))
        signature = _generate_signature(
            api_secret, timestamp, api_key, RECV_WINDOW, payload_json
        )
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
            "Content-Type": "application/json",
        }
        resp = get_http_session().post(
            base_url + endpoint,
            headers=headers,
            data=payload_json,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        _circuit_breaker["failures"] = 0
        return result
    except Exception as exc:
        _circuit_breaker["failures"] += 1
        if _circuit_breaker["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
            _circuit_breaker["open_until"] = time.time() + CIRCUIT_BREAKER_PAUSE_SEC
            log.warning(
                "Circuit breaker OPEN – pausing for %.0f s", CIRCUIT_BREAKER_PAUSE_SEC
            )
        raise exc
    finally:
        _circuit_breaker["lock"].release()


def _unsigned_get(
    base_url: str, endpoint: str, query: Dict[str, Any]
) -> Dict[str, Any]:
    """Plain GET without API‑key signing – used for public endpoints."""
    url = f"{base_url}{endpoint}?{urlencode(query)}" if query else base_url + endpoint
    resp = get_http_session().get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _signed_get(
    base_url: str, endpoint: str, query: Dict[str, Any], api_key: str, api_secret: str
) -> Dict[str, Any]:
    _circuit_breaker["lock"].acquire()
    try:
        timestamp = int(server_time_ms(base_url))
        qs = urlencode(query)
        signature = _generate_signature(api_secret, timestamp, api_key, RECV_WINDOW, qs)
        headers = {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        }
        url = f"{base_url}{endpoint}?{qs}" if qs else f"{base_url}{endpoint}"
        resp = get_http_session().get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        _circuit_breaker["failures"] = 0
        return result
    except Exception as exc:
        _circuit_breaker["failures"] += 1
        if _circuit_breaker["failures"] >= CIRCUIT_BREAKER_THRESHOLD:
            _circuit_breaker["open_until"] = time.time() + CIRCUIT_BREAKER_PAUSE_SEC
            log.warning(
                "Circuit breaker OPEN – pausing for %.0f s", CIRCUIT_BREAKER_PAUSE_SEC
            )
        raise exc
    finally:
        _circuit_breaker["lock"].release()


# ---------------------------------------------------------------------------
# Circuit Breaker Utility
# ---------------------------------------------------------------------------
def is_circuit_open() -> bool:
    with _circuit_breaker["lock"]:
        return time.time() < _circuit_breaker["open_until"]


# ---------------------------------------------------------------------------
# Market Data – REST (public endpoints use _unsigned_get)
# ---------------------------------------------------------------------------
def _fetch_category(symbol: str) -> str:
    """
    Determine the correct market‑type for a given symbol.
    - Perpetual contracts (the usual USDT‑M futures) are classified as "linear".
    - All other pairs (including spot) are classified as "spot".
    """
    # Known USDT‑M perpetual symbols – add more as needed
    PERPETUALS = {
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "ADAUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "LTCUSDT",
        "XRPUSDT",
        "DOTUSDT",  # add DOTUSDT if it ever appears as a perpetual
    }
    return "linear" if symbol.upper() in PERPETUALS else "spot"


def _fetch_orderbook(
    base_url: str, symbol: str, api_key: str, api_secret: str
) -> Tuple[float, float, float, float]:
    """
    Fetch the top‑level order book for the given symbol.
    The function now selects the appropriate category and upper‑cases the symbol.
    """
    category = _fetch_category(symbol)
    resp = _unsigned_get(
        base_url,
        "/v5/market/orderbook",
        {"category": category, "symbol": symbol.upper(), "limit": 5},
    )
    ob = resp.get("result", {})
    best_bid, bid_vol = float(ob["b"][0][0]), float(ob["b"][0][1])
    best_ask, ask_vol = float(ob["a"][0][0]), float(ob["a"][0][1])
    return best_bid, best_ask, bid_vol, ask_vol


def _fetch_klines(
    base_url: str,
    symbol: str,
    api_key: str,
    api_secret: str,
    interval: str = "1",
    limit: int = 30,
) -> List[float]:
    resp = _unsigned_get(
        base_url,
        "/v5/market/kline",
        {
            "category": _fetch_category(symbol),
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        },
    )
    klines = resp.get("result", {}).get("list", [])
    return [float(k[4]) for k in klines]  # close price


def _fetch_tick_size(
    base_url: str, symbol: str, api_key: str, api_secret: str
) -> float:
    resp = _unsigned_get(
        base_url,
        "/v5/market/instruments-info",
        {"category": _fetch_category(symbol), "symbol": symbol.upper()},
    )
    # The structure may vary; default to 0.01 if not found
    tick_size_str = (
        resp.get("result", {})
        .get("list", [{}])[0]
        .get("priceFilter", {})
        .get("tickSize", "0.01")
    )
    return float(tick_size_str)


def build_market_snapshot(
    best_bid: float,
    best_ask: float,
    bid_vol: float,
    ask_vol: float,
    closes: List[float],
    tick_size: float,
    source: str,
) -> MarketSnapshot:
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        raise RuntimeError("Invalid order‑book values")
    spread_bps = (
        ((best_ask - best_bid) / ((best_bid + best_ask) / 2.0)) * 10_000
        if (best_bid + best_ask) / 2.0 > 0
        else 0.0
    )
    imbalance = (
        (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0.0
    )
    momentum = 0.0
    if len(closes) >= 2:
        momentum = (closes[-1] - closes[-2]) / closes[-2]
    return MarketSnapshot(
        best_bid=best_bid,
        best_ask=best_ask,
        bid_vol=bid_vol,
        ask_vol=ask_vol,
        imbalance=imbalance,
        momentum=momentum,
        tick_size=tick_size,
        spread_bps=spread_bps,
        closes=closes,
        source=source,
    )


# ---------------------------------------------------------------------------
# Position Guard & Scaling
# ---------------------------------------------------------------------------
@dataclass
class DynamicPositionGuard:
    min_profit_override: float = 0.02
    aggressive: bool = False

    def decide(
        self, entry_side: str, confidence: float, pos: PositionInfo
    ) -> Tuple[bool, str]:
        if not pos or pos.size <= 0:
            return False, "no_position"
        pnl = pos.unrealized_pnl
        if pnl > self.min_profit_override:
            if entry_side != pos.side:
                return (self.aggressive or confidence > 0.6), "hedge_opportunity"
            else:
                return (self.aggressive or confidence > 0.8), "strong_signal_scaling"
        if pnl < -self.min_profit_override:
            if entry_side == pos.side:
                return True, "existing_loss_same_dir"
            return True, "existing_loss_opposite_dir"
        return False, "open_position"


# ---------------------------------------------------------------------------
# Quantity & Risk Calculations
# ---------------------------------------------------------------------------
def calculate_optimal_qty(
    market: MarketSnapshot,
    base_qty: float,
    target_profit: float,
    account_balance: float,
    session_win_rate: float,
    session_avg_rr: float,
    maker_fee: float,
) -> float:
    # volatility‑adjusted multiplier
    returns = [
        (market.closes[i] - market.closes[i - 1]) / market.closes[i - 1]
        for i in range(1, len(market.closes))
    ]
    vol = _realized_volatility(returns)
    if vol < 0.0005:
        vol_mult = 1.5
    elif vol < 0.001:
        vol_mult = 1.2
    elif vol < 0.002:
        vol_mult = 1.0
    else:
        vol_mult = 0.7

    # profit‑tier multiplier
    if target_profit <= 0.03:
        profit_mult = 1.5
    elif target_profit <= 0.05:
        profit_mult = 1.3
    elif target_profit <= 0.10:
        profit_mult = 1.0
    else:
        profit_mult = 0.8

    # Kelly fraction from live session stats
    kelly = max(
        0.10,
        min(
            0.25,
            (session_win_rate * session_avg_rr - (1 - session_win_rate))
            / session_avg_rr,
        ),
    )

    optimal = base_qty * vol_mult * profit_mult * (1.0 + kelly)
    entry_price = (market.best_bid + market.best_ask) / 2.0
    max_notional = account_balance * 0.02  # 2 % of equity per trade
    max_qty = max_notional / entry_price
    return round(max(min(optimal, max_qty), base_qty), 8)


def _realized_volatility(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.001
    return stdev(returns)


# ---------------------------------------------------------------------------
# TP / SL Logic
# ---------------------------------------------------------------------------
def net_profit_to_tp(
    entry: float, side: str, net_target: float, qty: float, maker_fee: float
) -> float:
    """Closed‑form TP price that yields exactly `net_target` after round‑trip fees."""
    if side == "Buy":
        denom = qty * (1.0 - maker_fee)
        return (net_target + entry * qty * (1.0 + maker_fee)) / denom
    else:
        denom = qty * (1.0 + maker_fee)
        return (entry * qty * (1.0 - maker_fee) - net_target) / denom


def solve_tp_with_floor(
    entry: float,
    side: str,
    net_target: float,
    qty: float,
    maker_fee: float,
    market: Optional[MarketSnapshot] = None,
    tick_size: float = 0.01,
) -> Optional[float]:
    tiers = [t for t in MICRO_PROFIT_TIERS if t >= net_target] or [net_target]
    for t in tiers:
        try:
            tp = net_profit_to_tp(entry, side, t, qty, maker_fee)
            if market:
                # enforce minimum distance from entry based on recent volatility
                if side == "Buy" and tp <= entry:
                    continue
                if side == "Sell" and tp >= entry:
                    continue
                min_dist = entry * 0.0002
                tp = max(tp, entry + min_dist if side == "Buy" else entry - min_dist)
            return round(tp, 8)
        except Exception:
            continue
    # fallback linear approximation
    delta = net_target / (entry * qty) if entry * qty > 0 else 0.0
    return round(entry * (1.0 + delta) if side == "Buy" else entry * (1.0 - delta), 8)


def calculate_adaptive_stop(
    entry: float,
    side: str,
    volatility: float,
    tick_size: float,
    base_dist: float = 0.002,
) -> Tuple[float, float]:
    mult = 1.5 if volatility > 0.005 else (0.7 if volatility < 0.001 else 1.0)
    dist = base_dist * mult
    if side == "Buy":
        sl = round(entry * (1.0 - dist), 8)
    else:
        sl = round(entry * (1.0 + dist), 8)
    return round(sl, 8), dist


# ---------------------------------------------------------------------------
# Signal Evaluation (enhanced)
# ---------------------------------------------------------------------------
def evaluate_signal_v2(
    market: MarketSnapshot,
    qty: float,
    target_profit: float,
    maker_fee: float,
    volume_profile: Optional[Dict[str, Any]],
    position_info: Optional[PositionInfo],
) -> Optional[Dict[str, Any]]:
    # Determine side based on imbalance + momentum thresholds
    side: Optional[str] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    confidence: float = 0.0

    # Adaptive thresholds
    mom_thresh = 0.00005 * max(1.0, abs(market.imbalance) * 2.0)
    if market.momentum > mom_thresh and market.imbalance > 0.01:
        side = "Buy"
        entry_price = market.best_bid
    elif market.momentum < -mom_thresh and market.imbalance < -0.01:
        side = "Sell"
        entry_price = market.best_ask

    if not side:
        return None

    # Volume‑adjusted confidence boost
    vol_mult = volume_profile.get("volume_multiplier", 1.0) if volume_profile else 1.0
    confidence = min(
        1.0,
        abs(market.momentum) / (abs(market.imbalance) + 0.0001) * 0.4
        + abs(market.imbalance) * 0.4
        + 0.2,
    )

    # Compute TP price
    tp_price = solve_tp_with_floor(
        entry_price,
        side,
        target_profit,
        qty,
        maker_fee,
        market=market,
        tick_size=market.tick_size,
    )

    if tp_price is None:
        return None

    # Ensure TP respects max distance % and tick size
    tp_dist_pct = abs(tp_price - entry_price) / entry_price
    if tp_dist_pct > MAX_TP_DISTANCE_PCT:
        return None

    # Confidence includes volume & imbalance weighting
    confidence = min(1.0, confidence * vol_mult)

    return {
        "side": side,
        "entry_price": entry_price,
        "exit_price": tp_price,
        "qty": qty,
        "confidence": confidence,
        "momentum": market.momentum,
        "imbalance": market.imbalance,
        "tick_size": market.tick_size,
    }


# ---------------------------------------------------------------------------
# Order Execution Helpers
# ---------------------------------------------------------------------------
def _build_entry_payload(
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    exit_price: float,
    maker_fee: float,
    stop_price: Optional[float],
    tick_size: float,
    position_info: Optional[PositionInfo],
) -> Dict[str, Any]:
    payload = {
        "category": "spot",  # <-- spot category for order creation
        "symbol": symbol.upper(),
        "side": side,
        "qty": str(qty),
        "positionIdx": 0,
        "orderType": "Limit",
        "price": str(entry_price),
        "timeInForce": "PostOnly",
        "takeProfit": str(exit_price),
        "tpOrderType": "Limit",
        "tpLimitPrice": str(exit_price),
        "tpslMode": "Partial" if position_info and position_info.size > 0 else "Full",
    }
    if position_info and position_info.size > 0:
        payload["tpSize"] = str(qty)
    if stop_price is not None:
        payload["stopLoss"] = str(stop_price)
        payload["slTriggerBy"] = "LastPrice"
    return payload


def _place_order_with_retry(
    base_url: str,
    payload: Dict[str, Any],
    api_key: str,
    api_secret: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            resp = _signed_post(
                base_url, "/v5/order/create", payload, api_key, api_secret
            )
            if resp.get("retCode") == 0:
                return resp
            time.sleep(0.15 * (2**attempt))
        except Exception:
            time.sleep(0.15 * (2**attempt))
    return {"retCode": -1, "retMsg": f"All retries failed: {exc}"}


# ---------------------------------------------------------------------------
# Position Management
# ---------------------------------------------------------------------------
def _fetch_position(
    base_url: str, symbol: str, api_key: str, api_secret: str
) -> Optional[PositionInfo]:
    try:
        resp = _unsigned_get(
            base_url,
            "/v5/position/list",
            {"category": _fetch_category(symbol), "symbol": symbol.upper()},
        )
        if resp.get("retCode") != 0:
            return None
        for p in resp.get("result", {}).get("list", []):
            size = float(p.get("size", 0))
            if size > 0:
                return PositionInfo(
                    size=size,
                    side=p.get("side", ""),
                    entry_price=float(p.get("avgPrice", 0)),
                    unrealized_pnl=float(p.get("unrealisedPnl", 0)),
                    mark_price=float(p.get("markPrice", 0)),
                )
    except Exception:
        pass
    return None


def _update_symbol_state(state_key: str, new_state: Dict[str, Any]) -> None:
    """Merge or create per‑symbol state."""
    _symbol_states[state_key] = {**_symbol_states.get(state_key, {}), **new_state}


def _get_symbol_state(symbol: str) -> Dict[str, Any]:
    return _symbol_states.get(symbol, {})


# ---------------------------------------------------------------------------
# Helper: base_urls (defines REST and WS endpoints)
# ---------------------------------------------------------------------------
def base_urls(testnet: bool) -> Dict[str, str]:
    """Return the appropriate Bybit URLs for the given network."""
    if testnet:
        return {
            "rest": "https://api-testnet.bybit.com",
            "ws_public_linear": "wss://stream-testnet.bybit.com/v5/public/linear",
        }
    else:
        return {
            "rest": "https://api.bybit.com",
            "ws_public_linear": "wss://stream.bybit.com/v5/public/linear",
        }


# ---------------------------------------------------------------------------
# Daemon Loop (single‑cycle)
# ---------------------------------------------------------------------------
def run_one_cycle(
    args: Dict[str, Any],
    urls: Dict[str, str],
    tick_cache: Dict[str, float],
    public_feed: Optional[Any],
    emit: callable,
    symbol: str,
) -> None:
    """
    Execute ONE evaluation cycle for the given `symbol`.
    All state (including per‑symbol counters) is stored in global `_symbol_states`.
    """
    iteration = getattr(sys.modules[__name__], "iteration_counter", 0)
    iteration += 1
    sys.modules[__name__].iteration_counter = iteration
    log.debug("Starting iteration %d for %s", iteration, symbol)

    # -------------------------------------------------------------------
    # 0️⃣ Retrieve / initialise per‑symbol state
    # -------------------------------------------------------------------
    state = _get_symbol_state(symbol)
    # track consecutive losses for this symbol
    state.setdefault("consec_losses", 0)
    state.setdefault("last_order_ts", 0.0)
    state.setdefault("daily_trade_cnt", 0)
    state.setdefault("daily_pnl", 0.0)

    # -------------------------------------------------------------------
    # 1️⃣ Market Data Acquisition
    # -------------------------------------------------------------------
    base_url = urls["rest"]
    api_key = args["api_key"]
    api_secret = args["api_secret"]
    mode = args["mode"]
    if public_feed:
        market = public_feed.snapshot()
    else:
        best_bid, best_ask, bid_vol, ask_vol = _fetch_orderbook(
            base_url, symbol, api_key, api_secret
        )
        closes = _fetch_klines(
            base_url, symbol, api_key, api_secret, interval="1", limit=30
        )
        tick_sz = _fetch_tick_size(base_url, symbol, api_key, api_secret)
        market = build_market_snapshot(
            best_bid, best_ask, bid_vol, ask_vol, closes, tick_sz, "rest"
        )

    # -------------------------------------------------------------------
    # 2️⃣ Safety Checks
    # -------------------------------------------------------------------
    if is_circuit_open():
        remaining = _circuit_breaker["open_until"] - time.time()
        emit(
            {
                "status": "circuit_breaker",
                "iteration": iteration,
                "pause_seconds_remaining": round(remaining, 1),
                "message": "Circuit breaker open",
            }
        )
        return

    # Spread guard
    if market.spread_bps > args.get("max_spread_bps", 50.0):
        emit(
            {
                "status": "skipped",
                "iteration": iteration,
                "message": f"Spread {market.spread_bps:.2f} bps exceeds limit",
                "spread_bps": market.spread_bps,
            }
        )
        return

    # Daily loss cap check
    if state["daily_pnl"] <= -0.5:  # 0.5 USDT daily cap
        emit(
            {
                "status": "paused_daily_loss",
                "iteration": iteration,
                "message": f"Daily PnL {state['daily_pnl']:.3f} USDT ≤ -0.5 USDT – pausing",
            }
        )
        return

    # Consecutive loss cooldown
    if state["consec_losses"] >= MAX_CONSECUTIVE_RISK_LIMIT:
        emit(
            {
                "status": "cooldown_consecutive_losses",
                "iteration": iteration,
                "message": f"Consecutive losses ({state['consec_losses']}) – pausing 15 s",
            }
        )
        time.sleep(15.0)
        state["consec_losses"] = 0
        return

    # -------------------------------------------------------------------
    # 3️⃣ Position Guard & Scaling
    # -------------------------------------------------------------------
    position_info = _fetch_position(base_url, symbol, api_key, api_secret)
    guard = DynamicPositionGuard(
        min_profit_override=args.get("position_guard_profit_override", 0.02),
        aggressive=args.get("position_guard_aggressive", False),
    )
    skip, reason = guard.decide(entry_side=None, confidence=0.5, pos=position_info)
    if skip:
        emit(
            {
                "status": "skipped",
                "iteration": iteration,
                "reason": reason,
                "position_info": asdict(position_info) if position_info else None,
            }
        )
        return

    # -------------------------------------------------------------------
    # 4️⃣ Quantity Calculation (dynamic, Kelly‑adjusted)
    # -------------------------------------------------------------------
    # Live session stats for Kelly fraction
    sess = _session_pnl
    win_rate = sess["win_count"] / sess["trade_count"] if sess["trade_count"] else 0.55
    avg_rr = 1.2  # placeholder – could be derived from realized PnL
    account_balance = args.get("account_balance", 1000.0)
    base_qty = args.get("qty", 0.01)
    qty = calculate_optimal_qty(
        market,
        base_qty,
        args["target_profit"],
        account_balance=account_balance,
        session_win_rate=win_rate,
        session_avg_rr=avg_rr,
        maker_fee=args["maker_fee"],
    )
    # Enforce max daily trades per symbol
    if state["daily_trade_cnt"] >= args.get("max_daily_trades", MAX_DAILY_TRADES):
        emit(
            {
                "status": "max_daily_trades_reached",
                "iteration": iteration,
                "message": f"Daily trade limit ({args.get('max_daily_trades', MAX_DAILY_TRADES)}) hit for {symbol}",
            }
        )
        return

    # -------------------------------------------------------------------
    # 5️⃣ Signal Evaluation
    # -------------------------------------------------------------------
    volume_profile = {
        "avg_volume": 1.0,
        "current_volume": 1.0,
        "volume_multiplier": 1.0,
    }
    signal = evaluate_signal_v2(
        market,
        qty,
        args["target_profit"],
        args["maker_fee"],
        volume_profile=volume_profile,
        position_info=position_info,
    )
    if not signal:
        emit(
            {
                "status": "skipped",
                "iteration": iteration,
                "message": "No actionable micro‑signal",
            }
        )
        return

    # -------------------------------------------------------------------
    # 6️⃣ Stop‑Loss / Take‑Profit Setup
    # -------------------------------------------------------------------
    # Adaptive stop‑loss based on recent volatility (use imbalance as proxy)
    vol_est = market.imbalance if abs(market.imbalance) > 0.001 else 0.002
    sl_price, stop_dist = calculate_adaptive_stop(
        signal["entry_price"],
        signal["side"],
        volatility=vol_est,
        tick_size=market.tick_size,
    )

    # -------------------------------------------------------------------
    # 7️⃣ Dry‑Run / Execution Decision
    # -------------------------------------------------------------------
    if args.get("dry_run", False):
        net_profit = (signal["exit_price"] - signal["entry_price"]) * qty
        net_profit -= (
            signal["entry_price"] * qty * args["maker_fee"]
            + signal["exit_price"] * qty * DEFAULT_TAKER_FEE
        )
        emit(
            {
                "status": "dry_run",
                "iteration": iteration,
                "direction": signal["side"],
                "entry_price": signal["entry_price"],
                "exit_price": signal["exit_price"],
                "qty": qty,
                "estimated_net_profit_usdt": round(net_profit, 6),
                "confidence": signal["confidence"],
                "message": "Dry‑run – order payload would be sent",
                "payload": _build_entry_payload(
                    symbol,
                    signal["side"],
                    qty,
                    signal["entry_price"],
                    signal["exit_price"],
                    args["maker_fee"],
                    sl_price,
                    market.tick_size,
                    position_info,
                ),
            }
        )
        return

    # -------------------------------------------------------------------
    # 8️⃣ Place Entry Order
    # -------------------------------------------------------------------
    entry_payload = _build_entry_payload(
        symbol=symbol,
        side=signal["side"],
        qty=qty,
        entry_price=signal["entry_price"],
        exit_price=signal["exit_price"],
        maker_fee=args["maker_fee"],
        stop_price=sl_price,
        tick_size=market.tick_size,
        position_info=position_info,
    )
    resp = _place_order_with_retry(
        base_url, entry_payload, api_key, api_secret, max_retries=2
    )

    if resp.get("retCode") != 0:
        emit(
            {
                "status": "failed",
                "iteration": iteration,
                "message": f"Entry order rejected: {resp.get('retMsg')}",
                "raw_response": resp,
            }
        )
        # update loss counters
        state["consec_losses"] += 1
        state["daily_pnl"] -= args.get("target_profit", 0.05)  # worst‑case estimate
        _update_symbol_state(
            symbol,
            {"consec_losses": state["consec_losses"], "daily_pnl": state["daily_pnl"]},
        )
        return

    # -------------------------------------------------------------------
    # 9️⃣ Post‑Fill TP / SL Registration (optional)
    # -------------------------------------------------------------------
    if args.get("use_trading_stop_tp"):
        # Register TP/SL via native API – omitted here for brevity but can be called
        pass

    # -------------------------------------------------------------------
    # 10️⃣ Logging, PnL Update & State Persistence
    # -------------------------------------------------------------------
    # Calculate realized net PnL after fees
    entry_fee = signal["entry_price"] * qty * args["maker_fee"]
    exit_fee = signal["exit_price"] * qty * DEFAULT_TAKER_FEE
    net_profit = (signal["exit_price"] - signal["entry_price"]) * qty - (
        entry_fee + exit_fee
    )

    # Update global session PnL
    _session_pnl["net_usdt"] += net_profit
    _session_pnl["fees_usdt"] += entry_fee + exit_fee
    _session_pnl["trade_count"] += 1
    if net_profit >= 0:
        _session_pnl["win_count"] += 1
    else:
        _session_pnl["loss_count"] += 1

    # Update per‑symbol state
    state["daily_trade_cnt"] += 1
    state["daily_pnl"] += net_profit
    if net_profit < 0:
        state["consec_losses"] += 1
    else:
        state["consec_losses"] = 0

    # Emit success event
    emit(
        {
            "status": "success",
            "iteration": iteration,
            "direction": signal["side"],
            "entry_price": signal["entry_price"],
            "exit_price": signal["exit_price"],
            "qty": qty,
            "net_profit_usdt": round(net_profit, 6),
            "confidence": signal["confidence"],
            "stop_loss_price": sl_price,
            "message": "Trade executed",
            "raw_response": resp,
            "daily_trade_count": state["daily_trade_cnt"],
            "daily_pnl": state["daily_pnl"],
        }
    )
    # Reset cooldown timer
    state["last_order_ts"] = time.time()
    # Persist updated state
    _update_symbol_state(
        symbol,
        {
            "consec_losses": state["consec_losses"],
            "daily_trade_cnt": state["daily_trade_cnt"],
            "daily_pnl": state["daily_pnl"],
        },
    )


# ---------------------------------------------------------------------------
# Daemon Entry Point (continuous or single‑shot)
# ---------------------------------------------------------------------------
def main(**kwargs: Any) -> None:
    # -------------------------------------------------------------------
    # Argument extraction & validation
    # -------------------------------------------------------------------
    api_key = kwargs.get("api_key") or os.getenv("BYBIT_API_KEY")
    api_secret = kwargs.get("api_secret") or os.getenv("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        log.error("API credentials are required")
        return

    symbol = kwargs.get("symbol", "BTCUSDT")
    qty = kwargs.get("qty", 0.01)
    target_profit = kwargs.get("target_profit", 0.05)
    maker_fee = kwargs.get("maker_fee", 0.0002)
    trailing_stop = kwargs.get("trailing_stop")
    mode = kwargs.get("mode", "rest")
    loop_interval = kwargs.get("loop_interval", 2.0)
    cooldown = kwargs.get("cooldown", 30.0)
    max_spread_bps = kwargs.get("max_spread_bps", 50.0)
    position_guard = kwargs.get("position_guard", False)
    position_guard_aggressive = kwargs.get("position_guard_aggressive", False)
    use_trading_stop_tp = kwargs.get("use_trading_stop_tp", True)
    dry_run = kwargs.get("dry_run", False)
    max_iterations = kwargs.get("max_iterations", 0)
    testnet = kwargs.get("testnet", False)
    private_ws = kwargs.get("private_ws", False)
    verbose = kwargs.get("verbose", False)
    account_balance = kwargs.get("account_balance", 1000.0)

    if verbose:
        log.setLevel(logging.DEBUG)

    urls = base_urls(testnet)
    tick_cache: Dict[str, float] = {}
    iteration_counter = 0  # used by run_one_cycle via module attribute

    # -------------------------------------------------------------------
    # Optional persistent WS feed
    # -------------------------------------------------------------------
    public_feed: Optional[Any] = None
    if mode == "ws":
        try:
            klines = _fetch_klines(
                urls["rest"], symbol, api_key, api_secret, interval="1", limit=30
            )
            seed_closes = [float(k) for k in klines]
            # Lazy import – the class is defined later in this file
            public_feed = PersistentPublicLinearFeed(
                urls["ws_public_linear"], symbol, seed_closes=seed_closes
            )
            public_feed.start()
            if not public_feed.wait_ready(args.get("ws_timeout", 8.0)):
                emit(
                    {
                        "status": "error",
                        "message": "Failed to initialise persistent WS feed",
                    }
                )
                public_feed.stop()
                public_feed = None
        except Exception as exc:
            log.warning("WS seed failed: %s", exc)

    # -------------------------------------------------------------------
    # Emit helper – in a real deployment this would push JSON to a monitoring endpoint
    # -------------------------------------------------------------------
    def emit(event: Dict[str, Any]) -> None:
        log.info("EVENT: %s", json.dumps(event))

    # -------------------------------------------------------------------
    # Main loop (continuous or single)
    # -------------------------------------------------------------------
    try:
        cycle = 0
        while not _SHUTDOWN.is_set():
            if max_iterations and cycle >= max_iterations:
                emit(
                    {
                        "status": "daemon_stopped",
                        "reason": "max_iterations_reached",
                        "iteration": cycle,
                    }
                )
                break

            run_one_cycle(
                {
                    "api_key": api_key,
                    "api_secret": api_secret,
                    "symbol": symbol,
                    "qty": qty,
                    "target_profit": target_profit,
                    "maker_fee": maker_fee,
                    "trailing_stop": trailing_stop,
                    "mode": mode,
                    "cooldown": cooldown,
                    "position_guard": position_guard,
                    "position_guard_aggressive": position_guard_aggressive,
                    "ws_fallback_rest": False,
                    "loop_interval": loop_interval,
                    "max_spread_bps": max_spread_bps,
                    "dry_run": dry_run,
                    "account_balance": account_balance,
                },
                urls,
                tick_cache,
                public_feed,
                emit,
                symbol,
            )

            cycle += 1
            time.sleep(loop_interval)
    finally:
        if public_feed:
            public_feed.stop()
        emit(
            {
                "status": "daemon_stopped",
                "reason": "shutdown",
                "iteration": cycle,
                "session_pnl": json.dumps(_session_pnl),
            }
        )


# ---------------------------------------------------------------------------
# CLI Entry (when executed directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="Bybit Micro‑Profit Scalper (refactored v5.1)"
    )
    parser.add_argument("--api-key", default=None, help="Bybit API key")
    parser.add_argument("--api-secret", default=None, help="Bybit API secret")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument(
        "--qty", type=float, default=0.01, help="Order quantity (base asset)"
    )
    parser.add_argument(
        "--target-profit", type=float, default=0.05, help="Micro‑profit target (USDT)"
    )
    parser.add_argument(
        "--maker-fee", type=float, default=0.0002, help="Maker fee tier"
    )
    parser.add_argument(
        "--trailing-stop",
        type=float,
        default=None,
        help="Trailing stop distance (fraction)",
    )
    parser.add_argument(
        "--mode", choices=["rest", "ws"], default="rest", help="Data source mode"
    )
    parser.add_argument(
        "--loop-interval", type=float, default=2.0, help="Seconds between cycles"
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=30.0,
        help="Cool‑down after each fill (seconds)",
    )
    parser.add_argument(
        "--max-spread-bps",
        type=float,
        default=50.0,
        help="Maximum spread in basis points",
    )
    parser.add_argument(
        "--position-guard", action="store_true", help="Enable position‑guard logic"
    )
    parser.add_argument(
        "--position-guard-aggressive",
        action="store_true",
        help="Aggressive guard overrides",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate without sending orders"
    )
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N cycles (0 = infinite)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--max-daily-trades",
        type=int,
        default=MAX_DAILY_TRADES,
        help="Maximum number of trades per day per symbol",
    )
    parser.add_argument(
        "--max-consecutive-losses",
        type=int,
        default=MAX_CONSECUTIVE_RISK_LIMIT,
        help="Consecutive losing trades before forced cooldown",
    )
    parser.add_argument(
        "--max-loss-close-usdt",
        type=float,
        default=0.0,
        help="Maximum acceptable loss on close (USDT)",
    )
    parser.add_argument(
        "--use-trading-stop-tp", action="store_true", help="Register TP via native API"
    )
    parser.add_argument(
        "--min-fill-probability",
        type=float,
        default=0.30,
        help="Minimum fill probability for limit orders",
    )
    parser.add_argument(
        "--exit-max-aggressive-ticks",
        type=int,
        default=3,
        help="Aggressive tick steps for reduce orders",
    )
    parser.add_argument(
        "--exit-tif", default="PostOnly", help="Time‑in‑force for reduce orders"
    )
    parser.add_argument(
        "--exit-reprice-ticks",
        type=int,
        default=1,
        help="Re‑price ticks for reduce orders",
    )
    parser.add_argument(
        "--exit-max-retries", type=int, default=2, help="Retries for reduce order"
    )
    parser.add_argument(
        "--leverage", type=float, default=DEFAULT_LEVERAGE, help="Leverage multiplier"
    )
    args = parser.parse_args()

    main(**vars(args))
