#!/usr/bin/env python3
# ==============================================================================
# bybit_smart_order.py — Pyrmethus AIChat Bybit Smart Order Engine v2.3.0-ASCENDED
# argc/aichat compatible · Automatic Position Sizing & Risk Management
#
# @describe Place a smart order with automatic position sizing, risk management,
#           and SL/TP bounds. Supports market/limit/conditional orders, fee-aware
#           risk sizing, ATR fallback stops, formula eval, and dry-run.
#
# @meta require-tools python3
#
# @option --symbol <TEXT>                Trading pair (e.g. BTCUSDT, default: BTCUSDT)
# @option --side <ENUM>                  Order side: Buy, Sell (default: Buy)
# @option --order-type <ENUM>            Order type: Market, Limit (default: Market)
# @option --entry-price <NUM>            Target entry price (required if Limit)
# @option --leverage <NUM>               Set account leverage before placing order
# @option --risk-pct <NUM>               % of account balance to risk (default: 1.0)
# @option --qty <NUM>                    Explicit order quantity (overrides auto-sizing)
# @option --sl-dist <NUM>                Stop loss distance in price
# @option --sl-price <NUM>               Absolute Stop Loss price
# @option --tp-price <NUM>               Absolute Take Profit price
# @option --rr-ratio <NUM>               Risk to Reward ratio for TP (default: 2.0)
# @option --tp-usdt <NUM>                Target Take Profit in absolute USDT value
# @option --sl-usdt <NUM>                Maximum Risk in USDT (overrides risk-pct)
# @option --trailing-stop <NUM>          Trailing Stop distance in price
# @option --trailing-activation <NUM>    Trailing Stop activation price
# @option --margin-mode <STR>            Margin mode: isolated, cross
# @option --position-idx <NUM>           Position index: 0=One-Way, 1=Buy, 2=Sell
# @option --time-in-force <STR>          GTC, IOC, FOK, PostOnly (default: GTC)
# @option --sl-trigger-by <STR>          Mark, Index, Last (default: Mark)
# @option --tp-trigger-by <STR>          Mark, Index, Last (default: Mark)
# @option --order-link-id <STR>          Custom order link ID (max 36 chars)
# @option --max-position-usdt <NUM>      Maximum notional USD safety cap
# @option --trigger-price <NUM>          Conditional trigger price
# @option --trigger-direction <NUM>      1=rise, 2=fall (auto if omitted)
# @option --trigger-by <STR>             Mark, Index, Last (default: Mark)
# @option --category <STR>               linear, inverse, spot (default: linear)
# @option --atr-mult <NUM>               ATR multiplier for default SL (default: 2.0)
# @option --slippage-bps <NUM>           Extra slippage buffer in bps for market entry (default: 5)
# @flag   --reduce-only                  Submit order as reduce-only
# @flag   --dry-run                      Calculate sizing without submitting
# @flag   --skip-leverage                Do not call set-leverage API
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import ast
import enum
import json
import logging
import math
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add current directory to path to locate modular packages
CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Core API & Proxy Imports
try:
    import bybit_core
except ImportError:
    bybit_core = None

try:
    import proxy_utils

    proxy_utils.set_proxy_environment()
except ImportError:
    proxy_utils = None

try:
    import scientific_calculator
except ImportError:
    scientific_calculator = None

import requests

__version__ = "2.3.0-ASCENDED"
__all__ = [
    "__version__",
    "calculate_exit_price_by_pnl",
    "calculate_exit_price_for_net_loss",
    "calculate_pnl_by_exit_price",
    "execute_smart_order",
    "format_precision",
    "run",
]

log = logging.getLogger("bybit_smart_order")

# ==============================================================================
# SECTION 1: Exit Codes & JSON Serializer
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2

# Fee defaults (Bybit linear VIP0)
_FEE_MAKER = 0.0002
_FEE_TAKER = 0.00055

# Bybit V5 tradeMode
_TRADE_MODE_CROSS = 0
_TRADE_MODE_ISOLATED = 1

_TRIGGER_MAP = {
    "Mark": "MarkPrice",
    "Index": "IndexPrice",
    "Last": "LastPrice",
    "MarkPrice": "MarkPrice",
    "IndexPrice": "IndexPrice",
    "LastPrice": "LastPrice",
}

# Instrument filter cache: symbol -> (monotonic_ts, payload)
_INSTR_CACHE: Dict[str, Tuple[float, Tuple]] = {}
_INSTR_CACHE_LOCK = threading.Lock()
_INSTR_CACHE_TTL = 300.0


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Decimal, Path, Enum, datetime, complex, sets."""

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
        if isinstance(obj, complex):
            return {"real": obj.real, "imag": obj.imag}
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])"
    r"|\033\[[0-9;?]*[a-zA-Z]"
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    if not sys.stderr.isatty():
        return False
    return os.environ.get("TERM", "xterm").lower() != "dumb"


def _cprint(
    text: str,
    file: Any = None,
    no_color: bool = False,
    end: str = "\n",
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(
    data: dict[str, Any],
    no_color: bool = False,
) -> None:
    """Render a human-friendly box UI to stderr for interactive CLI users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"
    if data.get("dry_run"):
        status_text = "DRY-RUN"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_PINK}⚡ [BYBIT SMART ORDER v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Symbol:{RESET}      {BOLD}{data.get('symbol', 'N/A')}{RESET}"
        f"  |  {NEON_CYAN}Side:{RESET} {NEON_YELLOW}{data.get('side', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Order Type:{RESET}  {data.get('order_type', 'Market')}"
        f"  |  {NEON_CYAN}Qty:{RESET} {NEON_GREEN}{data.get('qty', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Entry Price:{RESET} {data.get('entry_price', 0.0)}"
        f"  |  {NEON_CYAN}Lev:{RESET} {data.get('leverage', '—')}x"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_RED}Stop Loss:{RESET}   {data.get('stop_loss', 0.0)}"
        f"  (Exp Loss: ${data.get('expected_loss_usdt', 0.0)})"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_GREEN}Take Profit:{RESET} {data.get('take_profit', 0.0)}"
        f"  (Exp Profit: ${data.get('expected_profit_usdt', 0.0)})"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Notional:{RESET}    ${data.get('notional_usdt', 0.0)} USDT"
        f"  |  {NEON_CYAN}RR:{RESET} {data.get('rr_realized', data.get('rr_ratio', '—'))}"
    )
    if data.get("order_id"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} "
            f"{NEON_CYAN}Order ID:{RESET}    {data.get('order_id')}"
        )
    if data.get("risk_amount_usdt") is not None:
        _cprint(
            f"{NEON_PURPLE}│{RESET} "
            f"{NEON_CYAN}Risk Budget:{RESET} ${data.get('risk_amount_usdt', 0.0):.4f}"
            f"  |  {NEON_CYAN}Bal:{RESET} ${data.get('balance', 0.0):.2f}"
        )

    warnings = data.get("warnings") or []
    if warnings:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        for w in warnings[:4]:
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}⚑{RESET} {w}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: PRECISION MATH & FINANCIAL CALCULATORS
# ==============================================================================


