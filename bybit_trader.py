#!/usr/bin/env python3
# @describe Execute market/limit orders on Bybit V5 with live TA signals via Tor.
# @option --symbol!         Trading pair (e.g., BTCUSDT).
# @option --side!           Order side: Buy or Sell.
# @option --qty!            Quantity to trade.
# @option --category        Market category: spot, linear, or inverse (default: spot).
# @option --testnet         Use Bybit testnet instead of mainnet (flag).
# @option --balance         Fetch wallet balance before placing an order (flag).
# @option --limit-price     Price for a limit order. Omit for market orders.
# @option --time-in-force   GTC | IOC | FOK | PostOnly (default: GTC).
# @option --analyze         Print TA snapshot (RSI/MACD/BB/ATR/VWAP) before ordering (flag).
# @option --interval        Kline interval for TA: 1 3 5 15 30 60 120 240 (default: 15).
# @option --kline-limit     Number of candles to fetch for TA calculations (default: 200).
# @option --renew-circuit   Force a fresh Tor circuit before any operation (flag).
# @option --tor-check       Verify Tor connection and exit. No trade is placed (flag).

# ─────────────────────────────────────────────────────────────────────────────
# Standard-library
# ─────────────────────────────────────────────────────────────────────────────
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import List, Optional, Tuple
from urllib.parse import urlencode

# ─────────────────────────────────────────────────────────────────────────────
# Third-party  (pip install requests[socks] stem)
# ─────────────────────────────────────────────────────────────────────────────
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# stem is optional — gracefully disabled if not installed
try:
    from stem import Signal
    from stem.control import Controller

    STEM_AVAILABLE = True
except ImportError:
    STEM_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS  — always from environment variables, never hardcoded
# ══════════════════════════════════════════════════════════════════════════════
API_KEY: str = os.environ.get("BYBIT_API_KEY", "YOUR_BYBIT_API_KEY")
API_SECRET: str = os.environ.get("BYBIT_API_SECRET", "YOUR_BYBIT_API_SECRET")

MAINNET_URL: str = "https://api.bybit.com"
TESTNET_URL: str = "https://api-testnet.bybit.com"

RECV_WINDOW: int = 20_000  # ms — widened for Tor latency (was 10 000)

# ══════════════════════════════════════════════════════════════════════════════
# TOR CONFIGURATION  — all values overridable via environment variables
# ══════════════════════════════════════════════════════════════════════════════
TOR_SOCKS_HOST: str = os.environ.get("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT: int = int(os.environ.get("TOR_SOCKS_PORT", "9050"))
TOR_CTRL_HOST: str = os.environ.get("TOR_CTRL_HOST", "127.0.0.1")
TOR_CTRL_PORT: int = int(os.environ.get("TOR_CTRL_PORT", "9051"))

# socks5h:// — the trailing 'h' delegates DNS resolution to the Tor exit node.
# This prevents DNS leaks: your local resolver never sees api.bybit.com.
# Using plain socks5:// would resolve the hostname locally first, leaking
# the destination to your ISP before the connection enters the Tor circuit.
_SOCKS5H_BASE: str = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
TOR_PROXIES: dict = {
    "http": _SOCKS5H_BASE,
    "https": _SOCKS5H_BASE,
}

# Minimum wait between NEWNYM (circuit renewal) signals.
# Tor enforces a 10-second floor; we add 2 s of headroom.
MIN_NEWNYM_INTERVAL: float = float(os.environ.get("MIN_NEWNYM_INTERVAL", "12"))

# Tor-aware request timeouts — wider than the non-Tor defaults.
# Tor adds roughly 1–3 s of latency per hop; 3 hops = up to 9 s possible.
TOR_CONNECT_TIMEOUT: float = float(os.environ.get("TOR_CONNECT_TIMEOUT", "15"))
TOR_READ_TIMEOUT: float = float(os.environ.get("TOR_READ_TIMEOUT", "30"))
TOR_TIMEOUT: Tuple[float, float] = (TOR_CONNECT_TIMEOUT, TOR_READ_TIMEOUT)

# Validation sets
VALID_SIDES: frozenset = frozenset({"Buy", "Sell"})
VALID_CATEGORIES: frozenset = frozenset({"spot", "linear", "inverse"})
VALID_TIF: frozenset = frozenset({"GTC", "IOC", "FOK", "PostOnly"})
VALID_INTERVALS: frozenset = frozenset(
    {
        "1",
        "3",
        "5",
        "15",
        "30",
        "60",
        "120",
        "240",
        "360",
        "720",
        "D",
        "W",
        "M",
    }
)

# Neutral User-Agent — avoids leaking the requests library version string.
# A generic Firefox UA is indistinguishable from ordinary HTTPS traffic.
_TOR_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0"
)

# Symbol validation regex - prevents injection attacks
SYMBOL_PATTERN = re.compile(r"^[A-Z]{2,10}USDT?$")


