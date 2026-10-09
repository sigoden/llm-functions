# Bybit Trading Suite — Unified Architecture

> **Status:** all Bybit tools reviewed, fixed and tied into one dispatcher.
> **Entry point:** `tools/bybit_suite.py` (or `bin/bybit_suite`) — every Bybit
> capability is reachable through a single `--action` interface.
>
> **Safety model:** every action is read-only or a *dry-run plan* by default.
> The ONLY action that can place a live order is `execute_signal`, and only
> when called with `execute=true` after passing all safety gates.

---

## 1. How the tools tie together

```
                     ┌──────────────────────────────────────────┐
   CLI / LLM  ──────►│  tools/bybit_suite.py  (run(**kwargs))    │
   run-tool.py       └───────────────┬──────────────────────────┘
                                     │ dispatcher: terminal.run()
                                     ▼
                     ┌──────────────────────────────────────────┐
                     │        tools/bybit/  (canonical package)  │
                     ├──────────────────────────────────────────┤
                     │ base.py        V5 client, HMAC, rate      │
                     │                limiter, retry, Tor tiers  │
                     │ market.py      data + 35+ indicators      │
                     │ execution.py   orders, batches, stops     │
                     │ account.py     balance/positions/PnL      │
                     │ smart.py       risk-sized smart orders    │
                     │ price_action.py  STRUCTURE + TREND ENGINE │
                     │ backtest.py    strategy validation        │
                     │ unified.py     composite actions          │
                     │ terminal.py    BybitRealm + dispatcher    │
                     └──────┬───────────────────────┬───────────┘
                            │                       │
                 ┌──────────▼─────────┐   ┌─────────▼──────────────┐
                 │ tools/bybit_wbta.py│   │ utils/bybit_base.py     │
                 │ L2/flow/funding/OI │   │ utils/trading_engines.py│
                 │ observatory (WBTA) │   │ PnL + exit strategies   │
                 └────────────────────┘   └────────────────────────┘
```

* `bybit_market.py`, `bybit_analysis.py`, `bybit_account.py`, `bybit_trade.py`
  (repo root and `tools/`) are now thin ports over the same package — no more
  `pybit` dependency, one client implementation for everything.
* `bybit_realm.py`, `bybit_core.py`, `bybit_x.py`, `bybit_smart_order.py`,
  `bybit_position_manager.py`, `bybit_pro_suite.py`, `bybit_tool.py`,
  `bybit_wbta.py`, `bybit_live.py`, `bbt*.py`, `pyrm_*` remain available as
  standalone tools; the package is the canonical code path.

## 2. All actions (one dispatcher)

```sh
# Discovery
bin/bybit_suite --action list_actions

# Market data
bin/bybit_suite --action get_ticker --symbol BTCUSDT
bin/bybit_suite --action get_klines --symbol BTCUSDT --interval 60 --limit 300
bin/bybit_suite --action get_orderbook --symbol BTCUSDT
bin/bybit_suite --action get_funding_rate --symbol BTCUSDT
bin/bybit_suite --action get_open_interest --symbol BTCUSDT
bin/bybit_suite --action market_snapshot --symbol BTCUSDT

# Indicators
bin/bybit_suite --action calculate_rsi --symbol BTCUSDT --interval 60
bin/bybit_suite --action calculate_supertrend --symbol BTCUSDT --interval 60
# ... calculate_ema|sma|macd|atr|adx|stochastic|bollinger_bands|vwap|cci|
#     ichimoku|mfi|williams_r|tema|hma|klinger|cmf|fisher_transform|...

# Trend analysis for price action trading  (the core loop)
bin/bybit_suite --action price_action_analysis --symbol BTCUSDT --interval 60
bin/bybit_suite --action trend_scan --symbols BTCUSDT,ETHUSDT,SOLUSDT
bin/bybit_suite --action wbta_signal --symbol BTCUSDT --interval 15
bin/bybit_suite --action full_signal --symbol BTCUSDT --interval 60

# Planning / validation (always dry)
bin/bybit_suite --action build_trade_plan --symbol BTCUSDT --risk_pct 1
bin/bybit_suite --action backtest_pa --symbol BTCUSDT --interval 60 --limit 1000
bin/bybit_suite --action execute_signal --symbol BTCUSDT --execute false

# Account / execution (authenticated)
bin/bybit_suite --action get_wallet_balance
bin/bybit_suite --action get_positions
bin/bybit_suite --action get_open_orders
bin/bybit_suite --action place_smart_order --symbol BTCUSDT --side Buy --risk_pct 1
bin/bybit_suite --action set_trading_stop --symbol BTCUSDT --stop_loss 45000
bin/bybit_suite --action journal_stats
```

