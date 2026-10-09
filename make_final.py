import re

# 1. Read working file
with open("whalebot.working.py") as f:
    code = f.read()

# 2. Extract Tor proxy and Client from bybit_wbta.py
with open("tools/bybit_wbta.py") as f:
    orig = f.read()

tor_re = re.compile(
    r"(# ═════════════════════════════════════════════════════════════════════════════\n# TOR PROXY MANAGER.*?)\n# ═════════════════════════════════════════════════════════════════════════════\n# ORDERBOOK",
    re.DOTALL,
)
tor_match = tor_re.search(orig)
tor_code = tor_match.group(1) if tor_match else ""

# 3. Inject Tor Proxy and Client
code = re.sub(
    r"# ═════════════════════════════════════════════════════════════════════════════\n#  ORDERBOOK INTELLIGENCE ENGINE",
    tor_code
    + "\n# ═════════════════════════════════════════════════════════════════════════════\n# ORDERBOOK INTELLIGENCE ENGINE",
    code,
)

# 4. Modify OrderbookIntelligence
code = code.replace(
    'class OrderbookIntelligence:\n    """\n    Fetches and analyzes',
    'class OrderbookIntelligence:\n    """\n    Fetches and analyzes',
)
code = re.sub(
    r'def __init__\(self, symbol: str, base_url: str = "https://api\.bybit\.com"\) -> None:\n        self\.symbol   = symbol\.upper\(\)\n        self\.base_url = base_url',
    r"def __init__(self, symbol: str, client) -> None:\n        self.symbol = symbol.upper()\n        self.client = client",
    code,
    flags=re.DOTALL,
)
code = re.sub(
    r"    def _get\(self, endpoint: str, params: Dict\) -> Dict:.*?return \{\}",
    r"",
    code,
    flags=re.DOTALL,
)
code = code.replace("self._get(", "self.client._get(")


# 5. Modify TechnicalObservatory
code = re.sub(
    r'def __init__\(self, symbol: str, interval: str\) -> None:\n        self\.symbol   = symbol\.upper\(\)\n        self\.interval = interval\n        self\.base_url = "https://api\.bybit\.com"',
    r"def __init__(self, symbol: str, interval: str, client) -> None:\n        self.symbol = symbol.upper()\n        self.interval = interval\n        self.client = client",
    code,
    flags=re.DOTALL,
)
code = re.sub(
    r"    def _get\(self, endpoint: str, params: Dict\) -> Dict:.*?return \{\}",
    r"",
    code,
    flags=re.DOTALL,
)

# 6. Modify MarketOrchestrator
code = re.sub(
    r"def __init__\(self, symbol: str, interval: str, delay: int\) -> None:",
    r"def __init__(self, symbol: str, interval: str, delay: int, use_tor: bool = False, once: bool = False, json_out: bool = False) -> None:\n        self.use_tor = use_tor\n        self.once = once\n        self.json_out = json_out\n        self.client = BybitRealmClient(use_tor=use_tor)",
    code,
)
code = code.replace(
    "self.tech   = TechnicalObservatory(symbol, interval)",
    "self.tech   = TechnicalObservatory(symbol, interval, self.client)",
)
code = code.replace(
    "self.l2     = OrderbookIntelligence(symbol)",
    "self.l2     = OrderbookIntelligence(symbol, self.client)",
)

# symbol injection in run_cycle
code = code.replace(
    "ta_metrics = self.tech.build_indicators(df)",
    'ta_metrics = self.tech.build_indicators(df)\n            ta_metrics["symbol"] = self.symbol',
)

# display metrics json_out
code = code.replace(
    "            l2_label=l2_label,\n            l2_notes=l2_notes,\n        )",
    "            l2_label=l2_label,\n            l2_notes=l2_notes,\n            json_out=self.json_out,\n        )",
)
code = code.replace(
    "                self.run_cycle()\n                self.errors = 0",
    "                self.run_cycle()\n                if self.once:\n                    break\n                self.errors = 0",
)

# OutputRenderer display metrics
json_code = """        if json_out:
            import json
            output_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": ta.get("symbol", "unknown").upper(),
                "ta": ta,
                "orderbook": ob_met,
                "trades": tr_met,
                "funding_oi": fi_met,
                "l2_score": {
                    "bulls": l2_bulls,
                    "bears": l2_bears,
                    "label": l2_label,
                    "notes": l2_notes
                }
            }
            def convert_numpy(obj):
                import numpy as np
                if isinstance(obj, dict):
                    return {k: convert_numpy(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_numpy(x) for x in obj]
                elif isinstance(obj, (np.int64, np.int32)):
                    return int(obj)
                elif isinstance(obj, (np.float64, np.float32)):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                return obj
            print(json.dumps(convert_numpy(output_data)))
            return
"""

code = re.sub(
    r'    @classmethod\n    def display_metrics\(\n        cls,\n        ta: Dict\[str, Any\],\n        ob_met: Dict\[str, Any\],\n        tr_met: Dict\[str, Any\],\n        fi_met: Dict\[str, Any\],\n        l2_bulls: int,\n        l2_bears: int,\n        l2_label: str,\n        l2_notes: List\[str\],\n    \) -> None:\n        os\.system\("cls" if os\.name == "nt" else "clear"\)',
    f'    @classmethod\n    def display_metrics(\n        cls,\n        ta: Dict[str, Any],\n        ob_met: Dict[str, Any],\n        tr_met: Dict[str, Any],\n        fi_met: Dict[str, Any],\n        l2_bulls: int,\n        l2_bears: int,\n        l2_label: str,\n        l2_notes: List[str],\n        json_out: bool = False,\n    ) -> None:\n{json_code}\n        os.system("cls" if os.name == "nt" else "clear")',
    code,
)


# run func
run_code = """
def _coerce_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y"}

def run(
    symbol: str = None,
    interval: str = None,
    delay: int = None,
    use_tor=None,
    once=None,
    json_out=None,
):
    if not symbol:
        symbol = os.getenv("BYBIT_SYMBOL", "BTCUSDT")
    symbol = str(symbol).strip().upper()

    if interval is None:
        interval = str(os.getenv("BYBIT_INTERVAL", "15"))
    interval = str(interval).strip()

    if delay is None:
        delay = os.getenv("BYBIT_DELAY", "20")
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = 20

    use_tor  = _coerce_bool(use_tor,  False)
    once     = _coerce_bool(once,     True)
    json_out = _coerce_bool(json_out, True)

    orchestrator = MarketOrchestrator(
        symbol, interval, delay,
        use_tor=use_tor, once=once, json_out=json_out,
    )
    orchestrator.run()
    return f"Analysis complete for {symbol}"
"""
code = code + run_code

with open("tools/bybit_wbta.py", "w") as f:
    f.write(code)
