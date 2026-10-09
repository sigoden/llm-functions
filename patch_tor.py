with open("tools/bybit_wbta.py") as f:
    code = f.read()

# 1. OrderbookIntelligence
code = code.replace(
    'def __init__(self, symbol: str, base_url: str = "https://api.bybit.com") -> None:\n        self.symbol   = symbol.upper()\n        self.base_url = base_url',
    'def __init__(self, symbol: str, base_url: str = "https://api.bybit.com", use_tor: bool = False) -> None:\n        self.symbol   = symbol.upper()\n        self.base_url = base_url\n        self.use_tor = use_tor',
)
code = code.replace(
    "                    params=params,\n                    timeout=10,\n                )",
    "                    params=params,\n                    timeout=10,\n                    proxies={'http': 'socks5h://127.0.0.1:9050', 'https': 'socks5h://127.0.0.1:9050'} if getattr(self, 'use_tor', False) else None\n                )",
)

# 2. TechnicalObservatory
code = code.replace(
    'def __init__(self, symbol: str, interval: str) -> None:\n        self.symbol   = symbol.upper()\n        self.interval = interval\n        self.base_url = "https://api.bybit.com"',
    'def __init__(self, symbol: str, interval: str, use_tor: bool = False) -> None:\n        self.symbol   = symbol.upper()\n        self.interval = interval\n        self.base_url = "https://api.bybit.com"\n        self.use_tor = use_tor',
)
code = code.replace(
    "                    params=params,\n                    timeout=15,\n                )",
    "                    params=params,\n                    timeout=15,\n                    proxies={'http': 'socks5h://127.0.0.1:9050', 'https': 'socks5h://127.0.0.1:9050'} if getattr(self, 'use_tor', False) else None\n                )",
)

# 3. MarketOrchestrator
code = code.replace(
    "        self.tech   = TechnicalObservatory(symbol, interval)\n        self.l2     = OrderbookIntelligence(symbol, base_url=base_url)",
    "        self.tech   = TechnicalObservatory(symbol, interval, use_tor=self.use_tor)\n        self.l2     = OrderbookIntelligence(symbol, base_url=base_url, use_tor=self.use_tor)",
)

with open("tools/bybit_wbta.py", "w") as f:
    f.write(code)
