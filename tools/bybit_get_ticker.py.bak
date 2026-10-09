#!/usr/bin/env python3
import argparse
import json
from typing import Literal, Optional

import bybit_core
import requests


def run_tool(
    category: Literal["linear", "inverse", "option", "spot"],
    symbol: Optional[str] = None,
    use_tor: bool = True,
) -> str:
    """Get ticker data from Bybit.
    Args:
        category: Product type (linear, inverse, option, spot)
        symbol: Symbol (optional)
        use_tor: Use Tor (default: True)
    """
    params = {"category": category}
    if symbol:
        params["symbol"] = symbol

    # Simple public request
    cfg = bybit_core.get_config()
    cfg["use_tor"] = use_tor
    resp = requests.get(
        "https://api.bybit.com/v5/market/tickers",
        params=params,
        proxies=cfg["proxies"] if use_tor else None,
        timeout=30,
    ).json()

    return json.dumps(resp, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--symbol")
    parser.add_argument(
        "--use-tor", type=lambda x: str(x).lower() == "true", default=True
    )
    args = parser.parse_args()
    print(run_tool(args.category, args.symbol, use_tor=args.use_tor))
