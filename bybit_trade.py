#!/usr/bin/env python3
# @describe Bybit Trade Tools - place/cancel orders, leverage, TP/SL
# @option --symbol!        Trading pair (e.g., BTCUSDT)
# @option --side           Order side: Buy|Sell
# @option --order_type     Order type: Market|Limit (default: Market)
# @option --qty            Order quantity
# @option --price         Limit price (required for Limit orders)
# @option --time_in_force TIF: GTC|IOC|FOK|PostOnly (default: GTC)
# @option --action        Action: place_order|cancel|cancel_all|leverage|trading_stop|order_history
# @option --order_id      Order ID for cancel
# @option --leverage     Leverage value (1-100)
# @option --tp           Take profit price
# @option --sl            Stop loss price
# @option --use_tor      Route through Tor proxy (default: true)
"""
Bybit Execution & Trade Tools

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


def bybit_place_order(symbol, side, order_type, qty, price=None, time_in_force="GTC"):
    """Place a market or limit order"""
    bot = _realm()
    try:
        res = bot.place_order(
            symbol,
            side,
            qty,
            order_type,
            price=price,
            time_in_force=time_in_force,
        )
        print(json.dumps(res, indent=2))
    finally:
        bot.close()


def bybit_cancel_order(symbol, order_id):
    """Cancel an order by ID"""
    bot = _realm()
    try:
        print(json.dumps(bot.cancel_order(symbol, order_id), indent=2))
    finally:
        bot.close()


def bybit_cancel_all_orders(symbol):
    """Cancel all open orders for a symbol"""
    bot = _realm()
    try:
        print(json.dumps(bot.cancel_all_orders(symbol=symbol), indent=2))
    finally:
        bot.close()


def bybit_set_leverage(symbol, leverage):
    """Set leverage for a symbol"""
    bot = _realm()
    try:
        print(json.dumps(bot.set_leverage(symbol, leverage), indent=2))
    finally:
        bot.close()


def bybit_set_trading_stop(symbol, tp=None, sl=None):
    """Set take-profit / stop-loss on an open position"""
    bot = _realm()
    try:
        print(
            json.dumps(
                bot.set_trading_stop(symbol, take_profit=tp, stop_loss=sl), indent=2
            )
        )
    finally:
        bot.close()


def bybit_get_order_history(symbol=None, limit=20):
    """Get order history"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_order_history(symbol=symbol, limit=limit), indent=2))
    finally:
        bot.close()


def run(**kwargs) -> dict:
    """Unified entry point (used by run-tool.py)."""
    action = kwargs.get("action", "place_order")
    symbol = kwargs.get("symbol")
    if not symbol and action not in ("order_history",):
        return {"status": "error", "msg": "--symbol is required"}

    bot = _realm()
    try:
        if action == "place_order":
            qty = kwargs.get("qty")
            side = kwargs.get("side")
            if not qty or not side:
                return {"status": "error", "msg": "--qty and --side required"}
            return bot.place_order(
                symbol,
                side,
                float(qty),
                kwargs.get("order_type", "Market"),
                price=kwargs.get("price"),
                time_in_force=kwargs.get("time_in_force", "GTC"),
            )
        if action == "cancel":
            return bot.cancel_order(symbol, kwargs.get("order_id"))
        if action == "cancel_all":
            return bot.cancel_all_orders(symbol=symbol)
        if action == "leverage":
            lev = kwargs.get("leverage")
            if lev is None:
                return {"status": "error", "msg": "--leverage required"}
            return bot.set_leverage(symbol, int(float(lev)))
        if action == "trading_stop":
            return bot.set_trading_stop(
                symbol,
                take_profit=kwargs.get("tp"),
                stop_loss=kwargs.get("sl"),
            )
        if action == "order_history":
            return bot.get_order_history(
                symbol=symbol, limit=int(kwargs.get("limit", 20))
            )
        return {"status": "error", "msg": f"Unknown action: {action}"}
    finally:
        bot.close()


def main():
    parser = argparse.ArgumentParser(description="Bybit Trade Tools")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--side", default=None)
    parser.add_argument("--order_type", default="Market")
    parser.add_argument("--qty", default=None)
    parser.add_argument("--price", default=None)
    parser.add_argument("--time_in_force", default="GTC")
    parser.add_argument("--action", default="place_order")
    parser.add_argument("--order_id", default=None)
    parser.add_argument("--leverage", default=None)
    parser.add_argument("--tp", default=None)
    parser.add_argument("--sl", default=None)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
