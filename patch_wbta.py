import re
import sys

# Read the fully working whalebot
with open("whalebot.working.py") as f:
    code = f.read()

# Read the Tor proxy / json / run code from bybit_wbta
with open("tools/bybit_wbta.py") as f:
    old_code = f.read()

# 1. Extract TorProxyManager and BybitRealmClient from old code
tor_regex = re.compile(
    r"(# ═════════════════════════════════════════════════════════════════════════════\n# TOR PROXY MANAGER\n# ═════════════════════════════════════════════════════════════════════════════\n\nclass TorProxyManager:.*?)\n# ═════════════════════════════════════════════════════════════════════════════\n# ORDERBOOK INTELLIGENCE",
    re.DOTALL,
)
tor_match = tor_regex.search(old_code)
if tor_match:
    tor_block = tor_match.group(1)
else:
    print("Failed to find Tor block")
    sys.exit(1)

# 2. Inject Tor block into new code before Orderbook Intelligence
code = re.sub(
    r"# ═════════════════════════════════════════════════════════════════════════════\n#  ORDERBOOK INTELLIGENCE ENGINE",
    tor_block
    + "\n# ═════════════════════════════════════════════════════════════════════════════\n# ORDERBOOK INTELLIGENCE ENGINE",
    code,
)

# 3. Modify OrderbookIntelligence to accept BybitRealmClient
code = re.sub(
    r'class OrderbookIntelligence:.*?def __init__\(self, symbol: str, base_url: str = "https://api\.bybit\.com"\) -> None:\n        self\.symbol   = symbol\.upper\(\)\n        self\.base_url = base_url',
    r"class OrderbookIntelligence:\n    def __init__(self, symbol: str, client) -> None:\n        self.symbol = symbol.upper()\n        self.client = client",
    code,
    flags=re.DOTALL,
)

# Replace its _get with self.client._get
code = re.sub(
    r"    def _get\(self, endpoint: str, params: Dict\) -> Dict:.*?return \{\}",
    r"",
    code,
    flags=re.DOTALL,
)
code = re.sub(r"self\._get\(", r"self.client._get(", code)


# 4. Modify TechnicalObservatory to accept BybitRealmClient
code = re.sub(
    r'class TechnicalObservatory:.*?def __init__\(self, symbol: str, interval: str\) -> None:\n        self\.symbol   = symbol\.upper\(\)\n        self\.interval = interval\n        self\.base_url = "https://api\.bybit\.com"',
    r"class TechnicalObservatory:\n    def __init__(self, symbol: str, interval: str, client) -> None:\n        self.symbol = symbol.upper()\n        self.interval = interval\n        self.client = client",
    code,
    flags=re.DOTALL,
)

# Replace its _get with self.client._get
code = re.sub(
    r"    def _get\(self, endpoint: str, params: Dict\) -> Dict:.*?return \{\}",
    r"",
    code,
    flags=re.DOTALL,
)

# 5. Modify MarketOrchestrator
# Add use_tor and json_out to init
code = re.sub(
    r"class MarketOrchestrator:\n\n    def __init__\(self, symbol: str, interval: str, delay: int\) -> None:",
    r"class MarketOrchestrator:\n\n    def __init__(self, symbol: str, interval: str, delay: int, use_tor: bool = False, once: bool = False, json_out: bool = False) -> None:\n        self.use_tor = use_tor\n        self.once = once\n        self.json_out = json_out\n        self.client = BybitRealmClient(use_tor=use_tor)",
    code,
)
# Pass client
code = re.sub(
    r"self\.tech = TechnicalObservatory\(symbol, interval\)",
    r"self.tech = TechnicalObservatory(symbol, interval, self.client)",
    code,
)
code = re.sub(
    r"self\.l2 = OrderbookIntelligence\(symbol\)",
    r"self.l2 = OrderbookIntelligence(symbol, self.client)",
    code,
)

# Modify run_cycle to stamp symbol and pass json_out
code = re.sub(
    r"ta_metrics = self\.tech\.build_indicators\(df\)",
    r'ta_metrics = self.tech.build_indicators(df)\n            ta_metrics["symbol"] = self.symbol',
    code,
)
code = re.sub(
    r"OutputRenderer\.display_metrics\(\n            ta=ta_metrics,\n            ob_met=ob_metrics,\n            tr_met=tr_metrics,\n            fi_met=fi_metrics,\n            l2_bulls=l2_bulls,\n            l2_bears=l2_bears,\n            l2_label=l2_label,\n            l2_notes=l2_notes,\n        \)",
    r"OutputRenderer.display_metrics(\n            ta=ta_metrics,\n            ob_met=ob_metrics,\n            tr_met=tr_metrics,\n            fi_met=fi_metrics,\n            l2_bulls=l2_bulls,\n            l2_bears=l2_bears,\n            l2_label=l2_label,\n            l2_notes=l2_notes,\n            json_out=self.json_out\n        )",
    code,
)

# Modify run loop to handle self.once
code = re.sub(
    r"            try:\n                self\.run_cycle\(\)\n                self\.errors = 0\n                time\.sleep\(self\.delay\)",
    r"            try:\n                self.run_cycle()\n                if self.once:\n                    break\n                self.errors = 0\n                time.sleep(self.delay)",
    code,
)

# 6. OutputRenderer json_out
json_out_code = """    @classmethod
    def display_metrics(
        cls,
        ta: Dict[str, Any],
        ob_met: Dict[str, Any],
        tr_met: Dict[str, Any],
        fi_met: Dict[str, Any],
        l2_bulls: int,
        l2_bears: int,
        l2_label: str,
        l2_notes: List[str],
        json_out: bool = False,
    ) -> None:
        if json_out:
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
    r"    @classmethod\n    def display_metrics\(\n        cls,\n        ta: Dict\[str, Any\],\n        ob_met: Dict\[str, Any\],\n        tr_met: Dict\[str, Any\],\n        fi_met: Dict\[str, Any\],\n        l2_bulls: int,\n        l2_bears: int,\n        l2_label: str,\n        l2_notes: List\[str\],\n    \) -> None:",
    json_out_code,
    code,
)

# 7. Append _coerce_bool and run()
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

with open("tools/bybit_wbta_merged.py", "w") as f:
    f.write(code)

print("Patching complete")
