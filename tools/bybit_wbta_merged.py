"""
Market Trend Observatory v3.0 - Enhanced with L2 Orderbook Intelligence
═══════════════════════════════════════════════════════════════════════════════
Pure Analytical Engine | No Execution Layer | Read-Only Market Telemetry

New in v3.0:
  - L2 Orderbook depth analysis (bid/ask walls, imbalance, spread)
  - Order flow toxicity (VPIN proxy)
  - Liquidity heatmap (price levels with stacked liquidity)
  - Large order detection (iceberg/whale wall identification)
  - Bid/Ask cumulative depth curves
  - Recent trades flow (taker buy vs sell pressure)
  - Funding rate + open interest telemetry (perpetual futures)
  - Long/Short ratio from public endpoints
  - Liquidation level estimation
  - Market microstructure metrics (effective spread, mid-price, slippage est.)
  - All prior v2.0 indicators retained and intact
  - Zero execution code - pure analytical read-only feed
"""

import math
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure current directory is in sys.path to find proxy_utils
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    import proxy_utils

    proxy_utils.set_proxy_environment()
except Exception:
    pass

import numpy as np
import pandas as pd
import requests
from colorama import Fore, Style, init

# ── Initialize Colorama ──────────────────────────────────────────────────────
init(autoreset=True)

# ── Neon Palette ─────────────────────────────────────────────────────────────
NEON_CYAN = Fore.CYAN
NEON_GREEN = Fore.LIGHTGREEN_EX
NEON_YELLOW = Fore.YELLOW
NEON_RED = Fore.LIGHTRED_EX
NEON_PURPLE = Fore.MAGENTA
NEON_BLUE = Fore.BLUE
NEON_WHITE = Fore.WHITE
DIM = Style.DIM
BRIGHT = Style.BRIGHT
RESET = Style.RESET_ALL

# ── Layout Constants ──────────────────────────────────────────────────────────
BOX_WIDTH = 78
LABEL_W = 22
VALUE_W = 52


# ═════════════════════════════════════════════════════════════════════════════
# TOR PROXY MANAGER
# ═════════════════════════════════════════════════════════════════════════════


