#!/usr/bin/env python3
"""Offline test suite for the unified Bybit trading package.

Run:  .venv/bin/python -m unittest tests.test_bybit_suite -v
      (or `python -m unittest discover tests`)

All tests run without network access. BYBIT_SYNC_TIME=false is set so the
client constructor never attempts a live time sync.
"""

import io
import json
import os
import random
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("BYBIT_SYNC_TIME", "false")
os.environ.setdefault("PROXY_ENABLED", "false")

from tools.bybit.base import BybitBaseClient, TradingConfig  # noqa: E402
from tools.bybit.market import MarketDataMixin  # noqa: E402
from tools.bybit.price_action import (  # noqa: E402
    PriceActionEngine,
    Signal,
    analyze_price_action,
)
from tools.bybit.backtest import run_backtest  # noqa: E402
from utils.bybit_base import calculate_exit_price, calculate_pnl  # noqa: E402


# ── synthetic candle helpers ────────────────────────────────────────────────


def make_klines(
    drift_fn=lambda i: 0.0,
    start: float = 30000.0,
    n: int = 400,
    seed: int = 7,
    noise_pct: float = 0.004,
) -> list:
    """Generate a random-walk-with-drift OHLCV series (newest-first, Bybit fmt)."""
    rng = random.Random(seed)
    price = start
    klines = []
    ts = 1700000000000
    for i in range(n):
        drift = drift_fn(i)
        noise = price * noise_pct
        o = price + rng.uniform(-noise, noise)
        c = price + drift + rng.uniform(-noise, noise)
        h = max(o, c) + rng.uniform(0, noise * 0.6)
        l = min(o, c) - rng.uniform(0, noise * 0.6)
        v = 100 + rng.uniform(0, 50)
        klines.append([ts, str(o), str(h), str(l), str(c), str(v), "0"])
        ts += 3600 * 1000
        price = c
    return list(reversed(klines))  # newest first


