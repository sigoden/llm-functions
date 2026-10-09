#!/usr/bin/env python3
# ==============================================================================
# bybit_wbta.py — Bybit WhaleBot Technical Observatory v3.3-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Display · Tor/Proxy Support
#
# @describe Market Trend Observatory v3.3 — Complete L2 Orderbook Microstructure,
#           Trade Flow Analysis, Funding/OI Telemetry, 35+ Technical Indicators,
#           and Multi-Factor Long/Short Signal Engine. Read-only telemetry tool.
#
# @meta require-tools python3
#
# @option --symbol <TEXT>                Trading pair (e.g. BTCUSDT, ETHUSDT; default: BTCUSDT)
# @option --interval <TEXT>              Kline timeframe: 1, 5, 15, 60, 240, D (default: 15)
# @option --delay <NUM>                  Refresh seconds between cycles in loop mode (default: 20)
# @flag   --use-tor                      Enable Tor SOCKS5 proxy routing
# @flag   --once                         Run a single analysis cycle and exit (default)
# @flag   --json-out                     Output raw JSON payload for LLM/system integration
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import enum
import io
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Ensure current directory is in sys.path
CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Proxy Integration
try:
    import proxy_utils

    proxy_utils.set_proxy_environment()
except ImportError:
    proxy_utils = None

# Math / Data Science Imports
import numpy as np
import pandas as pd
import requests

try:
    import scientific_calculator as calc
except ImportError:
    calc = None

__version__ = "3.3.0-ASCENDED"
__all__ = [
    "MarketOrchestrator",
    "OrderbookIntelligence",
    "OutputRenderer",
    "TechnicalObservatory",
    "__version__",
    "run",
]

# ==============================================================================
# SECTION 1: Exit Codes & Custom JSON Serializer
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2


