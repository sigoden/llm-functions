#!/usr/bin/env python3
"""
BYBIT REALM - Production-Grade Trading System Tool for LLM Functions v4.0

Fixes & Improvements in this version (v4.0 over v3.2):
  ── Critical Bug Fixes ──
  • Implemented 6 missing methods: get_market_momentum, get_market_health,
    cancel_all_orders, calculate_kelly_criterion, calculate_trade_pnl,
    calculate_profit_target
  • Removed duplicate method definitions: amend_order, get_order_history,
    calculate_position_size (kept correct versions)
  • Removed duplicate action handlers in run(): get_order_history,
    calculate_position_size
  • Moved PySocksGeoRouter class BEFORE __main__ block so it's importable
  • Fixed circuit breaker deadlock: _on_failure() no longer sleeps under lock
  • Added get_trend_analysis to run() action Literal type hints

  ── Architectural Improvements ──
  • Merged _tier_pysocks and _tier_proxy into single _tier_socks (were identical)
  • Added _tier_proxychains4_requests hybrid: proxychains4 wrapping python process
  • Added Tor circuit renewal via SOCKS5 NEWNYM signal on control port
  • Added server time synchronization to prevent HMAC signature drift
  • Added request ID (X-Request-ID) tracking for log correlation
  • WebSocket reconnection moved to daemon thread (no callback blocking)
  • CLI now properly injects config into singleton dispatcher
  • configure_pysocks_from_env() wired into dispatcher initialization
  • Added PySocks global socket patching option for non-requests libraries
  • Added connection health scoring for endpoint rotation

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
      2. proxychains4 binary wrapping curl
      3. direct connection (fallback)

    When TOR_ENABLED=false:
      1. direct connection only
"""

# Standard library
import hashlib
import hmac
import json
import logging
import math
import os
import pickle
import random
import re
import socket as stdlib_socket
import statistics
import subprocess
import sys
import threading
import time
import uuid

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

import requests

# Third-party
import socks  # PySocks
import websocket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class NetworkError(Exception):
    pass


# -------------------------------------------------
#  Helper to guarantee a valid TimeInForce value
# -------------------------------------------------
def _sanitize_tif(tif: Any) -> str:
    """
    Bybit expects one of: GTC, IOC, FOK, PostOnly.
    If the caller supplies an empty string, "0", None, or invalid value,
    we fall back to the safest default – GTC.
    """
    if not tif or str(tif).strip() == "0":
        return "GTC"
    try:
        # Ensure input is string, convert to upper, and validate against Enum
        sanitized_tif = str(tif).strip().upper()
        return TimeInForce(sanitized_tif).value  # Use Enum for validation
    except ValueError:
        logger.warning(
            "Invalid TimeInForce value '%s' provided. Falling back to GTC.", tif
        )
        return "GTC"


# -------------------------------------------------
#  Helper to guarantee a valid Category value
# -------------------------------------------------
def _sanitize_category(cat: Any) -> str:
    """
    Sanitize category input to one of: linear, inverse, spot, option.
    Defaults to 'linear' if input is invalid, empty, or '0'.
    """
    if not cat or str(cat).strip() == "0":
        return Category.LINEAR.value
    s = str(cat).strip().lower()
    valid_categories = {c.value for c in Category}
    return s if s in valid_categories else Category.LINEAR.value


# -------------------------------------------------
#  Helper to guarantee a valid Order Type value
# -------------------------------------------------
def _sanitize_order_type(ot: Any) -> str:
    """
    Sanitize order type input to a valid Bybit type.
    Defaults to 'Limit' if input is invalid, empty, or '0'.
    """
    if not ot or str(ot).strip() == "0":
        return OrderType.LIMIT.value

    s = str(ot).strip().lower()
    valid_types = {
        "limit": OrderType.LIMIT,
        "market": OrderType.MARKET,
        "limitmaker": OrderType.LIMIT_MAKER,
        "stop": OrderType.STOP,
        "stoplimit": OrderType.STOP_LIMIT,
    }

    # Use title case for Bybit's expected format, e.g., 'Limit'
    return valid_types.get(s, OrderType.LIMIT).value


# -------------------------------------------------
#  Helper to guarantee a valid PositionIdx (int)
# -------------------------------------------------
def _sanitize_position_idx(idx: Any) -> int:
    """
    Sanitize position index to valid Bybit enum values (0, 1, 2).
    Falls back to 1 (HEDGE_BUY) if input is invalid or 0 is not explicitly used.
    """
    try:
        i = int(idx)
        if i in {0, 1, 2}:  # Valid Bybit indices
            return i
        else:
            logger.warning(
                "Invalid position_idx %d provided. Falling back to default (1).", i
            )
            return 1  # Fallback for invalid integer
    except (ValueError, TypeError):
        logger.warning(
            "Non-integer position_idx provided. Falling back to default (1)."
        )
        return 1  # Fallback for non-integer types


# -------------------------------------------------
#  Helper to guarantee a boolean value
# -------------------------------------------------
def _sanitize_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()  # Ensure lowercasing for reliable comparison
    return s in {"true", "1", "yes", "y"}


# -------------------------------------------------
#  Helper to guarantee a valid TriggerBy value
# -------------------------------------------------
def _sanitize_trigger_by(val: str) -> str:
    if not val or str(val).strip() == "0":
        return "MarkPrice"
    v = str(val).lower()
    return "MarkPrice" if "mark" in v else "LastPrice"


# ─────────────────────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# ─────────────────────────────────────────────────────────────


class TorError(Exception):
    """Base exception for Tor-related errors."""

    pass


class TorControlError(TorError):
    """Tor control port communication failed."""

    pass


class TorCircuitError(TorError):
    """Circuit renewal or establishment failed."""

    pass


class HTTPRequestFailed(RuntimeError):
    """HTTP request completed but returned non-success status."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
        body_snippet: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(message or f"HTTP {status} from {url}")
        self.status = status
        self.url = url
        self.body_snippet = body_snippet[:500] if body_snippet else ""
        self.headers = headers or {}


# BLOCK DETECTION UTILITIES
# ─────────────────────────────────────────────────────────────
@dataclass
class BlockClassification:
    is_blocked: bool
    block_type: str
    confidence: float
    evidence: str


BLOCK_INDICATORS = {
    "geo": [
        "geo",
        "region",
        "country",
        "location",
        "jurisdiction",
        "not available in your",
    ],
    "waf": ["cloudflare", "ddos", "protection", "firewall", "security check", "ray id"],
    "captcha": ["captcha", "challenge", "verify you are human", "bot detection"],
    "rate": ["rate limit", "too many requests", "slow down", "throttl"],
    "access": ["access denied", "forbidden", "blocked", "not authorized"],
}
HTTP_CODE_RE = re.compile(r"\b([45]\d{2})\b")


def classify_block(
    status_code: Optional[int] = None,
    body: str = "",
    headers: Optional[Dict[str, str]] = None,
    exception: Optional[Exception] = None,
) -> BlockClassification:
    headers = headers or {}
    body_lower = body.lower()
    evidence_parts = []
    if status_code is None and exception:
        match = HTTP_CODE_RE.search(str(exception))
        if match:
            status_code = int(match.group(1))
    if status_code == 403:
        evidence_parts.append("HTTP 403")
        if (
            "cloudflare" in body_lower
            or headers.get("server", "").lower() == "cloudflare"
        ):
            return BlockClassification(True, "waf", 0.95, "HTTP 403 + Cloudflare")
        if any(ind in body_lower for ind in BLOCK_INDICATORS["geo"]):
            return BlockClassification(True, "geo", 0.9, "HTTP 403 + geo indicators")
        return BlockClassification(True, "access", 0.8, "HTTP 403 Forbidden")
    if status_code == 451:
        return BlockClassification(
            True, "geo", 0.95, "HTTP 451 Unavailable For Legal Reasons"
        )
    if status_code == 429:
        return BlockClassification(True, "rate", 0.95, "HTTP 429 Too Many Requests")
    for block_type, indicators in BLOCK_INDICATORS.items():
        matches = [ind for ind in indicators if ind in body_lower]
        if matches:
            evidence_parts.append(f"{block_type}: {matches[:2]}")
    if "cf-ray" in headers or headers.get("server", "").lower() == "cloudflare":
        evidence_parts.append("Cloudflare detected")
    if evidence_parts:
        if any("waf" in e or "cloudflare" in e.lower() for e in evidence_parts):
            return BlockClassification(True, "waf", 0.7, "; ".join(evidence_parts))
        if any("geo" in e for e in evidence_parts):
            return BlockClassification(True, "geo", 0.7, "; ".join(evidence_parts))
        return BlockClassification(True, "unknown", 0.5, "; ".join(evidence_parts))
    return BlockClassification(False, "none", 0.0, "No block indicators found")


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
    import socks  # noqa: F401

    PYSOCKS_AVAILABLE = True
except ImportError:
    PYSOCKS_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
class NoWarningFilter(logging.Filter):
    def filter(self, record):
        return record.levelno != logging.WARNING


class ColorFormatter(logging.Formatter):
    COLORS = {
        "INFO": "\033[1;32m",  # Neon Green
        "ERROR": "\033[1;35m",  # Neon Magenta
        "CRITICAL": "\033[1;31m",  # Bold Red
        "DEBUG": "\033[1;36m",  # Neon Cyan
    }
    RESET = "\033[0m"

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, "")
        log_fmt = f"{log_color}%(asctime)s | %(levelname)-8s | %(name)s | %(message)s{self.RESET}"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


log_handler = RotatingFileHandler(
    "trading_bot.log", maxBytes=10 * 1024 * 1024, backupCount=5
)
log_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.addFilter(NoWarningFilter())
console_handler.setFormatter(ColorFormatter())

logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, log_handler],
)
logger = logging.getLogger("BybitRealm")


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────
class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"

    @classmethod
    def from_any(cls, val: Any) -> "OrderSide":
        if isinstance(val, cls):
            return val
        s = str(val).lower().strip()
        if "sell" in s:
            return cls.SELL
        if "buy" in s:
            return cls.BUY
        logger.warning(f"Invalid OrderSide: '{val}'. Defaulting to BUY.")
        return cls.BUY


class OrderType(str, Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    LIMIT_MAKER = "LimitMaker"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"

    @classmethod
    def from_any(cls, val: Any) -> "OrderType":
        if isinstance(val, cls):
            return val
        s = str(val).lower().strip()
        mapping = {
            "limit": cls.LIMIT,
            "market": cls.MARKET,
            "limitmaker": cls.LIMIT_MAKER,
            "stop": cls.STOP,
            "stoplimit": cls.STOP_LIMIT,
        }
        if s in mapping:
            return mapping[s]
        logger.warning(f"Invalid OrderType: '{val}'. Defaulting to LIMIT.")
        return cls.LIMIT


class Category(str, Enum):
    LINEAR = "linear"
    INVERSE = "inverse"
    SPOT = "spot"
    OPTION = "option"

    @classmethod
    def from_any(cls, val: Any) -> "Category":
        if isinstance(val, cls):
            return val
        if not val or str(val).strip() == "0":
            return cls.LINEAR
        s = str(val).lower().strip()
        try:
            return cls(s)
        except ValueError:
            logger.warning(f"Invalid Category: '{val}'. Defaulting to LINEAR.")
            return cls.LINEAR


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    @classmethod
    def from_any(cls, val: Any) -> "CircuitState":
        if isinstance(val, cls):
            return val
        s = str(val).upper().strip()
        if s in {e.value for e in cls}:
            return cls(s)
        logger.warning(f"Invalid CircuitState: '{val}'. Defaulting to CLOSED.")
        return cls.CLOSED


# ─────────────────────────────────────────────────────────────
# DIANOSTICS & ERROR HANDLING HELPERS
# ─────────────────────────────────────────────────────────────
HTTP_CODE_RE = re.compile(r"\b(4\d\d|5\d\d)\b")
BLOCK_HINTS = (
    "geo",
    "blocked",
    "forbidden",
    "access denied",
    "cloudflare",
    "captcha",
    "bot",
    "ddos",
    "restricted",
    "unavailable for legal reasons",
)


def _safe_snip(text: str, n: int = 300) -> str:
    text = text or ""
    text = text.replace("\n", "\\n")
    return text[:n]


class HTTPRequestFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
        body_snippet: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body_snippet = body_snippet
        self.headers = headers or {}


def looks_like_block(exc: Exception) -> bool:
    if isinstance(exc, HTTPRequestFailed) and exc.status in (403, 451):
        return True
    s = str(exc).lower()
    m = HTTP_CODE_RE.search(s)
    code = int(m.group(1)) if m else None
    if code in (403, 451):
        return True
    return any(h in s for h in BLOCK_HINTS)


def _tor_ctl_send(sock: stdlib_socket.socket, line: str) -> str:
    sock.sendall((line + "\r\n").encode("utf-8"))
    data = b""
    sock.settimeout(5)
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        txt = data.decode("utf-8", errors="replace")
        if "\n250 " in "\n" + txt or "\n5" in "\n" + txt:
            return txt
    return data.decode("utf-8", errors="replace")


def _tor_ctl_ok(reply: str) -> bool:
    return "250" in reply.splitlines()[0] if reply else False


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @classmethod
    def from_any(cls, val: Any) -> "Signal":
        if isinstance(val, cls):
            return val
        s = str(val).upper().strip()
        if s in {e.value for e in cls}:
            return cls(s)
        logger.warning(f"Invalid Signal: '{val}'. Defaulting to NEUTRAL.")
        return cls.NEUTRAL


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "PostOnly"

    @classmethod
    def from_any(cls, val: Any) -> "TimeInForce":
        if isinstance(val, cls):
            return val
        if not val or str(val).strip() == "0":
            return cls.GTC
        s = str(val).upper().strip()
        if s == "POSTONLY" or s == "POST_ONLY":
            return cls.POST_ONLY
        try:
            return cls(s)
        except ValueError:
            logger.warning(f"Invalid TimeInForce: '{val}'. Defaulting to GTC.")
            return cls.GTC


class PositionIdx(int, Enum):
    ONE_WAY = 0
    HEDGE_BUY = 1
    HEDGE_SELL = 2

    @classmethod
    def from_any(cls, val: Any) -> "PositionIdx":
        if isinstance(val, cls):
            return val
        try:
            i = int(val)
            if i in {0, 1, 2}:
                return cls(i)
        except (ValueError, TypeError):
            pass
        logger.warning(f"Invalid PositionIdx: '{val}'. Defaulting to 1 (HEDGE_BUY).")
        return cls.HEDGE_BUY


# ─────────────────────────────────────────────────────────────
# UTILITY: safe_float
# ─────────────────────────────────────────────────────────────
def _safe_float(val) -> Optional[float]:
    """Convert to float; return None for None or empty strings."""
    if val is None or str(val).strip() == "":
        return None
    return float(val)


def calculate_breakeven_with_fees(
    entry_price: float,
    position_size_usdt: float,
    maker_fee_rate: float = 0.0006,  # 0.06% for VIP 0-1
    taker_fee_rate: float = 0.001,  # 0.1% for VIP 0-1
    min_net_profit_usdt: float = 0.02,
    is_maker: bool = True,
    leverage: int = 1,
) -> Dict[str, float]:
    """
    Calculate breakeven price accounting for fees and minimum net profit.

    For a LONG position:
    - Entry fee = position_size_usdt * maker_fee_rate
    - Exit fee = position_size_usdt * taker_fee_rate
    - Total fees = entry_fee + exit_fee
    - Required gross profit = total_fees + min_net_profit_usdt
    - Breakeven price = entry_price + (required_gross_profit / position_size)

    For a SHORT position:
    - Entry fee = position_size_usdt * maker_fee_rate
    - Exit fee = position_size_usdt * taker_fee_rate
    - Total fees = entry_fee + exit_fee
    - Required gross profit = total_fees + min_net_profit_usdt
    - Breakeven price = entry_price - (required_gross_profit / position_size)

    Args:
        entry_price: Entry price of the position
        position_size_usdt: Position size in USDT
        maker_fee_rate: Maker fee rate (default 0.0006 for VIP 0-1)
        taker_fee_rate: Taker fee rate (default 0.001 for VIP 0-1)
        min_net_profit_usdt: Minimum net profit in USDT (default 0.02)
        is_maker: Whether entry was a maker order (default True)
        leverage: Leverage multiplier (default 1)

    Returns:
        Dictionary with breakeven prices for long and short positions
    """
    if position_size_usdt <= 0 or entry_price <= 0:
        raise ValueError("Position size and entry price must be positive")

    # Calculate position size in base asset
    position_size_base = position_size_usdt / entry_price

    # Calculate fees
    entry_fee_rate = maker_fee_rate if is_maker else taker_fee_rate
    entry_fee = position_size_usdt * entry_fee_rate
    exit_fee = position_size_usdt * taker_fee_rate
    total_fees = entry_fee + exit_fee

    # Required gross profit to achieve minimum net profit
    required_gross_profit = total_fees + min_net_profit_usdt

    # Calculate breakeven prices
    # For LONG: need price to rise enough to cover fees + min profit
    long_breakeven_price = entry_price + (required_gross_profit / position_size_base)

    # For SHORT: need price to fall enough to cover fees + min profit
    short_breakeven_price = entry_price - (required_gross_profit / position_size_base)

    # Calculate percentage moves needed
    long_move_pct = ((long_breakeven_price - entry_price) / entry_price) * 100
    short_move_pct = ((entry_price - short_breakeven_price) / entry_price) * 100

    # Calculate breakeven with leverage (margin-based)
    # With leverage, the required price move is divided by leverage
    long_breakeven_price_leveraged = entry_price + (
        required_gross_profit / (position_size_base * leverage)
    )
    short_breakeven_price_leveraged = entry_price - (
        required_gross_profit / (position_size_base * leverage)
    )

    long_move_pct_leveraged = (
        (long_breakeven_price_leveraged - entry_price) / entry_price
    ) * 100
    short_move_pct_leveraged = (
        (entry_price - short_breakeven_price_leveraged) / entry_price
    ) * 100

    return {
        "entry_price": round(entry_price, 6),
        "position_size_usdt": round(position_size_usdt, 2),
        "position_size_base": round(position_size_base, 6),
        "entry_fee": round(entry_fee, 4),
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
        "min_net_profit_usdt": round(min_net_profit_usdt, 4),
        "required_gross_profit": round(required_gross_profit, 4),
        "long_breakeven_price": round(long_breakeven_price, 6),
        "long_move_pct": round(long_move_pct, 4),
        "short_breakeven_price": round(short_breakeven_price, 6),
        "short_move_pct": round(short_move_pct, 4),
        "leverage": leverage,
        "long_breakeven_price_leveraged": round(long_breakeven_price_leveraged, 6),
        "long_move_pct_leveraged": round(long_move_pct_leveraged, 4),
        "short_breakeven_price_leveraged": round(short_breakeven_price_leveraged, 6),
        "short_move_pct_leveraged": round(short_move_pct_leveraged, 4),
    }


def calculate_profit_target_with_fees(
    entry_price: float,
    position_size_usdt: float,
    target_profit_usdt: float,
    maker_fee_rate: float = 0.0006,
    taker_fee_rate: float = 0.001,
    is_maker: bool = True,
    leverage: int = 1,
) -> Dict[str, float]:
    """
    Calculate target price to achieve specific profit after fees.

    Args:
        entry_price: Entry price of the position
        position_size_usdt: Position size in USDT
        target_profit_usdt: Target net profit in USDT
        maker_fee_rate: Maker fee rate (default 0.0006)
        taker_fee_rate: Taker fee rate (default 0.001)
        is_maker: Whether entry was a maker order (default True)
        leverage: Leverage multiplier (default 1)

    Returns:
        Dictionary with target prices for long and short positions
    """
    if position_size_usdt <= 0 or entry_price <= 0:
        raise ValueError("Position size and entry price must be positive")

    position_size_base = position_size_usdt / entry_price

    entry_fee_rate = maker_fee_rate if is_maker else taker_fee_rate
    entry_fee = position_size_usdt * entry_fee_rate
    exit_fee = position_size_usdt * taker_fee_rate
    total_fees = entry_fee + exit_fee

    required_gross_profit = total_fees + target_profit_usdt

    long_target_price = entry_price + (required_gross_profit / position_size_base)
    short_target_price = entry_price - (required_gross_profit / position_size_base)

    long_move_pct = ((long_target_price - entry_price) / entry_price) * 100
    short_move_pct = ((entry_price - short_target_price) / entry_price) * 100

    long_target_price_leveraged = entry_price + (
        required_gross_profit / (position_size_base * leverage)
    )
    short_target_price_leveraged = entry_price - (
        required_gross_profit / (position_size_base * leverage)
    )

    long_move_pct_leveraged = (
        (long_target_price_leveraged - entry_price) / entry_price
    ) * 100
    short_move_pct_leveraged = (
        (entry_price - short_target_price_leveraged) / entry_price
    ) * 100

    return {
        "entry_price": round(entry_price, 6),
        "position_size_usdt": round(position_size_usdt, 2),
        "target_profit_usdt": round(target_profit_usdt, 2),
        "entry_fee": round(entry_fee, 4),
        "exit_fee": round(exit_fee, 4),
        "total_fees": round(total_fees, 4),
        "required_gross_profit": round(required_gross_profit, 4),
        "long_target_price": round(long_target_price, 6),
        "long_move_pct": round(long_move_pct, 4),
        "short_target_price": round(short_target_price, 6),
        "short_move_pct": round(short_move_pct, 4),
        "leverage": leverage,
        "long_target_price_leveraged": round(long_target_price_leveraged, 6),
        "long_move_pct_leveraged": round(long_move_pct_leveraged, 4),
        "short_target_price_leveraged": round(short_target_price_leveraged, 6),
        "short_move_pct_leveraged": round(short_move_pct_leveraged, 4),
    }


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
        default_factory=lambda: int(os.getenv("TOR_SOCKS_PORT", "9060"))
    )
    tor_control_port: int = field(
        default_factory=lambda: int(os.getenv("TOR_CONTROL_PORT", "9061"))
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
    default_stop_loss_pct: float = 0.02
    default_take_profit_pct: float = 0.04
    order_timeout_seconds: int = 30

    # ── Iceberg ───────────────────────────────────────────────
    iceberg_min_slices: int = 3
    iceberg_max_slices: int = 10
    iceberg_delay: float = 0.5

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_max_bytes: int = 10485760
    log_backup_count: int = 5

    # ── Indicators ────────────────────────────────────────────
    rsi_period: int = 14
    sma_period: int = 20
    ema_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

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
                iceberg = data.get("iceberg", {})
                logging = data.get("logging", {})
                indicators = data.get("indicators", {})

                return cls(
                    api_key=data.get("api_key") or os.getenv("BYBIT_API_KEY", ""),
                    api_secret=data.get("api_secret")
                    or os.getenv("BYBIT_API_SECRET", ""),
                    use_tor=bool(
                        data.get("network", {}).get(
                            "use_tor",
                            os.getenv("TOR_ENABLED", "false").lower() == "true",
                        )
                    ),
                    tor_socks_port=int(
                        data.get("network", {}).get(
                            "tor_socks_port", int(os.getenv("TOR_SOCKS_PORT", "9050"))
                        )
                    ),
                    max_position_usdt=settings.get("max_position_usdt", 1000.0),
                    default_leverage=settings.get("leverage", 1),
                    default_stop_loss_pct=settings.get("default_stop_loss_pct", 0.02),
                    default_take_profit_pct=settings.get(
                        "default_take_profit_pct", 0.04
                    ),
                    order_timeout_seconds=settings.get("order_timeout_seconds", 30),
                    iceberg_min_slices=iceberg.get("min_slices", 3),
                    iceberg_max_slices=iceberg.get("max_slices", 10),
                    iceberg_delay=iceberg.get("delay_between_slices", 0.5),
                    log_level=logging.get("level", "INFO"),
                    log_max_bytes=logging.get("max_bytes", 10485760),
                    log_backup_count=logging.get("backup_count", 5),
                    rsi_period=indicators.get("rsi_period", 14),
                    sma_period=indicators.get("sma_period", 20),
                    ema_period=indicators.get("ema_period", 50),
                    macd_fast=indicators.get("macd_fast", 12),
                    macd_slow=indicators.get("macd_slow", 26),
                    macd_signal=indicators.get("macd_signal", 9),
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
            "https://api.bytick.com",
            "https://api.bybit.nl",
            "https://api.bybit.tr",
            "https://api.bybit.kz",
        ]

    def validate(self) -> None:
        logger.info(
            "Validating configuration. API Key present: %s",
            "Yes" if self.api_key else "No",
        )
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "BYBIT_API_KEY and BYBIT_API_SECRET must be set "
                "as environment variables or in trading_config.json."
            )


# ─────────────────────────────────────────────────────────────
# PySocks GEO IP ROUTING
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
            self.proxy_type = 5
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
        # Proxy forced to None to ensure direct connection
        session.proxies = {"http": None, "https": None}
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
# ─────────────────────────────────────────────────────────────
# TOR MANAGER
# ─────────────────────────────────────────────────────────────
@dataclass
class TorConfig:
    enabled: bool = False
    socks_host: str = "127.0.0.1"
    socks_port: int = 9060
    control_host: str = "127.0.0.1"
    control_port: int = 9061
    control_password: str = ""
    timeout: int = 30
    max_retries: int = 3
    use_pysocks: bool = True


class TorManager:
    RENEWAL_COOLDOWN_SECONDS = 15
    MAX_RENEWALS_PER_MINUTE = 4

    def __init__(self, config: TorConfig):
        self.config = config
        self.enabled = config.enabled
        self._tor_control_port_accessible: bool = False
        self._tor_socks_port_accessible: bool = False
        self._last_renewal_ts: float = 0.0
        self._renewal_timestamps: list = []
        self._renewed_for_requests: Set[str] = set()
        self._last_known_exit_ip: Optional[str] = None
        self._last_exit_ip_check: float = 0.0
        self._session: Optional[requests.Session] = None
        self._socks_session = self._build_socks_session(
            self.config.max_retries, self.config.socks_port
        )
        self._socks_alive = False
        self._socks_probe_ts = 0.0
        self._proxychains4_bin = self._find_proxychains4()
        if self.enabled:
            self._tor_socks_port_accessible = self._check_local_port(
                self.config.socks_port, name="SOCKS"
            )
            self._tor_control_port_accessible = self._check_local_port(
                self.config.control_port, name="ControlPort"
            )
            self._build_session()
            self._verify_tor_routing()

    def _check_local_port(self, port: int, name: str) -> bool:
        """Check if a local port is listening."""
        with stdlib_socket.socket(
            stdlib_socket.AF_INET, stdlib_socket.SOCK_STREAM
        ) as s:
            s.settimeout(2)
            return s.connect_ex(("127.0.0.1", port)) == 0

    @property
    def tor_control_accessible(self) -> bool:
        return self._tor_control_port_accessible

    @property
    def tor_socks_accessible(self) -> bool:
        return self._tor_socks_port_accessible

    def renew_tor_circuit(self, request_id: str = None) -> bool:
        """Renew Tor circuit via control port."""
        if not self.tor_control_accessible:
            logger.warning("Tor control port not accessible")
            return False
        try:
            with stdlib_socket.socket(
                stdlib_socket.AF_INET, stdlib_socket.SOCK_STREAM
            ) as sock:
                sock.settimeout(5)
                sock.connect(("127.0.0.1", self.config.control_port))
                # Authenticate
                auth_cmd = (
                    f'AUTHENTICATE "{self.config.control_password}"'
                    if self.config.control_password
                    else "AUTHENTICATE"
                )
                reply = _tor_ctl_send(sock, auth_cmd)
                if not _tor_ctl_ok(reply):
                    logger.error("Tor control auth failed: %s", reply[:200])
                    return False
                # Send NEWNYM signal
                reply = _tor_ctl_send(sock, "SIGNAL NEWNYM")
                if not _tor_ctl_ok(reply):
                    logger.error("Tor NEWNYM failed: %s", reply[:200])
                    return False
                self._last_renewal_ts = time.time()
                logger.info("Tor circuit renewed successfully")
                return True
        except Exception as exc:
            logger.error("Tor circuit renewal failed: %s", exc)
            return False

    @staticmethod
    def _find_proxychains4() -> Optional[str]:
        """Find proxychains4 binary."""
        import shutil

        for bin_name in ["proxychains4", "torify"]:
            path = shutil.which(bin_name)
            if path:
                return path
        return None

    # ── Tier implementations ─────────────────────────────────

    def _tier_socks(self, method, url, headers, params, json_data) -> dict:
        """Tier 1: SOCKS5 proxy via requests library (uses PySocks if installed)."""
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not available")
        if not self._socks_session:
            raise RuntimeError("SOCKS session not initialized")
        conn_timeout = (
            min(self.config.timeout, 8) if hasattr(self.config, "timeout") else 8
        )
        request_timeout = self.config.timeout if hasattr(self.config, "timeout") else 15
        timeouts = (conn_timeout, request_timeout)
        if json_data is not None:
            resp = self._socks_session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=json.dumps(json_data, sort_keys=True, separators=(",", ":")),
                timeout=timeouts,
            )
        else:
            resp = self._socks_session.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=timeouts,
            )
        self._socks_alive = True
        self._socks_probe_ts = time.time()
        return self._parse_response(resp)

    def _tier_proxychains4(self, method, url, headers, params, json_data) -> dict:
        """Tier 2: proxychains4 binary wrapping curl."""
        if not self._proxychains4_bin:
            raise RuntimeError("proxychains4 binary not found")

        cmd = [
            self._proxychains4_bin,
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-X",
            method,
        ]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data, sort_keys=True, separators=(",", ":"))]
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            url = f"{url}?{qs}"
        cmd.append(url)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout + 5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"proxychains4 curl failed: {result.stderr.strip()}")
        raw = result.stdout.strip()
        if not raw:
            raise RuntimeError("proxychains4 curl returned empty response")

        lines = raw.rsplit("\n", 1)
        body = lines[0] if len(lines) > 1 else raw
        http_code = int(lines[-1]) if len(lines) > 1 and lines[-1].isdigit() else 0

        if http_code == 403:
            raise RuntimeError(
                f"403 Forbidden (geo-blocked) via proxychains4 for url: {url}"
            )
        if http_code >= 400:
            raise RuntimeError(f"HTTP {http_code} via proxychains4 for url: {url}")

        data = json.loads(body)
        ret_code = data.get("retCode", 0)
        if ret_code != 0 and ret_code not in (110043,):
            raise RuntimeError(
                f"Bybit API error retCode={ret_code}: {data.get('retMsg', 'unknown')}"
            )
        return data

    def _tier_direct(self, method, url, headers, params, json_data) -> dict:
        """Tier 3: direct connection (no proxy)."""
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library not available")
        if json_data is not None:
            resp = self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=json.dumps(json_data, sort_keys=True, separators=(",", ":")),
                timeout=self.timeout,
            )
        else:
            resp = self._session.request(
                method,
                url,
                headers=headers,
                params=params,
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

    def _build_session(self):
        self._session = requests.Session()
        retry = Retry(
            total=self.config.max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _verify_tor_routing(self):
        """Verify that traffic is routed through Tor."""
        try:
            # Simple check by requesting a public IP service
            response = self._socks_session.get("https://api.ipify.org", timeout=5)
            if response.status_code == 200:
                self._last_known_exit_ip = response.text.strip()
                logger.info(
                    f"Tor routing verified. Exit IP: {self._last_known_exit_ip}"
                )
            else:
                logger.warning(
                    "Tor routing verification failed: Received status %d",
                    response.status_code,
                )
        except Exception as e:
            logger.warning("Tor routing verification failed: %s", e)

    @staticmethod
    def _parse_response(resp) -> dict:
        """Parse HTTP response, checking status, Bybit retCode, and JSON validity."""
        # Handle non-OK HTTP status codes first
        if resp.status_code >= 400:
            try:
                # Attempt to parse Bybit's specific error structure
                error_data = resp.json()
                ret_code = error_data.get("retCode")
                ret_msg = error_data.get("retMsg", "Unknown Bybit API error")

                # Raise a specific error for Bybit API errors
                if ret_code is not None:
                    raise HTTPRequestFailed(
                        status=resp.status_code,
                        url=resp.url,
                        body_snippet=str(error_data)[:200],
                        message=f"Bybit API Error {ret_code}: {ret_msg} (HTTP {resp.status_code})",
                    )
                else:
                    # If not a Bybit structured error, raise generic HTTP error
                    resp.raise_for_status()
            except ValueError:  # If response is not JSON
                resp.raise_for_status()  # Raise standard HTTP error
            except HTTPRequestFailed as hr:  # Re-raise our specific Bybit API error
                raise hr
            except Exception as e:  # Catch any other parsing/raising errors
                logger.error(
                    f"Error processing non-OK response ({resp.status_code}): {e}"
                )
                resp.raise_for_status()  # Ensure an error is raised

        # If HTTP status is OK (2xx), attempt JSON parsing
        try:
            data = resp.json()
            ret_code = data.get("retCode", 0)
            # Check Bybit's internal return code for success (0 or specific codes like 110043)
            if ret_code != 0 and ret_code not in (110043,):
                raise HTTPRequestFailed(
                    status=resp.status_code,
                    url=resp.url,
                    body_snippet=str(data)[:200],
                    message=f"Bybit API error retCode={ret_code}: {data.get('retMsg', 'unknown')}",
                )
            return data
        except ValueError:  # If response is not valid JSON
            logger.error("Received non-JSON response from API for URL: %s", resp.url)
            raise ValueError("API response was not valid JSON.")
        except HTTPRequestFailed as hr:  # Re-raise Bybit API specific errors
            raise hr
        except Exception as e:  # Catch other potential issues during parsing
            logger.error(f"Error parsing successful API response: {e}")
            raise HTTPRequestFailed(
                status=resp.status_code,
                url=resp.url,
                message=f"Failed to parse API response: {e}",
            )


class TorHTTPClient:
    def __init__(self, tor: TorManager) -> None:
        self.tor = tor
        self.session = requests.Session()
        if tor.enabled:
            proxy = f"socks5h://{tor.config.socks_host}:{tor.config.socks_port}"
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.session.headers.update(
            {
                "User-Agent": "bybit-realm/1.0 (tor-diagnostics)",
                "Accept": "application/json,text/plain,*/*",
            }
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Tuple[int, Dict[str, str], str]:
        try:
            r = self.session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=timeout,
            )
        except (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ProxyError,
            requests.exceptions.ConnectionError,
        ) as exc:
            raise NetworkError(f"Network/proxy failure: {exc}") from exc
        body_text = r.text or ""
        if not (200 <= r.status_code < 300):
            raise HTTPRequestFailed(
                f"HTTP {r.status_code} from {url}",
                status=r.status_code,
                url=url,
                body_snippet=_safe_snip(body_text, 500),
                headers=dict(r.headers),
            )
        return r.status_code, dict(r.headers), body_text


@dataclass
class LotSizeFilter:
    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> float:
        if self.qty_step <= 0:
            logger.warning(
                f"Invalid qty_step: {self.qty_step}. Returning original quantity."
            )
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
            logger.warning(
                f"Invalid tick_size: {self.tick_size}. Returning original price."
            )
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


class InstrumentCache:
    def __init__(self):
        self._cache: Dict[str, InstrumentInfo] = {}
        self._cache_file = os.environ.get("LLM_TOOL_CACHE_DIR")
        if self._cache_file:
            self._cache_file = os.path.join(self._cache_file, "instrument_cache.pkl")
            self._load_cache()

    def _load_cache(self):
        if self._cache_file and os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, "rb") as f:
                    self._cache = pickle.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self):
        if self._cache_file:
            try:
                with open(self._cache_file, "wb") as f:
                    pickle.dump(self._cache, f)
            except Exception:
                pass

    def get(self, key: str) -> Optional[InstrumentInfo]:
        info = self._cache.get(key)
        if info and not info.is_stale:
            return info
        return None

    def set(self, key: str, value: InstrumentInfo):
        self._cache[key] = value
        self._save_cache()

    def keys(self):
        return self._cache.keys()


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

    _RECV_WINDOW = "10000"
    MAX_API_RETRIES = 3
    _BLOCK_HINTS = (
        "geo",
        "blocked",
        "forbidden",
        "access denied",
        "cloudflare",
        "captcha",
        "bot",
    )
    _HTTP_CODE_RE = re.compile(r"\b(4\d\d|5\d\d)\b")

    def _looks_like_block(self, exc: Exception) -> bool:
        """Classify exception to see if it likely represents a geo/WAF block."""
        if isinstance(exc, HTTPRequestFailed):
            return exc.status in (403, 451)

        s = str(exc).lower()
        m = self._HTTP_CODE_RE.search(s)
        code = int(m.group(1)) if m else None

        if code in (403, 451):
            return True
        if any(h in s for h in self._BLOCK_HINTS):
            return True
        return False

    def __init__(self, config: TradingConfig) -> None:
        config.validate()
        self.config = config
        tor_config = TorConfig(
            enabled=config.use_tor,
            socks_port=config.tor_socks_port,
            control_port=config.tor_control_port,
            control_password=config.tor_control_pass,
        )
        self.tor = TorManager(tor_config)
        self.http = TorHTTPClient(self.tor)
        self.circuit = CircuitBreaker(
            failure_threshold=config.cb_failure_threshold,
            recovery_timeout=config.cb_recovery_timeout,
            cooldown=config.cb_cooldown,
        )
        self.limiter = RateLimiter(
            max_calls=config.rate_limit_calls,
            window=config.rate_limit_window,
        )
        self._renewed_for_requests: Set[str] = set()
        self._auto_recovery = True
        self._max_circuit_failures = 5
        self._circuit_failures = 0
        # ... (keep existing init code for other fields) ...
        self._instrument_cache = InstrumentCache()
        self._cache_lock = threading.Lock()
        self._fee_cache: Dict[str, Dict] = {}  # key: "symbol_category", value: fee_data
        self._fee_cache_ttl = 3600  # Cache for 1 hour
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
        """Sync local clock with Bybit server and re-sync if drift exceeds threshold."""
        try:
            local_before = int(time.time() * 1000)
            resp = self.tor.request(
                "GET",
                f"{self.config.get_endpoints()[0]}/v5/market/time",
                headers={"Content-Type": "application/json"},
                signed=False,
            )
            local_after = int(time.time() * 1000)
            server_time_nano = int(resp.get("result", {}).get("timeNano", "0"))
            if server_time_nano <= 0:
                logger.warning("Server time sync failed: invalid timeNano received.")
                return

            server_time_ms = server_time_nano // 1_000_000
            local_mid = (local_before + local_after) // 2
            new_offset = server_time_ms - local_mid

            # Re-sync if not synced, or if new offset significantly differs from current offset
            if (
                not self._time_synced or abs(new_offset - self._time_offset) > 500
            ):  # e.g., 500ms threshold
                self._time_offset = new_offset
                self._time_synced = True
                logger.warning(
                    "Clock drift detected/corrected: offset=%dms", self._time_offset
                )
            else:
                logger.debug(
                    "Server time sync successful: offset=%dms", self._time_offset
                )

        except Exception as exc:
            logger.warning("Server time sync failed: %s", exc)
            # Potentially reset _time_synced to False or use a fallback mechanism
            self._time_synced = False

    def _ensure_clock_sync(self) -> None:
        """Ensure local clock is synced with server."""
        if not self._time_synced:
            self._sync_server_time()

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

    def health_check(self) -> dict:
        """Enhanced health check with geo IP info and Tor status, with error handling."""
        try:
            # Check Tor connectivity
            if self.config.tor_enabled:
                if not self.tor.tor_socks_accessible:
                    return {"status": "error", "msg": "Tor proxy not accessible"}

            resp = self.api_request("GET", "/v5/market/time", signed=False)

            geo_ip = "N/A"
            geo_location = {"status": "not_checked"}

            # Get geo IP and location
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

            # Check circuit breaker state safely
            circuit_state = self.circuit.state.value if self.circuit else "UNKNOWN"
            circuit_fails = self.circuit.failure_count if self.circuit else 0

            # Check time offset safely
            time_offset = self._time_offset if hasattr(self, "_time_offset") else 0

            return {
                "status": "ok",
                "circuit": circuit_state,
                "circuit_fails": circuit_fails,
                "rate_usage": self.limiter.current_usage if self.limiter else 0,
                "server_time": resp.get("result", {}).get("timeNano"),
                "time_offset_ms": time_offset,
                "base_url": self.config.base_url,
                "tor_enabled": self.config.use_tor,
                "tor_socks_port": self.config.tor_socks_port,
                "tor_control_port": self.config.tor_control_port,
                "tor_control_accessible": self.tor.tor_control_accessible,
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
                "cache_symbols": list(self._instrument_cache.keys())
                if self._instrument_cache
                else [],
            }
        except Exception as exc:
            logger.error("Health check failed: %s", exc)
            return {"status": "error", "msg": str(exc)}

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

    def _get_error_action(self, error_code: int) -> str:
        """Get recommended user action for error."""
        if error_code == 10001:
            return "Wait and retry, or contact support"
        return "Review and retry"

    def _handle_api_error(
        self, error_code: int, error_msg: str, request_id: Optional[str] = None
    ) -> dict:
        """Handle API error with structured response."""
        parsed = self.parse_bybit_error(error_code, error_msg)

        # Snippet 20: Include request_id in logs for better correlation
        log_id = request_id if request_id else "N/A"
        logger.warning(
            "[%s] Bybit API error %d: %s (fatal=%s, retryable=%s)",
            log_id,
            error_code,
            error_msg,
            parsed["is_fatal"],
            parsed["is_retryable"],
        )

        # Snippet 11: Ensure consistent error return structure
        return {
            "status": "error",  # Ensure consistent error status
            "code": error_code,
            "message": error_msg,
            "category": parsed.get("category"),
            "is_fatal": parsed.get("is_fatal"),
            "is_retryable": parsed.get("is_retryable"),
            "user_action": parsed.get("user_action"),
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
    ) -> dict:
        self.limiter.acquire()
        request_id = str(uuid.uuid4())[:8]
        self._renewed_for_requests.discard(request_id)

        # Normalization
        for container in (params, json_data):
            if container:
                if "symbol" in container and isinstance(container["symbol"], str):
                    container["symbol"] = container["symbol"].upper()
                if "category" in container and isinstance(
                    container["category"], (Category, Enum)
                ):
                    container["category"] = container["category"].value
                # Ensure all numeric values are stringified for consistent API requirements
                for k, v in container.items():
                    if isinstance(v, (int, float)):
                        container[k] = str(v)

        self._ensure_clock_sync()
        ts = self._get_timestamp()

        # Build headers and payload
        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self._RECV_WINDOW,
            "Content-Type": "application/json",
        }

        payload = ""
        if method == "GET":
            payload = self._build_get_query(params or {})
        else:
            # Use strict separators to ensure exact match with Bybit expectation
            payload = json.dumps(json_data or {}, sort_keys=True, separators=(",", ":"))

        if signed:
            headers["X-BAPI-SIGN"] = self._sign(payload, ts)

        last_exc: Optional[Exception] = None
        for base_url in self.config.get_endpoints():
            url = f"{base_url}{endpoint}"
            try:
                # Use new structured HTTP client
                _, _, body = self.http.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json_data=json_data,
                )
                self._circuit_failures = 0
                return json.loads(body)
            except HTTPRequestFailed as exc:
                last_exc = exc
                classification = classify_block(
                    status_code=exc.status, body=exc.body_snippet, headers=exc.headers
                )
                logger.warning(
                    f"[{request_id}] HTTP {exc.status} from {base_url}. Block: {classification.is_blocked}"
                )
                if classification.is_blocked and classification.block_type in (
                    "geo",
                    "waf",
                    "access",
                ):
                    self._circuit_failures += 1
                    if (
                        self._auto_recovery
                        and self._circuit_failures < self._max_circuit_failures
                        and self.tor.enabled
                    ):
                        if request_id not in self._renewed_for_requests:
                            self._renewed_for_requests.add(request_id)
                            if self.tor.renew_tor_circuit(request_id=request_id):
                                time.sleep(2)
                                continue  # retry
                continue
            except NetworkError as exc:
                last_exc = exc
                continue
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"[{request_id}] Request failed after retries: {last_exc}")

    # ══════════════════════════════════════════════════════════
    # INSTRUMENT / LOT-SIZE + PRICE FILTER
    # ══════════════════════════════════════════════════════════
    def _fetch_instrument(self, symbol: str, category: str) -> InstrumentInfo:
        with self._cache_lock:
            info = self._instrument_cache.get(symbol)
            if info:
                return info

        logger.info("Fetching instrument info for %s…", symbol)
        resp = self.api_request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        try:
            item = resp["result"]["list"][0]
            lot_filters = item.get("lotSizeFilter", {})
            price_filters = item.get("priceFilter", {})

            lsf = LotSizeFilter(
                qty_step=float(
                    lot_filters.get("qtyStep", lot_filters.get("basePrecision", 1))
                ),
                min_order_qty=float(lot_filters.get("minOrderQty", 0)),
                max_order_qty=float(lot_filters.get("maxOrderQty", 1e9)),
                min_notional=float(lot_filters.get("minNotionalValue", 0)),
            )
            pf = PriceFilter(
                tick_size=float(price_filters.get("tickSize", 0.01)),
                min_price=float(price_filters.get("minPrice", 0)),
                max_price=float(price_filters.get("maxPrice", 1e12)),
            )
            info = InstrumentInfo(
                lot_size=lsf,
                price_flt=pf,
                symbol=symbol,
                status=item.get("status", "Trading"),
                fetched_at=time.time(),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error(
                "Failed to parse instrument filters for %s: %s. Item data: %s",
                symbol,
                e,
                item,
            )
            raise ValueError(
                f"Could not parse instrument filters for {symbol} due to malformed data."
            ) from e

        with self._cache_lock:
            self._instrument_cache.set(symbol, info)
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
        side: Literal["Buy", "Sell"],
        qty: float,
        price: Optional[float] = None,
        order_type: Literal[
            "Limit", "Market", "LimitMaker", "Stop", "StopLimit"
        ] = "Limit",
        category: Literal["linear", "inverse", "spot", "option"] = "linear",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: Literal["GTC", "IOC", "FOK", "PostOnly"] = "GTC",
        position_idx: int = 1,
        client_oid: Optional[str] = None,
        trailing_stop: Optional[float] = None,
    ) -> dict:
        """Place a single order on Bybit.
        Args:
            symbol: Trading pair symbol
            side: Order side
            qty: Quantity to order
            price: Order price
            order_type: Order type
            category: Product category
            stop_loss: Stop loss price
            take_profit: Take profit price
            reduce_only: Whether to reduce position only
            time_in_force: Time in force
            position_idx: Position index (0, 1, 2)
            client_oid: Client order ID
            trailing_stop: Trailing stop price
        """
        # Validate and convert inputs using Enums
        try:
            side_enum = OrderSide.from_any(side)
        except ValueError:
            return {
                "status": "error",
                "msg": f"Invalid order side: {side}. Must be 'Buy' or 'Sell'.",
            }

        try:
            order_type_enum = OrderType.from_any(order_type)
        except ValueError:
            return {
                "status": "error",
                "msg": f"Invalid order type: {order_type}. Must be one of {list(OrderType.__members__.keys())}",
            }

        time_in_force_enum = TimeInForce.from_any(time_in_force)
        category_enum = Category.from_any(category)
        position_idx_enum = PositionIdx.from_any(position_idx)
        reduce_only_bool = _sanitize_bool(reduce_only)

        qty = float(qty) if qty is not None else 0.0
        # Snippet 9: Ensure quantity is a positive number
        if qty <= 0:
            return {
                "status": "error",
                "msg": f"Order quantity must be positive. Received: {qty}",
            }

        price = _safe_float(price)
        stop_loss = _safe_float(stop_loss)
        take_profit = _safe_float(take_profit)
        trailing_stop = _safe_float(trailing_stop)

        adj_qty = self.adjust_quantity(symbol, qty, category_enum.value)

        payload: Dict[str, Any] = {
            "category": category_enum.value,
            "symbol": symbol,
            "side": side_enum.value,
            "orderType": order_type_enum.value,
            "qty": str(adj_qty),
            "timeInForce": time_in_force_enum.value,
            "positionIdx": position_idx_enum.value,
        }

        if price is not None:
            payload["price"] = str(
                self.adjust_price(symbol, price, category_enum.value)
            )
        if stop_loss is not None:
            payload["stopLoss"] = str(
                self.adjust_price(symbol, stop_loss, category_enum.value)
            )
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category_enum.value)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        if reduce_only_bool:
            payload["reduceOnly"] = True
        payload["orderLinkId"] = client_oid if client_oid else str(uuid.uuid4())[:10]

        logger.info(
            "Placing %s %s %s @ %s qty=%s",
            category_enum.value,
            side_enum.value,
            symbol,
            price or "MARKET",
            adj_qty,
        )

        return self.api_request("POST", "/v5/order/create", json_data=payload)

    # ══════════════════════════════════════════════════════════
    # BATCH ORDERS
    # ══════════════════════════════════════════════════════════
    def execute_scalp_batch(self, order_list: List[dict]) -> dict:
        """Execute a batch of orders on Bybit.
        Args:
            order_list: List of order dictionaries
        """
        if not order_list:
            raise ValueError("order_list must not be empty")
        if len(order_list) > self.config.max_orders_per_batch:
            raise ValueError(
                f"Bybit batch API max {self.config.max_orders_per_batch} orders"
            )

        batch = []
        for o in order_list:
            cat = o.get("category", Category.LINEAR)
            adj_qty = self.adjust_quantity(o["symbol"], float(o["qty"]), cat)
            entry: Dict[str, Any] = {
                "category": cat,
                "symbol": o["symbol"],
                "side": o["side"],
                "orderType": o.get("orderType", OrderType.LIMIT),
                "qty": str(adj_qty),
                "timeInForce": _sanitize_tif(o.get("timeInForce", TimeInForce.GTC)),
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
            json_data={"category": Category.LINEAR, "request": batch},
        )

    # ══════════════════════════════════════════════════════════
    # ICEBERG ORDERS
    # ══════════════════════════════════════════════════════════
    def place_iceberg_order(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        total_qty: float,
        price: float,
        slices: int = 5,
        category: Literal["linear", "inverse", "spot", "option"] = "linear",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        delay: Optional[float] = None,
    ) -> List[dict]:
        """Place an iceberg order by splitting it into multiple smaller orders.
        Args:
            symbol: Trading pair symbol
            side: Order side
            total_qty: Total quantity to order
            price: Price per slice
            slices: Number of slices
            category: Product category
            stop_loss: Stop loss price
            take_profit: Take profit price
            delay: Delay between slices in seconds
        """
        slices = max(
            self.config.iceberg_min_slices, min(self.config.iceberg_max_slices, slices)
        )
        delay = delay if delay is not None else self.config.iceberg_delay
        slice_qty = total_qty / slices
        results = []

        logger.info(
            "Iceberg: %s %s %s total=%.4f in %d slices @ %.4f",
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
                time.sleep(delay)
        return results

    def place_trailing_stop_order(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: float,
        trailing_distance: float,
        callback_rate: Optional[float] = None,
        category: Literal["linear", "inverse", "spot", "option"] = "linear",
        reduce_only: bool = False,
        position_idx: int = 0,
    ) -> dict:
        """Place a trailing stop order on Bybit.
        Args:
            symbol: Trading pair symbol
            side: Order side
            qty: Quantity to order
            trailing_distance: Trailing distance
            callback_rate: Callback rate
            category: Product category
            reduce_only: Whether to reduce position only
            position_idx: Position index (0, 1, 2)
        """
        adj_qty = self.adjust_quantity(symbol, qty, category)
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
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

    def check_balance(
        self,
        account_type: Literal["UNIFIED", "CONTRACT", "SPOT"] = "UNIFIED",
        coin: Optional[str] = None,
    ) -> dict:
        """Check wallet balance on Bybit.
        Args:
            account_type: Account type
            coin: Specific coin to check (e.g., USDT)
        """
        params = {"accountType": account_type}
        if coin:
            params["coin"] = coin

        return self.api_request(
            "GET", "/v5/account/wallet-balance", params=params, signed=True
        )

    def calculate_trailing_stop_levels(
        self,
        symbol: str,
        entry_price: float,
        side: Literal["Buy", "Sell"],
        trailing_distance: float,
        callback_rate: Optional[float] = None,
        category: Literal["linear", "inverse", "spot", "option"] = "linear",
    ) -> dict:
        """Calculate trailing stop activation and trigger levels.
        Args:
            symbol: Trading pair symbol
            entry_price: Entry price
            side: Order side
            trailing_distance: Trailing distance
            callback_rate: Callback rate
            category: Product category
        """
        try:
            ticker = self.get_ticker(symbol, category)
            current_price = float(ticker.get("lastPrice", entry_price))
            is_long = OrderSide.from_any(side) == OrderSide.BUY
            if is_long:
                activation_price = current_price + trailing_distance
                trigger_price = activation_price - trailing_distance
            else:
                activation_price = current_price - trailing_distance
                trigger_price = activation_price + trailing_distance
            if callback_rate is None and trailing_distance > 0:
                callback_rate = (trailing_distance / current_price) * 100
            return {
                "symbol": symbol,
                "activation_price": round(activation_price, 4),
                "trigger_price": round(trigger_price, 4),
                "distance_to_activation": round(
                    max(
                        0,
                        activation_price - current_price
                        if is_long
                        else current_price - activation_price,
                    ),
                    4,
                ),
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_trailing_stop_status(
        self,
        symbol: str,
        category: Literal["linear", "inverse", "spot", "option"] = "linear",
    ) -> dict:
        """Check trailing stop status for a symbol.
        Args:
            symbol: Trading pair symbol
            category: Product category
        """
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
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        payload: Dict[str, Any] = {"category": category, "symbol": symbol}
        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
        else:
            return {"status": "error", "msg": "order_id or client_oid required"}

        return self.api_request("POST", "/v5/order/cancel", json_data=payload)

    def cancel_all_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Cancel all open orders for a symbol (or all symbols in category)."""
        payload: Dict[str, Any] = {"category": category}
        if symbol:
            payload["symbol"] = symbol.upper()

        return self.api_request("POST", "/v5/order/cancel-all", json_data=payload)

    def amend_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> dict:
        """Amend an existing order by order_id or client_oid."""
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
        }

        # Validate that at least one modifiable parameter is provided
        if not any([qty, price, stop_loss, take_profit, trigger_price]):
            return {
                "status": "error",
                "msg": "No modifiable parameters provided for amendment.",
            }

        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
        else:
            return {"status": "error", "msg": "order_id or client_oid required"}
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
        if trigger_price is not None:
            payload["triggerPrice"] = str(
                self.adjust_price(symbol, trigger_price, category)
            )

        return self.api_request("POST", "/v5/order/amend", json_data=payload)

    def place_conditional_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        trigger_price: float,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.MARKET,
        trigger_by: str = "LastPrice",
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: TimeInForce = TimeInForce.GTC,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
    ) -> dict:
        """Place a conditional (stop/trigger) order."""
        # Ensure a proper enum value for TimeInForce
        time_in_force = _sanitize_tif(time_in_force)

        adj_qty = self.adjust_quantity(symbol, qty, category)
        is_buy = side == OrderSide.BUY
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(adj_qty),
            "triggerPrice": str(self.adjust_price(symbol, trigger_price, category)),
            "triggerDirection": 1 if is_buy else 2,
            "triggerBy": trigger_by,
            "timeInForce": time_in_force,
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
        if reduce_only:
            payload["reduceOnly"] = True
        if client_oid:
            payload["orderLinkId"] = client_oid

        return self.api_request("POST", "/v5/order/create", json_data=payload)

    def batch_amend_orders(
        self,
        order_list: List[dict],
        category: Category = Category.LINEAR,
    ) -> dict:
        """Batch amend multiple orders."""
        if not order_list:
            return {"status": "error", "msg": "order_list is empty"}
        batch = []
        for o in order_list:
            entry: Dict[str, Any] = {"symbol": o["symbol"]}
            if "orderId" in o:
                entry["orderId"] = o["orderId"]
            elif "orderLinkId" in o:
                entry["orderLinkId"] = o["orderLinkId"]
            if "qty" in o:
                entry["qty"] = str(
                    self.adjust_quantity(o["symbol"], float(o["qty"]), category)
                )
            if "price" in o:
                entry["price"] = str(
                    self.adjust_price(o["symbol"], float(o["price"]), category)
                )
            batch.append(entry)

        return self.api_request(
            "POST",
            "/v5/order/amend-batch",
            json_data={"category": category, "request": batch},
        )

    def batch_cancel_orders(
        self,
        order_list: List[dict],
        category: Category = Category.LINEAR,
    ) -> dict:
        """Batch cancel multiple orders."""
        if not order_list:
            return {"status": "error", "msg": "order_list is empty"}
        batch = []
        for o in order_list:
            entry: Dict[str, Any] = {"symbol": o["symbol"]}
            if "orderId" in o:
                entry["orderId"] = o["orderId"]
            elif "orderLinkId" in o:
                entry["orderLinkId"] = o["orderLinkId"]
            batch.append(entry)

        return self.api_request(
            "POST",
            "/v5/order/cancel-batch",
            json_data={"category": category, "request": batch},
        )

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
        settle_coin: str = "USDT",
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        else:
            params["settleCoin"] = settle_coin
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
        params: Dict[str, Any] = {"category": category, "limit": min(limit, 500)}
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
        settle_coin: str = "USDT",
    ) -> List[dict]:
        if isinstance(category, str):
            category = Category(category)
        # Snippet 12: Ensure category is correctly passed using enum value
        params: Dict[str, Any] = {"category": category.value}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin
        resp = self.api_request("GET", "/v5/position/list", params=params)

        # Robustness: Filter active positions
        positions_list = resp.get("result", {}).get("list", [])
        active_positions = []
        for pos in positions_list:
            size = _safe_float(pos.get("size", 0))
            if size and size > 0:
                active_positions.append(
                    {
                        "symbol": pos.get("symbol", "N/A"),
                        "side": pos.get("side", "None"),
                        "size": size,
                        "avgPrice": _safe_float(pos.get("avgPrice", 0)),
                        "markPrice": _safe_float(pos.get("markPrice", 0)),
                        "unrealisedPnl": _safe_float(pos.get("unrealisedPnl", 0)),
                        "leverage": _safe_float(pos.get("leverage", 1.0)),
                        "liqPrice": _safe_float(pos.get("liqPrice", 0)),
                        "stopLoss": pos.get("stopLoss", "0"),
                        "takeProfit": pos.get("takeProfit", "0"),
                        "trailingStop": pos.get("trailingStop", "0"),
                    }
                )
        return active_positions

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        # Snippet 13: Handle missing result or empty list gracefully
        resp = self.api_request(
            "GET", "/v5/account/wallet-balance", params={"accountType": account_type}
        )
        result_data = resp.get("result", {})
        accounts_list = result_data.get("list", [])
        if not accounts_list:  # Handle empty list case gracefully
            logger.warning("No account list found in wallet balance response.")
            return {"list": []}  # Return an empty list to prevent downstream errors
        return result_data

    def get_formatted_balance(
        self, account_type: str = "UNIFIED"
    ) -> List[Dict[str, float]]:
        """Retrieve and format wallet balances to show only coin and equity."""
        balance_data = self.get_wallet_balance(account_type=account_type)
        formatted_list = []
        for account in balance_data.get("list", []):
            for coin in account.get("coin", []):
                formatted_list.append(
                    {"coin": coin["coin"], "equity": float(coin["equity"])}
                )
        return formatted_list

    def print_formatted_balance(self, account_type: str = "UNIFIED") -> None:
        """Print formatted wallet balances."""
        balances = self.get_formatted_balance(account_type=account_type)
        print(f"\n--- Wallet Balances ({account_type}) ---")
        for b in balances:
            print(f"{b['coin']:<10}: {b['equity']:>15.4f}")
        print("----------------------------------\n")

    def get_formatted_positions(
        self, category: Category = Category.LINEAR, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve and format open positions to show essential details."""
        positions = self.get_positions(category=category, symbol=symbol)
        formatted_list = []
        for pos in positions:
            # Only include positions with size > 0
            if float(pos.get("size", 0)) > 0:
                formatted_list.append(
                    {
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "size": float(pos["size"]),
                        "avgPrice": float(pos["avgPrice"]),
                        "unrealisedPnl": float(pos.get("unrealisedPnl", 0)),
                    }
                )
        return formatted_list

    def print_formatted_positions(
        self, category: Category = Category.LINEAR, symbol: Optional[str] = None
    ) -> None:
        """Print formatted open positions."""
        positions = self.get_formatted_positions(category=category, symbol=symbol)
        print("\n--- Open Positions ---")
        if not positions:
            print("No open positions.")
        for p in positions:
            print(
                f"{p['symbol']:<10} | {p['side']:<4} | Size: {p['size']:<8.4f} | Avg: {p['avgPrice']:>10.2f} | PnL: {p['unrealisedPnl']:>10.4f}"
            )
        print("----------------------\n")

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
            "category": category,
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
        params: Dict[str, Any] = {"category": category, "limit": min(limit, 500)}
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
            win_rate=len(wins) / len(pnls) * 100
            if pnls
            else 0,  # Added check for len(pnls)
            avg_win=statistics.mean(wins) if wins else 0,  # Safe division by zero
            avg_loss=statistics.mean(losses) if losses else 0,  # Safe division by zero
            largest_win=max(wins) if wins else 0,  # Safe max on empty list
            largest_loss=min(losses) if losses else 0,  # Safe min on empty list
            total_fees=sum(fees),
            trade_count=len(pnls),
        )

    def get_fee_rate(self, symbol: str, category: Category = Category.LINEAR) -> dict:
        """Get trading fee rate for a symbol, with caching."""
        cache_key = f"{symbol}_{category.value}"
        now = time.time()
        if (
            cache_key in self._fee_cache
            and (now - self._fee_cache[cache_key]["timestamp"]) < self._fee_cache_ttl
        ):
            logger.debug("Using cached fee rate for %s", cache_key)
            return self._fee_cache[cache_key]["data"]

        resp = self.api_request(
            "GET",
            "/v5/account/fee-rate",
            params={"category": category.value, "symbol": symbol},
        )

        # Snippet 14: Improve default handling and return formatted dictionary
        fee_list = resp.get("result", {}).get("list", [{}])
        taker_fee = _safe_float(fee_list[0].get("takerFeeRate", 0.0006))
        maker_fee = _safe_float(fee_list[0].get("makerFeeRate", 0.0002))
        fee_data = {"takerFeeRate": taker_fee, "makerFeeRate": maker_fee}

        # Store in cache
        self._fee_cache[cache_key] = {"data": fee_data, "timestamp": now}
        return fee_data

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
            params["category"] = category
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
            "category": category,
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
        payload: Dict[str, Any] = {"category": category, "mode": mode}
        if symbol:
            payload["symbol"] = symbol
        if coin:
            payload["coin"] = coin

        return self.api_request("POST", "/v5/position/switch-mode", json_data=payload)

    # ══════════════════════════════════════════════════════════
    # MARKET DATA
    # ══════════════════════════════════════════════════════════
    def get_ticker(self, symbol: str, category: Category = Category.LINEAR) -> dict:
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        return items[0] if items else {}

    def get_orderbook(
        self, symbol: str, limit: int = 25, category: Category = Category.LINEAR
    ) -> dict:
        """Get market depth.
        Args:
            symbol: Trading symbol
            limit: Orderbook limit
            category: Market category
        """
        return self.api_request(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )

        return self.api_request(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )

    def get_mark_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Get current mark price and funding info via tickers endpoint."""
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
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
            "last_price": float(item.get("lastPrice", 0)),
            "funding_rate": float(item.get("fundingRate", 0)),
            "next_funding_time": int(item.get("nextFundingTime", 0)),
        }

    def get_index_price(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Get index price for a symbol via tickers endpoint."""
        resp = self.api_request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            return {}
        item = items[0]
        return {
            "symbol": symbol,
            "index_price": float(item.get("indexPrice", 0)),
            "mark_price": float(item.get("markPrice", 0)),
            "last_price": float(item.get("lastPrice", 0)),
        }

    def get_24hr_ticker(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        """Get 24-hour ticker statistics for one or all symbols."""
        params: Dict[str, Any] = {"category": category}
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
                    "last_price": _safe_float(item.get("lastPrice")) or 0.0,
                    "bid1_price": _safe_float(item.get("bid1Price")) or 0.0,
                    "ask1_price": _safe_float(item.get("ask1Price")) or 0.0,
                    "price_24h_change": (_safe_float(item.get("price24hPcnt")) or 0.0)
                    * 100,
                    "price_24h_high": _safe_float(item.get("highPrice24h")) or 0.0,
                    "price_24h_low": _safe_float(item.get("lowPrice24h")) or 0.0,
                    "volume_24h": _safe_float(item.get("volume24h")) or 0.0,
                    "turnover_24h": _safe_float(item.get("turnover24h")) or 0.0,
                    "open_interest": _safe_float(item.get("openInterest")) or 0.0,
                    "funding_rate": _safe_float(item.get("fundingRate")) or 0.0,
                    "next_funding_time": int(item.get("nextFundingTime") or 0),
                }
            )
        return results

    def get_price_bands(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Get upper/lower price limits."""
        resp = self.api_request(
            "GET",
            "/v5/market/price-limit",
            params={"category": category, "symbol": symbol},
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
        """Fetch klines for a symbol."""
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
        interval = mapping.get(str(interval).lower(), interval)
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["start"] = int(start_time)
        if end_time is not None:
            params["end"] = int(end_time)
        resp = self.api_request("GET", "/v5/market/kline", params=params, signed=False)
        return resp.get("result", {}).get("list", [])

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
            params={"category": category, "symbol": symbol, "limit": limit},
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
                "category": category,
                "symbol": symbol,
                "period": period,
                "limit": limit,
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
            params={"category": category, "symbol": symbol, "limit": 1},
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
            spread_score = (
                max(0, min(25, 25 - (spread * 500))) if spread != 999 else 0
            )  # Handle initial high spread

            # Depth analysis (bid+ask volume in top 25 levels)
            ob_result = ob.get("result", {})
            bid_depth = sum(float(b[1]) for b in ob_result.get("b", []))
            ask_depth = sum(float(a[1]) for a in ob_result.get("a", []))
            total_depth = bid_depth + ask_depth
            depth_imbalance = abs(bid_depth - ask_depth) / max(
                total_depth, 1e-9
            )  # Prevent division by zero
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

    # ══════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ══════════════════════════════════════════════════════════
    def calculate_vwap(self, ohlcv_list: List[dict]) -> float:
        """Calculate Volume Weighted Average Price (VWAP)."""
        if not ohlcv_list:
            return 0.0
        total_vol_price = 0.0
        total_vol = 0.0
        for k in ohlcv_list:
            # Bybit klines might use different keys depending on how they are fetched
            high = float(k.get("high") or k[2])
            low = float(k.get("low") or k[3])
            close = float(k.get("close") or k[4])
            vol = float(k.get("volume") or k[5])

            typical_price = (high + low + close) / 3
            total_vol_price += typical_price * vol
            total_vol += vol
        return round(total_vol_price / total_vol, 4) if total_vol > 0 else 0.0

    def calculate_ichimoku_cloud(
        self,
        highs: List[float],
        lows: List[float],
        tenkan_period: int = 9,
        kijun_period: int = 26,
        senkou_b_period: int = 52,
    ) -> dict:
        """Calculate Ichimoku Cloud components."""
        if len(highs) < senkou_b_period:
            return {"tenkan": 0.0, "kijun": 0.0, "senkou_a": 0.0, "senkou_b": 0.0}

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

    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        # Snippet 15: Validate RSI period
        if period <= 0:
            logger.warning(
                "RSI period must be positive. Got %d. Using default 14.", period
            )
            period = 14

        if len(prices) < period + 1:  # Need at least period + 1 prices for deltas
            logger.warning(
                "Not enough data points for RSI calculation (need %d, got %d)",
                period + 1,
                len(prices),
            )
            return 50.0  # Return neutral default

        # Check if all prices are the same
        if all(p == prices[0] for p in prices):
            logger.info(
                "Constant price detected for RSI calculation. Returning neutral 50.0."
            )
            return 50.0

        deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_ema(self, prices: List[float], period: int = 20) -> float:
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    def calculate_atr(self, ohlcv_list: List[dict], period: int = 14) -> float:
        if (
            len(ohlcv_list) < period + 1
        ):  # Need period + 1 bars to calculate period-length ATR
            logger.warning(
                "Not enough data points for ATR calculation (need %d, got %d)",
                period + 1,
                len(ohlcv_list),
            )
            return 0.0  # Return zero default

        tr_list = []
        for i in range(1, len(ohlcv_list)):
            high = float(ohlcv_list[i]["high"])
            low = float(ohlcv_list[i]["low"])
            prev_close = float(ohlcv_list[i - 1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        return sum(tr_list[-period:]) / period

    def calculate_bollinger_bands(
        self, prices: List[float], period: int = 20, std_dev: float = 2.0
    ) -> dict:
        if len(prices) < period:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}
        subset = prices[-period:]
        mean = sum(subset) / period
        variance = sum((x - mean) ** 2 for x in subset) / period
        std = math.sqrt(variance)
        return {
            "middle": round(mean, 4),
            "upper": round(mean + (std * std_dev), 4),
            "lower": round(mean - (std * std_dev), 4),
        }

    def calculate_macd(
        self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> dict:
        # Snippet 16: Validate MACD periods
        if not (fast > 0 and slow > 0 and signal > 0 and slow > fast):
            logger.warning(
                "Invalid periods for MACD calculation (fast=%d, slow=%d, signal=%d). Using defaults.",
                fast,
                slow,
                signal,
            )
            fast, slow, signal = 12, 26, 9

        if len(prices) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

        def get_ema_series(data, p):
            k = 2 / (p + 1)
            ema_list = [data[0]]
            for val in data[1:]:
                ema_list.append(val * k + ema_list[-1] * (1 - k))
            return ema_list

        fast_ema = get_ema_series(prices, fast)
        slow_ema = get_ema_series(prices, slow)
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
        prices: List[float],
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> dict:
        """Calculate Stochastic RSI with proper sliding window.

        Args:
            prices: List of closing prices
            period: RSI period (typically 14)
            smooth_k: %K smoothing period (typically 3)
            smooth_d: %D smoothing period (typically 3)
        """
        if len(prices) < period + smooth_k + smooth_d:
            return {"stoch_rsi": 0.0, "k": 0.0, "d": 0.0, "rsi": 50.0}

        # Calculate RSI values using sliding window
        rsi_values = []
        for i in range(period, len(prices) + 1):
            window = prices[i - period : i]
            rsi_val = self.calculate_rsi(window, period=period)
            rsi_values.append(rsi_val)

        if len(rsi_values) < smooth_k:
            return {
                "stoch_rsi": 0.0,
                "k": 0.0,
                "d": 0.0,
                "rsi": round(rsi_values[-1], 2),
            }

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
            "rsi": round(rsi_values[-1], 2) if rsi_values else 50.0,
        }

    def calculate_cci(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 20,
    ) -> float:
        if len(closes) < period:
            return 0.0
        tp = [
            (h + l + c) / 3
            for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])
        ]
        sma = sum(tp) / period
        mean_dev = sum(abs(x - sma) for x in tp) / period
        return round((tp[-1] - sma) / (0.015 * mean_dev) if mean_dev != 0 else 0.0, 2)

    def calculate_donchian_channels(
        self, highs: List[float], lows: List[float], period: int = 20
    ) -> dict:
        if len(highs) < period:
            return {"upper": 0.0, "lower": 0.0}
        return {
            "upper": round(max(highs[-period:]), 4),
            "lower": round(min(lows[-period:]), 4),
        }

    def calculate_adx(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> float:
        if len(closes) < period * 2:
            return 0.0
        tr_list, pos_dm, neg_dm = [], [], []
        for i in range(1, len(closes)):
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
        sum_pos = sum(pos_dm[-period:])
        sum_neg = sum(neg_dm[-period:])
        denom = sum_pos + sum_neg + 1e-9
        adx = 100 * abs(sum_pos - sum_neg) / denom
        return round(adx, 2)

    def calculate_fib_pivots(self, high: float, low: float, close: float) -> dict:
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
    # ADDITIONAL INDICATORS (20 new)
    # ══════════════════════════════════════════════════════════

    def calculate_obv(self, closes: List[float], volumes: List[float]) -> List[float]:
        """On-Balance Volume: cumulative volume weighted by price direction."""
        if len(closes) < 2 or len(volumes) < 2:
            return [0.0]
        n = min(len(closes), len(volumes))
        obv = [0.0]
        for i in range(1, n):
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])
        return obv

    def calculate_cvd(self, trades: List[dict]) -> dict:
        """Cumulative Volume Delta from recent trades."""
        buy_vol = 0.0
        sell_vol = 0.0
        cvd_series: List[float] = []
        running = 0.0
        for t in trades:
            qty = float(t.get("size", 0))
            side = t.get("side", "").lower()
            if side == "buy":
                buy_vol += qty
                running += qty
            else:
                sell_vol += qty
                running -= qty
            cvd_series.append(running)
        return {
            "cvd": round(running, 4),
            "buy_vol": round(buy_vol, 4),
            "sell_vol": round(sell_vol, 4),
            "delta": round(buy_vol - sell_vol, 4),
            "series_len": len(cvd_series),
            "cvd_last5": [round(v, 4) for v in cvd_series[-5:]],
        }

    def calculate_mfi(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        period: int = 14,
    ) -> float:
        """Money Flow Index: volume-weighted RSI (0-100)."""
        n = min(len(highs), len(lows), len(closes), len(volumes))
        if n < period + 1:
            return 50.0
        tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(n)]
        pos_flow = 0.0
        neg_flow = 0.0
        for i in range(n - period, n):
            mf = tp[i] * volumes[i]
            if tp[i] > tp[i - 1]:
                pos_flow += mf
            elif tp[i] < tp[i - 1]:
                neg_flow += mf
        if neg_flow == 0:
            return 100.0
        ratio = pos_flow / neg_flow
        return round(100 - (100 / (1 + ratio)), 2)

    def calculate_cmf(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        period: int = 20,
    ) -> float:
        """Chaikin Money Flow: accumulation/distribution pressure (-1 to +1)."""
        n = min(len(highs), len(lows), len(closes), len(volumes))
        if n < period:
            return 0.0
        mfv_sum = 0.0
        vol_sum = 0.0
        for i in range(n - period, n):
            hl = highs[i] - lows[i]
            if hl == 0:
                mf_mult = 0.0
            else:
                mf_mult = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / hl
            mfv_sum += mf_mult * volumes[i]
            vol_sum += volumes[i]
        return round(mfv_sum / vol_sum, 4) if vol_sum > 0 else 0.0

    def calculate_williams_r(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> float:
        """Williams %R: overbought/oversold oscillator (-100 to 0)."""
        if len(closes) < period:
            return -50.0
        hh = max(highs[-period:])
        ll = min(lows[-period:])
        if hh == ll:
            return -50.0
        return round(-100 * (hh - closes[-1]) / (hh - ll), 2)

    def calculate_parabolic_sar(
        self,
        highs: List[float],
        lows: List[float],
        af_start: float = 0.02,
        af_step: float = 0.02,
        af_max: float = 0.2,
    ) -> dict:
        """Parabolic SAR: trend-following stop-and-reverse levels."""
        n = len(highs)
        if n < 3:
            return {"sar": 0.0, "trend": "NEUTRAL"}
        bull = True
        sar = lows[0]
        ep = highs[0]
        af = af_start
        sar_values = [sar]
        for i in range(1, n):
            prev_sar = sar
            sar = prev_sar + af * (ep - prev_sar)
            if bull:
                sar = min(sar, lows[i - 1])
                if i >= 2:
                    sar = min(sar, lows[i - 2])
                if lows[i] < sar:
                    bull = False
                    sar = ep
                    ep = lows[i]
                    af = af_start
                elif highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_step, af_max)
            else:
                sar = max(sar, highs[i - 1])
                if i >= 2:
                    sar = max(sar, highs[i - 2])
                if highs[i] > sar:
                    bull = True
                    sar = ep
                    ep = highs[i]
                    af = af_start
                elif lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_step, af_max)
            sar_values.append(sar)
        return {
            "sar": round(sar_values[-1], 6),
            "trend": "BULLISH" if bull else "BEARISH",
            "ep": round(ep, 6),
            "af": round(af, 4),
        }

    def calculate_keltner_channels(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        ema_period: int = 20,
        atr_period: int = 14,
        atr_mult: float = 2.0,
    ) -> dict:
        """Keltner Channels: EMA-based volatility bands."""
        ema = self.calculate_ema(closes, ema_period)
        ohlcv = [
            {"high": h, "low": l, "close": c} for h, l, c in zip(highs, lows, closes)
        ]
        atr = self.calculate_atr(ohlcv, atr_period)
        return {
            "upper": round(ema + atr_mult * atr, 6),
            "middle": round(ema, 6),
            "lower": round(ema - atr_mult * atr, 6),
            "atr": round(atr, 6),
        }

    def calculate_roc(self, prices: List[float], period: int = 12) -> float:
        """Rate of Change: percentage change over N periods."""
        if len(prices) <= period or prices[-period - 1] == 0:
            return 0.0
        return round(
            ((prices[-1] - prices[-period - 1]) / prices[-period - 1]) * 100, 4
        )

    def calculate_trix(self, prices: List[float], period: int = 15) -> float:
        """TRIX: rate of change of triple-smoothed EMA."""
        if len(prices) < period * 3 + 1:
            return 0.0
        e1 = prices[:]
        for _ in range(3):
            smoothed = []
            mult = 2 / (period + 1)
            ema_val = sum(e1[:period]) / period
            smoothed.append(ema_val)
            for p in e1[period:]:
                ema_val = (p - ema_val) * mult + ema_val
                smoothed.append(ema_val)
            e1 = smoothed
        if len(e1) < 2 or e1[-2] == 0:
            return 0.0
        return round(((e1[-1] - e1[-2]) / e1[-2]) * 10000, 4)

    def calculate_ultimate_oscillator(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        p1: int = 7,
        p2: int = 14,
        p3: int = 28,
    ) -> float:
        """Ultimate Oscillator: multi-timeframe momentum (0-100)."""
        n = len(closes)
        if n < p3 + 1:
            return 50.0
        bp_list = []
        tr_list = []
        for i in range(1, n):
            bp = closes[i] - min(lows[i], closes[i - 1])
            tr = max(highs[i], closes[i - 1]) - min(lows[i], closes[i - 1])
            bp_list.append(bp)
            tr_list.append(tr)

        def avg_ratio(period: int) -> float:
            bp_s = sum(bp_list[-period:])
            tr_s = sum(tr_list[-period:])
            return bp_s / tr_s if tr_s > 0 else 0.5

        a1 = avg_ratio(p1)
        a2 = avg_ratio(p2)
        a3 = avg_ratio(p3)
        uo = 100 * (4 * a1 + 2 * a2 + a3) / 7
        return round(uo, 2)

    def calculate_choppiness_index(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14,
    ) -> float:
        """Choppiness Index: 0-100, high = choppy/ranging, low = trending."""
        n = len(closes)
        if n < period + 1:
            return 50.0
        atr_sum = 0.0
        for i in range(n - period, n):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            atr_sum += tr
        hh = max(highs[n - period : n])
        ll = min(lows[n - period : n])
        hl_range = hh - ll
        if hl_range <= 0:
            return 50.0
        ci = 100 * math.log10(atr_sum / hl_range) / math.log10(period)
        return round(ci, 2)

    def calculate_aroon(
        self, highs: List[float], lows: List[float], period: int = 25
    ) -> dict:
        """Aroon Oscillator: trend strength and direction."""
        if len(highs) < period + 1:
            return {"aroon_up": 50.0, "aroon_down": 50.0, "oscillator": 0.0}
        h_slice = highs[-(period + 1) :]
        l_slice = lows[-(period + 1) :]
        days_since_high = period - h_slice.index(max(h_slice))
        days_since_low = period - l_slice.index(min(l_slice))
        aroon_up = (period - days_since_high) / period * 100
        aroon_down = (period - days_since_low) / period * 100
        return {
            "aroon_up": round(aroon_up, 2),
            "aroon_down": round(aroon_down, 2),
            "oscillator": round(aroon_up - aroon_down, 2),
        }

    def calculate_dpo(self, closes: List[float], period: int = 20) -> float:
        """Detrended Price Oscillator: removes trend to show cycles."""
        shift = period // 2 + 1
        if len(closes) < period + shift:
            return 0.0
        sma_idx = -(shift + 1)
        sma_slice = closes[sma_idx - period + 1 : sma_idx + 1]
        sma = sum(sma_slice) / period if len(sma_slice) == period else 0.0
        return round(closes[-(shift + 1)] - sma, 6)

    def calculate_hma(self, prices: List[float], period: int = 9) -> float:
        """Hull Moving Average: fast, smooth trend-following average."""
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        half = period // 2
        sqrt_p = max(1, int(math.sqrt(period)))
        wma_half = self._wma(prices, half)
        wma_full = self._wma(prices, period)
        if not wma_half or not wma_full:
            return prices[-1]
        diff_series = [
            2 * h - f for h, f in zip(wma_half[-sqrt_p:], wma_full[-sqrt_p:])
        ]
        if len(diff_series) < sqrt_p:
            return diff_series[-1] if diff_series else prices[-1]
        result = self._wma(diff_series, sqrt_p)
        return round(result[-1], 6) if result else round(prices[-1], 6)

    def _wma(self, prices: List[float], period: int) -> List[float]:
        """Weighted Moving Average helper."""
        if len(prices) < period:
            return prices[:]
        results = []
        denom = period * (period + 1) / 2
        for i in range(period - 1, len(prices)):
            wsum = sum(prices[i - period + 1 + j] * (j + 1) for j in range(period))
            results.append(wsum / denom)
        return results

    def calculate_zlema(self, prices: List[float], period: int = 21) -> float:
        """Zero-Lag EMA: reduces EMA lag using price momentum."""
        if len(prices) < period:
            return prices[-1] if prices else 0.0
        lag = (period - 1) // 2
        adjusted = [
            prices[i] + (prices[i] - prices[i - lag]) if i >= lag else prices[i]
            for i in range(len(prices))
        ]
        return self.calculate_ema(adjusted, period)

    def calculate_supertrend(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 10,
        multiplier: float = 3.0,
    ) -> dict:
        """Supertrend: trend-following overlay with dynamic support/resistance."""
        n = len(closes)
        if n < period + 1:
            return {"supertrend": closes[-1] if closes else 0.0, "trend": "NEUTRAL"}
        ohlcv = [
            {"high": h, "low": l, "close": c} for h, l, c in zip(highs, lows, closes)
        ]
        atr_val = self.calculate_atr(ohlcv, period)

        # Snippet 17: Handle zero ATR
        if atr_val == 0:
            logger.warning(
                "ATR value is zero. Supertrend calculation may be inaccurate."
            )
            return {"supertrend": closes[-1] if closes else 0.0, "trend": "NEUTRAL"}

        hl2 = (highs[-1] + lows[-1]) / 2
        upper = hl2 + multiplier * atr_val
        lower = hl2 - multiplier * atr_val
        in_uptrend = closes[-1] > hl2
        st = lower if in_uptrend else upper
        return {
            "supertrend": round(st, 6),
            "upper_band": round(upper, 6),
            "lower_band": round(lower, 6),
            "trend": "BULLISH" if in_uptrend else "BEARISH",
        }

    def calculate_linear_regression_slope(
        self, prices: List[float], period: int = 20
    ) -> float:
        """Linear Regression Slope: rate of price change per bar."""
        if len(prices) < period:
            return 0.0
        y = prices[-period:]
        x_mean = (period - 1) / 2
        y_mean = sum(y) / period
        num = sum((i - x_mean) * (y[i] - y_mean) for i in range(period))
        den = sum((i - x_mean) ** 2 for i in range(period))
        return round(num / den, 6) if den != 0 else 0.0

    def calculate_stddev(self, prices: List[float], period: int = 20) -> float:
        """Standard Deviation of prices over period (volatility measure)."""
        if len(prices) < period:
            return 0.0
        window = prices[-period:]
        mean = sum(window) / period
        variance = sum((p - mean) ** 2 for p in window) / period
        return round(math.sqrt(variance), 6)

    def calculate_vroc(self, volumes: List[float], period: int = 14) -> float:
        """Volume Rate of Change: percentage change in volume over N bars."""
        if len(volumes) <= period or volumes[-period - 1] == 0:
            return 0.0
        return round(
            ((volumes[-1] - volumes[-period - 1]) / volumes[-period - 1]) * 100, 2
        )

    def calculate_elder_ray(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 13,
    ) -> dict:
        """Elder Ray: bull power (highs vs EMA) and bear power (lows vs EMA)."""
        ema = self.calculate_ema(closes, period)
        return {
            "bull_power": round(highs[-1] - ema, 6),
            "bear_power": round(lows[-1] - ema, 6),
            "ema": round(ema, 6),
        }

    def calculate_vwma(
        self, closes: List[float], volumes: List[float], period: int = 20
    ) -> float:
        """Volume Weighted Moving Average."""
        n = min(len(closes), len(volumes))
        if n < period:
            return closes[-1] if closes else 0.0
        pv_sum = sum(
            closes[n - period + i] * volumes[n - period + i] for i in range(period)
        )
        v_sum = sum(volumes[n - period : n])
        return round(pv_sum / v_sum, 6) if v_sum > 0 else round(closes[-1], 6)

    def calculate_awesome_oscillator(
        self, highs: List[float], lows: List[float], fast: int = 5, slow: int = 34
    ) -> float:
        """Awesome Oscillator: difference between 5-period and 34-period SMA of midpoints."""
        n = min(len(highs), len(lows))
        if n < slow:
            return 0.0
        mids = [(highs[i] + lows[i]) / 2 for i in range(n)]
        fast_sma = sum(mids[-fast:]) / fast
        slow_sma = sum(mids[-slow:]) / slow
        return round(fast_sma - slow_sma, 6)

    def calculate_accumulation_distribution(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
    ) -> float:
        """Accumulation/Distribution Line: current value."""
        n = min(len(highs), len(lows), len(closes), len(volumes))
        if n == 0:
            return 0.0
        ad = 0.0
        for i in range(n):
            hl = highs[i] - lows[i]
            if hl > 0:
                clv = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / hl
            else:
                clv = 0.0
            ad += clv * volumes[i]
        return round(ad, 2)

    # ══════════════════════════════════════════════════════════
    # ENHANCED VOLUME ANALYSIS
    # ══════════════════════════════════════════════════════════

    def get_volume_profile(
        self,
        symbol: str,
        num_bins: int = 20,
        lookback: int = 200,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Volume Profile: distribution of volume across price levels (POC, VAH, VAL)."""
        try:
            klines = self.get_klines(
                symbol, interval="15", limit=lookback, category=category
            )
            if not klines or len(klines) < 10:
                return {"status": "error", "msg": "Insufficient kline data"}
            klines.reverse()
            all_highs = [float(k[2]) for k in klines]
            all_lows = [float(k[3]) for k in klines]
            all_closes = [float(k[4]) for k in klines]
            all_vols = [float(k[5]) for k in klines]
            price_high = max(all_highs)
            price_low = min(all_lows)
            if price_high == price_low:
                logger.warning(
                    "Price range is zero for %s. Cannot calculate volume profile.",
                    symbol,
                )
                return {"status": "error", "msg": "No price range"}
            bin_size = (price_high - price_low) / num_bins
            bins: Dict[int, float] = dict.fromkeys(range(num_bins), 0.0)
            for i in range(len(klines)):
                tp = (all_highs[i] + all_lows[i] + all_closes[i]) / 3
                bin_idx = min(int((tp - price_low) / bin_size), num_bins - 1)
                bins[bin_idx] += all_vols[i]
            total_vol = sum(bins.values())
            if total_vol == 0:
                logger.warning(
                    "Total volume is zero for %s. Cannot calculate profile percentages.",
                    symbol,
                )
                # Return profile with zero volume and percentages
                return {
                    "status": "success",
                    "total_volume": 0.0,
                    "profile": [
                        {
                            "price": round(price_low + (i + 0.5) * bin_size, 6),
                            "volume": 0.0,
                            "pct": 0.0,
                            "in_value_area": False,
                        }
                        for i in range(num_bins)
                    ],
                }
            poc_idx = max(bins, key=bins.get)
            poc_price = price_low + (poc_idx + 0.5) * bin_size
            sorted_bins = sorted(bins.items(), key=lambda x: x[1], reverse=True)
            cum_vol = 0.0
            va_bins = set()
            for idx, vol in sorted_bins:
                cum_vol += vol
                va_bins.add(idx)
                if cum_vol >= total_vol * 0.70:
                    break
            vah = price_low + (max(va_bins) + 1) * bin_size
            val_price = price_low + min(va_bins) * bin_size
            profile = []
            for i in range(num_bins):
                level_price = price_low + (i + 0.5) * bin_size
                pct = (bins[i] / total_vol * 100) if total_vol > 0 else 0
                profile.append(
                    {
                        "price": round(level_price, 6),
                        "volume": round(bins[i], 2),
                        "pct": round(pct, 2),
                        "in_value_area": i in va_bins,
                    }
                )
            return {
                "symbol": symbol,
                "poc_price": round(poc_price, 6),
                "vah": round(vah, 6),
                "val": round(val_price, 6),
                "price_high": round(price_high, 6),
                "price_low": round(price_low, 6),
                "total_volume": round(total_vol, 2),
                "num_bars": len(klines),
                "profile": profile,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_volume_divergence(
        self,
        symbol: str,
        lookback: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detect price/volume divergence (bullish/bearish)."""
        try:
            klines = self.get_klines(
                symbol, interval="15", limit=lookback, category=category
            )
            if not klines or len(klines) < 20:
                return {"status": "error", "msg": "Insufficient data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            price_slope = self.calculate_linear_regression_slope(
                closes, min(20, len(closes))
            )
            vol_slope = self.calculate_linear_regression_slope(
                volumes, min(20, len(volumes))
            )
            obv = self.calculate_obv(closes, volumes)
            obv_slope = self.calculate_linear_regression_slope(obv, min(20, len(obv)))
            divergence = "NONE"
            if price_slope > 0 and vol_slope < 0:
                divergence = "BEARISH_DIVERGENCE"
            elif price_slope < 0 and vol_slope > 0:
                divergence = "BULLISH_DIVERGENCE"
            elif price_slope > 0 and obv_slope < 0:
                divergence = "BEARISH_OBV_DIVERGENCE"
            elif price_slope < 0 and obv_slope > 0:
                divergence = "BULLISH_OBV_DIVERGENCE"
            vol_avg_20 = sum(volumes[-20:]) / 20
            vol_avg_5 = sum(volumes[-5:]) / 5
            vol_surge = vol_avg_5 / vol_avg_20 if vol_avg_20 > 0 else 1.0
            return {
                "symbol": symbol,
                "divergence": divergence,
                "price_slope": round(price_slope, 6),
                "volume_slope": round(vol_slope, 6),
                "obv_slope": round(obv_slope, 6),
                "obv_current": round(obv[-1], 2),
                "vol_surge_ratio": round(vol_surge, 4),
                "vol_avg_5": round(vol_avg_5, 2),
                "vol_avg_20": round(vol_avg_20, 2),
                "current_price": round(closes[-1], 6),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # ENHANCED L2 ORDERBOOK ANALYSIS
    # ══════════════════════════════════════════════════════════

    def get_orderbook_heatmap(
        self,
        symbol: str,
        depth: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Orderbook heatmap: concentration zones, absorption detection, spoofing heuristic."""
        try:
            ob = self.get_orderbook(symbol, limit=depth, category=category)
            ob_result = ob.get("result", {})
            # Snippet 18: Handle malformed bid/ask data
            bids = [(float(b[0]), float(b[1])) for b in ob_result.get("b", [])]
            asks = [(float(a[0]), float(a[1])) for a in ob_result.get("a", [])]

            if not bids or not asks:
                return {"status": "error", "msg": "Empty orderbook"}

            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid = (best_bid + best_ask) / 2

            all_sizes = [b[1] for b in bids] + [a[1] for a in asks]
            avg_size = sum(all_sizes) / len(all_sizes) if all_sizes else 1.0
            std_size = math.sqrt(
                sum((s - avg_size) ** 2 for s in all_sizes) / max(len(all_sizes), 1)
            )

            wall_threshold = avg_size + 2.5 * std_size
            bid_walls = [
                {
                    "price": b[0],
                    "size": b[1],
                    "notional": round(b[0] * b[1], 2),
                    "sigma": round((b[1] - avg_size) / max(std_size, 1e-9), 2),
                }
                for b in bids
                if b[1] >= wall_threshold
            ]
            ask_walls = [
                {
                    "price": a[0],
                    "size": a[1],
                    "notional": round(a[0] * a[1], 2),
                    "sigma": round((a[1] - avg_size) / max(std_size, 1e-9), 2),
                }
                for a in asks
                if a[1] >= wall_threshold
            ]

            # Bid-ask ratio at each depth tier
            tiers = [5, 10, 25, 50]
            tier_ratios = {}
            for t in tiers:
                if t > len(bids) or t > len(asks):
                    continue
                b_vol = sum(b[1] for b in bids[:t])
                a_vol = sum(a[1] for a in asks[:t])
                tier_ratios[f"top_{t}"] = {
                    "bid_vol": round(b_vol, 4),
                    "ask_vol": round(a_vol, 4),
                    "ratio": round(b_vol / max(a_vol, 1e-9), 4),
                    "imbalance": round((b_vol - a_vol) / max(b_vol + a_vol, 1e-9), 4),
                }
            # Concentration zones: cluster dense bids/asks within 0.1% bands
            band_pct = 0.001
            bid_clusters: Dict[int, float] = {}
            for b in bids:
                band = int((mid - b[0]) / (mid * band_pct))
                bid_clusters[band] = bid_clusters.get(band, 0) + b[1]
            ask_clusters: Dict[int, float] = {}
            for a in asks:
                band = int((a[0] - mid) / (mid * band_pct))
                ask_clusters[band] = ask_clusters.get(band, 0) + a[1]

            top_bid_zones = sorted(
                bid_clusters.items(), key=lambda x: x[1], reverse=True
            )[:5]
            top_ask_zones = sorted(
                ask_clusters.items(), key=lambda x: x[1], reverse=True
            )[:5]

            bid_zones = [
                {"distance_pct": round(k * 0.1, 2), "volume": round(v, 4)}
                for k, v in top_bid_zones
            ]
            ask_zones = [
                {"distance_pct": round(k * 0.1, 2), "volume": round(v, 4)}
                for k, v in top_ask_zones
            ]

            # Spoofing heuristic: large orders far from mid that are disproportionate
            far_threshold = mid * 0.02  # 2% from mid
            near_bid_vol = sum(b[1] for b in bids if mid - b[0] < far_threshold)
            far_bid_vol = sum(b[1] for b in bids if mid - b[0] >= far_threshold)
            near_ask_vol = sum(a[1] for a in asks if a[0] - mid < far_threshold)
            far_ask_vol = sum(a[1] for a in asks if a[0] - mid >= far_threshold)
            spoof_score = 0
            if far_bid_vol > near_bid_vol * 2:
                spoof_score += 1
            if far_ask_vol > near_ask_vol * 2:
                spoof_score += 1

            return {
                "symbol": symbol,
                "mid_price": round(mid, 8),
                "spread_bps": round((best_ask - best_bid) / mid * 10000, 2),
                "avg_level_size": round(avg_size, 4),
                "size_std_dev": round(std_size, 4),
                "wall_threshold": round(wall_threshold, 4),
                "bid_walls": bid_walls[:5],
                "ask_walls": ask_walls[:5],
                "tier_ratios": tier_ratios,
                "bid_concentration_zones": bid_zones,
                "ask_concentration_zones": ask_zones,
                "spoof_risk_score": spoof_score,
                "spoof_note": "0=low, 1=moderate, 2=high",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_orderflow_analysis(
        self,
        symbol: str,
        depth: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Combined order flow: L2 book + recent trades CVD + momentum for trade decisions."""
        try:
            ob_analysis = self.get_l2_orderbook_analysis(
                symbol, depth=depth, category=category
            )
            trades = self.get_recent_trades(symbol, limit=500, category=category)
            cvd = (
                self.calculate_cvd(trades)
                if trades
                else {"cvd": 0, "buy_vol": 0, "sell_vol": 0, "delta": 0}
            )
            momentum = self.get_market_momentum(symbol, category=category)

            ob_pressure = ob_analysis.get("pressure", "BALANCED")
            cvd_val = cvd.get("cvd", 0)
            mom_signal = momentum.get("signal", "NEUTRAL")

            # Composite score
            score = 0
            if "BUY" in ob_pressure:
                score += 30 if "STRONG" in ob_pressure else 15
            elif "SELL" in ob_pressure:
                score -= 30 if "STRONG" in ob_pressure else 15

            # Safely calculate score contributions from CVD
            sell_vol_cvd = abs(
                cvd.get("sell_vol", 1)
            )  # Default to 1 if missing to prevent division by zero
            buy_vol_cvd = cvd.get("buy_vol", 1)  # Default to 1 if missing

            # Ensure denominator is not zero before division
            if sell_vol_cvd > 0:
                score += min(25, int(cvd_val / sell_vol_cvd * 25))
            elif cvd_val > 0:  # Handle case where sell_vol is zero but cvd is positive
                score += 25  # Max score if positive CVD and zero sell volume

            if buy_vol_cvd > 0:
                score -= min(25, int(abs(cvd_val) / buy_vol_cvd * 25))
            elif cvd_val < 0:  # Handle case where buy_vol is zero but cvd is negative
                score -= 25  # Min score if negative CVD and zero buy volume

            if "STRONG_BUY" in mom_signal:
                score += 20
            elif "BUY" in mom_signal:
                score += 10
            elif "STRONG_SELL" in mom_signal:
                score -= 20
            elif "SELL" in mom_signal:
                score -= 10

            if score >= 40:
                signal = "STRONG_BUY"
            elif score >= 15:
                signal = "BUY"
            elif score <= -40:
                signal = "STRONG_SELL"
            elif score <= -15:
                signal = "SELL"
            else:
                signal = "NEUTRAL"

            # Absorption detection: large volume at best bid/ask with no price movement
            absorption = "NONE"
            if trades and len(trades) >= 20:
                recent_prices = [float(t.get("price", 0)) for t in trades[-20:]]
                price_range = max(recent_prices) - min(recent_prices)
                total_vol = cvd.get("buy_vol", 0) + cvd.get("sell_vol", 0)
                if price_range < ob_analysis.get("spread", 1) * 2 and total_vol > 0:
                    if cvd.get("buy_vol", 0) > cvd.get("sell_vol", 0) * 1.5:
                        absorption = "ASK_ABSORPTION"
                    elif cvd.get("sell_vol", 0) > cvd.get("buy_vol", 0) * 1.5:
                        absorption = "BID_ABSORPTION"

            return {
                "symbol": symbol,
                "composite_score": score,
                "signal": signal,
                "orderbook_pressure": ob_pressure,
                "cvd": cvd,
                "momentum_signal": mom_signal,
                "momentum_imbalance": momentum.get("imbalance", 0),
                "absorption": absorption,
                "spread_bps": ob_analysis.get("spread_bps", 0),
                "top5_imbalance": ob_analysis.get("top5_imbalance", 0),
                "bid_walls": ob_analysis.get("bid_walls", [])[:3],
                "ask_walls": ob_analysis.get("ask_walls", [])[:3],
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # MICROPROFIT SCALPING
    # ══════════════════════════════════════════════════════════

    def macro_microprofit_scalp(
        self,
        symbol: str,
        risk_usdt: float = 5.0,
        min_edge_bps: float = 3.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Microprofit scalp: ultra-tight TP just above fees, uses spread + orderflow for edge.

        Only signals when spread is wide enough to cover round-trip fees and there
        is clear order flow imbalance.
        """
        try:
            precision = self.get_instrument_precision(
                symbol,
                category=category.value if isinstance(category, Category) else category,
            )
            if precision.get("status") == "error":
                return precision
            tick_size = precision["price"]["tick_size"]
            qty_step = precision["quantity"]["qty_step"]
            taker_fee = precision["fees"]["taker_fee_rate"]
            maker_fee = precision["fees"]["maker_fee_rate"]
            round_trip_taker_pct = precision["fees"]["round_trip_taker_pct"]

            ob = self.get_l2_orderbook_analysis(symbol, depth=25, category=category)
            if ob.get("status") == "error":
                return ob
            best_bid = ob["best_bid"]
            best_ask = ob["best_ask"]
            mid = ob["mid_price"]
            spread_bps = ob["spread_bps"]

            min_spread_bps = round_trip_taker_pct * 100 + min_edge_bps
            if spread_bps < min_spread_bps:
                return {
                    "symbol": symbol,
                    "strategy": "MICROPROFIT_SCALP",
                    "signal": "NO_TRADE",
                    "reason": f"Spread {spread_bps:.1f} bps < min required {min_spread_bps:.1f} bps (fees + edge)",
                    "spread_bps": round(spread_bps, 2),
                    "round_trip_fee_bps": round(round_trip_taker_pct * 100, 2),
                }

            orderflow = self.get_orderflow_analysis(symbol, depth=25, category=category)
            of_score = orderflow.get("composite_score", 0)
            of_signal = orderflow.get("signal", "NEUTRAL")

            if abs(of_score) < 10:
                return {
                    "symbol": symbol,
                    "strategy": "MICROPROFIT_SCALP",
                    "signal": "NO_TRADE",
                    "reason": f"Order flow too weak (score={of_score}), need >=10",
                    "orderflow_score": of_score,
                }

            side = "Buy" if of_score > 0 else "Sell"

            # Entry at best bid/ask (maker) for lower fees
            if side == "Buy":
                entry_price = best_bid + tick_size
                # TP = entry + spread - fees
                net_edge = spread_bps - round_trip_taker_pct * 100
                tp_distance = mid * (net_edge / 10000)
                tp_price = entry_price + tp_distance
                sl_price = entry_price - tp_distance * 1.5
            else:
                entry_price = best_ask - tick_size
                net_edge = spread_bps - round_trip_taker_pct * 100
                tp_distance = mid * (net_edge / 10000)
                tp_price = entry_price - tp_distance
                sl_price = entry_price + tp_distance * 1.5

            # Snap to tick
            entry_price = round(round(entry_price / tick_size) * tick_size, 8)
            tp_price = round(round(tp_price / tick_size) * tick_size, 8)
            sl_price = round(round(sl_price / tick_size) * tick_size, 8)

            qty = (
                risk_usdt / abs(entry_price - sl_price)
                if abs(entry_price - sl_price) > 0
                else 0
            )
            qty = round(round(qty / qty_step) * qty_step, 8)

            notional = entry_price * qty
            entry_fee = notional * maker_fee
            exit_fee = tp_price * qty * taker_fee
            total_fees = entry_fee + exit_fee
            gross_profit = abs(tp_price - entry_price) * qty
            net_profit = gross_profit - total_fees

            return {
                "symbol": symbol,
                "strategy": "MICROPROFIT_SCALP",
                "signal": side.upper(),
                "side": side,
                "entry_price": entry_price,
                "take_profit": tp_price,
                "stop_loss": sl_price,
                "qty": qty,
                "notional": round(notional, 4),
                "spread_bps": round(spread_bps, 2),
                "net_edge_bps": round(net_edge, 2),
                "entry_fee": round(entry_fee, 6),
                "exit_fee": round(exit_fee, 6),
                "total_fees": round(total_fees, 6),
                "gross_profit_est": round(gross_profit, 6),
                "net_profit_est": round(net_profit, 6),
                "profitable": net_profit > 0,
                "orderflow_score": of_score,
                "orderflow_signal": of_signal,
                "tick_size": tick_size,
                "note": "Use limit entry (maker fee), market TP (taker). Cancel if not filled within 5s.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # COMPREHENSIVE TREND ANALYSIS (all 32+ indicators)
    # ══════════════════════════════════════════════════════════

    def get_comprehensive_trend_analysis(
        self,
        symbol: str,
        interval: str = "15",
        lookback_periods: int = 200,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Full trend analysis using all 32+ indicators, volume profile, orderflow, and multi-timeframe alignment.

        Returns a -100 to +100 composite score with category weights:
        - Trend (25%): EMA stack, Supertrend, Parabolic SAR, HMA, ZLEMA, Ichimoku
        - Momentum (20%): RSI, MACD, Stoch RSI, Williams %R, Ultimate Osc, Awesome Osc, ROC, TRIX
        - Volatility (15%): BB, Keltner, ATR, Stddev, Choppiness, Donchian
        - Volume (20%): OBV, CMF, MFI, CVD, VWAP, VROC, A/D, volume divergence
        - Order Flow (10%): L2 imbalance, momentum
        - Market Structure (10%): ADX, Aroon, Elder Ray, CCI, DPO, Fib Pivots
        """
        try:
            klines = self.get_klines(
                symbol,
                interval=interval,
                limit=max(lookback_periods, 200),
                category=category,
            )
            if not klines or len(klines) < 50:
                return {
                    "status": "error",
                    "msg": f"Insufficient data ({len(klines) if klines else 0} bars, need 50+)",
                }
            klines.reverse()

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            current_price = closes[-1]

            # ── Trend Indicators (25 points max) ──
            trend_score = 0.0
            ema9 = self.calculate_ema(closes, 9)
            ema21 = self.calculate_ema(closes, 21)
            ema50 = self.calculate_ema(closes, 50)
            ema200 = self.calculate_ema(closes, 200)
            hma = self.calculate_hma(closes, 9)
            zlema = self.calculate_zlema(closes, 21)
            supertrend = self.calculate_supertrend(highs, lows, closes)
            psar = self.calculate_parabolic_sar(highs, lows)
            ichimoku = self.calculate_ichimoku_cloud(highs, lows)

            # EMA stack alignment (5pts)
            ema_stack = 0
            if ema9 > ema21:
                ema_stack += 1
            if ema21 > ema50:
                ema_stack += 1
            if ema50 > ema200:
                ema_stack += 1
            if current_price > ema21:
                ema_stack += 1
            trend_score += (ema_stack - 2) * 2.5  # -5 to +5 scaled

            # Supertrend (4pts)
            if supertrend["trend"] == "BULLISH":
                trend_score += 4
            else:
                trend_score -= 4

            # PSAR (4pts)
            if psar["trend"] == "BULLISH":
                trend_score += 4
            else:
                trend_score -= 4

            # HMA vs price (3pts)
            if current_price > hma:
                trend_score += 3
            else:
                trend_score -= 3

            # ZLEMA vs EMA21 (3pts)
            if zlema > ema21:
                trend_score += 3
            else:
                trend_score -= 3

            # Ichimoku cloud (6pts)
            ichi_score = 0
            if current_price > ichimoku.get("senkou_a", 0):
                ichi_score += 2
            if current_price > ichimoku.get("senkou_b", 0):
                ichi_score += 2
            if ichimoku.get("tenkan", 0) > ichimoku.get("kijun", 0):
                ichi_score += 2
            trend_score += ichi_score - 3  # center around 0

            # ── Momentum Indicators (20 points max) ──
            mom_score = 0.0
            rsi = self.calculate_rsi(closes)
            macd = self.calculate_macd(closes)
            stoch = self.calculate_stoch_rsi(closes)
            williams = self.calculate_williams_r(highs, lows, closes)
            uo = self.calculate_ultimate_oscillator(highs, lows, closes)
            ao = self.calculate_awesome_oscillator(highs, lows)
            roc = self.calculate_roc(closes)
            trix = self.calculate_trix(closes)

            # RSI (3pts)
            if rsi > 60:
                mom_score += min(3, (rsi - 50) / 10 * 3)
            elif rsi < 40:
                mom_score -= min(3, (50 - rsi) / 10 * 3)

            # MACD (3pts)
            if macd["macd"] > macd["signal"]:
                mom_score += 1.5
            else:
                mom_score -= 1.5
            if macd["macd"] > 0:
                mom_score += 1.5
            else:
                mom_score -= 1.5

            # Stoch RSI (2pts)
            if stoch["k"] > 50:
                mom_score += min(2, (stoch["k"] - 50) / 25 * 2)
            else:
                mom_score -= min(2, (50 - stoch["k"]) / 25 * 2)

            # Williams %R (2pts)
            if williams > -50:
                mom_score += min(2, (williams + 50) / 25 * 2)
            else:
                mom_score -= min(2, (-50 - williams) / 25 * 2)

            # Ultimate Oscillator (2pts)
            if uo > 50:
                mom_score += min(2, (uo - 50) / 25 * 2)
            else:
                mom_score -= min(2, (50 - uo) / 25 * 2)

            # Awesome Oscillator (2pts)
            if ao > 0:
                mom_score += 2
            else:
                mom_score -= 2

            # ROC (3pts)
            if roc > 0:
                mom_score += min(3, roc / 2)
            else:
                mom_score -= min(3, abs(roc) / 2)

            # TRIX (3pts)
            if trix > 0:
                mom_score += min(3, trix * 3)
            else:
                mom_score -= min(3, abs(trix) * 3)

            # ── Volatility Indicators (15 points max) ──
            vol_score = 0.0
            bb = self.calculate_bollinger_bands(closes)
            keltner = self.calculate_keltner_channels(highs, lows, closes)
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv)
            stddev = self.calculate_stddev(closes)
            chop = self.calculate_choppiness_index(highs, lows, closes)
            donchian = self.calculate_donchian_channels(highs, lows)

            # BB position (4pts)
            bb_range = bb["upper"] - bb["lower"] if bb["upper"] != bb["lower"] else 1
            bb_pos = (current_price - bb["lower"]) / bb_range
            vol_score += (bb_pos - 0.5) * 8  # -4 to +4

            # Keltner position (4pts)
            kelt_range = (
                keltner["upper"] - keltner["lower"]
                if keltner["upper"] != keltner["lower"]
                else 1
            )
            kelt_pos = (current_price - keltner["lower"]) / kelt_range
            vol_score += (kelt_pos - 0.5) * 8

            # Choppiness: low = trending (bonus), high = choppy (penalty) (4pts)
            if chop < 40:
                vol_score += 4  # strong trend
            elif chop < 50:
                vol_score += 2
            elif chop > 60:
                vol_score -= 2  # very choppy, reduce confidence
            # If choppy, dampen the overall score later

            # Donchian position (3pts)
            don_range = (
                donchian["upper"] - donchian["lower"]
                if donchian["upper"] != donchian["lower"]
                else 1
            )
            don_pos = (current_price - donchian["lower"]) / don_range
            vol_score += (don_pos - 0.5) * 6

            # ── Volume Indicators (20 points max) ──
            volume_score = 0.0
            obv = self.calculate_obv(closes, volumes)
            cmf = self.calculate_cmf(highs, lows, closes, volumes)
            mfi = self.calculate_mfi(highs, lows, closes, volumes)
            ohlcv_vwap = [
                {"high": h, "low": l, "close": c, "volume": v}
                for h, l, c, v in zip(highs, lows, closes, volumes)
            ]
            vwap = self.calculate_vwap(ohlcv_vwap)
            vroc = self.calculate_vroc(volumes)
            ad = self.calculate_accumulation_distribution(highs, lows, closes, volumes)

            # OBV slope (3pts)
            obv_slope = self.calculate_linear_regression_slope(obv, min(20, len(obv)))
            if obv_slope > 0:
                volume_score += 3
            else:
                volume_score -= 3

            # CMF (3pts)
            volume_score += min(3, max(-3, cmf * 15))

            # MFI (3pts)
            if mfi > 50:
                volume_score += min(3, (mfi - 50) / 16.7 * 3)
            else:
                volume_score -= min(3, (50 - mfi) / 16.7 * 3)

            # Price vs VWAP (3pts)
            if vwap > 0:
                if current_price > vwap:
                    volume_score += 3
                else:
                    volume_score -= 3

            # VROC (3pts)
            if vroc > 20:
                volume_score += 3
            elif vroc > 0:
                volume_score += 1
            elif vroc < -20:
                volume_score -= 3
            elif vroc < 0:
                volume_score -= 1

            # Volume surge (2pts)
            vol_avg = sum(volumes[-20:]) / min(len(volumes), 20) if volumes else 1
            if volumes[-1] > vol_avg * 1.5 and closes[-1] > closes[-2]:
                volume_score += 2
            elif volumes[-1] > vol_avg * 1.5 and closes[-1] < closes[-2]:
                volume_score -= 2

            # A/D direction (3pts)
            mid_ad = self.calculate_accumulation_distribution(
                highs[: len(highs) // 2],
                lows[: len(lows) // 2],
                closes[: len(closes) // 2],
                volumes[: len(volumes) // 2],
            )
            if ad > mid_ad:
                volume_score += 3
            else:
                volume_score -= 3

            # ── Order Flow (10 points max) ──
            flow_score = 0.0
            try:
                momentum = self.get_market_momentum(symbol, category=category)
                mom_sig = momentum.get("signal", "NEUTRAL")
                if "STRONG_BUY" in mom_sig:
                    flow_score += 7
                elif "BUY" in mom_sig:
                    flow_score += 4
                elif "STRONG_SELL" in mom_sig:
                    flow_score -= 7
                elif "SELL" in mom_sig:
                    flow_score -= 4

                imb = momentum.get("imbalance", 0)
                flow_score += imb * 3  # up to +/- 3
            except Exception:
                pass

            # ── Market Structure (10 points max) ──
            struct_score = 0.0
            adx = self.calculate_adx(highs, lows, closes)
            aroon = self.calculate_aroon(highs, lows)
            elder = self.calculate_elder_ray(highs, lows, closes)
            cci = self.calculate_cci(highs, lows, closes)
            dpo = self.calculate_dpo(closes)
            fib = self.calculate_fib_pivots(highs[-1], lows[-1], closes[-1])

            # ADX trend strength (2pts) - doesn't have direction, but amplifies
            trend_strength = "WEAK"
            if adx > 25:
                trend_strength = "STRONG"
            elif adx > 20:
                trend_strength = "MODERATE"

            # Aroon (2pts)
            if aroon["oscillator"] > 50:
                struct_score += 2
            elif aroon["oscillator"] < -50:
                struct_score -= 2
            else:
                struct_score += aroon["oscillator"] / 50 * 2

            # Elder Ray (2pts)
            if elder["bull_power"] > 0 and elder["bear_power"] > 0:
                struct_score += 2
            elif elder["bull_power"] < 0 and elder["bear_power"] < 0:
                struct_score -= 2
            elif elder["bull_power"] > 0:
                struct_score += 1
            else:
                struct_score -= 1

            # CCI (2pts)
            if cci > 100:
                struct_score += 2
            elif cci > 0:
                struct_score += 1
            elif cci < -100:
                struct_score -= 2
            else:
                struct_score -= 1

            # DPO (2pts)
            if dpo > 0:
                struct_score += 2
            else:
                struct_score -= 2

            # ── Composite ──
            raw_score = (
                trend_score
                + mom_score
                + vol_score
                + volume_score
                + flow_score
                + struct_score
            )
            # Clamp to [-100, 100]
            composite = max(-100, min(100, raw_score))

            # ADX amplification: if trend is strong, push score further from 0
            if trend_strength == "STRONG" and abs(composite) > 15:
                composite *= 1.15
                composite = max(-100, min(100, composite))

            # Choppiness dampening
            if chop > 62:
                composite *= 0.7

            # Multi-timeframe
            mtf = "NOT_CHECKED"
            if interval in ("15", "60", "1h"):
                try:
                    htf_int = "240" if interval in ("60", "1h") else "60"
                    htf_klines = self.get_klines(
                        symbol, interval=htf_int, limit=50, category=category
                    )
                    if htf_klines:
                        htf_klines.reverse()
                        htf_closes = [float(k[4]) for k in htf_klines]
                        htf_ema = self.calculate_ema(htf_closes, 21)
                        if current_price > htf_ema and composite > 0:
                            mtf = "ALIGNED_BULLISH"
                        elif current_price < htf_ema and composite < 0:
                            mtf = "ALIGNED_BEARISH"
                        else:
                            mtf = "DIVERGENT"
                except Exception:
                    mtf = "ERROR"

            # Classification
            comp_rounded = round(composite, 2)
            if comp_rounded >= 50:
                trend = "STRONG_BULLISH"
            elif comp_rounded >= 20:
                trend = "BULLISH"
            elif comp_rounded <= -50:
                trend = "STRONG_BEARISH"
            elif comp_rounded <= -20:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"

            # Advice
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
            if rsi > 78:
                advice = "OVERBOUGHT_CAUTION"
            elif rsi < 22:
                advice = "OVERSOLD_WATCH_REVERSAL"

            sl = current_price - atr * 2 if composite >= 0 else current_price + atr * 2
            tp = current_price + atr * 4 if composite >= 0 else current_price - atr * 4

            return {
                "symbol": symbol,
                "interval": interval,
                "composite_score": comp_rounded,
                "trend": trend,
                "trend_strength": trend_strength,
                "mtf_alignment": mtf,
                "current_price": round(current_price, 6),
                "action_advice": advice,
                "category_scores": {
                    "trend": round(trend_score, 2),
                    "momentum": round(mom_score, 2),
                    "volatility": round(vol_score, 2),
                    "volume": round(volume_score, 2),
                    "order_flow": round(flow_score, 2),
                    "market_structure": round(struct_score, 2),
                },
                "indicators": {
                    "ema9": round(ema9, 6),
                    "ema21": round(ema21, 6),
                    "ema50": round(ema50, 6),
                    "ema200": round(ema200, 6),
                    "hma": round(hma, 6),
                    "zlema": round(zlema, 6),
                    "supertrend": supertrend,
                    "psar": psar,
                    "ichimoku": ichimoku,
                    "rsi": round(rsi, 2),
                    "macd": macd,
                    "stoch_rsi": stoch,
                    "williams_r": round(williams, 2),
                    "ultimate_osc": round(uo, 2),
                    "awesome_osc": round(ao, 6),
                    "roc": round(roc, 4),
                    "trix": round(trix, 4),
                    "bb": bb,
                    "keltner": keltner,
                    "atr": round(atr, 6),
                    "stddev": round(stddev, 6),
                    "choppiness": round(chop, 2),
                    "donchian": donchian,
                    "obv_slope": round(obv_slope, 4),
                    "cmf": round(cmf, 4),
                    "mfi": round(mfi, 2),
                    "vwap": round(vwap, 6),
                    "vroc": round(vroc, 2),
                    "ad": round(ad, 2),
                    "adx": round(adx, 2),
                    "aroon": aroon,
                    "elder_ray": elder,
                    "cci": round(cci, 2),
                    "dpo": round(dpo, 6),
                    "fib_pivots": fib,
                },
                "risk_guidance": {
                    "suggested_sl": round(sl, 6),
                    "suggested_tp": round(tp, 6),
                    "rr_ratio": 2.0,
                    "atr": round(atr, 6),
                },
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Comprehensive trend analysis failed: %s", e)
            return {"status": "error", "msg": str(e)}

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
        # Validate inputs
        if win_rate <= 0 or win_rate >= 1:
            logger.warning(
                "Invalid win_rate for Kelly Criterion: %f. Must be between 0 and 1 (exclusive).",
                win_rate,
            )
            return 0.0
        if win_loss_ratio <= 0:
            logger.warning(
                "Invalid win_loss_ratio for Kelly Criterion: %f. Must be positive.",
                win_loss_ratio,
            )
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
            leverage: Leverage multiplier (if None, uses default from config)
        """
        price_diff = abs(entry_price - sl_price)
        if price_diff == 0:
            logger.warning(
                "Entry price equals stop loss price for %s. Cannot calculate position size.",
                symbol,
            )
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

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            ohlcv_list = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv_list, period=14)
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
    # ADVANCED POSITION SIZING
    # ══════════════════════════════════════════════════════════
    def get_adaptive_position_size(
        self,
        symbol: str,
        side: str,
        risk_usdt: float,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Adaptive position sizer that considers volatility, trend strength, orderbook imbalance, and win rate to dynamically size positions."""
        try:
            ticker = self.get_ticker(symbol, category)
            if not ticker:
                return {"status": "error", "msg": "Cannot fetch ticker"}
            last_price = float(ticker.get("lastPrice", 0))
            if last_price <= 0:
                return {"status": "error", "msg": "Invalid price"}

            klines = self.get_klines(
                symbol, interval="15", limit=100, category=category
            )
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient kline data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]

            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv, period=14)
            atr_pct = (atr / last_price) * 100 if last_price > 0 else 1.0

            rsi = self.calculate_rsi(closes, 14)
            adx_val = self.calculate_adx(highs, lows, closes, 14)

            vol_score = 1.0
            if atr_pct > 4.0:
                vol_score = 0.3
            elif atr_pct > 2.5:
                vol_score = 0.5
            elif atr_pct > 1.5:
                vol_score = 0.75
            elif atr_pct < 0.3:
                vol_score = 1.3

            trend_score = 1.0
            if adx_val > 40:
                trend_score = 1.3
            elif adx_val > 25:
                trend_score = 1.1
            elif adx_val < 15:
                trend_score = 0.7

            momentum_score = 1.0
            if side.lower() in ("buy", "long"):
                if rsi < 30:
                    momentum_score = 1.3
                elif rsi > 70:
                    momentum_score = 0.5
            elif rsi > 70:
                momentum_score = 1.3
            elif rsi < 30:
                momentum_score = 0.5

            ob_score = 1.0
            try:
                ob = self.get_orderbook(symbol, limit=25, category=category)
                if ob:
                    bids = ob.get("b", [])
                    asks = ob.get("a", [])
                    bid_vol = sum(float(b[1]) for b in bids[:10]) if bids else 1
                    ask_vol = sum(float(a[1]) for a in asks[:10]) if asks else 1
                    ratio = bid_vol / ask_vol if ask_vol > 0 else 1
                    if side.lower() in ("buy", "long"):
                        ob_score = min(1.3, max(0.6, ratio))
                    else:
                        ob_score = min(1.3, max(0.6, 1 / ratio if ratio > 0 else 1))
            except Exception:
                pass

            composite = (
                vol_score * 0.30
                + trend_score * 0.25
                + momentum_score * 0.25
                + ob_score * 0.20
            )
            adjusted_risk = risk_usdt * composite

            sl_distance = atr * 1.5
            sl_price = (
                last_price - sl_distance
                if side.lower() in ("buy", "long")
                else last_price + sl_distance
            )
            raw_qty = adjusted_risk / sl_distance if sl_distance > 0 else 0
            final_qty = self.adjust_quantity(symbol, raw_qty, category)

            tp_distance = sl_distance * 2.0
            tp_price = (
                last_price + tp_distance
                if side.lower() in ("buy", "long")
                else last_price - tp_distance
            )

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "entry_price": last_price,
                "quantity": final_qty,
                "stop_loss": round(sl_price, 6),
                "take_profit": round(tp_price, 6),
                "risk_usdt": round(adjusted_risk, 2),
                "base_risk": risk_usdt,
                "composite_multiplier": round(composite, 4),
                "scores": {
                    "volatility": round(vol_score, 3),
                    "trend_strength": round(trend_score, 3),
                    "momentum": round(momentum_score, 3),
                    "orderbook": round(ob_score, 3),
                },
                "atr": round(atr, 6),
                "atr_pct": round(atr_pct, 4),
                "rsi": round(rsi, 2),
                "adx": round(adx_val, 2),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_fixed_fractional_size(
        self,
        account_balance: float,
        risk_fraction: float,
        entry_price: float,
        sl_price: float,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Fixed fractional sizing: risk a fixed % of account on each trade."""
        risk_amount = account_balance * risk_fraction
        sl_dist = abs(entry_price - sl_price)
        if sl_dist == 0:
            return {"status": "error", "msg": "SL distance is zero"}
        raw_qty = risk_amount / sl_dist
        qty = self.adjust_quantity(symbol, raw_qty, category)
        actual_risk = qty * sl_dist
        return {
            "status": "ok",
            "account_balance": account_balance,
            "risk_fraction": risk_fraction,
            "risk_amount": round(risk_amount, 4),
            "quantity": qty,
            "actual_risk": round(actual_risk, 4),
            "actual_risk_pct": round((actual_risk / account_balance) * 100, 4)
            if account_balance > 0
            else 0,
        }

    def get_anti_martingale_size(
        self,
        base_qty: float,
        consecutive_wins: int,
        consecutive_losses: int,
        scale_factor: float = 0.5,
        max_multiplier: float = 3.0,
        min_multiplier: float = 0.25,
    ) -> dict:
        """Anti-martingale: increase size on wins, decrease on losses."""
        if consecutive_wins > 0:
            multiplier = min(max_multiplier, 1.0 + (consecutive_wins * scale_factor))
        elif consecutive_losses > 0:
            multiplier = max(
                min_multiplier, 1.0 - (consecutive_losses * scale_factor * 0.5)
            )
        else:
            multiplier = 1.0
        adjusted_qty = base_qty * multiplier
        return {
            "status": "ok",
            "base_qty": base_qty,
            "adjusted_qty": round(adjusted_qty, 6),
            "multiplier": round(multiplier, 4),
            "consecutive_wins": consecutive_wins,
            "consecutive_losses": consecutive_losses,
            "regime": "scaling_up"
            if multiplier > 1
            else "scaling_down"
            if multiplier < 1
            else "neutral",
        }

    def get_portfolio_heat(
        self,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Portfolio heat: total open risk across all positions as % of account equity."""
        try:
            positions = self.get_positions(category=category)
            balance_resp = self.get_wallet_balance()
            equity = 0.0
            if isinstance(balance_resp, dict):
                coins = (
                    balance_resp.get("result", {}).get("list", [{}])[0].get("coin", [])
                )
                for c in coins:
                    equity += float(c.get("usdValue", 0))
            if equity <= 0:
                total_eq = (
                    balance_resp.get("result", {})
                    .get("list", [{}])[0]
                    .get("totalEquity", "0")
                )
                equity = float(total_eq) if total_eq else 0

            total_risk = 0.0
            position_risks = []
            active_positions = [
                p for p in (positions or []) if float(p.get("size", 0)) > 0
            ]
            for pos in active_positions:
                size = float(pos.get("size", 0))
                entry = float(pos.get("avgPrice", 0))
                mark = float(pos.get("markPrice", 0))
                sl = float(pos.get("stopLoss", 0))
                unrealized = float(pos.get("unrealisedPnl", 0))
                side = pos.get("side", "")
                sym = pos.get("symbol", "")

                if sl > 0 and entry > 0:
                    if side == "Buy":
                        risk_per_unit = entry - sl
                    else:
                        risk_per_unit = sl - entry
                    position_risk = abs(risk_per_unit * size)
                else:
                    position_risk = abs(size * entry * 0.02)

                total_risk += position_risk
                position_risks.append(
                    {
                        "symbol": sym,
                        "side": side,
                        "size": size,
                        "entry": entry,
                        "mark": mark,
                        "unrealized_pnl": round(unrealized, 4),
                        "risk_usdt": round(position_risk, 4),
                    }
                )

            heat_pct = (total_risk / equity * 100) if equity > 0 else 0
            heat_level = (
                "LOW"
                if heat_pct < 3
                else "MODERATE"
                if heat_pct < 6
                else "HIGH"
                if heat_pct < 10
                else "EXTREME"
            )

            return {
                "status": "ok",
                "equity": round(equity, 2),
                "total_risk_usdt": round(total_risk, 4),
                "heat_pct": round(heat_pct, 4),
                "heat_level": heat_level,
                "active_positions": len(active_positions),
                "positions": position_risks,
                "can_add_risk": heat_pct < 6,
                "max_additional_risk": round(max(0, equity * 0.06 - total_risk), 2),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def calculate_max_position(
        self,
        symbol: str,
        side: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate maximum position size based on available margin and leverage."""
        try:
            ticker = self.get_ticker(symbol, category)
            if not ticker:
                return {"status": "error", "msg": "Cannot fetch ticker"}
            last_price = float(ticker.get("lastPrice", 0))
            if last_price <= 0:
                return {"status": "error", "msg": "Invalid price"}

            balance_resp = self.get_wallet_balance()
            available = 0.0
            if isinstance(balance_resp, dict):
                acct = balance_resp.get("result", {}).get("list", [{}])[0]
                available = float(acct.get("totalAvailableBalance", 0))

            positions = self.get_positions(category=category, symbol=symbol)
            leverage = 1.0
            if positions:
                leverage = float(positions[0].get("leverage", 1))
            else:
                leverage = float(self.config.default_leverage)

            max_notional = available * leverage
            raw_qty = max_notional / last_price if last_price > 0 else 0
            max_qty = self.adjust_quantity(symbol, raw_qty, category)

            safe_50 = self.adjust_quantity(symbol, raw_qty * 0.50, category)
            safe_25 = self.adjust_quantity(symbol, raw_qty * 0.25, category)
            safe_10 = self.adjust_quantity(symbol, raw_qty * 0.10, category)

            return {
                "status": "ok",
                "symbol": symbol,
                "last_price": last_price,
                "available_balance": round(available, 4),
                "leverage": leverage,
                "max_notional": round(max_notional, 2),
                "max_quantity": max_qty,
                "safe_50pct": safe_50,
                "safe_25pct": safe_25,
                "safe_10pct": safe_10,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # 5 PROFIT-MAXIMIZING STRATEGIES
    # ══════════════════════════════════════════════════════════
    def macro_momentum_sniper(
        self,
        symbol: str,
        risk_usdt: float = 5.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Momentum sniper: catches strong directional moves early using multi-indicator confluence (RSI breakout + MACD cross + volume surge + ADX filter)."""
        try:
            klines = self.get_klines(
                symbol, interval="15", limit=100, category=category
            )
            if not klines or len(klines) < 50:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]

            rsi = self.calculate_rsi(closes, 14)
            macd_data = self.calculate_macd(closes)
            atr = self.calculate_atr(ohlcv, 14)
            adx_val = self.calculate_adx(highs, lows, closes, 14)
            plus_di = 0.0
            minus_di = 0.0
            if len(highs) > 14:
                pos_dm = [
                    max(highs[i] - highs[i - 1], 0)
                    if (highs[i] - highs[i - 1]) > (lows[i - 1] - lows[i])
                    else 0
                    for i in range(1, len(highs))
                ]
                neg_dm = [
                    max(lows[i - 1] - lows[i], 0)
                    if (lows[i - 1] - lows[i]) > (highs[i] - highs[i - 1])
                    else 0
                    for i in range(1, len(lows))
                ]
                tr_list = [
                    max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    for i in range(1, len(closes))
                ]
                atr_sum = sum(tr_list[-14:]) + 1e-9
                plus_di = 100 * sum(pos_dm[-14:]) / atr_sum
                minus_di = 100 * sum(neg_dm[-14:]) / atr_sum

            avg_vol = (
                sum(volumes[-20:]) / 20
                if len(volumes) >= 20
                else sum(volumes) / len(volumes)
            )
            curr_vol = volumes[-1] if volumes else 0
            vol_surge = curr_vol / avg_vol if avg_vol > 0 else 1.0

            macd_line = macd_data.get("macd", 0) if isinstance(macd_data, dict) else 0
            signal_line = (
                macd_data.get("signal", 0) if isinstance(macd_data, dict) else 0
            )
            macd_hist = (
                macd_data.get("histogram", 0) if isinstance(macd_data, dict) else 0
            )

            bull_signals = 0
            bear_signals = 0

            if rsi > 50 and rsi < 75:
                bull_signals += 1
            elif rsi < 50 and rsi > 25:
                bear_signals += 1

            if macd_line > signal_line and macd_hist > 0:
                bull_signals += 1
            elif macd_line < signal_line and macd_hist < 0:
                bear_signals += 1

            if vol_surge > 1.5:
                bull_signals += 1 if closes[-1] > closes[-2] else 0
                bear_signals += 1 if closes[-1] < closes[-2] else 0

            if adx_val > 25:
                if plus_di > minus_di:
                    bull_signals += 1
                else:
                    bear_signals += 1

            ema_fast = self.calculate_ema(closes, 9)
            ema_slow = self.calculate_ema(closes, 21)
            if ema_fast > ema_slow:
                bull_signals += 1
            else:
                bear_signals += 1

            last_price = closes[-1]
            signal = "NONE"
            side = None
            confidence = 0

            if bull_signals >= 4 and adx_val > 20:
                signal = "STRONG_BUY"
                side = "Buy"
                confidence = min(95, bull_signals * 19)
            elif bull_signals >= 3:
                signal = "BUY"
                side = "Buy"
                confidence = min(80, bull_signals * 20)
            elif bear_signals >= 4 and adx_val > 20:
                signal = "STRONG_SELL"
                side = "Sell"
                confidence = min(95, bear_signals * 19)
            elif bear_signals >= 3:
                signal = "SELL"
                side = "Sell"
                confidence = min(80, bear_signals * 20)

            result: Dict[str, Any] = {
                "status": "ok",
                "signal": signal,
                "confidence": confidence,
                "bull_signals": bull_signals,
                "bear_signals": bear_signals,
                "indicators": {
                    "rsi": round(rsi, 2),
                    "macd": round(macd_line, 6),
                    "macd_signal": round(signal_line, 6),
                    "macd_hist": round(macd_hist, 6),
                    "adx": round(adx_val, 2),
                    "plus_di": round(plus_di, 2),
                    "minus_di": round(minus_di, 2),
                    "vol_surge": round(vol_surge, 2),
                    "ema_fast": round(ema_fast, 6),
                    "ema_slow": round(ema_slow, 6),
                },
            }

            if side and confidence >= 60:
                sl_dist = atr * 1.5
                tp_dist = atr * 3.0
                sl_price = (
                    last_price - sl_dist if side == "Buy" else last_price + sl_dist
                )
                tp_price = (
                    last_price + tp_dist if side == "Buy" else last_price - tp_dist
                )
                raw_qty = risk_usdt / sl_dist if sl_dist > 0 else 0
                qty = self.adjust_quantity(symbol, raw_qty, category)

                result["trade"] = {
                    "side": side,
                    "entry": last_price,
                    "stop_loss": round(sl_price, 6),
                    "take_profit": round(tp_price, 6),
                    "quantity": qty,
                    "risk_usdt": risk_usdt,
                    "rr_ratio": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0,
                }

            return result
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_mean_reversion_scalp(
        self,
        symbol: str,
        risk_usdt: float = 5.0,
        bb_threshold: float = 0.95,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Mean reversion scalper: profits from overextended price moves snapping back. Uses Bollinger Bands + RSI extremes + volume exhaustion."""
        try:
            klines = self.get_klines(symbol, interval="5", limit=100, category=category)
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]

            bb = self.calculate_bollinger_bands(closes, 20, 2.0)
            rsi = self.calculate_rsi(closes, 14)
            atr = self.calculate_atr(ohlcv, 14)

            upper = bb.get("upper", 0) if isinstance(bb, dict) else 0
            lower = bb.get("lower", 0) if isinstance(bb, dict) else 0
            middle = bb.get("middle", 0) if isinstance(bb, dict) else 0
            bb_width = upper - lower if upper and lower else 1

            last_price = closes[-1]
            bb_position = (last_price - lower) / bb_width if bb_width > 0 else 0.5

            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
            recent_vol = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else 1
            vol_declining = recent_vol < avg_vol * 0.8

            signal = "NONE"
            side = None
            confidence = 0
            reversion_score = 0

            if bb_position > bb_threshold and rsi > 70:
                reversion_score = min(
                    100, int((bb_position - bb_threshold) * 200 + (rsi - 70) * 2)
                )
                if vol_declining:
                    reversion_score += 15
                if reversion_score >= 40:
                    signal = "SELL_REVERSION"
                    side = "Sell"
                    confidence = min(90, reversion_score)

            elif bb_position < (1 - bb_threshold) and rsi < 30:
                reversion_score = min(
                    100, int(((1 - bb_threshold) - bb_position) * 200 + (30 - rsi) * 2)
                )
                if vol_declining:
                    reversion_score += 15
                if reversion_score >= 40:
                    signal = "BUY_REVERSION"
                    side = "Buy"
                    confidence = min(90, reversion_score)

            result: Dict[str, Any] = {
                "status": "ok",
                "signal": signal,
                "confidence": confidence,
                "reversion_score": reversion_score,
                "bb_position": round(bb_position, 4),
                "rsi": round(rsi, 2),
                "vol_declining": vol_declining,
                "bb_upper": round(upper, 6),
                "bb_middle": round(middle, 6),
                "bb_lower": round(lower, 6),
            }

            if side and confidence >= 50:
                tp_price = middle
                sl_dist = atr * 2.0
                sl_price = (
                    last_price + sl_dist if side == "Sell" else last_price - sl_dist
                )
                raw_qty = risk_usdt / sl_dist if sl_dist > 0 else 0
                qty = self.adjust_quantity(symbol, raw_qty, category)
                tp_dist = abs(last_price - tp_price)

                result["trade"] = {
                    "side": side,
                    "entry": last_price,
                    "target": round(tp_price, 6),
                    "stop_loss": round(sl_price, 6),
                    "quantity": qty,
                    "risk_usdt": risk_usdt,
                    "rr_ratio": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0,
                }

            return result
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_funding_arb_scan(
        self,
        top_n: int = 10,
        min_rate: float = 0.0005,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Funding rate arbitrage scanner: finds coins with extreme funding rates to exploit the next funding payment."""
        try:
            tickers_resp = self.api_request(
                "GET", "/v5/market/tickers", {"category": category.value}
            )
            tickers = tickers_resp.get("result", {}).get("list", [])
            if not tickers:
                return {"status": "error", "msg": "No tickers found"}

            opportunities = []
            for t in tickers:
                sym = t.get("symbol", "")
                funding = _safe_float(t.get("fundingRate")) or 0.0
                volume24h = _safe_float(t.get("volume24h")) or 0.0
                last_price = _safe_float(t.get("lastPrice")) or 0.0
                turnover = _safe_float(t.get("turnover24h")) or 0.0

                if abs(funding) < min_rate or volume24h <= 0:
                    continue

                annualized = funding * 3 * 365 * 100
                direction = "SHORT" if funding > 0 else "LONG"

                opportunities.append(
                    {
                        "symbol": sym,
                        "funding_rate": round(funding, 6),
                        "funding_rate_pct": round(funding * 100, 4),
                        "annualized_pct": round(annualized, 2),
                        "direction": direction,
                        "last_price": last_price,
                        "volume_24h": round(volume24h, 2),
                        "turnover_24h": round(turnover, 2),
                    }
                )

            opportunities.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)
            top_opps = opportunities[:top_n]

            return {
                "status": "ok",
                "total_opportunities": len(opportunities),
                "showing": len(top_opps),
                "min_rate_filter": min_rate,
                "opportunities": top_opps,
                "strategy_note": "Go SHORT when funding is positive (longs pay shorts), go LONG when negative (shorts pay longs). Hold through funding timestamp to collect payment.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_smart_dca(
        self,
        symbol: str,
        total_usdt: float,
        num_levels: int = 5,
        dip_pct: float = 1.0,
        use_rsi_weighting: bool = True,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Smart DCA: dynamically adjusts buy amounts at each level based on RSI, support levels, and volume. Buys more aggressively at deeper dips with better technical signals."""
        try:
            ticker = self.get_ticker(symbol, category)
            if not ticker:
                return {"status": "error", "msg": "Cannot fetch ticker"}
            last_price = float(ticker.get("lastPrice", 0))
            if last_price <= 0:
                return {"status": "error", "msg": "Invalid price"}

            klines = self.get_klines(
                symbol, interval="60", limit=100, category=category
            )
            closes = [float(k[4]) for k in klines] if klines else []

            levels = []
            weights = []
            for i in range(num_levels):
                level_price = last_price * (1 - (dip_pct / 100) * (i + 1))

                if use_rsi_weighting and len(closes) > 20:
                    simulated_closes = closes + [level_price]
                    rsi_at_level = self.calculate_rsi(simulated_closes, 14)
                    if rsi_at_level < 25:
                        weight = 2.0
                    elif rsi_at_level < 35:
                        weight = 1.5
                    elif rsi_at_level < 45:
                        weight = 1.2
                    else:
                        weight = 1.0
                else:
                    weight = 1.0 + (i * 0.3)

                weights.append(weight)
                levels.append(level_price)

            total_weight = sum(weights)
            orders = []
            cumulative_qty = 0.0
            cumulative_cost = 0.0

            for i, (level_price, weight) in enumerate(zip(levels, weights)):
                allocation = (weight / total_weight) * total_usdt
                raw_qty = allocation / level_price if level_price > 0 else 0
                qty = self.adjust_quantity(symbol, raw_qty, category)
                cost = qty * level_price
                cumulative_qty += qty
                cumulative_cost += cost
                avg_entry = (
                    cumulative_cost / cumulative_qty if cumulative_qty > 0 else 0
                )

                orders.append(
                    {
                        "level": i + 1,
                        "price": round(level_price, 6),
                        "dip_from_current_pct": round(
                            (1 - level_price / last_price) * 100, 2
                        ),
                        "quantity": qty,
                        "allocation_usdt": round(allocation, 2),
                        "weight": round(weight, 2),
                        "cumulative_qty": round(cumulative_qty, 6),
                        "avg_entry_if_filled": round(avg_entry, 6),
                    }
                )

            return {
                "status": "ok",
                "symbol": symbol,
                "current_price": last_price,
                "total_usdt": total_usdt,
                "num_levels": num_levels,
                "dip_spacing_pct": dip_pct,
                "rsi_weighted": use_rsi_weighting,
                "orders": orders,
                "if_all_filled": {
                    "total_qty": round(cumulative_qty, 6),
                    "avg_entry": round(cumulative_cost / cumulative_qty, 6)
                    if cumulative_qty > 0
                    else 0,
                    "total_cost": round(cumulative_cost, 2),
                    "breakeven_with_fees": round(
                        (cumulative_cost / cumulative_qty) * 1.0012, 6
                    )
                    if cumulative_qty > 0
                    else 0,
                },
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_liquidity_sweep(
        self,
        symbol: str,
        risk_usdt: float = 5.0,
        depth: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Liquidity sweep detector: identifies price levels where stop-losses are clustered and anticipates sweep-and-reverse patterns. Trades the reversal after liquidity is taken."""
        try:
            ob = self.get_orderbook(symbol, limit=depth, category=category)
            if not ob:
                return {"status": "error", "msg": "Cannot fetch orderbook"}

            klines = self.get_klines(
                symbol, interval="15", limit=100, category=category
            )
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient kline data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv, 14)
            last_price = closes[-1]

            bids = ob.get("b", [])
            asks = ob.get("a", [])

            bid_clusters = []
            for i in range(len(bids) - 2):
                cluster_vol = sum(
                    float(bids[j][1]) for j in range(i, min(i + 3, len(bids)))
                )
                avg_vol = sum(float(b[1]) for b in bids) / len(bids) if bids else 1
                if cluster_vol > avg_vol * 3:
                    bid_clusters.append(
                        {
                            "price": float(bids[i][0]),
                            "cluster_volume": round(cluster_vol, 4),
                            "significance": round(cluster_vol / avg_vol, 2),
                        }
                    )

            ask_clusters = []
            for i in range(len(asks) - 2):
                cluster_vol = sum(
                    float(asks[j][1]) for j in range(i, min(i + 3, len(asks)))
                )
                avg_vol = sum(float(a[1]) for a in asks) / len(asks) if asks else 1
                if cluster_vol > avg_vol * 3:
                    ask_clusters.append(
                        {
                            "price": float(asks[i][0]),
                            "cluster_volume": round(cluster_vol, 4),
                            "significance": round(cluster_vol / avg_vol, 2),
                        }
                    )

            recent_lows = sorted(lows[-20:])[:3]
            recent_highs = sorted(highs[-20:], reverse=True)[:3]

            sweep_below = []
            for low in recent_lows:
                for bc in bid_clusters:
                    if abs(bc["price"] - low) / last_price < 0.005:
                        sweep_below.append(
                            {
                                "level": round(low, 6),
                                "cluster_price": bc["price"],
                                "volume": bc["cluster_volume"],
                                "type": "long_stop_sweep",
                            }
                        )

            sweep_above = []
            for high in recent_highs:
                for ac in ask_clusters:
                    if abs(ac["price"] - high) / last_price < 0.005:
                        sweep_above.append(
                            {
                                "level": round(high, 6),
                                "cluster_price": ac["price"],
                                "volume": ac["cluster_volume"],
                                "type": "short_stop_sweep",
                            }
                        )

            signal = "NONE"
            trade = None
            if (
                sweep_below
                and last_price - min(s["level"] for s in sweep_below) < atr * 2
            ):
                signal = "LONG_AFTER_SWEEP"
                sweep_level = min(s["level"] for s in sweep_below)
                entry = sweep_level * 0.998
                sl = sweep_level - atr * 1.5
                tp = last_price + atr * 2
                raw_qty = risk_usdt / abs(entry - sl) if abs(entry - sl) > 0 else 0
                trade = {
                    "side": "Buy",
                    "entry_zone": round(entry, 6),
                    "stop_loss": round(sl, 6),
                    "take_profit": round(tp, 6),
                    "quantity": self.adjust_quantity(symbol, raw_qty, category),
                    "risk_usdt": risk_usdt,
                }
            elif (
                sweep_above
                and max(s["level"] for s in sweep_above) - last_price < atr * 2
            ):
                signal = "SHORT_AFTER_SWEEP"
                sweep_level = max(s["level"] for s in sweep_above)
                entry = sweep_level * 1.002
                sl = sweep_level + atr * 1.5
                tp = last_price - atr * 2
                raw_qty = risk_usdt / abs(sl - entry) if abs(sl - entry) > 0 else 0
                trade = {
                    "side": "Sell",
                    "entry_zone": round(entry, 6),
                    "stop_loss": round(sl, 6),
                    "take_profit": round(tp, 6),
                    "quantity": self.adjust_quantity(symbol, raw_qty, category),
                    "risk_usdt": risk_usdt,
                }

            result: Dict[str, Any] = {
                "status": "ok",
                "symbol": symbol,
                "signal": signal,
                "last_price": last_price,
                "atr": round(atr, 6),
                "sweep_below_targets": sweep_below[:5],
                "sweep_above_targets": sweep_above[:5],
                "bid_clusters": bid_clusters[:5],
                "ask_clusters": ask_clusters[:5],
            }
            if trade:
                result["trade"] = trade
            return result
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # 25 IMPORTANT MISSING FUNCTIONS
    # ══════════════════════════════════════════════════════════
    def get_risk_reward_analysis(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        qty: float,
        fee_rate: float = 0.0006,
    ) -> dict:
        """Full risk:reward analysis including fees, breakeven, and expected value."""
        is_long = take_profit > entry_price
        risk = abs(entry_price - stop_loss) * qty
        reward = abs(take_profit - entry_price) * qty
        fees = (entry_price * qty + take_profit * qty) * fee_rate
        fees_sl = (entry_price * qty + stop_loss * qty) * fee_rate
        net_reward = reward - fees
        net_risk = risk + fees_sl
        rr_raw = reward / risk if risk > 0 else 0
        rr_net = net_reward / net_risk if net_risk > 0 else 0
        breakeven_pct = (fee_rate * 2) * 100
        be_price = (
            entry_price * (1 + fee_rate * 2)
            if is_long
            else entry_price * (1 - fee_rate * 2)
        )

        return {
            "status": "ok",
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "direction": "LONG" if is_long else "SHORT",
            "qty": qty,
            "raw_risk": round(risk, 4),
            "raw_reward": round(reward, 4),
            "rr_ratio_raw": round(rr_raw, 3),
            "fees_on_win": round(fees, 4),
            "fees_on_loss": round(fees_sl, 4),
            "net_reward": round(net_reward, 4),
            "net_risk": round(net_risk, 4),
            "rr_ratio_net": round(rr_net, 3),
            "breakeven_move_pct": round(breakeven_pct, 4),
            "breakeven_price": round(be_price, 6),
            "min_winrate_for_profit": round(1 / (1 + rr_net) * 100, 2)
            if rr_net > 0
            else 100,
        }

    def get_liquidation_price(
        self,
        entry_price: float,
        leverage: float,
        side: str,
        maint_margin_rate: float = 0.004,
    ) -> dict:
        """Calculate estimated liquidation price for a leveraged position."""
        if leverage <= 0:
            logger.warning(
                "Leverage must be positive for liquidation price calculation. Got: %f",
                leverage,
            )
            return {"status": "error", "msg": "Leverage must be positive"}
        if side.lower() in ("buy", "long"):
            liq_price = entry_price * (1 - (1 / leverage) + maint_margin_rate)
            distance_pct = ((entry_price - liq_price) / entry_price) * 100
        else:
            liq_price = entry_price * (1 + (1 / leverage) - maint_margin_rate)
            distance_pct = ((liq_price - entry_price) / entry_price) * 100

        return {
            "status": "ok",
            "entry_price": entry_price,
            "leverage": leverage,
            "side": side,
            "maint_margin_rate": maint_margin_rate,
            "liquidation_price": round(liq_price, 6),
            "distance_pct": round(distance_pct, 4),
            "warning": "DANGER"
            if distance_pct < 2
            else "CAUTION"
            if distance_pct < 5
            else "OK",
        }

    def get_drawdown_analysis(
        self,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> dict:
        """Analyze max drawdown and recovery from closed PnL history."""
        try:
            pnl_list = self.get_pnl_history(category=category, limit=limit)
            if not pnl_list:
                return {"status": "error", "msg": "No PnL history"}

            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            dd_trades = 0
            equity_curve = []
            wins = 0
            losses = 0
            total_win = 0.0
            total_loss = 0.0
            streak = 0
            max_win_streak = 0
            max_loss_streak = 0

            for i, p in enumerate(reversed(pnl_list)):
                pnl = float(p.get("closedPnl", 0))
                cumulative += pnl
                equity_curve.append(round(cumulative, 4))

                if cumulative > peak:
                    peak = cumulative
                    dd_trades = 0

                dd = peak - cumulative
                dd_trades += 1
                max_dd = max(max_dd, dd)

                if pnl > 0:
                    wins += 1
                    total_win += pnl
                    if streak >= 0:
                        streak += 1
                    else:
                        streak = 1
                    max_win_streak = max(max_win_streak, streak)
                elif pnl < 0:
                    losses += 1
                    total_loss += abs(pnl)
                    if streak <= 0:
                        streak -= 1
                    else:
                        streak = -1
                    max_loss_streak = max(max_loss_streak, abs(streak))

            total_trades = wins + losses
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            avg_win = total_win / wins if wins > 0 else 0
            avg_loss = total_loss / losses if losses > 0 else 0
            profit_factor = total_win / total_loss if total_loss > 0 else float("inf")
            expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

            return {
                "status": "ok",
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": round(win_rate, 2),
                "total_pnl": round(cumulative, 4),
                "max_drawdown": round(max_dd, 4),
                "max_drawdown_pct": round((max_dd / peak * 100) if peak > 0 else 0, 2),
                "profit_factor": round(profit_factor, 3),
                "avg_win": round(avg_win, 4),
                "avg_loss": round(avg_loss, 4),
                "win_loss_ratio": round(avg_win / avg_loss, 3) if avg_loss > 0 else 0,
                "expectancy_per_trade": round(expectancy, 4),
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "equity_curve": equity_curve[-20:],
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_support_resistance(
        self,
        symbol: str,
        lookback: int = 100,
        num_levels: int = 5,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detect key support/resistance levels from price action pivot points."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=lookback, category=category
            )
            if not klines or len(klines) < 20:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            last_price = closes[-1]

            pivots_high = []
            pivots_low = []
            for i in range(2, len(klines) - 2):
                if (
                    highs[i] > highs[i - 1]
                    and highs[i] > highs[i - 2]
                    and highs[i] > highs[i + 1]
                    and highs[i] > highs[i + 2]
                ):
                    pivots_high.append(highs[i])
                if (
                    lows[i] < lows[i - 1]
                    and lows[i] < lows[i - 2]
                    and lows[i] < lows[i + 1]
                    and lows[i] < lows[i + 2]
                ):
                    pivots_low.append(lows[i])

            def cluster_levels(
                levels: List[float], tolerance: float = 0.003
            ) -> List[dict]:
                if not levels:
                    return []
                sorted_lvls = sorted(levels)
                clusters: List[List[float]] = [[sorted_lvls[0]]]
                for lvl in sorted_lvls[1:]:
                    if abs(lvl - clusters[-1][-1]) / clusters[-1][-1] < tolerance:
                        clusters[-1].append(lvl)
                    else:
                        clusters.append([lvl])
                result = []
                for c in clusters:
                    result.append(
                        {
                            "price": round(sum(c) / len(c), 6),
                            "touches": len(c),
                            "strength": min(100, len(c) * 25),
                        }
                    )
                result.sort(key=lambda x: x["touches"], reverse=True)
                return result[:num_levels]

            resistance_levels = cluster_levels(pivots_high)
            support_levels = cluster_levels(pivots_low)

            nearest_support = None
            nearest_resistance = None
            for s in sorted(support_levels, key=lambda x: abs(x["price"] - last_price)):
                if s["price"] < last_price:
                    nearest_support = s
                    break
            for r in sorted(
                resistance_levels, key=lambda x: abs(x["price"] - last_price)
            ):
                if r["price"] > last_price:
                    nearest_resistance = r
                    break

            return {
                "status": "ok",
                "symbol": symbol,
                "last_price": last_price,
                "resistance_levels": resistance_levels,
                "support_levels": support_levels,
                "nearest_support": nearest_support,
                "nearest_resistance": nearest_resistance,
                "support_distance_pct": round(
                    ((last_price - nearest_support["price"]) / last_price) * 100, 3
                )
                if nearest_support
                else None,
                "resistance_distance_pct": round(
                    ((nearest_resistance["price"] - last_price) / last_price) * 100, 3
                )
                if nearest_resistance
                else None,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_market_regime(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Classify market as TRENDING_UP, TRENDING_DOWN, RANGING, or VOLATILE using ADX + Choppiness + ATR."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=100, category=category
            )
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]

            adx_val = self.calculate_adx(highs, lows, closes, 14)
            plus_di = 0.0
            minus_di = 0.0
            if len(highs) > 14:
                pos_dm = [
                    max(highs[i] - highs[i - 1], 0)
                    if (highs[i] - highs[i - 1]) > (lows[i - 1] - lows[i])
                    else 0
                    for i in range(1, len(highs))
                ]
                neg_dm = [
                    max(lows[i - 1] - lows[i], 0)
                    if (lows[i - 1] - lows[i]) > (highs[i] - highs[i - 1])
                    else 0
                    for i in range(1, len(lows))
                ]
                tr_list = [
                    max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i - 1]),
                        abs(lows[i] - closes[i - 1]),
                    )
                    for i in range(1, len(closes))
                ]
                atr_sum = sum(tr_list[-14:]) + 1e-9
                plus_di = 100 * sum(pos_dm[-14:]) / atr_sum
                minus_di = 100 * sum(neg_dm[-14:]) / atr_sum
            atr = self.calculate_atr(ohlcv, 14)
            atr_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 else 0
            chop = self.calculate_choppiness_index(highs, lows, closes, 14)

            ema20 = self.calculate_ema(closes, 20)
            ema50 = self.calculate_ema(closes, 50)
            bb = self.calculate_bollinger_bands(closes, 20, 2.0)
            bb_width = 0
            if isinstance(bb, dict):
                upper = bb.get("upper", 0)
                lower = bb.get("lower", 0)
                mid = bb.get("middle", 1)
                bb_width = ((upper - lower) / mid * 100) if mid > 0 else 0

            if adx_val > 25 and chop < 50:
                if plus_di > minus_di:
                    regime = "TRENDING_UP"
                else:
                    regime = "TRENDING_DOWN"
            elif chop > 61.8:
                regime = "RANGING"
            elif atr_pct > 3.0 or bb_width > 8.0:
                regime = "VOLATILE"
            elif adx_val < 20 and chop > 50:
                regime = "RANGING"
            else:
                regime = "TRANSITIONAL"

            strategies = {
                "TRENDING_UP": "Trend-following longs, momentum strategies, breakout entries",
                "TRENDING_DOWN": "Trend-following shorts, momentum strategies, breakdown entries",
                "RANGING": "Mean reversion, range trading, sell resistance / buy support",
                "VOLATILE": "Reduce size, wider stops, avoid entries, wait for clarity",
                "TRANSITIONAL": "Small positions, wait for confirmation, use tight stops",
            }

            return {
                "status": "ok",
                "symbol": symbol,
                "regime": regime,
                "recommended_strategy": strategies.get(regime, ""),
                "adx": round(adx_val, 2),
                "plus_di": round(plus_di, 2),
                "minus_di": round(minus_di, 2),
                "choppiness": round(chop, 2),
                "atr": round(atr, 6),
                "atr_pct": round(atr_pct, 3),
                "bb_width_pct": round(bb_width, 3),
                "ema20": round(ema20, 6),
                "ema50": round(ema50, 6),
                "ema_trend": "BULLISH" if ema20 > ema50 else "BEARISH",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_entry_timing(
        self,
        symbol: str,
        side: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Score the current moment for entry quality (0-100) using multi-factor analysis."""
        try:
            klines = self.get_klines(symbol, interval="5", limit=100, category=category)
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]

            rsi = self.calculate_rsi(closes, 14)
            bb = self.calculate_bollinger_bands(closes, 20, 2.0)
            macd_data = self.calculate_macd(closes)

            score = 50
            factors = []
            is_buy = side.lower() in ("buy", "long")

            if is_buy:
                if rsi < 35:
                    score += 15
                    factors.append("RSI oversold (+15)")
                elif rsi < 45:
                    score += 5
                    factors.append("RSI favorable (+5)")
                elif rsi > 70:
                    score -= 20
                    factors.append("RSI overbought (-20)")
            elif rsi > 65:
                score += 15
                factors.append("RSI overbought (+15)")
            elif rsi > 55:
                score += 5
                factors.append("RSI favorable (+5)")
            elif rsi < 30:
                score -= 20
                factors.append("RSI oversold (-20)")

            if isinstance(bb, dict):
                upper = bb.get("upper", 0)
                lower = bb.get("lower", 0)
                bb_range = (
                    bb["upper"] - bb["lower"]
                    if isinstance(bb, dict) and bb["upper"] and bb["lower"]
                    else 1.0
                )
                bb_pos = (
                    (closes[-1] - bb["lower"]) / bb_range if bb_range > 1e-9 else 0.5
                )  # Prevent division by zero
                if is_buy and bb_pos < 0.2:
                    score += 10
                    factors.append("Near BB lower (+10)")
                elif not is_buy and bb_pos > 0.8:
                    score += 10
                    factors.append("Near BB upper (+10)")

            if isinstance(macd_data, dict):
                hist = macd_data.get("histogram", 0)
                if is_buy and hist > 0:
                    score += 10
                    factors.append("MACD bullish (+10)")
                elif not is_buy and hist < 0:
                    score += 10
                    factors.append("MACD bearish (+10)")

            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
            curr_vol = volumes[-1]
            if curr_vol > avg_vol * 1.5:
                if (is_buy and closes[-1] > closes[-2]) or (
                    not is_buy and closes[-1] < closes[-2]
                ):
                    score += 10
                    factors.append("Volume confirms (+10)")

            ema9 = self.calculate_ema(closes, 9)
            ema21 = self.calculate_ema(closes, 21)
            if is_buy and ema9 > ema21:
                score += 5
                factors.append("EMA alignment (+5)")
            elif not is_buy and ema9 < ema21:
                score += 5
                factors.append("EMA alignment (+5)")

            score = max(0, min(100, score))
            quality = (
                "EXCELLENT"
                if score >= 80
                else "GOOD"
                if score >= 65
                else "FAIR"
                if score >= 50
                else "POOR"
                if score >= 35
                else "AVOID"
            )

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "entry_score": score,
                "quality": quality,
                "factors": factors,
                "should_enter": score >= 60,
                "rsi": round(rsi, 2),
                "current_price": closes[-1],
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def scale_into_position(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        num_entries: int = 3,
        spacing_pct: float = 0.5,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate scaled entry orders at multiple price levels."""
        try:
            ticker = self.get_ticker(symbol, category)
            if not ticker:
                return {"status": "error", "msg": "Cannot fetch ticker"}
            last_price = float(ticker.get("lastPrice", 0))
            if last_price <= 0:
                return {"status": "error", "msg": "Invalid price"}

            orders = []
            weights = list(range(1, num_entries + 1))
            total_weight = sum(weights)

            for i in range(num_entries):
                if side.lower() in ("buy", "long"):
                    price = last_price * (1 - spacing_pct / 100 * i)
                else:
                    price = last_price * (1 + spacing_pct / 100 * i)

                qty_fraction = weights[i] / total_weight
                qty = self.adjust_quantity(symbol, total_qty * qty_fraction, category)
                adj_price = self.adjust_price(symbol, price, category)

                orders.append(
                    {
                        "level": i + 1,
                        "price": adj_price,
                        "quantity": qty,
                        "pct_of_total": round(qty_fraction * 100, 1),
                        "notional": round(adj_price * qty, 2),
                    }
                )

            total_notional = sum(o["notional"] for o in orders)
            avg_entry = total_notional / total_qty if total_qty > 0 else 0

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "total_qty": total_qty,
                "num_entries": num_entries,
                "spacing_pct": spacing_pct,
                "orders": orders,
                "avg_entry": round(avg_entry, 6),
                "total_notional": round(total_notional, 2),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def scale_out_position(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        num_exits: int = 3,
        tp_spacing_pct: float = 1.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate scaled exit orders for partial profit taking."""
        try:
            ticker = self.get_ticker(symbol, category)
            if not ticker:
                return {"status": "error", "msg": "Cannot fetch ticker"}
            last_price = float(ticker.get("lastPrice", 0))
            if last_price <= 0:
                return {"status": "error", "msg": "Invalid price"}

            orders = []
            weights = list(range(num_exits, 0, -1))
            total_weight = sum(weights)

            for i in range(num_exits):
                if side.lower() in ("buy", "long"):
                    price = last_price * (1 + tp_spacing_pct / 100 * (i + 1))
                else:
                    price = last_price * (1 - tp_spacing_pct / 100 * (i + 1))

                qty_fraction = weights[i] / total_weight
                qty = self.adjust_quantity(symbol, total_qty * qty_fraction, category)
                adj_price = self.adjust_price(symbol, price, category)

                orders.append(
                    {
                        "level": i + 1,
                        "price": adj_price,
                        "quantity": qty,
                        "pct_of_total": round(qty_fraction * 100, 1),
                        "profit_pct": round(tp_spacing_pct * (i + 1), 2),
                    }
                )

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "total_qty": total_qty,
                "num_exits": num_exits,
                "orders": orders,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def hedge_position(
        self,
        symbol: str,
        hedge_pct: float = 50.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate and optionally place a hedge against an existing position."""
        try:
            positions = self.get_positions(category=category, symbol=symbol)
            active = [p for p in (positions or []) if float(p.get("size", 0)) > 0]
            if not active:
                return {"status": "error", "msg": f"No active position for {symbol}"}

            pos = active[0]
            size = float(pos.get("size", 0))
            side = pos.get("side", "")
            entry = float(pos.get("avgPrice", 0))
            unrealized = float(pos.get("unrealisedPnl", 0))

            hedge_side = "Sell" if side == "Buy" else "Buy"
            hedge_qty = self.adjust_quantity(symbol, size * (hedge_pct / 100), category)

            ticker = self.get_ticker(symbol, category)
            last_price = float(ticker.get("lastPrice", 0)) if ticker else entry

            return {
                "status": "ok",
                "symbol": symbol,
                "original_position": {
                    "side": side,
                    "size": size,
                    "entry": entry,
                    "unrealized_pnl": round(unrealized, 4),
                },
                "hedge": {
                    "side": hedge_side,
                    "quantity": hedge_qty,
                    "hedge_pct": hedge_pct,
                    "estimated_price": last_price,
                    "notional": round(hedge_qty * last_price, 2),
                },
                "note": "Use place_order to execute the hedge. Consider using hedge mode (position_idx).",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_multi_timeframe_signals(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Consolidated signals across 5min, 15min, 1h, 4h timeframes."""
        try:
            timeframes = [("5", "5m"), ("15", "15m"), ("60", "1h"), ("240", "4h")]
            signals = {}
            bull_count = 0
            bear_count = 0

            for interval, label in timeframes:
                klines = self.get_klines(
                    symbol, interval=interval, limit=50, category=category
                )
                if not klines or len(klines) < 20:
                    signals[label] = {"signal": "INSUFFICIENT_DATA"}
                    continue

                closes = [float(k[4]) for k in klines]

                rsi = self.calculate_rsi(closes, 14)
                ema9 = self.calculate_ema(closes, 9)
                ema21 = self.calculate_ema(closes, 21)
                macd_data = self.calculate_macd(closes)
                macd_hist = (
                    macd_data.get("histogram", 0) if isinstance(macd_data, dict) else 0
                )

                bull = 0
                bear = 0
                if rsi > 50:
                    bull += 1
                else:
                    bear += 1
                if ema9 > ema21:
                    bull += 1
                else:
                    bear += 1
                if macd_hist > 0:
                    bull += 1
                else:
                    bear += 1

                if bull >= 3:
                    tf_signal = "BULLISH"
                    bull_count += 1
                elif bear >= 3:
                    tf_signal = "BEARISH"
                    bear_count += 1
                else:
                    tf_signal = "NEUTRAL"

                signals[label] = {
                    "signal": tf_signal,
                    "rsi": round(rsi, 2),
                    "ema9": round(ema9, 6),
                    "ema21": round(ema21, 6),
                    "macd_hist": round(macd_hist, 6),
                }

            total_tf = len(timeframes)
            if bull_count >= 3:
                consensus = "STRONG_BULLISH"
            elif bull_count >= 2:
                consensus = "BULLISH"
            elif bear_count >= 3:
                consensus = "STRONG_BEARISH"
            elif bear_count >= 2:
                consensus = "BEARISH"
            else:
                consensus = "MIXED"

            alignment = bull_count == total_tf or bear_count == total_tf

            return {
                "status": "ok",
                "symbol": symbol,
                "consensus": consensus,
                "alignment": alignment,
                "bull_timeframes": bull_count,
                "bear_timeframes": bear_count,
                "timeframes": signals,
                "recommendation": "Enter with confidence"
                if alignment
                else "Wait for alignment or reduce size",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_market_session_info(self) -> dict:
        """Return current trading session info (Asian/European/US) and optimal trading windows."""
        import datetime

        now = datetime.datetime.utcnow()
        hour = now.hour

        sessions = []
        if 0 <= hour < 8:
            sessions.append("ASIAN")
        if 7 <= hour < 16:
            sessions.append("EUROPEAN")
        if 13 <= hour < 22:
            sessions.append("US")
        if 22 <= hour or hour < 1:
            sessions.append("ASIAN_PRE")

        overlap = len(sessions) > 1
        volatility_expectation = (
            "HIGH" if overlap else "MODERATE" if sessions else "LOW"
        )

        return {
            "status": "ok",
            "utc_hour": hour,
            "utc_time": now.strftime("%H:%M:%S"),
            "active_sessions": sessions,
            "session_overlap": overlap,
            "volatility_expectation": volatility_expectation,
            "optimal_scalping": 13 <= hour <= 16,
            "optimal_swing": 8 <= hour <= 14,
            "funding_countdown_note": "Bybit funding every 8h: 00:00, 08:00, 16:00 UTC",
            "next_funding_hours": (8 - hour % 8) % 8,
        }

    def detect_whale_activity(
        self,
        symbol: str,
        threshold_multiplier: float = 5.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detect large/whale orders in recent trades and orderbook."""
        try:
            trades = self.get_recent_trades(symbol, limit=200, category=category)
            if not trades:
                return {"status": "error", "msg": "No trade data"}

            trade_list = (
                trades
                if isinstance(trades, list)
                else trades.get("result", {}).get("list", [])
            )
            if not trade_list:
                return {"status": "error", "msg": "Empty trade list"}

            sizes = [float(t.get("size", 0)) for t in trade_list]
            avg_size = sum(sizes) / len(sizes) if sizes else 1
            threshold = avg_size * threshold_multiplier

            whale_trades = []
            whale_buy_vol = 0.0
            whale_sell_vol = 0.0

            for t in trade_list:
                size = float(t.get("size", 0))
                if size >= threshold:
                    side = t.get("side", "")
                    price = float(t.get("price", 0))
                    whale_trades.append(
                        {
                            "price": price,
                            "size": size,
                            "side": side,
                            "multiple_of_avg": round(size / avg_size, 1),
                            "time": t.get("time", ""),
                        }
                    )
                    if side == "Buy":
                        whale_buy_vol += size
                    else:
                        whale_sell_vol += size

            whale_bias = "NEUTRAL"
            if whale_buy_vol > whale_sell_vol * 1.5:
                whale_bias = "ACCUMULATING"
            elif whale_sell_vol > whale_buy_vol * 1.5:
                whale_bias = "DISTRIBUTING"

            return {
                "status": "ok",
                "symbol": symbol,
                "avg_trade_size": round(avg_size, 4),
                "whale_threshold": round(threshold, 4),
                "whale_trades_count": len(whale_trades),
                "whale_buy_volume": round(whale_buy_vol, 4),
                "whale_sell_volume": round(whale_sell_vol, 4),
                "whale_bias": whale_bias,
                "recent_whales": whale_trades[:10],
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_fear_greed_signal(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Market sentiment composite from RSI, volume, funding, volatility, and price momentum."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=100, category=category
            )
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]

            rsi = self.calculate_rsi(closes, 14)
            atr = self.calculate_atr(ohlcv, 14)
            atr_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 else 0

            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1

            price_change_24h = (
                ((closes[-1] - closes[-24]) / closes[-24] * 100)
                if len(closes) >= 24 and closes[-24] > 0
                else 0
            )

            rsi_score = 0
            if rsi > 70:
                rsi_score = min(100, (rsi - 50) * 2)
            elif rsi < 30:
                rsi_score = max(0, rsi * 2)
            else:
                rsi_score = rsi

            vol_score = min(100, vol_ratio * 50)
            momentum_score = min(100, max(0, 50 + price_change_24h * 5))
            volatility_score = min(100, atr_pct * 20)

            composite = (
                rsi_score * 0.35
                + momentum_score * 0.30
                + vol_score * 0.15
                + volatility_score * 0.20
            )

            if composite > 75:
                label = "EXTREME_GREED"
            elif composite > 55:
                label = "GREED"
            elif composite > 45:
                label = "NEUTRAL"
            elif composite > 25:
                label = "FEAR"
            else:
                label = "EXTREME_FEAR"

            contrarian = {
                "EXTREME_GREED": "Consider taking profits or reducing longs",
                "GREED": "Be cautious with new longs",
                "NEUTRAL": "No strong contrarian signal",
                "FEAR": "Look for buying opportunities",
                "EXTREME_FEAR": "Strong buy signal (contrarian)",
            }

            return {
                "status": "ok",
                "symbol": symbol,
                "fear_greed_score": round(composite, 1),
                "label": label,
                "contrarian_advice": contrarian.get(label, ""),
                "components": {
                    "rsi_score": round(rsi_score, 1),
                    "momentum_score": round(momentum_score, 1),
                    "volume_score": round(vol_score, 1),
                    "volatility_score": round(volatility_score, 1),
                },
                "raw": {
                    "rsi": round(rsi, 2),
                    "atr_pct": round(atr_pct, 3),
                    "vol_ratio": round(vol_ratio, 2),
                    "price_change_24h": round(price_change_24h, 2),
                },
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def calculate_compound_growth(
        self,
        starting_capital: float,
        daily_return_pct: float,
        days: int = 30,
        win_rate_pct: float = 60.0,
        trades_per_day: int = 3,
    ) -> dict:
        """Project compound growth with given win rate and daily return assumptions."""
        capital = starting_capital
        daily_results = []

        for day in range(1, days + 1):
            day_pnl = 0.0
            if trades_per_day <= 0 or starting_capital <= 0:  # Handle invalid inputs
                logger.warning(
                    "Invalid input for compound growth: trades_per_day=%d, starting_capital=%f",
                    trades_per_day,
                    starting_capital,
                )
                break  # Exit loop if inputs are invalid

            # Calculate capital change for the day, ensuring no division by zero
            capital_change_per_trade = (daily_return_pct / 100.0) / max(
                trades_per_day, 1
            )  # Prevent div by zero

            for _ in range(trades_per_day):
                if random.random() * 100 < win_rate_pct:
                    day_pnl += (
                        capital * capital_change_per_trade
                    )  # Use calculated change
                else:
                    day_pnl -= (
                        capital * capital_change_per_trade * 0.8
                    )  # Use calculated change

            capital += day_pnl
            if day % 7 == 0 or day == days:
                daily_results.append(
                    {
                        "day": day,
                        "capital": round(capital, 2),
                        "growth_pct": round(
                            ((capital - starting_capital) / starting_capital) * 100, 2
                        )
                        if starting_capital > 0
                        else 0,
                    }
                )

        return {
            "status": "ok",
            "starting_capital": starting_capital,
            "daily_return_target_pct": daily_return_pct,
            "win_rate_pct": win_rate_pct,
            "trades_per_day": trades_per_day,
            "final_capital": round(capital, 2),
            "total_growth_pct": round(
                ((capital - starting_capital) / starting_capital) * 100, 2
            ),
            "total_profit": round(capital - starting_capital, 2),
            "milestones": daily_results,
            "note": "Projections assume constant parameters. Real results vary. Use half-Kelly sizing.",
        }

    def get_trade_checklist(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_usdt: float,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Pre-trade validation checklist: verifies all conditions before entering a trade."""
        checks = []
        passed = 0
        total = 0

        is_long = side.lower() in ("buy", "long")
        sl_dist = abs(entry_price - stop_loss)
        tp_dist = abs(take_profit - entry_price)
        rr = tp_dist / sl_dist if sl_dist > 0 else 0

        total += 1
        if rr >= 1.5:
            checks.append(
                {"check": "Risk:Reward >= 1.5", "passed": True, "value": round(rr, 2)}
            )
            passed += 1
        else:
            checks.append(
                {"check": "Risk:Reward >= 1.5", "passed": False, "value": round(rr, 2)}
            )

        total += 1
        if (is_long and stop_loss < entry_price) or (
            not is_long and stop_loss > entry_price
        ):
            checks.append({"check": "Stop loss correctly placed", "passed": True})
            passed += 1
        else:
            checks.append({"check": "Stop loss correctly placed", "passed": False})

        total += 1
        if (is_long and take_profit > entry_price) or (
            not is_long and take_profit < entry_price
        ):
            checks.append({"check": "Take profit correctly placed", "passed": True})
            passed += 1
        else:
            checks.append({"check": "Take profit correctly placed", "passed": False})

        try:
            heat = self.get_portfolio_heat(category=category)
            total += 1
            if isinstance(heat, dict) and heat.get("heat_pct", 100) < 10:
                checks.append(
                    {
                        "check": "Portfolio heat < 10%",
                        "passed": True,
                        "value": heat.get("heat_pct"),
                    }
                )
                passed += 1
            else:
                checks.append(
                    {
                        "check": "Portfolio heat < 10%",
                        "passed": False,
                        "value": heat.get("heat_pct")
                        if isinstance(heat, dict)
                        else "N/A",
                    }
                )
        except Exception:
            pass

        try:
            entry_quality = self.get_entry_timing(symbol, side, category)
            total += 1
            if (
                isinstance(entry_quality, dict)
                and entry_quality.get("entry_score", 0) >= 50
            ):
                checks.append(
                    {
                        "check": "Entry timing >= 50",
                        "passed": True,
                        "value": entry_quality.get("entry_score"),
                    }
                )
                passed += 1
            else:
                checks.append(
                    {
                        "check": "Entry timing >= 50",
                        "passed": False,
                        "value": entry_quality.get("entry_score")
                        if isinstance(entry_quality, dict)
                        else "N/A",
                    }
                )
        except Exception:
            pass

        try:
            mtf = self.get_multi_timeframe_signals(symbol, category)
            total += 1
            if isinstance(mtf, dict):
                consensus = mtf.get("consensus", "")
                aligned = (is_long and "BULL" in consensus) or (
                    not is_long and "BEAR" in consensus
                )
                checks.append(
                    {
                        "check": "Multi-TF alignment",
                        "passed": aligned,
                        "value": consensus,
                    }
                )
                if aligned:
                    passed += 1
            else:
                checks.append(
                    {"check": "Multi-TF alignment", "passed": False, "value": "N/A"}
                )
        except Exception:
            pass

        score = (passed / total * 100) if total > 0 else 0
        verdict = "GO" if score >= 70 else "CAUTION" if score >= 50 else "NO_TRADE"

        return {
            "status": "ok",
            "symbol": symbol,
            "side": side,
            "checklist_score": round(score, 1),
            "verdict": verdict,
            "passed": passed,
            "total_checks": total,
            "checks": checks,
        }

    def auto_set_leverage(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Automatically set optimal leverage based on current volatility."""
        try:
            klines = self.get_klines(symbol, interval="60", limit=50, category=category)
            if not klines or len(klines) < 20:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv, 14)
            atr_pct = (atr / closes[-1]) * 100 if closes[-1] > 0 else 5

            if atr_pct > 5.0:
                suggested = 1
            elif atr_pct > 3.0:
                suggested = 2
            elif atr_pct > 2.0:
                suggested = 3
            elif atr_pct > 1.0:
                suggested = 5
            elif atr_pct > 0.5:
                suggested = 10
            else:
                suggested = 15

            result = self.set_leverage(symbol, suggested, category=category)

            return {
                "status": "ok",
                "symbol": symbol,
                "atr_pct": round(atr_pct, 3),
                "suggested_leverage": suggested,
                "set_result": result,
                "reasoning": f"ATR={atr_pct:.1f}% → leverage {suggested}x (lower vol = higher leverage allowed)",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_unrealized_pnl_report(
        self,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detailed unrealized PnL breakdown for all open positions."""
        try:
            positions = self.get_positions(category=category)
            active = [p for p in (positions or []) if float(p.get("size", 0)) > 0]
            if not active:
                return {
                    "status": "ok",
                    "positions": [],
                    "total_unrealized": 0,
                    "msg": "No open positions",
                }

            total_unrealized = 0.0
            total_notional = 0.0
            details = []

            for pos in active:
                sym = pos.get("symbol", "")
                side = pos.get("side", "")
                size = float(pos.get("size", 0))
                entry = float(pos.get("avgPrice", 0))
                mark = float(pos.get("markPrice", 0))
                leverage = float(pos.get("leverage", 1))
                unrealized = float(pos.get("unrealisedPnl", 0))
                notional = size * mark

                pnl_pct = (
                    (unrealized / (size * entry) * 100) if (size * entry) > 0 else 0
                )
                roi = pnl_pct * leverage

                total_unrealized += unrealized
                total_notional += notional

                details.append(
                    {
                        "symbol": sym,
                        "side": side,
                        "size": size,
                        "entry": entry,
                        "mark": mark,
                        "leverage": leverage,
                        "unrealized_pnl": round(unrealized, 4),
                        "pnl_pct": round(pnl_pct, 3),
                        "roi_pct": round(roi, 3),
                        "notional": round(notional, 2),
                        "status": "PROFIT"
                        if unrealized > 0
                        else "LOSS"
                        if unrealized < 0
                        else "FLAT",
                    }
                )

            return {
                "status": "ok",
                "total_unrealized_pnl": round(total_unrealized, 4),
                "total_notional": round(total_notional, 2),
                "position_count": len(details),
                "positions": details,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def detect_divergence_signals(
        self,
        symbol: str,
        lookback: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detect RSI and MACD divergence signals (bullish/bearish)."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=lookback, category=category
            )
            if not klines or len(klines) < 30:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            lows = [float(k[3]) for k in klines]
            highs = [float(k[2]) for k in klines]

            rsi_values = []
            for i in range(14, len(closes)):
                rsi_values.append(self.calculate_rsi(closes[: i + 1], 14))

            divergences = []

            if len(rsi_values) >= 10:
                price_recent_low = min(lows[-10:])
                price_prev_low = (
                    min(lows[-20:-10]) if len(lows) >= 20 else price_recent_low
                )
                rsi_recent_low = min(rsi_values[-10:])
                rsi_prev_low = (
                    min(rsi_values[-20:-10])
                    if len(rsi_values) >= 20
                    else rsi_recent_low
                )

                if price_recent_low < price_prev_low and rsi_recent_low > rsi_prev_low:
                    divergences.append(
                        {
                            "type": "BULLISH_RSI_DIVERGENCE",
                            "description": "Price making lower lows but RSI making higher lows",
                            "strength": "STRONG" if rsi_recent_low < 35 else "MODERATE",
                        }
                    )

                price_recent_high = max(highs[-10:])
                price_prev_high = (
                    max(highs[-20:-10]) if len(highs) >= 20 else price_recent_high
                )
                rsi_recent_high = max(rsi_values[-10:])
                rsi_prev_high = (
                    max(rsi_values[-20:-10])
                    if len(rsi_values) >= 20
                    else rsi_recent_high
                )

                if (
                    price_recent_high > price_prev_high
                    and rsi_recent_high < rsi_prev_high
                ):
                    divergences.append(
                        {
                            "type": "BEARISH_RSI_DIVERGENCE",
                            "description": "Price making higher highs but RSI making lower highs",
                            "strength": "STRONG"
                            if rsi_recent_high > 65
                            else "MODERATE",
                        }
                    )

            volumes = [float(k[5]) for k in klines]
            obv = self.calculate_obv(closes, volumes)
            if len(obv) >= 20:
                obv_slope = (obv[-1] - obv[-10]) / 10 if len(obv) >= 10 else 0
                price_slope = (
                    (closes[-1] - closes[-10]) / 10 if len(closes) >= 10 else 0
                )

                if price_slope > 0 and obv_slope < 0:
                    divergences.append(
                        {
                            "type": "BEARISH_VOLUME_DIVERGENCE",
                            "description": "Price rising but OBV declining - distribution",
                            "strength": "MODERATE",
                        }
                    )
                elif price_slope < 0 and obv_slope > 0:
                    divergences.append(
                        {
                            "type": "BULLISH_VOLUME_DIVERGENCE",
                            "description": "Price falling but OBV rising - accumulation",
                            "strength": "MODERATE",
                        }
                    )

            return {
                "status": "ok",
                "symbol": symbol,
                "divergences_found": len(divergences),
                "divergences": divergences,
                "current_rsi": round(rsi_values[-1], 2) if rsi_values else None,
                "current_price": closes[-1],
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_optimal_entry_zones(
        self,
        symbol: str,
        side: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Identify optimal price zones for entry based on S/R, Fib, VWAP, and volume profile."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=200, category=category
            )
            if not klines or len(klines) < 50:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            last_price = closes[-1]

            recent_high = max(highs[-50:])
            recent_low = min(lows[-50:])
            fib_range = recent_high - recent_low

            fib_levels = {
                "fib_236": round(recent_high - fib_range * 0.236, 6),
                "fib_382": round(recent_high - fib_range * 0.382, 6),
                "fib_500": round(recent_high - fib_range * 0.500, 6),
                "fib_618": round(recent_high - fib_range * 0.618, 6),
                "fib_786": round(recent_high - fib_range * 0.786, 6),
            }

            ema20 = self.calculate_ema(closes, 20)
            ema50 = self.calculate_ema(closes, 50)
            bb = self.calculate_bollinger_bands(closes, 20, 2.0)
            bb_lower = bb.get("lower", 0) if isinstance(bb, dict) else 0
            bb_upper = bb.get("upper", 0) if isinstance(bb, dict) else 0

            try:
                vp = self.get_volume_profile(
                    symbol, num_bins=15, lookback=100, category=category
                )
                poc = (
                    vp.get("poc_price", last_price)
                    if isinstance(vp, dict)
                    else last_price
                )
                vah = vp.get("vah", last_price) if isinstance(vp, dict) else last_price
                val = vp.get("val", last_price) if isinstance(vp, dict) else last_price
            except Exception:
                poc = last_price
                vah = last_price
                val = last_price

            is_buy = side.lower() in ("buy", "long")
            zones = []

            key_levels = [
                ("EMA20", ema20),
                ("EMA50", ema50),
                (
                    "BB_Lower" if is_buy else "BB_Upper",
                    bb_lower if is_buy else bb_upper,
                ),
                ("Volume_POC", poc),
                ("Volume_VAL" if is_buy else "Volume_VAH", val if is_buy else vah),
            ]
            for name, value in fib_levels.items():
                key_levels.append((name, value))

            for name, level in key_levels:
                if is_buy and level < last_price:
                    distance = ((last_price - level) / last_price) * 100
                    if distance < 5:
                        zones.append(
                            {
                                "zone": name,
                                "price": round(level, 6),
                                "distance_pct": round(distance, 3),
                            }
                        )
                elif not is_buy and level > last_price:
                    distance = ((level - last_price) / last_price) * 100
                    if distance < 5:
                        zones.append(
                            {
                                "zone": name,
                                "price": round(level, 6),
                                "distance_pct": round(distance, 3),
                            }
                        )

            zones.sort(key=lambda x: x["distance_pct"])

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "current_price": last_price,
                "optimal_zones": zones[:8],
                "fib_levels": fib_levels,
                "volume_profile": {
                    "poc": round(poc, 6),
                    "vah": round(vah, 6),
                    "val": round(val, 6),
                },
                "moving_averages": {"ema20": round(ema20, 6), "ema50": round(ema50, 6)},
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_spread_analysis(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Analyze current bid/ask spread and estimate slippage for different order sizes."""
        try:
            ob = self.get_orderbook(symbol, limit=50, category=category)
            if not ob:
                return {"status": "error", "msg": "Cannot fetch orderbook"}

            result = ob.get("result", ob)
            bids = result.get("b", [])
            asks = result.get("a", [])
            if not bids or not asks:
                return {"status": "error", "msg": "Empty orderbook"}

            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            spread_bps = (spread / mid_price) * 10000

            test_sizes_usdt = [100, 500, 1000, 5000, 10000]
            slippage_estimates = []

            for size_usdt in test_sizes_usdt:
                filled = 0.0
                cost = 0.0
                for ask_price, ask_qty in asks:
                    ask_p = float(ask_price)
                    ask_q = float(ask_qty)
                    remaining = (size_usdt - cost) / ask_p if ask_p > 0 else 0
                    fill = min(ask_q, remaining)
                    filled += fill
                    cost += fill * ask_p
                    if cost >= size_usdt:
                        break

                avg_fill = cost / filled if filled > 0 else best_ask
                slippage = ((avg_fill - best_ask) / best_ask) * 10000

                slippage_estimates.append(
                    {
                        "size_usdt": size_usdt,
                        "avg_fill_price": round(avg_fill, 6),
                        "slippage_bps": round(slippage, 2),
                        "total_cost": round(cost, 2),
                    }
                )

            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids)
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks)

            return {
                "status": "ok",
                "symbol": symbol,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": round(mid_price, 6),
                "spread": round(spread, 6),
                "spread_bps": round(spread_bps, 2),
                "spread_quality": "TIGHT"
                if spread_bps < 3
                else "NORMAL"
                if spread_bps < 10
                else "WIDE",
                "bid_depth_usdt": round(bid_depth, 2),
                "ask_depth_usdt": round(ask_depth, 2),
                "depth_imbalance": round(bid_depth / ask_depth, 3)
                if ask_depth > 0
                else 0,
                "slippage_estimates": slippage_estimates,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_correlation_analysis(
        self,
        symbols: List[str],
        interval: str = "60",
        lookback: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate price correlation between multiple trading pairs."""
        try:
            price_data: Dict[str, List[float]] = {}
            for sym in symbols:
                klines = self.get_klines(
                    sym, interval=interval, limit=lookback, category=category
                )
                if klines and len(klines) >= 20:
                    price_data[sym] = [float(k[4]) for k in klines]

            if len(price_data) < 2:
                return {"status": "error", "msg": "Need at least 2 symbols with data"}

            min_len = min(len(v) for v in price_data.values())
            for sym in price_data:
                price_data[sym] = price_data[sym][-min_len:]

            returns: Dict[str, List[float]] = {}
            for sym, prices in price_data.items():
                ret = [
                    (prices[i] - prices[i - 1]) / prices[i - 1]
                    for i in range(1, len(prices))
                ]
                returns[sym] = ret

            correlations = {}
            syms = list(returns.keys())
            for i in range(len(syms)):
                for j in range(i + 1, len(syms)):
                    s1, s2 = syms[i], syms[j]
                    r1, r2 = returns[s1], returns[s2]
                    n = len(r1)
                    mean1, mean2 = sum(r1) / n, sum(r2) / n
                    cov = sum((r1[k] - mean1) * (r2[k] - mean2) for k in range(n)) / n
                    std1 = (
                        (sum((x - mean1) ** 2 for x in r1) / n) ** 0.5 if n > 0 else 0
                    )
                    std2 = (
                        (sum((x - mean2) ** 2 for x in r2) / n) ** 0.5 if n > 0 else 0
                    )

                    # Safe correlation calculation
                    if std1 == 0 or std2 == 0:
                        corr = 0.0  # Handle as undefined
                        logger.warning(
                            f"Zero standard deviation for {s1} or {s2} in correlation analysis."
                        )
                    else:
                        corr = cov / (std1 * std2)

                    pair = f"{s1}/{s2}"
                    label = (
                        "STRONG_POS"
                        if corr > 0.7
                        else "MODERATE_POS"
                        if corr > 0.3
                        else "WEAK"
                        if corr > -0.3
                        else "MODERATE_NEG"
                        if corr > -0.7
                        else "STRONG_NEG"
                    )
                    correlations[pair] = {
                        "correlation": round(corr, 4),
                        "label": label,
                        "diversification": corr < 0.3,
                    }

            return {
                "status": "ok",
                "symbols": syms,
                "period": f"{lookback} bars @ {interval}",
                "correlations": correlations,
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def smart_trailing_stop(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Calculate ATR-based adaptive trailing stop for an open position."""
        try:
            positions = self.get_positions(category=category, symbol=symbol)
            active = [p for p in (positions or []) if float(p.get("size", 0)) > 0]
            if not active:
                return {"status": "error", "msg": f"No active position for {symbol}"}

            pos = active[0]
            side = pos.get("side", "")
            entry = float(pos.get("avgPrice", 0))
            mark = float(pos.get("markPrice", 0))

            klines = self.get_klines(symbol, interval="15", limit=50, category=category)
            if not klines or len(klines) < 20:
                return {"status": "error", "msg": "Insufficient data"}

            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv, 14)

            if side == "Buy":
                tight_sl = mark - atr * 1.0
                normal_sl = mark - atr * 1.5
                wide_sl = mark - atr * 2.5
                in_profit = mark > entry
            else:
                tight_sl = mark + atr * 1.0
                normal_sl = mark + atr * 1.5
                wide_sl = mark + atr * 2.5
                in_profit = mark < entry

            recommended = normal_sl if in_profit else wide_sl

            return {
                "status": "ok",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "mark": mark,
                "atr": round(atr, 6),
                "in_profit": in_profit,
                "trailing_stops": {
                    "tight_1atr": round(tight_sl, 6),
                    "normal_1_5atr": round(normal_sl, 6),
                    "wide_2_5atr": round(wide_sl, 6),
                },
                "recommended": round(recommended, 6),
                "note": "Use set_trading_stop to apply. Recommended uses 1.5 ATR in profit, 2.5 ATR at loss.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

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

            klines.reverse()

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
                        if current_price > ema20_4h and score > 0:
                            mtf_alignment = "ALIGNED_BULLISH"
                        elif current_price < ema20_4h and score < 0:
                            mtf_alignment = "ALIGNED_BEARISH"
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

            # Risk Metrics
            stop_loss = (
                current_price - (atr * 2.0)
                if score >= 0
                else current_price + (atr * 2.0)
            )
            take_profit = (
                current_price + (atr * 4.0)
                if score >= 0
                else current_price - (atr * 4.0)
            )

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
                    "adx": self.calculate_adx(highs, lows, closes),
                    "cci": self.calculate_cci(highs, lows, closes),
                    "stoch_rsi": self.calculate_stoch_rsi(closes),
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

            # Log retry attempt
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Attempt %d/%d failed for action %s: %s. Retrying in %.1fs",
                attempt,
                max_retries,
                fn.__name__,
                last_exc,
                delay,
            )
            time.sleep(delay)

        logger.error(
            "All %d attempts exhausted for action %s. Last error: %s",
            max_retries,
            fn.__name__,
            last_exc,
        )
        return {"status": "error", "msg": str(last_exc)}

    # ══════════════════════════════════════════════════════════
    # BREAKEVEN & FEE ANALYSIS
    # ══════════════════════════════════════════════════════════
    def calculate_breakeven(
        self,
        entry_price: float,
        qty: float,
        side: str,
        fee_rate: float = 0.0006,
        funding_rate: float = 0.0,
        holding_periods: int = 0,
        leverage: float = 1.0,
    ) -> dict:
        """Calculate breakeven price accounting for trading fees, funding, and slippage."""
        notional = entry_price * qty
        entry_fee = notional * fee_rate
        exit_fee_est = notional * fee_rate
        total_fees = entry_fee + exit_fee_est
        funding_cost = (
            notional * abs(funding_rate) * holding_periods
            if holding_periods > 0
            else 0.0
        )
        total_cost = total_fees + funding_cost
        price_move_needed = total_cost / qty if qty > 0 else 0.0
        if side.lower() in ("buy", "long"):
            breakeven_price = entry_price + price_move_needed
        else:
            breakeven_price = entry_price - price_move_needed
        fee_pct_of_position = (total_cost / notional) * 100 if notional > 0 else 0.0
        margin_required = notional / leverage if leverage > 0 else notional
        fee_pct_of_margin = (
            (total_cost / margin_required) * 100 if margin_required > 0 else 0.0
        )
        return {
            "entry_price": entry_price,
            "breakeven_price": round(breakeven_price, 8),
            "price_move_needed": round(price_move_needed, 8),
            "price_move_pct": round((price_move_needed / entry_price) * 100, 6)
            if entry_price > 0
            else 0.0,
            "entry_fee": round(entry_fee, 8),
            "exit_fee_est": round(exit_fee_est, 8),
            "funding_cost": round(funding_cost, 8),
            "total_cost": round(total_cost, 8),
            "fee_pct_of_position": round(fee_pct_of_position, 4),
            "fee_pct_of_margin": round(fee_pct_of_margin, 4),
            "side": side,
            "qty": qty,
            "leverage": leverage,
            "notional": round(notional, 4),
            "margin_required": round(margin_required, 4),
        }

    def calculate_breakeven_with_fees(
        self,
        symbol: str,
        entry_price: float,
        qty: float,
        side: str,
        category: Category = Category.LINEAR,
        leverage: float = 1.0,
        holding_hours: float = 0.0,
    ) -> dict:
        """Live breakeven calculation using actual fee rates and funding from Bybit."""
        try:
            fee_resp = self.get_fee_rate(symbol, category)
            fee_list = fee_resp.get("result", {}).get("list", [])
            if fee_list:
                maker_fee = float(fee_list[0].get("makerFeeRate", 0.0002))
                taker_fee = float(fee_list[0].get("takerFeeRate", 0.0006))
            else:
                maker_fee = 0.0002
                taker_fee = 0.0006
            funding = self.get_funding_rate(symbol, category)
            fund_rate = float(funding.get("fundingRate", 0)) if funding else 0.0
            holding_periods = int(holding_hours / 8) if holding_hours > 0 else 0
            taker_be = self.calculate_breakeven(
                entry_price, qty, side, taker_fee, fund_rate, holding_periods, leverage
            )
            maker_be = self.calculate_breakeven(
                entry_price, qty, side, maker_fee, fund_rate, holding_periods, leverage
            )
            return {
                "symbol": symbol,
                "taker_breakeven": taker_be,
                "maker_breakeven": maker_be,
                "maker_fee_rate": maker_fee,
                "taker_fee_rate": taker_fee,
                "funding_rate": fund_rate,
                "holding_periods": holding_periods,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Breakeven calculation failed: %s", e)
            return {"status": "error", "msg": str(e)}

    def calculate_profit_after_fees(
        self,
        entry_price: float,
        exit_price: float,
        qty: float,
        side: str,
        fee_rate: float = 0.0006,
        funding_rate: float = 0.0,
        holding_periods: int = 0,
        leverage: float = 1.0,
    ) -> dict:
        """Calculate net profit after all fees, funding costs, with ROI on margin."""
        notional_entry = entry_price * qty
        notional_exit = exit_price * qty
        entry_fee = notional_entry * fee_rate
        exit_fee = notional_exit * fee_rate
        total_fees = entry_fee + exit_fee
        funding_cost = notional_entry * abs(funding_rate) * holding_periods
        if side.lower() in ("buy", "long"):
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty
        net_pnl = gross_pnl - total_fees - funding_cost
        margin = notional_entry / leverage if leverage > 0 else notional_entry
        roi_on_margin = (net_pnl / margin) * 100 if margin > 0 else 0.0
        roi_on_position = (
            (net_pnl / notional_entry) * 100 if notional_entry > 0 else 0.0
        )
        is_profitable = net_pnl > 0
        return {
            "gross_pnl": round(gross_pnl, 8),
            "entry_fee": round(entry_fee, 8),
            "exit_fee": round(exit_fee, 8),
            "total_fees": round(total_fees, 8),
            "funding_cost": round(funding_cost, 8),
            "net_pnl": round(net_pnl, 8),
            "roi_on_margin": round(roi_on_margin, 4),
            "roi_on_position": round(roi_on_position, 4),
            "is_profitable": is_profitable,
            "margin_used": round(margin, 4),
            "leverage": leverage,
            "side": side,
        }

    # ══════════════════════════════════════════════════════════
    # L2 ORDERBOOK ANALYSIS
    # ══════════════════════════════════════════════════════════
    def get_l2_orderbook_analysis(
        self,
        symbol: str,
        depth: int = 50,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Deep L2 orderbook analysis: imbalance, walls, support/resistance, liquidity."""
        try:
            ob = self.get_orderbook(symbol, limit=depth, category=category)
            ob_result = ob.get("result", {})
            bids = [(float(b[0]), float(b[1])) for b in ob_result.get("b", [])]
            asks = [(float(a[0]), float(a[1])) for a in ob_result.get("a", [])]
            if not bids or not asks:
                return {"status": "error", "msg": "Empty orderbook"}
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            spread_pct = (spread / mid_price) * 100
            total_bid_vol = sum(b[1] for b in bids)
            total_ask_vol = sum(a[1] for a in asks)
            total_vol = total_bid_vol + total_ask_vol
            imbalance = (
                (total_bid_vol - total_ask_vol) / total_vol if total_vol > 0 else 0
            )
            bid_notional = sum(b[0] * b[1] for b in bids)
            ask_notional = sum(a[0] * a[1] for a in asks)
            avg_bid_size = total_bid_vol / len(bids) if bids else 0
            avg_ask_size = total_ask_vol / len(asks) if asks else 0
            wall_threshold = max(avg_bid_size, avg_ask_size) * 3
            bid_walls = [
                {"price": b[0], "size": b[1]} for b in bids if b[1] >= wall_threshold
            ]
            ask_walls = [
                {"price": a[0], "size": a[1]} for a in asks if a[1] >= wall_threshold
            ]
            bid_cum = []
            cum = 0
            for b in bids:
                cum += b[1]
                bid_cum.append({"price": b[0], "cumulative": round(cum, 4)})
            ask_cum = []
            cum = 0
            for a in asks:
                cum += a[1]
                ask_cum.append({"price": a[0], "cumulative": round(cum, 4)})
            top5_bid_vol = sum(b[1] for b in bids[:5])
            top5_ask_vol = sum(a[1] for a in asks[:5])
            top5_imbalance = (
                (top5_bid_vol - top5_ask_vol) / (top5_bid_vol + top5_ask_vol)
                if (top5_bid_vol + top5_ask_vol) > 0
                else 0
            )
            pct_ranges = [0.001, 0.005, 0.01, 0.02, 0.05]
            depth_profile = {}
            for pct in pct_ranges:
                bid_limit = mid_price * (1 - pct)
                ask_limit = mid_price * (1 + pct)
                bid_vol_in_range = sum(b[1] for b in bids if b[0] >= bid_limit)
                ask_vol_in_range = sum(a[1] for a in asks if a[0] <= ask_limit)
                depth_profile[f"{pct * 100:.1f}%"] = {
                    "bid_vol": round(bid_vol_in_range, 4),
                    "ask_vol": round(ask_vol_in_range, 4),
                    "imbalance": round(
                        (bid_vol_in_range - ask_vol_in_range)
                        / max(bid_vol_in_range + ask_vol_in_range, 1e-9),
                        4,
                    ),
                }
            if imbalance > 0.3:
                pressure = "STRONG_BUY_PRESSURE"
            elif imbalance > 0.1:
                pressure = "BUY_PRESSURE"
            elif imbalance < -0.3:
                pressure = "STRONG_SELL_PRESSURE"
            elif imbalance < -0.1:
                pressure = "SELL_PRESSURE"
            else:
                pressure = "BALANCED"
            return {
                "symbol": symbol,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": round(mid_price, 8),
                "spread": round(spread, 8),
                "spread_pct": round(spread_pct, 6),
                "spread_bps": round(spread_pct * 100, 2),
                "total_bid_vol": round(total_bid_vol, 4),
                "total_ask_vol": round(total_ask_vol, 4),
                "bid_notional": round(bid_notional, 2),
                "ask_notional": round(ask_notional, 2),
                "imbalance": round(imbalance, 4),
                "top5_imbalance": round(top5_imbalance, 4),
                "pressure": pressure,
                "bid_walls": bid_walls[:5],
                "ask_walls": ask_walls[:5],
                "depth_profile": depth_profile,
                "bid_levels": len(bids),
                "ask_levels": len(asks),
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("L2 orderbook analysis failed for %s: %s", symbol, e)
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # MISSING BYBIT V5 ENDPOINTS
    # ══════════════════════════════════════════════════════════
    def get_account_info(self) -> dict:
        """Get account info: margin mode, SMP group, etc."""
        resp = self.api_request("GET", "/v5/account/info", params={})
        return resp.get("result", {})

    def get_instruments_info(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> List[dict]:
        """Get full instrument specifications (lot size, tick size, filters)."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if status:
            params["status"] = status
        resp = self.api_request(
            "GET", "/v5/market/instruments-info", params=params, signed=False
        )
        return resp.get("result", {}).get("list", [])

    def get_risk_limit(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        """Get risk limit tiers for a symbol."""
        resp = self.api_request(
            "GET",
            "/v5/market/risk-limit",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def set_risk_limit(
        self,
        symbol: str,
        risk_id: int,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Set risk limit tier for a position."""

        return self.api_request(
            "POST",
            "/v5/position/set-risk-limit",
            json_data={"category": category, "symbol": symbol, "riskId": risk_id},
        )

    def get_api_key_info(self) -> dict:
        """Get current API key permissions and info."""
        resp = self.api_request("GET", "/v5/user/query-api", params={})
        return resp.get("result", {})

    def get_funding_rate_history(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Get historical funding rates."""
        params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        resp = self.api_request(
            "GET", "/v5/market/funding/history", params=params, signed=False
        )
        return resp.get("result", {}).get("list", [])

    def get_server_time(self) -> dict:
        """Get Bybit server time."""
        resp = self.api_request("GET", "/v5/market/time", signed=False)
        return resp.get("result", {})

    def get_insurance_pool(self, coin: str = "USDT") -> List[dict]:
        """Get insurance pool data."""
        resp = self.api_request(
            "GET",
            "/v5/market/insurance",
            params={"coin": coin},
            signed=False,
        )
        return resp.get("result", {}).get("list", [])

    def get_delivery_price(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        """Get delivery price for futures."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        resp = self.api_request(
            "GET", "/v5/market/delivery-price", params=params, signed=False
        )
        return resp.get("result", {}).get("list", [])

    def get_historical_volatility(
        self,
        category: Category = Category.OPTION,
        period: int = 7,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Get historical volatility for options."""
        params: Dict[str, Any] = {"category": category, "period": period}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        resp = self.api_request(
            "GET", "/v5/market/historical-volatility", params=params, signed=False
        )
        return resp.get("result", [])

    def get_trade_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Get user's trade (execution) history."""
        params: Dict[str, Any] = {"category": category, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        resp = self.api_request("GET", "/v5/execution/list", params=params)
        return resp.get("result", {}).get("list", [])

    def get_borrow_history(
        self,
        currency: Optional[str] = None,
        limit: int = 50,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """Get borrow history for margin trading."""
        params: Dict[str, Any] = {"limit": limit}
        if currency:
            params["currency"] = currency
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        resp = self.api_request("GET", "/v5/account/borrow-history", params=params)
        return resp.get("result", {}).get("list", [])

    def get_collateral_info(self, currency: Optional[str] = None) -> List[dict]:
        """Get collateral info for assets."""
        params: Dict[str, Any] = {}
        if currency:
            params["currency"] = currency
        resp = self.api_request("GET", "/v5/account/collateral-info", params=params)
        return resp.get("result", {}).get("list", [])

    def get_coin_greeks(self) -> List[dict]:
        """Get current coin greeks (for options/delta)."""
        resp = self.api_request("GET", "/v5/asset/coin-greeks", params={})
        return resp.get("result", {}).get("list", [])

    def internal_transfer(
        self,
        coin: str,
        amount: str,
        from_account: str = "UNIFIED",
        to_account: str = "FUND",
    ) -> dict:
        """Transfer between account types (UNIFIED, SPOT, FUND, etc.)."""
        transfer_id = str(uuid.uuid4())
        payload = {
            "transferId": transfer_id,
            "coin": coin,
            "amount": amount,
            "fromAccountType": from_account,
            "toAccountType": to_account,
        }

        return self.api_request(
            "POST", "/v5/asset/transfer/inter-transfer", json_data=payload
        )

    def get_coin_balance(self, coin: str, account_type: str = "FUND") -> dict:
        """Get single coin balance for a specific account type."""
        resp = self.api_request(
            "GET",
            "/v5/asset/transfer/query-account-coin-balance",
            params={"coin": coin, "accountType": account_type},
        )
        return resp.get("result", {})

    def get_withdrawable_amount(self) -> dict:
        """Get withdrawable amount for the account."""
        resp = self.api_request(
            "GET", "/v5/asset/withdraw/withdrawable-amount", params={}
        )
        return resp.get("result", {})

    # ══════════════════════════════════════════════════════════
    # PROFITABLE MACRO STRATEGIES
    # ══════════════════════════════════════════════════════════
    def macro_dca_plan(
        self,
        symbol: str,
        total_usdt: float,
        num_orders: int = 5,
        price_range_pct: float = 5.0,
        side: str = "Buy",
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate a DCA (Dollar Cost Average) plan with orders spaced across a price range."""
        try:
            ticker = self.get_ticker(symbol, category)
            current_price = float(ticker.get("lastPrice", 0))
            if current_price <= 0:
                return {"status": "error", "msg": "Could not get current price"}
            usdt_per_order = total_usdt / num_orders
            orders = []
            if side.lower() == "buy":
                low_price = current_price * (1 - price_range_pct / 100)
                price_step = (current_price - low_price) / max(num_orders - 1, 1)
                for i in range(num_orders):
                    order_price = current_price - (price_step * i)
                    order_price = self.adjust_price(symbol, order_price, category)
                    order_qty = usdt_per_order / order_price if order_price > 0 else 0
                    order_qty = self.adjust_quantity(symbol, order_qty, category)
                    orders.append(
                        {
                            "level": i + 1,
                            "price": order_price,
                            "qty": order_qty,
                            "notional": round(order_price * order_qty, 4),
                        }
                    )
            else:
                high_price = current_price * (1 + price_range_pct / 100)
                price_step = (high_price - current_price) / max(num_orders - 1, 1)
                for i in range(num_orders):
                    order_price = current_price + (price_step * i)
                    order_price = self.adjust_price(symbol, order_price, category)
                    order_qty = usdt_per_order / order_price if order_price > 0 else 0
                    order_qty = self.adjust_quantity(symbol, order_qty, category)
                    orders.append(
                        {
                            "level": i + 1,
                            "price": order_price,
                            "qty": order_qty,
                            "notional": round(order_price * order_qty, 4),
                        }
                    )
            avg_price = (
                sum(o["price"] * o["qty"] for o in orders)
                / sum(o["qty"] for o in orders)
                if orders
                else 0
            )
            return {
                "symbol": symbol,
                "strategy": "DCA",
                "side": side,
                "current_price": current_price,
                "total_usdt": total_usdt,
                "num_orders": num_orders,
                "avg_entry_price": round(avg_price, 8),
                "price_range_pct": price_range_pct,
                "orders": orders,
                "note": "Use batch_orders or place orders individually. Review before executing.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_grid_plan(
        self,
        symbol: str,
        upper_price: float,
        lower_price: float,
        num_grids: int = 10,
        total_usdt: float = 100.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate a grid trading plan with buy/sell levels."""
        try:
            ticker = self.get_ticker(symbol, category)
            current_price = float(ticker.get("lastPrice", 0))
            if current_price <= 0:
                return {"status": "error", "msg": "Could not get current price"}
            if upper_price <= lower_price:
                return {"status": "error", "msg": "upper_price must be > lower_price"}
            grid_spacing = (upper_price - lower_price) / num_grids
            usdt_per_grid = total_usdt / num_grids
            grid_levels = []
            for i in range(num_grids + 1):
                level_price = lower_price + (grid_spacing * i)
                level_price = self.adjust_price(symbol, level_price, category)
                qty = usdt_per_grid / level_price if level_price > 0 else 0
                qty = self.adjust_quantity(symbol, qty, category)
                side = "Buy" if level_price < current_price else "Sell"
                grid_levels.append(
                    {
                        "level": i + 1,
                        "price": level_price,
                        "side": side,
                        "qty": qty,
                        "notional": round(level_price * qty, 4),
                    }
                )
            profit_per_grid = grid_spacing * (usdt_per_grid / current_price)
            return {
                "symbol": symbol,
                "strategy": "GRID",
                "current_price": current_price,
                "upper_price": upper_price,
                "lower_price": lower_price,
                "num_grids": num_grids,
                "grid_spacing": round(grid_spacing, 8),
                "grid_spacing_pct": round((grid_spacing / current_price) * 100, 4),
                "profit_per_grid_est": round(profit_per_grid, 6),
                "total_usdt": total_usdt,
                "grid_levels": grid_levels,
                "note": "Place limit orders at each level. Monitor and replace filled orders.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_scalp_momentum(
        self,
        symbol: str,
        risk_usdt: float = 10.0,
        rr_ratio: float = 2.0,
        atr_sl_mult: float = 1.5,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate a momentum-based scalp setup with ATR-based SL/TP."""
        try:
            analysis = self.get_trend_analysis(
                symbol, category=category, interval="15", lookback_periods=100
            )
            if analysis.get("status") == "error":
                return analysis
            momentum = self.get_market_momentum(symbol, category=category)
            ob = self.get_l2_orderbook_analysis(symbol, depth=25, category=category)
            current_price = analysis["current_price"]
            atr = analysis["indicators"]["atr"]
            rsi = analysis["indicators"]["rsi"]
            trend = analysis["trend"]
            score = analysis["score"]
            if abs(score) < 20:
                return {
                    "symbol": symbol,
                    "strategy": "SCALP_MOMENTUM",
                    "signal": "NO_TRADE",
                    "reason": f"Score too low ({score}), no clear momentum",
                    "trend": trend,
                    "rsi": rsi,
                }
            side = "Buy" if score > 0 else "Sell"
            sl_distance = atr * atr_sl_mult
            tp_distance = sl_distance * rr_ratio
            if side == "Buy":
                sl_price = current_price - sl_distance
                tp_price = current_price + tp_distance
            else:
                sl_price = current_price + sl_distance
                tp_price = current_price - tp_distance
            sl_price = self.adjust_price(symbol, sl_price, category)
            tp_price = self.adjust_price(symbol, tp_price, category)
            qty = self.calculate_position_size(
                symbol, current_price, sl_price, risk_usdt, category
            )
            be = self.calculate_breakeven(current_price, qty, side)
            return {
                "symbol": symbol,
                "strategy": "SCALP_MOMENTUM",
                "signal": side.upper(),
                "side": side,
                "entry_price": current_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "qty": qty,
                "risk_usdt": risk_usdt,
                "risk_reward": rr_ratio,
                "breakeven": be["breakeven_price"],
                "price_to_breakeven_pct": be["price_move_pct"],
                "trend": trend,
                "score": score,
                "rsi": rsi,
                "atr": atr,
                "momentum": momentum.get("signal", "N/A"),
                "ob_pressure": ob.get("pressure", "N/A")
                if isinstance(ob, dict)
                else "N/A",
                "ob_imbalance": ob.get("imbalance", 0) if isinstance(ob, dict) else 0,
                "note": "Review before executing. Use place_order action to enter.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_mean_reversion(
        self,
        symbol: str,
        risk_usdt: float = 10.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Mean reversion strategy using Bollinger Bands and RSI extremes."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=200, category=category
            )
            if not klines or len(klines) < bb_period + 10:
                return {"status": "error", "msg": "Insufficient kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            current_price = closes[-1]
            bb = self.calculate_bollinger_bands(
                closes, period=bb_period, std_dev=bb_std
            )
            rsi = self.calculate_rsi(closes, period=14)
            bb_width = (
                (bb["upper"] - bb["lower"]) / bb["middle"] * 100
                if bb["middle"] > 0
                else 0
            )
            bb_position = (
                (current_price - bb["lower"]) / (bb["upper"] - bb["lower"])
                if (bb["upper"] - bb["lower"]) > 0
                else 0.5
            )
            signal = "NO_TRADE"
            side = None
            sl_price = None
            tp_price = None
            if current_price <= bb["lower"] and rsi <= rsi_oversold:
                signal = "BUY_REVERSAL"
                side = "Buy"
                sl_price = current_price * 0.985
                tp_price = bb["middle"]
            elif current_price >= bb["upper"] and rsi >= rsi_overbought:
                signal = "SELL_REVERSAL"
                side = "Sell"
                sl_price = current_price * 1.015
                tp_price = bb["middle"]
            result = {
                "symbol": symbol,
                "strategy": "MEAN_REVERSION",
                "signal": signal,
                "current_price": current_price,
                "bb_upper": bb["upper"],
                "bb_middle": bb["middle"],
                "bb_lower": bb["lower"],
                "bb_width_pct": round(bb_width, 4),
                "bb_position": round(bb_position, 4),
                "rsi": round(rsi, 2),
                "timestamp": time.time(),
            }
            if side and sl_price and tp_price:
                sl_price = self.adjust_price(symbol, sl_price, category)
                tp_price = self.adjust_price(symbol, tp_price, category)
                qty = self.calculate_position_size(
                    symbol, current_price, sl_price, risk_usdt, category
                )
                be = self.calculate_breakeven(current_price, qty, side)
                result.update(
                    {
                        "side": side,
                        "entry_price": current_price,
                        "stop_loss": sl_price,
                        "take_profit": tp_price,
                        "qty": qty,
                        "risk_usdt": risk_usdt,
                        "breakeven": be["breakeven_price"],
                        "price_to_breakeven_pct": be["price_move_pct"],
                        "note": "Review before executing. Use place_order action to enter.",
                    }
                )
            return result
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_funding_arb(
        self,
        min_rate: float = 0.0005,
        category: Category = Category.LINEAR,
        top_n: int = 10,
    ) -> dict:
        """Find funding rate arbitrage opportunities across all perpetual contracts."""
        try:
            tickers = self.get_24hr_ticker(category=category)
            opportunities = []
            for t in tickers:
                fund_rate = t.get("funding_rate", 0)
                if abs(fund_rate) >= min_rate:
                    symbol = t["symbol"]
                    volume = t.get("volume_24h", 0)
                    price = t.get("last_price", 0)
                    annual_rate = fund_rate * 3 * 365 * 100
                    direction = "SHORT" if fund_rate > 0 else "LONG"
                    opportunities.append(
                        {
                            "symbol": symbol,
                            "funding_rate": fund_rate,
                            "annual_rate_pct": round(annual_rate, 2),
                            "direction": direction,
                            "last_price": price,
                            "volume_24h": volume,
                            "turnover_24h": t.get("turnover_24h", 0),
                        }
                    )
            opportunities.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)
            return {
                "strategy": "FUNDING_ARB",
                "min_rate_filter": min_rate,
                "total_opportunities": len(opportunities),
                "top_opportunities": opportunities[:top_n],
                "note": "Short when funding is positive (longs pay shorts), long when negative.",
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_twap(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        duration_minutes: int = 30,
        num_slices: int = 10,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Generate a TWAP (Time-Weighted Average Price) execution plan."""
        try:
            ticker = self.get_ticker(symbol, category)
            current_price = float(ticker.get("lastPrice", 0))
            slice_qty = total_qty / num_slices
            slice_qty = self.adjust_quantity(symbol, slice_qty, category)
            interval_seconds = (duration_minutes * 60) / num_slices
            slices = []
            for i in range(num_slices):
                exec_time = i * interval_seconds
                slices.append(
                    {
                        "slice": i + 1,
                        "qty": slice_qty,
                        "execute_at_seconds": round(exec_time, 1),
                        "execute_at_minutes": round(exec_time / 60, 2),
                    }
                )
            return {
                "symbol": symbol,
                "strategy": "TWAP",
                "side": side,
                "current_price": current_price,
                "total_qty": total_qty,
                "slice_qty": slice_qty,
                "num_slices": num_slices,
                "duration_minutes": duration_minutes,
                "interval_seconds": round(interval_seconds, 1),
                "est_notional": round(current_price * total_qty, 4),
                "slices": slices,
                "note": "Execute market orders at each time interval. Use place_order with Market type.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_breakout(
        self,
        symbol: str,
        risk_usdt: float = 10.0,
        lookback: int = 50,
        atr_mult: float = 0.5,
        rr_ratio: float = 3.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Detect breakout setups using Donchian channels, volume, and ADX."""
        try:
            klines = self.get_klines(
                symbol, interval="60", limit=lookback + 20, category=category
            )
            if not klines or len(klines) < lookback:
                return {"status": "error", "msg": "Insufficient data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            vols = [float(k[5]) for k in klines]
            current_price = closes[-1]
            donchian = self.calculate_donchian_channels(highs, lows, period=lookback)
            ohlcv = [
                {"high": h, "low": l, "close": c}
                for h, l, c in zip(highs, lows, closes)
            ]
            atr = self.calculate_atr(ohlcv, period=14)
            adx = self.calculate_adx(highs, lows, closes, period=14)
            vol_avg = (
                sum(vols[-20:]) / 20
                if len(vols) >= 20
                else sum(vols) / max(len(vols), 1)
            )
            vol_ratio = vols[-1] / vol_avg if vol_avg > 0 else 1.0
            upper = donchian["upper"]
            lower = donchian["lower"]
            range_size = upper - lower
            position_in_range = (
                (current_price - lower) / range_size if range_size > 0 else 0.5
            )
            signal = "NO_TRADE"
            side = None
            sl_price = None
            tp_price = None
            if current_price >= upper * 0.998 and vol_ratio > 1.2 and adx > 20:
                signal = "BULLISH_BREAKOUT"
                side = "Buy"
                sl_price = upper - (atr * atr_mult)
                tp_distance = abs(current_price - sl_price) * rr_ratio
                tp_price = current_price + tp_distance
            elif current_price <= lower * 1.002 and vol_ratio > 1.2 and adx > 20:
                signal = "BEARISH_BREAKOUT"
                side = "Sell"
                sl_price = lower + (atr * atr_mult)
                tp_distance = abs(sl_price - current_price) * rr_ratio
                tp_price = current_price - tp_distance
            result = {
                "symbol": symbol,
                "strategy": "BREAKOUT",
                "signal": signal,
                "current_price": current_price,
                "donchian_upper": upper,
                "donchian_lower": lower,
                "position_in_range": round(position_in_range, 4),
                "atr": round(atr, 8),
                "adx": adx,
                "vol_ratio": round(vol_ratio, 2),
                "timestamp": time.time(),
            }
            if side and sl_price and tp_price:
                sl_price = self.adjust_price(symbol, sl_price, category)
                tp_price = self.adjust_price(symbol, tp_price, category)
                qty = self.calculate_position_size(
                    symbol, current_price, sl_price, risk_usdt, category
                )
                be = self.calculate_breakeven(current_price, qty, side)
                result.update(
                    {
                        "side": side,
                        "entry_price": current_price,
                        "stop_loss": sl_price,
                        "take_profit": tp_price,
                        "qty": qty,
                        "risk_reward": rr_ratio,
                        "breakeven": be["breakeven_price"],
                        "price_to_breakeven_pct": be["price_move_pct"],
                        "note": "Review before executing. Use place_order action to enter.",
                    }
                )
            return result
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_portfolio_summary(
        self,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Get comprehensive portfolio summary with PnL, risk metrics, and position details."""
        try:
            wallet = self.get_wallet_balance(account_type="UNIFIED")
            positions = self.get_positions(category=category)
            coins = (
                wallet.get("list", [{}])[0].get("coin", [])
                if wallet.get("list")
                else []
            )
            total_equity = (
                float(wallet.get("list", [{}])[0].get("totalEquity", 0))
                if wallet.get("list")
                else 0
            )
            available = (
                float(wallet.get("list", [{}])[0].get("totalAvailableBalance", 0))
                if wallet.get("list")
                else 0
            )
            used_margin = (
                float(wallet.get("list", [{}])[0].get("totalInitialMargin", 0))
                if wallet.get("list")
                else 0
            )
            unrealized_pnl = (
                float(wallet.get("list", [{}])[0].get("totalPerpUPL", 0))
                if wallet.get("list")
                else 0
            )
            active_positions = []
            total_pos_value = 0
            for pos in positions:
                size = float(pos.get("size", 0))
                if size == 0:
                    continue
                entry = float(pos.get("avgPrice", 0))
                mark = float(pos.get("markPrice", 0))
                pos_value = float(pos.get("positionValue", 0))
                upl = float(pos.get("unrealisedPnl", 0))
                leverage = float(pos.get("leverage", 1))
                liq_price = float(pos.get("liqPrice", 0))
                total_pos_value += pos_value
                active_positions.append(
                    {
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side"),
                        "size": size,
                        "entry_price": entry,
                        "mark_price": mark,
                        "position_value": round(pos_value, 4),
                        "unrealized_pnl": round(upl, 4),
                        "pnl_pct": round((upl / (entry * size)) * 100, 4)
                        if entry * size > 0
                        else 0,
                        "leverage": leverage,
                        "liq_price": liq_price,
                        "sl": pos.get("stopLoss", "0"),
                        "tp": pos.get("takeProfit", "0"),
                        "trailing_stop": pos.get("trailingStop", "0"),
                    }
                )
            margin_usage_pct = (
                (used_margin / total_equity) * 100 if total_equity > 0 else 0
            )
            holdings = []
            for c in coins:
                bal = float(c.get("walletBalance", 0))
                if bal > 0:
                    holdings.append(
                        {
                            "coin": c.get("coin"),
                            "balance": bal,
                            "usd_value": float(c.get("usdValue", 0)),
                            "unrealized_pnl": float(c.get("unrealisedPnl", 0)),
                        }
                    )
            return {
                "total_equity": round(total_equity, 4),
                "available_balance": round(available, 4),
                "used_margin": round(used_margin, 4),
                "margin_usage_pct": round(margin_usage_pct, 2),
                "unrealized_pnl": round(unrealized_pnl, 4),
                "total_position_value": round(total_pos_value, 4),
                "active_positions_count": len(active_positions),
                "active_positions": active_positions,
                "holdings": holdings,
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def macro_smart_entry(
        self,
        symbol: str,
        side: str,
        risk_usdt: float = 10.0,
        category: Category = Category.LINEAR,
    ) -> dict:
        """Full smart entry analysis combining trend, momentum, orderbook, and risk."""
        try:
            trend = self.get_trend_analysis(
                symbol, category=category, interval="60", lookback_periods=200
            )
            momentum = self.get_market_momentum(symbol, category=category)
            ob = self.get_l2_orderbook_analysis(symbol, depth=50, category=category)
            health = self.get_market_health(symbol, category=category)
            if trend.get("status") == "error":
                return trend
            current_price = trend["current_price"]
            atr = trend["indicators"]["atr"]
            rsi = trend["indicators"]["rsi"]
            bb = trend["indicators"]["bb"]
            score = trend["score"]
            confidence = min(100, abs(score) + (health.get("health_score", 50) / 2))
            if side.lower() == "buy":
                sl_price = current_price - (atr * 2)
                tp_price = current_price + (atr * 4)
            else:
                sl_price = current_price + (atr * 2)
                tp_price = current_price - (atr * 4)
            sl_price = self.adjust_price(symbol, sl_price, category)
            tp_price = self.adjust_price(symbol, tp_price, category)
            qty = self.calculate_position_size(
                symbol, current_price, sl_price, risk_usdt, category
            )
            be = self.calculate_breakeven_with_fees(
                symbol, current_price, qty, side, category
            )
            warnings = []
            if rsi > 75 and side.lower() == "buy":
                warnings.append("RSI overbought - risky long entry")
            if rsi < 25 and side.lower() == "sell":
                warnings.append("RSI oversold - risky short entry")
            if health.get("health_score", 100) < 40:
                warnings.append("Low market health - wide spreads or thin liquidity")
            if abs(ob.get("imbalance", 0)) > 0.3:
                ob_side = "buy" if ob["imbalance"] > 0 else "sell"
                if ob_side != side.lower():
                    warnings.append(f"Orderbook pressure against your {side} direction")
            return {
                "symbol": symbol,
                "strategy": "SMART_ENTRY",
                "side": side,
                "entry_price": current_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "qty": qty,
                "risk_usdt": risk_usdt,
                "confidence": round(confidence, 1),
                "trend": trend["trend"],
                "trend_score": score,
                "rsi": rsi,
                "atr": atr,
                "bb_position": round(
                    (current_price - bb["lower"])
                    / max(bb["upper"] - bb["lower"], 1e-9),
                    4,
                ),
                "momentum_signal": momentum.get("signal", "N/A"),
                "ob_imbalance": ob.get("imbalance", 0),
                "ob_pressure": ob.get("pressure", "N/A"),
                "health_score": health.get("health_score", 0),
                "breakeven_taker": be.get("taker_breakeven", {}).get("breakeven_price"),
                "breakeven_maker": be.get("maker_breakeven", {}).get("breakeven_price"),
                "warnings": warnings,
                "mtf_alignment": trend.get("mtf_alignment", "NOT_CHECKED"),
                "action_advice": trend.get("action_advice", "WAIT"),
                "timestamp": time.time(),
                "note": "Review warnings before executing. Use place_order action to enter.",
            }
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # FEE AWARENESS & PRECISION HANDLING
    # ══════════════════════════════════════════════════════════
    def get_instrument_precision(self, symbol: str, category: str = "linear") -> dict:
        """Get full precision info for a symbol: tick size, lot step, min qty, min notional, fee rates."""
        info = self._fetch_instrument(symbol, category)
        lot = info.lot_size
        pf = info.price_flt

        qty_precision = (
            max(0, -int(math.floor(math.log10(lot.qty_step))))
            if lot.qty_step > 0
            else 0
        )
        price_precision = (
            max(0, -int(math.floor(math.log10(pf.tick_size))))
            if pf.tick_size > 0
            else 0
        )

        try:
            fee_data = self.get_fee_rate(symbol=symbol, category=category)
            fee_list = fee_data.get("list", [{}])
            taker_fee = _safe_float(fee_list[0].get("takerFeeRate", 0))
            maker_fee = _safe_float(fee_list[0].get("makerFeeRate", 0))
        except Exception:
            taker_fee = 0.0006
            maker_fee = 0.0002

        return {
            "status": "ok",
            "symbol": symbol,
            "category": category,
            "instrument_status": info.status,
            "price": {
                "tick_size": pf.tick_size,
                "min_price": pf.min_price,
                "max_price": pf.max_price,
                "precision_decimals": price_precision,
            },
            "quantity": {
                "qty_step": lot.qty_step,
                "min_order_qty": lot.min_order_qty,
                "max_order_qty": lot.max_order_qty,
                "min_notional": lot.min_notional,
                "precision_decimals": qty_precision,
            },
            "fees": {
                "taker_fee_rate": taker_fee,
                "maker_fee_rate": maker_fee,
                "taker_fee_pct": round(taker_fee * 100, 4),
                "maker_fee_pct": round(maker_fee * 100, 4),
                "round_trip_taker_pct": round(taker_fee * 2 * 100, 4),
                "round_trip_maker_pct": round(maker_fee * 2 * 100, 4),
            },
        }

    def validate_order_params(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float = None,
        order_type: str = "Limit",
        category: str = "linear",
    ) -> dict:
        """Pre-validate order parameters against instrument filters before placing."""
        info = self._fetch_instrument(symbol, category)
        lot = info.lot_size
        pf = info.price_flt
        errors = []
        warnings = []

        adj_qty = lot.adjust(qty)
        if qty != adj_qty:
            warnings.append(f"qty {qty} adjusted to {adj_qty} (step={lot.qty_step})")
        if adj_qty < lot.min_order_qty:
            errors.append(f"qty {adj_qty} below min {lot.min_order_qty}")
        if adj_qty > lot.max_order_qty:
            errors.append(f"qty {adj_qty} exceeds max {lot.max_order_qty}")

        adj_price = None
        if price is not None:
            adj_price = pf.adjust(price)
            if price != adj_price:
                warnings.append(
                    f"price {price} adjusted to {adj_price} (tick={pf.tick_size})"
                )
            if adj_price < pf.min_price and pf.min_price > 0:
                errors.append(f"price {adj_price} below min {pf.min_price}")
            if adj_price > pf.max_price:
                errors.append(f"price {adj_price} exceeds max {pf.max_price}")

            notional = adj_qty * adj_price
            if lot.min_notional > 0 and notional < lot.min_notional:
                errors.append(f"notional {notional:.4f} below min {lot.min_notional}")
                min_qty_for_notional = (
                    lot.min_notional / adj_price if adj_price > 0 else 0
                )
                warnings.append(
                    f"min qty for notional at this price: {lot.adjust(min_qty_for_notional)}"
                )

        try:
            fee_data = self.get_fee_rate(symbol=symbol, category=category)
            taker_fee = _safe_float(
                fee_data.get("list", [{}])[0].get("takerFeeRate", 0.0006)
            )
            maker_fee = _safe_float(
                fee_data.get("list", [{}])[0].get("makerFeeRate", 0.0002)
            )
        except Exception:
            taker_fee = 0.0006
            maker_fee = 0.0002

        ref_price = adj_price if adj_price else 0
        entry_fee_taker = adj_qty * ref_price * taker_fee if ref_price > 0 else 0
        entry_fee_maker = adj_qty * ref_price * maker_fee if ref_price > 0 else 0

        return {
            "status": "error" if errors else "ok",
            "valid": len(errors) == 0,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "original_qty": qty,
            "adjusted_qty": adj_qty,
            "original_price": price,
            "adjusted_price": adj_price,
            "notional": round(adj_qty * ref_price, 4) if ref_price > 0 else None,
            "est_entry_fee_taker": round(entry_fee_taker, 6),
            "est_entry_fee_maker": round(entry_fee_maker, 6),
            "errors": errors,
            "warnings": warnings,
            "instrument": {
                "tick_size": pf.tick_size,
                "qty_step": lot.qty_step,
                "min_order_qty": lot.min_order_qty,
                "min_notional": lot.min_notional,
            },
        }

    def calculate_fee_adjusted_targets(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        tp_pct: float = None,
        sl_pct: float = None,
        category: str = "linear",
        order_type: str = "taker",
    ) -> dict:
        """Calculate TP/SL targets that account for trading fees to ensure real profitability.

        Returns adjusted targets where TP clears all fees and SL accounts for fee drag.
        """
        info = self._fetch_instrument(symbol, category)
        pf = info.price_flt

        try:
            fee_data = self.get_fee_rate(symbol=symbol, category=category)
            fee_list = fee_data.get("list", [{}])
            taker_fee = _safe_float(fee_list[0].get("takerFeeRate", 0.0006))
            maker_fee = _safe_float(fee_list[0].get("makerFeeRate", 0.0002))
        except Exception:
            taker_fee = 0.0006
            maker_fee = 0.0002

        fee_rate = taker_fee if order_type == "taker" else maker_fee
        round_trip_fee_pct = fee_rate * 2

        notional = entry_price * qty
        entry_fee = notional * fee_rate

        if side == "Buy":
            breakeven_price = entry_price * (1 + round_trip_fee_pct)
            if tp_pct:
                raw_tp = entry_price * (1 + tp_pct)
                fee_adj_tp = entry_price * (1 + tp_pct + round_trip_fee_pct)
            else:
                raw_tp = None
                fee_adj_tp = None
            if sl_pct:
                raw_sl = entry_price * (1 - sl_pct)
                actual_loss_at_sl = (
                    (entry_price - raw_sl) * qty + (raw_sl * qty * fee_rate) + entry_fee
                )
            else:
                raw_sl = None
                actual_loss_at_sl = None
        else:
            breakeven_price = entry_price * (1 - round_trip_fee_pct)
            if tp_pct:
                raw_tp = entry_price * (1 - tp_pct)
                fee_adj_tp = entry_price * (1 - tp_pct - round_trip_fee_pct)
            else:
                raw_tp = None
                fee_adj_tp = None
            if sl_pct:
                raw_sl = entry_price * (1 + sl_pct)
                actual_loss_at_sl = (
                    (raw_sl - entry_price) * qty + (raw_sl * qty * fee_rate) + entry_fee
                )
            else:
                raw_sl = None
                actual_loss_at_sl = None

        breakeven_price = pf.adjust(breakeven_price)
        if fee_adj_tp is not None:
            fee_adj_tp = pf.adjust(fee_adj_tp)
        if raw_tp is not None:
            raw_tp = pf.adjust(raw_tp)
        if raw_sl is not None:
            raw_sl = pf.adjust(raw_sl)

        net_profit_at_tp = None
        if fee_adj_tp is not None:
            if side == "Buy":
                net_profit_at_tp = (
                    (fee_adj_tp - entry_price) * qty
                    - entry_fee
                    - (fee_adj_tp * qty * fee_rate)
                )
            else:
                net_profit_at_tp = (
                    (entry_price - fee_adj_tp) * qty
                    - entry_fee
                    - (fee_adj_tp * qty * fee_rate)
                )

        result = {
            "status": "ok",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "qty": qty,
            "notional": round(notional, 4),
            "fee_type": order_type,
            "fee_rate": fee_rate,
            "entry_fee": round(entry_fee, 6),
            "round_trip_fee_pct": round(round_trip_fee_pct * 100, 4),
            "breakeven_price": breakeven_price,
            "breakeven_distance_pct": round(
                abs(breakeven_price - entry_price) / entry_price * 100, 4
            ),
        }
        if raw_tp is not None:
            result["raw_tp"] = raw_tp
            result["fee_adjusted_tp"] = fee_adj_tp
            result["tp_shift_pct"] = round(
                abs(fee_adj_tp - raw_tp) / entry_price * 100, 4
            )
            result["net_profit_at_adj_tp"] = (
                round(net_profit_at_tp, 6) if net_profit_at_tp else None
            )
        if raw_sl is not None:
            result["raw_sl"] = raw_sl
            result["actual_loss_at_sl"] = (
                round(actual_loss_at_sl, 4) if actual_loss_at_sl else None
            )
        result["tick_size"] = pf.tick_size
        return result

    def get_min_order_value(self, symbol: str, category: str = "linear") -> dict:
        """Get the minimum order value (qty * price) for a symbol with current price context."""
        info = self._fetch_instrument(symbol, category)
        lot = info.lot_size
        pf = info.price_flt

        try:
            ticker = self.get_ticker(symbol=symbol, category=category)
            last_price = _safe_float(
                ticker.get("result", {}).get("list", [{}])[0].get("lastPrice", 0)
            )
        except Exception:
            last_price = 0

        min_value_from_qty = lot.min_order_qty * last_price if last_price > 0 else 0
        min_value_from_notional = lot.min_notional

        effective_min = max(min_value_from_qty, min_value_from_notional)
        min_qty_for_notional = (
            (lot.min_notional / last_price)
            if (last_price > 0 and lot.min_notional > 0)
            else 0
        )
        if min_qty_for_notional > 0:
            min_qty_for_notional = lot.adjust(
                max(min_qty_for_notional, lot.min_order_qty)
            )

        return {
            "status": "ok",
            "symbol": symbol,
            "last_price": last_price,
            "min_order_qty": lot.min_order_qty,
            "qty_step": lot.qty_step,
            "min_notional": lot.min_notional,
            "tick_size": pf.tick_size,
            "min_value_from_qty": round(min_value_from_qty, 4),
            "min_value_from_notional": lot.min_notional,
            "effective_min_order_value": round(effective_min, 4),
            "suggested_min_qty": min_qty_for_notional
            if min_qty_for_notional > 0
            else lot.min_order_qty,
        }

    # ══════════════════════════════════════════════════════════
    # ADVANCED POSITION MANAGEMENT
    # ══════════════════════════════════════════════════════════
    def get_position_detail(self, symbol: str, category: str = "linear") -> dict:
        """Get detailed position info with unrealized PnL, liquidation price, margin, funding costs."""
        positions = self.get_positions(category=category, symbol=symbol)
        if not positions:
            return {"status": "no_position", "symbol": symbol}
        pos = positions[0] if isinstance(positions, list) else positions
        size = _safe_float(pos.get("size", 0))
        if size == 0:
            return {"status": "no_position", "symbol": symbol}

        entry = _safe_float(pos.get("avgPrice", 0))
        mark = _safe_float(pos.get("markPrice", 0))
        liq = _safe_float(pos.get("liqPrice", 0))
        leverage = _safe_float(pos.get("leverage", 1))
        side_str = pos.get("side", "None")
        unrealised = _safe_float(pos.get("unrealisedPnl", 0))
        cum_realised = _safe_float(pos.get("cumRealisedPnl", 0))
        margin = _safe_float(pos.get("positionIM", 0))
        tp = _safe_float(pos.get("takeProfit", 0))
        sl = _safe_float(pos.get("stopLoss", 0))
        trailing = _safe_float(pos.get("trailingStop", 0))

        pnl_pct = (unrealised / margin * 100) if margin > 0 else 0
        dist_to_liq = abs(mark - liq) / mark * 100 if mark > 0 and liq > 0 else None

        try:
            fee_data = self.get_fee_rate(symbol=symbol, category=category)
            taker_fee = _safe_float(
                fee_data.get("list", [{}])[0].get("takerFeeRate", 0.0006)
            )
        except Exception:
            taker_fee = 0.0006
        close_fee_cost = size * mark * taker_fee

        return {
            "status": "ok",
            "symbol": symbol,
            "side": side_str,
            "size": size,
            "entry_price": entry,
            "mark_price": mark,
            "liq_price": liq,
            "leverage": leverage,
            "unrealised_pnl": round(unrealised, 4),
            "unrealised_pnl_pct": round(pnl_pct, 2),
            "cum_realised_pnl": round(cum_realised, 4),
            "position_margin": round(margin, 4),
            "take_profit": tp if tp > 0 else None,
            "stop_loss": sl if sl > 0 else None,
            "trailing_stop": trailing if trailing > 0 else None,
            "distance_to_liq_pct": round(dist_to_liq, 2) if dist_to_liq else None,
            "est_close_fee": round(close_fee_cost, 4),
            "net_pnl_after_close_fee": round(unrealised - close_fee_cost, 4),
            "position_value": round(size * mark, 2),
            "created_time": pos.get("createdTime"),
            "updated_time": pos.get("updatedTime"),
        }

    def scale_position(
        self,
        symbol: str,
        side: str,
        scale_pct: float,
        category: str = "linear",
        reduce: bool = False,
    ) -> dict:
        """Scale into or out of a position by a percentage of current size.

        Args:
            symbol: Trading symbol
            side: Buy or Sell
            scale_pct: Percentage to scale (e.g. 25.0 = 25%)
            category: Product category
            reduce: If True, reduce position; if False, add to position
        """
        cat_enum = Category(category)
        positions = self.get_positions(category=cat_enum, symbol=symbol)
        if not positions:
            return {"status": "error", "msg": "No open position to scale"}
        pos = positions[0] if isinstance(positions, list) else positions
        current_size = _safe_float(pos.get("size", 0))
        if current_size == 0:
            return {"status": "error", "msg": "Position size is zero"}

        scale_qty = current_size * (scale_pct / 100.0)
        scale_qty = self.adjust_quantity(symbol, scale_qty, category)

        if reduce:
            opp_side = "Sell" if side == "Buy" else "Buy"
            return self.place_order(
                symbol=symbol,
                side=OrderSide(opp_side),
                qty=scale_qty,
                order_type=OrderType.MARKET,
                category=cat_enum,
                reduce_only=True,
            )
        else:
            return self.place_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=scale_qty,
                order_type=OrderType.MARKET,
                category=cat_enum,
            )

    def close_position(
        self, symbol: str, category: str = "linear", qty_pct: float = 1.0
    ) -> dict:
        """Close a percentage of a position at market price."""
        cat_enum = Category(category)
        positions = self.get_positions(category=cat_enum, symbol=symbol)
        if not positions:
            return {"status": "error", "msg": "No open position"}
        pos = positions[0] if isinstance(positions, list) else positions
        size = _safe_float(pos.get("size", 0))
        if size == 0:
            return {"status": "error", "msg": "Position size is zero"}

        close_qty = size * qty_pct
        close_qty = self.adjust_quantity(symbol, close_qty, category)
        if close_qty == 0:
            return {"status": "error", "msg": "Calculated close quantity is zero"}

        side_str = pos.get("side", "")
        close_side = "Sell" if side_str == "Buy" else "Buy"

        return self.place_order(
            symbol=symbol,
            side=OrderSide(close_side),
            qty=close_qty,
            order_type=OrderType.MARKET,
            category=cat_enum,
            reduce_only=True,
        )

    def flip_position(
        self, symbol: str, category: str = "linear", scale: float = 1.0
    ) -> dict:
        """Close current position and open opposite direction. scale=1.0 means same size."""
        cat_enum = Category(category)
        positions = self.get_positions(category=cat_enum, symbol=symbol)
        if not positions:
            return {"status": "error", "msg": "No open position to flip"}
        pos = positions[0] if isinstance(positions, list) else positions
        size = _safe_float(pos.get("size", 0))
        if size == 0:
            return {"status": "error", "msg": "Position size is zero"}

        side_str = pos.get("side", "")
        close_side = "Sell" if side_str == "Buy" else "Buy"

        close_result = self.place_order(
            symbol=symbol,
            side=OrderSide(close_side),
            qty=size,
            order_type=OrderType.MARKET,
            category=cat_enum,
            reduce_only=True,
        )

        new_qty = self.adjust_quantity(symbol, size * scale, category)
        open_result = self.place_order(
            symbol=symbol,
            side=OrderSide(close_side),
            qty=new_qty,
            order_type=OrderType.MARKET,
            category=cat_enum,
        )

        return {
            "status": "ok",
            "close_result": close_result,
            "open_result": open_result,
            "old_side": side_str,
            "new_side": close_side,
            "old_size": size,
            "new_size": new_qty,
        }

    def auto_sl_tp(
        self,
        symbol: str,
        category: str = "linear",
        sl_pct: float = None,
        tp_pct: float = None,
        use_atr: bool = False,
        atr_mult: float = 1.5,
    ) -> dict:
        """Automatically set SL/TP on an open position based on percentage or ATR."""
        positions = self.get_positions(category=category, symbol=symbol)
        if not positions:
            return {"status": "error", "msg": "No open position"}
        pos = positions[0] if isinstance(positions, list) else positions
        entry = _safe_float(pos.get("avgPrice", 0))
        side_str = pos.get("side", "")
        if entry == 0 or not side_str:
            return {"status": "error", "msg": "Invalid position data"}

        if use_atr:
            klines = self.get_klines(
                symbol=symbol, interval="60", limit=50, category=category
            )
            if klines:
                klines.reverse()
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                ohlcv_data = [
                    {"high": h, "low": l, "close": c}
                    for h, l, c in zip(highs, lows, closes)
                ]
                atr = self.calculate_atr(ohlcv_data, period=14)
                if atr > 0:
                    if side_str == "Buy":
                        sl_price = entry - (atr * atr_mult)
                        tp_price = entry + (atr * atr_mult * 2)
                    else:
                        sl_price = entry + (atr * atr_mult)
                        tp_price = entry - (atr * atr_mult * 2)
                else:
                    return {"status": "error", "msg": "ATR calculation returned zero"}
            else:
                return {"status": "error", "msg": "Could not fetch kline data for ATR"}
        else:
            sl_frac = sl_pct if sl_pct else self.config.default_stop_loss
            tp_frac = tp_pct if tp_pct else self.config.default_take_profit
            if side_str == "Buy":
                sl_price = entry * (1 - sl_frac)
                tp_price = entry * (1 + tp_frac)
            else:
                sl_price = entry * (1 + sl_frac)
                tp_price = entry * (1 - tp_frac)

        sl_price = self.adjust_price(symbol, sl_price, category)
        tp_price = self.adjust_price(symbol, tp_price, category)

        result = self.set_trading_stop(
            symbol=symbol,
            stop_loss=sl_price,
            take_profit=tp_price,
            category=Category(category),
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "side": side_str,
            "entry": entry,
            "stop_loss_set": sl_price,
            "take_profit_set": tp_price,
            "method": "ATR" if use_atr else "percentage",
            "result": result,
        }

    def move_sl_to_breakeven(
        self, symbol: str, category: str = "linear", offset_pct: float = 0.001
    ) -> dict:
        """Move stop loss to breakeven (entry price + small offset for fees)."""
        positions = self.get_positions(category=category, symbol=symbol)
        if not positions:
            return {"status": "error", "msg": "No open position"}
        pos = positions[0] if isinstance(positions, list) else positions
        entry = _safe_float(pos.get("avgPrice", 0))
        side_str = pos.get("side", "")
        mark = _safe_float(pos.get("markPrice", 0))
        if entry == 0 or not side_str:
            return {"status": "error", "msg": "Invalid position data"}

        if side_str == "Buy":
            be_sl = entry * (1 + offset_pct)
            if mark <= be_sl:
                return {
                    "status": "error",
                    "msg": f"Price {mark} not above breakeven {be_sl}",
                }
        else:
            be_sl = entry * (1 - offset_pct)
            if mark >= be_sl:
                return {
                    "status": "error",
                    "msg": f"Price {mark} not below breakeven {be_sl}",
                }

        be_sl = self.adjust_price(symbol, be_sl, category)
        result = self.set_trading_stop(
            symbol=symbol, stop_loss=be_sl, category=Category(category)
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "side": side_str,
            "entry": entry,
            "breakeven_sl": be_sl,
            "mark_price": mark,
            "result": result,
        }

    def get_all_positions_summary(self, category: str = "linear") -> dict:
        """Get summary of all open positions with aggregated PnL."""
        positions = self.get_positions(category=category)
        if not positions:
            return {
                "status": "ok",
                "count": 0,
                "positions": [],
                "total_unrealised_pnl": 0,
            }

        summaries = []
        total_pnl = 0.0
        total_margin = 0.0
        for pos in positions:
            size = _safe_float(pos.get("size", 0))
            if size == 0:
                continue
            unrealised = _safe_float(pos.get("unrealisedPnl", 0))
            margin = _safe_float(pos.get("positionIM", 0))
            total_pnl += unrealised
            total_margin += margin
            summaries.append(
                {
                    "symbol": pos.get("symbol"),
                    "side": pos.get("side"),
                    "size": size,
                    "entry": _safe_float(pos.get("avgPrice", 0)),
                    "mark": _safe_float(pos.get("markPrice", 0)),
                    "unrealised_pnl": round(unrealised, 4),
                    "leverage": _safe_float(pos.get("leverage", 1)),
                    "liq_price": _safe_float(pos.get("liqPrice", 0)),
                }
            )

        return {
            "status": "ok",
            "count": len(summaries),
            "positions": summaries,
            "total_unrealised_pnl": round(total_pnl, 4),
            "total_margin_used": round(total_margin, 4),
        }

    # ══════════════════════════════════════════════════════════
    # TRADE JOURNALING
    # ══════════════════════════════════════════════════════════
    _trade_journal: List[Dict[str, Any]] = []

    def journal_record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        exit_price: float = None,
        strategy: str = "",
        tags: str = "",
        notes: str = "",
        category: str = "linear",
    ) -> dict:
        """Record a trade entry in the journal.

        Args:
            symbol: Trading symbol
            side: Buy or Sell
            entry_price: Entry price
            qty: Position size
            exit_price: Exit price (None if still open)
            strategy: Strategy name (e.g. 'scalp', 'swing', 'grid')
            tags: Comma-separated tags (e.g. 'breakout,momentum')
            notes: Free-form notes about the trade
            category: Product category
        """
        try:
            fee_data = self.get_fee_rate(symbol=symbol, category=category)
            taker_fee = _safe_float(
                fee_data.get("list", [{}])[0].get("takerFeeRate", 0.0006)
            )
        except Exception:
            taker_fee = 0.0006

        trade = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": time.time(),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "strategy": strategy,
            "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else [],
            "notes": notes,
            "category": category,
            "status": "closed" if exit_price else "open",
            "fee_rate": taker_fee,
        }

        if exit_price:
            notional = qty * entry_price
            if side == "Buy":
                gross_pnl = (exit_price - entry_price) * qty
            else:
                gross_pnl = (entry_price - exit_price) * qty
            entry_fee = notional * taker_fee
            exit_fee = qty * exit_price * taker_fee
            net_pnl = gross_pnl - entry_fee - exit_fee
            roi = (net_pnl / notional) * 100 if notional > 0 else 0
            trade["gross_pnl"] = round(gross_pnl, 4)
            trade["entry_fee"] = round(entry_fee, 4)
            trade["exit_fee"] = round(exit_fee, 4)
            trade["net_pnl"] = round(net_pnl, 4)
            trade["roi_pct"] = round(roi, 2)

        self._trade_journal.append(trade)
        return {"status": "ok", "trade": trade}

    def journal_close_trade(self, trade_id: str, exit_price: float) -> dict:
        """Close an open journal trade by ID with the exit price."""
        for trade in self._trade_journal:
            if trade["id"] == trade_id and trade["status"] == "open":
                trade["exit_price"] = exit_price
                trade["status"] = "closed"
                trade["closed_at"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC", time.gmtime()
                )

                entry_price = trade["entry_price"]
                qty = trade["qty"]
                side = trade["side"]
                fee_rate = trade.get("fee_rate", 0.0006)
                notional = qty * entry_price

                if side == "Buy":
                    gross_pnl = (exit_price - entry_price) * qty
                else:
                    gross_pnl = (entry_price - exit_price) * qty
                entry_fee = notional * fee_rate
                exit_fee = qty * exit_price * fee_rate
                net_pnl = gross_pnl - entry_fee - exit_fee
                roi = (net_pnl / notional) * 100 if notional > 0 else 0

                trade["gross_pnl"] = round(gross_pnl, 4)
                trade["entry_fee"] = round(entry_fee, 4)
                trade["exit_fee"] = round(exit_fee, 4)
                trade["net_pnl"] = round(net_pnl, 4)
                trade["roi_pct"] = round(roi, 2)

                return {"status": "ok", "trade": trade}
        return {
            "status": "error",
            "msg": f"Trade {trade_id} not found or already closed",
        }

    def journal_get_trades(
        self,
        symbol: str = None,
        strategy: str = None,
        status: str = None,
        limit: int = 50,
    ) -> dict:
        """Query journal trades with optional filters."""
        trades = self._trade_journal[:]
        if symbol:
            trades = [t for t in trades if t["symbol"] == symbol]
        if strategy:
            trades = [t for t in trades if t["strategy"] == strategy]
        if status:
            trades = [t for t in trades if t["status"] == status]
        trades = sorted(trades, key=lambda t: t["timestamp"], reverse=True)[:limit]
        return {"status": "ok", "count": len(trades), "trades": trades}

    def journal_performance(self, symbol: str = None, strategy: str = None) -> dict:
        """Calculate performance metrics from journal trades."""
        trades = [t for t in self._trade_journal if t["status"] == "closed"]
        if symbol:
            trades = [t for t in trades if t["symbol"] == symbol]
        if strategy:
            trades = [t for t in trades if t["strategy"] == strategy]

        if not trades:
            return {"status": "ok", "msg": "No closed trades found", "total_trades": 0}

        wins = [t for t in trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
        total_pnl = sum(t.get("net_pnl", 0) for t in trades)
        total_gross = sum(t.get("gross_pnl", 0) for t in trades)
        total_fees = sum(t.get("entry_fee", 0) + t.get("exit_fee", 0) for t in trades)
        avg_win = sum(t.get("net_pnl", 0) for t in wins) / len(wins) if wins else 0
        avg_loss = (
            sum(t.get("net_pnl", 0) for t in losses) / len(losses) if losses else 0
        )
        win_rate = len(wins) / len(trades) * 100 if trades else 0

        largest_win = max((t.get("net_pnl", 0) for t in trades), default=0)
        largest_loss = min((t.get("net_pnl", 0) for t in trades), default=0)

        # Profit factor
        gross_wins = sum(t.get("net_pnl", 0) for t in wins)
        gross_losses = abs(sum(t.get("net_pnl", 0) for t in losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Expectancy
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        # Max drawdown (sequential)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x["timestamp"]):
            cumulative += t.get("net_pnl", 0)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        # Sharpe-like ratio (simplified)
        returns = [t.get("roi_pct", 0) for t in trades]
        avg_return = sum(returns) / len(returns) if returns else 0
        std_return = (
            (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            if len(returns) > 1
            else 0
        )
        sharpe = avg_return / std_return if std_return > 0 else 0

        # By strategy breakdown
        strategies = {}
        for t in trades:
            s = t.get("strategy", "unknown") or "unknown"
            if s not in strategies:
                strategies[s] = {"count": 0, "net_pnl": 0, "wins": 0}
            strategies[s]["count"] += 1
            strategies[s]["net_pnl"] = round(
                strategies[s]["net_pnl"] + t.get("net_pnl", 0), 4
            )
            if t.get("net_pnl", 0) > 0:
                strategies[s]["wins"] += 1
        for s in strategies:
            strategies[s]["win_rate"] = (
                round(strategies[s]["wins"] / strategies[s]["count"] * 100, 1)
                if strategies[s]["count"] > 0
                else 0
            )

        return {
            "status": "ok",
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "total_net_pnl": round(total_pnl, 4),
            "total_gross_pnl": round(total_gross, 4),
            "total_fees_paid": round(total_fees, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "largest_win": round(largest_win, 4),
            "largest_loss": round(largest_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_ratio": round(sharpe, 2),
            "avg_roi_pct": round(avg_return, 2),
            "by_strategy": strategies,
        }

    def journal_export(self) -> dict:
        """Export full trade journal as a list."""
        return {
            "status": "ok",
            "total_trades": len(self._trade_journal),
            "open_trades": len(
                [t for t in self._trade_journal if t["status"] == "open"]
            ),
            "closed_trades": len(
                [t for t in self._trade_journal if t["status"] == "closed"]
            ),
            "journal": self._trade_journal[:],
        }

    def journal_import(self, trades: List[Dict[str, Any]]) -> dict:
        """Import trades into the journal from a list of trade dicts."""
        imported = 0
        for t in trades:
            if "symbol" in t and "side" in t and "entry_price" in t:
                if "id" not in t:
                    t["id"] = str(uuid.uuid4())[:8]
                if "timestamp" not in t:
                    t["timestamp"] = time.time()
                if "status" not in t:
                    t["status"] = "closed" if t.get("exit_price") else "open"
                self._trade_journal.append(t)
                imported += 1
        return {"status": "ok", "imported": imported, "total": len(self._trade_journal)}

    # ══════════════════════════════════════════════════════════
    # DIAGNOSTICS

    # ─────────────────────────────────────────────────────────
    # MISSING V5 ENDPOINTS
    # ─────────────────────────────────────────────────────────
    def batch_place_orders(
        self, orders: list, category: Category = Category.LINEAR
    ) -> dict:
        """Place up to 20 orders in a single batch request."""

        return self.api_request(
            "POST",
            "/v5/order/create-batch",
            json_data={
                "category": category.value,
                "request": orders,
            },
        )

    def set_tpsl_mode(
        self,
        symbol: str,
        tp_sl_mode: str = "Full",
        category: Category = Category.LINEAR,
    ) -> dict:
        """Set TP/SL mode: 'Full' (entire position) or 'Partial'."""

        return self.api_request(
            "POST",
            "/v5/position/set-tpsl-mode",
            json_data={
                "category": category.value,
                "symbol": symbol,
                "tpSlMode": tp_sl_mode,
            },
        )

    def add_reduce_margin(
        self, symbol: str, margin: str, category: Category = Category.LINEAR
    ) -> dict:
        """Add or reduce margin for isolated position. Positive=add, negative=reduce."""

        return self.api_request(
            "POST",
            "/v5/position/add-margin",
            json_data={
                "category": category.value,
                "symbol": symbol,
                "margin": margin,
            },
        )

    def set_auto_add_margin(
        self, symbol: str, auto_add: bool = True, category: Category = Category.LINEAR
    ) -> dict:
        """Toggle auto-add-margin for isolated margin positions."""

        return self.api_request(
            "POST",
            "/v5/position/set-auto-add-margin",
            json_data={
                "category": category.value,
                "symbol": symbol,
                "autoAddMargin": 1 if auto_add else 0,
            },
        )

    def get_executions(
        self,
        symbol: str = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
        start_time: int = None,
        end_time: int = None,
    ) -> list:
        """Get execution records (fills)."""
        params = {"category": category.value, "limit": str(limit)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = str(start_time)
        if end_time:
            params["endTime"] = str(end_time)
        resp = self.api_request("GET", "/v5/execution/list", params=params)
        return resp.get("result", {}).get("list", [])

    def confirm_risk_limit(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        """Confirm pending risk limit change."""

        return self.api_request(
            "POST",
            "/v5/position/confirm-pending-mmr",
            json_data={
                "category": category.value,
                "symbol": symbol,
            },
        )

    def set_collateral_coin(self, coin: str, switch: bool = True) -> dict:
        """Toggle whether a coin is used as collateral in unified margin."""

        return self.api_request(
            "POST",
            "/v5/account/set-collateral-switch",
            json_data={
                "coin": coin,
                "collateralSwitch": "ON" if switch else "OFF",
            },
        )

    def get_premium_index_kline(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 200,
        category: Category = Category.LINEAR,
    ) -> list:
        """Get premium index price kline data."""
        params = {
            "category": category.value,
            "symbol": symbol,
            "interval": interval,
            "limit": str(limit),
        }
        resp = self.api_request(
            "GET", "/v5/market/premium-index-price-kline", params=params
        )
        return resp.get("result", {}).get("list", [])

    def calculate_heikin_ashi(
        self, opens: list, highs: list, lows: list, closes: list
    ) -> dict:
        """Calculate Heikin-Ashi candles from OHLC data."""
        ha_close = [
            (o + h + l + c) / 4 for o, h, l, c in zip(opens, highs, lows, closes)
        ]
        ha_open = [opens[0]]
        for i in range(1, len(opens)):
            ha_open.append((ha_open[-1] + ha_close[i - 1]) / 2)
        ha_high = [max(h, ho, hc) for h, ho, hc in zip(highs, ha_open, ha_close)]
        ha_low = [min(l, ho, hc) for l, ho, hc in zip(lows, ha_open, ha_close)]
        return {
            "ha_open": ha_open[-5:],
            "ha_high": ha_high[-5:],
            "ha_low": ha_low[-5:],
            "ha_close": ha_close[-5:],
            "trend": "bullish" if ha_close[-1] > ha_open[-1] else "bearish",
            "candles": len(ha_close),
        }

    def calculate_renko(self, closes: list, brick_size: float = None) -> dict:
        """Calculate Renko bricks from close prices."""
        if brick_size is None:
            atr_val = self.calculate_atr(closes, closes, closes, 14)
            brick_size = (
                atr_val if isinstance(atr_val, (int, float)) else closes[-1] * 0.01
            )
        bricks = []
        base = closes[0]
        for c in closes[1:]:
            while c >= base + brick_size:
                base += brick_size
                bricks.append({"price": round(base, 8), "direction": "up"})
            while c <= base - brick_size:
                base -= brick_size
                bricks.append({"price": round(base, 8), "direction": "down"})
        up = sum(1 for b in bricks if b["direction"] == "up")
        down = len(bricks) - up
        return {
            "brick_size": brick_size,
            "total_bricks": len(bricks),
            "up_bricks": up,
            "down_bricks": down,
            "last_bricks": bricks[-5:] if bricks else [],
            "trend": "bullish" if up > down else "bearish" if down > up else "neutral",
        }

    def calculate_pivot_points(self, high: float, low: float, close: float) -> dict:
        """Calculate classic pivot points from high/low/close."""
        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)
        r3 = high + 2 * (pivot - low)
        s3 = low - 2 * (high - pivot)
        return {
            "pivot": round(pivot, 8),
            "r1": round(r1, 8),
            "r2": round(r2, 8),
            "r3": round(r3, 8),
            "s1": round(s1, 8),
            "s2": round(s2, 8),
            "s3": round(s3, 8),
        }

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
                "tor_socks_port": self.config.tor_socks_port,
                "tor_control_port": self.config.tor_control_port,
                "tor_control_accessible": self.tor.tor_control_accessible,
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
                "cache_symbols": list(self._instrument_cache.keys()),
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
        # ── Diagnostics ──
        "health_check",
        "connection_health",
        "reset_circuit",
        "renew_tor_circuit",
        "get_server_time",
        # ── Orders ──
        "place_order",
        "amend_order",
        "cancel_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_order_history",
        "batch_orders",
        "iceberg_order",
        "place_trailing_stop_order",
        "calculate_trailing_stop_levels",
        "get_trailing_stop_status",
        "place_conditional_order",
        "batch_amend_orders",
        "batch_cancel_orders",
        # ── Positions & Account ──
        "get_positions",
        "get_wallet_balance",
        "set_leverage",
        "set_trading_stop",
        "get_fee_rate",
        "get_transaction_log",
        "get_trade_history",
        "switch_margin_mode",
        "switch_position_mode",
        "get_account_info",
        "get_api_key_info",
        "get_borrow_history",
        "get_collateral_info",
        "get_coin_greeks",
        "internal_transfer",
        "get_coin_balance",
        # ── Market Data ──
        "get_ticker",
        "get_24hr_ticker",
        "get_orderbook",
        "get_klines",
        "get_recent_trades",
        "get_open_interest",
        "get_liquidations",
        "get_funding_rate",
        "get_funding_rate_history",
        "get_long_short_ratio",
        "get_mark_price",
        "get_index_price",
        "get_price_bands",
        "get_instruments_info",
        "get_risk_limit",
        "set_risk_limit",
        "get_insurance_pool",
        "get_delivery_price",
        "get_historical_volatility",
        # ── L2 Orderbook Analysis ──
        "get_l2_orderbook_analysis",
        # ── Market Intelligence ──
        "get_market_momentum",
        "get_market_health",
        "get_trend_analysis",
        # ── Technical Indicators ──
        "calculate_bollinger_bands",
        "calculate_macd",
        "calculate_stoch_rsi",
        "calculate_cci",
        "calculate_donchian_channels",
        "calculate_adx",
        "calculate_fib_pivots",
        "calculate_vwap",
        "calculate_ichimoku_cloud",
        # ── Risk & PnL ──
        "calculate_kelly_criterion",
        "calculate_trade_pnl",
        "calculate_profit_target",
        "calculate_sl_tp",
        "calculate_position_size",
        "calculate_volatility_adjusted_size",
        "calculate_breakeven",
        "calculate_breakeven_with_fees",
        "calculate_profit_after_fees",
        # ── PnL Reports ──
        "get_pnl_history",
        "get_pnl_report",
        # ── Profitable Macros ──
        "macro_dca_plan",
        "macro_grid_plan",
        "macro_scalp_momentum",
        "macro_mean_reversion",
        "macro_funding_arb",
        "macro_twap",
        "macro_breakout",
        "macro_portfolio_summary",
        "macro_smart_entry",
        "macro_microprofit_scalp",
        # ── Enhanced Volume & L2 Orderbook ──
        "volume_profile",
        "volume_divergence",
        "orderbook_heatmap",
        "orderflow_analysis",
        # ── Comprehensive Trend Analysis ──
        "comprehensive_trend_analysis",
        # ── Fee Awareness & Precision ──
        "get_instrument_precision",
        "validate_order_params",
        "calculate_fee_adjusted_targets",
        "get_min_order_value",
        # ── Advanced Position Management ──
        "get_position_detail",
        "scale_position",
        "close_position",
        "flip_position",
        "auto_sl_tp",
        "move_sl_to_breakeven",
        "get_all_positions_summary",
        # ── Trade Journaling ──
        "journal_record_trade",
        "journal_close_trade",
        "journal_get_trades",
        "journal_performance",
        "journal_export",
        "journal_import",
        # ── Advanced Position Sizing ──
        "adaptive_position_size",
        "fixed_fractional_size",
        "anti_martingale_size",
        "portfolio_heat",
        "max_position",
        # ── Profit-Maximizing Strategies ──
        "momentum_sniper",
        "mean_reversion_scalp",
        "funding_arb_scan",
        "smart_dca",
        "liquidity_sweep",
        # ── 25 Important Functions ──
        "risk_reward_analysis",
        "liquidation_price",
        "drawdown_analysis",
        "support_resistance",
        "market_regime",
        "entry_timing",
        "scale_into",
        "scale_out",
        "hedge_position",
        "multi_timeframe_signals",
        "market_session",
        "whale_detection",
        "fear_greed",
        "compound_growth",
        "trade_checklist",
        "auto_leverage",
        "unrealized_pnl",
        "divergence_signals",
        "optimal_entry_zones",
        "spread_analysis",
        "correlation_analysis",
        "smart_trailing_stop",
        # ── Missing V5 Endpoints ──
        "batch_place_orders",
        "set_tpsl_mode",
        "add_reduce_margin",
        "set_auto_add_margin",
        "get_executions",
        "confirm_risk_limit",
        "set_collateral_coin",
        "get_withdrawable_amount",
        "get_historical_klines",
        "get_premium_index_kline",
        # ── Missing Standalone Indicators ──
        "calculate_rsi",
        "calculate_ema",
        "calculate_atr",
        "calculate_obv",
        "calculate_cvd",
        "calculate_mfi",
        "calculate_cmf",
        "calculate_williams_r",
        "calculate_parabolic_sar",
        "calculate_keltner_channels",
        "calculate_roc",
        "calculate_trix",
        "calculate_ultimate_oscillator",
        "calculate_choppiness_index",
        "calculate_aroon",
        "calculate_dpo",
        "calculate_hma",
        "calculate_zlema",
        "calculate_supertrend",
        "calculate_elder_ray",
        "calculate_vwma",
        "calculate_awesome_oscillator",
        "calculate_accumulation_distribution",
        "calculate_heikin_ashi",
        "calculate_renko",
        "calculate_pivot_points",
        "calculate_position_risk",
        "calculate_compound_growth",
        "calculate_linear_regression",
        "calculate_stddev",
        "calculate_vroc",
        "calculate_kelly_criterion_standalone",
        "calculate_max_position",
        "calculate_bollinger_bandwidth",
    ],
    # ── Order fields ──────────────────────────────────────────
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_type: Optional[Literal["Limit", "Market", "LimitMaker"]] = None,
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
    # ── Market data ───────────────────────────────────────────
    limit: Optional[int] = 25,
    interval: Optional[str] = "1",
    interval_time: Optional[str] = "5min",
    # ── Momentum ──────────────────────────────────────────────
    strong_threshold: Optional[float] = 0.20,
    mild_threshold: Optional[float] = 0.08,
    # ── Risk helpers ──────────────────────────────────────────
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    risk_usdt: Optional[float] = None,
    sl_price: Optional[float] = None,
    fee_rate: Optional[float] = None,
    funding_rate: Optional[float] = None,
    holding_hours: Optional[float] = None,
    holding_periods: Optional[int] = None,
    # ── Batch / Iceberg ───────────────────────────────────────
    orders: Optional[str] = None,
    slices: Optional[int] = 5,
    delay: Optional[float] = None,
    # ── Time Range Filters ─────────────────────────────────────
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    # ── Position management ────────────────────────────────────
    scale_pct: Optional[float] = None,
    reduce: Optional[bool] = False,
    scale: Optional[float] = None,
    use_atr: Optional[bool] = False,
    offset_pct: Optional[float] = None,
    # ── Journal params ─────────────────────────────────────────
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    strategy: Optional[str] = None,
    tags: Optional[str] = None,
    trade_notes: Optional[str] = None,
    trade_id: Optional[str] = None,
    journal_status: Optional[str] = None,
    journal_data: Optional[str] = None,
    # ── Macro params ──────────────────────────────────────────
    total_usdt: Optional[float] = None,
    num_orders: Optional[int] = None,
    num_grids: Optional[int] = None,
    upper_price: Optional[float] = None,
    lower_price: Optional[float] = None,
    rr_ratio: Optional[float] = None,
    atr_mult: Optional[float] = None,
    duration_minutes: Optional[int] = None,
    num_slices: Optional[int] = None,
    min_rate: Optional[float] = None,
    top_n: Optional[int] = None,
    price_range_pct: Optional[float] = None,
    # ── Transfer params ───────────────────────────────────────
    coin: Optional[str] = None,
    amount: Optional[str] = None,
    from_account: Optional[str] = None,
    to_account: Optional[str] = None,
    risk_id: Optional[int] = None,
    currency: Optional[str] = None,
    trigger_price: Optional[float] = None,
    trigger_by: Optional[Literal["LastPrice", "IndexPrice", "MarkPrice"]] = None,
    depth: Optional[int] = None,
    period: Optional[int] = None,
    num_bins: Optional[int] = None,
    # ── New sizing / strategy params ─────────────────────────
    num_levels: Optional[int] = None,
    num_entries: Optional[int] = None,
    num_exits: Optional[int] = None,
    spacing_pct: Optional[float] = None,
    tp_spacing_pct: Optional[float] = None,
    hedge_pct: Optional[float] = None,
    dip_pct: Optional[float] = None,
    symbols: Optional[List[str]] = None,
    risk_fraction: Optional[float] = None,
    consecutive_wins: Optional[int] = None,
    consecutive_losses: Optional[int] = None,
    daily_return_pct: Optional[float] = None,
    days: Optional[int] = None,
    win_rate_pct: Optional[float] = None,
    trades_per_day: Optional[int] = None,
    starting_capital: Optional[float] = None,
    maint_margin_rate: Optional[float] = None,
    account_balance: Optional[float] = None,
    # ── New V5 endpoint params ────────────────────────────────────
    margin: Optional[str] = None,
    auto_add_margin: Optional[bool] = None,
    tp_sl_mode: Optional[Literal["Full", "Partial"]] = None,
    collateral_switch: Optional[bool] = None,
    brick_size: Optional[float] = None,
) -> dict:
    """BYBIT REALM v4.0 – Comprehensive Bybit V5 trading tool with all endpoints, indicators, breakeven logic, L2 analysis, and profitable macros.

    Args:
        action: Operation to perform (see Literal type for all options)
        symbol: Trading symbol (e.g., 'BTCUSDT')
        side: Order side ('Buy' or 'Sell')
        qty: Order quantity
        price: Limit price / entry_price for calculations
        order_type: Order type (Limit, Market, LimitMaker, Stop, StopLimit)
        category: Product category (linear, inverse, spot, option)
        order_id: Existing order ID for amend/cancel
        stop_loss: Stop loss price
        take_profit: Take profit price
        trailing_stop: Trailing stop distance
        reduce_only: Whether the order is reduce-only
        time_in_force: Time in force (GTC, IOC, FOK, PostOnly)
        position_idx: Position index (0=one-way, 1=hedge-buy, 2=hedge-sell)
        client_oid: Client order link ID
        leverage: Leverage for the position
        buy_leverage: Independent buy-side leverage
        sell_leverage: Independent sell-side leverage
        account_type: Account type (UNIFIED, SPOT, etc.)
        limit: Result count for list endpoints
        interval: Kline interval (1,3,5,15,30,60,120,240,360,720,D,W,M)
        interval_time: Open interest / L-S ratio interval (5min, 15min, 30min, 1h, 4h, 1d)
        strong_threshold: Momentum strong-signal cutoff
        mild_threshold: Momentum mild-signal cutoff
        sl_pct: Stop-loss pct / win_rate for Kelly / smooth_k for Stoch RSI
        tp_pct: Take-profit pct / win_loss_ratio for Kelly / smooth_d for Stoch RSI
        risk_usdt: Max USDT risk for position sizing / macro strategies
        sl_price: Explicit SL price / exit_price for PnL calc
        fee_rate: Fee rate for breakeven/profit calculations (default 0.0006)
        funding_rate: Funding rate for breakeven calculations
        holding_hours: Hours holding position (for funding cost in breakeven)
        holding_periods: Number of 8h funding periods
        scale_pct: Percentage to scale in/out of a position
        reduce: Whether order is reduce-only
        scale: Scale factor for position adjustment
        use_atr: Use ATR-based dynamic SL/TP placement
        offset_pct: Offset percentage for trailing stop
        entry_price: Entry price for trade journal logging
        exit_price: Exit price for trade journal logging
        strategy: Strategy name tag for trade journal
        tags: Comma-separated tags for trade journal entry
        trade_notes: Notes for trade journal entry
        trade_id: Trade ID for journal lookup/update
        journal_status: Filter journal by status (open, closed, all)
        journal_data: JSON string of trade dicts for bulk journal import (e.g. '[{"symbol":"BTCUSDT",...}]')
        orders: JSON string of order dicts for batch operations (e.g. '[{"symbol":"BTCUSDT",...}]')
        slices: Number of slices for iceberg orders
        delay: Seconds between iceberg slices
        start_time: Start timestamp in milliseconds for history queries
        end_time: End timestamp in milliseconds for history queries
        total_usdt: Total USDT budget for DCA/grid/TWAP macros
        num_orders: Number of orders for DCA macro
        num_grids: Number of grid levels for grid macro
        upper_price: Upper price bound for grid macro
        lower_price: Lower price bound for grid macro
        rr_ratio: Risk-reward ratio for scalp/breakout macros
        atr_mult: ATR multiplier for SL in scalp/breakout macros
        duration_minutes: Duration for TWAP execution
        num_slices: Number of TWAP slices
        min_rate: Minimum funding rate filter for funding arb macro
        top_n: Number of top results to return
        price_range_pct: Price range percentage for DCA macro
        coin: Coin symbol for transfers/balance queries
        amount: Amount for internal transfers
        from_account: Source account for transfers (UNIFIED, FUND, SPOT)
        to_account: Destination account for transfers
        risk_id: Risk limit tier ID
        currency: Currency filter for borrow history/collateral
        depth: Orderbook depth for L2 analysis (default 50)
        period: Period for historical volatility (7, 14, 21, 30, 60, 90, 180, 270)
        num_bins: Number of bins for volume profile analysis
        num_levels: Number of DCA/scale levels for smart_dca
        num_entries: Number of scale-in entry orders
        num_exits: Number of scale-out exit orders
        spacing_pct: Price spacing percentage between scale entries
        tp_spacing_pct: Take-profit spacing percentage between scale exits
        hedge_pct: Percentage of position to hedge (0-100)
        dip_pct: Dip spacing percentage for smart DCA levels
        symbols: List of trading symbols for correlation analysis
        risk_fraction: Risk fraction of account for fixed fractional sizing (e.g. 0.02 = 2%)
        consecutive_wins: Number of consecutive wins for anti-martingale sizing
        consecutive_losses: Number of consecutive losses for anti-martingale sizing
        daily_return_pct: Expected daily return percentage for compound growth projection
        days: Number of days for compound growth projection
        win_rate_pct: Win rate percentage for compound growth projection
        trades_per_day: Number of trades per day for compound growth projection
        starting_capital: Starting capital in USDT for compound growth projection
        maint_margin_rate: Maintenance margin rate for liquidation price calculation
        account_balance: Account balance for fixed fractional position sizing
        margin: Margin amount to add (positive) or reduce (negative) for isolated positions
        auto_add_margin: Whether to enable auto-add-margin for isolated positions
        tp_sl_mode: TP/SL mode - 'Full' (entire position) or 'Partial'
        collateral_switch: Whether to enable (true) or disable (false) coin as collateral
        brick_size: Renko brick size (auto-calculated from ATR if not provided)
    """
    bot = _get_dispatcher()

    _parsed_orders = None
    if orders:
        try:
            _parsed_orders = json.loads(orders) if isinstance(orders, str) else orders
        except (json.JSONDecodeError, TypeError):
            return {
                "status": "error",
                "msg": "orders must be a valid JSON array string",
            }
    _parsed_journal = None
    if journal_data:
        try:
            _parsed_journal = (
                json.loads(journal_data)
                if isinstance(journal_data, str)
                else journal_data
            )
        except (json.JSONDecodeError, TypeError):
            return {
                "status": "error",
                "msg": "journal_data must be a valid JSON array string",
            }

    try:
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

        pidx = PositionIdx.ONE_WAY
        if position_idx is not None:
            try:
                pidx = PositionIdx(int(position_idx))
            except (ValueError, TypeError):
                pass

        # ══════════════════════════════════════════════════════
        # DIAGNOSTICS
        # ══════════════════════════════════════════════════════
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
        elif action == "get_server_time":
            return bot.get_server_time()

        # ══════════════════════════════════════════════════════
        # ORDERS
        # ══════════════════════════════════════════════════════
        elif action == "place_order":
            if not symbol or not side or qty is None:
                return {"status": "error", "msg": "symbol, side, and qty are required"}
            ot = (
                OrderType(order_type)
                if order_type
                else (OrderType.LIMIT if price is not None else OrderType.MARKET)
            )
            return bot.place_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                price=price,
                order_type=ot,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only or False,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
                trailing_stop=trailing_stop,
            )
        elif action == "amend_order":
            if not symbol or (not order_id and not client_oid):
                return {
                    "status": "error",
                    "msg": "symbol and (order_id or client_oid) required",
                }
            return bot.amend_order(
                symbol=symbol,
                order_id=order_id,
                client_oid=client_oid,
                qty=qty,
                price=price,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trigger_price=trigger_price,
            )
        elif action == "cancel_order":
            if not symbol or (not order_id and not client_oid):
                return {
                    "status": "error",
                    "msg": "symbol and (order_id or client_oid) required",
                }
            return bot.cancel_order(
                symbol=symbol, order_id=order_id, client_oid=client_oid, category=cat
            )
        elif action in ["cancel_all_orders", "cancel_all"]:
            return bot.cancel_all_orders(symbol=symbol, category=cat)
        elif action in ["get_open_orders", "open_orders", "orders"]:
            return {
                "orders": bot.get_open_orders(
                    symbol=symbol, category=cat, limit=limit or 50
                )
            }
        elif action in ["get_order_history", "order_history", "history"]:
            return {
                "orders": bot.get_order_history(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 50,
                    start_time=start_time,
                    end_time=end_time,
                )
            }
        elif action == "batch_orders":
            if not _parsed_orders:
                return {
                    "status": "error",
                    "msg": "orders list is required (JSON string)",
                }
            return bot.safe_execute(bot.execute_scalp_batch, _parsed_orders)
        elif action == "iceberg_order":
            if not symbol or not side or qty is None:
                return {"status": "error", "msg": "symbol, side, and qty required"}
            if price is None:
                ticker = bot.get_ticker(symbol, cat)
                price = float(ticker.get("lastPrice", 0))
            results = bot.place_iceberg_order(
                symbol=symbol,
                side=OrderSide(side),
                total_qty=qty,
                price=price,
                slices=int(slices) if slices else 5,
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                delay=float(delay) if delay is not None else 0.5,
            )
            return {"status": "ok", "iceberg_results": results}
        elif action == "place_trailing_stop_order":
            if not symbol or not side or qty is None or trailing_stop is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, qty, and trailing_stop required",
                }
            return bot.place_trailing_stop_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                trailing_distance=trailing_stop,
                category=cat,
                reduce_only=reduce_only or False,
                position_idx=pidx,
            )
        elif action == "calculate_trailing_stop_levels":
            if not symbol or price is None or not side or trailing_stop is None:
                return {
                    "status": "error",
                    "msg": "symbol, price (entry), side, and trailing_stop required",
                }
            return bot.calculate_trailing_stop_levels(
                symbol=symbol,
                entry_price=price,
                side=OrderSide(side),
                trailing_distance=trailing_stop,
                category=cat,
            )
        elif action == "get_trailing_stop_status":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_trailing_stop_status(symbol=symbol, category=cat)
        elif action == "place_conditional_order":
            if not symbol or not side or qty is None or trigger_price is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, qty, and trigger_price required",
                }
            ot = (
                OrderType(order_type)
                if order_type
                else (OrderType.LIMIT if price is not None else OrderType.MARKET)
            )
            return bot.place_conditional_order(
                symbol=symbol,
                side=OrderSide(side),
                qty=qty,
                trigger_price=trigger_price,
                price=price,
                order_type=ot,
                trigger_by=trigger_by or "LastPrice",
                category=cat,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reduce_only=reduce_only or False,
                time_in_force=tif,
                position_idx=pidx,
                client_oid=client_oid,
            )
        elif action == "batch_amend_orders":
            if not _parsed_orders:
                return {
                    "status": "error",
                    "msg": "orders list is required (JSON string)",
                }
            return bot.batch_amend_orders(_parsed_orders, category=cat)
        elif action == "batch_cancel_orders":
            if not _parsed_orders:
                return {
                    "status": "error",
                    "msg": "orders list is required (JSON string)",
                }
            return bot.batch_cancel_orders(_parsed_orders, category=cat)

        # ══════════════════════════════════════════════════════
        # POSITIONS & ACCOUNT
        # ══════════════════════════════════════════════════════
        elif action == "get_positions":
            return {"positions": bot.get_positions(category=cat, symbol=symbol)}
        elif action in ["get_wallet_balance", "check_balance", "wallet_balance"]:
            return bot.get_wallet_balance(account_type=account_type or "UNIFIED")
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
        elif action == "get_trade_history":
            return {
                "trades": bot.get_trade_history(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 100,
                    start_time=start_time,
                    end_time=end_time,
                )
            }
        elif action == "switch_margin_mode":
            if not symbol or position_idx is None:
                return {
                    "status": "error",
                    "msg": "symbol and position_idx (trade_mode: 0=cross, 1=isolated) required",
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
                    "msg": "position_idx (mode: 0=one-way, 3=hedge) required",
                }
            return bot.switch_position_mode(
                category=cat, symbol=symbol, mode=position_idx
            )
        elif action == "get_account_info":
            return bot.get_account_info()
        elif action == "get_api_key_info":
            return bot.get_api_key_info()
        elif action == "get_borrow_history":
            return {
                "list": bot.get_borrow_history(
                    currency=currency,
                    limit=limit or 50,
                    start_time=start_time,
                    end_time=end_time,
                )
            }
        elif action == "get_collateral_info":
            return {"list": bot.get_collateral_info(currency=currency)}
        elif action == "get_coin_greeks":
            return {"list": bot.get_coin_greeks()}
        elif action == "internal_transfer":
            if not coin or not amount:
                return {"status": "error", "msg": "coin and amount required"}
            return bot.internal_transfer(
                coin=coin,
                amount=amount,
                from_account=from_account or "UNIFIED",
                to_account=to_account or "FUND",
            )
        elif action == "get_coin_balance":
            if not coin:
                return {"status": "error", "msg": "coin is required"}
            return bot.get_coin_balance(coin=coin, account_type=account_type or "FUND")

        # ══════════════════════════════════════════════════════
        # MARKET DATA
        # ══════════════════════════════════════════════════════
        elif action == "get_ticker":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_ticker(symbol=symbol, category=cat)
        elif action == "get_24hr_ticker":
            return {"tickers": bot.get_24hr_ticker(symbol=symbol, category=cat)}
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
                    start_time=start_time,
                    end_time=end_time,
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
        elif action == "get_funding_rate":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_funding_rate(symbol=symbol, category=cat)
        elif action == "get_funding_rate_history":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "list": bot.get_funding_rate_history(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 200,
                    start_time=start_time,
                    end_time=end_time,
                )
            }
        elif action == "get_long_short_ratio":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {
                "list": bot.get_long_short_ratio(
                    symbol=symbol, period=interval_time or "5min", limit=limit or 50
                )
            }
        elif action == "get_mark_price":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_mark_price(symbol=symbol, category=cat)
        elif action == "get_index_price":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_index_price(symbol=symbol, category=cat)
        elif action == "get_price_bands":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_price_bands(symbol=symbol, category=cat)
        elif action == "get_instruments_info":
            return {
                "list": bot.get_instruments_info(
                    symbol=symbol, category=cat, limit=limit or 500
                )
            }
        elif action == "get_risk_limit":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return {"list": bot.get_risk_limit(symbol=symbol, category=cat)}
        elif action == "set_risk_limit":
            if not symbol or risk_id is None:
                return {"status": "error", "msg": "symbol and risk_id required"}
            return bot.set_risk_limit(symbol=symbol, risk_id=risk_id, category=cat)
        elif action == "get_insurance_pool":
            return {"list": bot.get_insurance_pool(coin=coin or "USDT")}
        elif action == "get_delivery_price":
            return {
                "list": bot.get_delivery_price(
                    symbol=symbol, category=cat, limit=limit or 50
                )
            }
        elif action == "get_historical_volatility":
            return {
                "list": bot.get_historical_volatility(
                    period=period or 7, start_time=start_time, end_time=end_time
                )
            }

        # ══════════════════════════════════════════════════════
        # L2 ORDERBOOK ANALYSIS
        # ══════════════════════════════════════════════════════
        elif action == "get_l2_orderbook_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_l2_orderbook_analysis(
                symbol=symbol, depth=depth or 50, category=cat
            )

        # ══════════════════════════════════════════════════════
        # MARKET INTELLIGENCE
        # ══════════════════════════════════════════════════════
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
        elif action == "get_trend_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_trend_analysis(
                symbol=symbol,
                category=cat,
                interval=interval or "60",
                lookback_periods=max(limit or 200, 200),
                include_advanced_indicators=True,
            )

        # ══════════════════════════════════════════════════════
        # TECHNICAL INDICATORS
        # ══════════════════════════════════════════════════════
        elif action == "calculate_bollinger_bands":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return bot.calculate_bollinger_bands(
                closes, period=int(sl_pct or 20), std_dev=tp_pct or 2.0
            )
        elif action == "calculate_macd":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return bot.calculate_macd(closes)
        elif action == "calculate_stoch_rsi":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            smooth_k = int(sl_pct) if sl_pct and sl_pct > 0 else 3
            smooth_d = int(tp_pct) if tp_pct and tp_pct > 0 else 3
            return bot.calculate_stoch_rsi(
                closes, period=14, smooth_k=smooth_k, smooth_d=smooth_d
            )
        elif action == "calculate_cci":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return {"cci": bot.calculate_cci(highs, lows, closes)}
        elif action == "calculate_donchian_channels":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            return bot.calculate_donchian_channels(
                highs, lows, period=int(sl_pct or 20)
            )
        elif action == "calculate_adx":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return {"adx": bot.calculate_adx(highs, lows, closes)}
        elif action == "calculate_fib_pivots":
            if price is None or qty is None or sl_price is None:
                return {
                    "status": "error",
                    "msg": "price (High), qty (Low), sl_price (Close) required",
                }
            return bot.calculate_fib_pivots(high=price, low=qty, close=sl_price)
        elif action == "calculate_vwap":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            ohlcv = [
                {
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
                for k in klines
            ]
            return {"vwap": bot.calculate_vwap(ohlcv)}
        elif action == "calculate_ichimoku_cloud":
            if not symbol:
                return {
                    "status": "error",
                    "msg": "symbol required (fetches klines automatically)",
                }
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=max(limit or 200, 60),
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            return bot.calculate_ichimoku_cloud(highs, lows)

        # ══════════════════════════════════════════════════════
        # RISK, PNL & BREAKEVEN
        # ══════════════════════════════════════════════════════
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
                    "msg": "price (entry), sl_price (exit), qty, side required",
                }
            return bot.calculate_trade_pnl(
                entry=price,
                exit=sl_price,
                qty=qty,
                side=side,
                fee_rate=fee_rate or 0.0006,
            )
        elif action == "calculate_profit_target":
            if price is None or sl_price is None:
                return {"status": "error", "msg": "price (entry) and sl_price required"}
            return bot.calculate_profit_target(entry_price=price, sl_price=sl_price)
        elif action == "calculate_sl_tp":
            if not side or price is None:
                return {"status": "error", "msg": "side and price are required"}
            sl, tp = bot.calculate_sl_tp(
                entry_price=price, side=OrderSide(side), sl_pct=sl_pct, tp_pct=tp_pct
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
        elif action == "calculate_position_size":
            if not symbol or price is None or sl_price is None or risk_usdt is None:
                return {
                    "status": "error",
                    "msg": "symbol, price, sl_price, and risk_usdt required",
                }
            lev = leverage if leverage is not None else None
            qty_out = bot.calculate_position_size(
                symbol=symbol,
                entry_price=price,
                sl_price=sl_price,
                risk_usdt=risk_usdt,
                category=cat,
                leverage=lev,
            )
            return {
                "symbol": symbol,
                "entry_price": price,
                "sl_price": sl_price,
                "risk_usdt": risk_usdt,
                "quantity": qty_out,
                "leverage_used": lev,
            }
        elif action == "calculate_volatility_adjusted_size":
            if not symbol or price is None or risk_usdt is None:
                return {
                    "status": "error",
                    "msg": "symbol, price, sl_price, and risk_usdt required",
                }
            _sl = sl_price if sl_price is not None else price * 0.98
            _bal = account_balance if account_balance is not None else risk_usdt / 0.02
            result = bot.calculate_volatility_adjusted_size(
                symbol=symbol,
                entry_price=price,
                sl_price=_sl,
                risk_usdt=risk_usdt,
                account_balance=_bal,
                category=cat,
            )
            return (
                result
                if isinstance(result, dict)
                else {
                    "symbol": symbol,
                    "entry_price": price,
                    "risk_usdt": risk_usdt,
                    "quantity": result,
                }
            )
        elif action == "calculate_breakeven":
            if price is None or qty is None or not side:
                return {
                    "status": "error",
                    "msg": "price (entry), qty, and side required",
                }
            return bot.calculate_breakeven(
                entry_price=price,
                qty=qty,
                side=side,
                fee_rate=fee_rate or 0.0006,
                funding_rate=funding_rate or 0.0,
                holding_periods=holding_periods or 0,
                leverage=float(leverage) if leverage else 1.0,
            )
        elif action == "calculate_breakeven_with_fees":
            if not symbol or price is None or qty is None or not side:
                return {
                    "status": "error",
                    "msg": "symbol, price (entry), qty, and side required",
                }
            return bot.calculate_breakeven_with_fees(
                symbol=symbol,
                entry_price=price,
                qty=qty,
                side=side,
                category=cat,
                leverage=float(leverage) if leverage else 1.0,
                holding_hours=holding_hours or 0.0,
            )
        elif action == "calculate_profit_after_fees":
            if price is None or sl_price is None or qty is None or not side:
                return {
                    "status": "error",
                    "msg": "price (entry), sl_price (exit), qty, side required",
                }
            return bot.calculate_profit_after_fees(
                entry_price=price,
                exit_price=sl_price,
                qty=qty,
                side=side,
                fee_rate=fee_rate or 0.0006,
                funding_rate=funding_rate or 0.0,
                holding_periods=holding_periods or 0,
                leverage=float(leverage) if leverage else 1.0,
            )

        # ══════════════════════════════════════════════════════
        # PNL REPORTS
        # ══════════════════════════════════════════════════════
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

        # ══════════════════════════════════════════════════════
        # PROFITABLE MACRO STRATEGIES
        # ══════════════════════════════════════════════════════
        elif action == "macro_dca_plan":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.macro_dca_plan(
                symbol=symbol,
                total_usdt=total_usdt or 100.0,
                num_orders=num_orders or 5,
                price_range_pct=price_range_pct or 5.0,
                side=side or "Buy",
                category=cat,
            )
        elif action == "macro_grid_plan":
            if not symbol or upper_price is None or lower_price is None:
                return {
                    "status": "error",
                    "msg": "symbol, upper_price, and lower_price required",
                }
            return bot.macro_grid_plan(
                symbol=symbol,
                upper_price=upper_price,
                lower_price=lower_price,
                num_grids=num_grids or 10,
                total_usdt=total_usdt or 100.0,
                category=cat,
            )
        elif action == "macro_scalp_momentum":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.macro_scalp_momentum(
                symbol=symbol,
                risk_usdt=risk_usdt or 10.0,
                rr_ratio=rr_ratio or 2.0,
                atr_sl_mult=atr_mult or 1.5,
                category=cat,
            )
        elif action == "macro_mean_reversion":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.macro_mean_reversion(
                symbol=symbol, risk_usdt=risk_usdt or 10.0, category=cat
            )
        elif action == "macro_funding_arb":
            return bot.macro_funding_arb(
                min_rate=min_rate or 0.0005,
                category=cat,
                top_n=top_n or 10,
            )
        elif action == "macro_twap":
            if not symbol or not side or qty is None:
                return {"status": "error", "msg": "symbol, side, and qty required"}
            return bot.macro_twap(
                symbol=symbol,
                side=side,
                total_qty=qty,
                duration_minutes=duration_minutes or 30,
                num_slices=num_slices or 10,
                category=cat,
            )
        elif action == "macro_breakout":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.macro_breakout(
                symbol=symbol,
                risk_usdt=risk_usdt or 10.0,
                lookback=limit or 50,
                atr_mult=atr_mult or 0.5,
                rr_ratio=rr_ratio or 3.0,
                category=cat,
            )
        elif action == "macro_portfolio_summary":
            return bot.macro_portfolio_summary(category=cat)
        elif action == "macro_smart_entry":
            if not symbol or not side:
                return {"status": "error", "msg": "symbol and side required"}
            return bot.macro_smart_entry(
                symbol=symbol,
                side=side,
                risk_usdt=risk_usdt or 10.0,
                category=cat,
            )
        elif action == "macro_microprofit_scalp":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.macro_microprofit_scalp(
                symbol=symbol,
                risk_usdt=risk_usdt or 5.0,
                category=cat,
            )

        # ══════════════════════════════════════════════════════
        # ENHANCED VOLUME & L2 ORDERBOOK
        # ══════════════════════════════════════════════════════
        elif action == "volume_profile":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_volume_profile(
                symbol=symbol,
                num_bins=num_bins or 20,
                lookback=limit or 200,
                category=cat,
            )
        elif action == "volume_divergence":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_volume_divergence(
                symbol=symbol, lookback=limit or 50, category=cat
            )
        elif action == "orderbook_heatmap":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_orderbook_heatmap(
                symbol=symbol, depth=depth or 50, category=cat
            )
        elif action == "orderflow_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_orderflow_analysis(
                symbol=symbol, depth=depth or 50, category=cat
            )

        # ══════════════════════════════════════════════════════
        # COMPREHENSIVE TREND ANALYSIS
        # ══════════════════════════════════════════════════════
        elif action == "comprehensive_trend_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_comprehensive_trend_analysis(
                symbol=symbol,
                interval=interval or "15",
                lookback_periods=limit or 200,
                category=cat,
            )

        # ══════════════════════════════════════════════════════
        # FEE AWARENESS & PRECISION
        # ══════════════════════════════════════════════════════
        elif action == "get_instrument_precision":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_instrument_precision(symbol=symbol, category=cat.value)
        elif action == "validate_order_params":
            if not symbol or not side or qty is None:
                return {"status": "error", "msg": "symbol, side, and qty required"}
            return bot.validate_order_params(
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                order_type=order_type or "Limit",
                category=cat.value,
            )
        elif action == "calculate_fee_adjusted_targets":
            ep = entry_price if entry_price is not None else price
            if not symbol or not side or ep is None or qty is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, entry_price (or price), and qty required",
                }
            return bot.calculate_fee_adjusted_targets(
                symbol=symbol,
                side=side,
                entry_price=ep,
                qty=qty,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                category=cat.value,
            )
        elif action == "get_min_order_value":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_min_order_value(symbol=symbol, category=cat.value)

        # ══════════════════════════════════════════════════════
        # ADVANCED POSITION MANAGEMENT
        # ══════════════════════════════════════════════════════
        elif action == "get_position_detail":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.get_position_detail(symbol=symbol, category=cat.value)
        elif action == "scale_position":
            if not symbol or not side or scale_pct is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, and scale_pct required",
                }
            return bot.scale_position(
                symbol=symbol,
                side=side,
                scale_pct=scale_pct,
                category=cat.value,
                reduce=reduce or False,
            )
        elif action == "close_position":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}

            # Robustly parse qty (e.g., "75%" or 0.75 or 0.004)
            qty_pct = 1.0
            if qty is not None:
                if isinstance(qty, str):
                    if "%" in qty:
                        qty_pct = float(qty.replace("%", "")) / 100.0
                    else:
                        qty_pct = float(qty)
                else:
                    # If it's a number, assume it's a fraction or full amount
                    q = float(qty)
                    qty_pct = q if q <= 1.0 else 1.0

            return bot.close_position(
                symbol=symbol, category=cat.value, qty_pct=qty_pct
            )
        elif action == "flip_position":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.flip_position(
                symbol=symbol, category=cat.value, scale=scale or 1.0
            )
        elif action == "auto_sl_tp":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.auto_sl_tp(
                symbol=symbol,
                category=cat.value,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                use_atr=use_atr or False,
                atr_mult=atr_mult or 1.5,
            )
        elif action == "move_sl_to_breakeven":
            if not symbol:
                return {"status": "error", "msg": "symbol is required"}
            return bot.move_sl_to_breakeven(
                symbol=symbol,
                category=cat.value,
                offset_pct=offset_pct or 0.001,
            )
        elif action == "get_all_positions_summary":
            return bot.get_all_positions_summary(category=cat.value)

        # ══════════════════════════════════════════════════════
        # TRADE JOURNALING
        # ══════════════════════════════════════════════════════
        elif action == "journal_record_trade":
            if not symbol or not side or entry_price is None or qty is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, entry_price (use --entry-price), and qty required",
                }
            return bot.journal_record_trade(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                qty=qty,
                exit_price=exit_price,
                strategy=strategy or "",
                tags=tags or "",
                notes=trade_notes or "",
                category=cat.value,
            )
        elif action == "journal_close_trade":
            if not trade_id or exit_price is None:
                return {"status": "error", "msg": "trade_id and exit_price required"}
            return bot.journal_close_trade(trade_id=trade_id, exit_price=exit_price)
        elif action == "journal_get_trades":
            return bot.journal_get_trades(
                symbol=symbol,
                strategy=strategy,
                status=journal_status,
                limit=limit or 50,
            )
        elif action == "journal_performance":
            return bot.journal_performance(symbol=symbol, strategy=strategy)
        elif action == "journal_export":
            return bot.journal_export()
        elif action == "journal_import":
            if not _parsed_journal:
                return {
                    "status": "error",
                    "msg": "journal_data (JSON string of trade dicts) required",
                }
            return bot.journal_import(trades=_parsed_journal)

        # ══════════════════════════════════════════════════════
        # ADVANCED POSITION SIZING
        # ══════════════════════════════════════════════════════
        elif action == "adaptive_position_size":
            if not symbol or not side:
                return {"status": "error", "msg": "symbol and side required"}
            return bot.get_adaptive_position_size(
                symbol=symbol, side=side, risk_usdt=risk_usdt or 5.0, category=cat
            )
        elif action == "fixed_fractional_size":
            if (
                not symbol
                or price is None
                or stop_loss is None
                or account_balance is None
            ):
                return {
                    "status": "error",
                    "msg": "symbol, price (entry), stop_loss, and account_balance required",
                }
            return bot.get_fixed_fractional_size(
                account_balance=account_balance,
                risk_fraction=risk_fraction or 0.02,
                entry_price=price,
                sl_price=stop_loss,
                symbol=symbol,
                category=cat,
            )
        elif action == "anti_martingale_size":
            if qty is None:
                return {"status": "error", "msg": "qty (base_qty) required"}
            return bot.get_anti_martingale_size(
                base_qty=qty,
                consecutive_wins=consecutive_wins or 0,
                consecutive_losses=consecutive_losses or 0,
            )
        elif action == "portfolio_heat":
            return bot.get_portfolio_heat(category=cat)
        elif action == "max_position":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.calculate_max_position(
                symbol=symbol, side=side or "Buy", category=cat
            )

        # ══════════════════════════════════════════════════════
        # PROFIT-MAXIMIZING STRATEGIES
        # ══════════════════════════════════════════════════════
        elif action == "momentum_sniper":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.macro_momentum_sniper(
                symbol=symbol, risk_usdt=risk_usdt or 5.0, category=cat
            )
        elif action == "mean_reversion_scalp":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.macro_mean_reversion_scalp(
                symbol=symbol, risk_usdt=risk_usdt or 5.0, category=cat
            )
        elif action == "funding_arb_scan":
            return bot.macro_funding_arb_scan(
                top_n=top_n or 10, min_rate=min_rate or 0.0005, category=cat
            )
        elif action == "smart_dca":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.macro_smart_dca(
                symbol=symbol,
                total_usdt=total_usdt or 100.0,
                num_levels=num_levels or 5,
                dip_pct=dip_pct or 1.0,
                category=cat,
            )
        elif action == "liquidity_sweep":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.macro_liquidity_sweep(
                symbol=symbol,
                risk_usdt=risk_usdt or 5.0,
                depth=depth or 50,
                category=cat,
            )

        # ══════════════════════════════════════════════════════
        # 25 IMPORTANT FUNCTIONS
        # ══════════════════════════════════════════════════════
        elif action == "risk_reward_analysis":
            if price is None or stop_loss is None or take_profit is None or qty is None:
                return {
                    "status": "error",
                    "msg": "price (entry), stop_loss, take_profit, and qty required",
                }
            return bot.get_risk_reward_analysis(
                entry_price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                qty=qty,
                fee_rate=fee_rate or 0.0006,
            )
        elif action == "liquidation_price":
            if price is None or leverage is None or not side:
                return {
                    "status": "error",
                    "msg": "price (entry), leverage, and side required",
                }
            return bot.get_liquidation_price(
                entry_price=price,
                leverage=float(leverage),
                side=side,
                maint_margin_rate=maint_margin_rate or 0.004,
            )
        elif action == "drawdown_analysis":
            return bot.get_drawdown_analysis(category=cat, limit=limit or 50)
        elif action == "support_resistance":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.get_support_resistance(
                symbol=symbol, lookback=limit or 100, category=cat
            )
        elif action == "market_regime":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.get_market_regime(symbol=symbol, category=cat)
        elif action == "entry_timing":
            if not symbol or not side:
                return {"status": "error", "msg": "symbol and side required"}
            return bot.get_entry_timing(symbol=symbol, side=side, category=cat)
        elif action == "scale_into":
            if not symbol or not side or qty is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, and qty (total_qty) required",
                }
            return bot.scale_into_position(
                symbol=symbol,
                side=side,
                total_qty=qty,
                num_entries=num_entries or 3,
                spacing_pct=spacing_pct or 0.5,
                category=cat,
            )
        elif action == "scale_out":
            if not symbol or not side or qty is None:
                return {
                    "status": "error",
                    "msg": "symbol, side, and qty (total_qty) required",
                }
            return bot.scale_out_position(
                symbol=symbol,
                side=side,
                total_qty=qty,
                num_exits=num_exits or 3,
                tp_spacing_pct=tp_spacing_pct or 1.0,
                category=cat,
            )
        elif action == "hedge_position":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.hedge_position(
                symbol=symbol, hedge_pct=hedge_pct or 50.0, category=cat
            )
        elif action == "multi_timeframe_signals":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.get_multi_timeframe_signals(symbol=symbol, category=cat)
        elif action == "market_session":
            return bot.get_market_session_info()
        elif action == "whale_detection":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.detect_whale_activity(symbol=symbol, category=cat)
        elif action == "fear_greed":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.get_fear_greed_signal(symbol=symbol, category=cat)
        elif action == "compound_growth":
            return bot.calculate_compound_growth(
                starting_capital=starting_capital or 100.0,
                daily_return_pct=daily_return_pct or 1.0,
                days=days or 30,
                win_rate_pct=win_rate_pct or 60.0,
                trades_per_day=trades_per_day or 3,
            )
        elif action == "trade_checklist":
            if (
                not symbol
                or not side
                or price is None
                or stop_loss is None
                or take_profit is None
            ):
                return {
                    "status": "error",
                    "msg": "symbol, side, price (entry), stop_loss, take_profit required",
                }
            return bot.get_trade_checklist(
                symbol=symbol,
                side=side,
                entry_price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_usdt=risk_usdt or 5.0,
                category=cat,
            )
        elif action == "auto_leverage":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.auto_set_leverage(symbol=symbol, category=cat)
        elif action == "unrealized_pnl":
            return bot.get_unrealized_pnl_report(category=cat)
        elif action == "divergence_signals":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.detect_divergence_signals(
                symbol=symbol, lookback=max(limit or 100, 100), category=cat
            )
        elif action == "optimal_entry_zones":
            if not symbol or not side:
                return {"status": "error", "msg": "symbol and side required"}
            return bot.get_optimal_entry_zones(symbol=symbol, side=side, category=cat)
        elif action == "spread_analysis":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.get_spread_analysis(symbol=symbol, category=cat)
        elif action == "correlation_analysis":
            if not symbols or len(symbols) < 2:
                return {
                    "status": "error",
                    "msg": "symbols (list of 2+ symbols) required",
                }
            return bot.get_correlation_analysis(
                symbols=symbols,
                interval=interval or "60",
                lookback=limit or 50,
                category=cat,
            )
        elif action == "smart_trailing_stop":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.smart_trailing_stop(symbol=symbol, category=cat)

        # ══════════════════════════════════════════════════════
        # MISSING V5 ENDPOINTS
        # ══════════════════════════════════════════════════════
        elif action == "batch_place_orders":
            if not _parsed_orders:
                return {"status": "error", "msg": "orders list required (JSON string)"}
            return bot.batch_place_orders(_parsed_orders, category=cat)
        elif action == "set_tpsl_mode":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.set_tpsl_mode(
                symbol=symbol, tp_sl_mode=tp_sl_mode or "Full", category=cat
            )
        elif action == "add_reduce_margin":
            if not symbol or margin is None:
                return {"status": "error", "msg": "symbol and margin required"}
            return bot.add_reduce_margin(symbol=symbol, margin=margin, category=cat)
        elif action == "set_auto_add_margin":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.set_auto_add_margin(
                symbol=symbol,
                auto_add=auto_add_margin if auto_add_margin is not None else True,
                category=cat,
            )
        elif action == "get_executions":
            return {
                "executions": bot.get_executions(
                    symbol=symbol,
                    category=cat,
                    limit=limit or 50,
                    start_time=start_time,
                    end_time=end_time,
                )
            }
        elif action == "confirm_risk_limit":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.confirm_risk_limit(symbol=symbol, category=cat)
        elif action == "set_collateral_coin":
            if not coin:
                return {"status": "error", "msg": "coin required"}
            return bot.set_collateral_coin(
                coin=coin,
                switch=collateral_switch if collateral_switch is not None else True,
            )
        elif action == "get_withdrawable_amount":
            if not coin:
                return {"status": "error", "msg": "coin required"}
            return bot.get_withdrawable_amount(
                coin=coin, account_type=account_type or "FUND"
            )
        elif action == "get_historical_klines":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return {
                "klines": bot.get_historical_klines(
                    symbol=symbol,
                    interval=interval or "D",
                    limit=limit or 200,
                    category=cat,
                )
            }
        elif action == "get_premium_index_kline":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return {
                "klines": bot.get_premium_index_kline(
                    symbol=symbol,
                    interval=interval or "60",
                    limit=limit or 200,
                    category=cat,
                )
            }

        # ══════════════════════════════════════════════════════
        # STANDALONE INDICATORS
        # ══════════════════════════════════════════════════════
        elif action == "calculate_rsi":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "interval": interval or "60",
                "rsi": bot.calculate_rsi(closes, period=int(sl_pct or 14)),
            }
        elif action == "calculate_ema":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "interval": interval or "60",
                "ema": bot.calculate_ema(closes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_atr":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            # Build ohlcv list as expected by the method
            ohlcv_list = [
                {"high": float(k[2]), "low": float(k[3]), "close": float(k[4])}
                for k in klines
            ]
            period = int(sl_pct) if sl_pct else 14
            return {"symbol": symbol, "atr": bot.calculate_atr(ohlcv_list, period)}
        elif action == "calculate_obv":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            return {"symbol": symbol, "obv": bot.calculate_obv(closes, volumes)}
        elif action == "calculate_cvd":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            # Get actual trades for proper CVD calculation
            trades = bot.get_recent_trades(symbol=symbol, limit=200, category=cat)
            if not trades:
                return {"status": "error", "msg": "No trade data"}
            return {"symbol": symbol, "cvd": bot.calculate_cvd(trades)}
        elif action == "calculate_mfi":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            return {
                "symbol": symbol,
                "mfi": bot.calculate_mfi(
                    highs, lows, closes, volumes, period=int(sl_pct or 14)
                ),
            }
        elif action == "calculate_cmf":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            return {
                "symbol": symbol,
                "cmf": bot.calculate_cmf(
                    highs, lows, closes, volumes, period=int(sl_pct or 20)
                ),
            }
        elif action == "calculate_williams_r":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "williams_r": bot.calculate_williams_r(
                    highs, lows, closes, period=int(sl_pct or 14)
                ),
            }
        elif action == "calculate_parabolic_sar":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return bot.calculate_parabolic_sar(highs, lows, closes)
        elif action == "calculate_keltner_channels":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return bot.calculate_keltner_channels(highs, lows, closes)
        elif action == "calculate_roc":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "roc": bot.calculate_roc(closes, period=int(sl_pct or 12)),
            }
        elif action == "calculate_trix":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "trix": bot.calculate_trix(closes, period=int(sl_pct or 15)),
            }
        elif action == "calculate_ultimate_oscillator":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "ultimate_oscillator": bot.calculate_ultimate_oscillator(
                    highs, lows, closes
                ),
            }
        elif action == "calculate_choppiness_index":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "choppiness_index": bot.calculate_choppiness_index(highs, lows, closes),
            }
        elif action == "calculate_aroon":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            return bot.calculate_aroon(highs, lows)
        elif action == "calculate_dpo":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "dpo": bot.calculate_dpo(closes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_hma":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "hma": bot.calculate_hma(closes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_zlema":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "zlema": bot.calculate_zlema(closes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_supertrend":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return bot.calculate_supertrend(highs, lows, closes)
        elif action == "calculate_elder_ray":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return bot.calculate_elder_ray(highs, lows, closes)
        elif action == "calculate_vwma":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            return {
                "symbol": symbol,
                "vwma": bot.calculate_vwma(closes, volumes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_awesome_oscillator":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            return {
                "symbol": symbol,
                "awesome_oscillator": bot.calculate_awesome_oscillator(highs, lows),
            }
        elif action == "calculate_accumulation_distribution":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]
            return {
                "symbol": symbol,
                "accumulation_distribution": bot.calculate_accumulation_distribution(
                    highs, lows, closes, volumes
                ),
            }
        elif action == "calculate_heikin_ashi":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            opens = [float(k[1]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            return bot.calculate_heikin_ashi(opens, highs, lows, closes)
        elif action == "calculate_renko":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return bot.calculate_renko(closes, brick_size=brick_size)
        elif action == "calculate_pivot_points":
            if price is None or qty is None or sl_price is None:
                return {
                    "status": "error",
                    "msg": "price (High), qty (Low), sl_price (Close) required",
                }
            return bot.calculate_pivot_points(high=price, low=qty, close=sl_price)
        elif action == "calculate_position_risk":
            if not symbol or price is None or qty is None or stop_loss is None:
                return {
                    "status": "error",
                    "msg": "symbol, price, qty, stop_loss required",
                }
            return bot.calculate_position_risk(
                symbol=symbol,
                entry_price=price,
                qty=qty,
                stop_loss=stop_loss,
                category=cat,
            )
        elif action == "calculate_compound_growth":
            return bot.calculate_compound_growth(
                starting_capital=starting_capital or 100.0,
                daily_return_pct=daily_return_pct or 1.0,
                days=days or 30,
                win_rate_pct=win_rate_pct or 60.0,
                trades_per_day=trades_per_day or 3,
            )
        elif action == "calculate_linear_regression":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "linear_regression_slope": bot.calculate_linear_regression_slope(
                    closes, period=int(sl_pct or 20)
                ),
            }
        elif action == "calculate_stddev":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            return {
                "symbol": symbol,
                "stddev": bot.calculate_stddev(closes, period=int(sl_pct or 20)),
            }
        elif action == "calculate_vroc":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            volumes = [float(k[5]) for k in klines]
            return {
                "symbol": symbol,
                "vroc": bot.calculate_vroc(volumes, period=int(sl_pct or 14)),
            }
        elif action == "calculate_kelly_criterion_standalone":
            # Calculate win_loss_ratio from tp_pct and sl_pct
            avg_win = float(tp_pct) if tp_pct else 2.0
            avg_loss = float(sl_pct) if sl_pct else 1.0
            win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 2.0
            return bot.calculate_kelly_criterion(
                win_rate=float(win_rate_pct) / 100.0 if win_rate_pct else 0.55,
                win_loss_ratio=win_loss_ratio,
            )
        elif action == "calculate_max_position":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            return bot.calculate_max_position(symbol=symbol, category=cat)
        elif action == "calculate_bollinger_bandwidth":
            if not symbol:
                return {"status": "error", "msg": "symbol required"}
            klines = bot.get_klines(
                symbol=symbol,
                interval=interval or "60",
                limit=limit or 200,
                category=cat,
            )
            if not klines:
                return {"status": "error", "msg": "No kline data"}
            klines.reverse()
            closes = [float(k[4]) for k in klines]
            bb = bot.calculate_bollinger_bands(closes, period=int(sl_pct or 20))
            if (
                isinstance(bb, dict)
                and "upper" in bb
                and "lower" in bb
                and "middle" in bb
            ):
                bw = (
                    (bb["upper"] - bb["lower"]) / bb["middle"] * 100
                    if bb["middle"]
                    else 0
                )
                return {
                    "symbol": symbol,
                    "bollinger_bandwidth": round(bw, 4),
                    "upper": bb["upper"],
                    "lower": bb["lower"],
                    "middle": bb["middle"],
                }
            return {"symbol": symbol, "bollinger_bands": bb}

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
Examples:
  python bybit-realm.py --action health_check
  python bybit-realm.py --action get_ticker          --symbol BTCUSDT --category spot
  python bybit-realm.py --action get_market_momentum --symbol BTCUSDT
  python bybit-realm.py --action get_trend_analysis  --symbol BTCUSDT --interval 60 --limit 200
  python bybit-realm.py --action get_market_health   --symbol BTCUSDT
  python bybit-realm.py --action get_wallet_balance  --account-type UNIFIED
  python bybit-realm.py --action get_positions       --category linear
  python bybit-realm.py --action calculate_sl_tp     --side Buy  --price 65000
  python bybit-realm.py --action get_pnl_report      --symbol BTCUSDT
  python bybit-realm.py --action renew_tor_circuit
        """,
    )

    parser.add_argument(
        "--config",
        default="trading_config.json",
        help="Path to JSON configuration file",
    )
    parser.add_argument("--action", required=True, help="Action to perform")
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
    parser.add_argument("--order-id", dest="order_id", help="Order ID")
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
    parser.add_argument("--reduce-only", dest="reduce_only", action="store_true")
    parser.add_argument("--time-in-force", dest="time_in_force", default="GTC")
    parser.add_argument("--position-idx", dest="position_idx", type=int, default=0)
    parser.add_argument("--client-oid", dest="client_oid")
    parser.add_argument("--leverage", type=int)
    parser.add_argument("--buy-leverage", dest="buy_leverage", type=int)
    parser.add_argument("--sell-leverage", dest="sell_leverage", type=int)
    parser.add_argument("--account-type", dest="account_type", default="UNIFIED")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--interval", default="1")
    parser.add_argument("--interval-time", dest="interval_time", default="5min")
    parser.add_argument(
        "--strong-threshold", dest="strong_threshold", type=float, default=0.20
    )
    parser.add_argument(
        "--mild-threshold", dest="mild_threshold", type=float, default=0.08
    )
    parser.add_argument(
        "--sl-pct",
        dest="sl_pct",
        type=float,
        help="Stop loss percentage or smooth_k for Stoch RSI",
    )
    parser.add_argument(
        "--tp-pct",
        dest="tp_pct",
        type=float,
        help="Take profit percentage or smooth_d for Stoch RSI",
    )
    parser.add_argument("--risk-usdt", dest="risk_usdt", type=float)
    parser.add_argument("--sl-price", dest="sl_price", type=float)
    parser.add_argument(
        "--fee-rate",
        dest="fee_rate",
        type=float,
        help="Fee rate for breakeven/profit calc",
    )
    parser.add_argument(
        "--funding-rate",
        dest="funding_rate",
        type=float,
        help="Funding rate for breakeven calc",
    )
    parser.add_argument(
        "--holding-hours",
        dest="holding_hours",
        type=float,
        help="Hours holding position",
    )
    parser.add_argument(
        "--holding-periods",
        dest="holding_periods",
        type=int,
        help="Number of 8h funding periods",
    )
    parser.add_argument("--slices", type=int, default=5)
    parser.add_argument("--delay", type=float)
    parser.add_argument(
        "--start-time",
        dest="start_time",
        type=int,
        help="Start timestamp in milliseconds",
    )
    parser.add_argument(
        "--end-time", dest="end_time", type=int, help="End timestamp in milliseconds"
    )
    parser.add_argument(
        "--total-usdt",
        dest="total_usdt",
        type=float,
        help="Total USDT for DCA/grid/TWAP",
    )
    parser.add_argument(
        "--num-orders", dest="num_orders", type=int, help="Number of DCA orders"
    )
    parser.add_argument(
        "--num-grids", dest="num_grids", type=int, help="Number of grid levels"
    )
    parser.add_argument(
        "--upper-price", dest="upper_price", type=float, help="Upper price for grid"
    )
    parser.add_argument(
        "--lower-price", dest="lower_price", type=float, help="Lower price for grid"
    )
    parser.add_argument(
        "--rr-ratio", dest="rr_ratio", type=float, help="Risk-reward ratio"
    )
    parser.add_argument(
        "--atr-mult", dest="atr_mult", type=float, help="ATR multiplier for SL"
    )
    parser.add_argument(
        "--duration-minutes", dest="duration_minutes", type=int, help="TWAP duration"
    )
    parser.add_argument("--num-slices", dest="num_slices", type=int, help="TWAP slices")
    parser.add_argument(
        "--min-rate", dest="min_rate", type=float, help="Min funding rate filter"
    )
    parser.add_argument("--top-n", dest="top_n", type=int, help="Top N results")
    parser.add_argument(
        "--price-range-pct",
        dest="price_range_pct",
        type=float,
        help="Price range pct for DCA",
    )
    parser.add_argument("--coin", help="Coin for transfers/balance")
    parser.add_argument("--amount", help="Amount for transfers")
    parser.add_argument(
        "--from-account", dest="from_account", help="Source account for transfer"
    )
    parser.add_argument(
        "--to-account", dest="to_account", help="Dest account for transfer"
    )
    parser.add_argument(
        "--risk-id", dest="risk_id", type=int, help="Risk limit tier ID"
    )
    parser.add_argument("--currency", help="Currency filter")
    parser.add_argument("--depth", type=int, help="L2 orderbook depth")
    parser.add_argument("--period", type=int, help="Historical volatility period")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument(
        "--orders-file", dest="orders_file", help="JSON file with batch order list"
    )
    # ── New params for 30 new actions ────────────────────────
    parser.add_argument(
        "--num-levels", dest="num_levels", type=int, help="Number of DCA/scale levels"
    )
    parser.add_argument(
        "--num-entries", dest="num_entries", type=int, help="Number of scale-in entries"
    )
    parser.add_argument(
        "--num-exits", dest="num_exits", type=int, help="Number of scale-out exits"
    )
    parser.add_argument(
        "--spacing-pct",
        dest="spacing_pct",
        type=float,
        help="Price spacing pct for scale entries",
    )
    parser.add_argument(
        "--tp-spacing-pct",
        dest="tp_spacing_pct",
        type=float,
        help="TP spacing pct for scale exits",
    )
    parser.add_argument(
        "--hedge-pct", dest="hedge_pct", type=float, help="Hedge percentage of position"
    )
    parser.add_argument(
        "--dip-pct", dest="dip_pct", type=float, help="Dip spacing pct for smart DCA"
    )
    parser.add_argument(
        "--symbols", nargs="+", help="List of symbols for correlation analysis"
    )
    parser.add_argument(
        "--risk-fraction",
        dest="risk_fraction",
        type=float,
        help="Risk fraction for fixed fractional sizing",
    )
    parser.add_argument(
        "--consecutive-wins",
        dest="consecutive_wins",
        type=int,
        help="Consecutive wins for anti-martingale",
    )
    parser.add_argument(
        "--consecutive-losses",
        dest="consecutive_losses",
        type=int,
        help="Consecutive losses for anti-martingale",
    )
    parser.add_argument(
        "--daily-return-pct",
        dest="daily_return_pct",
        type=float,
        help="Daily return pct for compound growth",
    )
    parser.add_argument(
        "--days", type=int, help="Number of days for compound growth projection"
    )
    parser.add_argument(
        "--win-rate-pct",
        dest="win_rate_pct",
        type=float,
        help="Win rate pct for compound growth",
    )
    parser.add_argument(
        "--trades-per-day",
        dest="trades_per_day",
        type=int,
        help="Trades per day for compound growth",
    )
    parser.add_argument(
        "--starting-capital",
        dest="starting_capital",
        type=float,
        help="Starting capital for compound growth",
    )
    parser.add_argument(
        "--maint-margin-rate",
        dest="maint_margin_rate",
        type=float,
        help="Maintenance margin rate for liquidation calc",
    )
    parser.add_argument(
        "--account-balance",
        dest="account_balance",
        type=float,
        help="Account balance for fixed fractional sizing",
    )
    parser.add_argument("--margin", help="Margin to add/reduce for isolated positions")
    parser.add_argument(
        "--auto-add-margin",
        dest="auto_add_margin",
        action="store_true",
        help="Enable auto-add-margin",
    )
    parser.add_argument(
        "--tp-sl-mode", dest="tp_sl_mode", help="TP/SL mode: Full or Partial"
    )
    parser.add_argument(
        "--collateral-switch",
        dest="collateral_switch",
        action="store_true",
        help="Toggle coin as collateral",
    )
    parser.add_argument(
        "--brick-size", dest="brick_size", type=float, help="Renko brick size"
    )

    args = parser.parse_args()

    # FIX: inject CLI config into singleton so run() uses it
    config = TradingConfig.from_file(args.config)
    _get_dispatcher(config)

    orders_data = None
    if getattr(args, "orders_file", None):
        with open(args.orders_file) as f:
            orders_data = json.load(f)

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
        fee_rate=getattr(args, "fee_rate", None),
        funding_rate=getattr(args, "funding_rate", None),
        holding_hours=getattr(args, "holding_hours", None),
        holding_periods=getattr(args, "holding_periods", None),
        total_usdt=getattr(args, "total_usdt", None),
        num_orders=getattr(args, "num_orders", None),
        num_grids=getattr(args, "num_grids", None),
        upper_price=getattr(args, "upper_price", None),
        lower_price=getattr(args, "lower_price", None),
        rr_ratio=getattr(args, "rr_ratio", None),
        atr_mult=getattr(args, "atr_mult", None),
        duration_minutes=getattr(args, "duration_minutes", None),
        num_slices=getattr(args, "num_slices", None),
        min_rate=getattr(args, "min_rate", None),
        top_n=getattr(args, "top_n", None),
        price_range_pct=getattr(args, "price_range_pct", None),
        coin=getattr(args, "coin", None),
        amount=getattr(args, "amount", None),
        from_account=getattr(args, "from_account", None),
        to_account=getattr(args, "to_account", None),
        risk_id=getattr(args, "risk_id", None),
        currency=getattr(args, "currency", None),
        depth=getattr(args, "depth", None),
        period=getattr(args, "period", None),
        num_levels=getattr(args, "num_levels", None),
        num_entries=getattr(args, "num_entries", None),
        num_exits=getattr(args, "num_exits", None),
        spacing_pct=getattr(args, "spacing_pct", None),
        tp_spacing_pct=getattr(args, "tp_spacing_pct", None),
        hedge_pct=getattr(args, "hedge_pct", None),
        dip_pct=getattr(args, "dip_pct", None),
        symbols=getattr(args, "symbols", None),
        risk_fraction=getattr(args, "risk_fraction", None),
        consecutive_wins=getattr(args, "consecutive_wins", None),
        consecutive_losses=getattr(args, "consecutive_losses", None),
        daily_return_pct=getattr(args, "daily_return_pct", None),
        days=getattr(args, "days", None),
        win_rate_pct=getattr(args, "win_rate_pct", None),
        trades_per_day=getattr(args, "trades_per_day", None),
        starting_capital=getattr(args, "starting_capital", None),
        maint_margin_rate=getattr(args, "maint_margin_rate", None),
        account_balance=getattr(args, "account_balance", None),
        margin=getattr(args, "margin", None),
        auto_add_margin=getattr(args, "auto_add_margin", None),
        tp_sl_mode=getattr(args, "tp_sl_mode", None),
        collateral_switch=getattr(args, "collateral_switch", None),
        brick_size=getattr(args, "brick_size", None),
    )

    output_path = args.output or os.environ.get("LLM_OUTPUT")
    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        logger.info("Result written to %s", output_path)
    else:
        print(json.dumps(result, indent=2))
