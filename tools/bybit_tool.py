import sys

sys.path.append("/data/data/com.termux/files/home/.config/aichat/llm-functions/tools")
from target_analysis import USDTTargetCalculator

#!/usr/bin/env python3
"""
BYBIT REALM - Production-Grade Trading System Tool for LLM Functions v5.0

Upgrades & Additions (v5.0):
  • Merged Bash Scripts: Native Python equivalents for Mark Price, OrderBook Liquidity Zones, and Order Creation.
  • Integrated Position Manager: Manage Break-Even and Auto-Close operations directly inside the tool.
  • Integrated Micro-Profit Macros: Included calculate_micro_profit and macros (basic_scalp, leverage_momentum, wall_surfing).
  • Architecture Deduplication: Unified BybitToolDispatcher and BybitRealm into a single, cohesive BybitRealm client.
  • Enriched Orderbook Analysis: Now calculates liquidity_zone (thin, moderate, thick) matching legacy bash tools.
  • Backward Compatibility: All CLI args, env vars, and run() signatures are fully preserved.

Usage:
    Set environment variables BYBIT_API_KEY and BYBIT_API_SECRET before use.
    Optional: BYBIT_USE_TESTNET, TOR_ENABLED, TOR_SOCKS_PORT, PYSOCKS_ENABLED, etc.
"""
import ast
import hashlib
import hmac
import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple

# ── dotenv support ───────────────────────────────────────────
try:
    from dotenv import load_dotenv

    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.config/bybit/.env"),
    ]
    for env_path in env_paths:
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break
except ImportError:
    pass

# ── Optional dependencies ─────────────────────────────────────
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import socks

    PYSOCKS_AVAILABLE = True
except ImportError:
    PYSOCKS_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BybitRealm")


# ─────────────────────────────────────────────────────────────
# ENUMS & DATACLASSES
# ─────────────────────────────────────────────────────────────
class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    LIMIT_MAKER = "LimitMaker"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"


class Category(str, Enum):
    LINEAR = "linear"
    INVERSE = "inverse"
    SPOT = "spot"
    OPTION = "option"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "PostOnly"


class PositionIdx(int, Enum):
    ONE_WAY = 0
    HEDGE_BUY = 1
    HEDGE_SELL = 2


def _safe_float(val) -> Optional[float]:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


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


@dataclass
class InstrumentInfo:
    lot_size: LotSizeFilter
    price_flt: PriceFilter
    symbol: str
    status: str = "Trading"
    fetched_at: float = field(default_factory=time.time)


@dataclass
class OrderBookLevel:
    price: float
    qty: float


# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
@dataclass
class TradingConfig:
    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet: bool = field(
        default_factory=lambda: (
            os.getenv("BYBIT_USE_TESTNET", "false").lower() == "true"
        )
    )
    use_tor: bool = field(
        default_factory=lambda: os.getenv("TOR_ENABLED", "false").lower() == "true"
    )
    tor_socks_port: int = field(
        default_factory=lambda: int(os.getenv("TOR_SOCKS_PORT", "9050"))
    )
    tor_use_pysocks: bool = field(
        default_factory=lambda: os.getenv("TOR_USE_PYSOCKS", "true").lower() == "true"
    )
    request_timeout: int = 15
    max_retries: int = 3
    cb_failure_threshold: int = 5
    cb_recovery_timeout: float = 60.0
    cb_cooldown: float = 30.0
    rate_limit_calls: int = 10
    rate_limit_window: float = 1.0
    recv_window: int = 10000
    iceberg_min_slices: int = 3
    iceberg_max_slices: int = 10
    iceberg_delay: float = 0.5
    journal_path: str = "trade_journal.json"

    @property
    def base_url(self) -> str:
        return (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )


# ─────────────────────────────────────────────────────────────
# CORE SYSTEM COMPONENTS (Rate Limiter, Circuit Breaker, Tor)
# ─────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, capacity: int, window: float) -> None:
        self.capacity = capacity
        self.window = window
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._calls and self._calls[0] <= now - self.window:
                self._calls.popleft()
            if len(self._calls) >= self.capacity:
                sleep_for = self.window - (now - self._calls[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
                    now = time.monotonic()
            self._calls.append(now)


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        cooldown: float = 30.0,
    ):
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._cooldown = cooldown
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_ts = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition()
            return self._state

    def _maybe_transition(self):
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_ts >= self._recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN

    def record_success(self):
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_ts = time.monotonic()
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failure_count >= self._threshold
            ):
                self._state = CircuitState.OPEN

    def reset(self):
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0