def uptrend(n=400):
    return make_klines(lambda i: 45 if i < n // 2 else 120, start=30000, n=n)


def downtrend(n=400):
    return make_klines(lambda i: -60, start=60000, n=n)


def flat(n=400):
    return make_klines(lambda i: 0, start=50000, n=n)


# ── test cases ──────────────────────────────────────────────────────────────


class TestPnLMath(unittest.TestCase):
    def test_long_pnl(self):
        res = calculate_pnl(100.0, 110.0, 1.0, "Buy", fees=0.0, leverage=1.0)
        self.assertAlmostEqual(res["net_pnl"], 10.0)
        self.assertAlmostEqual(res["roe_pct"], 10.0)

    def test_short_pnl(self):
        res = calculate_pnl(100.0, 90.0, 1.0, "Sell", fees=0.0, leverage=5.0)
        self.assertAlmostEqual(res["net_pnl"], 10.0)
        # margin = 100*1/5 = 20 -> ROE 50%
        self.assertAlmostEqual(res["roe_pct"], 50.0)

    def test_exit_price_for_target(self):
        # long: need exit s.t. (exit-100)*1 - 0 = 5 -> 105
        self.assertAlmostEqual(calculate_exit_price(100, 5, 1, "Buy", 0), 105.0)
        # short: (exit-100)*-1 = 5 -> exit = 95
        self.assertAlmostEqual(calculate_exit_price(100, 5, 1, "Sell", 0), 95.0)


class TestSigningSecurity(unittest.TestCase):
    def test_sign_does_not_leak_key(self):
        cfg = TradingConfig()
        cfg.api_key = "SECRETKEY123"
        cfg.api_secret = "SECRETSECRET456"
        client = BybitBaseClient(cfg)
        buf = io.StringIO()
        with redirect_stderr(buf):
            sig = client._sign("{}", "1700000000000")
        self.assertTrue(sig)
        self.assertNotIn("SECRETKEY123", buf.getvalue())
        self.assertNotIn("SECRETSECRET456", buf.getvalue())

    def test_signed_request_without_credentials_is_gated(self):
        cfg = TradingConfig()
        cfg.api_key = ""
        cfg.api_secret = ""
        client = BybitBaseClient(cfg)
        res = client._request("GET", "/v5/account/info", {}, signed=True)
        self.assertEqual(res.get("status"), "error")
        self.assertEqual(res.get("code"), "NO_CREDENTIALS")


class TestIndicators(unittest.TestCase):
    def setUp(self):
        self.mix = MarketDataMixin()

    def test_ema_on_known_series(self):
        # EMA(2) of [1, 2, 3, 4] with k=2/3: values 1, 1.6667, 2.5556, 3.5185
        klines = [
            [0, "4", "4", "4", "4", "1", "0"],
            [0, "3", "3", "3", "3", "1", "0"],
            [0, "2", "2", "2", "2", "1", "0"],
            [0, "1", "1", "1", "1", "1", "0"],
        ]
        self.mix.get_klines = lambda *a, **k: {"list": klines}
        res = self.mix.calculate_ema("T", "60", 2)
        self.assertAlmostEqual(res["ema"], 3.5185, places=3)

    def test_rsi_extremes(self):
        up = [[0, str(p), str(p), str(p), str(p), "1", "0"] for p in range(30, 0, -1)]
        self.mix.get_klines = lambda *a, **k: {"list": up}
        self.assertAlmostEqual(self.mix.calculate_rsi("T", "60")["rsi"], 100.0, places=1)

    def test_supertrend_direction(self):
        self.mix.get_klines = lambda *a, **k: {"list": uptrend(120)}
        up = self.mix.calculate_supertrend("T", "60")
        self.assertEqual(up["trend"], "Up")
        self.mix.get_klines = lambda *a, **k: {"list": downtrend(120)}
        dn = self.mix.calculate_supertrend("T", "60")
        self.assertEqual(dn["trend"], "Down")

    def test_orderbook_normalization(self):
        # unwrapped shape (what the base client returns)
        unwrapped = {"b": [["100", "2"], ["99", "3"]], "a": [["101", "1"], ["102", "4"]]}
        bids, asks = self.mix._orderbook_levels(unwrapped)
        self.assertEqual(bids, [(100.0, 2.0), (99.0, 3.0)])
        self.assertEqual(asks, [(101.0, 1.0), (102.0, 4.0)])
        # nested shape must also work
        nested = {"result": unwrapped}
        bids2, asks2 = self.mix._orderbook_levels(nested)
        self.assertEqual(bids2, bids)
        self.assertEqual(asks2, asks)
        # garbage must not raise
        self.assertEqual(self.mix._orderbook_levels({}), ([], []))


class TestPriceActionEngine(unittest.TestCase):
    def setUp(self):
        self.eng = PriceActionEngine()

    def test_structure_classification(self):
        up = self.eng.market_structure(uptrend())
        self.assertEqual(up["state"], "UPTREND")
        dn = self.eng.market_structure(downtrend())
        self.assertEqual(dn["state"], "DOWNTREND")

    def test_signal_in_trend(self):
        s_up = self.eng.price_action_signal(uptrend(), min_confidence=50)
        self.assertEqual(s_up.action, "LONG")
        self.assertGreater(s_up.stop_loss, 0)
        self.assertLess(s_up.stop_loss, s_up.entry)  # SL below entry for longs
        self.assertEqual(len(s_up.take_profits), 3)

        s_dn = self.eng.price_action_signal(downtrend(), min_confidence=50)
        self.assertEqual(s_dn.action, "SHORT")
        self.assertGreater(s_dn.stop_loss, s_dn.entry)

    def test_flat_market_signal_gated(self):
        s = self.eng.price_action_signal(flat(), min_confidence=50)
        self.assertEqual(s.action, "WAIT")

    def test_analysis_wrapper(self):
        res = analyze_price_action(uptrend(300), min_confidence=50)
        self.assertEqual(res["status"], "ok")
        self.assertIn("structure", res)
        self.assertIn("zones", res)

    def test_trade_plan_sizing(self):
        signal = Signal("LONG", 72, 30000, 29700, [30300, 30600, 30900], 2.0,
                        "TREND_CONTINUATION")
        plan = self.eng.trade_plan(signal, balance=1000.0, risk_pct=1.0, leverage=1.0)
        self.assertEqual(plan["status"], "ok")
        # risk $10 over $300 stop distance -> qty 0.0333
        self.assertAlmostEqual(plan["qty"], 10 / 300, places=6)
        self.assertAlmostEqual(plan["risk_amount"], 10.0, places=2)

    def test_trade_plan_wait_is_no_trade(self):
        signal = Signal("WAIT", 0, 30000, 30000, [], 0, "NONE")
        plan = self.eng.trade_plan(signal, balance=1000.0)
        self.assertEqual(plan["status"], "no_trade")


class TestBacktest(unittest.TestCase):
    def test_trending_market_is_profitable(self):
        res = run_backtest(uptrend(), risk_pct=1.0, rr=2.0, sl_atr=1.5)
        s = res["summary"]
        self.assertGreater(s["total_trades"], 0)
        self.assertGreater(s["net_pnl"], 0)
        self.assertGreater(s["profit_factor"], 1.0)
        self.assertLess(s["max_drawdown_pct"], 10.0)  # 1% risk per trade

    def test_downtrend_market(self):
        res = run_backtest(downtrend(), risk_pct=1.0, rr=2.0, sl_atr=1.5)
        s = res["summary"]
        self.assertGreater(s["net_pnl"], 0)
        self.assertLess(s["max_drawdown_pct"], 10.0)

    def test_flat_market_losses_bounded(self):
        res = run_backtest(flat(), risk_pct=1.0, rr=2.0, sl_atr=1.5)
        s = res["summary"]
        # chop may lose a little, but never an uncontrolled drawdown
        self.assertLess(s["max_drawdown_pct"], 10.0)
        self.assertGreater(s["final_balance"], 800)


class TestDispatcher(unittest.TestCase):
    def setUp(self):
        # Fixture _request: serve klines for market endpoints only
        fixture_klines = uptrend(300)
        self.orig_request = BybitBaseClient._request

        def fake_request(self, method, endpoint, params=None, json_data=None,
                         signed=True, category="default"):
            if "kline" in endpoint:
                return {"list": fixture_klines}
            return {"status": "error", "msg": "unmocked endpoint: " + endpoint}

        BybitBaseClient._request = fake_request

    def tearDown(self):
        BybitBaseClient._request = self.orig_request

    def test_run_price_action_analysis(self):
        from tools.bybit.terminal import run as realm_run

        res = realm_run(action="price_action_analysis", symbol="BTCUSDT",
                        interval="60", limit=300, min_confidence=50)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["symbol"], "BTCUSDT")
        self.assertEqual(res["signal"]["action"], "LONG")

    def test_run_backtest_pa(self):
        from tools.bybit.terminal import run as realm_run

        res = realm_run(action="backtest_pa", symbol="BTCUSDT", interval="60",
                        limit=300, min_confidence=50)
        self.assertEqual(res["status"], "ok")
        self.assertGreater(res["summary"]["net_pnl"], 0)

    def test_unknown_action(self):
        from tools.bybit.terminal import run as realm_run

        res = realm_run(action="definitely_not_an_action")
        self.assertEqual(res["status"], "error")


if __name__ == "__main__":
    unittest.main()
