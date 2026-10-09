#!/usr/bin/env python3
# @describe Get current price of a cryptocurrency (e.g., bitcoin, ethereum).
# @arg coin! The coin name (e.g., bitcoin, ethereum).

import json
import os
import sys

import ccxt


def run(coin):
    try:
        # Use CCXT to fetch ticker, which is more robust than CoinGecko API
        # Mapping for popular coins
        mapping = {
            "bitcoin": "BTC/USDT",
            "ethereum": "ETH/USDT",
            "solana": "SOL/USDT",
        }
        symbol = mapping.get(coin.lower(), f"{coin.upper()}/USDT")

        exchange = ccxt.gateio()
        ticker = exchange.fetch_ticker(symbol)

        result = {coin.lower(): {"usd": ticker["last"]}}
        return result
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    # Check for argc environment variables first
    coin = os.environ.get("argc_coin")
    if not coin and len(sys.argv) > 1:
        coin = sys.argv[1]

    if coin:
        print(json.dumps(run(coin)))
    else:
        print("Usage: crypto_price.py <coin>")
        sys.exit(1)
