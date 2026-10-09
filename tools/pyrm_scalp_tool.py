#!/usr/bin/env python3
# ==============================================================================
# multi_scalper.py — Pyrmethus AIChat Multi-Symbol Scalper Engine v3.2-ASCENDED
# argc/aichat compatible · High-Leverage Orderbook Scalper · Dynamic Risk Engine
#
# @describe Multi-symbol high-leverage orderbook scalper bot for Bybit with
#           L2 depth analysis, Breakeven SL management, circuit breakers,
#           session performance tracking, filter caching, margin budgeting,
#           symbol cooldowns, and multi-factor signal filtering.
#
# @meta require-tools python3
#
# @option --symbols <TEXT>         Comma-separated list of symbols (default: liquid alts)
# @option --pos-pct <NUM>          Position value as % of account balance (default: 0)
# @option --pos-value <NUM>        USDT value per position (default: 50.0)
# @option --leverage <NUM>         Position leverage (default: 50)
# @option --target <NUM>           Target net profit in USDT (default: 0.04)
# @option --risk-reward <NUM>      Risk to reward ratio (default: 1.0)
# @option --trailing-stop <NUM>    Optional trailing stop distance
# @option --loop-delay <NUM>       Seconds between cycles in loop mode (default: 30)
# @option --max-workers <NUM>      Thread pool size for symbol analysis (default: 8)
# @option --max-positions <NUM>    Max concurrent open positions (default: 5)
# @option --cooldown <NUM>         Seconds before re-entering same symbol (default: 120)
# @option --spread-max-bps <NUM>   Max allowed spread in bps (default: 15)
# @flag   --loop                   Run continuously in a loop
# @flag   --dry-run                Perform analysis without submitting live orders
# @flag   --no-color               Disable ANSI color output
# @flag   --verbose                Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout      Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import concurrent.futures
import enum
import json
import logging
import os
import re
import signal
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 24-digit precision for crypto arithmetic
getcontext().prec = 24

CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# ---------------------------------------------------------------------------
# Sub-Module Imports (optional — graceful degradation)
# ---------------------------------------------------------------------------
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
    import micro_profit
except ImportError:
    micro_profit = None

try:
    import bybit_wbta
except ImportError:
    bybit_wbta = None

try:
    import scientific_calculator
except ImportError:
    scientific_calculator = None

