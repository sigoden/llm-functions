import importlib.util
import os
import sys
import unittest

# Add the tools directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Load bybit-realm.py dynamically
spec = importlib.util.spec_from_file_location(
    "bybit_realm",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../bybit-realm.py")),
)
bybit_realm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bybit_realm)

# Import the sanitization functions
from bybit_realm import (
    _sanitize_bool,
    _sanitize_order_type,
    _sanitize_position_idx,
    _sanitize_trigger_by,
)


class TestSanitization(unittest.TestCase):
    def test_sanitize_order_type(self):
        self.assertEqual(_sanitize_order_type("limit"), "Limit")
        self.assertEqual(_sanitize_order_type("Market"), "Market")
        self.assertEqual(_sanitize_order_type("0"), "Limit")
        self.assertEqual(_sanitize_order_type(None), "Limit")
        self.assertEqual(_sanitize_order_type("invalid"), "Limit")

    def test_sanitize_position_idx(self):
        self.assertEqual(_sanitize_position_idx(0), 0)
        self.assertEqual(_sanitize_position_idx(1), 1)
        self.assertEqual(_sanitize_position_idx(2), 2)
        self.assertEqual(_sanitize_position_idx(3), 1)  # Fallback
        self.assertEqual(_sanitize_position_idx("a"), 1)  # Fallback

    def test_sanitize_bool(self):
        self.assertTrue(_sanitize_bool(True))
        self.assertTrue(_sanitize_bool("true"))
        self.assertTrue(_sanitize_bool("1"))
        self.assertFalse(_sanitize_bool("false"))
        self.assertFalse(_sanitize_bool(0))

    def test_sanitize_trigger_by(self):
        self.assertEqual(_sanitize_trigger_by("MarkPrice"), "MarkPrice")
        self.assertEqual(_sanitize_trigger_by("LastPrice"), "LastPrice")
        self.assertEqual(_sanitize_trigger_by("mark"), "MarkPrice")
        self.assertEqual(_sanitize_trigger_by("something"), "LastPrice")
        self.assertEqual(_sanitize_trigger_by("0"), "MarkPrice")


if __name__ == "__main__":
    unittest.main()
