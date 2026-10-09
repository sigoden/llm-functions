/**
 * Bybit Market Tools
 * @describe Provides market data tools for Bybit
 */
const ccxt = require('ccxt');
const _ = require('lodash');

async function getExchange() {
  return new ccxt.bybit({
    sandbox: process.env.BYBIT_SANDBOX === 'true',
    enableRateLimit: true,
  });
}

/**
 * Get current ticker data for a specific symbol
 * @cmd Get current ticker data for a specific symbol
 * @option --symbol! <TEXT> Trading symbol (e.g., 'BTC/USDT')
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 */
exports.get_ticker = async function (args) {
  const { symbol } = args;
  try {
    const exchange = await getExchange();
    const ticker = await exchange.fetchTicker(symbol);
    return {
      exchange: 'bybit',
      symbol: ticker.symbol,
      last: ticker.last,
      bid: ticker.bid,
      ask: ticker.ask,
      high: ticker.high,
      low: ticker.low,
      volume: ticker.baseVolume,
      quoteVolume: ticker.quoteVolume,
      timestamp: ticker.timestamp,
      change: ticker.change,
      percentage: ticker.percentage
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get the order book for a specific symbol
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {number} [limit=100] - Data limit
 */
exports.get_orderbook = async function ({ symbol, limit = 100 }) {
  try {
    const exchange = await getExchange();
    const orderbook = await exchange.fetchOrderBook(symbol, limit);
    return {
      exchange: 'bybit',
      symbol,
      bids: orderbook.bids.slice(0, Math.min(limit, 20)),
      asks: orderbook.asks.slice(0, Math.min(limit, 20)),
      timestamp: orderbook.timestamp
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get recent trades for a specific symbol
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {number} [limit=100] - Number of trades to return
 */
exports.get_trades = async function ({ symbol, limit = 100 }) {
  try {
    const exchange = await getExchange();
    const trades = await exchange.fetchTrades(symbol, undefined, limit);
    return {
      exchange: 'bybit',
      symbol,
      trades: trades.map(t => ({
        id: t.id,
        price: t.price,
        amount: t.amount,
        side: t.side,
        timestamp: t.timestamp,
        datetime: t.datetime
      }))
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get OHLCV (K-line) data for a specific symbol
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {'1m'|'5m'|'15m'|'1h'|'4h'|'1d'} [timeframe='1h'] - Timeframe
 * @param {number} [limit=100] - Number of candles to return
 */
exports.get_ohlcv = async function ({ symbol, timeframe = '1h', limit = 100 }) {
  try {
    const exchange = await getExchange();
    const ohlcv = await exchange.fetchOHLCV(symbol, timeframe, undefined, limit);
    return {
      exchange: 'bybit',
      symbol,
      timeframe,
      ohlcv: ohlcv.map(candle => ({
        timestamp: candle[0],
        open: candle[1],
        high: candle[2],
        low: candle[3],
        close: candle[4],
        volume: candle[5]
      }))
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get a list of all active trading symbols on Bybit
 * @param {boolean} [active_only=true] - Whether to return only active symbols
 */
exports.get_symbols = async function ({ active_only = true }) {
  try {
    const exchange = await getExchange();
    const markets = await exchange.loadMarkets();
    const symbols = Object.keys(markets).filter(s => active_only ? markets[s].active : true);
    return {
      exchange: 'bybit',
      symbols,
      total: symbols.length
    };
  } catch (error) {
    return { error: error.message };
  }
};
