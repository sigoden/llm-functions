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
 * Get account balances and total USD value
 * @param {boolean} [include_zero=false] - Include zero balances
 */
exports.get_balances = async function ({ include_zero = false }) {
  try {
    const exchange = await getExchange();
    const balances = await exchange.fetchBalance();
    const nonZeroBalances = Object.entries(balances.total)
      .filter(([currency, amount]) => include_zero || amount > 0)
      .map(([currency, amount]) => ({
        currency,
        total: amount,
        free: balances.free[currency] || 0,
        used: balances.used[currency] || 0
      }));
    
    const totalUSD = await calculateTotalUSD(exchange, balances.total);
    return {
      exchange: 'bybit',
      balances: nonZeroBalances,
      total_balance_usd: totalUSD
    };
  } catch (error) {
    return { error: error.message };
  }
};

/**
 * Get active positions
 */
exports.get_positions = async function (args) {
  try {
    const exchange = await getExchange();
    const positions = await exchange.fetchPositions();
    const activePositions = positions.filter(p => p.contracts > 0);
    
    return {
      exchange: 'bybit',
      positions: activePositions.map(pos => ({
        symbol: pos.symbol,
        side: pos.side,
        size: pos.contracts,
        entryPrice: pos.entryPrice,
        markPrice: pos.markPrice,
        unrealizedPnl: pos.unrealizedPnl,
        percentage: pos.percentage,
        leverage: pos.leverage
      }))
    };
  } catch (error) {
    return { error: error.message };
  }
};

/**
 * Get general account information
 */
exports.get_account_info = async function (args) {
  try {
    const exchange = await getExchange();
    const accountInfo = await exchange.fetchAccount();
    return {
      exchange: 'bybit',
      account: {
        type: accountInfo.type,
        id: accountInfo.id,
        codes: accountInfo.codes,
        permissions: accountInfo.permissions,
        rateLimits: accountInfo.rateLimits
      }
    };
  } catch (error) {
    return { error: error.message };
  }
};

/**
 * Get margin mode for a specific symbol
 * @param {string} symbol - Trading symbol (e.g., 'BTC/USDT')
 */
exports.get_margin_mode = async function ({ symbol }) {
  try {
    const exchange = await getExchange();
    const margin = await exchange.fetchMarginMode(symbol);
    return {
      exchange: 'bybit',
      symbol,
      mode: margin
    };
  } catch (error) {
    return { error: error.message, symbol };
  }
};

async function calculateTotalUSD(exchange, balances) {
  try {
    let totalUSD = 0;
    const btcPrice = await exchange.fetchTicker('BTC/USDT');
    
    for (const [currency, amount] of Object.entries(balances)) {
      if (amount > 0) {
        if (currency === 'USDT' || currency === 'USD') {
          totalUSD += amount;
        } else if (currency === 'BTC') {
          totalUSD += amount * btcPrice.last;
        } else {
          try {
            const ticker = await exchange.fetchTicker(\`\${currency}/USDT\`);
            totalUSD += amount * ticker.last;
          } catch (e) {
            // Skip if no trading pair available
          }
        }
      }
    }
    return totalUSD;
  } catch (error) {
    return null;
  }
}