def format_precision(
    value: Any,
    step: Any,
    rounding_mode: str = ROUND_HALF_UP,
) -> str:
    """Round a numeric value to an exact decimal step string (no float drift)."""
    if value is None or value == "":
        return ""
    try:
        if step is None or float(step) <= 0:
            return format(Decimal(str(value)), "f")
    except (TypeError, ValueError):
        return str(value)
    try:
        val_d = Decimal(str(value))
        step_d = Decimal(str(step))
        quantized = val_d.quantize(step_d, rounding=rounding_mode)
        return format(quantized, "f")
    except (InvalidOperation, ValueError):
        return str(value)


def calculate_exit_price_by_pnl(
    entry_price: float,
    qty: float,
    side: str,
    pnl_usdt: float,
    e_fee: float = 0.0,
    x_fee: float = 0.0,
) -> float:
    """
    Exit price for target net PnL including entry/exit fees.

    Long:  exit = (pnl/qty + entry*(1+e_fee)) / (1 - x_fee)
    Short: exit = (entry*(1-e_fee) - pnl/qty) / (1 + x_fee)
    """
    if qty <= 0:
        return entry_price
    if side.lower() == "buy":
        denom = 1.0 - x_fee
        if denom <= 0:
            return entry_price
        return (pnl_usdt / qty + entry_price * (1.0 + e_fee)) / denom
    denom = 1.0 + x_fee
    return (entry_price * (1.0 - e_fee) - pnl_usdt / qty) / denom


def calculate_exit_price_for_net_loss(
    entry_price: float,
    qty: float,
    side: str,
    risk_usdt: float,
    e_fee: float = 0.0,
    x_fee: float = 0.0,
) -> float:
    """Stop price for maximum net loss risk_usdt (positive magnitude)."""
    if qty <= 0:
        return entry_price
    if side.lower() == "buy":
        denom = 1.0 - x_fee
        if denom <= 0:
            return entry_price
        return (entry_price * (1.0 + e_fee) - risk_usdt / qty) / denom
    denom = 1.0 + x_fee
    return (risk_usdt / qty + entry_price * (1.0 - e_fee)) / denom


def calculate_pnl_by_exit_price(
    entry_price: float,
    exit_price: float,
    qty: float,
    side: str,
) -> float:
    """Linear contract gross PnL in USDT (no fees)."""
    if side.lower() == "buy":
        return qty * (exit_price - entry_price)
    return qty * (entry_price - exit_price)


def calculate_net_pnl(
    entry_price: float,
    exit_price: float,
    qty: float,
    side: str,
    e_fee: float,
    x_fee: float,
) -> float:
    """Net PnL after entry and exit fees."""
    if side.lower() == "buy":
        gross = qty * (exit_price - entry_price)
    else:
        gross = qty * (entry_price - exit_price)
    fees = qty * entry_price * e_fee + qty * exit_price * x_fee
    return gross - fees


def _atr_wilder(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """Wilder's smoothed Average True Range (ATR)."""
    if len(highs) < period + 1:
        return None
    trs = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, len(highs))
    ]
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_eval_arithmetic(expr: str, balance: float) -> Optional[float]:
    """
    Evaluate a restricted arithmetic expression with `balance` injected.

    Allows + - * / ** % and parentheses only. No names except balance.
    """
    expr = expr.strip().replace("balance", str(balance))
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
        ast.Constant,
        ast.Load,
    )
    # py3.8 compat
    if hasattr(ast, "Num"):
        allowed_nodes = allowed_nodes + (ast.Num,)  # type: ignore

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return None

    try:
        return float(eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}))
    except Exception:
        return None


def _eval_formula(val: Any, balance: float) -> Any:
    """
    Resolve numeric inputs and optional formula strings.

    Priority:
      1. non-string → passthrough
      2. plain number string → float
      3. safe arithmetic with balance
      4. scientific_calculator.eval (if available)
    """
    if val is None or not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return None
    # Plain number
    try:
        return float(s)
    except ValueError:
        pass

    safe = _safe_eval_arithmetic(s, balance)
    if safe is not None and math.isfinite(safe):
        return safe

    if scientific_calculator:
        try:
            expr = s.replace("balance", str(balance))
            stats_res = scientific_calculator.execute_tool(mode="eval", expr=expr)
            if stats_res.get("success"):
                try:
                    out = float(stats_res.get("result", val))
                    if math.isfinite(out):
                        return out
                except (ValueError, TypeError):
                    pass
        except Exception as exc:
            log.debug("formula eval failed for %r: %s", val, exc)
    return val


# ==============================================================================
# SECTION 4: API REQUEST WRAPPERS
# ==============================================================================


def _safe_api(
    method: str,
    path: str,
    params: Optional[dict] = None,
    signed: bool = False,
    default: Any = None,
) -> dict:
    """Resilient API wrapper using bybit_core or falling back to direct requests."""
    if bybit_core and hasattr(bybit_core, "api_request"):
        try:
            return bybit_core.api_request(method, path, params=params, signed=signed)
        except Exception as err:
            log.exception("bybit_core API call failed: %s", err)

    base_url = "https://api.bybit.com"
    if bybit_core and hasattr(bybit_core, "api_base"):
        try:
            base_url = bybit_core.api_base() or base_url
        except Exception:
            pass

    url = base_url + path
    proxies = proxy_utils.get_proxies() if proxy_utils else None

    try:
        if method.upper() == "GET":
            resp = requests.get(url, params=params, timeout=10, proxies=proxies)
        else:
            resp = requests.post(url, json=params, timeout=10, proxies=proxies)
        return resp.json()
    except Exception as exc:
        return {
            "retCode": -1,
            "retMsg": f"Request exception: {exc}",
            "result": default or {},
        }


