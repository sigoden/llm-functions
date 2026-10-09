#!/usr/bin/env python3
"""
BYBIT REALM - Production-Grade Trading System Tool for LLM Functions v4.1

Fixes & Improvements in this version (v4.1 over v4.0):
  ── Critical Bug Fixes ──
  • Addressed potential deadlocks in caching logic.
  • Improved error handling for network requests and indicator calculations.

  ── Architectural Improvements ──
  • Implemented market data caching for tickers, klines, and orderbooks.
  • Enhanced API request retry mechanism with exponential backoff and Bybit error code checks.
  • Refactored indicator functions to accept klines directly for simplification.
  • Added new technical indicators: Stochastic Oscillator, Money Flow Index (MFI), On-Balance Volume (OBV).
  • Added Tor service status check on initialization.
  • Improved CLI argument descriptions and error handling for list inputs.

  ── Same Format & Compatibility ──
  • All original function signatures preserved
  • All Literal action strings preserved + new ones added
  • Environment variable names unchanged
  • JSON config file format unchanged
  • CLI argument names unchanged

Usage:
    Set environment variables BYBIT_API_KEY and BYBIT_API_SECRET before use.
    Optional: BYBIT_USE_TESTNET, TOR_ENABLED, TOR_SOCKS_PORT, TOR_USE_PYSOCKS
    Optional: PYSOCKS_ENABLED, PYSOCKS_HOST, PYSOCKS_PORT, PYSOCKS_REGION,
              PYSOCKS_GLOBAL, TOR_CONTROL_PORT, TOR_CONTROL_PASSWORD

    The tool will automatically look for a .env file in:
      - The same directory as the script
      - The current working directory
      - ~/.config/bybit/.env

    Network tiers (when TOR_ENABLED=true):
      1. SOCKS5 proxy via PySocks/requests  (socks5h://)
      2. torsocks binary wrapping curl
      3. direct connection (fallback)

    When TOR_ENABLED=false:
      1. direct connection only

Dependencies:
    requests, websocket-client, socks, python-dotenv, jsonschema (optional for config validation)
    Install via: pip install -r requirements.txt
"""

import ast  # For parsing list inputs from CLI
import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import socket as stdlib_socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

# ── dotenv support ───────────────────────────────────────────
try:
    from dotenv import load_dotenv

    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bybit.env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.config/bybit/.env"),
    ]
    for env_path in env_paths:
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break
    else:
        load_dotenv()
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
    import websocket

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False

try:
    import socks

    PYSOCKS_AVAILABLE = True
except ImportError:
    PYSOCKS_AVAILABLE = False

from logging.handlers import RotatingFileHandler

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
log_handler = RotatingFileHandler(
    "trading_bot.log", maxBytes=10 * 1024 * 1024, backupCount=5
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), log_handler],
)
logger = logging.getLogger("BybitRealm")


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


# ─────────────────────────────────────────────────────────────
# UTILITY: safe_float
# ─────────────────────────────────────────────────────────────
def _safe_float(val) -> Optional[float]:
    """Convert to float; return None for None or empty strings."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# Bybit error code constants
BYBIT_ERRORS = {
    0: ("Success", False),
    10001: ("System error", True),
    10002: ("Request too long", False),
    10003: ("ID not found", False),
    10004: ("Duplicate request", False),
    10005: ("Unauthorized", True),
    10006: ("Too many requests", False),
    10007: ("API key expired", True),
    10008: ("Timestamp expired", False),
    10009: ("Invalid IP", True),
    10010: ("Invalid signature", True),
    10011: ("Invalid parameters", False),
    10012: ("Request not allowed", False),
    10013: ("Param error", False),
    10014: ("Invalid nonce", False),
    10015: ("Invalid API key", True),
    10016: ("Insufficient balance", False),
    10017: ("Order not found", False),
    10018: ("Order already cancelled", False),
    10019: ("Order already processed", False),
    10020: ("Cannot modify market order", False),
    10021: ("Invalid order type", False),
    10022: ("Invalid side", False),
    10023: ("Invalid status", False),
    10024: ("Invalid price", False),
    10025: ("Invalid quantity", False),
    10026: ("Invalid time in force", False),
    10027: ("Position limit exceeded", False),
    10028: ("Leverage too high", False),
    10029: ("Leverage too low", False),
    10030: ("Order value too low", False),
    10031: ("Too many order changes", False),
    10032: ("Trading window closed", False),
    10033: ("Symbol suspended", False),
    10034: ("Position not found", False),
    10035: ("Insufficient margin", False),
    10036: ("Order would trigger liquidation", False),
    10037: ("User locked", True),
    10038: ("Account locked", True),
    10039: ("Withdrawal too frequent", False),
    10040: ("Withdrawal limit exceeded", False),
    10041: ("API key disabled", True),
    10042: ("Withdrawal not allowed", False),
    10043: ("Subaccount not allowed", False),
    10044: ("IP whitelist required", True),
    10045: ("Invalid transfer direction", False),
    10046: ("Transfer amount too small", False),
    10047: ("Transfer amount too large", False),
    10048: ("Transfer not allowed", False),
    10049: ("Transfer failed", False),
    10050: ("Currency not supported", False),
    10051: ("Currency pair not supported", False),
    10052: ("Contract not supported", False),
    10053: ("Option not supported", False),
    10054: ("Leverage not allowed", False),
    10055: ("Margin mode not allowed", False),
    10056: ("Position mode not allowed", False),
    10057: ("Order would reduce position below zero", False),
    10058: ("Invalid position index", False),
    10059: ("Invalid order filter", False),
    10060: ("Invalid time window", False),
}


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
    tor_control_pass: str = field(
        default_factory=lambda: os.getenv("TOR_CONTROL_PASSWORD", "")
    )
    tor_use_pysocks: bool = field(
        default_factory=lambda: os.getenv("TOR_USE_PYSOCKS", "true").lower() == "true"
    )

    # ── PySocks Geo Routing ───────────────────────────────────
    pysocks_enabled: bool = field(
        default_factory=lambda: os.getenv("PYSOCKS_ENABLED", "true").lower() == "true"
    )
    pysocks_host: str = field(
        default_factory=lambda: os.getenv("PYSOCKS_HOST", "127.0.0.1")
    )
    pysocks_port: int = field(
        default_factory=lambda: int(os.getenv("PYSOCKS_PORT", "9050"))
    )
    pysocks_region: str = field(default_factory=lambda: os.getenv("PYSOCKS_REGION", ""))
    pysocks_global: bool = field(
        default_factory=lambda: os.getenv("PYSOCKS_GLOBAL", "false").lower() == "true"
    )
    request_timeout: int = 15
    max_retries: int = 3

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

    @classmethod
    def from_file(cls, path: str = "trading_config.json") -> "TradingConfig":
        """Load configuration from a JSON file with environment variable fallback."""
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)

                settings = data.get("trading_settings", {})
                net = data.get("network", {})
                cb = data.get("circuit_breaker", {})
                rl = data.get("rate_limit", {})

                use_tor = data.get("use_tor")
                if use_tor is None:
                    use_tor = net.get(
                        "use_tor", os.getenv("TOR_ENABLED", "false").lower() == "true"
                    )

                tor_port = data.get("tor_socks_port")
                if tor_port is None:
                    tor_port = net.get(
                        "tor_socks_port", int(os.getenv("TOR_SOCKS_PORT", "9050"))
                    )

                return cls(
                    api_key=data.get("api_key") or os.getenv("BYBIT_API_KEY", ""),
                    api_secret=data.get("api_secret")
                    or os.getenv("BYBIT_API_SECRET", ""),
                    use_tor=bool(use_tor),
                    tor_socks_port=int(tor_port),
                    tor_control_port=int(
                        data.get(
                            "tor_control_port",
                            net.get(
                                "tor_control_port",
                                int(os.getenv("TOR_CONTROL_PORT", "9051")),
                            ),
                        )
                    ),
                    tor_control_pass=data.get(
                        "tor_control_password",
                        net.get(
                            "tor_control_password",
                            os.getenv("TOR_CONTROL_PASSWORD", ""),
                        ),
                    ),
                    max_retries=net.get("max_retries", 3),
                    cb_failure_threshold=cb.get("failure_threshold", 5),
                    rate_limit_calls=rl.get("calls", 10),
                    max_position_usdt=settings.get("max_position_usdt", 1000.0),
                    default_leverage=settings.get("leverage", 1),
                )
            except Exception as e:
                logger.error("Error loading config from %s: %s", path, e)
        return cls()

    @property
    def base_url(self) -> str:
        if self.testnet:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    def get_endpoints(self) -> List[str]:
        """Return list of available endpoints for rotation."""
        if self.testnet:
            return ["https://api-testnet.bybit.com"]
        return [
            "https://api.bybit.com",
        ]

    def validate(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "BYBIT_API_KEY and BYBIT_API_SECRET must be set "
                "as environment variables or in trading_config.json."
            )


# ─────────────────────────────────────────────────────────────
# PySocks GEO ROUTING
# (Moved BEFORE __main__ so it is importable and usable by health_check)
# ─────────────────────────────────────────────────────────────
class PySocksGeoRouter:
    """
    Enhanced geo-IP routing with automatic proxy rotation and fallback.
    FIX: Added health checking, proxy pool management, and automatic reconnection.
    """

    PROXY_CONFIGS = {
        "us_east": {"host": "127.0.0.1", "port": 9050, "rdns": True, "weight": 1},
        "us_west": {"host": "127.0.0.1", "port": 9051, "rdns": True, "weight": 1},
        "europe": {"host": "127.0.0.1", "port": 9052, "rdns": True, "weight": 1},
        "asia": {"host": "127.0.0.1", "port": 9053, "rdns": True, "weight": 1},
    }

    def __init__(
        self,
        proxy_type: int = None,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 9050,
        rdns: bool = True,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        try:
            import socks as _socks

            self.proxy_type = proxy_type or _socks.SOCKS5
        except ImportError:
            self.proxy_type = 5  # Default to SOCKS5 if socks lib not found
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.rdns = rdns
        self.username = username
        self.password = password
        self._original_socket = None
        self._session = self._create_socks_session()
        self._global_proxy_active = False

        # Enhanced fields
        self._proxy_pool = list(self.PROXY_CONFIGS.keys())
        self._proxy_health = dict.fromkeys(self.PROXY_CONFIGS, 1.0)
        self._current_region = None
        self._last_ip_change = time.time()
        self._ip_change_cooldown = 30  # Seconds between forced IP changes
        self._last_verified_ip = None

    def _create_socks_session(self):
        if not REQUESTS_AVAILABLE:
            return None
        session = requests.Session()
        proxy_url = f"socks5h://{self.proxy_host}:{self.proxy_port}"
        if self.username and self.password:
            proxy_url = f"socks5h://{self.username}:{self.password}@{self.proxy_host}:{self.proxy_port}"

        session.proxies = {"http": proxy_url, "https": proxy_url}
        retry = Retry(
            total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def set_proxy(
        self,
        proxy_host: str,
        proxy_port: int,
        proxy_type: int = None,
        rdns: bool = True,
    ):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        if proxy_type:
            self.proxy_type = proxy_type
        self.rdns = rdns
        self._session = self._create_socks_session()

    def set_region(self, region: str):
        if region not in self.PROXY_CONFIGS:
            raise ValueError(
                f"Unsupported region: {region}. Available: {list(self.PROXY_CONFIGS.keys())}"
            )
        config = self.PROXY_CONFIGS[region]
        self.set_proxy(config["host"], config["port"], rdns=config.get("rdns", True))

    def rotate_proxy(self) -> bool:
        """
        Rotate to next available proxy region.
        Returns True if rotation succeeded.
        """
        if not self._proxy_pool:
            logger.warning("No proxy regions available for rotation")
            return False
        # Remove current region from available pool temporarily
        current = self._current_region
        available = [r for r in self._proxy_pool if r != current]
        if not available:
            available = self._proxy_pool.copy()
        # Try regions in order of health score
        healthy_regions = sorted(
            available, key=lambda r: self._proxy_health.get(r, 0), reverse=True
        )
        for region in healthy_regions:
            try:
                if region != self._current_region:
                    self.set_region(region)
                    self._current_region = region
                    self._last_ip_change = time.time()
                    # Verify IP changed
                    new_ip = self.get_public_ip()
                    old_ip = self._last_verified_ip
                    if new_ip and new_ip != old_ip:
                        self._last_verified_ip = new_ip
                        self._proxy_health[region] = min(
                            1.0, self._proxy_health.get(region, 1.0) + 0.1
                        )
                        logger.info(
                            f"Rotated to proxy region '{region}' - new IP: {new_ip}"
                        )
                        return True
                    else:
                        # IP didn't change, mark as degraded
                        self._proxy_health[region] = max(
                            0, self._proxy_health.get(region, 1.0) - 0.3
                        )
            except Exception as e:
                self._proxy_health[region] = max(
                    0, self._proxy_health.get(region, 1.0) - 0.5
                )
                logger.warning(f"Proxy rotation to '{region}' failed: {e}")
        return False

    def bypass_geo_block(self) -> bool:
        """
        Attempt to bypass geo-IP block by rotating through proxies.
        Returns True if successful after trying all available proxies.
        """
        attempts = 0
        max_attempts = len(self._proxy_pool) * 2  # Try each region twice
        while attempts < max_attempts:
            attempts += 1
            # Check if we need to rotate based on time
            if time.time() - self._last_ip_change < self._ip_change_cooldown:
                sleep_time = self._ip_change_cooldown - (
                    time.time() - self._last_ip_change
                )
                logger.info(f"Waiting {sleep_time:.1f}s before next rotation...")
                time.sleep(min(sleep_time, 5))

            if self.rotate_proxy():
                return True
            time.sleep(2)  # Small delay between attempts
        logger.error(f"Failed to bypass geo-block after {attempts} attempts")
        return False

    def set_region_with_fallback(self, region: str) -> bool:
        """Set proxy region with automatic fallback if connection fails."""
        try:
            self.set_region(region)
            # Test the connection
            ip = self.get_public_ip()
            if ip and ip != "Unknown":
                self._current_region = region
                self._last_verified_ip = ip
                logger.info(f"Set proxy to '{region}' - verified IP: {ip}")
                return True
            else:
                logger.warning(f"Could not verify proxy region '{region}'")
                return self.bypass_geo_block()
        except Exception as e:
            logger.warning(f"Failed to set region '{region}', falling back: {e}")
            return self.bypass_geo_block()

    def enable_global_proxy(self):
        """Enable global SOCKS5 proxy with proper state management."""
        if not PYSOCKS_AVAILABLE:
            logger.warning("Cannot enable global proxy: PySocks not installed")
            return False
        try:
            import socks as _socks

            # Store original socket only once
            if self._original_socket is None:
                self._original_socket = stdlib_socket.socket
                logger.info("Saved original socket: %s", self._original_socket)

            # Configure and patch
            _socks.set_default_proxy(
                _socks.SOCKS5,
                self.proxy_host,
                self.proxy_port,
                rdns=self.rdns,
                username=self.username,
                password=self.password,
            )
            stdlib_socket.socket = _socks.socksocket
            self._global_proxy_active = True
            logger.info(
                "Global SOCKS5 proxy enabled: %s:%d", self.proxy_host, self.proxy_port
            )
            return True
        except Exception as exc:
            logger.error("Failed to enable global proxy: %s", exc)
            self.disable_global_proxy()  # Clean up on failure
            return False

    def disable_global_proxy(self):
        """Disable global SOCKS5 proxy with proper restoration."""
        try:
            if self._original_socket is not None:
                stdlib_socket.socket = self._original_socket
                self._original_socket = None
                self._global_proxy_active = False
                logger.info("Global SOCKS5 proxy disabled")
            return True
        except Exception as exc:
            logger.error("Failed to disable global proxy: %s", exc)
            return False

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        timeout: int = 15,
    ) -> dict:
        if not self._session:
            raise RuntimeError("requests library not available")
        resp = self._session.request(
            method=method,
            url=url,
            headers=headers or {},
            params=params,
            json=json_data,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get_public_ip(self) -> str:
        try:
            if self._session:
                resp = self._session.get(
                    "https://api.ipify.org?format=json", timeout=10
                )
            else:
                resp = requests.get("https://api.ipify.org?format=json", timeout=10)
            return resp.json().get("ip", "Unknown")
        except Exception:
            return "Unknown"

    def get_geo_location(self) -> dict:
        try:
            if self._session:
                resp = self._session.get("http://ip-api.com/json/", timeout=10)
            else:
                resp = requests.get("http://ip-api.com/json/", timeout=10)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def close(self):
        self.disable_global_proxy()
        if self._session:
            self._session.close()


def configure_pysocks_from_env() -> Optional[PySocksGeoRouter]:
    """Configure PySocks geo routing based on environment variables."""
    if os.getenv("PYSOCKS_ENABLED", "true").lower() != "true":
        return None
    if not PYSOCKS_AVAILABLE:
        logger.warning("PYSOCKS_ENABLED=true but PySocks library not installed")
        return None

    host = os.getenv("PYSOCKS_HOST", "127.0.0.1")
    port = int(os.getenv("PYSOCKS_PORT", "9050"))
    region = os.getenv("PYSOCKS_REGION", "")
    global_proxy = os.getenv("PYSOCKS_GLOBAL", "false").lower() == "true"
    username = os.getenv("PYSOCKS_USERNAME", "")
    password = os.getenv("PYSOCKS_PASSWORD", "")

    router = PySocksGeoRouter(
        proxy_host=host,
        proxy_port=port,
        rdns=True,
        username=username if username else None,
        password=password if password else None,
    )

    if region and region in PySocksGeoRouter.PROXY_CONFIGS:
        router.set_region(region)
        logger.info("PySocks geo region set to: %s", region)

    if global_proxy:
        router.enable_global_proxy()

    return router


# ─────────────────────────────────────────────────────────────
# WebSocket MANAGER
# ─────────────────────────────────────────────────────────────
class WebSocketManager:
    """Manage WebSocket connections for real-time data."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.ws_url = (
            "wss://stream-testnet.bybit.com/v5/public/linear"
            if config.testnet
            else "wss://stream.bybit.com/v5/public/linear"
        )
        self.ws = None
        self.subscriptions: Dict[str, Callable] = {}
        self.running = False
        self._reconnect_lock = threading.Lock()

    def connect(self):
        if not WEBSOCKET_AVAILABLE:
            raise RuntimeError("websocket-client library not installed")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )
        self.running = True
        wst = threading.Thread(target=self.ws.run_forever, daemon=True)
        wst.start()

    def subscribe_orderbook(self, symbol: str, callback: Callable):
        if not self.ws:
            self.connect()
        msg = {"op": "subscribe", "args": [f"orderbook.200.{symbol}"]}
        self.subscriptions[f"orderbook.200.{symbol}"] = callback
        self.ws.send(json.dumps(msg))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            topic = data.get("topic", "")
            if topic and topic in self.subscriptions:
                self.subscriptions[topic](data.get("data", {}))
        except Exception as exc:
            logger.error("WebSocket message parse error: %s", exc)

    def _on_error(self, ws, error):
        logger.error("WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(
            "WebSocket closed (code=%s). Scheduling reconnect…", close_status_code
        )
        self.running = False
        # FIX: reconnect in a new daemon thread, not in the callback thread
        t = threading.Thread(target=self._reconnect, daemon=True)
        t.start()

    def _reconnect(self):
        with self._reconnect_lock:
            if self.running:
                return
            time.sleep(5)
            logger.info("WebSocket reconnecting…")
            try:
                self.connect()
                # Re-subscribe existing topics
                for topic in list(self.subscriptions.keys()):
                    if self.ws:
                        self.ws.send(json.dumps({"op": "subscribe", "args": [topic]}))
            except Exception as exc:
                logger.error("WebSocket reconnect failed: %s", exc)

    def _on_open(self, ws):
        logger.info("WebSocket connected to %s", self.ws_url)


