#!/usr/bin/env python3
"""Cancel all orders on Bybit exchange."""

import json

import bybit_core
import bybit_turso_logger


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
):
    """Cancel all orders for a symbol on Bybit V5 API
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol name (e.g., BTCUSDT, ETHUSDT)
    """
    params = {"category": category, "symbol": symbol}

    data = bybit_core.api_request(
        "POST", "/v5/order/cancel-all", params=params, signed=True
    )

    if data.get("retCode") == 0:
        # Log mass cancellation to Turso
        bybit_turso_logger.log_event(
            "ORDER_CANCEL_ALL",
            {
                "symbol": symbol,
                "details": f"Category: {category}, Count: {len(data.get('result', {}).get('list', []))}",
            },
        )
        return {
            "success": True,
            "cancelled_count": len(data.get("result", {}).get("list", [])),
            "symbol": symbol,
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

    parser = ArgumentParser(description="Cancel all orders on Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()

    result = run(category=args.category, symbol=args.symbol)
    print(json.dumps(result, indent=2))