class TradeJournal:
    def __init__(self, path: str):
        self.path = path
        self._entries = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._entries = json.load(f)
            except Exception:
                pass

    def record(self, action: str, payload: dict, result: dict, symbol: str):
        with self._lock:
            self._entries.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "symbol": symbol,
                    "payload": payload,
                    "result": result,
                }
            )
            # Keep last 1000 to avoid bloat
            self._entries = self._entries[-1000:]
            try:
                with open(self.path, "w") as f:
                    json.dump(self._entries, f)
            except:
                pass


class TorManager:
    """Manages multi-tier network requests: Tor SOCKS5 -> torsocks -> Direct"""

    def __init__(self, config: TradingConfig):
        self.config = config
        self._session = self._build_session(proxies=None)
        self._socks_session = None
        self._torsocks_bin = shutil.which("torsocks") if config.use_tor else None

        if config.use_tor and config.tor_use_pysocks and REQUESTS_AVAILABLE:
            proxies = {
                "http": f"socks5h://127.0.0.1:{config.tor_socks_port}",
                "https": f"socks5h://127.0.0.1:{config.tor_socks_port}",
            }
            self._socks_session = self._build_session(proxies=proxies)

    def _build_session(self, proxies):
        if not REQUESTS_AVAILABLE:
            return None
        session = requests.Session()
        retry = Retry(
            total=self.config.max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        if proxies:
            session.proxies = proxies
        return session

    def request(
        self,
        method: str,
        url: str,
        headers: dict,
        params: dict = None,
        json_data: dict = None,
        signed: bool = True,
    ) -> dict:
        tiers = []
        if self.config.use_tor:
            if not signed and self._socks_session:
                tiers.append(self._tier_socks)
            if self._torsocks_bin:
                tiers.append(self._tier_torsocks)
        tiers.append(self._tier_direct)

        last_exc = None
        for tier in tiers:
            try:
                return tier(method, url, headers, params, json_data)
            except Exception as exc:
                last_exc = exc
        raise ConnectionError(f"All network tiers failed. Last error: {last_exc}")

    def _tier_socks(self, method, url, headers, params, json_data):
        resp = self._socks_session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.config.request_timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _tier_direct(self, method, url, headers, params, json_data):
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.config.request_timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _tier_torsocks(self, method, url, headers, params, json_data):
        cmd = [self._torsocks_bin, "curl", "-s", "-X", method]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data, separators=(",", ":"))]
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        cmd.append(url)

        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.config.request_timeout + 5
        )
        if res.returncode != 0:
            raise RuntimeError(f"torsocks failed: {res.stderr}")
        return json.loads(res.stdout)