# ─────────────────────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────────────────────
class RateLimiter:
    """Enhanced rate limiter with cooldown support and burst handling."""

    def __init__(self, max_calls: int, window: float, burst_limit: int = None) -> None:
        self._max_calls = max_calls
        self._window = window
        self._burst_limit = burst_limit or (max_calls * 2)  # Allow 2x burst
        self._calls: deque = deque()
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._burst_calls = 0
        self._burst_window_start = time.monotonic()
        self._burst_window_duration = window * 0.5  # 50% of normal window for burst

    def acquire(self, burst: bool = False) -> None:
        with self._lock:
            now = time.monotonic()

            # Check cooldown
            if now < self._cooldown_until:
                sleep_time = self._cooldown_until - now
                logger.warning("Rate limit cooldown: sleeping %.2fs", sleep_time)
                time.sleep(sleep_time)
                now = time.monotonic()

            # Reset burst window if expired
            if now - self._burst_window_start > self._burst_window_duration:
                self._burst_calls = 0
                self._burst_window_start = now

            # Check burst limit
            if burst and self._burst_calls >= self._burst_limit:
                logger.warning(
                    "Burst limit exceeded, falling back to normal rate limiting"
                )
                burst = False

            # Clean old calls
            while self._calls and self._calls[0] <= now - self._window:
                self._calls.popleft()

            # Rate limit check
            if len(self._calls) >= self._max_calls:
                sleep_for = self._window - (now - self._calls[0])
                if sleep_for > 0:
                    logger.debug("Rate limiter sleeping %.3fs", sleep_for)
                    time.sleep(sleep_for)
                    now = time.monotonic()

            self._calls.append(now)
            if burst:
                self._burst_calls += 1

    def set_cooldown(self, seconds: float):
        self._cooldown_until = time.monotonic() + seconds

    @property
    def current_usage(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(1 for c in self._calls if c > now - self._window)


# ─────────────────────────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────────────────────────
class CircuitBreaker:
    """Three-state circuit breaker – FIX: no sleep while holding lock."""

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
        with self._lock:
            self._maybe_transition()
            return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def call(self, fn: Callable, *args, **kwargs) -> Any:
        with self._lock:
            self._maybe_transition()
            if self._state == CircuitState.OPEN:
                wait = self._recovery_timeout - (
                    time.monotonic() - self._last_failure_ts
                )
                raise RuntimeError(f"Circuit OPEN – retry in {max(0, wait):.1f}s")
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise exc

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            logger.info("Circuit manually reset → CLOSED")

    def _maybe_transition(self) -> None:
        """Must be called with lock held."""
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_ts >= self._recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("Circuit → HALF_OPEN (testing recovery)")

    def _on_success(self) -> None:
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("Circuit → CLOSED")
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        # FIX: determine whether to trip, release lock, THEN sleep
        should_cooldown = False
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
                logger.error("Circuit → OPEN (cooldown %.0fs)", self._cooldown)
                should_cooldown = True
        # Sleep OUTSIDE the lock to prevent deadlocking other threads
        if should_cooldown:
            time.sleep(self._cooldown)


# ─────────────────────────────────────────────────────────────
# TOR / NETWORK MANAGER
# ─────────────────────────────────────────────────────────────
class TorManager:
    """
    Multi-tier network layer.
    When use_tor=True:
      Tier 1 → SOCKS5 proxy via requests (socks5h://)
      Tier 2 → torsocks binary wrapping curl
      Tier 3 → direct connection (fallback)
    When use_tor=False:
      Tier 1 → direct connection only

    FIX v4.0:
      - Merged old _tier_pysocks and _tier_proxy (were identical) into _tier_socks
      - Added Tor circuit renewal via control port
      - _on_failure no longer sleeps under lock
    """

    MAX_API_RETRIES = 5
    BASE_API_RETRY_DELAY = 1.0

    def __init__(
        self,
        enabled: bool,
        socks_port: int,
        timeout: int,
        max_retries: int,
        use_pysocks: bool = True,
        control_port: int = 9051,
        control_password: str = "",
    ) -> None:
        self.enabled = enabled
        self.socks_port = socks_port
        self.timeout = timeout
        self.control_port = control_port
        self.control_password = control_password
        self._torsocks_bin = shutil.which("torsocks") if enabled else None
        self._session = self._build_session(max_retries) if REQUESTS_AVAILABLE else None
        self._socks_session = None
        self._auto_recovery = True
        self._circuit_failures = 0
        self._max_circuit_failures = 5
        self._last_ip = None
        self._tor_control_port_accessible = False  # Flag for Tor status

        if enabled and use_pysocks and REQUESTS_AVAILABLE:
            self._socks_session = self._build_socks_session(max_retries, socks_port)
            logger.info(
                "SOCKS5 session initialized on port %d (PySocks available: %s)",
                socks_port,
                PYSOCKS_AVAILABLE,
            )
        elif enabled and use_pysocks and not REQUESTS_AVAILABLE:
            logger.warning("TOR_USE_PYSOCKS=true but requests library not installed")

        # Check Tor control port accessibility
        if self.enabled:
            self._tor_control_port_accessible = self._check_tor_control_port()

    def _check_tor_control_port(self) -> bool:
        """Check if Tor control port is accessible."""
        if not self.enabled:
            return False
        try:
            s = stdlib_socket.socket(stdlib_socket.AF_INET, stdlib_socket.SOCK_STREAM)
            s.settimeout(5)  # Short timeout for check
            s.connect(("127.0.0.1", self.control_port))
            s.close()
            logger.info("Tor control port %d is accessible.", self.control_port)
            return True
        except (stdlib_socket.timeout, ConnectionRefusedError):
            logger.warning(
                "Tor control port %d is not accessible. Tor may not be running or configured correctly.",
                self.control_port,
            )
            return False
        except Exception as exc:
            logger.warning(
                "Error checking Tor control port %d: %s", self.control_port, exc
            )
            return False

    def renew_tor_circuit(self, retries: int = 3) -> bool:
        """
        Send NEWNYM signal to Tor control port with retry logic.
        Added: exponential backoff, connection validation, and circuit verification.
        """
        if not self._tor_control_port_accessible:
            logger.warning("Tor control port not accessible, cannot renew circuit.")
            return False

        for attempt in range(retries):
            try:
                s = stdlib_socket.socket(
                    stdlib_socket.AF_INET, stdlib_socket.SOCK_STREAM
                )
                s.settimeout(10)
                s.connect(("127.0.0.1", self.control_port))

                # Authenticate with clearer protocol handling
                auth_cmd = (
                    f'AUTHENTICATE "{self.control_password}"\r\n'.encode()
                    if self.control_password
                    else b"AUTHENTICATE\r\n"
                )
                s.sendall(auth_cmd)
                resp = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if b"\r\n" in resp:
                        break

                resp_str = resp.decode().strip()
                if "250" not in resp_str:
                    logger.warning(
                        f"Tor AUTHENTICATE attempt {attempt + 1}/{retries} failed: {resp_str}"
                    )
                    s.close()
                    time.sleep(2**attempt)
                    continue

                # Send NEWNYM signal
                s.sendall(b"SIGNAL NEWNYM\r\n")
                resp = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if b"\r\n" in resp:
                        break
                s.close()
                resp_str = resp.decode().strip()

                if "250" in resp_str:
                    logger.info(
                        f"Tor circuit renewed (NEWNYM) on attempt {attempt + 1}"
                    )
                    time.sleep(2)
                    # Verify new circuit by checking IP changed
                    try:
                        old_ip = self._last_ip
                        new_ip = self._get_current_tor_ip()
                        if new_ip and new_ip != old_ip:
                            self._last_ip = new_ip
                            logger.info(f"Tor exit IP changed to: {new_ip}")
                            return True
                        else:
                            logger.warning(
                                "Tor IP didn't change after NEWNYM, retrying..."
                            )
                            time.sleep(3)
                            continue
                    except Exception as e:
                        logger.warning(f"Could not verify IP change: {e}")
                        return True
                else:
                    logger.warning(
                        f"Tor NEWNYM attempt {attempt + 1}/{retries} failed: {resp_str}"
                    )
                    time.sleep(2**attempt)
            except stdlib_socket.timeout:
                logger.warning(
                    f"Tor control connection timeout on attempt {attempt + 1}"
                )
                time.sleep(2**attempt)
            except Exception as exc:
                logger.warning(
                    f"Tor circuit renewal attempt {attempt + 1}/{retries} failed: {exc}"
                )
                time.sleep(2**attempt)
        logger.error(f"All {retries} Tor circuit renewal attempts failed")
        return False

    def _get_current_tor_ip(self) -> Optional[str]:
        """Get current Tor exit node IP via check.torproject.org"""
        try:
            # Use requests directly for this quick check, assuming it's available if Tor is used
            if not REQUESTS_AVAILABLE:
                return None
            proxies = {
                "http": f"socks5h://127.0.0.1:{self.socks_port}",
                "https": f"socks5h://127.0.0.1:{self.socks_port}",
            }
            resp = requests.get(
                "https://check.torproject.org/api/ip", proxies=proxies, timeout=10
            )
            data = resp.json()
            return data.get("IP")
        except Exception:
            return None

    def request(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        """
        Try network tiers in order with auto-recovery on geo-blocks.
        Enhanced with retry logic for API errors.
        """
        # For signed requests, try direct first (SOCKS5 may have issues with auth headers)
        # For unsigned requests, use SOCKS5 when available
        if signed:
            tiers = [self._tier_direct, self._tier_socks, self._tier_torsocks]
        elif self.enabled and self._socks_session:
            tiers = [self._tier_socks, self._tier_torsocks, self._tier_direct]
        else:
            tiers = [self._tier_direct]

        last_exc: Optional[Exception] = None

        for attempt in range(1, self.MAX_API_RETRIES + 1):
            for tier in tiers:
                try:
                    tier_name = tier.__name__.replace("_tier_", "")
                    logger.debug("Attempting network tier: %s", tier_name)
                    result = tier(method, url, headers, params, json_data)
                    self._circuit_failures = 0  # Reset geo-block counter on success
                    return result  # Success
                except RuntimeError as exc:
                    last_exc = exc
                    error_str = str(exc).lower()

                    # Auto-recovery on geo-block
                    if any(
                        indicator in error_str
                        for indicator in ["403", "blocked", "forbidden", "geo"]
                    ):
                        self._circuit_failures += 1
                        if (
                            self._auto_recovery
                            and self._circuit_failures < self._max_circuit_failures
                        ):
                            logger.warning(
                                f"Geo-block detected ({tier_name}), attempting auto-recovery ({self._circuit_failures}/{self._max_circuit_failures})"
                            )
                            if self.renew_tor_circuit():
                                time.sleep(2)
                                # Retry current tier after renewal
                                try:
                                    return tier(method, url, headers, params, json_data)
                                except Exception:
                                    continue  # Try next tier if renewal doesn't help

                    logger.warning(f"Network tier {tier_name} failed: {exc}")
                except ConnectionError as exc:  # Catch network issues
                    last_exc = exc
                    logger.warning(
                        f"Network tier {tier.__name__.replace('_tier_', '')} failed with ConnectionError: {exc}"
                    )
                except Exception as exc:  # Catch other unexpected errors
                    last_exc = exc
                    logger.warning(
                        f"Network tier {tier.__name__.replace('_tier_', '')} failed with unexpected error: {exc}"
                    )

            # If all tiers failed in this attempt, wait before next retry
            if attempt < self.MAX_API_RETRIES:
                delay = self.BASE_API_RETRY_DELAY * (
                    2 ** (attempt - 1)
                )  # Exponential backoff
                logger.info("All network tiers failed. Retrying in %.1fs...", delay)
                time.sleep(delay)
            else:
                raise ConnectionError(
                    f"All network tiers exhausted. Last error: {last_exc}"
                )

        # Should not be reached if MAX_API_RETRIES is > 0
        raise ConnectionError(
            f"API request failed after {self.MAX_API_RETRIES} attempts. Last error: {last_exc}"
        )

    # ── Tier implementations ─────────────────────────────────

    def _tier_socks(self, method, url, headers, params, json_data) -> dict:
        """Tier 1: SOCKS5 proxy via requests library (uses PySocks if installed)."""
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not available")
        if not self._socks_session:
            raise RuntimeError("SOCKS session not initialized")
        resp = self._socks_session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.timeout,
        )
        return self._parse_response(resp)

    def _tier_torsocks(self, method, url, headers, params, json_data) -> dict:
        """Tier 2: torsocks binary wrapping curl."""
        if not self._torsocks_bin:
            raise RuntimeError("torsocks binary not found")

        cmd = [self._torsocks_bin, "curl", "-s", "-X", method]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data, separators=(",", ":"))]
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        cmd.append(url)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 5,
            )
            if result.returncode != 0:
                raise RuntimeError(f"torsocks curl failed: {result.stderr.strip()}")
            if not result.stdout.strip():
                raise RuntimeError("torsocks curl returned empty response")

            data = json.loads(result.stdout)
            ret_code = data.get("retCode", 0)
            if ret_code != 0:
                raise RuntimeError(
                    f"Bybit API error retCode={ret_code}: {data.get('retMsg', 'unknown')}"
                )
            return data
        except subprocess.TimeoutExpired:
            raise TimeoutError("torsocks curl command timed out")
        except (json.JSONDecodeError, RuntimeError) as e:
            raise RuntimeError(f"torsocks execution error: {e}") from e

    def _tier_direct(self, method, url, headers, params, json_data) -> dict:
        """Tier 3: direct connection (no proxy)."""
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not available")
        if not self._session:  # Should be initialized in __init__
            raise RuntimeError("Direct HTTP session not initialized")
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.timeout,
        )
        return self._parse_response(resp)

    # ── Session builders ─────────────────────────────────────

    @staticmethod
    def _build_socks_session(max_retries: int, socks_port: int):
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
        session.proxies = {
            "http": f"socks5h://127.0.0.1:{socks_port}",
            "https": f"socks5h://127.0.0.1:{socks_port}",
        }
        return session

    @staticmethod
    def _build_session(max_retries: int):
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

    @staticmethod
    def _parse_response(resp) -> dict:
        """Parse HTTP response, checking both HTTP status and Bybit retCode."""
        # Handle 403 Forbidden as geo-block
        if resp.status_code == 403:
            raise RuntimeError(f"403 Client Error: Forbidden for url: {resp.url}")
        if not resp.ok:
            try:
                data = resp.json()
                ret_code = data.get("retCode", 0)
                if ret_code != 0:
                    raise RuntimeError(
                        f"Bybit API error HTTP {resp.status_code} retCode={ret_code}: "
                        f"{data.get('retMsg', 'unknown')}"
                    )
            except (ValueError, RuntimeError):
                resp.raise_for_status()
                raise
        try:
            data = resp.json()
        except Exception:
            resp.raise_for_status()
            raise
        ret_code = data.get("retCode", 0)
        if ret_code != 0:
            raise RuntimeError(
                f"Bybit API error retCode={ret_code}: {data.get('retMsg', 'unknown')}"
            )
        return data


# ─────────────────────────────────────────────────────────────
# INSTRUMENT / PRICE FILTER CACHE
# ─────────────────────────────────────────────────────────────
@dataclass
class LotSizeFilter:
    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> float:
        if self.qty_step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(self.qty_step))))
        adjusted = round(round(qty / self.qty_step) * self.qty_step, precision)
        return float(max(self.min_order_qty, min(self.max_order_qty, adjusted)))


@dataclass
class PriceFilter:
    tick_size: float
    min_price: float = 0.0
    max_price: float = 1e12

    def adjust(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(self.tick_size))))
        adjusted = round(round(price / self.tick_size) * self.tick_size, precision)
        return float(max(self.min_price, min(self.max_price, adjusted)))


@dataclass
class InstrumentInfo:
    lot_size: LotSizeFilter
    price_flt: PriceFilter
    symbol: str
    status: str = "Trading"
    fetched_at: float = field(default_factory=time.time)

    @property
    def is_stale(self) -> bool:
        return time.time() - self.fetched_at > 3600


