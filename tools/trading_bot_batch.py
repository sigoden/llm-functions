#!/usr/bin/env python3
# @describe Trading Bot with Batch Limit Orders + Ehlers SuperTrend + Fibonacci VWAP
# @option --symbol <VALUE> Trading symbol (default: BTCUSDT)
# @option --api-key <VALUE> Bybit API Key
# @option --api-secret <VALUE> Bybit API Secret
# @option --testnet Use testnet (default: true)
# @option --grid-levels <INT> Grid levels (default: 5)
# @option --grid-spacing <NUM> Grid spacing % (default: 0.01)
# @option --batch-size <INT> Max batch orders (default: 10)
# @option --initial-balance <NUM> Initial balance (default: 10000)
# @option --demo Run in demo mode
# @env BYBIT_API_KEY Bybit API Key
# @env BYBIT_API_SECRET Bybit API Secret
# @env BYBIT_TESTNET Use testnet (true/false)
"""
Production Trading Bot with Batch Limit Orders
- Ehlers SuperTrend Cross Strategy
- 5 Fibonacci VWAP Bands
- Batch Limit Order Management
- Grid Trading System
- Pure Python (no numpy/pandas)
"""

import hashlib
import hmac
import logging
import random
import signal
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_DOWN, Decimal, getcontext
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests

