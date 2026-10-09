{
  "bbt.py": {
    "description": "Unified Entry Point — Bybit Realm v5.0",
    "version": "5.0",
    "modules": [
      "bybit_smart_order",
      "bybit_wbta",
      "scientific_calculator",
      "proxy_utils"
    ],
    "commands": [
      {
        "name": "health_check",
        "description": "Checks the Bybit API server time."
      },
      {
        "name": "get_wallet_balance",
        "description": "Retrieves wallet balance for a given account type.",
        "options": {
          "account_type": "UNIFIED | CONTRACT | SPOT | INVESTMENT"
        }
      },
      {
        "name": "get_account_info",
        "description": "Retrieves detailed account information."
      },
      {
        "name": "get_positions",
        "description": "Fetches open positions.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair",
          "settle_coin": "Optional settlement coin (e.g., USDT)"
        }
      },
      {
        "name": "get_position_risk",
        "description": "Retrieves enriched position data including PnL%, liquidation distance, and heat.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair"
        }
      },
      {
        "name": "get_fee_rate",
        "description": "Fetches the trading fee rate.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair"
        }
      },
      {
        "name": "set_leverage",
        "description": "Sets leverage for a trading pair.",
        "options": {
          "symbol": "Trading pair (e.g., BTCUSDT)",
          "leverage": "Leverage value (integer)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "set_trading_stop",
        "description": "Sets Stop Loss and/or Take Profit for a position.",
        "options": {
          "symbol": "Trading pair",
          "stop_loss": "Stop loss price",
          "take_profit": "Take profit price",
          "trailing_stop": "Trailing stop value",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "set_position_mode",
        "description": "Switches between One-Way and Hedge mode.",
        "options": {
          "coin": "Account coin (e.g., USDT)",
          "mode": "0 (One-Way) or 3 (Hedge)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_executions",
        "description": "Retrieves recent trade executions.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair",
          "limit": "Result limit"
        }
      },
      {
        "name": "get_pnl_history",
        "description": "Fetches historical PnL data for closed positions.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair",
          "limit": "Result limit"
        }
      },
      {
        "name": "panic_close",
        "description": "Cancels all open orders and closes all positions.",
        "options": {
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "bulk_update_tp_sl",
        "description": "Applies specified Take Profit and Stop Loss to all open positions.",
        "options": {
          "category": "linear | spot | inverse",
          "tp": "Take profit price",
          "sl": "Stop loss price"
        }
      },
      {
        "name": "get_account_summary",
        "description": "Provides a summary of account balance and positions."
      },
      {
        "name": "get_pnl_summary",
        "description": "Aggregates PnL data over a specified period.",
        "options": {
          "symbol": "Optional trading pair",
          "days": "Number of days to analyze (default: 7)",
          "limit": "Result limit for history fetching"
        }
      },
      {
        "name": "update_trailing_stop",
        "description": "Applies a trailing stop to an open position.",
        "options": {
          "symbol": "Trading pair",
          "trailing_stop_pct": "Trailing stop percentage",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "set_tp_sl",
        "description": "Sets Take Profit and Stop Loss for a specific position.",
        "options": {
          "symbol": "Trading pair",
          "tp": "Take profit price",
          "sl": "Stop loss price",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "check_risk_limit",
        "description": "Checks if a proposed trade adheres to maximum position size constraints.",
        "options": {
          "symbol": "Trading pair",
          "qty": "Order quantity",
          "price": "Order price"
        }
      },
      {
        "name": "check_balance",
        "description": "Alias for get_wallet_balance."
      },
      {
        "name": "close_position",
        "description": "Closes an open position for a given symbol.",
        "options": {
          "symbol": "Trading pair",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_open_positions_summary",
        "description": "Provides a concise summary of all open positions."
      },
      {
        "name": "send_telegram_alert",
        "description": "Sends a notification via Telegram (logs message).",
        "options": {
          "message": "The alert message content",
          "level": "INFO | WARNING | ERROR (default: INFO)"
        }
      },
      {
        "name": "export_trade_history",
        "description": "Exports trade history to a CSV file.",
        "options": {
          "symbol": "Trading pair",
          "filename": "Output CSV filename (default: trade_history.csv)"
        }
      },
      {
        "name": "calculate_rsi",
        "description": "Calculates the Relative Strength Index (RSI).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval (e.g., 15, 60, D)",
          "period": "RSI period (default: 14)"
        }
      },
      {
        "name": "calculate_sma",
        "description": "Calculates the Simple Moving Average (SMA).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "SMA period (default: 50)"
        }
      },
      {
        "name": "calculate_ema",
        "description": "Calculates the Exponential Moving Average (EMA).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "EMA period (default: 20)"
        }
      },
      {
        "name": "calculate_macd",
        "description": "Calculates the Moving Average Convergence Divergence (MACD).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "fast": "Fast EMA period (default: 12)",
          "slow": "Slow EMA period (default: 26)",
          "signal": "Signal line period (default: 9)"
        }
      },
      {
        "name": "calculate_bollinger_bands",
        "description": "Calculates Bollinger Bands.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Period for calculation (default: 20)"
        }
      },
      {
        "name": "calculate_vwap",
        "description": "Calculates Volume Weighted Average Price (VWAP).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "limit": "Number of klines to consider (default: 50)"
        }
      },
      {
        "name": "calculate_atr",
        "description": "Calculates the Average True Range (ATR).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "ATR period (default: 14)"
        }
      },
      {
        "name": "calculate_stoch",
        "description": "Calculates the Stochastic Oscillator (%K and %D).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Stochastic period (default: 14)",
          "smooth_k": "Smoothing period for %K (default: 3)",
          "smooth_d": "Smoothing period for %D (default: 3)"
        }
      },
      {
        "name": "scan_scalping_opportunities",
        "description": "Scans for potential scalping opportunities based on multiple indicators.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval"
        }
      },
      {
        "name": "place_order",
        "description": "Places a limit or market order.",
        "options": {
          "symbol": "Trading pair",
          "side": "Buy | Sell",
          "qty": "Order quantity",
          "price": "Order price (for limit orders)",
          "order_type": "Limit | Market | etc.",
          "category": "linear | spot | inverse",
          "stop_loss": "Stop loss price",
          "take_profit": "Take profit price",
          "time_in_force": "GTC | IOC | FOK",
          "reduce_only": "Boolean flag",
          "client_oid": "Unique client order ID",
          "trigger_price": "Price for conditional orders",
          "trigger_by": "LastPrice | IndexPrice | MarkPrice",
          "tp_order_type": "Order type for take profit",
          "sl_order_type": "Order type for stop loss"
        }
      },
      {
        "name": "place_smart_trade",
        "description": "Places an order with smart sizing and risk management.",
        "options": {
          "symbol": "Trading pair",
          "side": "Buy | Sell",
          "qty": "Order quantity",
          "price": "Order price",
          "tp_pct": "Take profit percentage",
          "sl_pct": "Stop loss percentage",
          "trailing_stop_pct": "Trailing stop percentage",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "amend_order",
        "description": "Modifies an existing order.",
        "options": {
          "symbol": "Trading pair",
          "order_id": "Order ID to amend",
          "client_oid": "Client Order ID to amend",
          "qty": "New quantity",
          "price": "New price",
          "stop_loss": "New stop loss price",
          "take_profit": "New take profit price",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "cancel_order",
        "description": "Cancels a specific order.",
        "options": {
          "symbol": "Trading pair",
          "order_id": "Order ID to cancel",
          "client_oid": "Client Order ID to cancel",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "cancel_all_orders",
        "description": "Cancels all open orders for a symbol or account.",
        "options": {
          "symbol": "Optional trading pair",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_open_orders",
        "description": "Retrieves currently open orders.",
        "options": {
          "symbol": "Optional trading pair",
          "category": "linear | spot | inverse",
          "limit": "Result limit"
        }
      },
      {
        "name": "get_order_history",
        "description": "Retrieves historical orders.",
        "options": {
          "symbol": "Optional trading pair",
          "category": "linear | spot | inverse",
          "limit": "Result limit"
        }
      },
      {
        "name": "batch_place_orders",
        "description": "Places multiple orders simultaneously.",
        "options": {
          "orders": "List of order dictionaries",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_ticker",
        "description": "Retrieves ticker information for a symbol.",
        "options": {
          "symbol": "Trading pair",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_orderbook",
        "description": "Retrieves the order book for a symbol.",
        "options": {
          "symbol": "Trading pair",
          "limit": "Number of bids/asks to retrieve (default: 50)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_klines",
        "description": "Retrieves candlestick (kline) data.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval (e.g., 1, 5, 15, 60, D)",
          "limit": "Number of klines to retrieve (default: 50)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_recent_trades",
        "description": "Retrieves recent trades for a symbol.",
        "options": {
          "symbol": "Trading pair",
          "limit": "Number of trades to retrieve (default: 100)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_instruments_info",
        "description": "Retrieves detailed information about trading instruments.",
        "options": {
          "category": "linear | spot | inverse",
          "symbol": "Optional trading pair",
          "limit": "Result limit"
        }
      },
      {
        "name": "get_funding_rate",
        "description": "Fetches historical funding rates.",
        "options": {
          "symbol": "Trading pair",
          "category": "linear | spot | inverse",
          "limit": "Result limit"
        }
      },
      {
        "name": "get_open_interest",
        "description": "Retrieves open interest data.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Time interval (e.g., 1h, 4h, 1D)",
          "category": "linear | spot | inverse",
          "limit": "Result limit"
        }
      },
      {
        "name": "get_volatility_index",
        "description": "Retrieves historical volatility index data.",
        "options": {
          "category": "option",
          "period": "Analysis period"
        }
      },
      {
        "name": "get_orderbook_analysis",
        "description": "Analyzes the order book for imbalance, liquidity, and walls.",
        "options": {
          "symbol": "Trading pair",
          "depth": "Depth of order book to analyze (default: 50)",
          "tier_size": "Size threshold for tier analysis",
          "spoof_threshold": "Threshold for detecting spoofing"
        }
      },
      {
        "name": "get_volume_at_price",
        "description": "Aggregates volume at specific price levels in the order book.",
        "options": {
          "symbol": "Trading pair",
          "depth": "Depth of order book to consider",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "get_market_regime",
        "description": "Classifies the market regime (Trending, Ranging, Volatile).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "lookback": "Number of klines for analysis",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "scan_symbols",
        "description": "Scans multiple symbols for key market metrics.",
        "options": {
          "symbols": "List of trading pairs",
          "category": "linear | spot | inverse",
          "include_regime": "Boolean to include market regime in results"
        }
      },
      {
        "name": "get_journal",
        "description": "Retrieves entries from the trading journal.",
        "options": {
          "symbol": "Optional trading pair to filter by",
          "limit": "Number of entries to retrieve"
        }
      },
      {
        "name": "calculate_orderflow_delta",
        "description": "Calculates net order flow (Aggressive Buy Volume - Aggressive Sell Volume).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "limit": "Number of trades to consider"
        }
      },
      {
        "name": "calculate_liquidity_heatmap",
        "description": "Generates a heatmap of liquidity concentrations in the order book.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "depth": "Order book depth",
          "bucket_count": "Number of buckets for heatmap",
          "kline_limit": "Number of klines for volatility calculation"
        }
      },
      {
        "name": "calculate_market_depth_profile",
        "description": "Aggregates order book volume at specific percentage distances from the mid-price.",
        "options": {
          "symbol": "Trading pair",
          "depth": "Order book depth",
          "order_sizes": "Comma-separated list of order sizes (e.g., 100,500)",
          "distance_pcts": "Comma-separated list of percentage distances (e.g., 0.1,0.5)"
        }
      },
      {
        "name": "calculate_sr_levels",
        "description": "Detects support and resistance zones from the order book.",
        "options": {
          "symbol": "Trading pair",
          "top_n": "Number of top levels to return",
          "vol_cut": "Volume threshold for zone detection"
        }
      },
      {
        "name": "calculate_limit_micro_profit",
        "description": "Calculates net profit for a limit order, considering fees.",
        "options": {
          "entry_price": "Entry price of the position",
          "limit_price": "Exit price of the limit order",
          "side": "Buy | Sell",
          "qty": "Order quantity",
          "fee_rate": "Trading fee rate (default: 0.001)"
        }
      },
      {
        "name": "calculate_depth_weighted_profit",
        "description": "Calculates profit based on the weighted average fill price across order book depth.",
        "options": {
          "symbol": "Trading pair",
          "entry_price": "Entry price of the position",
          "limit_price": "Exit price for the calculation",
          "side": "Buy | Sell",
          "qty": "Order quantity"
        }
      },
      {
        "name": "calculate_support_resistance_levels",
        "description": "Identifies key support and resistance levels based on order book data and klines.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "depth": "Order book depth",
          "wall_multiplier": "Multiplier to identify significant walls"
        }
      },
      {
        "name": "calculate_fibonacci_levels",
        "description": "Calculates Fibonacci retracement levels based on recent high/low.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "lookback": "Number of klines to consider for high/low (default: 50)"
        }
      },
      {
        "name": "generate_market_depth_report",
        "description": "Generates a professional market depth analysis report including liquidity zones and sorted bids/asks.",
        "options": {
          "symbol": "Trading pair"
        }
      },
      {
        "name": "detect_high_confluence_levels",
        "description": "Identifies strong S/R zones by finding price levels with multi-method confluence.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "depth": "Order book depth for confluence calculation"
        }
      },
      {
        "name": "calculate_all_indicators",
        "description": "Calculates and returns values for all supported technical indicators.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval"
        }
      },
      {
        "name": "calculate_hma",
        "description": "Calculates the Hull Moving Average (HMA).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "HMA period (default: 20)"
        }
      },
      {
        "name": "calculate_fractals",
        "description": "Identifies bullish and bearish fractals.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval"
        }
      },
      {
        "name": "calculate_pivot_points",
        "description": "Calculates standard pivot points (Pivot, R1, S1).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Daily ('D') or other interval"
        }
      },
      {
        "name": "calculate_klinger",
        "description": "Calculates the Klinger Oscillator.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "fast": "Fast EMA period (default: 34)",
          "slow": "Slow EMA period (default: 55)"
        }
      },
      {
        "name": "calculate_cmf",
        "description": "Calculates the Chaikin Money Flow (CMF).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "CMF period (default: 20)"
        }
      },
      {
        "name": "calculate_adx_with_di",
        "description": "Calculates ADX along with Directional Indicators (+DI, -DI).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "ADX period (default: 14)"
        }
      },
      {
        "name": "calculate_elder_ray_index",
        "description": "Calculates the Elder Ray Index (Bull Power, Bear Power).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "EMA period for calculation (default: 13)"
        }
      },
      {
        "name": "calculate_kst",
        "description": "Calculates the Know Sure Thing (KST) oscillator.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval"
        }
      },
      {
        "name": "calculate_tema",
        "description": "Calculates the Triple Exponential Moving Average (TEMA).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "TEMA period (default: 20)"
        }
      },
      {
        "name": "calculate_ehler_rsi",
        "description": "Calculates Ehlers RSI smoothing.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Smoothing period (default: 14)"
        }
      },
      {
        "name": "calculate_ehler_stochastic",
        "description": "Calculates Ehlers Stochastic Oscillator.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Period for calculation (default: 14)"
        }
      },
      {
        "name": "stream_orderbook",
        "description": "Streams real-time order book data via WebSocket.",
        "options": {
          "symbol": "Trading pair",
          "duration": "Duration in seconds to stream (default: 10)"
        }
      },
      {
        "name": "micro_scalp",
        "description": "Places a small, quick scalp order with defined profit target and fees.",
        "options": {
          "symbol": "Trading pair",
          "qty": "Order quantity (default: 0.01)",
          "fee_rate": "Trading fee rate (default: 0.0005)",
          "target_profit": "Target profit percentage (default: 0.05)",
          "category": "linear | spot | inverse"
        }
      },
      {
        "name": "calculate_vwma",
        "description": "Calculates Volume Weighted Moving Average (VWMA).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "VWMA period (default: 20)"
        }
      },
      {
        "name": "calculate_bollinger_bands_pb",
        "description": "Calculates Bollinger Bands %B.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Bollinger Bands period (default: 20)"
        }
      },
      {
        "name": "calculate_roc",
        "description": "Calculates the Rate of Change (ROC).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "ROC period (default: 12)"
        }
      },
      {
        "name": "calculate_mfi",
        "description": "Calculates the Money Flow Index (MFI).",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "MFI period (default: 14)"
        }
      },
      {
        "name": "calculate_williams_r",
        "description": "Calculates Williams %R oscillator.",
        "options": {
          "symbol": "Trading pair",
          "interval": "Kline interval",
          "period": "Williams %R period (default: 14)"
        }
      },
      {
        "name": "analyze_symbol",
        "description": "Performs a comprehensive multi-timeframe analysis of a symbol."
      }
    ],
    "configuration_options": [
      {
        "name": "BYBIT_API_KEY",
        "description": "Your Bybit API key.",
        "type": "string",
        "required": true
      },
      {
        "name": "BYBIT_API_SECRET",
        "description": "Your Bybit API secret.",
        "type": "string",
        "required": true
      },
      {
        "name": "BYBIT_USE_TESTNET",
        "description": "Set to 'true' to use the Bybit testnet environment.",
        "type": "boolean",
        "default": "false"
      },
      {
        "name": "PROXY_ENABLED",
        "description": "Enable proxy usage if available (requires proxy_utils configuration).",
        "type": "boolean",
        "default": "false"
      },
      {
        "name": "JOURNAL_PATH",
        "description": "Path to the trade journal file (JSON format).",
        "type": "string",
        "default": "bybit_journal.json"
      },
      {
        "name": "REQUEST_TIMEOUT",
        "description": "Timeout in seconds for API requests.",
        "type": "integer",
        "default": "15"
      },
      {
        "name": "MAX_RETRIES",
        "description": "Maximum number of retries for failed API requests.",
        "type": "integer",
        "default": "3"
      },
      {
        "name": "MAX_POSITION_SIZE_USDT",
        "description": "Maximum allowed notional value for a single position in USDT.",
        "type": "float",
        "default": "1000"
      }
    ],
    "dependencies": [
      "requests",
      "python-dotenv",
      "websockets (optional, for streaming)"
    ],
    "usage_example": [
      "python bbt.py --action get_ticker --symbol BTCUSDT",
      "python bbt.py --action place_order --symbol ETHUSDT --side Buy --qty 0.1 --price 3000 --stop-loss 2950 --take-profit 3100",
      "python bbt.py --action calculate_rsi --symbol BTCUSDT --interval 60",
      "python bbt.py --action scan_symbols --symbols BTCUSDT,ETHUSDT,SOLUSDT --include-regime"
    ]
  }
}
