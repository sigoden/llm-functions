# 🌌 Bybit Tools Refcard – Neon Terminal Edition

*Welcome, Master of Mobile Arcana!*  
Herein lies the **refactored compendium** of every Bybit incantation, reorganized into coherent schools of magic. Each spell is presented with a terse description, the sacred parameters, and a **glowing example**—all wrapped in neon‑green highlights for swift incantation.

---

## ⚔️ 1. Order‑Placement Arts

### **`bybit_limit_order.sh`**  
**Purpose:** *Forge a limit order that blooms at a precise price.*  
**Key Parameters:**  
- `--symbol <SYMBOL>` – Trading pair, e.g., `BTCUSDT` (**neon‑green**).  
- `--side <SIDE>` – `buy` or `sell`.  
- `--qty <QTY>` – Order quantity (contracts).  
- `--price <PRICE>` – Limit price.  
- `--category <CATEGORY>` – `linear`, `inverse`, or `spot`.  
- `--time_in_force <TIF>` – `GTC`, `IOC`, or `FOK`.  
- `--client_oid <CLIENT_OID>` – Unique client order ID.  
- `--leverage <LEVERAGE>` – Desired leverage (if applicable).  

**Example Incantation:**  
```bash
bybit_limit_order.sh \
  --symbol BTCUSDT \
  --side buy \
  --qty 0.01 \
  --price 30000 \
  --category linear \
  --time_in_force GTC \
  --client_oid 001 \
  --leverage 10
```

---

### **`bybit_market_order.sh`**  
**Purpose:** *Summon a market order that executes instantly at the best available price.*  
**Parameters:** Same as above, minus `--price`.  

**Example:**  
```bash
bybit_market_order.sh \
  --symbol ETHUSDT \
  --side sell \
  --qty 0.02 \
  --category linear \
  --client_oid 002 \
  --leverage 5
```

---

### **`bybit_take_profit.sh`**  
**Purpose:** *Place a take‑profit order that springs when the market reaches your target.*  
**Parameters:** Identical to `bybit_limit_order.sh`, but you only supply `--stop_px` (the trigger price).  

**Example:**  
```bash
bybit_take_profit.sh \
  --symbol BTCUSDT \
  --side sell \
  --qty 0.015 \
  --stop_px 32000 \
  --category linear \
  --client_oid TP001 \
  --leverage 12
```

---

### **`bybit_stop_loss.sh`**  
**Purpose:** *Bind a stop‑loss that activates when price slips past a threshold.*  
**Parameters:** Same as `bybit_take_profit.sh`, but the trigger is a stop price.  

**Example:**  
```bash
bybit_stop_loss.sh \
  --symbol BTCUSDT \
  --side buy \
  --qty 0.01 \
  --stop_px 28000 \
  --category linear \
  --client_oid SL001 \
  --leverage 8
```

---

### **`bybit_order_amend.sh`**  
**Purpose:** *Modify an existing order’s price, quantity, or time‑in‑force.*  
**Parameters:**  
- `--order_id <ORDER_ID>` – The order to amend.  
- `--price <NEW_PRICE>` – New limit price.  
- `--qty <NEW_QTY>` – New quantity.  
- `--time_in_force <NEW_TIF>` – New TIF.  

**Example:**  
```bash
bybit_order_amend.sh \
  --order_id 001 \
  --price 31000 \
  --qty 0.015 \
  --time_in_force GTC
```

---

### **`bybit_order_create.sh`**  
**Purpose:** *A shortcut that merges `limit_order` and `market_order` semantics into a single, versatile creator.*  
**Parameters:** Mirrors `bybit_limit_order.sh` but adds `--order_type` (`limit` or `market`).  

**Example:**  
```bash
bybit_order_create.sh \
  --symbol SOLUSDT \
  --side buy \
  --qty 0.05 \
  --order_type limit \
  --price 25 \
  --category linear \
  --client_oid CRE001 \
  --leverage 5
```