class ToolJSONEncoder(json.JSONEncoder):
    """Safe JSON encoder handling Decimal, Path, Enum, datetime, complex, and NumPy types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, (datetime, timedelta)):
            return obj.isoformat() if isinstance(obj, datetime) else obj.total_seconds()
        if isinstance(obj, complex):
            return {"real": obj.real, "imag": obj.imag}
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        if isinstance(obj, np.generic):
            return obj.item()
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Layout Constants
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
NEON_BLUE = "\033[38;5;33m"
NEON_WHITE = "\033[38;5;255m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

BOX_WIDTH = 78
LABEL_W = 22

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def format_precision(value: Any, step: Any, rounding=ROUND_HALF_UP) -> str:
    """Format numeric values to an exact step decimal string without binary float drift."""
    if value is None or value == "":
        return "N/A"
    if step is None or float(step) <= 0:
        return format(Decimal(str(value)), "f")
    try:
        val_d = Decimal(str(value))
        step_d = Decimal(str(step))
        quantized = val_d.quantize(step_d, rounding=rounding)
        return format(quantized, "f")
    except (InvalidOperation, ValueError):
        return str(value)


# ==============================================================================
# SECTION 3: L2 ORDERBOOK INTELLIGENCE ENGINE
# ==============================================================================


class OrderbookIntelligence:
    """Fetches and analyzes L2 orderbook, trade flow, funding rate, and open interest."""

    def __init__(
        self,
        symbol: str,
        base_url: str = "https://api.bybit.com",
        use_tor: bool = False,
    ) -> None:
        self.symbol = symbol.upper()
        self.base_url = base_url
        self.use_tor = use_tor

    def _get(self, endpoint: str, params: dict) -> dict:
        """GET request wrapper with backup domain failover (api.bybit.com -> api.bytick.com)."""
        proxies = None
        if self.use_tor:
            proxies = {
                "http": "socks5h://127.0.0.1:9050",
                "https": "socks5h://127.0.0.1:9050",
            }
        elif proxy_utils:
            proxies = proxy_utils.get_proxies()

        for _attempt in range(2):
            try:
                resp = requests.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    timeout=10,
                    proxies=proxies,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") == 0:
                    return data.get("result", {})
            except Exception:
                pass
            if self.base_url == "https://api.bybit.com":
                self.base_url = "https://api.bytick.com"
            else:
                break
        return {}

    def fetch_orderbook(self, depth: int = 50) -> dict:
        result = self._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": self.symbol, "limit": depth},
        )
        if not result:
            return {}
        bids = [[float(p), float(s)] for p, s in result.get("b", [])]
        asks = [[float(p), float(s)] for p, s in result.get("a", [])]
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        return {"bids": bids, "asks": asks, "ts": result.get("ts", 0)}

    def fetch_recent_trades(self, limit: int = 200) -> list[dict]:
        result = self._get(
            "/v5/market/recent-trade",
            {"category": "linear", "symbol": self.symbol, "limit": limit},
        )
        trades = []
        for t in result.get("list", []):
            try:
                trades.append(
                    {
                        "price": float(t.get("price", 0)),
                        "size": float(t.get("size", 0)),
                        "side": str(t.get("side", "Buy")).capitalize(),
                        "ts": int(t.get("time", 0)),
                    }
                )
            except (ValueError, TypeError):
                continue
        return trades

    def fetch_funding_rate(self) -> dict:
        result = self._get(
            "/v5/market/tickers", {"category": "linear", "symbol": self.symbol}
        )
        ticker_list = result.get("list", [{}])
        if not ticker_list:
            return {}
        t = ticker_list[0]

        def sf(v) -> float:
            try:
                return float(v) if v else 0.0
            except:
                return 0.0

        return {
            "funding_rate": sf(t.get("fundingRate")),
            "next_funding_time": int(t.get("nextFundingTime", 0) or 0),
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
    ) -> list[dict]:
        result = self._get(
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": self.symbol,
                "intervalTime": interval,
                "limit": limit,
            },
        )
        return [
            {"oi": float(i.get("openInterest", 0)), "ts": int(i.get("timestamp", 0))}
            for i in result.get("list", [])
        ]

    def fetch_long_short_ratio(
        self, period: str = "5min", limit: int = 20
    ) -> list[dict]:
        result = self._get(
            "/v5/market/account-ratio",
            {
                "category": "linear",
                "symbol": self.symbol,
                "period": period,
                "limit": limit,
            },
        )
        return [
            {
                "buy_ratio": float(i.get("buyRatio", 0.5)),
                "sell_ratio": float(i.get("sellRatio", 0.5)),
                "ts": int(i.get("timestamp", 0)),
            }
            for i in result.get("list", [])
        ]

    def analyze_orderbook(self, ob: dict) -> dict[str, Any]:
        if not ob or not ob.get("bids") or not ob.get("asks"):
            return self._empty_ob_metrics()

        bids, asks = ob["bids"], ob["asks"]
        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid_price = (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else 0.0

        spread_abs = best_ask - best_bid
        spread_bps = (spread_abs / mid_price * 10000.0) if mid_price else 0.0

        bid_sizes = np.array([b[1] for b in bids])
        ask_sizes = np.array([a[1] for a in asks])
        tot_bid = float(bid_sizes.sum())
        tot_ask = float(ask_sizes.sum())
        tot_vol = tot_bid + tot_ask

        ob_imbalance = (tot_bid - tot_ask) / tot_vol if tot_vol > 0 else 0.0

        depth_levels = {}
        bid_prices = np.array([b[0] for b in bids])
        ask_prices = np.array([a[0] for a in asks])

        for pct in [0.1, 0.5, 1.0, 2.0]:
            bd = float(bid_sizes[bid_prices >= mid_price * (1.0 - pct / 100.0)].sum())
            ad = float(ask_sizes[ask_prices <= mid_price * (1.0 + pct / 100.0)].sum())
            depth_levels[f"bid_depth_{pct}pct"] = bd
            depth_levels[f"ask_depth_{pct}pct"] = ad

        bid_mean = float(bid_sizes.mean()) if len(bid_sizes) > 0 else 1.0
        ask_mean = float(ask_sizes.mean()) if len(ask_sizes) > 0 else 1.0

        bid_walls = [
            {"price": b[0], "size": b[1]} for b in bids if b[1] >= bid_mean * 3.0
        ]
        ask_walls = [
            {"price": a[0], "size": a[1]} for a in asks if a[1] >= ask_mean * 3.0
        ]
        bid_walls.sort(key=lambda x: x["size"], reverse=True)
        ask_walls.sort(key=lambda x: x["size"], reverse=True)

        best_bid_sz = bids[0][1] if bids else 1.0
        best_ask_sz = asks[0][1] if asks else 1.0
        micro_price = (
            (
                (best_bid * best_ask_sz + best_ask * best_bid_sz)
                / (best_bid_sz + best_ask_sz)
            )
            if (best_bid_sz + best_ask_sz) > 0
            else mid_price
        )

        # Slippage calculations
        slip_buy = self._calc_slippage(asks, 10000.0, mid_price)
        slip_sell = self._calc_slippage(bids, 10000.0, mid_price)

        impact_buy = self._price_impact_pct(asks, 100000.0, mid_price)
        impact_sell = self._price_impact_pct(bids, 100000.0, mid_price)

        sup_lvl = self._find_absorption_level(bids, mid_price, 5.0)
        res_lvl = self._find_absorption_level(asks, mid_price, 5.0)

        ask_1pct = depth_levels.get("ask_depth_1.0pct", 1.0)
        bid_1pct = depth_levels.get("bid_depth_1.0pct", 1.0)
        depth_ratio_1pct = bid_1pct / ask_1pct if ask_1pct > 0 else 1.0
        ob_skew = (
            (bid_1pct - ask_1pct) / (bid_1pct + ask_1pct)
            if (bid_1pct + ask_1pct) > 0
            else 0.0
        )
        liq_density = (
            (bid_1pct + ask_1pct) / (mid_price * 0.01) if mid_price > 0 else 0.0
        )

        # Volume profile S/R bins
        bin_w = max(0.0001, mid_price * 0.001)
        bid_bins, ask_bins = {}, {}
        for p, s in bids:
            bp = round(p / bin_w) * bin_w
            bid_bins[bp] = bid_bins.get(bp, 0.0) + s
        for p, s in asks:
            ap = round(p / bin_w) * bin_w
            ask_bins[ap] = ask_bins.get(ap, 0.0) + s

        ob_sups = sorted(
            [{"price": p, "volume": v} for p, v in bid_bins.items()],
            key=lambda x: x["volume"],
            reverse=True,
        )[:3]
        ob_reses = sorted(
            [{"price": p, "volume": v} for p, v in ask_bins.items()],
            key=lambda x: x["volume"],
            reverse=True,
        )[:3]
        ob_sups.sort(key=lambda x: x["price"], reverse=True)
        ob_reses.sort(key=lambda x: x["price"])

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "micro_price": micro_price,
            "spread_abs": spread_abs,
            "spread_bps": spread_bps,
            "total_bid_vol": tot_bid,
            "total_ask_vol": tot_ask,
            "ob_imbalance": ob_imbalance,
            "depth_levels": depth_levels,
            "bid_walls": bid_walls[:3],
            "ask_walls": ask_walls[:3],
            "slip_buy_bps": slip_buy,
            "slip_sell_bps": slip_sell,
            "impact_buy_100k": impact_buy,
            "impact_sell_100k": impact_sell,
            "support_level": sup_lvl,
            "resistance_level": res_lvl,
            "depth_ratio_1pct": depth_ratio_1pct,
            "ob_skew_1pct": ob_skew,
            "liq_density": liq_density,
            "ob_supports": ob_sups,
            "ob_resistances": ob_reses,
        }

    @staticmethod
    def _calc_slippage(levels: list, notional: float, mid_price: float) -> float:
        remaining, total_cost, total_qty = notional, 0.0, 0.0
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
        return round(abs((total_cost / total_qty) - mid_price) / mid_price * 10000.0, 2)

    @staticmethod
    def _price_impact_pct(levels: list, notional: float, mid_price: float) -> float:
        remaining, last_price = notional, mid_price
        for price, size in levels:
            if remaining <= 0:
                break
            remaining -= size * price
            last_price = price
        if mid_price == 0:
            return 0.0
        return abs(last_price - mid_price) / mid_price * 100.0

    @staticmethod
    def _find_absorption_level(
        levels: list, mid_price: float, threshold_mult: float = 5.0
    ) -> float:
        if not levels:
            return 0.0
        avg_size = np.mean([l[1] for l in levels]) if levels else 1.0
        threshold = avg_size * threshold_mult
        cum = 0.0
        for price, size in levels:
            cum += size
            if cum >= threshold:
                return float(price)
        return float(levels[-1][0]) if levels else 0.0

    @staticmethod
    def _empty_ob_metrics() -> dict:
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
            "ob_skew_1pct": 0.0,
            "liq_density": 0.0,
            "ob_supports": [],
            "ob_resistances": [],
        }

    def analyze_trades(self, trades: list[dict]) -> dict[str, Any]:
        if not trades:
            return self._empty_trade_metrics()

        df = pd.DataFrame(trades)
        df["is_buy"] = df["side"].str.upper() == "BUY"

        total_vol = float(df["size"].sum())
        buy_vol = float(df.loc[df["is_buy"], "size"].sum())
        sell_vol = float(df.loc[~df["is_buy"], "size"].sum())
        flow_imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0

        size_mean, size_std = df["size"].mean(), df["size"].std()
        large_thresh = size_mean + 2.0 * size_std
        large_trades = df[df["size"] >= large_thresh]
        lg_bv = float(large_trades.loc[large_trades["is_buy"], "size"].sum())
        lg_sv = float(large_trades.loc[~large_trades["is_buy"], "size"].sum())
        lg_tot = lg_bv + lg_sv
        large_flow_imb = (lg_bv - lg_sv) / lg_tot if lg_tot > 0 else 0.0

        trade_count = len(df)
        avg_trade_size = total_vol / trade_count if trade_count > 0 else 0.0
        largest_trade = float(df["size"].max())

        # VPIN proxy
        if total_vol > 0:
            v_bucket = total_vol / 10.0
            vpin_list, cbv, csv = [], 0.0, 0.0
            for _, r in df.iterrows():
                if r["is_buy"]:
                    cbv += r["size"]
                else:
                    csv += r["size"]
                if (cbv + csv) >= v_bucket:
                    vpin_list.append(abs(cbv - csv) / (cbv + csv))
                    cbv, csv = 0.0, 0.0
            vpin_proxy = float(np.mean(vpin_list)) if vpin_list else 0.0
        else:
            vpin_proxy = 0.0

        price_velo = (
            (df.tail(5)["price"].mean() - df.head(5)["price"].mean())
            / df.head(5)["price"].mean()
            * 100.0
            if len(df) >= 10 and df.head(5)["price"].mean() > 0
            else 0.0
        )

        recent_20 = df.tail(20)
        recent_bv = float(recent_20.loc[recent_20["is_buy"], "size"].sum())
        recent_sv = float(recent_20.loc[~recent_20["is_buy"], "size"].sum())
        agg_ratio = (
            (recent_bv - recent_sv) / (recent_bv + recent_sv)
            if (recent_bv + recent_sv) > 0
            else 0.0
        )

        df["cost"] = df["price"] * df["size"]
        vwap_recent = float(df["cost"].sum() / total_vol) if total_vol > 0 else 0.0
        realized_vol = (
            float(df["price"].pct_change().std() * math.sqrt(trade_count)) * 100.0
            if trade_count > 1
            else 0.0
        )
        time_span = (df["ts"].max() - df["ts"].min()) / 1000.0
        trade_density = trade_count / time_span if time_span > 0 else float(trade_count)

        return {
            "total_vol": total_vol,
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "flow_imbalance": flow_imbalance,
            "large_flow_imb": large_flow_imb,
            "large_buy_vol": lg_bv,
            "large_sell_vol": lg_sv,
            "large_trade_count": len(large_trades),
            "avg_trade_size": avg_trade_size,
            "largest_trade": largest_trade,
            "trade_count": trade_count,
            "vpin_proxy": vpin_proxy,
            "price_velocity": price_velo,
            "aggressor_ratio": agg_ratio,
            "vwap_recent": vwap_recent,
            "realized_vol": realized_vol,
            "trade_density": trade_density,
        }

    @staticmethod
    def _empty_trade_metrics() -> dict:
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
            "vwap_recent": 0.0,
            "realized_vol": 0.0,
            "trade_density": 0.0,
        }

    def analyze_funding_oi(
        self,
        ticker: dict,
        oi_history: list[dict],
        ls_ratio: list[dict],
        close_price: float = 1.0,
    ) -> dict[str, Any]:
        fr = ticker.get("funding_rate", 0.0)
        mark_p = ticker.get("mark_price", close_price) or close_price

        # Open Interest trend
        oi_val = ticker.get("open_interest", 0.0)
        oi_trend, oi_chg = 0.0, 0.0
        if oi_history and len(oi_history) >= 2:
            arr = np.array([x["oi"] for x in oi_history], dtype=float)
            oi_chg = (arr[0] - arr[-1]) / arr[-1] * 100.0 if arr[-1] else 0.0
            oi_trend = float(np.polyfit(range(len(arr)), arr, 1)[0])

        # Long/Short Net
        buy_ratio, sell_ratio, ls_net, ls_trend = 0.5, 0.5, 0.0, 0.0
        if ls_ratio:
            buy_ratio = ls_ratio[0].get("buy_ratio", 0.5)
            sell_ratio = ls_ratio[0].get("sell_ratio", 0.5)
            ls_net = buy_ratio - sell_ratio
            if len(ls_ratio) >= 3:
                recent_buy = np.mean([x["buy_ratio"] for x in ls_ratio[:3]])
                older_buy = np.mean([x["buy_ratio"] for x in ls_ratio[-3:]])
                ls_trend = recent_buy - older_buy

        ls_sent = (
            "CROWDED LONGS (Contrarian Risk)"
            if buy_ratio > 0.65
            else "CROWDED SHORTS (Squeeze Risk)"
            if sell_ratio > 0.65
            else "BALANCED"
        )

        # Precise Liquidation Estimates (Longs below Mark vs Shorts above Mark)
        mmr_10x, mmr_20x, mmr_50x, mmr_100x = 0.005, 0.010, 0.020, 0.025
        liq_long_10x = mark_p * (1.0 - 1.0 / 10.0 + mmr_10x)
        liq_short_10x = mark_p * (1.0 + 1.0 / 10.0 - mmr_10x)
        liq_long_20x = mark_p * (1.0 - 1.0 / 20.0 + mmr_20x)
        liq_short_20x = mark_p * (1.0 + 1.0 / 20.0 - mmr_20x)
        liq_long_50x = mark_p * (1.0 - 1.0 / 50.0 + mmr_50x)
        liq_short_50x = mark_p * (1.0 + 1.0 / 50.0 - mmr_50x)
        liq_long_100x = mark_p * (1.0 - 1.0 / 100.0 + mmr_100x)
        liq_short_100x = mark_p * (1.0 + 1.0 / 100.0 - mmr_100x)

        index_p = ticker.get("index_price", mark_p)
        basis = (mark_p - index_p) / index_p * 100.0 if index_p > 0 else 0.0
        turnover = ticker.get("turnover_24h", 0.0)
        est_lev = (
            ticker.get("open_interest_val", 0.0) / turnover if turnover > 0 else 0.0
        )

        mins_to_funding = (
            max(
                0,
                (ticker.get("next_funding_time", 0) - int(time.time() * 1000)) // 60000,
            )
            if ticker.get("next_funding_time", 0) > 0
            else -1
        )

        return {
            "funding_rate": fr,
            "funding_rate_pct": fr * 100.0,
            "funding_annualized": fr * 3 * 365 * 100.0,
            "funding_sentiment": "LONGS PAY (Bearish)"
            if fr > 0.001
            else "SHORTS PAY (Bullish)"
            if fr < -0.001
            else "NEUTRAL",
            "funding_bias": "BEARISH"
            if fr > 0.001
            else "BULLISH"
            if fr < -0.001
            else "NEUTRAL",
            "mins_to_funding": mins_to_funding,
            "open_interest": oi_val,
            "open_interest_value": ticker.get("open_interest_val", 0.0),
            "oi_change_pct": oi_chg,
            "oi_trend": oi_trend,
            "ls_buy_ratio": buy_ratio,
            "ls_sell_ratio": sell_ratio,
            "ls_net": ls_net,
            "ls_trend": ls_trend,
            "ls_sentiment": ls_sent,
            "volume_24h": ticker.get("volume_24h", 0.0),
            "turnover_24h": turnover,
            "high_24h": ticker.get("high_24h", 0.0),
            "low_24h": ticker.get("low_24h", 0.0),
            "prev_price_24h": ticker.get("prev_price_24h", 0.0),
            "mark_price": mark_p,
            "index_price": index_p,
            "mark_index_basis": basis,
            "est_leverage_ratio": est_lev,
            "liq_est_long_10x": round(liq_long_10x, 4),
            "liq_est_short_10x": round(liq_short_10x, 4),
            "liq_est_long_20x": round(liq_long_20x, 4),
            "liq_est_short_20x": round(liq_short_20x, 4),
            "liq_est_long_50x": round(liq_long_50x, 4),
            "liq_est_short_50x": round(liq_short_50x, 4),
            "liq_est_long_100x": round(liq_long_100x, 4),
            "liq_est_short_100x": round(liq_short_100x, 4),
        }

    def l2_signal_score(
        self, ob_met: dict, tr_met: dict, fi_met: dict, close_price: float
    ) -> tuple[int, int, str, list[str]]:
        bulls, bears = 0, 0
        notes = []

        ob_imb = ob_met.get("ob_imbalance", 0.0)
        if ob_imb > 0.15:
            bulls += 2
            notes.append(f"OB Imbalance: BID-HEAVY ({ob_imb:+.2f})")
        elif ob_imb < -0.15:
            bears += 2
            notes.append(f"OB Imbalance: ASK-HEAVY ({ob_imb:+.2f})")

        dr = ob_met.get("depth_ratio_1pct", 1.0)
        if dr > 1.3:
            bulls += 1
            notes.append(f"Depth Ratio 1%: Bids {dr:.2f}x deeper than Asks")
        elif dr < 0.7:
            bears += 1
            notes.append(f"Depth Ratio 1%: Asks {1 / dr:.2f}x deeper than Bids")

        flow_imb = tr_met.get("flow_imbalance", 0.0)
        if flow_imb > 0.2:
            bulls += 2
            notes.append(f"Trade Flow: TAKER BUY DOMINATED ({flow_imb:+.2f})")
        elif flow_imb < -0.2:
            bears += 2
            notes.append(f"Trade Flow: TAKER SELL DOMINATED ({flow_imb:+.2f})")

        fr = fi_met.get("funding_rate", 0.0)
        if fr < -0.0005:
            bulls += 1
            notes.append(f"Funding Negative ({fr * 100:.4f}%): Short Squeeze Potential")
        elif fr > 0.001:
            bears += 1
            notes.append(f"Funding Elevated ({fr * 100:.4f}%): Long Overcrowded")

        total = bulls + bears
        ratio = bulls / total if total > 0 else 0.5
        label = (
            "STRONG BULL FLOW"
            if ratio >= 0.7
            else "MILD BULL FLOW"
            if ratio >= 0.55
            else "STRONG BEAR FLOW"
            if ratio <= 0.3
            else "MILD BEAR FLOW"
            if ratio <= 0.45
            else "NEUTRAL FLOW"
        )

        return bulls, bears, label, notes


# ==============================================================================
# SECTION 4: TECHNICAL OBSERVATORY — COMPLETE 35+ TA INDICATORS ENGINE
# ==============================================================================


class TechnicalObservatory:
    """Pure mathematical derivation of 35+ Technical Analysis Indicators."""

    def __init__(
        self, symbol: str, interval: str = "15", use_tor: bool = False
    ) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.base_url = "https://api.bybit.com"
        self.use_tor = use_tor
        self._prev_close: float | None = None

    def fetch_klines(self, limit: int = 300) -> pd.DataFrame:
        proxies = (
            {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
            if self.use_tor
            else (proxy_utils.get_proxies() if proxy_utils else None)
        )
        params = {
            "category": "linear",
            "symbol": self.symbol,
            "interval": self.interval,
            "limit": limit,
        }

        for attempt in range(2):
            try:
                resp = requests.get(
                    f"{self.base_url}/v5/market/kline",
                    params=params,
                    timeout=15,
                    proxies=proxies,
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data.get("result", {}).get("list", [])
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
                return df.iloc[::-1].reset_index(drop=True)
            except Exception:
                if self.base_url == "https://api.bybit.com" and attempt == 0:
                    self.base_url = "https://api.bytick.com"
                    continue
                raise

    # ── Mathematical Primitives ──────────────────────────────────────────────
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

    def _calculate_lrs(self, s: pd.Series, p: int = 14) -> pd.Series:
        x = np.arange(p, dtype=float)
        x_dev = x - x.mean()
        x_var = (x_dev**2).sum()

        def get_slope(y):
            if np.isnan(y).any():
                return np.nan
            y_dev = y - y.mean()
            return (x_dev * y_dev).sum() / x_var

        return s.rolling(p).apply(get_slope, raw=True)

    def _calculate_fisher(self, df: pd.DataFrame, p: int = 9) -> pd.Series:
        h = df["high"].rolling(p).max()
        l = df["low"].rolling(p).min()
        hl2 = (df["high"] + df["low"]) / 2.0
        val = 0.33 * 2.0 * ((hl2 - l) / (h - l).replace(0.0, np.nan) - 0.5)
        val_smooth = (
            val.fillna(0.0).ewm(span=3, adjust=False).mean().clip(-0.999, 0.999)
        )
        return 0.5 * np.log(
            (1.0 + val_smooth) / (1.0 - val_smooth).replace(0.0, np.nan)
        )

    def _calculate_uo(self, df: pd.DataFrame) -> pd.Series:
        c, l, h = df["close"], df["low"], df["high"]
        prev_c = c.shift(1)
        min_l_prev_c = pd.concat([l, prev_c], axis=1).min(axis=1)
        max_h_prev_c = pd.concat([h, prev_c], axis=1).max(axis=1)
        bp = c - min_l_prev_c
        tr = max_h_prev_c - min_l_prev_c
        avg7 = bp.rolling(7).sum() / tr.rolling(7).sum().replace(0.0, np.nan)
        avg14 = bp.rolling(14).sum() / tr.rolling(14).sum().replace(0.0, np.nan)
        avg28 = bp.rolling(28).sum() / tr.rolling(28).sum().replace(0.0, np.nan)
        return (100.0 * (4.0 * avg7 + 2.0 * avg14 + avg28) / 7.0).fillna(50.0)

    def _calculate_rvgi(
        self, df: pd.DataFrame, p: int = 10
    ) -> tuple[pd.Series, pd.Series]:
        co = df["close"] - df["open"]
        hl = df["high"] - df["low"]
        num = (co + 2.0 * co.shift(1) + 2.0 * co.shift(2) + co.shift(3)) / 6.0
        den = (hl + 2.0 * hl.shift(1) + 2.0 * hl.shift(2) + hl.shift(3)) / 6.0
        rvgi = num.rolling(p).sum() / den.rolling(p).sum().replace(0.0, np.nan)
        signal = (
            rvgi + 2.0 * rvgi.shift(1) + 2.0 * rvgi.shift(2) + rvgi.shift(3)
        ) / 6.0
        return rvgi.fillna(0.0), signal.fillna(0.0)

    def _calculate_chv(self, df: pd.DataFrame, p: int = 10) -> pd.Series:
        hl_diff = df["high"] - df["low"]
        ema_hl = hl_diff.ewm(span=p, adjust=False).mean()
        return (
            100.0 * (ema_hl - ema_hl.shift(p)) / ema_hl.shift(p).replace(0.0, np.nan)
        ).fillna(0.0)

    def _calculate_rvi(self, s: pd.Series, p: int = 14) -> pd.Series:
        sd = s.rolling(10).std()
        change = s.diff()
        up_sd = sd.where(change > 0, 0.0)
        down_sd = sd.where(change < 0, 0.0)
        u_smooth = up_sd.rolling(p).sum()
        d_smooth = down_sd.rolling(p).sum()
        return (100.0 * (u_smooth / (u_smooth + d_smooth).replace(0.0, np.nan))).fillna(
            50.0
        )

    def _calculate_kvo(
        self, df: pd.DataFrame, fast: int = 34, slow: int = 55, sig: int = 13
    ) -> tuple[pd.Series, pd.Series]:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        trend = np.sign(tp.diff().fillna(0.0))
        sv = df["volume"] * trend
        fast_ema = sv.ewm(span=fast, adjust=False).mean()
        slow_ema = sv.ewm(span=slow, adjust=False).mean()
        kvo = fast_ema - slow_ema
        kvo_sig = kvo.ewm(span=sig, adjust=False).mean()
        return kvo, kvo_sig

    def _calculate_mass_index(
        self, df: pd.DataFrame, p1: int = 9, p2: int = 25
    ) -> pd.Series:
        hl = df["high"] - df["low"]
        ema1 = hl.ewm(span=p1, adjust=False).mean()
        ema2 = ema1.ewm(span=p1, adjust=False).mean()
        return (ema1 / ema2.replace(0.0, np.nan)).rolling(p2).sum().fillna(0.0)

    def _calculate_chop(self, df: pd.DataFrame, p: int = 14) -> pd.Series:
        tr = self._true_range(df)
        sum_tr = tr.rolling(p).sum()
        max_h = df["high"].rolling(p).max()
        min_l = df["low"].rolling(p).min()
        return (
            100.0
            * np.log10(sum_tr / (max_h - min_l).replace(0.0, np.nan))
            / np.log10(p)
        ).fillna(50.0)

    def _calculate_keltner(
        self, df: pd.DataFrame, ep: int = 20, ap: int = 10, mult: float = 2.0
    ):
        atr = self._rma(self._true_range(df), ap)
        mid = self._ema(df["close"], ep)
        return mid + mult * atr, mid, mid - mult * atr

    def _calculate_ulcer(self, s: pd.Series, p: int = 14) -> pd.Series:
        rm = s.rolling(p).max()
        return np.sqrt(
            ((100.0 * (s - rm) / rm.replace(0.0, np.nan)) ** 2).rolling(p).mean()
        )

    def _calculate_sar(
        self, df: pd.DataFrame, step: float = 0.02, maxstep: float = 0.20
    ):
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

    def _calculate_adx(self, df: pd.DataFrame, p: int = 14):
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

    def _calculate_supertrend(self, df: pd.DataFrame, p: int = 10, mult: float = 3.0):
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

    def _calculate_vortex(self, df: pd.DataFrame, p: int = 14):
        tr = self._true_range(df)
        atr_s = tr.rolling(p).sum()
        vp = (df["high"] - df["low"].shift()).abs().rolling(p).sum()
        vm = (df["low"] - df["high"].shift()).abs().rolling(p).sum()
        return (vp / atr_s.replace(0.0, np.nan)).fillna(0.0), (
            vm / atr_s.replace(0.0, np.nan)
        ).fillna(0.0)

    def _calculate_aroon(self, df: pd.DataFrame, p: int = 25):
        au = (
            df["high"]
            .rolling(p + 1)
            .apply(lambda x: (np.argmax(x) / p) * 100, raw=True)
        )
        ad = (
            df["low"].rolling(p + 1).apply(lambda x: (np.argmin(x) / p) * 100, raw=True)
        )
        return au.fillna(0.0), ad.fillna(0.0)

    def _calculate_elder_ray(self, df: pd.DataFrame, p: int = 13):
        e = self._ema(df["close"], p)
        return df["high"] - e, df["low"] - e

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        return (tp * df["volume"]).cumsum() / df["volume"].cumsum().replace(0.0, np.nan)

    def _calculate_pivots(self, df: pd.DataFrame) -> dict[str, float]:
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
    def _confluence_score(data: dict) -> tuple[int, int, str]:
        close = data.get("close", 0.0)
        bulls, bears = 0, 0
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
        label = (
            "STRONG BULL"
            if ratio >= 0.7
            else "MILD BULL"
            if ratio >= 0.55
            else "STRONG BEAR"
            if ratio <= 0.3
            else "MILD BEAR"
            if ratio <= 0.45
            else "NEUTRAL"
        )
        return bulls, bears, label

    def build_indicators(self, df: pd.DataFrame) -> dict[str, Any]:
        close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
        df = df.copy()

        # Trend moving averages
        df["SMA_20"] = close.rolling(20).mean()
        df["SMA_50"] = close.rolling(50).mean()
        df["SMA_200"] = close.rolling(200).mean()
        df["EMA_20"] = self._ema(close, 20)
        df["HMA_20"] = self._calculate_hma(close, 20)
        df["KAMA_10"] = self._calculate_kama(close, 10, 2, 30)
        df["DEMA_20"] = self._calculate_dema(close, 20)
        df["TEMA_20"] = self._calculate_tema(close, 20)

        # Ichimoku & VWAP
        df["Tenkan"] = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
        df["Kijun"] = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
        df["Senkou_A"] = ((df["Tenkan"] + df["Kijun"]) / 2.0).shift(26)
        df["Senkou_B"] = ((high.rolling(52).max() + low.rolling(52).min()) / 2.0).shift(
            26
        )
        df["VWAP"] = self._calculate_vwap(df)

        # Oscillators
        df["RSI_14"] = self._calculate_rsi(close, 14)
        rm = df["RSI_14"].rolling(14).min()
        rx = df["RSI_14"].rolling(14).max()
        df["StochRSI_K"] = 100.0 * (
            (df["RSI_14"] - rm) / (rx - rm).replace(0.0, np.nan)
        ).fillna(0.0)
        df["StochRSI_D"] = df["StochRSI_K"].rolling(3).mean()

        ef = self._ema(close, 12)
        es = self._ema(close, 26)
        df["MACD_Line"] = ef - es
        df["MACD_Signal"] = self._ema(df["MACD_Line"], 9)
        df["MACD_Hist"] = df["MACD_Line"] - df["MACD_Signal"]

        tp = (high + low + close) / 3.0
        stp = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        df["CCI_20"] = (tp - stp) / (0.015 * mad.replace(0.0, np.nan))

        hh = high.rolling(14).max()
        ll = low.rolling(14).min()
        df["WillR_14"] = -100.0 * (
            (hh - close) / (hh - ll).replace(0.0, np.nan)
        ).fillna(0.0)
        df["ROC_12"] = self._calculate_roc(close, 12)
        df["DPO_20"] = self._calculate_dpo(close, 20)
        df["TRIX_15"] = self._calculate_trix(close, 15)
        df["CMO_14"] = self._calculate_cmo(close, 14)
        df["Coppock"] = self._calculate_coppock(close)

        sk_r = 100.0 * ((close - ll) / (hh - ll).replace(0.0, np.nan)).fillna(0.0)
        df["Stoch_K"] = sk_r.rolling(3).mean()
        df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()

        # Volatility
        bbs = close.rolling(20).std()
        df["BB_Upper"] = df["EMA_20"] + 2.0 * bbs
        df["BB_Lower"] = df["EMA_20"] - 2.0 * bbs
        df["BB_Width"] = (
            (df["BB_Upper"] - df["BB_Lower"])
            / df["EMA_20"].replace(0.0, np.nan)
            * 100.0
        )

        tr = self._true_range(df)
        df["ATR_14"] = self._rma(tr, 14)
        df["Donchian_U"] = high.rolling(20).max()
        df["Donchian_L"] = low.rolling(20).min()
        df["KC_Upper"], df["KC_Mid"], df["KC_Lower"] = self._calculate_keltner(
            df, 20, 10, 2.0
        )
        df["Ulcer_14"] = self._calculate_ulcer(close, 14)
        df["SAR"], df["SAR_Dir"] = self._calculate_sar(df, 0.02, 0.20)

        # Volume Dynamics
        obv_d = np.sign(close.diff().fillna(0.0))
        df["OBV"] = (obv_d * volume).cumsum()

        tp2 = (high + low + close) / 3.0
        mf = tp2 * volume
        pmf = mf.where(tp2.diff() > 0.0, 0.0)
        nmf = mf.where(tp2.diff() < 0.0, 0.0)
        mfr = pmf.rolling(14).sum() / nmf.rolling(14).sum().replace(0.0, np.nan)
        df["MFI_14"] = 100.0 - (100.0 / (1.0 + mfr.fillna(0.0)))

        hlr = high - low
        mfm = np.where(hlr != 0, ((close - low) - (high - close)) / hlr, 0.0)
        df["CMF_20"] = (mfm * volume).rolling(20).sum() / volume.rolling(
            20
        ).sum().replace(0.0, np.nan)

        bv = volume.where(close > df["open"], 0.0)
        sv = volume.where(close <= df["open"], 0.0)
        df["Vol_Delta"] = bv - sv

        # Directional Indicators
        df["ADX"], df["PlusDI"], df["MinusDI"] = self._calculate_adx(df, 14)
        df["SuperTrend"], df["SuperTrend_Dir"] = self._calculate_supertrend(df, 10, 3.0)
        df["VI_Plus"], df["VI_Minus"] = self._calculate_vortex(df, 14)
        df["Aroon_Up"], df["Aroon_Down"] = self._calculate_aroon(df, 25)
        df["Bull_Power"], df["Bear_Power"] = self._calculate_elder_ray(df, 13)

        # 10 Advanced Metrics
        df["CHOP_14"] = self._calculate_chop(df, 14)
        df["LRS_14"] = self._calculate_lrs(close, 14)
        df["Fisher_9"] = self._calculate_fisher(df, 9)
        df["UO"] = self._calculate_uo(df)
        df["RVGI"], df["RVGI_Sig"] = self._calculate_rvgi(df, 10)
        df["CHV_10"] = self._calculate_chv(df, 10)
        df["RVI_14"] = self._calculate_rvi(close, 14)
        df["KVO"], df["KVO_Sig"] = self._calculate_kvo(df)
        df["Mass_Index"] = self._calculate_mass_index(df)
        df["ROCV_14"] = self._calculate_roc(volume, 14)

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


# ==============================================================================
# SECTION 5: MASTER ORCHESTRATOR & SIGNAL ENGINE
# ==============================================================================


class MarketOrchestrator:
    """Coordinates L2 orderbook, TA observatory, and Actionable Trade Setup generation."""

    def __init__(
        self,
        symbol: str,
        interval: str = "15",
        delay: int = 20,
        use_tor: bool = False,
        once: bool = True,
        json_out: bool = True,
        silent: bool = False,
    ) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.delay = delay
        self.use_tor = use_tor
        self.once = once
        self.json_out = json_out
        self.silent = silent

        self.l2 = OrderbookIntelligence(symbol, use_tor=use_tor)
        self.tech = TechnicalObservatory(symbol, interval=interval, use_tor=use_tor)

    def run_cycle(self) -> dict[str, Any]:
        df = self.tech.fetch_klines(limit=300)
        ta_metrics = self.tech.build_indicators(df)
        close_price = ta_metrics["close"]

        ob_raw = self.l2.fetch_orderbook(depth=50)
        ob_metrics = self.l2.analyze_orderbook(ob_raw)

        trades = self.l2.fetch_recent_trades(limit=200)
        tr_metrics = self.l2.analyze_trades(trades)

        ticker = self.l2.fetch_funding_rate()
        oi_hist = self.l2.fetch_open_interest_history()
        ls_ratio = self.l2.fetch_long_short_ratio()
        fi_metrics = self.l2.analyze_funding_oi(
            ticker, oi_hist, ls_ratio, close_price=close_price
        )

        l2_bulls, l2_bears, l2_label, l2_notes = self.l2.l2_signal_score(
            ob_metrics, tr_metrics, fi_metrics, close_price
        )

        # Multi-factor Signal Score Logic
        net_score = (l2_bulls + ta_metrics["conf_bulls"]) - (
            l2_bears + ta_metrics["conf_bears"]
        )
        regime = ta_metrics["regime"]
        atr = ta_metrics.get("ATR_14", close_price * 0.01)

        mult_tp = 3.0 if "TRENDING" in regime else 2.0
        mult_sl = 1.5 if "TRENDING" in regime else 1.0

        if net_score >= 3:
            signal_action = "LONG"
            side = "Buy"
            entry = (
                ob_metrics["best_bid"] if ob_metrics["best_bid"] > 0 else close_price
            )
            sl = round(entry - (atr * mult_sl), 4)
            tp = round(entry + (atr * mult_tp), 4)
        elif net_score <= -3:
            signal_action = "SHORT"
            side = "Sell"
            entry = (
                ob_metrics["best_ask"] if ob_metrics["best_ask"] > 0 else close_price
            )
            sl = round(entry + (atr * mult_sl), 4)
            tp = round(entry - (atr * mult_tp), 4)
        else:
            signal_action = "HOLD"
            side = "Neutral"
            entry = close_price
            sl = round(close_price - (atr * 1.0), 4)
            tp = round(close_price + (atr * 1.0), 4)

        trading_signal = {
            "action": signal_action,
            "side": side,
            "net_score": net_score,
            "recommended_entry": entry,
            "recommended_tp": tp,
            "recommended_sl": sl,
            "risk_reward_ratio": round(mult_tp / mult_sl, 2),
        }

        return {
            "symbol": self.symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trading_signal": trading_signal,
            "ta": ta_metrics,
            "orderbook": ob_metrics,
            "trades": tr_metrics,
            "funding_and_oi": fi_metrics,
            "l2_signal": {
                "bulls": l2_bulls,
                "bears": l2_bears,
                "label": l2_label,
                "notes": l2_notes,
            },
        }

    def run(self) -> dict[str, Any]:
        res = self.run_cycle()
        if not self.silent and not self.json_out:
            OutputRenderer.display_metrics(
                ta=res["ta"],
                ob_met=res["orderbook"],
                tr_met=res["trades"],
                fi_met=res["funding_and_oi"],
                l2_bulls=res["l2_signal"]["bulls"],
                l2_bears=res["l2_signal"]["bears"],
                l2_label=res["l2_signal"]["label"],
                l2_notes=res["l2_signal"]["notes"],
                json_out=False,
            )
        return res


# ==============================================================================
# SECTION 6: OUTPUT RENDERER & ROUTING
# ==============================================================================


class OutputRenderer:
    @staticmethod
    def display_metrics(
        ta: dict[str, Any],
        ob_met: dict[str, Any],
        tr_met: dict[str, Any],
        fi_met: dict[str, Any],
        l2_bulls: int,
        l2_bears: int,
        l2_label: str,
        l2_notes: list[str],
        json_out: bool = False,
    ) -> None:
        if json_out:
            print(
                json.dumps(
                    {
                        "ta": ta,
                        "orderbook": ob_met,
                        "trades": tr_met,
                        "funding_and_oi": fi_met,
                        "l2_signal": {
                            "bulls": l2_bulls,
                            "bears": l2_bears,
                            "label": l2_label,
                            "notes": l2_notes,
                        },
                    },
                    indent=2,
                    cls=ToolJSONEncoder,
                )
            )
            return

        if not _is_tty():
            return

        close = float(ta.get("close", 0.0))
        regime = ta.get("regime", "UNKNOWN")
        conf_label = ta.get("conf_label", "NEUTRAL")

        _cprint(f"\n{NEON_BLUE}╔{'═' * BOX_WIDTH}╗{RESET}")
        title = f"  BYBIT REALM TECHNICAL OBSERVATORY v{__version__}  "
        _cprint(
            f"{NEON_BLUE}║{NEON_CYAN}{BOLD}{title:<{BOX_WIDTH}}{RESET}{NEON_BLUE}║{RESET}"
        )
        _cprint(f"{NEON_BLUE}╠{'═' * BOX_WIDTH}╣{RESET}")

        _cprint(
            f"{NEON_BLUE}║{RESET} {NEON_YELLOW}Last Price:{RESET} {NEON_GREEN}{BOLD}{close:<16.4f}{RESET} | {NEON_YELLOW}Regime:{RESET} {NEON_PURPLE}{regime}{RESET}"
        )
        _cprint(
            f"{NEON_BLUE}║{RESET} {NEON_YELLOW}TA Signal:{RESET} {conf_label:<17} | {NEON_YELLOW}L2 Signal:{RESET} {l2_label}"
        )
        _cprint(f"{NEON_BLUE}╚{'═' * BOX_WIDTH}╝{RESET}\n")


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in ("/dev/stdout", "/dev/fd/1", "-"):
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 7: PROGRAMMATIC ENTRY POINT & CLI PARSER
# ==============================================================================


def run(
    symbol: str = "BTCUSDT",
    interval: str = "15",
    delay: int = 20,
    use_tor: bool = False,
    once: bool = True,
    json_out: bool = True,
    silent: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    orchestrator = MarketOrchestrator(
        symbol=symbol,
        interval=interval,
        delay=delay,
        use_tor=use_tor,
        once=once,
        json_out=json_out,
        silent=silent,
    )
    result = orchestrator.run()
    write_llm_output(result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bybit_wbta.py",
        description=f"Bybit WhaleBot Technical Observatory v{__version__}",
    )
    parser.add_argument(
        "--symbol", default="BTCUSDT", help="Trading pair symbol (e.g. BTCUSDT)"
    )
    parser.add_argument(
        "--interval", default="15", help="Timeframe interval (1, 5, 15, 60, 240, D)"
    )
    parser.add_argument(
        "--delay", type=int, default=20, help="Refresh delay in seconds"
    )
    parser.add_argument(
        "--use-tor",
        action="store_true",
        default=False,
        dest="use_tor",
        help="Enable Tor SOCKS5 proxy",
    )
    parser.add_argument(
        "--once", action="store_true", default=True, help="Run single cycle and exit"
    )
    parser.add_argument(
        "--json-out",
        action="store_true",
        default=True,
        dest="json_out",
        help="Output raw JSON",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable verbose debug logging",
    )
    return parser


if __name__ == "__main__":
    if "argc_symbol" in os.environ or any(k.startswith("argc_") for k in os.environ):
        sym = os.environ.get("argc_symbol", "BTCUSDT")
        tf = os.environ.get("argc_interval", "15")
        tor = os.environ.get("argc_use_tor", "false").lower() == "true"
        res = run(
            symbol=sym, interval=tf, use_tor=tor, once=True, json_out=True, silent=True
        )
        sys.exit(EXIT_SUCCESS)

    args = _build_parser().parse_args()
    res = run(
        symbol=args.symbol,
        interval=args.interval,
        delay=args.delay,
        use_tor=args.use_tor,
        once=args.once,
        json_out=args.json_out,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(EXIT_SUCCESS)


# Technical indicators helper methods (moved out of main block)
def _calculate_vortex(self, df, p: int = 14):
    au = df["high"].rolling(p + 1).apply(lambda x: (np.argmax(x) / p) * 100, raw=True)
    ad = df["low"].rolling(p + 1).apply(lambda x: (np.argmin(x) / p) * 100, raw=True)
    return au.fillna(0.0), ad.fillna(0.0)


def _calculate_elder_ray(self, df, p: int = 13):
    e = self._ema(df["close"], p)
    return df["high"] - e, df["low"] - e


def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum().replace(0.0, np.nan)


def _calculate_pivots(self, df: pd.DataFrame) -> dict[str, float]:
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
def _confluence_score(data: dict) -> tuple[int, int, str]:
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

    def build_indicators(self, df: pd.DataFrame) -> dict[str, Any]:
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
        self.config.get("elder_ray_period", 13)

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
        # 10 New Advanced Indicators (v3.1)
        df["CHOP_14"] = self._calculate_chop(df, 14)
        df["LRS_14"] = self._calculate_lrs(close, 14)
        df["Fisher_9"] = self._calculate_fisher(df, 9)
        df["UO"] = self._calculate_uo(df)
        df["RVGI"], df["RVGI_Sig"] = self._calculate_rvgi(df, 10)
        df["CHV_10"] = self._calculate_chv(df, 10)
        df["RVI_14"] = self._calculate_rvi(close, 14)
        df["KVO"], df["KVO_Sig"] = self._calculate_kvo(df)
        df["Mass_Index"] = self._calculate_mass_index(df)
        df["ROCV_14"] = self._calculate_rocv(volume, 14)

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
        ta: dict[str, Any],
        ob_met: dict[str, Any],
        tr_met: dict[str, Any],
        fi_met: dict[str, Any],
        l2_bulls: int,
        l2_bears: int,
        l2_label: str,
        l2_notes: list[str],
        json_out: bool = False,
    ) -> None:
        if json_out:
            import json

            print(
                json.dumps(
                    {
                        "ta": ta,
                        "orderbook": ob_met,
                        "trades": tr_met,
                        "funding_and_oi": fi_met,
                        "l2_signal": {
                            "bulls": l2_bulls,
                            "bears": l2_bears,
                            "label": l2_label,
                            "notes": l2_notes,
                        },
                    },
                    indent=2,
                    default=str,
                )
            )
            return

        os.system("cls" if os.name == "nt" else "clear")

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
        ob_skew = ob_met.get("ob_skew_1pct", 0.0)
        liq_dens = ob_met.get("liq_density", 0.0)
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
        cls._row(
            "OB Skew @1%",
            f"{NEON_GREEN if ob_skew > 0 else NEON_RED}{ob_skew:+.3f}{RESET}",
        )
        cls._row("Liq Density", f"{NEON_CYAN}{liq_dens:,.2f}{RESET} Vol/price-unit")

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
        vwap_rec = tr_met.get("vwap_recent", 0.0)
        real_vol = tr_met.get("realized_vol", 0.0)
        tr_dens = tr_met.get("trade_density", 0.0)

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
        cls._row("Sample VWAP", f"{NEON_YELLOW}{vwap_rec:.4f}{RESET}")
        cls._row("Realized Vol", f"{NEON_PURPLE}{real_vol:.4f}%{RESET}")
        cls._row("Trade Density", f"{NEON_CYAN}{tr_dens:.2f}{RESET} trades/sec")

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
        est_lev = fi_met.get("est_leverage_ratio", 0.0)

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
        cls._row("Est. Lev. Ratio", f"{NEON_YELLOW}{est_lev:.4f}{RESET} (OI / 24h Vol)")

        # ── Liquidation Estimates ─────────────────────────────────────────
        cls._section("C.2  ESTIMATED LIQUIDATION CASCADE ZONES")
        print(
            f"  {DIM}(Simplified estimates - not precise per-position liq. prices){RESET}"
        )

        for lev in [10, 20, 50, 100]:
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
        use_tor: bool = True,
        once: bool = False,
        json_out: bool = False,
        silent: bool = False,
    ) -> None:
        self.use_tor = use_tor
        self.once = once
        self.json_out = json_out
        self.silent = silent
        self.symbol = symbol.upper()
        self.interval = interval
        self.delay = delay

        # Dynamic Endpoint Failover / DNS check
        base_url = "https://api.bybit.com"
        if not self.use_tor:
            try:
                import socket

                # Attempt to resolve main domain
                socket.gethostbyname("api.bybit.com")
            except Exception:
                # Fall back to backup endpoint if name resolution fails
                base_url = "https://api.bytick.com"

        self.tech = TechnicalObservatory(symbol, interval, use_tor=self.use_tor)
        self.tech.base_url = base_url
        self.l2 = OrderbookIntelligence(symbol, base_url=base_url, use_tor=self.use_tor)
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

        # ── Actionable Multi-Factor Trading Signal Generation ─────────────
        ta_bulls = ta_metrics.get("conf_bulls", 0)
        ta_bears = ta_metrics.get("conf_bears", 0)
        regime = ta_metrics.get("regime", "RANGING")
        ta_metrics.get("RSI_14", 50.0)
        adx = ta_metrics.get("ADX", 20.0)

        # Microstructure & flow factors
        flow_imb = tr_metrics.get("flow_imbalance", 0.0)
        vpin = tr_metrics.get("vpin_proxy", 0.5)
        oi_trend = fi_metrics.get("oi_trend", 0.0)

        # Weighting factors
        l2_weight = 1.2
        ta_weight = 1.0
        flow_weight = 1.5

        weighted_bulls = (
            (l2_bulls * l2_weight)
            + (ta_bulls * ta_weight)
            + (max(0, flow_imb) * 10 * flow_weight)
        )
        weighted_bears = (
            (l2_bears * l2_weight)
            + (ta_bears * ta_weight)
            + (abs(min(0, flow_imb)) * 10 * flow_weight)
        )

        net_score = weighted_bulls - weighted_bears
        tot_score = weighted_bulls + weighted_bears

        if net_score > 6.0:
            signal_action = "LONG"
            signal_side = "BUY"
            signal_strength = "STRONG_BUY" if net_score > 12.0 else "BUY"
        elif net_score < -6.0:
            signal_action = "SHORT"
            signal_side = "SELL"
            signal_strength = "STRONG_SELL" if net_score < -12.0 else "SELL"
        else:
            signal_action = "HOLD"
            signal_side = "NEUTRAL"
            signal_strength = "NEUTRAL"

        mid_price = ob_metrics.get("mid_price", close)
        atr_val = ta_metrics.get("ATR_14", close * 0.01)

        # Dynamic multiplier based on regime & ADX
        mult_tp = 3.0 if regime == "TRENDING" and adx > 25.0 else 2.0
        mult_sl = 1.5 if regime == "TRENDING" else 1.0

        if signal_action == "LONG":
            recommended_entry = ob_metrics.get("best_bid", mid_price)
            recommended_sl = round(recommended_entry - (atr_val * mult_sl), 2)
            recommended_tp = round(recommended_entry + (atr_val * mult_tp), 2)
            rec_leverage = min(
                20, max(5, int(100 / max(1.0, (atr_val / mid_price * 100))))
            )
        elif signal_action == "SHORT":
            recommended_entry = ob_metrics.get("best_ask", mid_price)
            recommended_sl = round(recommended_entry + (atr_val * mult_sl), 2)
            recommended_tp = round(recommended_entry - (atr_val * mult_tp), 2)
            rec_leverage = min(
                20, max(5, int(100 / max(1.0, (atr_val / mid_price * 100))))
            )
        else:
            recommended_entry = mid_price
            recommended_sl = round(mid_price - (atr_val * 1.0), 2)
            recommended_tp = round(mid_price + (atr_val * 1.0), 2)
            rec_leverage = 1

        signal_confidence = (
            round((max(weighted_bulls, weighted_bears) / tot_score * 100), 2)
            if tot_score > 0
            else 50.0
        )

        trading_signal = {
            "action": signal_action,
            "side": signal_side,
            "signal_strength": signal_strength,
            "confidence_score": signal_confidence,
            "recommended_entry": recommended_entry,
            "recommended_tp": recommended_tp,
            "recommended_sl": recommended_sl,
            "recommended_leverage": rec_leverage,
            "risk_reward_ratio": round(mult_tp / mult_sl, 2),
            "scoring_breakdown": {
                "weighted_bulls": round(weighted_bulls, 2),
                "weighted_bears": round(weighted_bears, 2),
                "net_score": round(net_score, 2),
                "vpin_risk": "HIGH" if vpin > 0.8 else "NORMAL",
            },
            "summary": f"{signal_strength} for {self.symbol} | Net Score: {net_score:+.2f} (Confidence: {signal_confidence}%)",
        }

        # ── Render ────────────────────────────────────────────────────────
        if not getattr(self, "silent", False):
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

        return {
            "trading_signal": trading_signal,
            "ta": ta_metrics,
            "orderbook": ob_metrics,
            "trades": tr_metrics,
            "funding_and_oi": fi_metrics,
            "l2_signal": {
                "bulls": l2_bulls,
                "bears": l2_bears,
                "label": l2_label,
                "notes": l2_notes,
            },
        }

    def run(self) -> Any:
        """Main loop with exponential backoff on errors."""
        if not getattr(self, "silent", False) and not self.json_out:
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

        last_result = None
        while True:
            try:
                last_result = self.run_cycle()
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
        return last_result


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT  (mirrors bbt.py pattern)
# ═════════════════════════════════════════════════════════════════════════════


def _coerce_bool(val: Any, default: bool = False) -> bool:
    """Coerce any truthy string/bool/int value to a Python bool."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y"}


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


