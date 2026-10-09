const ccxt = require('ccxt');
const _ = require('lodash');

async function getExchange() {
  if (!process.env.BYBIT_API_KEY || !process.env.BYBIT_SECRET) {
    throw new Error('BYBIT_API_KEY and BYBIT_SECRET environment variables required');
  }
  return new ccxt.bybit({
    apiKey: process.env.BYBIT_API_KEY,
    secret: process.env.BYBIT_SECRET,
    sandbox: process.env.BYBIT_SANDBOX === 'true',
    enableRateLimit: true,
  });
}

/**
 * Calculate the optimal position size based on risk percentage of account balance
 * @typedef {Object} Args
 * @property {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @property {number} risk_pct - Percentage of balance to risk (e.g., 1 for 1%)
 * @property {number} stop_loss_price - The price where the stop loss is set
 * @property {string} side - 'buy' or 'sell'
 * @param {Args} args
 */
exports.calculate_risk_position = async function ({ symbol, risk_pct, stop_loss_price, side }) {
  try {
    const exchange = await getExchange();
    const balance = await exchange.fetchBalance();
    const usdt_balance = balance.total['USDT'] || 0;
    const ticker = await exchange.fetchTicker(symbol);
    const entry_price = ticker.last;

    const risk_amount = usdt_balance * (risk_pct / 100);
    const price_diff = Math.abs(entry_price - stop_loss_price);
    
    if (price_diff === 0) throw new Error('Stop loss price cannot be equal to entry price');

    const qty = risk_amount / price_diff;
    
    return {
      symbol,
      entry_price,
      stop_loss_price,
      risk_amount_usdt: risk_amount,
      suggested_qty: qty,
      position_value: qty * entry_price,
      leverage_needed: (qty * entry_price) / usdt_balance
    };
  } catch (error) {
    return { error: error.message };
  }
};

/**
 * Get a summary of the most volatile symbols in the last 24h
 * @typedef {Object} Args
 * @property {number} [limit=10] - Number of symbols to return
 * @param {Args} args
 */
exports.get_volatility_leaders = async function ({ limit = 10 }) {
  try {
    const exchange = await getExchange();
    const tickers = await exchange.fetchTickers();
    
    const volatility = Object.entries(tickers)
      .map(([symbol, data]) => ({
        symbol,
        change: data.percentage,
        high: data.high,
        low: data.low,
        volatility_pct: ((data.high - data.low) / data.low) * 100
      }))
      .sort((a, b) => b.volatility_pct - a.volatility_pct)
      .slice(0, limit);

    return { exchange: 'bybit', volatility_leaders: volatility };
  } catch (error) {
    return { error: error.message };
  }
};

/**
 * Set leverage for a specific symbol
 * @typedef {Object} Args
 * @property {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @property {number} leverage - Leverage value (e.g., 10)
 * @param {Args} args
 */
exports.set_leverage = async function ({ symbol, leverage }) {
  try {
    const exchange = await getExchange();
    // CCXT Bybit implementation for setLeverage
    await exchange.setLeverage(leverage, symbol);
    return {
      exchange: 'bybit',
      symbol,
      leverage,
      status: 'success'
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get the funding rate and next funding time for a symbol
 * @typedef {Object} Args
 * @property {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {Args} args
 */
exports.get_funding_info = async function ({ symbol }) {
  try {
    const exchange = await getExchange();
    const funding = await exchange.fetchFundingRate(symbol);
    return {
      exchange: 'bybit',
      symbol,
      funding_rate: funding.fundingRate,
      next_funding_timestamp: funding.timestamp,
      next_funding_time: new Date(funding.timestamp).toISOString()
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Close all open positions for a specific symbol immediately (Market Close)
 * @typedef {Object} Args
 * @property {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {Args} args
 */
exports.close_all_positions = async function ({ symbol }) {
  try {
    const exchange = await getExchange();
    const positions = await exchange.fetchPositions([symbol]);
    const activePos = positions.find(p => p.contracts > 0);

    if (!activePos) return { message: 'No active positions to close for ' + symbol };

    const side = activePos.side === 'long' ? 'sell' : 'buy';
    const amount = activePos.contracts;

    const order = await exchange.createOrder(symbol, 'market', side, amount);
    return {
      exchange: 'bybit',
      message: `Closed position for ${symbol}`,
      order_id: order.id,
      closed_qty: amount
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};