# ─────────────────────────────────────────────────────────────
# BYBIT REALM CORE CLIENT
# ─────────────────────────────────────────────────────────────
class BybitRealm:
    """Unified full-featured Bybit V5 API client."""

    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        self.net = TorManager(self.config)
        self.limiter = RateLimiter(
            capacity=self.config.rate_limit_calls, window=self.config.rate_limit_window
        )
        self.breaker = CircuitBreaker(
            failure_threshold=self.config.cb_failure_threshold,
            recovery_timeout=self.config.cb_recovery_timeout,
            cooldown=self.config.cb_cooldown,
        )
        self.journal = TradeJournal(self.config.journal_path)

        self._cache_lock = threading.Lock()
        self._instr_cache: Dict[str, InstrumentInfo] = {}
        self._ticker_cache: Dict[str, Tuple[dict, float]] = {}
        self._klines_cache: Dict[str, Tuple[List[List[float]], float]] = {}
        self._time_offset = 0
        self._time_synced = False

    # ── Auth & Request Routing ────────────────────────────────────────────────
    def _sync_time(self):
        try:
            local_b = int(time.time() * 1000)
            res = self.net.request(
                "GET",
                self.config.base_url + "/v5/market/time",
                headers={"Content-Type": "application/json"},
                signed=False,
            )
            server_t = int(res.get("result", {}).get("timeNano", "0")) // 1000000
            if server_t > 0:
                self._time_offset = server_t - (
                    (local_b + int(time.time() * 1000)) // 2
                )
                self._time_synced = True
        except Exception as e:
            logger.warning("Time sync failed: %s", e)

    def _get_timestamp(self) -> str:
        if not self._time_synced:
            self._sync_time()
        return str(int(time.time() * 1000) + self._time_offset)

    def _sign(self, payload: str, ts: str) -> str:
        msg = f"{ts}{self.config.api_key}{self.config.recv_window}{payload}"
        return hmac.new(
            self.config.api_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_data: dict = None,
        signed: bool = True,
    ) -> dict:
        self.limiter.acquire()
        if self.breaker.state == CircuitState.OPEN:
            raise RuntimeError("Circuit OPEN - request aborted")

        # Cleanup dicts
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        if json_data:
            json_data = {k: v for k, v in json_data.items() if v is not None}

        ts = self._get_timestamp()
        url = self.config.base_url + endpoint
        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4())[:8],
        }

        sign_payload = ""
        if signed:
            if method == "GET":
                sign_payload = "&".join(
                    f"{k}={str(v).lower() if isinstance(v, bool) else v}"
                    for k, v in sorted((params or {}).items())
                )
            else:
                sign_payload = json.dumps(
                    json_data or {}, sort_keys=True, separators=(",", ":")
                )

            headers.update(
                {
                    "X-BAPI-API-KEY": self.config.api_key,
                    "X-BAPI-TIMESTAMP": ts,
                    "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
                    "X-BAPI-SIGN": self._sign(sign_payload, ts),
                }
            )

        try:
            data = self.net.request(
                method,
                url,
                headers,
                params if method == "GET" else None,
                json_data if method != "GET" else None,
                signed,
            )
            ret_code = data.get("retCode", 0)
            if ret_code != 0:
                self.breaker.record_failure()
                return {
                    "status": "error",
                    "code": ret_code,
                    "msg": data.get("retMsg", "Unknown"),
                }
            self.breaker.record_success()
            return data
        except Exception as e:
            self.breaker.record_failure()
            logger.error("API Request failed: %s", e)
            return {"status": "error", "msg": str(e)}

    # ── Formatting & Instrument Caching ───────────────────────────────────────
    def _fetch_instrument(self, symbol: str, category: str) -> InstrumentInfo:
        cache_key = f"{symbol}_{category}"
        with self._cache_lock:
            if (
                cache_key in self._instr_cache
                and time.time() - self._instr_cache[cache_key].fetched_at < 3600
            ):
                return self._instr_cache[cache_key]

        res = self.api_request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        item = res.get("result", {}).get("list", [{}])[0]
        lot = item.get("lotSizeFilter", {})
        pft = item.get("priceFilter", {})

        info = InstrumentInfo(
            lot_size=LotSizeFilter(
                float(lot.get("qtyStep", 1)),
                float(lot.get("minOrderQty", 0)),
                float(lot.get("maxOrderQty", 1e9)),
            ),
            price_flt=PriceFilter(
                float(pft.get("tickSize", 0.01)),
                float(pft.get("minPrice", 0)),
                float(pft.get("maxPrice", 1e12)),
            ),
            symbol=symbol,
        )
        with self._cache_lock:
            self._instr_cache[cache_key] = info
        return info

    def adjust_qty(self, symbol: str, qty: float, category: str) -> float:
        return self._fetch_instrument(symbol, category).lot_size.adjust(qty)

    def adjust_price(self, symbol: str, price: float, category: str) -> float:
        return self._fetch_instrument(symbol, category).price_flt.adjust(price)

    def get_fee_rate(self, symbol: str, category: str = "linear") -> dict:
        """Fetches dynamic fee rates (Maker/Taker) for the account."""
        res = self.api_request(
            "GET",
            "/v5/account/fee-rate",
            params={"category": category, "symbol": symbol},
        )
        list_data = res.get("result", {}).get("list", [{}])
        if not list_data:
            return {"status": "error", "msg": "No fee data"}
        return {
            "symbol": symbol,
            "maker": float(list_data[0].get("makerFeeRate", 0.0002)),
            "taker": float(list_data[0].get("takerFeeRate", 0.0006)),
        }

    def get_market_regime(
        self,
        symbol: str,
        interval: str = "60",
        lookback: int = 100,
        category: str = "linear",
    ) -> dict:
        """Classifies market as TRENDING_UP, TRENDING_DOWN, RANGING, or VOLATILE."""
        klines = self.get_klines(symbol, interval, limit=lookback, category=category)
        if len(klines) < 30:
            return {"status": "error", "msg": "Insufficient data"}

        closes = [k[4] for k in klines]
        ema_short = statistics.mean(closes[:10])
        ema_long = statistics.mean(closes[:30])

        returns = [
            ((closes[i] - closes[i + 1]) / closes[i + 1])
            for i in range(len(closes) - 1)
        ]
        volatility = statistics.stdev(returns) * 100

        if volatility > 2.0:
            regime = "VOLATILE"
        elif ema_short > ema_long * 1.002:
            regime = "TRENDING_UP"
        elif ema_short < ema_long * 0.998:
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGING"

        return {"symbol": symbol, "regime": regime, "volatility": round(volatility, 4)}

    # ── Market Data ───────────────────────────────────────────────────────────
    def get_mark_price(self, symbol: str, category: str = "linear") -> dict:
        """Ported from bybit_mark_price.sh"""
        res = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        items = res.get("result", {}).get("list", [])
        if not items:
            return {"status": "error", "msg": "No data"}
        return {
            "symbol": symbol,
            "category": category,
            "mark_price": float(items[0].get("markPrice", 0)),
        }

    def get_ticker(self, symbol: str, category: str = "linear") -> dict:
        return self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 200,
        category: str = "linear",
    ) -> List[List[float]]:
        ckey = f"{symbol}_{interval}_{limit}_{category}"
        with self._cache_lock:
            if (
                ckey in self._klines_cache
                and time.time() - self._klines_cache[ckey][1] < 60
            ):
                return self._klines_cache[ckey][0]

        res = self.api_request(
            "GET",
            "/v5/market/kline",
            params={
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            signed=False,
        )
        data = res.get("result", {}).get("list", [])
        formatted = []
        for k in data:
            try:
                formatted.append(
                    [
                        int(k[0]),
                        float(k[1]),
                        float(k[2]),
                        float(k[3]),
                        float(k[4]),
                        float(k[5]),
                        float(k[6]),
                    ]
                )
            except:
                pass

        with self._cache_lock:
            self._klines_cache[ckey] = (formatted, time.time())
        return formatted

    def get_confluence(
        self,
        symbol: str,
        intervals: List[str] = ["5", "15", "60", "240"],
        category: str = "linear",
    ) -> dict:
        """Analyzes EMA trend and RSI momentum across multiple timeframes."""
        scores = []
        details = {}

        for tf in intervals:
            try:
                rsi_data = self.calculate_rsi(symbol, tf)
                ema_data = self.calculate_ema(symbol, tf)
                ticker = (
                    self.get_ticker(symbol, category)
                    .get("result", {})
                    .get("list", [{}])[0]
                )
                price = float(ticker.get("lastPrice", 0))

                rsi = rsi_data.get("rsi", 50)
                ema = ema_data.get("ema", price)

                # Scoring: Trend (EMA) + Momentum (RSI)
                trend = 1 if price > ema else -1
                momentum = 1 if rsi > 55 else (-1 if rsi < 45 else 0)

                details[tf] = {
                    "rsi": rsi,
                    "trend": "BULLISH" if trend > 0 else "BEARISH",
                    "momentum": momentum,
                }
                scores.append(trend + momentum)
            except Exception as e:
                logger.warning(f"Confluence failed for {tf}: {e}")

        total_score = sum(scores)
        max_possible = len(intervals) * 2

        if total_score >= max_possible * 0.7:
            recommendation = Signal.STRONG_BUY
        elif total_score > 0:
            recommendation = Signal.BUY
        elif total_score <= -max_possible * 0.7:
            recommendation = Signal.STRONG_SELL
        elif total_score < 0:
            recommendation = Signal.SELL
        else:
            recommendation = Signal.NEUTRAL

        return {
            "symbol": symbol,
            "recommendation": recommendation.value,
            "confluence_score": total_score,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_orderbook(
        self, symbol: str, limit: int = 50, category: str = "linear"
    ) -> dict:
        return self.api_request(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )

    def get_orderbook_analysis(
        self, symbol: str, depth: int = 50, category: str = "linear"
    ) -> dict:
        """Ported & Enhanced from bybit_orderBook.sh. Calculates liquidity based on volume depth."""
        raw = self.get_orderbook(symbol=symbol, category=category, limit=depth).get(
            "result", {}
        )
        bids = [(float(p), float(q)) for p, q in raw.get("b", [])]
        asks = [(float(p), float(q)) for p, q in raw.get("a", [])]
        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100

        # Depth within 0.1% of mid price
        depth_threshold = mid_price * 0.001
        bid_depth_01 = sum(q for p, q in bids if p >= mid_price - depth_threshold)
        ask_depth_01 = sum(q for p, q in asks if p <= mid_price + depth_threshold)
        total_depth_01 = bid_depth_01 + ask_depth_01

        total_bid_qty = sum(q for _, q in bids)
        total_ask_qty = sum(q for _, q in asks)
        total_vol = total_bid_qty + total_ask_qty
        imbalance = (
            ((total_bid_qty - total_ask_qty) / total_vol * 100) if total_vol > 0 else 0
        )

        # Improved Liquidity Zone logic: low spread AND high relative depth
        if spread_pct < 0.02 and total_depth_01 > (total_vol * 0.2):
            liquidity_zone = "thick"
        elif spread_pct < 0.1:
            liquidity_zone = "moderate"
        else:
            liquidity_zone = "thin"

        return {
            "symbol": symbol,
            "category": category,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round(mid_price, 6),
            "spread_pct": round(spread_pct, 6),
            "depth_01pct_total": round(total_depth_01, 2),
            "total_bid_qty": round(total_bid_qty, 2),
            "total_ask_qty": round(total_ask_qty, 2),
            "imbalance_pct": round(imbalance, 2),
            "liquidity_zone": liquidity_zone,
        }

    # ── Account & Positions ───────────────────────────────────────────────────
    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        return self.api_request(
            "GET", "/v5/account/wallet-balance", params={"accountType": account_type}
        )

    def get_positions(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        elif category == "linear":
            params["settleCoin"] = "USDT"
        return self.api_request("GET", "/v5/position/list", params=params)

    def get_account_summary(self) -> dict:
        """Aggregates equity, margin, and PnL info."""
        res = self.get_wallet_balance(account_type="UNIFIED")
        data = res.get("result", {}).get("list", [{}])[0]
        return {
            "total_equity": float(data.get("totalEquity", 0)),
            "total_margin_balance": float(data.get("totalMarginBalance", 0)),
            "total_available_balance": float(data.get("totalAvailableBalance", 0)),
            "total_perp_u_pnl": float(data.get("totalPerpUPL", 0)),
            "account_status": data.get("accountStatus", "Unknown"),
        }

    def panic_close(self, category: str = "linear") -> dict:
        """Cancels all orders and closes all positions immediately."""
        logger.warning("PANIC CLOSE TRIGGERED!")
        results = {"orders": self.cancel_all_orders(category=category)}

        pos_data = self.get_positions(category=category)
        positions = pos_data.get("result", {}).get("list", [])
        close_results = []
        for p in positions:
            size = float(p.get("size", 0))
            if size > 0:
                side = p["side"]
                close_side = "Sell" if side == "Buy" else "Buy"
                res = self.place_order(
                    symbol=p["symbol"],
                    side=close_side,
                    qty=size,
                    order_type="Market",
                    category=category,
                    reduce_only=True,
                    positionIdx=int(p.get("positionIdx", 0)),
                )
                close_results.append({"symbol": p["symbol"], "result": res})

        results["positions"] = close_results
        return results

    def check_equity_guard(self, max_drawdown_pct: float = 0.05) -> dict:
        """Triggers panic_close if equity drawdown exceeds threshold."""
        summary = self.get_account_summary()
        equity = summary["total_equity"]

        # We need a reference point. For simplicity in this CLI tool,
        # we'll use 'totalMarginBalance' or a cached initial value if available.
        # In a long-running bot, this would be initialized at start.
        initial_equity = summary["total_margin_balance"]  # Approximation
        if initial_equity <= 0:
            return {"status": "skipped", "msg": "No equity to guard"}

        drawdown = (initial_equity - equity) / initial_equity
        if drawdown >= max_drawdown_pct:
            return {
                "status": "triggered",
                "drawdown": drawdown,
                "panic": self.panic_close(),
            }

        return {"status": "ok", "drawdown": drawdown}

    def get_dashboard(self) -> dict:
        """High-level dashboard data for the Terminal."""
        acc = self.get_account_summary()
        pos_data = self.get_positions(category="linear")
        positions = pos_data.get("result", {}).get("list", [])
        active_pos = []
        for p in positions:
            if float(p.get("size", 0)) > 0:
                active_pos.append(
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "size": p["size"],
                        "pnl": p["unrealisedPnl"],
                        "pnl_pct": f"{float(p.get('unrealisedPnl', 0)) / (float(p.get('positionValue', 1)) or 1) * 100:.2f}%",
                    }
                )

        return {
            "account": {
                "total_equity": acc["total_equity"],
                "total_wallet_balance": acc["total_margin_balance"],
                "unrealised_pnl": acc["total_perp_u_pnl"],
                "total_margin_balance": acc["total_margin_balance"],
                "total_available_balance": acc["total_available_balance"],
            },
            "active_positions": active_pos,
            "positions_count": len(active_pos),
            "performance": {
                "win_rate_pct": 0,
                "profit_factor": 0,
                "net_pnl": 0,
            },  # Placeholder
        }

    def set_leverage(
        self, symbol: str, leverage: int, category: str = "linear"
    ) -> dict:
        return self.api_request(
            "POST",
            "/v5/position/set-leverage",
            json_data={
                "category": category,
                "symbol": symbol,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
        )

    def manage_position(
        self,
        symbol: str,
        action: str = "be",
        profit_usdt: float = 50,
        fee_rate: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        """Ported from position manager Python script (be/close). Enhanced with fee-adjusted BE and positionIdx fix."""
        pos_data = self.get_positions(category=category, symbol=symbol)
        positions = pos_data.get("result", {}).get("list", [])
        pos = next((p for p in positions if float(p.get("size", 0)) > 0), None)
        if not pos:
            return {"status": "error", "msg": "No open position found"}

        size = float(pos["size"])
        entry_price = float(pos["avgPrice"])
        unrealized_pnl = float(pos["unrealisedPnl"])
        side = pos["side"]
        pos_idx = int(pos.get("positionIdx", 0))

        # Dynamic Fee Discovery
        if fee_rate is None:
            fees = self.get_fee_rate(symbol, category)
            fee_rate = fees.get("taker", 0.0006)

        ticker = self.get_ticker(symbol, category)
        current_price = float(
            ticker.get("result", {}).get("list", [{}])[0].get("lastPrice", 0)
        )

        # Fee is charged twice (entry + exit). We use an estimate based on current/entry price.
        estimated_fees = size * (entry_price + current_price) * fee_rate
        net_profit = unrealized_pnl - estimated_fees

        if action == "close":
            if net_profit < profit_usdt:
                return {
                    "status": "skipped",
                    "msg": f"Net profit {net_profit:.2f} < threshold {profit_usdt}",
                }
            close_side = "Sell" if side == "Buy" else "Buy"
            return self.place_order(
                symbol=symbol,
                side=close_side,
                qty=size,
                order_type="Market",
                category=category,
                reduce_only=True,
                positionIdx=pos_idx,
            )

        elif action == "be":
            # True Break-Even: adjust for two-way fees
            if side == "Buy":
                be_price = entry_price * (1 + 2 * fee_rate)
            else:
                be_price = entry_price * (1 - 2 * fee_rate)

            adj_be = self.adjust_price(symbol, be_price, category)

            return self.api_request(
                "POST",
                "/v5/position/trading-stop",
                json_data={
                    "category": category,
                    "symbol": symbol,
                    "stopLoss": str(adj_be),
                    "slTriggerBy": "MarkPrice",
                    "tpslMode": "Full",
                    "positionIdx": pos_idx,
                },
            )

    # ── Order Management ──────────────────────────────────────────────────────
    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: Optional[float] = None,
        order_type: str = "Limit",
        category: str = "linear",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
        client_oid: Optional[str] = None,
        iceberg_slices: Optional[int] = None,
        **kwargs,
    ) -> dict:
        """Ported from bybit_order_create.sh & merged with realm logic. Supports Iceberg slicing."""
        if iceberg_slices and iceberg_slices > 1:
            results = []
            slice_qty = qty / iceberg_slices
            for i in range(iceberg_slices):
                slice_res = self.place_order(
                    symbol=symbol,
                    side=side,
                    qty=slice_qty,
                    price=price,
                    order_type=order_type,
                    category=category,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reduce_only=reduce_only,
                    time_in_force=time_in_force,
                    client_oid=f"{client_oid}_{i}" if client_oid else None,
                    **kwargs,
                )
                results.append(slice_res)
                if i < iceberg_slices - 1:
                    time.sleep(self.config.iceberg_delay)
            return {"status": "ok", "iceberg_results": results}

        adj_qty = self.adjust_qty(symbol, qty, category)
        payload = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(adj_qty),
            "timeInForce": time_in_force,
        }
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if reduce_only:
            payload["reduceOnly"] = True
        if client_oid:
            payload["orderLinkId"] = client_oid
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = str(v)

        res = self.api_request("POST", "/v5/order/create", json_data=payload)
        self.journal.record("place_order", payload, res, symbol)
        return res

    def cancel_all_orders(
        self, symbol: Optional[str] = None, category: str = "linear"
    ) -> dict:
        payload = {"category": category}
        if symbol:
            payload["symbol"] = symbol
        return self.api_request("POST", "/v5/order/cancel-all", json_data=payload)

    # ── Technical Indicators ──────────────────────────────────────────────────
    def _extract_klines(self, klines):
        return (
            [float(k[1]) for k in klines],
            [float(k[2]) for k in klines],
            [float(k[3]) for k in klines],
            [float(k[4]) for k in klines],
            [float(k[5]) for k in klines],
        )

    def calculate_ema(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(symbol, interval, limit=period + 50)
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error"}
        k = 2 / (period + 1)
        ema = closes[0]
        for p in closes[1:]:
            ema = p * k + ema * (1 - k)
        return {"ema": round(ema, 4)}

    def calculate_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(symbol, interval, limit=period + 50)
        closes = [float(k[4]) for k in reversed(klines)]
        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return {"rsi": 100.0}
        return {"rsi": round(100 - (100 / (1 + (avg_gain / avg_loss))), 2)}

    # ── Micro Profit & Macros ─────────────────────────────────────────────────
    def calculate_micro_profit(
        self,
        symbol: str,
        side: str,
        qty: float,
        target: float = 5.0,
        leverage: int = 1,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.00055,
        risk_reward: float = 2.0,
        bids_json: str = "",
        asks_json: str = "",
    ) -> dict:
        """Ported from micro_profit.py"""
        if bids_json and asks_json:
            bids = ast.literal_eval(bids_json)
            asks = ast.literal_eval(asks_json)
        else:
            ob = self.get_orderbook(symbol, limit=20).get("result", {})
            bids = [[float(p), float(q)] for p, q in ob.get("b", [])]
            asks = [[float(p), float(q)] for p, q in ob.get("a", [])]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread = best_ask - best_bid

        exit_price = best_bid * (1 + risk_reward * target / (best_bid * qty))

        return {
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "entry_price": best_bid,
            "spread": spread,
            "target_profit_usdt": target,
            "leverage": leverage,
            "exit_price": exit_price,
            "risk_reward_ratio": risk_reward,
        }

    def calculate_usdt_targets(
        self,
        entry_price: float,
        position_size: float,
        leverage: int = 1,
        target_usdt: float = 5.0,
        risk_reward: float = 2.0,
        max_risk_pct: float = 2.0,
        num_levels: int = 5,
    ) -> dict:
        """Calculate USDT profit targets using target_analysis logic."""
        calculator = USDTTargetCalculator(
            entry_price,
            position_size,
            leverage,
            maker_fee=0.0002,
            taker_fee=0.0004,
            account_balance=10000,
        )
        return calculator.calculate_profit_targets_usdt(
            target_usdt=target_usdt,
            risk_reward=risk_reward,
            max_risk_pct=max_risk_pct,
            num_levels=num_levels,
        )

    def run_macro(self, macro_name: str) -> dict:
        """Ported from profit_macros.sh"""
        if macro_name == "basic_scalp":
            return self.calculate_micro_profit(
                "BTCUSDT",
                "Buy",
                0.001,
                target=5.0,
                leverage=1,
                risk_reward=2.0,
                bids_json="[[50000, 10], [49990, 15], [49980, 20]]",
                asks_json="[[50010, 10], [50020, 15], [50030, 20]]",
            )
        elif macro_name == "leverage_momentum":
            return self.calculate_micro_profit(
                "ETHUSDT",
                "Buy",
                0.01,
                target=10.0,
                leverage=3,
                risk_reward=2.5,
                bids_json="[[3000, 100], [2995, 200], [2990, 150]]",
                asks_json="[[3005, 100], [3010, 200], [3015, 150]]",
            )
        elif macro_name == "wall_surfing":
            return self.calculate_micro_profit(
                "SOLUSDT",
                "Sell",
                0.1,
                target=15.0,
                leverage=1,
                risk_reward=2.0,
                bids_json="[[150, 500], [149.5, 800], [149, 300]]",
                asks_json="[[150.5, 500], [151, 800], [151.5, 300]]",
            )
        return {"status": "error", "msg": f"Unknown macro {macro_name}"}


# ─────────────────────────────────────────────────────────────
# UNIFIED ENTRY POINT
# ─────────────────────────────────────────────────────────────
_realm_instance: Optional[BybitRealm] = None
_realm_lock = threading.Lock()


def get_realm() -> BybitRealm:
    global _realm_instance
    if _realm_instance is None:
        with _realm_lock:
            if _realm_instance is None:
                _realm_instance = BybitRealm()
    return _realm_instance


def run(
    action: Literal[
        "health_check",
        "get_wallet_balance",
        "get_positions",
        "set_leverage",
        "manage_position",
        "place_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_orderbook_analysis",
        "get_mark_price",
        "get_ticker",
        "get_klines",
        "calculate_rsi",
        "calculate_ema",
        "calculate_micro_profit",
        "macro_basic_scalp",
        "macro_leverage_momentum",
        "macro_wall_surfing",
        "get_fee_rate",
        "get_market_regime",
        "get_confluence",
        "calculate_usdt_targets",
        "get_account_summary",
        "panic_close",
        "check_equity_guard",
        "get_dashboard",
    ],
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_type: str = "Limit",
    category: str = "linear",
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    time_in_force: str = "GTC",
    client_oid: Optional[str] = None,
    leverage: Optional[int] = None,
    profit_usdt: Optional[float] = 50,
    fee_rate: Optional[float] = 0.0006,
    target: Optional[float] = 5.0,
    maker_fee: Optional[float] = 0.0002,
    taker_fee: Optional[float] = 0.00055,
    risk_reward: Optional[float] = 2.0,
    kelly_win: Optional[float] = 0.55,
    limit: int = 50,
    interval: str = "60",
    iceberg_slices: Optional[int] = None,
    **kwargs,
) -> dict:
    """
    Bybit Realm Unified Tool execution.
    Maintains 100% parameter compatibility with legacy toolsets.
    """
    bot = get_realm()

    try:
        # Informational & Diagnostic
        if action == "health_check":
            return {"status": "ok", "circuit": bot.breaker.state.value}
        elif action == "get_wallet_balance":
            return bot.get_wallet_balance()
        elif action == "get_positions":
            return bot.get_positions(category=category, symbol=symbol)
        elif action == "get_fee_rate":
            return bot.get_fee_rate(symbol, category)
        elif action == "get_account_summary":
            return bot.get_account_summary()
        elif action == "panic_close":
            return bot.panic_close(category)
        elif action == "get_dashboard":
            return bot.get_dashboard()
        elif action == "check_equity_guard":
            return bot.check_equity_guard(float(kwargs.get("max_drawdown", 0.05)))

        # Market Data
        elif action == "get_mark_price":
            return bot.get_mark_price(symbol, category)
        elif action == "get_ticker":
            return bot.get_ticker(symbol, category)
        elif action == "get_klines":
            return {"klines": bot.get_klines(symbol, interval, limit, category)}
        elif action == "get_orderbook_analysis":
            return bot.get_orderbook_analysis(symbol, limit, category)
        elif action == "get_market_regime":
            return bot.get_market_regime(symbol, interval, limit, category)
        elif action == "get_confluence":
            intervals = kwargs.get("intervals", ["5", "15", "60", "240"])
            if isinstance(intervals, str):
                intervals = [i.strip() for i in intervals.split(",")]
            return bot.get_confluence(symbol, intervals, category)

        # Orders & Positions
        elif action == "set_leverage":
            return bot.set_leverage(symbol, leverage, category)
        elif action == "place_order":
            if leverage is not None:
                bot.set_leverage(symbol, leverage, category)
            return bot.place_order(
                symbol,
                side,
                qty,
                price,
                order_type,
                category,
                stop_loss,
                take_profit,
                time_in_force=time_in_force,
                client_oid=client_oid,
                iceberg_slices=iceberg_slices,
                **kwargs,
            )
        elif action == "cancel_all_orders":
            return bot.cancel_all_orders(symbol, category)
        elif action == "manage_position":
            return bot.manage_position(
                symbol,
                kwargs.get("position_action", "be"),
                profit_usdt,
                fee_rate,
                category,
            )

        # Analysis
        elif action == "calculate_rsi":
            return bot.calculate_rsi(symbol, interval, int(kwargs.get("period", 14)))
        elif action == "calculate_ema":
            return bot.calculate_ema(symbol, interval, int(kwargs.get("period", 20)))

        # Micro Profit & Macros
        elif action == "calculate_micro_profit":
            return bot.calculate_micro_profit(
                symbol,
                side,
                qty,
                target,
                leverage or 1,
                maker_fee,
                taker_fee,
                risk_reward,
                kwargs.get("bids_json", ""),
                kwargs.get("asks_json", ""),
            )
        elif action in [
            "macro_basic_scalp",
            "macro_leverage_momentum",
            "macro_wall_surfing",
        ]:
            return bot.run_macro(action.replace("macro_", ""))

        elif action == "calculate_usdt_targets":
            # Extract parameters
            entry_price = kwargs.get("entry_price")
            position_size = kwargs.get("position_size")
            leverage = kwargs.get("leverage", 1)
            target_usdt = kwargs.get("target_usdt", 5.0)
            risk_reward = kwargs.get("risk_reward", 2.0)
            max_risk_pct = kwargs.get("max_risk_pct", 2.0)
            num_levels = kwargs.get("num_levels", 5)
            if entry_price is None or position_size is None:
                return {
                    "status": "error",
                    "msg": "entry_price and position_size are required",
                }
            return bot.calculate_usdt_targets(
                entry_price,
                position_size,
                leverage,
                target_usdt,
                risk_reward,
                max_risk_pct,
                num_levels,
            )

        else:
            return {"status": "error", "msg": f"Unknown action {action}"}

    except Exception as e:
        logger.error(f"run({action}) failed: {e}", exc_info=True)
        return {"status": "error", "msg": str(e)}


# ─────────────────────────────────────────────────────────────
# CLI EXECUTABLE
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bybit Realm v5.0")
    parser.add_argument("--action", required=True)
    parser.add_argument("--symbol")
    parser.add_argument("--side", choices=["Buy", "Sell"])
    parser.add_argument("--qty", type=float)
    parser.add_argument("--price", type=float)
    parser.add_argument("--category", default="linear")
    parser.add_argument("--order-type", default="Limit")
    parser.add_argument("--time-in-force", default="GTC")
    parser.add_argument("--leverage", type=int)
    parser.add_argument("--profit-usdt", type=float, default=50)
    parser.add_argument("--position-action", default="be")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--interval", default="60")
    parser.add_argument("--export")

    args, unknown = parser.parse_known_args()
    kwargs = vars(args)

    # Parse remaining dynamic kwargs
    i = 0
    while i < len(unknown):
        key = unknown[i].lstrip("-").replace("-", "_")
        if i + 1 < len(unknown) and not unknown[i + 1].startswith("-"):
            try:
                kwargs[key] = (
                    float(unknown[i + 1])
                    if "." in unknown[i + 1]
                    else int(unknown[i + 1])
                )
            except:
                kwargs[key] = unknown[i + 1]
            i += 2
        else:
            kwargs[key] = True
            i += 1

    result = run(**kwargs)

    if args.export:
        with open(args.export, "w") as f:
            json.dump(result, f, indent=2)
    else:
        print(json.dumps(result, indent=2))

    sys.exit(0 if result.get("status") != "error" else 1)
