#!/usr/bin/env python3
# @describe All-in-One ETHUSDT Scalper: Analysis, Risk Management, Order Ops
# @option --auto-trade <BOOL> Execute trades automatically
# @option --qty <QTY> Order quantity
# @option --min-profit <USD> Min profit target

import argparse
import logging
import time

import bybit_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


class Scalper:
    def __init__(self, qty, min_profit):
        self.qty = qty
        self.min_profit = min_profit
        self.symbol = "ETHUSDT"
        self.category = "linear"

    def get_market_data(self):
        # Orderbook
        ob = bybit_core.api_request(
            "GET",
            "/v5/market/orderbook",
            {"category": self.category, "symbol": self.symbol, "limit": 50},
        )
        # Position
        pos = bybit_core.api_request(
            "GET",
            "/v5/position/list",
            {"category": self.category, "symbol": self.symbol},
            signed=True,
        )
        # Orders
        orders = bybit_core.api_request(
            "GET",
            "/v5/order/realtime",
            {"category": self.category, "symbol": self.symbol},
            signed=True,
        )
        return ob, pos, orders

    def place_order(self, side, order_type="Market", price=None):
        params = {
            "category": self.category,
            "symbol": self.symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(self.qty),
        }
        if price:
            params["price"] = str(price)
        return bybit_core.api_request(
            "POST", "/v5/order/create", params=params, signed=True
        )

    def cancel_all(self):
        return bybit_core.api_request(
            "POST",
            "/v5/order/cancel-all",
            {"category": self.category, "symbol": self.symbol},
            signed=True,
        )

    def run(self, auto_trade):
        logger.info("Bot started. Monitoring...")
        while True:
            ob, pos, orders = self.get_market_data()

            # Position logic
            active_pos = next(
                (
                    p
                    for p in pos.get("result", {}).get("list", [])
                    if float(p["size"]) > 0
                ),
                None,
            )
            if active_pos:
                pnl = float(active_pos["unrealisedPnl"])
                if pnl >= self.min_profit:
                    logger.info(f"Profit target reached ({pnl}). Closing.")
                    self.place_order("Sell" if active_pos["side"] == "Buy" else "Buy")

            # Entry logic
            elif auto_trade:
                bids = ob.get("result", {}).get("b", [])
                asks = ob.get("result", {}).get("a", [])
                if bids and asks:
                    imbalance = sum(float(b[1]) for b in bids[:10]) - sum(
                        float(a[1]) for a in asks[:10]
                    )
                    if imbalance > 50:
                        self.place_order("Buy")
                    elif imbalance < -50:
                        self.place_order("Sell")

            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--auto-trade", type=lambda x: x.lower() == "true", default=False
    )
    parser.add_argument("--qty", default="0.01")
    parser.add_argument("--min-profit", type=float, default=1.0)
    args = parser.parse_args()
    bot = Scalper(args.qty, args.min_profit)
    bot.run(args.auto_trade)
