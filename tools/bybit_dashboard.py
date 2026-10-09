#!/usr/bin/env python3
"""Unified Trading Dashboard for Bybit."""

import json

import bybit_core


def run(
    symbols: str = "BTCUSDT,ETHUSDT,TRUMPUSDT",
):
    """Unified Trading Dashboard for a summary of balance, positions, and key symbols
    Args:
        symbols: Comma-separated symbols to watch (default: BTCUSDT,ETHUSDT,TRUMPUSDT)
    """
    result = {"success": True, "balance": None, "positions": [], "tickers": []}

    # Get balance
    try:
        data = bybit_core.api_request(
            "GET", "/v5/account/wallet", params={"accountType": "UNIFIED"}, signed=True
        )
        if data.get("retCode") == 0:
            result["balance"] = (
                data.get("result", {}).get("list", [{}])[0].get("coin", [])
            )
    except:
        pass

    # Get positions
    try:
        data = bybit_core.api_request(
            "GET",
            "/v5/position/list",
            params={"category": "linear", "settleCoin": "USDT"},
            signed=True,
        )
        if data.get("retCode") == 0:
            positions = data.get("result", {}).get("list", [])
            result["positions"] = [p for p in positions if float(p.get("size", 0)) > 0]
    except:
        pass

    # Get tickers
    symbol_list = [s.strip() for s in symbols.split(",")]
    for sym in symbol_list:
        try:
            data = bybit_core.api_request(
                "GET",
                "/v5/market/tickers",
                params={"category": "linear", "symbol": sym},
                signed=False,
            )
            if data.get("retCode") == 0:
                tickers = data.get("result", {}).get("list", [])
                result["tickers"].extend(tickers)
        except:
            pass

    return result


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Get trading dashboard from Bybit")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,TRUMPUSDT")
    args = parser.parse_args()

    result = run(symbols=args.symbols)
    print(json.dumps(result, indent=2))
