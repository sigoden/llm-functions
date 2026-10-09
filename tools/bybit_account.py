#!/usr/bin/env python3
# @describe Bybit Account Tools - balance, positions, orders, PnL
# @option --coin           Filter by coin (default: USDT)
# @option --symbol         Filter by symbol (e.g., BTCUSDT)
# @option --action         Action: balance|positions|open_orders|closed_pnl|executions
# @option --order_id       Order ID for executions lookup
# @option --limit          Number of results (default: 20)
# @option --use_tor       Route through Tor proxy (default: true)
"""
Bybit Account & Position Tools

Runs on the unified `tools.bybit` client (no pybit dependency).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bybit.terminal import BybitRealm  # noqa: E402


def _realm():
    return BybitRealm()


def bybit_get_balance(coin=None):
    """Get wallet balance"""
    bot = _realm()
    try:
        res = bot.get_wallet_balance()
        coins = res.get("list", [{}])[0].get("coin", [])
        if coin:
            coins = [c for c in coins if c.get("coin", "").upper() == coin.upper()]
        print(json.dumps(coins, indent=2))
    finally:
        bot.close()


def bybit_get_positions(symbol=None):
    """Get open positions"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_positions(symbol=symbol), indent=2))
    finally:
        bot.close()


def bybit_get_open_orders(symbol=None):
    """Get open orders"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_open_orders(symbol=symbol), indent=2))
    finally:
        bot.close()


def bybit_get_closed_pnl(symbol=None, limit=20):
    """Get closed PnL history"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_pnl_history(symbol=symbol, limit=limit), indent=2))
    finally:
        bot.close()


def bybit_get_executions(symbol=None, order_id=None):
    """Get trade executions"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_executions(symbol=symbol, order_id=order_id), indent=2))
    finally:
        bot.close()


def run(**kwargs) -> dict:
    """Unified entry point (used by run-tool.py)."""
    action = kwargs.get("action", "balance")
    symbol = kwargs.get("symbol")
    coin = kwargs.get("coin")
    limit = int(kwargs.get("limit", 20))
    order_id = kwargs.get("order_id")

    bot = _realm()
    try:
        if action == "balance":
            res = bot.get_wallet_balance()
            coins = res.get("list", [{}])[0].get("coin", [])
            if coin:
                coins = [c for c in coins if c.get("coin", "").upper() == coin.upper()]
            return {"status": "ok", "coins": coins}
        if action == "positions":
            return bot.get_positions(symbol=symbol)
        if action == "open_orders":
            return bot.get_open_orders(symbol=symbol)
        if action == "closed_pnl":
            return bot.get_pnl_history(symbol=symbol, limit=limit)
        if action == "executions":
            return bot.get_executions(symbol=symbol, order_id=order_id)
        return {"status": "error", "msg": f"Unknown action: {action}"}
    finally:
        bot.close()


def main():
    parser = argparse.ArgumentParser(description="Bybit Account Tools")
    parser.add_argument("--coin", default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--action", default="balance")
    parser.add_argument("--order_id", default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