__version__ = "3.2.0-ASCENDED"
__all__ = [
    "CircuitBreaker",
    "FilterCache",
    "MarginBudget",
    "PerformanceTracker",
    "__version__",
    "manage_positions",
    "process_symbol",
    "run",
    "run_scalper_cycle",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = CURRENT_DIR / "scalper.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("multi_scalper")

SESSION_START_MS = int(time.time() * 1000)
JOURNAL_FILE = CURRENT_DIR / "trade_journal.jsonl"
_SHUTDOWN = threading.Event()

# Default liquid linear perps (avoid delisted MATIC/FTM tickers)
_DEFAULT_SYMBOLS = (
    "XRPUSDT,ADAUSDT,DOGEUSDT,TRXUSDT,SOLUSDT,"
    "LINKUSDT,AVAXUSDT,DOTUSDT,ATOMUSDT,NEARUSDT,"
    "LTCUSDT,BCHUSDT,APTUSDT,ARBUSDT,OPUSDT"
)

# Fee constants (Bybit linear VIP0 defaults)
_TAKER_FEE = 0.00055
_MAKER_FEE = 0.00020

# ==============================================================================
# SECTION 1: Exit Codes & JSON Serializer
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2


class ToolJSONEncoder(json.JSONEncoder):
    """Safe JSON encoder for Decimal, Path, Enum, datetime, timedelta, set."""

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
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal UI
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
    data: Dict[str, Any],
    no_color: bool = False,
) -> None:
    """Render a human-friendly box UI to stderr."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", True)
    status_color = NEON_GREEN if success else NEON_RED
    status_sym = "✓" if success else "✗"
    status_text = "RUNNING" if success else "FAILED"
    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_PINK}⚡ [MULTI-SYMBOL SCALPER v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_sym} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Wallet Balance:{RESET}  "
        f"${data.get('balance', 0.0):.2f} USDT"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Realized PnL:{RESET}    "
        f"${data.get('session_realized', 0.0):+.4f} USDT"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Unrealized PnL:{RESET}  "
        f"${data.get('session_unrealized', 0.0):+.4f} USDT"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Target/Trade:{RESET}    "
        f"${data.get('target', 0.04):.4f} USDT"
        f"  |  {NEON_CYAN}Leverage:{RESET} {data.get('leverage', 50)}x"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} "
        f"{NEON_CYAN}Orders Placed:{RESET}   "
        f"{data.get('orders_placed', 0)}"
        f"  |  {NEON_CYAN}Cycle:{RESET} {data.get('cycle_ms', 0)} ms"
    )

    perf = data.get("performance") or {}
    if perf:
        wins = perf.get("wins", 0)
        losses = perf.get("losses", 0)
        total = wins + losses
        wr = f"{wins / total:.1%}" if total else "N/A"
        _cprint(
            f"{NEON_PURPLE}│{RESET} "
            f"{NEON_CYAN}Signals:{RESET}         "
            f"{wr}  ({wins}W / {losses}L est.)  "
            f"Avg: ${perf.get('avg_profit', 0.0):+.4f}"
        )

    open_syms = data.get("open_symbols") or []
    if open_syms:
        _cprint(
            f"{NEON_PURPLE}│{RESET} "
            f"{NEON_CYAN}Open Positions:{RESET}  "
            f"{', '.join(open_syms[:8])}"
            f"{'…' if len(open_syms) > 8 else ''}"
        )

    skipped = data.get("skipped_symbols") or {}
    if skipped:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}{BOLD}Skipped ({len(skipped)}):{RESET}"
        )
        for sym, reason in list(skipped.items())[:6]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_YELLOW}⚑{RESET} {sym}: {reason}")
        if len(skipped) > 6:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(skipped) - 6} more{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: PRECISION MATH
# ==============================================================================


def _d(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    s = str(value).replace("\x00", "").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def format_precision(
    value: Any,
    step: Any,
    rounding_mode: str = ROUND_HALF_UP,
) -> str:
    """Quantize value to exchange step without float drift."""
    if value is None or value == "":
        return ""
    if step is None or float(step) <= 0:
        return format(Decimal(str(value)), "f")
    try:
        val_d = Decimal(str(value))
        step_d = Decimal(str(step))
        return format(val_d.quantize(step_d, rounding=rounding_mode), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return float(price)
    p_d = Decimal(str(price))
    t_d = Decimal(str(tick_size))
    return float((p_d / t_d).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * t_d)


def quantize_qty(qty: float, qty_step: float, rounding=ROUND_DOWN) -> float:
    """Floor quantity to qty_step (exchange-safe)."""
    if qty_step <= 0:
        return float(qty)
    return float(Decimal(str(qty)).quantize(Decimal(str(qty_step)), rounding=rounding))


# ==============================================================================
# SECTION 4: CIRCUIT BREAKER · PERFORMANCE · CACHE · MARGIN · COOLDOWN
# ==============================================================================


@dataclass
class CircuitBreaker:
    """Per-symbol consecutive-failure breaker with timed recovery."""

    threshold: int = 3
    reset_seconds: float = 300.0
    _failures: Dict[str, int] = field(default_factory=dict)
    _opened_at: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_failure(self, symbol: str) -> None:
        with self._lock:
            self._failures[symbol] = self._failures.get(symbol, 0) + 1
            if self._failures[symbol] >= self.threshold:
                if symbol not in self._opened_at:
                    self._opened_at[symbol] = time.monotonic()
                    log.warning(
                        "[CircuitBreaker] %s OPEN after %d failures (retry in %.0fs).",
                        symbol,
                        self.threshold,
                        self.reset_seconds,
                    )

    def record_success(self, symbol: str) -> None:
        with self._lock:
            self._failures.pop(symbol, None)
            self._opened_at.pop(symbol, None)

    def is_open(self, symbol: str) -> bool:
        with self._lock:
            opened = self._opened_at.get(symbol)
            if opened is None:
                return False
            elapsed = time.monotonic() - opened
            if elapsed >= self.reset_seconds:
                log.info(
                    "[CircuitBreaker] %s HALF-OPEN after %.0fs.",
                    symbol,
                    elapsed,
                )
                del self._opened_at[symbol]
                self._failures[symbol] = 0
                return False
            return True


@dataclass
class PerformanceTracker:
    """
    Thread-safe signal/outcome accumulator.

    Records expected edge at entry time (not fill-confirmed PnL).
    Labelled as estimates in the UI.
    """

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _profits: List[float] = field(default_factory=list)
    _wins: int = 0
    _losses: int = 0
    _orders: int = 0

    def record_signal(self, expected_pnl: float) -> None:
        with self._lock:
            self._profits.append(expected_pnl)
            self._orders += 1
            if expected_pnl >= 0:
                self._wins += 1
            else:
                self._losses += 1

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            total = self._wins + self._losses
            total_pnl = sum(self._profits)
            avg = total_pnl / total if total else 0.0
            sharpe = 0.0
            if len(self._profits) >= 2:
                try:
                    stdev = statistics.stdev(self._profits)
                    sharpe = (avg / stdev) if stdev > 0 else 0.0
                except statistics.StatisticsError:
                    pass
            return {
                "wins": self._wins,
                "losses": self._losses,
                "total_signals": total,
                "orders_placed": self._orders,
                "total_expected_pnl": round(total_pnl, 4),
                "avg_profit": round(avg, 4),
                "sharpe_proxy": round(sharpe, 3),
            }


@dataclass
class FilterCache:
    """TTL cache for instrument filters to avoid API storms."""

    ttl_seconds: float = 300.0
    _data: Dict[str, Tuple[float, Tuple]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, symbol: str) -> Optional[Tuple]:
        with self._lock:
            item = self._data.get(symbol)
            if item is None:
                return None
            ts, value = item
            if time.monotonic() - ts > self.ttl_seconds:
                del self._data[symbol]
                return None
            return value

    def put(self, symbol: str, value: Tuple) -> None:
        with self._lock:
            self._data[symbol] = (time.monotonic(), value)


@dataclass
class MarginBudget:
    """
    Thread-safe remaining-margin allocator.

    Prevents concurrent workers from collectively exceeding a fraction
    of account equity.
    """

    _remaining: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self, total: float) -> None:
        with self._lock:
            self._remaining = max(0.0, total)

    def try_reserve(self, amount: float) -> bool:
        with self._lock:
            if amount <= 0:
                return True
            if amount > self._remaining:
                return False
            self._remaining -= amount
            return True

    def release(self, amount: float) -> None:
        with self._lock:
            self._remaining += max(0.0, amount)

    @property
    def remaining(self) -> float:
        with self._lock:
            return self._remaining


@dataclass
class SymbolCooldown:
    """Block re-entry on a symbol for N seconds after a fill/submit."""

    cooldown_seconds: float = 120.0
    _last_entry: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def mark(self, symbol: str) -> None:
        with self._lock:
            self._last_entry[symbol] = time.monotonic()

    def is_cooling(self, symbol: str) -> bool:
        with self._lock:
            t = self._last_entry.get(symbol)
            if t is None:
                return False
            return (time.monotonic() - t) < self.cooldown_seconds

    def remaining(self, symbol: str) -> float:
        with self._lock:
            t = self._last_entry.get(symbol)
            if t is None:
                return 0.0
            left = self.cooldown_seconds - (time.monotonic() - t)
            return max(0.0, left)


# Module singletons
_circuit_breaker = CircuitBreaker()
_perf_tracker = PerformanceTracker()
_filter_cache = FilterCache(ttl_seconds=300.0)
_margin_budget = MarginBudget()
_cooldown = SymbolCooldown(cooldown_seconds=120.0)
_orders_this_cycle = 0
_orders_lock = threading.Lock()


# ==============================================================================
# SECTION 5: BYBIT API HELPERS
# ==============================================================================


def set_symbol_leverage(symbol: str, leverage: int) -> bool:
    if not bybit_core:
        return False
    try:
        res = bybit_core.api_request(
            "POST",
            "/v5/position/set-leverage",
            params={
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
            signed=True,
        )
        msg = str(res.get("retMsg", "")).lower()
        ok = res.get("retCode") == 0 or "already" in msg or "not modified" in msg
        if not ok:
            import re

            match = re.search(
                r"maxLeverage\s*\[(\d+)\]", res.get("retMsg", ""), re.IGNORECASE
            )
            if match:
                val = int(match.group(1))
                max_lev = val // 100 if val >= 100 else val
                if 0 < max_lev < leverage:
                    log.info(
                        "[%s] Auto-adjusting leverage %dx -> %dx (risk limit)",
                        symbol,
                        leverage,
                        max_lev,
                    )
                    return set_symbol_leverage(symbol, max_lev)

            log.warning(
                "[%s] set leverage %dx failed: %s",
                symbol,
                leverage,
                res.get("retMsg"),
            )
        return ok
    except Exception as exc:
        log.error("[%s] leverage exception: %s", symbol, exc)
        return False


def get_wallet_balance() -> float:
    """USDT equity from UNIFIED, then CONTRACT."""
    if not bybit_core:
        return 0.0
    for acct in ("UNIFIED", "CONTRACT"):
        try:
            res = bybit_core.api_request(
                "GET",
                "/v5/account/wallet-balance",
                params={"accountType": acct},
                signed=True,
            )
            if res.get("retCode") != 0:
                continue
            for acct_row in res.get("result", {}).get("list", []) or []:
                for coin in acct_row.get("coin", []) or []:
                    if coin.get("coin") == "USDT":
                        val = float(
                            coin.get("equity", coin.get("walletBalance", 0)) or 0
                        )
                        if val > 0:
                            log.debug("Balance %s: $%.2f", acct, val)
                            return val
        except Exception as exc:
            log.debug("Balance fetch (%s) failed: %s", acct, exc)
    return 0.0


def get_symbol_filters(
    symbol: str,
) -> Tuple[float, float, float, float]:
    """
    Return (qty_step, min_qty, tick_size, min_notional).

    Cached for FilterCache.ttl_seconds to cut redundant instrument calls.
    """
    cached = _filter_cache.get(symbol)
    if cached is not None:
        return cached  # type: ignore[return-value]

    qty_step, min_qty, tick_size, min_notional = 0.001, 0.001, 0.0001, 5.0

    if not bybit_core:
        result = (qty_step, min_qty, tick_size, min_notional)
        _filter_cache.put(symbol, result)
        return result

    try:
        if hasattr(bybit_core, "get_instruments_info"):
            res = bybit_core.get_instruments_info(category="linear", symbol=symbol)
        else:
            res = bybit_core.api_request(
                "GET",
                "/v5/market/instruments-info",
                params={"category": "linear", "symbol": symbol},
            )

        if res.get("retCode") == 0:
            lst = res.get("result", {}).get("list", [])
            if lst:
                info = lst[0]
                lot = info.get("lotSizeFilter", {}) or {}
                price = info.get("priceFilter", {}) or {}
                qty_step = float(lot.get("qtyStep", qty_step) or qty_step)
                min_qty = float(lot.get("minOrderQty", min_qty) or min_qty)
                tick_size = float(price.get("tickSize", tick_size) or tick_size)

                # min notional — lotSizeFilter or leverageFilter
                for filt in (lot, info.get("leverageFilter", {}) or {}):
                    for key in (
                        "minNotionalValue",
                        "minOrderAmt",
                        "notionalValue",
                    ):
                        raw = filt.get(key)
                        if raw not in (None, ""):
                            try:
                                min_notional = float(raw)
                                break
                            except (TypeError, ValueError):
                                pass
                    if min_notional and min_notional != 5.0:
                        break
    except Exception as exc:
        log.debug("[%s] get_symbol_filters: %s", symbol, exc)

    result = (qty_step, min_qty, tick_size, min_notional)
    _filter_cache.put(symbol, result)
    return result


def detect_sr_walls(
    bids: List[Any],
    asks: List[Any],
    tick_size: float,
    best_bid: float,
    best_ask: float,
) -> Tuple[float, float]:
    """
    Nearest S/R walls via volume z-score (or scientific_calculator stats).

    Falls back to 15-tick offset when walls are absent.
    """

    def _wall_price(levels: List[Any], side: str) -> Optional[float]:
        sizes = [float(lv[1]) for lv in levels if len(lv) >= 2]
        if len(sizes) < 2:
            return None

        mean = stdev = mx = 0.0
        # Prefer scientific_calculator when present
        if scientific_calculator:
            try:
                stats = scientific_calculator.execute_tool(
                    mode="stats", data=sizes
                ).get("result", {})
                mean = float(stats.get("mean", 0) or 0)
                stdev = float(stats.get("stdev", 0) or 0)
                mx = float(stats.get("max", 0) or 0)
            except Exception:
                mean = statistics.mean(sizes)
                try:
                    stdev = statistics.stdev(sizes)
                except statistics.StatisticsError:
                    stdev = 0.0
                mx = max(sizes)
        else:
            mean = statistics.mean(sizes)
            try:
                stdev = statistics.stdev(sizes)
            except statistics.StatisticsError:
                stdev = 0.0
            mx = max(sizes)

        threshold = mean + 1.5 * stdev if stdev > 0 else mean * 1.5
        # Require the wall to actually stand out
        if mx < threshold:
            return None
        for lv in levels:
            if len(lv) >= 2 and float(lv[1]) >= threshold:
                return float(lv[0])
        return None

    support = _wall_price(bids, "bid")
    resistance = _wall_price(asks, "ask")

    final_support = support if support is not None else best_bid - 15 * tick_size
    final_resistance = (
        resistance if resistance is not None else best_ask + 15 * tick_size
    )

    if abs(final_support - best_bid) < 4 * tick_size:
        final_support = best_bid - 4 * tick_size
    if abs(final_resistance - best_ask) < 4 * tick_size:
        final_resistance = best_ask + 4 * tick_size

    return final_support, final_resistance


def get_active_positions() -> List[Dict[str, Any]]:
    if not bybit_core:
        return []
    try:
        if hasattr(bybit_core, "get_positions"):
            res = bybit_core.get_positions(
                category="linear", settle_coin="USDT", limit=50
            )
        else:
            res = bybit_core.api_request(
                "GET",
                "/v5/position/list",
                params={"category": "linear", "settleCoin": "USDT"},
                signed=True,
            )
        if res.get("retCode") == 0:
            return [
                p
                for p in res.get("result", {}).get("list", [])
                if float(p.get("size", 0) or 0) > 0
            ]
    except Exception as exc:
        log.error("get_active_positions: %s", exc)
    return []


def open_position_symbols(positions: Optional[List[Dict]] = None) -> set:
    """Set of symbols that already have non-zero size."""
    pos = positions if positions is not None else get_active_positions()
    return {str(p.get("symbol", "")).upper() for p in pos if p.get("symbol")}


def log_trade(action: str, symbol: str, details: Dict[str, Any]) -> None:
    """O(1) JSONL append journal."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "symbol": symbol,
        "details": details,
    }
    try:
        line = json.dumps(entry, ensure_ascii=False, cls=ToolJSONEncoder)
        with open(JOURNAL_FILE, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception as exc:
        log.error("journal write failed: %s", exc)


# ==============================================================================
# SECTION 6: POSITION MANAGER (BREAKEVEN)
# ==============================================================================


def manage_positions(target_profit: float) -> float:
    """
    Move SL to fee-aware breakeven once net unrealized clears trigger.

    Returns aggregate net unrealized PnL (fees deducted).
    """
    positions = get_active_positions()
    if not positions:
        return 0.0

    total_unrealized = 0.0
    log.info("[Position Manager] %d open position(s)", len(positions))

    for pos in positions:
        if _SHUTDOWN.is_set():
            break
        try:
            symbol = pos["symbol"]
            size = float(pos.get("size", 0) or 0)
            entry_price = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0) or 0)
            side = str(pos.get("side", "")).capitalize()
            unr_pnl = float(pos.get("unrealisedPnl", 0) or 0)
            current_sl = float(pos.get("stopLoss", 0) or 0)
            pos_idx = int(pos.get("positionIdx", 0) or 0)

            if size <= 0 or entry_price <= 0:
                continue

            entry_fee = size * entry_price * _TAKER_FEE
            exit_fee = size * entry_price * _MAKER_FEE
            total_fees = entry_fee + exit_fee
            net_pnl = unr_pnl - total_fees
            total_unrealized += net_pnl

            log.info(
                "[%s] %s size=%.4f entry=%.6f netPnL=$%+.4f fees=$%.4f",
                symbol,
                side,
                size,
                entry_price,
                net_pnl,
                total_fees,
            )

            trigger = max(0.003, target_profit * 0.10)
            if net_pnl < trigger:
                continue

            _, _, tick_size, _ = get_symbol_filters(symbol)

            # SL fill is taker — both legs taker for BE offset
            be_per_unit = entry_price * _TAKER_FEE * 2

            # UPGRADE: Dynamic Trailing SL
            # If net profit exceeds 50% of the target, lock in half of the accumulated profit
            lock_in_profit = 0.0
            if net_pnl > (target_profit * 0.5):
                mark_price = float(pos.get("markPrice", entry_price) or entry_price)
                if mark_price > 0:
                    lock_in_profit = abs(mark_price - entry_price) * 0.5

            if side == "Buy":
                be_price = round_to_tick(
                    entry_price + be_per_unit + lock_in_profit + tick_size, tick_size
                )
                already = current_sl >= be_price > 0
            else:
                be_price = round_to_tick(
                    entry_price - be_per_unit - lock_in_profit - tick_size, tick_size
                )
                already = 0 < current_sl <= be_price

            if already:
                # Only debug log if it's purely BE, else it might spam if trailing
                if lock_in_profit == 0.0:
                    log.debug("[%s] SL already at/beyond BE", symbol)
                continue
            if not bybit_core:
                continue

            be_str = format_precision(be_price, tick_size)
            log_type = "Trailing SL" if lock_in_profit > 0 else "BE trigger"
            log.info(
                "[%s] %s ($%.4f net PnL) → Moving SL to %s",
                symbol,
                log_type,
                net_pnl,
                be_str,
            )
            res = bybit_core.api_request(
                "POST",
                "/v5/position/trading-stop",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "stopLoss": be_str,
                    "positionIdx": pos_idx,
                },
                signed=True,
            )
            if res.get("retCode") == 0:
                log.info("[%s] SL → breakeven OK", symbol)
                log_trade(
                    "move_sl_to_breakeven",
                    symbol,
                    {
                        "entry_price": entry_price,
                        "new_sl": be_str,
                        "net_pnl": net_pnl,
                    },
                )
            else:
                log.warning("[%s] SL amend failed: %s", symbol, res.get("retMsg"))
        except Exception as exc:
            log.error("manage_positions %s: %s", pos.get("symbol", "?"), exc)

    return total_unrealized


