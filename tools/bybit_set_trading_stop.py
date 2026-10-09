#!/usr/bin/env python3
"""Set trading stop (TP/SL) on Bybit exchange."""

import json

import bybit_core


def run(
    symbol: str = "BTCUSDT",
    tp_usdt: str = None,
    sl_usdt: str = None,
):
    """Set Trading Stop (TP/SL)
    Args:
        symbol: Symbol (e.g., BTCUSDT)
        tp_usdt: Take Profit price
        sl_usdt: Stop Loss price
    """
    params = {"category": "linear", "symbol": symbol}
    if tp_usdt:
        params["takeProfit"] = str(tp_usdt)
    if sl_usdt:
        params["stopLoss"] = str(sl_usdt)

    data = bybit_core.api_request(
        "POST", "/v5/position/trading-stop", params=params, signed=True
    )

    if data.get("retCode") == 0:
        return {
            "success": True,
            "symbol": symbol,
            "take_profit": tp_usdt,
            "stop_loss": sl_usdt,
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

    parser = ArgumentParser(description="Set trading stop on Bybit")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--tp-usdt", default=None)
    parser.add_argument("--sl-usdt", default=None)
    args = parser.parse_args()

    result = run(symbol=args.symbol, tp_usdt=args.tp_usdt, sl_usdt=args.sl_usdt)
    print(json.dumps(result, indent=2))
