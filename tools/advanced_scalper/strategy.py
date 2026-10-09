from collections import deque
from statistics import stdev
from typing import Any, Dict, Optional, Tuple


class MultiTimeframeAnalyzer:
    """Analyze multiple timeframes for trend confirmation."""

    def __init__(self, max_frames: int = 3):
        self.timeframes = {
            "1m": deque(maxlen=60),
            "5m": deque(maxlen=12),
            "15m": deque(maxlen=4),
        }

    def update(self, close_price: float, timestamp_ms: int):
        current_minute = int((timestamp_ms / 1000) / 60)

        self.timeframes["1m"].append((current_minute, close_price))
        if current_minute % 5 == 0:
            self.timeframes["5m"].append((current_minute // 5, close_price))
        if current_minute % 15 == 0:
            self.timeframes["15m"].append((current_minute // 15, close_price))

    def get_trend_signal(self) -> Tuple[float, str]:
        scores = []
        for tf_name, data in self.timeframes.items():
            if len(data) < 2:
                continue
            prices = [p[1] for p in data]
            if len(prices) >= 2:
                sma = sum(prices) / len(prices)
                current_price = prices[-1]
                scores.append((current_price - sma) / sma)

        if not scores:
            return 0.0, "neutral"

        avg_score = sum(scores) / len(scores)
        if avg_score > 0.005:
            return min(1.0, avg_score * 100), "bullish"
        elif avg_score < -0.005:
            return min(1.0, abs(avg_score) * 100), "bearish"
        return 0.0, "neutral"


class SignalEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.analyzers = {
            sym: MultiTimeframeAnalyzer() for sym in config.get("symbols", {})
        }

    def calculate_optimal_qty(
        self, closes: list, base_qty: float, target_profit: float, window: int = 20
    ) -> float:
        if len(closes) < window:
            return base_qty

        recent = closes[-window:]
        returns = [
            (recent[i] - recent[i - 1]) / recent[i - 1] for i in range(1, len(recent))
        ]
        vol = stdev(returns) if len(returns) > 1 else 0.001

        vol_multiplier = max(0.5, min(1.5, 0.001 / max(vol, 0.0001)))
        profit_multiplier = (
            1.5 if target_profit <= 0.05 else (1.2 if target_profit <= 0.10 else 1.0)
        )
        return round(base_qty * vol_multiplier * profit_multiplier, 8)

    def analyze_orderbook_walls(self, raw_data: Dict[str, Any]) -> Tuple[float, float]:
        """Calculates deep volume profile from full orderbook to detect walls."""
        # This requires the MarketDataStreamer to store full 50 levels of orderbook.
        # We will stub this for now, returning dummy multipliers.
        return 1.0, 1.0

    def evaluate(
        self, symbol: str, state: Dict[str, Any], timestamp_ms: int
    ) -> Optional[Dict[str, Any]]:
        best_bid = state["best_bid"]
        best_ask = state["best_ask"]
        bid_vol = state["bid_vol"]
        ask_vol = state["ask_vol"]
        closes = state["closes"]

        if best_bid <= 0 or best_ask <= 0 or len(closes) < 2:
            return None

        self.analyzers[symbol].update(closes[-1], timestamp_ms)
        trend_score, trend_dir = self.analyzers[symbol].get_trend_signal()

        sym_cfg = self.config["symbols"][symbol]
        base_qty = sym_cfg["qty"]
        target_profit = self.config.get("target_profit_pct", 0.05) / 100.0

        qty = self.calculate_optimal_qty(closes, base_qty, target_profit)

        denominator = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / denominator if denominator > 0 else 0.0
        momentum = (closes[-1] - closes[-2]) / closes[-2]

        vol_bid_mult, vol_ask_mult = self.analyze_orderbook_walls(state)

        # Simple signal generation
        side = None
        if momentum > 0.001 and imbalance > 0.5 and trend_dir == "bullish":
            side = "Buy"
        elif momentum < -0.001 and imbalance < -0.5 and trend_dir == "bearish":
            side = "Sell"

        if not side:
            return None

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": best_bid if side == "Buy" else best_ask,
            "imbalance": imbalance,
            "momentum": momentum,
            "trend": trend_dir,
        }
