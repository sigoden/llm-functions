import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

from pybit.unified_trading import HTTP, WebSocket

# Configuration
USE_TOR = os.getenv("USE_TOR")
PROXY = "socks5h://127.0.0.1:9050" if USE_TOR else None

# Initialize HTTP session for order placement
session = HTTP(
    testnet=False,
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
    proxy=PROXY,
)


@dataclass
class OrderBook:
    """L2 Order Book State"""

    best_bid: float
    best_ask: float
    bid_vol: float
    ask_vol: float
    timestamp: int

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_pct(self) -> float:
        return (self.spread / self.best_bid) * 100

    @property
    def imbalance(self) -> float:
        return self.bid_vol / self.ask_vol if self.ask_vol > 0 else 0


class SpreadBot:
    def __init__(self, symbol: str, qty: float, min_spread_pct: float = 0.01):
        self.symbol = symbol
        self.qty = qty
        self.min_spread_pct = min_spread_pct
        self.ob: Optional[OrderBook] = None
        self.tick_size, self.qty_step = self._get_precision()
        self.ws = None
        self.running = False

    def _get_precision(self):
        """Fetch tick size and quantity step."""
        instruments = session.get_instruments_info(
            category="linear", symbol=self.symbol
        )
        price_filter = instruments["result"]["list"][0]["priceFilter"]
        lot_size = instruments["result"]["list"][0]["lotSizeFilter"]
        return float(price_filter["tickSize"]), float(lot_size["qtyStep"])

    def _parse_orderbook(self, data: dict) -> OrderBook:
        """Parse WebSocket orderbook data into OrderBook object."""
        bids = data.get("b", [])
        asks = data.get("a", [])

        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        bid_vol = sum(float(x[1]) for x in bids[:25])
        ask_vol = sum(float(x[1]) for x in asks[:25])

        return OrderBook(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            timestamp=data.get("ts", 0),
        )

    async def on_message(self, message):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)

            # Check for orderbook update
            if data.get("topic") == f"orderbook.100ms.{self.symbol}":
                self.ob = self._parse_orderbook(data["data"])
                await self.check_and_trade()

        except Exception as e:
            print(f"Error: {e}")

    async def check_and_trade(self):
        """Check spread and place orders if profitable."""
        if not self.ob:
            return

        spread_pct = self.ob.spread_pct

        # Check if spread is wide enough
        if spread_pct < self.min_spread_pct:
            print(
                json.dumps(
                    {
                        "status": "watching",
                        "spread_pct": round(spread_pct, 4),
                        "best_bid": self.ob.best_bid,
                        "best_ask": self.ob.best_ask,
                    }
                )
            )
            return

        # Calculate target prices (front-run the spread)
        buy_price = round(self.ob.best_bid + self.tick_size, 8)
        sell_price = round(self.ob.best_ask - self.tick_size, 8)

        # Calculate expected profit
        expected_profit = ((sell_price - buy_price) / buy_price) * 100

        print(
            json.dumps(
                {
                    "status": "opportunity",
                    "spread_pct": round(spread_pct, 4),
                    "buy_at": buy_price,
                    "sell_at": sell_price,
                    "expected_profit_pct": round(expected_profit, 4),
                    "imbalance": round(self.ob.imbalance, 2),
                }
            )
        )

        # Place orders (PostOnly for maker fees)
        try:
            # Buy order
            session.place_order(
                category="linear",
                symbol=self.symbol,
                side="Buy",
                orderType="Limit",
                qty=str(self.qty),
                price=str(buy_price),
                timeInForce="PostOnly",
            )

            # Sell order
            session.place_order(
                category="linear",
                symbol=self.symbol,
                side="Sell",
                orderType="Limit",
                qty=str(self.qty),
                price=str(sell_price),
                timeInForce="PostOnly",
            )

            print(
                json.dumps(
                    {"status": "orders_placed", "buy": buy_price, "sell": sell_price}
                )
            )

        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}))

    async def start(self):
        """Start WebSocket connection and trading loop."""
        self.running = True

        # Initialize WebSocket
        self.ws = WebSocket(testnet=False, channel_type="linear", proxy=PROXY)

        # Subscribe to orderbook stream
        self.ws.subscribe(
            topic=f"orderbook.100ms.{self.symbol}", callback=self.on_message
        )

        print(f"🚀 Spread bot started for {self.symbol}")

        # Keep running
        while self.running:
            await asyncio.sleep(0.1)

    def stop(self):
        """Stop the bot."""
        self.running = False
        if self.ws:
            self.ws.unsubscribe(f"orderbook.100ms.{self.symbol}")


# Usage
if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    qty = float(sys.argv[2]) if len(sys.argv) > 2 else 0.001
    min_spread = float(sys.argv[3]) if len(sys.argv) > 3 else 0.01

    bot = SpreadBot(symbol, qty, min_spread)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        bot.stop()
        print("🛑 Bot stopped")
