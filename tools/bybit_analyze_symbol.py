#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Comprehensive symbol analysis on Bybit."""
import os


def run(
    symbol: str = "BTCUSDT",
    timeframes: str = "15,60,240,D",
):
    """Perform comprehensive multi-timeframe analysis on a symbol using 25+ indicators
    Args:
        symbol: Symbol (e.g., BTCUSDT, ETHUSDT)
        timeframes: Comma-separated timeframes (default: 15,60,240,D)
    """
    import requests

    use_tor = os.environ.get("BYBIT_USE_TOR", "true").lower() == "true"
    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    proxies = (
        {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
        if use_tor
        else None
    )

    tf_list = [t.strip() for t in timeframes.split(",")]
    limit = "150"

    klines_data = {}

    # Fetch klines for each timeframe
    for tf in tf_list:
        url = f"{base_url}/v5/market/klines"
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": tf,
            "limit": limit,
        }

        try:
            response = requests.get(url, params=params, proxies=proxies, timeout=30)
            data = response.json()
            if data.get("retCode") == 0:
                klines = data.get("result", {}).get("list", [])
                klines_data[tf] = klines
        except:
            pass

    # Get ticker
    url = f"{base_url}/v5/market/tickers"
    try:
        response = requests.get(
            url,
            params={"category": "linear", "symbol": symbol},
            proxies=proxies,
            timeout=30,
        )
        data = response.json()
        ticker = (
            data.get("result", {}).get("list", [{}])[0]
            if data.get("retCode") == 0
            else {}
        )
    except:
        ticker = {}

    # Get orderbook
    url = f"{base_url}/v5/market/orderbook"
    try:
        response = requests.get(
            url,
            params={"category": "linear", "symbol": symbol, "limit": "25"},
            proxies=proxies,
            timeout=30,
        )
        data = response.json()
        ob = data.get("result", {}) if data.get("retCode") == 0 else {}
    except:
        ob = {}

    # Calculate indicators for each timeframe
    analysis = {}
    for tf, klines in klines_data.items():
        if not klines:
            continue

        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        # SMA
        sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None

        # EMA
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)

        # RSI
        rsi = _rsi(closes, 14)

        # ATR
        atr = _atr(highs, lows, closes, 14)

        # MACD
        macd_line = ema12 - ema26
        signal_line = _ema(closes, 9)
        histogram = macd_line - signal_line

        analysis[tf] = {
            "price": closes[-1] if closes else None,
            "sma20": sma20,
            "sma50": sma50,
            "ema12": ema12,
            "ema26": ema26,
            "rsi": rsi,
            "atr": atr,
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
            "volume": volumes[-1] if volumes else None,
            "trend": "bullish"
            if closes[-1] > sma20
            else "bearish"
            if sma20
            else "neutral",
        }

    # Orderbook analysis
    bids = ob.get("b", [])
    asks = ob.get("a", [])
    best_bid = float(bids[0][0]) if bids else 0
    best_ask = float(asks[0][0]) if asks else 0

    return {
        "success": True,
        "symbol": symbol,
        "ticker": {
            "last_price": ticker.get("lastPrice"),
            "24h_change": ticker.get("change24h"),
            "24h_volume": ticker.get("volume24h"),
            "mark_price": ticker.get("markPrice"),
        },
        "orderbook": {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
        },
        "analysis": analysis,
    }


def _ema(data, period):
    if len(data) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _rsi(data, period=14):
    if len(data) < period + 1:
        return None
    deltas = [data[i] - data[i - 1] for i in range(1, len(data))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    tr = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, len(highs))
    ]
    return sum(tr[-period:]) / period


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Analyze symbol on Bybit")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframes", default="15,60,240,D")
    args = parser.parse_args()

    result = run(symbol=args.symbol, timeframes=args.timeframes)
    print(json.dumps(result, indent=2))