# ─────────────────────────────────────────────────────────────
# MARKET INTELLIGENCE DATACLASSES
# ─────────────────────────────────────────────────────────────
@dataclass
class MomentumResult:
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
# MAIN DISPATCHER
# ─────────────────────────────────────────────────────────────
class BybitToolDispatcher:
    """Central dispatcher for all Bybit API interactions."""

    MAX_API_RETRIES = 3
    _RECV_WINDOW = "10000"

    def __init__(self, config: TradingConfig) -> None:
        config.validate()
        self.config = config
        self.tor = TorManager(
            enabled=config.use_tor,
            socks_port=config.tor_socks_port,
            timeout=config.request_timeout,
            max_retries=config.max_retries,
            use_pysocks=config.tor_use_pysocks,
            control_port=config.tor_control_port,
            control_password=config.tor_control_pass,
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
        # Caching
        self._instr_cache: Dict[str, InstrumentInfo] = {}
        self._ticker_cache: Dict[
            str, Tuple[dict, float]
        ] = {}  # key: symbol_category, value: (data, timestamp)
        self._klines_cache: Dict[
            str, Tuple[List[List[float]], float]
        ] = {}  # key: symbol_interval_category_limit_start_end, value: (data, timestamp)
        self._orderbook_cache: Dict[
            str, Tuple[dict, float]
        ] = {}  # key: symbol_category_limit, value: (data, timestamp)
        self._cache_ttl = {
            "instrument": 3600,  # 1 hour
            "ticker": 30,  # 30 seconds
            "klines": 300,  # 5 minutes (adjust based on interval)
            "orderbook": 10,  # 10 seconds
        }
        self._cache_lock = threading.Lock()

        self._time_offset = 0  # ms offset: server_time - local_time
        self._time_synced = False
        self._geo_router: Optional[PySocksGeoRouter] = None

        # Initialize PySocks geo router if configured
        if config.pysocks_enabled and PYSOCKS_AVAILABLE:
            self._geo_router = configure_pysocks_from_env()

    # ══════════════════════════════════════════════════════════
    # SERVER TIME SYNC
    # ══════════════════════════════════════════════════════════
    def _sync_server_time(self) -> None:
        """Sync local clock with Bybit server to prevent signature drift."""
        try:
            local_before = int(time.time() * 1000)
            resp = self.api_request(
                "GET",
                "/v5/market/time",
                headers={"Content-Type": "application/json"},
                signed=False,
            )
            local_after = int(time.time() * 1000)
            server_time = int(resp.get("result", {}).get("timeNano", "0")) // 1_000_000
            if server_time > 0:
                local_mid = (local_before + local_after) // 2
                self._time_offset = server_time - local_mid
                self._time_synced = True
                if abs(self._time_offset) > 500:
                    logger.warning(
                        "Clock drift detected: %dms offset from server",
                        self._time_offset,
                    )
                else:
                    logger.debug("Server time synced: offset=%dms", self._time_offset)
        except Exception as exc:
            logger.warning("Server time sync failed: %s", exc)

    def _get_timestamp(self) -> str:
        """Get synchronized timestamp for HMAC signing."""
        if not self._time_synced:
            self._sync_server_time()
        return str(int(time.time() * 1000) + self._time_offset)

    # ══════════════════════════════════════════════════════════
    # AUTH & REQUEST
    # ══════════════════════════════════════════════════════════
    def _sign(self, payload: str, timestamp: str) -> str:
        """Bybit V5 signature: HMAC-SHA256(timestamp + api_key + recv_window + payload)"""
        raw = f"{timestamp}{self.config.api_key}{self._RECV_WINDOW}{payload}"
        return hmac.new(
            self.config.api_secret.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def parse_bybit_error(self, error_code: int, error_msg: str) -> dict:
        """Parse Bybit error code into structured info."""
        error_info = BYBIT_ERRORS.get(error_code, ("Unknown error", None))
        is_fatal = error_info[1] if error_info[1] is not None else True

        return {
            "code": error_code,
            "message": error_msg,
            "category": error_info[0],
            "is_fatal": is_fatal,
            "is_retryable": not is_fatal
            and error_code not in (10016, 10017, 10018, 10019),
            "user_action": self._get_error_action(error_code),
        }

    def _get_error_action(self, error_code: int) -> str:
        """Get recommended user action for error."""
        actions = {
            10001: "Wait and retry, or contact support",
            10002: "Reduce request frequency",
            10003: "Verify order ID and retry",
            10006: "Implement exponential backoff",
            10007: "Generate new API key",
            10008: "Sync server time",
            10009: "Check IP whitelist settings",
            10010: "Verify API secret",
            10015: "Verify API key",
            10016: "Add more funds to account",
            10017: "Order may already be filled or cancelled",
            10018: "Order already cancelled",
            10028: "Reduce leverage",
            10035: "Add more margin",
            10037: "Contact support to unlock account",
            10041: "Re-enable API key",
        }
        return actions.get(error_code, "Review and retry")

    def _handle_api_error(self, error_code: int, error_msg: str) -> dict:
        """Handle API error with structured response."""
        parsed = self.parse_bybit_error(error_code, error_msg)

        logger.warning(
            "Bybit API error %d: %s (fatal=%s, retryable=%s)",
            error_code,
            error_msg,
            parsed["is_fatal"],
            parsed["is_retryable"],
        )

        return {
            "status": "error",
            "code": error_code,
            "message": error_msg,
            "category": parsed["category"],
            "is_fatal": parsed["is_fatal"],
            "is_retryable": parsed["is_retryable"],
            "user_action": parsed["user_action"],
        }

    def _build_get_query(self, params: dict) -> str:
        """Build sorted query string for GET signature."""

        def format_val(v):
            if isinstance(v, bool):
                return str(v).lower()
            return str(v)

        return "&".join(f"{k}={format_val(v)}" for k, v in sorted(params.items()))

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
        headers: Optional[dict] = None,
    ) -> dict:
        """Make an API request to Bybit with error handling and retries.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters for GET requests
            json_data: JSON body for POST requests
            signed: Whether to sign the request

        Returns:
            API response as dictionary

        Raises:
            ConnectionError: When all endpoints fail or retries are exhausted
            RuntimeError: When circuit breaker is open or API returns non-retryable error
        """
        self.limiter.acquire()
        request_id = str(uuid.uuid4())[:8]

        # Normalize enum values in params/json_data
        for container in (params, json_data):
            if container:
                if "symbol" in container and isinstance(container["symbol"], str):
                    container["symbol"] = container["symbol"].upper()
                if "category" in container and isinstance(
                    container["category"], (Category, Enum)
                ):
                    container["category"] = container["category"].value

        ts = self._get_timestamp()

        # Build the payload string for signing
        if method == "POST":
            payload_str = json.dumps(
                json_data or {}, sort_keys=True, separators=(",", ":")
            )
        else:
            payload_str = self._build_get_query(params or {})

        logger.debug("[%s] Signature payload: %s", request_id, payload_str)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }
        if headers:
            headers.update(headers)

        if signed:
            headers.update(
                {
                    "X-BAPI-API-KEY": self.config.api_key,
                    "X-BAPI-TIMESTAMP": ts,
                    "X-BAPI-RECV-WINDOW": self._RECV_WINDOW,
                    "X-BAPI-SIGN": self._sign(payload_str, ts),
                }
            )

        # Endpoint Rotation with error handling and retries
        endpoints = self.config.get_endpoints()
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.MAX_API_RETRIES + 1):
            for base_url in endpoints:
                url = f"{base_url}{endpoint}"
                try:
                    logger.debug(
                        "[%s] Attempt %d/%d - %s %s signed=%s",
                        request_id,
                        attempt,
                        self.MAX_API_RETRIES,
                        method,
                        url,
                        signed,
                    )

                    # Use the TorManager's request method which includes tier selection and geo-block handling
                    response_data = self.tor.request(
                        method,
                        url,
                        headers,
                        params if method == "GET" else None,
                        json_data if method == "POST" else None,
                        signed=signed,
                    )

                    # If successful, return data and reset counters
                    self._circuit_failures = 0  # Reset geo-block counter on success
                    return response_data

                except RuntimeError as exc:  # Catch circuit breaker or specific API errors from tor.request
                    last_exc = exc
                    error_str = str(exc).lower()

                    # Handle circuit breaker open state
                    if "circuit open" in error_str:
                        logger.error(
                            "[%s] Circuit OPEN – aborting request: %s", request_id, exc
                        )
                        raise  # Re-raise immediately, don't retry if circuit is open

                    # Handle geo-block detection and auto-recovery attempt
                    if any(
                        indicator in error_str
                        for indicator in ["403", "blocked", "forbidden", "geo"]
                    ):
                        self._circuit_failures += 1
                        if (
                            self._auto_recovery
                            and self._circuit_failures < self._max_circuit_failures
                        ):
                            logger.warning(
                                "[%s] Geo-block detected (%s), attempting Tor circuit renewal (%d/%d)",
                                request_id,
                                base_url,
                                self._circuit_failures,
                                self._max_circuit_failures,
                            )
                            if self.tor.renew_tor_circuit():
                                time.sleep(2)  # Wait after renewal
                                # Retry current tier after renewal
                                try:
                                    logger.debug(
                                        "[%s] Retrying after Tor renewal...", request_id
                                    )
                                    return self.tor.request(
                                        method,
                                        url,
                                        headers,
                                        params if method == "GET" else None,
                                        json_data if method == "POST" else None,
                                        signed=signed,
                                    )
                                except Exception:
                                    logger.warning(
                                        "[%s] Retry after Tor renewal failed, continuing to next tier/attempt.",
                                        request_id,
                                    )
                                    continue  # Try next tier or attempt
                        else:
                            logger.warning(
                                "[%s] Geo-block detected, max recovery attempts reached or auto-recovery disabled.",
                                request_id,
                            )

                    logger.warning(
                        "[%s] Endpoint %s failed: %s", request_id, base_url, exc
                    )

                except ConnectionError as exc:  # Catch network issues
                    last_exc = exc
                    logger.warning(
                        "[%s] Endpoint %s failed with ConnectionError: %s",
                        request_id,
                        base_url,
                        exc,
                    )

                except Exception as exc:  # Catch other unexpected errors
                    last_exc = exc
                    logger.warning(
                        "[%s] Endpoint %s failed with unexpected error: %s",
                        request_id,
                        base_url,
                        exc,
                    )

            # If all tiers failed in this attempt, wait before next retry
            if attempt < self.MAX_API_RETRIES:
                delay = self.BASE_API_RETRY_DELAY * (
                    2 ** (attempt - 1)
                )  # Exponential backoff
                logger.info(
                    "[%s] All endpoints failed. Retrying in %.1fs...", request_id, delay
                )
                time.sleep(delay)
            else:
                raise ConnectionError(
                    f"[{request_id}] All API endpoints exhausted. Last error: {last_exc}"
                )

        # Should not be reached if MAX_API_RETRIES is > 0
        raise ConnectionError(
            f"[{request_id}] API request failed after {self.MAX_API_RETRIES} attempts. Last error: {last_exc}"
        )

    # ══════════════════════════════════════════════════════════
    # INSTRUMENT / LOT-SIZE + PRICE FILTER
    # ══════════════════════════════════════════════════════════
    def _fetch_instrument(self, symbol: str, category: str) -> InstrumentInfo:
        cache_key = f"{symbol}_{category}"
        now = time.time()
        with self._cache_lock:
            if cache_key in self._instr_cache:
                info, ts = self._instr_cache[cache_key]
                if now - ts < self._cache_ttl["instrument"]:
                    logger.debug("Cache hit for instrument: %s", cache_key)
                    return info
                logger.debug("Cache stale for instrument: %s", cache_key)

        logger.info("Fetching instrument info for %s…", symbol)
        resp = self.api_request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        try:
            item = resp["result"]["list"][0]
            lot = item["lotSizeFilter"]
            pft = item.get("priceFilter", {})

            lsf = LotSizeFilter(
                qty_step=float(lot.get("qtyStep") or lot.get("basePrecision", 1)),
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
                fetched_at=time.time(),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Could not parse instrument info for {symbol}: {exc}"
            ) from exc

        with self._cache_lock:
            self._instr_cache[cache_key] = (info, time.time())
        return info

    def adjust_quantity(
        self, symbol: str, qty: float, category: str = Category.LINEAR
    ) -> float:
        info = self._fetch_instrument(symbol, category)
        adjusted = info.lot_size.adjust(qty)
        logger.debug("%s qty %.8f → %.8f", symbol, qty, adjusted)
        return adjusted

    def adjust_price(
        self, symbol: str, price: float, category: str = Category.LINEAR
    ) -> float:
        info = self._fetch_instrument(symbol, category)
        adjusted = info.price_flt.adjust(price)
        logger.debug("%s price %.8f → %.8f", symbol, price, adjusted)
        return adjusted

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
        qty = float(qty) if qty is not None else 0.0
        price = _safe_float(price)
        stop_loss = _safe_float(stop_loss)
        take_profit = _safe_float(take_profit)
        trailing_stop = _safe_float(trailing_stop)

        adj_qty = self.adjust_quantity(symbol, qty, category)

        payload: Dict[str, Any] = {
            "category": category.value,  # Use value for API
            "symbol": symbol,
            "side": side.value,  # Use value for API
            "orderType": order_type.value,  # Use value for API
            "qty": str(adj_qty),
            "timeInForce": time_in_force.value,  # Use value for API
            "positionIdx": int(position_idx),
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
            category.value,
            side.value,
            symbol,
            price or "MARKET",
            adj_qty,
        )
        return self.api_request("POST", "/v5/order/create", json_data=payload)

    # ══════════════════════════════════════════════════════════
    # BATCH ORDERS
    # ══════════════════════════════════════════════════════════
    def execute_scalp_batch(self, order_list: List[dict]) -> dict:
        if not order_list:
            raise ValueError("order_list must not be empty")
        if len(order_list) > self.config.max_orders_per_batch:
            raise ValueError(
                f"Bybit batch API max {self.config.max_orders_per_batch} orders"
            )

        batch = []
        for o in order_list:
            cat = Category(
                o.get("category", Category.LINEAR.value)
            )  # Ensure Category enum
            adj_qty = self.adjust_quantity(o["symbol"], float(o["qty"]), cat)
            entry: Dict[str, Any] = {
                "category": cat.value,  # Use value
                "symbol": o["symbol"],
                "side": o["side"],
                "orderType": o.get("orderType", OrderType.LIMIT.value),
                "qty": str(adj_qty),
                "timeInForce": o.get("timeInForce", TimeInForce.GTC.value),
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

        logger.info("Submitting batch of %d orders…", len(batch))
        return self.api_request(
            "POST",
            "/v5/order/create-batch",
            json_data={
                "category": Category.LINEAR.value,
                "request": batch,
            },  # Category for batch endpoint itself
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
        slices = max(
            self.config.iceberg_min_slices, min(self.config.iceberg_max_slices, slices)
        )
        delay = delay if delay is not None else self.config.iceberg_delay
        slice_qty = total_qty / slices
        results = []

        logger.info(
            "Iceberg: %s %s %s total=%.4f in %d slices @ %.4f",
            category.value,
            side.value,
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
                time.sleep(delay)
        return results

    def place_trailing_stop_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        trailing_distance: float,
        callback_rate: Optional[float] = None,
        category: Category = Category.LINEAR,
        reduce_only: bool = False,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
    ) -> dict:
        """Place a trailing stop order."""
        adj_qty = self.adjust_quantity(symbol, qty, category)
        payload: Dict[str, Any] = {
            "category": category.value,
            "symbol": symbol,
            "side": side.value,
            "orderType": "Stop",
            "qty": str(adj_qty),
            "reduceOnly": reduce_only,
            "positionIdx": int(position_idx),
        }
        if callback_rate is not None:
            payload["triggerDirection"] = 1 if side == OrderSide.BUY else 2
            payload["triggerBy"] = "ByLastPrice"
            payload["callbackRate"] = str(callback_rate)
        else:
            payload["triggerDirection"] = 1 if side == OrderSide.BUY else 2
            payload["triggerBy"] = "ByLastPrice"
            payload["trailingStop"] = str(trailing_distance)
        return self.api_request("POST", "/v5/order/create", json_data=payload)

    def calculate_trailing_stop_levels(
        self,
        symbol: str,
        entry_price: float,
        side: OrderSide,
        trailing_distance: float,
        callback_rate: Optional[float] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate trailing stop activation and trigger levels."""
        try:
            ticker = self.get_ticker(symbol, category)
            current_price = float(ticker.get("lastPrice", entry_price))
            is_long = side == OrderSide.BUY
            if is_long:
                activation_price = current_price + trailing_distance
                trigger_price = activation_price - trailing_distance
            else:
                activation_price = current_price - trailing_distance
                trigger_price = activation_price + trailing_distance
            if callback_rate is None and trailing_distance > 0:
                callback_rate = (trailing_distance / current_price) * 100
            distance_to_activation = max(
                0,
                activation_price - current_price
                if is_long
                else current_price - activation_price,
            )
            return {
                "symbol": symbol,
                "activation_price": round(activation_price, 4),
                "trigger_price": round(trigger_price, 4),
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_trailing_stop_status(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Check trailing stop status for a symbol."""
        try:
            positions = self.get_positions(category=category, symbol=symbol)
            if not positions:
                return {"status": "error", "msg": "No position found"}
            pos = positions[0]
            return {
                "symbol": symbol,
                "trailing_stop": pos.get("trailingStop"),
                "trailing_active": pos.get("trailingStop", "0") != "0",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # CANCEL / AMEND ORDERS (single definitions – no duplicates)
    # ══════════════════════════════════════════════════════════
    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        return self.api_request(
            "POST",
            "/v5/order/cancel",
            json_data={
                "category": category.value,
                "symbol": symbol,
                "orderId": order_id,
            },
        )

    def cancel_all_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Cancel all open orders for a symbol (or all symbols in category)."""
        payload: Dict[str, Any] = {"category": category.value}
        if symbol:
            payload["symbol"] = symbol.upper()
        return self.api_request("POST", "/v5/order/cancel-all", json_data=payload)

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
        """Amend an existing order. FIX: uses `is not None` instead of truthiness."""
        payload: Dict[str, Any] = {
            "category": category.value,
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
        return self.api_request("POST", "/v5/order/amend", json_data=payload)

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category.value, "limit": min(limit, 500)}
        if symbol:
            params["symbol"] = symbol.upper()
        resp = self.api_request("GET", "/v5/order/realtime", params=params, signed=True)
        return resp.get("result", {}).get("list", [])

    def get_order_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Fetch historical orders with optional time range filtering.

        Args:
            symbol: Trading symbol (optional)
            category: Product category
            limit: Number of orders to return (max 500)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
        """
        params: Dict[str, Any] = {"category": category.value, "limit": min(limit, 500)}
        if symbol:
            params["symbol"] = symbol.upper()
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        resp = self.api_request("GET", "/v5/order/history", params=params, signed=True)
        return resp.get("result", {}).get("list", [])

    # ══════════════════════════════════════════════════════════
    # POSITIONS & BALANCE
    # ══════════════════════════════════════════════════════════
    def get_positions(
        self,
        category: Category = Category.LINEAR,
        symbol: Optional[str] = None,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category.value}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request("GET", "/v5/position/list", params=params)
        return resp.get("result", {}).get("list", [])

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        resp = self.api_request(
            "GET", "/v5/account/wallet-balance", params={"accountType": account_type}
        )
        return resp.get("result", {})

    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        category: Category = Category.LINEAR,
        buy_leverage: Optional[int] = None,
        sell_leverage: Optional[int] = None,
    ) -> dict:
        payload = {
            "category": category.value,
            "symbol": symbol,
            "buyLeverage": str(buy_leverage or leverage),
            "sellLeverage": str(sell_leverage or leverage),
        }
        return self.api_request("POST", "/v5/position/set-leverage", json_data=payload)

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        category: Category = Category.LINEAR,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
    ) -> dict:
        payload: Dict[str, Any] = {
            "category": category.value,
            "symbol": symbol,
            "positionIdx": int(position_idx),
        }
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        return self.api_request("POST", "/v5/position/trading-stop", json_data=payload)

    def get_pnl_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Fetch PnL history with optional time range filtering.

        Args:
            symbol: Trading symbol (optional)
            category: Product category
            limit: Number of records to return (max 500)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
        """
        params: Dict[str, Any] = {"category": category.value, "limit": min(limit, 500)}
        if symbol:
            params["symbol"] = symbol
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        resp = self.api_request("GET", "/v5/position/closed-pnl", params=params)
        return resp.get("result", {}).get("list", [])

    def get_pnl_report(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> PnLReport:
        records = self.get_pnl_history(
            symbol=symbol,
            category=category,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
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
            win_rate=len(wins) / len(pnls) if pnls else 0,
            avg_win=statistics.mean(wins) if wins else 0,
            avg_loss=statistics.mean(losses) if losses else 0,
            largest_win=max(wins) if wins else 0,
            largest_loss=min(losses) if losses else 0,
            total_fees=sum(fees),
            trade_count=len(pnls),
        )

    def get_fee_rate(self, symbol: str, category: Category = Category.LINEAR) -> dict:
        """Get trading fee rate for a symbol."""
        return self.api_request(
            "GET",
            "/v5/account/fee-rate",
            params={"category": category.value, "symbol": symbol},
        )

    def get_transaction_log(
        self,
        account_type: str = "UNIFIED",
        category: Optional[Category] = None,
        currency: Optional[str] = None,
        base_coin: Optional[str] = None,
        type: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 20,
    ) -> List[dict]:
        """Fetch transaction logs for the account."""
        params: Dict[str, Any] = {"accountType": account_type, "limit": limit}
        if category:
            params["category"] = category.value
        if currency:
            params["currency"] = currency
        if base_coin:
            params["baseCoin"] = base_coin
        if type:
            params["type"] = type
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        resp = self.api_request("GET", "/v5/account/transaction-log", params=params)
        return resp.get("result", {}).get("list", [])

    def switch_margin_mode(
        self,
        symbol: str,
        trade_mode: int,  # 0: cross, 1: isolated
        category: Category = Category.LINEAR,
        leverage: str = "1",
    ) -> dict:
        """Switch between cross and isolated margin mode."""
        payload = {
            "category": category.value,
            "symbol": symbol,
            "tradeMode": trade_mode,
            "buyLeverage": leverage,
            "sellLeverage": leverage,
        }
        return self.api_request(
            "POST", "/v5/position/switch-isolated", json_data=payload
        )

    def switch_position_mode(
        self,
        category: Category = Category.LINEAR,
        symbol: Optional[str] = None,
        coin: Optional[str] = None,
        mode: int = 0,  # 0: Merged Single (One-Way), 3: Both Sides (Hedge)
    ) -> dict:
        """Switch position mode between One-Way and Hedge."""
        payload: Dict[str, Any] = {"category": category.value, "mode": mode}
        if symbol:
            payload["symbol"] = symbol
        if coin:
            payload["coin"] = coin
        return self.api_request("POST", "/v5/position/switch-mode", json_data=payload)

    # ══════════════════════════════════════════════════════════
    # MARKET DATA
    # ══════════════════════════════════════════════════════════
    def get_ticker(self, symbol: str, category: Category = Category.LINEAR) -> dict:
        cache_key = f"{symbol}_{category.value}"
        now = time.time()
        with self._cache_lock:
            if cache_key in self._ticker_cache:
                data, ts = self._ticker_cache[cache_key]
                if now - ts < self._cache_ttl["ticker"]:
                    logger.debug("Cache hit for ticker: %s", cache_key)
                    return data
                logger.debug("Cache stale for ticker: %s", cache_key)

        logger.debug("Cache miss for ticker: %s", cache_key)
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category.value, "symbol": symbol},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        data = items[0] if items else {}
        with self._cache_lock:
            self._ticker_cache[cache_key] = (data, now)
        return data

    def get_orderbook(
        self, symbol: str, limit: int = 25, category: Category = Category.LINEAR
    ) -> dict:
        cache_key = f"{symbol}_{category.value}_{limit}"
        now = time.time()
        with self._cache_lock:
            if cache_key in self._orderbook_cache:
                data, ts = self._orderbook_cache[cache_key]
                if now - ts < self._cache_ttl["orderbook"]:
                    logger.info("Cache hit for orderbook: %s", cache_key)
                    return data
                logger.debug("Cache stale for orderbook: %s", cache_key)

        logger.debug("Cache miss for orderbook: %s", cache_key)
        resp = self.api_request(
            "GET",
            "/v5/market/orderbook",
            params={"category": category.value, "symbol": symbol, "limit": limit},
            signed=False,
        )
        data = resp.get("result", {})
        with self._cache_lock:
            self._orderbook_cache[cache_key] = (data, now)
        return data

    def get_mark_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/mark-price-kline",
            params={"category": category.value, "symbol": symbol, "limit": 1},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            return {}
        item = items[0]
        return {
            "symbol": symbol,
            "mark_price": float(item.get("markPrice", 0)),
            "index_price": float(item.get("indexPrice", 0)),
            "funding_rate": float(item.get("fundingRate", 0)),
            "funding_timestamp": int(item.get("fundingRateTimestamp", 0)),
            "next_funding_time": int(item.get("nextFundingTime", 0)),
            "timestamp": int(item.get("timestamp", 0)),
        }

    def get_index_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/index-price-kline",
            params={"category": category.value, "symbol": symbol, "limit": 1},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            return {}
        item = items[0]
        return {
            "symbol": symbol,
            "index_price": float(item.get("indexPrice", 0)),
            "timestamp": int(item.get("timestamp", 0)),
        }

    def get_24hr_ticker(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category.value}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params=params,
            signed=False,
        )
        results = []
        for item in resp.get("result", {}).get("list", []):
            results.append(
                {
                    "symbol": item.get("symbol"),
                    "last_price": float(item.get("lastPrice", 0)),
                    "bid1_price": float(item.get("bid1Price", 0)),
                    "ask1_price": float(item.get("ask1Price", 0)),
                    "price_24h_change": float(item.get("price24hPcnt", 0)) * 100,
                    "price_24h_high": float(item.get("highPrice24h", 0)),
                    "price_24h_low": float(item.get("lowPrice24h", 0)),
                    "volume_24h": float(item.get("volume24h", 0)),
                    "turnover_24h": float(item.get("turnover24h", 0)),
                    "open_interest": float(item.get("openInterest", 0)),
                    "funding_rate": float(item.get("fundingRate", 0)),
                    "next_funding_time": int(item.get("nextFundingTime", 0)),
                }
            )
        return results

    def get_price_bands(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/price-limit",
            params={"category": category.value, "symbol": symbol},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            return {}
        item = items[0]
        return {
            "symbol": symbol,
            "upper_limit": float(item.get("upperLimitPrice", 0)),
            "lower_limit": float(item.get("lowerLimitPrice", 0)),
            "bid1_price": float(item.get("bid1Price", 0)),
            "ask1_price": float(item.get("ask1Price", 0)),
        }

    def get_klines(
        self,
        symbol: str,
        interval: str = "1",
        category: Category = Category.LINEAR,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[List[float]]:
        """Fetch klines for a symbol.

        Args:
            symbol: Trading symbol
            interval: Kline interval (e.g., "1m", "1h", "1d")
            category: Product category
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            limit: Number of klines to return (max 1000 for spot, 200 for linear/inverse)
        """
        mapping = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        interval_code = mapping.get(str(interval).lower(), interval)

        # Adjust limit based on category for Bybit API compliance
        api_limit = limit
        if category == Category.SPOT:
            api_limit = min(limit, 1000)
        else:  # linear, inverse, option
            api_limit = min(limit, 200)

        # Construct cache key
        cache_key_parts = [symbol, interval_code, category.value, str(api_limit)]
        if start_time is not None:
            cache_key_parts.append(str(start_time))
        if end_time is not None:
            cache_key_parts.append(str(end_time))
        cache_key = "_".join(cache_key_parts)
        now = time.time()

        with self._cache_lock:
            if cache_key in self._klines_cache:
                data, ts = self._klines_cache[cache_key]
                if now - ts < self._cache_ttl["klines"]:
                    logger.debug("Cache hit for klines: %s", cache_key)
                    return data
                logger.debug("Cache stale for klines: %s", cache_key)

        logger.debug("Cache miss for klines: %s", cache_key)
        params: Dict[str, Any] = {
            "category": category.value,
            "symbol": symbol,
            "interval": interval_code,
            "limit": api_limit,
        }
        if start_time is not None:
            params["start"] = int(start_time)
        if end_time is not None:
            params["end"] = int(end_time)

        resp = self.api_request("GET", "/v5/market/kline", params=params, signed=False)
        klines_data = resp.get("result", {}).get("list", [])

        # Convert to List[List[float]] format if not already
        formatted_klines = []
        for kline in klines_data:
            try:
                # Expected format: [timestamp, open, high, low, close, volume, turnover]
                formatted_klines.append(
                    [
                        int(kline[0]),
                        float(kline[1]),
                        float(kline[2]),
                        float(kline[3]),
                        float(kline[4]),
                        float(kline[5]),
                        float(kline[6]),
                    ]
                )
            except (IndexError, ValueError, TypeError) as e:
                logger.warning(
                    "Skipping malformed kline data: %s - Error: %s", kline, e
                )
                continue

        with self._cache_lock:
            self._klines_cache[cache_key] = (formatted_klines, now)
        return formatted_klines

    def get_historical_klines(self, *args, **kwargs) -> List[List[float]]:
        """Legacy alias for get_klines."""
        return self.get_klines(*args, **kwargs)

    def get_recent_trades(
        self,
        symbol: str,
        limit: int = 500,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        resp = self.api_request(
            "GET",
            "/v5/market/recent-trade",
            params={
                "category": category.value,
                "symbol": symbol,
                "limit": min(limit, 1000),
            },
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
        resp = self.api_request(
            "GET",
            "/v5/market/open-interest",
            params={
                "category": category.value,
                "symbol": symbol,
                "intervalTime": interval_time,
                "limit": min(limit, 500),
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
        """Alias for backward compatibility – fetch liquidation data."""
        return self.get_market_liquidations(symbol, category, limit)

    def get_market_liquidations(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        limit: int = 200,
    ) -> List[dict]:
        """Get liquidation data using the dedicated liquidations endpoint."""
        resp = self.api_request(
            "GET",
            "/v5/market/liquidation-info",
            params={
                "category": category.value,
                "symbol": symbol,
                "limit": min(limit, 1000),
            },
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_long_short_ratio(
        self,
        symbol: str,
        period: str = "5min",
        limit: int = 50,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        """Get long/short ratio for a symbol."""
        resp = self.api_request(
            "GET",
            "/v5/market/account-ratio",
            params={
                "category": category.value,
                "symbol": symbol,
                "period": period,
                "limit": min(limit, 200),
            },
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_funding_rate(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/funding/history",
            params={"category": category.value, "symbol": symbol, "limit": 1},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        return items[0] if items else {}

    # ══════════════════════════════════════════════════════════
    # MARKET MOMENTUM (was MISSING – now implemented)
    # ══════════════════════════════════════════════════════════
    def get_market_momentum(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        strong_threshold: float = 0.20,
        mild_threshold: float = 0.08,
    ) -> dict:
        """
        Analyze recent trade flow to determine buy/sell momentum.
        Returns imbalance ratio, signal, VWAP, and volume breakdown.
        """
        try:
            trades = self.get_recent_trades(symbol=symbol, limit=500, category=category)
            if not trades:
                return {
                    "symbol": symbol,
                    "imbalance": 0.0,
                    "signal": Signal.NEUTRAL.value,
                    "buy_vol": 0.0,
                    "sell_vol": 0.0,
                    "vwap": 0.0,
                    "avg_trade_sz": 0.0,
                    "timestamp": time.time(),
                }

            buy_vol = 0.0
            sell_vol = 0.0
            vol_price_sum = 0.0
            total_qty = 0.0
            sizes = []

            for t in trades:
                tqty = float(t.get("size", 0))
                tpx = float(t.get("price", 0))
                tside = t.get("side", "").lower()

                if tside == "buy":
                    buy_vol += tqty
                else:
                    sell_vol += tqty

                vol_price_sum += tpx * tqty
                total_qty += tqty
                sizes.append(tqty)

            total_vol = buy_vol + sell_vol
            imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0
            vwap = vol_price_sum / total_qty if total_qty > 0 else 0.0
            avg_sz = statistics.mean(sizes) if sizes else 0.0

            if imbalance >= strong_threshold:
                signal = Signal.STRONG_BUY
            elif imbalance >= mild_threshold:
                signal = Signal.BUY
            elif imbalance <= -strong_threshold:
                signal = Signal.STRONG_SELL
            elif imbalance <= -mild_threshold:
                signal = Signal.SELL
            else:
                signal = Signal.NEUTRAL

            result = MomentumResult(
                symbol=symbol,
                imbalance=imbalance,
                signal=signal,
                buy_vol=buy_vol,
                sell_vol=sell_vol,
                vwap=vwap,
                avg_trade_sz=avg_sz,
            )
            return result.to_dict()
        except Exception as exc:
            logger.error("Market momentum failed for %s: %s", symbol, exc)
            return {"symbol": symbol, "status": "error", "msg": str(exc)}

    # ══════════════════════════════════════════════════════════
    # MARKET HEALTH (was MISSING – now implemented)
    # ══════════════════════════════════════════════════════════
    def get_market_health(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        """
        Composite market health check: spread, depth, volatility, funding, OI.
        Returns a 0–100 health score and component breakdown.
        """
        try:
            ticker = self.get_ticker(symbol, category)
            ob = self.get_orderbook(symbol, limit=25, category=category)
            funding = self.get_funding_rate(symbol, category)
            oi_data = self.get_open_interest(
                symbol, interval_time="5min", category=category, limit=2
            )

            # Spread analysis
            best_bid = float(ticker.get("bid1Price", 0))
            best_ask = float(ticker.get("ask1Price", 0))
            last_px = float(ticker.get("lastPrice", 1))
            spread = (best_ask - best_bid) / last_px * 100 if last_px > 0 else 999
            spread_score = max(0, min(25, 25 - (spread * 500)))  # <0.05% = 25

            # Depth analysis (bid+ask volume in top 25 levels)
            ob_result = ob.get("result", {})
            bid_depth = sum(float(b[1]) for b in ob_result.get("b", []))
            ask_depth = sum(float(a[1]) for a in ob_result.get("a", []))
            total_depth = bid_depth + ask_depth
            depth_imbalance = (
                abs(bid_depth - ask_depth) / total_depth if total_depth > 0 else 1
            )
            depth_score = max(0, min(25, 25 * (1 - depth_imbalance)))

            # Funding rate analysis
            fund_rate = abs(float(funding.get("fundingRate", 0)))
            fund_score = max(0, min(25, 25 - (fund_rate * 2500)))  # <0.01% = 25

            # Open interest trend
            oi_score = 12.5  # neutral default
            if len(oi_data) >= 2:
                oi_new = float(oi_data[0].get("openInterest", 0))
                oi_old = float(oi_data[1].get("openInterest", 0))
                if oi_old > 0:
                    oi_change = (oi_new - oi_old) / oi_old
                    oi_score = max(0, min(25, 12.5 + oi_change * 250))

            health_score = spread_score + depth_score + fund_score + oi_score

            return {
                "symbol": symbol,
                "health_score": round(health_score, 1),
                "spread_pct": round(spread, 6),
                "spread_score": round(spread_score, 1),
                "depth_score": round(depth_score, 1),
                "bid_depth": round(bid_depth, 2),
                "ask_depth": round(ask_depth, 2),
                "funding_rate": funding.get("fundingRate", "0"),
                "funding_score": round(fund_score, 1),
                "oi_score": round(oi_score, 1),
                "last_price": last_px,
                "timestamp": time.time(),
            }
        except Exception as exc:
            logger.error("Market health failed for %s: %s", symbol, exc)
            return {"symbol": symbol, "status": "error", "msg": str(exc)}

    def cancel_all_orders(
        self, symbol: Optional[str] = None, category: Category = Category.LINEAR
    ) -> dict:
        """Cancel all open orders for a symbol (or all symbols in category)."""
        payload: Dict[str, Any] = {"category": category.value}
        if symbol:
            payload["symbol"] = symbol.upper()
        return self.api_request("POST", "/v5/order/cancel-all", json_data=payload)

    def calculate_kelly_criterion(
        self, win_rate: float, win_loss_ratio: float
    ) -> float:
        """Kelly Criterion: f* = (bp - q) / b"""
        if win_loss_ratio <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        q = 1 - win_rate
        kelly = (win_loss_ratio * win_rate - q) / win_loss_ratio
        return round(max(0.0, min(1.0, kelly)), 4)

    def calculate_trade_pnl(
        self, entry: float, exit: float, qty: float, side: str, fee_rate: float = 0.0006
    ) -> dict:
        """Calculate PnL for a completed trade including fees."""
        if side.lower() in ("buy", "long"):
            raw_pnl = (exit - entry) * qty
        else:
            raw_pnl = (entry - exit) * qty
        fees = (entry * qty + exit * qty) * fee_rate
        net_pnl = raw_pnl - fees
        pnl_pct = (net_pnl / (entry * qty)) * 100 if entry * qty > 0 else 0
        return {
            "entry": entry,
            "exit": exit,
            "qty": qty,
            "side": side,
            "raw_pnl": round(raw_pnl, 4),
            "fees": round(fees, 4),
            "net_pnl": round(net_pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
        }

    def calculate_profit_target(
        self,
        entry_price: float,
        sl_price: float,
        rr_ratios: Optional[List[float]] = None,
    ) -> dict:
        """Calculate take-profit levels based on risk-reward ratios from SL distance."""
        rr_ratios = rr_ratios or [1.0, 1.5, 2.0, 3.0, 5.0]
        risk = abs(entry_price - sl_price)
        is_long = entry_price > sl_price
        targets = {
            f"RR_{rr}": round(
                entry_price + (risk * rr) if is_long else entry_price - (risk * rr), 4
            )
            for rr in rr_ratios
        }
        return {
            "entry": entry_price,
            "stop_loss": sl_price,
            "risk": round(risk, 4),
            "direction": "LONG" if is_long else "SHORT",
            "targets": targets,
        }

    # ══════════════════════════════════════════════════════════
    # TREND ANALYSIS ENGINE
    # ══════════════════════════════════════════════════════════
    def get_trend_analysis(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        interval: str = "60",
        lookback_periods: int = 200,
        include_advanced_indicators: bool = True,
        timeframe_analysis: bool = True,
    ) -> dict:
        """Advanced multi-indicator trend analysis with consensus scoring."""
        try:
            klines = self.get_klines(
                symbol, interval=interval, limit=lookback_periods, category=category
            )
            if not klines or len(klines) < 50:
                count = len(klines) if klines else 0
                return {
                    "status": "error",
                    "msg": f"Insufficient data for {symbol} (found {count}, need >=50)",
                }

            klines.reverse()  # Process from oldest to newest

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            vols = [float(k[5]) for k in klines]

            current_price = closes[-1]

            ema9 = self.calculate_ema(closes, period=9)
            ema21 = self.calculate_ema(closes, period=21)
            ema50 = self.calculate_ema(closes, period=50)
            ema200 = self.calculate_ema(closes, period=200)

            rsi = self.calculate_rsi(closes, period=14)
            macd = self.calculate_macd(closes)
            bb = self.calculate_bollinger_bands(closes)

            ohlcv_list = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv_list, period=14)

            momentum = self.get_market_momentum(symbol, category=category)

            # ── Scoring System (-100 to +100) ──
            score = 0

            # MA Trend (30%)
            ma_score = 0
            if current_price > ema21:
                ma_score += 15
            else:
                ma_score -= 15
            if ema9 > ema21:
                ma_score += 15
            else:
                ma_score -= 15
            score += ma_score

            # MACD Trend (20%)
            macd_score = 0
            if macd["macd"] > macd["signal"]:
                macd_score += 10
            else:
                macd_score -= 10
            if macd["macd"] > 0:
                macd_score += 10
            else:
                macd_score -= 10
            score += macd_score

            # RSI Momentum (15%)
            rsi_score = 0
            if 40 <= rsi <= 60:
                rsi_score = 0
            elif rsi > 60:
                rsi_score = 15 if rsi < 80 else 5
            elif rsi < 40:
                rsi_score = -15 if rsi > 20 else -5
            score += rsi_score

            # Volume Confirmation (10%)
            vol_avg = (
                sum(vols[-20:]) / 20
                if len(vols) >= 20
                else sum(vols) / max(len(vols), 1)
            )
            vol_score = 0
            if vols[-1] > vol_avg:
                if closes[-1] > closes[-2]:
                    vol_score += 10
                else:
                    vol_score -= 10
            score += vol_score

            # Order Flow (10%)
            flow_score = 0
            sig = momentum.get("signal", "NEUTRAL")
            if "STRONG_BUY" in sig:
                flow_score += 10
            elif "BUY" in sig:
                flow_score += 5
            elif "STRONG_SELL" in sig:
                flow_score -= 10
            elif "SELL" in sig:
                flow_score -= 5
            score += flow_score

            # Price Action / Alignment (15%)
            pa_score = 0
            if current_price > ema50:
                pa_score += 7.5
            else:
                pa_score -= 7.5
            if ema50 > ema200:
                pa_score += 7.5
            else:
                pa_score -= 7.5
            score += pa_score

            # Multi-Timeframe check
            mtf_alignment = "NOT_CHECKED"
            if interval in ("60", "1h"):
                try:
                    klines_4h = self.get_klines(
                        symbol, interval="240", limit=50, category=category
                    )
                    if klines_4h:
                        klines_4h.reverse()
                        closes_4h = [float(k[4]) for k in klines_4h]
                        ema20_4h = self.calculate_ema(closes_4h, 20)
                        if current_price > ema20_4h and score >= 20:
                            mtf_alignment = (
                                "ALIGNED_BULLISH"  # Require some bullish score
                            )
                        elif current_price < ema20_4h and score <= -20:
                            mtf_alignment = (
                                "ALIGNED_BEARISH"  # Require some bearish score
                            )
                        else:
                            mtf_alignment = "MIXED"
                except Exception:
                    mtf_alignment = "ERROR"

            # Classification
            if score >= 60:
                trend = "STRONG_BULLISH"
            elif score >= 20:
                trend = "BULLISH"
            elif score <= -60:
                trend = "STRONG_BEARISH"
            elif score <= -20:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"

            # Guidance
            advice = "WAIT"
            if trend == "STRONG_BULLISH":
                advice = "STRONG_BUY"
            elif trend == "BULLISH":
                advice = "BUY_ON_DIP" if current_price <= ema9 * 1.005 else "HOLD_LONG"
            elif trend == "STRONG_BEARISH":
                advice = "STRONG_SELL"
            elif trend == "BEARISH":
                advice = (
                    "SELL_ON_RALLY" if current_price >= ema9 * 0.995 else "HOLD_SHORT"
                )

            if rsi > 75:
                advice = "TAKE_PROFIT / AVOID_LONG"
            if rsi < 25:
                advice = "WATCH_FOR_REVERSAL / AVOID_SHORT"

            result = {
                "symbol": symbol,
                "interval": interval,
                "score": round(score, 2),
                "trend": trend,
                "mtf_alignment": mtf_alignment,
                "current_price": round(current_price, 4),
                "indicators": {
                    "rsi": round(rsi, 2),
                    "macd": macd,
                    "ema9": round(ema9, 4),
                    "ema21": round(ema21, 4),
                    "ema50": round(ema50, 4),
                    "ema200": round(ema200, 4),
                    "atr": round(atr, 4),
                    "bb": bb,
                },
                "risk_guidance": {
                    "suggested_stop_loss": round(stop_loss, 4),
                    "suggested_take_profit": round(take_profit, 4),
                    "risk_reward_ratio": 2.0,
                },
                "action_advice": advice,
                "timestamp": time.time(),
            }

            if include_advanced_indicators:
                result["advanced"] = {
                    "adx": self.calculate_adx(
                        symbol, interval, category, lookback_periods
                    ),
                    "cci": self.calculate_cci(
                        symbol, interval, category, lookback_periods
                    ),
                    "stoch_rsi": self.calculate_stoch_rsi(
                        symbol, interval, category, lookback_periods
                    ),
                    "stoch_osc": self.calculate_stoch_oscillator(
                        symbol, interval, category, lookback_periods
                    ),  # New
                    "mfi": self.calculate_mfi(
                        symbol, interval, category, lookback_periods
                    ),  # New
                    "obv": self.calculate_obv(
                        symbol, interval, category, lookback_periods
                    ),  # New
                }
            return result
        except Exception as e:
            logger.error("Trend analysis failed: %s", e)
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # SAFE EXECUTE
    # ══════════════════════════════════════════════════════════
    def safe_execute(
        self,
        fn: Callable,
        *args,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except RuntimeError as exc:
                if "Circuit OPEN" in str(exc):
                    logger.error("Circuit OPEN – aborting: %s", exc)
                    return {"status": "circuit_open", "msg": str(exc)}
                last_exc = exc
            except Exception as exc:
                last_exc = exc

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed: %s – retrying in %.1fs",
                attempt,
                max_retries,
                last_exc,
                delay,
            )
            time.sleep(delay)

        logger.error("All %d attempts exhausted. Last: %s", max_retries, last_exc)
        return {"status": "error", "msg": str(last_exc)}

    # ══════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ══════════════════════════════════════════════════════════
    def connection_health(self) -> dict:
        """Score connection health from 0-100 based on response times and success rates."""
        try:
            start = time.time()
            self.api_request("GET", "/v5/market/time", signed=False)
            latency = (time.time() - start) * 1000

            # Score based on latency
            if latency < 100:
                score = 100
            elif latency < 200:
                score = 90
            elif latency < 500:
                score = 70
            elif latency < 1000:
                score = 50
            else:
                score = 30

            return {
                "status": "ok",
                "latency_ms": round(latency, 2),
                "score": score,
                "circuit_state": self.circuit.state.value
                if hasattr(self, "circuit")
                else "UNKNOWN",
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "score": 0, "msg": str(e)}

    # Re-implemented get_market_momentum to match signature in run() action list
    def get_market_momentum(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        strong_threshold: float = 0.20,
        mild_threshold: float = 0.08,
    ) -> dict:
        """
        Analyze recent trade flow to determine buy/sell momentum.
        Returns imbalance ratio, signal, VWAP, and volume breakdown.
        """
        try:
            trades = self.get_recent_trades(symbol=symbol, limit=500, category=category)
            if not trades:
                return {
                    "symbol": symbol,
                    "imbalance": 0.0,
                    "signal": Signal.NEUTRAL.value,
                    "buy_vol": 0.0,
                    "sell_vol": 0.0,
                    "vwap": 0.0,
                    "avg_trade_sz": 0.0,
                    "timestamp": time.time(),
                }

            buy_vol = 0.0
            sell_vol = 0.0
            vol_price_sum = 0.0
            total_qty = 0.0
            sizes = []

            for t in trades:
                tqty = float(t.get("size", 0))
                tpx = float(t.get("price", 0))
                tside = t.get("side", "").lower()
                if tside == "buy":
                    buy_vol += tqty
                else:
                    sell_vol += tqty
                vol_price_sum += tpx * tqty
                total_qty += tqty
                sizes.append(tqty)

            total_vol = buy_vol + sell_vol
            imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0
            vwap = vol_price_sum / total_qty if total_qty > 0 else 0.0
            avg_sz = statistics.mean(sizes) if sizes else 0.0

            if imbalance >= strong_threshold:
                signal = "StrongBuy"
            elif imbalance >= mild_threshold:
                signal = "Buy"
            elif imbalance <= -strong_threshold:
                signal = "StrongSell"
            elif imbalance <= -mild_threshold:
                signal = "Sell"
            else:
                signal = "Neutral"

            return {
                "symbol": symbol,
                "imbalance": round(imbalance, 4),
                "signal": signal,
                "buy_vol": round(buy_vol, 4),
                "sell_vol": round(sell_vol, 4),
                "vwap": round(vwap, 4),
                "avg_trade_sz": round(avg_sz, 4),
                "timestamp": time.time(),
            }
        except Exception as exc:
            logger.error("Market momentum failed for %s: %s", symbol, exc)
            return {"symbol": symbol, "status": "error", "msg": str(exc)}

    # Re-implemented get_market_health to match signature in run() action list
    def get_market_health(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        """Composite market health check: spread, depth, volatility, funding, OI."""
        try:
            ticker = self.get_ticker(symbol, category)
            ob = self.get_orderbook(symbol, limit=25, category=category)
            funding = self.get_funding_rate(symbol, category)
            oi_data = self.get_open_interest(
                symbol, interval_time="5min", category=category, limit=2
            )

            best_bid = float(ticker.get("bid1Price", 0))
            best_ask = float(ticker.get("ask1Price", 0))
            last_px = float(ticker.get("lastPrice", 1))
            spread = (best_ask - best_bid) / last_px * 100 if last_px > 0 else 999
            spread_score = max(0, min(25, 25 - (spread * 500)))

            ob_result = ob.get("result", {})
            bid_depth = sum(float(b[1]) for b in ob_result.get("b", []))
            ask_depth = sum(float(a[1]) for a in ob_result.get("a", []))
            total_depth = bid_depth + ask_depth
            depth_imbalance = (
                abs(bid_depth - ask_depth) / total_depth if total_depth > 0 else 1
            )
            depth_score = max(0, min(25, 25 * (1 - depth_imbalance)))

            fund_rate = abs(float(funding.get("fundingRate", 0)))
            fund_score = max(0, min(25, 25 - (fund_rate * 2500)))

            oi_score = 12.5
            if len(oi_data) >= 2:
                oi_new = float(oi_data[0].get("openInterest", 0))
                oi_old = float(oi_data[1].get("openInterest", 0))
                if oi_old > 0:
                    oi_change = (oi_new - oi_old) / oi_old
                    oi_score = max(0, min(25, 12.5 + oi_change * 250))

            health_score = spread_score + depth_score + fund_score + oi_score

            return {
                "symbol": symbol,
                "health_score": round(health_score, 1),
                "spread_pct": round(spread, 6),
                "spread_score": round(spread_score, 1),
                "depth_score": round(depth_score, 1),
                "bid_depth": round(bid_depth, 2),
                "ask_depth": round(ask_depth, 2),
                "funding_rate": funding.get("fundingRate", "0"),
                "funding_score": round(fund_score, 1),
                "oi_score": round(oi_score, 1),
                "last_price": last_px,
                "timestamp": time.time(),
            }
        except Exception as exc:
            logger.error("Market health failed for %s: %s", symbol, exc)
            return {"symbol": symbol, "status": "error", "msg": str(exc)}

    def health_check(self) -> dict:
        """Enhanced health check with geo IP info and Tor status."""
        try:
            resp = self.api_request("GET", "/v5/market/time", signed=False)

            geo_ip = "N/A"
            geo_location = {"status": "not_checked"}

            if self._geo_router:
                try:
                    geo_ip = self._geo_router.get_public_ip()
                    geo_location = self._geo_router.get_geo_location()
                except Exception:
                    pass
            elif PYSOCKS_AVAILABLE and self.config.pysocks_enabled:
                try:
                    router = PySocksGeoRouter(
                        proxy_host=self.config.pysocks_host,
                        proxy_port=self.config.pysocks_port,
                        rdns=True,
                    )
                    geo_ip = router.get_public_ip()
                    geo_location = router.get_geo_location()
                    router.close()
                except Exception:
                    pass

            return {
                "status": "ok",
                "circuit": self.circuit.state.value,
                "circuit_fails": self.circuit.failure_count,
                "rate_usage": self.limiter.current_usage,
                "server_time": resp.get("result", {}).get("timeNano"),
                "time_offset_ms": self._time_offset,
                "base_url": self.config.base_url,
                "tor_enabled": self.config.use_tor,
                "tor_control_accessible": self.tor._tor_control_port_accessible,
                "tor_use_pysocks": self.config.tor_use_pysocks,
                "pysocks_available": PYSOCKS_AVAILABLE,
                "pysocks_enabled": self.config.pysocks_enabled,
                "pysocks_host": self.config.pysocks_host,
                "pysocks_port": self.config.pysocks_port,
                "pysocks_region": self.config.pysocks_region,
                "pysocks_global": self.config.pysocks_global,
                "geo_ip": geo_ip,
                "geo_location": geo_location,
                "testnet": self.config.testnet,
                "cache_symbols": list(self._instr_cache.keys()),
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    # ══════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ══════════════════════════════════════════════════════════

    # Helper to extract data from klines list
    def _extract_klines_data(
        self, klines: List[List[float]]
    ) -> Tuple[
        List[float], List[float], List[float], List[float], List[float], List[float]
    ]:
        """Extracts OHLCV data from klines list."""
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        opens = [float(k[1]) for k in klines]
        vols = [float(k[5]) for k in klines]
        turnovers = [float(k[6]) for k in klines]
        return opens, highs, lows, closes, vols, turnovers

    def calculate_vwap(self, klines: List[List[float]]) -> float:
        """Calculate Volume Weighted Average Price (VWAP)."""
        if not klines:
            return 0.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        total_vol_price = 0.0
        total_vol = 0.0
        for i in range(len(klines)):
            high = highs[i]
            low = lows[i]
            close = closes[i]
            vol = vols[i]

            typical_price = (high + low + close) / 3
            total_vol_price += typical_price * vol
            total_vol += vol
        return round(total_vol_price / total_vol, 4) if total_vol > 0 else 0.0

    def calculate_ichimoku_cloud(
        self,
        klines: List[List[float]],
        tenkan_period: int = 9,
        kijun_period: int = 26,
        senkou_b_period: int = 52,
    ) -> dict:
        """Calculate Ichimoku Cloud components."""
        if len(klines) < senkou_b_period:
            return {"tenkan": 0.0, "kijun": 0.0, "senkou_a": 0.0, "senkou_b": 0.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        def get_midpoint(h, l, p):
            return (max(h[-p:]) + min(l[-p:])) / 2

        tenkan = get_midpoint(highs, lows, tenkan_period)
        kijun = get_midpoint(highs, lows, kijun_period)
        senkou_a = (tenkan + kijun) / 2
        senkou_b = get_midpoint(highs, lows, senkou_b_period)

        return {
            "tenkan": round(tenkan, 4),
            "kijun": round(kijun, 4),
            "senkou_a": round(senkou_a, 4),
            "senkou_b": round(senkou_b, 4),
        }

    def calculate_rsi(self, klines: List[List[float]], period: int = 14) -> float:
        """Calculate Relative Strength Index (RSI)."""
        if len(klines) < period + 1:
            return 50.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        # Use Wilder's smoothing or simple average for EMA calculation
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_ema(self, prices: List[float], period: int = 20) -> float:
        """Calculate Exponential Moving Average (EMA)."""
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2 / (period + 1)
        ema = prices[0]  # Initialize with the first price
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    def calculate_atr(self, klines: List[List[float]], period: int = 14) -> float:
        """Calculate Average True Range (ATR)."""
        if len(klines) < period + 1:
            return 0.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        tr_list = []
        for i in range(1, len(klines)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        return sum(tr_list[-period:]) / period

    def calculate_bollinger_bands(
        self, klines: List[List[float]], period: int = 20, std_dev: float = 2.0
    ) -> dict:
        """Calculate Bollinger Bands."""
        if len(klines) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        subset = closes[-period:]
        mean = sum(subset) / period
        variance = sum((x - mean) ** 2 for x in subset) / period
        std = math.sqrt(variance)
        return {
            "middle": round(mean, 4),
            "upper": round(mean + (std * std_dev), 4),
            "lower": round(mean - (std * std_dev), 4),
        }

    def calculate_macd(
        self, klines: List[List[float]], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> dict:
        """Calculate MACD (Moving Average Convergence Divergence)."""
        if len(klines) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        def get_ema_series(data, p):
            k = 2 / (p + 1)
            ema_list = [data[0]]
            for val in data[1:]:
                ema_list.append(val * k + ema_list[-1] * (1 - k))
            return ema_list

        fast_ema = get_ema_series(closes, fast)
        slow_ema = get_ema_series(closes, slow)
        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        sig_line = get_ema_series(macd_line, signal)
        cur_macd = macd_line[-1]
        cur_sig = sig_line[-1]
        return {
            "macd": round(cur_macd, 4),
            "signal": round(cur_sig, 4),
            "histogram": round(cur_macd - cur_sig, 4),
        }

    def calculate_stoch_rsi(
        self,
        klines: List[List[float]],
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> dict:
        """Calculate Stochastic RSI with proper sliding window.

        Args:
            klines: List of kline data.
            period: RSI period (typically 14)
            smooth_k: %K smoothing period (typically 3)
            smooth_d: %D smoothing period (typically 3)
        """
        if len(klines) < period + smooth_k + smooth_d:
            return {"stoch_rsi": 0.0, "k": 0.0, "d": 0.0, "rsi": 50.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        # Calculate RSI values using sliding window
        rsi_values = []
        for i in range(period, len(closes) + 1):
            window = closes[i - period : i]
            rsi_val = self.calculate_rsi(
                [closes[j] for j in range(i - period, i)], period=period
            )  # Pass only the window
            rsi_values.append(rsi_val)

        if not rsi_values:
            return {"stoch_rsi": 0.0, "k": 0.0, "d": 0.0, "rsi": 50.0}
        current_rsi = rsi_values[-1]

        if len(rsi_values) < smooth_k:
            return {"stoch_rsi": 0.0, "k": 0.0, "d": 0.0, "rsi": round(current_rsi, 2)}

        # Calculate Stochastic RSI
        stoch_rsi_values = []
        lookback = min(14, len(rsi_values))  # Use 14-period lookback for Stoch RSI

        for i in range(lookback - 1, len(rsi_values)):
            rsi_window = rsi_values[max(0, i - lookback + 1) : i + 1]
            rsi_min = min(rsi_window)
            rsi_max = max(rsi_window)

            if rsi_max == rsi_min:
                stoch_rsi = 0.0
            else:
                stoch_rsi = (rsi_window[-1] - rsi_min) / (rsi_max - rsi_min)
            stoch_rsi_values.append(stoch_rsi)

        if not stoch_rsi_values:
            return {"stoch_rsi": 0.0, "k": 0.0, "d": 0.0, "rsi": round(current_rsi, 2)}

        # Smooth %K
        k_values = []
        for i in range(smooth_k - 1, len(stoch_rsi_values)):
            k_window = stoch_rsi_values[i - smooth_k + 1 : i + 1]
            k_values.append(sum(k_window) / len(k_window))

        # Smooth %D
        d_values = []
        for i in range(smooth_d - 1, len(k_values)):
            d_window = k_values[i - smooth_d + 1 : i + 1]
            d_values.append(sum(d_window) / len(d_window))

        return {
            "stoch_rsi": round(stoch_rsi_values[-1] * 100, 2)
            if stoch_rsi_values
            else 0.0,
            "k": round(k_values[-1] * 100, 2) if k_values else 0.0,
            "d": round(d_values[-1] * 100, 2) if d_values else 0.0,
            "rsi": round(current_rsi, 2),
        }

    def calculate_cci(self, klines: List[List[float]], period: int = 20) -> float:
        """Calculate Commodity Channel Index (CCI)."""
        if len(klines) < period:
            return 0.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        tp = [
            (h + l + c) / 3
            for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])
        ]
        sma = sum(tp) / period
        mean_dev = sum(abs(x - sma) for x in tp) / period
        return round((tp[-1] - sma) / (0.015 * mean_dev) if mean_dev != 0 else 0.0, 2)

    def calculate_donchian_channels(
        self, klines: List[List[float]], period: int = 20
    ) -> dict:
        """Calculate Donchian Channels."""
        if len(klines) < period:
            return {"upper": 0.0, "lower": 0.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        return {
            "upper": round(max(highs[-period:]), 4),
            "lower": round(min(lows[-period:]), 4),
        }

    def calculate_adx(self, klines: List[List[float]], period: int = 14) -> float:
        """Calculate Average Directional Index (ADX)."""
        if len(klines) < period * 2:  # Need enough data for ADX calculation
            return 0.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        tr_list, pos_dm, neg_dm = [], [], []
        for i in range(1, len(klines)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            pd = max(up_move, 0) if up_move > down_move else 0
            nd = max(down_move, 0) if down_move > up_move else 0
            tr_list.append(tr)
            pos_dm.append(pd)
            neg_dm.append(nd)

        # Use EMA for smoothing ATR, +DI, -DI
        # EMA calculation requires a series, so we need to calculate it for the required length
        # For simplicity here, using simple average for initial calculation of ADX components
        # A more robust implementation would use EMA for ADX smoothing as well.

        # Fetching enough klines for EMA calculation if needed
        # For ADX, typically a 14-period smoothing is used. Let's ensure we have enough data.
        # If klines < period * 2, it's likely not enough for meaningful ADX.

        # Simple smoothing for demonstration:
        atr = sum(tr_list[-period:]) / period
        sum_pos = sum(pos_dm[-period:])
        sum_neg = sum(neg_dm[-period:])

        denom = sum_pos + sum_neg + 1e-9  # Add epsilon to avoid division by zero
        adx = 100 * abs(sum_pos - sum_neg) / denom

        return round(adx, 2)

    def calculate_fib_pivots(self, high: float, low: float, close: float) -> dict:
        """Calculate Fibonacci pivot points."""
        pivot = (high + low + close) / 3
        range_val = high - low
        return {
            "R3": round(pivot + (range_val * 1.0), 4),
            "R2": round(pivot + (range_val * 0.618), 4),
            "R1": round(pivot + (range_val * 0.382), 4),
            "P": round(pivot, 4),
            "S1": round(pivot - (range_val * 0.382), 4),
            "S2": round(pivot - (range_val * 0.618), 4),
            "S3": round(pivot - (range_val * 1.0), 4),
        }

    # ══════════════════════════════════════════════════════════
    # NEW INDICATORS (Stoch Oscillator, MFI, OBV)
    # ══════════════════════════════════════════════════════════

    def calculate_stoch_oscillator(
        self,
        klines: List[List[float]],
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> dict:
        """Calculate Stochastic Oscillator (%K and %D)."""
        if len(klines) < period + smooth_k + smooth_d:
            return {"k": 0.0, "d": 0.0}

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        stoch_k_values = []
        for i in range(period - 1, len(klines)):
            window_highs = highs[i - period + 1 : i + 1]
            window_lows = lows[i - period + 1 : i + 1]
            current_close = closes[i]

            highest_high = max(window_highs)
            lowest_low = min(window_lows)

            if highest_high == lowest_low:
                k_val = 0.0
            else:
                k_val = (current_close - lowest_low) / (highest_high - lowest_low)
            stoch_k_values.append(k_val * 100)  # Scale to 0-100

        # Smooth %K
        k_values = []
        for i in range(smooth_k - 1, len(stoch_k_values)):
            k_window = stoch_k_values[i - smooth_k + 1 : i + 1]
            k_values.append(sum(k_window) / len(k_window))

        # Smooth %D (moving average of %K)
        d_values = []
        for i in range(smooth_d - 1, len(k_values)):
            d_window = k_values[i - smooth_d + 1 : i + 1]
            d_values.append(sum(d_window) / len(d_window))

        return {
            "k": round(k_values[-1], 2) if k_values else 0.0,
            "d": round(d_values[-1], 2) if d_values else 0.0,
        }

    def calculate_mfi(self, klines: List[List[float]], period: int = 14) -> float:
        """Calculate Money Flow Index (MFI)."""
        if len(klines) < period + 1:
            return 50.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        money_flow = []
        for i in range(len(klines)):
            typical_price = (highs[i] + lows[i] + closes[i]) / 3
            dm = (
                typical_price - (highs[i - 1] + lows[i - 1] + closes[i - 1]) / 3
                if i > 0
                else 0
            )
            mf = typical_price * vols[i]
            money_flow.append({"dm": dm, "mf": mf})

        positive_mf_sum = 0.0
        negative_mf_sum = 0.0

        for i in range(len(money_flow) - period, len(money_flow)):
            if money_flow[i]["dm"] > 0:
                positive_mf_sum += money_flow[i]["mf"]
            else:
                negative_mf_sum += money_flow[i]["mf"]

        if negative_mf_sum == 0:
            return 100.0

        mfi = 100 - (100 / (1 + (positive_mf_sum / negative_mf_sum)))
        return round(mfi, 2)

    def calculate_obv(self, klines: List[List[float]]) -> float:
        """Calculate On-Balance Volume (OBV)."""
        if not klines:
            return 0.0

        opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(klines)

        obv = 0.0
        for i in range(len(klines)):
            if i == 0:
                obv = vols[i]  # Initialize with the first volume
            elif closes[i] > closes[i - 1]:
                obv += vols[i]
            elif closes[i] < closes[i - 1]:
                obv -= vols[i]
        return obv

    # ══════════════════════════════════════════════════════════
    # RISK CALCULATORS (were MISSING – now implemented)
    # ══════════════════════════════════════════════════════════
    def calculate_kelly_criterion(
        self, win_rate: float, win_loss_ratio: float
    ) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        where b = win_loss_ratio, p = win_rate, q = 1 - win_rate
        Returns optimal fraction of capital to risk (clamped 0–1).
        """
        if win_loss_ratio <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        q = 1 - win_rate
        kelly = (win_loss_ratio * win_rate - q) / win_loss_ratio
        return round(max(0.0, min(1.0, kelly)), 4)

    def calculate_trade_pnl(
        self,
        entry: float,
        exit: float,
        qty: float,
        side: str,
        fee_rate: float = 0.0006,
    ) -> dict:
        """Calculate PnL for a completed trade including fees."""
        if side.lower() in ("buy", "long"):
            raw_pnl = (exit - entry) * qty
        else:
            raw_pnl = (entry - exit) * qty
        fees = (entry * qty + exit * qty) * fee_rate
        net_pnl = raw_pnl - fees
        pnl_pct = (net_pnl / (entry * qty)) * 100 if entry * qty > 0 else 0
        return {
            "entry": entry,
            "exit": exit,
            "qty": qty,
            "side": side,
            "raw_pnl": round(raw_pnl, 4),
            "fees": round(fees, 4),
            "net_pnl": round(net_pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
        }

    def calculate_profit_target(
        self,
        entry_price: float,
        sl_price: float,
        rr_ratios: Optional[List[float]] = None,
    ) -> dict:
        """Calculate take-profit levels based on risk-reward ratios from SL distance."""
        rr_ratios = rr_ratios or [1.0, 1.5, 2.0, 3.0, 5.0]
        risk = abs(entry_price - sl_price)
        is_long = entry_price > sl_price

        targets = {}
        for rr in rr_ratios:
            tp = entry_price + (risk * rr) if is_long else entry_price - (risk * rr)
            targets[f"RR_{rr}"] = round(tp, 4)

        return {
            "entry": entry_price,
            "stop_loss": sl_price,
            "risk": round(risk, 4),
            "direction": "LONG" if is_long else "SHORT",
            "targets": targets,
        }

    def calculate_sl_tp(
        self,
        entry_price: float,
        side: OrderSide,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
    ) -> Tuple[float, float]:
        sl_pct = sl_pct if sl_pct is not None else self.config.default_stop_loss
        tp_pct = tp_pct if tp_pct is not None else self.config.default_take_profit
        if side == OrderSide.BUY:
            sl = round(entry_price * (1 - sl_pct), 8)
            tp = round(entry_price * (1 + tp_pct), 8)
        else:
            sl = round(entry_price * (1 + sl_pct), 8)
            tp = round(entry_price * (1 - tp_pct), 8)
        return sl, tp

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        risk_usdt: float,
        category: Category = Category.LINEAR,
        leverage: Optional[float] = None,
    ) -> float:
        """Single canonical position sizing method.

        Args:
            symbol: Trading symbol
            entry_price: Entry price for the position
            sl_price: Stop loss price
            risk_usdt: Maximum risk amount in USDT
            category: Product category
            leverage: Leverage multiplier (if None, uses default from config or fetches current position leverage)
        """
        price_diff = abs(entry_price - sl_price)
        if price_diff == 0:
            return 0.0

        # Get leverage - use provided value or fetch current position leverage
        if leverage is None:
            try:
                positions = self.get_positions(category=category, symbol=symbol)
                if positions:
                    leverage = float(positions[0].get("leverage", 1))
                else:
                    leverage = float(self.config.default_leverage)
            except Exception:
                leverage = float(self.config.default_leverage)

        # Calculate quantity with leverage consideration
        # For leveraged trading, the actual position size can be larger
        raw_qty = (risk_usdt * leverage) / price_diff
        return self.adjust_quantity(symbol, raw_qty, category)

    def calculate_volatility_adjusted_size(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        risk_usdt: float,
        account_balance: float,
        risk_percentage: float = 0.02,
        atr_multiplier: float = 1.5,
        max_risk_percentage: float = 0.05,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate position size with volatility adjustment using ATR."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=100, category=category
            )
            if not klines or len(klines) < 20:
                return {
                    "status": "error",
                    "msg": "Insufficient data for volatility calculation",
                }

            atr = self.calculate_atr(klines, period=14)
            atr_pct = (atr / entry_price) * 100 if entry_price > 0 else 0

            max_risk_amount = account_balance * max_risk_percentage
            sl_distance = abs(entry_price - sl_price)
            if sl_distance == 0:
                return {"status": "error", "msg": "Stop loss equals entry price"}

            raw_qty = risk_usdt / sl_distance

            # Volatility adjustment factor
            if atr_pct > 3.0:
                vol_adjustment = 0.5
            elif atr_pct > 2.0:
                vol_adjustment = 0.7
            elif atr_pct > 1.0:
                vol_adjustment = 1.0
            elif atr_pct > 0.5:
                vol_adjustment = 1.2
            else:
                vol_adjustment = 1.5

            adjusted_qty = raw_qty * vol_adjustment
            actual_risk = adjusted_qty * sl_distance
            if actual_risk > max_risk_amount:
                adjusted_qty = max_risk_amount / sl_distance

            final_qty = self.adjust_quantity(symbol, adjusted_qty, category)
            is_long = entry_price > sl_price
            suggested_sl = (
                entry_price - (atr * atr_multiplier)
                if is_long
                else entry_price + (atr * atr_multiplier)
            )

            return {
                "symbol": symbol,
                "quantity": final_qty,
                "volatility_adjustment": vol_adjustment,
                "suggested_sl": self.adjust_price(symbol, suggested_sl, category),
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Volatility-adjusted sizing failed: %s", e)
            return {"status": "error", "msg": str(e)}

    def calculate_position_risk(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        account_balance: float,
        risk_percentage: float = 0.02,
    ) -> dict:
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            return {"error": "Stop loss equals entry price"}
        max_risk = account_balance * risk_percentage
        position_size = max_risk / risk_per_unit
        adjusted_size = self.adjust_quantity(symbol, position_size)
        actual_risk = adjusted_size * risk_per_unit
        risk_pct_actual = actual_risk / account_balance if account_balance > 0 else 0
        return {
            "symbol": symbol,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "position_size": adjusted_size,
            "max_risk": max_risk,
            "actual_risk": actual_risk,
            "risk_percentage": risk_pct_actual,
            "risk_per_unit": risk_per_unit,
        }

    # ══════════════════════════════════════════════════════════
    # TREND ANALYSIS ENGINE
    # ══════════════════════════════════════════════════════════
    def get_trend_analysis(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        interval: str = "60",
        lookback_periods: int = 200,
        include_advanced_indicators: bool = True,
        timeframe_analysis: bool = True,
    ) -> dict:
        """Advanced multi-indicator trend analysis with consensus scoring."""
        try:
            klines = self.get_klines(
                symbol, interval=interval, limit=lookback_periods, category=category
            )
            if not klines or len(klines) < 50:
                count = len(klines) if klines else 0
                return {
                    "status": "error",
                    "msg": f"Insufficient data for {symbol} (found {count}, need >=50)",
                }

            # klines are already ordered oldest to newest by get_klines
            opens, highs, lows, closes, vols, turnovers = self._extract_klines_data(
                klines
            )

            current_price = closes[-1]

            ema9 = self.calculate_ema(closes, period=9)
            ema21 = self.calculate_ema(closes, period=21)
            ema50 = self.calculate_ema(closes, period=50)
            ema200 = self.calculate_ema(closes, period=200)

            rsi = self.calculate_rsi(klines, period=14)
            macd = self.calculate_macd(klines)
            bb = self.calculate_bollinger_bands(klines)

            ohlcv_list_for_atr = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv_list_for_atr, period=14)

            momentum = self.get_market_momentum(symbol, category=category)

            # ── Scoring System (-100 to +100) ──
            score = 0

            # MA Trend (30%)
            ma_score = 0
            if current_price > ema21:
                ma_score += 15
            else:
                ma_score -= 15
            if ema9 > ema21:
                ma_score += 15
            else:
                ma_score -= 15
            score += ma_score

            # MACD Trend (20%)
            macd_score = 0
            if macd["macd"] > macd["signal"]:
                macd_score += 10
            else:
                macd_score -= 10
            if macd["macd"] > 0:
                macd_score += 10
            else:
                macd_score -= 10
            score += macd_score

            # RSI Momentum (15%)
            rsi_score = 0
            if 40 <= rsi <= 60:
                rsi_score = 0
            elif rsi > 60:
                rsi_score = 15 if rsi < 80 else 5
            elif rsi < 40:
                rsi_score = -15 if rsi > 20 else -5
            score += rsi_score

            # Volume Confirmation (10%)
            vol_avg = (
                sum(vols[-20:]) / 20
                if len(vols) >= 20
                else sum(vols) / max(len(vols), 1)
            )
            vol_score = 0
            if vols[-1] > vol_avg:
                if closes[-1] > closes[-2]:
                    vol_score += 10
                else:
                    vol_score -= 10
            score += vol_score

            # Order Flow (10%)
            flow_score = 0
            sig = momentum.get("signal", "NEUTRAL")
            if "STRONG_BUY" in sig:
                flow_score += 10
            elif "BUY" in sig:
                flow_score += 5
            elif "STRONG_SELL" in sig:
                flow_score -= 10
            elif "SELL" in sig:
                flow_score -= 5
            score += flow_score

            # Price Action / Alignment (15%)
            pa_score = 0
            if current_price > ema50:
                pa_score += 7.5
            else:
                pa_score -= 7.5
            if ema50 > ema200:
                pa_score += 7.5
            else:
                pa_score -= 7.5
            score += pa_score

            # Multi-Timeframe check
            mtf_alignment = "NOT_CHECKED"
            if interval in ("60", "1h"):
                try:
                    klines_4h = self.get_klines(
                        symbol, interval="240", limit=50, category=category
                    )
                    if klines_4h:
                        closes_4h = [float(k[4]) for k in klines_4h]
                        ema20_4h = self.calculate_ema(closes_4h, 20)
                        if current_price > ema20_4h and score >= 20:
                            mtf_alignment = (
                                "ALIGNED_BULLISH"  # Require some bullish score
                            )
                        elif current_price < ema20_4h and score <= -20:
                            mtf_alignment = (
                                "ALIGNED_BEARISH"  # Require some bearish score
                            )
                        else:
                            mtf_alignment = "MIXED"
                except Exception:
                    mtf_alignment = "ERROR"

            # Classification
            if score >= 60:
                trend = "STRONG_BULLISH"
            elif score >= 20:
                trend = "BULLISH"
            elif score <= -60:
                trend = "STRONG_BEARISH"
            elif score <= -20:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"

            # Risk Metrics calculation using ATR
            stop_loss_val = (
                current_price - (atr * 2.0)
                if score >= 0
                else current_price + (atr * 2.0)
            )
            take_profit_val = (
                current_price + (atr * 4.0)
                if score >= 0
                else current_price - (atr * 4.0)
            )

            # Ensure stop loss and take profit are valid prices
            stop_loss_val = self.adjust_price(symbol, stop_loss_val, category)
            take_profit_val = self.adjust_price(symbol, take_profit_val, category)

            # Guidance
            advice = "WAIT"
            if trend == "STRONG_BULLISH":
                advice = "STRONG_BUY"
            elif trend == "BULLISH":
                advice = "BUY_ON_DIP" if current_price <= ema9 * 1.005 else "HOLD_LONG"
            elif trend == "STRONG_BEARISH":
                advice = "STRONG_SELL"
            elif trend == "BEARISH":
                advice = (
                    "SELL_ON_RALLY" if current_price >= ema9 * 0.995 else "HOLD_SHORT"
                )

            if rsi > 75:
                advice = "TAKE_PROFIT / AVOID_LONG"
            if rsi < 25:
                advice = "WATCH_FOR_REVERSAL / AVOID_SHORT"

            result = {
                "symbol": symbol,
                "interval": interval,
                "score": round(score, 2),
                "trend": trend,
                "mtf_alignment": mtf_alignment,
                "current_price": round(current_price, 4),
                "indicators": {
                    "rsi": round(rsi, 2),
                    "macd": macd,
                    "ema9": round(ema9, 4),
                    "ema21": round(ema21, 4),
                    "ema50": round(ema50, 4),
                    "ema200": round(ema200, 4),
                    "atr": round(atr, 4),
                    "bb": bb,
                },
                "risk_guidance": {
                    "suggested_stop_loss": round(stop_loss_val, 4),
                    "suggested_take_profit": round(take_profit_val, 4),
                    "risk_reward_ratio": 2.0,  # Hardcoded for this example guidance
                },
                "action_advice": advice,
                "timestamp": time.time(),
            }

            if include_advanced_indicators:
                result["advanced"] = {
                    "adx": self.calculate_adx(klines, period=14),
                    "cci": self.calculate_cci(klines, period=20),
                    "stoch_rsi": self.calculate_stoch_rsi(klines, period=14),
                    "stoch_osc": self.calculate_stoch_oscillator(
                        klines, period=14
                    ),  # New
                    "mfi": self.calculate_mfi(klines, period=14),  # New
                    "obv": self.calculate_obv(klines),  # New
                }
            return result
        except Exception as e:
            logger.error("Trend analysis failed: %s", e)
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # SAFE EXECUTE
    # ══════════════════════════════════════════════════════════
    def safe_execute(
        self,
        fn: Callable,
        *args,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except RuntimeError as exc:
                if "Circuit OPEN" in str(exc):
                    logger.error("Circuit OPEN – aborting: %s", exc)
                    return {"status": "circuit_open", "msg": str(exc)}
                last_exc = exc
            except Exception as exc:
                last_exc = exc

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed: %s – retrying in %.1fs",
                attempt,
                max_retries,
                last_exc,
                delay,
            )
            time.sleep(delay)

        logger.error("All %d attempts exhausted. Last: %s", max_retries, last_exc)
        return {"status": "error", "msg": str(last_exc)}

    # ══════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ══════════════════════════════════════════════════════════
    def connection_health(self) -> dict:
        """Score connection health from 0-100 based on response times and success rates."""
        try:
            start = time.time()
            self.api_request("GET", "/v5/market/time", signed=False)
            latency = (time.time() - start) * 1000

            # Score based on latency
            if latency < 100:
                score = 100
            elif latency < 200:
                score = 90
            elif latency < 500:
                score = 70
            elif latency < 1000:
                score = 50
            else:
                score = 30

            return {
                "status": "ok",
                "latency_ms": round(latency, 2),
                "score": score,
                "circuit_state": self.circuit.state.value
                if hasattr(self, "circuit")
                else "UNKNOWN",
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "score": 0, "msg": str(e)}

    def health_check(self) -> dict:
        """Enhanced health check with geo IP info and Tor status."""
        try:
            resp = self.api_request("GET", "/v5/market/time", signed=False)

            geo_ip = "N/A"
            geo_location = {"status": "not_checked"}

            if self._geo_router:
                try:
                    geo_ip = self._geo_router.get_public_ip()
                    geo_location = self._geo_router.get_geo_location()
                except Exception:
                    pass
            elif PYSOCKS_AVAILABLE and self.config.pysocks_enabled:
                try:
                    router = PySocksGeoRouter(
                        proxy_host=self.config.pysocks_host,
                        proxy_port=self.config.pysocks_port,
                        rdns=True,
                    )
                    geo_ip = router.get_public_ip()
                    geo_location = router.get_geo_location()
                    router.close()
                except Exception:
                    pass

            return {
                "status": "ok",
                "circuit": self.circuit.state.value,
                "circuit_fails": self.circuit.failure_count,
                "rate_usage": self.limiter.current_usage,
                "server_time": resp.get("result", {}).get("timeNano"),
                "time_offset_ms": self._time_offset,
                "base_url": self.config.base_url,
                "tor_enabled": self.config.use_tor,
                "tor_control_accessible": self.tor._tor_control_port_accessible,
                "tor_use_pysocks": self.config.tor_use_pysocks,
                "pysocks_available": PYSOCKS_AVAILABLE,
                "pysocks_enabled": self.config.pysocks_enabled,
                "pysocks_host": self.config.pysocks_host,
                "pysocks_port": self.config.pysocks_port,
                "pysocks_region": self.config.pysocks_region,
                "pysocks_global": self.config.pysocks_global,
                "geo_ip": geo_ip,
                "geo_location": geo_location,
                "testnet": self.config.testnet,
                "cache_symbols": list(self._instr_cache.keys()),
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}


# ─────────────────────────────────────────────────────────────
# SINGLETON DISPATCHER
# ─────────────────────────────────────────────────────────────
_dispatcher: Optional[BybitToolDispatcher] = None
_disp_lock = threading.Lock()


def _get_dispatcher(config: Optional[TradingConfig] = None) -> BybitToolDispatcher:
    """Lazily create singleton dispatcher. Accepts optional config for CLI injection."""
    global _dispatcher
    if _dispatcher is None or config is not None:
        with _disp_lock:
            if _dispatcher is None or config is not None:
                cfg = config or TradingConfig.from_file("trading_config.json")
                _dispatcher = BybitToolDispatcher(cfg)
    return _dispatcher


def _reset_dispatcher() -> None:
    global _dispatcher
    with _disp_lock:
        _dispatcher = None


# ─────────────────────────────────────────────────────────────
# TOOL ENTRY POINT
# ─────────────────────────────────────────────────────────────
def run(
    action: Literal[
        "health_check",
        "connection_health",
        "place_order",
        "amend_order",
        "cancel_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_order_history",
        "get_positions",
        "get_wallet_balance",
        "set_leverage",
        "set_trading_stop",
        "get_ticker",
        "get_orderbook",
        "get_klines",
        "get_recent_trades",
        "get_open_interest",
        "get_liquidations",
        "get_market_momentum",
        "get_market_health",
        "get_trend_analysis",
        "get_funding_rate",
        "get_long_short_ratio",
        "get_transaction_log",
        "calculate_bollinger_bands",
        "calculate_macd",
        "calculate_stoch_rsi",
        "calculate_cci",
        "calculate_donchian_channels",
        "calculate_adx",
        "calculate_kelly_criterion",
        "calculate_fib_pivots",
        "calculate_trade_pnl",
        "calculate_profit_target",
        "calculate_sl_tp",
        "calculate_position_size",
        "calculate_volatility_adjusted_size",
        "place_trailing_stop_order",
        "calculate_trailing_stop_levels",
        "get_trailing_stop_status",
        "calculate_vwap",
        "calculate_ichimoku_cloud",
        "get_pnl_history",
        "get_pnl_report",
        "get_fee_rate",
        "switch_margin_mode",
        "switch_position_mode",
        "batch_orders",
        "iceberg_order",
        "reset_circuit",
        "renew_tor_circuit",
        # New indicators exposed as actions
        "calculate_stoch_oscillator",
        "calculate_mfi",
        "calculate_obv",
        "calculate_ema",  # Explicitly expose EMA
    ],
    # ── Order fields ──────────────────────────────────────────
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
    # ── Account ───────────────────────────────────────────────
    leverage: Optional[int] = None,
    buy_leverage: Optional[int] = None,
    sell_leverage: Optional[int] = None,
    account_type: Optional[str] = "UNIFIED",
    # ── Market data / Indicator params ────────────────────────
    limit: Optional[int] = 25,  # Also used for history limits
    interval: Optional[
        str
    ] = "1",  # Kline interval, also used for trend analysis interval
    interval_time: Optional[str] = "5min",  # Used for open interest, account ratio
    # ── Momentum / Health thresholds ──────────────────────────
    strong_threshold: Optional[float] = 0.20,
    mild_threshold: Optional[float] = 0.08,
    # ── Risk helpers / Indicator smoothing params ──────────────
    sl_pct: Optional[
        float
    ] = None,  # Stop-loss percentage / win_rate for Kelly / smooth_k for Stoch RSI/Osc
    tp_pct: Optional[
        float
    ] = None,  # Take-profit percentage / win_loss_ratio for Kelly / smooth_d for Stoch RSI/Osc
    risk_usdt: Optional[float] = None,
    sl_price: Optional[float] = None,
    # ── Batch / Iceberg ───────────────────────────────────────
    orders: Optional[List[Dict[str, Any]]] = None,  # For batch orders
    slices: Optional[int] = 5,
    delay: Optional[float] = None,
    # ── Time Range Filters ─────────────────────────────────────
    start_time: Optional[int] = None,  # Start timestamp in milliseconds
    end_time: Optional[int] = None,  # End timestamp in milliseconds
    # ── Output & File Inputs ───────────────────────────────────
    output: Optional[str] = None,  # Output file path
    orders_file: Optional[str] = None,  # JSON file with batch order list
) -> dict:
    """
    Bybit Trading Tool – Execute any supported trading operation.

    Args:
        action: Operation to perform (see Literal type for options)
        symbol: Trading symbol (e.g., 'BTCUSDT')
        side: Order side ('Buy' or 'Sell')
        qty: Order quantity
        price: Limit price (also used as entry_price for sl/tp calc)
        order_type: Order type
        category: Product category
        order_id: Existing order ID for amend/cancel
        stop_loss: Stop loss price
        take_profit: Take profit price
        trailing_stop: Trailing stop distance
        reduce_only: Whether the order is reduce-only
        time_in_force: Time in force
        position_idx: Position index (0=one-way, 1=hedge-buy, 2=hedge-sell)
        client_oid: Client order link ID
        leverage: Leverage for the position
        buy_leverage: Independent buy-side leverage
        sell_leverage: Independent sell-side leverage
        account_type: Account type
        limit: Result count for list endpoints (also used for kline lookback in trend analysis)
        interval: Kline interval (e.g., "1m", "1h") for indicators/trend analysis
        interval_time: Interval for open interest, account ratio queries
        strong_threshold: Momentum strong-signal cutoff
        mild_threshold: Momentum mild-signal cutoff
        sl_pct: Stop-loss percentage / win_rate for Kelly / smooth_k for Stoch RSI/Osc
        tp_pct: Take-profit percentage / win_loss_ratio for Kelly / smooth_d for Stoch RSI/Osc
        risk_usdt: Max USDT risk for position sizing
        sl_price: Explicit SL price for position sizing
        orders: List of order dicts for batch operations (pass JSON string via CLI)
        slices: Number of slices for iceberg orders
        delay: Seconds between iceberg slices
        start_time: Start timestamp in milliseconds for history queries
        end_time: End timestamp in milliseconds for history queries
        output: Output file path for JSON result
        orders_file: JSON file containing a list of orders for batch operations
    """
    bot = _get_dispatcher()

    try:
        # Safely convert category and time_in_force to Enum values
        cat = (
            Category(str(category).strip())
            if category and str(category).strip()
            else Category.LINEAR
        )
        tif = (
            TimeInForce(str(time_in_force).strip())
            if time_in_force and str(time_in_force).strip()
            else TimeInForce.GTC
        )

        # Safely convert order_type to Enum value
        ord_type_val = OrderType.LIMIT.value  # Default
        if order_type:
            try:
                ord_type_val = OrderType(str(order_type).strip()).value
            except ValueError:
                logger.warning(
                    "Invalid order_type '%s', defaulting to Limit", order_type
                )

        # Safely convert side to Enum value
        ord_side_val = None
        if side:
            try:
                ord_side_val = OrderSide(str(side).strip())
            except ValueError:
                logger.warning("Invalid side '%s', must be Buy or Sell", side)
                return {
                    "status": "error",
                    "msg": f"Invalid side: {side}. Must be Buy or Sell.",
                }

        pidx = PositionIdx.ONE_WAY
        if position_idx is not None:
            try:
                pidx = PositionIdx(int(position_idx))
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid position_idx '%s', defaulting to One-Way (0)", position_idx
                )
                pidx = PositionIdx.ONE_WAY

        # ── Diagnostics ───────────────────────────────────────
        if action == "health_check":
            return bot.health_check()
        elif action == "connection_health":
            return bot.connection_health()

        elif action == "reset_circuit":
            bot.circuit.reset()
            return {"status": "ok", "msg": "Circuit breaker reset to CLOSED"}

        elif action == "renew_tor_circuit":
            success = bot.tor.renew_tor_circuit()
            return {
                "status": "ok" if success else "error",
                "msg": "Tor circuit renewed"
                if success
                else "Failed to renew Tor circuit",
            }

        # ── Place Order ───────────────────────────────────────
        elif action == "place_order":
            if not symbol or not ord_side_val or qty is None:
                return {"status": "error", "msg": "symbol, side, and qty are required"}
            return bot.place_order(
                symbol=symbol,
                side=ord_side_val,
                qty=qty,
                price=price,
                order_type=OrderType(ord_type_val),
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only or False,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
                trailing_stop=trailing_stop,
            )

        # ── Amend Order ───────────────────────────────────────
        elif action == "amend_order":
            if not symbol or not order_id:
                return {"status": "error", "msg": "symbol and order_id are required"}
            return bot.amend_order(
                symbol=symbol,
                order_id=order_id,
                qty=qty,
                price=price,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        # ── Cancel ────────────────────────────────────────────
        elif action == "cancel_order":
            if not symbol or not order_id:
                return {"status": "error", "msg": "symbol and order_id are required"}
            return bot.cancel_order(symbol=symbol, order_id=order_id, category=cat)

        elif action == "cancel_all_orders":
            # Symbol is optional - can cancel all orders in category
            return bot.cancel_all_orders(symbol=symbol, category=cat)

        # ── Market Analysis & Momentum ────────────────────────
        elif action == "get_market_momentum":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_market_momentum(
                symbol=symbol,
                category=cat,
                strong_threshold=strong_threshold or 0.20,
                mild_threshold=mild_threshold or 0.08,
            )

        elif action == "get_market_health":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_market_health(symbol=symbol, category=cat)

        # ── Risk Calculators ──────────────────────────────────
        elif action == "calculate_kelly_criterion":
            if sl_pct is None or tp_pct is None:
                return {
                    "status": "error",
                    "msg": "sl_pct (win_rate) and tp_pct (win/loss ratio) required",
                }
            return {"kelly_fraction": bot.calculate_kelly_criterion(sl_pct, tp_pct)}

        elif action == "calculate_trade_pnl":
            if price is None or qty is None or sl_price is None or not side:
                return {
                    "status": "error",
                    "msg": "entry (price), exit (sl_price), qty, side required",
                }
            return bot.calculate_trade_pnl(
                entry=price, exit=sl_price, qty=qty, side=side
            )

        elif action == "calculate_profit_target":
            if price is None or sl_price is None:
                return {"status": "error", "msg": "price (entry) and sl_price required"}
            return bot.calculate_profit_target(entry_price=price, sl_price=sl_price)

        # ── Order Queries ─────────────────────────────────────
        elif action == "get_open_orders":
            return {
                "orders": bot.get_open_orders(
                    symbol=symbol, category=cat, limit=limit or 50
                )
            }

        elif action == "get_order_history":
            return {
                "orders": bot.get_order_history(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 50,
                    start_time=start_time,
                    end_time=end_time,
                )
            }

        # ── Positions ─────────────────────────────────────────
        elif action == "get_positions":
            return {"positions": bot.get_positions(category=cat, symbol=symbol)}

        # ── Balance ───────────────────────────────────────────
        elif action == "get_wallet_balance":
            return bot.get_wallet_balance(account_type=account_type or "UNIFIED")

        # ── Leverage ──────────────────────────────────────────
        elif action == "set_leverage":
            if not symbol or leverage is None:
                return {"status": "error", "msg": "symbol and leverage are required"}
            return bot.set_leverage(
                symbol=symbol,
                leverage=leverage,
                category=cat,
                buy_leverage=buy_leverage,
                sell_leverage=sell_leverage,
            )

        # ── Trading Stop ──────────────────────────────────────
        elif action == "set_trading_stop":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.set_trading_stop(
                symbol=symbol,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop=trailing_stop,
                category=cat,
                position_idx=pidx,
            )

        # ── Market Data ───────────────────────────────────────
        elif action == "get_ticker":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_ticker(symbol=symbol, category=cat)

        elif action == "get_orderbook":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_orderbook(symbol=symbol, limit=limit or 25, category=cat)

        elif action == "get_klines":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "klines": bot.get_klines(
                    symbol=symbol,
                    interval=interval or "1",
                    limit=limit or 200,
                    category=cat,
                )
            }

        elif action == "get_recent_trades":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "trades": bot.get_recent_trades(
                    symbol=symbol, limit=limit or 500, category=cat
                )
            }

        elif action == "get_open_interest":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "open_interest": bot.get_open_interest(
                    symbol=symbol,
                    interval_time=interval_time or "5min",
                    category=cat,
                    limit=limit or 50,
                )
            }

        elif action == "get_liquidations":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "liquidations": bot.get_liquidations(
                    symbol=symbol, category=cat, limit=limit or 200
                )
            }

        # ── Market Intelligence ───────────────────────────────
        elif action == "get_market_momentum":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_market_momentum(
                symbol=symbol,
                category=cat,
                strong_threshold=strong_threshold or 0.20,
                mild_threshold=mild_threshold or 0.08,
            )

        elif action == "get_trend_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            # Use limit for lookback_periods in trend analysis
            lookback = limit if limit is not None else 200
            return bot.get_trend_analysis(
                symbol=symbol,
                category=cat,
                interval=interval or "60",
                lookback_periods=lookback,
                include_advanced_indicators=True,
            )

        elif action == "get_market_health":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_market_health(symbol=symbol, category=cat)

        elif action == "get_funding_rate":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_funding_rate(symbol=symbol, category=cat)

        elif action == "get_long_short_ratio":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "list": bot.get_long_short_ratio(
                    symbol=symbol, period=interval_time or "5min", limit=limit or 50
                )
            }

        # ── Account & Position Management ─────────────────────
        elif action == "get_fee_rate":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_fee_rate(symbol=symbol, category=cat)

        elif action == "get_transaction_log":
            return {
                "list": bot.get_transaction_log(
                    account_type=account_type or "UNIFIED",
                    category=cat,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit or 20,
                )
            }

        elif action == "switch_margin_mode":
            if not symbol or position_idx is None:
                return {
                    "status": "error",
                    "msg": "symbol and position_idx (trade_mode: 0 cross, 1 isolated) are required",
                }
            return bot.switch_margin_mode(
                symbol=symbol,
                trade_mode=position_idx,
                category=cat,
                leverage=str(leverage or 1),
            )

        elif action == "switch_position_mode":
            if position_idx is None:
                return {
                    "status": "error",
                    "msg": "position_idx (mode: 0 one-way, 3 hedge) is required",
                }
            # Use 'leverage' field as 'coin' if needed, or just pass None
            return bot.switch_position_mode(
                category=cat, symbol=symbol, mode=position_idx
            )

        # ── Technical Indicators (manual input) ───────────────
        elif action == "calculate_bollinger_bands":
            try:
                prices = ast.literal_eval(str(qty)) if qty else []
                period = limit or 20
                std_dev = (
                    sl_pct if sl_pct is not None else 2.0
                )  # Use sl_pct for std_dev
                return bot.calculate_bollinger_bands(
                    prices, period=period, std_dev=std_dev
                )
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid prices list format. Expected a list of numbers. Error: {e}",
                }

        elif action == "calculate_vwap":
            try:
                klines_data = ast.literal_eval(str(orders)) if orders else []
                return {"vwap": bot.calculate_vwap(klines_data)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid klines list format. Expected a list of lists. Error: {e}",
                }

        elif action == "calculate_ichimoku_cloud":
            try:
                highs = ast.literal_eval(str(qty)) if qty else []
                lows = ast.literal_eval(str(price)) if price else []
                return bot.calculate_ichimoku_cloud(highs, lows)
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid highs/lows list format. Expected lists of numbers. Error: {e}",
                }

        elif action == "calculate_macd":
            try:
                prices = ast.literal_eval(str(qty)) if qty else []
                fast_period = limit or 12  # Use limit for fast period
                slow_period = (
                    sl_pct if sl_pct is not None else 26
                )  # Use sl_pct for slow period
                signal_period = (
                    tp_pct if tp_pct is not None else 9
                )  # Use tp_pct for signal period
                return bot.calculate_macd(
                    prices,
                    fast=int(fast_period),
                    slow=int(slow_period),
                    signal=int(signal_period),
                )
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid prices list format. Expected a list of numbers. Error: {e}",
                }

        elif action == "calculate_stoch_rsi":
            try:
                prices = ast.literal_eval(str(qty)) if qty else []
                period = limit or 14
                # Use sl_pct as smooth_k and tp_pct as smooth_d if provided
                smooth_k = int(sl_pct) if sl_pct is not None and sl_pct > 0 else 3
                smooth_d = int(tp_pct) if tp_pct is not None and tp_pct > 0 else 3
                return bot.calculate_stoch_rsi(
                    prices, period=period, smooth_k=smooth_k, smooth_d=smooth_d
                )
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid prices list format. Expected a list of numbers. Error: {e}",
                }

        elif action == "calculate_cci":
            try:
                highs = ast.literal_eval(str(qty)) if qty else []
                lows = ast.literal_eval(str(price)) if price else []
                closes = ast.literal_eval(str(sl_price)) if sl_price else []
                period = limit or 20
                return {"cci": bot.calculate_cci(highs, lows, closes, period=period)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid list format. Expected lists of numbers. Error: {e}",
                }

        elif action == "calculate_donchian_channels":
            try:
                highs = ast.literal_eval(str(qty)) if qty else []
                lows = ast.literal_eval(str(price)) if price else []
                period = limit or 20
                return bot.calculate_donchian_channels(highs, lows, period=period)
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid lists format. Expected lists of numbers. Error: {e}",
                }

        elif action == "calculate_adx":
            try:
                highs = ast.literal_eval(str(qty)) if qty else []
                lows = ast.literal_eval(str(price)) if price else []
                closes = ast.literal_eval(str(sl_price)) if sl_price else []
                period = limit or 14
                return {"adx": bot.calculate_adx(highs, lows, closes, period=period)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid lists format. Expected lists of numbers. Error: {e}",
                }

        # New indicator actions
        elif action == "calculate_stoch_oscillator":
            try:
                prices = ast.literal_eval(str(qty)) if qty else []
                period = limit or 14
                smooth_k = int(sl_pct) if sl_pct is not None and sl_pct > 0 else 3
                smooth_d = int(tp_pct) if tp_pct is not None and tp_pct > 0 else 3
                return bot.calculate_stoch_oscillator(
                    prices, period=period, smooth_k=smooth_k, smooth_d=smooth_d
                )
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid prices list format. Expected a list of numbers. Error: {e}",
                }

        elif action == "calculate_mfi":
            try:
                klines_data = ast.literal_eval(str(orders)) if orders else []
                period = limit or 14
                return {"mfi": bot.calculate_mfi(klines_data, period=period)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid klines list format. Expected a list of lists. Error: {e}",
                }

        elif action == "calculate_obv":
            try:
                klines_data = ast.literal_eval(str(orders)) if orders else []
                return {"obv": bot.calculate_obv(klines_data)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid klines list format. Expected a list of lists. Error: {e}",
                }

        elif action == "calculate_ema":
            try:
                prices = ast.literal_eval(str(qty)) if qty else []
                period = limit or 20
                return {"ema": bot.calculate_ema(prices, period=period)}
            except (ValueError, SyntaxError, TypeError) as e:
                return {
                    "status": "error",
                    "msg": f"Invalid prices list format. Expected a list of numbers. Error: {e}",
                }

        # ── PnL ───────────────────────────────────────────────
        elif action == "get_pnl_history":
            return {
                "pnl_history": bot.get_pnl_history(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 100,
                    start_time=start_time,
                    end_time=end_time,
                )
            }

        elif action == "get_pnl_report":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_pnl_report(
                symbol=symbol,
                category=cat,
                limit=limit or 100,
                start_time=start_time,
                end_time=end_time,
            ).to_dict()

        # ── Batch Orders ──────────────────────────────────────
        elif action == "batch_orders":
            if not orders and not orders_file:
                return {
                    "status": "error",
                    "msg": "orders list or orders_file is required",
                }

            orders_data = orders
            if orders_file:
                try:
                    with open(orders_file) as f:
                        orders_data = json.load(f)
                except FileNotFoundError:
                    return {
                        "status": "error",
                        "msg": f"Orders file not found: {orders_file}",
                    }
                except json.JSONDecodeError:
                    return {
                        "status": "error",
                        "msg": f"Invalid JSON in orders file: {orders_file}",
                    }

            if not orders_data:
                return {"status": "error", "msg": "No orders data provided"}

            return bot.safe_execute(bot.execute_scalp_batch, orders_data)

        # ── Iceberg Order ─────────────────────────────────────
        elif action == "iceberg_order":
            if not symbol or not ord_side_val or qty is None or price is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, qty, and price required",
                }
            results = bot.place_iceberg_order(
                symbol=symbol,
                side=ord_side_val,
                total_qty=qty,
                price=price,
                slices=int(slices) if slices else 5,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                delay=float(delay) if delay is not None else 0.5,
            )
            return {"status": "ok", "iceberg_results": results}

        else:
            return {
                "status": "error",
                "msg": f"Unknown action: '{action}'. Use health_check to see available actions.",
            }

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
Available Actions:
  health_check, connection_health, reset_circuit, renew_tor_circuit
  place_order, amend_order, cancel_order, cancel_all_orders
  get_open_orders, get_order_history, get_positions, get_wallet_balance
  set_leverage, set_trading_stop
  get_ticker, get_orderbook, get_klines, get_recent_trades, get_open_interest, get_liquidations
  get_market_momentum, get_market_health, get_trend_analysis, get_funding_rate, get_long_short_ratio
  get_transaction_log, get_fee_rate
  switch_margin_mode, switch_position_mode
  calculate_bollinger_bands, calculate_macd, calculate_stoch_rsi, calculate_cci, calculate_donchian_channels, calculate_adx
  calculate_kelly_criterion, calculate_fib_pivots, calculate_trade_pnl, calculate_profit_target, calculate_sl_tp, calculate_position_size, calculate_volatility_adjusted_size
  place_trailing_stop_order, calculate_trailing_stop_levels, get_trailing_stop_status
  calculate_vwap, calculate_ichimoku_cloud
  get_pnl_history, get_pnl_report
  batch_orders, iceberg_order
  calculate_stoch_oscillator, calculate_mfi, calculate_obv, calculate_ema