# ══════════════════════════════════════════════════════════════════════════════
# TOR MANAGER — circuit lifecycle via stem
# ══════════════════════════════════════════════════════════════════════════════
class TorManager:
    """
    Manages the Tor control-port connection and circuit renewal.

    Uses cookie authentication (CookieAuthentication 1 in torrc) so no
    plaintext password is ever stored.  Falls back to no-auth for setups
    that allow unauthenticated control connections on localhost.

    Tor enforces a minimum of 10 seconds between NEWNYM signals.
    This class tracks the last renewal time and enforces MIN_NEWNYM_INTERVAL
    to avoid silent rejections of rapid renewal requests.
    """

    def __init__(self) -> None:
        self._last_newnym: float = 0.0
        self._controller: Optional[Controller] = None  # type: ignore[name-defined]
        self._available: bool = STEM_AVAILABLE

    def _get_controller(self) -> Optional["Controller"]:  # type: ignore[name-defined]
        """Open (or reuse) a stem Controller connected to the Tor control port."""
        if not self._available:
            return None
        try:
            if self._controller and self._controller.is_alive():
                return self._controller
            ctrl = Controller.from_port(
                address=TOR_CTRL_HOST,
                port=TOR_CTRL_PORT,
            )
            ctrl.authenticate()  # auto-detects cookie / no-auth
            self._controller = ctrl
            return ctrl
        except Exception as exc:
            logger.warning(
                "Cannot connect to Tor control port %s:%s — %s. "
                "Circuit renewal disabled.",
                TOR_CTRL_HOST,
                TOR_CTRL_PORT,
                exc,
            )
            self._available = False
            return None

    def renew_circuit(self, force: bool = False) -> bool:
        """
        Send NEWNYM to request a fresh Tor circuit.

        Returns True on success, False if unavailable or rate-limited.
        Tor itself enforces a 10-second minimum between NEWNYM signals;
        we gate locally to avoid silent drops.
        """
        now = time.monotonic()
        elapsed = now - self._last_newnym

        if not force and elapsed < MIN_NEWNYM_INTERVAL:
            wait = MIN_NEWNYM_INTERVAL - elapsed
            logger.info(
                "Circuit renewal rate-limited — %.1f s until next allowed NEWNYM.",
                wait,
            )
            return False

        ctrl = self._get_controller()
        if ctrl is None:
            return False

        try:
            ctrl.signal(Signal.NEWNYM)
            self._last_newnym = time.monotonic()
            logger.info("Tor circuit renewed via NEWNYM signal.")
            # Brief pause so Tor has time to build the new circuit
            time.sleep(1.5)
            return True
        except Exception as exc:
            logger.warning("NEWNYM signal failed: %s", exc)
            return False

    def bootstrap_status(self) -> Optional[int]:
        """
        Return the Tor bootstrap progress percentage (0–100).
        Returns None if the control port is unavailable.
        """
        ctrl = self._get_controller()
        if ctrl is None:
            return None
        try:
            info = ctrl.get_info("status/bootstrap-phase", default="")
            # e.g. "NOTICE BOOTSTRAP PROGRESS=100 TAG=done SUMMARY=Done"
            for part in info.split():
                if part.startswith("PROGRESS="):
                    return int(part.split("=")[1])
        except Exception:
            pass
        return None

    def is_bootstrapped(self) -> bool:
        """Return True when Tor reports 100 % bootstrap progress."""
        pct = self.bootstrap_status()
        if pct is None:
            # Control port unavailable — assume bootstrapped if SOCKS is reachable
            return _socks_port_open()
        return pct == 100

    def close(self) -> None:
        """Close the controller connection cleanly."""
        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None


# Module-level singleton — shared by all functions in this module
TOR = TorManager()


# ══════════════════════════════════════════════════════════════════════════════
# TOR UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════
def _socks_port_open() -> bool:
    """Return True if Tor's SOCKS port is accepting connections."""
    try:
        with socket.create_connection((TOR_SOCKS_HOST, TOR_SOCKS_PORT), timeout=5):
            return True
    except OSError:
        return False


