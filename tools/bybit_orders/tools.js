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
 * List orders on Bybit
 * @param {string} [symbol] - Filter by symbol
 * @param {'open'|'closed'|'canceled'} [status] - Filter by status
 * @param {number} [limit=50] - Number of orders to return
 */
exports.list_orders = async function ({ symbol, status, limit = 50 }) {
  try {
    const exchange = await getExchange();
    const orders = await exchange.fetchOrders(symbol, undefined, limit);
    let filteredOrders = orders;
    if (status) {
      filteredOrders = orders.filter(order => order.status === status);
    }
    return {
      exchange: 'bybit',
      orders: filteredOrders.map(order => ({
        id: order.id,
        symbol: order.symbol,
        side: order.side,
        type: order.type,
        amount: order.amount,
        price: order.price,
        filled: order.filled,
        remaining: order.remaining,
        status: order.status,
        timestamp: order.timestamp,
        datetime: order.datetime
      })),
      total_count: filteredOrders.length
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

/**
 * Get a specific order by ID
 * @param {string} order_id - Order ID
 * @param {string} [symbol] - Symbol for the order
 */
exports.get_order = async function ({ order_id, symbol }) {
  try {
    const exchange = await getExchange();
    const order = await exchange.fetchOrder(order_id, symbol);
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
        timestamp: order.timestamp,
        datetime: order.datetime
      }
    };
  } catch (error) {
    return { error: error.message, order_id };
  }
};

/**
 * Cancel an existing order
 * @param {string} order_id - Order ID to cancel
 * @param {string} [symbol] - Symbol for the order
 */
exports.cancel_order = async function ({ order_id, symbol }) {
  try {
    const exchange = await getExchange();
    await exchange.cancelOrder(order_id, symbol);
    return {
      exchange: 'bybit',
      message: \`Order \${order_id} canceled successfully\`,
      order_id
    };
  } catch (error) {
    return { error: error.message, order_id };
  }
};

/**
 * Modify an existing order
 * @param {string} order_id - Order ID to modify
 * @param {string} [symbol] - Symbol for the order
 * @param {number} [new_price] - New price
 * @param {number} [new_amount] - New amount
 */
exports.modify_order = async function ({ order_id, symbol, new_price, new_amount }) {
  try {
    const exchange = await getExchange();
    const modifyParams = { order: order_id };
    if (new_price) modifyParams.price = new_price;
    if (new_amount) modifyParams.amount = new_amount;
    await exchange.modifyOrder(modifyParams, symbol);
    return {
      exchange: 'bybit',
      message: \`Order \${order_id} modified successfully\`,
      order_id
    };
  } catch (error) {
    return { error: error.message, order_id };
  }
};
