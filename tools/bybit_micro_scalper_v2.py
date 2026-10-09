#!/usr/bin/env python3
# ==============================================================================
# bybit_micro_scalper_v2.py — Bybit Micro‑Profit Scalper (v2)
#
# @describe Bybit Micro‑Profit Scalper (v2)
# @meta title Bybit Micro‑Profit Scalper (v2)
# @meta description High‑frequency scalping tool optimized for tiny $0.02‑$0.20 net profit targets using order‑book and momentum conditions.
# @env BYBIT_API_KEY              Bybit API key.
# @env BYBIT_API_SECRET           Bybit API secret.
# @option --symbol=BTCUSDT         Target crypto derivative trading pair.
# @option --qty=0.01               Order size/quantity (base asset).
# @option --target-profit=0.05      Desired micro‑profit target in USDT (0.02‑0.20 recommended).
# @option --maker-fee=0.0002       Maker fee tier (default 0.02% = 0.0002).
# @option --trailing-stop=0.001    <Optional> Trailing distance (price delta) expressed as a decimal fraction of entry price (e.g. 0.001 = 0.1%).
# @option --balance-check!          <Optional> Perform a pre‑order account‑balance validation.
# ==============================================================================

import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any, Dict

import requests

BASE_URL = "https://bybit.com"


# ---------------------------------------------------------------------------
# Helper: signature generation
# ---------------------------------------------------------------------------
def generate_signature(
    secret: str, timestamp: int, api_key: str, recv_window: int, payload: str
) -> str:
    param_str = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(
        secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# Helper: signed POST request
# ---------------------------------------------------------------------------
def send_signed_post(
    endpoint: str, payload_dict: Dict[str, Any], api_key: str, api_secret: str
) -> Dict[str, Any]:
    timestamp = int(time.time() * 1000)
    recv_window = 5000
    payload_json = json.dumps(payload_dict)
    signature = generate_signature(
        api_secret, timestamp, api_key, recv_window, payload_json
    )

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(recv_window),
        "Content-Type": "application/json",
    }

    response = requests.post(BASE_URL + endpoint, headers=headers, data=payload_json)
    return response.json()


# ---------------------------------------------------------------------------
# Helper: fetch market data (order‑book + candles) with cache‑busting headers
# ---------------------------------------------------------------------------
def get_market_data(symbol: str) -> Dict[str, Any]:
    session = requests.Session()
    session.headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})

    # Order book
    ob_url = f"{BASE_URL}/v5/market/orderbook?category=linear&symbol={symbol}&limit=5"
    ob_data = session.get(ob_url).json()["result"]
    best_bid, best_ask = float(ob_data["b"]), float(ob_data["a"])
    bid_vol = sum(float(b) for b in ob_data["b"])
    ask_vol = sum(float(a) for a in ob_data["a"])
    imbalance = (
        (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0.0
    )

    # 1‑minute candles for micro‑momentum
    kline_url = (
        f"{BASE_URL}/v5/market/kline?category=linear&symbol={symbol}&interval=1&limit=3"
    )
    kline_list = session.get(kline_url).json()["result"]["list"]
    close_now = float(kline_list[-1]["close"])
    close_past = float(kline_list[-2]["close"])
    momentum = (close_now - close_past) / close_past if close_past > 0 else 0.0

    # Tick‑size verification
    inst_url = f"{BASE_URL}/v5/market/instruments-info?category=linear&symbol={symbol}"
    inst_data = session.get(inst_url).json()["result"]["list"]
    tick_size = float(inst_data[0]["priceFilter"]["tickSize"])

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "imbalance": imbalance,
        "momentum": momentum,
        "tick_size": tick_size,
    }


# ---------------------------------------------------------------------------
# Helper: round price to nearest tick
# ---------------------------------------------------------------------------
def round_to_tick(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 8)


