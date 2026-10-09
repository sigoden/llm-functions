#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Optional

# Add utils to path for bybit_base import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
import bybit_base


def run(
    category: str,
    symbol: Optional[str] = None,
    testnet: Optional[bool] = None,
    use_tor: Optional[bool] = None,
):
    """View open orders from Bybit exchange using V5 API
    with Tor support for privacy.
    Args:
        category: Product type (linear/inverse/option/spot)
        symbol: Symbol name
        testnet: Whether to use testnet
        use_tor: Whether to use Tor
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

    params = {"category": category}
    if symbol:
        params["symbol"] = symbol
    elif category == "linear":
        params["settleCoin"] = "USDT"

    resp = bybit_base.api_request("GET", "/v5/order/realtime", params, signed=True)
    if resp.get("retCode") != 0:
        return json.dumps(
            {"error": f"Orders fetch failed: {resp.get('retMsg')}"}, indent=2
        )

    orders = resp.get("result", {}).get("list", [])

    # Format Human-Readable Output
    summary_lines = []
    if orders:
        summary_lines.append("\n--- Open Orders ---")
        for o in orders:
            price = o.get("price") if o.get("orderType") == "Limit" else "Market"
            summary_lines.append(
                f"{o['symbol']} | Side: {o['side']} | Type: {o['orderType']} | Price: {price} | Qty: {o['qty']} | Status: {o['orderStatus']}"
            )
    else:
        summary_lines.append("\nNo open orders found.")

    results = {
        "category": category,
        "symbol_filter": symbol or "All",
        "open_orders": orders,
    }

    connection_info = (
        f"\n🔒 Connection: Tor (Exit IP: {exit_ip})"
        if exit_ip
        else "\n🔓 Connection: Direct"
    )
    return f"""✅ Open Orders Retrieved Successfully!{connection_info}
{"".join(summary_lines)}

JSON Data:
{json.dumps(results, indent=2)}"""


def run(**kwargs):
    """Entry point for the tool runner."""
    return run(**kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--symbol")
    parser.add_argument("--testnet", type=lambda x: str(x).lower() == "true")
    parser.add_argument("--use-tor", type=lambda x: str(x).lower() == "true")
    args = parser.parse_args()
    print(run(args.category, args.symbol, args.testnet, args.use_tor))