def run(
    symbol: str = "BTCUSDT",
    interval: str = "15",
    delay: int = 20,
    use_tor: bool = True,
    once: bool = True,
    json_out: bool = True,
    silent: bool = False,
    **kwargs,
) -> Any:
    """
    Unified entry point — Bybit WhaleBot Technical Observatory.

    Parameters map 1-to-1 to the CLI @option flags defined in the header.
    All parameters have safe defaults so the tool works without arguments.

    Args:
        symbol:   Trading pair (e.g. BTCUSDT). Falls back to BYBIT_SYMBOL env.
        interval: Timeframe (1/5/15/60/240/D). Falls back to BYBIT_INTERVAL env.
        delay:    Refresh seconds between cycles. Falls back to BYBIT_DELAY env.
        use_tor:  Route through Tor SOCKS5 proxy.
        once:     Run a single cycle and exit (True) or loop indefinitely (False).
        json_out: Emit raw JSON instead of the coloured terminal display.
        silent:   Do not print anything to stdout, only return the data.
    """
    # Env-var fallbacks (allow empty-string callers to get defaults)
    if not symbol:
        symbol = os.getenv("BYBIT_SYMBOL", "BTCUSDT")
    symbol = str(symbol).strip().upper()

    if not interval:
        interval = str(os.getenv("BYBIT_INTERVAL", "15"))
    interval = str(interval).strip()

    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = int(os.getenv("BYBIT_DELAY", "20"))

    # Accept raw strings/ints from argc runner
    use_tor = _coerce_bool(use_tor, True)
    once = _coerce_bool(once, True)
    json_out = _coerce_bool(json_out, True)

    orchestrator = MarketOrchestrator(
        symbol,
        interval,
        delay,
        use_tor=use_tor,
        once=once,
        json_out=json_out,
        silent=silent,
    )
    return orchestrator.run()


