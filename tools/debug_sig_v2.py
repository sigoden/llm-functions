import inspect
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from bybit.terminal import BybitRealm

bot = BybitRealm()
method = bot.batch_place_orders
sig = inspect.signature(method)
print(f"Signature: {sig}")

params = {"orders": []}
try:
    # Pass as kwargs to test if bound method works
    method(**params)
    print("Call successful (with empty orders)")
except Exception as e:
    import traceback

    print(f"Call failed: {e}")
    traceback.print_exc()
