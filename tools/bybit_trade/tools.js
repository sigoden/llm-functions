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

async function calculateOrderFee(exchange, symbol, type, amount, price) {
  try {
    const fee = await exchange.calculateFee(symbol, type, 'buy', amount, price);
    return {
      rate: fee.rate,
      cost: fee.cost,
      currency: fee.currency
    };
  } catch (error) {
    const defaultFeeRate = type === 'market' ? 0.001 : 0.0001;
    return {
      rate: defaultFeeRate,
      cost: amount * price * defaultFeeRate,
      currency: 'USDT'
    };
  }
}

/**
 * Create a new order on Bybit
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {'buy'|'sell'} side - Order side
 * @param {'market'|'limit'|'stop_loss'|'take_profit'} type - Order type
 * @param {number} amount - Order amount in base currency
 * @param {number} [price] - Order price (required for limit orders)
 */
exports.create_order = async function ({ symbol, side, type, amount, price }) {
  try {
    const exchange = await getExchange();
    if (type === 'limit' && !price) {
      throw new Error('price required for limit orders');
    }
    const order = await exchange.createOrder(symbol, type, side, amount, price);
    return {
      exchange: 'bybit',
      order: {
        id: order.id,
        symbol: order.symbol,
        side: order.side,
        type: order.type,
        amount: order.amount,
        price: order.price,
        filled: order.filled,
        remaining: order.remaining,
        status: order.status,
        timestamp: order.timestamp
      }
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Preview an order and calculate estimated costs and fees
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {'buy'|'sell'} side - Order side
 * @param {'market'|'limit'|'stop_loss'|'take_profit'} type - Order type
 * @param {number} amount - Order amount in base currency
 * @param {number} [price] - Order price (required for limit orders)
 */
exports.preview_order = async function ({ symbol, side, type, amount, price }) {
  try {
    const exchange = await getExchange();
    const ticker = await exchange.fetchTicker(symbol);
    const estimatedPrice = type === 'market' ? (side === 'buy' ? ticker.ask : ticker.bid) : price;
    if (!estimatedPrice) throw new Error('Price is required for preview of limit orders');
    const feeInfo = await calculateOrderFee(exchange, symbol, type, amount, estimatedPrice);
    return {
      exchange: 'bybit',
      preview: {
        symbol,
        side,
        type,
        amount,
        price: estimatedPrice,
        estimatedCost: amount * estimatedPrice,
        fee: feeInfo,
        marketInfo: {
          bid: ticker.bid,
          ask: ticker.ask,
          spread: ticker.ask - ticker.bid,
          spreadPercent: ((ticker.ask - ticker.bid) / ticker.bid * 100).toFixed(4)
        }
      }
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Calculate estimated trading fees for a specific order
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 * @param {'market'|'limit'|'stop_loss'|'take_profit'} type - Order type
 * @param {number} amount - Order amount in base currency
 * @param {number} [price] - Order price
 */
exports.calculate_fee = async function ({ symbol, type, amount, price }) {
  try {
    const exchange = await getExchange();
    const ticker = await exchange.fetchTicker(symbol);
    const feePrice = price || ticker.last;
    const fee = await calculateOrderFee(exchange, symbol, type, amount, feePrice);
    return {
      exchange: 'bybit',
      fee: {
        symbol,
        type,
        amount,
        price: feePrice,
        fee: fee,
        timestamp: Date.now()
      }
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};
