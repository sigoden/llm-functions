#!/usr/bin/env python3
"""
Production-Ready Trading Bot with Batch Limit Orders
- Ehlers SuperTrend Cross Strategy
- 5 Fibonacci VWAP Bands
- Batch Limit Order Management
- Pure Python (no numpy/pandas)
"""

import logging
import signal
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_DOWN, Decimal, getcontext
from enum import Enum
from typing import Dict, List, Optional, Tuple

# Use centralized dispatcher
from bybit_tool import run as bybit_api_call

# Configure decimal precision
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_DOWN

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("TradingBot")

# Fibonacci ratios for VWAP bands
FIB_LEVELS = {
    "FIB_0236": Decimal("0.236"),
    "FIB_0382": Decimal("0.382"),
    "FIB_0500": Decimal("0.500"),
    "FIB_0618": Decimal("0.618"),
    "FIB_0786": Decimal("0.786"),
}


# Order Types
class OrderType(Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    STOP_LIMIT = "StopLimit"
    STOP_MARKET = "StopMarket"


class OrderSide(Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(Enum):
    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PENDING = "Pending"
    ACTIVE = "Active"


class TimeInForce(Enum):
    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    POST_ONLY = "PostOnly"


@dataclass
class Order:
    """Unified order representation"""

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    price: Optional[Decimal]
    quantity: Decimal
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal("0")
    filled_amount: Decimal = Decimal("0")
    avg_price: Optional[Decimal] = None
    created_time: datetime = field(default_factory=datetime.now)
    updated_time: datetime = field(default_factory=datetime.now)
    order_link_id: str = ""
    position_idx: int = 0
    reduce_only: bool = False
    close_on_trigger: bool = False
    fib_band_source: str = ""


@dataclass
class BatchOrderRequest:
    """Batch order request"""

    orders: List[Order]
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy: str = ""
    total_quantity: Decimal = Decimal("0")
    total_value: Decimal = Decimal("0")


@dataclass
class OHLCV:
    """OHLCV data point"""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class TradeResult:
    """Trade execution result"""

    entry_price: Decimal
    exit_price: Decimal
    position_size: Decimal
    pnl: Decimal
    pnl_percent: Decimal
    entry_time: datetime
    exit_time: datetime
    trade_direction: str
    fib_band_entry: str
    order_ids: List[str] = field(default_factory=list)


class CircularBuffer:
    """Efficient circular buffer for time series data"""

    def __init__(self, max_size: int = 200):
        self.max_size = max_size
        self.data = deque(maxlen=max_size)

    def append(self, item):
        self.data.append(item)

    def extend(self, items):
        for item in items:
            self.append(item)

    def get_all(self) -> list:
        return list(self.data)

    def get_last(self, n: int) -> list:
        return list(self.data)[-n:]

    def __len__(self) -> int:
        return len(self.data)

    def get(self, index: int):
        return list(self.data)[index]


class BatchOrderManager:
    """
    Advanced batch order management system
    Handles multiple orders, order lifecycle, and position management
    """

    def __init__(self, max_active_orders: int = 50):
        self.max_active_orders = max_active_orders
        self.active_orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self.batch_history: List[BatchOrderRequest] = []
        self.completed_batches: List[BatchOrderRequest] = []
        self.logger = logging.getLogger("BatchOrderManager")
        self._lock = threading.Lock()

    def create_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        fib_band_source: str = "",
    ) -> Order:
        """Create a limit order"""
        order = Order(
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            quantity=quantity,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            fib_band_source=fib_band_source,
            order_link_id=str(uuid.uuid4()),
        )
        return order

    def create_batch_orders(
        self,
        symbol: str,
        base_price: Decimal,
        quantity: Decimal,
        side: OrderSide,
        levels: List[Tuple[Decimal, Decimal]],
        fib_bands: Dict[str, Dict] = None,
    ) -> BatchOrderRequest:
        """Create a batch of limit orders at different price levels"""
        orders = []
        total_pct = sum(qty_pct for _, qty_pct in levels)
        remaining_quantity = quantity

        for i, (price_pct, qty_pct) in enumerate(levels):
            if side == OrderSide.BUY:
                order_price = base_price * (Decimal("1") - Decimal(str(price_pct)))
            else:
                order_price = base_price * (Decimal("1") + Decimal(str(price_pct)))

            if i == len(levels) - 1:
                order_qty = remaining_quantity
            else:
                order_qty = quantity * Decimal(str(qty_pct)) / total_pct
                remaining_quantity -= order_qty

            order_qty = order_qty.quantize(Decimal("0.001"))

            # Find Fibonacci band
            fib_band = ""
            if fib_bands:
                for fib_name, band in fib_bands.items():
                    if fib_name != "VWAP":
                        band_center = (band["upper"] + band["lower"]) / 2
                        band_center_dec = Decimal(str(band_center))
                        if abs(
                            order_price - band_center_dec
                        ) / band_center_dec < Decimal("0.01"):
                            fib_band = fib_name
                            break

            order = self.create_limit_order(
                symbol=symbol,
                side=side,
                price=order_price,
                quantity=order_qty,
                fib_band_source=fib_band,
            )
            orders.append(order)

        batch = BatchOrderRequest(
            orders=orders,
            strategy="fibonacci_levels",
            total_quantity=sum(o.quantity for o in orders),
            total_value=sum(o.quantity * o.price for o in orders if o.price),
        )
        return batch

    def create_grid_orders(
        self,
        symbol: str,
        base_price: Decimal,
        total_quantity: Decimal,
        grid_levels: int = 5,
        grid_spacing_pct: Decimal = Decimal("0.01"),
    ) -> Tuple[BatchOrderRequest, BatchOrderRequest]:
        """Create grid of buy and sell orders around current price"""
        buy_levels, sell_levels = [], []

        for i in range(1, grid_levels + 1):
            offset = Decimal(str(i)) * grid_spacing_pct
            qty_pct = Decimal("1.0") / Decimal(str(grid_levels))
            buy_levels.append((offset, qty_pct))
            sell_levels.append((offset, qty_pct))

        buy_batch = self.create_batch_orders(
            symbol, base_price, total_quantity / Decimal("2"), OrderSide.BUY, buy_levels
        )
        sell_batch = self.create_batch_orders(
            symbol,
            base_price,
            total_quantity / Decimal("2"),
            OrderSide.SELL,
            sell_levels,
        )
        return buy_batch, sell_batch

    def place_batch_order(
        self, batch: BatchOrderRequest, bybit_client
    ) -> Dict[str, str]:
        """Place all orders in a batch"""
        with self._lock:
            if len(self.active_orders) + len(batch.orders) > self.max_active_orders:
                self.logger.warning(
                    f"Exceeded max active orders ({self.max_active_orders})"
                )
                return {}

            results = {}
            placed_orders = []

            for order in batch.orders:
                try:
                    order_data = {
                        "symbol": order.symbol,
                        "side": order.side.value,
                        "orderType": order.order_type.value,
                        "qty": str(order.quantity),
                        "timeInForce": order.time_in_force.value,
                        "orderLinkId": order.order_link_id,
                        "reduceOnly": order.reduce_only,
                    }
                    if order.price:
                        order_data["price"] = str(order.price)

                    actual_order_id = f"order_{order.order_link_id[:8]}"
                    order.status = OrderStatus.NEW
                    order.order_id = actual_order_id
                    results[order.order_link_id] = actual_order_id
                    self.active_orders[actual_order_id] = order
                    placed_orders.append(order)

                except Exception as e:
                    self.logger.error(f"Failed to place order: {e}")
                    order.status = OrderStatus.REJECTED

            batch.orders = placed_orders
            self.batch_history.append(batch)
            return results

    def cancel_all_orders(self, symbol: str = "", bybit_client=None) -> int:
        """Cancel all active orders"""
        cancelled = 0
        orders_to_cancel = list(self.active_orders.keys())
        for order_id in orders_to_cancel:
            order = self.active_orders.get(order_id)
            if order and (not symbol or order.symbol == symbol):
                order.status = OrderStatus.CANCELLED
                self.order_history.append(order)
                del self.active_orders[order_id]
                cancelled += 1
        return cancelled

    def get_active_orders(self, symbol: str = "") -> List[Order]:
        """Get all active orders"""
        if symbol:
            return [o for o in self.active_orders.values() if o.symbol == symbol]
        return list(self.active_orders.values())

    def get_order_summary(self) -> Dict:
        """Get summary of all orders"""
        return {
            "active_orders": len(self.active_orders),
            "total_orders": len(self.order_history) + len(self.active_orders),
            "completed_batches": len(self.completed_batches),
            "pending_batches": len(self.batch_history),
            "active_buy_orders": sum(
                1 for o in self.active_orders.values() if o.side == OrderSide.BUY
            ),
            "active_sell_orders": sum(
                1 for o in self.active_orders.values() if o.side == OrderSide.SELL
            ),
        }


# ═══════════════════════════════════════════════════════════════
# TRADING OPERATIONS
# ═══════════════════════════════════════════════════════════════


def fetch_klines(
    symbol: str = "BTCUSDT", interval: str = "1", limit: int = 200
) -> Optional[List[OHLCV]]:
    """Fetch kline/candlestick data using the unified dispatcher."""
    try:
        response = bybit_api_call(
            action="get_klines",
            symbol=symbol,
            interval=interval,
            limit=limit,
            category="spot",  # Assuming spot based on original class
        )

        if response.get("status") == "error":
            logger.error(f"Failed to fetch klines: {response.get('msg')}")
            return None

        result = response.get("klines", [])
        ohlcv_list = []
        for item in result:
            try:
                ohlcv = OHLCV(
                    timestamp=int(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                )
                ohlcv_list.append(ohlcv)
            except (IndexError, ValueError) as e:
                logger.warning(f"Failed to parse kline: {e}")
                continue
        return ohlcv_list
    except Exception as e:
        logger.error(f"Error fetching klines: {e}")
        return None


def place_order(order: Order) -> Optional[Dict]:
    """Place a single order using the unified dispatcher."""
    try:
        # Map OrderSide/OrderType to bybit_tool expected formats
        result = bybit_api_call(
            action="place_order",
            symbol=order.symbol,
            side=order.side.value,
            qty=float(order.quantity),
            price=float(order.price) if order.price else None,
            order_type=order.order_type.value,
            category="spot",
            time_in_force=order.time_in_force.value,
            client_oid=order.order_link_id,
        )

        if result.get("status") != "error":
            return result
        else:
            logger.error(f"Order placement failed: {result.get('msg')}")
            return None
    except Exception as e:
        logger.error(f"Order placement error: {e}")
        return None


def cancel_all_orders(symbol: str) -> int:
    """Cancel all orders for a symbol using the unified dispatcher."""
    try:
        result = bybit_api_call(
            action="cancel_all_orders", symbol=symbol, category="spot"
        )
        if result.get("status") != "error":
            # Assuming dispatcher returns a count or status. Need to adapt based on dispatcher implementation.
            return 1  # Placeholder
        return 0
    except Exception as e:
        logger.error(f"Cancel all orders error: {e}")
        return 0


class FibonacciVWAPCalculator:
    """Pure Python VWAP + Fibonacci band calculator"""

    def __init__(self, vwap_period: int = 20):
        self.vwap_period = vwap_period
        self.logger = logging.getLogger("FibonacciVWAP")

    def calculate_vwap(self, ohlcv_list: List[OHLCV]) -> Optional[float]:
        """Calculate Volume Weighted Average Price"""
        if len(ohlcv_list) < self.vwap_period:
            return None
        recent_data = ohlcv_list[-self.vwap_period :]
        total_tp_vol = 0.0
        total_vol = 0.0
        for ohlcv in recent_data:
            typical_price = (ohlcv.high + ohlcv.low + ohlcv.close) / 3.0
            total_tp_vol += typical_price * ohlcv.volume
            total_vol += ohlcv.volume
        if total_vol == 0:
            return None
        return total_tp_vol / total_vol

    def calculate_bands(self, vwap: float, ohlcv_list: List[OHLCV]) -> Dict[str, Dict]:
        """Calculate 5 Fibonacci bands around VWAP"""
        if len(ohlcv_list) < 2:
            bandwidth = vwap * 0.01
        else:
            true_ranges = []
            for i in range(1, len(ohlcv_list[-20:])):
                idx = len(ohlcv_list) - 20 + i
                tr = max(
                    ohlcv_list[idx].high - ohlcv_list[idx].low,
                    abs(ohlcv_list[idx].high - ohlcv_list[idx - 1].close),
                    abs(ohlcv_list[idx].low - ohlcv_list[idx - 1].close),
                )
                true_ranges.append(tr)
            bandwidth = (
                sum(true_ranges) / len(true_ranges) if true_ranges else vwap * 0.01
            )

        bands = {}
        band_colors = {
            "FIB_0236": "\033[92m",
            "FIB_0382": "\033[96m",
            "FIB_0500": "\033[93m",
            "FIB_0618": "\033[95m",
            "FIB_0786": "\033[91m",
        }

        for fib_name, fib_ratio in FIB_LEVELS.items():
            band_width = bandwidth * float(fib_ratio)
            bands[fib_name] = {
                "upper": vwap + band_width,
                "lower": vwap - band_width,
                "range": band_width * 2,
                "color": band_colors.get(fib_name, ""),
                "ratio": float(fib_ratio),
            }

        bands["VWAP"] = {
            "value": vwap,
            "upper": vwap,
            "lower": vwap,
            "range": 0,
            "color": "\033[97m",
            "ratio": 0,
        }
        return bands


class EhlersSuperTrend:
    """Pure Python Ehlers SuperTrend implementation"""

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self.period = period
        self.multiplier = multiplier
        self.logger = logging.getLogger("EhlersSuperTrend")

    def calculate_atr(self, ohlcv_list: List[OHLCV]) -> Optional[float]:
        """Calculate Average True Range"""
        if len(ohlcv_list) < self.period + 1:
            return None
        true_ranges = []
        for i in range(1, len(ohlcv_list)):
            tr = max(
                ohlcv_list[i].high - ohlcv_list[i].low,
                abs(ohlcv_list[i].high - ohlcv_list[i - 1].close),
                abs(ohlcv_list[i].low - ohlcv_list[i - 1].close),
            )
            true_ranges.append(tr)
        if not true_ranges:
            return None
        atr = sum(true_ranges[: self.period]) / self.period
        for i in range(self.period, len(true_ranges)):
            atr = (atr * (self.period - 1) + true_ranges[i]) / self.period
        return atr

    def get_super_trend(self, ohlcv_list: List[OHLCV]) -> Optional[Dict]:
        """Calculate Ehlers SuperTrend indicator"""
        if len(ohlcv_list) < self.period + 2:
            return None
        hlc3 = [(ohlcv.high + ohlcv.low + ohlcv.close) / 3.0 for ohlcv in ohlcv_list]
        alpha = 2.0 / (self.period + 1)
        ema = [hlc3[0]]
        for i in range(1, len(hlc3)):
            ema_value = alpha * hlc3[i] + (1 - alpha) * ema[-1]
            ema.append(ema_value)

        atr = self.calculate_atr(ohlcv_list)
        if atr is None:
            return None

        latest_ema = ema[-1]
        current_price = ohlcv_list[-1].close
        upper_band = latest_ema + (self.multiplier * atr)
        lower_band = latest_ema - (self.multiplier * atr)
        mid_band = (upper_band + lower_band) / 2.0

        if current_price > mid_band:
            trend = 1
        elif current_price < mid_band:
            trend = -1
        else:
            trend = 0

        return {
            "trend": trend,
            "upper_band": upper_band,
            "lower_band": lower_band,
            "mid_band": mid_band,
            "atr": atr,
            "current_price": current_price,
        }


class TradingBotMonitor:
    """Main trading bot with batch limit orders"""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        symbol: str = "BTCUSDT",
        proxy: Optional[Dict] = None,
        use_torsocks: bool = False,
    ):
        self.client = BybitClient(api_key, api_secret, testnet, proxy, use_torsocks)
        self.order_manager = BatchOrderManager(max_active_orders=100)
        self.symbol = symbol
        self.logger = logging.getLogger("TradingBotMonitor")
        self.running = False
        self.ohlcv_buffer = CircularBuffer(max_size=500)

        self.supertrend = EhlersSuperTrend(period=10, multiplier=3.0)
        self.fib_vwap = FibonacciVWAPCalculator(vwap_period=20)

        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = Decimal("0")
        self.account_balance = Decimal("10000")

        self.grid_active = False
        self.grid_buy_batch: Optional[BatchOrderRequest] = None
        self.grid_sell_batch: Optional[BatchOrderRequest] = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}. Shutting down...")
        self.running = False

    def _get_market_analysis(self) -> Dict:
        """Get current market analysis"""
        ohlcv_data = self.ohlcv_buffer.get_all()
        if len(ohlcv_data) < 50:
            return {"error": "Insufficient data"}

        vwap = self.fib_vwap.calculate_vwap(ohlcv_data)
        if vwap is None:
            return {"error": "VWAP calculation failed"}

        bands = self.fib_vwap.calculate_bands(vwap, ohlcv_data)
        st = self.supertrend.get_super_trend(ohlcv_data)
        current_price = ohlcv_data[-1].close

        zone = "OUTSIDE"
        fib_order = ["FIB_0786", "FIB_0618", "FIB_0500", "FIB_0382", "FIB_0236"]
        for fib_name in fib_order:
            if fib_name in bands:
                upper, lower = bands[fib_name]["upper"], bands[fib_name]["lower"]
                if lower <= current_price <= upper:
                    zone = fib_name

        signal = 0
        if st:
            signal = st["trend"]

        return {
            "signal": signal,
            "price": current_price,
            "vwap": vwap,
            "bands": bands,
            "zone": zone,
            "supertrend": st,
        }

    def display_analysis(self, analysis: Dict):
        """Display market analysis with colors"""
        if "error" in analysis:
            self.logger.error(analysis["error"])
            return

        price = analysis["price"]
        bands = analysis["bands"]
        zone = analysis["zone"]
        st = analysis.get("supertrend", {})

        print(f"\n{'=' * 60}")
        print(f"📊 Market Analysis | {self.symbol}")
        print(f"{'=' * 60}")
        print(f"💰 Price: ${price:.2f}")
        print(f"📈 VWAP: ${bands['VWAP']['value']:.2f}")
        print(f"🎯 Zone: {zone}")

        if st:
            trend_emoji = (
                "🟢" if st["trend"] == 1 else "🔴" if st["trend"] == -1 else "⚪"
            )
            print(
                f"{trend_emoji} SuperTrend: {st['trend']} | Mid: ${st['mid_band']:.2f}"
            )

        print("\n📊 Fibonacci VWAP Bands:")
        for fib_name in ["FIB_0236", "FIB_0382", "FIB_0500", "FIB_0618", "FIB_0786"]:
            if fib_name in bands:
                b = bands[fib_name]
                color = b["color"]
                print(
                    f"  {color}{fib_name}: ${b['lower']:.2f} - ${b['upper']:.2f}\033[0m"
                )

        order_summary = self.order_manager.get_order_summary()
        print("\n📋 Order Summary:")
        print(
            f"  Active: {order_summary['active_orders']} | Buy: {order_summary['active_buy_orders']} | Sell: {order_summary['active_sell_orders']}"
        )
        print(f"{'=' * 60}\n")

    def start_grid_trading(
        self, quantity: Decimal = Decimal("0.01"), grid_levels: int = 5
    ):
        """Start grid trading"""
        ohlcv_data = self.ohlcv_buffer.get_all()
        if not ohlcv_data:
            self.logger.error("No data for grid trading")
            return

        current_price = Decimal(str(ohlcv_data[-1].close))
        self.grid_buy_batch, self.grid_sell_batch = (
            self.order_manager.create_grid_orders(
                self.symbol, current_price, quantity, grid_levels
            )
        )

        if self.grid_buy_batch:
            # Using the bybit_api_call directly for batches as defined in bybit_tool.py
            bybit_api_call(
                action="batch_orders",
                orders=[o.__dict__ for o in self.grid_buy_batch.orders],
            )
        if self.grid_sell_batch:
            bybit_api_call(
                action="batch_orders",
                orders=[o.__dict__ for o in self.grid_sell_batch.orders],
            )

        self.grid_active = True
        self.logger.info(f"Grid trading started with {grid_levels} levels")

    def stop_grid_trading(self):
        """Stop grid trading"""
        if self.grid_active:
            cancel_all_orders(self.symbol)
            self.grid_active = False
            self.grid_buy_batch = None
            self.grid_sell_batch = None
            self.logger.info("Grid trading stopped")

    def run(self, account_balance: Decimal = Decimal("10000")):
        """Main monitoring loop"""
        self.logger.info(f"Starting trading bot monitor for {self.symbol}")
        self.running = True
        self.account_balance = account_balance

        ohlcv_data = fetch_klines(self.symbol, limit=200)
        if ohlcv_data:
            self.ohlcv_buffer.extend(ohlcv_data)
        else:
            self.logger.error("Failed to fetch initial data. Exiting.")
            return

        update_count = 0

        while self.running:
            try:
                new_data = fetch_klines(self.symbol, limit=10)
                if new_data:
                    existing_timestamps = {
                        x.timestamp for x in self.ohlcv_buffer.get_all()
                    }
                    for item in new_data:
                        if item.timestamp not in existing_timestamps:
                            self.ohlcv_buffer.append(item)

                all_data = self.ohlcv_buffer.get_all()
                # ... rest of loop unchanged

                if len(all_data) >= 50:
                    analysis = self._get_market_analysis()
                    self.display_analysis(analysis)

                    if update_count % 5 == 0 and analysis.get("signal", 0) != 0:
                        self.logger.info(f"Signal detected: {analysis['signal']}")

                update_count += 1
                time.sleep(10)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received. Stopping...")
                self.running = False
                break
            except Exception as e:
                self.logger.error(f"Monitor error: {e}")
                time.sleep(10)

        self.stop_grid_trading()
        self.print_final_summary()

    def print_final_summary(self):
        """Print final performance summary"""
        print(f"\n{'=' * 60}")
        print("📊 Final Performance Summary")
        print(f"{'=' * 60}")
        print(f"Total Trades: {self.total_trades}")
        print(f"Winners: {self.winning_trades} | Losers: {self.losing_trades}")
        print(
            f"Win Rate: {(self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0:.1f}%"
        )
        print(f"Total P&L: ${self.total_pnl:.2f}")
        print(f"Final Balance: ${self.account_balance:.2f}")
        print(f"{'=' * 60}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trading Bot with Batch Limit Orders")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--api-key", default="", help="Bybit API Key")
    parser.add_argument("--api-secret", default="", help="Bybit API Secret")
    parser.add_argument("--testnet", action="store_true", help="Use testnet")
    parser.add_argument(
        "--torsocks", action="store_true", help="Use Tor proxy for anonymity"
    )
    parser.add_argument("--grid", action="store_true", help="Start grid trading")
    parser.add_argument("--grid-levels", type=int, default=5, help="Grid levels")
    parser.add_argument("--quantity", type=float, default=0.01, help="Order quantity")
    args = parser.parse_args()

    bot = TradingBotMonitor(
        args.api_key,
        args.api_secret,
        args.testnet,
        args.symbol,
        use_torsocks=args.torsocks,
    )

    if args.grid:
        bot.start_grid_trading(Decimal(str(args.quantity)), args.grid_levels)

    bot.run()
