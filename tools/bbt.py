#!/usr/bin/env python3
# ==============================================================================
# bbt.py — Pyrmethus AIChat Bybit Realm Master Trading Engine v5.2.0-ASCENDED
# argc/aichat compatible · Full Bybit V5 REST & WS Integration · TA & Order Engine
#
# @describe Unified Bybit V5 Trading, Analysis, Indicator Observatory & Execution Suite
#
# @meta require-tools python3
#
# @option --action! <ENUM>               health_check, get_wallet_balance, get_account_info, get_positions, get_position_risk, get_fee_rate, set_leverage, set_trading_stop, set_position_mode, get_executions, get_pnl_history, panic_close, bulk_update_tp_sl, get_account_summary, get_pnl_summary, update_trailing_stop, set_tp_sl, check_risk_limit, check_balance, close_position, get_open_positions_summary, send_telegram_alert, export_trade_history, calculate_rsi, calculate_sma, calculate_ema, calculate_macd, calculate_bollinger_bands, calculate_vwap, calculate_atr, calculate_stoch, scan_scalping_opportunities, place_order, amend_order, cancel_order, cancel_all_orders, get_open_orders, get_order_history, batch_place_orders, place_smart_trade, get_ticker, get_orderbook, get_klines, get_recent_trades, get_instruments_info, get_funding_rate, get_open_interest, get_volatility_index, get_orderbook_analysis, get_volume_at_price, get_market_regime, scan_symbols, get_journal, calculate_support_resistance_levels, calculate_fibonacci_levels, calculate_volume_profile, calculate_orderflow_delta, calculate_market_depth_profile, detect_high_confluence_levels, deep_level_sort, calculate_sr_levels, generate_market_depth_report, calculate_limit_micro_profit, calculate_depth_weighted_profit, calculate_all_indicators, calculate_hma, calculate_fractals, calculate_pivot_points, calculate_klinger, calculate_cmf, calculate_adx_with_di, calculate_elder_ray_index, calculate_kst, calculate_tema, calculate_ehler_rsi, calculate_ehler_stochastic, calculate_vwma, calculate_bollinger_bands_pb, calculate_roc, calculate_mfi, calculate_williams_r, analyze_symbol, place_breakeven_order, get_asset_info, transfer_funds, get_transfer_history, set_margin_mode, get_borrow_history
# @option --symbol <TEXT>                Trading pair (e.g. BTCUSDT)
# @option --side <ENUM>                  Buy, Sell
# @option --qty <NUM>                    Order quantity
# @option --price <NUM>                  Order price
# @option --category <ENUM>              linear, spot, inverse (default: linear)
# @option --stop-loss <NUM>              Stop loss price
# @option --take-profit <NUM>            Take profit price
# @option --leverage <NUM>               Leverage value
# @option --interval <TEXT>              Kline interval: 1, 5, 15, 60, 240, D (default: 60)
# @option --limit <NUM>                  Result limit (default: 50)
# @flag   --json                         Output raw JSON format
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           BYBIT REALM v5.2.0 — Advanced Trading & Analysis Suite             ║
║                                                                              ║
║  Modules Integrated:                                                         ║
║  • bybit_core         : Low-Level Thread-Safe Engine & Failover Routing      ║
║  • bybit_smart_order  : Position Sizing & Risk Management Execution          ║
║  • bybit_wbta         : L2 Orderbook Intelligence & Technical Observatory     ║
║  • scientific_calc    : Math, Matrix, & Statistical Analytics Engine          ║
║  • proxy_utils        : Geo-IP Bypass & TOR Proxy Routing                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import csv
import enum
import hashlib
import hmac
import json
import logging
import math
import os
import re
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests
from dotenv import load_dotenv

# Add current directory to sys.path
CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# ── Module Imports with Fallback Handlers ─────────────────────────────────────
try:
    import proxy_utils

    proxy_utils.set_proxy_environment()
except ImportError:
    proxy_utils = None

try:
    import bybit_core
except ImportError:
    bybit_core = None

try:
    import bybit_smart_order
except ImportError:
    bybit_smart_order = None

try:
    import bybit_wbta
except ImportError:
    bybit_wbta = None

try:
    import scientific_calculator
except ImportError:
    scientific_calculator = None

load_dotenv(override=False)  # Never override shell-exported vars
dotenv_path = CURRENT_DIR.parent / ".env"
load_dotenv(dotenv_path=dotenv_path, override=False)  # Never override shell-exported vars

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BybitRealm")

__version__ = "5.2.0-ASCENDED"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: DECIMAL, PRECISION & JSON SERIALIZER HELPERS
# ══════════════════════════════════════════════════════════════════════════════


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Decimal, Path, Enum, datetime, timedelta, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


def format_precision(value: Any, step: Any, rounding=ROUND_HALF_UP) -> str:
    """Format a numeric value to an exact decimal step string without binary float drift."""
    if value is None or value == "":
        return ""
    if step is None or float(step) <= 0:
        return format(Decimal(str(value)), "f")
    try:
        val_d = Decimal(str(value))
        step_d = Decimal(str(step))
        quantized = val_d.quantize(step_d, rounding=rounding)
        return format(quantized, "f")
    except (InvalidOperation, ValueError):
        return str(value)


