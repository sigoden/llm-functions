#!/usr/bin/env python3
# ==============================================================================
# bybit_position_manager.py — Pyrmethus AIChat Bybit Position Manager v2.2.0-ASCENDED
# argc/aichat compatible · Breakeven & Target Profit Manager · Precision Quantization
#
# @describe Manage open positions on Bybit: Move Stop-Loss to Breakeven or Close position if net profit threshold is met.
#
# @meta require-tools python3
#
# @option --symbol <TEXT>                Trading pair symbol (e.g. BTCUSDT, default: BTCUSDT)
# @option --action <ENUM>                Action to perform: be, close (default: be)
# @option --profit-usdt <NUM>            Target USDT net profit threshold to trigger action (default: 50)
# @option --fee-rate <NUM>               Estimated taker fee rate for roundtrip calculation (default: 0.0006)
# @option --category <ENUM>              Instrument category: linear, inverse (default: linear)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import enum
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Add current directory to path
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

import requests

__version__ = "2.2.0-ASCENDED"
__all__ = [
    "__version__",
    "execute_manage_position",
    "format_precision",
    "run",
]

# ==============================================================================
# SECTION 1: Exit Codes & JSON Serializer
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2


class ToolJSONEncoder(json.JSONEncoder):
    """Safe JSON encoder handling Decimal, Path, Enum, datetime, complex, and sets."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, (datetime, timedelta)):
            return obj.isoformat() if isinstance(obj, datetime) else obj.total_seconds()
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

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly box UI to stderr for terminal users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BYBIT POSITION MANAGER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Symbol:{RESET}      {BOLD}{data.get('symbol', 'N/A')}{RESET}  |  {NEON_CYAN}Action:{RESET} {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )

    if "net_profit" in data:
        np_col = NEON_GREEN if data.get("net_profit", 0) >= 0 else NEON_RED
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Net Profit:{RESET}  {np_col}${data.get('net_profit', 0.0):.2f} USDT{RESET}  (Target: ${data.get('threshold', 0)} USDT)"
        )

    if "new_stop_loss" in data:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_GREEN}New Stop Loss:{RESET} {data.get('new_stop_loss')}"
        )

    if "message" in data:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Message:{RESET}     {data.get('message')}"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: PRECISION & LONG/SHORT HELPERS
# ==============================================================================


def format_precision(value: Any, step: Any, rounding_mode=ROUND_HALF_UP) -> str:
    """Round a numeric value to an exact decimal step string without binary float drift."""
    if value is None or value == "":
        return ""
    if step is None or float(step) <= 0:
        return format(Decimal(str(value)), "f")
    try:
        val_d = Decimal(str(value))
        step_d = Decimal(str(step))
        quantized = val_d.quantize(step_d, rounding=rounding_mode)
        return format(quantized, "f")
    except (InvalidOperation, ValueError):
        return str(value)


def calculate_breakeven_price(
    side: str, entry_price: float, fee_rate: float = 0.0006
) -> float:
    """Calculate fee-adjusted breakeven price covering entry and exit trading fees."""
    side_clean = side.capitalize()
    if (
        side_clean == "Buy"
    ):  # Long position requires higher price to cover roundtrip fees
        return entry_price * (1.0 + 2.0 * fee_rate)
    # Short position requires lower price to cover roundtrip fees
    return entry_price * (1.0 - 2.0 * fee_rate)


# ==============================================================================
# SECTION 4: API REQUEST WRAPPERS
# ==============================================================================


def _safe_api(
    method: str, path: str, params: dict | None = None, signed: bool = False
) -> dict:
    """Resilient API wrapper using bybit_core or falling back to direct HTTP requests."""
    if bybit_core and hasattr(bybit_core, "api_request"):
        try:
            return bybit_core.api_request(method, path, params=params, signed=signed)
        except Exception as err:
            logging.exception("bybit_core API call failed: %s", err)

    # Direct HTTPS fallback
    base_url = "https://api.bybit.com"
    url = base_url + path
    proxies = proxy_utils.get_proxies() if proxy_utils else None

    try:
        if method.upper() == "GET":
            resp = requests.get(url, params=params, timeout=10, proxies=proxies)
        else:
            resp = requests.post(url, json=params, timeout=10, proxies=proxies)
        return resp.json()
    except Exception as exc:
        return {"retCode": -1, "retMsg": f"Request exception: {exc}", "result": {}}


def get_instrument_precision(
    symbol: str, category: str = "linear"
) -> tuple[float, float]:
    """Fetch tickSize and qtyStep for exact string formatting."""
    tick_size, qty_step = 0.01, 0.001
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
            price = info.get("priceFilter", {}) or {}
            qty_step = float(lot.get("qtyStep", qty_step) or qty_step)
            tick_size = float(price.get("tickSize", tick_size) or tick_size)
    return tick_size, qty_step


# ==============================================================================
# SECTION 5: CORE POSITION MANAGEMENT ENGINE
# ==============================================================================


def execute_manage_position(
    symbol: str = "BTCUSDT",
    action: str = "be",
    profit_usdt: float = 50.0,
    fee_rate: float = 0.0006,
    category: str = "linear",
    verbose: bool = False,
) -> dict[str, Any]:
    """Core position management logic: Breakeven movement & Profit Target Closing."""
    symbol = symbol.upper().strip()
    action = action.lower().strip()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(
            f"Managing position for {symbol} | Action: {action} | Target Profit: ${profit_usdt}"
        )

    # 1. Fetch Open Position
    pos_data = _safe_api(
        "GET",
        "/v5/position/list",
        params={"category": category, "symbol": symbol},
        signed=True,
    )
    if pos_data.get("retCode") != 0:
        return {
            "success": False,
            "error": f"Failed to fetch position: {pos_data.get('retMsg')}",
        }

    positions = pos_data.get("result", {}).get("list", [])
    position = next(
        (
            p
            for p in positions
            if float(p.get("size", 0)) > 0 and p.get("symbol") == symbol
        ),
        None,
    )

    if not position:
        return {"success": False, "error": f"No open position found for {symbol}"}

    size = float(position.get("size", 0))
    entry_price = float(position.get("avgPrice", 0) or position.get("entryPrice", 0))
    side = position.get("side", "Buy").capitalize()
    pos_idx = int(position.get("positionIdx", 0))
    unrealized_pnl = float(
        position.get("unrealisedPnl", position.get("unrealizedPnl", 0))
    )

    # 2. Fetch Precision Specs & Market Price
    tick_size, qty_step = get_instrument_precision(symbol, category)

    ticker_data = _safe_api(
        "GET",
        "/v5/market/tickers",
        params={"category": category, "symbol": symbol},
        signed=False,
    )
    if ticker_data.get("retCode") != 0 or not ticker_data.get("result", {}).get("list"):
        return {"success": False, "error": "Failed to retrieve current market price"}

    current_price = float(
        ticker_data.get("result", {}).get("list", [{}])[0].get("lastPrice", 0)
    )
    if current_price <= 0:
        return {"success": False, "error": "Invalid market price returned from ticker"}

    # 3. Calculate Fees & Net Profit
    # Roundtrip fee calculation: Entry cost fee + Exit cost fee
    entry_fee = size * entry_price * fee_rate
    exit_fee = size * current_price * fee_rate
    total_fees = entry_fee + exit_fee
    net_profit = unrealized_pnl - total_fees

    # 4. Action: Move to Break-Even (be)
    if action == "be":
        be_price = calculate_breakeven_price(side, entry_price, fee_rate)
        be_price_str = format_precision(be_price, tick_size, ROUND_HALF_UP)

        params = {
            "category": category,
            "symbol": symbol,
            "positionIdx": pos_idx,
            "stopLoss": be_price_str,
        }

        res = _safe_api("POST", "/v5/position/trading-stop", params=params, signed=True)
        if res.get("retCode") == 0:
            return {
                "success": True,
                "action": "move_to_break_even",
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "new_stop_loss": float(be_price_str),
                "fee_adjusted": True,
                "message": f"Stop loss moved to fee-adjusted breakeven ({be_price_str})",
            }
        return {
            "success": False,
            "error": f"Failed to set breakeven SL: {res.get('retMsg')}",
        }

    # 5. Action: Close Position if Profit Threshold Reached (close)
    elif action == "close":
        if net_profit < profit_usdt:
            return {
                "success": False,
                "action": "check_profit_threshold",
                "message": f"Net profit (${net_profit:.2f} USDT) is below target threshold (${profit_usdt:.2f} USDT)",
                "net_profit": round(net_profit, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_fees": round(total_fees, 2),
                "threshold": profit_usdt,
            }

        close_side = "Sell" if side == "Buy" else "Buy"
        qty_str = format_precision(size, qty_step, ROUND_DOWN)

        params = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": qty_str,
            "timeInForce": "GTC",
            "reduceOnly": True,
            "positionIdx": pos_idx,
        }

        res = _safe_api("POST", "/v5/order/create", params=params, signed=True)
        if res.get("retCode") == 0:
            return {
                "success": True,
                "action": "close_position",
                "symbol": symbol,
                "side": side,
                "size": float(qty_str),
                "entry_price": entry_price,
                "exit_price": current_price,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "fees": round(total_fees, 2),
                "net_profit": round(net_profit, 2),
                "order_id": res.get("result", {}).get("orderId"),
                "message": f"Closed position for {symbol} with net profit of ${net_profit:.2f} USDT",
            }
        return {
            "success": False,
            "error": f"Failed to submit close order: {res.get('retMsg')}",
        }

    return {
        "success": False,
        "error": f"Invalid action '{action}'. Use 'be' or 'close'.",
    }


# ==============================================================================
# SECTION 6: OUTPUT ROUTING (LLM vs Human Terminal)
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
# SECTION 7: PROGRAMMATIC ENTRY POINT FOR AICHAT
# ==============================================================================


def run(
    symbol: str = "BTCUSDT",
    action: str = "be",
    profit_usdt: float = 50.0,
    fee_rate: float = 0.0006,
    category: str = "linear",
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute position management and route outputs appropriately."""
    result = execute_manage_position(
        symbol=symbol,
        action=action,
        profit_usdt=profit_usdt,
        fee_rate=fee_rate,
        category=category,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


# ==============================================================================
# SECTION 8: CLI ARGUMENT PARSER
# ==============================================================================


def _coerce(val: str) -> Any:
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
        prog="bybit_position_manager.py",
        description=f"Bybit Position Manager Tool v{__version__}",
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT", help="Trading pair symbol (e.g., BTCUSDT)"
    )
    parser.add_argument(
        "--action",
        default="be",
        choices=["be", "close"],
        help="Action: 'be' (breakeven) or 'close'",
    )
    parser.add_argument(
        "--profit-usdt",
        type=float,
        default=50.0,
        help="Target USDT net profit threshold",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.0006,
        help="Taker fee rate for roundtrip calculation",
    )
    parser.add_argument("--category", default="linear", choices=["linear", "inverse"])
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose debug logging"
    )
    return parser


if __name__ == "__main__":
    if any(k.startswith("argc_") for k in os.environ):
        kwargs = {}
        for k, v in os.environ.items():
            if k.startswith("argc_"):
                kwargs[k[5:].replace("-", "_")] = _coerce(v)
        res = run(**kwargs)
        sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)

    args = _build_parser().parse_args()
    res = run(
        symbol=args.symbol,
        action=args.action,
        profit_usdt=args.profit_usdt,
        fee_rate=args.fee_rate,
        category=args.category,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)
