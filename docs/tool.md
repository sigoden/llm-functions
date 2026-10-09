# Bybit Terminal Tool

## Short Description
Bybit V5 terminal tool – a self-contained client for the Bybit V5 API that bundles order‑book analysis, risk checks, technical indicators, micro‑profit calculators and a unified CLI entry point.

## Long Description
A self‑contained client for the Bybit V5 API that bundles order‑book analysis, risk checks, technical indicators, micro‑profit calculators and a unified CLI entry point. It provides tools for trading, market‑regime detection, and strategy automation, all accessible via natural‑language prompts.

## JSON Parameter Schema
```json
{
  "name": "bybit_terminal",
  "description": "Bybit V5 terminal tool",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "description": "Operation name (e.g., place_order, calculate_all_indicators)"
      },
      "symbol": {
        "type": "string",
        "description": "Trading pair, e.g., BTCUSD"
      },
      "side": {
        "type": "string",
        "enum": ["Buy", "Sell"],
        "description": "Order side"
      },
      "qty": {
        "type": "number",
        "description": "Quantity"
      },
      "price": {
        "type": "number",
        "description": "Limit price (optional)"
      },
      "stop_loss": {
        "type": "number",
        "description": "Stop‑loss price (optional)"
      },
      "take_profit": {
        "type": "number",
        "description": "Take‑profit price (optional)"
      },
      "category": {
        "type": "string",
        "enum": ["linear", "inverse"],
        "default": "linear",
        "description": "Product category"
      },
      "interval": {
        "type": "string",
        "default": "60",
        "description": "Kline interval for indicators"
      },
      "depth": {
        "type": "integer",
        "default": 50,
        "description": "Order‑book depth"
      },
      "kwargs": {
        "type": "object",
        "description": "Extra action‑specific arguments"
      }
    },
    "required": ["action"]
  }
}
```

## Defining the Tool – Language Conventions
### Bash
Use `# @describe`, `# @option`, and `# @flag` comments to declare options.

### JavaScript
Use JSDoc `@typedef` and `@property` tags.

### Python
Use type hints and a docstring with `Optional` markers.

## Quick‑Start Helpers
- `argc create@tool bybit_terminal.py`
- `aichat -f docs/tool.md <<'EOF'`
  `create tools/bybit_terminal.py`
  `description: Bybit V5 terminal tool`
  `parameters:`
  `  action (required): Operation name`
  `  symbol (required): Trading pair`
  `  side (required): Buy|Sell`
  `  qty (required): Quantity`
  `  price?: Limit price`
  `  stop_loss?: Stop‑loss price`
  `  take_profit?: Take‑profit price`
  `  category?: linear|inverse`
  `  interval?: Kline interval`
  `  depth?: Order‑book depth`
  `  **kwargs: Extra args`
  `EOF`

## Tool Output
Write results to `$LLM_OUTPUT` or `stdout`. Use `LLM_OUTPUT_COLOR=1` for colourised output.

## Typical File Layout
```
tools/
└─ bybit_terminal/
   ├─ bybit_terminal.py
   └─ README.md
```

## Example Usage (Python)
```python
from bybit_terminal import run

result = run(
    action="place_order",
    symbol="BTCUSD",
    side="Buy",
    qty=0.001,
    price=25000,
    stop_loss=24500,
    take_profit=26000,
)
print(result)
```

---  

Replace placeholders with concrete values for your implementation.
