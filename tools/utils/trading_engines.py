from collections import deque

import numpy as np


class StatisticalArbitrageEngine:
    def __init__(self, window=20, z_threshold=2.0, lookback=100):
        self.price_history = deque(maxlen=lookback)
        self.window = window
        self.z_threshold = z_threshold

    def analyze_market_making_opportunity(
        self,
        bid_prices,
        bid_volumes,
        ask_prices,
        ask_volumes,
        current_price,
        inventory_ratio=0.0,
    ):
        total_bid_vol = sum(bid_volumes[:5])
        total_ask_vol = sum(ask_volumes[:5])
        if total_bid_vol + total_ask_vol == 0:
            return {"error": "No liquidity"}

        imbalance = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)
        bid_wap = (
            sum(p * v for p, v in zip(bid_prices[:5], bid_volumes[:5])) / total_bid_vol
            if total_bid_vol > 0
            else current_price
        )
        ask_wap = (
            sum(p * v for p, v in zip(ask_prices[:5], ask_volumes[:5])) / total_ask_vol
            if total_ask_vol > 0
            else current_price
        )
        spread = ask_wap - bid_wap
        mid_price = (ask_wap + bid_wap) / 2
        vol_factor = self._calculate_volatility_factor(current_price)
        inventory_skew = inventory_ratio * 0.5
        optimal_bid = mid_price - (spread / 2) * (1 + vol_factor - inventory_skew)
        optimal_ask = mid_price + (spread / 2) * (1 + vol_factor + inventory_skew)
        expected_profit = (optimal_ask - optimal_bid) * 100 / mid_price

        return {
            "optimal_bid": round(optimal_bid, 6),
            "optimal_ask": round(optimal_ask, 6),
            "expected_profit_bps": round(expected_profit, 2),
            "orderbook_imbalance": round(imbalance, 4),
        }

    def analyze_statistical_arbitrage(self, current_price):
        self.price_history.append(current_price)
        if len(self.price_history) < self.window + 5:
            return {"status": "accumulating_data"}
        prices = list(self.price_history)
        sma = np.mean(prices[-self.window :])
        std = np.std(prices[-self.window :])
        if std == 0:
            return {"status": "no_volatility"}
        z_score = (current_price - sma) / std
        signal = (
            "LONG"
            if z_score < -self.z_threshold
            else ("SHORT" if z_score > self.z_threshold else "NEUTRAL")
        )
        return {
            "action": signal,
            "z_score": round(z_score, 4),
            "mean_price": round(sma, 6),
        }

    def _calculate_volatility_factor(self, current_price):
        if len(self.price_history) < 10:
            return 0.1
        recent = list(self.price_history)[-10:]
        returns = [
            (recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))
        ]
        volatility = np.std(returns) * np.sqrt(10)
        return min(volatility * 100, 2.0)


class ExitStrategyEngine:
    def calculate_multi_exit_levels(
        self,
        entry_price,
        position_size,
        side,
        current_price,
        atr,
        volume_profile,
        bids,
        asks,
    ):
        # Increased sensitivity: Tighter ATR multiplier (1.0 instead of 2.0) for rapid scalping
        # We need to compute ATR exit here based on the passed atr

        atr_distance = atr * 1.0  # Aggressive multiplier
        if side.lower() == "long":
            exit_price = current_price + atr_distance
        else:
            exit_price = current_price - atr_distance

        return {
            "best_exit": round(exit_price, 6),
            "urgency": 85,  # Increased urgency
            "all_exit_levels": [("atr_scalp", exit_price, 0.5)],
        }
