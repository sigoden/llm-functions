#!/usr/bin/env node
// =============================================================================
// bybit_trader agent — JS entry point for the llm-functions agent runner.
//
// The real engine lives in ./bybit_trader.py (Pyrmethus Bybit V5 master,
// v8.0.0). This wrapper exposes it to aichat through the standard agent
// contract used by scripts/run-agent.js:
//
//   * _instructions — dynamic instructions hook (dynamic_instructions: true)
//   * bybit_trader  — the action function referenced by index.yaml
//
// The engine is spawned with LLM_OUTPUT unset so it prints its JSON result to
// stdout; scripts/run-agent.js owns writing the final payload to LLM_OUTPUT.
// =============================================================================

const path = require("path");
const { spawnSync } = require("child_process");

const MASTER = path.join(__dirname, "bybit_trader.py");

/**
 * Spawn the Python master engine and return its parsed JSON result.
 * @param {string[]} args - CLI arguments passed to bybit_trader.py
 * @returns {object} The parsed JSON payload produced by the engine.
 */
function runMaster(args) {
  const env = Object.create(process.env);
  // The engine must print to stdout here; the agent runner owns LLM_OUTPUT.
  delete env.LLM_OUTPUT;

  const result = spawnSync("python3", [MASTER, ...args], {
    encoding: "utf8",
    env,
    maxBuffer: 64 * 1024 * 1024,
  });

  if (result.error) {
    throw result.error;
  }

  const stdout = String(result.stdout || "").trim();

  // The engine reports failures as structured JSON on stdout with a non-zero
  // exit code. Prefer handing that payload to the LLM over throwing, so the
  // model sees the real error (fail-closed messages, valid actions, etc.).
  if (stdout) {
    try {
      return JSON.parse(stdout);
    } catch {
      // fall through to the exit-status handling below
    }
  }

  if (result.status !== 0) {
    const detail = String(result.stderr || stdout)
      .trim()
      .split("\n")
      .slice(-5)
      .join("\n");
    throw new Error(
      `bybit_trader engine failed (exit ${result.status}): ${detail}`
    );
  }

  throw new Error("bybit_trader engine produced no parsable output");
}

exports._instructions = async function () {
  const data = runMaster(["_instructions"]);
  if (data && typeof data.instructions === "string") {
    return data.instructions;
  }
  return JSON.stringify(data, null, 2);
};

/**
 * Hardened Bybit V5 USDT-perpetual market analytics, risk management, and guarded execution engine.
 * Use it for technical analysis, micro-scalp scans, orderbook and price-action reads, tickers,
 * fills/history, wallet balance, open positions and orders, live risk status, leverage changes,
 * the kill switch, and gated scalp execution or reduce-only closes.
 * @typedef {Object} Args
 * @property {'calc_micro_profit'|'cancel_orders'|'close_position'|'execute_scalp'|'fills'|'history'|'instrument'|'kill_switch'|'open_orders'|'orderbook'|'positions'|'price_action'|'risk_status'|'scan'|'set_leverage'|'ta'|'ticker'|'wallet_balance'} action - Engine action to execute
 * @property {string} [symbol] - USDT-perpetual symbol such as SOLUSDT (default: agent default_symbol)
 * @property {string} [timeframe] - Kline timeframe: 1, 3, 5, 15, 60, or D (default: 1)
 * @property {string} [side] - Order side: Buy or Sell (default: Buy)
 * @property {number} [target_profit_usdt] - Target net scalp profit in USDT (default: agent target_micro_profit)
 * @property {number} [qty] - Order quantity in base coin; derived from notional and instrument step when omitted
 * @property {integer} [leverage] - Leverage multiplier for set_leverage and execution (default: agent default_leverage)
 * @property {number} [max_spread_pct] - Maximum allowed spread percentage for the execution gate (default: agent max_spread_pct)
 * @property {number} [max_notional_usdt] - Maximum order notional in USDT (default: agent max_notional_usdt)
 * @property {boolean} [post_only] - Prefer PostOnly maker execution (default: true)
 * @property {boolean} [live] - Request live execution; still requires every independent safety gate (default: false)
 * @property {string} [live_confirmation] - Exact live confirmation token required for live execution
 * @property {boolean} [reduce_only] - Reduce-only close; bypasses directional entry signals (default: false)
 * @property {boolean} [use_cache] - Reuse cached market data when still fresh (default: false)
 */
exports.bybit_trader = async function (args) {
  return runMaster([JSON.stringify(args || {})]);
};
