#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Set leverage for trading on Bybit exchange."""
import os


def run(
    category: str = "linear",
    symbol: str = "BTCUSDT",
    leverage: str = "10",
):
    """Set leverage for trading on Bybit V5 API
    Args:
        category: Product type (linear/inverse)
        symbol: Symbol name (e.g., BTCUSDT, ETHUSDT)
        leverage: Leverage multiplier (e.g., 1, 2, 5, 10)
    """
    import bybit_core
    import bybit_turso_logger

    params = {
        "category": category,
        "symbol": symbol,
        "buyLeverage": leverage,
        "sellLeverage": leverage,
    }

    data = bybit_core.api_request(
        "POST", "/v5/position/set-leverage", params=params, signed=True
    )

    if data.get("retCode") == 0 or data.get("retCode") == 110043:
        # Log leverage change to Turso
        bybit_turso_logger.log_event(
            "LEVERAGE_CHANGE",
            {
                "symbol": symbol,
                "details": f"Leverage: {leverage}, Category: {category}, Resp: {data.get('retMsg')}",
            },
        )
        return {"success": True, "symbol": symbol, "leverage": leverage}
    else:
        return {"success": False, "error": data.get("retMsg")}


if __name__ == "__main__":
    import json
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Set leverage on Bybit")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--leverage", default="10")
    args = parser.parse_args()

    result = run(
        category=args.category, symbol=args.symbol, leverage=args.leverage
    )
    print(json.dumps(result, indent=2))