# ==============================================================================
# SECTION 7: SYMBOL PROCESSOR
# ==============================================================================


def process_symbol(
    symbol: str,
    target_value_usdt: float,
    leverage: int,
    risk_reward: float,
    target: float,
    trailing_stop: Optional[float],
    balance: float,
    dry_run: bool,
    skipped: Dict[str, str],
    skipped_lock: threading.Lock,
    occupied: set,
    spread_max_bps: float = 15.0,
) -> None:
    """Analyze one symbol; submit order when multi-factor signal aligns."""

    def _skip(reason: str) -> None:
        log.info("[%s] skip: %s", symbol, reason)
        with skipped_lock:
            skipped[symbol] = reason

    symbol = symbol.upper().strip()
    tag = f"[{'DRY-RUN ' if dry_run else ''}{symbol}]"

    if _SHUTDOWN.is_set():
        _skip("shutdown requested")
        return

    if _circuit_breaker.is_open(symbol):
        _skip("circuit breaker open")
        return

    if symbol in occupied:
        _skip("already has open position")
        return

    if _cooldown.is_cooling(symbol):
        _skip(f"cooldown ({_cooldown.remaining(symbol):.0f}s left)")
        return

    log.info("%s analysing…", tag)

    if not dry_run and not set_symbol_leverage(symbol, leverage):
        _skip("leverage config failed")
        _circuit_breaker.record_failure(symbol)
        return

    try:
        # ---- Orderbook ---------------------------------------------------
        if bybit_core and hasattr(bybit_core, "get_orderbook"):
            ob = bybit_core.get_orderbook(symbol=symbol, limit=25)
        elif bybit_core:
            ob = bybit_core.api_request(
                "GET",
                "/v5/market/orderbook",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "limit": "25",
                },
            )
        else:
            _skip("bybit_core unavailable")
            return

        if ob.get("retCode") != 0:
            _skip(f"orderbook error: {ob.get('retMsg')}")
            _circuit_breaker.record_failure(symbol)
            return

        bids = ob.get("result", {}).get("b", []) or []
        asks = ob.get("result", {}).get("a", []) or []
        if not bids or not asks:
            _skip("empty book")
            return

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            _skip("invalid bid/ask")
            return

        spread_bps = ((best_ask - best_bid) / best_bid) * 10_000.0
        if spread_bps > spread_max_bps:
            _skip(f"spread {spread_bps:.1f}bps > {spread_max_bps}")
            return

        # ---- Decay-weighted imbalance ------------------------------------
        bid_w = sum(float(b[1]) / (i + 1) for i, b in enumerate(bids))
        ask_w = sum(float(a[1]) / (i + 1) for i, a in enumerate(asks))
        tot_w = bid_w + ask_w
        imbalance = bid_w / tot_w if tot_w > 0 else 0.5

        # ---- WBTA --------------------------------------------------------
        trend_sig = "NEUTRAL"
        l2_bulls = l2_bears = price_velo = agg_ratio = 0.0

        if bybit_wbta:
            try:
                wb = bybit_wbta.run(
                    symbol=symbol,
                    interval="15",
                    once=True,
                    json_out=True,
                    silent=True,
                )
                if isinstance(wb, dict):
                    trend_sig = str(
                        (wb.get("trading_signal") or {}).get("action", "NEUTRAL")
                    ).upper()
                    l2 = wb.get("l2_signal") or {}
                    if isinstance(l2, dict):
                        l2_bulls = float(l2.get("bulls", 0) or 0)
                        l2_bears = float(l2.get("bears", 0) or 0)
                    tr = wb.get("trades") or {}
                    if isinstance(tr, dict):
                        price_velo = float(tr.get("price_velocity", 0) or 0)
                        agg_ratio = float(tr.get("aggressor_ratio", 0) or 0)
            except Exception as err:
                log.warning("%s WBTA error: %s", tag, err)

        net_l2 = l2_bulls - l2_bears
        denom = max(1.0, abs(net_l2) + abs(bid_w - ask_w))
        momentum = (
            (net_l2 / denom)
            + max(-1.0, min(1.0, agg_ratio - 1.0))
            + max(-1.0, min(1.0, price_velo * 10.0))
        )

        log.debug(
            "%s imb=%.3f trend=%s mom=%.3f spread=%.1fbps",
            tag,
            imbalance,
            trend_sig,
            momentum,
            spread_bps,
        )

        # ---- Direction ---------------------------------------------------
        if imbalance > 0.55:
            side = "Buy"
            entry_price = best_ask
            if "SELL" in trend_sig or momentum < -0.1:
                _skip(f"Buy conflict trend={trend_sig} mom={momentum:+.3f}")
                return
        elif imbalance < 0.45:
            side = "Sell"
            entry_price = best_bid
            if "BUY" in trend_sig or momentum > 0.1:
                _skip(f"Sell conflict trend={trend_sig} mom={momentum:+.3f}")
                return
        else:
            _skip(f"neutral book ({imbalance:.1%})")
            return

        # ---- Filters & sizing --------------------------------------------
        qty_step, min_qty, tick_size, min_notional = get_symbol_filters(symbol)
        min_notional = max(float(min_notional or 5.0), 5.0)

        support, resistance = detect_sr_walls(bids, asks, tick_size, best_bid, best_ask)

        target_val = max(float(target_value_usdt), min_notional + 0.05)

        # UPGRADE: Dynamic Momentum Sizing
        # Bet 50% heavier if we have high confluence between extreme orderbook imbalance and strong momentum
        is_strong_buy = imbalance >= 0.70 and momentum >= 0.8
        is_strong_sell = imbalance <= 0.30 and momentum <= -0.8
        if is_strong_buy or is_strong_sell:
            target_val *= 1.50
            log.info(
                "[%s] 🚀 STRONG MOMENTUM DETECTED! Scaling position size by 1.5x",
                symbol,
            )

        # qty from notional, quantized to qty_step
        raw_qty = target_val / entry_price
        qty = max(min_qty, quantize_qty(raw_qty, qty_step, ROUND_DOWN))

        # Bump until notional satisfied (bounded)
        for _ in range(50):
            if qty * entry_price >= min_notional:
                break
            qty = quantize_qty(qty + qty_step, qty_step, ROUND_DOWN)

        if qty * entry_price < min_notional:
            _skip(
                f"cannot reach min notional ${min_notional:.2f} "
                f"with qty_step={qty_step}"
            )
            return

        required_margin = (qty * entry_price) / max(leverage, 1)

        # Per-symbol hard cap vs equity
        if required_margin > balance * 0.70:
            _skip(f"margin ${required_margin:.2f} > 70% equity ${balance * 0.70:.2f}")
            return

        # Cross-thread margin budget (prevents over-commit)
        if not _margin_budget.try_reserve(required_margin):
            _skip(
                f"margin budget exhausted "
                f"(need ${required_margin:.2f}, "
                f"left ${_margin_budget.remaining:.2f})"
            )
            return

        margin_reserved = True
        try:
            # ---- Exit solver ---------------------------------------------
            metrics: Dict[str, Any] = {}
            if micro_profit:
                try:
                    metrics = micro_profit.calculate_micro_profit(
                        symbol=symbol,
                        side=side.lower(),
                        qty=qty,
                        leverage=leverage,
                        target=target,
                        risk_reward=risk_reward,
                        use_vwap_entry=True,
                        bids_json=json.dumps(bids),
                        asks_json=json.dumps(asks),
                    )
                except Exception as err:
                    log.warning("%s micro_profit: %s", tag, err)

            entry_calc = float(metrics.get("entry_price", entry_price))
            if entry_calc <= 0:
                entry_calc = entry_price

            default_tp = entry_calc * 1.005 if side == "Buy" else entry_calc * 0.995
            default_sl = entry_calc * 0.995 if side == "Buy" else entry_calc * 1.005
            tp_price = float(metrics.get("target_exit_price", default_tp))
            sl_price = float(metrics.get("stop_loss_price", default_sl))

            # Clamp to S/R
            if side == "Buy":
                tp_price = round_to_tick(
                    max(
                        min(tp_price, resistance - tick_size),
                        entry_calc + 2 * tick_size,
                    ),
                    tick_size,
                )
                sl_price = round_to_tick(
                    min(
                        sl_price,
                        support - 2 * tick_size,
                        entry_calc - 2 * tick_size,
                    ),
                    tick_size,
                )
            else:
                tp_price = round_to_tick(
                    min(
                        max(tp_price, support + tick_size),
                        entry_calc - 2 * tick_size,
                    ),
                    tick_size,
                )
                sl_price = round_to_tick(
                    max(
                        sl_price,
                        resistance + 2 * tick_size,
                        entry_calc + 2 * tick_size,
                    ),
                    tick_size,
                )

            # Fee-aware BE guard on TP
            fees = qty * entry_calc * (_TAKER_FEE + _MAKER_FEE)
            if side == "Buy":
                be_tp = entry_calc + (fees / qty)
                if tp_price < be_tp:
                    _skip(f"TP {tp_price:.6f} < BE {be_tp:.6f}")
                    return
            else:
                be_tp = entry_calc - (fees / qty)
                if tp_price > be_tp:
                    _skip(f"TP {tp_price:.6f} > BE {be_tp:.6f}")
                    return

            # Sanity: SL on correct side of entry
            if side == "Buy" and sl_price >= entry_calc:
                _skip(f"invalid long SL {sl_price} >= entry")
                return
            if side == "Sell" and sl_price <= entry_calc:
                _skip(f"invalid short SL {sl_price} <= entry")
                return

            qty_str = format_precision(qty, qty_step, ROUND_DOWN)
            tp_str = format_precision(tp_price, tick_size, ROUND_HALF_UP)
            sl_str = format_precision(sl_price, tick_size, ROUND_HALF_UP)

            # Use the formatted string directly to avoid float precision drift
            final_qty_val = float(qty_str) if qty_str else 0.0
            if final_qty_val <= 0:
                _skip("qty quantized to zero")
                return

            notional = final_qty_val * entry_calc
            log.info(
                "%s %s qty=%s ($%.2f) TP=%s SL=%s",
                tag,
                side,
                qty_str,
                notional,
                tp_str,
                sl_str,
            )

            # ---- Submit --------------------------------------------------
            if not bybit_smart_order:
                log.info("%s bybit_smart_order missing — analysis only", tag)
                return

            log.info("%s submitting smart order…", tag)
            order_kwargs: Dict[str, Any] = {
                "symbol": symbol,
                "side": side,
                "qty": qty_str,  # PASS STRING DIRECTLY
                "order_type": "Market",
                "sl_price": float(sl_str),
                "leverage": leverage,
                "dry_run": dry_run,
            }
            if trailing_stop:
                order_kwargs["trailing_stop"] = trailing_stop
            else:
                order_kwargs["tp_price"] = float(tp_str)

            res = bybit_smart_order.run(**order_kwargs)
            log.info(
                "%s result: %s",
                tag,
                json.dumps(res, cls=ToolJSONEncoder),
            )

            if res.get("success"):
                _circuit_breaker.record_success(symbol)
                _cooldown.mark(symbol)
                exp = float(res.get("expected_profit_usdt", target) or target)
                _perf_tracker.record_signal(exp if not dry_run else 0.0)
                global _orders_this_cycle
                with _orders_lock:
                    _orders_this_cycle += 1
                if not dry_run:
                    log_trade("execute_order", symbol, res)
                # Keep margin reserved on success (position is live)
                margin_reserved = False  # don't release below
            else:
                _circuit_breaker.record_failure(symbol)
                # release margin on failed submit
        finally:
            if margin_reserved:
                _margin_budget.release(required_margin)

    except Exception as exc:
        log.error("%s exception: %s", tag, exc, exc_info=True)
        _circuit_breaker.record_failure(symbol)


