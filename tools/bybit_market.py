#!/usr/bin/env python3
# @describe Bybit Market Data Tools - Get orderbook, ticker, klines, funding rate
# @option --symbol!        Trading pair (e.g., BTCUSDT)
# @option --action         Action: orderbook|ticker|klines|funding|instruments
# @option --limit          Orderbook/klines limit (default: 50)
# @option --interval       Kline interval: 1|3|5|15|30|60|120|240|D (default: 15)
# @option --use_tor       Route through Tor proxy (default: true)
"""
Bybit Market Data Tools
Public market data endpoints — no authentication required.

Runs on the unified `tools.bybit` client (no pybit dependency).
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bybit.terminal import BybitRealm  # noqa: E402


def _realm():
    return BybitRealm()


def bybit_get_orderbook(symbol, limit=50):
    """Get orderbook depth (L2)"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_orderbook(symbol, limit=limit), indent=2))
    finally:
        bot.close()


def bybit_get_ticker(symbol):
    """Get 24h ticker information"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_ticker(symbol), indent=2))
    finally:
        bot.close()


def bybit_get_klines(symbol, interval="15", limit=100):
    """Get candlestick/kline data"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_klines(symbol, interval=interval, limit=limit), indent=2))
    finally:
        bot.close()


def bybit_get_funding_rate(symbol):
    """Get current funding rate"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_funding_rate(symbol, limit=1), indent=2))
    finally:
        bot.close()


def bybit_get_instruments(symbol):
    """Get instrument info (tick size, lot size, etc.)"""
    bot = _realm()
    try:
        print(json.dumps(bot.get_instruments_info("linear", symbol), indent=2))
    finally:
        bot.close()


def run(**kwargs) -> dict:
    """Unified entry point (used by run-tool.py)."""
    action = kwargs.get("action", "ticker")
    symbol = kwargs.get("symbol")
    if not symbol:
        return {"error": "--symbol is required"}
    limit = int(kwargs.get("limit", 50))
    interval = kwargs.get("interval", "15")

    bot = _realm()
    try:
        if action == "orderbook":
            return bot.get_orderbook(symbol, limit=limit)
        if action == "ticker":
            return bot.get_ticker(symbol)
        if action == "klines":
            return bot.get_klines(symbol, interval=interval, limit=limit)
        if action == "funding":
            return bot.get_funding_rate(symbol, limit=1)
        if action == "instruments":
            return bot.get_instruments_info("linear", symbol)
        return {"error": f"Unknown action: {action}"}
    finally:
        bot.close()


def main():
    parser = argparse.ArgumentParser(description="Bybit Market Tools")
    parser.add_argument("--action", default="ticker", help="Action to perform")
    parser.add_argument("--symbol", required=True, help="Trading pair")
    parser.add_argument("--limit", type=int, default=50, help="Limit")
    parser.add_argument("--interval", default="15", help="Kline interval")
    args = parser.parse_args()

    print(json.dumps(run(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
