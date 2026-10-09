#!/usr/bin/env python3
# ==============================================================================

import argparse
import json
import os
import sys
from typing import Optional

# Add utils to path for bybit_base import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
import bybit_base


def run(
    symbol: Optional[str] = None,
    include_orders: bool = False,
    testnet: Optional[bool] = None,
    use_tor: Optional[bool] = None,
) -> str:
    """View positions and optionally open orders from Bybit exchange.
    Args:
        symbol: Symbol (optional, e.g., BTCUSDT)
        include_orders: Include open orders (default: False)
        testnet: Use testnet (optional)
        use_tor: Use Tor (optional)
    """

    config = bybit_base.get_config()
    if testnet is not None:
        config["testnet"] = testnet
    if use_tor is not None:
        config["use_tor"] = use_tor

    exit_ip = None
    if config.get("use_tor"):
        try:
            exit_ip = bybit_base.verify_tor_connection()
        except bybit_base.TorError as e:
            return f"⚠️ Tor Error: {e}\nFalling back to direct connection..."

    # 1. Fetch Positions
    params = {"category": "linear", "settleCoin": "USDT"}
    if symbol:
        params["symbol"] = symbol

    resp = bybit_base.api_request("GET", "/v5/position/list", params, signed=True)
    if resp.get("retCode") != 0:
        return json.dumps(
            {"error": f"Positions fetch failed: {resp.get('retMsg')}"}, indent=2
        )

    positions = resp.get("result", {}).get("list", [])
    if symbol:
        filtered_positions = [p for p in positions if p["symbol"] == symbol]
    else:
        filtered_positions = [p for p in positions if float(p["size"]) != 0]

    # 2. Fetch Open Orders if requested
    open_orders = []
    if include_orders and symbol:
        order_params = {"category": "linear", "symbol": symbol}
        order_resp = bybit_base.api_request(
            "GET", "/v5/order/realtime", order_params, signed=True
        )
        if order_resp.get("retCode") == 0:
            open_orders = order_resp.get("result", {}).get("list", [])

    # 3. Format Human-Readable Output
    summary_lines = []
    if filtered_positions:
        summary_lines.append("\n--- Positions ---")
        for p in filtered_positions:
            summary_lines.append(
                f"{p['symbol']} | Side: {p['side']} | Size: {p['size']} | Entry: {p['avgPrice']} | Mark: {p['markPrice']} | UPNL: {p['unrealisedPnl']}"
            )

    if open_orders:
        summary_lines.append("\n--- Open Orders ---")
        for o in open_orders:
            summary_lines.append(
                f"{o['symbol']} | Side: {o['side']} | Type: {o['orderType']} | Price: {o.get('price', 'MKT')} | Qty: {o['qty']} | Status: {o['orderStatus']}"
            )

    results = {
        "symbol_filter": symbol if symbol else "All",
        "positions": filtered_positions,
        "open_orders": open_orders,
    }

    connection_info = (
        f"\n🔒 Connection: Tor (Exit IP: {exit_ip})"
        if exit_ip
        else "\n🔓 Connection: Direct"
    )
    return f"""✅ Status Retrieved Successfully!{connection_info}
{"".join(summary_lines)}

JSON Data:
{json.dumps(results, indent=2)}"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--include-orders", action="store_true")
    parser.add_argument("--testnet", type=lambda x: str(x).lower() == "true")
    parser.add_argument("--use-tor", type=lambda x: str(x).lower() == "true")
    args = parser.parse_args()
    print(run(args.symbol, args.include_orders, args.testnet, args.use_tor))
