import sys
from pathlib import Path

sys.path.append(
    str(Path("/data/data/com.termux/files/home/.config/aichat/llm-functions/tools/"))
)
from bybit_terminal import BybitRealm


def test_functions():
    bot = BybitRealm()
    methods = [
        "get_wallet_balance",
        "get_positions",
        "get_account_info",
        "get_fee_rate",
        "get_ticker",
        "get_orderbook",
        "calculate_rsi",
        "calculate_ema",
        "calculate_macd",
    ]

    print(f"Testing {len(methods)} methods...")

    for method_name in methods:
        try:
            method = getattr(bot, method_name)
            # Use a dummy symbol for market data methods
            if method_name in [
                "get_ticker",
                "get_orderbook",
                "calculate_rsi",
                "calculate_ema",
                "calculate_macd",
            ]:
                result = method(symbol="BTCUSDT")
            else:
                result = method()

            print(f"PASS: {method_name}")
        except Exception as e:
            print(f"FAIL: {method_name} - Error: {e}")


if __name__ == "__main__":
    test_functions()