def main() -> None:
    """Interactive CLI entry point with argparse and argc_* env-var fast path."""
    parser = argparse.ArgumentParser(
        description="Bybit WhaleBot Technical Observatory — Market Trend Observatory v3.0"
    )
    parser.add_argument(
        "--symbol", type=str, default=None, help="Trading pair (e.g. BTCUSDT)"
    )
    parser.add_argument(
        "--interval", type=str, default=None, help="Timeframe (1/5/15/60/240/D)"
    )
    parser.add_argument(
        "--delay", type=int, default=None, help="Refresh seconds (default: 20)"
    )
    parser.add_argument(
        "--use-tor",
        type=str,
        default=None,
        dest="use_tor",
        help='Enable Tor proxy ("true"/"false")',
    )
    parser.add_argument(
        "--once", type=str, default=None, help='Single cycle and exit ("true"/"false")'
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        dest="json_out",
        help='Raw JSON output ("true"/"false")',
    )

    args, _ = parser.parse_known_args()

    # Interactive prompt when launched with no arguments from a TTY
    if len(sys.argv) == 1 and sys.stdin.isatty():
        print(f"\n{NEON_CYAN}{'═' * 54}{RESET}")
        print(f"{NEON_PURPLE}{BRIGHT}  NEON MARKET TREND OBSERVATORY  v3.0{RESET}")
        print(
            f"{NEON_CYAN}  L2 Orderbook | Microstructure | Funding | OI | Flow{RESET}"
        )
        print(f"{NEON_CYAN}{'═' * 54}{RESET}\n")

        args.symbol = (
            input(f"{NEON_CYAN}Target symbol   (default: BTCUSDT) : {RESET}").strip()
            or "BTCUSDT"
        )
        args.interval = (
            input(f"{NEON_CYAN}Timeframe       (1/5/15/60/D)      : {RESET}").strip()
            or "15"
        )
        delay_s = input(
            f"{NEON_CYAN}Refresh seconds (default: 20)      : {RESET}"
        ).strip()
        args.delay = int(delay_s) if delay_s.isdigit() else 20
        if args.once is None:
            args.once = "false"
        if args.json_out is None:
            args.json_out = "false"

    try:
        run(
            symbol=args.symbol,
            interval=args.interval,
            delay=args.delay,
            use_tor=args.use_tor,
            once=args.once,
            json_out=args.json_out,
        )
    except KeyboardInterrupt:
        print(
            f"\n\n{NEON_PURPLE}The observatory screen goes dark.  Safe travels, seeker.{RESET}\n"
        )
        sys.exit(0)
    except Exception as exc:
        if _coerce_bool(args.json_out, True):
            import json

            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            print(f"\n{NEON_RED}[FATAL ERROR] {exc}{RESET}")
        sys.exit(1)


__all__ = ["run"]


if __name__ == "__main__":
    # ── Fast path: argc runner injects parameters as argc_* env vars ──────────
    _argc_vars = {k[5:]: v for k, v in os.environ.items() if k.startswith("argc_")}
    if _argc_vars:
        _argc_kwargs: dict[str, Any] = {}
        _int_keys = {"delay"}
        _bool_keys = {"use_tor", "once", "json_out"}
        for _param, _v in _argc_vars.items():
            if _param in _bool_keys:
                _argc_kwargs[_param] = _v.lower() in ("true", "1", "yes")
            elif _param in _int_keys:
                try:
                    _argc_kwargs[_param] = int(_v)
                except ValueError:
                    _argc_kwargs[_param] = _v
            else:
                _argc_kwargs[_param] = _v
        run(**_argc_kwargs)
        sys.exit(0)

    # ── Standard interactive / argparse path ──────────────────────────────────
    main()
