#!/usr/bin/env python3
"""
BYBIT REALM - Production-Grade Trading System Tool for LLM Functions v4.1

A comprehensive Bybit trading tool that provides:
• Symbol-Aware Quantity Engine  (lotSizeFilter auto-query + cache)
• Circuit Breaker & Resilience  (exponential backoff + auto-reset)
• High-Performance Batch Orders (POST /v5/order/create-batch)
• Market Intelligence           (order flow imbalance + momentum + VWAP + RSI + BB)
• Multi-Tier Network Failover   (Proxy → Torsocks → Direct)
• Position & Risk Management    (PnL tracking, stop-loss, take-profit)
• Rate Limiter                  (sliding window, prevents 429 errors)
• Open Interest & Liquidations  (market structure analysis)
• Order History & PnL Report    (closed trade analytics)
• Iceberg Orders                (large order splitting)
• Trailing Stop                 (dynamic risk management)
• Price Precision Engine        (tickSize enforcement)
• Server Time Sync              (clock skew prevention for auth)
• Get Server Time               (public endpoint)
• Conditional Orders            (stop/trigger-based entry)
• Account Info                  (UTA mode, margin, fee rates)
• Instrument Search             (filter by base coin, status)
• Spread Analysis               (bid-ask spread + depth imbalance)
• ATR-Based Position Sizing     (volatility-adjusted sizing)
• Multi-Symbol Batch Queries    (tickers for a list of symbols)

Usage:
    Set environment variables BYBIT_API_KEY and BYBIT_API_SECRET before use.
    Optional: BYBIT_USE_TESTNET, TOR_ENABLED, TOR_SOCKS_PORT, TOR_CONTROL_PORT

Changes v4.1:
    • Fixed duplicate adjust_quantity method
    • Fixed hmac.new → hmac.new (correct API)
    • Fixed load_env_file() logger ordering (deferred log messages)
    • Fixed TorManager tier ordering logic (property checked correctly)
    • Fixed Circuit breaker deadlock risk in _on_failure
    • Fixed renew_tor_identity CRLF line endings
    • Fixed api_request_with_retry missing signed= passthrough
    • Added __all__ for clean LLM tool imports
    • Added MomentumResult.to_dict() consistency in run()
    • Added input validation helpers to reduce boilerplate
    • Added get_mark_price() convenience method
    • Added get_index_price() convenience method
    • Added place_order_with_sizing() convenience composite method
    • Improved type annotations throughout
    • Improved logging consistency (no f-strings in logger calls)
    • Added cache invalidation method
    • Added TorManager session storage for reuse
"""

import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

# ── Optional dependencies ─────────────────────────────────────
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# LOGGING  (must be defined before load_env_file is called)
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BybitRealm")


# ── .env file loading ──────────────────────────────────────────
def load_env_file() -> None:
    """Load .env file if present. Safe to call after logger is initialised."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        logger.info("Loaded .env file via python-dotenv: %s", env_path)
    except ImportError:
        # Fallback: manual .env parsing (no dependency required)
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())
        logger.info("Loaded .env file (manual parse): %s", env_path)


# Load .env on module import (logger is now defined above)
load_env_file()


# ─────────────────────────────────────────────────────────────
# PUBLIC API SURFACE
# ─────────────────────────────────────────────────────────────
__all__ = [
    "BybitToolDispatcher",
    "Category",
    "CircuitState",
    "InstrumentInfo",
    "LotSizeFilter",
    "MomentumResult",
    "OrderSide",
    "OrderType",
    "PnLReport",
    "PositionIdx",
    "PriceFilter",
    "Signal",
    "TimeInForce",
    "TradingConfig",
    "TriggerBy",
    "run",
]


# ─────────────────────────────────────────────────────────────
# ENUMS
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


class TriggerBy(str, Enum):
    LAST_PRICE = "LastPrice"
    INDEX_PRICE = "IndexPrice"
    MARK_PRICE = "MarkPrice"


# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
@dataclass
class TradingConfig:
    """Central configuration – all values sourced from environment variables."""

    # ── Auth ──────────────────────────────────────────────────
    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))

    # ── Network ───────────────────────────────────────────────
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
    tor_control_port: int = field(
        default_factory=lambda: int(os.getenv("TOR_CONTROL_PORT", "9051"))
    )
    request_timeout: int = 15
    max_retries: int = 3

    # ── Clock sync ────────────────────────────────────────────
    clock_sync_threshold_ms: int = 500

    # ── Circuit Breaker ───────────────────────────────────────
    cb_failure_threshold: int = 5
    cb_recovery_timeout: float = 60.0
    cb_cooldown: float = 30.0

    # ── Rate Limiting ─────────────────────────────────────────
    rate_limit_calls: int = 10
    rate_limit_window: float = 1.0

    # ── Risk Management ───────────────────────────────────────
    max_position_usdt: float = 1000.0
    default_leverage: int = 1
    default_stop_loss: float = 0.02
    default_take_profit: float = 0.04
    max_orders_per_batch: int = 20

    # ── Iceberg ───────────────────────────────────────────────
    iceberg_min_slices: int = 3
    iceberg_max_slices: int = 10
    iceberg_delay: float = 0.5

    @property
    def base_url(self) -> str:
        """Determine the appropriate Bybit API base URL."""
        base = (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )
        return base

    def validate(self) -> None:
        """Raise ValueError if required credentials are missing."""
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "BYBIT_API_KEY and BYBIT_API_SECRET must be set "
                "as environment variables."
            )


# ─────────────────────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────────────────────
class RateLimiter:
    """Sliding-window rate limiter – thread-safe."""

    def __init__(self, max_calls: int, window: float) -> None:
        self._max_calls = max_calls
        self._window = window
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a call slot is available within the current window."""
        with self._lock:
            now = time.monotonic()
            # Evict timestamps outside the sliding window
            while self._calls and self._calls[0] <= now - self._window:
                self._calls.popleft()

            if len(self._calls) >= self._max_calls:
                sleep_for = self._window - (now - self._calls[0])
                if sleep_for > 0:
                    logger.debug("Rate limiter sleeping %.3fs", sleep_for)
                    time.sleep(sleep_for)

            self._calls.append(time.monotonic())

    @property
    def current_usage(self) -> int:
        """Return number of calls consumed in current window."""
        now = time.monotonic()
        with self._lock:
            return sum(1 for c in self._calls if c > now - self._window)


# ─────────────────────────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────────────────────────
class CircuitBreaker:
    """
    Three-state circuit breaker with thread-safe state transitions.

    States:
        CLOSED    – normal operation
        OPEN      – all calls rejected, waiting for recovery_timeout
        HALF_OPEN – one probe call allowed; success → CLOSED, fail → OPEN
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        cooldown: float = 30.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._cooldown = cooldown
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_ts = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute fn through the circuit breaker."""
        with self._lock:
            self._maybe_transition()
            if self._state == CircuitState.OPEN:
                wait = self._recovery_timeout - (
                    time.monotonic() - self._last_failure_ts
                )
                raise RuntimeError(f"Circuit OPEN – retry in {max(0.0, wait):.1f}s")
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("Circuit manually reset → CLOSED")

    # ── Internal transition helpers ───────────────────────────

    def _maybe_transition(self) -> None:
        """Called while holding self._lock."""
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_ts >= self._recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("Circuit → HALF_OPEN (testing recovery)")

    def _on_success(self) -> None:
        with self._lock:
            prev = self._state
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            if prev != CircuitState.CLOSED:
                logger.info("Circuit → CLOSED (recovered)")

    def _on_failure(self) -> None:
        # FIX: capture cooldown value before sleeping outside the lock
        # to avoid holding the lock during a sleep() call.
        do_cooldown = False
        cooldown_duration = 0.0

        with self._lock:
            self._failure_count += 1
            self._last_failure_ts = time.monotonic()
            logger.warning(
                "Circuit failure %d/%d", self._failure_count, self._threshold
            )
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failure_count >= self._threshold
            ):
                self._state = CircuitState.OPEN
                do_cooldown = True
                cooldown_duration = self._cooldown
                logger.error("Circuit → OPEN (cooldown %.0fs)", cooldown_duration)

        # Sleep OUTSIDE the lock so other threads are not blocked
        if do_cooldown:
            time.sleep(cooldown_duration)


