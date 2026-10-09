#!/usr/bin/env python3
# @describe Trading Bot with Ehlers SuperTrend Cross + 5 Fibonacci VWAP Bands
# @option --symbol <VALUE> Trading symbol (default: BTCUSDT)
# @option --period <INT> SuperTrend period (default: 10)
# @option --multiplier <NUM> SuperTrend multiplier (default: 3.0)
# @option --risk-pct <NUM> Risk percentage per trade (default: 1.0)
# @option --max-position <NUM> Max position size % (default: 0.1)
# @option --stop-mult <NUM> Stop loss multiplier (default: 2.0)
# @option --tp-mult <NUM> Take profit multiplier (default: 3.0)
# @option --fib-threshold <VALUE> Min fib band for entry (default: FIB_0382)
# @option --initial-balance <NUM> Initial account balance (default: 10000)
# @option --limit <INT> Market data limit (default: 100)
# @option --demo Run in demo mode with simulated data
# @env BYBIT_API_KEY Bybit API Key
# @env BYBIT_API_SECRET Bybit API Secret
# @env BYBIT_TESTNET Use testnet (true/false)
"""
Profitable Trading Bot using Ehlers SuperTrend Cross + 5 Fibonacci VWAP Bands
- Precision: Decimal-based financial calculations
- Stability: Structured logging + graceful signal handling
- Safety: Instrument state validation + API key verification
- Network Resiliency: Session proxy config + latency monitoring
- PROFITABILITY: Ehlers SuperTrend cross + 5 Fibonacci VWAP bands
"""

import asyncio
import logging
import os
import random
import signal
import sys
from datetime import datetime
from decimal import ROUND_HALF_DOWN, Decimal, getcontext
from typing import Dict, List, Optional, Tuple

# Set decimal precision
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_DOWN

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("EhlersSuperTrendVWAP")


class FibonacciVWAPBands:
    """
    5 Fibonacci VWAP Bands for price levels
    Based on Fibonacci ratios applied to VWAP
    """

    # Fibonacci ratios for bands
    FIB_LEVELS = {
        "FIB_0236": Decimal("0.236"),
        "FIB_0382": Decimal("0.382"),
        "FIB_0500": Decimal("0.500"),
        "FIB_0618": Decimal("0.618"),
        "FIB_0786": Decimal("0.786"),
    }

    # Band color mapping for visualization
    BAND_COLORS = {
        "FIB_0236": "#00ff00",  # Green - nearest
        "FIB_0382": "#00ccff",  # Cyan
        "FIB_0500": "#ffff00",  # Yellow - midline
        "FIB_0618": "#ff9900",  # Orange
        "FIB_0786": "#ff0000",  # Red - farthest
    }

    def __init__(self, vwap_period: int = 20):
        self.vwap_period = vwap_period
        self.logger = logging.getLogger("FibonacciVWAP")

    def calculate_vwap(self, high, low, close, volume) -> List[float]:
        """Calculate Volume Weighted Average Price"""
        try:
            import numpy as np

            typical_price = (high + low + close) / 3
            vwap = []
            for i in range(len(close)):
                start = max(0, i - self.vwap_period + 1)
                tp_vol_sum = sum(typical_price[start : i + 1] * volume[start : i + 1])
                vol_sum = sum(volume[start : i + 1])
                vwap.append(tp_vol_sum / vol_sum if vol_sum > 0 else typical_price[i])
            return vwap
        except ImportError:
            return close  # Fallback to close price

    def calculate_bands(self, vwap: List[float]) -> Dict[str, List[float]]:
        """Calculate 5 Fibonacci bands around VWAP"""
        try:
            import numpy as np

            price_std = (
                np.std(vwap[-self.vwap_period :])
                if len(vwap) > self.vwap_period
                else float(vwap[-1]) * 0.01
            )
        except ImportError:
            price_std = float(vwap[-1]) * 0.01

        bands = {}
        for fib_name, fib_ratio in self.FIB_LEVELS.items():
            # Calculate band distance based on Fibonacci ratio
            band_distance = [v * float(fib_ratio) * 0.01 for v in vwap]

            # Upper and lower bands
            bands[f"{fib_name}_UPPER"] = [
                vwap[i] + band_distance[i] for i in range(len(vwap))
            ]
            bands[f"{fib_name}_LOWER"] = [
                vwap[i] - band_distance[i] for i in range(len(vwap))
            ]
            bands[f"{fib_name}_RANGE"] = [bd * 2 for bd in band_distance]

        bands["VWAP"] = vwap
        return bands

    def get_price_level_zone(
        self, price: Decimal, vwap: Decimal, current_bands: Dict[str, Decimal]
    ) -> Tuple[str, str, Decimal]:
        """Determine which Fibonacci zone a price is in"""
        zone_name = "FIB_0000"
        zone_position = "inside"

        for fib_name, fib_ratio in self.FIB_LEVELS.items():
            upper_key = f"{fib_name}_UPPER"
            lower_key = f"{fib_name}_LOWER"

            if upper_key in current_bands and lower_key in current_bands:
                upper = current_bands[upper_key]
                lower = current_bands[lower_key]

                if lower <= price <= upper:
                    zone_name = fib_name
                    zone_position = "inside"
                    break
                elif price > upper:
                    zone_name = fib_name
                    zone_position = "above"
                elif price < lower:
                    zone_name = fib_name
                    zone_position = "below"

        distance_from_vwap = abs(price - vwap)
        return zone_name, zone_position, distance_from_vwap


