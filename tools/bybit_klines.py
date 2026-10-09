#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Get candlestick/kline data from Bybit exchange."""
import os
import sys


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
    interval: str = "60",
    start: str = None,
    end: str = None,
    limit: str = "200",
):
    """Get candlestick/kline data from Bybit V5 API
    Args:
        category: Product type (linear/inverse/spot)
        symbol: Symbol name (e.g., BTCUSDT, ETHUSDT)
        interval: Time interval (1,3,5,15,30,60,120,240,360,720,D,W,M)
        start: Start timestamp (optional)
        end: End timestamp (optional)
        limit: Limit number of results (default: 200)
    """
    import requests

    use_tor = os.environ.get("BYBIT_USE_TOR", "true").lower() == "true"
    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    url = f"{base_url}/v5/market/klines"

    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    proxies = (
        {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
        if use_tor
        else None
    )

    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=30)
        data = response.json()

        if data.get("retCode") == 0:
            klines = data.get("result", {}).get("list", [])
            return {"success": True, "count": len(klines), "klines": klines[:10]}
        else:
            return {"success": False, "error": data.get("retMsg")}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get klines from Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="60")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", default="200")
    args = parser.parse_args()

    result = run(
        category=args.category,
        symbol=args.symbol,
        interval=args.interval,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2))