# ==============================================================================
# SECTION 8: ORCHESTRATOR
# ==============================================================================


def run_scalper_cycle(
    symbols: List[str],
    target_value_usdt: float = 50.0,
    leverage: int = 50,
    risk_reward: float = 1.0,
    pos_pct: float = 0.0,
    target: float = 0.04,
    trailing_stop: Optional[float] = None,
    dry_run: bool = False,
    no_color: bool = False,
    max_workers: int = 8,
    max_positions: int = 5,
    cooldown: float = 120.0,
    spread_max_bps: float = 15.0,
) -> Dict[str, Any]:
    """One multi-symbol evaluation + optional order cycle."""
    global _orders_this_cycle
    t0 = time.monotonic()
    _orders_this_cycle = 0
    _cooldown.cooldown_seconds = float(cooldown)

    balance = 2.00
    session_unrealized = 0.0
    session_realized = 0.0
    positions: List[Dict[str, Any]] = []
    occupied: set = set()

    if not dry_run:
        real_balance = get_wallet_balance()
        if real_balance > 0:
            balance = real_balance
        log.info("Wallet USDT equity: $%.2f", balance)

        session_unrealized = manage_positions(target_profit=target)
        positions = get_active_positions()
        occupied = open_position_symbols(positions)

        if bybit_core:
            try:
                closed = bybit_core.api_request(
                    "GET",
                    "/v5/position/closed-pnl",
                    params={
                        "category": "linear",
                        "startTime": SESSION_START_MS,
                        "limit": 50,
                    },
                    signed=True,
                )
                if closed.get("retCode") == 0:
                    session_realized = sum(
                        float(x.get("closedPnl", 0) or 0)
                        for x in closed.get("result", {}).get("list", [])
                    )
            except Exception as err:
                log.error("closed-pnl fetch: %s", err)

        log.info(
            "[Session] realized=$%+.4f unrealized=$%+.4f total=$%+.4f open=%d %s",
            session_realized,
            session_unrealized,
            session_realized + session_unrealized,
            len(positions),
            sorted(occupied),
        )

        # Concurrency guards
        if len(positions) >= max_positions:
            log.info(
                "Max positions (%d) reached — management only this cycle.",
                max_positions,
            )
            payload = _summary(
                balance,
                session_realized,
                session_unrealized,
                target,
                leverage,
                symbols,
                {},
                occupied,
                t0,
                no_color,
            )
            return payload

        if balance < 10.0 and len(positions) >= 3:
            log.info("Small-account guard (bal<$10, pos>=3) — skip entries.")
            payload = _summary(
                balance,
                session_realized,
                session_unrealized,
                target,
                leverage,
                symbols,
                {"*": "small account concurrency limit"},
                occupied,
                t0,
                no_color,
            )
            return payload
    else:
        log.info("DRY-RUN mode — balance fallback $%.2f", balance)

    # Position notional
    if pos_pct > 0:
        target_value_usdt = balance * (pos_pct / 100.0)
        log.info("pos_pct=%.1f%% → notional $%.2f", pos_pct, target_value_usdt)
    else:
        cap = max(5.05, balance * 2.0)
        if target_value_usdt > cap:
            target_value_usdt = cap
            log.info("notional capped → $%.2f", target_value_usdt)

    # Margin budget: 70% of equity, reduced by approx margin of open pos
    open_margin_est = 0.0
    for p in positions:
        try:
            sz = float(p.get("size", 0) or 0)
            px = float(p.get("avgPrice", 0) or p.get("markPrice", 0) or 0)
            open_margin_est += (sz * px) / max(leverage, 1)
        except (TypeError, ValueError):
            pass
    budget_total = max(0.0, balance * 0.70 - open_margin_est)
    _margin_budget.reset(budget_total)
    log.info(
        "Margin budget $%.2f (equity $%.2f − open≈$%.2f) | "
        "target PnL $%.4f | lev %dx | notional $%.2f | symbols %d",
        budget_total,
        balance,
        open_margin_est,
        target,
        leverage,
        target_value_usdt,
        len(symbols),
    )

    skipped: Dict[str, str] = {}
    skipped_lock = threading.Lock()
    workers = min(len(symbols), max(1, max_workers))

    # Pre-filter occupied / cooling symbols before pool (saves threads)
    runnable = []
    for sym in symbols:
        s = sym.upper().strip()
        if not s:
            continue
        if s in occupied:
            skipped[s] = "already has open position"
            continue
        if _cooldown.is_cooling(s):
            skipped[s] = f"cooldown ({_cooldown.remaining(s):.0f}s left)"
            continue
        if _circuit_breaker.is_open(s):
            skipped[s] = "circuit breaker open"
            continue
        runnable.append(s)

    if not runnable:
        log.info("No runnable symbols this cycle.")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    process_symbol,
                    sym,
                    target_value_usdt,
                    leverage,
                    risk_reward,
                    target,
                    trailing_stop,
                    balance,
                    dry_run,
                    skipped,
                    skipped_lock,
                    occupied,
                    spread_max_bps,
                ): sym
                for sym in runnable
            }
            for fut in concurrent.futures.as_completed(futs):
                sym = futs[fut]
                try:
                    fut.result()
                except Exception as exc:
                    log.error("[%s] thread error: %s", sym, exc, exc_info=True)
                    _circuit_breaker.record_failure(sym)

    return _summary(
        balance,
        session_realized,
        session_unrealized,
        target,
        leverage,
        symbols,
        skipped,
        occupied,
        t0,
        no_color,
    )


