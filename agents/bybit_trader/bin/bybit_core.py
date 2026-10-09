#!/usr/bin/env python3
# ==============================================================================
# bybit_core.py — Pyrmethus AIChat Bybit Core V5 API Engine v2.4.1-ASCENDED
# Thread-Safe REST Client · Domain Failover · Proxy Integration · HMAC Signing
#
# @describe Core V5 API driver for Bybit with session pooling, HMAC signing,
#           failover domain routing, and automatic server time synchronization.
#
# @meta require-tools python3
#
# @option --action <ENUM>  Test action: health_check, server_time, config
#                          (default: health_check)
# @flag   --no-color       Disable ANSI color output
# @flag   --verbose        Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout   Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import random
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

# Import proxy_utils if present in the environment
try:
    import proxy_utils
except ImportError:
    proxy_utils = None

# Module-level logger — configure via BYBIT_CORE_LOG env var
log = logging.getLogger("bybit.core")
if not log.handlers and os.environ.get("BYBIT_CORE_LOG"):
    level_name = os.environ.get("BYBIT_CORE_LOG", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    log.addHandler(handler)

import requests

__version__ = "2.4.1-ASCENDED"
__all__ = [
    "__version__",
    "amend_order",
    "api_base",
    "api_request",
    "cancel_order",
    "from_int_price",
    "get_config",
    "get_instruments_info",
    "get_orderbook",
    "get_positions",
    "get_ticker",
    "health_check",
    "is_rate_limited",
    "is_testnet",
    "reset_session",
    "server_time",
    "set_account_margin_mode",
    "switch_cross_isolated",
    "switch_position_mode",
    "sync_server_time_offset",
    "to_int_price",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_RECV_WINDOW_DEFAULT = 20_000
_RECV_WINDOW_MAX = 60_000
_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5

_BASE_URL_MAINNET = "https://api.bybit.com"
_BASE_URL_BACKUP = "https://api.bytick.com"
_BASE_URL_TESTNET = "https://api-testnet.bybit.com"

_DEFAULT_PRICE_SCALE = 3

# HTTP status codes that warrant a retry
_RETRY_STATUSES = frozenset(
    {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
        520,
        521,
        522,
        523,
        524,
        525,
        526,
        527,
        528,
        529,
        530,
    }
)

# Bybit retCodes that are transient and may succeed on retry
_RETRYABLE_RET_CODES = frozenset(
    {
        10006,
        10018,
        130006,
        130018,
        130021,
        130029,
        130105,
        130106,
        131203,
    }
)

# Bybit retCodes that are permanent — never retry
_PERMANENT_RET_CODES = frozenset(
    {
        10001,
        10002,
        10003,
        10004,
        10005,
        10007,
        10010,
        10017,
    }
)

_OK_RET_CODES = frozenset({0})

# Minimum backoff floor (seconds) — avoids zero-delay hammering on attempt 0
_BACKOFF_FLOOR = 0.05

# Config cache TTL (seconds) — avoids repeated os.environ reads on hot paths
_CONFIG_CACHE_TTL = 5.0

# Threading primitives
_SESSION_LOCK = threading.Lock()
_TIME_LOCK = threading.RLock()  # Re-entrant to allow nested reads
_CONFIG_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Configuration Engine (with TTL cache)
# ---------------------------------------------------------------------------
_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_CACHE_TS: float = 0.0


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    """
    Return the runtime configuration dict.

    Re-reads environment variables at most every _CONFIG_CACHE_TTL seconds
    to reduce overhead on hot API paths. Pass force_refresh=True to bypass.
    """
    global _CONFIG_CACHE, _CONFIG_CACHE_TS

    now = time.monotonic()
    if (
        not force_refresh
        and _CONFIG_CACHE is not None
        and (now - _CONFIG_CACHE_TS) < _CONFIG_CACHE_TTL
    ):
        return _CONFIG_CACHE

    with _CONFIG_LOCK:
        # Double-check after acquiring lock
        now = time.monotonic()
        if (
            not force_refresh
            and _CONFIG_CACHE is not None
            and (now - _CONFIG_CACHE_TS) < _CONFIG_CACHE_TTL
        ):
            return _CONFIG_CACHE

        testnet = os.environ.get("BYBIT_TESTNET", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        base_url = _BASE_URL_TESTNET if testnet else _BASE_URL_MAINNET

        use_proxy = (
            os.environ.get("BYBIT_USE_TOR", "false").lower() in ("1", "true", "yes")
            or os.environ.get("PROXY_ENABLED", "false").lower() in ("1", "true", "yes")
            or os.environ.get("BYBIT_USE_PROXY", "false").lower()
            in ("1", "true", "yes")
        )

        proxies: dict[str, str] | None = None
        proxy_url = ""

        if use_proxy:
            if proxy_utils:
                proxies = proxy_utils.get_proxies()
                proxy_url = (proxies or {}).get("https", "")
            else:
                proxy_url = os.environ.get(
                    "BYBIT_PROXY_URL",
                    os.environ.get("PROXY_HOST", "socks5h://127.0.0.1:9050"),
                )
                # FIX: Hardened proxy URL formatting to prevent double ports
                if not proxy_url.startswith(("socks", "http")):
                    if ":" in proxy_url:
                        proxy_url = f"socks5h://{proxy_url}"
                    else:
                        proxy_url = f"socks5h://{proxy_url}:9050"
                proxies = {"http": proxy_url, "https": proxy_url}

        try:
            timeout = float(os.environ.get("BYBIT_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT

        try:
            recv_window = int(
                os.environ.get("BYBIT_RECV_WINDOW", str(_RECV_WINDOW_DEFAULT))
            )
        except (TypeError, ValueError):
            recv_window = _RECV_WINDOW_DEFAULT
        recv_window = max(1, min(_RECV_WINDOW_MAX, recv_window))

        try:
            max_retries = int(os.environ.get("BYBIT_MAX_RETRIES", str(_MAX_RETRIES)))
        except (TypeError, ValueError):
            max_retries = _MAX_RETRIES

        try:
            price_scale = int(
                os.environ.get("BYBIT_PRICE_SCALE", str(_DEFAULT_PRICE_SCALE))
            )
        except (TypeError, ValueError):
            price_scale = _DEFAULT_PRICE_SCALE

        k_raw = os.environ.get("BYBIT_API_KEY", "").strip()
        s_raw = os.environ.get("BYBIT_API_SECRET", "").strip()
        if len(k_raw) > len(s_raw) and len(k_raw) > 25 and len(s_raw) < 25:
            k_raw, s_raw = s_raw, k_raw

        cfg: dict[str, Any] = {
            "api_key": k_raw,
            "api_secret": s_raw,
            "base_url": base_url,
            "backup_base_url": (_BASE_URL_BACKUP if not testnet else _BASE_URL_TESTNET),
            "testnet": testnet,
            "use_proxy": use_proxy,
            "proxy_url": proxy_url,
            "proxies": proxies,
            "timeout": timeout,
            "recv_window": recv_window,
            "sign_type": os.environ.get("BYBIT_SIGN_TYPE", "2"),
            "max_retries": max(0, max_retries),
            "price_scale": max(0, price_scale),
            "auto_int_prices": os.environ.get("BYBIT_AUTO_INT_PRICES", "false").lower()
            in ("1", "true", "yes"),
            "use_server_time": os.environ.get("BYBIT_USE_SERVER_TIME", "true").lower()
            in ("1", "true", "yes"),
        }

        _CONFIG_CACHE = cfg
        _CONFIG_CACHE_TS = time.monotonic()
        return cfg


def api_base() -> str:
    """Return current API base URL."""
    return get_config()["base_url"]


def is_testnet() -> bool:
    """Return True if operating in testnet environment."""
    return get_config()["testnet"]


# ---------------------------------------------------------------------------
# Session Management & Connection Pooling
# ---------------------------------------------------------------------------
_SESSION: requests.Session | None = None
_SESSION_PROXY_KEY: str | None = None


def _build_session() -> requests.Session:
    """Construct a requests.Session with an optimized connection pool adapter."""
    sess = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20,
        pool_maxsize=50,
        pool_block=False,
    )
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(
        {
            "User-Agent": f"bybit-core/{__version__} (+python-requests)",
            "Accept": "application/json",
        }
    )
    return sess


def _get_session() -> requests.Session:
    """Return (or build) the module-level cached session, rebuilding on proxy change."""
    global _SESSION, _SESSION_PROXY_KEY
    cfg = get_config()
    proxy_key = f"{cfg['use_proxy']}:{cfg.get('proxy_url', '')}"

    # Fast path — no lock needed when session is valid
    if _SESSION is not None and _SESSION_PROXY_KEY == proxy_key:
        return _SESSION

    with _SESSION_LOCK:
        # Re-check inside lock (double-checked locking)
        if _SESSION is None or _SESSION_PROXY_KEY != proxy_key:
            _reset_session_unlocked()
            _SESSION = _build_session()
            _SESSION_PROXY_KEY = proxy_key
    return _SESSION


def _reset_session_unlocked() -> None:
    """Close and clear the session. Must be called while holding _SESSION_LOCK."""
    global _SESSION, _SESSION_PROXY_KEY
    if _SESSION is not None:
        try:
            _SESSION.close()
        except Exception:
            pass
        _SESSION = None
    _SESSION_PROXY_KEY = None


def reset_session() -> None:
    """Publicly close and drop the cached Session instance."""
    with _SESSION_LOCK:
        _reset_session_unlocked()


# ---------------------------------------------------------------------------
# Price Scaling Helpers
# ---------------------------------------------------------------------------


def to_int_price(price: Any, scale: int | None = None) -> str:
    """
    Convert a decimal price to a Bybit integer-price string.

    FIX: Uses Decimal arithmetic to prevent binary float drift
    (e.g. 0.1 + 0.2 = 0.30000000000000004) which causes API rejections.
    """
    if scale is None:
        scale = get_config().get("price_scale", _DEFAULT_PRICE_SCALE)
    scale = max(0, int(scale))
    try:
        d_price = Decimal(str(price))
        scaled = int(d_price * (10**scale))
        return str(scaled)
    except (TypeError, ValueError, InvalidOperation):
        return str(price)


def from_int_price(int_price: Any, scale: int | None = None) -> float:
    """Convert a Bybit integer-price string back to a float."""
    if scale is None:
        scale = get_config().get("price_scale", _DEFAULT_PRICE_SCALE)
    scale = max(0, int(scale))
    try:
        return float(Decimal(str(int_price)) / (10**scale))
    except (TypeError, ValueError, InvalidOperation):
        return 0.0


# ---------------------------------------------------------------------------
# Signing & Parameter Serialization
# ---------------------------------------------------------------------------


def _stringify_param(v: Any) -> str:
    """
    Convert API parameter values to strings without binary float drift.
    dict values are JSON-serialized for composite filter params.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (float, Decimal)):
        try:
            return format(Decimal(str(v)), "f")
        except (InvalidOperation, ValueError):
            return str(v)
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


def _sorted_query_string(params: Mapping[str, Any]) -> str:
    """Build a deterministically sorted query string for HMAC signing."""
    items = [
        (k, _stringify_param(v)) for k, v in sorted(params.items()) if v is not None
    ]
    return urllib.parse.urlencode(items)


def _sign(cfg: dict[str, Any], ts: str, payload: str) -> str:
    """
    Calculate Bybit V5 HMAC-SHA256 signature.

    Signature string: {timestamp}{api_key}{recv_window}{payload}
    The recv_window in the message must match the header value exactly.
    """
    msg = (
        f"{ts}"
        f"{cfg['api_key']}"
        f"{cfg['recv_window']}"  # int — str(int) matches header str(recv_window)
        f"{payload}"
    )
    return hmac.new(
        cfg["api_secret"].encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _default_price_fields() -> list[str] | None:
    if not get_config().get("auto_int_prices"):
        return None
    return [
        "price",
        "stopLoss",
        "takeProfit",
        "activePrice",
        "trailingStop",
        "tpLimitPrice",
        "slLimitPrice",
        "triggerPrice",
    ]


# ---------------------------------------------------------------------------
# Server Time & Clock Synchronization
# ---------------------------------------------------------------------------
_SERVER_TIME_OFFSET_MS: int | None = None
# FIX: Replaced threading.Event with RLock.
# Previously, if Thread A was syncing, Thread B would see the Event set,
# skip syncing, and return None (causing auth failures).
# Now, Thread B safely waits for Thread A to finish and reads the updated offset.
_SYNCING_SERVER_TIME = threading.RLock()


def server_time() -> int | None:
    """
    Return Bybit server timestamp in milliseconds.

    Uses a direct session call (bypassing _timestamp_ms) to break the
    mutual recursion with sync_server_time_offset → api_request → _timestamp_ms
    → sync_server_time_offset that could cause infinite recursion on first call.
    """
    cfg = get_config()
    proxies = cfg.get("proxies")
    timeout = cfg.get("timeout", _DEFAULT_TIMEOUT)

    base_urls = [cfg["base_url"]]
    if not cfg.get("testnet") and cfg.get("backup_base_url"):
        base_urls.append(cfg["backup_base_url"])

    for attempt in range(3):
        target_base = base_urls[attempt % len(base_urls)]
        url = f"{target_base}/v5/market/time"
        try:
            sess = _get_session()
            resp = sess.get(url, proxies=proxies, timeout=timeout)
            if resp.status_code != 200:
                log.debug("server_time HTTP %s: %s", resp.status_code, resp.text[:128])
                time.sleep(0.1)
                continue
            data = resp.json()
            if data.get("retCode") != 0:
                time.sleep(0.1)
                continue

            # FIX: Use the root 'time' field which is the exact server ms timestamp.
            # Fallback to timeNano if root time is missing.
            raw_ms = data.get("time")
            if raw_ms is not None:
                return int(raw_ms)

            r = data.get("result") or {}
            raw_nano = r.get("timeNano")
            if raw_nano is not None:
                return int(int(raw_nano) / 1_000_000)

        except Exception as exc:
            log.debug("server_time() request failed: %s", exc)
            time.sleep(0.1)

    return None


def sync_server_time_offset() -> int | None:
    """
    Measure and cache the offset between Bybit server clock and local clock.

    Returns offset_ms (server_ms - local_ms), or the last cached value on
    failure. Thread-safe; re-entrant calls on the same thread are allowed,
    while concurrent calls from other threads will block and wait for the
    sync to complete.
    """
    global _SERVER_TIME_OFFSET_MS

    # Try to acquire without blocking to check if another thread is syncing
    if not _SYNCING_SERVER_TIME.acquire(blocking=False):
        # Another thread is syncing, wait for it to finish
        log.debug(
            "sync_server_time_offset(): waiting for another thread to finish syncing."
        )
        _SYNCING_SERVER_TIME.acquire(blocking=True)
        _SYNCING_SERVER_TIME.release()
        return _SERVER_TIME_OFFSET_MS

    try:
        local_before = int(time.time() * 1000)
        st = server_time()
        local_after = int(time.time() * 1000)

        if st is None:
            log.warning("Could not sync server time; server_time() returned None.")
            return _SERVER_TIME_OFFSET_MS

        # Use midpoint of local timestamps to estimate round-trip latency
        local_mid = (local_before + local_after) // 2
        offset = st - local_mid

        with _TIME_LOCK:
            _SERVER_TIME_OFFSET_MS = offset

        log.debug(
            "Server time synced: server=%d local_mid=%d offset=%+d ms (rtt=%d ms)",
            st,
            local_mid,
            offset,
            local_after - local_before,
        )
        return offset
    finally:
        _SYNCING_SERVER_TIME.release()


def _timestamp_ms(cfg: dict[str, Any]) -> str:
    """
    Return the best available timestamp string in milliseconds.

    Reads _SERVER_TIME_OFFSET_MS under lock to avoid torn reads.
    Triggers a background-safe sync if the offset is not yet initialized.
    """
    if cfg.get("use_server_time"):
        with _TIME_LOCK:
            offset = _SERVER_TIME_OFFSET_MS

        if offset is None:
            # First call: sync now (re-entrancy guard in sync_server_time_offset
            # prevents recursion if we arrive here from within server_time())
            sync_server_time_offset()
            with _TIME_LOCK:
                offset = _SERVER_TIME_OFFSET_MS

        if offset is not None:
            return str(int(time.time() * 1000) + offset)

    return str(int(time.time() * 1000))


def health_check() -> bool:
    """Return True if the Bybit public API is reachable and responding."""
    result = api_request("GET", "/v5/market/time", signed=False, retries=1)
    return result.get("retCode") == 0


def is_rate_limited(response: Mapping[str, Any]) -> bool:
    """Return True if the response indicates a rate-limit condition."""
    return response.get("retCode") in _RETRYABLE_RET_CODES


# ---------------------------------------------------------------------------
# Backoff Engine
# ---------------------------------------------------------------------------


def _sleep_backoff(attempt: int) -> None:
    """
    Exponential backoff with full jitter.
    """
    base = min(30.0, (2**attempt) * 0.5)
    delay = max(_BACKOFF_FLOOR, random.uniform(0, base))
    log.debug("Backoff: attempt=%d sleeping=%.3fs", attempt, delay)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Core API Request Function
# ---------------------------------------------------------------------------


def api_request(
    method: str,
    endpoint: str,
    params: Mapping[str, Any] | None = None,
    signed: bool = False,
    *,
    timeout: float | None = None,
    recv_window: int | None = None,
    sign_type: str | None = None,
    retries: int | None = None,
    price_scale: int | None = None,
    price_fields: list[str] | bool | None = None,
) -> dict[str, Any]:
    """
    Execute a Bybit V5 REST API request with HMAC signing, retry logic,
    domain failover, and optional integer-price scaling.
    """
    # --- Input validation --------------------------------------------------
    if not isinstance(method, str):
        return {
            "retCode": -1,
            "retMsg": f"method must be str, got {type(method).__name__}",
        }
    method = method.upper()
    if method not in ("GET", "POST", "PUT", "DELETE"):
        return {"retCode": -1, "retMsg": f"Unsupported HTTP method: {method}"}

    if not isinstance(endpoint, str) or not endpoint:
        return {"retCode": -1, "retMsg": "endpoint must be a non-empty string"}

    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    # --- Config resolution -------------------------------------------------
    cfg = get_config()

    if recv_window is not None:
        try:
            cfg = dict(cfg)
            cfg["recv_window"] = max(1, min(_RECV_WINDOW_MAX, int(recv_window)))
        except (TypeError, ValueError):
            pass

    sign_type = sign_type or cfg.get("sign_type", "2")
    retries = (
        retries if retries is not None else int(cfg.get("max_retries", _MAX_RETRIES))
    )
    timeout = timeout if timeout is not None else cfg.get("timeout", _DEFAULT_TIMEOUT)
    price_scale = (
        price_scale
        if price_scale is not None
        else int(cfg.get("price_scale", _DEFAULT_PRICE_SCALE))
    )

    if price_fields is True:
        price_fields = _default_price_fields()
    elif price_fields is None and cfg.get("auto_int_prices"):
        price_fields = _default_price_fields()

    # --- Auth guard --------------------------------------------------------
    if signed and (not cfg["api_key"] or not cfg["api_secret"]):
        return {
            "retCode": 10002,
            "retMsg": ("Missing BYBIT_API_KEY / BYBIT_API_SECRET in environment"),
        }

    # --- Optional integer price scaling ------------------------------------
    if params and price_fields and price_scale > 0:
        scaled: dict[str, Any] = {}
        for k, v in params.items():
            if k in price_fields and v not in (None, ""):
                scaled[k] = to_int_price(v, price_scale)
            else:
                scaled[k] = v
        params = scaled

    # --- Request construction ----------------------------------------------
    body_str: str | None = None
    query_str: str = ""

    if method == "GET":
        query_str = _sorted_query_string(params or {})
        path_url = f"{endpoint}?{query_str}" if query_str else endpoint
    else:
        body_str = json.dumps(params or {}, separators=(",", ":"), ensure_ascii=False)
        path_url = endpoint

    headers: dict[str, str] = {"X-BAPI-SIGN-TYPE": str(sign_type)}
    if method != "GET":
        headers["Content-Type"] = "application/json"

    if signed:
        ts = _timestamp_ms(cfg)
        payload = body_str if method != "GET" else query_str
        sig = _sign(cfg, ts, payload or "")
        headers.update(
            {
                "X-BAPI-API-KEY": cfg["api_key"],
                "X-BAPI-SIGN": sig,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": str(cfg["recv_window"]),
            }
        )

    sess = _get_session()
    proxies = cfg["proxies"]

    last_resp: dict[str, Any] = {"retCode": -1, "retMsg": "no response"}

    # Build failover domain list (testnet has no separate backup)
    base_urls: list[str] = [cfg["base_url"]]
    if not cfg["testnet"] and cfg.get("backup_base_url"):
        base_urls.append(cfg["backup_base_url"])

    for attempt in range(retries + 1):
        domain_idx = 0 if attempt == 0 else (attempt % len(base_urls))
        target_base = base_urls[domain_idx]
        full_url = f"{target_base}{path_url}"

        t_start = time.monotonic()
        try:
            if method == "GET":
                resp = sess.get(
                    full_url,
                    headers=headers,
                    proxies=proxies,
                    timeout=timeout,
                )
            else:
                resp = sess.request(
                    method,
                    full_url,
                    data=body_str,
                    headers=headers,
                    proxies=proxies,
                    timeout=timeout,
                )

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            log.debug(
                "%s %s → HTTP %s (%d ms)",
                method,
                full_url,
                resp.status_code,
                elapsed_ms,
            )

            # Introspect rate-limit headers for observability
            remaining = resp.headers.get("X-Bapi-Limit-Status")
            if remaining is not None:
                log.debug("Rate limit remaining: %s", remaining)

            # --- Non-200 HTTP response handling ----------------------------
            if resp.status_code != 200:
                last_resp = {
                    "retCode": resp.status_code,
                    "retMsg": f"HTTP {resp.status_code}: {resp.text[:512]}",
                }
                if resp.status_code in _RETRY_STATUSES and attempt < retries:
                    log.warning(
                        "HTTP %s on %s %s (attempt %d/%d)",
                        resp.status_code,
                        method,
                        full_url,
                        attempt + 1,
                        retries,
                    )
                    _sleep_backoff(attempt)
                    continue
                return last_resp

            # --- JSON decode -----------------------------------------------
            try:
                last_resp = resp.json()
            except ValueError:
                return {
                    "retCode": -1,
                    "retMsg": (
                        f"Non-JSON response (HTTP {resp.status_code}): "
                        f"{resp.text[:512]}"
                    ),
                }

            rc = last_resp.get("retCode")

            # Permanent errors — return immediately, never retry
            if rc in _PERMANENT_RET_CODES:
                log.debug("Permanent retCode %s — not retrying.", rc)
                return last_resp

            # Transient errors — retry with backoff
            if rc in _RETRYABLE_RET_CODES and attempt < retries:
                log.warning(
                    "Bybit retCode %s on %s %s (attempt %d/%d)",
                    rc,
                    method,
                    full_url,
                    attempt + 1,
                    retries,
                )
                # Re-sync clock on timestamp errors
                if rc in (10002, 10006):
                    sync_server_time_offset()
                _sleep_backoff(attempt)
                continue

            return last_resp

        except requests.exceptions.ProxyError as exc:
            last_resp = {"retCode": -1, "retMsg": f"Proxy error: {exc}"}
            log.error("Proxy error on %s %s: %s", method, full_url, exc)
            if attempt < min(retries, 2):
                _sleep_backoff(attempt)
                continue
            return last_resp

        except requests.exceptions.SSLError as exc:
            log.error("SSL error on %s %s: %s", method, full_url, exc)
            return {"retCode": -1, "retMsg": f"SSL error: {exc}"}

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            last_resp = {"retCode": -1, "retMsg": f"Network error: {exc}"}
            log.warning(
                "Network error on %s %s (attempt %d/%d): %s",
                method,
                full_url,
                attempt + 1,
                retries,
                exc,
            )
            if attempt < retries:
                _sleep_backoff(attempt)
                continue
            return last_resp

        except requests.exceptions.RequestException as exc:
            log.error("Unhandled request error on %s %s: %s", method, full_url, exc)
            return {"retCode": -1, "retMsg": f"Request error: {exc}"}

    return last_resp


# ---------------------------------------------------------------------------
# Position & Margin Management Helpers (V5)
# ---------------------------------------------------------------------------


def switch_position_mode(
    category: str,
    mode: int,
    symbol: str | None = None,
    coin: str | None = None,
) -> dict[str, Any]:
    """POST /v5/position/switch-mode"""
    body: dict[str, Any] = {"category": category, "mode": mode}
    if symbol:
        body["symbol"] = symbol
    if coin:
        body["coin"] = coin
    return api_request("POST", "/v5/position/switch-mode", params=body, signed=True)


def switch_cross_isolated(
    category: str,
    symbol: str,
    trade_mode: int,
    buy_leverage: str | None = None,
    sell_leverage: str | None = None,
) -> dict[str, Any]:
    """POST /v5/position/switch-isolated"""
    body: dict[str, Any] = {
        "category": category,
        "symbol": symbol,
        "tradeMode": trade_mode,
    }
    if buy_leverage is not None:
        body["buyLeverage"] = str(buy_leverage)
    if sell_leverage is not None:
        body["sellLeverage"] = str(sell_leverage)
    return api_request("POST", "/v5/position/switch-isolated", params=body, signed=True)


def set_account_margin_mode(set_margin_mode: str) -> dict[str, Any]:
    """POST /v5/account/set-margin-mode"""
    return api_request(
        "POST",
        "/v5/account/set-margin-mode",
        params={"setMarginMode": set_margin_mode},
        signed=True,
    )


# ---------------------------------------------------------------------------
# Market Data Convenience Wrappers
# ---------------------------------------------------------------------------


def get_instruments_info(
    category: str = "linear",
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """GET /v5/market/instruments-info"""
    p: dict[str, Any] = {"category": category, "limit": limit}
    if symbol:
        p["symbol"] = symbol
    if status:
        p["status"] = status
    return api_request("GET", "/v5/market/instruments-info", params=p)


def get_ticker(
    symbol: str,
    category: str = "linear",
) -> dict[str, Any]:
    """GET /v5/market/tickers"""
    return api_request(
        "GET",
        "/v5/market/tickers",
        params={"category": category, "symbol": symbol},
    )


def get_orderbook(
    symbol: str,
    category: str = "linear",
    limit: int = 50,
) -> dict[str, Any]:
    """GET /v5/market/orderbook"""
    return api_request(
        "GET",
        "/v5/market/orderbook",
        params={"category": category, "symbol": symbol, "limit": limit},
    )


# ---------------------------------------------------------------------------
# Account & Order Management Convenience Wrappers
# ---------------------------------------------------------------------------


def get_positions(
    category: str = "linear",
    symbol: str | None = None,
    settle_coin: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """GET /v5/position/list"""
    p: dict[str, Any] = {"category": category, "limit": limit}
    if symbol:
        p["symbol"] = symbol
    if settle_coin:
        p["settleCoin"] = settle_coin
    return api_request("GET", "/v5/position/list", params=p, signed=True)


def cancel_order(
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
) -> dict[str, Any]:
    """POST /v5/order/cancel"""
    if not order_id and not order_link_id:
        return {
            "retCode": -1,
            "retMsg": "cancel_order requires order_id or order_link_id",
        }
    body: dict[str, Any] = {"category": category, "symbol": symbol}
    if order_id:
        body["orderId"] = order_id
    if order_link_id:
        body["orderLinkId"] = order_link_id
    return api_request("POST", "/v5/order/cancel", params=body, signed=True)


def amend_order(
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
    qty: str | None = None,
    price: str | None = None,
    trigger_price: str | None = None,
    stop_loss: str | None = None,
    take_profit: str | None = None,
) -> dict[str, Any]:
    """POST /v5/order/amend"""
    if not order_id and not order_link_id:
        return {
            "retCode": -1,
            "retMsg": "amend_order requires order_id or order_link_id",
        }
    body: dict[str, Any] = {"category": category, "symbol": symbol}
    if order_id:
        body["orderId"] = order_id
    if order_link_id:
        body["orderLinkId"] = order_link_id
    if qty is not None:
        body["qty"] = qty
    if price is not None:
        body["price"] = price
    if trigger_price is not None:
        body["triggerPrice"] = trigger_price
    if stop_loss is not None:
        body["stopLoss"] = stop_loss
    if take_profit is not None:
        body["takeProfit"] = take_profit
    return api_request("POST", "/v5/order/amend", params=body, signed=True)


# ---------------------------------------------------------------------------
# Standalone CLI Test Execution
# ---------------------------------------------------------------------------

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _is_tty() -> bool:
    if not sys.stderr.isatty():
        return False
    return os.environ.get("TERM", "xterm").lower() != "dumb"


def _c(text: str, no_color: bool = False) -> str:
    """Strip ANSI codes when color is disabled or output is not a TTY."""
    if no_color or not _is_tty():
        return re.sub(r"\033\[[0-9;]*m", "", text)
    return text


def run(
    action: str = "health_check", no_color: bool = False, verbose: bool = False
) -> str:
    """Run Bybit Core API test checks.

    Args:
        action: Test action: health_check, server_time, config (default: health_check)
        no_color: Disable ANSI color output
        verbose: Enable detailed debug log output
    """
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )

    if action == "health_check":
        healthy = health_check()
        status = "online" if healthy else "offline"
        return json.dumps({"success": healthy, "status": status}, indent=2)
    elif action == "server_time":
        st = server_time()
        offset = sync_server_time_offset()
        utc_dt = (
            datetime.fromtimestamp(st / 1000, tz=timezone.utc).isoformat()
            if st is not None
            else None
        )
        return json.dumps(
            {
                "server_time_ms": st,
                "server_time_utc": utc_dt,
                "offset_ms": offset,
            },
            indent=2,
        )
    elif action == "config":
        cfg = dict(get_config())
        cfg["api_key"] = f"{cfg['api_key'][:4]}***" if cfg["api_key"] else "NOT_SET"
        cfg["api_secret"] = "***REDACTED***" if cfg["api_secret"] else "NOT_SET"
        return json.dumps(cfg, indent=2)
    return json.dumps({"error": f"Invalid action: {action}"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="bybit_core.py",
        description=f"Bybit Core API Engine v{__version__}",
    )
    parser.add_argument(
        "--action",
        default="health_check",
        choices=["health_check", "server_time", "config"],
        help="Test action to perform (default: health_check)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed debug log output",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )

    no_color: bool = args.no_color

    if args.action == "health_check":
        healthy = health_check()
        status = "online" if healthy else "offline"
        color = NEON_GREEN if healthy else NEON_RED
        sys.stderr.write(
            _c(f"{color}{BOLD}Bybit API: {status.upper()}{RESET}\n", no_color)
        )
        print(json.dumps({"success": healthy, "status": status}, indent=2))

    elif args.action == "server_time":
        st = server_time()
        offset = sync_server_time_offset()
        utc_dt = (
            datetime.fromtimestamp(st / 1000, tz=timezone.utc).isoformat()
            if st is not None
            else None
        )

        # FIX: Cleaned up ternary operator precedence for predictable formatting
        if offset is not None:
            msg = f"{NEON_CYAN}Server time:{RESET} {utc_dt or 'unavailable'} (offset {offset:+d} ms)\n"
        else:
            msg = f"{NEON_CYAN}Server time:{RESET} {utc_dt or 'unavailable'}\n"

        sys.stderr.write(_c(msg, no_color))

        print(
            json.dumps(
                {
                    "server_time_ms": st,
                    "server_time_utc": utc_dt,
                    "offset_ms": offset,
                },
                indent=2,
            )
        )

    elif args.action == "config":
        cfg = dict(get_config())
        # Redact secrets before display
        cfg["api_key"] = f"{cfg['api_key'][:4]}***" if cfg["api_key"] else "NOT_SET"
        cfg["api_secret"] = "***REDACTED***" if cfg["api_secret"] else "NOT_SET"
        sys.stderr.write(
            _c(f"{NEON_YELLOW}Configuration (secrets redacted):{RESET}\n", no_color)
        )
        print(json.dumps(cfg, indent=2))
