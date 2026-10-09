#!/usr/bin/env python3
# ==============================================================================
# bbt_scanner.py — Pyrmethus AIChat Bybit High-Confluence Market Scanner v1.0.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Fast multi-pair market scanner & technical confluence signal generator for Bybit V5 linear perpetuals.
#
# @meta require-tools python3
#
# @option --symbols <TEXT>               Comma-separated symbol list (default: top volume pairs)
# @option --interval <TEXT>              Kline timeframe: 1, 5, 15, 60, 240, D (default: 15)
# @option --min-volume <NUM>             Minimum 24h USDT turnover filter (default: 10000000)
# @option --limit <NUM>                  Number of top ranked signals to report (default: 5)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @flag   --use-cache                    Enable result caching for market data
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import statistics
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional

import requests

CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    import bybit_core
except ImportError:
    bybit_core = None

__version__ = "1.0.0"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 124
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130


class ToolJSONEncoder(json.JSONEncoder):
    """Zero-crash custom JSON encoder supporting standard and dynamic Python objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


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
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SCAN COMPLETED" if success else "FAILED"

    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BYBIT HIGH-CONFLUENCE MARKET SCANNER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Interval:{RESET} {data.get('interval', '15')}m  |  {NEON_CYAN}Scanned Pairs:{RESET} {NEON_YELLOW}{data.get('total_scanned', 0)}{RESET}  |  {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    signals = data.get("top_signals", [])
    if signals:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Top Profit Opportunities ({len(signals)}):{RESET}"
        )
        for idx, sig in enumerate(signals, 1):
            dir_color = (
                NEON_GREEN
                if sig["direction"] == "BUY"
                else (NEON_RED if sig["direction"] == "SELL" else NEON_YELLOW)
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET} {BOLD}#{idx}{RESET} {NEON_CYAN}{sig['symbol']:<10}{RESET} {dir_color}{sig['direction']:<4}{RESET} | Score: {NEON_YELLOW}{sig['score']:.1f}/100{RESET} | Entry: {sig['entry']:.4f}"
            )
            _cprint(
                f"{NEON_PURPLE}│{RESET}    {DIM}SL: {sig['stop_loss']:.4f} | TP: {sig['take_profit']:.4f} | Reason: {sig['reason']}{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


class ToolCache:
    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or Path.home() / ".cache" / "aichat_tools"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 300) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            cache_file.unlink(missing_ok=True)
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def fetch_top_turnover_symbols(
    min_volume: float = 10000000.0, limit: int = 30
) -> List[dict]:
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    try:
        resp = requests.get(url, timeout=10).json()
        if resp.get("retCode") == 0:
            tickers = resp.get("result", {}).get("list", [])
            valid = [
                t
                for t in tickers
                if t.get("symbol", "").endswith("USDT")
                and float(t.get("turnover24h", 0)) >= min_volume
            ]
            valid.sort(key=lambda x: float(x.get("turnover24h", 0)), reverse=True)
            return valid[:limit]
    except Exception as err:
        logging.debug(f"Ticker fetch failed: {err}")
    return []


def fetch_klines(symbol: str, interval: str = "15", limit: int = 100) -> List[list]:
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url, timeout=10).json()
        if resp.get("retCode") == 0:
            klines = resp.get("result", {}).get("list", [])
            klines.reverse()
            return klines
    except Exception:
        pass
    return []


def analyze_symbol_confluence(
    symbol: str, ticker: dict, interval: str = "15"
) -> Optional[dict]:
    klines = fetch_klines(symbol, interval=interval, limit=100)
    if len(klines) < 30:
        return None

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    curr_price = closes[-1]

    # 1. RSI (14)
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0 for d in deltas[-14:]]
    losses = [-d if d < 0 else 0 for d in deltas[-14:]]
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    rs = (avg_gain / avg_loss) if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    # 2. EMAs
    def get_ema(data, period):
        k = 2 / (period + 1)
        ema = data[0]
        for v in data[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    ema20 = get_ema(closes, 20)
    ema50 = get_ema(closes, 50)

    # 3. ATR (14)
    trs = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, len(closes))
    ]
    atr = sum(trs[-14:]) / 14 if trs else curr_price * 0.01

    # 4. Bollinger Bands
    sma20 = sum(closes[-20:]) / 20
    std_dev = statistics.stdev(closes[-20:])
    bb_upper = sma20 + (2 * std_dev)
    bb_lower = sma20 - (2 * std_dev)

    # Confluence Score Engine (0 to 100)
    score = 50.0
    direction = "NEUTRAL"
    reasons = []

    # Oversold Bullish Reversion
    if curr_price <= bb_lower and rsi < 32:
        score += 35
        direction = "BUY"
        reasons.append(f"BB Lower Touch & RSI Oversold ({rsi:.1f})")
    elif curr_price >= bb_upper and rsi > 68:
        score += 35
        direction = "SELL"
        reasons.append(f"BB Upper Touch & RSI Overbought ({rsi:.1f})")

    # Trend Momentum
    if ema20 > ema50 and curr_price > ema20:
        if direction == "BUY":
            score += 15
        elif direction == "NEUTRAL":
            score += 20
            direction = "BUY"
            reasons.append("EMA20/50 Bullish Expansion")
    elif ema20 < ema50 and curr_price < ema20:
        if direction == "SELL":
            score += 15
        elif direction == "NEUTRAL":
            score += 20
            direction = "SELL"
            reasons.append("EMA20/50 Bearish Breakdown")

    # Volume Confirmation
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] > avg_vol * 1.8:
        score += 10
        reasons.append("High Volume Spike")

    if direction == "NEUTRAL" or score < 60:
        return None

    # Calculate precise SL / TP
    sl_dist = atr * 1.8
    if direction == "BUY":
        stop_loss = curr_price - sl_dist
        take_profit = curr_price + (sl_dist * 2.0)
    else:
        stop_loss = curr_price + sl_dist
        take_profit = curr_price - (sl_dist * 2.0)

    return {
        "symbol": symbol,
        "direction": direction,
        "score": min(score, 100.0),
        "entry": curr_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rsi": round(rsi, 2),
        "atr": round(atr, 4),
        "turnover_24h": float(ticker.get("turnover24h", 0)),
        "reason": ", ".join(reasons) if reasons else "Confluence Trigger",
    }


def execute_tool(
    symbols: Optional[str] = None,
    interval: str = "15",
    min_volume: float = 10000000.0,
    limit: int = 5,
    mode: str = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    cache = ToolCache()
    cache_key = f"bbt_scanner:{symbols}:{interval}:{min_volume}:{limit}"

    if use_cache:
        cached = cache.get(cache_key, ttl_seconds=120)
        if cached:
            cached["cached"] = True
            return cached

    if symbols:
        raw_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        tickers = [{"symbol": s, "turnover24h": "100000000"} for s in raw_symbols]
    else:
        tickers = fetch_top_turnover_symbols(min_volume=min_volume, limit=25)

    signals = []
    for t in tickers:
        sym = t["symbol"]
        res = analyze_symbol_confluence(sym, t, interval=interval)
        if res:
            signals.append(res)

    signals.sort(key=lambda x: x["score"], reverse=True)
    top_signals = signals[:limit]

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    output = {
        "success": True,
        "interval": interval,
        "total_scanned": len(tickers),
        "signals_found": len(signals),
        "top_signals": top_signals,
        "duration_ms": duration_ms,
        "cached": False,
        "exit_code": EXIT_SUCCESS,
    }

    if use_cache:
        cache.set(cache_key, output)

    return output


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)
            sys.stdout.flush()


def run(
    symbols: Optional[str] = None,
    interval: str = "15",
    min_volume: float = 10000000.0,
    limit: int = 5,
    mode: Literal["summary", "detailed"] = "summary",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    res = execute_tool(
        symbols=symbols,
        interval=interval,
        min_volume=min_volume,
        limit=limit,
        mode=mode,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbt_scanner.py",
        description=f"Bybit High-Confluence Market Scanner v{__version__}",
    )
    parser.add_argument("--symbols", help="Comma-separated symbol list")
    parser.add_argument(
        "--interval", default="15", help="Kline timeframe (default: 15)"
    )
    parser.add_argument(
        "--min-volume", type=float, default=10000000.0, help="Minimum 24h turnover USD"
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Number of top signals to return"
    )
    parser.add_argument("--mode", choices=["summary", "detailed"], default="summary")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    result = execute_tool(
        symbols=args.symbols,
        interval=args.interval,
        min_volume=args.min_volume,
        limit=args.limit,
        mode=args.mode,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(result, no_color=args.no_color)
    write_llm_output(result)
    sys.exit(result.get("exit_code", EXIT_SUCCESS))
