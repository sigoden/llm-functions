# Bybit Trading Tools

A comprehensive collection of Argc-compatible Python tools for Bybit V5 API trading.

## 📦 Installation

```bash
# Install dependencies
pip install pybit pandas pandas_ta argc

# Set environment variables
export BYBIT_API_KEY="your_api_key"
export BYBIT_API_SECRET="your_api_secret"

# Optional: Enable testnet
export BYBIT_TESTNET=true

# Optional: Enable Tor proxy (for geo-bypassing)
export USE_TOR=true
```

## 🛠️ Tools Overview

### 1. Market Data (No Auth Required)
| Tool | Description |
|------|-------------|
| `bybit_get_orderbook` | Fetch L2 orderbook depth |
| `bybit_get_ticker` | Get 24h ticker info |
| `bybit_get_klines` | Get candlestick data |
| `bybit_get_instrument` | Get instrument specs |
| `bybit_get_funding_rate` | Get funding rate |
| `bybit_get_risk_limit` | Get risk limit |

### 2. Account & Positions (Auth Required)
| Tool | Description |
|------|-------------|
| `bybit_get_balance` | Get wallet balance |
| `bybit_get_positions` | View open positions |
| `bybit_get_open_orders` | Get open orders |
| `bybit_get_closed_pnl` | Get closed PnL |
| `bybit_get_order_history` | Get order history |
| `bybit_get_account_info` | Get account info |
| `bybit_get_fee_rate` | Get fee rate |
| `bybit_get_leverage` | Get leverage info |

### 3. Trading (Auth Required)
| Tool | Description |
|------|-------------|
| `bybit_place_order` | Place Market/Limit order |
| `bybit_cancel_order` | Cancel single order |
| `bybit_cancel_all_orders` | Cancel all orders |
| `bybit_set_leverage` | Set leverage |
| `bybit_set_trading_stop` | Set TP/SL |
| `bybit_amend_order` | Amend order |
| `bybit_set_position_mode` | Set Hedge/One-Way mode |
| `bybit_set_risk_limit` | Set risk limit |
| `bybit_get_executions` | Get execution history |

### 4. Analysis (No Auth Required)
| Tool | Description |
|------|-------------|
| `bybit_get_indicators` | RSI, EMA, ATR, MACD, Bollinger Bands |
| `bybit_analyze_symbol` | Multi-timeframe analysis |
| `bybit_analyze_orderbook` | Depth imbalance & walls |
| `bybit_get_depth` | Full orderbook |
| `bybit_get_volume_profile` | VWAP and volume stats |
| `bybit_get_support_resistance` | S/R levels |

## 🚀 Usage Examples

### Market Data
```bash
# Get orderbook
argc bybit_market.py bybit_get_orderbook --symbol BTCUSDT --limit 25

# Get ticker
argc bybit_market.py bybit_get_ticker --symbol BTCUSDT

# Get klines
argc bybit_market.py bybit_get_klines --symbol BTCUSDT --interval 60 --limit 100

# Get instrument info
argc bybit_market.py bybit_get_instrument --symbol BTCUSDT
```

### Account Data
```bash
# Get balance
argc bybit_account.py bybit_get_balance --coin USDT

# Get positions
argc bybit_account.py bybit_get_positions --symbol BTCUSDT

# Get open orders
argc bybit_account.py bybit_get_open_orders --symbol BTCUSDT
```

### Trading
```bash
# Place limit order with PostOnly
argc bybit_trade.py bybit_place_order \
  --symbol BTCUSDT \
  --side Buy \
  --order-type Limit \
  --qty 0.001 \
  --price 50000 \
  --time-in-force PostOnly

# Set leverage
argc bybit_trade.py bybit_set_leverage --symbol BTCUSDT --leverage 10

# Set TP/SL
argc bybit_trade.py bybit_set_trading_stop --symbol BTCUSDT --tp 55000 --sl 48000

# Cancel order
argc bybit_trade.py bybit_cancel_order --symbol BTCUSDT --order-id "your-order-id"
```

### Analysis
```bash
# Get technical indicators
argc bybit_analysis.py bybit_get_indicators --symbol BTCUSDT --interval 60

# Multi-timeframe analysis
argc bybit_analysis.py bybit_analyze_symbol --symbol BTCUSDT

# Analyze orderbook
argc bybit_analysis.py bybit_analyze_orderbook --symbol BTCUSDT

# Get support/resistance
argc bybit_analysis.py bybit_get_support_resistance --symbol BTCUSDT
```

## 🌐 Tor Proxy Support

Route all traffic through Tor network:

```bash
export USE_TOR=true
argc bybit_market.py bybit_get_orderbook --symbol BTCUSDT
```

Or use torsocks wrapper:
```bash
torsocks argc bybit_account.py bybit_get_balance
```

## 📁 File Structure

```
tools/
├── bybit_market.py      # Market data tools
├── bybit_account.py     # Account/position tools
├── bybit_trade.py       # Execution tools
├── bybit_analysis.py    # Analysis tools
├── bybit_pro_suite.py   # Unified pro suite
├── functions.json       # Tool definitions
├── requirements.txt     # Dependencies
├── README.md            # This file
└── utils/
    ├── bybit_base.py    # Base API utilities
    └── indicators.py    # RSI/EMA calculations
```

## ⚠️ Risk Warning

- Always use `PostOnly` for maker fee optimization
- Set appropriate leverage and position limits
- Test on testnet before production trading
- Never risk more than you can afford to lose

## 🔧 Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BYBIT_API_KEY` | Your Bybit API Key | For private endpoints |
| `BYBIT_API_SECRET` | Your Bybit API Secret | For private endpoints |
| `BYBIT_TESTNET` | Use testnet (true/false) | No |
| `USE_TOR` | Use Tor proxy (true/false) | No |
| `TOR_PROXY` | Tor proxy URL | No |

## 📊 Pro Suite (Advanced)

The `bybit_pro_suite.py` provides a unified CLI with advanced features:

```bash
# Analyze orderbook
python bybit_pro_suite.py analyze_orderbook --symbol BTCUSDT

# Trading dashboard
python bybit_pro_suite.py trading_dashboard

# Smart order with auto-sizing
python bybit_pro_suite.py smart_order \
  --symbol BTCUSDT \
  --side Buy \
  --risk-pct 1.0 \
  --sl-dist 100
```

## 🏗️ Architecture

- **pybit**: Unified trading library for Bybit V5 API
- **pandas**: Data manipulation
- **pandas_ta**: Technical analysis indicators
- **argc**: CLI argument parsing
- **Decimal**: Precise calculations (no rounding errors)
- **Tor SOCKS5**: Geo-bypassing support
