# 📚 Bybit V5 API Trading Guide

## 🎯 Table of Contents
1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Market Data Endpoints](#market-data-endpoints)
4. [Trade Management](#trade-management)
5. [Position Management](#position-management)
6. [Account Operations](#account-operations)
7. [Asset Management](#asset-management)
8. [Code Examples](#code-examples)
9. [Best Practices](#best-practices)
10. [Troubleshooting](#troubleshooting)

---

## 📖 Overview

The Bybit V5 API provides a unified framework for trading across all product lines:
- **Spot Trading** (`category: spot`)
- **Linear Derivatives** (`category: linear`)
- **Inverse Derivatives** (`category: inverse`)
- **Options Trading** (`category: option`)

### Key Features
- Single standardized API structure
- Unified authentication across all products
- Comprehensive market data and trading capabilities
- Real-time WebSocket feeds
- Advanced order management

---

## 🔐 Authentication

### Required Headers
All authenticated requests must include these headers:

| Header | Type | Description |
|--------|------|-------------|
| `X-BAPI-API-KEY` | String | Your API key |
| `X-BAPI-TIMESTAMP` | Integer | Unix timestamp in milliseconds |
| `X-BAPI-SIGN` | String | HMAC-SHA256 signature |
| `X-BAPI-RECV-WINDOW` | Integer | Request expiration window (default: 5000ms) |

### Signature Generation
```python
import hmac
import hashlib
import json
import time


def generate_signature(api_secret, timestamp, api_key, recv_window, payload):
    """
    Generate HMAC-SHA256 signature for Bybit API
    """
    query_string = f"{timestamp}{api_key}{recv_window}{payload}"
    signature = hmac.new(
        api_secret.encode(), query_string.encode(), hashlib.sha256
    ).hexdigest()
    return signature
```

---

## 📊 Market Data Endpoints

### Public Endpoints (No Authentication Required)

#### 1. Get Kline Data
```http
GET /v5/market/kline
```
**Parameters:**
- `category` (required): spot, linear, inverse, option
- `symbol` (required): Trading pair
- `interval` (required): 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
- `start` (optional): Start timestamp
- `end` (optional): End timestamp
- `limit` (optional): Max 1000

#### 2. Get Order Book
```http
GET /v5/market/orderbook
```
**Parameters:**
- `category` (required): spot, linear, inverse
- `symbol` (required): Trading pair
- `limit` (optional): 10, 25, 50, 100, 200, 500

#### 3. Get Tickers
```http
GET /v5/market/tickers
```
**Parameters:**
- `category` (required): spot, linear, inverse, option
- `symbol` (optional): Specific symbol
- `expDate` (optional): Options expiry date

#### 4. Get Recent Trades
```http
GET /v5/market/recent-trade
```
**Parameters:**
- `category` (required): spot, linear, inverse, option
- `symbol` (required): Trading pair
- `limit` (optional): Max 500

---

## 📈 Trade Management

### 1. Create Order
```http
POST /v5/order/create
```
**Request Body:**
```json
{
    "category": "linear",
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "qty": "0.001",
    "price": "42000",
    "timeInForce": "GTC",
    "stopLoss": "41000",
    "takeProfit": "43000"
}
```

### 2. Cancel Order
```http
POST /v5/order/cancel
```
**Request Body:**
```json
{
    "category": "linear",
    "symbol": "BTCUSDT",
    "orderId": "123456789"
}
```

### 3. Get Open Orders
```http
GET /v5/order/realtime
```
**Parameters:**
- `category` (required): spot, linear, inverse, option
- `symbol` (optional): Specific symbol

### 4. Get Order History
```http
GET /v5/order/history
```
**Parameters:**
- `category` (required): spot, linear, inverse, option
- `symbol` (optional): Specific symbol

---

## 🎯 Position Management

### 1. Get Position List
```http
GET /v5/position/list
```
**Parameters:**
- `category` (required): linear, inverse
- `symbol` (optional): Specific symbol

### 2. Set Leverage
```http
POST /v5/position/set-leverage
```
**Request Body:**
```json
{
    "category": "linear",
    "symbol": "BTCUSDT",
    "buyLeverage": "10",
    "sellLeverage": "10"
}
```

### 3. Set Trading Stop
```http
POST /v5/position/set-trading-stop
```
**Request Body:**
```json
{
    "category": "linear",
    "symbol": "BTCUSDT",
    "takeProfit": "43000",
    "stopLoss": "41000",
    "trailingStop": "500"
}
```

---

## 💼 Account Operations

### 1. Get Wallet Balance
```http
GET /v5/account/wallet-balance
```
**Parameters:**
- `accountType` (required): UNIFIED, CONTRACT, SPOT

### 2. Get Account Info
```http
GET /v5/account/info
```

### 3. Get Fee Rates
```http
GET /v5/account/fee-rate
```
**Parameters:**
- `category` (optional): spot, linear, inverse, option
- `symbol` (optional): Specific symbol

---

## 💰 Asset Management

### 1. Get Deposit Address
```http
GET /v5/asset/deposit/query-address
```
**Parameters:**
- `coin` (required): Currency code
- `chainType` (optional): Blockchain network

### 2. Create Withdrawal
```http
POST /v5/asset/withdraw/create
```
**Request Body:**
```json
{
    "coin": "USDT",
    "address": "0x...",
    "chainType": "ERC20",
    "amount": "100"
}
```

### 3. Internal Transfer
```http
POST /v5/asset/transfer/inter-transfer
```
**Request Body:**
```json
{
    "transferId": "internal_transfer",
    "coin": "USDT",
    "amount": "100",
    "fromAccountType": "UNIFIED",
    "toAccountType": "SPOT"
}
```

---

## 💻 Code Examples

### Python Trading Bot Example

```python
import requests
import hmac
import hashlib
import json
import time


class BybitTrader:
    def __init__(self, api_key, api_secret, testnet=True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        )
        self.recv_window = "5000"

    def _generate_signature(self, payload):
        timestamp = str(int(time.time() * 1000))
        query_string = f"{timestamp}{self.api_key}{self.recv_window}{payload}"
        signature = hmac.new(
            self.api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    def _make_request(self, method, endpoint, payload=""):
        timestamp, signature = self._generate_signature(payload)

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": self.recv_window,
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}{endpoint}"

        if method == "GET":
            response = requests.get(url, headers=headers, params=payload)
        else:
            response = requests.post(url, headers=headers, data=payload)

        return response.json()

    def place_order(self, symbol, side, order_type, qty, price=None, **kwargs):
        """Place a trading order"""
        endpoint = "/v5/order/create"

        order_data = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
            **kwargs,
        }

        if price:
            order_data["price"] = str(price)

        payload = json.dumps(order_data, separators=(",", ":"))
        return self._make_request("POST", endpoint, payload)

    def get_positions(self):
        """Get current positions"""
        endpoint = "/v5/position/list"
        payload = {"category": "linear"}
        return self._make_request("GET", endpoint, payload)

    def get_balance(self):
        """Get wallet balance"""
        endpoint = "/v5/account/wallet-balance"
        payload = {"accountType": "UNIFIED"}
        return self._make_request("GET", endpoint, payload)

    def cancel_order(self, symbol, order_id):
        """Cancel an order"""
        endpoint = "/v5/order/cancel"
        payload = json.dumps(
            {"category": "linear", "symbol": symbol, "orderId": order_id},
            separators=(",", ":"),
        )
        return self._make_request("POST", endpoint, payload)


# Usage Example
if __name__ == "__main__":
    trader = BybitTrader(
        api_key="your_api_key", api_secret="your_api_secret", testnet=True
    )

    # Place a limit order
    order = trader.place_order(
        symbol="BTCUSDT",
        side="Buy",
        order_type="Limit",
        qty=0.001,
        price=42000,
        stopLoss=41000,
        takeProfit=43000,
    )

    print("Order Response:", order)

    # Get positions
    positions = trader.get_positions()
    print("Positions:", positions)

    # Get balance
    balance = trader.get_balance()
    print("Balance:", balance)
```

### JavaScript/Node.js Example

```javascript
const crypto = require('crypto');
const https = require('https');

class BybitAPI {
    constructor(apiKey, apiSecret, testnet = true) {
        this.apiKey = apiKey;
        this.apiSecret = apiSecret;
        this.baseURL = testnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';
        this.recvWindow = 5000;
    }

    generateSignature(payload) {
        const timestamp = Date.now().toString();
        const queryString = `${timestamp}${this.apiKey}${this.recvWindow}${payload}`;
        return {
            timestamp,
            signature: crypto
                .createHmac('sha256', this.apiSecret)
                .update(queryString)
                .digest('hex')
        };
    }

    async makeRequest(method, endpoint, payload = '') {
        const { timestamp, signature } = this.generateSignature(payload);
        
        const headers = {
            'X-BAPI-API-KEY': this.apiKey,
            'X-BAPI-TIMESTAMP': timestamp,
            'X-BAPI-SIGN': signature,
            'X-BAPI-RECV-WINDOW': this.recvWindow.toString(),
            'Content-Type': 'application/json'
        };

        const url = `${this.baseURL}${endpoint}`;
        
        return new Promise((resolve, reject) => {
            const req = https.request(url, {
                method,
                headers,
                body: payload
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => resolve(JSON.parse(data)));
            });

            req.on('error', reject);
            req.write(payload);
            req.end();
        });
    }

    async placeOrder(symbol, side, orderType, qty, price = null, options = {}) {
        const endpoint = '/v5/order/create';
        const orderData = {
            category: 'linear',
            symbol,
            side,
            orderType,
            qty: qty.toString(),
            ...options
        };

        if (price) {
            orderData.price = price.toString();
        }

        const payload = JSON.stringify(orderData);
        return this.makeRequest('POST', endpoint, payload);
    }
}

// Usage Example
const trader = new BybitAPI('your_api_key', 'your_api_secret', true);

trader.placeOrder('BTCUSDT', 'Buy', 'Limit', 0.001, 42000, {
    stopLoss: 41000,
    takeProfit: 43000
}).then(response => console.log(response))
.catch(error => console.error(error));
```

---

## 📋 Best Practices

### 1. Rate Limiting
- **Market Data**: 120 requests per minute
- **Trading**: 600 requests per minute
- **Account**: 120 requests per minute
- Implement exponential backoff for rate limit errors

### 2. Error Handling
```python
def handle_api_response(response):
    if response.get("retCode") != 0:
        error_code = response.get("retCode")
        error_msg = response.get("retMsg")

        if error_code == 10002:  # Rate limit
            time.sleep(1)
            return "retry"
        elif error_code == 10003:  # Invalid IP
            return "check_api_settings"
        elif error_code == 10004:  # Invalid signature
            return "check_credentials"
        else:
            print(f"Error {error_code}: {error_msg}")
            return "error"

    return response.get("result")
```

### 3. Position Management
- Always use stop-loss orders
- Monitor position size relative to account balance
- Implement position size limits
- Use proper leverage management

### 4. Security
- Store API keys securely
- Use IP whitelisting
- Implement proper error handling
- Use testnet for development

---

## 🚨 Troubleshooting

### Common Error Codes

| Error Code | Description | Solution |
|------------|-------------|----------|
| 10002 | Rate limit exceeded | Implement backoff and retry |
| 10003 | Invalid IP address | Check API key IP restrictions |
| 10004 | Invalid signature | Verify API credentials |
| 10006 | Insufficient balance | Check account balance |
| 10010 | Order closed | Refresh order book |
| 10016 | Liquidation | Close position immediately |
| 11006 | Position size too small | Increase minimum quantity |

### Geographic Restrictions
If you encounter "Amazon CloudFront distribution is configured to block access from your country":

1. **Use VPN**: Connect to a supported region
2. **Alternative Endpoints**: Try different API endpoints
3. **Third-Party Services**: Use trading bots or platforms with API access
4. **Manual Trading**: Use web/mobile interface

### Connection Issues
```python
def test_connection():
    try:
        response = requests.get(f"{base_url}/v5/market/time", timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"Connection failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"Connection error: {e}")
        return False
```

---

## 📚 Additional Resources

### Official Documentation
- [Bybit V5 API Docs](https://bybit-exchange.github.io/docs/v5/intro)
- [API Explorer](https://bybit-exchange.github.io/docs/api-explorer/v5)
- [WebSocket Documentation](https://bybit-exchange.github.io/docs/v5/websocket)

### SDKs and Libraries
- [Python SDK](https://github.com/tiagosiebler/bybit-api)
- [Node.js SDK](https://github.com/bybit-exchange/bybit-api-node)
- [Java SDK](https://github.com/bybit-exchange/bybit-api-java)

### Community
- [Discord Community](https://discord.gg/bybit)
- [Telegram Group](https://t.me/bybitofficial)
- [GitHub Issues](https://github.com/bybit-exchange/bybit-official-api-node/issues)

---

## 🎯 Quick Start Checklist

### Setup
- [ ] Create Bybit account
- [ ] Generate API keys
- [ ] Set IP whitelist
- [ ] Test API connection
- [ ] Configure risk parameters

### Trading
- [ ] Implement position sizing
- [ ] Set stop-loss rules
- [ ] Configure take-profit targets
- [ ] Test with paper trading
- [ ] Monitor performance

### Security
- [ ] Secure API keys
- [ ] Use testnet initially
- [ ] Implement error handling
- [ ] Set up monitoring
- [ ] Regular security audits

---

## 🔄 WebSocket Integration

### Public WebSocket
```javascript
// Connect to public WebSocket
const ws = new WebSocket('wss://stream.bybit.com/v5/public/linear');

// Subscribe to tickers
ws.onopen = () => {
    ws.send(JSON.stringify({
        "op": "subscribe",
        "args": ["tickers.BTCUSDT"]
    }));
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('Ticker data:', data);
};
```

### Private WebSocket
```javascript
// Connect to private WebSocket
const ws = new WebSocket('wss://stream.bybit.com/v5/private');

// Authenticate
ws.onopen = () => {
    const auth = generateAuthSignature();
    ws.send(JSON.stringify({
        "op": "auth",
        "args": [auth.apiKey, auth.expires, auth.signature]
    }));
};

// Subscribe to orders
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.topic === 'order') {
        console.log('Order update:', data.data);
    }
};
```

---

## 📊 Advanced Order Types

### Conditional Orders
```python
# Create conditional order
conditional_order = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "orderType": "Limit",
    "side": "Buy",
    "qty": "0.001",
    "price": "42000",
    "triggerPrice": "42500",  # Trigger condition
    "orderFilter": "Order",  # Order type filter
    "triggerDirection": "1",  # 1=above, 2=below
    "triggerBy": "LastPrice",  # Trigger by last price
}
```

### Iceberg Orders
```python
# Create iceberg order
iceberg_order = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "orderType": "Limit",
    "side": "Buy",
    "qty": "0.1",
    "price": "42000",
    "icebergQty": "0.01",  # Display quantity
}
```

---

## 🎯 Trading Strategies

### Grid Trading
```python
class GridTrader:
    def __init__(self, symbol, grid_size, grid_count):
        self.symbol = symbol
        self.grid_size = grid_size
        self.grid_count = grid_count
        self.orders = []

    def create_grid(self, center_price):
        """Create grid orders around center price"""
        for i in range(-self.grid_count // 2, self.grid_count // 2 + 1):
            if i == 0:
                continue

            price = center_price + (i * self.grid_size)
            side = "Buy" if i < 0 else "Sell"

            order = self.place_order(
                symbol=self.symbol,
                side=side,
                order_type="Limit",
                qty=0.001,
                price=price,
            )
            self.orders.append(order)
```

### Scalping Strategy
```python
class Scalper:
    def __init__(self, symbol, profit_target, stop_loss):
        self.symbol = symbol
        self.profit_target = profit_target
        self.stop_loss = stop_loss

    def scalp_trade(self, entry_price, side):
        """Execute scalp trade with tight risk management"""
        if side == "Buy":
            stop_price = entry_price - self.stop_loss
            profit_price = entry_price + self.profit_target
        else:
            stop_price = entry_price + self.stop_loss
            profit_price = entry_price - self.profit_target

        return self.place_order(
            symbol=self.symbol,
            side=side,
            order_type="Market",
            qty=0.001,
            stopLoss=stop_price,
            takeProfit=profit_price,
        )
```

---

## 📈 Risk Management

### Position Sizing Calculator
```python
def calculate_position_size(account_balance, risk_percent, entry_price, stop_price):
    """Calculate optimal position size based on risk"""
    risk_amount = account_balance * (risk_percent / 100)
    price_diff = abs(entry_price - stop_price)
    position_size = risk_amount / price_diff

    return {
        "position_size": position_size,
        "risk_amount": risk_amount,
        "price_diff": price_diff,
    }


# Example usage
account_balance = 10000  # USDT
risk_percent = 2  # 2% risk
entry_price = 42000
stop_price = 41000

position_info = calculate_position_size(
    account_balance, risk_percent, entry_price, stop_price
)
print(f"Position size: {position_info['position_size']:.6f} BTC")
```

### Portfolio Risk Monitor
```python
class RiskMonitor:
    def __init__(self, max_portfolio_risk, max_position_risk):
        self.max_portfolio_risk = max_portfolio_risk
        self.max_position_risk = max_position_risk
    
    def check_risk_limits(self, positions, account_balance):
        """Check if positions exceed risk limits"""
        total_risk = 0
        alerts = []
        
        for position in positions:
            position_risk = self.calculate_position_risk(position)
            total_risk += position_risk
            
            if position_risk > self.max_position_risk:
                alerts.append(f"Position {position['symbol']} exceeds max risk")
        
        portfolio_risk_pct = (total_risk / account_balance) * 100
        
        if portfolio_risk_pct > self.max_portfolio_risk:
            alerts.append(f"Portfolio risk {portfolio_risk_pct:.2f}% exceeds limit")
        
        return alerts
```

---

## 🔧 Development Tools

### API Testing Script
```python
def test_api_endpoints(trader):
    """Test all major API endpoints"""
    results = {}

    # Test market data
    try:
        klines = trader.get_klines("BTCUSDT", "1", limit=10)
        results["market_data"] = "✅ Working"
    except Exception as e:
        results["market_data"] = f"❌ Error: {e}"

    # Test account info
    try:
        balance = trader.get_balance()
        results["account"] = "✅ Working"
    except Exception as e:
        results["account"] = f"❌ Error: {e}"

    # Test order placement (small test order)
    try:
        test_order = trader.place_order("BTCUSDT", "Buy", "Limit", 0.001, 30000)
        results["order"] = "✅ Working"
        # Cancel test order
        trader.cancel_order("BTCUSDT", test_order["result"]["orderId"])
    except Exception as e:
        results["order"] = f"❌ Error: {e}"

    return results
```

### Performance Monitor
```python
import time
from collections import deque


class PerformanceMonitor:
    def __init__(self, window_size=100):
        self.response_times = deque(maxlen=window_size)
        self.error_count = 0
        self.success_count = 0

    def log_request(self, start_time, success=True):
        """Log API request performance"""
        response_time = time.time() - start_time
        self.response_times.append(response_time)

        if success:
            self.success_count += 1
        else:
            self.error_count += 1

    def get_stats(self):
        """Get performance statistics"""
        if not self.response_times:
            return {}

        avg_time = sum(self.response_times) / len(self.response_times)
        max_time = max(self.response_times)
        min_time = min(self.response_times)
        total_requests = self.success_count + self.error_count
        error_rate = (
            (self.error_count / total_requests) * 100 if total_requests > 0 else 0
        )

        return {
            "avg_response_time": avg_time,
            "max_response_time": max_time,
            "min_response_time": min_time,
            "total_requests": total_requests,
            "success_rate": 100 - error_rate,
            "error_rate": error_rate,
        }
```

---

## 📋 Complete Endpoint Reference

### Market Data Endpoints (/v5/market/)
- `GET /v5/market/kline` - Historical candlestick data
- `GET /v5/market/instruments-info` - Trading specifications
- `GET /v5/market/orderbook` - Order book depth
- `GET /v5/market/tickers` - Real-time trading stats
- `GET /v5/market/recent-trade` - Recent trade history
- `GET /v5/market/open-interest` - Open interest metrics
- `GET /v5/market/funding/history` - Funding rate history
- `GET /v5/market/index-price-kline` - Index price data
- `GET /v5/market/mark-price-kline` - Mark price data
- `GET /v5/market/premium-index-price-kline` - Premium index data
- `GET /v5/market/historical-volatility` - Volatility data
- `GET /v5/market/insurance` - Insurance fund data
- `GET /v5/market/risk-limit` - Risk limit tiers
- `GET /v5/market/delivery-price` - Delivery prices

### Trade Management Endpoints (/v5/order/)
- `POST /v5/order/create` - Create new order
- `POST /v5/order/amend` - Modify existing order
- `POST /v5/order/cancel` - Cancel single order
- `GET /v5/order/realtime` - Get open orders
- `GET /v5/order/history` - Get order history
- `POST /v5/order/create-batch` - Batch create orders
- `POST /v5/order/amend-batch` - Batch amend orders
- `POST /v5/order/cancel-batch` - Batch cancel orders
- `POST /v5/order/cancel-all` - Cancel all orders
- `GET /v5/order/execution-list` - Get execution history

### Position Management Endpoints (/v5/position/)
- `GET /v5/position/list` - Get position list
- `POST /v5/position/set-leverage` - Set leverage
- `POST /v5/position/switch-mode` - Switch position mode
- `POST /v5/position/set-tpsl-mode` - Set TP/SL mode
- `POST /v5/position/set-trading-stop` - Set trading stops
- `POST /v5/position/set-auto-add-margin` - Auto add margin
- `POST /v5/position/add-margin` - Add margin manually
- `GET /v5/position/closed-pnl` - Get closed P&L

### Account Operations Endpoints (/v5/account/)
- `GET /v5/account/wallet-balance` - Get wallet balance
- `GET /v5/account/info` - Get account info
- `POST /v5/account/set-margin-mode` - Set margin mode
- `GET /v5/account/transaction-log` - Get transaction log
- `GET /v5/account/fee-rate` - Get fee rates
- `GET /v5/account/borrow-history` - Get borrow history
- `POST /v5/account/quick-repayment` - Quick repayment
- `POST /v5/account/set-mmp` - Set MMP settings
- `GET /v5/account/mmp-state` - Get MMP state
- `POST /v5/account/reset-mmp` - Reset MMP

### Asset Management Endpoints (/v5/asset/)
- `GET /v5/asset/deposit/query-address` - Get deposit address
- `GET /v5/asset/deposit/query-record` - Get deposit records
- `POST /v5/asset/withdraw/create` - Create withdrawal
- `POST /v5/asset/withdraw/cancel` - Cancel withdrawal
- `GET /v5/asset/withdraw/query-record` - Get withdrawal records
- `POST /v5/asset/transfer/inter-transfer` - Internal transfer
- `GET /v5/asset/transfer/query-inter-transfer-list` - Query internal transfers
- `POST /v5/asset/transfer/universal-transfer` - Universal transfer
- `GET /v5/asset/transfer/query-universal-transfer-list` - Query universal transfers

### User Management Endpoints (/v5/user/)
- `POST /v5/user/create-submember` - Create sub-account
- `POST /v5/user/create-subapi` - Create sub-API key
- `GET /v5/user/query-api` - Query API keys
- `POST /v5/user/update-api` - Update API key
- `POST /v5/user/delete-submember` - Delete sub-account

---

*This comprehensive guide covers all essential aspects of Bybit V5 API trading, from basic setup to advanced strategies and risk management. For the most up-to-date information, always refer to the official Bybit documentation.*