# ─────────────────────────────────────────────────────────────
# TOR / NETWORK MANAGER
# ─────────────────────────────────────────────────────────────
class TorManager:
    """
    Multi-tier network layer.

    Tier 1 → SOCKS5 proxy via requests (if Tor enabled + reachable)
    Tier 2 → torsocks binary via subprocess (if binary found)
    Tier 3 → direct HTTPS connection (always available as fallback)
    """

    def __init__(
        self,
        enabled: bool,
        socks_port: int,
        timeout: int,
        max_retries: int,
        control_port: int = 9051,
    ) -> None:
        self.enabled = enabled
        self.socks_port = socks_port
        self.control_port = control_port
        self.timeout = timeout
        self.max_retries = max_retries
        self._proxy_url = f"socks5h://127.0.0.1:{socks_port}" if enabled else None
        self._torsocks_bin = shutil.which("torsocks")
        # Build a shared session once; reuse across all requests
        self._session = self._build_session(max_retries) if REQUESTS_AVAILABLE else None

    # ── Public interface ──────────────────────────────────────

    def request(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict:
        """
        Try each network tier in priority order.
        Returns the first successful response dict.
        Raises ConnectionError if all tiers fail.
        """
        tiers: List[Callable] = []

        # Tier 1: SOCKS5 proxy (only when Tor is enabled and reachable)
        if self.enabled:
            if self._is_tor_reachable():
                tiers.append(self._tier_proxy)
            if self._torsocks_bin:
                tiers.append(self._tier_torsocks)

        # Tier 3: direct connection (always present)
        tiers.append(self._tier_direct)

        last_exc: Optional[Exception] = None
        tor_tiers_failed = 0

        for tier in tiers:
            try:
                logger.debug("Trying network tier: %s", tier.__name__)
                return tier(method, url, headers, params, json_data)
            except Exception as exc:
                last_exc = exc
                logger.warning("Network tier %s failed: %s", tier.__name__, exc)

                # Track consecutive Tor-tier failures for identity renewal
                if tier in (self._tier_proxy, self._tier_torsocks):
                    tor_tiers_failed += 1
                    if tor_tiers_failed >= 2 and self.enabled:
                        logger.warning(
                            "Multiple Tor tiers failed; attempting identity renewal"
                        )
                        self._safe_renew_identity()
                continue

        raise ConnectionError(
            f"All network tiers exhausted for {url}. Last: {last_exc}"
        )

    # ── Tier implementations ──────────────────────────────────

    def _tier_proxy(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict],
        json_data: Optional[dict],
    ) -> dict:
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not installed")
        proxies = {"http": self._proxy_url, "https": self._proxy_url}
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            proxies=proxies,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse_bybit_response(resp.json())

    def _tier_torsocks(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict],
        json_data: Optional[dict],
    ) -> dict:
        if not self._torsocks_bin:
            raise RuntimeError("torsocks binary not found")

        cmd = [self._torsocks_bin, "curl", "-s", "-X", method]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data)]
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        cmd.append(url)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout + 5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"torsocks exited {result.returncode}: {result.stderr}")
        return self._parse_bybit_response(json.loads(result.stdout))

    def _tier_direct(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict],
        json_data: Optional[dict],
    ) -> dict:
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not installed")
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse_bybit_response(resp.json())

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _parse_bybit_response(data: Any) -> dict:
        """Raise RuntimeError for Bybit API-level errors (retCode != 0)."""
        if isinstance(data, dict) and data.get("retCode", 0) != 0:
            raise RuntimeError(
                f"Bybit API error {data.get('retCode')}: {data.get('retMsg')}"
            )
        return data

    @staticmethod
    def _build_session(max_retries: int) -> "requests.Session":
        session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _is_tor_reachable(self) -> bool:
        """Return True if the SOCKS5 port is accepting connections."""
        if not self.enabled:
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            reachable = sock.connect_ex(("127.0.0.1", self.socks_port)) == 0
            sock.close()
            return reachable
        except Exception:
            return False

    def _safe_renew_identity(self) -> None:
        """Attempt identity renewal; log but never raise."""
        try:
            self.renew_tor_identity()
        except Exception as exc:
            logger.warning("Tor identity renewal failed: %s", exc)

    def renew_tor_identity(self) -> None:
        """
        Send NEWNYM signal via the Tor control port to obtain a new circuit.
        Requires ControlPort to be enabled in torrc.
        Uses CRLF line endings as required by the Tor control protocol.
        """
        logger.info("Sending NEWNYM to Tor control port %d", self.control_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect(("127.0.0.1", self.control_port))

            # Authenticate with empty password (works when CookieAuthentication
            # is not set and HashedControlPassword is unset)
            sock.sendall(b"AUTHENTICATE\r\n")
            auth_resp = sock.recv(1024)
            if not auth_resp.startswith(b"250"):
                raise RuntimeError(
                    f"Tor auth failed: {auth_resp.decode(errors='replace')}"
                )

            sock.sendall(b"SIGNAL NEWNYM\r\n")
            newnym_resp = sock.recv(1024)
            if not newnym_resp.startswith(b"250"):
                raise RuntimeError(
                    f"NEWNYM failed: {newnym_resp.decode(errors='replace')}"
                )

            logger.info("Tor identity renewed successfully")
        finally:
            sock.close()


# ─────────────────────────────────────────────────────────────
# INSTRUMENT CACHE
# ─────────────────────────────────────────────────────────────
@dataclass
class LotSizeFilter:
    """Quantity constraints for a trading instrument."""

    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> float:
        """Round qty to the nearest valid step and clamp to [min, max]."""
        if self.qty_step <= 0:
            return qty
        # Determine decimal precision from step size
        precision = max(0, -int(math.floor(math.log10(self.qty_step))))
        adjusted = round(round(qty / self.qty_step) * self.qty_step, precision)
        return float(max(self.min_order_qty, min(self.max_order_qty, adjusted)))


@dataclass
class PriceFilter:
    """Price constraints for a trading instrument."""

    tick_size: float
    min_price: float = 0.0
    max_price: float = 1e12

    def adjust(self, price: float) -> float:
        """Round price to the nearest valid tick and clamp to [min, max]."""
        if self.tick_size <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(self.tick_size))))
        adjusted = round(round(price / self.tick_size) * self.tick_size, precision)
        return float(max(self.min_price, min(self.max_price, adjusted)))


@dataclass
class InstrumentInfo:
    """Cached instrument metadata."""

    lot_size: LotSizeFilter
    price_flt: PriceFilter
    symbol: str
    status: str = "Trading"
    fetched_at: float = field(default_factory=time.time)

    _CACHE_TTL_S: float = field(default=3600.0, init=False, repr=False, compare=False)

    @property
    def is_stale(self) -> bool:
        """True if cached data is older than 1 hour."""
        return time.time() - self.fetched_at > self._CACHE_TTL_S


# ─────────────────────────────────────────────────────────────
# MARKET INTELLIGENCE DATA CLASSES
# ─────────────────────────────────────────────────────────────
@dataclass
class MomentumResult:
    """Result of a market momentum / order-flow analysis."""

    symbol: str
    imbalance: float
    signal: Signal
    buy_vol: float
    sell_vol: float
    vwap: float = 0.0
    avg_trade_sz: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "imbalance": round(self.imbalance, 4),
            "signal": self.signal.value,
            "buy_vol": round(self.buy_vol, 4),
            "sell_vol": round(self.sell_vol, 4),
            "vwap": round(self.vwap, 4),
            "avg_trade_sz": round(self.avg_trade_sz, 4),
            "timestamp": self.timestamp,
        }


@dataclass
class PnLReport:
    """Aggregated closed-trade PnL statistics."""

    symbol: str
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    total_fees: float
    trade_count: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_pnl": round(self.total_pnl, 4),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "largest_win": round(self.largest_win, 4),
            "largest_loss": round(self.largest_loss, 4),
            "total_fees": round(self.total_fees, 4),
            "trade_count": self.trade_count,
        }


# ─────────────────────────────────────────────────────────────
# CLOCK SYNC
# ─────────────────────────────────────────────────────────────
class ClockSync:
    """
    Tracks the offset between local clock and Bybit server time.
    Prevents error 10002 (request timestamp expired) caused by clock skew.
    """

    _SYNC_TTL_S: float = 300.0  # Re-sync every 5 minutes

    def __init__(self, threshold_ms: int = 500) -> None:
        self._offset_ms: int = 0
        self._synced_at: float = 0.0
        self._threshold_ms: int = threshold_ms
        self._lock = threading.Lock()

    def sync(self, server_time_ms: int) -> None:
        """Update the local→server offset from a known server timestamp (ms)."""
        with self._lock:
            local_ms = int(time.time() * 1000)
            self._offset_ms = server_time_ms - local_ms
            self._synced_at = time.monotonic()
            logger.debug("Clock sync: offset=%dms", self._offset_ms)

    def now_ms(self) -> str:
        """Return the corrected current timestamp as a millisecond string."""
        with self._lock:
            return str(int(time.time() * 1000) + self._offset_ms)

    @property
    def offset_ms(self) -> int:
        """Current clock offset in milliseconds (for diagnostics)."""
        with self._lock:
            return self._offset_ms

    @property
    def needs_sync(self) -> bool:
        """True if the last sync was more than _SYNC_TTL_S seconds ago."""
        return time.monotonic() - self._synced_at > self._SYNC_TTL_S


# ─────────────────────────────────────────────────────────────
# INPUT VALIDATION HELPERS
# ─────────────────────────────────────────────────────────────
def _require(*pairs: Tuple[str, Any]) -> Optional[dict]:
    """
    Validate that required parameters are not None.

    Usage:
        err = _require(("symbol", symbol), ("side", side))
        if err: return err

    Returns None if all required params are present, else an error dict.
    """
    missing = [name for name, val in pairs if val is None]
    if missing:
        return {
            "status": "error",
            "msg": f"Required parameter(s) missing: {', '.join(missing)}",
        }
    return None


