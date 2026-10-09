import importlib.util
import sys
from pathlib import Path

# Load bybit-terminal.py dynamically to handle the hyphen in the filename
file_path = Path(__file__).parent.parent / "bybit-terminal.py"
spec = importlib.util.spec_from_file_location("bybit_terminal", file_path)
bybit_terminal = importlib.util.module_from_spec(spec)
sys.modules["bybit_terminal"] = bybit_terminal
spec.loader.exec_module(bybit_terminal)

BybitRealm = bybit_terminal.BybitRealm
TradingConfig = bybit_terminal.TradingConfig

import unittest
from unittest.mock import MagicMock


class TestSmartTrade(unittest.TestCase):
    def test_smart_trade_buy_trending_up(self):
        config = TradingConfig()
        bot = BybitRealm(config)
        # Mock dependencies
        bot.get_market_regime = MagicMock(
            return_value={"status": "ok", "regime": "TRENDING_UP"}
        )
        bot.place_order = MagicMock(return_value={"status": "ok"})

        result = bot.place_smart_trade(
            "BTCUSDT", "Buy", 0.001, 50000.0, tp_pct=1.0, sl_pct=1.0
        )

        self.assertEqual(result["status"], "ok")
        bot.place_order.assert_called_once()
        # Verify TP/SL calculations (Buy 50000 + 1% = 50500, 50000 - 1% = 49500)
        _, kwargs = bot.place_order.call_args
        self.assertEqual(kwargs["take_profit"], 50500.0)
        self.assertEqual(kwargs["stop_loss"], 49500.0)

    def test_smart_trade_buy_trending_down(self):
        config = TradingConfig()
        bot = BybitRealm(config)
        bot.get_market_regime = MagicMock(
            return_value={"status": "ok", "regime": "TRENDING_DOWN"}
        )
        bot.place_order = MagicMock()

        result = bot.place_smart_trade("BTCUSDT", "Buy", 0.001, 50000.0)

        self.assertEqual(result["status"], "error")
        self.assertIn("Refusing Buy", result["msg"])
        bot.place_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
