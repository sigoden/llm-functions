# Project Rules

## Bybit V5 API Integration Guardrails

1. **Unrealized PnL Spelling Key:**
   - Always query `"unrealisedPnl"` (with an **"s"**) when reading linear position info.
   - For safety, use fallback checks: `pos.get("unrealisedPnl", pos.get("unrealizedPnl", 0))`.

2. **Position Index Mapping (Hedge vs One-Way):**
   - Never hardcode `"positionIdx": 0` when amending trading stops or sending orders.
   - Always dynamically extract the position index from the active position payload: `int(pos.get("positionIdx", 0))`. (Long = 1, Short = 2 in Hedge margin mode; One-Way = 0).

3. **Smart Order Trigger Parameters:**
   - Ensure friendly trigger strings (`"Mark"`, `"Index"`, `"Last"`) are mapped to Bybit-compliant values (`"MarkPrice"`, `"IndexPrice"`, `"LastPrice"`) for `tpTriggerBy` and `slTriggerBy`.

## Scalping & Exit Strategy Rules

1. **Order Book Wall Offset Placements:**
   - **Take Profit (TP):** Place TP **1 tick inside** (before) the order book walls (`resistance_price - 1 * tick_size` for Longs; `support_price + 1 * tick_size` for Shorts) to prioritize execution before the wall rejects the price.
   - **Stop Loss (SL):** Place SL **2 ticks behind** the order book walls (`support_price - 2 * tick_size` for Longs; `resistance_price + 2 * tick_size` for Shorts) to leverage the wall as a defensive shield.

2. **Fee-Adjusted Breakeven SL:**
   - When moving Stop Loss to breakeven, estimate exit fees using the **Taker rate (0.06%)**, not the Maker rate (0.02%), since stop-outs trigger Taker market orders.
   - Lock in a **1-tick micro-profit** beyond the fee offset to guarantee a positive net PnL trade in the journal.