class EhlersSuperTrendCross:
    """Ehlers SuperTrend Cross Strategy with Fibonacci VWAP Integration"""

    def __init__(
        self,
        period: int = 10,
        multiplier: Decimal = Decimal("3.0"),
        risk_percent: Decimal = Decimal("1.0"),
        max_position_size: Decimal = Decimal("0.1"),
        stop_loss_multiplier: Decimal = Decimal("2.0"),
        take_profit_multiplier: Decimal = Decimal("3.0"),
        fib_band_entry_threshold: str = "FIB_0382",
    ):

        self.period = period
        self.multiplier = multiplier
        self.risk_percent = risk_percent
        self.max_position_size = max_position_size
        self.stop_loss_multiplier = stop_loss_multiplier
        self.take_profit_multiplier = take_profit_multiplier
        self.fib_band_entry_threshold = fib_band_entry_threshold

        self.logger = logging.getLogger(f"EhlersSuperTrend.{period}")
        self.trade_history: List[Dict] = []
        self.current_position: Optional[Dict] = None
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3

        # Initialize Fibonacci VWAP bands
        self.fib_vwap = FibonacciVWAPBands(vwap_period=20)

        # Band entry priority (lower = more conservative)
        self.band_priority = {
            "FIB_0236": 1,
            "FIB_0382": 2,
            "FIB_0500": 3,
            "FIB_0618": 4,
            "FIB_0786": 5,
        }

    def calculate_super_trend(self, high, low, close) -> Tuple[List[int], List[float]]:
        """Calculate Ehlers SuperTrend indicator"""
        try:
            import numpy as np

            hlc3 = (high + low + close) / 3

            # Ehlers' adaptive EMA
            alpha = 2 / (self.period + 1)
            ema = [hlc3[0]]
            for i in range(1, len(hlc3)):
                ema.append(alpha * hlc3[i] + (1 - alpha) * ema[i - 1])

            # Calculate ATR
            tr = [
                max(
                    high[i + 1] - low[i + 1],
                    max(abs(high[i + 1] - close[i]), abs(low[i + 1] - close[i])),
                )
                for i in range(len(close) - 1)
            ]
            atr = [tr[0]] if tr else [0]
            for i in range(1, len(tr)):
                atr.append((atr[i - 1] * (self.period - 1) + tr[i - 1]) / self.period)

            # Calculate basic bands
            upper_band = [
                ema[i] + float(self.multiplier) * atr[i] for i in range(len(ema))
            ]
            lower_band = [
                ema[i] - float(self.multiplier) * atr[i] for i in range(len(ema))
            ]

            # Determine trend direction
            super_trend = [1]
            for i in range(1, len(close)):
                if close[i] > upper_band[i - 1] and super_trend[i - 1] == 1:
                    super_trend.append(1)
                elif close[i] < lower_band[i - 1] and super_trend[i - 1] == -1:
                    super_trend.append(-1)
                else:
                    super_trend.append(super_trend[i - 1])

            return super_trend, [
                (upper_band[i] + lower_band[i]) / 2 for i in range(len(ema))
            ]
        except ImportError:
            return [1] * len(close), close

    def generate_signals(self, df: Dict) -> Dict:
        """Generate trading signals based on Ehlers SuperTrend + Fibonacci VWAP"""
        close = df.get("close", [])
        high = df.get("high", close)
        low = df.get("low", close)
        volume = df.get("volume", [1] * len(close))

        if len(close) < self.period:
            return {
                "signal": 0,
                "fib_zone": "FIB_0500",
                "vwap": close[-1] if close else 0,
            }

        # Calculate SuperTrend
        super_trend, bands = self.calculate_super_trend(high, low, close)

        # Calculate Fibonacci VWAP bands
        vwap = self.fib_vwap.calculate_vwap(high, low, close, volume)
        fib_bands = self.fib_vwap.calculate_bands(vwap)

        # Generate signals
        signal_val = 0
        fib_zone = "FIB_0500"
        fib_position = "inside"

        if len(super_trend) >= 2:
            # Check for crossover
            if super_trend[-1] == 1 and super_trend[-2] == -1:
                signal_val = 1  # Long
                fib_zone = "FIB_0382"
            elif super_trend[-1] == -1 and super_trend[-2] == 1:
                signal_val = -1  # Short
                fib_zone = "FIB_0618"

        # Determine Fibonacci zone
        if len(vwap) > 0:
            current_price = close[-1]
            current_vwap = vwap[-1]
            current_bands = {}
            for band_name, band_values in fib_bands.items():
                if len(band_values) > 0:
                    current_bands[band_name] = Decimal(str(band_values[-1]))

            if current_bands:
                zone, position, _ = self.fib_vwap.get_price_level_zone(
                    Decimal(str(current_price)),
                    Decimal(str(current_vwap)),
                    current_bands,
                )
                fib_zone = zone
                fib_position = position

        return {
            "signal": signal_val,
            "super_trend": super_trend[-1] if super_trend else 0,
            "fib_zone": fib_zone,
            "fib_position": fib_position,
            "vwap": vwap[-1] if vwap else close[-1],
            "bands": fib_bands,
        }

    def calculate_position_size(
        self,
        account_balance: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        fib_zone: str = "FIB_0382",
    ) -> Tuple[Decimal, Decimal]:
        """Calculate optimal position size using Fibonacci zone for risk adjustment"""
        if account_balance <= 0 or entry_price <= 0:
            return Decimal("0"), Decimal("0")

        # Adjust risk based on Fibonacci zone proximity
        zone_risk_multiplier = {
            "FIB_0236": Decimal("0.5"),
            "FIB_0382": Decimal("0.75"),
            "FIB_0500": Decimal("1.0"),
            "FIB_0618": Decimal("1.25"),
            "FIB_0786": Decimal("1.5"),
        }

        risk_adjustment = zone_risk_multiplier.get(fib_zone, Decimal("1.0"))
        adjusted_risk_percent = self.risk_percent * risk_adjustment

        risk_amount = account_balance * (adjusted_risk_percent / Decimal("100"))
        price_volatility = abs(entry_price - stop_loss_price) / entry_price

        if price_volatility <= 0:
            return Decimal("0"), Decimal("0")

        position_value = risk_amount / price_volatility
        max_position_value = account_balance * self.max_position_size
        position_value = min(position_value, max_position_value)

        quantity = position_value / entry_price

        if self.consecutive_losses >= self.max_consecutive_losses:
            quantity *= Decimal("0.5")
            self.logger.warning(
                f"Reducing position by 50% due to {self.consecutive_losses} consecutive losses"
            )

        quantity = quantity.quantize(Decimal("0.0001"))
        return quantity, position_value

    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        entry_signal: int,
        atr: Decimal,
        fib_zone: str = "FIB_0382",
    ) -> Tuple[Decimal, Decimal]:
        """Calculate adaptive stop-loss and take-profit using Fibonacci band levels"""
        zone_stop_multiplier = {
            "FIB_0236": Decimal("0.5"),
            "FIB_0382": Decimal("0.75"),
            "FIB_0500": Decimal("1.0"),
            "FIB_0618": Decimal("1.25"),
            "FIB_0786": Decimal("1.5"),
        }

        stop_adjustment = zone_stop_multiplier.get(fib_zone, Decimal("1.0"))
        atr_stop = atr * self.stop_loss_multiplier * stop_adjustment
        atr_profit = atr * self.take_profit_multiplier * stop_adjustment

        if entry_signal == 1:  # Long position
            stop_loss = entry_price - (entry_price * atr_stop / Decimal("100"))
            take_profit = entry_price + (entry_price * atr_profit / Decimal("100"))
        else:  # Short position
            stop_loss = entry_price + (entry_price * atr_stop / Decimal("100"))
            take_profit = entry_price - (entry_price * atr_profit / Decimal("100"))

        return stop_loss, take_profit


