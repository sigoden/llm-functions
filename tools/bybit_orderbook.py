#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Get orderbook information from Bybit exchange."""
import os


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
    limit: str = "25",
):
    """Get orderbook information from Bybit V5 API
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol name (e.g., BTCUSDT, ETHUSDT)
        limit: Limit depth (default: 25)
    """
    import requests

    use_tor = os.environ.get("BYBIT_USE_TOR", "true").lower() == "true"
    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    url = f"{base_url}/v5/market/orderbook"

    params = {"category": category, "symbol": symbol, "limit": limit}

    proxies = (
        {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
        if use_tor
        else None
    )

    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=30)
        data = response.json()

        if data.get("retCode") == 0:
            result = data.get("result", {})
            bids = result.get("b", [])
            asks = result.get("a", [])

            # Calculate spread
            best_bid = float(bids[0][0]) if bids else 0
            best_ask = float(asks[0][0]) if asks else 0
            spread = best_ask - best_bid if best_bid and best_ask else 0

            return {
                "success": True,
                "symbol": symbol,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "bids": bids[:5],
                "asks": asks[:5],
            }
        else:
            return {"success": False, "error": data.get("retMsg")}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get orderbook from Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", default="25")
    args = parser.parse_args()

    result = run(category=args.category, symbol=args.symbol, limit=args.limit)
    print(json.dumps(result, indent=2))