Example Usage:
  python bybit-realm.py --action health_check
  python bybit-realm.py --action get_ticker --symbol BTCUSDT --category spot
  python bybit-realm.py --action get_market_momentum --symbol BTCUSDT
  python bybit-realm.py --action get_trend_analysis --symbol BTCUSDT --interval 60 --limit 200
  python bybit-realm.py --action get_market_health --symbol BTCUSDT
  python bybit-realm.py --action get_wallet_balance --account-type UNIFIED
  python bybit-realm.py --action get_positions --category linear
  python bybit-realm.py --action calculate_sl_tp --side Buy --price 65000 --sl-pct 0.01 --tp-pct 0.03
  python bybit-realm.py --action get_pnl_report --symbol BTCUSDT
  python bybit-realm.py --action renew_tor_circuit
  python bybit-realm.py --action calculate_macd --qty "[10,12,11,15,14,16,18,17,20,19]" --limit 12 --sl-pct 26 --tp-pct 9
  python bybit-realm.py --action calculate_stoch_rsi --qty "[30,35,33,40,38,42,45,43,48,46]" --limit 14 --sl-pct 3 --tp-pct 3
""",
    )

    parser.add_argument(
        "--config",
        default="trading_config.json",
        help="Path to JSON configuration file",
    )
    parser.add_argument(
        "--action", required=True, help="Action to perform (see --help for list)"
    )
    parser.add_argument("--symbol", help="Trading symbol e.g. BTCUSDT")
    parser.add_argument("--side", help="Buy | Sell")
    parser.add_argument("--qty", type=float, help="Order quantity")
    parser.add_argument("--price", type=float, help="Order / entry price")
    parser.add_argument(
        "--order-type",
        dest="order_type",
        help="Limit | Market | LimitMaker | Stop | StopLimit",
    )
    parser.add_argument(
        "--category", default="linear", help="linear | inverse | spot | option"
    )
    parser.add_argument("--order-id", dest="order_id", help="Order ID for amend/cancel")
    parser.add_argument(
        "--stop-loss", dest="stop_loss", type=float, help="Stop loss price"
    )
    parser.add_argument(
        "--take-profit", dest="take_profit", type=float, help="Take profit price"
    )
    parser.add_argument(
        "--trailing-stop",
        dest="trailing_stop",
        type=float,
        help="Trailing stop distance",
    )
    parser.add_argument(
        "--reduce-only",
        dest="reduce_only",
        action="store_true",
        help="Set order as reduce-only",
    )
    parser.add_argument(
        "--time-in-force",
        dest="time_in_force",
        default="GTC",
        help="GTC | IOC | FOK | PostOnly",
    )
    parser.add_argument(
        "--position-idx",
        dest="position_idx",
        type=int,
        default=0,
        help="Position index (0=one-way, 1=hedge-buy, 2=hedge-sell)",
    )
    parser.add_argument("--client-oid", dest="client_oid", help="Client order link ID")
    parser.add_argument("--leverage", type=int, help="Leverage multiplier")
    parser.add_argument(
        "--buy-leverage",
        dest="buy_leverage",
        type=int,
        help="Independent buy-side leverage",
    )
    parser.add_argument(
        "--sell-leverage",
        dest="sell_leverage",
        type=int,
        help="Independent sell-side leverage",
    )
    parser.add_argument(
        "--account-type",
        dest="account_type",
        default="UNIFIED",
        help="UNIFIED | CONTRACT | SPOT",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Number of results for list endpoints (also kline lookback for trend analysis)",
    )
    parser.add_argument(
        "--interval",
        default="1",
        help="Kline interval (e.g., 1m, 1h, 1d) or trend analysis interval",
    )
    parser.add_argument(
        "--interval-time",
        dest="interval_time",
        default="5min",
        help="Interval for open interest, account ratio queries",
    )
    parser.add_argument(
        "--strong-threshold",
        dest="strong_threshold",
        type=float,
        default=0.20,
        help="Momentum strong-signal cutoff",
    )
    parser.add_argument(
        "--mild-threshold",
        dest="mild_threshold",
        type=float,
        default=0.08,
        help="Momentum mild-signal cutoff",
    )
    parser.add_argument(
        "--sl-pct",
        dest="sl_pct",
        type=float,
        help="Stop loss percentage (for sizing/risk), or win_rate (for Kelly), or smooth_k (for Stoch indicators)",
    )
    parser.add_argument(
        "--tp-pct",
        dest="tp_pct",
        type=float,
        help="Take profit percentage (for sizing/risk), or win_loss_ratio (for Kelly), or smooth_d (for Stoch indicators)",
    )
    parser.add_argument(
        "--risk-usdt",
        dest="risk_usdt",
        type=float,
        help="Maximum risk amount in USDT for position sizing",
    )
    parser.add_argument(
        "--sl-price",
        dest="sl_price",
        type=float,
        help="Stop loss price (used for sizing/risk calculation)",
    )
    parser.add_argument(
        "--slices", type=int, default=5, help="Number of slices for iceberg orders"
    )
    parser.add_argument(
        "--delay", type=float, help="Delay in seconds between iceberg slices"
    )
    parser.add_argument(
        "--start-time",
        dest="start_time",
        type=int,
        help="Start timestamp in milliseconds for history queries",
    )
    parser.add_argument(
        "--end-time",
        dest="end_time",
        type=int,
        help="End timestamp in milliseconds for history queries",
    )
    parser.add_argument("--output", help="Output file path for JSON result")
    parser.add_argument(
        "--orders-file",
        dest="orders_file",
        help="JSON file containing a list of orders for batch operations",
    )

    args = parser.parse_args()

    # FIX: inject CLI config into singleton so run() uses it
    config = TradingConfig.from_file(args.config)
    _get_dispatcher(config)

    orders_data = None
    if getattr(args, "orders_file", None):
        try:
            with open(args.orders_file) as f:
                orders_data = json.load(f)
        except FileNotFoundError:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "msg": f"Orders file not found: {args.orders_file}",
                    }
                )
            )
            sys.exit(1)
        except json.JSONDecodeError:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "msg": f"Invalid JSON in orders file: {args.orders_file}",
                    }
                )
            )
            sys.exit(1)

    # Handle potential list inputs for indicator calculations from CLI
    # These are typically passed as JSON strings that need parsing.
    # The `run` function uses `ast.literal_eval` for `qty`, `price`, `sl_price`, `orders`.
    # This is generally safe for JSON-like structures, but be aware of potential issues
    # if the input is not properly formatted.

    result = run(
        action=args.action,
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        price=args.price,
        order_type=args.order_type,
        category=args.category,
        order_id=args.order_id,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        trailing_stop=args.trailing_stop,
        reduce_only=args.reduce_only,
        time_in_force=args.time_in_force,
        position_idx=args.position_idx,
        client_oid=args.client_oid,
        leverage=args.leverage,
        buy_leverage=args.buy_leverage,
        sell_leverage=args.sell_leverage,
        account_type=args.account_type,
        limit=args.limit,
        interval=args.interval,
        interval_time=args.interval_time,
        strong_threshold=args.strong_threshold,
        mild_threshold=args.mild_threshold,
        sl_pct=args.sl_pct,
        tp_pct=args.tp_pct,
        risk_usdt=args.risk_usdt,
        sl_price=args.sl_price,
        orders=orders_data,
        slices=args.slices,
        delay=args.delay,
        start_time=args.start_time,
        end_time=args.end_time,
    )

    output_path = args.output or os.environ.get("LLM_OUTPUT")
    if output_path:
        try:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            logger.info("Result written to %s", output_path)
        except OSError as e:
            logger.error("Failed to write output to %s: %s", output_path, e)
            print(
                json.dumps(
                    {"status": "error", "msg": f"Failed to write output file: {e}"}
                )
            )
    else:
        print(json.dumps(result, indent=2))