def get_instrument_info(
    symbol: str,
    category: str = "linear",
) -> Tuple[dict, float, float, float, float, float, Optional[float], Optional[float]]:
    """
    Fetch instrument precision specs (cached).

    Returns:
        (info, qty_step, tick_size, min_qty, min_price, min_notional,
         max_leverage, max_mkt_qty)
    """
    cache_key = f"{category}:{symbol}"
    now = time.monotonic()
    with _INSTR_CACHE_LOCK:
        hit = _INSTR_CACHE.get(cache_key)
        if hit and (now - hit[0]) < _INSTR_CACHE_TTL:
            return hit[1]  # type: ignore[return-value]

    qty_step = 0.001
    tick_size = 0.01
    min_qty = 0.0
    min_price = 0.0
    min_notional = 5.0
    max_leverage: Optional[float] = None
    max_mkt_qty: Optional[float] = None
    info: dict = {}

    # Prefer bybit_core helper
    if bybit_core and hasattr(bybit_core, "get_instruments_info"):
        data = bybit_core.get_instruments_info(category=category, symbol=symbol)
    else:
        data = _safe_api(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
            signed=False,
        )

    if data.get("retCode") == 0:
        lst = data.get("result", {}).get("list", [])
        if lst:
            info = lst[0]
            lot = info.get("lotSizeFilter", {}) or {}
            price_filter = info.get("priceFilter", {}) or {}
            lev_filter = info.get("leverageFilter", {}) or {}

            qty_step = float(lot.get("qtyStep", qty_step) or qty_step)
            tick_size = float(price_filter.get("tickSize", tick_size) or tick_size)
            min_qty = float(lot.get("minOrderQty", 0) or 0)
            min_price = float(price_filter.get("minPrice", 0) or 0)
            try:
                max_mkt_qty = float(lot.get("maxMktOrderQty") or 0) or None
            except (TypeError, ValueError):
                max_mkt_qty = None
            try:
                max_leverage = float(lev_filter.get("maxLeverage") or 0) or None
            except (TypeError, ValueError):
                max_leverage = None

            for flt in (lot, lev_filter):
                for value_key in (
                    "minNotionalValue",
                    "minOrderAmt",
                    "notionalValue",
                ):
                    raw = flt.get(value_key)
                    if raw not in (None, ""):
                        try:
                            min_notional = float(raw)
                            break
                        except (TypeError, ValueError):
                            pass
                if min_notional and min_notional != 5.0:
                    break

    result = (
        info,
        qty_step,
        tick_size,
        min_qty,
        min_price,
        min_notional,
        max_leverage,
        max_mkt_qty,
    )
    with _INSTR_CACHE_LOCK:
        _INSTR_CACHE[cache_key] = (time.monotonic(), result)
    return result


