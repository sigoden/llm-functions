#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Get ticker information from Bybit exchange."""
import os


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
):
    """Get ticker information from Bybit V5 API
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol filter (optional, gets all if not provided)
    """
    import requests

    use_tor = os.environ.get("BYBIT_USE_TOR", "true").lower() == "true"
    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    url = f"{base_url}/v5/market/tickers"

    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    proxies = (
        {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
        if use_tor
        else None
    )

    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=30)
        data = response.json()

        if data.get("retCode") == 0:
            tickers = data.get("result", {}).get("list", [])
            return {"success": True, "count": len(tickers), "tickers": tickers}
        else:
            return {"success": False, "error": data.get("retMsg")}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get tickers from Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    result = run(category=args.category, symbol=args.symbol)
    print(json.dumps(result, indent=2))