# ─────────────────────────────────────────────────────────────
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────
class BybitToolDispatcher:
    """Central dispatcher for all Bybit API interactions."""

    _RECV_WINDOW = "5000"

    def __init__(self, config: TradingConfig) -> None:
        config.validate()
        self.config = config
        self.tor = TorManager(
            enabled=config.use_tor,
            socks_port=config.tor_socks_port,
            timeout=config.request_timeout,
            max_retries=config.max_retries,
            control_port=config.tor_control_port,
        )
        self.circuit = CircuitBreaker(
            failure_threshold=config.cb_failure_threshold,
            recovery_timeout=config.cb_recovery_timeout,
            cooldown=config.cb_cooldown,
        )
        self.limiter = RateLimiter(
            max_calls=config.rate_limit_calls,
            window=config.rate_limit_window,
        )
        self.clock = ClockSync(config.clock_sync_threshold_ms)
        self._instr_cache: Dict[str, InstrumentInfo] = {}
        self._cache_lock = threading.Lock()

    # ══════════════════════════════════════════════════════════
    # AUTH & REQUEST
    # ══════════════════════════════════════════════════════════

    def _sign(self, payload: str, timestamp: str) -> str:
        """Generate HMAC-SHA256 signature for Bybit v5 authentication."""
        msg = f"{timestamp}{self.config.api_key}{self._RECV_WINDOW}{payload}"
        return hmac.new(
            key=self.config.api_secret.encode(),
            msg=msg.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

    def _ensure_clock_sync(self) -> None:
        """Lazily synchronise clock before authenticated requests."""
        if self.clock.needs_sync:
            try:
                resp = self._raw_public_get("/v5/market/time")
                ts_nano = resp.get("result", {}).get("timeNano")
                if ts_nano:
                    self.clock.sync(int(ts_nano) // 1_000_000)
            except Exception as exc:
                logger.warning("Clock sync failed (using local time): %s", exc)

    def _raw_public_get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """
        Direct unauthenticated GET – used internally for clock sync only.
        Bypasses rate limiter and circuit breaker.
        """
        url = f"{self.config.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        return self.tor.request("GET", url, headers, params, None)

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        """
        Core request method.
        Applies: rate limiting → clock sync → signing → circuit breaker → network.
        """
        self.limiter.acquire()

        if signed:
            self._ensure_clock_sync()

        url = f"{self.config.base_url}{endpoint}"
        ts = self.clock.now_ms() if signed else str(int(time.time() * 1000))

        # Build the string that gets signed
        if method == "POST":
            payload_str = json.dumps(json_data or {}, separators=(",", ":"))
        else:
            payload_str = "&".join(
                f"{k}={v}" for k, v in sorted((params or {}).items())
            )

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self._RECV_WINDOW,
        }
        if signed:
            headers["X-BAPI-SIGN"] = self._sign(payload_str, ts)

        return self.circuit.call(
            self.tor.request,
            method,
            url,
            headers,
            params if method == "GET" else None,
            json_data if method == "POST" else None,
        )

    def api_request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,  # FIX: was missing signed param
        max_retries: int = 3,
    ) -> dict:
        """
        Wrapper around api_request with exponential-backoff retry.
        Handles 403 (IP ban / Tor circuit) and 404 specially.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                return self.api_request(method, endpoint, params, json_data, signed)
            except RuntimeError as exc:
                last_exc = exc
                error_msg = str(exc)

                # 403 → attempt Tor identity renewal then retry
                if "403" in error_msg or "Forbidden" in error_msg:
                    logger.warning(
                        "403 Forbidden on attempt %d/%d; renewing Tor identity",
                        attempt,
                        max_retries,
                    )
                    self.tor._safe_renew_identity()
                    if attempt >= max_retries:
                        raise
                    time.sleep(2**attempt)
                    continue

                # 404 → endpoint does not exist, no point retrying
                if "404" in error_msg or "Not Found" in error_msg:
                    logger.warning("404 Not Found: %s", endpoint)
                    raise

                # Other API errors → retry with backoff
                if attempt >= max_retries:
                    raise
                wait = 2**attempt
                logger.warning(
                    "API error attempt %d/%d: %s – retrying in %ds",
                    attempt,
                    max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)

            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise
                wait = 2**attempt
                logger.warning(
                    "Network error attempt %d/%d: %s – retrying in %ds",
                    attempt,
                    max_retries,
                    exc,
                    wait,
                )
                time.sleep(wait)

        # Should be unreachable, but satisfies type checker
        raise last_exc or RuntimeError("Max retries exceeded")

    # ══════════════════════════════════════════════════════════
    # INSTRUMENT / LOT-SIZE + PRICE FILTER CACHE
    # ══════════════════════════════════════════════════════════

    def _fetch_instrument(self, symbol: str, category: str) -> InstrumentInfo:
        """Fetch and cache instrument info (lot size + price filter), thread-safe."""
        with self._cache_lock:
            info = self._instr_cache.get(symbol)
            if info and not info.is_stale:
                return info

        logger.info("Fetching instrument info for %s (category=%s)", symbol, category)
        try:
            resp = self.api_request(
                "GET",
                "/v5/market/instruments-info",
                params={"category": category, "symbol": symbol},
                signed=False,
            )
        except Exception as exc:
            raise ValueError(
                f"API request failed for instrument info ({symbol}): {exc}"
            ) from exc

        try:
            item = resp["result"]["list"][0]
            lot = item["lotSizeFilter"]
            pft = item.get("priceFilter", {})

            lsf = LotSizeFilter(
                qty_step=float(lot["qtyStep"]),
                min_order_qty=float(lot["minOrderQty"]),
                max_order_qty=float(lot.get("maxOrderQty", 1e9)),
                min_notional=float(lot.get("minNotionalValue", 0)),
            )
            pf = PriceFilter(
                tick_size=float(pft.get("tickSize", 0.01)),
                min_price=float(pft.get("minPrice", 0)),
                max_price=float(pft.get("maxPrice", 1e12)),
            )
            info = InstrumentInfo(
                lot_size=lsf,
                price_flt=pf,
                symbol=symbol,
                status=item.get("status", "Trading"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Could not parse instrument info for {symbol}: {exc}"
            ) from exc

        with self._cache_lock:
            self._instr_cache[symbol] = info

        return info

    def adjust_quantity(
        self,
        symbol: str,
        qty: float,
        category: str = Category.LINEAR,
    ) -> float:
        """Return a quantity rounded to the symbol's lotSize rules."""
        info = self._fetch_instrument(symbol, category)
        adjusted = info.lot_size.adjust(qty)
        logger.debug("%s qty %.8f → %.8f", symbol, qty, adjusted)
        return adjusted

    def adjust_price(
        self,
        symbol: str,
        price: float,
        category: str = Category.LINEAR,
    ) -> float:
        """Return a price rounded to the symbol's tickSize rules."""
        info = self._fetch_instrument(symbol, category)
        adjusted = info.price_flt.adjust(price)
        logger.debug("%s price %.8f → %.8f", symbol, price, adjusted)
        return adjusted

    def invalidate_instrument_cache(self, symbol: Optional[str] = None) -> None:
        """
        Invalidate cached instrument data.
        Pass symbol to invalidate a single entry, or None to clear all.
        """
        with self._cache_lock:
            if symbol:
                self._instr_cache.pop(symbol, None)
                logger.info("Instrument cache invalidated for %s", symbol)
            else:
                self._instr_cache.clear()
                logger.info("Instrument cache fully cleared")

    # ══════════════════════════════════════════════════════════
    # SERVER TIME (PUBLIC)
    # ══════════════════════════════════════════════════════════

    def get_server_time(self) -> dict:
        """Fetch Bybit server time (public, no auth required)."""
        resp = self.api_request("GET", "/v5/market/time", signed=False)
        result = resp.get("result", {})
        time_nano = result.get("timeNano", "0")
        time_ms = int(time_nano) // 1_000_000 if time_nano else 0
        if time_ms:
            self.clock.sync(time_ms)
        return {
            "time_nano": time_nano,
            "time_ms": time_ms,
            "time_second": time_ms // 1000 if time_ms else 0,
        }

    # ══════════════════════════════════════════════════════════
    # SINGLE ORDER
    # ══════════════════════════════════════════════════════════

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.LIMIT,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: TimeInForce = TimeInForce.GTC,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
        trailing_stop: Optional[float] = None,
    ) -> dict:
        """Place a single order with optional SL/TP and trailing stop."""
        adj_qty = self.adjust_quantity(symbol, qty, category)

        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(adj_qty),
            "timeInForce": time_in_force,
            "positionIdx": position_idx,
        }
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        if reduce_only:
            payload["reduceOnly"] = True
        if client_oid:
            payload["orderLinkId"] = client_oid

        logger.info(
            "Placing %s %s %s @ %s qty=%s",
            category,
            side,
            symbol,
            price or "MARKET",
            adj_qty,
        )
        return self.api_request_with_retry(
            "POST", "/v5/order/create", json_data=payload
        )

    # ══════════════════════════════════════════════════════════
    # CONVENIENCE: PLACE ORDER WITH AUTO SIZING
    # ══════════════════════════════════════════════════════════

    def place_order_with_sizing(
        self,
        symbol: str,
        side: OrderSide,
        risk_usdt: float,
        sl_pct: float,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.LIMIT,
        category: Category = Category.LINEAR,
        tp_pct: Optional[float] = None,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
    ) -> dict:
        """
        Place an order with automatic position sizing based on a fixed
        USDT risk amount and stop-loss percentage.

        Calculates qty so that hitting the stop-loss costs exactly risk_usdt.
        Also attaches SL and optional TP prices.
        """
        if price is None:
            # Fetch current mark price for sizing calculation
            ticker = self.get_ticker(symbol=symbol, category=category)
            price = float(ticker.get("markPrice") or ticker.get("lastPrice", 0))
            if price == 0:
                raise ValueError(f"Cannot determine price for {symbol}")

        sl_price, tp_price = self.calculate_sl_tp(
            entry_price=price,
            side=side,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
        )
        qty = self.calculate_position_size(
            symbol=symbol,
            entry_price=price,
            sl_price=sl_price,
            risk_usdt=risk_usdt,
            category=category,
        )

        return self.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            category=category,
            stop_loss=sl_price,
            take_profit=tp_price if tp_pct else None,
            position_idx=position_idx,
            client_oid=client_oid,
        )

    # ══════════════════════════════════════════════════════════
    # CONDITIONAL / TRIGGER ORDERS
    # ══════════════════════════════════════════════════════════

    def place_conditional_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        trigger_price: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trigger_by: TriggerBy = TriggerBy.LAST_PRICE,
        time_in_force: TimeInForce = TimeInForce.GTC,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
        reduce_only: bool = False,
    ) -> dict:
        """
        Place a conditional (stop) order that fires when trigger_price is hit.
        Useful for breakout entries or stop-entry strategies.
        """
        adj_qty = self.adjust_quantity(symbol, qty, category)
        adj_trig = self.adjust_price(symbol, trigger_price, category)

        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(adj_qty),
            "triggerPrice": str(adj_trig),
            "triggerBy": trigger_by,
            "timeInForce": time_in_force,
            "positionIdx": position_idx,
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

        logger.info(
            "Conditional %s %s %s trigger=%.4f qty=%s",
            category,
            side,
            symbol,
            adj_trig,
            adj_qty,
        )
        return self.api_request_with_retry(
            "POST", "/v5/order/create", json_data=payload
        )

    # ══════════════════════════════════════════════════════════
    # BATCH ORDERS
    # ══════════════════════════════════════════════════════════

    def execute_scalp_batch(self, order_list: List[dict]) -> dict:
        """Place up to max_orders_per_batch orders in a single API call."""
        if not order_list:
            raise ValueError("order_list must not be empty")
        if len(order_list) > self.config.max_orders_per_batch:
            raise ValueError(
                f"Batch limit is {self.config.max_orders_per_batch} orders "
                f"(got {len(order_list)})"
            )

        batch: List[Dict[str, Any]] = []
        for o in order_list:
            cat = o.get("category", Category.LINEAR)
            adj_qty = self.adjust_quantity(o["symbol"], float(o["qty"]), cat)

            entry: Dict[str, Any] = {
                "category": cat,
                "symbol": o["symbol"],
                "side": o["side"],
                "orderType": o.get("orderType", OrderType.LIMIT),
                "qty": str(adj_qty),
                "timeInForce": o.get("timeInForce", TimeInForce.GTC),
            }
            if "price" in o:
                entry["price"] = str(
                    self.adjust_price(o["symbol"], float(o["price"]), cat)
                )
            if "stopLoss" in o:
                entry["stopLoss"] = str(
                    self.adjust_price(o["symbol"], float(o["stopLoss"]), cat)
                )
            if "takeProfit" in o:
                entry["takeProfit"] = str(
                    self.adjust_price(o["symbol"], float(o["takeProfit"]), cat)
                )
            if "orderLinkId" in o:
                entry["orderLinkId"] = o["orderLinkId"]
            batch.append(entry)

        logger.info("Submitting batch of %d orders", len(batch))
        return self.api_request_with_retry(
            "POST",
            "/v5/order/create-batch",
            json_data={"category": Category.LINEAR, "request": batch},
        )

    # ══════════════════════════════════════════════════════════
    # ICEBERG ORDERS
    # ══════════════════════════════════════════════════════════

    def place_iceberg_order(
        self,
        symbol: str,
        side: OrderSide,
        total_qty: float,
        price: float,
        slices: int = 5,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        delay: Optional[float] = None,
    ) -> List[dict]:
        """
        Split a large order into multiple smaller slices to reduce market impact.
        SL/TP are attached to the first slice only.
        Returns a list of individual slice results.
        """
        slices = max(
            self.config.iceberg_min_slices,
            min(self.config.iceberg_max_slices, slices),
        )
        effective_delay = delay if delay is not None else self.config.iceberg_delay
        slice_qty = total_qty / slices
        results: List[dict] = []

        logger.info(
            "Iceberg: %s %s %s total=%.4f slices=%d @ %.4f",
            category,
            side,
            symbol,
            total_qty,
            slices,
            price,
        )
        for i in range(slices):
            result = self.safe_execute(
                self.place_order,
                symbol=symbol,
                side=side,
                qty=slice_qty,
                price=price,
                category=category,
                stop_loss=stop_loss if i == 0 else None,
                take_profit=take_profit if i == 0 else None,
                client_oid=f"iceberg_{symbol}_{int(time.time())}_{i}",
            )
            results.append({"slice": i + 1, "result": result})
            logger.info("Iceberg slice %d/%d placed", i + 1, slices)
            if i < slices - 1:
                time.sleep(effective_delay)

        return results

    # ══════════════════════════════════════════════════════════
    # CANCEL / AMEND ORDERS
    # ══════════════════════════════════════════════════════════

    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Cancel a single open order by orderId."""
        return self.api_request_with_retry(
            "POST",
            "/v5/order/cancel",
            json_data={
                "category": category,
                "symbol": symbol,
                "orderId": order_id,
            },
        )

    def cancel_all_orders(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Cancel all open orders for a symbol. Use with care."""
        logger.warning("Cancelling ALL open orders for %s", symbol)
        return self.api_request_with_retry(
            "POST",
            "/v5/order/cancel-all",
            json_data={"category": category, "symbol": symbol},
        )

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        """Return currently open orders."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/order/realtime", params=params)
        return resp.get("result", {}).get("list", [])

    def amend_order(
        self,
        symbol: str,
        order_id: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> dict:
        """Modify price, qty, SL, or TP on an existing open order."""
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
        }
        if qty is not None:
            payload["qty"] = str(self.adjust_quantity(symbol, qty, category))
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )

        return self.api_request_with_retry("POST", "/v5/order/amend", json_data=payload)

    # ══════════════════════════════════════════════════════════
    # POSITIONS & ACCOUNT
    # ══════════════════════════════════════════════════════════

    def get_positions(
        self,
        category: Category = Category.LINEAR,
        symbol: Optional[str] = None,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/position/list", params=params)
        return resp.get("result", {}).get("list", [])

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        resp = self.api_request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": account_type},
        )
        return resp.get("result", {})

    def get_account_info(self) -> dict:
        """
        Fetch account info: UTA upgrade status, margin mode, DCP status,
        time window, and account-level margin data.
        """
        resp = self.api_request("GET", "/v5/account/info")
        return resp.get("result", {})

    def get_fee_rates(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        """Fetch maker/taker fee rates for the account."""
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/account/fee-rate", params=params)
        return resp.get("result", {}).get("list", [])

    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        category: Category = Category.LINEAR,
        buy_leverage: Optional[int] = None,
        sell_leverage: Optional[int] = None,
    ) -> dict:
        payload = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(buy_leverage or leverage),
            "sellLeverage": str(sell_leverage or leverage),
        }
        return self.api_request_with_retry(
            "POST", "/v5/position/set-leverage", json_data=payload
        )

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        category: Category = Category.LINEAR,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
    ) -> dict:
        """Update SL / TP / trailing stop on an open position."""
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "positionIdx": position_idx,
        }
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)

        return self.api_request_with_retry(
            "POST", "/v5/position/trading-stop", json_data=payload
        )

    def get_pnl_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 100,
    ) -> List[dict]:
        """Fetch closed PnL records."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/position/closed-pnl", params=params)
        return resp.get("result", {}).get("list", [])

    def get_pnl_report(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        limit: int = 100,
    ) -> PnLReport:
        """Aggregate closed PnL records into a summary report."""
        records = self.get_pnl_history(symbol=symbol, category=category, limit=limit)
        if not records:
            return PnLReport(
                symbol=symbol,
                total_pnl=0,
                win_count=0,
                loss_count=0,
                win_rate=0,
                avg_win=0,
                avg_loss=0,
                largest_win=0,
                largest_loss=0,
                total_fees=0,
                trade_count=0,
            )

        pnls = [float(r.get("closedPnl", 0)) for r in records]
        fees = [float(r.get("cumExecFee", 0)) for r in records]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        return PnLReport(
            symbol=symbol,
            total_pnl=sum(pnls),
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=len(wins) / len(pnls) if pnls else 0.0,
            avg_win=statistics.mean(wins) if wins else 0.0,
            avg_loss=statistics.mean(losses) if losses else 0.0,
            largest_win=max(wins) if wins else 0.0,
            largest_loss=min(losses) if losses else 0.0,
            total_fees=sum(fees),
            trade_count=len(pnls),
        )

    def get_order_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        """Fetch historical (filled / cancelled) orders."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/order/history", params=params)
        return resp.get("result", {}).get("list", [])

    # ══════════════════════════════════════════════════════════
    # MARKET DATA
    # ══════════════════════════════════════════════════════════

    def get_ticker(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        return items[0] if items else {}

    def get_tickers_bulk(
        self,
        symbols: List[str],
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        """
        Fetch tickers for multiple symbols in a single API call.
        Bybit returns all tickers for the category; we filter client-side.
        """
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category},
            signed=False,
        )
        all_tickers = resp.get("result", {}).get("list", [])
        sym_set = {s.upper() for s in symbols}
        return [t for t in all_tickers if t.get("symbol", "") in sym_set]

    def get_orderbook(
        self,
        symbol: str,
        limit: int = 25,
        category: Category = Category.LINEAR,
    ) -> dict:
        return self.api_request_with_retry(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "1",
        limit: int = 200,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        resp = self.api_request_with_retry(
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
        return resp.get("result", {}).get("list", [])

    def get_recent_trades(
        self,
        symbol: str,
        limit: int = 500,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        resp = self.api_request(
            "GET",
            "/v5/market/recent-trade",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_open_interest(
        self,
        symbol: str,
        interval_time: str = "5min",
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        """Fetch open interest history."""
        resp = self.api_request(
            "GET",
            "/v5/market/open-interest",
            params={
                "category": category,
                "symbol": symbol,
                "intervalTime": interval_time,
                "limit": limit,
            },
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_liquidations(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        limit: int = 200,
    ) -> List[dict]:
        """Fetch recent liquidation orders."""
        resp = self.api_request(
            "GET",
            "/v5/market/liquidation",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_instruments_info(
        self,
        category: Category = Category.LINEAR,
        symbol: Optional[str] = None,
        base_coin: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        Search / list tradeable instruments with optional filters.
        Useful for discovering available symbols or verifying an instrument exists.
        """
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if base_coin:
            params["baseCoin"] = base_coin
        if status:
            params["status"] = status
        resp = self.api_request(
            "GET", "/v5/market/instruments-info", params=params, signed=False
        )
        return resp.get("result", {}).get("list", [])

    def get_mark_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> float:
        """Return the current mark price for a symbol (float, 0.0 on error)."""
        ticker = self.get_ticker(symbol=symbol, category=category)
        return float(ticker.get("markPrice", 0.0))

    def get_index_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> float:
        """Return the current index price for a symbol (float, 0.0 on error)."""
        ticker = self.get_ticker(symbol=symbol, category=category)
        return float(ticker.get("indexPrice", 0.0))

    # ══════════════════════════════════════════════════════════
    # SPREAD & DEPTH ANALYSIS
    # ══════════════════════════════════════════════════════════

    def get_spread_analysis(
        self,
        symbol: str,
        depth: int = 5,
        category: Category = Category.LINEAR,
    ) -> dict:
        """
        Analyse orderbook spread and bid/ask depth imbalance.

        Returns mid_price, spread_abs, spread_pct,
                bid_depth (USDT), ask_depth (USDT),
                depth_imbalance ([-1, +1], +1 = strong bid pressure).
        """
        raw = self.get_orderbook(symbol=symbol, limit=depth, category=category)
        result = raw.get("result", raw)
        bids = result.get("b", [])[:depth]
        asks = result.get("a", [])[:depth]

        if not bids or not asks:
            return {"error": "Empty orderbook"}

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid

        bid_depth = sum(float(p) * float(q) for p, q in bids)
        ask_depth = sum(float(p) * float(q) for p, q in asks)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth else 0.0

        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread_abs": round(spread, 8),
            "spread_pct": round(spread / mid_price * 100, 6) if mid_price else 0,
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
            "depth_imbalance": round(imbalance, 4),
        }

    # ══════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _compute_rsi(closes: List[float], period: int = 14) -> float:
        """Wilder's RSI from a chronological list of close prices."""
        if len(closes) < period + 1:
            return float("nan")
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)

    @staticmethod
    def _compute_bollinger(
        closes: List[float],
        period: int = 20,
        std_dev: float = 2.0,
    ) -> Dict[str, float]:
        """Bollinger Bands from a chronological list of close prices."""
        if len(closes) < period:
            return {
                "upper": float("nan"),
                "middle": float("nan"),
                "lower": float("nan"),
            }
        window = closes[-period:]
        middle = sum(window) / period
        variance = sum((x - middle) ** 2 for x in window) / period
        std = math.sqrt(variance)
        return {
            "upper": round(middle + std_dev * std, 6),
            "middle": round(middle, 6),
            "lower": round(middle - std_dev * std, 6),
        }

    @staticmethod
    def _compute_atr(
        closes: List[float],
        highs: List[float],
        lows: List[float],
        period: int = 14,
    ) -> float:
        """Average True Range from chronological OHLC lists."""
        if len(closes) < period + 1:
            return float("nan")
        trs: List[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def get_technical_analysis(
        self,
        symbol: str,
        interval: str = "15",
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        limit: int = 100,
        category: Category = Category.LINEAR,
    ) -> dict:
        """
        Fetch klines and compute RSI + Bollinger Bands + ATR.
        Kline format from Bybit (newest first):
            [startTime, open, high, low, close, vol, turnover]

        Returns: current_price, rsi, rsi_signal, bollinger, bb_signal, atr.
        """
        klines = self.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit + 1,
            category=category,
        )
        if not klines:
            return {"error": "No kline data returned"}

        # Bybit returns newest first – reverse for chronological order
        klines_asc = list(reversed(klines))
        closes = [float(k[4]) for k in klines_asc]
        highs = [float(k[2]) for k in klines_asc]
        lows = [float(k[3]) for k in klines_asc]

        rsi = self._compute_rsi(closes, period=rsi_period)
        bb = self._compute_bollinger(closes, period=bb_period, std_dev=bb_std)
        atr = self._compute_atr(closes, highs, lows, period=14)

        current_price = closes[-1]

        bb_signal = (
            "OVERSOLD"
            if current_price < bb["lower"]
            else "OVERBOUGHT"
            if current_price > bb["upper"]
            else "NEUTRAL"
        )
        rsi_signal = (
            "OVERSOLD"
            if not math.isnan(rsi) and rsi < 30
            else "OVERBOUGHT"
            if not math.isnan(rsi) and rsi > 70
            else "NEUTRAL"
        )

        return {
            "symbol": symbol,
            "interval": interval,
            "current_price": current_price,
            "rsi": rsi,
            "rsi_signal": rsi_signal,
            "bollinger": bb,
            "bb_signal": bb_signal,
            "atr": round(atr, 6),
        }

    # ══════════════════════════════════════════════════════════
    # MARKET INTELLIGENCE
    # ══════════════════════════════════════════════════════════

    def get_market_momentum(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        strong_threshold: float = 0.20,
        mild_threshold: float = 0.08,
    ) -> MomentumResult:
        """
        Calculate real-time order flow imbalance + VWAP.

        Imbalance = (buy_vol − sell_vol) / (buy_vol + sell_vol) ∈ [−1, +1]
        """
        trades = self.get_recent_trades(symbol, limit=500, category=category)

        buy_vol = sum(float(t["size"]) for t in trades if t["side"] == "Buy")
        sell_vol = sum(float(t["size"]) for t in trades if t["side"] == "Sell")
        total = buy_vol + sell_vol
        imbalance = (buy_vol - sell_vol) / total if total > 0 else 0.0

        vwap_num = sum(float(t["price"]) * float(t["size"]) for t in trades)
        vwap = vwap_num / total if total > 0 else 0.0
        avg_sz = total / len(trades) if trades else 0.0

        signal = (
            Signal.STRONG_BUY
            if imbalance > strong_threshold
            else Signal.BUY
            if imbalance > mild_threshold
            else Signal.STRONG_SELL
            if imbalance < -strong_threshold
            else Signal.SELL
            if imbalance < -mild_threshold
            else Signal.NEUTRAL
        )

        logger.info(
            "Momentum %s → %s (imbalance=%.4f vwap=%.4f)",
            symbol,
            signal.value,
            imbalance,
            vwap,
        )
        return MomentumResult(
            symbol=symbol,
            imbalance=imbalance,
            signal=signal,
            buy_vol=buy_vol,
            sell_vol=sell_vol,
            vwap=vwap,
            avg_trade_sz=avg_sz,
        )

    def get_funding_rate(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Fetch the most recent funding rate for a perpetual contract."""
        resp = self.api_request(
            "GET",
            "/v5/market/funding/history",
            params={"category": category, "symbol": symbol, "limit": 1},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        return items[0] if items else {}

    # ══════════════════════════════════════════════════════════
    # RISK HELPERS
    # ══════════════════════════════════════════════════════════

    def calculate_sl_tp(
        self,
        entry_price: float,
        side: OrderSide,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        Return (stop_loss_price, take_profit_price) for a given entry.
        Defaults to config.default_stop_loss / default_take_profit.
        """
        sl_pct = sl_pct if sl_pct is not None else self.config.default_stop_loss
        tp_pct = tp_pct if tp_pct is not None else self.config.default_take_profit

        if side == OrderSide.BUY:
            sl = round(entry_price * (1 - sl_pct), 8)
            tp = round(entry_price * (1 + tp_pct), 8)
        else:
            sl = round(entry_price * (1 + sl_pct), 8)
            tp = round(entry_price * (1 - tp_pct), 8)

        logger.debug("SL/TP %s entry=%.4f → sl=%.4f tp=%.4f", side, entry_price, sl, tp)
        return sl, tp

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        risk_usdt: float,
        category: Category = Category.LINEAR,
    ) -> float:
        """
        Fixed-risk position sizing.
        Returns quantity sized so that max loss = risk_usdt when SL is hit.
        """
        price_diff = abs(entry_price - sl_price)
        if price_diff == 0:
            logger.warning(
                "calculate_position_size: entry_price == sl_price, returning 0"
            )
            return 0.0
        raw_qty = risk_usdt / price_diff
        return self.adjust_quantity(symbol, raw_qty, category)

    def calculate_atr_position_size(
        self,
        symbol: str,
        risk_usdt: float,
        atr_mult: float = 1.5,
        interval: str = "15",
        category: Category = Category.LINEAR,
    ) -> dict:
        """
        ATR-based position sizing: SL distance = atr_mult × ATR.
        Returns suggested qty, ATR, and implied SL distance in price units.
        """
        ta = self.get_technical_analysis(
            symbol=symbol, interval=interval, category=category
        )
        atr = ta.get("atr", float("nan"))
        if math.isnan(atr) or atr == 0:
            raise RuntimeError("Could not compute ATR")

        current_price = ta.get("current_price", 0.0)
        if current_price == 0:
            raise RuntimeError("No current price available")

        sl_distance = atr * atr_mult
        raw_qty = risk_usdt / sl_distance
        adj_qty = self.adjust_quantity(symbol, raw_qty, category)

        return {
            "symbol": symbol,
            "current_price": current_price,
            "atr": round(atr, 6),
            "atr_mult": atr_mult,
            "sl_distance": round(sl_distance, 6),
            "risk_usdt": risk_usdt,
            "quantity": adj_qty,
        }

    def check_max_position(self, symbol: str, usdt_value: float) -> bool:
        """Return True if adding usdt_value would not breach the position cap."""
        positions = self.get_positions(symbol=symbol)
        current = sum(float(p.get("positionValue", 0)) for p in positions)
        allowed = self.config.max_position_usdt - current
        if usdt_value > allowed:
            logger.warning(
                "Position cap reached for %s: current=%.2f cap=%.2f requested=%.2f",
                symbol,
                current,
                self.config.max_position_usdt,
                usdt_value,
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════
    # SAFE EXECUTE (exponential backoff + circuit-aware)
    # ══════════════════════════════════════════════════════════

    def safe_execute(
        self,
        fn: Callable,
        *args: Any,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        """Execute fn with exponential backoff; aborts immediately if circuit is OPEN."""
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except RuntimeError as exc:
                if "Circuit OPEN" in str(exc):
                    logger.error("Circuit OPEN – aborting safe_execute: %s", exc)
                    return {"status": "circuit_open", "msg": str(exc)}
                last_exc = exc
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Attempt %d/%d failed: %s – retrying in %.1fs",
                    attempt,
                    max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
            except Exception as exc:
                last_exc = exc
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Attempt %d/%d failed: %s – retrying in %.1fs",
                    attempt,
                    max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)

        logger.error("All %d attempts exhausted. Last error: %s", max_retries, last_exc)
        return {"status": "error", "msg": str(last_exc)}

    # ══════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ══════════════════════════════════════════════════════════

    def health_check(self) -> dict:
        """Quick sanity check: server connectivity + component states."""
        try:
            resp = self.api_request("GET", "/v5/market/time", signed=False)
            time_nano = resp.get("result", {}).get("timeNano")
            if time_nano:
                self.clock.sync(int(time_nano) // 1_000_000)
            return {
                "status": "ok",
                "circuit": self.circuit.state.value,
                "circuit_fails": self.circuit.failure_count,
                "rate_usage": self.limiter.current_usage,
                "server_time_nano": time_nano,
                "base_url": self.config.base_url,
                "tor_enabled": self.config.use_tor,
                "tor_reachable": self.tor._is_tor_reachable(),
                "testnet": self.config.testnet,
                "clock_offset_ms": self.clock.offset_ms,
                "cache_symbols": list(self._instr_cache.keys()),
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}


# ─────────────────────────────────────────────────────────────
# GLOBAL DISPATCHER INSTANCE (singleton, lazy, thread-safe)
# ─────────────────────────────────────────────────────────────
_dispatcher: Optional[BybitToolDispatcher] = None
_disp_lock = threading.Lock()


def _get_dispatcher() -> BybitToolDispatcher:
    """Return (or create) the singleton BybitToolDispatcher."""
    global _dispatcher
    if _dispatcher is None:
        with _disp_lock:
            if _dispatcher is None:
                _dispatcher = BybitToolDispatcher(TradingConfig())
    return _dispatcher


# ─────────────────────────────────────────────────────────────
# TOOL ENTRY POINT
# ─────────────────────────────────────────────────────────────
def run(
    action: Literal[
        "health_check",
        "get_server_time",
        "place_order",
        "place_order_with_sizing",
        "place_conditional_order",
        "amend_order",
        "cancel_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_order_history",
        "get_positions",
        "get_wallet_balance",
        "get_account_info",
        "get_fee_rates",
        "set_leverage",
        "set_trading_stop",
        "get_ticker",
        "get_tickers_bulk",
        "get_orderbook",
        "get_klines",
        "get_recent_trades",
        "get_open_interest",
        "get_liquidations",
        "get_instruments_info",
        "get_spread_analysis",
        "get_technical_analysis",
        "get_market_momentum",
        "get_funding_rate",
        "get_mark_price",
        "get_index_price",
        "calculate_sl_tp",
        "calculate_position_size",
        "calculate_atr_position_size",
        "get_pnl_history",
        "get_pnl_report",
        "batch_orders",
        "iceberg_order",
        "reset_circuit",
        "invalidate_cache",
    ],
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_type: Optional[
        Literal["Limit", "Market", "LimitMaker", "Stop", "StopLimit"]
    ] = None,
    category: Optional[Literal["linear", "inverse", "spot", "option"]] = None,
    order_id: Optional[str] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    trailing_stop: Optional[float] = None,
    reduce_only: Optional[bool] = False,
    time_in_force: Optional[Literal["GTC", "IOC", "FOK", "PostOnly"]] = None,
    position_idx: Optional[int] = None,
    client_oid: Optional[str] = None,
    trigger_price: Optional[float] = None,
    trigger_by: Optional[Literal["LastPrice", "IndexPrice", "MarkPrice"]] = None,
    leverage: Optional[int] = None,
    buy_leverage: Optional[int] = None,
    sell_leverage: Optional[int] = None,
    account_type: Optional[str] = "UNIFIED",
    limit: Optional[int] = 25,
    interval: Optional[str] = "1",
    interval_time: Optional[str] = "5min",
    depth: Optional[int] = 5,
    symbols: Optional[List[str]] = None,
    base_coin: Optional[str] = None,
    status: Optional[str] = None,
    strong_threshold: Optional[float] = 0.20,
    mild_threshold: Optional[float] = 0.08,
    rsi_period: Optional[int] = 14,
    bb_period: Optional[int] = 20,
    bb_std: Optional[float] = 2.0,
    atr_mult: Optional[float] = 1.5,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    risk_usdt: Optional[float] = None,
    sl_price: Optional[float] = None,
    orders: Optional[List[Dict[str, Any]]] = None,
    slices: Optional[int] = 5,
    delay: Optional[float] = None,
) -> dict:
    """Bybit Trading Tool - Execute any supported trading operation.
    Args:
        action: The action to perform (e.g., "place_order", "get_ticker")
        symbol: Trading symbol (e.g., "BTCUSDT")
        side: Order side - "Buy" or "Sell"
        qty: Order quantity
        price: Order price (for limit orders)
        order_type: Order type - "Limit", "Market", "LimitMaker", "Stop", "StopLimit"
        category: Category - "linear", "inverse", "spot", or "option"
        order_id: Order ID for cancel/amend operations
        stop_loss: Stop loss price
        take_profit: Take profit price
        trailing_stop: Trailing stop distance
        reduce_only: Reduce only flag (True/False)
        time_in_force: Time in force - "GTC", "IOC", "FOK", "PostOnly"
        position_idx: Position index (0=one-way, 1=hedge-buy, 2=hedge-sell)
        client_oid: Client order link ID
        trigger_price: Trigger price for conditional orders
        trigger_by: Trigger by - "LastPrice", "IndexPrice", "MarkPrice"
        leverage: Leverage value
        buy_leverage: Buy-side leverage
        sell_leverage: Sell-side leverage
        account_type: Account type (default: "UNIFIED")
        limit: Result limit
        interval: Kline interval (e.g., "1", "15", "1h")
        interval_time: Open interest interval (e.g., "5min")
        depth: Orderbook depth
        symbols: List of symbols for bulk queries
        base_coin: Base coin filter
        status: Status filter
        strong_threshold: Strong threshold for momentum (default: 0.20)
        mild_threshold: Mild threshold for momentum (default: 0.08)
        rsi_period: RSI period (default: 14)
        bb_period: Bollinger Bands period (default: 20)
        bb_std: Bollinger Bands standard deviation (default: 2.0)
        atr_mult: ATR multiplier for position sizing (default: 1.5)
        sl_pct: Stop loss percentage (decimal, e.g., 0.02)
        tp_pct: Take profit percentage (decimal, e.g., 0.04)
        risk_usdt: Max USDT risk per trade
        sl_price: Stop loss price (for position sizing)
        orders: List of orders for batch operations
        slices: Number of slices for iceberg orders (default: 5)
        delay: Delay between iceberg slices (seconds)
    Returns:
        dict: Structured result of the operation. On error always contains
              {"status": "error", "msg": "<reason>"}.
    """
    bot = _get_dispatcher()

    try:
        cat = Category(category or "linear")
        tif = TimeInForce(time_in_force or "GTC")
        pidx = PositionIdx(position_idx if position_idx is not None else 0)
        trig = TriggerBy(trigger_by or "LastPrice")

        # ── Health / Diagnostics ──────────────────────────────
        if action == "health_check":
            return bot.health_check()

        elif action == "reset_circuit":
            bot.circuit.reset()
            return {"status": "ok", "msg": "Circuit breaker reset to CLOSED"}

        elif action == "invalidate_cache":
            bot.invalidate_instrument_cache(symbol=symbol)
            return {
                "status": "ok",
                "msg": f"Cache cleared for {symbol or 'all symbols'}",
            }

        # ── Server Time ───────────────────────────────────────
        elif action == "get_server_time":
            return bot.get_server_time()

        # ── Place Order ───────────────────────────────────────
        elif action == "place_order":
            err = _require(("symbol", symbol), ("side", side), ("qty", qty))
            if err:
                return err
            return bot.place_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                price=price,
                order_type=OrderType(order_type or "Limit"),
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only or False,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
                trailing_stop=trailing_stop,
            )

        # ── Place Order with Auto Sizing ──────────────────────
        elif action == "place_order_with_sizing":
            err = _require(
                ("symbol", symbol),
                ("side", side),
                ("risk_usdt", risk_usdt),
                ("sl_pct", sl_pct),
            )
            if err:
                return err
            return bot.place_order_with_sizing(
                symbol=symbol,
                side=OrderSide(side),
                risk_usdt=risk_usdt,
                sl_pct=sl_pct,
                price=price,
                order_type=OrderType(order_type or "Limit"),
                category=cat,
                tp_pct=tp_pct,
                position_idx=pidx,
                client_oid=client_oid,
            )

        # ── Conditional Order ─────────────────────────────────
        elif action == "place_conditional_order":
            err = _require(
                ("symbol", symbol),
                ("side", side),
                ("qty", qty),
                ("trigger_price", trigger_price),
            )
            if err:
                return err
            return bot.place_conditional_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                trigger_price=trigger_price,
                order_type=OrderType(order_type or "Market"),
                price=price,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trigger_by=trig,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
                reduce_only=reduce_only or False,
            )

        # ── Amend Order ───────────────────────────────────────
        elif action == "amend_order":
            err = _require(("symbol", symbol), ("order_id", order_id))
            if err:
                return err
            return bot.amend_order(
                symbol=symbol,
                order_id=order_id,
                qty=qty,
                price=price,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        # ── Cancel Order ──────────────────────────────────────
        elif action == "cancel_order":
            err = _require(("symbol", symbol), ("order_id", order_id))
            if err:
                return err
            return bot.cancel_order(symbol=symbol, order_id=order_id, category=cat)

        # ── Cancel All Orders ─────────────────────────────────
        elif action == "cancel_all_orders":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.cancel_all_orders(symbol=symbol, category=cat)

        # ── Open Orders ───────────────────────────────────────
        elif action == "get_open_orders":
            return {
                "orders": bot.get_open_orders(
                    symbol=symbol, category=cat, limit=limit or 50
                )
            }

        # ── Order History ─────────────────────────────────────
        elif action == "get_order_history":
            return {
                "orders": bot.get_order_history(
                    symbol=symbol, category=cat, limit=limit or 50
                )
            }

        # ── Positions ─────────────────────────────────────────
        elif action == "get_positions":
            return {"positions": bot.get_positions(category=cat, symbol=symbol)}

        # ── Wallet Balance ────────────────────────────────────
        elif action == "get_wallet_balance":
            return bot.get_wallet_balance(account_type=account_type or "UNIFIED")

        # ── Account Info ──────────────────────────────────────
        elif action == "get_account_info":
            return bot.get_account_info()

        # ── Fee Rates ─────────────────────────────────────────
        elif action == "get_fee_rates":
            return {"fee_rates": bot.get_fee_rates(symbol=symbol, category=cat)}

        # ── Set Leverage ──────────────────────────────────────
        elif action == "set_leverage":
            err = _require(("symbol", symbol), ("leverage", leverage))
            if err:
                return err
            return bot.set_leverage(
                symbol=symbol,
                leverage=leverage,
                category=cat,
                buy_leverage=buy_leverage,
                sell_leverage=sell_leverage,
            )

        # ── Set Trading Stop ──────────────────────────────────
        elif action == "set_trading_stop":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.set_trading_stop(
                symbol=symbol,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop=trailing_stop,
                category=cat,
                position_idx=pidx,
            )

        # ── Ticker ────────────────────────────────────────────
        elif action == "get_ticker":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_ticker(symbol=symbol, category=cat)

        # ── Bulk Tickers ──────────────────────────────────────
        elif action == "get_tickers_bulk":
            err = _require(("symbols", symbols))
            if err:
                return err
            return {"tickers": bot.get_tickers_bulk(symbols=symbols, category=cat)}

        # ── Orderbook ─────────────────────────────────────────
        elif action == "get_orderbook":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_orderbook(symbol=symbol, limit=limit or 25, category=cat)

        # ── Klines ────────────────────────────────────────────
        elif action == "get_klines":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "klines": bot.get_klines(
                    symbol=symbol,
                    interval=interval or "1",
                    limit=limit or 200,
                    category=cat,
                )
            }

        # ── Recent Trades ─────────────────────────────────────
        elif action == "get_recent_trades":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "trades": bot.get_recent_trades(
                    symbol=symbol, limit=limit or 500, category=cat
                )
            }

        # ── Open Interest ─────────────────────────────────────
        elif action == "get_open_interest":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "open_interest": bot.get_open_interest(
                    symbol=symbol,
                    interval_time=interval_time or "5min",
                    category=cat,
                    limit=limit or 50,
                )
            }

        # ── Liquidations ──────────────────────────────────────
        elif action == "get_liquidations":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "liquidations": bot.get_liquidations(
                    symbol=symbol, category=cat, limit=limit or 200
                )
            }

        # ── Instruments Info ──────────────────────────────────
        elif action == "get_instruments_info":
            return {
                "instruments": bot.get_instruments_info(
                    category=cat,
                    symbol=symbol,
                    base_coin=base_coin,
                    status=status,
                    limit=limit or 100,
                )
            }

        # ── Spread Analysis ───────────────────────────────────
        elif action == "get_spread_analysis":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_spread_analysis(
                symbol=symbol, depth=depth or 5, category=cat
            )

        # ── Technical Analysis ────────────────────────────────
        elif action == "get_technical_analysis":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_technical_analysis(
                symbol=symbol,
                interval=interval or "15",
                rsi_period=rsi_period or 14,
                bb_period=bb_period or 20,
                bb_std=bb_std or 2.0,
                limit=limit or 100,
                category=cat,
            )

        # ── Market Momentum ───────────────────────────────────
        elif action == "get_market_momentum":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_market_momentum(
                symbol=symbol,
                category=cat,
                strong_threshold=strong_threshold or 0.20,
                mild_threshold=mild_threshold or 0.08,
            ).to_dict()  # FIX: always call .to_dict() for JSON consistency

        # ── Funding Rate ──────────────────────────────────────
        elif action == "get_funding_rate":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_funding_rate(symbol=symbol, category=cat)

        # ── Mark Price ────────────────────────────────────────
        elif action == "get_mark_price":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "symbol": symbol,
                "mark_price": bot.get_mark_price(symbol=symbol, category=cat),
            }

        # ── Index Price ───────────────────────────────────────
        elif action == "get_index_price":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "symbol": symbol,
                "index_price": bot.get_index_price(symbol=symbol, category=cat),
            }

        # ── Calculate SL / TP ─────────────────────────────────
        elif action == "calculate_sl_tp":
            err = _require(("side", side), ("price", price))
            if err:
                return err
            sl, tp = bot.calculate_sl_tp(
                entry_price=price,
                side=OrderSide(side),
                sl_pct=sl_pct,
                tp_pct=tp_pct,
            )
            return {
                "symbol": symbol,
                "entry_price": price,
                "side": side,
                "stop_loss": sl,
                "take_profit": tp,
                "sl_pct": sl_pct or bot.config.default_stop_loss,
                "tp_pct": tp_pct or bot.config.default_take_profit,
            }

        # ── Position Sizing ───────────────────────────────────
        elif action == "calculate_position_size":
            err = _require(
                ("symbol", symbol),
                ("price", price),
                ("sl_price", sl_price),
                ("risk_usdt", risk_usdt),
            )
            if err:
                return err
            qty_out = bot.calculate_position_size(
                symbol=symbol,
                entry_price=price,
                sl_price=sl_price,
                risk_usdt=risk_usdt,
                category=cat,
            )
            return {
                "symbol": symbol,
                "entry_price": price,
                "sl_price": sl_price,
                "risk_usdt": risk_usdt,
                "quantity": qty_out,
            }

        # ── ATR Position Sizing ───────────────────────────────
        elif action == "calculate_atr_position_size":
            err = _require(("symbol", symbol), ("risk_usdt", risk_usdt))
            if err:
                return err
            try:
                return bot.calculate_atr_position_size(
                    symbol=symbol,
                    risk_usdt=risk_usdt,
                    atr_mult=atr_mult or 1.5,
                    interval=interval or "15",
                    category=cat,
                )
            except Exception as exc:
                return {"status": "error", "msg": str(exc)}

        # ── PnL History ───────────────────────────────────────
        elif action == "get_pnl_history":
            return {
                "pnl_history": bot.get_pnl_history(
                    symbol=symbol, category=cat, limit=limit or 100
                )
            }

        # ── PnL Report ────────────────────────────────────────
        elif action == "get_pnl_report":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_pnl_report(
                symbol=symbol, category=cat, limit=limit or 100
            ).to_dict()

        # ── Batch Orders ──────────────────────────────────────
        elif action == "batch_orders":
            err = _require(("orders", orders))
            if err:
                return err
            return bot.safe_execute(bot.execute_scalp_batch, orders)

        # ── Iceberg Order ─────────────────────────────────────
        elif action == "iceberg_order":
            err = _require(
                ("symbol", symbol),
                ("side", side),
                ("qty", qty),
                ("price", price),
            )
            if err:
                return err
            results = bot.place_iceberg_order(
                symbol=symbol,
                side=OrderSide(side),
                total_qty=qty,
                price=price,
                slices=slices or 5,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                delay=delay,
            )
            return {"status": "ok", "iceberg_results": results}

        else:
            return {"status": "error", "msg": f"Unknown action: {action}"}

    except Exception as exc:
        logger.exception("run() unhandled exception for action=%s", action)
        return {"status": "error", "msg": str(exc)}


