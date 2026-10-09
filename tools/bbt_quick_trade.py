#!/usr/bin/env python3
# ==============================================================================
# bbt_quick_trade.py — Pyrmethus AIChat Bybit Execution & DCA Ladder Manager v1.0.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe One-click profit-locker, breakeven mover & DCA entry laddering manager for Bybit V5.
#
# @meta require-tools python3
#
# @option --symbol! <TEXT>               Trading pair (e.g. BTCUSDT)
# @option --action! <ENUM>               move_breakeven, lock_profit, partial_close, dca_ladder
# @option --percent <NUM>                Percentage of position to close (default: 50.0)
# @option --dca-steps <NUM>              Number of DCA limit orders (default: 3)
# @option --dca-range-pct <NUM>          Price depth % range for DCA ladder (default: 1.5)
# @option --total-qty <NUM>              Total quantity for DCA ladder
# @option --side <ENUM>                  Buy, Sell (for DCA ladder)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

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


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "EXECUTION SUCCESS" if success else "FAILED"

    box_w = 66
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BYBIT QUICK TRADE MANAGER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Symbol:{RESET}   {BOLD}{data.get('symbol', 'N/A')}{RESET}  |  {NEON_CYAN}Action:{RESET} {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )

    if "breakeven_price" in data:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}New SL (Breakeven):{RESET} {NEON_GREEN}${data.get('breakeven_price'):.4f}{RESET}"
        )

    if "orders_placed" in data:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Orders Placed:{RESET} {NEON_GREEN}{data.get('orders_placed')}{RESET} orders in DCA ladder"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def get_position(symbol: str) -> Optional[dict]:
    if bybit_core and hasattr(bybit_core, "get_positions"):
        res = bybit_core.get_positions(category="linear", symbol=symbol.upper())
        if res.get("retCode") == 0:
            lst = [
                p
                for p in res.get("result", {}).get("list", [])
                if float(p.get("size", 0)) > 0
            ]
            return lst[0] if lst else None
    return None


def execute_tool(
    symbol: str,
    action: str,
    percent: float = 50.0,
    dca_steps: int = 3,
    dca_range_pct: float = 1.5,
    total_qty: Optional[float] = None,
    side: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    sym = symbol.upper()

    if action == "move_breakeven":
        pos = get_position(sym)
        if not pos:
            return {
                "success": False,
                "error": f"No active position found for {sym}",
                "exit_code": EXIT_ERROR,
            }

        entry_px = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
        pos_side = pos.get("side", "Buy")

        # Include fee offset (0.11% cover)
        fee_offset = entry_px * 0.0011
        be_price = (
            (entry_px + fee_offset) if pos_side == "Buy" else (entry_px - fee_offset)
        )

        res = bybit_core.api_request(
            "POST",
            "/v5/position/trading-stop",
            params={"category": "linear", "symbol": sym, "stopLoss": f"{be_price:.4f}"},
            signed=True,
        )

        if res.get("retCode") == 0:
            return {
                "success": True,
                "symbol": sym,
                "action": action,
                "entry_price": entry_px,
                "breakeven_price": round(be_price, 4),
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                "exit_code": EXIT_SUCCESS,
            }
        return {"success": False, "error": res.get("retMsg"), "exit_code": EXIT_ERROR}

    elif action in ("lock_profit", "partial_close"):
        pos = get_position(sym)
        if not pos:
            return {
                "success": False,
                "error": f"No active position found for {sym}",
                "exit_code": EXIT_ERROR,
            }

        size = float(pos.get("size", 0))
        pos_side = pos.get("side", "Buy")
        close_side = "Sell" if pos_side == "Buy" else "Buy"
        close_qty = round(size * (percent / 100.0), 3)

        res = bybit_core.api_request(
            "POST",
            "/v5/order/create",
            params={
                "category": "linear",
                "symbol": sym,
                "side": close_side,
                "orderType": "Market",
                "qty": str(close_qty),
                "reduceOnly": True,
            },
            signed=True,
        )

        if res.get("retCode") == 0:
            return {
                "success": True,
                "symbol": sym,
                "action": action,
                "closed_qty": close_qty,
                "remaining_size": round(size - close_qty, 3),
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                "exit_code": EXIT_SUCCESS,
            }
        return {"success": False, "error": res.get("retMsg"), "exit_code": EXIT_ERROR}

    elif action == "dca_ladder":
        if not total_qty or not side:
            return {
                "success": False,
                "error": "dca_ladder requires --total-qty and --side",
                "exit_code": EXIT_ERROR,
            }

        ticker_res = bybit_core.get_ticker(symbol=sym)
        last_px = float(
            ticker_res.get("result", {}).get("list", [{}])[0].get("lastPrice", 0)
        )
        if last_px == 0:
            return {
                "success": False,
                "error": "Could not fetch ticker price",
                "exit_code": EXIT_ERROR,
            }

        step_qty = round(total_qty / dca_steps, 3)
        orders = []

        for i in range(dca_steps):
            offset_pct = (i + 1) * (dca_range_pct / dca_steps) / 100.0
            price = (
                (last_px * (1 - offset_pct))
                if side.capitalize() == "Buy"
                else (last_px * (1 + offset_pct))
            )

            res = bybit_core.api_request(
                "POST",
                "/v5/order/create",
                params={
                    "category": "linear",
                    "symbol": sym,
                    "side": side.capitalize(),
                    "orderType": "Limit",
                    "qty": str(step_qty),
                    "price": f"{price:.4f}",
                    "timeInForce": "GTC",
                },
                signed=True,
            )
            if res.get("retCode") == 0:
                orders.append(res.get("result", {}).get("orderId"))

        return {
            "success": True,
            "symbol": sym,
            "action": action,
            "orders_placed": len(orders),
            "orders": orders,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            "exit_code": EXIT_SUCCESS,
        }

    return {
        "success": False,
        "error": f"Unsupported action: {action}",
        "exit_code": EXIT_ERROR,
    }


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
    symbol: str,
    action: str,
    percent: float = 50.0,
    dca_steps: int = 3,
    dca_range_pct: float = 1.5,
    total_qty: Optional[float] = None,
    side: Optional[str] = None,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    res = execute_tool(
        symbol=symbol,
        action=action,
        percent=percent,
        dca_steps=dca_steps,
        dca_range_pct=dca_range_pct,
        total_qty=total_qty,
        side=side,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbt_quick_trade.py",
        description=f"Bybit Quick Trade Manager v{__version__}",
    )
    parser.add_argument("--symbol", required=True, help="Trading pair symbol")
    parser.add_argument(
        "--action",
        required=True,
        choices=["move_breakeven", "lock_profit", "partial_close", "dca_ladder"],
    )
    parser.add_argument(
        "--percent", type=float, default=50.0, help="Percentage for partial close"
    )
    parser.add_argument(
        "--dca-steps", type=int, default=3, help="Number of DCA limit levels"
    )
    parser.add_argument(
        "--dca-range-pct", type=float, default=1.5, help="DCA price depth percentage"
    )
    parser.add_argument("--total-qty", type=float, help="Total DCA quantity")
    parser.add_argument("--side", choices=["Buy", "Sell"], help="Side for DCA ladder")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    result = execute_tool(
        symbol=args.symbol,
        action=args.action,
        percent=args.percent,
        dca_steps=args.dca_steps,
        dca_range_pct=args.dca_range_pct,
        total_qty=args.total_qty,
        side=args.side,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(result, no_color=args.no_color)
    write_llm_output(result)
    sys.exit(result.get("exit_code", EXIT_SUCCESS))
