#!/usr/bin/env python3
"""Get open orders from Bybit exchange."""

import json

import bybit_core


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
):
    """Get open orders from Bybit V5 API
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol filter (optional)
    """
    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    data = bybit_core.api_request(
        "GET", "/v5/order/realtime", params=params, signed=True
    )

    if data.get("retCode") == 0:
        orders = data.get("result", {}).get("list", [])
        return {
            "success": True,
            "count": len(orders),
            "orders": orders,
            "data": data.get("result"),
        }
    else:
        return {
            "success": False,
            "error": data.get("retMsg"),
            "retCode": data.get("retCode"),
        }


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get open orders from Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    result = run(category=args.category, symbol=args.symbol)
    print(json.dumps(result, indent=2))