def get_ticker(symbol: str, category: str = "linear") -> Optional[dict]:
    if bybit_core and hasattr(bybit_core, "get_ticker"):
        data = bybit_core.get_ticker(symbol=symbol, category=category)
    else:
        data = _safe_api(
            "GET",
            "/v5/market/tickers",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
    if data.get("retCode") != 0:
        return None
    lst = data.get("result", {}).get("list", [])
    return lst[0] if lst else None


def get_wallet_balance() -> Tuple[float, Optional[dict]]:
    for acct in ("UNIFIED", "CONTRACT"):
        data = _safe_api(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": acct},
            signed=True,
        )
        if data.get("retCode") == 0:
            rows = data.get("result", {}).get("list", []) or [{}]
            coins = rows[0].get("coin", []) if rows else []
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
            equity = float(usdt.get("equity", usdt.get("walletBalance", 0)) or 0)
            if equity > 0:
                return equity, data
    return 0.0, None


def get_klines(
    symbol: str,
    interval: str = "60",
    limit: int = 100,
    category: str = "linear",
) -> List[list]:
    data = _safe_api(
        "GET",
        "/v5/market/kline",
        params={
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": str(limit),
        },
        signed=False,
    )
    if data.get("retCode") != 0:
        return []
    klines = data.get("result", {}).get("list", [])
    klines.reverse()  # oldest → newest
    return klines


def _friendly_ret_msg(ret_code: Any, ret_msg: Any) -> str:
    hints = {
        10001: "Invalid parameter — check qty/price/tick size.",
        10002: "Auth/timestamp issue — check API keys & clock sync.",
        110001: "Order not found or already filled/cancelled.",
        110007: "Insufficient available balance.",
        110012: "Insufficient available balance for order.",
        110043: "Leverage not modified (already set) — safe to ignore.",
        110090: "Order value below minimum notional.",
        110094: "Order quantity below minimum.",
    }
    base = str(ret_msg or "unknown error")
    hint = hints.get(int(ret_code) if ret_code is not None else -1)
    return f"{base} ({hint})" if hint else base


# ==============================================================================
# SECTION 5: CORE SMART ORDER ENGINE
# ==============================================================================


def execute_smart_order(
    symbol: str = "BTCUSDT",
    side: str = "Buy",
    order_type: str = "Market",
    entry_price: Any = None,
    leverage: Any = None,
    risk_pct: Any = 1.0,
    qty: Any = None,
    sl_dist: Any = None,
    sl_price: Any = None,
    tp_price: Any = None,
    rr_ratio: Any = 2.0,
    tp_usdt: Any = None,
    sl_usdt: Any = None,
    trailing_stop: Any = None,
    trailing_activation: Any = None,
    reduce_only: bool = False,
    margin_mode: Optional[str] = None,
    position_idx: int = 0,
    time_in_force: str = "GTC",
    sl_trigger_by: str = "Mark",
    tp_trigger_by: str = "Mark",
    order_link_id: Optional[str] = None,
    dry_run: bool = False,
    max_position_usdt: Any = None,
    trigger_price: Any = None,
    trigger_direction: Any = None,
    trigger_by: Any = None,
    category: str = "linear",
    atr_mult: float = 2.0,
    slippage_bps: float = 5.0,
    skip_leverage: bool = False,
) -> dict[str, Any]:
    """
    Core smart order placement with fee-aware position sizing and risk bounds.

    Steps:
        0. Normalize inputs / optional formulas
        1. Instrument specs (cached)
        2. Margin mode & leverage
        3. Market price
        4. Effective entry (+ optional slippage buffer for market risk)
        5. Risk budget
        6. ATR fallback SL
        7. SL bounds
        8. Position sizing (entry fee + SL exit as taker)
        9. TP bounds (TP exit as maker by default)
       10. Build payload & submit (unless dry_run)
    """
    warnings: List[str] = []

    # ------------------------------------------------------------------
    # 0. Input Normalization
    # ------------------------------------------------------------------
    symbol = (symbol or "BTCUSDT").upper().strip()
    category = (category or "linear").lower().strip()
    if category not in ("linear", "inverse", "spot"):
        category = "linear"

    side_l = (side or "buy").lower().strip()
    side = "Buy" if side_l == "buy" else ("Sell" if side_l == "sell" else "Buy")

    ot_l = (order_type or "market").lower().strip()
    order_type = "Limit" if ot_l == "limit" else "Market"

    tif = (time_in_force or "GTC").strip()
    if tif not in ("GTC", "IOC", "FOK", "PostOnly"):
        tif = "GTC"
        warnings.append("Invalid time_in_force; defaulting to GTC.")

    # Balance for formulas (signed call — skip hard fail on dry-run)
    balance, _bal_raw = get_wallet_balance()
    balance_for_math = balance if balance > 0 else 10.0
    if balance <= 0:
        warnings.append(
            "Wallet balance unavailable or zero; using $10 paper balance for risk math."
        )
        balance = 0.0

    def E(v: Any) -> Any:
        return _eval_formula(v, balance_for_math)

    entry_price = E(entry_price)
    leverage = E(leverage)
    risk_pct = E(risk_pct)
    qty = E(qty)
    sl_dist = E(sl_dist)
    sl_price = E(sl_price)
    tp_price = E(tp_price)
    rr_ratio = E(rr_ratio)
    tp_usdt = E(tp_usdt)
    sl_usdt = E(sl_usdt)
    trailing_stop = E(trailing_stop)
    trailing_activation = E(trailing_activation)
    max_position_usdt = E(max_position_usdt)
    trigger_price = E(trigger_price)
    trigger_direction = E(trigger_direction)
    atr_mult = float(E(atr_mult) or 2.0)
    slippage_bps = float(E(slippage_bps) or 0.0)

    entry_price = _safe_float(entry_price)
    leverage_i: Optional[int] = None
    if leverage is not None and str(leverage) != "":
        try:
            leverage_i = int(float(leverage))
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": f"Invalid leverage: {leverage!r}",
            }
    risk_pct = _safe_float(risk_pct, 1.0) or 1.0
    qty = _safe_float(qty)
    sl_dist = _safe_float(sl_dist)
    sl_price = _safe_float(sl_price)
    tp_price = _safe_float(tp_price)
    rr_ratio = _safe_float(rr_ratio, 2.0) or 2.0
    tp_usdt = _safe_float(tp_usdt)
    sl_usdt = _safe_float(sl_usdt)
    trailing_stop = _safe_float(trailing_stop)
    trailing_activation = _safe_float(trailing_activation)
    max_position_usdt = _safe_float(max_position_usdt)
    trigger_price = _safe_float(trigger_price)
    trigger_direction_i: Optional[int] = None
    if trigger_direction is not None and str(trigger_direction) != "":
        try:
            trigger_direction_i = int(float(trigger_direction))
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": "trigger_direction must be 1 (rise) or 2 (fall).",
            }

    if not (0 < risk_pct <= 100):
        return {
            "success": False,
            "error": f"--risk-pct must be in (0, 100], got {risk_pct}.",
        }
    if rr_ratio <= 0:
        return {
            "success": False,
            "error": f"--rr-ratio must be > 0, got {rr_ratio}.",
        }
    if order_link_id and len(str(order_link_id)) > 36:
        return {
            "success": False,
            "error": (
                f"--order-link-id exceeds 36 characters "
                f"(got {len(str(order_link_id))})."
            ),
        }
    if order_type == "Limit" and (entry_price is None or entry_price <= 0):
        return {
            "success": False,
            "error": "Limit orders require a positive --entry-price.",
        }

    sl_trigger_by = _TRIGGER_MAP.get(str(sl_trigger_by or "Mark"), "MarkPrice")
    tp_trigger_by = _TRIGGER_MAP.get(str(tp_trigger_by or "Mark"), "MarkPrice")

    try:
        position_idx = int(position_idx)
    except (TypeError, ValueError):
        position_idx = 0
    if position_idx not in (0, 1, 2):
        position_idx = 0

    # ------------------------------------------------------------------
    # 1. Instrument Specs
    # ------------------------------------------------------------------
    (
        info,
        qty_step,
        tick_size,
        min_qty,
        min_price,
        min_notional,
        max_leverage,
        max_mkt_qty,
    ) = get_instrument_info(symbol, category=category)

    if info and info.get("status") and str(info.get("status")) != "Trading":
        warnings.append(f"Instrument status is {info.get('status')!r}, not Trading.")

    # ------------------------------------------------------------------
    # 2. Margin Mode & Leverage
    # ------------------------------------------------------------------
    if margin_mode and not dry_run:
        mode_l = margin_mode.lower().strip()
        trade_mode = _TRADE_MODE_ISOLATED if mode_l == "isolated" else _TRADE_MODE_CROSS
        # Correct V5 endpoint for per-symbol cross/isolated switch
        body: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "tradeMode": trade_mode,
        }
        if leverage_i:
            body["buyLeverage"] = str(leverage_i)
            body["sellLeverage"] = str(leverage_i)
        sw = _safe_api(
            "POST",
            "/v5/position/switch-isolated",
            params=body,
            signed=True,
        )
        if sw.get("retCode") not in (0, None) and sw.get("retCode") != 0:
            # Non-fatal — some accounts use portfolio margin
            msg = sw.get("retMsg", "")
            if "not modified" not in str(msg).lower():
                warnings.append(f"Margin mode switch: {msg}")

    if leverage_i and leverage_i > 0 and not skip_leverage and not dry_run:
        if max_leverage and leverage_i > max_leverage:
            warnings.append(
                f"Leverage {leverage_i}x capped to instrument max {max_leverage:g}x."
            )
            leverage_i = int(max_leverage)
        lev_params: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(leverage_i),
            "sellLeverage": str(leverage_i),
        }
        # positionIdx only for hedge mode on some endpoints; set-leverage
        # accepts buy/sell leverage pair without idx on one-way.
        lev_res = _safe_api(
            "POST",
            "/v5/position/set-leverage",
            params=lev_params,
            signed=True,
        )
        rc = lev_res.get("retCode")
        if rc not in (0, 110043):  # 110043 = not modified
            warnings.append(
                f"set-leverage: {_friendly_ret_msg(rc, lev_res.get('retMsg'))}"
            )

    # ------------------------------------------------------------------
    # 3. Market Price
    # ------------------------------------------------------------------
    ticker = get_ticker(symbol, category=category)
    if not ticker:
        return {
            "success": False,
            "error": f"Failed to retrieve ticker for {symbol}.",
            "warnings": warnings,
        }

    last_px = _safe_float(ticker.get("lastPrice"), 0.0) or 0.0
    mark_px = _safe_float(ticker.get("markPrice"), last_px) or last_px
    bid_px = _safe_float(ticker.get("bid1Price"), last_px) or last_px
    ask_px = _safe_float(ticker.get("ask1Price"), last_px) or last_px
    current_price = last_px if last_px > 0 else mark_px
    if current_price <= 0:
        return {
            "success": False,
            "error": "Invalid market price from exchange.",
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 4. Effective Entry Price
    # ------------------------------------------------------------------
    is_limit = order_type == "Limit"
    if is_limit and entry_price and entry_price > 0:
        calc_price = float(entry_price)
        if min_price and calc_price < min_price:
            return {
                "success": False,
                "error": (f"Entry {calc_price} below minPrice {min_price}."),
                "warnings": warnings,
            }
    # Market: use ask for buys / bid for sells when available
    elif side == "Buy":
        calc_price = ask_px if ask_px > 0 else current_price
    else:
        calc_price = bid_px if bid_px > 0 else current_price

    # Risk sizing entry buffer for market orders (slippage)
    slip = max(0.0, slippage_bps) / 10_000.0
    risk_entry = calc_price
    if not is_limit and slip > 0:
        risk_entry = (
            calc_price * (1.0 + slip) if side == "Buy" else calc_price * (1.0 - slip)
        )

    # ------------------------------------------------------------------
    # 5. Risk Budget
    # ------------------------------------------------------------------
    if sl_usdt is not None:
        risk_amount = float(sl_usdt)
    else:
        risk_amount = balance_for_math * (risk_pct / 100.0)

    if risk_amount <= 0:
        return {
            "success": False,
            "error": (
                "Computed risk_amount is zero or negative; "
                "check --sl-usdt / --risk-pct / account balance."
            ),
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 6. ATR Fallback SL
    # ------------------------------------------------------------------
    atr: Optional[float] = None
    klines = get_klines(symbol, interval="60", limit=100, category=category)
    if klines:
        try:
            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            atr = _atr_wilder(highs, lows, closes, 14)
        except Exception:
            atr = None
    if not atr or atr <= 0:
        atr = calc_price * 0.01
        warnings.append("ATR unavailable; using 1% price fallback.")

    atr_mult = max(0.1, float(atr_mult))

    # ------------------------------------------------------------------
    # 7. Stop-Loss Boundary
    # ------------------------------------------------------------------
    if sl_price is not None:
        stop_loss = float(sl_price)
    elif sl_dist is not None:
        stop_loss = (
            calc_price - abs(sl_dist) if side == "Buy" else calc_price + abs(sl_dist)
        )
    else:
        stop_loss = (
            calc_price - (atr * atr_mult)
            if side == "Buy"
            else calc_price + (atr * atr_mult)
        )

    if side == "Buy" and stop_loss >= calc_price:
        return {
            "success": False,
            "error": (
                f"Invalid Long Stop Loss ({stop_loss}): Must be < Entry ({calc_price})."
            ),
            "warnings": warnings,
        }
    if side == "Sell" and stop_loss <= calc_price:
        return {
            "success": False,
            "error": (
                f"Invalid Short Stop Loss ({stop_loss}): "
                f"Must be > Entry ({calc_price})."
            ),
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 8. Position Sizing (fee-aware)
    # Entry: maker if limit/PostOnly else taker
    # SL exit: always model as taker (market stop)
    # TP exit: model as maker (limit TP)
    # ------------------------------------------------------------------
    is_maker_entry = tif == "PostOnly" or is_limit
    e_fee = _FEE_MAKER if is_maker_entry else _FEE_TAKER
    x_fee_sl = _FEE_TAKER
    x_fee_tp = _FEE_MAKER

    # Denominator = loss per 1 qty at SL including fees (using risk_entry)
    if side == "Buy":
        denom = risk_entry * (1.0 + e_fee) - stop_loss * (1.0 - x_fee_sl)
    else:
        denom = stop_loss * (1.0 + x_fee_sl) - risk_entry * (1.0 - e_fee)

    if denom > 1e-12:
        qty_calc = risk_amount / denom
    else:
        px_dist = abs(risk_entry - stop_loss)
        qty_calc = risk_amount / px_dist if px_dist > 0 else 0.0
        warnings.append("Fee-adjusted SL distance near zero; using raw price distance.")

    explicit_qty = qty is not None and qty > 0
    if explicit_qty:
        actual_qty = float(qty)  # type: ignore[arg-type]
    else:
        actual_qty = float(format_precision(qty_calc, qty_step, ROUND_DOWN) or 0)

    # Min qty bump + optional SL re-anchor when auto-sizing
    if min_qty and actual_qty < min_qty:
        actual_qty = min_qty
        if not explicit_qty and sl_price is None:
            stop_loss = calculate_exit_price_for_net_loss(
                risk_entry, actual_qty, side, risk_amount, e_fee, x_fee_sl
            )
            warnings.append("Qty raised to minOrderQty; SL re-anchored to risk budget.")
        elif explicit_qty:
            warnings.append(
                "Explicit qty below minOrderQty — raised to exchange minimum; "
                "risk budget may be exceeded."
            )

    qty_str = format_precision(actual_qty, qty_step, ROUND_DOWN)
    actual_qty = float(qty_str) if qty_str else 0.0
    if actual_qty <= 0:
        return {
            "success": False,
            "error": "Quantity quantized to zero; check qty_step / risk inputs.",
            "warnings": warnings,
        }

    # Market max qty
    if not is_limit and max_mkt_qty and actual_qty > max_mkt_qty:
        actual_qty = float(format_precision(max_mkt_qty, qty_step, ROUND_DOWN) or 0)
        qty_str = format_precision(actual_qty, qty_step, ROUND_DOWN)
        warnings.append(f"Qty capped to maxMktOrderQty={max_mkt_qty}.")

    notional = actual_qty * calc_price

    # Notional cap — re-check risk after shrink
    if max_position_usdt and notional > max_position_usdt:
        cap_qty = float(
            format_precision(max_position_usdt / calc_price, qty_step, ROUND_DOWN) or 0
        )
        if cap_qty <= 0:
            return {
                "success": False,
                "error": "max_position_usdt cap reduced qty to zero.",
                "warnings": warnings,
            }
        actual_qty = cap_qty
        qty_str = format_precision(actual_qty, qty_step, ROUND_DOWN)
        actual_qty = float(qty_str) if qty_str else 0.0
        notional = actual_qty * calc_price
        warnings.append(f"Notional capped to max_position_usdt=${max_position_usdt}.")
        # If auto-sized, tighten SL to keep risk; if user SL fixed, warn
        net_at_sl = abs(
            calculate_net_pnl(risk_entry, stop_loss, actual_qty, side, e_fee, x_fee_sl)
        )
        if not explicit_qty and sl_price is None and net_at_sl > risk_amount:
            stop_loss = calculate_exit_price_for_net_loss(
                risk_entry, actual_qty, side, risk_amount, e_fee, x_fee_sl
            )
            warnings.append(
                "SL re-anchored after notional cap to preserve risk budget."
            )
        elif net_at_sl > risk_amount * 1.05:
            warnings.append(
                f"After cap, estimated SL loss ${net_at_sl:.4f} exceeds "
                f"risk budget ${risk_amount:.4f}."
            )

    # Min notional (Bybit linear often $5)
    min_notional = max(float(min_notional or 0), 0.0)
    if min_notional and notional < min_notional:
        if explicit_qty:
            return {
                "success": False,
                "error": (
                    f"Notional ${notional:.2f} < exchange minimum "
                    f"${min_notional:.2f} for {symbol}."
                ),
                "warnings": warnings,
            }
        # Bump qty to satisfy min notional
        need_qty = min_notional / calc_price * 1.01
        bumped = float(
            format_precision(max(need_qty, min_qty or 0), qty_step, ROUND_DOWN) or 0
        )
        # ROUND_DOWN may still be under — step up once
        if bumped * calc_price < min_notional:
            try:
                bumped = float(
                    format_precision(bumped + float(qty_step), qty_step, ROUND_DOWN)
                    or 0
                )
            except Exception:
                pass
        actual_qty = bumped
        qty_str = format_precision(actual_qty, qty_step, ROUND_DOWN)
        actual_qty = float(qty_str) if qty_str else 0.0
        notional = actual_qty * calc_price
        if notional < min_notional:
            return {
                "success": False,
                "error": (
                    f"Cannot reach min notional ${min_notional:.2f} "
                    f"(got ${notional:.2f}). Increase risk or qty."
                ),
                "warnings": warnings,
            }
        warnings.append(f"Qty bumped to satisfy min notional ${min_notional:.2f}.")
        if sl_price is None:
            stop_loss = calculate_exit_price_for_net_loss(
                risk_entry, actual_qty, side, risk_amount, e_fee, x_fee_sl
            )

    # Re-validate SL side after any re-anchor
    if side == "Buy" and stop_loss >= calc_price:
        return {
            "success": False,
            "error": (
                f"SL re-anchor produced invalid long SL ({stop_loss}) "
                f">= entry ({calc_price}). Risk too small vs fees."
            ),
            "warnings": warnings,
        }
    if side == "Sell" and stop_loss <= calc_price:
        return {
            "success": False,
            "error": (
                f"SL re-anchor produced invalid short SL ({stop_loss}) "
                f"<= entry ({calc_price}). Risk too small vs fees."
            ),
            "warnings": warnings,
        }

    stop_loss_str = format_precision(stop_loss, tick_size, ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # 9. Take-Profit Boundary
    # ------------------------------------------------------------------
    if tp_price is not None:
        take_profit = float(tp_price)
        if side == "Buy" and take_profit <= calc_price:
            return {
                "success": False,
                "error": (
                    f"Invalid Long TP ({take_profit}): Must be > Entry ({calc_price})."
                ),
                "warnings": warnings,
            }
        if side == "Sell" and take_profit >= calc_price:
            return {
                "success": False,
                "error": (
                    f"Invalid Short TP ({take_profit}): Must be < Entry ({calc_price})."
                ),
                "warnings": warnings,
            }
    elif tp_usdt is not None:
        take_profit = calculate_exit_price_by_pnl(
            calc_price,
            actual_qty,
            side,
            float(tp_usdt),
            e_fee,
            x_fee_tp,
        )
    else:
        dist = abs(calc_price - float(stop_loss_str))
        take_profit = (
            calc_price + (dist * rr_ratio)
            if side == "Buy"
            else calc_price - (dist * rr_ratio)
        )

    take_profit_str = format_precision(take_profit, tick_size, ROUND_HALF_UP)

    # Gross + net expectations
    exp_profit_gross = calculate_pnl_by_exit_price(
        calc_price, float(take_profit_str), actual_qty, side
    )
    exp_loss_gross = calculate_pnl_by_exit_price(
        calc_price, float(stop_loss_str), actual_qty, side
    )
    exp_profit_net = calculate_net_pnl(
        calc_price,
        float(take_profit_str),
        actual_qty,
        side,
        e_fee,
        x_fee_tp,
    )
    exp_loss_net = calculate_net_pnl(
        risk_entry,
        float(stop_loss_str),
        actual_qty,
        side,
        e_fee,
        x_fee_sl,
    )

    rr_realized = None
    if abs(exp_loss_net) > 1e-12:
        rr_realized = round(abs(exp_profit_net / exp_loss_net), 3)

    # Optional: skip TP if trailing stop only
    include_tp = True
    if trailing_stop and tp_price is None and tp_usdt is None:
        # Still attach RR-based TP as safety unless user wants trail-only;
        # keep TP for risk symmetry (scalper may pass tp_price=None intentionally)
        log.info(
            "Trailing stop enabled without explicit TP; keeping default target profit for safety symmetry."
        )

    # ------------------------------------------------------------------
    # 10. Build Payload & Submit
    # ------------------------------------------------------------------
    params: Dict[str, Any] = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": "Limit" if is_limit else "Market",
        "qty": qty_str,
        "timeInForce": tif,
        "positionIdx": position_idx,
        "slTriggerBy": sl_trigger_by,
        "tpTriggerBy": tp_trigger_by,
        "stopLoss": stop_loss_str,
    }
    # Market orders: Bybit prefers IOC/FOK; GTC on market is often ignored
    if not is_limit and params["timeInForce"] == "GTC":
        params["timeInForce"] = "IOC"

    if include_tp and take_profit_str:
        params["takeProfit"] = take_profit_str

    if is_limit:
        params["price"] = format_precision(calc_price, tick_size, ROUND_HALF_UP)

    if trailing_stop and trailing_stop > 0:
        params["trailingStop"] = format_precision(
            trailing_stop, tick_size, ROUND_HALF_UP
        )
    if trailing_activation and trailing_activation > 0:
        params["activePrice"] = format_precision(
            trailing_activation, tick_size, ROUND_HALF_UP
        )

    # Conditional / stop-order trigger
    if trigger_price is not None and trigger_price > 0:
        params["triggerPrice"] = format_precision(
            trigger_price, tick_size, ROUND_HALF_UP
        )
        if trigger_direction_i in (1, 2):
            params["triggerDirection"] = trigger_direction_i
        else:
            # 1 = rises to, 2 = falls to
            params["triggerDirection"] = 1 if trigger_price >= current_price else 2
        tb_raw = str(trigger_by or "Mark").replace("Price", "")
        tb_raw = tb_raw.capitalize() if tb_raw else "Mark"
        params["triggerBy"] = _TRIGGER_MAP.get(tb_raw, "MarkPrice")
        # Bybit conditional on linear
        params.setdefault("orderFilter", "Order")

    if reduce_only:
        params["reduceOnly"] = True
    if order_link_id:
        params["orderLinkId"] = str(order_link_id)

    result_payload: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "symbol": symbol,
        "side": side,
        "order_type": "Limit" if is_limit else "Market",
        "category": category,
        "qty": actual_qty,
        "entry_price": calc_price,
        "risk_entry_price": risk_entry,
        "stop_loss": float(stop_loss_str),
        "take_profit": float(take_profit_str) if take_profit_str else None,
        "expected_profit_usdt": round(exp_profit_gross, 4),
        "expected_loss_usdt": round(abs(exp_loss_gross), 4),
        "expected_profit_net_usdt": round(exp_profit_net, 4),
        "expected_loss_net_usdt": round(abs(exp_loss_net), 4),
        "risk_amount_usdt": round(risk_amount, 4),
        "rr_ratio": rr_ratio,
        "rr_realized": rr_realized,
        "notional_usdt": round(notional, 4),
        "balance": round(balance if balance > 0 else balance_for_math, 4),
        "leverage": leverage_i,
        "atr": round(atr, 8) if atr else None,
        "fees": {
            "entry": e_fee,
            "sl_exit": x_fee_sl,
            "tp_exit": x_fee_tp,
        },
        "order_params": params,
        "warnings": warnings,
        "version": __version__,
    }

    if dry_run:
        result_payload["note"] = "Dry run active. Order was NOT submitted to exchange."
        return result_payload

    data = _safe_api("POST", "/v5/order/create", params=params, signed=True)
    if data.get("retCode") == 0:
        result_payload.update(
            {
                "order_id": data.get("result", {}).get("orderId"),
                "order_link_id": data.get("result", {}).get("orderLinkId"),
                "data": data.get("result"),
            }
        )
        return result_payload

    return {
        "success": False,
        "error": _friendly_ret_msg(data.get("retCode"), data.get("retMsg")),
        "retCode": data.get("retCode"),
        "order_params": params,
        "warnings": warnings,
        "symbol": symbol,
        "side": side,
        "qty": actual_qty,
        "entry_price": calc_price,
        "stop_loss": float(stop_loss_str),
        "take_profit": float(take_profit_str) if take_profit_str else None,
        "notional_usdt": round(notional, 4),
        "version": __version__,
    }


# ==============================================================================
# SECTION 6: OUTPUT ROUTING
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )
    if out_path in ("/dev/stdout", "/dev/fd/1", "-"):
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


# ==============================================================================
# SECTION 7: PROGRAMMATIC ENTRY POINT
# ==============================================================================


def run(
    symbol: str = "BTCUSDT",
    side: str = "Buy",
    order_type: str = "Market",
    entry_price: Any = None,
    leverage: Any = None,
    risk_pct: Any = 1.0,
    qty: Any = None,
    sl_dist: Any = None,
    sl_price: Any = None,
    tp_price: Any = None,
    rr_ratio: Any = 2.0,
    tp_usdt: Any = None,
    sl_usdt: Any = None,
    trailing_stop: Any = None,
    trailing_activation: Any = None,
    reduce_only: bool = False,
    margin_mode: Optional[str] = None,
    position_idx: int = 0,
    time_in_force: str = "GTC",
    sl_trigger_by: str = "Mark",
    tp_trigger_by: str = "Mark",
    order_link_id: Optional[str] = None,
    dry_run: bool = False,
    max_position_usdt: Any = None,
    trigger_price: Any = None,
    trigger_direction: Any = None,
    trigger_by: Any = None,
    category: str = "linear",
    atr_mult: float = 2.0,
    slippage_bps: float = 5.0,
    skip_leverage: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute Bybit smart order placement with precision risk bounds."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )
        log.debug("run() kwargs symbol=%s side=%s dry_run=%s", symbol, side, dry_run)

    result = execute_smart_order(
        symbol=symbol,
        side=side,
        order_type=order_type,
        entry_price=entry_price,
        leverage=leverage,
        risk_pct=risk_pct,
        qty=qty,
        sl_dist=sl_dist,
        sl_price=sl_price,
        tp_price=tp_price,
        rr_ratio=rr_ratio,
        tp_usdt=tp_usdt,
        sl_usdt=sl_usdt,
        trailing_stop=trailing_stop,
        trailing_activation=trailing_activation,
        reduce_only=reduce_only,
        margin_mode=margin_mode,
        position_idx=position_idx,
        time_in_force=time_in_force,
        sl_trigger_by=sl_trigger_by,
        tp_trigger_by=tp_trigger_by,
        order_link_id=order_link_id,
        dry_run=dry_run,
        max_position_usdt=max_position_usdt,
        trigger_price=trigger_price,
        trigger_direction=trigger_direction,
        trigger_by=trigger_by,
        category=category,
        atr_mult=atr_mult,
        slippage_bps=slippage_bps,
        skip_leverage=skip_leverage,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


# ==============================================================================
# SECTION 8: CLI ARGUMENT PARSER & ENTRYPOINT
# ==============================================================================


def _coerce(val: str) -> Any:
    """Coerce env-var string → None | bool | int | float | str."""
    if val == "":
        return None
    low = val.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    # Keep "1"/"0" as ints for position_idx etc.; flags use true/false
    try:
        if "." not in val and "e" not in low and "E" not in val:
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
        prog="bybit_smart_order.py",
        description=f"Bybit Smart Order Engine v{__version__}",
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--side", default="Buy", choices=["Buy", "Sell", "buy", "sell"])
    parser.add_argument(
        "--order-type",
        default="Market",
        choices=["Market", "Limit", "market", "limit"],
    )
    # Use str for formula-capable numeric fields on CLI too
    parser.add_argument("--entry-price", default=None)
    parser.add_argument("--leverage", default=None)
    parser.add_argument("--risk-pct", default="1.0")
    parser.add_argument("--qty", default=None)
    parser.add_argument("--sl-dist", default=None)
    parser.add_argument("--sl-price", default=None)
    parser.add_argument("--tp-price", default=None)
    parser.add_argument("--rr-ratio", default="2.0")
    parser.add_argument("--tp-usdt", default=None)
    parser.add_argument("--sl-usdt", default=None)
    parser.add_argument("--trailing-stop", default=None)
    parser.add_argument("--trailing-activation", default=None)
    parser.add_argument("--reduce-only", action="store_true")
    parser.add_argument("--margin-mode", choices=["isolated", "cross"])
    parser.add_argument("--position-idx", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument(
        "--time-in-force",
        default="GTC",
        choices=["GTC", "IOC", "FOK", "PostOnly"],
    )
    parser.add_argument(
        "--sl-trigger-by",
        default="Mark",
        choices=["Mark", "Index", "Last"],
    )
    parser.add_argument(
        "--tp-trigger-by",
        default="Mark",
        choices=["Mark", "Index", "Last"],
    )
    parser.add_argument("--order-link-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-position-usdt", default=None)
    parser.add_argument("--trigger-price", dest="trigger_price", default=None)
    parser.add_argument("--trigger-direction", dest="trigger_direction", default=None)
    parser.add_argument(
        "--trigger-by",
        dest="trigger_by",
        choices=["Mark", "Index", "Last"],
        default=None,
    )
    parser.add_argument(
        "--category",
        default="linear",
        choices=["linear", "inverse", "spot"],
    )
    parser.add_argument("--atr-mult", default="2.0")
    parser.add_argument("--slippage-bps", default="5")
    parser.add_argument("--skip-leverage", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


if __name__ == "__main__":
    if any(k.startswith("argc_") for k in os.environ):
        kwargs: dict[str, Any] = {}
        for k, v in os.environ.items():
            if k.startswith("argc_"):
                key = k[5:].replace("-", "_")
                coerced = _coerce(v)
                if coerced is not None:
                    kwargs[key] = coerced
        res = run(**kwargs)
        sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)

    args = _build_parser().parse_args()
    res = run(
        symbol=args.symbol,
        side=args.side,
        order_type=args.order_type,
        entry_price=args.entry_price,
        leverage=args.leverage,
        risk_pct=args.risk_pct,
        qty=args.qty,
        sl_dist=args.sl_dist,
        sl_price=args.sl_price,
        tp_price=args.tp_price,
        rr_ratio=args.rr_ratio,
        tp_usdt=args.tp_usdt,
        sl_usdt=args.sl_usdt,
        trailing_stop=args.trailing_stop,
        trailing_activation=args.trailing_activation,
        reduce_only=args.reduce_only,
        margin_mode=args.margin_mode,
        position_idx=args.position_idx,
        time_in_force=args.time_in_force,
        sl_trigger_by=args.sl_trigger_by,
        tp_trigger_by=args.tp_trigger_by,
        order_link_id=args.order_link_id,
        dry_run=args.dry_run,
        max_position_usdt=args.max_position_usdt,
        trigger_price=args.trigger_price,
        trigger_direction=args.trigger_direction,
        trigger_by=args.trigger_by,
        category=args.category,
        atr_mult=float(args.atr_mult or 2.0),
        slippage_bps=float(args.slippage_bps or 0.0),
        skip_leverage=args.skip_leverage,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)