# Configure decimal precision
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_DOWN

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("trading_bot_batch.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("TradingBotBatch")

# Constants
BASE_URL = "https://api.bybit.com"
TESTNET_URL = "https://api-testnet.bybit.com"

# Fibonacci ratios
FIB_LEVELS = {
    "FIB_0236": Decimal("0.236"),
    "FIB_0382": Decimal("0.382"),
    "FIB_0500": Decimal("0.500"),
    "FIB_0618": Decimal("0.618"),
    "FIB_0786": Decimal("0.786"),
}


class OrderType(Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    STOP_LIMIT = "StopLimit"
    STOP_MARKET = "StopMarket"


class OrderSide(Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(Enum):
    PENDING = "Pending"
    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"


class TimeInForce(Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
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
    reduce_only: bool = False
    fib_band: str = ""


@dataclass
class BatchOrder:
    """Batch order container"""

    batch_id: str
    orders: List[Order]
    strategy: str = ""
    total_quantity: Decimal = Decimal("0")
    total_value: Decimal = Decimal("0")
    created_time: datetime = field(default_factory=datetime.now)
    status: str = "pending"


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


class BatchOrderManager:
    """Advanced batch order management system"""

    def __init__(self, max_active_orders: int = 50):
        self.max_active_orders = max_active_orders
        self.active_orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self.batch_history: List[BatchOrder] = []
        self.completed_batches: List[BatchOrder] = []
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
        fib_band: str = "",
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
            fib_band=fib_band,
            order_link_id=str(uuid.uuid4())[:8],
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
    ) -> BatchOrder:
        """Create batch of limit orders at different price levels"""
        orders = []
        total_pct = sum(qty_pct for _, qty_pct in levels)
        remaining_quantity = quantity

        for i, (price_pct, qty_pct) in enumerate(levels):
            # Calculate order price
            if side == OrderSide.BUY:
                order_price = base_price * (Decimal("1") - Decimal(str(price_pct)))
            else:
                order_price = base_price * (Decimal("1") + Decimal(str(price_pct)))

            # Calculate order quantity
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
                    if fib_name != "VWAP" and "upper" in band and "lower" in band:
                        band_center = (band["upper"] + band["lower"]) / 2
                        if abs(order_price - Decimal(str(band_center))) / Decimal(
                            str(band_center)
                        ) < Decimal("0.01"):
                            fib_band = fib_name
                            break

            order = self.create_limit_order(
                symbol, side, order_price, order_qty, fib_band=fib_band
            )
            orders.append(order)

        batch = BatchOrder(
            batch_id=str(uuid.uuid4())[:8],
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
    ) -> Tuple[BatchOrder, BatchOrder]:
        """Create grid of buy and sell orders"""
        # Buy orders below current price
        buy_levels = []
        for i in range(1, grid_levels + 1):
            offset = Decimal(str(i)) * grid_spacing_pct
            qty_pct = Decimal("1.0") / Decimal(str(grid_levels))
            buy_levels.append((offset, qty_pct))

        buy_batch = self.create_batch_orders(
            symbol, base_price, total_quantity / Decimal("2"), OrderSide.BUY, buy_levels
        )

        # Sell orders above current price
        sell_levels = []
        for i in range(1, grid_levels + 1):
            offset = Decimal(str(i)) * grid_spacing_pct
            qty_pct = Decimal("1.0") / Decimal(str(grid_levels))
            sell_levels.append((offset, qty_pct))

        sell_batch = self.create_batch_orders(
            symbol,
            base_price,
            total_quantity / Decimal("2"),
            OrderSide.SELL,
            sell_levels,
        )

        return buy_batch, sell_batch

    def create_fibonacci_grid(
        self,
        symbol: str,
        base_price: Decimal,
        total_quantity: Decimal,
        fib_bands: Dict[str, Dict],
    ) -> Tuple[BatchOrder, BatchOrder]:
        """Create orders at Fibonacci band levels"""
        fib_order = ["FIB_0236", "FIB_0382", "FIB_0500", "FIB_0618", "FIB_0786"]
        buy_levels = []
        sell_levels = []

        for fib_name in fib_order:
            if fib_name in fib_bands:
                band = fib_bands[fib_name]
                if "upper" in band and "lower" in band:
                    band_center = (band["upper"] + band["lower"]) / 2
                    if base_price > 0:
                        if band_center < base_price:
                            offset = (base_price - band_center) / base_price
                            qty_pct = Decimal("1.0") / Decimal(str(len(fib_order)))
                            buy_levels.append((Decimal(str(offset)), qty_pct))
                        else:
                            offset = (band_center - base_price) / base_price
                            qty_pct = Decimal("1.0") / Decimal(str(len(fib_order)))
                            sell_levels.append((Decimal(str(offset)), qty_pct))

        buy_batch = None
        sell_batch = None

        if buy_levels:
            buy_batch = self.create_batch_orders(
                symbol,
                base_price,
                total_quantity * Decimal("0.5"),
                OrderSide.BUY,
                buy_levels,
                fib_bands,
            )
        if sell_levels:
            sell_batch = self.create_batch_orders(
                symbol,
                base_price,
                total_quantity * Decimal("0.5"),
                OrderSide.SELL,
                sell_levels,
                fib_bands,
            )

        return buy_batch, sell_batch

    def place_batch(self, batch: BatchOrder, bybit_client) -> Dict[str, str]:
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
                    result = bybit_client.place_order(order)
                    if result:
                        order.order_id = result.get("orderId", order.order_link_id)
                        order.status = OrderStatus.NEW
                        results[order.order_link_id] = order.order_id
                        self.active_orders[order.order_id] = order
                        placed_orders.append(order)
                        self.logger.info(
                            f"Placed {order.side.value} {order.quantity} @ ${order.price}"
                        )
                except Exception as e:
                    self.logger.error(f"Failed to place order: {e}")
                    order.status = OrderStatus.REJECTED

            batch.orders = placed_orders
            batch.status = "placed"
            self.batch_history.append(batch)
            return results

    def cancel_order(self, order_id: str, bybit_client) -> bool:
        """Cancel a single order"""
        with self._lock:
            if order_id in self.active_orders:
                order = self.active_orders[order_id]
                order.status = OrderStatus.CANCELLED
                order.updated_time = datetime.now()
                self.order_history.append(order)
                del self.active_orders[order_id]
                self.logger.info(f"Cancelled order {order_id}")
                return True
            return False

    def cancel_all_orders(self, symbol: str = "", bybit_client=None) -> int:
        """Cancel all active orders"""
        cancelled = 0
        orders_to_cancel = list(self.active_orders.keys())
        for order_id in orders_to_cancel:
            order = self.active_orders.get(order_id)
            if order and (not symbol or order.symbol == symbol):
                if self.cancel_order(order_id, bybit_client):
                    cancelled += 1
        return cancelled

    def get_active_orders(self, symbol: str = "") -> List[Order]:
        """Get all active orders"""
        if symbol:
            return [o for o in self.active_orders.values() if o.symbol == symbol]
        return list(self.active_orders.values())

    def get_summary(self) -> Dict:
        """Get order summary"""
        return {
            "active_orders": len(self.active_orders),
            "total_orders": len(self.order_history) + len(self.active_orders),
            "completed_batches": len(self.completed_batches),
            "pending_batches": len(self.batch_history),
            "active_buy": sum(
                1 for o in self.active_orders.values() if o.side == OrderSide.BUY
            ),
            "active_sell": sum(
                1 for o in self.active_orders.values() if o.side == OrderSide.SELL
            ),
        }


class BybitClient:
    """Lightweight Bybit API client"""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        proxy: Optional[Dict] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = TESTNET_URL if testnet else BASE_URL
        self.proxy = proxy
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update(proxy)
        self.logger = logging.getLogger("BybitClient")
        self.rate_limiter = deque(maxlen=10)

    def _generate_signature(self, params: Dict) -> str:
        """Generate HMAC SHA256 signature"""
        param_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        return hmac.new(
            self.api_secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _rate_limit_check(self):
        """Simple rate limiter"""
        now = time.time()
        while self.rate_limiter and now - self.rate_limiter[0] > 1:
            self.rate_limiter.popleft()
        if len(self.rate_limiter) >= 10:
            sleep_time = 1 - (now - self.rate_limiter[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        self.rate_limiter.append(now)

    def fetch_klines(
        self, symbol: str = "BTCUSDT", interval: str = "1", limit: int = 200
    ) -> Optional[List[OHLCV]]:
        """Fetch kline/candlestick data"""
        self._rate_limit_check()
        endpoint = f"{self.base_url}/v5/market/kline"
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": str(interval),
            "limit": min(limit, 1000),
        }

        try:
            response = self.session.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("retCode") != 0:
                self.logger.error(f"API error: {data.get('retMsg')}")
                return None
            result = data.get("result", {}).get("list", [])
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
                    self.logger.warning(f"Failed to parse kline: {e}")
            return ohlcv_list
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {e}")
            return None

    def place_order(self, order: Order) -> Optional[Dict]:
        """Place a single order"""
        self._rate_limit_check()
        endpoint = f"{self.base_url}/v5/order/create"
        params = {
            "category": "spot",
            "symbol": order.symbol,
            "side": order.side.value,
            "orderType": order.order_type.value,
            "qty": str(order.quantity),
            "timeInForce": order.time_in_force.value,
            "orderLinkId": order.order_link_id,
        }
        if order.price:
            params["price"] = str(order.price)
        if order.stop_price:
            params["stopPrice"] = str(order.stop_price)
        if order.reduce_only:
            params["reduceOnly"] = "true"

        # Add auth
        timestamp = str(int(time.time() * 1000))
        params["api_key"] = self.api_key
        params["timestamp"] = timestamp
        params["sign"] = self._generate_signature(params)

        try:
            response = self.session.post(endpoint, json=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("retCode") == 0:
                return data.get("result", {})
            else:
                self.logger.error(f"Order failed: {data.get('retMsg')}")
                return None
        except Exception as e:
            self.logger.error(f"Order error: {e}")
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order"""
        self._rate_limit_check()
        endpoint = f"{self.base_url}/v5/order/cancel"
        params = {"category": "spot", "symbol": symbol, "orderId": order_id}
        timestamp = str(int(time.time() * 1000))
        params["api_key"] = self.api_key
        params["timestamp"] = timestamp
        params["sign"] = self._generate_signature(params)
        try:
            response = self.session.post(endpoint, json=params, timeout=10)
            return response.json().get("retCode") == 0
        except Exception as e:
            self.logger.error(f"Cancel error: {e}")
            return False

    def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all orders for symbol"""
        self._rate_limit_check()
        endpoint = f"{self.base_url}/v5/order/cancel-all"
        params = {"category": "spot", "symbol": symbol}
        timestamp = str(int(time.time() * 1000))
        params["api_key"] = self.api_key
        params["timestamp"] = timestamp
        params["sign"] = self._generate_signature(params)
        try:
            response = self.session.post(endpoint, json=params, timeout=10)
            data = response.json()
            if data.get("retCode") == 0:
                return data.get("result", {}).get("cancelledOrders", 0)
            return 0
        except Exception as e:
            self.logger.error(f"Cancel all error: {e}")
            return 0


class FibonacciVWAPCalculator:
    """Pure Python VWAP + Fibonacci bands"""

    def __init__(self, vwap_period: int = 20):
        self.vwap_period = vwap_period

    def calculate_vwap(self, ohlcv_list: List[OHLCV]) -> Optional[float]:
        """Calculate VWAP"""
        if len(ohlcv_list) < self.vwap_period:
            return None
        recent_data = ohlcv_list[-self.vwap_period :]
        total_tp_vol = sum(
            ((o.high + o.low + o.close) / 3.0) * o.volume for o in recent_data
        )
        total_vol = sum(o.volume for o in recent_data)
        return total_tp_vol / total_vol if total_vol > 0 else None

    def calculate_bands(self, vwap: float, ohlcv_list: List[OHLCV]) -> Dict[str, Dict]:
        """Calculate 5 Fibonacci bands"""
        if len(ohlcv_list) < 2:
            bandwidth = vwap * 0.01
        else:
            true_ranges = []
            for i in range(1, min(21, len(ohlcv_list))):
                tr = max(
                    ohlcv_list[-i].high - ohlcv_list[-i].low,
                    abs(ohlcv_list[-i].high - ohlcv_list[-i - 1].close),
                    abs(ohlcv_list[-i].low - ohlcv_list[-i - 1].close),
                )
                true_ranges.append(tr)
            bandwidth = (
                sum(true_ranges) / len(true_ranges) if true_ranges else vwap * 0.01
            )

        bands = {}
        for fib_name, fib_ratio in FIB_LEVELS.items():
            band_width = bandwidth * float(fib_ratio)
            bands[fib_name] = {
                "upper": vwap + band_width,
                "lower": vwap - band_width,
                "range": band_width * 2,
            }
        bands["VWAP"] = {"value": vwap, "upper": vwap, "lower": vwap, "range": 0}
        return bands


class EhlersSuperTrend:
    """Pure Python Ehlers SuperTrend"""

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self.period = period
        self.multiplier = multiplier

    def calculate_atr(self, ohlcv_list: List[OHLCV]) -> Optional[float]:
        """Calculate ATR"""
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
        """Calculate SuperTrend"""
        if len(ohlcv_list) < self.period + 2:
            return None
        hlc3 = [(o.high + o.low + o.close) / 3.0 for o in ohlcv_list]
        alpha = 2.0 / (self.period + 1)
        ema = [hlc3[0]]
        for i in range(1, len(hlc3)):
            ema.append(alpha * hlc3[i] + (1 - alpha) * ema[-1])
        atr = self.calculate_atr(ohlcv_list)
        if atr is None:
            return None
        latest_ema = ema[-1]
        current_price = ohlcv_list[-1].close
        upper_band = latest_ema + (self.multiplier * atr)
        lower_band = latest_ema - (self.multiplier * atr)
        mid_band = (upper_band + lower_band) / 2.0
        trend = 1 if current_price > mid_band else -1 if current_price < mid_band else 0
        return {
            "trend": trend,
            "upper_band": upper_band,
            "lower_band": lower_band,
            "mid_band": mid_band,
            "atr": atr,
            "current_price": current_price,
        }


class TradingBotBatch:
    """Main trading bot with batch limit orders"""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        symbol: str = "BTCUSDT",
        proxy: Optional[Dict] = None,
        demo: bool = True,
    ):
        self.client = BybitClient(api_key, api_secret, testnet, proxy)
        self.order_manager = BatchOrderManager(max_active_orders=100)
        self.symbol = symbol
        self.demo = demo
        self.logger = logging.getLogger("TradingBotBatch")
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
        self.grid_buy_batch = None
        self.grid_sell_batch = None
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
        for fib_name in ["FIB_0786", "FIB_0618", "FIB_0500", "FIB_0382", "FIB_0236"]:
            if fib_name in bands:
                if (
                    bands[fib_name]["lower"]
                    <= current_price
                    <= bands[fib_name]["upper"]
                ):
                    zone = fib_name
        return {
            "price": current_price,
            "vwap": vwap,
            "bands": bands,
            "supertrend": st,
            "fib_zone": zone,
        }

    def _display_analysis(self, analysis: Dict):
        """Display market analysis"""
        if "error" in analysis:
            self.logger.warning(analysis["error"])
            return
        price = analysis["price"]
        vwap = analysis["vwap"]
        st = analysis["supertrend"]
        zone = analysis["fib_zone"]
        trend = "UP" if st["trend"] == 1 else "DOWN" if st["trend"] == -1 else "FLAT"
        self.logger.info(
            f"Price: ${price:.2f} | VWAP: ${vwap:.2f} | Trend: {trend} | Zone: {zone}"
        )
        for fib_name in ["FIB_0236", "FIB_0382", "FIB_0500", "FIB_0618", "FIB_0786"]:
            if fib_name in analysis["bands"]:
                band = analysis["bands"][fib_name]
                self.logger.info(
                    f"  {fib_name}: ${band['lower']:.2f} - ${band['upper']:.2f}"
                )

    def _simulate_trade(
        self, analysis: Dict, account_balance: Decimal
    ) -> Optional[TradeResult]:
        """Simulate trade for demo"""
        st = analysis.get("supertrend", {})
        if st.get("trend", 0) == 0:
            return None
        current_price = Decimal(str(analysis["price"]))
        fib_zone = analysis.get("fib_zone", "FIB_0500")
        direction = "LONG" if st["trend"] == 1 else "SHORT"
        quantity = Decimal("0.001")
        position_value = quantity * current_price
        fib_pnl_pct = {
            "FIB_0236": (0.008, -0.004),
            "FIB_0382": (0.012, -0.006),
            "FIB_0500": (0.015, -0.008),
            "FIB_0618": (0.018, -0.010),
            "FIB_0786": (0.022, -0.012),
        }
        win_pct, loss_pct = fib_pnl_pct.get(fib_zone, (0.01, -0.005))
        is_win = random.random() > 0.5
        pnl = position_value * Decimal(str(win_pct if is_win else loss_pct))
        return TradeResult(
            entry_price=current_price,
            exit_price=current_price,
            position_size=quantity,
            pnl=pnl,
            pnl_percent=(pnl / position_value * 100)
            if position_value > 0
            else Decimal("0"),
            entry_time=datetime.now(),
            exit_time=datetime.now() + timedelta(minutes=5),
            trade_direction=direction,
            fib_band_entry=fib_zone,
        )

    def run(
        self, iterations: int = 100, grid_levels: int = 5, grid_spacing: float = 0.01
    ):
        """Main monitoring loop"""
        self.logger.info(f"Starting batch order trading bot for {self.symbol}")
        self.running = True

        # Initial data fetch
        if self.demo:
            base_price = 50000
            for i in range(200):
                ohlcv = OHLCV(
                    timestamp=int(time.time()) - (200 - i) * 60000,
                    open=base_price + random.gauss(0, 100),
                    high=base_price + abs(random.gauss(0, 150)),
                    low=base_price - abs(random.gauss(0, 150)),
                    close=base_price + random.gauss(0, 100),
                    volume=random.uniform(500, 1500),
                )
                self.ohlcv_buffer.append(ohlcv)
                base_price = ohlcv.close
        else:
            ohlcv_data = self.client.fetch_klines(self.symbol, limit=200)
            if ohlcv_data:
                self.ohlcv_buffer.extend(ohlcv_data)
            else:
                self.logger.error("Failed to fetch initial data. Exiting.")
                return

        update_count = 0

        while self.running and update_count < iterations:
            try:
                # Fetch latest data
                if not self.demo:
                    new_data = self.client.fetch_klines(self.symbol, limit=10)
                    if new_data:
                        existing_ts = {x.timestamp for x in self.ohlcv_buffer.get_all()}
                        for item in new_data:
                            if item.timestamp not in existing_ts:
                                self.ohlcv_buffer.append(item)

                all_data = self.ohlcv_buffer.get_all()
                if len(all_data) >= 50:
                    analysis = self._get_market_analysis()
                    self._display_analysis(analysis)

                    # Simulate trade every 5 updates
                    if update_count % 5 == 0:
                        trade = self._simulate_trade(analysis, self.account_balance)
                        if trade:
                            self.total_trades += 1
                            if trade.pnl > 0:
                                self.winning_trades += 1
                            else:
                                self.losing_trades += 1
                            self.total_pnl += trade.pnl
                            self.account_balance += trade.pnl
                            self.logger.info(
                                f"{'WIN' if trade.pnl > 0 else 'LOSS'} | {trade.trade_direction} | {trade.fib_band_entry} | P&L: ${trade.pnl:.2f} | Balance: ${self.account_balance:.2f}"
                            )

                update_count += 1
                time.sleep(1)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt. Stopping...")
                self.running = False
                break
            except Exception as e:
                self.logger.error(f"Monitor error: {e}")
                time.sleep(1)

        self._print_summary()

    def _print_summary(self):
        """Print final summary"""
        print(f"\n{'=' * 60}")
        print("FINAL PERFORMANCE SUMMARY")
        print(f"{'=' * 60}")
        print(f"Total Trades: {self.total_trades}")
        print(f"Winning Trades: {self.winning_trades}")
        print(f"Losing Trades: {self.losing_trades}")
        if self.total_trades > 0:
            win_rate = (self.winning_trades / self.total_trades) * 100
            print(f"Win Rate: {win_rate:.1f}%")
            print(f"Total P&L: ${self.total_pnl:.2f}")
            print(f"Final Balance: ${self.account_balance:.2f}")
            if self.account_balance > 0:
                roi = (self.total_pnl / self.account_balance) * 100
                print(f"ROI: {roi:.2f}%")
        print(f"{'=' * 60}")
        summary = self.order_manager.get_summary()
        print(f"Active Orders: {summary['active_orders']}")
        print(f"Total Orders Placed: {summary['total_orders']}")
        print(f"{'=' * 60}")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Batch Order Trading Bot")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-secret", default="")
    parser.add_argument("--testnet", action="store_true", default=True)
    parser.add_argument("--grid-levels", type=int, default=5)
    parser.add_argument("--grid-spacing", type=float, default=0.01)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--initial-balance", type=float, default=10000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--demo", action="store_true", default=True)
    args = parser.parse_args()

    import os

    api_key = args.api_key or os.getenv("BYBIT_API_KEY", "")
    api_secret = args.api_secret or os.getenv("BYBIT_API_SECRET", "")

    bot = TradingBotBatch(
        api_key, api_secret, args.testnet, args.symbol, demo=args.demo
    )
    bot.account_balance = Decimal(str(args.initial_balance))
    bot.run(
        iterations=args.iterations,
        grid_levels=args.grid_levels,
        grid_spacing=args.grid_spacing,
    )


if __name__ == "__main__":
    main()
