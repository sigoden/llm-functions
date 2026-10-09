# Bybit Developer Hardening Kit

This skill provides procedural patterns for building production-grade Bybit V5 tools that handle network latency, API outages, and market volatility.

## 1. Resilience Patterns

### Exponential Backoff (WebSocket)
When handling WebSocket disconnections (especially via Tor), use capped exponential backoff with jitter.

1.  **Initialize**: `attempts = 0`, `base = 1.0`, `max = 300`.
2.  **Calculate**: `delay = min(base * (2 ** attempts), max)`.
3.  **Jitter**: `total_delay = delay + (delay * 0.1 * random())`.
4.  **Wait**: `sleep(total_delay)`.

### Structured Error Handling
Do not treat all API errors as fatal. Map Bybit `retCode` to retryability:

- **Retryable (False Category)**: 10006 (Too many requests), 10001 (System error), 10016 (Insufficient balance - wait for funds).
- **Fatal (True Category)**: 10005 (Unauthorized), 10007 (API Key expired), 10010 (Invalid signature).

## 2. Trading Logic Patterns

### ATR Volatility Adjustment
Adjust position size based on current market regime.

- **Formula**: `atr_pct = (ATR / EntryPrice) * 100`.
- **Regimes**:
    - `atr_pct > 3.0`: High Volatility. Reduce qty by 50%.
    - `atr_pct < 0.5`: Low Volatility. Increase qty by 50%.
    - `1.0 < atr_pct < 2.0`: Normal. Standard sizing.

### Trailing Stop Implementation
Track the "Extreme Price" (EP) reached since activation.

- **Long**: `StopPrice = Max(EP) - TrailingDistance`.
- **Short**: `StopPrice = Min(EP) + TrailingDistance`.

## 3. Critical Constraints

### Signature Ordering
- **Bybit V5 Requirement**: The signature string MUST match the query parameter order in the URL.
- **Pitfall**: Avoid `sorted(params.items())` if the request library does not also sort them. Match the library's behavior (usually insertion order).

### Tor Latency
- Always use `RECV_WINDOW = 20000` (20s) when operating through Tor to prevent timestamp expiration errors.