@dataclass
class LotSizeFilter:
    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> str:
        if self.qty_step <= 0:
            return format(Decimal(str(qty)), "f")
        step = Decimal(str(self.qty_step))
        q = Decimal(str(qty))
        adjusted = (q / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step
        final_qty = max(
            Decimal(str(self.min_order_qty)),
            min(Decimal(str(self.max_order_qty)), adjusted),
        )
        return format(final_qty, "f")


@dataclass
class PriceFilter:
    tick_size: float
    min_price: float = 0.0
    max_price: float = 1e12

    def adjust(self, price: float) -> str:
        if self.tick_size <= 0:
            return format(Decimal(str(price)), "f")
        tick = Decimal(str(self.tick_size))
        p = Decimal(str(price))
        adjusted = (p / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        final_price = max(
            Decimal(str(self.min_price)), min(Decimal(str(self.max_price)), adjusted)
        )
        return format(final_price, "f")


@dataclass
class InstrumentInfo:
    lot_size: LotSizeFilter
    price_flt: PriceFilter
    symbol: str
    status: str = "Trading"
    fetched_at: float = field(default_factory=time.time)


def _get_api_credentials() -> Tuple[str, str]:
    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if len(key) > len(secret) and len(key) > 25 and len(secret) < 25:
        key, secret = secret, key
    return key, secret

@dataclass
class TradingConfig:
    api_key: str = field(default_factory=lambda: _get_api_credentials()[0])
    api_secret: str = field(default_factory=lambda: _get_api_credentials()[1])
    testnet: bool = field(
        default_factory=lambda: (
            os.getenv("BYBIT_USE_TESTNET", "false").lower() in ("true", "1")
        )
    )
    use_proxy: bool = field(
        default_factory=lambda: (
            os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1")
        )
    )
    journal_path: str = field(
        default_factory=lambda: os.getenv("JOURNAL_PATH", "bybit_journal.json")
    )
    timeout: int = field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "15"))
    )
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    recv_window: int = 30000

    @property
    def base_url(self) -> str:
        return (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: TERMINAL COLOR PALETTE & UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def print_human_readable_ui(
    action: str, data: dict[str, Any], no_color: bool = False
) -> None:
    """Render a human-friendly colorized status box for interactive terminal runs."""
    if not _is_tty() or no_color:
        return

    status = data.get("status", "ok") if isinstance(data, dict) else "ok"
    is_success = status != "error"
    status_color = NEON_GREEN if is_success else NEON_RED
    status_symbol = "✓" if is_success else "✗"
    status_text = "SUCCESS" if is_success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BYBIT REALM v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}   {action}")

    if isinstance(data, dict):
        if "symbol" in data:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Symbol:{RESET}   {data['symbol']}"
            )
        if "msg" in data:
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Message:{RESET}  {data['msg']}")
        if "net_pnl" in data:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Net PnL:{RESET}  {NEON_YELLOW}${data['net_pnl']}{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: LONG VS SHORT LOGIC HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def calculate_pnl(
    side: str, entry_price: float, mark_price: float, qty: float
) -> tuple[float, float]:
    """Calculate unrealized PnL ($) and PnL (%) for Long vs Short positions."""
    side_clean = side.capitalize()
    if side_clean == "Buy":
        pnl_usd = (mark_price - entry_price) * qty
    else:
        pnl_usd = (entry_price - mark_price) * qty

    cost_usd = entry_price * qty
    pnl_pct = (pnl_usd / cost_usd * 100.0) if cost_usd > 0 else 0.0
    return round(pnl_usd, 4), round(pnl_pct, 3)


def calculate_liquidation_distance(
    side: str, mark_price: float, liq_price: float
) -> Tuple[Optional[float], str]:
    """Calculate distance (%) to liquidation price and assign position heat level."""
    if liq_price <= 0 or mark_price <= 0:
        return None, "UNKNOWN"

    side_clean = side.capitalize()
    if side_clean == "Buy":
        dist_pct = (mark_price - liq_price) / mark_price * 100.0
    else:
        dist_pct = (liq_price - mark_price) / mark_price * 100.0

    if dist_pct <= 0 or dist_pct < 3.0:
        heat = "CRITICAL"
    elif dist_pct < 8.0:
        heat = "HIGH"
    elif dist_pct < 20.0:
        heat = "MEDIUM"
    else:
        heat = "LOW"

    return round(dist_pct, 3), heat


def calculate_breakeven_price(
    side: str, entry_price: float, fee_rate: float = 0.00055
) -> float:
    """Calculate breakeven exit price covering entry and exit trading fees."""
    side_clean = side.capitalize()
    if side_clean == "Buy":
        return entry_price * (1.0 + 2.0 * fee_rate)
    else:
        return entry_price * (1.0 - 2.0 * fee_rate)


def validate_tp_sl(
    side: str,
    entry_price: float,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
) -> Optional[str]:
    """Validate TP and SL price boundaries based on Long vs Short direction."""
    side_clean = side.capitalize()
    if side_clean == "Buy":
        if tp is not None and tp <= entry_price:
            return (
                f"Invalid Long TP ({tp}): Take Profit must be > Entry ({entry_price})."
            )
        if sl is not None and sl >= entry_price:
            return f"Invalid Long SL ({sl}): Stop Loss must be < Entry ({entry_price})."
    else:
        if tp is not None and tp >= entry_price:
            return (
                f"Invalid Short TP ({tp}): Take Profit must be < Entry ({entry_price})."
            )
        if sl is not None and sl <= entry_price:
            return (
                f"Invalid Short SL ({sl}): Stop Loss must be > Entry ({entry_price})."
            )
    return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: RATE LIMITER & TRADE JOURNAL
# ══════════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """Thread-safe Token Bucket Rate Limiter using monotonic time."""

    def __init__(self, capacity: int = 20, refill_per_ms: float = 0.02):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_per_ms = refill_per_ms
        self.last_check = time.monotonic() * 1000
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic() * 1000
            delta = now - self.last_check
            self.tokens = min(
                float(self.capacity), self.tokens + delta * self.refill_per_ms
            )
            self.last_check = now
            if self.tokens < 1.0:
                sleep_time = (1.0 - self.tokens) / self.refill_per_ms
                time.sleep(sleep_time / 1000.0)
                self.tokens = 1.0
            self.tokens -= 1.0


class TradeJournal:
    """Persistent JSON trade journal with thread-safe recording."""

    def __init__(self, path: str = "bybit_journal.json"):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._entries: List[dict] = self._load()

    def _load(self) -> List[dict]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def _save(self):
        try:
            self._path.write_text(
                json.dumps(
                    self._entries, indent=2, ensure_ascii=False, cls=ToolJSONEncoder
                ),
                encoding="utf-8",
            )
        except OSError as err:
            logger.warning(f"Failed to write trade journal: {err}")

    def record(
        self, action: str, payload: dict, result: dict, symbol: Optional[str] = None
    ) -> str:
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "symbol": symbol or payload.get("symbol", "N/A"),
            "payload": payload,
            "result": result,
            "status": "success" if result.get("status") != "error" else "failed",
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
        return entry["id"]

    def get_entries(self, symbol: Optional[str] = None, limit: int = 50) -> List[dict]:
        with self._lock:
            entries = self._entries
            if symbol:
                entries = [
                    e for e in entries if e.get("symbol", "").upper() == symbol.upper()
                ]
            return entries[-limit:]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: MAIN BYBIT REALM CLIENT IMPLEMENTATION
# ══════════════════════════════════════════════════════════════════════════════


class BybitRealm:
    """Master Bybit V5 Trading, Market Observatory & Analysis Engine."""

    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        if proxy_utils and self.config.use_proxy:
            self.session.proxies = proxy_utils.get_proxies()
        elif os.getenv("BYBIT_TOR_PROXY") or os.getenv("BYBIT_PROXY_URL") or os.getenv("PROXY_URL"):
            p_url = os.getenv("BYBIT_TOR_PROXY") or os.getenv("BYBIT_PROXY_URL") or os.getenv("PROXY_URL")
            self.session.proxies = {"http": p_url, "https": p_url}

        self._limiter = RateLimiter()
        self.journal = TradeJournal(self.config.journal_path)
        self._cache_lock = threading.Lock()
        self._instr_cache: Dict[str, InstrumentInfo] = {}

    def _sign(self, ts: str, payload: str = "") -> str:
        msg = f"{ts}{self.config.api_key}{self.config.recv_window}{payload}"
        return hmac.new(
            self.config.api_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _build_query_string(self, params: dict) -> str:
        if not params:
            return ""
        clean_params = {k: str(v) for k, v in params.items() if v is not None}
        return "&".join(f"{k}={v}" for k, v in sorted(clean_params.items()))

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        if bybit_core and hasattr(bybit_core, "api_request"):
            try:
                return bybit_core.api_request(
                    method=method,
                    endpoint=endpoint,
                    params=params or json_data,
                    signed=signed,
                )
            except Exception as err:
                logger.debug(f"bybit_core delegation failed: {err}")

        self._limiter.acquire()
        ts = str(int(time.time() * 1000))
        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
            "Content-Type": "application/json",
        }
        url = self.config.base_url + endpoint
        sign_payload = ""

        if signed:
            if method.upper() == "GET":
                sign_payload = self._build_query_string(params or {})
                if sign_payload:
                    url = f"{url}?{sign_payload}"
                params = None
            else:
                sign_payload = (
                    json.dumps(json_data, sort_keys=True) if json_data else ""
                )
            headers["X-BAPI-SIGN"] = self._sign(ts, sign_payload)

        try:
            resp = self.session.request(
                method,
                url,
                params=params,
                data=sign_payload if (signed and method.upper() == "POST") else None,
                json=json_data if not (signed and method.upper() == "POST") else None,
                headers=headers,
                timeout=self.config.timeout,
            )
            if resp.status_code != 200:
                return {"status": "error", "code": resp.status_code, "msg": resp.text}

            data = resp.json()
            if data.get("retCode") == 0:
                return data.get("result", data)
            return {
                "status": "error",
                "code": data.get("retCode"),
                "msg": data.get("retMsg"),
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}

    # ── Instrument Precision Fetcher ─────────────────────────────────────────
    def _fetch_instrument(
        self, symbol: str, category: str = "linear"
    ) -> InstrumentInfo:
        cache_key = f"{symbol}_{category}"
        with self._cache_lock:
            if (
                cache_key in self._instr_cache
                and time.time() - self._instr_cache[cache_key].fetched_at < 3600
            ):
                return self._instr_cache[cache_key]

        res = self._request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol.upper()},
            signed=False,
        )
        item = res.get("list", [{}])[0] if isinstance(res, dict) else {}
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
            symbol=symbol.upper(),
        )
        with self._cache_lock:
            self._instr_cache[cache_key] = info
        return info

    def adjust_qty(self, symbol: str, qty: float, category: str = "linear") -> str:
        return self._fetch_instrument(symbol, category).lot_size.adjust(qty)

    def adjust_price(self, symbol: str, price: float, category: str = "linear") -> str:
        return self._fetch_instrument(symbol, category).price_flt.adjust(price)

    # ── Account & Public Endpoints ───────────────────────────────────────────
    def health_check(self) -> dict:
        return self._request("GET", "/v5/market/time", signed=False)

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        return self._request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": account_type},
            signed=True,
        )

    def get_account_info(self) -> dict:
        return self._request("GET", "/v5/account/info", params={}, signed=True)

    def get_fee_rate(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        params: dict = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/account/fee-rate", params=params, signed=True)

    def get_positions(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        settle_coin: Optional[str] = None,
    ) -> dict:
        params: dict = {"category": category}
        if symbol:
            params["symbol"] = symbol.upper()
        if category == "linear" and not settle_coin:
            params["settleCoin"] = "USDT"
        return self._request("GET", "/v5/position/list", params=params, signed=True)

    def get_position_risk(
        self, category: str = "linear", symbol: Optional[str] = None
    ) -> dict:
        raw = self.get_positions(category=category, symbol=symbol)
        positions = raw.get("list", []) if isinstance(raw, dict) else []

        enriched: List[dict] = []
        for pos in positions:
            size = float(pos.get("size", 0))
            if size == 0:
                continue

            side = pos.get("side", "Buy")
            entry = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
            liq = float(pos.get("liqPrice", 0))
            mark = float(pos.get("markPrice", 0))
            notional = size * mark
            pnl_usd, pnl_pct = calculate_pnl(side, entry, mark, size)
            liq_dist_pct, heat = calculate_liquidation_distance(side, mark, liq)

            enriched.append(
                {
                    **pos,
                    "notional_usd": round(notional, 2),
                    "calculated_pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "liq_dist_pct": liq_dist_pct,
                    "position_heat": heat,
                }
            )

        return {
            "status": "ok",
            "category": category,
            "positions": enriched,
            "total_positions": len(enriched),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def set_leverage(
        self, symbol: str, leverage: int, category: str = "linear"
    ) -> dict:
        return self._request(
            "POST",
            "/v5/position/set-leverage",
            json_data={
                "category": category,
                "symbol": symbol.upper(),
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
            signed=True,
        )

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {"category": category, "symbol": symbol.upper()}
        if stop_loss is not None:
            payload["stopLoss"] = self.adjust_price(symbol, stop_loss, category)
        if take_profit is not None:
            payload["takeProfit"] = self.adjust_price(symbol, take_profit, category)
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        return self._request(
            "POST", "/v5/position/trading-stop", json_data=payload, signed=True
        )

    def set_position_mode(
        self, coin: str = "USDT", mode: int = 0, category: str = "linear"
    ) -> dict:
        return self._request(
            "POST",
            "/v5/position/switch-mode",
            json_data={"category": category, "coin": coin.upper(), "mode": mode},
            signed=True,
        )

    def get_asset_info(self, account_type: Optional[str] = None, coin: Optional[str] = None) -> dict:
        params = {}
        if account_type:
            params["accountType"] = account_type
        if coin:
            params["coin"] = coin.upper()
        return self._request("GET", "/v5/asset/transfer/query-asset-info", params=params, signed=True)

    def transfer_funds(self, coin: str, amount: float, from_account: str, to_account: str) -> dict:
        payload = {
            "transferId": str(uuid.uuid4()),
            "coin": coin.upper(),
            "amount": str(amount),
            "fromAccountType": from_account,
            "toAccountType": to_account,
        }
        return self._request("POST", "/v5/asset/transfer/inter-transfer", json_data=payload, signed=True)

    def get_transfer_history(self, coin: Optional[str] = None, limit: int = 50) -> dict:
        params: dict = {"limit": limit}
        if coin:
            params["coin"] = coin.upper()
        return self._request("GET", "/v5/asset/transfer/query-inter-transfer-list", params=params, signed=True)

    def set_margin_mode(self, set_margin_mode: str = "REGULAR_MARGIN") -> dict:
        payload = {"setMarginMode": set_margin_mode}
        return self._request("POST", "/v5/account/set-margin-mode", json_data=payload, signed=True)

    def get_borrow_history(self, currency: Optional[str] = None, limit: int = 50) -> dict:
        params: dict = {"limit": limit}
        if currency:
            params["currency"] = currency.upper()
        return self._request("GET", "/v5/account/borrow-history", params=params, signed=True)

    def get_executions(
        self, category: str = "linear", symbol: Optional[str] = None, limit: int = 50
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/execution/list", params=params, signed=True)

    def get_pnl_history(
        self, category: str = "linear", symbol: Optional[str] = None, limit: int = 50
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/position/closed-pnl", params=params, signed=True
        )

    # ── Orders & Execution ───────────────────────────────────────────────────
    def place_order(
        self,
        symbol: str,
        side: Literal["Buy", "Sell"],
        qty: float,
        price: Optional[float] = None,
        order_type: str = "Limit",
        category: str = "linear",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        **kwargs,
    ) -> dict:
        if price:
            err = validate_tp_sl(side, float(price), take_profit, stop_loss)
            if err:
                return {"status": "error", "msg": err}

        adj_qty = self.adjust_qty(symbol, qty, category)
        payload: dict = {
            "category": category,
            "symbol": symbol.upper(),
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": adj_qty,
            "timeInForce": kwargs.get("time_in_force", "GTC"),
        }
        if price is not None:
            payload["price"] = self.adjust_price(symbol, price, category)
        if stop_loss is not None:
            payload["stopLoss"] = self.adjust_price(symbol, stop_loss, category)
        if take_profit is not None:
            payload["takeProfit"] = self.adjust_price(symbol, take_profit, category)

        for k in (
            "reduceOnly",
            "orderLinkId",
            "triggerPrice",
            "triggerBy",
            "positionIdx",
        ):
            if k in kwargs and kwargs[k] is not None:
                payload[k] = kwargs[k]

        result = self._request(
            "POST", "/v5/order/create", json_data=payload, signed=True
        )
        self.journal.record("place_order", payload, result, symbol=symbol.upper())
        return result

    def amend_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {"category": category, "symbol": symbol.upper()}
        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
        if qty is not None:
            payload["qty"] = self.adjust_qty(symbol, qty, category)
        if price is not None:
            payload["price"] = self.adjust_price(symbol, price, category)
        if stop_loss is not None:
            payload["stopLoss"] = self.adjust_price(symbol, stop_loss, category)
        if take_profit is not None:
            payload["takeProfit"] = self.adjust_price(symbol, take_profit, category)

        return self._request("POST", "/v5/order/amend", json_data=payload, signed=True)

    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
        category: str = "linear",
    ) -> dict:
        payload: dict = {"category": category, "symbol": symbol.upper()}
        if order_id:
            payload["orderId"] = order_id
        elif client_oid:
            payload["orderLinkId"] = client_oid
        return self._request("POST", "/v5/order/cancel", json_data=payload, signed=True)

    def cancel_all_orders(
        self, symbol: Optional[str] = None, category: str = "linear"
    ) -> dict:
        payload: dict = {"category": category, "settleCoin": "USDT"}
        if symbol:
            payload["symbol"] = symbol.upper()
        return self._request(
            "POST", "/v5/order/cancel-all", json_data=payload, signed=True
        )

    def get_open_orders(
        self, symbol: Optional[str] = None, category: str = "linear", limit: int = 50
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/order/realtime", params=params, signed=True)

    def get_order_history(
        self, symbol: Optional[str] = None, category: str = "linear", limit: int = 50
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request("GET", "/v5/order/history", params=params, signed=True)

    def batch_place_orders(self, orders: List[dict], category: str = "linear") -> dict:
        formatted: List[dict] = []
        for o in orders[:10]:
            sym = o["symbol"].upper()
            item = {
                "symbol": sym,
                "side": o["side"].capitalize(),
                "orderType": o.get("order_type", "Limit").capitalize(),
                "qty": self.adjust_qty(sym, o["qty"], category),
                "timeInForce": o.get("time_in_force", "GTC"),
            }
            if o.get("price") is not None:
                item["price"] = self.adjust_price(sym, o["price"], category)
            formatted.append(item)

        payload = {"category": category, "request": formatted}
        return self._request(
            "POST", "/v5/order/create-batch", json_data=payload, signed=True
        )

    def close_position(self, symbol: str, category: str = "linear") -> dict:
        pos_data = self.get_positions(category=category, symbol=symbol)
        positions = pos_data.get("list", []) if isinstance(pos_data, dict) else []

        for pos in positions:
            size = float(pos.get("size", 0))
            if size > 0:
                pos_side = pos.get("side", "Buy").capitalize()
                close_side = "Sell" if pos_side == "Buy" else "Buy"
                pos_idx = int(pos.get("positionIdx", 0))
                return self.place_order(
                    symbol=symbol,
                    side=close_side,
                    qty=size,
                    order_type="Market",
                    reduceOnly=True,
                    positionIdx=pos_idx,
                    category=category,
                )
        return {"status": "error", "msg": f"No open position found for {symbol}"}

    def panic_close(self, category: str = "linear") -> dict:
        cancel_res = self.cancel_all_orders(category=category)
        positions = self.get_positions(category=category).get("list", [])
        closures = []
        for pos in positions:
            size = float(pos.get("size", 0))
            if size > 0:
                pos_side = pos.get("side", "Buy").capitalize()
                close_side = "Sell" if pos_side == "Buy" else "Buy"
                pos_idx = int(pos.get("positionIdx", 0))
                closures.append(
                    self.place_order(
                        symbol=pos["symbol"],
                        side=close_side,
                        qty=size,
                        order_type="Market",
                        reduceOnly=True,
                        positionIdx=pos_idx,
                        category=category,
                    )
                )
        return {"status": "ok", "cancellations": cancel_res, "closures": closures}

    def bulk_update_tp_sl(
        self,
        category: str = "linear",
        tp: Optional[float] = None,
        sl: Optional[float] = None,
    ) -> dict:
        positions = self.get_positions(category=category).get("list", [])
        updates = []
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                updates.append(
                    self.set_trading_stop(
                        symbol=pos["symbol"],
                        take_profit=tp,
                        stop_loss=sl,
                        category=category,
                    )
                )
        return {"status": "ok", "updates": updates}

    def set_tp_sl(
        self,
        symbol: str,
        tp: Optional[float] = None,
        sl: Optional[float] = None,
        category: str = "linear",
    ) -> dict:
        return self.set_trading_stop(
            symbol=symbol, take_profit=tp, stop_loss=sl, category=category
        )

    def place_breakeven_order(
        self, symbol: str, fee_rate: float = 0.00055, category: str = "linear"
    ) -> dict:
        pos_data = self.get_positions(symbol=symbol, category=category)
        positions = pos_data.get("list", []) if isinstance(pos_data, dict) else []
        if not positions:
            return {"status": "error", "msg": f"No open position found for {symbol}"}

        pos = positions[0]
        size = float(pos.get("size", 0))
        if size == 0:
            return {"status": "error", "msg": f"Position size is 0 for {symbol}"}

        entry_price = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
        pos_side = pos.get("side", "Buy").capitalize()
        pos_idx = int(pos.get("positionIdx", 0))

        be_price = calculate_breakeven_price(pos_side, entry_price, fee_rate)
        close_side = "Sell" if pos_side == "Buy" else "Buy"

        return self.place_order(
            symbol=symbol,
            side=close_side,
            qty=size,
            price=be_price,
            order_type="Limit",
            reduceOnly=True,
            positionIdx=pos_idx,
            category=category,
        )

    # ── Market Data ──────────────────────────────────────────────────────────
    def get_ticker(self, symbol: str, category: str = "linear") -> dict:
        return self._request(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol.upper()},
            signed=False,
        )

    def get_orderbook(
        self, symbol: str, limit: int = 25, category: str = "linear"
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "60",
        limit: int = 200,
        category: str = "linear",
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/kline",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
            signed=False,
        )

    def get_recent_trades(
        self, symbol: str, limit: int = 100, category: str = "linear"
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/recent-trade",
            params={"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
        )

    def get_instruments_info(
        self, category: str = "linear", symbol: Optional[str] = None, limit: int = 100
    ) -> dict:
        params: dict = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol.upper()
        return self._request(
            "GET", "/v5/market/instruments-info", params=params, signed=False
        )

    def get_funding_rate(
        self, symbol: str, category: str = "linear", limit: int = 10
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/funding/history",
            params={"category": category, "symbol": symbol.upper(), "limit": limit},
            signed=False,
        )

    def get_open_interest(
        self,
        symbol: str,
        interval: str = "1h",
        category: str = "linear",
        limit: int = 50,
    ) -> dict:
        return self._request(
            "GET",
            "/v5/market/open-interest",
            params={
                "category": category,
                "symbol": symbol.upper(),
                "intervalTime": interval,
                "limit": limit,
            },
            signed=False,
        )

    def get_volatility_index(
        self, category: str = "option", period: Optional[int] = None
    ) -> dict:
        params: dict = {"category": category}
        if period:
            params["period"] = period
        return self._request(
            "GET", "/v5/market/historical-volatility", params=params, signed=False
        )

    # ── Modular Sub-Tool Delegations ─────────────────────────────────────────
    def place_smart_trade(self, **kwargs) -> dict:
        if not bybit_smart_order:
            return {
                "status": "error",
                "msg": "bybit_smart_order sub-tool is unavailable.",
            }
        return bybit_smart_order.run(**kwargs)

    def get_market_regime(
        self,
        symbol: str,
        interval: str = "60",
        lookback: int = 100,
        category: str = "linear",
    ) -> dict:
        klines_data = self.get_klines(
            symbol=symbol, interval=interval, limit=lookback, category=category
        )
        klines = klines_data.get("list", []) if isinstance(klines_data, dict) else []
        if len(klines) < 20:
            return {"status": "error", "msg": "Insufficient kline data"}

        closes = [float(k[4]) for k in reversed(klines)]
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]

        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))
        ]
        volatility = statistics.stdev(returns) * 100 if len(returns) > 1 else 0.0

        def _ema(data: List[float], period: int) -> float:
            k = 2 / (period + 1)
            ema = data[0]
            for v in data[1:]:
                ema = v * k + ema * (1 - k)
            return ema

        ema_short = _ema(closes, 10)
        ema_long = _ema(closes, 30)

        trs: List[float] = [
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            for i in range(1, len(closes))
        ]
        avg_tr = statistics.mean(trs[-14:]) if trs else 0
        tr_ratio = (avg_tr / closes[-1] * 100) if closes[-1] > 0 else 0.0

        trending_up = ema_short > ema_long * 1.001 and tr_ratio > 0.4
        trending_down = ema_short < ema_long * 0.999 and tr_ratio > 0.4
        high_vol = volatility > 2.5

        if high_vol and not (trending_up or trending_down):
            regime = "VOLATILE"
        elif trending_up:
            regime = "TRENDING_UP"
        elif trending_down:
            regime = "TRENDING_DOWN"
        else:
            regime = "RANGING"

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "interval": interval,
            "regime": regime,
            "metrics": {
                "ema_short": round(ema_short, 6),
                "ema_long": round(ema_long, 6),
                "ema_cross_pct": round((ema_short - ema_long) / ema_long * 100, 4)
                if ema_long
                else 0.0,
                "volatility_pct": round(volatility, 4),
                "atr_pct": round(tr_ratio, 4),
                "last_close": closes[-1],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_orderbook_analysis(
        self,
        symbol: str,
        category: str = "linear",
        depth: int = 50,
        wall_multiplier: float = 3.5,
    ) -> dict:
        raw = self.get_orderbook(symbol=symbol, category=category, limit=depth)
        ob_data = raw.get("result", raw) if isinstance(raw, dict) else {}

        bids: List[Tuple[float, float]] = [
            (float(p), float(q)) for p, q in ob_data.get("b", [])
        ]
        asks: List[Tuple[float, float]] = [
            (float(p), float(q)) for p, q in ob_data.get("a", [])
        ]

        if not bids or not asks:
            return {"status": "error", "msg": "Empty orderbook"}

        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0

        bid_avg = bid_vol / depth if depth > 0 else 0
        ask_avg = ask_vol / depth if depth > 0 else 0
        bid_walls = [b for b in bids if b[1] > bid_avg * wall_multiplier]
        ask_walls = [a for a in asks if a[1] > ask_avg * wall_multiplier]

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "obi": round(obi, 4),
            "bid_vol": round(bid_vol, 2),
            "ask_vol": round(ask_vol, 2),
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "volume_profile": {
                "bid_tiers": [{"price": b[0], "volume": b[1]} for b in bids[:5]],
                "ask_tiers": [{"price": a[0], "volume": a[1]} for a in asks[:5]],
            },
        }

    def scan_scalping_opportunities(self, symbol: str, interval: str = "15") -> dict:
        rsi = self.calculate_rsi(symbol=symbol, interval=interval).get("rsi", 50)
        ema20 = self.calculate_ema(symbol=symbol, interval=interval, period=20).get(
            "ema", 0
        )
        bb = self.calculate_bollinger_bands(symbol=symbol, interval=interval).get(
            "lower", 0
        )
        vwap = self.calculate_vwap(symbol=symbol, interval=interval).get("vwap", 0)
        atr = self.calculate_atr(symbol=symbol, interval=interval).get("atr", 0)
        stoch = self.calculate_stochastic(symbol=symbol, interval=interval).get("k", 50)

        ticker_raw = self.get_ticker(symbol=symbol)
        t_list = ticker_raw.get("list", [{}]) if isinstance(ticker_raw, dict) else [{}]
        price = float(t_list[0].get("lastPrice", 0))

        signal_str = "NEUTRAL"
        if price < bb and rsi < 35 and stoch < 20 and price > vwap:
            signal_str = "BUY_REVERSION"
        elif price > ema20 and rsi < 45 and stoch > 20:
            signal_str = "BUY_TREND"
        elif price > bb and rsi > 65 and stoch > 80 and price < vwap:
            signal_str = "SELL_REVERSION"

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "signal": signal_str,
            "price": price,
            "rsi": rsi,
            "ema20": ema20,
            "bb_lower": bb,
            "vwap": vwap,
            "stoch_k": stoch,
            "suggested_stop_dist": round(atr * 2, 4) if atr else 0.0,
        }

    # ── Advanced Orderbook & Level Observatory ────────────────────────────────
    def calculate_support_resistance_levels(
        self,
        symbol: str,
        interval: str = "60",
        depth: int = 50,
        wall_multiplier: float = 3.0,
    ) -> dict:
        ob_analysis = self.get_orderbook_analysis(
            symbol=symbol, depth=depth, wall_multiplier=wall_multiplier
        )
        walls_sup = (
            [b[0] for b in ob_analysis.get("bid_walls", [])]
            if isinstance(ob_analysis, dict)
            else []
        )
        walls_res = (
            [a[0] for a in ob_analysis.get("ask_walls", [])]
            if isinstance(ob_analysis, dict)
            else []
        )

        klines = self.get_klines(symbol=symbol, interval=interval, limit=100).get(
            "list", []
        )
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]

        swing_highs = (
            [
                highs[i]
                for i in range(1, len(highs) - 1)
                if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]
            ]
            if len(highs) > 2
            else []
        )
        swing_lows = (
            [
                lows[i]
                for i in range(1, len(lows) - 1)
                if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]
            ]
            if len(lows) > 2
            else []
        )

        pivot_data = self.calculate_pivot_points(symbol=symbol, interval=interval)
        pivot = pivot_data.get("pivot", 0.0)
        r1 = pivot_data.get("r1", 0.0)
        s1 = pivot_data.get("s1", 0.0)

        vp_data = self.calculate_volume_profile(symbol=symbol, interval=interval)
        poc = vp_data.get("poc", 0.0) if isinstance(vp_data, dict) else 0.0

        if r1:
            swing_highs.append(r1)
        if poc:
            swing_highs.append(poc)
        if s1:
            swing_lows.append(s1)
        if poc:
            swing_lows.append(poc)

        def get_confluence(levels, historical_points, tolerance=0.005):
            confluent = []
            for lvl in levels:
                score = 0
                for pt in historical_points:
                    if pt > 0 and abs(lvl - pt) / pt < tolerance:
                        score += 1
                confluent.append({"price": round(lvl, 4), "confluence": score})
            return sorted(confluent, key=lambda x: x["confluence"], reverse=True)

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "support": get_confluence(walls_sup or swing_lows[:5], swing_lows),
            "resistance": get_confluence(walls_res or swing_highs[:5], swing_highs),
            "pivots": {"pivot": pivot, "r1": r1, "s1": s1},
            "poc": poc,
        }

    def calculate_fibonacci_retracement(
        self, symbol: str, interval: str = "60", lookback: int = 50
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=lookback).get(
            "list", []
        )
        if not klines:
            return {"status": "error", "msg": "No kline data available"}
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        high_price, low_price = max(highs), min(lows)
        diff = high_price - low_price
        levels = {
            "0.0%": high_price,
            "23.6%": high_price - 0.236 * diff,
            "38.2%": high_price - 0.382 * diff,
            "50.0%": high_price - 0.5 * diff,
            "61.8%": high_price - 0.618 * diff,
            "78.6%": high_price - 0.786 * diff,
            "100.0%": low_price,
        }
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "levels": {k: round(v, 4) for k, v in levels.items()},
        }

    def calculate_fibonacci_levels(
        self, symbol: str, interval: str = "60", lookback: int = 50
    ) -> dict:
        return self.calculate_fibonacci_retracement(
            symbol=symbol, interval=interval, lookback=lookback
        )

    def calculate_volume_profile(
        self, symbol: str, interval: str = "60", limit: int = 100, price_bins: int = 20
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit).get(
            "list", []
        )
        if not klines:
            return {"status": "error", "msg": "No kline data"}

        data = [
            {"h": float(k[2]), "l": float(k[3]), "v": float(k[5])}
            for k in reversed(klines)
        ]
        prices = [d["h"] for d in data] + [d["l"] for d in data]
        min_p, max_p = min(prices), max(prices)
        bin_size = (max_p - min_p) / price_bins if price_bins > 0 else 1.0
        profile = dict.fromkeys(range(price_bins), 0.0)

        for d in data:
            avg_p = (d["h"] + d["l"]) / 2
            idx = int((avg_p - min_p) / bin_size) if bin_size > 0 else 0
            idx = min(max(idx, 0), price_bins - 1)
            profile[idx] += d["v"]

        poc_idx = max(profile, key=profile.get)
        poc_price = min_p + (poc_idx * bin_size) + (bin_size / 2)

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "poc": round(poc_price, 4),
            "profile": {
                round(min_p + i * bin_size, 4): round(vol, 2)
                for i, vol in profile.items()
            },
        }

    def calculate_orderflow_delta(self, symbol: str, limit: int = 100) -> dict:
        trades_res = self.get_recent_trades(symbol=symbol, limit=limit)
        trades = trades_res.get("list", []) if isinstance(trades_res, dict) else []
        delta = 0.0
        for t in trades:
            vol = float(t.get("v", 0) or t.get("size", 0))
            side = t.get("S") or t.get("side", "Buy")
            if side == "Buy":
                delta += vol
            else:
                delta -= vol
        return {"status": "ok", "symbol": symbol.upper(), "delta": round(delta, 2)}

    def calculate_market_depth_profile(
        self,
        symbol: str,
        depth: int = 200,
        order_sizes: Optional[List[float]] = None,
        distance_pcts: Optional[List[float]] = None,
    ) -> dict:
        if distance_pcts is None:
            distance_pcts = [0.1, 0.5, 1.0]

        ob_res = self.get_orderbook(symbol=symbol, limit=depth)
        ob = ob_res.get("result", ob_res) if isinstance(ob_res, dict) else {}
        bids = [{"p": float(p), "v": float(q)} for p, q in ob.get("b", [])]
        asks = [{"p": float(p), "v": float(q)} for p, q in ob.get("a", [])]

        mid = (bids[0]["p"] + asks[0]["p"]) / 2 if bids and asks else 0
        if mid == 0:
            return {"status": "error", "msg": "Invalid market depth"}

        profile = {}
        for pct in distance_pcts:
            bid_vol = sum(b["v"] for b in bids if b["p"] >= mid * (1 - pct / 100))
            ask_vol = sum(a["v"] for a in asks if a["p"] <= mid * (1 + pct / 100))
            profile[f"{pct}%"] = {
                "bid_vol": round(bid_vol, 2),
                "ask_vol": round(ask_vol, 2),
            }

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "mid_price": mid,
            "profile": profile,
        }

    def detect_high_confluence_levels(
        self, symbol: str, interval: str = "60", depth: int = 50
    ) -> dict:
        sr_data = self.calculate_support_resistance_levels(
            symbol=symbol, interval=interval, depth=depth
        )
        all_levels = []
        for s in sr_data.get("support", []):
            all_levels.append(
                {"price": s["price"], "score": s["confluence"], "type": "Support"}
            )
        for r in sr_data.get("resistance", []):
            all_levels.append(
                {"price": r["price"], "score": r["confluence"], "type": "Resistance"}
            )

        confluence_zones = sorted(all_levels, key=lambda x: x["score"], reverse=True)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "high_confluence_zones": confluence_zones[:5],
        }

    def deep_level_sort(
        self, symbol: str, level_cnt: int = 10, vol_thresh: float = 0.5
    ) -> dict:
        ob_res = self.get_orderbook(symbol=symbol)
        orderbook = ob_res.get("result", ob_res) if isinstance(ob_res, dict) else {}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]

        def bucket(prices, is_bid):
            sorted_items = sorted(prices, key=lambda x: x[0], reverse=is_bid)
            buckets = []
            cum_vol = 0.0
            price_sum = 0.0
            count = 0
            for price, qty in sorted_items:
                cum_vol += qty
                price_sum += price
                count += 1
                if cum_vol >= vol_thresh:
                    buckets.append([round(price_sum / count, 4), round(cum_vol, 2)])
                    cum_vol = 0.0
                    price_sum = 0.0
                    count = 0
            return buckets

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "bid_levels": bucket(bids, True)[:level_cnt],
            "ask_levels": bucket(asks, False)[:level_cnt],
        }

    def calculate_sr_levels(
        self, symbol: str, top_n: int = 7, vol_cut: float = 0.4
    ) -> dict:
        ob_res = self.get_orderbook(symbol=symbol)
        orderbook = ob_res.get("result", ob_res) if isinstance(ob_res, dict) else {}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]

        def find_sr(levels: list, direction: int) -> list:
            levels = sorted(levels, key=lambda x: x[0], reverse=(direction > 0))
            vol_acc = 0.0
            zones = []
            for price, qty in levels:
                vol_acc += qty
                if vol_acc >= vol_cut:
                    zones.append(price)
                    vol_acc = 0.0
            return zones

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "support_levels": find_sr(bids, 1)[:top_n],
            "resistance_levels": find_sr(asks, -1)[:top_n],
        }

    def generate_market_depth_report(self, symbol: str) -> dict:
        ob_analysis = self.get_orderbook_analysis(symbol=symbol, depth=100)
        sr_levels = self.calculate_sr_levels(symbol=symbol)
        profile = self.calculate_market_depth_profile(symbol=symbol)

        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "orderbook_analysis": ob_analysis,
            "support_resistance": sr_levels,
            "depth_profile": profile,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def calculate_limit_micro_profit(
        self,
        entry_price: float,
        limit_price: float,
        side: str,
        qty: float,
        fee_rate: float = 0.0006,
    ) -> dict:
        gross_pnl, pnl_pct = calculate_pnl(side, entry_price, limit_price, qty)
        fee_est = limit_price * qty * fee_rate
        net_pnl = gross_pnl - fee_est

        return {
            "status": "ok",
            "side": side,
            "entry_price": entry_price,
            "exit_price": limit_price,
            "qty": qty,
            "gross_pnl": round(gross_pnl, 4),
            "fee_estimated": round(fee_est, 4),
            "net_pnl": round(net_pnl, 4),
            "pnl_pct": pnl_pct,
        }

    def calculate_depth_weighted_profit(
        self, symbol: str, entry_price: float, limit_price: float, side: str, qty: float
    ) -> dict:
        ob_res = self.get_orderbook(symbol=symbol)
        orderbook = ob_res.get("result", ob_res) if isinstance(ob_res, dict) else {}
        bids = [[float(p), float(q)] for p, q in orderbook.get("b", [])]
        asks = [[float(p), float(q)] for p, q in orderbook.get("a", [])]
        levels = asks if side.lower() == "buy" else bids

        total_vol, weighted_sum = 0.0, 0.0
        for p, q in levels:
            if (side.lower() == "buy" and p <= limit_price) or (
                side.lower() == "sell" and p >= limit_price
            ):
                take = min(q, qty - total_vol)
                weighted_sum += p * take
                total_vol += take
                if total_vol >= qty:
                    break

        if total_vol < qty:
            return {
                "status": "error",
                "msg": "Insufficient orderbook depth for requested qty",
            }
        fill_price = weighted_sum / total_vol
        return self.calculate_limit_micro_profit(entry_price, fill_price, side, qty)

    # ── Technical Indicators Observatory ─────────────────────────────────────
    def calculate_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period + 1:
            return {"status": "error", "msg": "Insufficient data"}

        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return {"status": "ok", "symbol": symbol.upper(), "rsi": 100.0}

        rs = avg_gain / avg_loss
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "rsi": round(100 - (100 / (1 + rs)), 2),
        }

    def calculate_sma(
        self, symbol: str, interval: str = "60", period: int = 50
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 10
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "Insufficient data for SMA"}
        sma = sum(closes[-period:]) / period
        return {"status": "ok", "symbol": symbol.upper(), "sma": round(sma, 4)}

    def calculate_ema(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "Insufficient data"}

        k = 2 / (period + 1)
        ema = closes[0]
        for p in closes[1:]:
            ema = p * k + ema * (1 - k)
        return {"status": "ok", "symbol": symbol.upper(), "ema": round(ema, 4)}

    def calculate_macd(
        self,
        symbol: str,
        interval: str = "60",
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=slow + 50).get(
            "list", []
        )
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < slow:
            return {"status": "error", "msg": "Insufficient data"}

        def get_ema(data, p):
            k = 2 / (p + 1)
            e = data[0]
            for v in data[1:]:
                e = v * k + e * (1 - k)
            return e

        macd = get_ema(closes, fast) - get_ema(closes, slow)
        return {"status": "ok", "symbol": symbol.upper(), "macd": round(macd, 4)}

    def calculate_bollinger_bands(
        self, symbol: str, interval: str = "15", period: int = 20
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=period).get(
            "list", []
        )
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "Insufficient data"}

        sma = sum(closes) / period
        std_dev = statistics.stdev(closes) if len(closes) > 1 else 0.0
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "upper": round(sma + (std_dev * 2), 4),
            "middle": round(sma, 4),
            "lower": round(sma - (std_dev * 2), 4),
        }

    def calculate_vwap(
        self, symbol: str, interval: str = "15", limit: int = 50
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=limit).get(
            "list", []
        )
        total_pv = sum(float(k[4]) * float(k[5]) for k in klines)
        total_v = sum(float(k[5]) for k in klines)
        vwap = total_pv / total_v if total_v != 0 else 0
        return {"status": "ok", "symbol": symbol.upper(), "vwap": round(vwap, 4)}

    def calculate_atr(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if len(klines) < period + 1:
            return {"status": "error", "msg": "Insufficient data"}

        tr_list = []
        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i - 1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        atr = sum(tr_list[-period:]) / period if tr_list else 0.0
        return {"status": "ok", "symbol": symbol.upper(), "atr": round(atr, 4)}

    def calculate_stochastic(
        self,
        symbol: str,
        interval: str = "15",
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "Insufficient data"}

        lowest_low = min(lows[:period])
        highest_high = max(highs[:period])
        k = (
            ((closes[0] - lowest_low) / (highest_high - lowest_low)) * 100
            if highest_high != lowest_low
            else 50.0
        )
        return {"status": "ok", "symbol": symbol.upper(), "k": round(k, 2)}

    def calculate_hma(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period:
            return {"status": "error", "msg": "Insufficient data"}

        def _wma(prices, p):
            denom = p * (p + 1) / 2
            return [
                sum(prices[i - p + 1 + j] * (j + 1) for j in range(p)) / denom
                for i in range(p - 1, len(prices))
            ]

        half_len = int(period / 2)
        sqrt_len = int(math.sqrt(period))
        wma_half = _wma(closes, half_len)
        wma_full = _wma(closes, period)
        diff = [2 * h - f for h, f in zip(wma_half[-sqrt_len:], wma_full[-sqrt_len:])]
        hma = _wma(diff, sqrt_len)
        return {"status": "ok", "symbol": symbol.upper(), "hma": round(hma[-1], 4)}

    def calculate_adx(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]

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
        return {"status": "ok", "symbol": symbol.upper(), "adx": round(adx, 2)}

    def calculate_cci(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        tp = [
            (h + l + c) / 3
            for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])
        ]
        sma = sum(tp) / period if tp else 0
        md = sum(abs(x - sma) for x in tp) / period if tp else 0
        cci = (tp[-1] - sma) / (0.015 * md) if md != 0 else 0
        return {"status": "ok", "symbol": symbol.upper(), "cci": round(cci, 2)}

    def calculate_ichimoku(
        self,
        symbol: str,
        interval: str = "60",
        tenkan: int = 9,
        kijun: int = 26,
        senkou_b: int = 52,
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=senkou_b + 50
        ).get("list", [])
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]

        def get_midpoint(h, l, p):
            return (max(h[-p:]) + min(l[-p:])) / 2 if len(h) >= p else 0.0

        t = get_midpoint(highs, lows, tenkan)
        k = get_midpoint(highs, lows, kijun)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "tenkan": round(t, 4),
            "kijun": round(k, 4),
            "senkou_a": round((t + k) / 2, 4),
            "senkou_b": round(get_midpoint(highs, lows, senkou_b), 4),
        }

    def calculate_fractals(self, symbol: str, interval: str = "60") -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=10).get(
            "list", []
        )
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        bullish = (
            (
                lows[-3] < lows[-4]
                and lows[-3] < lows[-5]
                and lows[-3] < lows[-2]
                and lows[-3] < lows[-1]
            )
            if len(lows) >= 5
            else False
        )
        bearish = (
            (
                highs[-3] > highs[-4]
                and highs[-3] > highs[-5]
                and highs[-3] > highs[-2]
                and highs[-3] > highs[-1]
            )
            if len(highs) >= 5
            else False
        )
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "bullish_fractal": bullish,
            "bearish_fractal": bearish,
        }

    def calculate_pivot_points(self, symbol: str, interval: str = "D") -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=2).get(
            "list", []
        )
        if not klines:
            return {
                "status": "error",
                "msg": "No kline data available for pivot calculation",
            }
        high, low, close = float(klines[0][2]), float(klines[0][3]), float(klines[0][4])
        pivot = (high + low + close) / 3
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "pivot": round(pivot, 4),
            "r1": round(2 * pivot - low, 4),
            "s1": round(2 * pivot - high, 4),
        }

    def calculate_klinger(
        self, symbol: str, interval: str = "60", fast: int = 34, slow: int = 55
    ) -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=slow + 50).get(
            "list", []
        )
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]

        trend = [0] * len(closes)
        for i in range(1, len(closes)):
            trend[i] = (
                1
                if closes[i] > closes[i - 1]
                else (-1 if closes[i] < closes[i - 1] else trend[i - 1])
            )

        vf = []
        for i in range(1, len(closes)):
            dm = highs[i] - lows[i]
            clv = (
                ((closes[i] - lows[i]) - (highs[i] - closes[i])) / dm if dm != 0 else 0
            )
            vf.append(volumes[i] * abs(2 * clv - 1) * trend[i] * 100)

        def _get_ema_series(data, p):
            if not data:
                return [0.0]
            k = 2 / (p + 1)
            ema = [data[0]]
            for val in data[1:]:
                ema.append(val * k + ema[-1] * (1 - k))
            return ema

        fast_ema = _get_ema_series(vf, fast)
        slow_ema = _get_ema_series(vf, slow)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "klinger": round(fast_ema[-1] - slow_ema[-1], 2),
        }

    def calculate_cmf(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if len(klines) < period + 1:
            return {"status": "error", "msg": "Insufficient data"}

        mfv_list, vol_list = [], []
        for k in reversed(klines):
            h, l, c, v = float(k[2]), float(k[3]), float(k[4]), float(k[5])
            mfv = (((c - l) - (h - c)) / (h - l) * v) if (h - l) != 0 else 0
            mfv_list.append(mfv)
            vol_list.append(v)

        cmf = (
            sum(mfv_list[-period:]) / sum(vol_list[-period:])
            if sum(vol_list[-period:]) != 0
            else 0
        )
        return {"status": "ok", "symbol": symbol.upper(), "cmf": round(cmf, 4)}

    def calculate_adx_with_di(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        return self.calculate_adx(symbol=symbol, interval=interval, period=period)

    def calculate_elder_ray_index(
        self, symbol: str, interval: str = "60", period: int = 13
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if len(klines) < period:
            return {"status": "error", "msg": "Insufficient data"}
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]

        k = 2 / (period + 1)
        ema = closes[0]
        for p in closes[1:]:
            ema = p * k + ema * (1 - k)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "bull_power": round(highs[-1] - ema, 4),
            "bear_power": round(lows[-1] - ema, 4),
            "ema": round(ema, 4),
        }

    def calculate_kst(self, symbol: str, interval: str = "60") -> dict:
        klines = self.get_klines(symbol=symbol, interval=interval, limit=100).get(
            "list", []
        )
        if len(klines) < 35:
            return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]

        def roc(data, p):
            return [
                (data[i] - data[i - p]) / data[i - p] * 100 for i in range(p, len(data))
            ]

        r1, r2, r3, r4 = (
            roc(closes, 10),
            roc(closes, 15),
            roc(closes, 20),
            roc(closes, 30),
        )
        kst = (
            sum(r1[-10:]) / 10
            + sum(r2[-15:]) / 15 * 2
            + sum(r3[-20:]) / 20 * 3
            + sum(r4[-30:]) / 30 * 4
        )
        return {"status": "ok", "symbol": symbol.upper(), "kst": round(kst, 4)}

    def calculate_tema(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if len(klines) < period:
            return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]

        def ema(data, p):
            k = 2 / (p + 1)
            ema_val = data[0]
            for val in data[1:]:
                ema_val = val * k + ema_val * (1 - k)
            return ema_val

        e1 = ema(closes, period)
        e2 = ema([e1], period)
        e3 = ema([e2], period)
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "tema": round(3 * e1 - 3 * e2 + e3, 4),
        }

    def calculate_ehler_rsi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if not klines:
            return {"status": "error", "msg": "Insufficient data"}
        closes = [float(k[4]) for k in reversed(klines)]
        alpha = 2 / (period + 1)
        rsi = closes[0]
        for price in closes[1:]:
            rsi = (price * alpha) + (rsi * (1 - alpha))
        return {"status": "ok", "symbol": symbol.upper(), "ehler_rsi": round(rsi, 4)}

    def calculate_ehler_stochastic(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        if len(klines) < period:
            return {"status": "error", "msg": "Insufficient data"}
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        closes = [float(k[4]) for k in reversed(klines)]
        lowest_low = min(lows[-period:])
        highest_high = max(highs[-period:])
        stochastic = (
            (closes[-1] - lowest_low) / (highest_high - lowest_low) * 100
            if (highest_high - lowest_low) != 0
            else 50
        )
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "ehler_stoch": round(stochastic, 2),
        }

    def calculate_vwma(
        self, symbol: str, interval: str = "60", period: int = 20
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 50
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        volumes = [float(k[5]) for k in reversed(klines)]

        pv = sum(c * v for c, v in zip(closes[-period:], volumes[-period:]))
        v = sum(volumes[-period:])
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "vwma": round(pv / v if v != 0 else 0, 4),
        }

    def calculate_bollinger_bands_pb(
        self, symbol: str, interval: str = "15", period: int = 20
    ) -> dict:
        bb = self.calculate_bollinger_bands(
            symbol=symbol, interval=interval, period=period
        )
        if bb.get("status") != "ok":
            return bb

        klines = self.get_klines(symbol=symbol, interval=interval, limit=1).get(
            "list", []
        )
        if not klines:
            return {"status": "error", "msg": "No price data"}
        price = float(klines[0][4])

        pb = (
            (price - bb["lower"]) / (bb["upper"] - bb["lower"])
            if (bb["upper"] - bb["lower"]) != 0
            else 0.5
        )
        return {"status": "ok", "symbol": symbol.upper(), "pb": round(pb, 4)}

    def calculate_roc(
        self, symbol: str, interval: str = "60", period: int = 12
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 5
        ).get("list", [])
        closes = [float(k[4]) for k in reversed(klines)]
        if len(closes) < period + 1:
            return {"status": "error", "msg": "Insufficient data"}
        roc = ((closes[-1] - closes[-period - 1]) / closes[-period - 1]) * 100
        return {"status": "ok", "symbol": symbol.upper(), "roc": round(roc, 2)}

    def calculate_mfi(
        self, symbol: str, interval: str = "60", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 5
        ).get("list", [])
        if len(klines) < period + 1:
            return {"status": "error", "msg": "Insufficient data"}

        data = [
            {"h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in reversed(klines)
        ]
        tp = [(d["h"] + d["l"] + d["c"]) / 3 for d in data]
        mf = [t * d["v"] for t, d in zip(tp, data)]

        pos_mf = sum(m for i, m in enumerate(mf[1:]) if tp[i + 1] > tp[i])
        neg_mf = sum(abs(m) for i, m in enumerate(mf[1:]) if tp[i + 1] < tp[i])

        mfi = 100 - (100 / (1 + (pos_mf / neg_mf))) if neg_mf != 0 else 100
        return {"status": "ok", "symbol": symbol.upper(), "mfi": round(mfi, 2)}

    def calculate_williams_r(
        self, symbol: str, interval: str = "15", period: int = 14
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=period + 5
        ).get("list", [])
        if not klines:
            return {"status": "error", "msg": "Insufficient data"}
        highs = [float(k[2]) for k in reversed(klines)]
        lows = [float(k[3]) for k in reversed(klines)]
        close = float(klines[-1][4])

        h_max = max(highs[-period:])
        l_min = min(lows[-period:])

        wr = (h_max - close) / (h_max - l_min) * -100 if h_max != l_min else 0
        return {"status": "ok", "symbol": symbol.upper(), "williams_r": round(wr, 2)}

    def calculate_all_indicators(self, symbol: str, interval: str = "60") -> dict:
        indicator_map = {
            "rsi": lambda: self.calculate_rsi(symbol, interval),
            "macd": lambda: self.calculate_macd(symbol, interval),
            "adx": lambda: self.calculate_adx(symbol, interval),
            "cci": lambda: self.calculate_cci(symbol, interval),
            "ichimoku": lambda: self.calculate_ichimoku(symbol, interval),
            "sma": lambda: self.calculate_sma(symbol, interval),
            "ema": lambda: self.calculate_ema(symbol, interval),
            "bollinger": lambda: self.calculate_bollinger_bands(symbol, interval),
            "vwap": lambda: self.calculate_vwap(symbol, interval),
            "atr": lambda: self.calculate_atr(symbol, interval),
            "stoch": lambda: self.calculate_stochastic(symbol, interval),
            "hma": lambda: self.calculate_hma(symbol, interval),
            "vwma": lambda: self.calculate_vwma(symbol, interval),
            "bollinger_pb": lambda: self.calculate_bollinger_bands_pb(symbol, interval),
            "roc": lambda: self.calculate_roc(symbol, interval),
            "mfi": lambda: self.calculate_mfi(symbol, interval),
            "williams_r": lambda: self.calculate_williams_r(symbol, interval),
            "cmf": lambda: self.calculate_cmf(symbol, interval),
            "elder_ray": lambda: self.calculate_elder_ray_index(symbol, interval),
            "kst": lambda: self.calculate_kst(symbol, interval),
            "tema": lambda: self.calculate_tema(symbol, interval),
            "ehler_rsi": lambda: self.calculate_ehler_rsi(symbol, interval),
            "ehler_stoch": lambda: self.calculate_ehler_stochastic(symbol, interval),
        }
        results = {name: func() for name, func in indicator_map.items()}
        return {"status": "ok", "symbol": symbol.upper(), "indicators": results}

    def analyze_symbol(self, symbol: str) -> dict:
        timeframes = ["15", "60", "240", "D"]
        analysis = {}
        for tf in timeframes:
            try:
                regime = self.get_market_regime(symbol, interval=tf)
                rsi = self.calculate_rsi(symbol, interval=tf)
                ema = self.calculate_ema(symbol, interval=tf)
                atr = self.calculate_atr(symbol, interval=tf)
                analysis[tf] = {
                    "regime": regime.get("regime"),
                    "rsi": rsi.get("rsi"),
                    "ema": ema.get("ema"),
                    "atr": atr.get("atr"),
                    "volatility": regime.get("metrics", {}).get("volatility_pct"),
                }
            except Exception:
                continue

        ticker_raw = self.get_ticker(symbol)
        t_list = ticker_raw.get("list", [{}]) if isinstance(ticker_raw, dict) else [{}]
        ticker = t_list[0] if t_list else {}
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "last_price": ticker.get("lastPrice"),
            "price_24h_pcnt": ticker.get("price24hPcnt"),
            "high_24h": ticker.get("highPrice24h"),
            "low_24h": ticker.get("lowPrice24h"),
            "analysis": analysis,
        }

    # ── Summary & Export Helpers ─────────────────────────────────────────────
    def get_account_summary(self) -> dict:
        return {
            "balance": self.get_wallet_balance(),
            "positions": self.get_position_risk(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_open_positions_summary(self, category: str = "linear") -> dict:
        pos_res = self.get_position_risk(category=category)
        positions = pos_res.get("positions", [])
        return {"status": "ok", "count": len(positions), "positions": positions}

    def get_pnl_summary(
        self, symbol: Optional[str] = None, limit: int = 100, days: int = 7
    ) -> dict:
        history_resp = self.get_pnl_history(symbol=symbol, limit=limit)
        history = history_resp.get("list", []) if isinstance(history_resp, dict) else []
        if not history:
            return {"status": "ok", "msg": "No trade history found", "total_pnl": 0.0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filtered = [
            p
            for p in history
            if datetime.fromtimestamp(
                int(p.get("updatedTime", 0)) / 1000, tz=timezone.utc
            )
            > cutoff
        ]

        total_pnl = sum(float(trade.get("closedPnl", 0)) for trade in filtered)
        total_fees = sum(
            float(trade.get("openFee", 0)) + float(trade.get("closeFee", 0))
            for trade in filtered
        )
        wins = [t for t in filtered if float(t.get("closedPnl", 0)) > 0]
        losses = [t for t in filtered if float(t.get("closedPnl", 0)) <= 0]

        return {
            "status": "ok",
            "days": days,
            "trades_analyzed": len(filtered),
            "total_pnl": round(total_pnl, 4),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(total_pnl - total_fees, 4),
            "win_rate_pct": round(len(wins) / len(filtered) * 100, 2)
            if filtered
            else 0.0,
            "avg_win": round(sum(float(t["closedPnl"]) for t in wins) / len(wins), 4)
            if wins
            else 0.0,
            "avg_loss": round(
                sum(float(t["closedPnl"]) for t in losses) / len(losses), 4
            )
            if losses
            else 0.0,
        }

    def check_risk_limit(self, symbol: str, qty: float, price: float) -> dict:
        max_size = float(os.getenv("MAX_POSITION_SIZE_USDT", "1000"))
        notional = qty * price
        if notional > max_size:
            return {
                "status": "error",
                "msg": f"Risk Limit Exceeded: Notional ({notional:.2f}) > Max Allowed ({max_size:.2f})",
            }
        return {"status": "ok", "msg": "Trade within risk limits", "notional": notional}

    def export_trade_history(
        self, symbol: str, filename: str = "trade_history.csv"
    ) -> dict:
        history = self.get_order_history(symbol=symbol, limit=100)
        items = history.get("list", []) if isinstance(history, dict) else []
        if not items:
            return {"status": "error", "msg": "No trade history to export"}
        try:
            with open(filename, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=items[0].keys())
                writer.writeheader()
                writer.writerows(items)
            return {
                "status": "ok",
                "msg": f"Exported {len(items)} entries to {filename}",
            }
        except Exception as err:
            return {"status": "error", "msg": f"Export failed: {err}"}

    def scan_symbols(
        self, symbols: List[str], category: str = "linear", include_regime: bool = False
    ) -> dict:
        results = []
        for sym in symbols:
            ticker_raw = self.get_ticker(symbol=sym, category=category)
            t_list = ticker_raw.get("list", []) if isinstance(ticker_raw, dict) else []
            if t_list:
                t = t_list[0]
                entry = {
                    "symbol": sym.upper(),
                    "last_price": float(t.get("lastPrice", 0)),
                    "change_24h_pct": float(t.get("price24hPcnt", 0)) * 100.0,
                    "turnover_24h": float(t.get("turnover24h", 0)),
                }
                if include_regime:
                    reg_data = self.get_market_regime(sym, category=category)
                    entry["regime"] = reg_data.get("regime", "UNKNOWN")
                results.append(entry)

        results.sort(key=lambda x: x["turnover_24h"], reverse=True)
        return {"status": "ok", "count": len(results), "symbols": results}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: UNIFIED PROGRAMMATIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

_realm: Optional[BybitRealm] = None
_realm_lock = threading.Lock()


def get_realm() -> BybitRealm:
    global _realm
    if _realm is None:
        with _realm_lock:
            if _realm is None:
                _realm = BybitRealm()
    return _realm


def run(
    action: str,
    symbol: Optional[str] = None,
    side: Optional[Literal["Buy", "Sell"]] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    leverage: Optional[int] = None,
    category: str = "linear",
    interval: str = "60",
    limit: int = 50,
    no_color: bool = False,
    **kwargs,
) -> dict:
    bot = get_realm()
    logger.info(
        f"Executing Action: {action} | Symbol: {symbol} | Side: {side} | Qty: {qty} | Price: {price}"
    )

    try:
        # ── Health & Account ─────────────────────────────────────────────────
        if action == "health_check":
            res = bot.health_check()
        elif action in ("get_wallet_balance", "check_balance"):
            res = bot.get_wallet_balance(
                account_type=kwargs.get("account_type", "UNIFIED")
            )
        elif action == "get_account_info":
            res = bot.get_account_info()
        elif action == "get_asset_info":
            res = bot.get_asset_info(account_type=kwargs.get("account_type"), coin=kwargs.get("coin"))
        elif action == "transfer_funds":
            res = bot.transfer_funds(
                coin=kwargs.get("coin", "USDT"),
                amount=float(qty or kwargs.get("amount", 0)),
                from_account=kwargs.get("from_account", "UNIFIED"),
                to_account=kwargs.get("to_account", "FUND"),
            )
        elif action == "get_transfer_history":
            res = bot.get_transfer_history(coin=kwargs.get("coin"), limit=limit)
        elif action == "set_margin_mode":
            res = bot.set_margin_mode(set_margin_mode=kwargs.get("set_margin_mode", "REGULAR_MARGIN"))
        elif action == "get_borrow_history":
            res = bot.get_borrow_history(currency=kwargs.get("currency"), limit=limit)
        elif action == "get_positions":
            res = bot.get_positions(category=category, symbol=symbol)
        elif action == "get_position_risk":
            res = bot.get_position_risk(category=category, symbol=symbol)
        elif action == "get_fee_rate":
            res = bot.get_fee_rate(category=category, symbol=symbol)
        elif action == "get_account_summary":
            res = bot.get_account_summary()
        elif action in ("get_open_positions_summary", "send_open_positions_summary"):
            res = bot.get_open_positions_summary(category=category)

        # ── Orders & Smart Trades ────────────────────────────────────────────
        elif action == "place_smart_trade":
            res = bot.place_smart_trade(
                symbol=symbol or "BTCUSDT",
                side=side or "Buy",
                qty=qty,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                leverage=leverage,
                category=category,
                **kwargs,
            )
        elif action == "place_order":
            if not symbol or not side or qty is None:
                res = {
                    "status": "error",
                    "msg": "symbol, side, and qty are required for place_order",
                }
            else:
                res = bot.place_order(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                    category=category,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    **kwargs,
                )
        elif action == "amend_order":
            res = bot.amend_order(
                symbol=symbol,
                qty=qty,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                category=category,
                **kwargs,
            )
        elif action == "cancel_order":
            res = bot.cancel_order(symbol=symbol, category=category, **kwargs)
        elif action == "cancel_all_orders":
            res = bot.cancel_all_orders(symbol=symbol, category=category)
        elif action == "close_position":
            res = bot.close_position(symbol=symbol, category=category)
        elif action == "panic_close":
            res = bot.panic_close(category=category)
        elif action == "batch_place_orders":
            res = bot.batch_place_orders(
                orders=kwargs.get("orders", []), category=category
            )
        elif action == "set_leverage":
            res = bot.set_leverage(
                symbol=symbol, leverage=leverage or 10, category=category
            )
        elif action in ("set_trading_stop", "set_tp_sl"):
            res = bot.set_trading_stop(
                symbol=symbol,
                stop_loss=stop_loss,
                take_profit=take_profit,
                category=category,
            )
        elif action == "update_trailing_stop":
            res = bot.set_trading_stop(
                symbol=symbol,
                trailing_stop=kwargs.get("trailing_stop_pct"),
                category=category,
            )
        elif action == "bulk_update_tp_sl":
            res = bot.bulk_update_tp_sl(category=category, tp=take_profit, sl=stop_loss)
        elif action == "place_breakeven_order":
            res = bot.place_breakeven_order(symbol=symbol, category=category)

        # ── Market Data ──────────────────────────────────────────────────────
        elif action == "get_ticker":
            res = bot.get_ticker(symbol=symbol, category=category)
        elif action == "get_orderbook":
            res = bot.get_orderbook(symbol=symbol, limit=limit, category=category)
        elif action == "get_klines":
            res = bot.get_klines(
                symbol=symbol, interval=interval, limit=limit, category=category
            )
        elif action == "get_recent_trades":
            res = bot.get_recent_trades(symbol=symbol, limit=limit, category=category)
        elif action == "get_instruments_info":
            res = bot.get_instruments_info(
                category=category, symbol=symbol, limit=limit
            )
        elif action == "get_funding_rate":
            res = bot.get_funding_rate(symbol=symbol, category=category, limit=limit)
        elif action == "get_open_interest":
            res = bot.get_open_interest(
                symbol=symbol, interval=interval, category=category, limit=limit
            )
        elif action == "get_volatility_index":
            res = bot.get_volatility_index(category=category)
        elif action == "get_open_orders":
            res = bot.get_open_orders(symbol=symbol, category=category, limit=limit)
        elif action == "get_order_history":
            res = bot.get_order_history(symbol=symbol, category=category, limit=limit)
        elif action == "get_executions":
            res = bot.get_executions(category=category, symbol=symbol, limit=limit)
        elif action == "get_pnl_history":
            res = bot.get_pnl_history(category=category, symbol=symbol, limit=limit)
        elif action == "get_pnl_summary":
            res = bot.get_pnl_summary(
                symbol=symbol, limit=limit, days=int(kwargs.get("days", 7))
            )

        # ── Observatory & Analytics ──────────────────────────────────────────
        elif action == "get_market_regime":
            res = bot.get_market_regime(
                symbol=symbol or "BTCUSDT", interval=interval, category=category
            )
        elif action in ("get_orderbook_analysis", "get_volume_at_price"):
            res = bot.get_orderbook_analysis(
                symbol=symbol or "BTCUSDT", category=category
            )
        elif action == "scan_scalping_opportunities":
            res = bot.scan_scalping_opportunities(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action == "scan_symbols":
            res = bot.scan_symbols(
                symbols=kwargs.get("symbols", [symbol] if symbol else ["BTCUSDT"]),
                category=category,
                include_regime=bool(kwargs.get("include_regime", False)),
            )
        elif action == "calculate_support_resistance_levels":
            res = bot.calculate_support_resistance_levels(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action in (
            "calculate_fibonacci_retracement",
            "calculate_fibonacci_levels",
        ):
            res = bot.calculate_fibonacci_levels(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action == "calculate_volume_profile":
            res = bot.calculate_volume_profile(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action == "calculate_orderflow_delta":
            res = bot.calculate_orderflow_delta(symbol=symbol or "BTCUSDT", limit=limit)
        elif action == "calculate_market_depth_profile":
            res = bot.calculate_market_depth_profile(
                symbol=symbol or "BTCUSDT", depth=limit
            )
        elif action == "detect_high_confluence_levels":
            res = bot.detect_high_confluence_levels(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action == "deep_level_sort":
            res = bot.deep_level_sort(symbol=symbol or "BTCUSDT")
        elif action == "calculate_sr_levels":
            res = bot.calculate_sr_levels(symbol=symbol or "BTCUSDT")
        elif action == "generate_market_depth_report":
            res = bot.generate_market_depth_report(symbol=symbol or "BTCUSDT")
        elif action == "calculate_limit_micro_profit":
            res = bot.calculate_limit_micro_profit(
                entry_price=float(kwargs.get("entry_price", price or 0)),
                limit_price=float(kwargs.get("limit_price", price or 0)),
                side=side or "Buy",
                qty=float(qty or 0),
            )
        elif action == "calculate_depth_weighted_profit":
            res = bot.calculate_depth_weighted_profit(
                symbol=symbol or "BTCUSDT",
                entry_price=float(kwargs.get("entry_price", price or 0)),
                limit_price=float(kwargs.get("limit_price", price or 0)),
                side=side or "Buy",
                qty=float(qty or 0),
            )
        elif action == "calculate_all_indicators":
            res = bot.calculate_all_indicators(
                symbol=symbol or "BTCUSDT", interval=interval
            )
        elif action == "analyze_symbol":
            res = bot.analyze_symbol(symbol=symbol or "BTCUSDT")

        # Dynamic Technical Indicator Routing
        elif action.startswith("calculate_"):
            ind = action.replace("calculate_", "").lower()
            method_name = f"calculate_{ind}"
            if hasattr(bot, method_name):
                func = getattr(bot, method_name)
                res = func(symbol=symbol or "BTCUSDT", interval=interval, **kwargs)
            else:
                res = {
                    "status": "error",
                    "msg": f"Indicator '{ind}' is not natively supported.",
                }

        # ── Miscellaneous ───────────────────────────────────────────────────
        elif action == "get_journal":
            res = {
                "status": "ok",
                "entries": bot.journal.get_entries(symbol=symbol, limit=limit),
            }
        elif action == "check_risk_limit":
            res = bot.check_risk_limit(symbol=symbol, qty=qty or 0, price=price or 0)
        elif action == "export_trade_history":
            res = bot.export_trade_history(
                symbol=symbol, filename=kwargs.get("filename", "trade_history.csv")
            )
        elif action in ("send_telegram_alert", "alert"):
            logger.info(f"ALERT: {kwargs.get('message', 'Alert triggered')}")
            res = {"status": "ok", "msg": "Alert logged."}
        else:
            res = {"status": "error", "msg": f"Action '{action}' is not supported."}

        print_human_readable_ui(action, res, no_color=no_color)
        write_llm_output(res)
        return res

    except Exception as exc:
        logger.error(f"Execution error on action '{action}': {exc}", exc_info=True)
        err_res = {"status": "error", "action": action, "msg": str(exc)}
        print_human_readable_ui(action, err_res, no_color=no_color)
        write_llm_output(err_res)
        return err_res


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: CLI PARSER & ARGC INTERFACE
# ══════════════════════════════════════════════════════════════════════════════


def _coerce_type(val: str) -> Any:
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbt.py",
        description=f"Bybit Realm Master Trading Engine v{__version__}",
    )
    parser.add_argument("--action", required=True, help="Action to perform")
    parser.add_argument("--symbol", help="Trading pair (e.g. BTCUSDT)")
    parser.add_argument("--side", choices=["Buy", "Sell"], help="Order side")
    parser.add_argument("--qty", type=float, help="Order quantity")
    parser.add_argument("--price", type=float, help="Order price")
    parser.add_argument(
        "--category", default="linear", choices=["linear", "spot", "inverse"]
    )
    parser.add_argument(
        "--stop-loss", type=float, dest="stop_loss", help="Stop loss price"
    )
    parser.add_argument(
        "--take-profit", type=float, dest="take_profit", help="Take profit price"
    )
    parser.add_argument("--leverage", type=int, help="Leverage value")
    parser.add_argument("--interval", default="60", help="Kline interval")
    parser.add_argument("--limit", type=int, default=50, help="Result limit")
    parser.add_argument("--json", action="store_true", help="Output raw JSON format")
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable detailed debug logging"
    )
    return parser


if __name__ == "__main__":
    # Argc environment variable parsing fast path
    if any(k.startswith("argc_") for k in os.environ):
        action = os.environ.get("argc_action")
        symbol = os.environ.get("argc_symbol")
        side = os.environ.get("argc_side")
        qty = float(os.environ["argc_qty"]) if os.environ.get("argc_qty") else None
        price = (
            float(os.environ["argc_price"]) if os.environ.get("argc_price") else None
        )
        stop_loss = (
            float(os.environ["argc_stop_loss"])
            if os.environ.get("argc_stop_loss")
            else None
        )
        take_profit = (
            float(os.environ["argc_take_profit"])
            if os.environ.get("argc_take_profit")
            else None
        )
        category = os.environ.get("argc_category", "linear")
        no_color = os.environ.get("argc_no_color") == "true"

        kwargs = {}
        for k, v in os.environ.items():
            if k.startswith("argc_") and k not in (
                "argc_action",
                "argc_symbol",
                "argc_side",
                "argc_qty",
                "argc_price",
                "argc_stop_loss",
                "argc_take_profit",
                "argc_category",
                "argc_no_color",
            ):
                kwargs[k[5:].replace("-", "_")] = _coerce_type(v)

        result = run(
            action=action,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            category=category,
            no_color=no_color,
            **kwargs,
        )
        sys.exit(0 if result.get("status") != "error" else 1)

    # Standard CLI fallback path
    parser = _build_parser()
    args, unknown = parser.parse_known_args()

    extra_kwargs = {}
    for i in range(0, len(unknown), 2):
        if unknown[i].startswith("--") and i + 1 < len(unknown):
            key = unknown[i].lstrip("-").replace("-", "_")
            extra_kwargs[key] = _coerce_type(unknown[i + 1])

    res = run(
        action=args.action,
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        price=args.price,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        leverage=args.leverage,
        category=args.category,
        interval=args.interval,
        limit=args.limit,
        no_color=args.no_color,
        **extra_kwargs,
    )

    sys.exit(0 if res.get("status") != "error" else 1)
