from typing import Optional

from .base import logger


class SmartOrderMixin:
    def place_smart_order(
        self,
        symbol: str = "BTCUSDT",
        side: str = "Buy",
        risk_pct: float = 1.0,
        sl_dist: Optional[float] = None,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        category: str = "linear",
    ):
        """Place a smart order with automatic position sizing and risk management."""

        # 1. Get current price
        ticker_res = self.get_ticker(symbol, category=category)
        ticker = ticker_res.get("list", [{}])[0]
        current_price = float(ticker.get("lastPrice", 0))
        if not current_price:
            return {"status": "error", "msg": f"Could not fetch price for {symbol}"}

        # 2. Get available balance
        bal_res = self.get_wallet_balance()
        bal_list = bal_res.get("list", bal_res.get("result", {}).get("list", [{}]))[
            0
        ].get("coin", [])
        # Find settlement coin for linear (usually USDT)
        settle_coin = "USDT"
        coin_entry = next((c for c in bal_list if c.get("coin") == settle_coin), {})
        balance = float(
            coin_entry.get("availableToWithdraw")
            or coin_entry.get("availableToTrade")
            or coin_entry.get("walletBalance")
            or 0
        )
        if balance <= 0:
            return {"status": "error", "msg": "Insufficient balance"}

        # 3. Calculate Risk Amount
        risk_amount = balance * (risk_pct / 100)

        # 4. Determine Stop Loss
        if sl_price:
            stop_loss = sl_price
        elif sl_dist:
            stop_loss = (
                current_price - sl_dist
                if side.lower() in ["buy", "long"]
                else current_price + sl_dist
            )
        else:
            # Use ATR if no SL provided
            atr = self.calculate_atr(symbol, "15").get("atr", current_price * 0.01)
            stop_loss = (
                current_price - (atr * 2)
                if side.lower() in ["buy", "long"]
                else current_price + (atr * 2)
            )

        # 5. Calculate Position Size
        price_diff = abs(current_price - stop_loss)
        if price_diff > 0:
            qty = risk_amount / price_diff
        else:
            qty = risk_amount / current_price

        # 6. Determine Take Profit (2:1 reward/risk default)
        if tp_price:
            take_profit = tp_price
        else:
            tp_distance = price_diff * 2
            take_profit = (
                current_price + tp_distance
                if side.lower() in ["buy", "long"]
                else current_price - tp_distance
            )

        # 7. Execute Order
        logger.info(
            f"SmartOrder: {side} {qty} {symbol} SL:{stop_loss} TP:{take_profit}"
        )
        return self.place_order(
            symbol=symbol,
            side=side.capitalize(),
            qty=qty,
            order_type="Market",
            stop_loss=stop_loss,
            take_profit=take_profit,
            category=category,
        )
