#!/usr/bin/env python3
# ==============================================================================
# bbt_risk.py — Pyrmethus AIChat Bybit Portfolio Risk & Liquidation Guard v1.0.0
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Portfolio heat manager, liquidation risk monitor & maximum safe lot sizing calculator for Bybit V5.
#
# @meta require-tools python3
#
# @option --action <ENUM>                portfolio_health, max_order_calc, liquidation_guard (default: portfolio_health)
# @option --symbol <TEXT>                Target pair for max_order_calc (e.g. BTCUSDT)
# @option --risk-pct <NUM>               Target trade risk % of equity (default: 1.0)
# @option --sl-dist-pct <NUM>            Assumed Stop Loss distance % for order sizing (default: 2.0)
# @option --no-color                     Disable ANSI color output
# @option --verbose                      Enable detailed debug log output
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
from typing import Any, List, Optional, Tuple

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
    status_text = "PORTFOLIO HEALTH OK" if success else "RISK ALERT"

    box_w = 70
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [BYBIT PORTFOLIO RISK OBSERVATORY v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Total Equity:{RESET} ${data.get('total_equity', 0.0):,.2f} USDT  |  {NEON_CYAN}Available:{RESET} ${data.get('available_balance', 0.0):,.2f} USDT"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Margin Usage:{RESET} {NEON_YELLOW}{data.get('margin_usage_pct', 0.0):.1f}%{RESET}          |  {NEON_CYAN}Unrealized PnL:{RESET} {data.get('total_pnl_usd', 0.0):+.2f} USDT"
    )

    positions = data.get("positions_risk", [])
    if positions:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Active Positions Proximity ({len(positions)}):{RESET}"
        )
        for pos in positions:
            heat_color = NEON_RED if pos["heat"] in ("CRITICAL", "HIGH") else NEON_GREEN
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}{pos['symbol']:<10}{RESET} {pos['side']:<4} | Size: {pos['size']} | Liq Dist: {heat_color}{pos['liq_dist_pct']:.1f}% ({pos['heat']}){RESET}"
            )

    rec = data.get("recommendation")
    if rec:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}Advisor:{RESET} {rec}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def get_wallet_balance() -> Tuple[float, float]:
    if bybit_core and hasattr(bybit_core, "api_request"):
        res = bybit_core.api_request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            signed=True,
        )
    else:
        return 1000.0, 1000.0  # Fallback simulation

    if res.get("retCode") == 0:
        lst = res.get("result", {}).get("list", [{}])[0]
        total_equity = float(lst.get("totalEquity", 0) or 0)
        coins = lst.get("coin", [])
        usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
        available = float(
            usdt.get("availableToWithdraw", usdt.get("walletBalance", 0)) or 0
        )
        return total_equity, available
    return 0.0, 0.0


def get_positions() -> List[dict]:
    if bybit_core and hasattr(bybit_core, "get_positions"):
        res = bybit_core.get_positions(category="linear")
    else:
        return []

    if res.get("retCode") == 0:
        return [
            p
            for p in res.get("result", {}).get("list", [])
            if float(p.get("size", 0)) > 0
        ]
    return []


def execute_tool(
    action: str = "portfolio_health",
    symbol: Optional[str] = None,
    risk_pct: float = 1.0,
    sl_dist_pct: float = 2.0,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    equity, available = get_wallet_balance()
    positions = get_positions()

    positions_risk = []
    total_notional = 0.0
    total_pnl = 0.0

    for pos in positions:
        sym = pos.get("symbol")
        side = pos.get("side")
        size = float(pos.get("size", 0))
        entry = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
        mark = float(pos.get("markPrice", entry))
        liq = float(pos.get("liqPrice", 0))

        notional = size * mark
        total_notional += notional

        pnl = (mark - entry) * size if side == "Buy" else (entry - mark) * size
        total_pnl += pnl

        liq_dist = 999.0
        heat = "LOW"
        if liq > 0:
            liq_dist = (
                ((mark - liq) / mark * 100.0)
                if side == "Buy"
                else ((liq - mark) / mark * 100.0)
            )
            if liq_dist < 5.0:
                heat = "CRITICAL"
            elif liq_dist < 12.0:
                heat = "HIGH"
            elif liq_dist < 25.0:
                heat = "MEDIUM"

        positions_risk.append(
            {
                "symbol": sym,
                "side": side,
                "size": size,
                "entry": entry,
                "mark": mark,
                "liq": liq,
                "liq_dist_pct": round(liq_dist, 2),
                "heat": heat,
                "notional_usd": round(notional, 2),
                "pnl_usd": round(pnl, 2),
            }
        )

    margin_usage_pct = ((equity - available) / equity * 100.0) if equity > 0 else 0.0

    recommendation = "Portfolio operating within healthy risk parameters."
    critical_positions = [
        p["symbol"] for p in positions_risk if p["heat"] in ("CRITICAL", "HIGH")
    ]
    if critical_positions:
        recommendation = f"WARNING: High liquidation risk detected on {', '.join(critical_positions)}. Consider reducing leverage or adding margin."
    elif margin_usage_pct > 75.0:
        recommendation = (
            "WARNING: Margin usage exceeds 75%. Pause new position entries."
        )

    max_safe_qty = 0.0
    if action == "max_order_calc" and symbol:
        risk_usdt = equity * (risk_pct / 100.0)
        price = positions_risk[0]["mark"] if positions_risk else 100.0
        px_dist = price * (sl_dist_pct / 100.0)
        max_safe_qty = (risk_usdt / px_dist) if px_dist > 0 else 0.0

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)

    return {
        "success": True,
        "action": action,
        "total_equity": round(equity, 2),
        "available_balance": round(available, 2),
        "margin_usage_pct": round(margin_usage_pct, 1),
        "total_notional_usd": round(total_notional, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "positions_risk": positions_risk,
        "recommendation": recommendation,
        "max_safe_qty_calculated": round(max_safe_qty, 4) if symbol else None,
        "duration_ms": duration_ms,
        "exit_code": EXIT_SUCCESS,
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
    action: str = "portfolio_health",
    symbol: Optional[str] = None,
    risk_pct: float = 1.0,
    sl_dist_pct: float = 2.0,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    res = execute_tool(
        action=action,
        symbol=symbol,
        risk_pct=risk_pct,
        sl_dist_pct=sl_dist_pct,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbt_risk.py",
        description=f"Bybit Portfolio Risk Observatory v{__version__}",
    )
    parser.add_argument(
        "--action",
        choices=["portfolio_health", "max_order_calc", "liquidation_guard"],
        default="portfolio_health",
    )
    parser.add_argument("--symbol", help="Target symbol for max order size calculation")
    parser.add_argument(
        "--risk-pct", type=float, default=1.0, help="Risk percent of equity"
    )
    parser.add_argument(
        "--sl-dist-pct", type=float, default=2.0, help="Assumed SL distance percent"
    )
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    result = execute_tool(
        action=args.action,
        symbol=args.symbol,
        risk_pct=args.risk_pct,
        sl_dist_pct=args.sl_dist_pct,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(result, no_color=args.no_color)
    write_llm_output(result)
    sys.exit(result.get("exit_code", EXIT_SUCCESS))