## 3. Price action engine (trend analysis)

`tools/bybit/price_action.py` produces the tradeable signal:

1. **Market structure** — swing highs/lows (fractal confirmation) → HH/HL =
   `UPTREND`, LH/LL = `DOWNTREND`, otherwise `RANGE`; plus a slope/EMA
   fallback (net move > 4×ATR with EMA alignment) so strong trends are never
   missed when swings are sparse. Break-of-structure is flagged (`BULLISH_BOS`
   / `BEARISH_BOS`).
2. **EMA stack** — 20/50/200 alignment + price vs fast EMA.
3. **Momentum filters** — Wilder RSI and ADX. RSI only vetoes in weak trends
   (ADX < 25): overbought/oversold readings are *expected* in healthy trends.
4. **Trigger refinement** — pullback-to-EMA / rejection-candle setups add
   confidence.
5. **Output** — LONG / SHORT / WAIT with entry, structure-based stop (below
   the last swing low / above the last swing high, padded by ATR), 1R/1.5R/2.5R
   TP ladder, confidence score and the full reason list.

`full_signal` requires **agreement between the price-action engine and the
WBTA observatory** (L2 orderbook imbalance, taker flow, funding, OI, L/S
ratio). Conflicts are reported as WAIT.

## 4. Risk-first sizing (the "profitable" part)

Profitability can never be guaranteed — anyone who promises it is lying to
you. What this suite guarantees is a **risk-first framework** that keeps you
alive long enough for a positive expectancy edge to compound:

* position size is derived from **risk per trade (% of balance ÷ SL distance)**,
  never from a fixed lot;
* default 1% risk per trade, hard cap 2% in `execute_signal`;
* stop is always structure + ATR based and is attached to the order;
* minimum reward:risk gate (default 2:1);
* scale-out plan (50% at 1R → stop to breakeven → rest to 2R);
* `execute_signal` gates: confidence ≥ 60, side must match the signal, risk
  under cap, and `execute=true` must be passed explicitly;
* every fill is journaled to SQLite (`bybit_trades.db`) and surfaced via
  `journal_stats` (win rate, profit factor, expectancy).

Use `backtest_pa` on each symbol/timeframe before trading it. Backtests use
closed candles, conservative fills (SL counted first on ambiguous bars) and
taker fees on every fill. Treat them as research, not promises.

## 5. Environment

```env
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_USE_TESTNET=false      # true → api-testnet.bybit.com
PROXY_ENABLED=true           # route through Tor on device
PROXY_TYPE=socks5h
PROXY_HOST=127.0.0.1
PROXY_PORT=9050
BYBIT_SYNC_TIME=true         # false disables the server-time sync on init
```

## 6. Testing

```sh
# offline suite (no network, no orders)
.venv/bin/python -m unittest tests.test_bybit_suite -v

# read-only live smoke test on the device (never places orders)
.venv/bin/python scripts/bybit_live_smoke.py [--with-auth] [--tor]
```

## 7. What was fixed in this pass

* `tools/bybit/` package was un-importable (missing `utils/bybit_base.py`,
  `utils/trading_engines.py`) — both modules created with real implementations.
* **Security:** removed a debug print that leaked the API key to stderr;
  signed requests now fail fast with `NO_CREDENTIALS` when keys are unset.
* **Indicator math:** ATR / choppiness used the *newer* candle as "previous
  close"; SuperTrend was a fixed channel (always "Up"); TEMA was a no-op;
  stochastic ignored its smoothing; ADX/RSI were not Wilder-smoothed;
  Williams %R mixed candle orders; half-trend read the oldest candles;
  Ehlers-RSI was an EMA of price. All corrected and unit-tested.
* **Orderbook normalization:** OBI, market-depth profile and maker-scalp
  checks silently read an empty `result` key — fixed with a shape-tolerant
  parser, so those signals now return real numbers.
* **Backtester:** short-side cash accounting inverted; equity curve excluded
  unrealized PnL and included short-sale proceeds. Rewritten to a
  realized + mark-to-market equity model with proper scale-out attribution.
* **WBTA observatory** is now callable from the unified dispatcher
  (`wbta_signal`) and feeds `full_signal`.
* `bybit_market.py` / `bybit_analysis.py` / `bybit_account.py` /
  `bybit_trade.py` ported off `pybit` onto the unified client.