class TorProxyManager:
    """Manages Tor proxy connections for API requests."""

    def __init__(self, use_tor: bool = False):
        self.use_tor = use_tor
        self.tor_session = None
        self._setup_tor()

    def _setup_tor(self):
        """Setup Tor SOCKS5 proxy session."""
        if not self.use_tor:
            return

        # Check if already running via torsocks
        if "libtorsocks" in os.environ.get(
            "LD_PRELOAD", ""
        ) or "torsocks" in os.environ.get("LD_PRELOAD", ""):
            self.tor_session = requests.Session()
            # Test Tor connection
            try:
                resp = self.tor_session.get(
                    "https://api.ipify.org?format=json", timeout=5
                )
                if resp.status_code == 200:
                    print(
                        f"{NEON_GREEN}✓ Routed via torsocks: {resp.json().get('ip', 'unknown')}{RESET}"
                    )
            except Exception as e:
                print(f"{NEON_YELLOW}⚠ torsocks route test failed: {e}{RESET}")
            return

        # Attempt to re-exec with torsocks
        try:
            import subprocess

            if (
                subprocess.call(
                    ["which", "torsocks"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                == 0
            ):
                print(f"{NEON_GREEN}Re-executing process via torsocks...{RESET}")
                os.execvp("torsocks", ["torsocks", sys.executable] + sys.argv)
        except Exception:
            pass

        # Fallback to standard PySocks if torsocks re-exec failed or wasn't available
        try:
            # Create a SOCKS5 proxy session
            self.tor_session = requests.Session()
            self.tor_session.proxies = {
                "http": "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            }

            # Test Tor connection
            try:
                resp = self.tor_session.get(
                    "https://api.ipify.org?format=json", timeout=5
                )
                if resp.status_code == 200:
                    print(
                        f"{NEON_GREEN}✓ Tor proxy active: {resp.json().get('ip', 'unknown')}{RESET}"
                    )
            except Exception as e:
                print(f"{NEON_YELLOW}⚠ Tor proxy test failed: {e}{RESET}")

        except Exception as e:
            print(
                f"{NEON_YELLOW}⚠ SOCKS proxy support or PySocks missing, Tor disabled: {e}{RESET}"
            )
            self.use_tor = False
            self.tor_session = None

    def get_session(self) -> requests.Session:
        """Get appropriate session (Tor or regular)."""
        return (
            self.tor_session
            if self.use_tor and self.tor_session
            else requests.Session()
        )


# ═════════════════════════════════════════════════════════════════════════════
# BYBIT REALM API CLIENT (Integrated from tools/bbt.py)
# ═════════════════════════════════════════════════════════════════════════════


class BybitRealmClient:
    """Unified Entry Point — Bybit Realm v5.0 with Tor support."""

    def __init__(self, use_tor: bool = False):
        self.use_tor = use_tor
        self.tor_manager = TorProxyManager(use_tor)
        self.session = self.tor_manager.get_session()
        self.base_url = "https://api.bybit.com"
        self.backup_url = "https://api.bytick.com"

        # API credentials (optional for public endpoints)
        self.api_key = os.getenv("BYBIT_API_KEY", "")
        self.api_secret = os.getenv("BYBIT_API_SECRET", "")

        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.1  # 100ms minimum between requests

    def _rate_limit(self):
        """Simple rate limiter."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()

    def _get(self, endpoint: str, params: Dict = None) -> Dict:
        """Safe GET wrapper with timeout, error handling, and backup domain failover."""
        self._rate_limit()

        for attempt in range(2):
            try:
                url = f"{self.base_url}{endpoint}"
                resp = self.session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") == 0:
                    return data.get("result", {})
            except Exception:
                pass

            if self.base_url == "https://api.bybit.com":
                self.base_url = self.backup_url
            else:
                break

        return {}

    def get_ticker(self, symbol: str) -> Dict:
        """Get ticker data for a symbol."""
        return self.client._get(
            "/v5/market/tickers", {"category": "linear", "symbol": symbol.upper()}
        )

    def get_orderbook(self, symbol: str, depth: int = 50) -> Dict:
        """Fetch L2 orderbook snapshot."""
        return self.client._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol.upper(), "limit": depth},
        )

    def get_klines(
        self, symbol: str, interval: str = "15", limit: int = 300
    ) -> List[Dict]:
        """Fetch kline/candlestick data."""
        result = self.client._get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
        )
        return result.get("list", [])

    def get_recent_trades(self, symbol: str, limit: int = 200) -> List[Dict]:
        """Fetch recent public trades."""
        result = self.client._get(
            "/v5/market/recent-trade",
            {"category": "linear", "symbol": symbol.upper(), "limit": limit},
        )
        return result.get("list", [])

    def get_funding_rate(self, symbol: str) -> Dict:
        """Fetch current funding rate and predicted next funding."""
        result = self.client._get(
            "/v5/market/tickers", {"category": "linear", "symbol": symbol.upper()}
        )
        ticker_list = result.get("list", [{}])
        if not ticker_list:
            return {}
        t = ticker_list[0]

        def sf(v) -> float:
            if v is None or v == "":
                return 0.0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        return {
            "funding_rate": sf(t.get("fundingRate")),
            "next_funding_time": int(t.get("nextFundingTime", 0)),
            "open_interest": sf(t.get("openInterest")),
            "open_interest_val": sf(t.get("openInterestValue")),
            "turnover_24h": sf(t.get("turnover24h")),
            "volume_24h": sf(t.get("volume24h")),
            "high_24h": sf(t.get("highPrice24h")),
            "low_24h": sf(t.get("lowPrice24h")),
            "prev_price_24h": sf(t.get("prevPrice24h")),
            "mark_price": sf(t.get("markPrice")),
            "index_price": sf(t.get("indexPrice")),
            "bid1_price": sf(t.get("bid1Price")),
            "ask1_price": sf(t.get("ask1Price")),
        }

    def get_open_interest_history(
        self, symbol: str, interval: str = "5min", limit: int = 30
    ) -> List[Dict]:
        """Fetch historical open interest for trend analysis."""
        result = self.client._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol.upper(),
                "intervalTime": interval,
                "limit": limit,
            },
        )
        oi_list = result.get("list", [])
        parsed = []
        for item in oi_list:
            try:
                parsed.append(
                    {
                        "oi": float(item.get("openInterest", 0)),
                        "ts": int(item.get("timestamp", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return parsed

    def get_long_short_ratio(
        self, symbol: str, period: str = "5min", limit: int = 20
    ) -> List[Dict]:
        """Fetch global long/short account ratio history."""
        result = self.client._get(
            "/v5/market/account-ratio",
            {
                "category": "linear",
                "symbol": symbol.upper(),
                "period": period,
                "limit": limit,
            },
        )
        ls_list = result.get("list", [])
        parsed = []
        for item in ls_list:
            try:
                parsed.append(
                    {
                        "buy_ratio": float(item.get("buyRatio", 0)),
                        "sell_ratio": float(item.get("sellRatio", 0)),
                        "ts": int(item.get("timestamp", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return parsed


# ═════════════════════════════════════════════════════════════════════════════
# ORDERBOOK INTELLIGENCE ENGINE
# ═════════════════════════════════════════════════════════════════════════════


class OrderbookIntelligence:
    def __init__(self, symbol: str, client) -> None:
        self.symbol = symbol.upper()
        self.client = client

        # Rolling history for VPIN and flow tracking
        self._trade_buckets: deque = deque(maxlen=50)
        self._prev_oi: Optional[float] = None
        self._oi_history: deque = deque(maxlen=30)
        self._funding_history: deque = deque(maxlen=10)

    # ── Raw Data Fetchers ─────────────────────────────────────────────────────

    def fetch_orderbook(self, depth: int = 50) -> Dict:
        """
        Fetch L2 orderbook snapshot.
        depth=50 returns 50 best bids and 50 best asks.
        """
        result = self.client._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": self.symbol, "limit": depth},
        )
        if not result:
            return {}

        bids_raw = result.get("b", [])
        asks_raw = result.get("a", [])

        # Convert to float arrays [[price, size], ...]
        bids = [[float(p), float(s)] for p, s in bids_raw]
        asks = [[float(p), float(s)] for p, s in asks_raw]

        # Sort: bids descending (best bid first), asks ascending (best ask first)
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        return {
            "bids": bids,
            "asks": asks,
            "ts": result.get("ts", 0),
            "update_id": result.get("u", 0),
        }

    def fetch_recent_trades(self, limit: int = 200) -> List[Dict]:
        """
        Fetch recent public trades.
        Returns list of {price, size, side, timestamp}.
        """
        result = self.client._get(
            "/v5/market/recent-trade",
            {"category": "linear", "symbol": self.symbol, "limit": limit},
        )
        trades = result.get("list", [])
        parsed = []
        for t in trades:
            try:
                parsed.append(
                    {
                        "price": float(t.get("price", 0)),
                        "size": float(t.get("size", 0)),
                        "side": str(t.get("side", "Buy")),
                        "ts": int(t.get("time", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return parsed

    def fetch_funding_rate(self) -> Dict:
        """Fetch current funding rate and predicted next funding."""
        result = self.client._get(
            "/v5/market/tickers",
            {"category": "linear", "symbol": self.symbol},
        )
        ticker_list = result.get("list", [{}])
        if not ticker_list:
            return {}
        t = ticker_list[0]

        def sf(v) -> float:
            if v is None or v == "":
                return 0.0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        def si(v) -> int:
            if v is None or v == "":
                return 0
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        return {
            "funding_rate": sf(t.get("fundingRate")),
            "next_funding_time": si(t.get("nextFundingTime")),
            "predicted_rate": sf(t.get("predictedDeliveryPrice")),
            "open_interest": sf(t.get("openInterest")),
            "open_interest_val": sf(t.get("openInterestValue")),
            "turnover_24h": sf(t.get("turnover24h")),
            "volume_24h": sf(t.get("volume24h")),
            "high_24h": sf(t.get("highPrice24h")),
            "low_24h": sf(t.get("lowPrice24h")),
            "prev_price_24h": sf(t.get("prevPrice24h")),
            "mark_price": sf(t.get("markPrice")),
            "index_price": sf(t.get("indexPrice")),
            "bid1_price": sf(t.get("bid1Price")),
            "ask1_price": sf(t.get("ask1Price")),
        }

    def fetch_open_interest_history(
        self, interval: str = "5min", limit: int = 30
    ) -> List[Dict]:
        """
        Fetch historical open interest for trend analysis.
        interval options: 5min, 15min, 30min, 1h, 4h, 1d
        """
        result = self.client._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": self.symbol,
                "intervalTime": interval,
                "limit": limit,
            },
        )
        oi_list = result.get("list", [])
        parsed = []
        for item in oi_list:
            try:
                parsed.append(
                    {
                        "oi": float(item.get("openInterest", 0)),
                        "ts": int(item.get("timestamp", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return parsed

    def fetch_long_short_ratio(
        self, period: str = "5min", limit: int = 20
    ) -> List[Dict]:
        """
        Fetch global long/short account ratio history.
        period: 5min, 15min, 30min, 1h, 4h, 1d
        """
        result = self.client._get(
            "/v5/market/account-ratio",
            {
                "category": "linear",
                "symbol": self.symbol,
                "period": period,
                "limit": limit,
            },
        )
        ls_list = result.get("list", [])
        parsed = []
        for item in ls_list:
            try:
                parsed.append(
                    {
                        "buy_ratio": float(item.get("buyRatio", 0)),
                        "sell_ratio": float(item.get("sellRatio", 0)),
                        "ts": int(item.get("timestamp", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return parsed

    # ── Orderbook Analysis Calculations ──────────────────────────────────────

    def analyze_orderbook(self, ob: Dict) -> Dict[str, Any]:
        """
        Derive microstructure metrics from the raw L2 orderbook snapshot.
        All values are pure calculations from public price/size data.
        """
        if not ob or not ob.get("bids") or not ob.get("asks"):
            return self._empty_ob_metrics()

        bids = ob["bids"]  # [[price, size], ...] descending
        asks = ob["asks"]  # [[price, size], ...] ascending

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid_price = (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else 0.0

        # ── Spread Analysis ───────────────────────────────────────────────
        spread_abs = best_ask - best_bid
        spread_bps = (spread_abs / mid_price * 10000.0) if mid_price else 0.0

        # ── Depth Aggregation ─────────────────────────────────────────────
        bid_prices = np.array([b[0] for b in bids])
        bid_sizes = np.array([b[1] for b in bids])
        ask_prices = np.array([a[0] for a in asks])
        ask_sizes = np.array([a[1] for a in asks])

        total_bid_vol = float(bid_sizes.sum())
        total_ask_vol = float(ask_sizes.sum())
        total_vol = total_bid_vol + total_ask_vol

        # ── Bid/Ask Imbalance ─────────────────────────────────────────────
        # Range: -1.0 (fully ask-heavy) to +1.0 (fully bid-heavy)
        ob_imbalance = (
            (total_bid_vol - total_ask_vol) / total_vol if total_vol > 0 else 0.0
        )

        # ── Cumulative Depth at Distance Levels ───────────────────────────
        # How much liquidity exists within 0.1%, 0.5%, 1.0% of mid-price
        depth_levels = {}
        for pct in [0.1, 0.5, 1.0, 2.0]:
            bid_thresh = mid_price * (1.0 - pct / 100.0)
            ask_thresh = mid_price * (1.0 + pct / 100.0)
            bid_depth = float(bid_sizes[bid_prices >= bid_thresh].sum())
            ask_depth = float(ask_sizes[ask_prices <= ask_thresh].sum())
            depth_levels[f"bid_depth_{pct}pct"] = bid_depth
            depth_levels[f"ask_depth_{pct}pct"] = ask_depth

        # ── Whale Wall Detection ──────────────────────────────────────────
        # Identify orders significantly larger than average (3x mean = wall)
        bid_mean = float(bid_sizes.mean()) if len(bid_sizes) > 0 else 1.0
        ask_mean = float(ask_sizes.mean()) if len(ask_sizes) > 0 else 1.0
        wall_threshold = 3.0

        bid_walls = [
            {"price": bids[i][0], "size": bids[i][1]}
            for i in range(len(bids))
            if bids[i][1] >= bid_mean * wall_threshold
        ]
        ask_walls = [
            {"price": asks[i][0], "size": asks[i][1]}
            for i in range(len(asks))
            if asks[i][1] >= ask_mean * wall_threshold
        ]

        # Sort by size descending - largest walls first
        bid_walls.sort(key=lambda x: x["size"], reverse=True)
        ask_walls.sort(key=lambda x: x["size"], reverse=True)

        # ── Weighted Mid Price (micro-price) ──────────────────────────────
        # Accounts for queue imbalance at best bid/ask
        best_bid_sz = bids[0][1] if bids else 1.0
        best_ask_sz = asks[0][1] if asks else 1.0
        micro_price = (
            (best_bid * best_ask_sz + best_ask * best_bid_sz)
            / (best_bid_sz + best_ask_sz)
            if (best_bid_sz + best_ask_sz) > 0
            else mid_price
        )

        # ── Slippage Estimation ───────────────────────────────────────────
        # Cost to market-buy/sell a notional amount through the book ($10,000 USDT)
        slippage_notional = 10000.0
        slip_buy = self._calc_slippage(asks, slippage_notional, mid_price)
        slip_sell = self._calc_slippage(bids, slippage_notional, mid_price, side="sell")

        # ── Price Impact Curve ────────────────────────────────────────────
        # % price move for each $100k of notional executed
        impact_buy_100k = self._price_impact_pct(asks, 100_000.0, mid_price)
        impact_sell_100k = self._price_impact_pct(
            bids, 100_000.0, mid_price, side="sell"
        )

        # ── Orderbook Absorption Level ────────────────────────────────────
        # Nearest level where cumulative depth exceeds a threshold
        # (signals strong support/resistance)
        support_level = self._find_absorption_level(bids, mid_price, threshold_mult=5.0)
        resistance_level = self._find_absorption_level(
            asks, mid_price, threshold_mult=5.0
        )

        # ── Ask/Bid Depth Ratio at 1% ─────────────────────────────────────
        ask_1pct = depth_levels.get("ask_depth_1.0pct", 1.0)
        bid_1pct = depth_levels.get("bid_depth_1.0pct", 1.0)
        depth_ratio_1pct = bid_1pct / ask_1pct if ask_1pct > 0 else 1.0

        # ── Orderbook Volume Profile Support / Resistance ──────────────────
        bin_width = max(0.0001, mid_price * 0.001)
        bid_bins = {}
        for price, size in bids:
            bin_price = round(price / bin_width) * bin_width
            bid_bins[bin_price] = bid_bins.get(bin_price, 0.0) + size
        ask_bins = {}
        for price, size in asks:
            bin_price = round(price / bin_width) * bin_width
            ask_bins[bin_price] = ask_bins.get(bin_price, 0.0) + size
        ob_supports = sorted(
            [{"price": p, "volume": v} for p, v in bid_bins.items()],
            key=lambda x: x["volume"],
            reverse=True,
        )[:3]
        ob_resistances = sorted(
            [{"price": p, "volume": v} for p, v in ask_bins.items()],
            key=lambda x: x["volume"],
            reverse=True,
        )[:3]
        ob_supports.sort(key=lambda x: x["price"], reverse=True)
        ob_resistances.sort(key=lambda x: x["price"])

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "micro_price": micro_price,
            "spread_abs": spread_abs,
            "spread_bps": spread_bps,
            "total_bid_vol": total_bid_vol,
            "total_ask_vol": total_ask_vol,
            "ob_imbalance": ob_imbalance,
            "depth_levels": depth_levels,
            "bid_walls": bid_walls[:3],
            "ask_walls": ask_walls[:3],
            "slip_buy_bps": slip_buy,
            "slip_sell_bps": slip_sell,
            "impact_buy_100k": impact_buy_100k,
            "impact_sell_100k": impact_sell_100k,
            "support_level": support_level,
            "resistance_level": resistance_level,
            "depth_ratio_1pct": depth_ratio_1pct,
            "bid_walls_count": len(bid_walls),
            "ask_walls_count": len(ask_walls),
            "ob_supports": ob_supports,
            "ob_resistances": ob_resistances,
        }

    @staticmethod
    def _calc_slippage(
        levels: List,
        notional: float,
        mid_price: float,
        side: str = "buy",
    ) -> float:
        """
        Walk the book to calculate average fill price vs mid.
        Returns slippage in basis points.
        """
        remaining = notional
        total_cost = 0.0
        total_qty = 0.0

        for price, size in levels:
            if remaining <= 0:
                break
            qty = min(size, remaining / price)
            cost = qty * price
            total_qty += qty
            total_cost += cost
            remaining -= cost

        if total_qty == 0 or mid_price == 0:
            return 0.0

        avg_fill = total_cost / total_qty
        slip = abs(avg_fill - mid_price) / mid_price * 10000.0
        return round(slip, 2)

    @staticmethod
    def _price_impact_pct(
        levels: List,
        notional: float,
        mid_price: float,
        side: str = "buy",
    ) -> float:
        """
        Estimate percentage price movement from executing a notional amount.
        Returns impact percentage.
        """
        remaining = notional
        last_price = mid_price

        for price, size in levels:
            if remaining <= 0:
                break
            cost = size * price
            remaining -= cost
            last_price = price

        if mid_price == 0:
            return 0.0
        return abs(last_price - mid_price) / mid_price * 100.0

    @staticmethod
    def _find_absorption_level(
        levels: List,
        mid_price: float,
        threshold_mult: float = 5.0,
    ) -> float:
        """
        Find the first price level where cumulative size exceeds
        the average size by threshold_mult.
        This identifies significant support/resistance clusters.
        """
        if not levels:
            return 0.0
        sizes = [l[1] for l in levels]
        avg_size = np.mean(sizes) if sizes else 1.0
        threshold = avg_size * threshold_mult

        cumulative = 0.0
        for price, size in levels:
            cumulative += size
            if cumulative >= threshold:
                return float(price)
        return float(levels[-1][0]) if levels else 0.0

    @staticmethod
    def _empty_ob_metrics() -> Dict:
        """Return zeroed structure when orderbook fetch fails."""
        return {
            "best_bid": 0.0,
            "best_ask": 0.0,
            "mid_price": 0.0,
            "micro_price": 0.0,
            "spread_abs": 0.0,
            "spread_bps": 0.0,
            "total_bid_vol": 0.0,
            "total_ask_vol": 0.0,
            "ob_imbalance": 0.0,
            "depth_levels": {},
            "bid_walls": [],
            "ask_walls": [],
            "slip_buy_bps": 0.0,
            "slip_sell_bps": 0.0,
            "impact_buy_100k": 0.0,
            "impact_sell_100k": 0.0,
            "support_level": 0.0,
            "resistance_level": 0.0,
            "depth_ratio_1pct": 1.0,
            "bid_walls_count": 0,
            "ask_walls_count": 0,
            "ob_supports": [],
            "ob_resistances": [],
        }

    # ── Trade Flow Analysis ───────────────────────────────────────────────────

    def analyze_trades(self, trades: List[Dict]) -> Dict[str, Any]:
        """
        Analyze recent public trades for flow imbalance, aggressor dominance,
        large trade detection, and VPIN proxy calculation.
        """
        if not trades:
            return self._empty_trade_metrics()

        df = pd.DataFrame(trades)
        df["is_buy"] = df["side"].str.upper() == "BUY"

        total_vol = float(df["size"].sum())
        buy_vol = float(df.loc[df["is_buy"], "size"].sum())
        sell_vol = float(df.loc[~df["is_buy"], "size"].sum())

        # ── Taker Flow Imbalance ──────────────────────────────────────────
        # +1.0 = all taker buys; -1.0 = all taker sells
        flow_imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0

        # ── Large Trade Filter ────────────────────────────────────────────
        # Trades > 2 standard deviations above mean size
        size_mean = df["size"].mean()
        size_std = df["size"].std()
        large_thresh = size_mean + 2.0 * size_std
        large_trades = df[df["size"] >= large_thresh]
        large_buy_vol = float(large_trades.loc[large_trades["is_buy"], "size"].sum())
        large_sell_vol = float(large_trades.loc[~large_trades["is_buy"], "size"].sum())
        large_total = large_buy_vol + large_sell_vol
        large_flow_imb = (
            (large_buy_vol - large_sell_vol) / large_total if large_total > 0 else 0.0
        )

        # ── Trade Frequency ───────────────────────────────────────────────
        trade_count = len(df)
        avg_trade_size = total_vol / trade_count if trade_count > 0 else 0.0
        largest_trade = float(df["size"].max())

        # ── VPIN Proxy ────────────────────────────────────────────────────
        # Volume-Synchronized Probability of Informed Trading
        # Groups trades into 10 constant-volume buckets
        if total_vol > 0:
            v_bucket_size = total_vol / 10.0
            vpin_values = []
            current_bv = 0.0
            current_sv = 0.0
            for _, row in df.iterrows():
                sz = float(row["size"])
                is_b = bool(row["is_buy"])
                if is_b:
                    current_bv += sz
                else:
                    current_sv += sz
                if (current_bv + current_sv) >= v_bucket_size:
                    tv = current_bv + current_sv
                    vpin_values.append(
                        abs(current_bv - current_sv) / tv if tv > 0 else 0.0
                    )
                    current_bv = 0.0
                    current_sv = 0.0
            if (current_bv + current_sv) > 0:
                tv = current_bv + current_sv
                vpin_values.append(abs(current_bv - current_sv) / tv)
            vpin_proxy = float(np.mean(vpin_values)) if vpin_values else 0.0
        else:
            vpin_proxy = 0.0

        # ── Recent Price Velocity from Trades ─────────────────────────────
        if len(df) >= 10:
            recent_5 = df.tail(5)["price"].mean()
            earlier_5 = df.head(5)["price"].mean()
            price_velo = (
                (recent_5 - earlier_5) / earlier_5 * 100.0 if earlier_5 else 0.0
            )
        else:
            price_velo = 0.0

        # ── Aggressor Ratio (last 20 trades) ─────────────────────────────
        recent_20 = df.tail(20)
        recent_buy_v = float(recent_20.loc[recent_20["is_buy"], "size"].sum())
        recent_sell_v = float(recent_20.loc[~recent_20["is_buy"], "size"].sum())
        recent_total = recent_buy_v + recent_sell_v
        aggressor_ratio = (
            (recent_buy_v - recent_sell_v) / recent_total if recent_total > 0 else 0.0
        )

        return {
            "total_vol": total_vol,
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "flow_imbalance": flow_imbalance,
            "large_flow_imb": large_flow_imb,
            "large_buy_vol": large_buy_vol,
            "large_sell_vol": large_sell_vol,
            "large_trade_count": len(large_trades),
            "avg_trade_size": avg_trade_size,
            "largest_trade": largest_trade,
            "trade_count": trade_count,
            "vpin_proxy": vpin_proxy,
            "price_velocity": price_velo,
            "aggressor_ratio": aggressor_ratio,
        }

    @staticmethod
    def _empty_trade_metrics() -> Dict:
        return {
            "total_vol": 0.0,
            "buy_vol": 0.0,
            "sell_vol": 0.0,
            "flow_imbalance": 0.0,
            "large_flow_imb": 0.0,
            "large_buy_vol": 0.0,
            "large_sell_vol": 0.0,
            "large_trade_count": 0,
            "avg_trade_size": 0.0,
            "largest_trade": 0.0,
            "trade_count": 0,
            "vpin_proxy": 0.0,
            "price_velocity": 0.0,
            "aggressor_ratio": 0.0,
        }

    # ── Funding & OI Analysis ─────────────────────────────────────────────────

    def analyze_funding_oi(
        self,
        ticker: Dict,
        oi_history: List[Dict],
        ls_ratio: List[Dict],
        close_price: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Derive sentiment and positioning signals from funding rates,
        open interest trends, and long/short ratios.
        """
        result: Dict[str, Any] = {}
        result["close_price"] = close_price

        # ── Funding Rate Analysis ─────────────────────────────────────────
        fr = ticker.get("funding_rate", 0.0)
        result["funding_rate"] = fr
        result["funding_rate_pct"] = fr * 100.0
        result["funding_annualized"] = fr * 3 * 365 * 100.0  # 3x/day * 365

        next_ts = ticker.get("next_funding_time", 0)
        if next_ts > 0:
            now_ms = int(time.time() * 1000)
            mins_to_funding = max(0, (next_ts - now_ms) // 60000)
            result["mins_to_funding"] = int(mins_to_funding)
        else:
            result["mins_to_funding"] = -1

        # Funding sentiment: positive = longs pay shorts (bearish pressure)
        if fr > 0.001:
            result["funding_sentiment"] = "LONGS PAY  (Bearish Pressure)"
            result["funding_bias"] = "BEARISH"
        elif fr < -0.001:
            result["funding_sentiment"] = "SHORTS PAY (Bullish Pressure)"
            result["funding_bias"] = "BULLISH"
        else:
            result["funding_sentiment"] = "NEUTRAL"
            result["funding_bias"] = "NEUTRAL"

        # ── Open Interest ─────────────────────────────────────────────────
        current_oi = ticker.get("open_interest", 0.0)
        result["open_interest"] = current_oi
        result["open_interest_value"] = ticker.get("open_interest_val", 0.0)

        if oi_history and len(oi_history) >= 2:
            oi_vals = [x["oi"] for x in oi_history]
            oi_arr = np.array(oi_vals, dtype=float)
            oi_change = (
                (oi_arr[0] - oi_arr[-1]) / oi_arr[-1] * 100.0 if oi_arr[-1] else 0.0
            )
            oi_trend = float(np.polyfit(range(len(oi_arr)), oi_arr, 1)[0])

            result["oi_change_pct"] = oi_change
            result["oi_trend"] = oi_trend  # positive = OI growing
            result["oi_max"] = float(oi_arr.max())
            result["oi_min"] = float(oi_arr.min())
        else:
            result["oi_change_pct"] = 0.0
            result["oi_trend"] = 0.0
            result["oi_max"] = current_oi
            result["oi_min"] = current_oi

        # ── OI + Price Divergence Signal ──────────────────────────────────
        # Rising price + rising OI   = trend confirmation (strong)
        # Rising price + falling OI  = potential reversal (weak)
        # Falling price + rising OI  = potential reversal (short squeeze risk)
        # Falling price + falling OI = trend continuation (distribution)
        result["oi_price_signal"] = "N/A"  # Set below in combine step

        # ── Long/Short Ratio ──────────────────────────────────────────────
        if ls_ratio:
            latest_ls = ls_ratio[0]
            buy_ratio = latest_ls.get("buy_ratio", 0.5)
            sell_ratio = latest_ls.get("sell_ratio", 0.5)
            result["ls_buy_ratio"] = buy_ratio
            result["ls_sell_ratio"] = sell_ratio
            result["ls_net"] = buy_ratio - sell_ratio

            if len(ls_ratio) >= 3:
                recent_buy = np.mean([x["buy_ratio"] for x in ls_ratio[:3]])
                older_buy = np.mean([x["buy_ratio"] for x in ls_ratio[-3:]])
                result["ls_trend"] = (
                    recent_buy - older_buy
                )  # positive = more longs recently
            else:
                result["ls_trend"] = 0.0

            # Contrarian note: extreme long ratio can signal overleveraged longs
            if buy_ratio > 0.65:
                result["ls_sentiment"] = "CROWDED LONGS  (Contrarian ↓ Risk)"
            elif sell_ratio > 0.65:
                result["ls_sentiment"] = "CROWDED SHORTS (Contrarian ↑ Risk)"
            else:
                result["ls_sentiment"] = "BALANCED"
        else:
            result["ls_buy_ratio"] = 0.5
            result["ls_sell_ratio"] = 0.5
            result["ls_net"] = 0.0
            result["ls_trend"] = 0.0
            result["ls_sentiment"] = "N/A"

        # ── 24h Market Summary ────────────────────────────────────────────
        result["volume_24h"] = ticker.get("volume_24h", 0.0)
        result["turnover_24h"] = ticker.get("turnover_24h", 0.0)
        result["high_24h"] = ticker.get("high_24h", 0.0)
        result["low_24h"] = ticker.get("low_24h", 0.0)
        result["prev_price_24h"] = ticker.get("prev_price_24h", 0.0)
        result["mark_price"] = ticker.get("mark_price", 0.0)
        result["index_price"] = ticker.get("index_price", 0.0)

        # Mark vs Index spread (basis)
        mark = result["mark_price"]
        index = result["index_price"]
        result["mark_index_basis"] = (
            (mark - index) / index * 100.0 if index > 0 else 0.0
        )

        # ── Liquidation Estimation (proximity) ───────────────────────────
        # Estimate liquidation cascade zones using 10x and 20x leverage positions with MMR
        # Maintenance Margin Rate: ~0.5% for 10x, ~1.0% for 20x
        mark_p = (
            result["mark_price"]
            if result["mark_price"] > 0
            else (close_price if close_price > 0 else 1.0)
        )
        mmr_10x = 0.005
        mmr_20x = 0.010
        result["liq_est_long_10x"] = mark_p * (1.0 - 1.0 / 10.0 + mmr_10x)
        result["liq_est_short_10x"] = mark_p * (1.0 + 1.0 / 10.0 - mmr_10x)
        result["liq_est_long_20x"] = mark_p * (1.0 - 1.0 / 20.0 + mmr_20x)
        result["liq_est_short_20x"] = mark_p * (1.0 + 1.0 / 20.0 - mmr_20x)

        return result

    # ── Composite L2 Decision Matrix ──────────────────────────────────────────

    @staticmethod
    def l2_signal_score(
        ob_metrics: Dict,
        trade_metrics: Dict,
        funding_oi: Dict,
        close_price: float,
    ) -> Tuple[int, int, str, List[str]]:
        """
        Generate a composite L2 signal score from all microstructure data.
        Returns (bull_votes, bear_votes, label, [reasoning strings])
        """
        bulls = 0
        bears = 0
        notes = []

        # ── Orderbook Signals ─────────────────────────────────────────────
        ob_imb = ob_metrics.get("ob_imbalance", 0.0)
        if ob_imb > 0.15:
            bulls += 2
            notes.append(f"OB Imbalance: BID-HEAVY {ob_imb:+.2f} (buy pressure)")
        elif ob_imb < -0.15:
            bears += 2
            notes.append(f"OB Imbalance: ASK-HEAVY {ob_imb:+.2f} (sell pressure)")

        depth_ratio = ob_metrics.get("depth_ratio_1pct", 1.0)
        if depth_ratio > 1.3:
            bulls += 1
            notes.append(f"Depth Ratio 1%: Bids {depth_ratio:.2f}x deeper than asks")
        elif depth_ratio < 0.7:
            bears += 1
            notes.append(
                f"Depth Ratio 1%: Asks {1 / depth_ratio:.2f}x deeper than bids"
            )

        # Large walls
        bid_walls = ob_metrics.get("bid_walls", [])
        ask_walls = ob_metrics.get("ask_walls", [])
        if bid_walls:
            w = bid_walls[0]
            notes.append(f"Bid Wall: {w['size']:.1f} @ {w['price']:.2f} (support)")
            if w["price"] < close_price:
                bulls += 1
        if ask_walls:
            w = ask_walls[0]
            notes.append(f"Ask Wall: {w['size']:.1f} @ {w['price']:.2f} (resistance)")
            if w["price"] > close_price:
                bears += 1

        spread_bps = ob_metrics.get("spread_bps", 0.0)
        if spread_bps < 2.0:
            bulls += 1
            notes.append(f"Spread: {spread_bps:.2f}bps - Tight (high liquidity)")
        elif spread_bps > 10.0:
            bears += 1
            notes.append(f"Spread: {spread_bps:.2f}bps - Wide (low liquidity / risk)")

        # ── Trade Flow Signals ────────────────────────────────────────────
        flow_imb = trade_metrics.get("flow_imbalance", 0.0)
        if flow_imb > 0.2:
            bulls += 2
            notes.append(f"Trade Flow: {flow_imb:+.2f} Taker Buy Dominated")
        elif flow_imb < -0.2:
            bears += 2
            notes.append(f"Trade Flow: {flow_imb:+.2f} Taker Sell Dominated")

        large_imb = trade_metrics.get("large_flow_imb", 0.0)
        if large_imb > 0.3:
            bulls += 2
            notes.append(f"Large Trades: Whale Buy Pressure {large_imb:+.2f}")
        elif large_imb < -0.3:
            bears += 2
            notes.append(f"Large Trades: Whale Sell Pressure {large_imb:+.2f}")

        vpin = trade_metrics.get("vpin_proxy", 0.0)
        if vpin > 0.4:
            notes.append(f"VPIN: {vpin:.3f} - ELEVATED (informed trading risk)")
        else:
            notes.append(f"VPIN: {vpin:.3f} - Normal noise level")

        agg_ratio = trade_metrics.get("aggressor_ratio", 0.0)
        if agg_ratio > 0.25:
            bulls += 1
            notes.append(f"Aggressor: {agg_ratio:+.2f} Recent taker buying")
        elif agg_ratio < -0.25:
            bears += 1
            notes.append(f"Aggressor: {agg_ratio:+.2f} Recent taker selling")

        vel = trade_metrics.get("price_velocity", 0.0)
        if vel > 0.05:
            bulls += 1
            notes.append(f"Price Velocity: +{vel:.4f}% (upward momentum in trades)")
        elif vel < -0.05:
            bears += 1
            notes.append(f"Price Velocity: {vel:.4f}% (downward momentum in trades)")

        # ── Funding & OI Signals ──────────────────────────────────────────
        fr = funding_oi.get("funding_rate", 0.0)
        if fr < -0.0005:
            bulls += 1
            notes.append(f"Funding: {fr * 100:.4f}% Negative (shorts paying = bullish)")
        elif fr > 0.001:
            bears += 1
            notes.append(f"Funding: {fr * 100:.4f}% High Positive (longs overcrowded)")

        oi_trend = funding_oi.get("oi_trend", 0.0)
        if oi_trend > 0:
            notes.append("OI Trend: Growing (new money entering market)")
        else:
            notes.append("OI Trend: Shrinking (positions being closed)")

        ls_net = funding_oi.get("ls_net", 0.0)
        if ls_net > 0.1:
            bears += 1  # Contrarian: crowded longs = fade signal
            notes.append(
                f"L/S Ratio: Longs dominant {ls_net:+.2f} (contrarian bearish)"
            )
        elif ls_net < -0.1:
            bulls += 1  # Contrarian: crowded shorts = squeeze potential
            notes.append(f"L/S Ratio: Shorts dominant {ls_net:+.2f} (squeeze risk)")

        basis = funding_oi.get("mark_index_basis", 0.0)
        if abs(basis) > 0.1:
            notes.append(f"Mark/Index Basis: {basis:+.4f}% (futures premium/discount)")

        # ── Composite Label ───────────────────────────────────────────────
        total = bulls + bears
        ratio = bulls / total if total > 0 else 0.5
        if ratio >= 0.70:
            label = "STRONG BULL FLOW"
        elif ratio >= 0.55:
            label = "MILD BULL FLOW"
        elif ratio <= 0.30:
            label = "STRONG BEAR FLOW"
        elif ratio <= 0.45:
            label = "MILD BEAR FLOW"
        else:
            label = "NEUTRAL FLOW"

        return bulls, bears, label, notes


# ═════════════════════════════════════════════════════════════════════════════
#  TECHNICAL OBSERVATORY - Indicator Calculation Engine (v2.0 Complete)
# ═════════════════════════════════════════════════════════════════════════════


class TechnicalObservatory:
    """
    Pure mathematical derivation of 35+ technical indicators.
    All algorithms use native Pandas/NumPy vector operations only.
    """

    def __init__(self, symbol: str, interval: str) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.base_url = "https://api.bybit.com"
        self._prev_close: Optional[float] = None

        # Load parameters from config file
        self.config = {}
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )
        if os.path.exists(config_path):
            try:
                import json

                with open(config_path) as f:
                    full_config = json.load(f)
                    self.config = full_config.get("indicator_settings", {})
            except Exception:
                pass

    def fetch_klines(self, limit: int = 300) -> pd.DataFrame:
        endpoint = "/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": self.symbol,
            "interval": self.interval,
            "limit": limit,
        }
        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{self.base_url}{endpoint}", params=params, timeout=15
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") != 0:
                    raise ValueError(f"Exchange error: {data.get('retMsg')}")
                raw = data["result"]["list"]
                if not raw:
                    raise ValueError("No kline candles returned.")
                df = pd.DataFrame(
                    raw,
                    columns=[
                        "start_time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "turnover",
                    ],
                )
                for col in ["open", "high", "low", "close", "volume", "turnover"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.iloc[::-1].reset_index(drop=True)
                return df
            except Exception as exc:
                if self.base_url == "https://api.bybit.com" and attempt == 0:
                    self.base_url = "https://api.bytick.com"
                    continue
                raise RuntimeError(f"API fetch error: {exc}") from exc

    # ── Primitives ────────────────────────────────────────────────────────────

    def _wma(self, s: pd.Series, p: int) -> pd.Series:
        w = np.arange(1, p + 1, dtype=float)
        return s.rolling(p).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)

    def _ema(self, s: pd.Series, span: int) -> pd.Series:
        return s.ewm(span=span, adjust=False).mean()

    def _rma(self, s: pd.Series, p: int) -> pd.Series:
        return s.ewm(alpha=1.0 / p, adjust=False).mean()

    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        h, l, c = df["high"], df["low"], df["close"]
        return pd.concat(
            [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
        ).max(axis=1)

    def _calculate_hma(self, s: pd.Series, p: int) -> pd.Series:
        return self._wma(
            2.0 * self._wma(s, max(1, p // 2)) - self._wma(s, p),
            max(1, int(np.sqrt(p))),
        )

    def _calculate_dema(self, s: pd.Series, p: int) -> pd.Series:
        e1 = self._ema(s, p)
        return 2.0 * e1 - self._ema(e1, p)

    def _calculate_tema(self, s: pd.Series, p: int) -> pd.Series:
        e1 = self._ema(s, p)
        e2 = self._ema(e1, p)
        e3 = self._ema(e2, p)
        return 3.0 * e1 - 3.0 * e2 + e3

    def _calculate_kama(
        self, s: pd.Series, p: int = 10, fast: int = 2, slow: int = 30
    ) -> pd.Series:
        n = len(s)
        arr = s.to_numpy(dtype=float)
        k = np.full(n, np.nan)
        fa = 2.0 / (fast + 1)
        sa = 2.0 / (slow + 1)
        if n <= p:
            return pd.Series(k, index=s.index)
        k[p] = float(np.nanmean(arr[:p]))
        for i in range(p + 1, n):
            ch = abs(arr[i] - arr[i - p])
            path = np.sum(np.abs(np.diff(arr[i - p : i + 1])))
            er = ch / path if path else 0.0
            sc = (er * (fa - sa) + sa) ** 2
            k[i] = k[i - 1] + sc * (arr[i] - k[i - 1])
        return pd.Series(k, index=s.index)

    def _calculate_rsi(self, s: pd.Series, p: int = 14) -> pd.Series:
        d = s.diff()
        g = d.where(d > 0, 0.0)
        l = (-d).where(d < 0, 0.0)
        rs = self._rma(g, p) / self._rma(l, p).replace(0.0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs.fillna(0.0)))

    def _calculate_roc(self, s: pd.Series, p: int = 12) -> pd.Series:
        return ((s - s.shift(p)) / s.shift(p).replace(0.0, np.nan)) * 100.0

    def _calculate_dpo(self, s: pd.Series, p: int = 20) -> pd.Series:
        return s - s.rolling(p).mean().shift(p // 2 + 1)

    def _calculate_trix(self, s: pd.Series, p: int = 15) -> pd.Series:
        e1 = self._ema(s, p)
        e2 = self._ema(e1, p)
        e3 = self._ema(e2, p)
        return e3.pct_change() * 100.0

    def _calculate_cmo(self, s: pd.Series, p: int = 14) -> pd.Series:
        d = s.diff()
        us = d.where(d > 0, 0.0).rolling(p).sum()
        ds = (-d).where(d < 0, 0.0).rolling(p).sum()
        return 100.0 * (us - ds) / (us + ds).replace(0.0, np.nan)

    def _calculate_coppock(
        self, s: pd.Series, w: int = 10, r1: int = 14, r2: int = 11
    ) -> pd.Series:
        return self._wma(self._calculate_roc(s, r1) + self._calculate_roc(s, r2), w)

    def _calculate_keltner(self, df, ep: int = 20, ap: int = 10, mult: float = 2.0):
        atr = self._rma(self._true_range(df), ap)
        mid = self._ema(df["close"], ep)
        return mid + mult * atr, mid, mid - mult * atr

    def _calculate_ulcer(self, s: pd.Series, p: int = 14) -> pd.Series:
        rm = s.rolling(p).max()
        return np.sqrt(
            ((100.0 * (s - rm) / rm.replace(0.0, np.nan)) ** 2).rolling(p).mean()
        )

    def _calculate_sar(self, df, step: float = 0.02, maxstep: float = 0.20):
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        n = len(h)
        sar = np.full(n, np.nan)
        dr = np.ones(n, dtype=int)
        if n < 2:
            return pd.Series(sar, index=df.index), pd.Series(dr, index=df.index)
        bull = True
        af = step
        ep = h[0]
        sar[0] = l[0]
        for i in range(1, n):
            ps = sar[i - 1]
            if bull:
                sar[i] = ps + af * (ep - ps)
                sar[i] = min(sar[i], l[i - 1])
                if i >= 2:
                    sar[i] = min(sar[i], l[i - 2])
                if l[i] < sar[i]:
                    bull = False
                    af = step
                    sar[i] = ep
                    ep = l[i]
                    dr[i] = -1
                else:
                    dr[i] = 1
                    if h[i] > ep:
                        ep = h[i]
                        af = min(af + step, maxstep)
            else:
                sar[i] = ps + af * (ep - ps)
                sar[i] = max(sar[i], h[i - 1])
                if i >= 2:
                    sar[i] = max(sar[i], h[i - 2])
                if h[i] > sar[i]:
                    bull = True
                    af = step
                    sar[i] = ep
                    ep = h[i]
                    dr[i] = 1
                else:
                    dr[i] = -1
                    if l[i] < ep:
                        ep = l[i]
                        af = min(af + step, maxstep)
        return pd.Series(sar, index=df.index), pd.Series(dr, index=df.index)

    def _calculate_adx(self, df, p: int = 14):
        h, l = df["high"], df["low"]
        atr = self._rma(self._true_range(df), p)
        pdm = h.diff().clip(lower=0.0)
        mdm = (-l.diff()).clip(lower=0.0)
        mask = pdm > mdm
        pdm = pdm.where(mask, 0.0)
        mdm = mdm.where(~mask, 0.0)
        sp = self._rma(pdm, p)
        sm = self._rma(mdm, p)
        sa = atr.replace(0.0, np.nan)
        pdi = 100.0 * (sp / sa).fillna(0.0)
        mdi = 100.0 * (sm / sa).fillna(0.0)
        dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
        return self._rma(dx.fillna(0.0), p), pdi, mdi

    def _calculate_supertrend(self, df, p: int = 10, mult: float = 3.0):
        atr = self._rma(self._true_range(df), p)
        hl2 = (df["high"] + df["low"]) / 2.0
        c = df["close"].to_numpy(dtype=float)
        n = len(c)
        bub = (hl2 + mult * atr).to_numpy(dtype=float)
        blb = (hl2 - mult * atr).to_numpy(dtype=float)
        fub = bub.copy()
        flb = blb.copy()
        st = np.zeros(n, dtype=float)
        dr = np.ones(n, dtype=int)
        for i in range(1, n):
            fub[i] = (
                bub[i] if bub[i] < fub[i - 1] or c[i - 1] > fub[i - 1] else fub[i - 1]
            )
            flb[i] = (
                blb[i] if blb[i] > flb[i - 1] or c[i - 1] < flb[i - 1] else flb[i - 1]
            )
            if st[i - 1] == fub[i - 1]:
                dr[i] = -1 if c[i] <= fub[i] else 1
            else:
                dr[i] = 1 if c[i] >= flb[i] else -1
            st[i] = flb[i] if dr[i] == 1 else fub[i]
        return pd.Series(st, index=df.index), pd.Series(dr, index=df.index)

    def _calculate_vortex(self, df, p: int = 14):
        tr = self._true_range(df)
        atr_s = tr.rolling(p).sum()
        vp = (df["high"] - df["low"].shift()).abs().rolling(p).sum()
        vm = (df["low"] - df["high"].shift()).abs().rolling(p).sum()
        return (vp / atr_s.replace(0.0, np.nan)).fillna(0.0), (
            vm / atr_s.replace(0.0, np.nan)
        ).fillna(0.0)

    def _calculate_aroon(self, df, p: int = 25):
        au = (
            df["high"]
            .rolling(p + 1)
            .apply(lambda x: (np.argmax(x) / p) * 100, raw=True)
        )
        ad = (
            df["low"].rolling(p + 1).apply(lambda x: (np.argmin(x) / p) * 100, raw=True)
        )
        return au.fillna(0.0), ad.fillna(0.0)

    def _calculate_elder_ray(self, df, p: int = 13):
        e = self._ema(df["close"], p)
        return df["high"] - e, df["low"] - e

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        return (tp * df["volume"]).cumsum() / df["volume"].cumsum().replace(0.0, np.nan)

    def _calculate_pivots(self, df: pd.DataFrame) -> Dict[str, float]:
        prev = df.iloc[-2]
        h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])
        pp = (h + l + c) / 3.0
        return {
            "PP": pp,
            "R1": 2 * pp - l,
            "R2": pp + (h - l),
            "R3": h + 2 * (pp - l),
            "S1": 2 * pp - h,
            "S2": pp - (h - l),
            "S3": l - 2 * (h - pp),
        }

    @staticmethod
    def _classify_regime(adx, pdi, mdi, rsi, close, ema20) -> str:
        if adx > 30 and pdi > mdi and close > ema20:
            return "STRONG UPTREND"
        if adx > 30 and mdi > pdi and close < ema20:
            return "STRONG DOWNTREND"
        if adx > 20:
            return "TRENDING"
        if 40 < rsi < 60:
            return "RANGING / CONSOLIDATION"
        if rsi >= 70:
            return "OVERBOUGHT RANGE"
        if rsi <= 30:
            return "OVERSOLD RANGE"
        return "NEUTRAL / UNCERTAIN"

    @staticmethod
    def _confluence_score(data: Dict) -> Tuple[int, int, str]:
        close = data.get("close", 0.0)
        bulls = 0
        bears = 0
        checks = [
            close > data.get("SMA_20", close),
            close > data.get("EMA_20", close),
            close > data.get("HMA_20", close),
            close > data.get("KAMA_10", close),
            close > data.get("DEMA_20", close),
            close > data.get("TEMA_20", close),
            close > data.get("Tenkan", close),
            close > data.get("Kijun", close),
            data.get("SuperTrend_Dir", 0) == 1,
            data.get("SAR_Dir", 0) == 1,
            data.get("RSI_14", 50) > 50,
            data.get("MACD_Hist", 0) > 0,
            data.get("StochRSI_K", 50) > data.get("StochRSI_D", 50),
            data.get("CCI_20", 0) > 0,
            data.get("WillR_14", -50) > -50,
            data.get("ROC_12", 0) > 0,
            data.get("CMO_14", 0) > 0,
            data.get("Coppock", 0) > 0,
            data.get("PlusDI", 0) > data.get("MinusDI", 0),
            data.get("VI_Plus", 0) > data.get("VI_Minus", 0),
            data.get("Aroon_Up", 0) > data.get("Aroon_Down", 0),
            data.get("Vol_Delta", 0) > 0,
            data.get("CMF_20", 0) > 0,
            data.get("MFI_14", 50) > 50,
            data.get("Bull_Power", 0) > 0,
            data.get("Bear_Power", 0) > 0,
        ]
        for r in checks:
            if r:
                bulls += 1
            else:
                bears += 1
        total = bulls + bears
        ratio = bulls / total if total else 0.5
        if ratio >= 0.70:
            label = "STRONG BULL"
        elif ratio >= 0.55:
            label = "MILD BULL"
        elif ratio <= 0.30:
            label = "STRONG BEAR"
        elif ratio <= 0.45:
            label = "MILD BEAR"
        else:
            label = "NEUTRAL"
        return bulls, bears, label

    def build_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        df = df.copy()

        # Extract parameters from config with safe defaults
        sma_p = self.config.get("sma_trend_period", 20)
        ema_p = self.config.get("ema_trend_period", 20)
        hma_p = self.config.get("hma_trend_period", 20)
        dema_p = self.config.get("dema_period", 20)
        tema_p = self.config.get("tema_period", 20)

        kama_p = self.config.get("kama_period", 10)
        kama_f = self.config.get("kama_fast_period", 2)
        kama_s = self.config.get("kama_slow_period", 30)

        tenkan_p = self.config.get("ichimoku_tenkan_period", 9)
        kijun_p = self.config.get("ichimoku_kijun_period", 26)
        senkou_b_p = self.config.get("ichimoku_senkou_span_b_period", 52)
        chikou_offset = self.config.get("ichimoku_chikou_span_offset", 26)

        rsi_p = self.config.get("rsi_period", 14)
        stoch_rsi_p = self.config.get("stoch_rsi_period", 14)
        stoch_k_p = self.config.get("stoch_k_period", 3)
        stoch_d_p = self.config.get("stoch_d_period", 3)

        macd_f = self.config.get("macd_fast_period", 12)
        macd_s = self.config.get("macd_slow_period", 26)
        macd_sig = self.config.get("macd_signal_period", 9)

        cci_p = self.config.get("cci_period", 20)
        willr_p = self.config.get("williams_r_period", 14)
        roc_p = self.config.get("roc_period", 12)
        dpo_p = self.config.get("dpo_period", 20)
        trix_p = self.config.get("trix_period", 15)
        cmo_p = self.config.get("cmo_period", 14)

        bb_p = self.config.get("bollinger_bands_period", 20)
        bb_std = self.config.get("bollinger_bands_std_dev", 2.0)

        atr_p = self.config.get("atr_period", 14)
        donchian_p = self.config.get("donchian_period", 20)

        kc_p = self.config.get("keltner_period", 20)
        kc_mult = self.config.get("keltner_atr_multiplier", 2.0)

        ulcer_p = self.config.get("ulcer_period", 14)
        sar_step = self.config.get("psar_acceleration", 0.02)
        sar_max = self.config.get("psar_max_acceleration", 0.20)

        mfi_p = self.config.get("mfi_period", 14)
        cmf_p = self.config.get("cmf_period", 20)

        adx_p = self.config.get("adx_period", 14)
        st_p = self.config.get("supertrend_period", 10)
        st_mult = self.config.get("supertrend_multiplier", 3.0)
        vortex_p = self.config.get("vortex_period", 14)
        aroon_p = self.config.get("aroon_period", 25)
        elder_p = self.config.get("elder_ray_period", 13)

        df["SMA_20"] = close.rolling(sma_p).mean()
        df["EMA_20"] = self._ema(close, ema_p)
        df["HMA_20"] = self._calculate_hma(close, hma_p)
        df["KAMA_10"] = self._calculate_kama(close, kama_p, kama_f, kama_s)
        df["DEMA_20"] = self._calculate_dema(close, dema_p)
        df["TEMA_20"] = self._calculate_tema(close, tema_p)
        df["Tenkan"] = (
            high.rolling(tenkan_p).max() + low.rolling(tenkan_p).min()
        ) / 2.0
        df["Kijun"] = (high.rolling(kijun_p).max() + low.rolling(kijun_p).min()) / 2.0
        df["Senkou_A"] = ((df["Tenkan"] + df["Kijun"]) / 2.0).shift(chikou_offset)
        df["Senkou_B"] = (
            (high.rolling(senkou_b_p).max() + low.rolling(senkou_b_p).min()) / 2.0
        ).shift(chikou_offset)
        df["VWAP"] = self._calculate_vwap(df)

        df["RSI_14"] = self._calculate_rsi(close, rsi_p)
        rm = df["RSI_14"].rolling(stoch_rsi_p).min()
        rx = df["RSI_14"].rolling(stoch_rsi_p).max()
        df["StochRSI_K"] = 100.0 * (
            (df["RSI_14"] - rm) / (rx - rm).replace(0.0, np.nan)
        ).fillna(0.0)
        df["StochRSI_D"] = df["StochRSI_K"].rolling(stoch_d_p).mean()

        ef = self._ema(close, macd_f)
        es = self._ema(close, macd_s)
        df["MACD_Line"] = ef - es
        df["MACD_Signal"] = self._ema(df["MACD_Line"], macd_sig)
        df["MACD_Hist"] = df["MACD_Line"] - df["MACD_Signal"]

        tp = (high + low + close) / 3.0
        stp = tp.rolling(cci_p).mean()
        mad = tp.rolling(cci_p).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        df["CCI_20"] = (tp - stp) / (0.015 * mad.replace(0.0, np.nan))

        hh = high.rolling(willr_p).max()
        ll = low.rolling(willr_p).min()
        df["WillR_14"] = -100.0 * (
            (hh - close) / (hh - ll).replace(0.0, np.nan)
        ).fillna(0.0)
        df["ROC_12"] = self._calculate_roc(close, roc_p)
        df["DPO_20"] = self._calculate_dpo(close, dpo_p)
        df["TRIX_15"] = self._calculate_trix(close, trix_p)
        df["CMO_14"] = self._calculate_cmo(close, cmo_p)
        df["Coppock"] = self._calculate_coppock(close)

        sk_r = 100.0 * ((close - ll) / (hh - ll).replace(0.0, np.nan)).fillna(0.0)
        df["Stoch_K"] = sk_r.rolling(stoch_k_p).mean()
        df["Stoch_D"] = df["Stoch_K"].rolling(stoch_d_p).mean()

        bbs = close.rolling(bb_p).std()
        df["BB_Upper"] = df["EMA_20"] + bb_std * bbs
        df["BB_Lower"] = df["EMA_20"] - bb_std * bbs
        df["BB_Width"] = (
            (df["BB_Upper"] - df["BB_Lower"])
            / df["EMA_20"].replace(0.0, np.nan)
            * 100.0
        )

        tr = self._true_range(df)
        df["ATR_14"] = self._rma(tr, atr_p)
        df["Donchian_U"] = high.rolling(donchian_p).max()
        df["Donchian_L"] = low.rolling(donchian_p).min()
        df["KC_Upper"], df["KC_Mid"], df["KC_Lower"] = self._calculate_keltner(
            df, kc_p, atr_p, kc_mult
        )
        df["Ulcer_14"] = self._calculate_ulcer(close, ulcer_p)
        df["SAR"], df["SAR_Dir"] = self._calculate_sar(df, sar_step, sar_max)

        obv_d = np.sign(close.diff().fillna(0.0))
        df["OBV"] = (obv_d * volume).cumsum()

        tp2 = (high + low + close) / 3.0
        mf = tp2 * volume
        pmf = mf.where(tp2.diff() > 0.0, 0.0)
        nmf = mf.where(tp2.diff() < 0.0, 0.0)
        mfr = pmf.rolling(mfi_p).sum() / nmf.rolling(mfi_p).sum().replace(0.0, np.nan)
        df["MFI_14"] = 100.0 - (100.0 / (1.0 + mfr.fillna(0.0)))

        hlr = high - low
        mfm = np.where(hlr != 0, ((close - low) - (high - close)) / hlr, 0.0)
        vol_sum = volume.rolling(cmf_p).sum()
        df["CMF_20"] = (mfm * volume).rolling(cmf_p).sum() / vol_sum.replace(
            0.0, np.nan
        )

        bv = volume.where(close > df["open"], 0.0)
        sv = volume.where(close <= df["open"], 0.0)
        df["Vol_Delta"] = bv - sv

        df["ADX"], df["PlusDI"], df["MinusDI"] = self._calculate_adx(df, adx_p)
        df["SuperTrend"], df["SuperTrend_Dir"] = self._calculate_supertrend(
            df, st_p, st_mult
        )
        df["VI_Plus"], df["VI_Minus"] = self._calculate_vortex(df, vortex_p)
        df["Aroon_Up"], df["Aroon_Down"] = self._calculate_aroon(df, aroon_p)
        df["Bull_Power"], df["Bear_Power"] = self._calculate_elder_ray(df, elder_p)

        pivots = self._calculate_pivots(df)
        latest = df.iloc[-1].to_dict()
        latest.update(pivots)

        cur = float(close.iloc[-1])
        latest["close"] = cur
        latest["prev_close"] = self._prev_close if self._prev_close else cur
        self._prev_close = cur

        latest["regime"] = self._classify_regime(
            float(df["ADX"].iloc[-1]),
            float(df["PlusDI"].iloc[-1]),
            float(df["MinusDI"].iloc[-1]),
            float(df["RSI_14"].iloc[-1]),
            cur,
            float(df["EMA_20"].iloc[-1]),
        )
        bulls, bears, conf = self._confluence_score(latest)
        latest["conf_bulls"] = bulls
        latest["conf_bears"] = bears
        latest["conf_label"] = conf
        return latest


# ═════════════════════════════════════════════════════════════════════════════
#  OUTPUT RENDERER - Enhanced Display Engine v3.0
# ═════════════════════════════════════════════════════════════════════════════


class OutputRenderer:
    """
    Terminal layout engine.
    Renders technical indicators + full L2 orderbook intelligence panels.
    """

    @staticmethod
    def _fv(val: Any, dec: int = 4) -> str:
        try:
            f = float(val)
            return "  N/A  " if math.isnan(f) else f"{f:.{dec}f}"
        except (TypeError, ValueError):
            return str(val)

    @staticmethod
    def _col_thresh(val: float, lo: float, hi: float, inv: bool = False) -> str:
        a = val > hi
        b = val < lo
        if inv:
            a, b = b, a
        return NEON_GREEN if a else NEON_RED if b else NEON_YELLOW

    @staticmethod
    def _pct_bar(ratio: float, width: int = 24) -> str:
        """Render a two-tone ASCII bar for bull/bear ratio."""
        ratio = max(0.0, min(1.0, ratio))
        filled = int(ratio * width)
        empty = width - filled
        return f"{NEON_GREEN}{'█' * filled}{NEON_RED}{'░' * empty}{RESET}"

    @staticmethod
    def _imbalance_bar(val: float, width: int = 20) -> str:
        """
        Centered bar: 0 is center, +1 fills right (bull), -1 fills left (bear).
        """
        half = width // 2
        pos = max(-1.0, min(1.0, val))
        center = half
        if pos >= 0:
            filled = int(pos * half)
            bar = (
                f"{DIM}{'─' * center}{RESET}"
                f"{NEON_GREEN}{'█' * filled}"
                f"{DIM}{'─' * (half - filled)}{RESET}"
            )
        else:
            filled = int(abs(pos) * half)
            bar = (
                f"{DIM}{'─' * (half - filled)}{RESET}"
                f"{NEON_RED}{'█' * filled}"
                f"{DIM}{'─' * center}{RESET}"
            )
        return bar

    @staticmethod
    def _section(title: str) -> None:
        pad = "─" * max(0, BOX_WIDTH - len(title) - 6)
        print(
            f"\n{NEON_BLUE}╠─{NEON_PURPLE}{BRIGHT}  ◈ {title}  "
            f"{RESET}{NEON_BLUE}{pad}╣{RESET}"
        )

    @staticmethod
    def _row(label: str, val_str: str) -> None:
        lbl = f"{NEON_CYAN}{label:<{LABEL_W}}{RESET}"
        print(f"  {NEON_BLUE}│{RESET} {lbl} {NEON_BLUE}:{RESET} {val_str}")

    @staticmethod
    def _note_row(text: str) -> None:
        print(f"  {NEON_BLUE}│{RESET}  {NEON_YELLOW}⚑{RESET} {text}")

    @staticmethod
    def _price_change_str(cur: float, prev: float) -> str:
        if prev == 0.0:
            return f"{NEON_YELLOW}N/A{RESET}"
        pct = (cur - prev) / abs(prev) * 100.0
        return (
            f"{NEON_GREEN}▲ +{pct:.3f}%{RESET}"
            if pct > 0
            else f"{NEON_RED}▼ {pct:.3f}%{RESET}"
            if pct < 0
            else f"{NEON_YELLOW}─ 0.000%{RESET}"
        )

    @classmethod
    def display_metrics(
        cls,
        ta: Dict[str, Any],
        ob_met: Dict[str, Any],
        tr_met: Dict[str, Any],
        fi_met: Dict[str, Any],
        l2_bulls: int,
        l2_bears: int,
        l2_label: str,
        l2_notes: List[str],
    ) -> None:
        os.system("cls" if os.name == "nt" else "clear")

        import io
        import sys

        original_stdout = sys.stdout
        captured_stdout = io.StringIO()
        sys.stdout = captured_stdout

        close = float(ta.get("close", 0.0))
        prev_close = float(ta.get("prev_close", close))
        time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        regime = ta.get("regime", "UNKNOWN")
        conf_label = ta.get("conf_label", "NEUTRAL")
        ta_bulls = int(ta.get("conf_bulls", 0))
        ta_bears = int(ta.get("conf_bears", 0))
        chg_str = cls._price_change_str(close, prev_close)

        border = "═" * BOX_WIDTH
        print(f"\n{NEON_BLUE}╔{border}╗{RESET}")

        title = "  NEON MARKET TREND OBSERVATORY  v3.0  │  L2 + MICROSTRUCTURE FEED  "
        print(
            f"{NEON_BLUE}║{NEON_CYAN}{BRIGHT}{title:<{BOX_WIDTH}}{RESET}{NEON_BLUE}║{RESET}"
        )
        print(f"{NEON_BLUE}╠{border}╣{RESET}")

        def hrow(l1, v1, l2="", v2=""):
            line = f"  {NEON_YELLOW}{l1:<14}{RESET}: {v1:<32}"
            if l2:
                line += f" {NEON_YELLOW}{l2:<10}{RESET}: {v2}"
            print(f"{NEON_BLUE}║{RESET}{line}")

        mark_p = fi_met.get("mark_price", close)
        index_p = fi_met.get("index_price", close)
        hrow("Timestamp", time_str)
        hrow(
            "Last Price",
            f"{NEON_GREEN}{BRIGHT}{close:<15.4f}{RESET}",
            "Change",
            chg_str,
        )
        hrow(
            "Mark Price",
            f"{NEON_YELLOW}{mark_p:.4f}{RESET}",
            "Index",
            f"{NEON_CYAN}{index_p:.4f}{RESET}",
        )
        hrow("Regime", f"{NEON_PURPLE}{BRIGHT}{regime}{RESET}")

        # Combined signal header
        ta_total = ta_bulls + ta_bears
        l2_total = l2_bulls + l2_bears
        ta_pct = int(ta_bulls / ta_total * 100) if ta_total else 50
        l2_pct = int(l2_bulls / l2_total * 100) if l2_total else 50
        ta_bar = cls._pct_bar(ta_bulls / ta_total if ta_total else 0.5, 16)
        l2_bar = cls._pct_bar(l2_bulls / l2_total if l2_total else 0.5, 16)

        print(f"{NEON_BLUE}╠{border}╣{RESET}")
        print(
            f"{NEON_BLUE}║{RESET}  "
            f"{NEON_YELLOW}TA Confluence  {RESET}: {ta_bar}  "
            f"{ta_pct}%  {NEON_PURPLE}{conf_label}{RESET}"
        )
        print(
            f"{NEON_BLUE}║{RESET}  "
            f"{NEON_YELLOW}L2 Flow Score  {RESET}: {l2_bar}  "
            f"{l2_pct}%  {NEON_PURPLE}{l2_label}{RESET}"
        )

        # ════════════════════════════════════════════════════════════════
        # SECTION A - ORDERBOOK MICROSTRUCTURE
        # ════════════════════════════════════════════════════════════════
        cls._section("A. ORDERBOOK MICROSTRUCTURE")

        best_bid = ob_met.get("best_bid", 0.0)
        best_ask = ob_met.get("best_ask", 0.0)
        mid = ob_met.get("mid_price", 0.0)
        micro = ob_met.get("micro_price", 0.0)
        sp_abs = ob_met.get("spread_abs", 0.0)
        sp_bps = ob_met.get("spread_bps", 0.0)
        ob_imb = ob_met.get("ob_imbalance", 0.0)
        tot_bid = ob_met.get("total_bid_vol", 0.0)
        tot_ask = ob_met.get("total_ask_vol", 0.0)
        dr_1pct = ob_met.get("depth_ratio_1pct", 1.0)
        slip_b = ob_met.get("slip_buy_bps", 0.0)
        slip_s = ob_met.get("slip_sell_bps", 0.0)
        imp_b = ob_met.get("impact_buy_100k", 0.0)
        imp_s = ob_met.get("impact_sell_100k", 0.0)
        sup_lvl = ob_met.get("support_level", 0.0)
        res_lvl = ob_met.get("resistance_level", 0.0)

        sp_col = (
            NEON_GREEN if sp_bps < 3.0 else NEON_YELLOW if sp_bps < 8.0 else NEON_RED
        )
        imb_bar = cls._imbalance_bar(ob_imb, 22)
        imb_col = (
            NEON_GREEN if ob_imb > 0.1 else NEON_RED if ob_imb < -0.1 else NEON_YELLOW
        )

        cls._row(
            "Best Bid / Ask",
            f"{NEON_GREEN}{best_bid:.4f}{RESET}  /  {NEON_RED}{best_ask:.4f}{RESET}",
        )
        cls._row(
            "Mid / Micro-Price",
            f"{NEON_YELLOW}{mid:.4f}{RESET}  /  {NEON_CYAN}{micro:.4f}{RESET}",
        )
        cls._row("Spread", f"{sp_col}{sp_abs:.4f}  ({sp_bps:.2f} bps){RESET}")
        cls._row(
            "OB Imbalance",
            f"{imb_bar}  {imb_col}{ob_imb:+.3f}{RESET}  "
            f"[{'BID HEAVY ▲' if ob_imb > 0.1 else 'ASK HEAVY ▼' if ob_imb < -0.1 else 'BALANCED'}]",
        )
        cls._row(
            "Depth Bid / Ask",
            f"{NEON_GREEN}{tot_bid:>10,.1f}{RESET}  /  "
            f"{NEON_RED}{tot_ask:>10,.1f}{RESET}",
        )
        cls._row(
            "Depth Ratio @1%",
            (
                f"{NEON_GREEN}"
                if dr_1pct > 1.2
                else f"{NEON_RED}"
                if dr_1pct < 0.8
                else f"{NEON_YELLOW}"
            )
            + f"Bids {dr_1pct:.2f}x vs Asks{RESET}",
        )

        # Depth levels table
        dl = ob_met.get("depth_levels", {})
        for pct_tag in ["0.1", "0.5", "1.0", "2.0"]:
            bd = dl.get(f"bid_depth_{pct_tag}pct", 0.0)
            ad = dl.get(f"ask_depth_{pct_tag}pct", 0.0)
            ratio = bd / ad if ad > 0 else 0.0
            rc = NEON_GREEN if ratio > 1.2 else NEON_RED if ratio < 0.8 else NEON_YELLOW
            cls._row(
                f"  Depth ±{pct_tag}%",
                f"Bid:{NEON_GREEN}{bd:>8,.1f}{RESET}  "
                f"Ask:{NEON_RED}{ad:>8,.1f}{RESET}  "
                f"Ratio:{rc}{ratio:.2f}x{RESET}",
            )

        cls._row(
            "Slip Buy / Sell",
            f"{NEON_YELLOW}{slip_b:.2f} bps{RESET}  /  "
            f"{NEON_YELLOW}{slip_s:.2f} bps{RESET}",
        )
        cls._row(
            "Impact @$100k",
            f"Buy:{NEON_RED}{imp_b:.4f}%{RESET}  Sell:{NEON_GREEN}{imp_s:.4f}%{RESET}",
        )
        cls._row(
            "Absorption Sup",
            f"{NEON_GREEN}{sup_lvl:.4f}{RESET}  "
            f"{'▲ Below Price' if sup_lvl < close else '▼ Above Price'}",
        )
        cls._row(
            "Absorption Res",
            f"{NEON_RED}{res_lvl:.4f}{RESET}  "
            f"{'▲ Below Price' if res_lvl < close else '▼ Above Price'}",
        )

        # Display Top 3 Orderbook Density Levels
        ob_sups = ob_met.get("ob_supports", [])
        ob_reses = ob_met.get("ob_resistances", [])
        for i, s in enumerate(ob_sups):
            dist = (close - s["price"]) / close * 100.0 if close > 0 else 0.0
            cls._row(
                f"  OB Support S{i + 1}",
                f"{NEON_GREEN}{s['price']:.4f}{RESET}  (Vol: {s['volume']:,.1f}, -{dist:.2f}%)",
            )
        for i, r in enumerate(ob_reses):
            dist = (r["price"] - close) / close * 100.0 if close > 0 else 0.0
            cls._row(
                f"  OB Resistance R{i + 1}",
                f"{NEON_RED}{r['price']:.4f}{RESET}  (Vol: {r['volume']:,.1f}, +{dist:.2f}%)",
            )

        # ── Whale Walls ───────────────────────────────────────────────────
        cls._section("A.2  WHALE WALLS  (Orders > 3× Average Size)")

        bid_walls = ob_met.get("bid_walls", [])
        ask_walls = ob_met.get("ask_walls", [])

        if bid_walls:
            for i, w in enumerate(bid_walls[:3]):
                dist_pct = (close - w["price"]) / close * 100.0
                cls._row(
                    f"  Bid Wall #{i + 1}",
                    f"{NEON_GREEN}{w['size']:>10,.2f} "
                    f"@ {w['price']:.4f}  "
                    f"[{dist_pct:.3f}% below price]{RESET}",
                )
        else:
            cls._row("  Bid Walls", f"{DIM}None detected{RESET}")

        if ask_walls:
            for i, w in enumerate(ask_walls[:3]):
                dist_pct = (w["price"] - close) / close * 100.0
                cls._row(
                    f"  Ask Wall #{i + 1}",
                    f"{NEON_RED}{w['size']:>10,.2f} "
                    f"@ {w['price']:.4f}  "
                    f"[{dist_pct:.3f}% above price]{RESET}",
                )
        else:
            cls._row("  Ask Walls", f"{DIM}None detected{RESET}")

        # ════════════════════════════════════════════════════════════════
        # SECTION B - TRADE FLOW & AGGRESSOR ANALYSIS
        # ════════════════════════════════════════════════════════════════
        cls._section("B. TRADE FLOW & AGGRESSOR ANALYSIS")

        total_vol = tr_met.get("total_vol", 0.0)
        buy_vol = tr_met.get("buy_vol", 0.0)
        sell_vol = tr_met.get("sell_vol", 0.0)
        flow_imb = tr_met.get("flow_imbalance", 0.0)
        lg_imb = tr_met.get("large_flow_imb", 0.0)
        lg_bv = tr_met.get("large_buy_vol", 0.0)
        lg_sv = tr_met.get("large_sell_vol", 0.0)
        lg_cnt = tr_met.get("large_trade_count", 0)
        avg_sz = tr_met.get("avg_trade_size", 0.0)
        max_sz = tr_met.get("largest_trade", 0.0)
        t_count = tr_met.get("trade_count", 0)
        vpin = tr_met.get("vpin_proxy", 0.0)
        price_vel = tr_met.get("price_velocity", 0.0)
        agg_ratio = tr_met.get("aggressor_ratio", 0.0)

        flow_bar = cls._imbalance_bar(flow_imb, 22)
        flow_col = (
            NEON_GREEN
            if flow_imb > 0.1
            else NEON_RED
            if flow_imb < -0.1
            else NEON_YELLOW
        )
        lg_bar = cls._imbalance_bar(lg_imb, 22)
        lg_col = (
            NEON_GREEN if lg_imb > 0.1 else NEON_RED if lg_imb < -0.1 else NEON_YELLOW
        )
        agg_bar = cls._imbalance_bar(agg_ratio, 22)

        cls._row(
            "Taker Buy / Sell",
            f"{NEON_GREEN}{buy_vol:>10,.1f}{RESET}  /  "
            f"{NEON_RED}{sell_vol:>10,.1f}{RESET}  "
            f"Total:{NEON_YELLOW}{total_vol:,.1f}{RESET}",
        )
        cls._row(
            "Flow Imbalance",
            f"{flow_bar}  {flow_col}{flow_imb:+.3f}{RESET}  "
            f"[{'TAKER BUY ▲' if flow_imb > 0.1 else 'TAKER SELL ▼' if flow_imb < -0.1 else 'NEUTRAL'}]",
        )
        cls._row(
            "Aggressor (20T)",
            f"{agg_bar}  "
            f"{NEON_GREEN if agg_ratio > 0 else NEON_RED}{agg_ratio:+.3f}{RESET}",
        )
        cls._row(
            "Price Velocity",
            f"{NEON_GREEN if price_vel > 0 else NEON_RED}{price_vel:+.5f}%{RESET}",
        )
        cls._row(
            "Large Trade Flow",
            f"{lg_bar}  {lg_col}{lg_imb:+.3f}{RESET}  ({lg_cnt} whale trades)",
        )
        cls._row(
            "Whale Buy/Sell Vol",
            f"{NEON_GREEN}{lg_bv:>8,.1f}{RESET}  /  {NEON_RED}{lg_sv:>8,.1f}{RESET}",
        )
        cls._row(
            "Trades Sampled",
            f"{NEON_CYAN}{t_count}{RESET}  Avg:{NEON_YELLOW}{avg_sz:.3f}{RESET}  "
            f"Max:{NEON_RED}{max_sz:.3f}{RESET}",
        )

        vpin_col = (
            NEON_RED if vpin > 0.4 else NEON_YELLOW if vpin > 0.25 else NEON_GREEN
        )
        vpin_tag = (
            "HIGH - Informed Trader Risk"
            if vpin > 0.4
            else "ELEVATED"
            if vpin > 0.25
            else "NORMAL"
        )
        cls._row("VPIN Proxy", f"{vpin_col}{vpin:.4f}  [{vpin_tag}]{RESET}")

        # ════════════════════════════════════════════════════════════════
        # SECTION C - FUNDING, OI & SENTIMENT
        # ════════════════════════════════════════════════════════════════
        cls._section("C. FUNDING RATE, OPEN INTEREST & SENTIMENT")

        fr_pct = fi_met.get("funding_rate_pct", 0.0)
        fr_ann = fi_met.get("funding_annualized", 0.0)
        fr_bias = fi_met.get("funding_bias", "NEUTRAL")
        fr_sent = fi_met.get("funding_sentiment", "N/A")
        mins_fund = fi_met.get("mins_to_funding", -1)
        oi_val = fi_met.get("open_interest", 0.0)
        oi_usd = fi_met.get("open_interest_value", 0.0)
        oi_chg = fi_met.get("oi_change_pct", 0.0)
        oi_trend = fi_met.get("oi_trend", 0.0)
        ls_buy = fi_met.get("ls_buy_ratio", 0.5)
        ls_sell = fi_met.get("ls_sell_ratio", 0.5)
        ls_net = fi_met.get("ls_net", 0.0)
        ls_trend = fi_met.get("ls_trend", 0.0)
        ls_sent = fi_met.get("ls_sentiment", "N/A")
        vol24 = fi_met.get("volume_24h", 0.0)
        turn24 = fi_met.get("turnover_24h", 0.0)
        hi24 = fi_met.get("high_24h", 0.0)
        lo24 = fi_met.get("low_24h", 0.0)
        basis = fi_met.get("mark_index_basis", 0.0)

        fr_col = (
            NEON_RED if fr_pct > 0.05 else NEON_GREEN if fr_pct < -0.01 else NEON_YELLOW
        )
        oi_col = NEON_GREEN if oi_trend > 0 else NEON_RED
        bias_col = (
            NEON_GREEN
            if fr_bias == "BULLISH"
            else NEON_RED
            if fr_bias == "BEARISH"
            else NEON_YELLOW
        )
        ls_bar = cls._pct_bar(ls_buy, 20)

        cls._row(
            "Funding Rate",
            f"{fr_col}{fr_pct:+.6f}%{RESET}  Annualized: {fr_col}{fr_ann:+.2f}%{RESET}",
        )
        cls._row("Funding Bias", f"{bias_col}{BRIGHT}{fr_sent}{RESET}")
        funding_time_str = f"{mins_fund} min remaining" if mins_fund >= 0 else "N/A"
        cls._row("Next Funding", f"{NEON_YELLOW}{funding_time_str}{RESET}")

        cls._row(
            "Mark/Index Basis",
            f"{NEON_CYAN if abs(basis) < 0.05 else NEON_YELLOW}{basis:+.5f}%{RESET}",
        )

        cls._row(
            "Open Interest",
            f"{NEON_CYAN}{oi_val:>14,.2f}{RESET}  "
            f"≈ ${NEON_YELLOW}{oi_usd / 1e6:,.2f}M{RESET}",
        )
        cls._row(
            "OI Change",
            f"{oi_col}{oi_chg:+.3f}%{RESET}  "
            f"Trend: {oi_col}{'↑ Growing' if oi_trend > 0 else '↓ Shrinking'}{RESET}",
        )

        cls._row(
            "Long/Short Ratio",
            f"{ls_bar}  L:{NEON_GREEN}{ls_buy:.3f}{RESET}  "
            f"S:{NEON_RED}{ls_sell:.3f}{RESET}  "
            f"Net:{NEON_YELLOW}{ls_net:+.3f}{RESET}",
        )
        ls_trend_col = NEON_GREEN if ls_trend > 0 else NEON_RED
        cls._row(
            "L/S Trend",
            f"{ls_trend_col}{ls_trend:+.4f}{RESET}  {NEON_PURPLE}{ls_sent}{RESET}",
        )

        cls._row(
            "Volume 24H",
            f"{NEON_CYAN}{vol24:>14,.2f}{RESET}  "
            f"Turnover: ${NEON_YELLOW}{turn24 / 1e6:,.1f}M{RESET}",
        )
        cls._row(
            "24H High / Low",
            f"{NEON_GREEN}{hi24:.4f}{RESET}  /  "
            f"{NEON_RED}{lo24:.4f}{RESET}  "
            f"Range: {NEON_YELLOW}{((hi24 - lo24) / lo24 * 100 if lo24 > 0 else 0):.2f}%{RESET}",
        )

        # ── Liquidation Estimates ─────────────────────────────────────────
        cls._section("C.2  ESTIMATED LIQUIDATION CASCADE ZONES")
        print(
            f"  {DIM}(Simplified estimates - not precise per-position liq. prices){RESET}"
        )

        for lev in [10, 20]:
            ll = fi_met.get(f"liq_est_long_{lev}x", 0.0)
            ls = fi_met.get(f"liq_est_short_{lev}x", 0.0)
            ld = (close - ll) / close * 100.0 if close > 0 else 0
            lsd = (ls - close) / close * 100.0 if close > 0 else 0
            cls._row(
                f"  Longs @{lev}x",
                f"{NEON_RED}{ll:.4f}{RESET}  ({ld:.2f}% below current price)",
            )
            cls._row(
                f"  Shorts @{lev}x",
                f"{NEON_GREEN}{ls:.4f}{RESET}  ({lsd:.2f}% above current price)",
            )

        # ════════════════════════════════════════════════════════════════
        # SECTION D - L2 SIGNAL NOTES
        # ════════════════════════════════════════════════════════════════
        cls._section("D. L2 MICROSTRUCTURE SIGNAL NOTES")
        for note in l2_notes:
            cls._note_row(note)

        # ════════════════════════════════════════════════════════════════
        # SECTION I-VI  TECHNICAL ANALYSIS (from v2.0, preserved fully)
        # ════════════════════════════════════════════════════════════════

        # I. TREND LEY LINES
        cls._section("I. TREND LEY LINES")
        for lbl, key in [
            ("SMA (20)", "SMA_20"),
            ("EMA (20)", "EMA_20"),
            ("HMA (20)", "HMA_20"),
            ("KAMA (10)", "KAMA_10"),
            ("DEMA (20)", "DEMA_20"),
            ("TEMA (20)", "TEMA_20"),
        ]:
            val = float(ta.get(key, 0.0))
            col = NEON_GREEN if close > val else NEON_RED
            cls._row(lbl, f"{col}{val:.4f}{RESET}  {'▲' if close > val else '▼'}")

        vwap_v = float(ta.get("VWAP", close))
        cls._row(
            "VWAP",
            f"{NEON_GREEN if close > vwap_v else NEON_RED}{vwap_v:.4f}{RESET}  "
            f"[{'ABOVE' if close > vwap_v else 'BELOW'}]",
        )

        tk = float(ta.get("Tenkan", 0.0))
        kj = float(ta.get("Kijun", 0.0))
        sa = ta.get("Senkou_A", np.nan)
        sb = ta.get("Senkou_B", np.nan)
        cls._row("Ichimoku Tenkan", f"{NEON_CYAN}{tk:.4f}{RESET}")
        cls._row("Ichimoku Kijun", f"{NEON_CYAN}{kj:.4f}{RESET}")
        cls._row("Senkou A / B", f"{NEON_YELLOW}{cls._fv(sa)} / {cls._fv(sb)}{RESET}")

        # II. MOMENTUM
        cls._section("II. MOMENTUM OSCILLATIONS")
        rsi_v = float(ta.get("RSI_14", 50))
        rc = (
            NEON_RED
            if rsi_v > 70 or rsi_v < 30
            else NEON_GREEN
            if 45 < rsi_v < 55
            else NEON_YELLOW
        )
        rtag = "OVERBOUGHT" if rsi_v > 70 else "OVERSOLD" if rsi_v < 30 else "NEUTRAL"
        cls._row("RSI (14)", f"{rc}{rsi_v:.2f}{RESET}  [{rtag}]")

        sk = float(ta.get("StochRSI_K", 0))
        sd = float(ta.get("StochRSI_D", 0))
        cls._row(
            "StochRSI K/D",
            f"{NEON_CYAN}{sk:.2f}{RESET}/{NEON_YELLOW}{sd:.2f}{RESET}  "
            f"[{'K>D ▲' if sk > sd else 'K<D ▼'}]",
        )

        kk = float(ta.get("Stoch_K", 0))
        kd = float(ta.get("Stoch_D", 0))
        stc = NEON_RED if kk > 80 or kk < 20 else NEON_YELLOW
        cls._row("Stochastic K/D", f"{stc}{kk:.2f}/{kd:.2f}{RESET}")

        ml = float(ta.get("MACD_Line", 0))
        ms = float(ta.get("MACD_Signal", 0))
        mh = float(ta.get("MACD_Hist", 0))
        cls._row(
            "MACD Line/Sig", f"{NEON_CYAN}{ml:.4f}{RESET}/{NEON_YELLOW}{ms:.4f}{RESET}"
        )
        cls._row(
            "MACD Histogram", f"{NEON_GREEN if mh > 0 else NEON_RED}{mh:+.4f}{RESET}"
        )

        cci = float(ta.get("CCI_20", 0))
        cls._row("CCI (20)", f"{cls._col_thresh(cci, -100, 100)}{cci:.2f}{RESET}")
        wr = float(ta.get("WillR_14", -50))
        cls._row(
            "Williams %R",
            f"{NEON_RED if wr > -20 else NEON_GREEN if wr < -80 else NEON_YELLOW}{wr:.2f}{RESET}",
        )
        for lbl, key, fmt in [
            ("ROC (12)", "ROC_12", "+.3f"),
            ("DPO (20)", "DPO_20", "+.4f"),
            ("TRIX (15)", "TRIX_15", "+.6f"),
            ("CMO (14)", "CMO_14", "+.2f"),
            ("Coppock", "Coppock", "+.6f"),
        ]:
            v = float(ta.get(key, 0))
            cls._row(lbl, f"{NEON_GREEN if v > 0 else NEON_RED}{v:{fmt}}{RESET}")

        # III. VOLATILITY
        cls._section("III. VOLATILITY BOUNDS")
        bbu = float(ta.get("BB_Upper", 0))
        bbl = float(ta.get("BB_Lower", 0))
        bbw = float(ta.get("BB_Width", 0))
        kcu = float(ta.get("KC_Upper", 0))
        kcm = float(ta.get("KC_Mid", 0))
        kcl = float(ta.get("KC_Lower", 0))
        sq = bbu < kcu and bbl > kcl
        cls._row(
            "Bollinger Bands",
            f"U:{NEON_RED}{bbu:.2f}{RESET}  L:{NEON_GREEN}{bbl:.2f}{RESET}  "
            f"W:{NEON_YELLOW}{bbw:.2f}%{RESET}",
        )
        cls._row(
            "Keltner Channels",
            f"U:{NEON_RED}{kcu:.2f}{RESET}  M:{NEON_YELLOW}{kcm:.2f}{RESET}  "
            f"L:{NEON_GREEN}{kcl:.2f}{RESET}",
        )
        cls._row(
            "BB/KC Squeeze",
            f"{NEON_YELLOW + '⚡ SQUEEZE ACTIVE' if sq else NEON_CYAN + 'No Squeeze'}{RESET}",
        )
        cls._row("ATR (14)", f"{NEON_YELLOW}{cls._fv(ta.get('ATR_14'))}{RESET}")
        dcu = float(ta.get("Donchian_U", 0))
        dcl = float(ta.get("Donchian_L", 0))
        cls._row(
            "Donchian Chan",
            f"U:{NEON_RED}{dcu:.2f}{RESET}  L:{NEON_GREEN}{dcl:.2f}{RESET}",
        )
        sar_v = float(ta.get("SAR", 0))
        sar_d = int(ta.get("SAR_Dir", 1))
        cls._row(
            "Parabolic SAR",
            f"{NEON_GREEN if sar_d == 1 else NEON_RED}{sar_v:.4f}  "
            f"{'▲ BULL' if sar_d == 1 else '▼ BEAR'}{RESET}",
        )
        ui = float(ta.get("Ulcer_14", 0))
        cls._row(
            "Ulcer Index",
            f"{NEON_RED if ui > 10 else NEON_YELLOW if ui > 5 else NEON_GREEN}{ui:.4f}{RESET}",
        )

        # IV. VOLUME
        cls._section("IV. VOLUME & FLOW DYNAMICS")
        cls._row("OBV", f"{NEON_CYAN}{float(ta.get('OBV', 0)):>18,.0f}{RESET}")
        mfi = float(ta.get("MFI_14", 50))
        cls._row(
            "MFI (14)",
            f"{NEON_RED if mfi > 80 else NEON_GREEN if mfi < 20 else NEON_YELLOW}{mfi:.2f}{RESET}",
        )
        cmf = float(ta.get("CMF_20", 0))
        cls._row("CMF (20)", f"{NEON_GREEN if cmf > 0 else NEON_RED}{cmf:+.4f}{RESET}")
        dlt = float(ta.get("Vol_Delta", 0))
        cls._row(
            "Volume Delta",
            f"{NEON_GREEN if dlt > 0 else NEON_RED}{dlt:>+14,.0f}{RESET}",
        )

        # V. DIRECTIONAL
        cls._section("V. DIRECTIONAL STRENGTH")
        adx_v = float(ta.get("ADX", 0))
        pdi = float(ta.get("PlusDI", 0))
        mdi = float(ta.get("MinusDI", 0))
        ac = NEON_GREEN if adx_v > 40 else NEON_YELLOW if adx_v > 25 else NEON_RED
        atag = "VERY STRONG" if adx_v > 40 else "STRONG" if adx_v > 25 else "WEAK"
        cls._row(
            "ADX (14)",
            f"{ac}{adx_v:.2f} [{atag}]{RESET}  "
            f"+DI:{NEON_GREEN}{pdi:.2f}{RESET} -DI:{NEON_RED}{mdi:.2f}{RESET}",
        )
        st_v = float(ta.get("SuperTrend", 0))
        st_d = int(ta.get("SuperTrend_Dir", 0))
        cls._row(
            "SuperTrend",
            f"{NEON_GREEN if st_d == 1 else NEON_RED}{st_v:.4f}  "
            f"{'▲ UPTREND' if st_d == 1 else '▼ DOWNTREND'}{RESET}",
        )
        vp = float(ta.get("VI_Plus", 0))
        vm = float(ta.get("VI_Minus", 0))
        cls._row(
            "Vortex VI+/VI-",
            f"{NEON_GREEN if vp > vm else NEON_RED}VI+:{vp:.3f} VI-:{vm:.3f} "
            f"{'BULL' if vp > vm else 'BEAR'}{RESET}",
        )
        au = float(ta.get("Aroon_Up", 0))
        ad_v = float(ta.get("Aroon_Down", 0))
        cls._row(
            "Aroon Up/Down",
            f"{NEON_GREEN if au > ad_v else NEON_RED}Up:{au:.1f} Dn:{ad_v:.1f} "
            f"{'▲' if au > ad_v else '▼'}{RESET}",
        )
        bp = float(ta.get("Bull_Power", 0))
        bep = float(ta.get("Bear_Power", 0))
        cls._row(
            "Elder Ray",
            f"Bull:{NEON_GREEN if bp > 0 else NEON_RED}{bp:+.4f}{RESET}  "
            f"Bear:{NEON_GREEN if bep > 0 else NEON_RED}{bep:+.4f}{RESET}",
        )

        # VI. PIVOTS
        cls._section("VI. PIVOT POINTS  (Classic Floor)")
        pp_v = float(ta.get("PP", 0))
        cls._row("Pivot (PP)", f"{NEON_WHITE}{pp_v:.4f}{RESET}")
        for lbl, key in [
            ("Resistance R3", "R3"),
            ("Resistance R2", "R2"),
            ("Resistance R1", "R1"),
        ]:
            val = float(ta.get(key, 0))
            cls._row(lbl, f"{NEON_RED if close < val else NEON_GREEN}{val:.4f}{RESET}")
        for lbl, key in [
            ("Support   S1", "S1"),
            ("Support   S2", "S2"),
            ("Support   S3", "S3"),
        ]:
            val = float(ta.get(key, 0))
            cls._row(lbl, f"{NEON_GREEN if close > val else NEON_RED}{val:.4f}{RESET}")

        # ════════════════════════════════════════════════════════════════
        # VII. ACTIONABLE PLAYBOOK & TRADE SETUP
        # ════════════════════════════════════════════════════════════════
        cls._section("VII. ACTIONABLE PLAYBOOK & TRADE SETUP")

        # 1. Determine Bias
        bias_str = "NEUTRAL / WAIT"
        bias_col = NEON_YELLOW
        bias_desc = "Mixed/weak signals. Wait for trend/flow alignment."

        if ta_pct >= 60 and l2_pct >= 55:
            bias_str = (
                "STRONG LONG"
                if ta_pct >= 75 and l2_pct >= 65
                else "LONG (TREND-ALIGNED)"
            )
            bias_col = NEON_GREEN
            bias_desc = "Trend is Bullish & L2 buying pressure dominant. Buy pullbacks."
        elif ta_pct <= 40 and l2_pct <= 45:
            bias_str = (
                "STRONG SHORT"
                if ta_pct <= 25 and l2_pct <= 35
                else "SHORT (TREND-ALIGNED)"
            )
            bias_col = NEON_RED
            bias_desc = (
                "Trend is Bearish & L2 selling pressure dominant. Short rallies."
            )
        elif ta_pct >= 60 and l2_pct <= 40:
            bias_str = "DIVERGENT (BULLISH TREND / BEARISH FLOW)"
            bias_col = NEON_CYAN
            bias_desc = "Medium-term Trend Up, but L2 distribution. Buy support limits."
        elif ta_pct <= 40 and l2_pct >= 60:
            bias_str = "DIVERGENT (BEARISH TREND / BULLISH FLOW)"
            bias_col = NEON_PURPLE
            bias_desc = (
                "Medium-term Trend Down, but L2 accumulation. Short resistance limits."
            )

        cls._row("Trading Bias", f"{bias_col}{BRIGHT}{bias_str}{RESET}")
        cls._row("Market Context", f"{DIM}{bias_desc}{RESET}")

        # Get pivot levels
        s1_val = float(ta.get("S1", 0.0))
        s2_val = float(ta.get("S2", 0.0))
        r1_val = float(ta.get("R1", 0.0))
        r2_val = float(ta.get("R2", 0.0))

        # Fallbacks
        if s1_val == 0.0 or r1_val == 0.0:
            s1_val = close * 0.995
            s2_val = close * 0.990
            r1_val = close * 1.005
            r2_val = close * 1.010

        bid_walls = ob_met.get("bid_walls", [])
        ask_walls = ob_met.get("ask_walls", [])
        wall_bid_p = bid_walls[0]["price"] if bid_walls else 0.0
        wall_ask_p = ask_walls[0]["price"] if ask_walls else 0.0

        entry_low = 0.0
        entry_high = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        rr_ratio = 0.0

        if "LONG" in bias_str or (
            bias_str.startswith("DIVERGENT") and "BULLISH TREND" in bias_str
        ):
            entry_high = close * 0.999
            entry_low = (
                wall_bid_p
                if (0 < wall_bid_p < close and wall_bid_p >= s2_val)
                else s1_val
            )
            if entry_low >= entry_high:
                entry_low = entry_high * 0.995

            stop_loss = min(s2_val, wall_bid_p * 0.998 if wall_bid_p > 0 else s2_val)
            if stop_loss >= entry_low:
                stop_loss = entry_low * 0.995

            take_profit = r2_val if ta_pct >= 75 else r1_val
            risk = entry_high - stop_loss
            reward = take_profit - entry_high
            rr_ratio = reward / risk if risk > 0 else 0.0

            cls._row(
                "Action Setup", f"{NEON_GREEN}BUY LIMIT SETUP (Bullish Bias){RESET}"
            )
            cls._row(
                "Entry Zone", f"{NEON_GREEN}{entry_low:.4f} - {entry_high:.4f}{RESET}"
            )
            cls._row(
                "Stop Loss (SL)",
                f"{NEON_RED}{stop_loss:.4f}{RESET}  (-{((entry_high - stop_loss) / entry_high * 100):.2f}%)",
            )
            cls._row(
                "Take Profit (TP)",
                f"{NEON_GREEN}{take_profit:.4f}{RESET}  (+{((take_profit - entry_high) / entry_high * 100):.2f}%)",
            )

        elif "SHORT" in bias_str or (
            bias_str.startswith("DIVERGENT") and "BEARISH TREND" in bias_str
        ):
            entry_low = close * 1.001
            entry_high = (
                wall_ask_p if (wall_ask_p > close and wall_ask_p <= r2_val) else r1_val
            )
            if entry_high <= entry_low:
                entry_high = entry_low * 1.005

            stop_loss = max(r2_val, wall_ask_p * 1.002 if wall_ask_p > 0 else r2_val)
            if stop_loss <= entry_high:
                stop_loss = entry_high * 1.005

            take_profit = s2_val if ta_pct <= 25 else s1_val
            risk = stop_loss - entry_low
            reward = entry_low - take_profit
            rr_ratio = reward / risk if risk > 0 else 0.0

            cls._row(
                "Action Setup", f"{NEON_RED}SELL LIMIT SETUP (Bearish Bias){RESET}"
            )
            cls._row(
                "Entry Zone", f"{NEON_RED}{entry_low:.4f} - {entry_high:.4f}{RESET}"
            )
            cls._row(
                "Stop Loss (SL)",
                f"{NEON_RED}{stop_loss:.4f}{RESET}  (-{((stop_loss - entry_low) / entry_low * 100):.2f}%)",
            )
            cls._row(
                "Take Profit (TP)",
                f"{NEON_GREEN}{take_profit:.4f}{RESET}  (+{((entry_low - take_profit) / entry_low * 100):.2f}%)",
            )

        else:
            cls._row(
                "Action Setup",
                f"{NEON_YELLOW}RANGE REVERSION SETUP (Grid trade S2 to R2){RESET}",
            )
            cls._row("Buy Grid Zone", f"{NEON_GREEN}{s2_val:.4f} - {s1_val:.4f}{RESET}")
            cls._row("Sell Grid Zone", f"{NEON_RED}{r1_val:.4f} - {r2_val:.4f}{RESET}")
            rr_ratio = (
                (r1_val - s1_val) / (s1_val - s2_val)
                if (s1_val > s2_val and r1_val > s1_val)
                else 1.5
            )

        if rr_ratio > 0:
            rr_col = (
                NEON_GREEN
                if rr_ratio >= 2.0
                else NEON_YELLOW
                if rr_ratio >= 1.2
                else NEON_RED
            )
            cls._row(
                "Risk/Reward (R:R)",
                f"{rr_col}{rr_ratio:.2f}:1{RESET} "
                + (
                    f"({NEON_GREEN}Favorable{RESET})"
                    if rr_ratio >= 2.0
                    else f"({NEON_YELLOW}Moderate{RESET})"
                    if rr_ratio >= 1.2
                    else f"({NEON_RED}Unfavorable{RESET})"
                ),
            )

        print(f"\n{NEON_BLUE}╚{'═' * BOX_WIDTH}╝{RESET}")

        # Restore stdout
        sys.stdout = original_stdout

        # Get full report layout
        report = captured_stdout.getvalue()

        # Write to actual console
        sys.stdout.write(report)
        sys.stdout.flush()

        # Write logs
        try:
            import json
            import re

            log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "bot_logs"
            )
            os.makedirs(log_dir, exist_ok=True)
            symbol = ta.get("symbol", "unknown").upper()

            # 1. Plain text observatory layout log
            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
            clean_report = ansi_escape.sub("", report)
            with open(
                os.path.join(log_dir, f"observatory_{symbol}.log"),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(clean_report + "\n" + "=" * BOX_WIDTH + "\n")

            # 2. Structured telemetry event log
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "close": close,
                "regime": regime,
                "ta_score": f"{ta_bulls}/{ta_bears}",
                "l2_score": f"{l2_bulls}/{l2_bears}",
                "spread_bps": ob_met.get("spread_bps", 0.0),
                "ob_imbalance": ob_met.get("ob_imbalance", 0.0),
                "taker_imbalance": tr_met.get("flow_imbalance", 0.0),
                "vpin": tr_met.get("vpin_proxy", 0.0),
                "funding_rate": fi_met.get("funding_rate", 0.0),
                "open_interest": fi_met.get("open_interest", 0.0),
            }
            with open(
                os.path.join(log_dir, f"telemetry_{symbol}.jsonl"),
                "a",
                encoding="utf-8",
            ) as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  MASTER ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════


class MarketOrchestrator:
    """
    Coordinates all data fetching and analysis pipelines.
    Runs the continuous observation loop with graceful error recovery.
    """

    def __init__(
        self,
        symbol: str,
        interval: str,
        delay: int,
        use_tor: bool = False,
        once: bool = False,
        json_out: bool = False,
    ) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.delay = delay
        self.use_tor = use_tor
        self.once = once
        self.json_out = json_out
        self.client = BybitRealmClient(use_tor=use_tor)

        # Dynamic Endpoint Failover / DNS check
        base_url = "https://api.bybit.com"
        try:
            import socket

            # Attempt to resolve main domain
            socket.gethostbyname("api.bybit.com")
        except Exception:
            # Fall back to backup endpoint if name resolution fails
            base_url = "https://api.bytick.com"

        self.tech = TechnicalObservatory(symbol, interval)
        self.tech.base_url = base_url
        self.l2 = OrderbookIntelligence(symbol, base_url=base_url)
        self.errors = 0
        self.max_errors = 8
        self._last_ta_metrics = None
        self._last_ob_metrics = None
        self._last_tr_metrics = None
        self._last_fi_metrics = None

    def run_cycle(self) -> None:
        """Execute one complete data fetch and render cycle."""

        # ── Fetch All Data Sources ────────────────────────────────────────
        # Technical klines
        try:
            df = self.tech.fetch_klines(limit=300)
            ta_metrics = self.tech.build_indicators(df)
            ta_metrics["symbol"] = self.symbol
            if ta_metrics and float(ta_metrics.get("close", 0.0)) > 0:
                self._last_ta_metrics = ta_metrics
            elif self._last_ta_metrics:
                ta_metrics = self._last_ta_metrics
        except Exception:
            if self._last_ta_metrics:
                ta_metrics = self._last_ta_metrics
            else:
                raise

        close = float(ta_metrics.get("close", 0.0))

        # L2 Orderbook
        try:
            ob_raw = self.l2.fetch_orderbook(depth=50)
            ob_metrics = self.l2.analyze_orderbook(ob_raw)
            if ob_metrics and ob_metrics.get("best_bid", 0.0) > 0:
                self._last_ob_metrics = ob_metrics
            elif self._last_ob_metrics:
                ob_metrics = self._last_ob_metrics
        except Exception:
            if self._last_ob_metrics:
                ob_metrics = self._last_ob_metrics
            else:
                ob_metrics = self.l2.analyze_orderbook({})

        # Recent trades
        try:
            trades = self.l2.fetch_recent_trades(limit=200)
            tr_metrics = self.l2.analyze_trades(trades)
            if tr_metrics and tr_metrics.get("trade_count", 0) > 0:
                self._last_tr_metrics = tr_metrics
            elif self._last_tr_metrics:
                tr_metrics = self._last_tr_metrics
        except Exception:
            if self._last_tr_metrics:
                tr_metrics = self._last_tr_metrics
            else:
                tr_metrics = self.l2.analyze_trades([])

        # Funding, OI, L/S
        try:
            ticker = self.l2.fetch_funding_rate()
            oi_hist = self.l2.fetch_open_interest_history(interval="5min", limit=30)
            ls_ratio = self.l2.fetch_long_short_ratio(period="5min", limit=20)
            fi_metrics = self.l2.analyze_funding_oi(
                ticker, oi_hist, ls_ratio, close_price=close
            )

            # If ticker API failed or returned 0 (e.g. mark_price = 0), fall back to cached
            if (
                not ticker or not fi_metrics or fi_metrics.get("mark_price", 0.0) == 0.0
            ) and self._last_fi_metrics:
                fi_metrics = self._last_fi_metrics.copy()
                fi_metrics["close_price"] = close
            elif fi_metrics and fi_metrics.get("mark_price", 0.0) > 0.0:
                self._last_fi_metrics = fi_metrics
        except Exception:
            if self._last_fi_metrics:
                fi_metrics = self._last_fi_metrics.copy()
                fi_metrics["close_price"] = close
            else:
                fi_metrics = self.l2.analyze_funding_oi({}, [], [], close_price=close)

        # ── OI + Price Divergence Signal ──────────────────────────────────
        oi_trend = fi_metrics.get("oi_trend", 0.0)
        prev_c = float(ta_metrics.get("prev_close", close))
        price_up = close > prev_c

        if price_up and oi_trend > 0:
            fi_metrics["oi_price_signal"] = "↑ Price + ↑ OI = TREND CONFIRMED"
        elif price_up and oi_trend <= 0:
            fi_metrics["oi_price_signal"] = "↑ Price + ↓ OI = WEAK / REVERSAL RISK"
        elif not price_up and oi_trend > 0:
            fi_metrics["oi_price_signal"] = "↓ Price + ↑ OI = SHORT SQUEEZE RISK"
        else:
            fi_metrics["oi_price_signal"] = "↓ Price + ↓ OI = DISTRIBUTION / WEAK"

        # ── L2 Signal Composite ───────────────────────────────────────────
        l2_bulls, l2_bears, l2_label, l2_notes = self.l2.l2_signal_score(
            ob_metrics, tr_metrics, fi_metrics, close
        )

        # Add OI/Price signal to notes
        l2_notes.append(f"OI/Price Signal: {fi_metrics['oi_price_signal']}")
        l2_notes.append(f"L/S Sentiment:   {fi_metrics.get('ls_sentiment', 'N/A')}")

        # ── Render ────────────────────────────────────────────────────────
        OutputRenderer.display_metrics(
            ta=ta_metrics,
            ob_met=ob_metrics,
            tr_met=tr_metrics,
            fi_met=fi_metrics,
            l2_bulls=l2_bulls,
            l2_bears=l2_bears,
            l2_label=l2_label,
            l2_notes=l2_notes,
            json_out=self.json_out,
        )

    def run(self) -> None:
        """Main loop with exponential backoff on errors."""
        print(
            f"\n{NEON_GREEN}Observatory v3.0 activated.  "
            f"Streaming {self.symbol} @ {self.interval}m  "
            f"every {self.delay}s.{RESET}"
        )
        print(
            f"{NEON_YELLOW}Data sources: Klines | L2 Orderbook | Trades | "
            f"Funding | OI | L/S Ratio{RESET}\n"
        )
        time.sleep(1.5)

        while True:
            try:
                self.run_cycle()
                if self.once:
                    break
                self.errors = 0
                time.sleep(self.delay)

            except RuntimeError as api_err:
                self.errors += 1
                print(f"\n{NEON_RED}[API Error #{self.errors}] {api_err}{RESET}")
                if self.errors >= self.max_errors:
                    print(f"{NEON_RED}Max errors reached. Exiting.{RESET}")
                    sys.exit(1)
                wait = min(5 * self.errors, 60)
                print(f"{NEON_YELLOW}Retry in {wait}s...{RESET}")
                time.sleep(wait)

            except Exception as exc:
                self.errors += 1
                print(f"\n{NEON_RED}[Loop Error #{self.errors}] {exc}{RESET}")
                time.sleep(5)


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════


def main() -> None:
    try:
        print(f"\n{NEON_CYAN}{'═' * 54}{RESET}")
        print(f"{NEON_PURPLE}{BRIGHT}  NEON MARKET TREND OBSERVATORY  v3.0{RESET}")
        print(
            f"{NEON_CYAN}  L2 Orderbook | Microstructure | Funding | OI | Flow{RESET}"
        )
        print(f"{NEON_CYAN}{'═' * 54}{RESET}\n")

        symbol = (
            input(f"{NEON_CYAN}Target symbol   (default: BTCUSDT) : {RESET}").strip()
            or "BTCUSDT"
        )
        interval = (
            input(f"{NEON_CYAN}Timeframe       (1/5/15/60/D)      : {RESET}").strip()
            or "15"
        )
        delay_s = input(
            f"{NEON_CYAN}Refresh seconds (default: 20)      : {RESET}"
        ).strip()
        delay = int(delay_s) if delay_s.isdigit() else 20

        orchestrator = MarketOrchestrator(symbol, interval, delay)
        orchestrator.run()

    except KeyboardInterrupt:
        print(
            f"\n\n{NEON_PURPLE}The observatory screen goes dark.  "
            f"Safe travels, seeker.{RESET}\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()


def _coerce_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y"}


def run(
    symbol: str = None,
    interval: str = None,
    delay: int = None,
    use_tor=None,
    once=None,
    json_out=None,
):
    if not symbol:
        symbol = os.getenv("BYBIT_SYMBOL", "BTCUSDT")
    symbol = str(symbol).strip().upper()

    if interval is None:
        interval = str(os.getenv("BYBIT_INTERVAL", "15"))
    interval = str(interval).strip()

    if delay is None:
        delay = os.getenv("BYBIT_DELAY", "20")
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = 20

    use_tor = _coerce_bool(use_tor, False)
    once = _coerce_bool(once, True)
    json_out = _coerce_bool(json_out, True)

    orchestrator = MarketOrchestrator(
        symbol,
        interval,
        delay,
        use_tor=use_tor,
        once=once,
        json_out=json_out,
    )
    orchestrator.run()
    return f"Analysis complete for {symbol}"
