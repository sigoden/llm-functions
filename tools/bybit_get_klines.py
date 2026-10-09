#!/usr/bin/env python3
# ==============================================================================
# bybit_get_klines.py — Bybit Get Klines Tool (Tor-Ready)
#
# @describe Get candlestick/kline data from Bybit exchange using V5 API
# @option --category! <linear|inverse|spot>        Product type (linear/inverse/spot)
# @option --symbol! <SYMBOL>                     Symbol name (e.g., BTCUSDT, ETHUSDT)
# @option --interval <1|3|5|15|30|60|120|240|360|720|D|W|M>  Time interval (default: 1)
# @option --start <TIMESTAMP>                    Start timestamp (optional)
# @option --end <TIMESTAMP>                      End timestamp (optional)
# @option --limit <NUMBER>                       Limit number of results (default: 200)
# @option --testnet <true|false>                 Use testnet (default: true)
# @option --use-tor <true|false>                 Use Tor for privacy (default: true)
# ==============================================================================

import json
import os
import sys
from typing import Optional

import requests

# Add utils to path for tor_utils import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
from tor_utils import get_proxy_config, verify_tor_connection


def run(
    category: str,
    symbol: str,
    interval: str = "1",
    start: Optional[int] = None,
    end: Optional[int] = None,
    limit: int = 200,
    testnet: bool = True,
    use_tor: bool = True,
):
    """
    Get candlestick/kline data from Bybit exchange using V5 API
    with Tor support for privacy.
    """

    # Set base URL
    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

    # Prepare request parameters
    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    if start:
        params["start"] = start
    if end:
        params["end"] = end

    # Get proxy configuration with Tor support
    proxies, timeout, user_agent = get_proxy_config(use_tor=use_tor)

    # Verify Tor connection if using Tor
    exit_ip = None
    if use_tor and proxies:
        try:
            exit_ip = verify_tor_connection()
        except RuntimeError as e:
            return f"⚠️ Tor Error: {e}\nFalling back to direct connection..."

    # Prepare headers
    headers = {
        "User-Agent": user_agent,
    }

    try:
        # Make the request (no authentication needed for public endpoints)
        response = requests.get(
            f"{base_url}/v5/market/kline",
            params=params,
            proxies=proxies,
            timeout=timeout,
            headers=headers,
        )

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            return f"""❌ API Response Error!
Status Code: {response.status_code}
Response Text: {response.text}

This often happens if the API is blocked by CloudFront or a firewall.
"""

        if response.status_code == 200 and response_data.get("retCode") == 0:
            result = response_data.get("result", {})
            klines = result.get("list", [])

            if klines:
                # Format kline data (reverse order to show oldest first)
                kline_info = []
                for kline in reversed(klines[:20]):  # Show last 20 candles
                    timestamp = kline[0]
                    open_price = kline[1]
                    high_price = kline[2]
                    low_price = kline[3]
                    close_price = kline[4]
                    volume = kline[5]
                    turnover = kline[6] if len(kline) > 6 else "N/A"

                    kline_info.append(f"""
Time: {timestamp}
Open: {open_price}
High: {high_price}
Low: {low_price}
Close: {close_price}
Volume: {volume}
Turnover: {turnover}
""")

                # Calculate basic stats
                latest = klines[0] if klines else None
                stats = ""
                if latest:
                    stats = f"""
Latest Candle:
Open: {latest[1]}
High: {latest[2]}
Low: {latest[3]}
Close: {latest[4]}
Volume: {latest[5]}
"""

                connection_info = (
                    f"\n🔒 Connection: Tor (Exit IP: {exit_ip})"
                    if exit_ip
                    else "\n🔓 Connection: Direct"
                )
                return f"""✅ Kline Data Retrieved Successfully!{connection_info}

Symbol: {symbol}
Category: {category}
Interval: {interval}
Total Candles: {len(klines)}
Start: {start or "Auto"}
End: {end or "Auto"}
{stats}

Recent Candles (showing last 20):
{"".join(kline_info)}

Full Response:
{json.dumps(response_data, indent=2)}"""
            else:
                return f"""✅ Kline Data Retrieved Successfully!
No kline data found for the specified criteria.

Full Response:
{json.dumps(response_data, indent=2)}"""
        else:
            return f"""❌ Kline Retrieval Failed!
Status Code: {response.status_code}
Error Code: {response_data.get("retCode", "N/A")}
Error Message: {response_data.get("retMsg", "Unknown error")}

Full Response:
{json.dumps(response_data, indent=2)}"""

    except requests.exceptions.RequestException as e:
        return f"❌ Network Error: {e!s}"
    except Exception as e:
        return f"❌ Unexpected Error: {e!s}"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--category")
    parser.add_argument("--symbol")
    parser.add_argument("--interval")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--testnet", type=lambda x: str(x).lower() == "true")
    parser.add_argument("--use-tor", type=lambda x: str(x).lower() == "true")
    args = parser.parse_args()
    kwargs = {k: v for k, v in vars(args).items() if v is not None}
    print(run(**kwargs))