def assert_tor_connection() -> str:
    """
    Verify that all HTTP traffic is actually exiting through Tor.

    Hits check.torproject.org/api/ip through the SOCKS5h proxy.
    Raises RuntimeError if the response does not confirm Tor usage.

    Returns the detected exit-node IP string (for logging — never store this).
    """
    if not _socks_port_open():
        raise RuntimeError(
            f"Tor SOCKS port {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT} is not reachable. "
            "Is Tor running?"
        )

    # Tor Project's own check endpoint — returns {"IsTor": true, "IP": "..."}
    check_url = "https://check.torproject.org/api/ip"
    try:
        resp = requests.get(
            check_url,
            proxies=TOR_PROXIES,
            timeout=TOR_TIMEOUT,
            headers={"User-Agent": _TOR_USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Tor check request failed: {exc}") from exc

    if not data.get("IsTor", False):
        raise RuntimeError(
            "Traffic is NOT routing through Tor. "
            f"Detected IP: {data.get('IP', 'unknown')}. "
            "Aborting to prevent identity exposure."
        )

    exit_ip = data.get("IP", "hidden")
    logger.info("Tor connection confirmed. Exit node IP: %s", exit_ip)
    return exit_ip


def wait_for_bootstrap(timeout_s: float = 120.0) -> None:
    """
    Block until Tor reports 100 % bootstrap or timeout_s elapses.

    Raises RuntimeError on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pct = TOR.bootstrap_status()
        if pct is None:
            # No control port — fall back to SOCKS reachability
            if _socks_port_open():
                logger.info("Tor SOCKS port is open; assuming bootstrapped.")
                return
        elif pct == 100:
            logger.info("Tor bootstrap complete (100 %%).")
            return
        else:
            logger.info("Tor bootstrapping: %d %% ...", pct)
        time.sleep(2)

    raise RuntimeError(f"Tor did not finish bootstrapping within {timeout_s} seconds.")


# ══════════════════════════════════════════════════════════════════════════════
# NETWORKING — Session with Tor proxies, retry, and User-Agent scrub
# ══════════════════════════════════════════════════════════════════════════════
def _build_tor_session(
    total_retries: int = 3, backoff_factor: float = 1.0
) -> requests.Session:
    """
    Build a requests.Session pre-configured for Tor.

    Key differences from the non-Tor session:
    - proxies = TOR_PROXIES  (socks5h:// — DNS resolved inside Tor)
    - User-Agent header replaced with a neutral Firefox string
    - backoff_factor increased to 1.0 (vs 0.5) to respect Tor circuit build times
    - status_forcelist includes 429 (Bybit rate-limit) and 5xx
    """
    session = requests.Session()

    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Attach Tor proxy to every request made through this session
    session.proxies.update(TOR_PROXIES)

    # Overwrite the default User-Agent so the requests library version
    # does not fingerprint this client
    session.headers.update({"User-Agent": _TOR_USER_AGENT})

    return session


SESSION: requests.Session = _build_tor_session()


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class InstrumentInfo:
    """Tick and lot-size constraints from /v5/market/instruments-info."""

    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    max_order_qty: Decimal
    min_notional: Decimal
    price_scale: int
    status: str


@dataclass
class Candle:
    """One OHLCV bar from /v5/market/kline."""

    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal


@dataclass
class OrderResult:
    """Structured order response — dot-access and mypy-friendly."""

    success: bool
    order_id: str = ""
    order_link_id: str = ""
    ret_code: int = 0
    ret_msg: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "orderId": self.order_id,
            "orderLinkId": self.order_link_id,
            "retCode": self.ret_code,
            "retMsg": self.ret_msg,
        }


# ══════════════════════════════════════════════════════════════════════════════
# PRECISION ENGINE
# ══════════════════════════════════════════════════════════════════════════════
def _decimal_places(step: Decimal) -> int:
    sign, digits, exponent = step.as_tuple()
    return max(0, -exponent)


def round_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    """Round value DOWN to the nearest exact multiple of tick."""
    if tick <= 0:
        raise ValueError(f"tick must be positive, got {tick}")
    places = _decimal_places(tick)
    quantizer = Decimal(10) ** -places
    floored = (value // tick) * tick
    return floored.quantize(quantizer, rounding=ROUND_DOWN)


def validate_notional(qty: Decimal, price: Decimal, min_notional: Decimal) -> None:
    """Raise ValueError if qty × price < min_notional."""
    notional = qty * price
    if notional < min_notional:
        raise ValueError(
            f"Notional {notional} is below the exchange minimum {min_notional}."
        )


# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ══════════════════════════════════════════════════════════════════════════════
def gen_signature(payload: str, timestamp: str) -> str:
    """HMAC-SHA256 for Bybit V5. Payload = JSON body (POST) or query string (GET)."""
    param_str = timestamp + API_KEY + str(RECV_WINDOW) + payload
    return hmac.new(
        bytes(API_SECRET, "utf-8"),
        param_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_auth_headers(payload: str, timestamp: str) -> dict:
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": gen_signature(payload, timestamp),
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RATE-LIMIT HEADER INSPECTION
# ══════════════════════════════════════════════════════════════════════════════
def _inspect_rate_limit_headers(response: requests.Response) -> None:
    limit = response.headers.get("X-Bapi-Limit", "?")
    limit_status = response.headers.get("X-Bapi-Limit-Status", "?")
    reset_ts = response.headers.get("X-Bapi-Limit-Reset-Timestamp", "?")
    retry_after = response.headers.get("Retry-After")
    logger.debug(
        "RateLimit — limit=%s  status=%s  reset_ts=%s",
        limit,
        limit_status,
        reset_ts,
    )
    if retry_after:
        logger.warning(
            "Retry-After header: wait %s s before next request.", retry_after
        )
    try:
        if limit_status != "?" and int(limit_status) < int(limit) * 0.10:
            logger.warning(
                "Rate-limit nearly exhausted: %s of %s remaining.",
                limit_status,
                limit,
            )
    except (ValueError, TypeError):
        pass


def _check_bybit_response(result: dict) -> None:
    ret_code = result.get("retCode", -1)
    ret_msg = result.get("retMsg", "unknown error")
    if ret_code != 0:
        logger.warning("Bybit API error — retCode=%s  retMsg=%s", ret_code, ret_msg)
    else:
        logger.info("Bybit API success — retCode=0  retMsg=%s", ret_msg)


# ══════════════════════════════════════════════════════════════════════════════
# SHARED HTTP HELPERS — with Tor-aware retry-on-circuit-error
# ══════════════════════════════════════════════════════════════════════════════
def _post(
    base_url: str,
    path: str,
    payload: str,
    _retry: bool = True,  # internal flag: retry once after circuit renewal
) -> Tuple[dict, requests.Response]:
    """
    Authenticated POST through the Tor session.

    On ConnectionError or Timeout, attempts one Tor circuit renewal then
    retries the request once.  This handles transient Tor circuit failures
    without surfacing them to callers.
    """
    timestamp = str(int(time.time() * 1000))
    headers = {
        **_build_auth_headers(payload, timestamp),
        "Content-Type": "application/json",
    }
    try:
        response = SESSION.post(
            f"{base_url}{path}",
            headers=headers,
            data=payload,
            timeout=TOR_TIMEOUT,
        )
        _inspect_rate_limit_headers(response)
        response.raise_for_status()
        return response.json(), response

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        if _retry:
            logger.warning(
                "Transient network error (%s). Renewing Tor circuit and retrying …", exc
            )
            TOR.renew_circuit()
            return _post(base_url, path, payload, _retry=False)
        raise


def _get_authed(
    base_url: str,
    path: str,
    params: dict,
    _retry: bool = True,
) -> Tuple[dict, requests.Response]:
    """
    Authenticated GET through the Tor session.

    Same circuit-renewal retry logic as _post().
    """
    query_string = urlencode(params)
    timestamp = str(int(time.time() * 1000))
    headers = {
        **_build_auth_headers(query_string, timestamp),
        "Content-Type": "application/json",
    }
    try:
        response = SESSION.get(
            f"{base_url}{path}",
            headers=headers,
            params=params,
            timeout=TOR_TIMEOUT,
        )
        _inspect_rate_limit_headers(response)
        response.raise_for_status()
        return response.json(), response

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        if _retry:
            logger.warning(
                "Transient network error (%s). Renewing Tor circuit and retrying …", exc
            )
            TOR.renew_circuit()
            return _get_authed(base_url, path, params, _retry=False)
        raise


def _parse_order_result(raw: dict) -> OrderResult:
    result_block = raw.get("result", {})
    ret_code = raw.get("retCode", -1)
    return OrderResult(
        success=(ret_code == 0),
        order_id=result_block.get("orderId", ""),
        order_link_id=result_block.get("orderLinkId", ""),
        ret_code=ret_code,
        ret_msg=raw.get("retMsg", ""),
        raw=raw,
    )


# ══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT INFO
# ══════════════════════════════════════════════════════════════════════════════
def get_instrument_info(
    symbol: str,
    category: str = "spot",
    testnet: bool = False,
) -> InstrumentInfo:
    """Fetch live tick / lot-size constraints from /v5/market/instruments-info."""
    base_url = TESTNET_URL if testnet else MAINNET_URL
    params = {"category": category, "symbol": symbol.upper()}
    try:
        response = SESSION.get(
            f"{base_url}/v5/market/instruments-info",
            params=params,
            timeout=TOR_TIMEOUT,
        )
        _inspect_rate_limit_headers(response)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Instrument info fetch failed: {exc}") from exc

    items = data.get("result", {}).get("list", [])
    if not items:
        raise ValueError(
            f"No instrument info for {symbol.upper()} in category '{category}'."
        )
    info = items[0]
    pf = info.get("priceFilter", {})
    lf = info.get("lotSizeFilter", {})
    return InstrumentInfo(
        tick_size=Decimal(pf.get("tickSize", "0.01")),
        qty_step=Decimal(lf.get("qtyStep", "0.001")),
        min_order_qty=Decimal(lf.get("minOrderQty", "0.001")),
        max_order_qty=Decimal(lf.get("maxOrderQty", "99999")),
        min_notional=Decimal(lf.get("minNotionalValue", "0") or "0"),
        price_scale=int(info.get("priceScale", "2")),
        status=info.get("status", "Unknown"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# INPUT VALIDATION
# ══════════════════════════════════════════════════════════════════════════════
def validate_symbol(symbol: str) -> str:
    """Validate symbol against strict regex pattern to prevent injection."""
    if not symbol:
        raise ValueError("Symbol cannot be empty.")
    symbol_upper = symbol.upper()
    if not SYMBOL_PATTERN.match(symbol_upper):
        raise ValueError(
            f"Invalid symbol '{symbol}'. Must match pattern ^[A-Z]{{2,10}}USDT?$"
        )
    return symbol_upper


def validate_inputs(symbol: str, side: str, qty: str, category: str) -> Decimal:
    """Validate core trading parameters. Returns qty as Decimal."""
    symbol_validated = validate_symbol(symbol)
    if side not in VALID_SIDES:
        raise ValueError(f"Invalid side '{side}'. Must be {sorted(VALID_SIDES)}.")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'.")
    try:
        qty_d = Decimal(str(qty))
    except InvalidOperation:
        raise ValueError(f"Invalid quantity '{qty}'.")
    if qty_d <= 0:
        raise ValueError(f"Quantity must be > 0; got {qty_d}.")
    return qty_d


# ══════════════════════════════════════════════════════════════════════════════
# MARKET ORDER
# ══════════════════════════════════════════════════════════════════════════════
def place_order(
    symbol: str,
    side: str,
    qty: str,
    category: str = "spot",
    testnet: bool = False,
) -> OrderResult:
    """Place a market order via Bybit V5 through the Tor-proxied session."""
    base_url = TESTNET_URL if testnet else MAINNET_URL

    try:
        symbol_validated = validate_symbol(symbol)
        qty_raw = validate_inputs(symbol, side, qty, category)
    except ValueError as exc:
        logger.error("Validation: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))

    try:
        info = get_instrument_info(symbol, category, testnet)
    except (RuntimeError, ValueError) as exc:
        logger.error("Instrument info: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))

    if info.status != "Trading":
        msg = f"{symbol.upper()} status='{info.status}' — not accepting orders."
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)

    qty_snapped = round_to_tick(qty_raw, info.qty_step)
    if qty_snapped != qty_raw:
        logger.info("qty snapped %s → %s", qty_raw, qty_snapped)
    if qty_snapped < info.min_order_qty:
        msg = f"qty {qty_snapped} < minOrderQty {info.min_order_qty}."
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)
    if qty_snapped > info.max_order_qty:
        msg = f"qty {qty_snapped} > maxOrderQty {info.max_order_qty}."
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)

    payload = json.dumps(
        {
            "category": category,
            "symbol": symbol_validated,
            "side": side.capitalize(),
            "orderType": "Market",
            "qty": str(qty_snapped),
        },
        separators=(",", ":"),
    )

    logger.info(
        "Market %s %s qty=%s cat=%s %s",
        side.capitalize(),
        symbol_validated,
        qty_snapped,
        category,
        "TESTNET" if testnet else "MAINNET",
    )

    try:
        raw, _ = _post(base_url, "/v5/order/create", payload)
        _check_bybit_response(raw)
        result = _parse_order_result(raw)
        if result.success:
            logger.info("Market order placed — orderId=%s", result.order_id)
        return result
    except requests.exceptions.Timeout:
        msg = "Request timed out (Tor circuit may be slow)."
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)
    except requests.exceptions.ConnectionError as exc:
        msg = f"Connection error (Tor circuit failed?): {exc}"
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)
    except requests.exceptions.HTTPError as exc:
        msg = f"HTTP error: {exc}"
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"Parse error: {exc}"
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# LIMIT ORDER
# ══════════════════════════════════════════════════════════════════════════════
def place_limit_order(
    symbol: str,
    side: str,
    qty: str,
    price: str,
    category: str = "spot",
    time_in_force: str = "GTC",
    testnet: bool = False,
) -> OrderResult:
    """Place a limit order with tick-snapping for both price and quantity."""
    base_url = TESTNET_URL if testnet else MAINNET_URL

    try:
        symbol_validated = validate_symbol(symbol)
        qty_raw = validate_inputs(symbol, side, qty, category)
    except ValueError as exc:
        logger.error("Validation: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))

    if time_in_force not in VALID_TIF:
        msg = f"Invalid timeInForce '{time_in_force}'."
        logger.error(msg)
        return OrderResult(success=False, ret_msg=msg)

    try:
        price_raw = Decimal(str(price))
    except InvalidOperation:
        return OrderResult(success=False, ret_msg=f"Invalid price '{price}'.")
    if price_raw <= 0:
        return OrderResult(
            success=False, ret_msg=f"Price must be > 0; got {price_raw}."
        )

    try:
        info = get_instrument_info(symbol, category, testnet)
    except (RuntimeError, ValueError) as exc:
        logger.error("Instrument info: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))

    if info.status != "Trading":
        msg = f"{symbol.upper()} status='{info.status}'."
        return OrderResult(success=False, ret_msg=msg)

    price_snapped = round_to_tick(price_raw, info.tick_size)
    qty_snapped = round_to_tick(qty_raw, info.qty_step)

    if price_snapped != price_raw:
        logger.info(
            "price snapped %s → %s (tick=%s)", price_raw, price_snapped, info.tick_size
        )
    if qty_snapped != qty_raw:
        logger.info(
            "qty snapped %s → %s (step=%s)", qty_raw, qty_snapped, info.qty_step
        )

    if qty_snapped < info.min_order_qty:
        return OrderResult(
            success=False,
            ret_msg=f"qty {qty_snapped} < minOrderQty {info.min_order_qty}.",
        )
    if qty_snapped > info.max_order_qty:
        return OrderResult(
            success=False,
            ret_msg=f"qty {qty_snapped} > maxOrderQty {info.max_order_qty}.",
        )

    if info.min_notional > 0:
        try:
            validate_notional(qty_snapped, price_snapped, info.min_notional)
        except ValueError as exc:
            logger.error(str(exc))
            return OrderResult(success=False, ret_msg=str(exc))

    payload = json.dumps(
        {
            "category": category,
            "symbol": symbol_validated,
            "side": side.capitalize(),
            "orderType": "Limit",
            "price": str(price_snapped),
            "qty": str(qty_snapped),
            "timeInForce": time_in_force,
        },
        separators=(",", ":"),
    )

    logger.info(
        "Limit %s %s qty=%s price=%s TIF=%s cat=%s %s",
        side.capitalize(),
        symbol_validated,
        qty_snapped,
        price_snapped,
        time_in_force,
        category,
        "TESTNET" if testnet else "MAINNET",
    )

    try:
        raw, _ = _post(base_url, "/v5/order/create", payload)
        _check_bybit_response(raw)
        result = _parse_order_result(raw)
        if result.success:
            logger.info("Limit order placed — orderId=%s", result.order_id)
        return result
    except requests.exceptions.Timeout:
        return OrderResult(success=False, ret_msg="Request timed out.")
    except requests.exceptions.ConnectionError as exc:
        return OrderResult(success=False, ret_msg=f"Connection error: {exc}")
    except requests.exceptions.HTTPError as exc:
        return OrderResult(success=False, ret_msg=f"HTTP error: {exc}")
    except (json.JSONDecodeError, ValueError) as exc:
        return OrderResult(success=False, ret_msg=f"Parse error: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return OrderResult(success=False, ret_msg=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# KLINE / OHLCV
# ══════════════════════════════════════════════════════════════════════════════
def get_kline(
    symbol: str,
    category: str = "spot",
    interval: str = "15",
    limit: int = 200,
    testnet: bool = False,
) -> List[Candle]:
    """Fetch OHLCV candles from /v5/market/kline. Returns chronological order."""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Invalid interval '{interval}'.")
    limit = max(1, min(limit, 1000))
    base_url = TESTNET_URL if testnet else MAINNET_URL
    params = {
        "category": category,
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": str(limit),
    }
    try:
        response = SESSION.get(
            f"{base_url}/v5/market/kline",
            params=params,
            timeout=TOR_TIMEOUT,
        )
        _inspect_rate_limit_headers(response)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Kline fetch failed: {exc}") from exc

    raw_list = data.get("result", {}).get("list", [])
    if not raw_list:
        raise ValueError(f"No kline data for {symbol.upper()}.")

    candles = [
        Candle(
            ts=int(row[0]),
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=Decimal(row[5]),
            turnover=Decimal(row[6]),
        )
        for row in raw_list
    ]
    candles.reverse()  # Bybit returns newest-first; we want chronological
    logger.info(
        "Fetched %d candles for %s (interval=%s)",
        len(candles),
        symbol.upper(),
        interval,
    )
    return candles


# ══════════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════════════════════
def calc_rsi(candles: List[Candle], period: int = 14) -> Optional[Decimal]:
    """Wilder-smoothed RSI. Overbought >70, Oversold <30."""
    if len(candles) < period + 1:
        return None
    closes = [c.close for c in candles]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else Decimal(0) for d in deltas]
    losses = [-d if d < 0 else Decimal(0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return (Decimal("100") - (Decimal("100") / (Decimal("1") + rs))).quantize(
        Decimal("0.01")
    )


def _ema(values: List[Decimal], period: int) -> List[Decimal]:
    """EMA — returns list same length as input (zero-padded at head)."""
    if len(values) < period:
        return []
    k = Decimal(2) / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return [Decimal(0)] * (period - 1) + result


def calc_macd(
    candles: List[Candle],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
    """MACD line, signal line, histogram. histogram>0 = bullish momentum."""
    if len(candles) < slow + signal_period:
        return None
    closes = [c.close for c in candles]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_series = [f - s for f, s in zip(ema_fast, ema_slow) if f != 0 and s != 0]
    if len(macd_series) < signal_period:
        return None
    sig_series = _ema(macd_series, signal_period)
    if not sig_series:
        return None
    m = macd_series[-1].quantize(Decimal("0.000001"))
    s = sig_series[-1].quantize(Decimal("0.000001"))
    h = (m - s).quantize(Decimal("0.000001"))
    return m, s, h


def calc_bollinger_bands(
    candles: List[Candle],
    period: int = 20,
    num_std: float = 2.0,
) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
    """Bollinger Bands: (upper, middle, lower)."""
    if len(candles) < period:
        return None
    recent = [c.close for c in candles[-period:]]
    mean = sum(recent) / period
    variance = sum((x - mean) ** 2 for x in recent) / period
    std_dev = variance.sqrt()
    mult = Decimal(str(num_std))
    q = Decimal("0.00001")
    return (
        (mean + mult * std_dev).quantize(q),
        mean.quantize(q),
        (mean - mult * std_dev).quantize(q),
    )


def calc_atr(candles: List[Candle], period: int = 14) -> Optional[Decimal]:
    """Average True Range (Wilder smoothing). Use for stop-loss sizing."""
    if len(candles) < period + 1:
        return None
    trs = [
        max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        for i in range(1, len(candles))
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr.quantize(Decimal("0.000001"))


def calc_vwap(candles: List[Candle]) -> Optional[Decimal]:
    """Session VWAP. price > VWAP = bullish intraday bias."""
    if not candles:
        return None
    pv = sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles)
    v = sum(c.volume for c in candles)
    return (pv / v).quantize(Decimal("0.00001")) if v else None


# ══════════════════════════════════════════════════════════════════════════════
# TA SIGNAL AGGREGATOR
# ══════════════════════════════════════════════════════════════════════════════
def get_ta_signal(candles: List[Candle]) -> dict:
    """
    Compute all indicators and emit a composite BUY / SELL / NEUTRAL signal.
    BUY  requires ≥ 2 of: RSI<35, MACD histogram>0, close<BB lower.
    SELL requires ≥ 2 of: RSI>65, MACD histogram<0, close>BB upper.
    """
    rsi = calc_rsi(candles)
    macd = calc_macd(candles)
    bb = calc_bollinger_bands(candles)
    atr = calc_atr(candles)
    vwap = calc_vwap(candles)

    last_close = candles[-1].close if candles else Decimal(0)
    macd_line = signal_line = histogram = None
    if macd:
        macd_line, signal_line, histogram = macd
    bb_upper = bb_mid = bb_lower = None
    if bb:
        bb_upper, bb_mid, bb_lower = bb

    buy_score = sum(
        [
            rsi is not None and rsi < 35,
            histogram is not None and histogram > 0,
            bb_lower is not None and last_close < bb_lower,
        ]
    )
    sell_score = sum(
        [
            rsi is not None and rsi > 65,
            histogram is not None and histogram < 0,
            bb_upper is not None and last_close > bb_upper,
        ]
    )

    signal = "BUY" if buy_score >= 2 else "SELL" if sell_score >= 2 else "NEUTRAL"

    return {
        "signal": signal,
        "buy_score": f"{buy_score}/3",
        "sell_score": f"{sell_score}/3",
        "last_close": str(last_close),
        "rsi": str(rsi) if rsi else "N/A",
        "macd": str(macd_line) if macd_line else "N/A",
        "macd_signal": str(signal_line) if signal_line else "N/A",
        "histogram": str(histogram) if histogram else "N/A",
        "bb_upper": str(bb_upper) if bb_upper else "N/A",
        "bb_mid": str(bb_mid) if bb_mid else "N/A",
        "bb_lower": str(bb_lower) if bb_lower else "N/A",
        "atr": str(atr) if atr else "N/A",
        "vwap": str(vwap) if vwap else "N/A",
        "price_vs_vwap": (
            "above"
            if vwap and last_close > vwap
            else "below"
            if vwap and last_close < vwap
            else "N/A"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# WALLET BALANCE
# ══════════════════════════════════════════════════════════════════════════════
def get_wallet_balance(
    account_type: str = "UNIFIED",
    coin: Optional[str] = None,
    testnet: bool = False,
) -> dict:
    """Fetch wallet balance from GET /v5/account/wallet-balance."""
    base_url = TESTNET_URL if testnet else MAINNET_URL
    params: dict = {"accountType": account_type.upper()}
    if coin:
        params["coin"] = coin.upper()
    logger.info(
        "Fetching wallet balance — accountType=%s%s  %s",
        account_type.upper(),
        f"  coin={coin.upper()}" if coin else "",
        "TESTNET" if testnet else "MAINNET",
    )
    try:
        raw, _ = _get_authed(base_url, "/v5/account/wallet-balance", params)
        _check_bybit_response(raw)
        return raw
    except requests.exceptions.Timeout:
        return {"error": "Wallet balance request timed out."}
    except requests.exceptions.ConnectionError as exc:
        return {"error": f"Connection error: {exc}"}
    except requests.exceptions.HTTPError as exc:
        return {"error": f"HTTP error: {exc}"}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"Parse error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return {"error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bybit V5 market/limit orders + TA signals via Tor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables
  BYBIT_API_KEY        Bybit API key (required)
  BYBIT_API_SECRET     Bybit API secret (required)
  TOR_SOCKS_HOST       Tor SOCKS host (default: 127.0.0.1)
  TOR_SOCKS_PORT       Tor SOCKS port (default: 9050)
  TOR_CTRL_HOST        Tor control host (default: 127.0.0.1)
  TOR_CTRL_PORT        Tor control port (default: 9051)
  MIN_NEWNYM_INTERVAL  Seconds between circuit renewals (default: 12)
  TOR_CONNECT_TIMEOUT  Connect timeout in seconds (default: 15)
  TOR_READ_TIMEOUT     Read timeout in seconds (default: 30)

Examples
  # Verify Tor and exit
  ./bybit_tor.sh --tor-check

  # Market buy on testnet
  ./bybit_tor.sh --symbol BTCUSDT --side Buy --qty 0.001 --testnet

  # Limit sell with circuit renewal + TA snapshot
  ./bybit_tor.sh --symbol ETHUSDT --side Sell --qty 0.1 \\
      --limit-price 2450.00 --renew-circuit --analyze

  # Balance check only
  ./bybit_tor.sh --balance --symbol BTCUSDT --side Buy --qty 0
        """,
    )

    parser.add_argument("--symbol", required=False, default=None)
    parser.add_argument(
        "--side", required=False, default=None, choices=["Buy", "Sell", "buy", "sell"]
    )
    parser.add_argument("--qty", required=False, default=None)
    parser.add_argument(
        "--category", default="spot", choices=["spot", "linear", "inverse"]
    )
    parser.add_argument("--limit-price", default=None)
    parser.add_argument(
        "--time-in-force", default="GTC", choices=["GTC", "IOC", "FOK", "PostOnly"]
    )
    parser.add_argument("--testnet", action="store_true", default=False)
    parser.add_argument("--balance", action="store_true", default=False)
    parser.add_argument("--analyze", action="store_true", default=False)
    parser.add_argument("--interval", default="15", choices=sorted(VALID_INTERVALS))
    parser.add_argument("--kline-limit", type=int, default=200)
    # Tor-specific flags
    parser.add_argument(
        "--renew-circuit",
        action="store_true",
        default=False,
        help="Renew the Tor circuit before any operation.",
    )
    parser.add_argument(
        "--tor-check",
        action="store_true",
        default=False,
        help="Verify Tor connection and exit (no trade placed).",
    )

    args = parser.parse_args()
    exit_code = 0

    # ── Step 0: Tor bootstrap check ───────────────────────────────────────────
    try:
        wait_for_bootstrap(timeout_s=120)
    except RuntimeError as exc:
        logger.error("Tor bootstrap failed: %s", exc)
        sys.exit(1)

    # ── Step 1: --tor-check (standalone, exits immediately) ───────────────────
    if args.tor_check:
        try:
            exit_ip = assert_tor_connection()
            print(json.dumps({"tor": True, "exit_ip": exit_ip}, indent=2))
            sys.exit(0)
        except RuntimeError as exc:
            print(json.dumps({"tor": False, "error": str(exc)}, indent=2))
            sys.exit(1)

    # ── Step 2: Verify Tor on every non-check invocation ─────────────────────
    try:
        assert_tor_connection()
    except RuntimeError as exc:
        logger.error("Tor verification failed: %s — aborting.", exc)
        sys.exit(1)

    # ── Step 3: Optional circuit renewal ─────────────────────────────────────
    if args.renew_circuit:
        if TOR.renew_circuit(force=True):
            logger.info("Fresh Tor circuit established.")
        else:
            logger.warning(
                "Circuit renewal unavailable; proceeding on existing circuit."
            )

    # ── Step 4: Wallet balance ────────────────────────────────────────────────
    if args.balance:
        balance_result = get_wallet_balance(testnet=args.testnet)
        print(json.dumps(balance_result, indent=2))
        print()

    # ── Step 5: TA snapshot ───────────────────────────────────────────────────
    if args.analyze and args.symbol:
        try:
            candles = get_kline(
                symbol=args.symbol,
                category=args.category,
                interval=args.interval,
                limit=args.kline_limit,
                testnet=args.testnet,
            )
            ta = get_ta_signal(candles)
            print("═" * 52)
            print(f"  TA SNAPSHOT  {args.symbol.upper()}  interval={args.interval}")
            print("═" * 52)
            for k, v in ta.items():
                print(f"  {k:<18} {v}")
            print("═" * 52)
            print()
        except (RuntimeError, ValueError) as exc:
            logger.error("TA analysis failed: %s", exc)

    # ── Step 6: Order placement (requires --symbol / --side / --qty) ──────────
    if args.symbol and args.side and args.qty:
        # Reject qty=0 explicitly
        try:
            qty_val = Decimal(str(args.qty))
            if qty_val <= 0:
                logger.error("Quantity must be > 0; got %s", qty_val)
                print(
                    json.dumps(
                        {"success": False, "retMsg": "Quantity must be > 0"}, indent=2
                    )
                )
                TOR.close()
                sys.exit(1)
        except InvalidOperation:
            pass

        if args.limit_price is not None:
            result = place_limit_order(
                symbol=args.symbol,
                side=args.side.capitalize(),
                qty=args.qty,
                price=args.limit_price,
                category=args.category,
                time_in_force=args.time_in_force,
                testnet=args.testnet,
            )
        else:
            result = place_order(
                symbol=args.symbol,
                side=args.side.capitalize(),
                qty=args.qty,
                category=args.category,
                testnet=args.testnet,
            )

        print(json.dumps(result.to_dict(), indent=2))

        if not result.success:
            exit_code = 1

    # ── Cleanup ───────────────────────────────────────────────────────────────
    TOR.close()
    sys.exit(exit_code)