# ---------------------------------------------------------------------------
# Helper: account‑balance validation (optional)
# ---------------------------------------------------------------------------
def check_account_balance(
    api_key: str, api_secret: str, required_margin: float
) -> bool:
    """
    Query the Bybit account endpoint and verify that the *free* balance can cover
    the required margin. Returns True if OK, False otherwise.
    """
    endpoint = "/v5/account"
    payload = {}
    resp = send_signed_post(endpoint, payload, api_key, api_secret)
    if resp.get("retCode") != 0:
        print(
            json.dumps({"status": "error", "message": f"Balance check failed: {resp}"})
        )
        return False

    balance = resp.get("result", {}).get("accountInfo", {}).get("list", {})
    free_balance = 0.0
    for asset in balance:
        if asset["asset"] == "USDT":  # we only care about USDT margin for this script
            free_balance = float(asset["balance"])
            break

    if free_balance < required_margin:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "message": f"Insufficient free balance (USDT {free_balance:.2f}) to cover required margin ({required_margin:.2f})",
                }
            )
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
def main(args: Dict[str, Any]) -> None:
    # -----------------------------------------------------------------------
    # Argument extraction (preserve original flag names)
    # -----------------------------------------------------------------------
    api_key = args.get("api_key") or os.environ.get("BYBIT_API_KEY")
    api_secret = args.get("api_secret") or os.environ.get("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Missing BYBIT_API_KEY or BYBIT_API_SECRET in environment variables.",
                }
            )
        )
        return
    symbol = args.get("symbol")
    qty = float(args.get("qty"))
    target_profit = float(args.get("target_profit"))
    maker_fee = float(args.get("maker_fee"))

    # New optional flags
    trailing_stop = args.get("trailing_stop")  # e.g. 0.001 = 0.1% of entry price
    balance_check = args.get("balance_check", False)  # boolean flag

    # -----------------------------------------------------------------------
    # Basic validation
    # -----------------------------------------------------------------------
    if not (0.01 <= target_profit <= 1.00):
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Target profit must be within micro‑bounds ($0.01 to $1.00 USDT)",
                }
            )
        )
        return

    # -----------------------------------------------------------------------
    # Fetch market data (with cache‑busting headers)
    # -----------------------------------------------------------------------
    try:
        market = get_market_data(symbol)
        best_bid, best_ask = market["best_bid"], market["best_ask"]
        imbalance, momentum = market["imbalance"], market["momentum"]
        tick_size = market["tick_size"]
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "message": f"Market data fetch failed: {exc!s}"}
            )
        )
        return

    # -----------------------------------------------------------------------
    # Determine trade side & compute entry / exit prices
    # -----------------------------------------------------------------------
    side, tp_side, entry_price, exit_price = None, None, 0.0, 0.0

    # Micro‑imbalance + momentum trigger
    if momentum > 0.0001 and imbalance > 0.05:
        side, tp_side = "Buy", "Sell"
        entry_price = best_bid
        # ---- entry fee (maker fee) -------------------------------------------------
        entry_fee = entry_price * qty * maker_fee
        # ---- raw exit price that yields `target_profit` after fees -----------------
        raw_exit = (target_profit + entry_fee + entry_price * qty) / (
            qty * (1 - maker_fee)
        )
        exit_price = round_to_tick(raw_exit, tick_size)

    elif momentum < -0.0001 and imbalance < -0.05:
        side, tp_side = "Sell", "Buy"
        entry_price = best_ask
        entry_fee = entry_price * qty * maker_fee
        raw_exit = ((entry_price * qty) - entry_fee - target_profit) / (
            qty * (1 + maker_fee)
        )
        exit_price = round_to_tick(raw_exit, tick_size)

    # -----------------------------------------------------------------------
    # Skip logic – no viable signal
    # -----------------------------------------------------------------------
    if not side:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "momentum": round(momentum, 6),
                    "imbalance": round(imbalance, 2),
                    "message": "Market velocity is stable; waiting for clear micro‑imbalance structural shifts.",
                }
            )
        )
        return

    # -----------------------------------------------------------------------
    # Validate tick‑size vs target profit
    # -----------------------------------------------------------------------
    if abs(exit_price - entry_price) <= tick_size:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "message": f"Target profit of ${target_profit} matches or falls below a single tick size ({tick_size}). Increase quantity or target_profit.",
                }
            )
        )
        return

    # -----------------------------------------------------------------------
    # OPTIONAL: trailing‑stop handling
    # -----------------------------------------------------------------------
    # If the user supplied a trailing‑stop distance we attach it to the take‑profit
    # order. Bybit supports a `trailingStop` field on the TP order when
    # `tpTriggerBy` is set to `Trailing`. We compute the stop‑price as:
    #   stop_price = exit_price * (1 - trailing_stop)   (for longs)
    #   stop_price = exit_price * (1 + trailing_stop)   (for shorts)
    # The stop‑price is then used as the TP trigger price.
    if trailing_stop:
        if side == "Buy":  # long position
            tp_sl_price = exit_price * (1 - float(trailing_stop))
        else:  # short position
            tp_sl_price = exit_price * (1 + float(trailing_stop))

        # Ensure the trailing stop is still respectful of the tick size
        tp_sl_price = round_to_tick(tp_sl_price, tick_size)

        # Update the TP payload fields
        take_profit_payload = {
            "tpOrderType": "Limit",
            "tpPrice": str(tp_sl_price),
            "tpTriggerBy": "Trailing",
        }
    else:
        # Default static TP (same as original script)
        take_profit_payload = {
            "tpOrderType": "Limit",
            "tpPrice": str(exit_price),
            "tpTriggerBy": "LastPrice",
        }

    # -----------------------------------------------------------------------
    # OPTIONAL: balance safety check
    # -----------------------------------------------------------------------
    if balance_check:
        # Estimate required margin: entry_price * qty * (1 + maker_fee)
        required_margin = entry_price * qty * (1 + maker_fee)
        if not check_account_balance(api_key, api_secret, required_margin):
            # `check_account_balance` already printed an error JSON – abort.
            return

    # -----------------------------------------------------------------------
    # Build and send the *chained* order (entry + attached TP)
    # -----------------------------------------------------------------------
    entry_payload = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Limit",
        "qty": str(qty),
        "price": str(entry_price),
        "timeInForce": "PostOnly",
        "positionIdx": 0,
        "takeProfit": str(exit_price),
        # The following fields are added only when a trailing‑stop is configured:
        **take_profit_payload,
        # Bybit V5 also allows us to attach a *stop‑loss* if desired; for now we
        # only chain the TP. If you need a separate stop‑loss, add it here.
    }

    entry_res = send_signed_post("/v5/order/create", entry_payload, api_key, api_secret)

    if entry_res.get("retCode") != 0:
        print(
            json.dumps(
                {"status": "failed", "stage": "execution", "response": entry_res}
            )
        )
        return

    # -----------------------------------------------------------------------
    # Emit success payload (unchanged signature – still ends up in $LLM_OUTPUT or stdout)
    # -----------------------------------------------------------------------
    result = {
        "status": "success",
        "direction": "LONG" if side == "Buy" else "SHORT",
        "metrics": {"momentum": round(momentum, 6), "imbalance": round(imbalance, 2)},
        "execution": {
            "entry_limit_price": entry_price,
            "take_profit_price": exit_price,
            "tick_spread_required": round(abs(exit_price - entry_price) / tick_size, 1),
            "order_id": entry_res.get("result", {}).get("orderId"),
            "architecture": "Chained Native TP Order (Automated Risk Lifecycle)",
        },
    }

    # If a trailing‑stop was configured we expose it for logging / monitoring
    if trailing_stop:
        result["execution"]["trailing_stop"] = {
            "distance": float(trailing_stop),
            "trigger_price": round(tp_sl_price, 8),
        }

    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Entry‑point – compatible with the original `argc`‑based runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Convert CLI flags (--key=value) into a clean dict expected by `main()`
    args_dict: Dict[str, Any] = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--"):
            k, v = arg.split("=", 1)
            # Normalise flag names to match the @option definitions
            args_dict[k.replace("--", "").replace("-", "_")] = v

    main(args_dict)
