import sys

sys.path.insert(0, "tools")
import bybit_wbta

try:
    print(
        bybit_wbta.run(
            symbol="BTCUSDT",
            interval="15",
            delay=20,
            use_tor="false",
            once="true",
            json_out="true",
        )
    )
except Exception as e:
    print(f"Exception: {e}")