---

### **`bybit_order_cancel.sh`**  
**Purpose:** *Erase an order from the book, optionally batch‑cancelling several.*  
**Parameters:**  
- `--order_id <OID>` – Single order ID.  
- `--order_ids <LIST>` – Comma‑separated list for batch removal.  

**Example (single):**  
```bash
bybit_order_cancel.sh --order_id 001
```  
**Example (batch):**  
```bash
bybit_order_cancel.sh --order_ids 001,002,003
```

---

## 📈 2. Market‑Data Oracles

### **`bybit_symbol_info.sh`**  
**Purpose:** *Reveal the metadata of a symbol—contract type, lot size,filters, and more.*  
**Parameter:** `--symbol <SYMBOL>`  
**Example:**  
```bash
bybit_symbol_info.sh --symbol BTCUSDT
```

### **`bybit_orderbook_depth.sh`**  
**Purpose:** *Peek into the order‑book depth, compute spread, and classify liquidity zones.*  
**Parameters:**  
- `--symbol <SYMBOL>`  
- `--category <CATEGORY>` (default: `PERPETUAL`)  
- `--limit <LEVELS>` (default: `20`)  

**Example:**  
```bash
bybit_orderbook_depth.sh --symbol BTCUSDT --limit 30
```

### **`bybit_kline_trend.sh`**  
**Purpose:** *Fetch recent klines (candlesticks) and compute momentum, volatility, and bias.*  
**Parameters:**  
- `--symbol <SYMBOL>`  
- `--interval <INTERVAL>` (e.g., `1h`, `4h`)  
- `--limit <NUM>` (default: `100`)  
- `--category <CATEGORY>`  

**Example:**  
```bash
bybit_kline_trend.sh --symbol ETHUSDT --interval 4h --limit 50
```

### **`bybit_funding_rate.sh`**  
**Purpose:** *Inspect historic funding‑rate data, essential for carry‑trade strategies.*  
**Parameters:**  
- `--symbol <SYMBOL>`  
- `--category <CATEGORY>` (default: `PERPETUAL`)  
- `--start <START>` & `--end <END>` (milliseconds)  

**Example:**  
```bash
bybit_funding_rate.sh --symbol BTCUSDT --start 1690000000000 --end 1700000000000
```

### **`bybit_closed_pnl.sh`**  
**Purpose:** *Query closed profit‑and‑loss records for past trades.*  
**Parameters:**  
- `--category <CATEGORY>` (default: `linear`)  
- `--symbol <SYMBOL>` (optional)  
- `--start_time` & `--end_time` (ms)  
- `--limit <NUM>` (default: `50`)  

**Example:**  
```bash
bybit_closed_pnl.sh --symbol BTCUSDT --start_time 1680000000000 --end_time 1690000000000
```

### **`bybit_execution_list.sh`**  
**Purpose:** *Retrieve a chronicle of recent executions for a symbol.*  
**Parameter:** `--symbol <SYMBOL>` (optional filter).  

**Example:**  
```bash
bybit_execution_list.sh --symbol BTCUSDT
```

### **`bybit_position_info.sh`**  
**Purpose:** *Expose current position details—size, entry price, mark price, unrealized PnL, liquidation price.*  
**Parameter:** `--symbol <SYMBOL>`  
**Example:**  
```bash
bybit_position_info.sh --symbol BTCUSDT
```

### **`bybit_position_list.sh`**  
**Purpose:** *List all open positions, optionally filtered by symbol.*  
**Parameter:** `--symbol <SYMBOL>` (optional).  

**Example:**  
```bash
bybit_position_list.sh --symbol ETHUSDT
```

### **`bybit_position_liquidation.sh`**  
**Purpose:** *Fetch recent liquidation data for a symbol.*  
**Parameter:** `--symbol <SYMBOL>`  
**Example:**  
```bash
bybit_position_liquidation.sh --symbol BTCUSDT
```

