import sys

sys.path.append(".")
import json

from tools.bybit_terminal import BybitRealm

realm = BybitRealm()
print("--- BALANCE ---")
print(json.dumps(realm.get_wallet_balance(), indent=2))
print("\n--- POSITIONS ---")
print(json.dumps(realm.get_positions(category="linear"), indent=2))
