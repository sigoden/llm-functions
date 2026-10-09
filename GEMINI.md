# Bybit Tooling Project

This project provides a set of Python-based tools for interacting with the Bybit V5 API.

## Core Mandates & Setup

### Geographic Restrictions
Bybit's API (`api.bybit.com`) is blocked in certain regions via Amazon CloudFront. 
**Mandate:** Always ensure a proxy or VPN is used if operating from a restricted region.

### Proxy Configuration
All Bybit tools support standard environment variables for proxies:
- `HTTP_PROXY`
- `HTTPS_PROXY` (Supports `socks5h://` for Tor)

**Tor Usage (Termux/Android):**
- Use `proxychains4 curl` for reliable routing if SOCKS5 environment variables fail.
- Deny local address warning in `proxychains4` is expected; use direct URL instead of IP if needed.
- `RECV_WINDOW` should be set to `20000` (20s) to handle Tor latency.

### Authentication & API Notes
Tools require the following environment variables:
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `BYBIT_TESTNET` (set to `true` or `false`)

**V5 API Quirks:**
- `GET /v5/account/wallet-balance`: This is a **GET** request. Using `POST` will return a `404 Not Found`.
- `GET /v5/position/list`: Requires `settleCoin` parameter (e.g., `settleCoin=USDT`) for linear contracts.
- **Signature Ordering:** Bybit V5 requires the signature string to exactly match the query parameter order in the URL. Do NOT alphabetically sort parameters before signing if the request library (like `curl` or certain `requests` versions) preserves insertion order.


## Available Tools

- `bybit_get_balance.py`: Get wallet balances (Unified, Contract, or Spot).
- `bybit_get_positions.py`: List open positions.
- `bybit_get_ticker.py`: Get real-time ticker data.
- `bybit_get_klines.py`: Fetch candlestick data.
- `bybit_get_orderbook.py`: Get market depth.
- `bybit_get_open_orders.py`: List active orders.
- `bybit_place_order.py`: Create new orders.
- `bybit_cancel_order.py`: Cancel a specific order.
- `bybit_cancel_all_orders.py`: Cancel all orders for a symbol.
- `bybit_set_leverage.py`: Set leverage for a position.

### Memory & Session Tools
- `memory_manager.sh`: Manage persistent memory for AIChat conversations.
- `session_manager.sh`: Manage AIChat sessions with persistent context.
- `memory_aware_tool.sh`: Execute tools with automatic memory storage.
- `memory_analytics.py`: Generate analytics and insights from stored memory.
- `context_analyzer.py`: Analyze context and suggest relevant tools.

## Technical Details

- **Language:** Python 3
- **Dependencies:** `requests`, `pysocks` (for SOCKS5 support)
- **API Version:** Bybit V5
- **Error Handling:** Tools handle non-JSON responses from CloudFront/Firewalls and provide clear feedback.

## Running Tools

Use the `scripts/run-tool.py` dispatcher:
```bash
HTTPS_PROXY="socks5h://127.0.0.1:9050" python3 scripts/run-tool.py bybit_get_balance '{"account_type": "UNIFIED", "testnet": false}'
```
TRUMPUSDT trade initiated. Monitoring for profit/loss.
TRUMPUSDT and BTCUSDT position check failed due to mcp command invocation error. Further debugging needed for script execution via proxychains4.
