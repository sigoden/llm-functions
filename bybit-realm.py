#!/usr/bin/env python3
"""BYBIT REALM v4.2 - LLM Hardened"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

# from utils.bybit_base import Category
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BybitRealm")


def load_env_file() -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


load_env_file()

__all__ = [
    "BybitToolDispatcher",
    "Category",
    "CircuitState",
    "OrderSide",
    "OrderType",
    "PositionIdx",
    "Signal",
    "TimeInForce",
    "TradingConfig",
    "TriggerBy",
    "run",
]


class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    LIMIT_MAKER = "LimitMaker"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"


class Category(str, Enum):
    LINEAR = "linear"
    INVERSE = "inverse"
    SPOT = "spot"
    OPTION = "option"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class Signal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "PostOnly"


class PositionIdx(int, Enum):
    ONE_WAY = 0
    HEDGE_BUY = 1
    HEDGE_SELL = 2


class TriggerBy(str, Enum):
    LAST_PRICE = "LastPrice"
    INDEX_PRICE = "IndexPrice"
    MARK_PRICE = "MarkPrice"


@dataclass
class TradingConfig:
    api_key: str = field(default_factory=lambda: os.getenv("BYBIT_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BYBIT_API_SECRET", ""))
    testnet: bool = field(
        default_factory=lambda: (
            os.getenv("BYBIT_USE_TESTNET", "false").lower() == "true"
        )
    )
    use_tor: bool = field(
        default_factory=lambda: os.getenv("TOR_ENABLED", "false").lower() == "true"
    )
    tor_socks_port: int = field(
        default_factory=lambda: int(os.getenv("TOR_SOCKS_PORT", "9050"))
    )
    tor_control_port: int = field(
        default_factory=lambda: int(os.getenv("TOR_CONTROL_PORT", "9051"))
    )
    request_timeout: int = 15
    max_retries: int = 3
    clock_sync_threshold_ms: int = 500
    cb_failure_threshold: int = 5
    cb_recovery_timeout: float = 60.0
    cb_cooldown: float = 30.0
    rate_limit_calls: int = 10
    rate_limit_window: float = 1.0
    max_position_usdt: float = 1000.0
    default_leverage: int = 1
    default_stop_loss: float = 0.02
    default_take_profit: float = 0.04
    max_orders_per_batch: int = 20
    iceberg_min_slices: int = 3
    iceberg_max_slices: int = 10
    iceberg_delay: float = 0.5

    @property
    def base_url(self) -> str:
        return (
            "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        )

    def validate(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError("BYBIT_API_KEY and BYBIT_API_SECRET required")


class RateLimiter:
    def __init__(self, max_calls: int, window: float) -> None:
        self._max_calls = max_calls
        self._window = window
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._calls and self._calls[0] <= now - self._window:
                self._calls.popleft()
            if len(self._calls) >= self._max_calls:
                sleep_for = self._window - (now - self._calls[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self._calls.append(time.monotonic())

    @property
    def current_usage(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(1 for c in self._calls if c > now - self._window)


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        cooldown: float = 30.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._cooldown = cooldown
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_ts = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._last_failure_ts >= self._recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
            if self._state == CircuitState.OPEN:
                raise RuntimeError(
                    f"Circuit OPEN – retry in {max(0.0, self._recovery_timeout - (time.monotonic() - self._last_failure_ts)):.1f}s"
                )
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def _on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        do_sleep = False
        with self._lock:
            self._failure_count += 1
            self._last_failure_ts = time.monotonic()
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failure_count >= self._threshold
            ):
                self._state = CircuitState.OPEN
                do_sleep = True
        if do_sleep:
            time.sleep(self._cooldown)


class TorManager:
    def __init__(
        self,
        enabled: bool,
        socks_port: int,
        timeout: int,
        max_retries: int,
        control_port: int = 9051,
    ) -> None:
        self.enabled = enabled
        self.socks_port = socks_port
        self.control_port = control_port
        self.timeout = timeout
        self._proxy_url = f"socks5h://127.0.0.1:{socks_port}" if enabled else None
        self._torsocks_bin = shutil.which("torsocks")
        self._session = self._build_session(max_retries) if REQUESTS_AVAILABLE else None
        self._tor_reachable = False
        self._tor_check_ts = 0.0

    def request(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict:
        tiers = []
        if self.enabled and self._is_tor_reachable():
            tiers.append(self._tier_proxy)
        if self.enabled and self._torsocks_bin:
            tiers.append(self._tier_torsocks)
        tiers.append(self._tier_direct)
        last_exc = None
        for tier in tiers:
            try:
                return tier(method, url, headers, params, json_data)
            except Exception as exc:
                last_exc = exc
                continue
        raise ConnectionError(f"All network tiers exhausted. Last: {last_exc}")

    def _tier_proxy(self, method, url, headers, params, json_data) -> dict:
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            proxies={"http": self._proxy_url, "https": self._proxy_url},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse(resp.json())

    def _tier_torsocks(self, method, url, headers, params, json_data) -> dict:
        cmd = [self._torsocks_bin, "curl", "-s", "-X", method]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if json_data:
            cmd += ["-d", json.dumps(json_data)]
        if params:
            url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
        cmd.append(url)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.timeout + 5
        )
        if result.returncode != 0:
            raise RuntimeError(f"torsocks exited {result.returncode}")
        return self._parse(json.loads(result.stdout))

    def _tier_direct(self, method, url, headers, params, json_data) -> dict:
        resp = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse(resp.json())

    @staticmethod
    def _parse(data: Any) -> dict:
        if isinstance(data, dict) and data.get("retCode", 0) != 0:
            raise RuntimeError(
                f"Bybit API error {data.get('retCode')}: {data.get('retMsg')}"
            )
        return data

    @staticmethod
    def _build_session(max_retries: int):
        session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _is_tor_reachable(self) -> bool:
        if not self.enabled:
            return False
        now = time.monotonic()
        if now - self._tor_check_ts < 60.0:
            return self._tor_reachable
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            self._tor_reachable = sock.connect_ex(("127.0.0.1", self.socks_port)) == 0
            sock.close()
        except:
            self._tor_reachable = False
        self._tor_check_ts = now
        return self._tor_reachable

    def _safe_renew_identity(self) -> None:
        try:
            self.renew_tor_identity()
        except:
            pass

    def renew_tor_identity(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect(("127.0.0.1", self.control_port))
            sock.sendall(b"AUTHENTICATE\r\n")
            if not sock.recv(1024).startswith(b"250"):
                raise RuntimeError("Auth failed")
            sock.sendall(b"SIGNAL NEWNYM\r\n")
            self._tor_check_ts = 0.0
        finally:
            sock.close()


@dataclass
class LotSizeFilter:
    qty_step: float
    min_order_qty: float
    max_order_qty: float
    min_notional: float = 0.0

    def adjust(self, qty: float) -> float:
        if self.qty_step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(self.qty_step))))
        return float(
            max(
                self.min_order_qty,
                min(
                    self.max_order_qty,
                    round(round(qty / self.qty_step) * self.qty_step, precision),
                ),
            )
        )


@dataclass
class PriceFilter:
    tick_size: float
    min_price: float = 0.0
    max_price: float = 1e12

    def adjust(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        precision = max(0, -int(math.floor(math.log10(self.tick_size))))
        return float(
            max(
                self.min_price,
                min(
                    self.max_price,
                    round(round(price / self.tick_size) * self.tick_size, precision),
                ),
            )
        )


@dataclass
class InstrumentInfo:
    lot_size: LotSizeFilter
    price_flt: PriceFilter
    symbol: str
    status: str = "Trading"
    fetched_at: float = field(default_factory=time.time)

    @property
    def is_stale(self) -> bool:
        return time.time() - self.fetched_at > 3600


@dataclass
class MomentumResult:
    symbol: str
    imbalance: float
    signal: Signal
    buy_vol: float
    sell_vol: float
    vwap: float = 0.0
    avg_trade_sz: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "imbalance": round(self.imbalance, 4),
            "signal": self.signal.value,
            "buy_vol": round(self.buy_vol, 4),
            "sell_vol": round(self.sell_vol, 4),
            "vwap": round(self.vwap, 4),
            "avg_trade_sz": round(self.avg_trade_sz, 4),
            "timestamp": self.timestamp,
        }


@dataclass
class PnLReport:
    symbol: str
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    total_fees: float
    trade_count: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_pnl": round(self.total_pnl, 4),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "largest_win": round(self.largest_win, 4),
            "largest_loss": round(self.largest_loss, 4),
            "total_fees": round(self.total_fees, 4),
            "trade_count": self.trade_count,
        }


class ClockSync:
    def __init__(self, threshold_ms: int = 500) -> None:
        self._offset_ms: int = 0
        self._synced_at: float = 0.0
        self._lock = threading.Lock()

    def sync(self, server_time_ms: int) -> None:
        with self._lock:
            self._offset_ms = server_time_ms - int(time.time() * 1000)
            self._synced_at = time.monotonic()

    def now_ms(self) -> str:
        with self._lock:
            return str(int(time.time() * 1000) + self._offset_ms)

    @property
    def offset_ms(self) -> int:
        with self._lock:
            return self._offset_ms

    @property
    def needs_sync(self) -> bool:
        return time.monotonic() - self._synced_at > 300.0


def _require(*pairs: Tuple[str, Any]) -> Optional[dict]:
    missing = [name for name, val in pairs if val is None]
    return (
        {
            "status": "error",
            "msg": f"Required parameter(s) missing: {', '.join(missing)}",
        }
        if missing
        else None
    )


class BybitToolDispatcher:
    _RECV_WINDOW = "5000"

    def __init__(self, config: TradingConfig) -> None:
        config.validate()
        self.config = config
        self.tor = TorManager(
            config.use_tor,
            config.tor_socks_port,
            config.request_timeout,
            config.max_retries,
            config.tor_control_port,
        )
        self.circuit = CircuitBreaker(
            config.cb_failure_threshold, config.cb_recovery_timeout, config.cb_cooldown
        )
        self.limiter = RateLimiter(config.rate_limit_calls, config.rate_limit_window)
        self.clock = ClockSync(config.clock_sync_threshold_ms)
        self._instr_cache: Dict[str, InstrumentInfo] = {}
        self._cache_lock = threading.Lock()

    def _sign(self, payload: str, timestamp: str) -> str:
        return hmac.new(
            self.config.api_secret.encode(),
            f"{timestamp}{self.config.api_key}{self._RECV_WINDOW}{payload}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _ensure_clock_sync(self) -> None:
        if self.clock.needs_sync:
            try:
                resp = self.tor.request(
                    "GET",
                    f"{self.config.base_url}/v5/market/time",
                    {"Content-Type": "application/json"},
                    None,
                    None,
                )
                ts_nano = resp.get("result", {}).get("timeNano")
                if ts_nano:
                    self.clock.sync(int(ts_nano) // 1_000_000)
            except:
                pass

    def _sanitize(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: self._sanitize(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize(v) for v in data]
        elif isinstance(data, Enum):
            return data.value
        elif isinstance(data, str) and data.lower() in ("true", "false"):
            return data.lower() == "true"
        return data

    def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
    ) -> dict:
        self.limiter.acquire()
        if signed:
            self._ensure_clock_sync()
        url = f"{self.config.base_url}{endpoint}"
        ts = self.clock.now_ms() if signed else str(int(time.time() * 1000))
        sanitized_json = self._sanitize(json_data or {})
        payload_str = (
            json.dumps(sanitized_json, separators=(",", ":"))
            if method == "POST"
            else "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        )
        headers = {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": self._RECV_WINDOW,
        }
        if signed:
            headers["X-BAPI-SIGN"] = self._sign(payload_str, ts)
        return self.circuit.call(
            self.tor.request,
            method,
            url,
            headers,
            params if method == "GET" else None,
            sanitized_json if method == "POST" else None,
        )

    def api_request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        signed: bool = True,
        max_retries: int = 3,
    ) -> dict:
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                return self.api_request(method, endpoint, params, json_data, signed)
            except RuntimeError as exc:
                last_exc = exc
                error_msg = str(exc)
                if "403" in error_msg:
                    self.tor._safe_renew_identity()
                if attempt >= max_retries:
                    raise
                time.sleep(2**attempt)
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    raise
                time.sleep(2**attempt)
        raise last_exc or RuntimeError("Max retries exceeded")

    def _fetch_instrument(self, symbol: str, category: str) -> InstrumentInfo:
        with self._cache_lock:
            info = self._instr_cache.get(symbol)
            if info and not info.is_stale:
                return info
        resp = self.api_request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": category, "symbol": symbol},
            signed=False,
        )
        item = resp["result"]["list"][0]
        lot = item["lotSizeFilter"]
        pft = item.get("priceFilter", {})
        info = InstrumentInfo(
            LotSizeFilter(
                float(lot["qtyStep"]),
                float(lot["minOrderQty"]),
                float(lot.get("maxOrderQty", 1e9)),
            ),
            PriceFilter(float(pft.get("tickSize", 0.01))),
            symbol,
        )
        with self._cache_lock:
            self._instr_cache[symbol] = info
            return info

    def adjust_quantity(
        self, symbol: str, qty: float, category: str = Category.LINEAR
    ) -> float:
        return self._fetch_instrument(symbol, category).lot_size.adjust(qty)

    def adjust_price(
        self, symbol: str, price: float, category: str = Category.LINEAR
    ) -> float:
        return self._fetch_instrument(symbol, category).price_flt.adjust(price)

    def invalidate_instrument_cache(self, symbol: Optional[str] = None) -> None:
        with self._cache_lock:
            if symbol:
                self._instr_cache.pop(symbol, None)
            else:
                self._instr_cache.clear()

    def get_server_time(self) -> dict:
        resp = self.api_request("GET", "/v5/market/time", signed=False)
        time_nano = resp.get("result", {}).get("timeNano", "0")
        time_ms = int(time_nano) // 1_000_000 if time_nano else 0
        if time_ms:
            self.clock.sync(time_ms)
        return {
            "time_nano": time_nano,
            "time_ms": time_ms,
            "time_second": time_ms // 1000 if time_ms else 0,
        }

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: Optional[float] = None,
        order_type: OrderType = OrderType.LIMIT,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: TimeInForce = TimeInForce.GTC,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
        trailing_stop: Optional[float] = None,
    ) -> dict:
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(self.adjust_quantity(symbol, qty, category)),
            "timeInForce": time_in_force,
            "positionIdx": int(position_idx.value),
        }
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        payload["reduceOnly"] = bool(reduce_only)
        if client_oid:
            payload["orderLinkId"] = client_oid
        return self.api_request_with_retry(
            "POST", "/v5/order/create", json_data=payload
        )

    def place_conditional_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        trigger_price: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        category: Category = Category.LINEAR,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trigger_by: TriggerBy = TriggerBy.LAST_PRICE,
        time_in_force: TimeInForce = TimeInForce.GTC,
        position_idx: PositionIdx = PositionIdx.ONE_WAY,
        client_oid: Optional[str] = None,
        reduce_only: bool = False,
    ) -> dict:
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(self.adjust_quantity(symbol, qty, category)),
            "triggerPrice": str(self.adjust_price(symbol, trigger_price, category)),
            "triggerBy": trigger_by,
            "timeInForce": time_in_force,
            "positionIdx": position_idx,
        }
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if reduce_only:
            payload["reduceOnly"] = True
        if client_oid:
            payload["orderLinkId"] = client_oid
        return self.api_request_with_retry(
            "POST", "/v5/order/create", json_data=payload
        )

    def execute_scalp_batch(self, order_list: List[dict]) -> dict:
        if not order_list:
            raise ValueError("order_list empty")
        batch = []
        for o in order_list:
            cat = o.get("category", Category.LINEAR)
            entry: Dict[str, Any] = {
                "category": cat,
                "symbol": o["symbol"],
                "side": o["side"],
                "orderType": o.get("orderType", OrderType.LIMIT),
                "qty": str(self.adjust_quantity(o["symbol"], float(o["qty"]), cat)),
                "timeInForce": o.get("timeInForce", TimeInForce.GTC),
            }
            if "price" in o:
                entry["price"] = str(
                    self.adjust_price(o["symbol"], float(o["price"]), cat)
                )
            batch.append(entry)
        return self.api_request_with_retry(
            "POST",
            "/v5/order/create-batch",
            json_data={"category": Category.LINEAR, "request": batch},
        )

    def cancel_order(
        self, symbol: str, order_id: str, category: Category = Category.LINEAR
    ) -> dict:
        return self.api_request_with_retry(
            "POST",
            "/v5/order/cancel",
            json_data={"category": category, "symbol": symbol, "orderId": order_id},
        )

    def cancel_all_orders(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        return self.api_request_with_retry(
            "POST",
            "/v5/order/cancel-all",
            json_data={"category": category, "symbol": symbol},
        )

    def get_open_orders(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        return (
            self.api_request("GET", "/v5/order/realtime", params=params)
            .get("result", {})
            .get("list", [])
        )

    def amend_order(
        self,
        symbol: str,
        order_id: str,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
        }
        if qty is not None:
            payload["qty"] = str(self.adjust_quantity(symbol, qty, category))
        if price is not None:
            payload["price"] = str(self.adjust_price(symbol, price, category))
        return self.api_request_with_retry("POST", "/v5/order/amend", json_data=payload)

    def get_positions(
        self, category: Category = Category.LINEAR, symbol: Optional[str] = None
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return (
            self.api_request("GET", "/v5/position/list", params=params)
            .get("result", {})
            .get("list", [])
        )

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> dict:
        return self.api_request(
            "GET", "/v5/account/wallet-balance", params={"accountType": account_type}
        ).get("result", {})

    def get_account_info(self) -> dict:
        return self.api_request("GET", "/v5/account/info").get("result", {})

    def get_fee_rates(
        self, symbol: Optional[str] = None, category: Category = Category.LINEAR
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return (
            self.api_request("GET", "/v5/account/fee-rate", params=params)
            .get("result", {})
            .get("list", [])
        )

    def set_leverage(
        self, symbol: str, leverage: int, category: Category = Category.LINEAR
    ) -> dict:
        return self.api_request_with_retry(
            "POST",
            "/v5/position/set-leverage",
            json_data={
                "category": category,
                "symbol": symbol,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            },
        )

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        category: Category = Category.LINEAR,
    ) -> dict:
        payload: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "positionIdx": 0,
        }
        if stop_loss is not None:
            payload["stopLoss"] = str(self.adjust_price(symbol, stop_loss, category))
        if take_profit is not None:
            payload["takeProfit"] = str(
                self.adjust_price(symbol, take_profit, category)
            )
        if trailing_stop is not None:
            payload["trailingStop"] = str(trailing_stop)
        return self.api_request_with_retry(
            "POST", "/v5/position/trading-stop", json_data=payload
        )

    def get_pnl_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 100,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        return (
            self.api_request("GET", "/v5/position/closed-pnl", params=params)
            .get("result", {})
            .get("list", [])
        )

    def get_pnl_report(
        self, symbol: str, category: Category = Category.LINEAR, limit: int = 100
    ) -> PnLReport:
        records = self.get_pnl_history(symbol=symbol, category=category, limit=limit)
        if not records:
            return PnLReport(symbol, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        pnls = [float(r.get("closedPnl", 0)) for r in records]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return PnLReport(
            symbol,
            sum(pnls),
            len(wins),
            len(losses),
            len(wins) / len(pnls) if pnls else 0,
            statistics.mean(wins) if wins else 0,
            statistics.mean(losses) if losses else 0,
            max(wins) if wins else 0,
            min(losses) if losses else 0,
            sum([float(r.get("cumExecFee", 0)) for r in records]),
            len(pnls),
        )

    def get_order_history(
        self,
        symbol: Optional[str] = None,
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        return (
            self.api_request("GET", "/v5/order/history", params=params)
            .get("result", {})
            .get("list", [])
        )

    def get_ticker(self, symbol: str, category: Category = Category.LINEAR) -> dict:
        items = (
            self.api_request(
                "GET",
                "/v5/market/tickers",
                params={"category": category, "symbol": symbol},
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )
        return items[0] if items else {}

    def get_tickers_bulk(
        self, symbols: List[str], category: Category = Category.LINEAR
    ) -> List[dict]:
        all_tickers = (
            self.api_request(
                "GET", "/v5/market/tickers", params={"category": category}, signed=False
            )
            .get("result", {})
            .get("list", [])
        )
        sym_set = {s.upper() for s in symbols}
        return [t for t in all_tickers if t.get("symbol", "") in sym_set]

    def get_orderbook(
        self, symbol: str, limit: int = 25, category: Category = Category.LINEAR
    ) -> dict:
        return self.api_request_with_retry(
            "GET",
            "/v5/market/orderbook",
            params={"category": category, "symbol": symbol, "limit": limit},
            signed=False,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "1",
        limit: int = 200,
        category: Category = Category.LINEAR,
    ) -> List[dict]:
        return (
            self.api_request_with_retry(
                "GET",
                "/v5/market/kline",
                params={
                    "category": category,
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                },
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )

    def get_recent_trades(
        self, symbol: str, limit: int = 500, category: Category = Category.LINEAR
    ) -> List[dict]:
        return (
            self.api_request(
                "GET",
                "/v5/market/recent-trade",
                params={"category": category, "symbol": symbol, "limit": limit},
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )

    def get_open_interest(
        self,
        symbol: str,
        interval_time: str = "5min",
        category: Category = Category.LINEAR,
        limit: int = 50,
    ) -> List[dict]:
        return (
            self.api_request(
                "GET",
                "/v5/market/open-interest",
                params={
                    "category": category,
                    "symbol": symbol,
                    "intervalTime": interval_time,
                    "limit": limit,
                },
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )

    def get_liquidations(
        self, symbol: str, category: Category = Category.LINEAR, limit: int = 200
    ) -> List[dict]:
        return (
            self.api_request(
                "GET",
                "/v5/market/liquidation",
                params={"category": category, "symbol": symbol, "limit": limit},
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )

    def get_instruments_info(
        self,
        category: Category = Category.LINEAR,
        symbol: Optional[str] = None,
        base_coin: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if base_coin:
            params["baseCoin"] = base_coin
        if status:
            params["status"] = status
        return (
            self.api_request(
                "GET", "/v5/market/instruments-info", params=params, signed=False
            )
            .get("result", {})
            .get("list", [])
        )

    def get_mark_price(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> float:
        return float(self.get_ticker(symbol, category).get("markPrice", 0.0))

    def get_index_price(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> float:
        return float(self.get_ticker(symbol, category).get("indexPrice", 0.0))

    def get_spread_analysis(
        self, symbol: str, depth: int = 5, category: Category = Category.LINEAR
    ) -> dict:
        raw = self.get_orderbook(symbol=symbol, limit=depth, category=category).get(
            "result", {}
        )
        bids = raw.get("b", [])[:depth]
        asks = raw.get("a", [])[:depth]
        if not bids or not asks:
            return {"error": "Empty orderbook"}
        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2.0
        bid_depth = sum(float(p) * float(q) for p, q in bids)
        ask_depth = sum(float(p) * float(q) for p, q in asks)
        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread_abs": round(best_ask - best_bid, 8),
            "spread_pct": round((best_ask - best_bid) / mid_price * 100, 6),
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
            "depth_imbalance": round(
                (bid_depth - ask_depth) / (bid_depth + ask_depth), 4
            ),
        }

    @staticmethod
    def _compute_rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return float("nan")
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        return (
            100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 2)
        )

    @staticmethod
    def _compute_bollinger(
        closes: List[float], period: int = 20, std_dev: float = 2.0
    ) -> Dict[str, float]:
        if len(closes) < period:
            return {
                "upper": float("nan"),
                "middle": float("nan"),
                "lower": float("nan"),
            }
        window = closes[-period:]
        middle = sum(window) / period
        std = math.sqrt(sum((x - middle) ** 2 for x in window) / period)
        return {
            "upper": round(middle + std_dev * std, 6),
            "middle": round(middle, 6),
            "lower": round(middle - std_dev * std, 6),
        }

    @staticmethod
    def _compute_atr(
        closes: List[float], highs: List[float], lows: List[float], period: int = 14
    ) -> float:
        if len(closes) < period + 1:
            return float("nan")
        trs = [
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            for i in range(1, len(closes))
        ]
        return sum(trs[-period:]) / period

    def get_technical_analysis(
        self,
        symbol: str,
        interval: str = "15",
        rsi_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        limit: int = 100,
        category: Category = Category.LINEAR,
    ) -> dict:
        klines = self.get_klines(
            symbol=symbol, interval=interval, limit=limit + 1, category=category
        )
        if not klines:
            return {"error": "No kline data returned"}
        klines_asc = list(reversed(klines))
        closes = [float(k[4]) for k in klines_asc]
        highs = [float(k[2]) for k in klines_asc]
        lows = [float(k[3]) for k in klines_asc]
        current_price = closes[-1]
        rsi = self._compute_rsi(closes, rsi_period)
        bb = self._compute_bollinger(closes, bb_period, bb_std)
        atr = self._compute_atr(closes, highs, lows, 14)
        return {
            "symbol": symbol,
            "interval": interval,
            "current_price": current_price,
            "rsi": rsi,
            "rsi_signal": "OVERSOLD"
            if not math.isnan(rsi) and rsi < 30
            else "OVERBOUGHT"
            if not math.isnan(rsi) and rsi > 70
            else "NEUTRAL",
            "bollinger": bb,
            "bb_signal": "OVERSOLD"
            if current_price < bb["lower"]
            else "OVERBOUGHT"
            if current_price > bb["upper"]
            else "NEUTRAL",
            "atr": round(atr, 6),
        }

    def get_market_momentum(
        self,
        symbol: str,
        category: Category = Category.LINEAR,
        strong_threshold: float = 0.20,
        mild_threshold: float = 0.08,
    ) -> MomentumResult:
        trades = self.get_recent_trades(symbol, limit=500, category=category)
        buy_vol = sum(float(t["size"]) for t in trades if t["side"] == "Buy")
        sell_vol = sum(float(t["size"]) for t in trades if t["side"] == "Sell")
        total = buy_vol + sell_vol
        imbalance = (buy_vol - sell_vol) / total if total > 0 else 0.0
        vwap = (
            sum(float(t["price"]) * float(t["size"]) for t in trades) / total
            if total > 0
            else 0.0
        )
        signal = (
            Signal.STRONG_BUY
            if imbalance > strong_threshold
            else Signal.BUY
            if imbalance > mild_threshold
            else Signal.STRONG_SELL
            if imbalance < -strong_threshold
            else Signal.SELL
            if imbalance < -mild_threshold
            else Signal.NEUTRAL
        )
        return MomentumResult(
            symbol,
            imbalance,
            signal,
            buy_vol,
            sell_vol,
            vwap,
            total / len(trades) if trades else 0.0,
        )

    def get_funding_rate(
        self, symbol: str, category: Category = Category.LINEAR
    ) -> dict:
        items = (
            self.api_request(
                "GET",
                "/v5/market/funding/history",
                params={"category": category, "symbol": symbol, "limit": 1},
                signed=False,
            )
            .get("result", {})
            .get("list", [])
        )
        return items[0] if items else {}

    def calculate_sl_tp(
        self,
        entry_price: float,
        side: OrderSide,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
    ) -> Tuple[float, float]:
        sl_pct = sl_pct if sl_pct is not None else self.config.default_stop_loss
        tp_pct = tp_pct if tp_pct is not None else self.config.default_take_profit
        if side == OrderSide.BUY:
            return round(entry_price * (1 - sl_pct), 8), round(
                entry_price * (1 + tp_pct), 8
            )
        return round(entry_price * (1 + sl_pct), 8), round(
            entry_price * (1 - tp_pct), 8
        )

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        sl_price: float,
        risk_usdt: float,
        category: Category = Category.LINEAR,
    ) -> float:
        price_diff = abs(entry_price - sl_price)
        return (
            self.adjust_quantity(symbol, risk_usdt / price_diff, category)
            if price_diff > 0
            else 0.0
        )

    def calculate_atr_position_size(
        self,
        symbol: str,
        risk_usdt: float,
        atr_mult: float = 1.5,
        interval: str = "15",
        category: Category = Category.LINEAR,
    ) -> dict:
        ta = self.get_technical_analysis(
            symbol=symbol, interval=interval, category=category
        )
        atr = ta.get("atr", float("nan"))
        current_price = ta.get("current_price", 0.0)
        if math.isnan(atr) or atr == 0 or current_price == 0:
            return {"error": "Could not compute ATR or price"}
        sl_distance = atr * atr_mult
        return {
            "symbol": symbol,
            "current_price": current_price,
            "atr": round(atr, 6),
            "atr_mult": atr_mult,
            "sl_distance": round(sl_distance, 6),
            "risk_usdt": risk_usdt,
            "quantity": self.adjust_quantity(symbol, risk_usdt / sl_distance, category),
        }

    def check_max_position(self, symbol: str, usdt_value: float) -> bool:
        current = sum(
            float(p.get("positionValue", 0)) for p in self.get_positions(symbol=symbol)
        )
        return usdt_value <= (self.config.max_position_usdt - current)

    def safe_execute(
        self,
        fn: Callable,
        *args: Any,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except RuntimeError as exc:
                if "Circuit OPEN" in str(exc):
                    return {"status": "circuit_open", "msg": str(exc)}
                last_exc = exc
                time.sleep(base_delay * (2 ** (attempt - 1)))
            except Exception as exc:
                last_exc = exc
                time.sleep(base_delay * (2 ** (attempt - 1)))
        return {"status": "error", "msg": str(last_exc)}

    def health_check(self) -> dict:
        try:
            resp = self.api_request("GET", "/v5/market/time", signed=False)
            time_nano = resp.get("result", {}).get("timeNano")
            if time_nano:
                self.clock.sync(int(time_nano) // 1_000_000)
            return {
                "status": "ok",
                "circuit": self.circuit.state.value,
                "circuit_fails": self.circuit.failure_count,
                "rate_usage": self.limiter.current_usage,
                "server_time": time_nano,
                "base_url": self.config.base_url,
                "tor_enabled": self.config.use_tor,
                "testnet": self.config.testnet,
                "clock_offset_ms": self.clock.offset_ms,
            }
        except Exception as exc:
            return {"status": "error", "msg": str(exc)}


_dispatcher: Optional[BybitToolDispatcher] = None
_disp_lock = threading.Lock()


def _get_dispatcher() -> BybitToolDispatcher:
    global _dispatcher
    if _dispatcher is None:
        with _disp_lock:
            if _dispatcher is None:
                _dispatcher = BybitToolDispatcher(TradingConfig())
    return _dispatcher


VALID_ACTIONS = [
    "health_check",
    "get_server_time",
    "place_order",
    "place_conditional_order",
    "amend_order",
    "cancel_order",
    "cancel_all_orders",
    "get_open_orders",
    "get_order_history",
    "get_positions",
    "get_wallet_balance",
    "get_account_info",
    "get_fee_rates",
    "set_leverage",
    "set_trading_stop",
    "get_ticker",
    "get_tickers_bulk",
    "get_orderbook",
    "get_klines",
    "get_recent_trades",
    "get_open_interest",
    "get_liquidations",
    "get_instruments_info",
    "get_spread_analysis",
    "get_technical_analysis",
    "get_market_momentum",
    "get_funding_rate",
    "get_mark_price",
    "get_index_price",
    "calculate_sl_tp",
    "calculate_position_size",
    "calculate_atr_position_size",
    "get_pnl_history",
    "get_pnl_report",
    "batch_orders",
    "iceberg_order",
    "reset_circuit",
    "invalidate_cache",
    "renew_tor_circuit",
]


def run(
    action: str,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    qty: Optional[Any] = None,
    price: Optional[Any] = None,
    order_type: Optional[str] = None,
    category: Optional[str] = None,
    order_id: Optional[str] = None,
    stop_loss: Optional[Any] = None,
    take_profit: Optional[Any] = None,
    trailing_stop: Optional[Any] = None,
    reduce_only: Optional[bool] = False,
    time_in_force: Optional[str] = None,
    position_idx: Optional[Any] = None,
    client_oid: Optional[str] = None,
    trigger_price: Optional[Any] = None,
    trigger_by: Optional[str] = None,
    leverage: Optional[Any] = None,
    buy_leverage: Optional[Any] = None,
    sell_leverage: Optional[Any] = None,
    account_type: Optional[str] = "UNIFIED",
    limit: Optional[Any] = 25,
    interval: Optional[str] = "1",
    interval_time: Optional[str] = "5min",
    depth: Optional[Any] = 5,
    symbols: Optional[List[str]] = None,
    base_coin: Optional[str] = None,
    status: Optional[str] = None,
    strong_threshold: Optional[Any] = 0.20,
    mild_threshold: Optional[Any] = 0.08,
    rsi_period: Optional[Any] = 14,
    bb_period: Optional[Any] = 20,
    bb_std: Optional[Any] = 2.0,
    atr_mult: Optional[Any] = 1.5,
    sl_pct: Optional[Any] = None,
    tp_pct: Optional[Any] = None,
    risk_usdt: Optional[Any] = None,
    sl_price: Optional[Any] = None,
    orders: Optional[List[Dict[str, Any]]] = None,
    slices: Optional[Any] = 5,
    delay: Optional[Any] = None,
) -> dict:

    # LLM Hardening: Type casting
    def _f(v):
        return float(v) if v is not None else None

    def _i(v):
        return int(v) if v is not None else None

    qty, price, stop_loss, take_profit, trailing_stop, trigger_price = (
        _f(qty),
        _f(price),
        _f(stop_loss),
        _f(take_profit),
        _f(trailing_stop),
        _f(trigger_price),
    )
    (
        leverage,
        buy_leverage,
        sell_leverage,
        limit,
        depth,
        rsi_period,
        bb_period,
        slices,
    ) = (
        _i(leverage),
        _i(buy_leverage),
        _i(sell_leverage),
        _i(limit),
        _i(depth),
        _i(rsi_period),
        _i(bb_period),
        _i(slices),
    )
    (
        strong_threshold,
        mild_threshold,
        bb_std,
        atr_mult,
        sl_pct,
        tp_pct,
        risk_usdt,
        sl_price,
        delay,
    ) = (
        _f(strong_threshold),
        _f(mild_threshold),
        _f(bb_std),
        _f(atr_mult),
        _f(sl_pct),
        _f(tp_pct),
        _f(risk_usdt),
        _f(sl_price),
        _f(delay),
    )
    position_idx = _i(position_idx)

    if action not in VALID_ACTIONS:
        return {
            "status": "error",
            "msg": f"Invalid action '{action}'. Valid actions: {', '.join(VALID_ACTIONS)}",
        }

    bot = _get_dispatcher()
    try:
        cat = Category(category or "linear")
        tif = TimeInForce(time_in_force or "GTC")
        pidx = PositionIdx(position_idx if position_idx is not None else 0)
        trig = TriggerBy(trigger_by or "LastPrice")

        if action == "health_check":
            return bot.health_check()
        elif action == "reset_circuit":
            bot.circuit.reset()
            return {"status": "ok", "msg": "Circuit breaker reset"}
        elif action == "invalidate_cache":
            bot.invalidate_instrument_cache(symbol)
            return {"status": "ok", "msg": f"Cache cleared for {symbol or 'all'}"}
        elif action == "get_server_time":
            return bot.get_server_time()
        elif action == "place_order":
            err = _require(("symbol", symbol), ("side", side), ("qty", qty))
            if err:
                return err
            return bot.place_order(
                symbol,
                OrderSide(side),
                qty,
                price,
                OrderType(order_type or "Limit"),
                cat,
                stop_loss,
                take_profit,
                reduce_only or False,
                tif,
                pidx,
                client_oid,
                trailing_stop,
            )
        elif action == "place_conditional_order":
            err = _require(
                ("symbol", symbol),
                ("side", side),
                ("qty", qty),
                ("trigger_price", trigger_price),
            )
            if err:
                return err
            return bot.place_conditional_order(
                symbol,
                OrderSide(side),
                qty,
                trigger_price,
                OrderType(order_type or "Market"),
                price,
                cat,
                stop_loss,
                take_profit,
                trig,
                tif,
                pidx,
                client_oid,
                reduce_only or False,
            )
        elif action == "amend_order":
            err = _require(("symbol", symbol), ("order_id", order_id))
            if err:
                return err
            return bot.amend_order(symbol, order_id, qty, price, cat)
        elif action == "cancel_order":
            err = _require(("symbol", symbol), ("order_id", order_id))
            if err:
                return err
            return bot.cancel_order(symbol, order_id, cat)
        elif action == "cancel_all_orders":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.cancel_all_orders(symbol, cat)
        elif action == "get_open_orders":
            return {"orders": bot.get_open_orders(symbol, cat, limit or 50)}
        elif action == "get_order_history":
            return {"orders": bot.get_order_history(symbol, cat, limit or 50)}
        elif action == "get_positions":
            return {"positions": bot.get_positions(cat, symbol)}
        elif action == "get_wallet_balance":
            return bot.get_wallet_balance(account_type or "UNIFIED")
        elif action == "get_account_info":
            return bot.get_account_info()
        elif action == "get_fee_rates":
            return {"fee_rates": bot.get_fee_rates(symbol, cat)}
        elif action == "set_leverage":
            err = _require(("symbol", symbol), ("leverage", leverage))
            if err:
                return err
            return bot.set_leverage(symbol, leverage, cat)
        elif action == "set_trading_stop":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.set_trading_stop(
                symbol, stop_loss, take_profit, trailing_stop, cat
            )
        elif action == "get_ticker":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_ticker(symbol, cat)
        elif action == "get_tickers_bulk":
            err = _require(("symbols", symbols))
            if err:
                return err
            return {"tickers": bot.get_tickers_bulk(symbols, cat)}
        elif action == "get_orderbook":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_orderbook(symbol, limit or 25, cat)
        elif action == "get_klines":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "klines": bot.get_klines(symbol, interval or "1", limit or 200, cat)
            }
        elif action == "get_recent_trades":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {"trades": bot.get_recent_trades(symbol, limit or 500, cat)}
        elif action == "get_open_interest":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {
                "open_interest": bot.get_open_interest(
                    symbol, interval_time or "5min", cat, limit or 50
                )
            }
        elif action == "get_liquidations":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {"liquidations": bot.get_liquidations(symbol, cat, limit or 200)}
        elif action == "get_instruments_info":
            return {
                "instruments": bot.get_instruments_info(
                    cat, symbol, base_coin, status, limit or 100
                )
            }
        elif action == "get_spread_analysis":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_spread_analysis(symbol, depth or 5, cat)
        elif action == "get_technical_analysis":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_technical_analysis(
                symbol,
                interval or "15",
                rsi_period or 14,
                bb_period or 20,
                bb_std or 2.0,
                limit or 100,
                cat,
            )
        elif action == "get_market_momentum":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_market_momentum(
                symbol, cat, strong_threshold or 0.20, mild_threshold or 0.08
            ).to_dict()
        elif action == "get_funding_rate":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_funding_rate(symbol, cat)
        elif action == "get_mark_price":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {"symbol": symbol, "mark_price": bot.get_mark_price(symbol, cat)}
        elif action == "get_index_price":
            err = _require(("symbol", symbol))
            if err:
                return err
            return {"symbol": symbol, "index_price": bot.get_index_price(symbol, cat)}
        elif action == "calculate_sl_tp":
            err = _require(("side", side), ("price", price))
            if err:
                return err
            sl, tp = bot.calculate_sl_tp(price, OrderSide(side), sl_pct, tp_pct)
            return {
                "symbol": symbol,
                "entry_price": price,
                "side": side,
                "stop_loss": sl,
                "take_profit": tp,
            }
        elif action == "calculate_position_size":
            err = _require(
                ("symbol", symbol),
                ("price", price),
                ("sl_price", sl_price),
                ("risk_usdt", risk_usdt),
            )
            if err:
                return err
            return {
                "symbol": symbol,
                "entry_price": price,
                "sl_price": sl_price,
                "risk_usdt": risk_usdt,
                "quantity": bot.calculate_position_size(
                    symbol, price, sl_price, risk_usdt, cat
                ),
            }
        elif action == "calculate_atr_position_size":
            err = _require(("symbol", symbol), ("risk_usdt", risk_usdt))
            if err:
                return err
            return bot.calculate_atr_position_size(
                symbol, risk_usdt, atr_mult or 1.5, interval or "15", cat
            )
        elif action == "get_pnl_history":
            return {"pnl_history": bot.get_pnl_history(symbol, cat, limit or 100)}
        elif action == "get_pnl_report":
            err = _require(("symbol", symbol))
            if err:
                return err
            return bot.get_pnl_report(symbol, cat, limit or 100).to_dict()
        elif action == "batch_orders":
            err = _require(("orders", orders))
            if err:
                return err
            return bot.safe_execute(bot.execute_scalp_batch, orders)
        elif action == "iceberg_order":
            err = _require(
                ("symbol", symbol), ("side", side), ("qty", qty), ("price", price)
            )
            if err:
                return err
            return {
                "status": "ok",
                "iceberg_results": bot.place_iceberg_order(
                    symbol,
                    OrderSide(side),
                    qty,
                    price,
                    slices or 5,
                    cat,
                    stop_loss,
                    take_profit,
                    delay,
                ),
            }
        else:
            return {"status": "error", "msg": f"Unknown action: {action}"}
    except Exception as exc:
        logger.exception("run() unhandled exception for action=%s", action)
        return {"status": "error", "msg": str(exc)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bybit Trading Tool – CLI Interface")
    parser.add_argument("--action", required=True)
    parser.add_argument("--symbol")
    parser.add_argument("--side")
    parser.add_argument("--qty", type=float)
    parser.add_argument("--price", type=float)
    parser.add_argument("--order-type")
    parser.add_argument("--category", default="linear")
    parser.add_argument("--order-id")
    parser.add_argument("--stop-loss", type=float)
    parser.add_argument("--take-profit", type=float)
    parser.add_argument("--trailing-stop", type=float)
    parser.add_argument("--reduce-only", action="store_true")
    parser.add_argument("--time-in-force", default="GTC")
    parser.add_argument("--position-idx", type=int, default=0)
    parser.add_argument("--client-oid")
    parser.add_argument("--trigger-price", type=float)
    parser.add_argument("--trigger-by", default="LastPrice")
    parser.add_argument("--leverage", type=int)
    parser.add_argument("--buy-leverage", type=int)
    parser.add_argument("--sell-leverage", type=int)
    parser.add_argument("--account-type", default="UNIFIED")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--interval", default="1")
    parser.add_argument("--interval-time", default="5min")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--symbols")
    parser.add_argument("--base-coin")
    parser.add_argument("--status")
    parser.add_argument("--strong-threshold", type=float, default=0.20)
    parser.add_argument("--mild-threshold", type=float, default=0.08)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--bb-period", type=int, default=20)
    parser.add_argument("--bb-std", type=float, default=2.0)
    parser.add_argument("--atr-mult", type=float, default=1.5)
    parser.add_argument("--sl-pct", type=float)
    parser.add_argument("--tp-pct", type=float)
    parser.add_argument("--risk-usdt", type=float)
    parser.add_argument("--sl-price", type=float)
    parser.add_argument("--slices", type=int, default=5)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--output")
    parser.add_argument("--orders-file")

    args = parser.parse_args()

    orders_data = None
    if args.orders_file:
        with open(args.orders_file) as fh:
            orders_data = json.load(fh)

    symbols_list = None
    if getattr(args, "symbols", None):
        symbols_list = [s.strip() for s in args.symbols.split(",") if s.strip()]

    result = run(
        action=args.action,
        symbol=args.symbol,
        side=args.side,
        qty=args.qty,
        price=args.price,
        order_type=getattr(args, "order_type", None),
        category=args.category,
        order_id=getattr(args, "order_id", None),
        stop_loss=getattr(args, "stop_loss", None),
        take_profit=getattr(args, "take_profit", None),
        trailing_stop=getattr(args, "trailing_stop", None),
        reduce_only=getattr(args, "reduce_only", False),
        time_in_force=getattr(args, "time_in_force", "GTC"),
        position_idx=getattr(args, "position_idx", 0),
        client_oid=getattr(args, "client_oid", None),
        trigger_price=getattr(args, "trigger_price", None),
        trigger_by=getattr(args, "trigger_by", "LastPrice"),
        leverage=args.leverage,
        buy_leverage=getattr(args, "buy_leverage", None),
        sell_leverage=getattr(args, "sell_leverage", None),
        account_type=getattr(args, "account_type", "UNIFIED"),
        limit=args.limit,
        interval=args.interval,
        interval_time=getattr(args, "interval_time", "5min"),
        depth=getattr(args, "depth", 5),
        symbols=symbols_list,
        base_coin=getattr(args, "base_coin", None),
        status=getattr(args, "status", None),
        strong_threshold=getattr(args, "strong_threshold", 0.20),
        mild_threshold=getattr(args, "mild_threshold", 0.08),
        rsi_period=getattr(args, "rsi_period", 14),
        bb_period=getattr(args, "bb_period", 20),
        bb_std=getattr(args, "bb_std", 2.0),
        atr_mult=getattr(args, "atr_mult", 1.5),
        sl_pct=getattr(args, "sl_pct", None),
        tp_pct=getattr(args, "tp_pct", None),
        risk_usdt=getattr(args, "risk_usdt", None),
        sl_price=getattr(args, "sl_price", None),
        orders=orders_data,
        slices=args.slices,
        delay=args.delay,
    )

    output_path = args.output or os.environ.get("LLM_OUTPUT")
    if output_path:
        with open(output_path, "w") as fh:
            json.dump(result, fh, indent=2)
    else:
        print(json.dumps(result, indent=2))