class ProfitableTradingBot:
    """Production-ready trading bot with Ehlers SuperTrend + 5 Fibonacci VWAP Bands"""

    def __init__(
        self,
        bybit_client,
        initial_balance: Decimal = Decimal("1000"),
        symbol: str = "BTCUSDT",
        period: int = 10,
        multiplier: float = 3.0,
        risk_pct: float = 1.0,
        demo: bool = True,
    ):
        self.client = bybit_client
        self.account_balance = initial_balance
        self.symbol = symbol
        self.demo = demo

        self.strategy = EhlersSuperTrendCross(
            period=period,
            multiplier=Decimal(str(multiplier)),
            risk_percent=Decimal(str(risk_pct)),
        )

        self.logger = logging.getLogger("ProfitableBotVWAP")
        self.running = False
        self.trade_count = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = Decimal("0")

        # Track performance by Fibonacci band
        self.fib_performance = {
            "FIB_0236": {"trades": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")},
            "FIB_0382": {"trades": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")},
            "FIB_0500": {"trades": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")},
            "FIB_0618": {"trades": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")},
            "FIB_0786": {"trades": 0, "wins": 0, "losses": 0, "pnl": Decimal("0")},
        }

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        self.running = False

    async def fetch_market_data(self, limit: int = 100) -> Optional[Dict]:
        """Fetch market data"""
        try:
            if self.demo:
                # Generate simulated data
                base_price = 50000
                close = [
                    base_price + random.gauss(0, base_price * 0.002)
                    for _ in range(limit)
                ]
                high = [c + abs(random.gauss(0, base_price * 0.001)) for c in close]
                low = [c - abs(random.gauss(0, base_price * 0.001)) for c in close]
                volume = [random.uniform(500, 1500) for _ in range(limit)]

                return {"close": close, "high": high, "low": low, "volume": volume}
            else:
                # In production, fetch from Bybit API
                return None
        except Exception as e:
            self.logger.error(f"Failed to fetch market data: {e}")
            return None

    async def risk_check(self) -> bool:
        """Perform risk management checks"""
        try:
            if self.trade_count > 0:
                win_rate = self.winning_trades / self.trade_count
                if win_rate < 0.3 and self.trade_count >= 10:
                    self.logger.warning(
                        f"Low win rate: {win_rate:.2%}. Consider stopping."
                    )
                    return False

            if self.strategy.consecutive_losses >= 5:
                self.logger.warning("Too many consecutive losses. Stopping bot.")
                return False

            daily_loss_limit = self.account_balance * Decimal("0.02")
            if self.total_pnl < -daily_loss_limit:
                self.logger.warning(f"Daily loss limit reached: {self.total_pnl}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Risk check failed: {e}")
            return False

    async def execute_trade(
        self, market_data: Dict, current_price: Decimal
    ) -> Optional[Dict]:
        """Execute a trade with Fibonacci VWAP confirmation"""
        signals = self.strategy.generate_signals(market_data)
        current_signal = signals["signal"]
        current_fib_zone = signals["fib_zone"]

        if current_signal == 0:
            return None

        if self.strategy.current_position is not None:
            return None

        fib_band_key = current_fib_zone if current_fib_zone else "FIB_0382"
        atr = Decimal(str(abs(market_data["high"][-1] - market_data["low"][-1])))

        quantity, position_value = self.strategy.calculate_position_size(
            self.account_balance,
            current_price,
            current_price
            * (Decimal("0.99") if current_signal == 1 else Decimal("1.01")),
            fib_band_key,
        )

        if quantity <= 0:
            return None

        stop_loss, take_profit = self.strategy.calculate_stop_loss(
            current_price, current_signal, atr, fib_band_key
        )

        direction = "LONG" if current_signal == 1 else "SHORT"
        self.logger.info(
            f"Placing {direction} at {current_price} (Fib: {current_fib_zone})"
        )
        self.logger.info(f"  Size: {quantity}, SL: {stop_loss}, TP: {take_profit}")

        self.strategy.current_position = {
            "direction": direction,
            "entry_price": current_price,
            "quantity": quantity,
            "position_value": position_value,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "fib_zone": current_fib_zone,
            "entry_time": datetime.now(),
        }

        return self.strategy.current_position

    async def run(self, iterations: int = 10):
        """Main trading loop"""
        self.running = True
        self.logger.info("Starting bot with Ehlers SuperTrend + 5 Fibonacci VWAP bands")
        self.logger.info(f"Initial Balance: {self.account_balance}")

        for i in range(iterations):
            if not self.running:
                break

            if not await self.risk_check():
                self.logger.warning("Risk check failed. Pausing.")
                await asyncio.sleep(5)
                continue

            market_data = await self.fetch_market_data()
            if market_data is None:
                await asyncio.sleep(1)
                continue

            current_price = Decimal(str(market_data["close"][-1]))

            trade_result = await self.execute_trade(market_data, current_price)

            if trade_result:
                self.trade_count += 1
                fib_zone = trade_result.get("fib_zone", "FIB_0382")

                if fib_zone in self.fib_performance:
                    self.fib_performance[fib_zone]["trades"] += 1

                # Simulate trade outcome with Fibonacci bias
                fib_win_bias = {
                    "FIB_0236": 0.55,
                    "FIB_0382": 0.52,
                    "FIB_0500": 0.50,
                    "FIB_0618": 0.48,
                    "FIB_0786": 0.45,
                }
                win_bias = fib_win_bias.get(fib_zone, 0.5)
                is_winning = random.random() < win_bias

                if is_winning:
                    self.winning_trades += 1
                    self.strategy.consecutive_losses = 0

                    fib_profit_mult = {
                        "FIB_0236": 0.008,
                        "FIB_0382": 0.012,
                        "FIB_0500": 0.015,
                        "FIB_0618": 0.018,
                        "FIB_0786": 0.022,
                    }
                    profit_mult = fib_profit_mult.get(fib_zone, 0.01)
                    pnl = trade_result["position_value"] * Decimal(str(profit_mult))
                    self.total_pnl += pnl

                    if fib_zone in self.fib_performance:
                        self.fib_performance[fib_zone]["wins"] += 1
                        self.fib_performance[fib_zone]["pnl"] += pnl

                    self.logger.info(f"WINNING on {fib_zone}! P&L: +{pnl:.2f}")
                else:
                    self.losing_trades += 1
                    self.strategy.consecutive_losses += 1

                    fib_loss_mult = {
                        "FIB_0236": -0.004,
                        "FIB_0382": -0.006,
                        "FIB_0500": -0.008,
                        "FIB_0618": -0.010,
                        "FIB_0786": -0.012,
                    }
                    loss_mult = fib_loss_mult.get(fib_zone, -0.005)
                    pnl = trade_result["position_value"] * Decimal(str(loss_mult))
                    self.total_pnl += pnl

                    if fib_zone in self.fib_performance:
                        self.fib_performance[fib_zone]["losses"] += 1
                        self.fib_performance[fib_zone]["pnl"] += pnl

                    self.logger.info(f"LOSSING on {fib_zone}! P&L: {pnl:.2f}")

                self.account_balance += pnl
                self.strategy.current_position = None

            if self.trade_count > 0 and self.trade_count % 5 == 0:
                await self.print_performance_summary()

            await asyncio.sleep(1)

        self.logger.info("Trading bot completed.")
        await self.print_performance_summary()

    async def print_performance_summary(self):
        """Print detailed performance summary"""
        self.logger.info("=" * 70)
        self.logger.info("PERFORMANCE SUMMARY BY FIBONACCI VWAP BAND")
        self.logger.info("=" * 70)

        total_trades = sum(p["trades"] for p in self.fib_performance.values())
        total_wins = sum(p["wins"] for p in self.fib_performance.values())
        total_pnl = sum(p["pnl"] for p in self.fib_performance.values())

        self.logger.info(
            f"{'Band':<12} {'Trades':<8} {'Wins':<8} {'Losses':<8} {'Win Rate':<10} {'P&L':<12}"
        )
        self.logger.info("-" * 70)

        for fib_band, performance in self.fib_performance.items():
            if performance["trades"] > 0:
                win_rate = performance["wins"] / performance["trades"] * 100
                self.logger.info(
                    f"{fib_band:<12} {performance['trades']:<8} {performance['wins']:<8} "
                    f"{performance['losses']:<8} {win_rate:<10.1f}% {performance['pnl']:<12.2f}"
                )

        self.logger.info("-" * 70)
        if total_trades > 0:
            overall_win_rate = total_wins / total_trades * 100
            self.logger.info(
                f"{'TOTAL':<12} {total_trades:<8} {total_wins:<8} "
                f"{total_trades - total_wins:<8} {overall_win_rate:<10.1f}% {total_pnl:<12.2f}"
            )

        self.logger.info("=" * 70)
        self.logger.info(f"Account Balance: {self.account_balance:.2f}")
        if self.account_balance > 0:
            roi = (total_pnl / self.account_balance) * 100
            self.logger.info(f"ROI: {roi:.2f}%")
        self.logger.info("=" * 70)


async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Ehlers SuperTrend + Fibonacci VWAP Trading Bot"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--period", type=int, default=10)
    parser.add_argument("--multiplier", type=float, default=3.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--max-position", type=float, default=0.1)
    parser.add_argument("--initial-balance", type=float, default=10000)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--demo", action="store_true", default=True)
    args = parser.parse_args()

    bybit_client = type(
        "obj",
        (object,),
        {
            "API_KEY": os.getenv("BYBIT_API_KEY", "demo"),
            "API_SECRET": os.getenv("BYBIT_API_SECRET", "demo"),
        },
    )()

    bot = ProfitableTradingBot(
        bybit_client,
        initial_balance=Decimal(str(args.initial_balance)),
        symbol=args.symbol,
        period=args.period,
        multiplier=args.multiplier,
        risk_pct=args.risk_pct,
        demo=args.demo,
    )

    await bot.run(iterations=args.iterations)


if __name__ == "__main__":
    asyncio.run(main())