def _summary(
    balance: float,
    session_realized: float,
    session_unrealized: float,
    target: float,
    leverage: int,
    symbols: List[str],
    skipped: Dict[str, str],
    occupied: set,
    t0: float,
    no_color: bool,
) -> Dict[str, Any]:
    cycle_ms = int((time.monotonic() - t0) * 1000)
    with _orders_lock:
        placed = _orders_this_cycle
    payload: Dict[str, Any] = {
        "success": True,
        "balance": round(balance, 4),
        "session_realized": round(session_realized, 4),
        "session_unrealized": round(session_unrealized, 4),
        "target": target,
        "leverage": leverage,
        "symbols_processed": symbols,
        "skipped_symbols": skipped,
        "open_symbols": sorted(occupied),
        "orders_placed": placed,
        "cycle_ms": cycle_ms,
        "performance": _perf_tracker.summary(),
        "version": __version__,
    }
    print_human_readable_ui(payload, no_color=no_color)
    write_llm_output(payload)
    return payload


def write_llm_output(data: Dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    blob = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    if out_path in ("/dev/stdout", "/dev/fd/1", "-"):
        sys.stdout.write(blob)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(blob)
        except OSError as err:
            sys.stderr.write(f"Failed writing LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(blob)
            sys.stdout.flush()


def run(**kwargs: Any) -> Dict[str, Any]:
    """AIChat / argc entry point."""
    symbols_raw = kwargs.get(
        "symbols",
        "XRPUSDT,ADAUSDT,DOGEUSDT,TRXUSDT,GALAUSDT,SOLUSDT,"
        "POLUSDT,SUIUSDT,LINKUSDT,AVAXUSDT,DOTUSDT,CHZUSDT,"
        "VETUSDT,ATOMUSDT,NEARUSDT",
    )
    symbols = [s.strip().upper() for s in str(symbols_raw).split(",") if s.strip()]

    pos_val_raw = kwargs.get("pos_value", 50.0)
    try:
        pos_val = float(pos_val_raw)
    except (ValueError, TypeError):
        pos_val = 50.0

    target_raw = kwargs.get("target", 0.02)
    try:
        target_val = float(target_raw)
    except (ValueError, TypeError):
        target_val = 0.02

    return run_scalper_cycle(
        symbols=symbols,
        target_value_usdt=pos_val,
        leverage=int(kwargs.get("leverage", 50)),
        risk_reward=float(kwargs.get("risk_reward", 1.0)),
        pos_pct=float(kwargs.get("pos_pct", 0.0)),
        target=target_val,
        trailing_stop=(
            float(kwargs["trailing_stop"])
            if kwargs.get("trailing_stop") not in (None, "")
            else None
        ),
        dry_run=bool(kwargs.get("dry_run", False)),
        no_color=bool(kwargs.get("no_color", False)),
        max_workers=int(kwargs.get("max_workers", 8)),
        max_positions=int(kwargs.get("max_positions", 5)),
        cooldown=float(kwargs.get("cooldown", 120.0)),
        spread_max_bps=float(kwargs.get("spread_max_bps", 15.0)),
    )


# ==============================================================================
# SECTION 9: CLI ARGUMENT PARSER & ARGC/AICHAT INTERFACE
# ==============================================================================


def _coerce(val: str) -> Any:
    """Coerce env-var string to most specific Python type."""
    if val == "":
        return None
    low = val.lower()
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
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
        prog="multi_scalper.py",
        description=f"Multi-Symbol Scalper Engine v{__version__}",
    )
    parser.add_argument(
        "--symbols",
        default=(
            "XRPUSDT,ADAUSDT,DOGEUSDT,TRXUSDT,GALAUSDT,SOLUSDT,"
            "POLUSDT,SUIUSDT,LINKUSDT,AVAXUSDT,DOTUSDT,CHZUSDT,"
            "VETUSDT,ATOMUSDT,NEARUSDT"
        ),
        help="Comma-separated symbols",
    )
    parser.add_argument(
        "--pos-value",
        type=float,
        default=50.0,
        dest="pos_value",
        help="USDT position value target",
    )
    parser.add_argument(
        "--pos-pct",
        type=float,
        default=0.0,
        dest="pos_pct",
        help="Position value as %% of account balance",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=50,
        help="Leverage multiplier",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=0.02,
        help="Target net USDT profit per trade",
    )
    parser.add_argument(
        "--risk-reward",
        type=float,
        default=1.0,
        dest="risk_reward",
        help="Risk/Reward ratio",
    )
    parser.add_argument(
        "--trailing-stop",
        type=float,
        default=None,
        dest="trailing_stop",
        help="Trailing stop distance",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously in a loop",
    )
    parser.add_argument(
        "--loop-delay",
        type=int,
        default=15,
        dest="loop_delay",
        help="Seconds delay between loop cycles",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        dest="max_workers",
        help="Thread pool size for concurrent symbol analysis",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Simulate without submitting live orders",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color UI",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser


def _install_signal_handlers() -> None:
    """Register SIGINT/SIGTERM to set the global shutdown event."""

    def _handler(signum: int, frame: Any) -> None:
        log.info("Signal %d received — requesting graceful shutdown...", signum)
        _SHUTDOWN.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


if __name__ == "__main__":
    # ---------- argc/aichat environment variable interface ----------
    if any(k.startswith("argc_") for k in os.environ):
        kwargs: dict[str, Any] = {}
        for k, v in os.environ.items():
            if k.startswith("argc_"):
                coerced = _coerce(v)
                # FIX: skip None so run() defaults are not overwritten
                if coerced is not None:
                    kwargs[k[5:].replace("-", "_")] = coerced
        res = run(**kwargs)
        sys.exit(EXIT_SUCCESS if res.get("success", True) else EXIT_ERROR)

    # ---------- Standard CLI interface ----------
    args = _build_parser().parse_args()

    # FIX: verbose flag now configures logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("Verbose logging enabled.")

    _install_signal_handlers()

    symbol_list = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.loop:
        log.info(
            "Loop mode active — starting scalper daemon (cycle interval: %ds).",
            args.loop_delay,
        )
        cycle_num = 0
        while not _SHUTDOWN.is_set():
            cycle_num += 1
            log.info("--- Cycle #%d ---", cycle_num)
            try:
                run_scalper_cycle(
                    symbols=symbol_list,
                    target_value_usdt=args.pos_value,
                    leverage=args.leverage,
                    risk_reward=args.risk_reward,
                    pos_pct=args.pos_pct,
                    target=args.target,
                    trailing_stop=args.trailing_stop,
                    dry_run=args.dry_run,
                    no_color=args.no_color,
                    max_workers=args.max_workers,
                    max_positions=getattr(args, "max_positions", 5),
                    cooldown=getattr(args, "cooldown", 120.0),
                    spread_max_bps=getattr(args, "spread_max_bps", 15.0),
                )
            except Exception as exc:
                log.error("Loop cycle #%d exception: %s", cycle_num, exc)

            # FIX: sleep in interruptible chunks so SIGTERM is responsive
            log.info(
                "Cycle #%d complete. Next cycle in %ds.",
                cycle_num,
                args.loop_delay,
            )
            deadline = time.monotonic() + args.loop_delay
            while not _SHUTDOWN.is_set() and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))

        log.info("Scalper daemon shut down cleanly.")
    else:
        run_scalper_cycle(
            symbols=symbol_list,
            target_value_usdt=args.pos_value,
            leverage=args.leverage,
            risk_reward=args.risk_reward,
            pos_pct=args.pos_pct,
            target=args.target,
            trailing_stop=args.trailing_stop,
            dry_run=args.dry_run,
            no_color=args.no_color,
            max_workers=args.max_workers,
            max_positions=getattr(args, "max_positions", 5),
            cooldown=getattr(args, "cooldown", 120.0),
            spread_max_bps=getattr(args, "spread_max_bps", 15.0),
        )

    sys.exit(EXIT_SUCCESS)
