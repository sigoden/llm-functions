import re

with open("tools/bybit_wbta_merged.py") as f:
    code = f.read()
code = re.sub(
    r"def __init__\(self, symbol: str, interval: str, delay: int\) -> None:\n        self.symbol     = symbol.upper\(\)\n        self.interval   = interval\n        self.delay      = delay",
    r"def __init__(self, symbol: str, interval: str, delay: int, use_tor: bool = False, once: bool = False, json_out: bool = False) -> None:\n        self.symbol     = symbol.upper()\n        self.interval   = interval\n        self.delay      = delay\n        self.use_tor = use_tor\n        self.once = once\n        self.json_out = json_out\n        self.client = BybitRealmClient(use_tor=use_tor)",
    code,
)

with open("tools/bybit_wbta_merged.py", "w") as f:
    f.write(code)
