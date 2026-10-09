# @describe Get Bybit orderbook
# @option --category <linear|inverse> Product type
# @option --symbol <SYMBOL> Symbol
# @option --limit <NUMBER> Limit
# @option --use-tor <BOOL> Use Tor
#!/usr/bin/env python3
import argparse
import json

import bybit_core


def run(category, symbol, limit=25, use_tor=True):
    params = {"category": category, "symbol": symbol, "limit": limit}
    resp = bybit_core.api_request("GET", "/v5/market/orderbook", params=params)
    print(resp)

    if resp.get("retCode") != 0:
        return json.dumps(resp, indent=2)

    res = resp.get("result", {})
    bids = res.get("b", [])
    asks = res.get("a", [])

    summary = [f"--- Orderbook: {symbol} ---"]
    if bids and asks:
        spread = float(asks[0][0]) - float(bids[0][0])
        summary.append(f"Spread: {spread:.4f}")
    else:
        summary.append("Spread: N/A")

    summary.append("\nTop Bids:")
    for b in bids[:5]:
        summary.append(f"  Price: {b[0]} | Size: {b[1]}")
    summary.append("\nTop Asks:")
    for a in asks[:5]:
        summary.append(f"  Price: {a[0]} | Size: {a[1]}")

    return "\n".join(summary) + "\n\nJSON Data:\n" + json.dumps(resp, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--use-tor", type=lambda x: str(x).lower() == "true", default=True
    )
    args = parser.parse_args()
    print(run(args.category, args.symbol, args.limit, args.use_tor))
