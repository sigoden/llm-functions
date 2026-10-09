#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
"""Analyze L2 Orderbook for depth, imbalance, and walls."""
import json
import os


def run(
    symbol: str = "BTCUSDT",
    limit: int = 50,
) -> dict:
    """Analyze L2 Orderbook for depth, imbalance, and walls.
    Args:
        symbol: Symbol (e.g., BTCUSDT)
        limit: Depth limit (default: 50)
    """
    import requests

    use_tor = os.environ.get("BYBIT_USE_TOR", "true").lower() == "true"
    testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    url = f"{base_url}/v5/market/orderbook"

    params = {"category": "linear", "symbol": symbol, "limit": str(limit)}

    proxies = (
        {"http": "socks5://127.0.0.1:9050", "https": "socks5://127.0.0.1:9050"}
        if use_tor
        else None
    )

    try:
        response = requests.get(url, params=params, proxies=proxies, timeout=30)
        data = response.json()

        if data.get("retCode") != 0:
            return {"success": False, "error": data.get("retMsg")}

        result = data.get("result", {})
        bids = [[float(p), float(q)] for p, q in result.get("b", [])]
        asks = [[float(p), float(q)] for p, q in result.get("a", [])]

        if not bids or not asks:
            return {"success": False, "error": "Empty orderbook"}

        # Calculate metrics
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100

        # Cumulative volumes
        bid_volumes = [v for _, v in bids]
        ask_volumes = [v for _, v in asks]

        cum_bid_vol = []
        cum_ask_vol = []
        total_bid = 0
        total_ask = 0

        for v in bid_volumes:
            total_bid += v
            cum_bid_vol.append(total_bid)

        for v in ask_volumes:
            total_ask += v
            cum_ask_vol.append(total_ask)

        # Imbalance
        imbalance = (
            (total_bid - total_ask) / (total_bid + total_ask)
            if (total_bid + total_ask) > 0
            else 0
        )

        # Find walls (large orders)
        bid_walls = []
        ask_walls = []
        wall_threshold = max(total_bid, total_ask) * 0.1  # 10% of total

        for i, (price, vol) in enumerate(bids):
            if vol > wall_threshold:
                bid_walls.append(
                    {
                        "price": price,
                        "volume": vol,
                        "cum_pct": cum_bid_vol[i] / total_bid if total_bid > 0 else 0,
                    }
                )

        for i, (price, vol) in enumerate(asks):
            if vol > wall_threshold:
                ask_walls.append(
                    {
                        "price": price,
                        "volume": vol,
                        "cum_pct": cum_ask_vol[i] / total_ask if total_ask > 0 else 0,
                    }
                )

        # Depth zones
        depth_levels = 5
        bid_depth = []
        ask_depth = []

        for i in range(min(depth_levels, len(bids))):
            bid_depth.append(
                {
                    "level": i + 1,
                    "price": bids[i][0],
                    "volume": bids[i][1],
                    "cum_volume": cum_bid_vol[i],
                }
            )
            ask_depth.append(
                {
                    "level": i + 1,
                    "price": asks[i][0],
                    "volume": asks[i][1],
                    "cum_volume": cum_ask_vol[i],
                }
            )

        # Market sentiment
        if imbalance > 0.3:
            sentiment = "strongly_bullish"
        elif imbalance > 0.1:
            sentiment = "bullish"
        elif imbalance < -0.3:
            sentiment = "strongly_bearish"
        elif imbalance < -0.1:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        return {
            "success": True,
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread": spread,
            "spread_pct": spread_pct,
            "total_bid_volume": total_bid,
            "total_ask_volume": total_ask,
            "imbalance": imbalance,
            "sentiment": sentiment,
            "bid_walls": bid_walls[:5],
            "ask_walls": ask_walls[:5],
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Analyze orderbook on Bybit")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    result = run(symbol=args.symbol, limit=args.limit)
    print(json.dumps(result, indent=2))
