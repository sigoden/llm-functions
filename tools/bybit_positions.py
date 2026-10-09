#!/usr/bin/env python3
"""View positions on Bybit exchange."""

import json

import bybit_core


def run(
    category: str = "linear",
    symbol: str = "",
):
    """View positions from Bybit V5 API
    Args:
        category: Product type (linear/inverse)
        symbol: Symbol filter (optional, gets all if not provided)
    """
    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    data = bybit_core.api_request(
        "GET", "/v5/position/list", params=params, signed=True
    )

    if data.get("retCode") == 0:
        positions = data.get("result", {}).get("list", [])
        # Filter active positions
        active = [p for p in positions if float(p.get("size", 0)) > 0]
        return {
            "success": True,
            "count": len(active),
            "positions": active,
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

    parser = ArgumentParser(description="Get positions from Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="")
    args = parser.parse_args()

    result = run(category=args.category, symbol=args.symbol)
    print(json.dumps(result, indent=2))
