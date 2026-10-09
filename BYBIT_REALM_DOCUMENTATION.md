# 📘 Bybit Realm: Technical Specification & User Manual

> **Note:** the whole Bybit toolchain is now tied together through the unified
> suite — see **[BYBIT_SUITE.md](BYBIT_SUITE.md)** for the canonical entry
> point (`bybit_suite.py`), the price-action trend engine, the backtester and
> the safety model. This document remains the deep reference for the
> standalone Realm tool.

**Version:** 4.1.0  
**Status:** Production-Ready  
**Classification:** Trading Infrastructure / API Wrapper

---

## 1. Executive Summary
**Bybit Realm** is a high-resilience, production-grade Python framework designed for algorithmic trading on the Bybit exchange. Unlike simple API wrappers, Bybit Realm integrates **network-layer obfuscation (Tor/SOCKS5)**, **automated risk management**, and **quantitative analysis tools** into a single dispatcher pattern.

It is engineered to handle the volatility of crypto markets while bypassing geo-restrictions and maintaining strict rate-limit compliance.

---

## 2. System Architecture

### 2.1 The Dispatcher Pattern
The core of the system is the `BybitToolDispatcher`. It acts as a central hub that manages:
* **Authentication:** HMAC-SHA256 signing for all private requests.
* **Networking:** A multi-tier failover system (SOCKS5 $\rightarrow$ Tor $\rightarrow$ Direct).
* **Stability:** A Circuit Breaker to prevent "cascading failures" during API outages.
* **Compliance:** A token-bucket rate limiter to prevent API bans.

### 2.2 Network Stack & Resilience
| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Application** | Python `requests` / `websocket-client` | API Communication |
| **Proxy Layer** | `PySocks` / `proxychains4` | IP Rotation & Geo-Bypassing |
| **Routing** | Tor Network (SOCKS5) | Anonymity & Regional Access |
| **Transport** | HTTPS / TLS 1.3 | Secure Data Transmission |

**Resilience Logic:**
- **Circuit Breaker:** If the API returns $X$ consecutive errors, the circuit opens, blocking all requests for $Y$ seconds to prevent account flagging or resource exhaustion.
- **Rate Limiting:** Implements a sliding window to ensure requests stay within Bybit's tier-based limits.
- **Time Sync:** Automatically calculates the offset between local system time and Bybit server time to prevent `timestamp expired` errors.

---

## 3. Functional Capabilities

### 3.1 Order Execution Engine
* **Standard Orders:** Market, Limit, Conditional.
* **Advanced Execution:** 
    * **Iceberg Orders:** Splits large orders into smaller "slices" to hide market impact and reduce slippage.
    * **Batch Execution:** Executes multiple orders across different symbols simultaneously for scalp strategies.
* **Risk-Integrated Entry:** Automatically calculates `qty` based on a fixed USDT risk amount and stop-loss distance.

### 3.2 Quantitative Analysis Suite
The tool includes a built-in math engine for technical analysis:
* **Trend Indicators:** EMA (9, 21, 50, 200), MACD, Bollinger Bands.
* **Momentum:** RSI, Stoch RSI (Fixed sliding window), ATR.
* **Market Sentiment:** Order book imbalance and VWAP analysis.
* **Trend Scoring:** A proprietary scoring system that aggregates multiple indicators into a `BULLISH` / `BEARISH` / `NEUTRAL` signal.

### 3.3 Risk & Portfolio Management
* **Position Sizing:** $\text{Quantity} = \frac{\text{Risk Amount}}{\text{Entry} - \text{Stop Loss}} \times \text{Leverage}$
* **PnL Tracking:** Detailed reporting including realized PnL, fee deduction, and win-rate analytics.
* **Global Safety:** `cancel_all_orders` functionality for emergency exits (supports category-wide cancellation).

---

## 4. API Reference (Quick Guide)

### 4.1 Core Methods
| Method | Parameters | Description |
| :--- | :--- | :--- |
| `place_order()` | `symbol, side, qty, price, order_type, ...` | Executes a trade with optional SL/TP. |
| `get_trend_analysis()` | `symbol, interval, limit` | Returns a comprehensive market health report. |
| `calculate_position_size()`| `symbol, entry, sl, risk_usdt, leverage` | Returns the exact qty to trade. |
| `get_pnl_report()` | `symbol, category, start_time, end_time` | Generates a performance audit. |
| `get_liquidations()` | `symbol` | Fetches real-time liquidation data. |
| `iceberg_order()` | `symbol, side, qty, price, slices, delay` | Executes a hidden large order. |

---

## 5. Configuration Guide

### 5.1 Environment Setup
Create a `.env` file in the root directory:
```env
# API Credentials
BYBIT_API_KEY=your_api_key_here
BYBIT_API_SECRET=your_api_secret_here
BYBIT_USE_TESTNET=true

# Network Settings
TOR_ENABLED=true
PYSOCKS_GLOBAL=true
PYSOCKS_PORT=9050
```

### 5.2 Trading Logic Config (`trading_config.json`)
```json
{
  "trading_settings": {
    "max_position_usdt": 5000,
    "leverage": 10,
    "default_stop_loss": 0.02,
    "iceberg_delay": 0.5
  },
  "circuit_breaker": {
    "failure_threshold": 5,
    "recovery_timeout": 60
  },
  "rate_limit": {
    "calls": 10,
    "window": 1.0
  }
}
```

---

## 6. Operational Workflow (Example)

**Scenario: Automated Trend-Following Trade**
1. **Analysis:** Call `get_trend_analysis("BTCUSDT")`.
2. **Validation:** If `trend == "BULLISH"` and `score > 40`.
3. **Risk Calc:** Call `calculate_position_size` with $100 risk and 2% SL.
4. **Execution:** Call `place_order` using the calculated quantity.
5. **Monitoring:** Use `get_recent_trades` to verify fill price.

---

## 7. Maintenance & Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| `Circuit OPEN` | Too many API errors (500s/429s) | Wait 60s or call `dispatcher.circuit.reset()`. |
| `403 Forbidden` | Geo-block or IP ban | Renew Tor circuit: `dispatcher.tor.renew_tor_circuit()`. |
| `Invalid Qty` | Precision mismatch | Ensure `qty` matches the symbol's `lotSizeFilter`. |
| `Auth Failure` | Incorrect API Key/Secret | Check `.env` file and ensure API permissions are "Trade". |
| `Timestamp Error` | Local clock drift | The tool handles this automatically, but ensure NTP is enabled on the host. |

---
**End of Document**
