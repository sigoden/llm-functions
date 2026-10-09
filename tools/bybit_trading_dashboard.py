#!/usr/bin/env python3
# @describe Unified Trading Dashboard for a summary of balance, positions, and key symbols
# @option --symbols <TEXT> Comma-separated symbols to watch (default: BTCUSDT,ETHUSDT,TRUMPUSDT)
# @option --testnet <BOOL> Use testnet (default: from .env)
# @option --use_tor <BOOL> Route traffic through Tor network (default: from .env)
"""
bybit_trading_dashboard.py - Unified Trading Dashboard (Tor-Ready)

Usage:
  python bybit_trading_dashboard.py --symbols BTCUSDT,ETHUSDT --use_tor true
  python bybit_trading_dashboard.py --testnet true

Environment Variables:
  BYBIT_USE_TOR=true/false - Enable Tor routing
  TOR_SOCKS_HOST=127.0.0.1 - Tor SOCKS proxy host
  TOR_SOCKS_PORT=9050 - Tor SOCKS proxy port
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
from utils import bybit_base


def run(
    symbols: str = "BTCUSDT,ETHUSDT,TRUMPUSDT",
    testnet: bool = None,
    use_tor: bool = None,
):
    config = bybit_base.get_config()
    if testnet is not None:
        config["testnet"] = testnet
    if use_tor is not None:
        config["use_tor"] = use_tor

    # Display connection info
    if config.get("use_tor"):
        print(f"[Dashboard] Using Tor proxy: {bybit_base.TOR_PROXIES}")
    else:
        print(f"[Dashboard] Using direct connection: {config['base_url']}")

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    # 1. Account Summary
    balance_resp = bybit_base.api_request(
        "GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True
    )
    if balance_resp.get("retCode") != 0:
        return json.dumps(
            {"error": f"Balance fetch failed: {balance_resp.get('retMsg')}"}, indent=2
        )

    wallet = balance_resp["result"]["list"][0]
    summary = {
        "account": "UNIFIED",
        "total_equity": float(wallet["totalEquity"]),
        "total_margin_balance": float(wallet["totalMarginBalance"]),
        "total_available_balance": float(wallet["totalAvailableBalance"]),
        "total_pnl": float(wallet["totalPerpUPL"]),
        "coins": {
            c["coin"]: float(c["walletBalance"])
            for c in wallet["coin"]
            if float(c["walletBalance"]) > 0
        },
    }

    # 2. Open Positions
    pos_resp = bybit_base.api_request(
        "GET",
        "/v5/position/list",
        {"category": "linear", "settleCoin": "USDT"},
        signed=True,
    )
    active_positions = []
    if pos_resp.get("retCode") == 0:
        for p in pos_resp["result"]["list"]:
            if float(p["size"]) != 0:
                active_positions.append(
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "size": p["size"],
                        "entry": p["avgPrice"],
                        "mark": p["markPrice"],
                        "uPnl": p["unrealisedPnl"],
                        "liq": p["liqPrice"],
                    }
                )

    # 3. Market Watch
    market_data = []
    for symbol in symbol_list:
        ticker = bybit_base.api_request(
            "GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )
        if ticker.get("retCode") == 0 and ticker["result"]["list"]:
            t = ticker["result"]["list"][0]

            # Fetch indicators for watch list
            kline = bybit_base.api_request(
                "GET",
                "/v5/market/kline",
                {"category": "linear", "symbol": symbol, "interval": "60", "limit": 50},
            )
            rsi_val = None
            if kline.get("retCode") == 0:
                closes = [float(k[4]) for k in reversed(kline["result"]["list"])]
                rsi = bybit_base.calculate_rsi(closes)
                rsi_val = rsi[-1] if rsi else None

            market_data.append(
                {
                    "symbol": symbol,
                    "price": t["lastPrice"],
                    "change24h": f"{float(t['price24hPcnt']) * 100:.2f}%",
                    "rsi": rsi_val,
                }
            )

    dashboard = {
        "timestamp": bybit_base.now_iso(),
        "account_summary": summary,
        "active_positions": active_positions,
        "market_watch": market_data,
    }

    return json.dumps(dashboard, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified Trading Dashboard")
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,TRUMPUSDT",
        help="Comma-separated symbols to watch",
    )
    parser.add_argument(
        "--testnet",
        type=lambda x: str(x).lower() == "true",
        help="Use testnet (default: from .env)",
    )
    parser.add_argument(
        "--use_tor",
        type=lambda x: str(x).lower() == "true",
        help="Route traffic through Tor (default: from .env)",
    )
    args = parser.parse_args()
    print(run(args.symbols, args.testnet, args.use_tor))
