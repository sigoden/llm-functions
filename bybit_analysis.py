#!/usr/bin/env python3
# @describe Bybit Advanced Analysis - Technical indicators, orderbook, multi-TF analysis
# @option --symbol!        Trading pair (e.g., BTCUSDT)
# @option --action         Action: indicators|analyze|orderbook|smart_order|price_action
# @option --interval       Kline interval: 1|3|5|15|30|60|120|240|D (default: 60)
# @option --limit          Number of candles (default: 100)
# @option --side           Order side for smart_order: Buy|Sell
# @option --qty            Quantity for smart_order
# @option --risk_pct       Risk percentage for smart_order (default: 1.0)
# @option --use_tor       Route through Tor proxy (default: true)
"""
Bybit Advanced Analysis Tools
Technical indicators, orderbook analysis, price-action signals and smart orders.

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


def bybit_get_indicators(symbol, interval=60, limit=100):
    """Get technical indicators (RSI, EMA, ATR, MACD, ADX, Bollinger)"""
    bot = _realm()
    try:
        result = {
            "symbol": symbol,
            "interval": interval,
            "rsi": bot.calculate_rsi(symbol, interval),
            "ema_20": bot.calculate_ema(symbol, interval, 20),
            "ema_50": bot.calculate_ema(symbol, interval, 50),
            "atr": bot.calculate_atr(symbol, interval),
            "macd": bot.calculate_macd(symbol, interval),
            "adx": bot.calculate_adx(symbol, interval),
            "bollinger": bot.calculate_bollinger_bands(symbol, interval),
            "stochastic": bot.calculate_stochastic(symbol, interval),
        }
        print(json.dumps(result, indent=2))
    finally:
        bot.close()


def bybit_analyze_symbol(symbol):
    """Multi-timeframe analysis (15m, 1h, 4h, 1D)"""
    bot = _realm()
    try:
        results = {}
        for tf, name in [("15", "15m"), ("60", "1h"), ("240", "4h"), ("D", "1D")]:
            reg = bot.get_market_regime(symbol, tf)
            ema = bot.calculate_ema(symbol, tf, 20)
            ticker = bot.get_ticker(symbol).get("list", [{}])[0]
            price = float(ticker.get("lastPrice", 0) or 0)
            trend = (
                "Bullish"
                if price > ema.get("ema", price)
                else "Bearish"
            )
            momentum = (
                "Strong"
                if ema.get("ema") and abs(price - ema["ema"]) / ema["ema"] > 0.01
                else "Weak"
            )
            results[name] = {
                "trend": trend,
                "momentum": momentum,
                "price": price,
                "ema20": ema.get("ema"),
                "regime": reg.get("regime"),
            }
        print(json.dumps(results, indent=2))
    finally:
        bot.close()


def bybit_analyze_orderbook(symbol, limit=25):
    """Analyze orderbook depth and imbalance"""
    bot = _realm()
    try:
        result = bot.get_orderbook_analysis(symbol, limit)
        print(json.dumps(result, indent=2))
    finally:
        bot.close()


def bybit_price_action(symbol, interval=60, limit=300):
    """Price-action trend analysis: structure, EMA stack, S/R zones, signal"""
    bot = _realm()
    try:
        result = bot.price_action_analysis(symbol, interval, limit)
        print(json.dumps(result, indent=2))
    finally:
        bot.close()


def bybit_smart_order(symbol, side, qty=None, risk_pct=1.0):
    """Smart order with automatic risk-based position sizing and ATR stop.

    `qty` is optional: when omitted the position is sized from `risk_pct`
    of the wallet balance and a 2x-ATR stop (risk-first sizing)."""
    bot = _realm()
    try:
        if qty is not None:
            # Fixed-qty path: ATR-bracketed market order with TP/SL attached
            atr = bot.calculate_atr(symbol, "15").get("atr", 0) or 0
            ticker = bot.get_ticker(symbol).get("list", [{}])[0]
            price = float(ticker.get("lastPrice", 0) or 0)
            if price and atr:
                if side.lower() in ("buy", "long"):
                    tp, sl = price + 2 * atr, price - 1.5 * atr
                else:
                    tp, sl = price - 2 * atr, price + 1.5 * atr
                result = bot.place_order(
                    symbol, side, qty, "Market", stop_loss=sl, take_profit=tp
                )
            else:
                result = bot.place_order(symbol, side, qty, "Market")
        else:
            result = bot.place_smart_order(
                symbol=symbol, side=side, risk_pct=risk_pct
            )
        print(json.dumps(result, indent=2))
    finally:
        bot.close()


def run(**kwargs) -> dict:
    """Unified entry point (used by run-tool.py)."""
    action = kwargs.get("action", "indicators")
    symbol = kwargs.get("symbol")
    if not symbol:
        return {"error": "--symbol is required"}
    interval = kwargs.get("interval", "60")
    limit = int(kwargs.get("limit", 100))

    bot = _realm()
    try:
        if action == "indicators":
            return {
                "symbol": symbol,
                "interval": interval,
                "rsi": bot.calculate_rsi(symbol, interval),
                "ema_20": bot.calculate_ema(symbol, interval, 20),
                "ema_50": bot.calculate_ema(symbol, interval, 50),
                "atr": bot.calculate_atr(symbol, interval),
                "macd": bot.calculate_macd(symbol, interval),
                "adx": bot.calculate_adx(symbol, interval),
            }
        if action == "analyze":
            res = {}
            for tf in ["15", "60", "240", "D"]:
                reg = bot.get_market_regime(symbol, tf)
                ema = bot.calculate_ema(symbol, tf, 20)
                ticker = bot.get_ticker(symbol).get("list", [{}])[0]
                price = float(ticker.get("lastPrice", 0) or 0)
                res[tf] = {
                    "trend": "Bullish" if price > ema.get("ema", price) else "Bearish",
                    "price": price,
                    "ema20": ema.get("ema"),
                    "regime": reg.get("regime"),
                }
            return res
        if action == "orderbook":
            return bot.get_orderbook_analysis(symbol, limit)
        if action == "price_action":
            return bot.price_action_analysis(symbol, interval, limit)
        if action == "smart_order":
            qty = kwargs.get("qty")
            risk_pct = float(kwargs.get("risk_pct", 1.0))
            if qty is not None:
                return bot.place_smart_order(
                    symbol=symbol,
                    side=kwargs.get("side", "Buy"),
                    risk_pct=risk_pct,
                )
            return bot.place_smart_order(
                symbol=symbol, side=kwargs.get("side", "Buy"), risk_pct=risk_pct
            )
        return {"error": f"Unknown action: {action}"}
    finally:
        bot.close()


def main():
    parser = argparse.ArgumentParser(description="Bybit Analysis Tools")
    parser.add_argument("--action", default="indicators", help="Action to perform")
    parser.add_argument("--symbol", required=True, help="Trading pair")
    parser.add_argument("--interval", default="60", help="Kline interval")
    parser.add_argument("--limit", type=int, default=100, help="Limit")
    parser.add_argument("--side", default="Buy", help="Buy or Sell")
    parser.add_argument("--qty", type=float, default=None, help="Quantity")
    parser.add_argument("--risk_pct", type=float, default=1.0, help="Risk %")
    args = parser.parse_args()

    print(json.dumps(run(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