# ─────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Bybit Trading Tool – CLI Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bybit-realm.py --action health_check
  python bybit-realm.py --action get_server_time
  python bybit-realm.py --action get_ticker --symbol BTCUSDT
  python bybit-realm.py --action get_klines --symbol BTCUSDT --interval 15 --limit 200
  python bybit-realm.py --action get_technical_analysis --symbol BTCUSDT --interval 15
  python bybit-realm.py --action get_spread_analysis --symbol BTCUSDT --depth 10
  python bybit-realm.py --action get_market_momentum --symbol BTCUSDT
  python bybit-realm.py --action get_tickers_bulk --symbols BTCUSDT,ETHUSDT,SOLUSDT
  python bybit-realm.py --action get_instruments_info --base-coin BTC
  python bybit-realm.py --action calculate_sl_tp --side Buy --price 65000
  python bybit-realm.py --action calculate_atr_position_size --symbol BTCUSDT --risk-usdt 50
  python bybit-realm.py --action get_positions
  python bybit-realm.py --action get_wallet_balance
  python bybit-realm.py --action get_account_info
  python bybit-realm.py --action get_fee_rates --symbol BTCUSDT
  python bybit-realm.py --action get_pnl_report --symbol BTCUSDT
  python bybit-realm.py --action get_mark_price --symbol BTCUSDT
  python bybit-realm.py --action get_index_price --symbol BTCUSDT
  python bybit-realm.py --action place_order_with_sizing --symbol BTCUSDT --side Buy --risk-usdt 50 --sl-pct 0.02
  python bybit-realm.py --action place_conditional_order --symbol BTCUSDT --side Buy --qty 0.01 --trigger-price 70000
  python bybit-realm.py --action invalidate_cache --symbol BTCUSDT
        """,
    )

    # ── Core ──────────────────────────────────────────────────
    parser.add_argument("--action", required=True, help="Action to perform")
    parser.add_argument("--symbol", help="Trading symbol")
    parser.add_argument("--side", help="Buy or Sell")
    parser.add_argument("--qty", type=float, help="Order quantity")
    parser.add_argument("--price", type=float, help="Order price")
    parser.add_argument("--order-type", help="Limit|Market|LimitMaker|Stop|StopLimit")
    parser.add_argument(
        "--category", default="linear", help="linear|inverse|spot|option"
    )
    parser.add_argument("--order-id", help="Order ID")
    parser.add_argument("--stop-loss", type=float, help="Stop loss price")
    parser.add_argument("--take-profit", type=float, help="Take profit price")
    parser.add_argument("--trailing-stop", type=float, help="Trailing stop distance")
    parser.add_argument("--reduce-only", action="store_true", help="Reduce only flag")
    parser.add_argument("--time-in-force", default="GTC", help="GTC|IOC|FOK|PostOnly")
    parser.add_argument(
        "--position-idx", type=int, default=0, help="0=one-way 1=hedge-buy 2=hedge-sell"
    )
    parser.add_argument("--client-oid", help="Client order link ID")
    parser.add_argument(
        "--trigger-price", type=float, help="Trigger price for conditional orders"
    )
    parser.add_argument(
        "--trigger-by", default="LastPrice", help="LastPrice|IndexPrice|MarkPrice"
    )

    # ── Account ───────────────────────────────────────────────
    parser.add_argument("--leverage", type=int, help="Leverage")
    parser.add_argument("--buy-leverage", type=int, help="Buy-side leverage")
    parser.add_argument("--sell-leverage", type=int, help="Sell-side leverage")
    parser.add_argument("--account-type", default="UNIFIED", help="Account type")

    # ── Market data ───────────────────────────────────────────
    parser.add_argument("--limit", type=int, default=25, help="Result limit")
    parser.add_argument("--interval", default="1", help="Kline interval")
    parser.add_argument(
        "--interval-time", default="5min", help="Open-interest interval"
    )
    parser.add_argument("--depth", type=int, default=5, help="Orderbook depth")
    parser.add_argument("--symbols", help="Comma-separated symbols")
    parser.add_argument("--base-coin", help="Base coin filter")
    parser.add_argument("--status", help="Status filter")

    # ── Momentum ──────────────────────────────────────────────
    parser.add_argument("--strong-threshold", type=float, default=0.20)
    parser.add_argument("--mild-threshold", type=float, default=0.08)

    # ── Technical analysis ────────────────────────────────────
    parser.add_argument("--rsi-period", type=int, default=14, help="RSI period")
    parser.add_argument("--bb-period", type=int, default=20, help="Bollinger period")
    parser.add_argument("--bb-std", type=float, default=2.0, help="Bollinger std")
    parser.add_argument("--atr-mult", type=float, default=1.5, help="ATR multiplier")

    # ── Risk ──────────────────────────────────────────────────
    parser.add_argument("--sl-pct", type=float, help="Stop-loss as decimal (e.g. 0.02)")
    parser.add_argument(
        "--tp-pct", type=float, help="Take-profit as decimal (e.g. 0.04)"
    )
    parser.add_argument("--risk-usdt", type=float, help="Max USDT risk per trade")
    parser.add_argument("--sl-price", type=float, help="Stop-loss price (for sizing)")

    # ── Iceberg ───────────────────────────────────────────────
    parser.add_argument("--slices", type=int, default=5, help="Iceberg slices")
    parser.add_argument("--delay", type=float, help="Delay between slices (s)")

    # ── Output ────────────────────────────────────────────────
    parser.add_argument("--output", help="Output file path (JSON)")
    parser.add_argument("--orders-file", help="JSON file with batch orders")

    args = parser.parse_args()

    # ── Load batch orders from file ───────────────────────────
    orders_data: Optional[List[dict]] = None
    if args.orders_file:
        with open(args.orders_file) as fh:
            orders_data = json.load(fh)

    # ── Parse comma-separated symbols ────────────────────────
    symbols_list: Optional[List[str]] = None
    if getattr(args, "symbols", None):
        symbols_list = [s.strip() for s in args.symbols.split(",") if s.strip()]

    result = run(
        action=args.action,
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        price=args.price,
        order_type=getattr(args, "order_type", None),
        category=args.category,
        order_id=getattr(args, "order_id", None),
        stop_loss=getattr(args, "stop_loss", None),
        take_profit=getattr(args, "take_profit", None),
        trailing_stop=getattr(args, "trailing_stop", None),
        reduce_only=getattr(args, "reduce_only", False),
        time_in_force=getattr(args, "time_in_force", "GTC"),
        position_idx=getattr(args, "position_idx", 0),
        client_oid=getattr(args, "client_oid", None),
        trigger_price=getattr(args, "trigger_price", None),
        trigger_by=getattr(args, "trigger_by", "LastPrice"),
        leverage=args.leverage,
        buy_leverage=getattr(args, "buy_leverage", None),
        sell_leverage=getattr(args, "sell_leverage", None),
        account_type=getattr(args, "account_type", "UNIFIED"),
        limit=args.limit,
        interval=args.interval,
        interval_time=getattr(args, "interval_time", "5min"),
        depth=getattr(args, "depth", 5),
        symbols=symbols_list,
        base_coin=getattr(args, "base_coin", None),
        status=getattr(args, "status", None),
        strong_threshold=getattr(args, "strong_threshold", 0.20),
        mild_threshold=getattr(args, "mild_threshold", 0.08),
        rsi_period=getattr(args, "rsi_period", 14),
        bb_period=getattr(args, "bb_period", 20),
        bb_std=getattr(args, "bb_std", 2.0),
        atr_mult=getattr(args, "atr_mult", 1.5),
        sl_pct=getattr(args, "sl_pct", None),
        tp_pct=getattr(args, "tp_pct", None),
        risk_usdt=getattr(args, "risk_usdt", None),
        sl_price=getattr(args, "sl_price", None),
        orders=orders_data,
        slices=args.slices,
        delay=args.delay,
    )

    # ── Output handling ───────────────────────────────────────
    output_path = args.output or os.environ.get("LLM_OUTPUT")
    if output_path:
        with open(output_path, "w") as fh:
            json.dump(result, fh, indent=2)
        logger.info("Result written to %s", output_path)
    else:
        print(json.dumps(result, indent=2))
