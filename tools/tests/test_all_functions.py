import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

# Load bybit-terminal.py dynamically
file_path = Path(__file__).parent.parent / "bybit-terminal.py"
spec = importlib.util.spec_from_file_location("bybit_terminal", file_path)
bybit_terminal = importlib.util.module_from_spec(spec)
sys.modules["bybit_terminal"] = bybit_terminal
spec.loader.exec_module(bybit_terminal)

BybitRealm = bybit_terminal.BybitRealm
TradingConfig = bybit_terminal.TradingConfig


class TestBybitRealmPortfolio(unittest.TestCase):
    def setUp(self):
        config = TradingConfig()
        self.bot = BybitRealm(config)

    def test_panic_close(self):
        # Mock dependencies
        self.bot.cancel_all_orders = MagicMock(return_value={"status": "ok"})
        self.bot.get_positions = MagicMock(
            return_value={"list": [{"symbol": "BTCUSDT", "size": "0.1", "side": "Buy"}]}
        )
        self.bot.place_order = MagicMock(return_value={"status": "ok"})

        result = self.bot.panic_close()

        self.assertEqual(result["status"], "ok")
        self.bot.cancel_all_orders.assert_called_once()
        self.bot.place_order.assert_called_once_with(
            symbol="BTCUSDT",
            side="Sell",
            qty=0.1,
            order_type="Market",
            category="linear",
        )

    def test_bulk_update_tp_sl(self):
        self.bot.get_positions = MagicMock(
            return_value={"list": [{"symbol": "BTCUSDT", "size": "0.1"}]}
        )
        self.bot.set_trading_stop = MagicMock(return_value={"status": "ok"})

        result = self.bot.bulk_update_tp_sl(tp=60000.0, sl=40000.0)

        self.assertEqual(result["status"], "ok")
        self.bot.set_trading_stop.assert_called_with(
            symbol="BTCUSDT", take_profit=60000.0, stop_loss=40000.0, category="linear"
        )

    def test_get_account_summary(self):
        self.bot.get_wallet_balance = MagicMock(return_value={"USDT": "1000"})
        self.bot.get_positions = MagicMock(return_value={"list": []})

        result = self.bot.get_account_summary()

        self.assertIn("balance", result)
        self.assertIn("positions", result)
        self.bot.get_wallet_balance.assert_called_once()

    def test_get_pnl_summary(self):
        self.bot.get_pnl_history = MagicMock(
            return_value={
                "list": [
                    {
                        "closedPnl": "10.0",
                        "updatedTime": str(
                            int((datetime.now(timezone.utc).timestamp()) * 1000)
                        ),
                    }
                ]
            }
        )
        result = self.bot.get_pnl_summary(days=1)
        self.assertEqual(result["total_realized_pnl"], 10.0)

    def test_set_tp_sl(self):
        self.bot.set_trading_stop = MagicMock(return_value={"status": "ok"})
        result = self.bot.set_tp_sl("BTCUSDT", tp=60000.0, sl=40000.0)
        self.assertEqual(result["status"], "ok")
        self.bot.set_trading_stop.assert_called_with(
            symbol="BTCUSDT", take_profit=60000.0, stop_loss=40000.0, category="linear"
        )

    def test_check_risk_limit(self):
        # Default MAX is 1000
        result = self.bot.check_risk_limit("BTCUSDT", 0.01, 50000)  # 500
        self.assertEqual(result["status"], "ok")

        result = self.bot.check_risk_limit("BTCUSDT", 1.0, 50000)  # 50000
        self.assertEqual(result["status"], "error")

    def test_get_open_positions_summary(self):
        self.bot.get_positions = MagicMock(
            return_value={
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "size": "0.1",
                        "side": "Buy",
                        "avgPrice": "50000",
                        "markPrice": "51000",
                        "unrealisedPnl": "100",
                    }
                ]
            }
        )
        result = self.bot.get_open_positions_summary()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["summary"]), 1)

    def test_calculate_rsi(self):
        dummy_klines = [["", "", "", "", str(i * 100)] for i in range(50)]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_rsi("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("rsi", result)

    def test_calculate_ema(self):
        dummy_klines = [["", "", "", "", str(i * 100)] for i in range(50)]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_ema("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("ema", result)

    def test_calculate_macd(self):
        dummy_klines = [["", "", "", "", str(i * 100)] for i in range(200)]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_macd("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("macd", result)

    def test_scan_scalping_opportunities(self):
        # Conditions for BUY_REVERSION: price < bb, rsi < 35, price > vwap, stoch < 20
        self.bot.calculate_rsi = MagicMock(return_value={"rsi": 30})
        self.bot.calculate_ema = MagicMock(return_value={"ema": 49000})
        self.bot.calculate_bollinger_bands = MagicMock(return_value={"lower": 48000})
        self.bot.calculate_vwap = MagicMock(return_value={"vwap": 46000})
        self.bot.calculate_stochastic = MagicMock(return_value={"k": 10})  # stoch < 20
        self.bot.get_ticker = MagicMock(return_value={"list": [{"lastPrice": "47500"}]})

        result = self.bot.scan_scalping_opportunities("BTCUSDT")
        self.assertEqual(result["signal"], "BUY_REVERSION")

    def test_calculate_bollinger_bands(self):
        dummy_klines = [["", "", "", "", str(i * 100)] for i in range(50)]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_bollinger_bands("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("upper", result)
        self.assertIn("lower", result)

    def test_get_volume_at_price(self):
        self.bot.get_orderbook = MagicMock(
            return_value={"result": {"b": [["50000", "1"]], "a": [["50001", "1"]]}}
        )
        result = self.bot.get_volume_at_price("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("bids", result)
        self.assertIn("asks", result)

    def test_get_orderbook_analysis(self):
        self.bot.get_orderbook = MagicMock(
            return_value={
                "result": {
                    "b": [["50000", "10"], ["49990", "1"]],
                    "a": [["50001", "1"], ["50010", "10"]],
                }
            }
        )
        result = self.bot.get_orderbook_analysis("BTCUSDT", depth=2)
        self.assertEqual(result["status"], "ok")
        self.assertIn("volume_profile", result)
        self.assertEqual(len(result["volume_profile"]["bid_tiers"]), 2)

    def test_calculate_atr(self):
        # dummy klines: [startTime, open, high, low, close, ...] -> highs[2], lows[3], close[4]
        dummy_klines = [["", "", "500", "400", "450", "100"] for _ in range(20)]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_atr("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("atr", result)

    def test_calculate_stochastic(self):
        dummy_klines = [
            ["", "", str(i * 10), str(i * 5), str(i * 7), "100"] for i in range(50)
        ]
        self.bot.get_klines = MagicMock(return_value={"list": dummy_klines})
        result = self.bot.calculate_stochastic("BTCUSDT")
        self.assertEqual(result["status"], "ok")
        self.assertIn("k", result)

    def test_send_telegram_alert(self):
        result = self.bot.send_telegram_alert("test")
        self.assertFalse(result)

    def test_export_trade_history(self):
        self.bot.get_order_history = MagicMock(
            return_value={"list": [{"symbol": "BTCUSDT", "side": "Buy"}]}
        )
        result = self.bot.export_trade_history("BTCUSDT", filename="test_history.csv")
        self.assertEqual(result["status"], "ok")
        import os

        self.assertTrue(os.path.exists("test_history.csv"))
        os.remove("test_history.csv")


if __name__ == "__main__":
    unittest.main()