### **`bybit_fee_rate.sh`**  
**Purpose:** *Lookup the fee rate for a symbol and category.*  
**Parameters:** `--symbol <SYMBOL>`, `--category <CATEGORY>` (default: `PERPETUAL`).  

**Example:**  
```bash
bybit_fee_rate.sh --symbol BTCUSDT
```

---

## 🛡️ 3. Risk & PnL Safeguards

### **`bybit_position_transactions.sh`**  
**Purpose:** *Retrieve the transaction history of a position, useful for audit trails.*  
**Parameter:** `--symbol <SYMBOL>` (optional).  

**Example:**  
```bash
bybit_position_transactions.sh --symbol BTCUSDT
```

### **`bybit_mark_price.sh`**  
**Purpose:** *Obtain the current mark price for a symbol—critical for funding‑rate calculations.*  
**Parameter:** `--symbol <SYMBOL>`  
**Example:**  
```bash
bybit_mark_price.sh --symbol BTCUSDT
```

---

## 🧙‍♂️ 4. Technical‑Analysis Toolkit

### **`bybit_ta.py`**  
**Purpose:** *A Pythonic oracle that computes a plethora of technical indicators (SMA, EMA, RSI, MACD, etc.) on Bybit market data.*  
**Usage:**  
```bash
python bybit_ta.py --symbol BTCUSDT --action rsi --period 14
```  
**Key Actions:** `sma`, `ema`, `wma`, `rsi`, `macd`, `adx`, `bb`, `atr`, `stoch_rsi`, `williams_r`, `keltner`, `supertrend`, `aroon`, `fib_pivots`, …  

**Example (EMA 12,15):**  
```bash
python bybit_ta.py --symbol ETHUSDT --action ema --fast 12 --slow 15
```

---

## 📂 5. Utility & Automation Helpers

### **`custom_search.sh`**  
**Purpose:** *Perform advanced web searches (Google Custom Search) with filters, download images, and base‑64 encode for vision models.*  
**Parameters:** Numerous (see `custom_search.sh` help).  
**Quick Example (search for PDFs about “Termux optimization”):**  
```bash
custom_search.sh --query "Termux optimization filetype:pdf" --num_results 5 --output_format markdown
```

---

### 🎇 **Putting It All Together**

To weave these spells into a seamless workflow, consider the following **combo incantation**:

```bash
# 1️⃣ Fetch order‑book depth → 2️⃣ Compute spread → 3️⃣ Place a limit order if spread > threshold
orderbook=$(bybit_orderbook_depth.sh --symbol BTCUSDT --limit 30)
spread=$(echo "$orderbook" | jq '.spread')
if (( $(echo "$spread > 0.02" | bc -l) )); then
  bybit_limit_order.sh --symbol BTCUSDT --side buy --qty 0.01 --price $(echo "$spread * 30000" | bc) --category linear --client_oid COMBO001 --leverage 10
fi
```

*The above snippet illustrates how **`tmux`**, **`jq`**, and the Bybit tools can be orchestrated into a single, elegant automation.*

---

## 📜 6. Quick‑Reference Cheat Sheet (Neon‑Green Highlights)

- **`--symbol`** – *The trading pair, e.g., `BTCUSDT`*  
- **`--side`** – *`buy` or `sell`*  
- **`--qty`** – *Order size in contracts*  
- **`--price`** – *Limit price (for limit/T‑P/L orders)*  
- **`--category`** – *`linear`, `inverse`, or `spot`*  
- **`--time_in_force`** – *`GTC`, `IOC`, `FOK`*  
- **`--client_oid`** – *Unique identifier you assign*  
- **`--leverage`** – *Desired leverage multiplier*  
- **`--stop_px`** – *Trigger price for TP/SL orders*  

*Keep this table etched in your mind; it is the **rune‑key** to all Bybit incantations.*

---

*May your API keys stay secret, your rates stay favorable, and your terminal never crash.*  
**— Pyrmethus, the Neon Terminal Sage**  
